"""Deterministic detectors — design doc §5.1, first slice.

Scope of this slice: package manifests, entry points, and deployment units.
These are the rows of §5.1's table that need no code parsing, and they cover
identity precedence rungs 1 and 2 (§8.2). Code-marker rows — web framework
registrations, client libraries, schedulers — are ast-grep's job and land next;
until then components carry the type these signals alone can justify, with a
confidence that says so.

The standing rule from §5.2 applies even here: a detector never invents a
component. Every component below is anchored to a file that declares it.
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from collections import defaultdict

import yaml

from . import admission as _admission
from .ir import Component, Evidence, Identity, Location

# Conventional roots stripped before slugging a module path (§8.2 rung 3).
PATH_ROOTS = ("src", "packages", "lib", "libs", "modules")


def _line_of(text: str, needle: str) -> tuple[int, str]:
    """Locate `needle` in `text` for evidence purposes. tomllib and json give
    no line numbers, so recover one by search — approximate but honest, and
    §5.4 needs a location to point a curator at."""
    for n, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return n, line.strip()
    return 0, ""


def _slug(*parts: str) -> str:
    joined = "::".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9:._-]+", "-", joined).strip("-")


def _normalise_path(rel_dir: str) -> str:
    segs = [s for s in rel_dir.split(os.sep) if s and s not in PATH_ROOTS]
    return "/".join(segs) or "."


# ── deployment units — identity precedence 1 (§8.2) ──────────────────────

_SERVICE_LINE = re.compile(r"^  (?P<name>[A-Za-z0-9][\w.-]*):\s*$")
_DOCKER_CMD = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(?P<cmd>.+)$", re.IGNORECASE)


def _is_compose(root: str, rel: str) -> bool:
    """Identify a compose file by CONTENT, not filename.

    Filename matching was the first version and it was wrong: this repo's
    compose files are named after the solution they deploy
    (`egeria-quickstart-local.yaml`, `egeria-freshstart.yaml`) and contain no
    "compose" substring at all, so a name-based rule silently missed every
    solution deployment while happily finding the add-ons. Worth recording as
    a finding in its own right — compose filename conventions are unreliable,
    and any detector depending on them under-reports exactly the components
    that matter most.
    """
    if not rel.lower().endswith((".yaml", ".yml")):
        return False
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            head = [next(fh, "") for _ in range(80)]
    except OSError:
        return False
    return any(re.match(r"^services:\s*$", line) for line in head)


def deployment_units(root: str, files: list[str]) -> dict[str, list[str]]:
    """Directories that constitute a deployed artifact, mapped to the evidence
    files that declare them.

    §8.2's floor matters here: a deployment unit is a separate deployed
    *artifact* — its own image or compose service — not a runtime configuration
    flag. Dockerfiles and compose files are artifacts; a `DEMO_MODE` flag is
    not, and nothing in this function will see one.
    """
    units: dict[str, list[str]] = defaultdict(list)
    for rel in files:
        base = os.path.basename(rel)
        if base.startswith("Dockerfile") or base.endswith(".containerfile") or _is_compose(root, rel):
            units[os.path.dirname(rel) or "."].append(rel)
    return dict(units)


def _enclosing_unit(unit: str, units: dict[str, list[str]]) -> str:
    """The nearest deployment unit strictly containing `unit`, if any.

    A component nested inside another deployment unit takes that unit as its
    qualifying context (§8.2) — PyegeriaWebHandler under egeria-quickstart is
    a different component from PyegeriaWebHandler under egeria-freshstart.
    """
    best = ""
    for other in units:
        if other != unit and other != "." and unit.startswith(other + os.sep):
            if len(other) > len(best):
                best = other
    return best


def dockerfile_entry(root: str, rel: str) -> tuple[str, int, str]:
    """The CMD/ENTRYPOINT a Dockerfile declares, as evidence for what the
    image actually runs. Last one wins, matching Docker's own semantics."""
    best = ("", 0, "")
    try:
        text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
    except OSError:
        return best
    for n, line in enumerate(text.splitlines(), 1):
        if m := _DOCKER_CMD.match(line):
            best = (m.group("cmd").strip(), n, line.strip())
    return best


def compose_services(root: str, rel: str) -> list[tuple[str, str, int]]:
    """(service key, component name, line) per service in a compose file.

    Uses a real YAML parser. The first version read the file line-wise, on the
    stated assumption that compose files in the wild carry anchors, merge keys
    and templating a strict parser would reject. **That assumption was wrong** —
    PyYAML parses all 25 compose files in egeria-workspaces without error — and
    the shallow reader accumulated three separate failure modes before this was
    checked: filename-based detection missed every solution deployment,
    first-wins missed layered overrides, and a column-0 comment inside the
    services block silently truncated the parse after 4 of 7 services. Worth
    recording as a finding: the cheap-parser instinct cost more than it saved,
    and each failure was silent rather than loud.

    `container_name` is preferred over the service key. The key is an internal
    handle (`apache-web`); container_name is what the human sees in `docker ps`
    and what they call it — validated against the hand-written ground truth,
    where every named component is a declared container_name.

    Line numbers come from a targeted re-scan, since PyYAML discards them and
    §5.4 needs a location to point a curator at.
    """
    path = os.path.join(root, rel)
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(data, dict):
        return []
    services = data.get("services")
    if not isinstance(services, dict):
        return []

    lines = text.splitlines()

    def line_of(key: str, value: str) -> int:
        pat = re.compile(rf"^\s*(?:container_name:\s*)?{re.escape(value)}\s*:?\s*$")
        for n, line in enumerate(lines, 1):
            if pat.match(line):
                return n
        for n, line in enumerate(lines, 1):
            if re.match(rf"^\s{{1,4}}{re.escape(key)}\s*:\s*$", line):
                return n
        return 0

    out: list[tuple[str, str, int]] = []
    for key, body in services.items():
        if not isinstance(key, str):
            continue
        name = key
        if isinstance(body, dict):
            cn = body.get("container_name")
            if isinstance(cn, str) and cn.strip():
                name = cn.strip()
        out.append((key, name, line_of(key, name)))
    return out


# ── package manifests — identity precedence 2 (§8.2) ─────────────────────

def python_manifests(root: str, files: list[str]) -> list[dict]:
    found = []
    for rel in (f for f in files if os.path.basename(f) == "pyproject.toml"):
        path = os.path.join(root, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
            data = tomllib.loads(text)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        project = data.get("project") or {}
        name = project.get("name")
        if not name:
            continue
        scripts = list((project.get("scripts") or {}).keys())
        gui = list((project.get("gui-scripts") or {}).keys())
        members = ((data.get("tool") or {}).get("uv") or {}).get("workspace", {}).get("members", [])
        has_build = "build-system" in data
        found.append({
            "rel": rel, "dir": os.path.dirname(rel) or ".", "name": name,
            "scripts": scripts + gui, "workspace_members": members,
            "installable": has_build, "text": text, "ecosystem": "python",
            # Backlog.md "interface extraction" entry, item B (language
            # bindings) — a package's OWN declared description, read verbatim
            # and never classified into "is/isn't a binding" here (Dan,
            # 2026-08-30: "not sure about language bindings unless they are
            # exported as a specific library"). The self-description IS the
            # evidence — pyegeria's own says "A python client for the Egeria
            # metadata management system", which needs no inference to read
            # as a binding — so this surfaces the fact for a curator or later
            # stage to judge, the same restraint `protocol` already exercises
            # by staying empty rather than guessed from a port number.
            "description": (project.get("description") or "").strip(),
        })
    return found


def node_manifests(root: str, files: list[str]) -> list[dict]:
    found = []
    for rel in (f for f in files if os.path.basename(f) == "package.json"):
        path = os.path.join(root, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
            data = json.loads(text)
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("name")
        if not name or not isinstance(name, str):
            continue
        binv = data.get("bin")
        bins = list(binv) if isinstance(binv, dict) else ([name] if binv else [])
        description = data.get("description")
        found.append({
            "rel": rel, "dir": os.path.dirname(rel) or ".", "name": name,
            "scripts": bins, "workspace_members": data.get("workspaces") or [],
            "installable": True, "text": text, "ecosystem": "node",
            "description": description.strip() if isinstance(description, str) else "",
        })
    return found


_GRADLE_INCLUDE_RE = re.compile(r"^\s*(include(?:Build)?)\s*\(?(.*)$")
_GRADLE_QUOTED_RE = re.compile(r"['\"]:?([^'\"]+)['\"]")


def _strip_gradle_comment(line: str) -> str:
    i = line.find("//")
    return line[:i] if i >= 0 else line


def gradle_modules(root: str, files: list[str]) -> list[dict]:
    """Gradle multi-module layout, parsed as STATEMENTS rather than lines.

    The previous version matched one quoted name per line beginning with
    `include`. Gradle's `include` takes a comma-separated list that is
    conventionally written across many lines, and on `apache/kafka` that read
    **1 module of 64** (finding 93) — while the note it emitted, "not expanded
    into components in this slice", made a parse failure look like a deliberate
    scoping decision.

    Finding 5 in a new file: a line-wise reader on a construct that spans lines,
    failing silently rather than loudly. So this consumes the whole statement —
    a trailing comma continues it — and strips `//` comments first.

    `includeBuild` is captured separately: a composite build is a different
    thing from a subproject, and conflating them would put another project's
    modules in this project's component set.
    """
    found = []
    for rel in (f for f in files if os.path.basename(f) in ("settings.gradle", "settings.gradle.kts")):
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        lines = text.splitlines()
        modules: list[str] = []
        builds: list[str] = []
        i = 0
        while i < len(lines):
            m = _GRADLE_INCLUDE_RE.match(_strip_gradle_comment(lines[i]))
            if not m:
                i += 1
                continue
            keyword, rest = m.group(1), m.group(2)
            chunk = [rest]
            # A trailing comma continues the statement onto the next line.
            while chunk[-1].rstrip().rstrip(")").rstrip().endswith(","):
                i += 1
                if i >= len(lines):
                    break
                chunk.append(_strip_gradle_comment(lines[i]))
            names = _GRADLE_QUOTED_RE.findall(" ".join(chunk))
            (builds if keyword == "includeBuild" else modules).extend(names)
            i += 1
        if modules or builds:
            found.append({"rel": rel, "dir": os.path.dirname(rel) or ".",
                          "modules": modules, "included_builds": builds,
                          "text": text, "ecosystem": "gradle"})
    return found


def gradle_module_dir(module_path: str, settings_dir: str) -> str:
    """`connect:api` -> `connect/api`, relative to the settings file."""
    rel = module_path.strip().lstrip(":").replace(":", "/")
    if settings_dir and settings_dir != ".":
        return f"{settings_dir.rstrip('/')}/{rel}"
    return rel


# ── assembly ─────────────────────────────────────────────────────────────

def classify(manifest: dict) -> tuple[str | None, int, str]:
    """Type, confidence, and why — from manifest signals alone.

    Honest ceiling: manifests distinguish "has an entry point" from "does not",
    which separates Console Command from Software Library and nothing else. A
    package whose entry point launches a web server is a Software Service, and
    no manifest says so — that needs the code-marker detectors. Confidence is
    set to reflect that ceiling rather than overstating what was established.
    """
    if manifest.get("scripts"):
        return "Console Command", 70, "declares console entry points"
    if manifest.get("workspace_members"):
        return None, 0, "workspace root — a container, not a component"
    if manifest.get("installable"):
        return "Software Library", 60, "installable package with no entry point"
    return None, 0, "manifest declares neither entry point nor build system"


PACKAGE_MAIN = re.compile(r"^package\s+main\s*$", re.M)


def _declares_main(root: str, comp_dir: str, files: list[str]) -> bool:
    """Is this component an executable — i.e. does a `.go` file **directly in
    its root** declare `package main`?

    Two corrections over the original `basename == "main.go"` test, both from
    Kubernetes (finding 78):

    * **Go entry points are not required to be called `main.go`.** `k8s`'s are
      `cmd/kube-scheduler/scheduler.go`, `cmd/kubelet/kubelet.go` and so on, so
      a filename test missed every one of the six components that matter and
      left the entry-point set populated by test binaries instead.
    * **Only the component's own root counts.** Scanning the whole subtree
      typed `pkg/controlplane` as an executable because *some* nested test
      helper declares `package main`.

    Same shape as the container rule (finding 72): the language says what a
    thing is; a filename convention only usually agrees.
    """
    prefix = (comp_dir.rstrip("/") + "/") if comp_dir else ""
    for rel in files:
        if not rel.endswith(".go"):
            continue
        if "/" in rel[len(prefix):]:          # not directly in the root
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
                head = fh.read(4096)
        except OSError:
            continue
        if PACKAGE_MAIN.search(head):
            return True
    return False


def go_subsystems(root: str, files: list[str]) -> list[dict]:
    """Go **package trees** — the component unit a Go repo actually has.

    Added after finding 69, where Prometheus scored 0/11. Manifest identity is
    structurally blind on Go: `go.mod` declares a *module*, and in a
    single-module repo one module spans the entire architecture. Prometheus has
    six `go.mod` files and not one corresponds to a component.

    The unit that does correspond is the **top-level directory of a module**.
    That is not fitted to Prometheus — it is Go's own layout convention (`cmd/`
    for binaries, `internal/` for non-exported code, one directory per
    subsystem), and it is what the import graph respects: Go imports name a
    package by directory path, so directory boundaries *are* dependency
    boundaries.

    Two deliberate rules:

    * **Descend past directories that are not packages.** In Go a directory is
      a package only if it holds `.go` files *directly*; one that holds only
      subdirectories is a path segment, not a unit of code. So the component
      root is the first directory down from the module root that is itself a
      package.

      This is Go semantics, not a convention list, and it subsumes the
      hardcoded `cmd/` carve-out this function originally had. Prometheus's
      `cmd/` holds no `.go` files directly, so `cmd/prometheus` and
      `cmd/promtool` fall out as separate binaries for free. Milvus needs the
      general form: `internal/` (0 direct `.go` files) and
      `internal/distributed/` (0) are both pure containers, and a rule naming
      only `cmd` collapsed Milvus's entire architecture into a single
      `internal/**` component (finding 72).
    * **Files directly at a module root are skipped.** They belong to the
      module's own package, which is the whole module — the same
      "workspace root is a container, not a component" rule the npm detector
      already applies.

    Nested modules are honoured: a directory owned by a deeper `go.mod` is
    attributed to that module, not to the enclosing one.
    """
    from . import imports as _imports   # go_module_index lives with resolution
    module_index = _imports.go_module_index(root, files)     # longest path first
    if not module_index:
        return []
    go_files = [f for f in files if f.endswith(".go")]
    if not go_files:
        return []
    # Directories holding .go files DIRECTLY — i.e. the real Go packages.
    pkg_dirs = {os.path.dirname(f) for f in go_files}

    # dir -> owning module, longest module dir first so nested modules win
    by_dir = sorted(((mp, md) for mp, md in module_index), key=lambda t: len(t[1]), reverse=True)

    found: dict[str, dict] = {}
    for rel in go_files:
        for mod_path, mod_dir in by_dir:
            prefix = (mod_dir.rstrip("/") + "/") if mod_dir else ""
            if prefix and not rel.startswith(prefix):
                continue
            inner = rel[len(prefix):]
            parts = inner.split("/")
            if len(parts) < 2:
                # Directly at a module root. For the OUTERMOST module that is
                # the repo itself — not a component, same as the npm
                # workspace-root rule. But a NESTED module root is a distinct
                # published library and is a perfectly good component: k8s's
                # `staging/src/k8s.io/cloud-provider` carries its own go.mod,
                # four .go files and eight metadata files, and skipping them
                # orphaned exactly those twelve (finding 75).
                if not mod_dir:
                    break
                d = mod_dir
                sub = mod_dir
                entry = found.setdefault(d, {
                    "dir": d, "module": mod_path, "subsystem": sub,
                    "import_path": mod_path, "files": [], "has_main": False,
                })
                entry["files"].append(rel)
                break
            # Descend to the first directory that is itself a Go package.
            walked: list[str] = []
            d = None
            for part in parts[:-1]:
                walked.append(part)
                cand = f"{prefix}{'/'.join(walked)}"
                if cand in pkg_dirs:
                    d = cand
                    break
            if d is None:               # unreachable: the file's own dir is a package
                break
            sub = d[len(prefix):]
            entry = found.setdefault(d, {
                "dir": d, "module": mod_path, "subsystem": sub,
                "import_path": f"{mod_path.rstrip('/')}/{sub}",
                "files": [], "has_main": False,
            })
            entry["files"].append(rel)
            break
    for entry in found.values():
        entry["has_main"] = _declares_main(root, entry["dir"], entry["files"])
    return sorted(found.values(), key=lambda e: e["dir"])


def compose_slugs(root: str, files: list[str], units: dict | None = None) -> dict[tuple[str, str], str]:
    """{(deployment unit, plain slug): final slug} for every compose service in `files`.

    A compose service has no files, so its slug IS its scope locator, and reclassifications, verdicts
    and referenced-only rows key on it. The plain slug is `<dir basename>::<name>`. When more than one
    directory carries the same plain slug (a/deploy and b/deploy both run `kafka`), EVERY one of them is
    qualified by its whole directory path (`a-deploy::kafka`), independent of which is seen first and of
    which files a scoped run was handed (callers pass the full census). A name no other directory shares
    keeps its plain slug, so existing slugs and verdicts are unchanged.

    Path text is not injective ("a/b-c" and "a-b/c" both read "a-b-c"), so the final slug TEXT of every
    service in the repository, plain or qualified, is compared in one pass; each QUALIFIED slug whose text
    is also carried by another service gets a short hash of its exact path appended. A plain slug never
    changes, and a qualified slug nobody else carries is left as it is."""
    import hashlib
    entries: list[tuple[str, str, str]] = []                      # (unit, name, plain slug)
    for unit, decls in (units if units is not None else deployment_units(root, files)).items():
        names: dict[str, str] = {}
        for decl in decls:
            if os.path.basename(decl).startswith("Dockerfile") or _admission.is_fixture_path(decl):
                continue
            for key, name, _line in compose_services(root, decl):
                if key not in names or (name != key and names[key] == key):
                    names[key] = name
        for name in names.values():
            entries.append((unit, name, _slug(os.path.basename(unit), name)))
    units_by_plain: dict[str, set[str]] = defaultdict(set)
    for unit, _name, plain in entries:
        units_by_plain[plain].add(unit)
    candidate: dict[tuple[str, str], tuple[str, bool]] = {}      # (unit, plain) -> (text, qualified?)
    for unit, name, plain in entries:
        if len(units_by_plain[plain]) > 1:
            candidate[(unit, plain)] = (_slug(unit.replace(os.sep, "-"), name), True)
        else:
            candidate[(unit, plain)] = (plain, False)
    seen: dict[str, int] = defaultdict(int)
    for text, _q in candidate.values():
        seen[text] += 1
    return {k: (f"{text}.{hashlib.sha1(k[0].encode()).hexdigest()[:6]}" if q and seen[text] > 1 else text)
            for k, (text, q) in candidate.items()}


def _component_dir(c: Component) -> str:
    g = (c.files or [""])[0]
    for suffix in ("/**", "/*"):
        if g.endswith(suffix):
            return g[: -len(suffix)] or "."
    return "." if g == "**" or not g else g


def _builds_twin(twin: Component, facts: dict, unit: str, manifest: bool = False) -> bool:
    """May this compose service be merged into `twin`, a Dockerfile or manifest component? Only if it
    BUILDS the twin's directory, or declares no build and names no image at all.

    * A DOCKERFILE twin is built when the directory of the Dockerfile the service uses (its `dockerfile:`
      resolved against the build context, itself resolved against the compose file's directory; the
      context itself when no dockerfile is given) is the twin's directory.
    * A MANIFEST twin is built when EITHER the build context OR that Dockerfile's directory is the
      manifest's directory (`context: ./api` with the Dockerfile in docker/, or context `.` with
      `dockerfile: api/Dockerfile`).

    A remote build URL counts as no build. A service that runs a foreign image (`image: nginx`, no
    build) is not that component, however the names line up."""
    if facts.get("build"):
        ctx = os.path.normpath(os.path.join(unit, facts["build"]))
        df = facts.get("dockerfile") or ""
        dockerfile_dir = os.path.normpath((os.path.dirname(os.path.join(ctx, df)) or ".") if df else ctx)
        target = os.path.normpath(_component_dir(twin))
        return dockerfile_dir == target or (manifest and ctx == target)
    return not facts.get("image")


def _manifest_and_dockerfile_components(root: str, files: list[str], components: list[Component],
                                        evidence: list[Evidence], notes: list[str],
                                        ) -> tuple[dict, list[dict], set[str]]:
    """The manifest and Dockerfile-unit passes of `build_components`, over `files`. Returns (units,
    manifests, slugs of the Dockerfile-unit components). Split out so the compose pass can ask the same
    question of the WHOLE repository (the census) as of the files a scoped run was handed."""
    units = deployment_units(root, files)
    manifests = python_manifests(root, files) + node_manifests(root, files)

    # Deployment context for a manifest = the nearest enclosing deployment unit.
    def context_for(d: str) -> str:
        best = ""
        for unit in units:
            if unit != "." and (d == unit or d.startswith(unit + os.sep)):
                if len(unit) > len(best):
                    best = unit
        return best

    for m in manifests:
        ctype, conf, why = classify(m)
        if ctype is None:
            notes.append(f"{m['rel']}: skipped — {why}")
            continue

        ctx = context_for(m["dir"])
        if ctx:
            ident = Identity("deployment-unit", os.path.basename(ctx), ctx)
            conf = min(95, conf + 15)   # §8.2 rung 1 is a stronger claim
        else:
            ident = Identity("package-name", m["name"])

        slug = _slug(os.path.basename(ctx) if ctx else "", m["name"])
        comp = Component(
            slug=slug,
            name=m["name"],
            type=ctype,
            identity=ident,
            files=[f"{m['dir']}/**" if m["dir"] != "." else "**"],
            confidence=conf,
            confidence_level="Derived",
            admission_evidence=f"from {m['rel']}",
        )
        components.append(comp)

        line, excerpt = _line_of(m["text"], m["name"])
        evidence.append(Evidence(
            subject_kind="component", subject_slug=slug,
            assertion=f"component identity = {ident.method}:{ident.value}",
            detector=f"manifest:{m['ecosystem']}",
            locations=[Location(m["rel"], line, excerpt)],
            confidence=conf, confidence_level="Derived",
        ))
        evidence.append(Evidence(
            subject_kind="component", subject_slug=slug,
            assertion=f"solutionComponentType = {ctype}",
            detector=f"manifest:{m['ecosystem']}",
            locations=[Location(m["rel"], line, excerpt)],
            confidence=conf, confidence_level="Derived",
        ))
        # Backlog.md "interface extraction" entry, item B — a published,
        # installable package with no entry point (ctype == "Software
        # Library") is exactly pyegeria's shape, and its own declared
        # description is the evidence Dan's example asks for. Verbatim, not
        # classified: this records the fact a package with this name and
        # description is published, and leaves "is it actually a binding for
        # something" to whoever reads it — same restraint as everywhere else
        # ports/evidence stop short of inference.
        if ctype == "Software Library" and m.get("description"):
            evidence.append(Evidence(
                subject_kind="component", subject_slug=slug,
                assertion=f"publishes a {m['ecosystem']} package — possible language binding",
                detector=f"manifest:{m['ecosystem']}",
                locations=[Location(m["rel"], 0, m["description"][:200])],
                confidence=conf, confidence_level="Derived",
            ))

    # A deployment unit carrying a Dockerfile but NO manifest is still a
    # component — and it is identity precedence rung 1 (§8.2), the strongest
    # claim available. Missing these was the first version's worst failure:
    # PyegeriaWebHandler, the most substantial component in egeria-workspaces,
    # went entirely unreported because it ships no pyproject.toml. Anything
    # containerised is deployed, and deployment is what §8.2 now leads on.
    dockerfile_slugs: set[str] = set()
    claimed_dirs = {m["dir"] for m in manifests}
    for unit, decls in units.items():
        dockerfiles = [d for d in decls if os.path.basename(d).startswith("Dockerfile")
                       or d.endswith(".containerfile")]
        if not dockerfiles or unit in claimed_dirs or unit == ".":
            continue
        # Only if the unit actually contains first-party code — a directory of
        # nothing but Dockerfiles is a build context, not a component.
        owned = [f for f in files if f.startswith(unit + os.sep)]
        if len(owned) <= len(decls):
            notes.append(f"{unit}: Dockerfile present but no first-party code — "
                         f"treated as a build context, not a component")
            continue

        name = os.path.basename(unit)
        # §8.2: where a deployment context exists it QUALIFIES the slug,
        # otherwise identically-named units collide. This is not cosmetic —
        # egeria-workspaces ships PyegeriaWebHandler twice, once per solution
        # deployment, and the unqualified slug silently dropped the second as
        # a duplicate. Two components is the pre-registered correct answer
        # (plan §3, T1 item 3); collapsing them is the failure the revised
        # precedence chain exists to prevent.
        parent = _enclosing_unit(unit, units)
        slug = _slug(os.path.basename(parent) if parent else "", name)
        if any(c.slug == slug for c in components):
            continue
        cmd, line, excerpt = dockerfile_entry(root, dockerfiles[0])
        components.append(Component(
            slug=slug, name=name, type="Long Running Daemon",
            identity=Identity("deployment-unit", name, parent or unit),
            files=[f"{unit}/**"], confidence=75, confidence_level="Derived",
            admission_evidence=f"from {dockerfiles[0]}",
        ))
        dockerfile_slugs.add(slug)
        evidence.append(Evidence(
            subject_kind="component", subject_slug=slug,
            assertion=f"component identity = deployment-unit:{name}",
            detector="dockerfile:presence",
            locations=[Location(dockerfiles[0], line, excerpt or "")],
            confidence=75, confidence_level="Derived",
        ))
        if cmd:
            evidence.append(Evidence(
                subject_kind="component", subject_slug=slug,
                assertion="solutionComponentType = Long Running Daemon",
                detector="dockerfile:entrypoint",
                locations=[Location(dockerfiles[0], line, excerpt)],
                confidence=70, confidence_level="Derived",
            ))
    return units, manifests, dockerfile_slugs


_HASHED = re.compile(r"\.[0-9a-f]{6}$")


def _plan_compose(slugs: dict, census_comps: list[Component], dockerfile_slugs: set[str],
                  per_unit: dict, per_unit_facts: dict) -> dict[tuple[str, str], tuple[str, str | None]]:
    """{(unit, service key): (slug, slug of the component it merges into or None)}, over the whole census.

    Order and scope independent: services are visited in sorted order over the CENSUS, so a scoped run and
    the full run give the same service the same slug and the same merge.
    1. natural slugs are claimed first (plain, or qualified by directory), then a service that does NOT
       build its same-named Dockerfile/manifest twin takes `<slug>.svc`, and a qualified slug that needed a
       hash takes that; these DERIVED slugs are then made unique against every slug in use by extending
       them with `.2`, `.3`, ... so a service literally named `foo.svc` cannot collide."""
    used = {c.slug for c in census_comps}
    plan: dict[tuple[str, str], tuple[str, str | None]] = {}
    derived: list[tuple[tuple[str, str], str]] = []
    claimed: dict[str, tuple[str, str]] = {}                       # natural compose slug -> its service
    for unit in sorted(per_unit):
        for key in sorted(per_unit[unit]):
            name = per_unit[unit][key][0]
            plain = _slug(os.path.basename(unit), name)
            slug = slugs.get((unit, plain), plain)
            f = per_unit_facts[unit].get(key, {"image": "", "build": ""})
            twin = next((c for c in census_comps if c.slug == slug), None)
            if twin is None:
                twin = next((c for c in census_comps if c.slug in dockerfile_slugs and c.perspective != "deployment"
                             and c.identity.deployment_context == unit and c.identity.value == name), None)
            if twin is not None and twin.perspective != "deployment":
                if _builds_twin(twin, f, unit, manifest=twin.slug not in dockerfile_slugs):
                    plan[(unit, key)] = (slug, twin.slug)
                    continue
                derived.append(((unit, key), f"{slug}.svc" if slug in used else slug))
                continue
            if _HASHED.search(slug):
                derived.append(((unit, key), slug))
                continue
            if slug in claimed:                                    # layered duplicate of a compose service
                plan[(unit, key)] = (slug, slug)
                continue
            claimed[slug] = (unit, key)
            used.add(slug)
            plan[(unit, key)] = (slug, None)
    for k, slug in derived:
        final, n = slug, 1
        while final in used:
            n += 1
            final = f"{slug}.{n}"
        used.add(final)
        plan[k] = (final, None)
    return plan


def build_components(root: str, files: list[str], all_files: list[str] | None = None,
                     census_info: dict | None = None) -> tuple[list[Component], list[Evidence], list[str], dict[str, int]]:
    components: list[Component] = []
    evidence: list[Evidence] = []
    notes: list[str] = []

    units, manifests, dockerfile_slugs = _manifest_and_dockerfile_components(root, files, components, evidence, notes)

    # Compose services are components with no first-party code of their own
    # (§8.2b's add-on and shared-infra tiers) — real components, but not ones a
    # manifest declares.
    #
    # Merged ACROSS every compose file in a unit rather than first-wins. These
    # deployments are layered: a base file declares `apache-web:` with no
    # container_name and an override supplies `container_name:
    # quickstart-web-server`. First-wins registered the base and discarded the
    # override, losing the human-facing name for 3 of 16 components. Layered
    # compose is the norm, not an oddity, so any per-file pass under-reports.
    #
    # Whose boundary is it (DESIGN-BLUEPRINT-NODE-ADMISSION.md)? Each merged service is classified by
    # the evidence of its boundary: a build context here (built), an image this repository publishes
    # (shipped), or an image it neither builds nor publishes (referenced only, which `admission.split`
    # takes out of the components). Compose files under test/example directories are fixtures.
    # EVERYTHING that decides a compose service's slug, its merge and its class is read from the whole
    # repository (`census`, the full first-party list), never from the files a scoped run was handed, so a
    # scoped run and the full run agree on the same service: its slug (a reclassification keys on it), its
    # twin, and whether CI publishes its image. A scoped run only chooses which services to EMIT.
    census = all_files if all_files is not None else files
    if census is files or list(census) == list(files):
        census_comps, census_units, census_manifests, census_dockerfile_slugs = (
            components, units, manifests, dockerfile_slugs)
    else:
        census_comps = []
        census_units, census_manifests, census_dockerfile_slugs = _manifest_and_dockerfile_components(
            root, census, census_comps, [], [])
    per_unit: dict[str, dict[str, tuple[str, str, int]]] = {}
    per_unit_facts: dict[str, dict[str, dict]] = {}
    fixture_services = 0
    for unit, decls in census_units.items():
        merged: dict[str, tuple[str, str, int]] = {}   # service key -> (name, decl, line)
        facts: dict[str, dict] = {}                     # service key -> {"image", "build"} merged across files
        for decl in decls:
            if os.path.basename(decl).startswith("Dockerfile"):
                continue
            svc_facts = _admission.compose_service_facts(root, decl)
            if _admission.is_fixture_path(decl):
                fixture_services += len(svc_facts)
                continue
            for key, f in svc_facts.items():
                cur = facts.setdefault(key, {"image": "", "build": "", "dockerfile": "", "decl": decl})
                cur["image"] = cur["image"] or f["image"]
                if f["build"] and not cur["build"]:
                    cur["build"], cur["dockerfile"], cur["decl"] = f["build"], f.get("dockerfile", ""), decl
            for key, name, line in compose_services(root, decl):
                prior = merged.get(key)
                # A declared container_name always beats a bare service key,
                # whichever file it arrived in.
                if prior is None or (name != key and prior[0] == key):
                    merged[key] = (name, decl, line)
        per_unit[unit], per_unit_facts[unit] = merged, facts
    if fixture_services:
        notes.append(f"{fixture_services} compose service(s) under test or example directories — a fixture, "
                     f"never a component")

    published = _admission.published_images(root, census, [m["name"] for m in census_manifests])
    for facts in per_unit_facts.values():
        for f in facts.values():
            if f["build"] and f["image"]:
                published.setdefault(_admission.normalise_image(f["image"]), f["decl"])

    if census_info is not None:
        census_info["published"] = dict(published)      # one list, from the census, for the record too
    plan = _plan_compose(compose_slugs(root, census, census_units), census_comps, census_dockerfile_slugs,
                         per_unit, per_unit_facts)
    by_slug = {c.slug: c for c in components}
    for unit, merged in per_unit.items():
        if unit not in units:
            continue                                    # outside this run's scope: decided, not emitted
        for key, (name, decl, line) in merged.items():
            f = per_unit_facts[unit].get(key, {"image": "", "build": ""})
            slug, merge_into = plan[(unit, key)]
            if merge_into is not None:
                # Merged into the Dockerfile/manifest component of the same directory: the stronger,
                # built-here reading. The compose service still names the image it ships, which is how a
                # service elsewhere is linked to the repository that builds it.
                twin = by_slug.get(merge_into)
                if twin is not None:
                    twin.image = twin.image or _admission.normalise_image(f["image"])
                continue
            named = name != key
            cls, why = _admission.classify_compose_service(f, f.get("decl") or decl, published)
            comp = Component(
                slug=slug, name=name, type="Third Party Process",
                identity=Identity("deployment-unit", name, unit),
                files=[], confidence=75 if named else 65, confidence_level="Derived",
                # deployment perspective (§4.1) — a running container, not a
                # source directory. See ir.py's Component.perspective docstring
                # for why this is tagged rather than reconciled with the
                # Dockerfile/manifest components below, which stay `physical`.
                perspective="deployment",
                admission=cls, admission_evidence=why, image=_admission.normalise_image(f["image"]),
            )
            components.append(comp)
            by_slug[slug] = comp
            evidence.append(Evidence(
                subject_kind="component", subject_slug=slug,
                assertion=("declared container_name on a compose service" if named
                           else "declared as a compose service"),
                detector="compose:container_name" if named else "compose:services",
                locations=[Location(decl, line, f"{name}")],
                confidence=75 if named else 65, confidence_level="Derived",
            ))

    # Go package trees (§8.2 rung 3 — module path). See go_subsystems().
    for g in go_subsystems(root, files):
        ident = Identity("module-path", g["import_path"])
        ctype = "Console Command" if g["has_main"] else "Software Library"
        components.append(Component(
            slug=_slug("go", g["subsystem"].replace("/", "-")),
            name=g["subsystem"],
            type=ctype,
            identity=ident,
            files=[f"{g['dir']}/**"],
            # Lower than a manifest claim on purpose: the directory boundary is
            # reliable, the *level* is a convention rather than a declaration,
            # and the type is inferred from `main.go` alone.
            confidence=55,
            confidence_level="Derived",
            proposed_by=["go-subsystem"],
            perspective="logical",
        ))
        gslug = _slug("go", g["subsystem"].replace("/", "-"))
        evidence.append(Evidence(
            subject_kind="component", subject_slug=gslug,
            assertion=f"component identity = module-path:{g['import_path']}",
            detector="go-subsystem",
            locations=[Location(f"{g['dir']}", 0,
                                f"{len(g['files'])} .go files under module {g['module']}")],
            confidence=55, confidence_level="Derived",
        ))
        evidence.append(Evidence(
            subject_kind="component", subject_slug=gslug,
            assertion=f"solutionComponentType = {ctype}",
            detector="go-subsystem",
            locations=[Location(f"{g['dir']}", 0,
                                "main.go present" if g["has_main"] else "no main.go — library package tree")],
            confidence=45, confidence_level="Derived",
        ))
    if not go_subsystems(root, files) and any(f.endswith(".go") for f in files):
        notes.append("go files present but no go.mod resolved — no Go components")

    # Gradle modules ARE components (finding 93). Previously reported and not
    # expanded, on the reasoning that "at egeria's 254 modules the partition
    # question is about shape, not leaves". That reasoning has expired twice
    # over: `go_subsystems` now expands Go package trees on exactly the same
    # basis (795 candidates on Kubernetes), and deterministic distillation
    # (finding 79) exists to narrow an over-proposed set. §2a is explicit that
    # finding more structure must not score worse.
    #
    # A declared module list is a BETTER identity source than Go's directory
    # convention, because it is declared rather than inferred: `include` is the
    # project telling you what its parts are.
    seen_gradle_dirs: set[str] = set()
    for g in gradle_modules(root, files):
        for module in g["modules"]:
            mdir = gradle_module_dir(module, g["dir"])
            if not mdir or mdir in seen_gradle_dirs:
                continue
            seen_gradle_dirs.add(mdir)
            slug = _slug("gradle", module.replace(":", "-"))
            components.append(Component(
                slug=slug, name=module,
                # Honest ceiling, same as classify()'s: a module declaration
                # says a part exists, not how it runs. Only a main class or an
                # application plugin would separate Console Command from
                # Software Library, and neither is read here.
                type="Software Library",
                identity=Identity("package-name", module),
                files=[f"{mdir}/**"],
                confidence=65, confidence_level="Derived",
                proposed_by=["gradle-module"],
                perspective="logical",
                admission_evidence=f"from {g['rel']}",
            ))
            evidence.append(Evidence(
                subject_kind="component", subject_slug=slug,
                assertion=f"component identity = package-name:{module}",
                detector="gradle-module",
                locations=[Location(g["rel"], 0, f"declared by `include` in {g['rel']}")],
                confidence=65, confidence_level="Derived",
            ))
        if g.get("included_builds"):
            notes.append(f"{g['rel']}: {len(g['included_builds'])} includeBuild composite "
                         f"build(s) not expanded — a separate project, not a subproject: "
                         f"{', '.join(g['included_builds'][:4])}")


    # Code-marker pass (§5.1's code half) — logical-perspective components the
    # manifest and deployment detectors structurally cannot see. Package roots
    # come from the manifests already found, so markers refine a boundary that
    # was detected, never invent one from nothing.
    from .code_markers import propose as _propose_markers
    # Package roots for marker attribution. Previously derived from Python and
    # Node manifests alone, which silently discarded every marker in a repo
    # whose manifests live elsewhere: Prometheus has `web/ui/package.json`, so
    # every root sat under `web/ui/` and all 8 Go marker matches fell outside
    # them and were dropped — "0 logical components from 8 matches". Go module
    # roots and Gradle module directories are package roots too (findings 70,
    # 93), and the repo root is the floor rather than only a fallback, since a
    # single-module Go repo has no other root to offer.
    package_roots_set = {m["dir"] for m in manifests}
    try:
        from . import imports as _imports_mod
    except ImportError:
        import imports as _imports_mod
    for _mod_path, _mod_dir in _imports_mod.go_module_index(root, files):
        package_roots_set.add(_mod_dir or ".")
    for _g in gradle_modules(root, files):
        package_roots_set.add(_g["dir"] or ".")
    package_roots_set.add(".")
    package_roots = sorted(package_roots_set)
    mc, me, mn, operation_counts = _propose_markers(root, files, package_roots)
    components.extend(mc)
    evidence.extend(me)
    notes.extend(mn)

    return components, evidence, notes, operation_counts
