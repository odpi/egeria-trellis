"""Repo-role classification — what does this repository *represent*
(library, application, tutorial, ...) before asking what its architecture
is (design §5.5b, "What the repo *is* — classification before analysis").

Why this module exists. Spike finding 58 ran architecture recovery against
`workshops`, OpenLineage's tutorial materials, and got 45 proposed
components — 44 of them manufactured from compose files that *demonstrate*
a deployment rather than declaring one. Finding 60 records the fix a human
applied by hand: reading the README, which said the repo's intent was a
tutorial, settled it in two seconds. That is exactly the classification
this module automates — **not a new idea, a generalisation of one already
proven** (§5.5b's framing).

**It is a gate, not a weighting.** Per the maintainer (§5.5b): "If we
classify a repo as being a tutorial there is no point trying to discover
an architecture." A `tutorial`/`samples`/`documentation` primary role means
architecture recovery does not run at all — not runs-and-scores-low. This
module only classifies; the caller (not built here — deferred, see the
task boundary below) is what wires the gate.

**Cardinality: multi-valued with one primary.** `egeria-workspaces` is
genuinely deployment *and* tutorial; `trellis` is application *and*
library. `RepoRoleClassification.roles` is a ranked list; `.primary` is
`roles[0]` and is what drives the expectation set and the gate. The
others are retained as evidence, not discarded — a monorepo playing two
roles is a fact about the repo, not classifier noise.

**No score. Ordering is fine, grading is not.** §5.5a(c) and §5.5b are
explicit: a checklist becomes a score, and a score punishes deliberate
choices (a small stable library documenting lightly on purpose does not
"lose points" against a heavily-documented one). Every function in this
module ranks or gates categorically — README-stated intent outranks
inference, a fixed precedence breaks ties among structurally-inferred
roles — never by summing points into a number.

**Vocabulary (7 values) — a placeholder for an Egeria valid value set,
deliberately not a Python enum.** §5.5b's "Vocabulary check against
Egeria" ran `DeployedImplementationType` / `ResourceUse` / `Category` /
`ProjectStatus` and confirmed none of them encode "what kind of body of
work is this" — the adjacent slot, `deployedImplementationType`, already
holds `"GitHub Repository"` (hosting technology; see
`surveyors/egeria_publisher.py:295`), and overloading it with role would
repeat the §4.1c "one field, two meanings" mistake. The design's
"Recommended shape" paragraph (§5.5b) calls for a new Egeria **valid
value set** — catalogable, queryable, extensible without a code change —
the same mechanism `ConfidenceLevel` already uses. That migration is a
separate Dr.Egeria/pyegeria task; **this module does not write to
Egeria** and these seven constants are the whole vocabulary until it
does. When that migration happens, these constants become the seed data
for the valid value set, not dead code to delete.

Graceful absence, never an exception — same posture as `doc_locations.py`:
no token, no network, and a rate limit all degrade to a result carrying a
note in `.notes`, never a raised exception. This module reuses
`doc_locations`'s client construction and repo-fetch helpers directly
rather than re-implementing them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from github import Github, GithubException

from resource_explorer.github.doc_locations import (
    DocLocations,
    _degraded,
    _get_repo,
    _make_client,
    resolve_doc_locations,
)

# ---------------------------------------------------------------------------
# Vocabulary (§5.5b "Vocabulary check against Egeria" + "Recommended shape")
# ---------------------------------------------------------------------------

ROLE_LIBRARY = "library"
ROLE_APPLICATION = "application"
ROLE_MIDDLEWARE = "middleware"
ROLE_TOOL = "tool"
ROLE_TUTORIAL = "tutorial"
ROLE_SAMPLES = "samples"
ROLE_DOCUMENTATION = "documentation"
#: The eighth role: the repository IS an open dataset (or says so), not software that happens to hold data. Added with
#: the open-data repository signal (dataset_descriptor.py); see `_dataset_evidence`.
ROLE_DATASET = "dataset"

# The eight values, order-insensitive. Kept as module-level constants
# (not an enum) precisely so a future migration to an Egeria valid value
# set is a data change, not a code change — see the module docstring.
ROLE_VALUES: tuple[str, ...] = (
    ROLE_LIBRARY, ROLE_APPLICATION, ROLE_MIDDLEWARE, ROLE_TOOL,
    ROLE_TUTORIAL, ROLE_SAMPLES, ROLE_DOCUMENTATION, ROLE_DATASET,
)

# Precedence used ONLY to pick a primary among roles that fired from
# structural inference (no README-stated intent). This is a disclosed,
# fixed tie-break order — not a score. Rationale, most-specific first:
#
#   1. documentation / tutorial / samples — these are "this is not
#      software to run" signals, and per the gate philosophy above they
#      should win primary whenever they fire structurally, the same way
#      README-stated intent wins when it fires explicitly.
#   2. middleware / application — a deployment artifact is concrete
#      evidence the repo ships a runnable thing; more specific than a
#      manifest file, which most source repos carry regardless of intent.
#   3. tool — entry points without deployment artifacts.
#   4. library — weakest/most-common signal (nearly every repo with code
#      has *a* manifest file), so it sits last in the tie-break.
#
# Every role that fires is still reported in `.roles`; this order affects
# ONLY which one lands at index 0 when more than one fires structurally.
_PRIMARY_PRECEDENCE: tuple[str, ...] = (
    ROLE_DOCUMENTATION, ROLE_TUTORIAL, ROLE_SAMPLES, ROLE_DATASET,
    ROLE_MIDDLEWARE, ROLE_APPLICATION, ROLE_TOOL, ROLE_LIBRARY,
)


# ---------------------------------------------------------------------------
# Output contract — mirrors doc_locations.py's shape
# ---------------------------------------------------------------------------

@dataclass
class RoleEvidence:
    """One concrete piece of evidence for one role: which signal fired,
    and the concrete detail (a path, a filename, or a quoted README
    line) — never a number."""
    signal: str
    detail: str


@dataclass
class RoleResult:
    """One role, ranked, carrying every signal that fired for it. A repo
    can (and often does) have several of these — §5.5b's `egeria-workspaces`
    (deployment + tutorial) and `trellis` (application + library) cases."""
    role: str
    evidence: list[RoleEvidence] = field(default_factory=list)

    def add(self, signal: str, detail: str) -> None:
        self.evidence.append(RoleEvidence(signal=signal, detail=detail))


@dataclass
class RepoRoleClassification:
    """Every role found for `owner/repo`, ranked, primary first. Deliberately
    a ranked bag of findings with their evidence, not a verdict with a score
    — §5.5a(c) point 4 / §5.5b: report the observations, do not grade them."""
    owner_repo: str
    roles: list[RoleResult] = field(default_factory=list)
    stated_intent: bool = False  # True iff primary came from README-stated intent, not inference
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> RoleResult | None:
        return self.roles[0] if self.roles else None

    def has_role(self, role: str) -> bool:
        return any(r.role == role for r in self.roles)


# ---------------------------------------------------------------------------
# Signal 1: README-stated intent (§5.2 step 0, finding 58/60)
# ---------------------------------------------------------------------------

# Sentence-shaped patterns, deliberately NOT bare keyword matching. Finding
# 60 names the failure mode directly: "'Workshop' and 'example' appear in
# the READMEs of real software... The judgement is about intent." Requiring
# a subject ("this repo/project") + a copula/verb near the noun is a cheap
# proxy for "this is a statement of what the thing IS", not an incidental
# mention. Each pattern captures the whole matched span as the quotable
# evidence line.
_SUBJECT = r"(?:this|the)\s+(?:repo|repository|project)"
_COPULA = r"(?:is|is intended as|serves as|contains|provides|holds)"

_README_INTENT_PATTERNS: dict[str, re.Pattern] = {
    # Order matters: this is also the precedence used when the README
    # states more than one intent (rare, but not impossible) — checked
    # top to bottom, first match wins per role-noun group below.
    ROLE_TUTORIAL: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(tutorial|workshop|walkthrough|training material)s?\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_SAMPLES: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(sample|samples|example project|demo|demonstration)s?\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_DOCUMENTATION: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,80}}"
        rf"\b(documentation site|docs site|documentation( for)?|website)\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_LIBRARY: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(library|sdk|client library|python package|npm package)\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_MIDDLEWARE: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(middleware|message broker|proxy|integration layer|service mesh)\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_TOOL: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(command[- ]line tool|cli tool|utility)\b[^.\n]*",
        re.IGNORECASE,
    ),
    ROLE_APPLICATION: re.compile(
        rf"{_SUBJECT}[^.\n]{{0,60}}\b{_COPULA}\b[^.\n]{{0,40}}"
        rf"\b(application|web service|platform|server)\b[^.\n]*",
        re.IGNORECASE,
    ),
}


def _readme_stated_intent(readme_text: str) -> list[tuple[str, str]]:
    """Pure function: scan README prose for a sentence stating the repo's
    own intent. Returns [(role, quoted_evidence_line), ...] — usually 0 or
    1 entries; more than one is reported rather than resolved here, since
    the module's job is to surface, not adjudicate. See finding 60 and
    §5.2 step 0: "prose docs stating what the thing is outrank inference —
    this is not invention, it is reading a human's statement about their
    own architecture."
    """
    if not readme_text:
        return []
    out: list[tuple[str, str]] = []
    for role, pattern in _README_INTENT_PATTERNS.items():
        m = pattern.search(readme_text)
        if m:
            out.append((role, m.group(0).strip()))
    return out


# ---------------------------------------------------------------------------
# Signal 2: deployment artifacts
# ---------------------------------------------------------------------------

_DEPLOYMENT_FILE_RE = re.compile(
    r"(^|/)(Dockerfile[^/]*|docker-compose[^/]*\.ya?ml|compose[^/]*\.ya?ml)$",
    re.IGNORECASE,
)
_DEPLOYMENT_DIR_PREFIXES = ("charts/", "helm/", "k8s/", "kubernetes/", "manifests/")

# Description/topic/README keywords that push a deployment-artifact hit
# toward `middleware` instead of the `application` default.
_MIDDLEWARE_KEYWORDS = (
    "middleware", "message broker", "message queue", "proxy", "gateway",
    "service mesh", "connector", "integration layer", "event bus",
)


def _deployment_artifacts(tree_paths: list[str]) -> list[str]:
    """Pure function: paths in `tree_paths` that are deployment artifacts
    — Dockerfiles, compose files, Helm/k8s manifest directories. Path-based
    only (no content fetch for every YAML file) to stay Discovery-tier
    cheap, matching §5.6's cost posture."""
    out: list[str] = []
    for path in tree_paths:
        if _DEPLOYMENT_FILE_RE.search(path):
            out.append(path)
            continue
        low = path.lower()
        for prefix in _DEPLOYMENT_DIR_PREFIXES:
            if low == prefix.rstrip("/") or low.startswith(prefix):
                out.append(path)
                break
    return out


def _looks_middleware(description: str | None, topics: list[str], readme_text: str | None) -> str | None:
    """Return the matched keyword if the repo's own words suggest
    middleware rather than a generic application, else None."""
    haystack = " ".join([
        (description or ""),
        " ".join(topics or []),
        (readme_text or "")[:2000],  # first ~2000 chars is plenty; avoid scanning a whole README twice
    ]).lower()
    for kw in _MIDDLEWARE_KEYWORDS:
        if kw in haystack:
            return kw
    return None


# ---------------------------------------------------------------------------
# Signal 3: published package manifest -> library
# ---------------------------------------------------------------------------

# go.mod/pom.xml/Cargo.toml are the task spec's literal list; build.gradle(.kts)
# is added on top of it — live-verified against odpi/egeria (this session):
# a Gradle-built JVM monorepo with neither pom.xml nor go.mod would otherwise
# get zero package-manifest evidence at all, which is a gap in coverage of
# signal 3's intent ("published package manifest"), not a vocabulary change.
_MANIFEST_BARE_PRESENCE = ("go.mod", "pom.xml", "Cargo.toml", "build.gradle", "build.gradle.kts")


def _manifest_evidence(gh: Github, repo, tree_paths: list[str], notes: list[str]) -> list[str]:
    """Evidence strings for a published-package manifest. `go.mod` /
    `pom.xml` / `Cargo.toml` count on bare presence (per task spec); a
    `pyproject.toml` must actually declare `[build-system]` and a
    `package.json` must declare both `name` and `version` — otherwise
    either could just be tooling config (e.g. a bare `[tool.ruff]`
    pyproject with no packaging section at all)."""
    names = {p.rsplit("/", 1)[-1]: p for p in tree_paths if "/" not in p}
    evidence: list[str] = []

    for fname in _MANIFEST_BARE_PRESENCE:
        if fname in names:
            evidence.append(fname)

    if "pyproject.toml" in names:
        text = _fetch_text(gh, repo, "pyproject.toml", notes)
        if text and "[build-system]" in text:
            evidence.append("pyproject.toml ([build-system])")

    if "package.json" in names:
        text = _fetch_text(gh, repo, "package.json", notes)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("name") and data.get("version"):
                    evidence.append("package.json (name+version)")
            except (ValueError, TypeError) as exc:
                _degraded(notes, exc, "could not parse package.json")

    return evidence


# ---------------------------------------------------------------------------
# Signal 4: entry points -> tool
# ---------------------------------------------------------------------------

def _entry_point_evidence(gh: Github, repo, tree_paths: list[str], notes: list[str]) -> list[str]:
    """Console-script declarations, or conventional `cmd/`/`bin/`
    directories — the repo produces something you run, not just something
    you import."""
    evidence: list[str] = []
    top_dirs = {p.split("/", 1)[0] for p in tree_paths if "/" in p}
    for d in ("cmd", "bin"):
        if d in top_dirs:
            evidence.append(f"{d}/")

    names = {p.rsplit("/", 1)[-1]: p for p in tree_paths if "/" not in p}
    if "pyproject.toml" in names:
        text = _fetch_text(gh, repo, "pyproject.toml", notes)
        if text and ("[project.scripts]" in text or "[tool.poetry.scripts]" in text):
            evidence.append("pyproject.toml ([project.scripts])")

    return evidence


# ---------------------------------------------------------------------------
# Signal 5: tutorial/sample markers
# ---------------------------------------------------------------------------

_TUTORIAL_DIR_NAMES = {"tutorials", "workshops", "labs"}
_SAMPLES_DIR_NAMES = {"samples", "examples"}
_NOTEBOOK_DIR_NAMES = {"notebooks"}  # counted toward tutorial, alongside the ratio check

# PRESENCE, not proportion. This was a *ratio* threshold (>= 20% of tracked
# files must be .ipynb), and that is wrong for two independent reasons found
# live against `odpi/egeria-workspaces`:
#
#  1. **It contradicts the multi-valued decision.** Roles are multi-valued with
#     one primary (design §5.5b), so a repo does not have to be *mostly* a
#     tutorial to *have* a tutorial role. A proportion test silently assumes
#     single-role classification, and structurally prevents any large repo from
#     carrying a secondary tutorial role at all.
#  2. **It is an invented cut-off**, which this project rejects on principle —
#     the same reason Newman modularity was refused as a threshold (§5.5) and
#     the reason ranking reports a fraction rather than a cut-off (finding 77).
#
# `egeria-workspaces` carries 37 real Jupyter notebooks — unambiguously tutorial
# material — which is 0.6% of its 6200 files and therefore invisible to a ratio.
# An absolute presence floor is a different kind of test: 37 notebooks is 37
# notebooks however much other code sits beside them. The ratio is still
# REPORTED as evidence, so a reader can weigh it; it just no longer gates.
_IPYNB_MIN_FILES = 5


def _tutorial_sample_dir_evidence(tree_paths: list[str]) -> dict[str, list[str]]:
    """Pure function: TOP-LEVEL directory names matching tutorial/sample
    conventions. Deliberately checks the directory's *name*, not its
    content — finding 60 warns path heuristics alone misfire, which is
    why this signal never stands alone as the sole basis for
    `tutorial`/`samples` when a README states otherwise (README intent is
    checked first and dominates).

    Deliberately top-level only, not "anywhere in the tree": a nested
    `documentation/examples/` two levels into a large codebase (live-verified
    against prometheus/prometheus, this session) is internal doc content,
    not evidence the repo's own role is samples. A dedicated top-level
    `examples/` is a much stronger, more conventional signal.
    """
    out: dict[str, list[str]] = {ROLE_TUTORIAL: [], ROLE_SAMPLES: []}
    seen_dirs: set[str] = set()
    for path in tree_paths:
        parts = path.split("/")
        if len(parts) < 2:
            continue  # a file at repo root, not inside any directory
        dirname = parts[0].lower()
        key = parts[0]
        if key in seen_dirs:
            continue
        if dirname in _TUTORIAL_DIR_NAMES or dirname in _NOTEBOOK_DIR_NAMES:
            out[ROLE_TUTORIAL].append(key + "/")
            seen_dirs.add(key)
        elif dirname in _SAMPLES_DIR_NAMES:
            out[ROLE_SAMPLES].append(key + "/")
            seen_dirs.add(key)
    return out


def _ipynb_ratio_evidence(tree_paths: list[str]) -> str | None:
    """Pure function: high .ipynb ratio is deterministic tutorial evidence
    that needs no README (spike finding 60: "a deterministic signal worth
    pairing with it... would flag the case cheaply even where no README
    states intent")."""
    ipynb_count = sum(1 for p in tree_paths if p.lower().endswith(".ipynb"))
    if ipynb_count < _IPYNB_MIN_FILES:
        return None
    ratio = ipynb_count / len(tree_paths) if tree_paths else 0.0
    return f"{ipynb_count} .ipynb files ({ratio:.1%} of {len(tree_paths)} tracked)"


# ---------------------------------------------------------------------------
# Signal 6: documentation dominance
# ---------------------------------------------------------------------------

_DOC_NAME_SUFFIXES = ("-docs", "-doc", ".github.io")
_DOC_EXACT_NAMES = {"docs", "documentation", "website", "site"}
_DOC_CONTENT_EXTENSIONS = (".md", ".mdx", ".rst", ".adoc", ".html")
_DOC_SITE_CONFIGS = (
    "mkdocs.yml", "mkdocs.yaml", "docusaurus.config.js", "docusaurus.config.ts",
    "_config.yml", "hugo.toml", "config.toml", "book.toml",
)
_DOC_CONTENT_RATIO_THRESHOLD = 0.7
_DOC_CONTENT_MIN_FILES = 5


def _name_matches_doc_convention(repo_name: str) -> bool:
    """Pure function: does the repo's OWN name follow a documentation-repo
    naming convention — `kubernetes/website`, `odpi/egeria-docs`,
    `milvus-io/milvus-docs`, `something.github.io`. Same convention
    `doc_locations._SIBLING_NAME_PATTERNS` checks for a sibling, applied
    to the repo's own name instead."""
    low = repo_name.strip().lower()
    if low in _DOC_EXACT_NAMES:
        return True
    return any(low.endswith(suffix) for suffix in _DOC_NAME_SUFFIXES)


# A static-site generator config gets its OWN signal name rather than being
# folded into `documentation-dominance` prose. Measured 2026-08-24 on the
# 60-repo corpus: `OpenLineage/openlineage-site` is 71% doc-shaped files with
# `docusaurus.config.js` at root, and the *same* classification finds a
# `package.json` — Docusaurus's own. The recovery gate read that manifest as
# "there is an architecture here" and overrode the skip. The generator config
# is what distinguishes a manifest that belongs to the documented software from
# one that belongs to the machinery rendering the documentation, so the gate
# needs it as data, not as a substring of an evidence sentence.
SIGNAL_DOC_SITE_GENERATOR = "doc-site-generator"


def _documentation_dominance_evidence(repo_name: str, tree_paths: list[str]) -> list[tuple[str, str]]:
    """Pure function combining the name-convention check and a content-
    ratio check (mostly markdown/rst/html, or a static-site generator
    config at root) into `(signal, detail)` pairs.

    Returns pairs rather than bare strings so the generator config keeps its
    own signal name — see `SIGNAL_DOC_SITE_GENERATOR` above."""
    evidence: list[tuple[str, str]] = []
    if _name_matches_doc_convention(repo_name):
        evidence.append(("documentation-dominance",
                         f"repo name '{repo_name}' matches documentation-repo naming convention"))

    names_at_root = {p for p in tree_paths if "/" not in p}
    for cfg in _DOC_SITE_CONFIGS:
        if cfg in names_at_root:
            evidence.append((SIGNAL_DOC_SITE_GENERATOR,
                             f"static-site generator config at root: {cfg}"))
            break  # one is enough evidence; avoid listing every generator checked

    if len(tree_paths) >= _DOC_CONTENT_MIN_FILES:
        doc_count = sum(1 for p in tree_paths if p.lower().endswith(_DOC_CONTENT_EXTENSIONS))
        ratio = doc_count / len(tree_paths)
        if ratio >= _DOC_CONTENT_RATIO_THRESHOLD:
            evidence.append(("documentation-dominance",
                             f"{doc_count}/{len(tree_paths)} files are doc-shaped content ({ratio:.0%})"))

    return evidence


# ---------------------------------------------------------------------------
# Signal 7: absence as evidence, both directions (§5.5b, trellis.md)
# ---------------------------------------------------------------------------

def _apply_absence_evidence(roles: dict[str, RoleResult], has_deployment: bool) -> None:
    """Mutates `roles` in place, adding a note to whichever roles are
    present. No deployment artifacts is CONFIRMATION for `library`
    ("trellis.md records exactly that: no Dockerfiles, no compose files...
    it has no deployment perspective at all") and a point AGAINST
    `application`/`middleware` when those were inferred from something
    other than a deployment artifact (e.g. an entry point alone). Neither
    direction removes a role that already has other evidence — this is a
    note added alongside it, never a veto."""
    if has_deployment:
        return
    if ROLE_LIBRARY in roles:
        roles[ROLE_LIBRARY].add(
            "absence-as-evidence",
            "no Dockerfile/compose/charts/helm/k8s manifests found — "
            "confirms library role (§5.5b, trellis.md)",
        )
    for role in (ROLE_APPLICATION, ROLE_MIDDLEWARE):
        if role in roles and not any(e.signal == "deployment-artifacts" for e in roles[role].evidence):
            roles[role].add(
                "absence-as-evidence",
                f"no deployment artifacts found despite {role} signal from another source — "
                f"weakens {role} classification (§5.5b)",
            )


# ---------------------------------------------------------------------------
# Signal 8: the repository is a dataset
# ---------------------------------------------------------------------------

_DESCRIPTOR_FILE_NAMES = ("datapackage.json", "croissant.json", "metadata.json", "CITATION.cff")
_BUILD_TOOLING_NAMES = {"pyproject.toml", "setup.py", "setup.cfg", "package.json", "Makefile", "CMakeLists.txt",
                        "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts"}
_DATA_BYTES_SHARE = 0.5


def _data_byte_share(entries: list[tuple[str, int | None]]) -> tuple[int, int] | None:
    """(bytes of recognised data files, bytes of every file) over the entries whose size is known, or None when no
    size is known (a tree that states none says nothing about proportion: absent, not 0%)."""
    from resource_explorer.surveyors.sub_surveyors.data_profiler import _DATA_EXTENSIONS

    sized = [(p, n) for p, n in entries if n is not None]
    if not sized:
        return None
    total = sum(n for _, n in sized)
    data = sum(n for p, n in sized if p.rsplit(".", 1)[-1].lower() in _DATA_EXTENSIONS and "." in p.rsplit("/", 1)[-1])
    return data, total


def _dataset_evidence(gh: Github, repo, entries: list[tuple[str, int | None]], readme_text: str | None,
                      has_deployment: bool, notes: list[str]) -> list[tuple[str, str]]:
    """(signal, detail) pairs supporting `dataset`: a descriptor that names a dataset (datapackage.json, a Croissant
    file whose @type is Dataset, a CITATION.cff with `type: dataset`, a Hugging Face dataset card), or data files at
    or above half of the tree's bytes with no build tooling. A README DOI alone does not (software is archived on
    Zenodo too). Ranked like the other roles; no score."""
    from resource_explorer.surveyors.sub_surveyors import dataset_descriptor as dd

    out: list[tuple[str, str]] = []
    paths = [p for p, _ in entries]
    parsers = {"datapackage.json": dd.parse_datapackage, "croissant.json": dd.parse_croissant,
               "metadata.json": dd.parse_croissant, "CITATION.cff": dd.parse_citation_cff}
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if name not in _DESCRIPTOR_FILE_NAMES or path.count("/") >= dd._MAX_DEPTH:
            continue
        text = _fetch_text(gh, repo, path, notes)
        row = parsers[name](text, path) if text else None
        if row and row["names_a_dataset"]:
            ident = row["identifier"] if row["identifier"] != "not_reported" else "no identifier stated"
            out.append(("dataset-descriptor", f"{path} ({ident})"))
    card = dd.parse_hf_card(readme_text, "README.md") if readme_text else None
    if card:
        out.append(("huggingface-dataset-card", "README.md front matter: license + dataset_info"))
    share = _data_byte_share(entries)
    names = {p.rsplit("/", 1)[-1] for p in paths}
    if share and share[1] and share[0] / share[1] >= _DATA_BYTES_SHARE and not has_deployment \
            and not (names & _BUILD_TOOLING_NAMES):
        out.append(("data-bytes-share", f"data files are {share[0]:,} of {share[1]:,} bytes "
                                        f"({share[0] / share[1]:.0%}), and no build tooling"))
    return out


# ---------------------------------------------------------------------------
# Network plumbing — thin, and every call independently guarded
# ---------------------------------------------------------------------------

def _fetch_text(gh: Github, repo, path: str, notes: list[str]) -> str | None:
    try:
        content = repo.get_contents(path)
        return content.decoded_content.decode("utf-8", errors="ignore")
    except GithubException as exc:
        if getattr(exc, "status", None) != 404:
            _degraded(notes, exc, f"could not fetch '{path}'")
        return None
    except Exception as exc:
        _degraded(notes, exc, f"could not fetch '{path}' (network)")
        return None


def _fetch_readme_text(repo, notes: list[str]) -> str | None:
    try:
        readme = repo.get_readme()
        return readme.decoded_content.decode("utf-8", errors="ignore")
    except GithubException as exc:
        if getattr(exc, "status", None) != 404:
            _degraded(notes, exc, "could not fetch README")
        return None
    except Exception as exc:
        _degraded(notes, exc, "could not fetch README (network)")
        return None


def _fetch_tree_entries(repo, notes: list[str]) -> list[tuple[str, int | None]]:
    """Full recursive file-tree listing, one API call in the common case, as (path, size) with the size the tree
    states or None when it does not give an integer. On truncation (very large repos) returns the partial list and
    records a note rather than doing a full manual walk — a partial-but-honest result over every other signal in
    this module's graceful-degradation posture, not a crash."""
    try:
        tree = repo.get_git_tree(repo.default_branch, recursive=True)
    except Exception as exc:
        _degraded(notes, exc, "could not fetch file tree")
        return []
    entries = []
    for e in tree.tree:
        if e.type != "blob":
            continue
        size = getattr(e, "size", None)
        entries.append((e.path, size if isinstance(size, int) and not isinstance(size, bool) else None))
    if getattr(tree, "truncated", False):
        notes.append(
            f"file tree truncated at {len(entries)} entries; deployment/manifest/"
            f"dominance signals may be incomplete for this large a repo"
        )
    return entries


def _fetch_tree_paths(repo, notes: list[str]) -> list[str]:
    return [p for p, _ in _fetch_tree_entries(repo, notes)]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def classify_repo_role(owner_repo: str, client: Github | None = None,
                       locations: DocLocations | None = None) -> RepoRoleClassification:
    """Classify `owner/repo`'s role(s). README-stated intent (signal 1)
    dominates and, when present, decides the primary outright — everything
    else is retained as evidence, never discarded (§5.5b: "outrank
    inference... this is not invention"). Absent a stated intent, the
    primary is chosen from whichever structural signals fired, broken by
    `_PRIMARY_PRECEDENCE` (a disclosed order, not a score).

    Graceful absence: an unreachable repo (no token, no network, rate
    limited) returns an empty `.roles` list with an explanatory note,
    never an exception — same contract as `doc_locations.resolve_doc_locations`.
    """
    gh = client or _make_client()
    notes: list[str] = []
    repo = _get_repo(gh, owner_repo, notes)
    if repo is None:
        return RepoRoleClassification(owner_repo=owner_repo, notes=notes)

    readme_text = _fetch_readme_text(repo, notes)
    tree_entries = _fetch_tree_entries(repo, notes)
    tree_paths = [p for p, _ in tree_entries]

    # Reuse doc_locations for doc-dir/sibling/homepage signals rather than
    # re-resolving them — a real (non-tombstone) in-repo doc dir, or a
    # documentation-repo-shaped sibling, corroborates `documentation`.
    # Accept a pre-resolved DocLocations: resolution is the expensive half and
    # `expectations.build_report` already has one, so re-resolving here repeated
    # ~6s of identical work per report.
    if locations is None:
        locations = resolve_doc_locations(owner_repo, client=gh)
    notes.extend(locations.notes)

    roles: dict[str, RoleResult] = {}

    def _role(name: str) -> RoleResult:
        return roles.setdefault(name, RoleResult(role=name))

    # --- Signal 1: README-stated intent ------------------------------
    stated = _readme_stated_intent(readme_text or "")
    for role, line in stated:
        _role(role).add("readme-stated-intent", line)

    # --- Signal 2: deployment artifacts -------------------------------
    deployment_paths = _deployment_artifacts(tree_paths)
    if deployment_paths:
        middleware_kw = _looks_middleware(repo.description, _safe_topics(repo, notes), readme_text)
        target = ROLE_MIDDLEWARE if middleware_kw else ROLE_APPLICATION
        for p in deployment_paths[:10]:  # cap evidence volume; presence is the point, not the count
            _role(target).add("deployment-artifacts", p)
        if middleware_kw:
            _role(target).add("deployment-artifacts", f"middleware keyword matched: '{middleware_kw}'")

    # --- Signal 3: published package manifest -> library --------------
    for detail in _manifest_evidence(gh, repo, tree_paths, notes):
        _role(ROLE_LIBRARY).add("package-manifest", detail)

    # --- Signal 4: entry points -> tool --------------------------------
    for detail in _entry_point_evidence(gh, repo, tree_paths, notes):
        _role(ROLE_TOOL).add("entry-points", detail)

    # --- Signal 5: tutorial/sample directory markers -------------------
    dir_hits = _tutorial_sample_dir_evidence(tree_paths)
    for role, paths in dir_hits.items():
        for p in paths:
            _role(role).add("tutorial-sample-dirs", p)
    ipynb_evidence = _ipynb_ratio_evidence(tree_paths)
    if ipynb_evidence:
        _role(ROLE_TUTORIAL).add("ipynb-ratio", ipynb_evidence)

    # --- Signal 6: documentation dominance ------------------------------
    for signal, detail in _documentation_dominance_evidence(repo.name, tree_paths):
        _role(ROLE_DOCUMENTATION).add(signal, detail)
    # Deliberately NOT: "a real (non-tombstone) docs/ dir exists" as its own
    # documentation-role trigger. Live-verified against milvus-io/milvus
    # (Discovery finding, this session): almost every substantial software
    # repo has *some* docs/ directory — that is normal maintenance, not
    # evidence the REPO's role is documentation. Reusing doc_locations here
    # is still worthwhile (see `locations.notes` below and the `real_doc_dirs`
    # note), but as informational context, not as a vote toward the role.
    if locations.real_doc_dirs and not roles.get(ROLE_DOCUMENTATION):
        names = ", ".join(d.path for d in locations.real_doc_dirs)
        notes.append(
            f"has real (non-tombstone) in-repo doc dir(s): {names} — "
            f"not treated as documentation-role evidence on its own (§5.5b); "
            f"a docs/ subdirectory is normal for a non-documentation repo"
        )

    # --- Signal 8: the repository is a dataset ---------------------------
    for signal, detail in _dataset_evidence(gh, repo, tree_entries, readme_text, bool(deployment_paths), notes):
        _role(ROLE_DATASET).add(signal, detail)

    # --- Signal 7: absence as evidence, both directions -----------------
    _apply_absence_evidence(roles, has_deployment=bool(deployment_paths))

    if not roles:
        notes.append(f"no role signals found for '{owner_repo}' — classification is empty")
        return RepoRoleClassification(owner_repo=owner_repo, notes=notes)

    stated_roles = [r for r, _ in stated if r in roles]
    if stated_roles:
        ranked = [r for r in _PRIMARY_PRECEDENCE if r in stated_roles]
        ranked += [r for r in _PRIMARY_PRECEDENCE if r in roles and r not in ranked]
        stated_flag = True
        notes.append(
            "README states its own intent; that role is primary and overrides "
            "structural inference (§5.5b, spike finding 60)"
        )
    else:
        ranked = [r for r in _PRIMARY_PRECEDENCE if r in roles]
        stated_flag = False

    return RepoRoleClassification(
        owner_repo=owner_repo,
        roles=[roles[r] for r in ranked],
        stated_intent=stated_flag,
        notes=notes,
    )


def _safe_topics(repo, notes: list[str]) -> list[str]:
    try:
        return repo.get_topics()
    except Exception as exc:
        _degraded(notes, exc, "could not fetch topics")
        return []
