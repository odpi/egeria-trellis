"""Where is a repo's homepage, and where is its documentation? Pure logic.

Scouting needs two cheap answers, not an analysis of the documentation:

* the **homepage** — the project's own site (or, failing that, its org's);
* the **documentation** — the place the docs live — and whether it answers.

They are different things and one field cannot hold both. GitHub's `homepage`
field is very often the vendor's marketing site (DataKitchen's dataops-testgen
declares `https://datakitchen.io`), while the packaging manifest says in so many
words `Documentation = "https://docs.datakitchen.io/testgen/what-is-testgen/"`.
The old tiered finder stopped at GitHub's field and never looked.

So this collects a candidate from EVERY source and decides afterwards, recording
for each where it came from. No I/O in here: the caller hands over file texts,
which keeps every judgement testable without a network.
"""
from __future__ import annotations

import configparser
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ── what is never "the project's site" ─────────────────────────────────────

# Where the code already lives. A repo whose declared homepage is its own forge
# page tells us nothing new.
CODE_HOSTS = {"github.com", "www.github.com", "gitlab.com", "bitbucket.org"}

# Tooling/vendor sites that appear in READMEs as attribution and are never the
# project's own site. Found the hard way: unitycatalog-python's README yielded
# stainlessapi.com, its SDK generator.
VENDOR_HOSTS = {
    "stainlessapi.com", "www.stainlessapi.com", "speakeasyapi.dev",
    "www.speakeasyapi.dev", "openapi-generator.tech", "swagger.io",
    "readthedocs.org", "readthedocs.com", "netlify.com", "vercel.com",
    "opensource.org", "creativecommons.org", "contributor-covenant.org",
    "semver.org", "keepachangelog.com", "conventionalcommits.org",
}

BADGE_HOSTS = {
    "img.shields.io", "shields.io", "badge.fury.io", "travis-ci.org",
    "travis-ci.com", "codecov.io", "coveralls.io", "circleci.com",
    "app.codacy.com", "sonarcloud.io", "badgen.net", "anaconda.org",
}

# Registrars' and templates' stand-in domains, plus hosts that can never be a
# public site. A leftover `Homepage = "https://example.com"` in pyproject is the
# real case (testgen).
_PLACEHOLDER_SUFFIXES = ("example.com", "example.org", "example.net", "example.edu")
_PLACEHOLDER_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "example", "yourdomain.com",
                      "your-domain.com", "yoursite.com", "foo.bar", "foo.com", "domain.com"}

#: manifest keys that explicitly NAME the documentation, lower-cased.
EXPLICIT_DOC_KEYS = ("documentation", "docs", "doc", "documentations", "user guide", "userguide")

_MD_LINK = re.compile(r"(!?)\[[^\]]*\]\(\s*(<?)(https?://[^\s)>]+)\2\s*[^)]*\)")
_MD_REF = re.compile(r"^\s*\[[^\]]+\]:\s*(https?://\S+)", re.MULTILINE)
_HTML_HREF = re.compile(r"<a\s[^>]*href=[\"'](https?://[^\"']+)[\"']", re.IGNORECASE)
_RST_LINK = re.compile(r"`[^`<]*<(https?://[^>\s]+)>`_{1,2}")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def is_placeholder(url: str) -> bool:
    h = host_of(url)
    if not h:
        return False
    if h in _PLACEHOLDER_HOSTS:
        return True
    bare = h.removeprefix("www.")
    return any(bare == s or bare.endswith("." + s) for s in _PLACEHOLDER_SUFFIXES)


def reject_reason(url: str, repo_host: str = "") -> str:
    """Why this URL can never be the project's site, or "" when it can be."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return "not an http(s) URL"
    h = host_of(url)
    if not h:
        return "no host"
    if is_placeholder(url):
        return "placeholder"
    if h in BADGE_HOSTS:
        return "badge host"
    if h in CODE_HOSTS or (repo_host and h == repo_host):
        return "code host"
    if h in VENDOR_HOSTS:
        return "attribution host"
    from resource_explorer.ingestion.site_discovery import is_non_doc_host
    if is_non_doc_host(url):
        return "registry or hub"
    return ""


def looks_like_docs(url: str) -> bool:
    """Cheap shape test: a docs subdomain, a Read-the-Docs style host, or a
    /docs/ path. Used to let a docs-shaped URL found under another label still
    answer for the documentation."""
    p = urlparse(url)
    h = (p.hostname or "").lower()
    if h.startswith(("docs.", "doc.", "documentation.", "wiki.")):
        return True
    if h.endswith((".readthedocs.io", ".readthedocs.org", ".gitbook.io")):
        return True
    path = (p.path or "").lower()
    return any(seg in path.split("/") for seg in ("docs", "doc", "documentation", "wiki"))


# ── candidates ─────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    url: str
    source: str            # human-readable: "pyproject.toml [project.urls] Documentation"
    source_key: str        # stable key, e.g. "pyproject_urls"
    role: str              # "docs_explicit" | "homepage" | "readme" | "github" | "org"
    confidence: int = 90
    rejected: str = ""     # reason, when filtered out (kept so the report can show it)

    def as_dict(self) -> dict:
        d = {"url": self.url, "source": self.source, "source_key": self.source_key,
             "role": self.role, "confidence": self.confidence}
        if self.rejected:
            d["rejected"] = self.rejected
        return d


def _lowered(mapping) -> dict:
    return {str(k).lower().strip(): (str(k), str(v).strip()) for k, v in (mapping or {}).items()
            if isinstance(v, (str, int, float))}


def _from_pyproject(text: str) -> list[Candidate]:
    import tomllib
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    out: list[Candidate] = []
    urls = (data.get("project", {}) or {}).get("urls", {}) or {}
    # Poetry keeps them beside the name instead.
    poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
    for label, section in (("[project.urls]", urls), ("[tool.poetry]", {
            k: poetry[k] for k in ("homepage", "documentation", "repository") if k in poetry})):
        for low, (orig, val) in _lowered(section).items():
            if not val:
                continue
            where = f"pyproject.toml {label} {orig}"
            if low in EXPLICIT_DOC_KEYS or low.startswith("doc"):
                out.append(Candidate(val, where, "pyproject_urls", "docs_explicit", 95))
            elif low in ("homepage", "home", "website", "home-page"):
                out.append(Candidate(val, where, "pyproject_urls", "homepage", 90))
    return out


def _from_package_json(text: str) -> list[Candidate]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    hp = data.get("homepage")
    if isinstance(hp, str) and hp.strip():
        return [Candidate(hp.strip(), 'package.json "homepage"', "package_json", "homepage", 90)]
    return []


def _from_cargo(text: str) -> list[Candidate]:
    import tomllib
    try:
        pkg = tomllib.loads(text).get("package", {}) or {}
    except Exception:
        return []
    out = []
    if str(pkg.get("documentation") or "").strip():
        out.append(Candidate(str(pkg["documentation"]).strip(), "Cargo.toml [package] documentation",
                             "cargo_toml", "docs_explicit", 95))
    if str(pkg.get("homepage") or "").strip():
        out.append(Candidate(str(pkg["homepage"]).strip(), "Cargo.toml [package] homepage",
                             "cargo_toml", "homepage", 90))
    return out


def _from_setup_cfg(text: str) -> list[Candidate]:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(text)
    except Exception:
        return []
    out = []
    v = (cp.get("metadata", "url", fallback="") or "").strip()
    if v:
        out.append(Candidate(v, "setup.cfg [metadata] url", "setup_cfg", "homepage", 85))
    for line in (cp.get("metadata", "project_urls", fallback="") or "").splitlines():
        if "=" in line:
            k, _, val = line.partition("=")
            k, val = k.strip(), val.strip()
            if val and (k.lower() in EXPLICIT_DOC_KEYS or k.lower().startswith("doc")):
                out.append(Candidate(val, f"setup.cfg [metadata] project_urls {k}", "setup_cfg",
                                     "docs_explicit", 90))
    return out


def _from_pom(text: str) -> list[Candidate]:
    import xml.etree.ElementTree as ET
    try:
        root_el = ET.fromstring(text)
    except Exception:
        return []

    def _local(el):
        return el.tag.rsplit("}", 1)[-1]

    out = []
    for el in root_el:
        if _local(el) == "url" and (el.text or "").strip():
            out.append(Candidate(el.text.strip(), "pom.xml <url>", "maven_pom", "homepage", 90))
    for el in root_el.iter():
        if _local(el) == "organization":
            for child in el:
                if _local(child) == "url" and (child.text or "").strip():
                    out.append(Candidate(child.text.strip(), "pom.xml <organization><url>",
                                         "maven_pom", "org", 80))
    return out


MANIFESTS = {
    "pyproject.toml": _from_pyproject,
    "package.json": _from_package_json,
    "Cargo.toml": _from_cargo,
    "setup.cfg": _from_setup_cfg,
    "pom.xml": _from_pom,
}
README_NAMES = ("README.md", "README.rst", "readme.md", "README", "README.txt")


def wanted_file_names(root_names) -> list[str]:
    """Which of a repo's root files are worth fetching: the manifests, and ONE
    README (the first of README_NAMES present). Nothing else is ever read."""
    present = set(root_names)
    out = [n for n in MANIFESTS if n in present]
    for r in README_NAMES:
        if r in present:
            out.append(r)
            break
    return out


def readme_links(text: str, repo_host: str = "") -> list[str]:
    """External, non-badge links in a README, in document order, repeats kept
    (the repeat count is the signal)."""
    links: list[str] = []
    for is_image, _q, url in _MD_LINK.findall(text):
        if is_image:                      # ![...](...) is a badge or screenshot
            continue
        links.append(url.rstrip(".,;"))
    links += [u.rstrip(".,;") for u in _MD_REF.findall(text)]
    links += [u.rstrip(".,;") for u in _HTML_HREF.findall(text)]
    links += [u.rstrip(".,;") for u in _RST_LINK.findall(text)]
    return [u for u in links if not reject_reason(u, repo_host)]


def name_tokens(github_url: str) -> list[str]:
    """org and repo names from the GitHub URL, normalised for host matching —
    repo first, it is the stronger signal."""
    parts = [p for p in urlparse(github_url).path.split("/") if p]
    out = []
    for p in parts[:2]:
        t = p.removesuffix(".git").lower().replace("-", "").replace("_", "")
        if len(t) >= 4:
            out.append(t)
    return list(reversed(out))


def _repo_words(github_url: str) -> list[str]:
    """Words of the REPO name (not the org), for project-vs-org labelling."""
    parts = [p for p in urlparse(github_url).path.split("/") if p]
    if len(parts) < 2:
        return []
    repo = parts[1].removesuffix(".git").lower()
    words = [w for w in re.split(r"[^a-z0-9]+", repo) if len(w) >= 4]
    joined = repo.replace("-", "").replace("_", "")
    return ([joined] if len(joined) >= 4 else []) + words


def _org_word(github_url: str) -> str:
    parts = [p for p in urlparse(github_url).path.split("/") if p]
    return parts[0].lower().replace("-", "").replace("_", "") if parts else ""


def site_scope(url: str, github_url: str) -> str:
    """'project' | 'org_or_vendor' | 'unknown'.

    A site is the PROJECT's when its host or path names the repo; it is the org
    or vendor's when its host echoes the org (or is a bare domain) and nothing
    in it names the repo. Cheap on purpose: a label to record, never a filter.
    """
    p = urlparse(url)
    host = (p.hostname or "").lower().replace("-", "")
    path = (p.path or "").lower().replace("-", "").replace("_", "")
    hay_host = host.replace(".", "")
    for w in _repo_words(github_url):
        w = w.replace("-", "").replace("_", "")
        if w and (w in hay_host or w in path):
            return "project"
    org = _org_word(github_url)
    bare_path = path.strip("/") == ""
    if org and len(org) >= 4 and org in hay_host:
        return "org_or_vendor" if bare_path else "unknown"
    return "unknown"


# ── the decision ───────────────────────────────────────────────────────────

@dataclass
class Discovery:
    homepage: Candidate | None = None
    docs: Candidate | None = None
    homepage_scope: str = ""
    docs_scope: str = ""
    candidates: list[Candidate] = field(default_factory=list)   # every one seen, rejected included

    @property
    def others_seen(self) -> list[dict]:
        return [c.as_dict() for c in self.candidates]


def _readme_candidates(text: str, github_url: str, readme_name: str) -> tuple[list[Candidate], Candidate | None]:
    """(homepage-ish candidates, best docs candidate) from README links."""
    repo_host = host_of(github_url)
    links = readme_links(text, repo_host)
    if not links:
        return [], None
    by_host = Counter(host_of(u) for u in links)
    tokens = name_tokens(github_url)

    def affinity(host: str) -> int:
        flat = host.replace("-", "").replace(".", "")
        return 1 if any(t and t in flat for t in tokens) else 0

    # Docs: the most-linked host that is docs-shaped; otherwise none.
    docs_hosts = [h for h in by_host if any(looks_like_docs(u) for u in links if host_of(u) == h)]
    docs_cand = None
    if docs_hosts:
        best = sorted(docs_hosts, key=lambda h: (-affinity(h), -by_host[h],
                                                 next(i for i, u in enumerate(links) if host_of(u) == h)))[0]
        urls = Counter(u for u in links if host_of(u) == best and looks_like_docs(u))
        top = urls.most_common(1)[0][0] if urls else next(u for u in links if host_of(u) == best)
        docs_cand = Candidate(top, f"{readme_name} ({by_host[best]} links to {best})",
                              "readme_link", "readme", 60)
    # Homepage-ish: name affinity first, then document order (old rule).
    ordered = []
    for t in tokens:
        for u in links:
            if t in host_of(u).replace("-", "").replace(".", "") and u not in ordered:
                ordered.append(u)
    for u in links:
        if u not in ordered:
            ordered.append(u)
    home = [Candidate(ordered[0], f"{readme_name} link", "readme_link", "readme", 60)]
    return home, docs_cand


def decide(github_url: str, github_homepage: str, files: dict[str, str]) -> Discovery:
    """Collect from every source, then choose. `files` maps root file name to text."""
    repo_host = host_of(github_url)
    d = Discovery()

    def add(c: Candidate) -> Candidate:
        c.rejected = reject_reason(c.url, repo_host)
        d.candidates.append(c)
        return c

    gh = (github_homepage or "").strip()
    if gh:
        add(Candidate(gh, "GitHub homepage field", "github_homepage", "github", 100))
    for name, parser in MANIFESTS.items():
        if name in files:
            for c in parser(files[name]):
                add(c)
    readme_name = next((n for n in README_NAMES if n in files), "")
    readme_home, readme_docs = [], None
    if readme_name:
        readme_home, readme_docs = _readme_candidates(files[readme_name], github_url, readme_name)
        for c in readme_home:
            add(c)
        if readme_docs and readme_docs.url not in {c.url for c in readme_home}:
            add(readme_docs)
        elif readme_docs:
            # same URL already listed as the README's homepage link; record the
            # docs reading of it under its own source so the report shows both
            d.candidates.append(Candidate(readme_docs.url, readme_docs.source, "readme_link",
                                          "readme", 60))

    live = [c for c in d.candidates if not c.rejected]

    # Documentation. An entry labelled Documentation/Docs wins; then any
    # accepted candidate shaped like docs; then the README's most-linked docs host.
    def first(pred):
        return next((c for c in live if pred(c)), None)

    d.docs = (first(lambda c: c.role == "docs_explicit")
              or first(lambda c: c.role in ("github", "homepage", "org") and looks_like_docs(c.url))
              or (readme_docs if readme_docs and not reject_reason(readme_docs.url, repo_host) else None))

    # Homepage. GitHub's declared field first, then manifests, README last.
    d.homepage = (first(lambda c: c.role == "github")
                  or first(lambda c: c.role == "homepage")
                  or first(lambda c: c.role == "org")
                  or first(lambda c: c.role == "docs_explicit")      # the old finder took Documentation here
                  or first(lambda c: c.role == "readme" and c.url != (d.docs.url if d.docs else ""))
                  or first(lambda c: c.role == "readme"))
    if d.homepage:
        d.homepage_scope = site_scope(d.homepage.url, github_url)
    if d.docs:
        d.docs_scope = site_scope(d.docs.url, github_url)
    return d


# ── reading the stored answer back ─────────────────────────────────────────

FINDING_KIND = "docs_discovery"

#: the short cue shown next to the homepage link. No sentence, by decision.
CUE_OK = "docs ✓"
CUE_UNREACHABLE = "docs unreachable"
CUE_NONE = "no docs found"
CUE_NOT_CHECKED = "docs not checked"


def read_summary(registry, slug: str, docs_url: str = "") -> dict:
    """The stored docs answer for one repo, in the shape the Scouting overview and
    the question evidence both read. Never raises.

    `cue` is "" when the step has never run for this repo: nothing was measured,
    which is not "no docs found".
    """
    out = {"docs_url": docs_url or "", "docs_state": "", "docs_status": None,
           "docs_final_url": "", "docs_last_modified": "", "docs_source": "", "docs_scope": "",
           "homepage_source": "", "homepage_scope": "", "docs_candidates": [], "cue": ""}
    try:
        rows = registry.query_findings(slug, FINDING_KIND)
    except Exception:
        rows = []
    row = next((r for r in rows if r.get("check_name") == "documentation"), None)
    if not row:
        if docs_url:
            out["docs_state"], out["cue"] = "not_checked", CUE_NOT_CHECKED
        return out
    try:
        detail = json.loads(row.get("detail_json") or "{}")
    except (TypeError, ValueError):
        detail = {}
    reach = detail.get("reachability") or {}
    out.update({
        "docs_url": detail.get("docs_url") or docs_url or "",
        "docs_source": detail.get("docs_source", ""),
        "docs_scope": detail.get("docs_scope", ""),
        "homepage_source": detail.get("homepage_source", ""),
        "homepage_scope": detail.get("homepage_scope", ""),
        "docs_candidates": detail.get("candidates") or [],
        "docs_status": reach.get("status"),
        "docs_final_url": reach.get("final_url", ""),
        "docs_last_modified": reach.get("last_modified", ""),
    })
    state = detail.get("docs_state", "")
    if state == "found":
        r = reach.get("state", "not_checked")
        out["docs_state"] = r
        out["cue"] = {"reachable": CUE_OK, "unreachable": CUE_UNREACHABLE}.get(r, CUE_NOT_CHECKED)
    elif state == "none_found":
        out["docs_state"], out["cue"] = "none_found", CUE_NONE
    else:
        out["docs_state"], out["cue"] = "unknown", CUE_NOT_CHECKED
    return out
