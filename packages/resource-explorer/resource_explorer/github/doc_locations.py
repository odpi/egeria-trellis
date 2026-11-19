"""Documentation-location resolution — where does a project's documentation
actually live, and where is a specific artifact (architecture doc, changelog,
examples, ...) within it.

Why this module exists (design §5.5a/§5.5b, spike finding 68): a survey that
only reads a repo's own `docs/` directory finds architecture documentation in
2 of 12 measured cases. Four of five topologies measured (finding 68) keep
documentation in a **sibling repo** (`kubernetes/website`, `odpi/egeria-docs`,
`milvus-io/milvus-docs`, `prometheus/docs`) rather than in-tree. Answering
"is this documented" by checking one directory's mtime is worse than not
answering: `kubernetes/kubernetes/docs/` is 1412 days stale by that naive
metric, on one of the best-documented projects in existence, because the
real docs moved to `kubernetes/website` (pushed today) and `docs/` is a
**tombstone** — `.gitignore` and `OWNERS`, nothing else. A tombstone is a
*positive* curation signal (deliberate relocation), not absence, and must be
reported as such (§5.5a(c) point 3).

**No ranking, no score.** §5.5a(c) point 4 and §5.5b are explicit: this
module reports locations and dates as dated evidence, never a "documentation
health" number. A small stable library documenting lightly on purpose and a
project that deliberately archived stale docs both look different from "no
docs", but neither is worse than the other — that judgement is a human's
(or a later, separate classification pass's) to make, not this module's.

Two entry points:

- `resolve_doc_locations(owner_repo)` — every documentation location found
  for a project: in-repo doc directories (real or tombstone), the declared
  homepage, sibling documentation repos in the same org, and off-site
  architecture links pulled from the README.
- `find_artifact(owner_repo, kind)` — resolve one specific kind of artifact
  (`architecture`, `readme`, `examples`, `deployment`, `changelog`,
  `api-reference`) to a **location**, never a boolean. Exactly four
  outcomes — `in-repo` / `sibling-repo` / `doc-site` / `not-found` — and
  only the last is an absence. This is the fix for the Kubernetes case
  above: naive in-repo-only search returns "not found, stale 1412 days";
  the correct answer is "in `kubernetes/website`, pushed today".

Graceful absence, never an exception (rule from `code_markers._binary()`
and `detect.py`'s `_coupling_components`): no token, no network, and a
rate limit all degrade to a result carrying a clear note in `.notes`
rather than raising. Every network call in this module is individually
guarded so one failing signal (e.g. the README fetch) never takes down the
others (e.g. sibling-repo listing) — same posture as `_coupling_components`
treating a missing signal file as "a note, not an error".

Token handling mirrors `github/client.py`: `GITHUB_TOKEN` comes from
`get_config().github`, passed to PyGithub's `Github(...)` constructor —
never placed in argv, a URL, or a log line (PyGithub sends it as an
`Authorization` header internally).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from github import Github, GithubException
from github.ContentFile import ContentFile
from github.Repository import Repository

from resource_explorer.config import get_config

# In-repo directories checked for documentation content, in the order
# listed in the task spec. Order matters only for output stability, not
# correctness — all matches are reported, not just the first.
DOC_DIR_NAMES: list[str] = ["docs", "documentation", "doc", "website", "site"]

# A doc directory holding only entries from this set (case-insensitive, no
# subdirectories with other content) is a tombstone: someone retired it
# deliberately rather than letting it rot. Finding 68's exact Kubernetes
# case is `.gitignore` + `OWNERS`. README variants are included because a
# directory's only remaining content is often a "moved to X" pointer file.
_STUB_ENTRY_NAMES: frozenset[str] = frozenset({
    "owners", "owners.md", ".gitignore", ".gitkeep", ".keep",
    "readme.md", "readme", "readme.rst", "readme.txt",
    "codeowners", "license", "license.md", ".gitattributes",
})

# Sibling-repo name patterns checked against every other repo in the same
# owner/org. "{repo}" is substituted with the project's own repo name.
# Order is priority order for find_artifact()'s "best sibling" pick, not a
# filter — every match is still reported by resolve_doc_locations().
# Sibling repositories that plausibly hold a project's documentation.
#
# Widened 2026-08-25: documentation does not only live in repos that call
# themselves documentation. Tutorials, workshops and examples describe a
# system's shape as directly as a design doc does — often more concretely —
# and `OpenLineage/workshops` and `odpi/egeria-workspaces` are both in this
# corpus. A name that turns out to hold nothing costs one shallow search;
# a name we never try costs the document.
_SIBLING_NAME_PATTERNS: list[str] = [
    "{repo}-docs", "{repo}-doc", "{repo}.github.io", "docs", "website", "site",
    "{repo}-website", "{repo}-site", "{repo}-tutorial", "{repo}-tutorials",
    "{repo}-examples", "{repo}-workshop", "{repo}-workshops",
    "tutorial", "tutorials", "workshop", "workshops", "examples", "learn",
]

# Kind -> filename/dirname substrings (lowercase) that count as a match
# when scanning a doc directory or repo root. Deliberately loose substring
# matching rather than exact filenames — real-world naming varies
# (`internal_architecture.md`, `ARCHITECTURE_OVERVIEW.md`, ...).
_ARTIFACT_KEYWORDS: dict[str, list[str]] = {
    "architecture": ["architecture", "design-docs", "design_docs", "solution-blueprint"],
    "readme": ["readme"],
    "examples": ["example"],
    "deployment": ["deploy", "install", "quickstart", "getting-started", "getting_started"],
    "changelog": ["changelog", "changes", "release-notes", "release_notes", "history"],
    "api-reference": ["api-reference", "api_reference", "apireference", "api/", "reference"],
}

Outcome = Literal["in-repo", "sibling-repo", "doc-site", "not-found"]


@dataclass
class DocDirResult:
    """One in-repo candidate doc directory (`docs/`, `documentation/`, ...)."""
    path: str
    is_tombstone: bool
    entry_names: list[str]
    last_commit_date: str | None = None  # ISO 8601, from the dir's own commit history


@dataclass
class SiblingRepo:
    """A same-owner repo whose name matches a documentation-repo convention."""
    full_name: str
    matched_pattern: str
    pushed_at: str | None = None  # ISO 8601, GitHub's `pushed_at`


@dataclass
class ReadmeLink:
    """An off-site link found in the README, plausibly pointing at published docs."""
    text: str
    url: str


@dataclass
class DocLocations:
    """Every documentation location found for `owner/repo`, each with its
    own evidence. Deliberately a bag of findings, not a verdict — §5.5a(c)
    point 4: report the observations, do not rank them."""
    owner_repo: str
    in_repo_dirs: list[DocDirResult] = field(default_factory=list)
    homepage: str | None = None
    sibling_repos: list[SiblingRepo] = field(default_factory=list)
    readme_links: list[ReadmeLink] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # degraded-signal / error notes

    @property
    def real_doc_dirs(self) -> list[DocDirResult]:
        """In-repo doc dirs that are NOT tombstones — i.e. actually hold content."""
        return [d for d in self.in_repo_dirs if not d.is_tombstone]

    @property
    def tombstoned_dirs(self) -> list[DocDirResult]:
        return [d for d in self.in_repo_dirs if d.is_tombstone]


@dataclass
class ArtifactResult:
    """The result of find_artifact(): a LOCATION, never a boolean. Only
    outcome == "not-found" is an absence — the other three all point
    somewhere, with evidence and a date where one is available."""
    kind: str
    outcome: Outcome
    evidence: str  # a path (in-repo), "owner/repo:path" (sibling-repo), or a URL (doc-site)
    date: str | None = None  # last-commit date (in-repo/sibling) or None (doc-site: unverified)
    note: str = ""


def _is_tombstone(entry_names: list[str]) -> bool:
    """A directory is a tombstone when every entry is a stub name (or the
    directory is empty) — content moved elsewhere and only pointer files
    were left behind. See finding 68(b): `kubernetes/kubernetes/docs/`
    holds exactly `.gitignore` and `OWNERS`.

    Any single entry outside `_STUB_ENTRY_NAMES` (a subdirectory, a real
    markdown page, an image, ...) is enough to call it real content — this
    deliberately errs toward "real" so a tombstone claim is conservative:
    reporting a maintained dir as a tombstone would be worse than the
    reverse, since the tombstone claim is treated as a positive signal.
    """
    if not entry_names:
        return True
    return all(name.strip().lower() in _STUB_ENTRY_NAMES for name in entry_names)


# KNOWN LIMITATION, found in live verification against the five measured
# projects (design §5.5b): a bare `website` / `docs` sibling is the project's
# own documentation only when the ORG is effectively the project.
# `kubernetes/website` is Kubernetes's docs and is matched correctly. But
# `odpi/website` is the ODPI *foundation's* site, and ODPI hosts several
# independent projects — so it is returned as a sibling for BOTH `odpi/egeria`
# and `odpi/egeria-workspaces`, and is the documentation of neither.
#
# Not fixed here, deliberately. The naive fix — drop bare `website`/`docs` —
# would break the Kubernetes case, which is the one this whole module exists
# for (finding 68). The distinguishing signal is whether the org hosts one
# project or many, which needs org-level context this function does not have.
# It matters less than it first appears, because **the evidence already carries
# the discriminator**. Verified 2026-08-23:
#
#     odpi/website        pushed_at 2019-11-07   <- ~7 years stale
#     kubernetes/website  pushed_at 2026-08-23
#     prometheus/docs     pushed_at 2026-08-21
#     odpi/egeria-docs    pushed_at 2026-08-22
#
# Every genuine documentation sibling was pushed within two days; the false
# match is stale by seven years. A consumer that reads the date it is given can
# discount it without this module making the call — which is the point of
# §5.5a(c)'s "report the observations, do not rank them". Suppressing it here
# would BE the ranking judgement that rule forbids.
#
# And in the intended flow it costs nothing: §5.5b asks the user whether to
# include a sibling repo, never adds one silently, so an extra dated candidate
# in that prompt is a checkbox, not a wrong answer.
def _match_sibling_names(repo_name: str, candidate_names: list[str]) -> dict[str, str]:
    """Return {candidate_name: matched_pattern} for every name in
    `candidate_names` that matches one of `_SIBLING_NAME_PATTERNS` for
    `repo_name`, case-insensitively. Pure function so sibling-matching
    logic is testable without a network call."""
    patterns = {p.format(repo=repo_name).lower() for p in _SIBLING_NAME_PATTERNS}
    out: dict[str, str] = {}
    for name in candidate_names:
        low = name.strip().lower()
        if low == repo_name.lower():
            continue  # never match the project against itself
        if low in patterns:
            # recover which pattern matched, for evidence/debugging
            for p in _SIBLING_NAME_PATTERNS:
                if p.format(repo=repo_name).lower() == low:
                    out[name] = p
                    break
    return out


_ARCHITECTURE_LINK_HINTS = ("architect", "design", "blueprint", "internals", "overview")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _extract_offsite_architecture_links(readme_text: str, owner_repo: str) -> list[ReadmeLink]:
    """Pull markdown links out of a README that (a) point off-site — not
    back into this same repo — and (b) look architecture-related by
    link-text or URL keyword. This is intentionally a heuristic filter
    (§5.5a(a): "an outward hop... cheap, once"), not an attempt to
    classify every external link the README carries."""
    own_repo_marker = f"github.com/{owner_repo}".lower()
    out: list[ReadmeLink] = []
    for text, url in _MD_LINK_RE.findall(readme_text or ""):
        low_text, low_url = text.lower(), url.lower()
        if own_repo_marker in low_url:
            continue
        if any(hint in low_text or hint in low_url for hint in _ARCHITECTURE_LINK_HINTS):
            out.append(ReadmeLink(text=text.strip(), url=url.strip()))
    return out


def _make_client() -> Github:
    """Same construction as `GitHubClient.__init__` — PyGithub handles the
    token as an Authorization header internally, so it is never in argv,
    a URL, or anything this module logs."""
    cfg = get_config().github
    from resource_explorer.github.bounded_retry import bounded_kwargs

    # TR-62a: an explicit timeout and a retry that never sleeps past a bound (github/bounded_retry.py).
    return Github(cfg.token or None, base_url=cfg.base_url, per_page=100, **bounded_kwargs(cfg.token))


def _degraded(notes: list[str], exc: Exception, what: str) -> None:
    """Append a clear, non-throwing note for a failed sub-operation.
    Matches `_coupling_components`'s "a missing precomputed signal is a
    note, not an error" posture — a rate limit or auth failure on one
    signal (e.g. the README fetch) must not prevent the others (e.g.
    sibling-repo listing) from being attempted and reported."""
    notes.append(f"{what}: {type(exc).__name__}: {exc}")


def _get_repo(gh: Github, owner_repo: str, notes: list[str]) -> Repository | None:
    try:
        return gh.get_repo(owner_repo)
    except GithubException as exc:
        _degraded(notes, exc, f"could not fetch repo '{owner_repo}'")
        return None
    except Exception as exc:  # network errors, timeouts, etc. — never raise out of this module
        _degraded(notes, exc, f"could not fetch repo '{owner_repo}' (network)")
        return None


def _dir_last_commit_date(repo: Repository, path: str, notes: list[str]) -> str | None:
    try:
        commits = repo.get_commits(path=path)
        first = commits[0]
        return first.commit.committer.date.isoformat()
    except Exception as exc:
        # Not fatal — the directory/tombstone classification stands even
        # without a date; a missing date is itself reported via the note.
        _degraded(notes, exc, f"could not date '{path}'")
        return None


def _list_in_repo_dirs(repo: Repository, notes: list[str]) -> list[DocDirResult]:
    found: list[DocDirResult] = []
    for name in DOC_DIR_NAMES:
        try:
            contents = repo.get_contents(name)
        except GithubException as exc:
            if getattr(exc, "status", None) == 404:
                continue  # normal: this candidate dir just doesn't exist
            _degraded(notes, exc, f"could not read '{name}'")
            continue
        except Exception as exc:
            _degraded(notes, exc, f"could not read '{name}' (network)")
            continue

        if isinstance(contents, ContentFile):
            # A file named e.g. "docs" (unusual) rather than a directory —
            # treat as real content, not a tombstone (nothing to relocate).
            found.append(DocDirResult(path=name, is_tombstone=False, entry_names=[contents.name]))
            continue

        entry_names = [c.name for c in contents]
        tombstone = _is_tombstone(entry_names)
        last_date = _dir_last_commit_date(repo, name, notes)
        found.append(DocDirResult(
            path=name, is_tombstone=tombstone, entry_names=entry_names,
            last_commit_date=last_date,
        ))
    return found


def _list_sibling_repos(gh: Github, repo: Repository, notes: list[str]) -> list[SiblingRepo]:
    """Sibling documentation repos, found by PROBING the enumerated names.

    This listed every repository in the owning organisation and filtered the
    result. On `apache` that is ~2,700 repos, paged; the presentation session
    measured the step at **3 repos in 10 minutes**, network-blocked at 0.5% CPU,
    against 1 second for the other three Discovery steps over 60 repos.

    Probing is **exactly equivalent**, not an approximation: `_match_sibling_names`
    matches by case-insensitive equality against a small enumerated pattern set,
    so asking for those names directly can find nothing the listing would have
    found. Roughly five requests, of which the misses are cheap 404s, instead of
    paging an entire organisation.
    """
    owner_login = repo.owner.login
    repo_name = repo.name
    out: list[SiblingRepo] = []
    seen: set[str] = set()
    resolved: set[str] = set()
    for pattern in _SIBLING_NAME_PATTERNS:
        candidate = pattern.format(repo=repo_name)
        if candidate.lower() == repo_name.lower() or candidate.lower() in seen:
            continue
        seen.add(candidate.lower())
        try:
            sibling = gh.get_repo(f"{owner_login}/{candidate}")
        except GithubException as exc:
            if getattr(exc, "status", None) != 404:
                _degraded(notes, exc, f"probing sibling '{owner_login}/{candidate}'")
            continue
        except Exception as exc:
            _degraded(notes, exc, f"probing sibling '{owner_login}/{candidate}' (network)")
            continue
        # GitHub follows renames, and probing exposes two failure modes that
        # listing an organisation could not — both found live, not reasoned
        # about:
        #
        #  * a redirect can land on a DIFFERENT owner. `prometheus/prometheus`
        #    probing `prometheus.github.io` resolves to
        #    `prometheus-junkyard/prometheus.github.io`, an archived repo of
        #    another account. A sibling of this project must belong to this
        #    project's owner.
        #  * two patterns can redirect to the SAME repo.
        #    `kubernetes/kubernetes.github.io` and `kubernetes/website` are one
        #    repository, and it was reported twice.
        if sibling.owner.login.lower() != owner_login.lower():
            continue
        if sibling.full_name.lower() in resolved:
            continue
        resolved.add(sibling.full_name.lower())
        pushed_at = sibling.pushed_at.isoformat() if sibling.pushed_at else None
        out.append(SiblingRepo(full_name=sibling.full_name, matched_pattern=pattern,
                               pushed_at=pushed_at))
    return out


def _readme_links(repo: Repository, notes: list[str]) -> list[ReadmeLink]:
    try:
        readme = repo.get_readme()
        text = readme.decoded_content.decode("utf-8", errors="ignore")
    except GithubException as exc:
        if getattr(exc, "status", None) != 404:
            _degraded(notes, exc, "could not fetch README")
        return []
    except Exception as exc:
        _degraded(notes, exc, "could not fetch README (network)")
        return []
    return _extract_offsite_architecture_links(text, repo.full_name)


def resolve_doc_locations(owner_repo: str, client: Github | None = None) -> DocLocations:
    """Resolve every documentation location for `owner/repo`.

    Each of the four signal types (in-repo dirs, homepage, siblings,
    README links) is fetched independently and failures are isolated —
    a rate-limited sibling-repo listing must not prevent an already-fetched
    homepage field from being reported. On total failure to reach the repo
    at all (bad token, network down, repo not found), returns a DocLocations
    with empty lists and an explanatory note rather than raising.
    """
    gh = client or _make_client()
    notes: list[str] = []
    repo = _get_repo(gh, owner_repo, notes)
    if repo is None:
        return DocLocations(owner_repo=owner_repo, notes=notes)

    in_repo_dirs = _list_in_repo_dirs(repo, notes)
    homepage = (repo.homepage or "").strip() or None
    sibling_repos = _list_sibling_repos(gh, repo, notes)
    readme_links = _readme_links(repo, notes)

    return DocLocations(
        owner_repo=owner_repo,
        in_repo_dirs=in_repo_dirs,
        homepage=homepage,
        sibling_repos=sibling_repos,
        readme_links=readme_links,
        notes=notes,
    )


def _find_in_dir(repo: Repository, dir_path: str, keywords: list[str], notes: list[str]) -> str | None:
    """Shallow (root + one level) search of `dir_path` for an entry whose
    path contains any of `keywords`. Kept shallow deliberately — this is a
    Discovery-tier lookup (§5.5a(c) point 1: "costs nothing, gates the
    expensive tiers"), not a full-repo crawl."""
    try:
        contents = repo.get_contents(dir_path)
    except Exception as exc:
        _degraded(notes, exc, f"could not search '{dir_path}'")
        return None
    if isinstance(contents, ContentFile):
        contents = [contents]

    def _matches(p: str) -> bool:
        low = p.lower()
        return any(kw in low for kw in keywords)

    for c in contents:
        if _matches(c.path):
            return c.path
    # one level deeper, only into subdirectories
    for c in contents:
        if c.type == "dir":
            try:
                sub = repo.get_contents(c.path)
            except Exception as exc:
                _degraded(notes, exc, f"could not search '{c.path}'")
                continue
            for s in sub:
                if _matches(s.path):
                    return s.path
    return None


def _find_root(repo: Repository, keywords: list[str], notes: list[str]) -> str | None:
    return _find_in_dir(repo, "", keywords, notes)


def _search_sibling(gh: Github, sibling, keywords: list[str], notes: list[str]):
    """Find a specific artifact page inside a sibling documentation repo.

    Searches the sibling's ROOT and then its own documentation directories.
    Root alone was the original behaviour and it is why every sibling answer in
    the corpus came back as a bare pointer at the repo rather than a page
    (measured 2026-08-25: marquez, OpenLineage, docling-java, all pointers) —
    a documentation website keeps its pages under `docs/` or `content/`, not
    scattered at the top level, so the one place we looked was the one place
    they are not.

    Returns `(repo, path_or_None)`; the repo comes back so a caller can date
    the match without fetching it twice.
    """
    try:
        sib_repo = gh.get_repo(sibling.full_name)
    except Exception as exc:  # noqa: BLE001
        _degraded(notes, exc, f"could not open sibling '{sibling.full_name}'")
        return None, None

    match = _find_root(sib_repo, keywords, notes)
    if match:
        return sib_repo, match
    for name in DOC_DIR_NAMES + ["content", "src"]:
        match = _find_in_dir(sib_repo, name, keywords, notes)
        if match:
            return sib_repo, match
    return sib_repo, None


def find_artifacts(owner_repo: str, kind: str, client: Github | None = None,
                   locations: DocLocations | None = None) -> list[ArtifactResult]:
    """**Every** location for `kind`, not the best one.

    `find_artifact` returns the first — which was the whole model until
    2026-08-25, and it discards real answers. Measured on this corpus:
    `OpenLineage/OpenLineage` has three sibling documentation repos, a declared
    homepage and two in-repo doc directories — six sources — and the single-
    answer form picked one, which yielded nothing. Milvus's README links point
    at `milvus.io/docs/...` pages, including a Tutorials Overview, none of which
    a repo-shaped answer can express.

    Two things follow, and both were Dan's framing:

    * **Documentation is plural.** `DocLocations` has always been "a bag of
      findings, not a verdict" (§5.5a(c) point 4); it was `find_artifact` that
      collapsed it. This restores the bag for one artifact kind.
    * **A source is often not a repository.** A published documentation site is
      a first-class location, not a fallback for when no repo was found. It is
      still returned as `doc-site` and still unreadable from here — but recorded
      as a source rather than an absence, which is what a recommendation to
      ingest it can be attached to.

    Ordered cheapest-and-most-specific first, same as `find_artifact`, so
    `find_artifacts(...)[0]` is that function's answer. Never empty: a genuine
    absence is a single `not-found`.
    """
    gh = client or _make_client()
    keywords = _ARTIFACT_KEYWORDS.get(kind, [kind.lower()])
    if locations is None:
        locations = resolve_doc_locations(owner_repo, client=gh)

    repo = _get_repo(gh, owner_repo, locations.notes)
    if repo is None:
        return [ArtifactResult(kind=kind, outcome="not-found", evidence="",
                               note="repo unreachable; " + "; ".join(locations.notes))]

    out: list[ArtifactResult] = []
    seen: set = set()

    def _add(result: ArtifactResult) -> None:
        key = (result.outcome, result.evidence)
        if result.evidence and key not in seen:
            seen.add(key)
            out.append(result)

    for d in locations.real_doc_dirs:
        match = _find_in_dir(repo, d.path, keywords, locations.notes)
        if match:
            _add(ArtifactResult(kind=kind, outcome="in-repo", evidence=match,
                                date=_dir_last_commit_date(repo, match, locations.notes)))

    root_match = _find_root(repo, keywords, locations.notes)
    if root_match:
        _add(ArtifactResult(kind=kind, outcome="in-repo", evidence=root_match,
                            date=_dir_last_commit_date(repo, root_match, locations.notes)))

    # EVERY sibling, not just the first — three of them on OpenLineage, and
    # nothing said the first was the one holding the architecture.
    for sib in locations.sibling_repos:
        sib_repo, sib_match = _search_sibling(gh, sib, keywords, locations.notes)
        if sib_match:
            _add(ArtifactResult(
                kind=kind, outcome="sibling-repo",
                evidence=f"{sib.full_name}:{sib_match}",
                date=_dir_last_commit_date(sib_repo, sib_match, locations.notes)))
        elif sib_repo is not None:
            _add(ArtifactResult(
                kind=kind, outcome="sibling-repo", evidence=sib.full_name,
                date=sib.pushed_at,
                note="specific artifact path not located; repo matches a "
                     "documentation-repo naming convention"))

    if locations.homepage:
        _add(ArtifactResult(kind=kind, outcome="doc-site", evidence=locations.homepage,
                            note="declared homepage; existence of a page for this "
                                 "kind not verified"))

    # Off-site links the README itself offers. Frequently the only pointer at a
    # documentation site that is not a repository at all.
    for link in locations.readme_links:
        _add(ArtifactResult(kind=kind, outcome="doc-site", evidence=link.url,
                            note=f"linked from README as '{link.text[:60]}'"))

    if not out:
        out.append(ArtifactResult(kind=kind, outcome="not-found", evidence=""))
    return out


def find_artifact(owner_repo: str, kind: str, client: Github | None = None,
                  locations: DocLocations | None = None) -> ArtifactResult:
    """Resolve one kind of documentation artifact to a LOCATION.

    Exactly four outcomes — `in-repo`, `sibling-repo`, `doc-site`,
    `not-found` — and only `not-found` is an absence (design §5.5b, "The
    expectation set — and reporting *where*, not *whether*"). This is the
    fix for the failure mode finding 68 names: naively checking only
    `kubernetes/kubernetes`'s own `docs/` for architecture content returns
    "not found" (in fact it returns a tombstone) when the true answer is
    "in `kubernetes/website`, pushed today" — a boolean checklist has no
    way to say that, a location does.

    Search order, cheapest and most-specific first:
      1. Real (non-tombstone) in-repo doc dirs, shallow keyword search.
      2. The repo root, for kinds like `readme` and `changelog` that
         conventionally live there rather than under a doc dir.
      3. Sibling documentation repos: a specific-file match if one is
         found, else — if a sibling exists at all — a generic pointer at
         that sibling repo (still `sibling-repo`, not `not-found`; the
         repo's existence is itself the evidence for "docs moved here").
      4. The declared homepage, as `doc-site` (existence not verified by
         content fetch — a homepage field is not itself proof of a page
         for this specific kind, hence no date on this outcome).
      5. `not-found` — a genuine absence, only once 1–4 have all failed.
    """
    gh = client or _make_client()
    keywords = _ARTIFACT_KEYWORDS.get(kind, [kind.lower()])
    # Resolution is the expensive half — sibling probes, contents listings, the
    # README — and it is IDENTICAL for every artifact kind. Re-running it per
    # kind made a 4-kind report do the same ~6s of work five times: measured at
    # 57s per repo against 6s for resolution alone, which is the gap the
    # presentation session saw (41-58s) and I did not, because I had timed
    # `resolve_doc_locations` rather than the step that calls it.
    #
    # Callers resolving several kinds should resolve once and pass it in;
    # `expectations.build_report` does.
    if locations is None:
        locations = resolve_doc_locations(owner_repo, client=gh)

    repo = _get_repo(gh, owner_repo, locations.notes)
    if repo is None:
        return ArtifactResult(kind=kind, outcome="not-found", evidence="",
                               note="repo unreachable; " + "; ".join(locations.notes))

    # 1. Real in-repo doc dirs.
    for d in locations.real_doc_dirs:
        match = _find_in_dir(repo, d.path, keywords, locations.notes)
        if match:
            date = _dir_last_commit_date(repo, match, locations.notes)
            return ArtifactResult(kind=kind, outcome="in-repo", evidence=match, date=date)

    # 2. Repo root (README/CHANGELOG live at top level, not under docs/).
    root_match = _find_root(repo, keywords, locations.notes)
    if root_match:
        date = _dir_last_commit_date(repo, root_match, locations.notes)
        return ArtifactResult(kind=kind, outcome="in-repo", evidence=root_match, date=date)

    # 3. Sibling doc repos.
    if locations.sibling_repos:
        best = locations.sibling_repos[0]
        sib_repo, sib_match = _search_sibling(gh, best, keywords, locations.notes)
        if sib_match:
            sib_date = _dir_last_commit_date(sib_repo, sib_match, locations.notes)
            return ArtifactResult(
                kind=kind, outcome="sibling-repo",
                evidence=f"{best.full_name}:{sib_match}", date=sib_date,
            )
        # No specific file located, but a documentation-shaped sibling
        # repo exists — its existence is itself the evidence (finding 68:
        # this is exactly the Kubernetes case, where a shallow search of
        # `kubernetes/website`'s root won't reliably land on one filename,
        # but reporting "not-found" would repeat the naive metric's error).
        return ArtifactResult(
            kind=kind, outcome="sibling-repo", evidence=best.full_name,
            date=best.pushed_at,
            note="specific artifact path not located; repo matches a documentation-repo naming convention",
        )

    # 4. Declared homepage.
    if locations.homepage:
        return ArtifactResult(
            kind=kind, outcome="doc-site", evidence=locations.homepage,
            note="homepage declared; existence of this specific artifact not verified by content fetch",
        )

    # 5. Genuine absence.
    return ArtifactResult(kind=kind, outcome="not-found", evidence="")
