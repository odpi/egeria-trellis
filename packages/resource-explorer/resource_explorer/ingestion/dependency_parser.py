"""Parse dependency manifest files to build a project dependency graph."""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

#: A requirements-file inline comment: "#" after whitespace ("docling-core==2.50.1    # remove potential conflicts").
_INLINE_COMMENT = re.compile(r"\s+#.*$")


def strip_inline_comment(text: str) -> str:
    """A requirements line or a stored version without its trailing comment. The parser kept it, so data_prep_kit's
    docling-core version was stored as "==2.50.1    # remove potential conflicts ..."."""
    return _INLINE_COMMENT.sub("", text or "").strip()


class DependencyParser:
    """
    Parses manifest files for multiple ecosystems and returns a list of dependency dicts.

    Supported:
      Python  — pyproject.toml, requirements.txt, setup.py
      Node.js — package.json
      Go      — go.mod
      Java    — pom.xml, build.gradle, build.gradle.kts
    """

    def parse(self, local_root: Path, resource_slug: str) -> list[dict]:
        deps: list[dict] = []
        root = Path(local_root)
        gradle_catalog = self._load_gradle_version_catalog(root)

        for manifest in root.rglob("pyproject.toml"):
            if "_test" not in str(manifest) and "vendor" not in str(manifest):
                deps.extend(self._rooted(root, manifest, self._parse_pyproject(manifest)))

        for manifest in root.rglob("requirements*.txt"):
            if "vendor" not in str(manifest):
                deps.extend(self._rooted(root, manifest, self._parse_requirements(manifest)))

        for manifest in root.rglob("setup.py"):
            if "vendor" not in str(manifest):
                deps.extend(self._rooted(root, manifest, self._parse_setup_py(manifest)))

        for manifest in root.rglob("package.json"):
            parts = manifest.relative_to(root).parts
            if "node_modules" not in parts and "vendor" not in parts:
                deps.extend(self._rooted(root, manifest, self._parse_package_json(manifest)))

        for manifest in root.rglob("go.mod"):
            if "vendor" not in str(manifest):
                deps.extend(self._rooted(root, manifest, self._parse_go_mod(manifest)))

        for manifest in root.rglob("pom.xml"):
            if "vendor" not in str(manifest):
                deps.extend(self._rooted(root, manifest, self._parse_pom_xml(manifest)))

        for pattern in ("build.gradle", "build.gradle.kts"):
            for manifest in root.rglob(pattern):
                if "vendor" not in str(manifest):
                    deps.extend(self._rooted(root, manifest, self._parse_gradle(manifest, gradle_catalog)))

        # Deduplicate by (dep_name, ecosystem, source_file). `source_file` is
        # just the filename (e.g. every build.gradle in a multi-module repo
        # collapses to the literal string "build.gradle"), so for Gradle this
        # dedup is effectively project-wide, not per-file.
        #
        # That makes the resolve-or-not outcome of a version RACE against
        # rglob's traversal order: if a downstream module's unversioned
        # `implementation 'g:a'` (BOM-managed) is seen before the BOM file's
        # own resolved `g:a:1.2.3` constraint for the same coordinate, first-
        # write-wins would keep the empty one and silently discard the
        # resolved version. `rglob` order is filesystem-dependent and not a
        # contract, so "prefer whichever came first" is not a real policy —
        # it is an unrepeatable coin flip. Preferring a resolved version over
        # an empty one is a real policy: it can only ever replace "we don't
        # know" with "we know", never the reverse, so it cannot introduce the
        # substituted-vs-declared confusion this module exists to avoid.
        seen: dict[tuple, int] = {}
        unique: list[dict] = []
        for d in deps:
            # Gradle keeps its project-wide key (see above); every other manifest is keyed by its own path,
            # so two pyproject.toml files in one repo no longer collapse into the first one read.
            src = d["source_file"]
            key = (d["dep_name"].lower(), d["ecosystem"],
                   src.rsplit("/", 1)[-1] if src.rsplit("/", 1)[-1].startswith("build.gradle") else src)
            idx = seen.get(key)
            if idx is None:
                seen[key] = len(unique)
                unique.append(d)
            elif not unique[idx]["dep_version"] and d["dep_version"]:
                unique[idx] = d

        return unique

    @staticmethod
    def _rooted(root: Path, manifest: Path, deps: list[dict]) -> list[dict]:
        """Record each row's manifest as its path under the repo root, not just its filename.

        `source_file` was the bare filename, so `my_egeria/pyproject.toml`'s `pyegeria` row read as the
        root `pyproject.toml`'s. Consumers (deployment_evidence, the dependency surveyor, the file
        classifier) already treat the value as a repo-relative path with `/` separators.
        """
        rel = manifest.relative_to(root).as_posix()
        for d in deps:
            d["source_file"] = rel
        return deps

    # ── parsers ───────────────────────────────────────────────────────────────

    def _parse_pyproject(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        try:
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    return []

            with open(path, "rb") as f:
                data = tomllib.load(f)

            src = str(path.name)

            # PEP 517/518 [project.dependencies]
            for dep_str in data.get("project", {}).get("dependencies", []):
                name, ver = self._split_pep508(dep_str)
                deps.append(self._dep("python", name, ver, "runtime", src))

            # [project.optional-dependencies]
            for group, dep_list in data.get("project", {}).get("optional-dependencies", {}).items():
                for dep_str in dep_list:
                    name, ver = self._split_pep508(dep_str)
                    deps.append(self._dep("python", name, ver, "optional", src))

            # Poetry [tool.poetry.dependencies]
            for name, spec in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
                if name.lower() == "python":
                    continue
                ver = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
                deps.append(self._dep("python", name, ver, "runtime", src))

            for name, spec in data.get("tool", {}).get("poetry", {}).get("dev-dependencies", {}).items():
                ver = spec if isinstance(spec, str) else ""
                deps.append(self._dep("python", name, ver, "dev", src))

        except Exception as exc:
            logger.debug("pyproject.toml parse error at %s: %s", path, exc)
        return deps

    def _parse_requirements(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        dep_type = "dev" if "dev" in path.name or "test" in path.name else "runtime"
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-r", "-c", "--")):
                    continue
                name, ver = self._split_pep508(strip_inline_comment(line))
                if name:
                    deps.append(self._dep("python", name, ver, dep_type, path.name))
        except Exception as exc:
            logger.debug("requirements parse error at %s: %s", path, exc)
        return deps

    def _parse_setup_py(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            # Regex for install_requires=[...] — handles multi-line with quotes
            m = re.search(r'install_requires\s*=\s*\[(.*?)\]', text, re.DOTALL)
            if m:
                for item in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                    name, ver = self._split_pep508(item)
                    if name:
                        deps.append(self._dep("python", name, ver, "runtime", "setup.py"))
        except Exception as exc:
            logger.debug("setup.py parse error at %s: %s", path, exc)
        return deps

    def _parse_package_json(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            src = str(path.relative_to(path.parent.parent) if path.parent.parent.exists() else path.name)
            for name, ver in data.get("dependencies", {}).items():
                deps.append(self._dep("javascript", name, str(ver), "runtime", src))
            for name, ver in data.get("devDependencies", {}).items():
                deps.append(self._dep("javascript", name, str(ver), "dev", src))
            for name, ver in data.get("peerDependencies", {}).items():
                deps.append(self._dep("javascript", name, str(ver), "optional", src))
        except Exception as exc:
            logger.debug("package.json parse error at %s: %s", path, exc)
        return deps

    def _parse_go_mod(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        try:
            in_require = False
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.startswith("require ("):
                    in_require = True
                    continue
                if in_require and line == ")":
                    in_require = False
                    continue
                if in_require or line.startswith("require "):
                    dep_line = line.removeprefix("require ").strip()
                    # Strip inline comments
                    dep_line = dep_line.split("//")[0].strip()
                    if not dep_line:
                        continue
                    parts = dep_line.split()
                    if len(parts) >= 1:
                        name = parts[0]
                        ver = parts[1] if len(parts) >= 2 else ""
                        dep_type = "indirect" if "// indirect" in line else "runtime"
                        deps.append(self._dep("go", name, ver, dep_type, "go.mod"))
        except Exception as exc:
            logger.debug("go.mod parse error at %s: %s", path, exc)
        return deps

    def _parse_pom_xml(self, path: Path) -> list[dict]:
        deps: list[dict] = []
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}")[0] + "}"

            for dep in root.iter(f"{ns}dependency"):
                group = (dep.findtext(f"{ns}groupId") or "").strip()
                artifact = (dep.findtext(f"{ns}artifactId") or "").strip()
                ver = (dep.findtext(f"{ns}version") or "").strip()
                scope = (dep.findtext(f"{ns}scope") or "runtime").strip()
                name = f"{group}:{artifact}" if group else artifact
                if name:
                    dep_type = "test" if scope == "test" else ("dev" if scope in ("provided", "optional") else "runtime")
                    deps.append(self._dep("java", name, ver, dep_type, "pom.xml"))
        except Exception as exc:
            logger.debug("pom.xml parse error at %s: %s", path, exc)
        return deps

    #: Gradle configurations, mapped to this module's dep_type vocabulary.
    #: `compileOnly` is dev rather than runtime deliberately: it is on the compile
    #: classpath and absent at runtime, so treating it as a runtime dependency
    #: would overstate what actually ships.
    _GRADLE_CONFIGS = {
        "implementation": "runtime", "api": "runtime", "runtimeOnly": "runtime",
        "compile": "runtime", "runtime": "runtime",
        "compileOnly": "dev", "annotationProcessor": "dev", "developmentOnly": "dev",
        "testImplementation": "test", "testCompileOnly": "test",
        "testRuntimeOnly": "test", "testCompile": "test",
    }

    #: `implementation 'g:a:v'` / `implementation("g:a")` — Groovy and Kotlin DSL.
    _GRADLE_STRING_RE = re.compile(
        r"^\s*(?P<config>[A-Za-z]+)\s*\(?\s*['\"](?P<coord>[^'\"]+)['\"]\s*\)?\s*$"
    )
    #: `implementation group: 'g', name: 'a', version: 'v'` — the map form.
    _GRADLE_MAP_RE = re.compile(
        r"^\s*(?P<config>[A-Za-z]+)\s+group\s*:\s*['\"](?P<group>[^'\"]+)['\"]"
        r"\s*,\s*name\s*:\s*['\"](?P<name>[^'\"]+)['\"]"
        r"(?:\s*,\s*version\s*:\s*['\"](?P<version>[^'\"]+)['\"])?"
    )
    #: `implementation project(':x')` — an internal module, NOT an external package.
    _GRADLE_PROJECT_RE = re.compile(r"^\s*[A-Za-z]+\s*\(?\s*project\s*\(")
    #: `implementation(libs.spring.boot.starter.web)` — version-catalog accessor,
    #: no quotes at all, so `_GRADLE_STRING_RE` (which requires them) never matches it.
    _GRADLE_CATALOG_RE = re.compile(
        r"^\s*(?P<config>[A-Za-z]+)\s*\(\s*libs\.(?P<accessor>[A-Za-z0-9_.]+)\s*\)\s*$"
    )
    #: `xVersion = '1.2.3'` / `def xVersion = "1.2.3"` / `val xVersion = "1.2.3"` —
    #: an `ext { }` property or Kotlin DSL `val`, read anywhere in the file rather
    #: than scoped to a block (simpler, and the name filter below is what keeps it
    #: safe — see `_extract_gradle_variables`). Deliberately `search()`ed rather
    #: than anchored to line-start: a single-line `ext { xVersion = '1.2.3' }`
    #: puts the assignment mid-line, after `ext {`, not at column 0.
    _GRADLE_VAR_RE = re.compile(
        r"(?:^|[{;]|\bdef\s+|\bval\s+)\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*[Vv]ersion)\s*="
        r"\s*(?P<q>['\"])(?P<value>(?:(?!(?P=q)).)*)(?P=q)"
    )
    #: A version field that is ENTIRELY one variable/catalog reference —
    #: `$xVersion`, `${xVersion}`, `${libs.versions.x}`, `${libs.versions.x.get()}`.
    #: Deliberately anchored (`^...$`): `${x}-SNAPSHOT` or `1.$x` are not handled,
    #: because splicing a resolved fragment into a partial string is a guess about
    #: the surrounding text, not a resolution — worse than leaving it empty. The
    #: name itself is matched non-greedily so an optional trailing `.get()` (the
    #: version-catalog accessor call) is peeled off rather than swallowed into it.
    _GRADLE_VAR_REF_RE = re.compile(r"^\$\{?(?P<name>[A-Za-z0-9_.]+?)(?:\.get\(\))?\}?$")

    def _load_gradle_version_catalog(self, root: Path) -> dict:
        """`gradle/libs.versions.toml`, Gradle's version-catalog file, parsed into
        `{"versions": {name: version}, "libraries": {alias: {"module": "g:a", "version": v}}}`.

        This is the standard, single-file location Gradle itself looks for
        (https://docs.gradle.org/current/userguide/version_catalogs.html) — one
        catalog for the whole build, not one per module.

        Absent or unparseable degrades to empty dicts rather than raising:
        measured 2026-09-01, Egeria — the project this parser was built
        against — has no such file at all (229 build.gradle files, zero
        references to `libs.`), so this path exists for the general case, not
        because the worked example needs it. A missing or broken catalog must
        not take the rest of the dependency parse down with it.
        """
        catalog_path = root / "gradle" / "libs.versions.toml"
        empty = {"versions": {}, "libraries": {}}
        if not catalog_path.exists():
            return empty
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]
            with open(catalog_path, "rb") as f:
                data = tomllib.load(f)
        except Exception as exc:
            logger.debug("libs.versions.toml parse error at %s: %s", catalog_path, exc)
            return empty

        versions = {str(k): str(v) for k, v in (data.get("versions") or {}).items()
                    if isinstance(v, str)}
        libraries: dict[str, dict] = {}
        for alias, spec in (data.get("libraries") or {}).items():
            if isinstance(spec, str):
                # Shorthand: "group:artifact:version" or "group:artifact".
                parts = spec.split(":")
                if len(parts) == 3:
                    libraries[alias] = {"module": f"{parts[0]}:{parts[1]}", "version": parts[2]}
                elif len(parts) == 2:
                    libraries[alias] = {"module": spec, "version": ""}
                continue
            if not isinstance(spec, dict):
                continue
            module = spec.get("module") or ""
            if not module and spec.get("group") and spec.get("name"):
                module = f"{spec['group']}:{spec['name']}"
            if not module:
                continue
            version = ""
            v = spec.get("version")
            if isinstance(v, str):
                version = v
            elif isinstance(v, dict):
                version = versions.get(v.get("ref", ""), "")
            libraries[alias] = {"module": module, "version": version}
        return {"versions": versions, "libraries": libraries}

    @staticmethod
    def _catalog_entry(table: dict, accessor: str) -> str | None:
        """Map a dotted catalog accessor (`spring.boot.starter`, as written in a
        build file) back to its TOML alias (`spring-boot-starter` or
        `spring_boot_starter`, as written in libs.versions.toml) and return that
        table's key, or None. Gradle's real accessor-generation algorithm is more
        elaborate (camelCase splitting); this covers the two separators the spec
        allows, which is what every catalog observed in practice uses."""
        for candidate in (accessor, accessor.replace(".", "-"), accessor.replace(".", "_")):
            if candidate in table:
                return candidate
        return None

    def _extract_gradle_variables(self, text: str) -> dict[str, str]:
        """`{name: value}` for every `xVersion = '...'` assignment in the file
        (an `ext { }` property or Kotlin `val`), collected across the whole text
        rather than scoped to the `ext { }` block specifically.

        Restricted to names ending in `Version`/`version` on purpose: that is the
        real, near-universal Gradle convention (confirmed against Egeria's own
        `bom/build.gradle` — `logbackVersion`, `jacksonVersion`, `slf4jVersion`,
        etc., 70+ of them), and it is doing safety work, not just narrowing scope.
        Without it, an unrelated line like `description = 'Egeria BOM...'` would
        be captured as a "variable" and become eligible for substitution into
        `dep_version` if anything ever referenced `${description}` — a coincidence
        away from a wrong version being reported as resolved.
        """
        variables: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.split("//", 1)[0]
            m = self._GRADLE_VAR_RE.search(line)
            if m:
                variables[m.group("name")] = m.group("value")
        return variables

    def _resolve_gradle_version(self, raw: str, variables: dict, catalog_versions: dict) -> tuple[str, str]:
        """(version, version_source) for one version field from a Gradle
        coordinate string.

        A version literally present is `"declared"`. A version resolved by
        substituting a same-file variable or a version-catalog entry is tagged
        with WHICH of those it was — never folded into `"declared"`, because a
        substituted value is a claim this code made, not one the manifest made,
        and `cve_scan` (and anyone reading `dep_version` later) needs to be able
        to tell those apart. Anything this cannot resolve — a partial
        interpolation, a name not in scope, a catalog with no such entry — comes
        back `("", "")`: no guess, and no provenance claimed for a value that
        was not actually produced.
        """
        v = raw.strip()
        if not v:
            return "", ""
        if "$" not in v:
            return v, "declared"
        m = self._GRADLE_VAR_REF_RE.match(v)
        if not m:
            return "", ""
        name = m.group("name")
        if name.startswith("libs.versions."):
            key = self._catalog_entry(catalog_versions, name[len("libs.versions."):])
            return (catalog_versions[key], "version_catalog") if key else ("", "")
        if name in variables:
            return variables[name], "variable_interpolation"
        return "", ""

    def _parse_gradle(self, path: Path, catalog: dict | None = None) -> list[dict]:
        """Coordinates declared in a Gradle build file, with their versions
        resolved where that is actually possible.

        `build.gradle` is Groovy (or Kotlin) *code*, not a data format, so this
        still only reads literal/near-literal forms — it does not run Gradle.
        Three sources of a version are handled, cheapest and most common first:

        1. **Declared inline** — `implementation 'g:a:1.2.3'`. Read directly;
           tagged `"declared"`.
        2. **A same-file variable** — `ext { xVersion = '1.2.3' }` then
           `"g:a:${xVersion}"`. Substituted; tagged `"variable_interpolation"`.
           This is the case that matters for the project this was built
           against: Egeria's `bom/build.gradle` declares 80 dependency
           constraints entirely this way (measured 2026-09-01) — every one of
           them was previously recorded with an empty version.
        3. **A version catalog** — `gradle/libs.versions.toml`, resolved via
           `${libs.versions.x}` inside a version string, or a whole
           `implementation(libs.alias)` line. Substituted; tagged
           `"version_catalog"`. Egeria uses no version catalog at all
           (measured 2026-09-01), so this path is unexercised by the worked
           example — see `tests/test_gradle_dependency_parsing.py` for
           synthetic coverage instead.

        Two exclusions matter more than the parsing:

        * `project(':some:module')` is an internal module, not a package. Recording
          those would invent dependencies that exist in no registry, and every one
          would come back unmatched from a CVE lookup — noise indistinguishable
          from a clean result.
        * A coordinate whose version comes from a BOM/platform this code did not
          resolve (`group:artifact` with no version segment at all, or a variable/
          catalog reference this cannot look up) is recorded **with an empty
          version**, not dropped and not guessed. `cve_scan` already classifies
          an empty version as unqueryable with "no pinned version to query"
          rather than as clean, so recording the coordinate turns "we never
          looked" into "we looked and cannot answer" — a different and more
          useful state than either dropping it or inventing a version.

        What could not be resolved is logged per file rather than silently
        dropped, because a dependency list quietly missing its version-catalog
        entries looks complete and is worse than none.
        """
        deps: list[dict] = []
        unresolved = internal = variable_resolved = catalog_resolved = 0
        catalog = catalog or {"versions": {}, "libraries": {}}
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.debug("gradle read error at %s: %s", path, exc)
            return deps

        variables = self._extract_gradle_variables(text)
        source = path.name
        for raw in text.splitlines():
            line = raw.split("//", 1)[0].rstrip()
            if not line.strip():
                continue
            head = line.strip().split(None, 1)[0].split("(", 1)[0]
            if head not in self._GRADLE_CONFIGS:
                continue
            dep_type = self._GRADLE_CONFIGS[head]

            if self._GRADLE_PROJECT_RE.match(line):
                internal += 1
                continue

            m = self._GRADLE_CATALOG_RE.match(line)
            if m:
                alias = self._catalog_entry(catalog["libraries"], m.group("accessor"))
                entry = catalog["libraries"].get(alias) if alias else None
                if entry and entry.get("module"):
                    ver = entry.get("version") or ""
                    if ver:
                        catalog_resolved += 1
                    deps.append(self._dep(
                        "java", entry["module"], ver, dep_type, source,
                        version_source="version_catalog" if ver else ""))
                else:
                    # No catalog loaded, or this alias is not in it — cannot
                    # even recover a coordinate here, so nothing is recorded
                    # (recording a fabricated name would be worse than
                    # skipping it — see the map/string-form's own comments
                    # on not inventing packages).
                    unresolved += 1
                continue

            m = self._GRADLE_MAP_RE.match(line)
            if m:
                ver, vsrc = self._resolve_gradle_version(
                    m.group("version") or "", variables, catalog["versions"])
                if vsrc == "variable_interpolation":
                    variable_resolved += 1
                elif vsrc == "version_catalog":
                    catalog_resolved += 1
                elif m.group("version") and not ver:
                    unresolved += 1
                deps.append(self._dep(
                    "java", f"{m.group('group')}:{m.group('name')}",
                    ver, dep_type, source, version_source=vsrc))
                continue

            m = self._GRADLE_STRING_RE.match(line)
            if m:
                coord = m.group("coord")
                parts = coord.split(":")
                if len(parts) == 3:
                    ver, vsrc = self._resolve_gradle_version(parts[2], variables, catalog["versions"])
                    if vsrc == "variable_interpolation":
                        variable_resolved += 1
                    elif vsrc == "version_catalog":
                        catalog_resolved += 1
                    elif not ver:
                        # Had a third segment and it did not resolve to a
                        # plain literal or a known variable/catalog entry —
                        # an unresolved reference, not a version.
                        unresolved += 1
                    deps.append(self._dep(
                        "java", f"{parts[0]}:{parts[1]}", ver, dep_type, source, version_source=vsrc))
                elif len(parts) == 2:
                    # No version segment at all: resolved by a BOM or platform,
                    # not absent — not counted as unresolved.
                    deps.append(self._dep("java", coord, "", dep_type, source))
                else:
                    unresolved += 1
                continue

            # A configuration line naming something this cannot read — an
            # unrecognised catalog form, a function call, a plugin extension.
            unresolved += 1

        if unresolved or internal or variable_resolved or catalog_resolved:
            logger.info(
                "gradle %s: %d dependency(ies) recorded (%d version(s) from a "
                "same-file variable, %d from the version catalog), %d unresolved "
                "(version catalog/variable/plugin), %d internal project() refs skipped",
                path, len(deps), variable_resolved, catalog_resolved, unresolved, internal,
            )
        return deps

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _dep(ecosystem: str, name: str, version: str, dep_type: str, source_file: str,
              version_source: str | None = None) -> dict:
        """Build one dependency row.

        `version_source` is the provenance for `dep_version`, and it is the
        whole point of this method rather than a detail of it — see
        `dep_version_source`'s column comment in registry.py for the full
        vocabulary and why it exists. Every caller in this module parses a
        version straight out of the manifest text at the dependency's own
        declaration site, so the default when a version is present and the
        caller has not overridden it is `"declared"`: literally what the
        manifest wrote there, not looked up or substituted from anywhere
        else. Gradle is the one parser in this file that ever resolves a
        version from somewhere OTHER than the declaration site (an `ext`
        variable, a version catalog), and it always passes `version_source`
        explicitly for those rows rather than taking this default — see
        `_resolve_gradle_version`.

        No version resolved -> no source. An empty `dep_version` with a
        non-empty `version_source` would claim provenance for a fact this
        does not have.
        """
        version = (version or "").strip()
        if version_source is None:
            version_source = "declared" if version else ""
        return {
            "dep_name": name.strip(),
            "dep_version": version,
            "dep_version_source": version_source,
            "dep_type": dep_type,
            "ecosystem": ecosystem,
            "source_file": source_file,
        }

    @staticmethod
    def _split_pep508(spec: str) -> tuple[str, str]:
        """Split a PEP 508 dependency string into (name, version_specifier)."""
        spec = spec.strip().split(";")[0].strip()  # strip environment markers
        m = re.match(r'^([A-Za-z0-9_.\-\[\]]+)\s*([>=<!~^,\s].*)?$', spec)
        if m:
            return m.group(1).split("[")[0], (m.group(2) or "").strip()
        return spec, ""
