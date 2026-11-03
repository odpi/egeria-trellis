"""Gradle dependency parsing — deliberately partial, and honest about it.

Before 2026-08-31 `dependency_parser.py` read six manifest kinds and none of them
was Gradle, so Egeria — 239 `build.gradle` files — had **zero** dependency rows.
`dependency_analysis` had nothing to report and `cve_scan` could never run,
which capped `security_summary` at 7 of 8 inputs for the project this tool is
built around.

`build.gradle` is Groovy (or Kotlin) *code*, not a data format, so this reads
literal forms plus two specific, cheap-to-resolve indirections: a same-file
`ext`/`val` variable, and a `gradle/libs.versions.toml` version catalog. It
still cannot run Gradle, so a BOM/platform-managed version (the common Egeria
case) stays unresolved. That is acceptable only because of what it does with
what it cannot resolve: an unresolvable version becomes **empty**, never a
guess and never the raw variable/accessor text; internal module references are
**excluded** rather than recorded as packages that exist in no registry; and a
version that WAS resolved from a variable or a catalog carries a
`dep_version_source` that says so, distinct from `"declared"` (the manifest
wrote it there literally) — see `_dep()`'s docstring in dependency_parser.py.
That distinction is the point of this whole module: a substituted version
reused as though it were declared is a worse bug than no version at all,
because it produces a confident CVE answer about a version nobody actually
pinned.
"""
from __future__ import annotations

import re
import textwrap

import pytest

from resource_explorer.ingestion.dependency_parser import DependencyParser


def _parse(tmp_path, body: str, name: str = "build.gradle", catalog=None):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return DependencyParser()._parse_gradle(p, catalog)


def test_a_pinned_coordinate_keeps_its_version(tmp_path):
    got = _parse(tmp_path, """
        dependencies {
            implementation 'org.slf4j:slf4j-api:2.0.13'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_name"] == "org.slf4j:slf4j-api"
    assert got[0]["dep_version"] == "2.0.13"
    assert got[0]["ecosystem"] == "java"
    # A version literally written at the dependency's own declaration site is
    # "declared" — the baseline case every other provenance tag is judged
    # against.
    assert got[0]["dep_version_source"] == "declared"


def test_a_coordinate_with_no_version_is_recorded_not_dropped(tmp_path):
    """Egeria's real shape: versions come from a BOM, so `group:artifact` is the
    norm rather than an error. Dropping these would leave the project with zero
    dependencies, which is what it had before this parser existed.

    `cve_scan` classifies an empty version as "no pinned version to query" —
    unqueryable, explicitly not clean — so recording them turns "we never looked"
    into "we looked and cannot answer without the BOM".
    """
    got = _parse(tmp_path, """
        dependencies {
            testImplementation 'org.springframework.boot:spring-boot'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_name"] == "org.springframework.boot:spring-boot"
    assert got[0]["dep_version"] == ""


def test_an_unresolved_variable_is_not_recorded_as_a_version(tmp_path):
    """`${jacksonVersion}` is a Groovy variable, not a version.

    Passing it through would put a string no registry can match into
    `dep_version`; `cve_scan` would then reject it as "a range or unparseable",
    which is a true statement about the wrong thing. Empty says what actually
    happened: declared here, resolved elsewhere.
    """
    got = _parse(tmp_path, """
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${jacksonVersion}'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_name"] == "com.fasterxml.jackson.core:jackson-databind"
    assert got[0]["dep_version"] == "", (
        f"recorded {got[0]['dep_version']!r} as a version — it is an unresolved variable"
    )


def test_internal_project_references_are_not_dependencies(tmp_path):
    """`project(':x')` is a module of this build, not a package.

    Recording them would invent dependencies that exist in no registry, and every
    one would come back unmatched from a CVE lookup — noise indistinguishable
    from a clean result. Egeria's test modules are mostly these.
    """
    got = _parse(tmp_path, """
        dependencies {
            testImplementation project(':open-metadata-implementation:admin-services')
            implementation project(":frameworks:audit-log-framework")
            implementation 'org.slf4j:slf4j-api:2.0.13'
        }
    """)
    names = [d["dep_name"] for d in got]
    assert names == ["org.slf4j:slf4j-api"], names
    assert not any(n.startswith(":") for n in names)


def test_kotlin_dsl_parenthesised_form(tmp_path):
    got = _parse(tmp_path, """
        dependencies {
            implementation("org.jetbrains.kotlin:kotlin-stdlib:1.9.0")
            testImplementation("io.mockk:mockk")
        }
    """, name="build.gradle.kts")
    by = {d["dep_name"]: d for d in got}
    assert by["org.jetbrains.kotlin:kotlin-stdlib"]["dep_version"] == "1.9.0"
    assert by["io.mockk:mockk"]["dep_version"] == ""


def test_the_map_form(tmp_path):
    got = _parse(tmp_path, """
        dependencies {
            implementation group: 'commons-io', name: 'commons-io', version: '2.15.1'
            compileOnly group: 'org.projectlombok', name: 'lombok'
        }
    """)
    by = {d["dep_name"]: d for d in got}
    assert by["commons-io:commons-io"]["dep_version"] == "2.15.1"
    assert by["org.projectlombok:lombok"]["dep_version"] == ""


@pytest.mark.parametrize("config,expected", [
    ("implementation", "runtime"),
    ("api", "runtime"),
    ("runtimeOnly", "runtime"),
    # compileOnly is on the compile classpath and absent at runtime, so calling
    # it runtime would overstate what actually ships.
    ("compileOnly", "dev"),
    ("annotationProcessor", "dev"),
    ("testImplementation", "test"),
    ("testRuntimeOnly", "test"),
])
def test_configuration_maps_to_dep_type(tmp_path, config, expected):
    got = _parse(tmp_path, f"""
        dependencies {{
            {config} 'g:a:1.0'
        }}
    """)
    assert len(got) == 1, got
    assert got[0]["dep_type"] == expected


def test_comments_and_unrelated_lines_are_ignored(tmp_path):
    got = _parse(tmp_path, """
        plugins { id 'java' }
        // implementation 'commented:out:1.0'
        description = 'not a dependency'
        dependencies {
            implementation 'real:dep:1.0'  // trailing comment
        }
    """)
    assert [d["dep_name"] for d in got] == ["real:dep"], got


def test_a_same_file_variable_resolves_the_version(tmp_path):
    """The Egeria worked example: `bom/build.gradle` declares its constraints
    entirely as `ext { xVersion = '1.2.3' }` plus `api("g:a:${xVersion}")`.
    """
    got = _parse(tmp_path, """
        ext {
            jacksonVersion = '2.15.0'
        }
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${jacksonVersion}'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_version"] == "2.15.0"
    assert got[0]["dep_version_source"] == "variable_interpolation"


def test_a_variable_reference_with_no_matching_definition_stays_empty(tmp_path):
    """Known-negative for the variable-resolution path: a `${...}` reference
    to a name never assigned anywhere in the file must NOT resolve — proving
    the substitution can actually fail rather than always finding something.
    """
    got = _parse(tmp_path, """
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${jacksonVersion}'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_version"] == ""
    assert got[0]["dep_version_source"] == ""


def test_variable_substitution_requires_the_whole_field(tmp_path):
    """`${xVersion}-SNAPSHOT` is a partial interpolation. Splicing the resolved
    fragment into the surrounding text would be a guess about the rest of the
    string, not a resolution — this must stay empty rather than invent
    "2.15.0-SNAPSHOT", which nothing actually declared.
    """
    got = _parse(tmp_path, """
        ext {
            jacksonVersion = '2.15.0'
        }
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${jacksonVersion}-SNAPSHOT'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_version"] == "", got[0]
    assert got[0]["dep_version_source"] == ""


def test_a_non_version_named_variable_is_never_captured(tmp_path):
    """`description = '...'` must not become a substitutable "variable" just
    because it is an assignment — only names ending in Version are eligible,
    which is the real Gradle convention and the guard against an unrelated
    string leaking into `dep_version` by coincidence of name collision.
    """
    got = _parse(tmp_path, """
        description = 'jacksonVersion'
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${description}'
        }
    """)
    assert len(got) == 1, got
    assert got[0]["dep_version"] == ""


def test_version_catalog_resolves_a_whole_dependency_line(tmp_path):
    """`implementation(libs.alias)` — no quotes at all, so it needs its own
    regex; the catalog supplies both the coordinate and the version."""
    catalog_toml = tmp_path / "gradle"
    catalog_toml.mkdir()
    (catalog_toml / "libs.versions.toml").write_text(textwrap.dedent("""
        [versions]
        jackson = "2.17.0"

        [libraries]
        jackson-databind = { module = "com.fasterxml.jackson.core:jackson-databind", version.ref = "jackson" }
    """))
    catalog = DependencyParser()._load_gradle_version_catalog(tmp_path)
    assert catalog["libraries"]["jackson-databind"]["version"] == "2.17.0"

    got = _parse(tmp_path, """
        dependencies {
            implementation(libs.jackson.databind)
        }
    """, catalog=catalog)
    assert len(got) == 1, got
    assert got[0]["dep_name"] == "com.fasterxml.jackson.core:jackson-databind"
    assert got[0]["dep_version"] == "2.17.0"
    assert got[0]["dep_version_source"] == "version_catalog"


def test_version_catalog_resolves_an_inline_version_reference(tmp_path):
    """`"g:a:${libs.versions.x.get()}"` — the catalog supplies only the
    version; the coordinate is still written literally."""
    catalog = {"versions": {"jackson": "2.17.0"}, "libraries": {}}
    got = _parse(tmp_path, """
        dependencies {
            implementation 'com.fasterxml.jackson.core:jackson-databind:${libs.versions.jackson.get()}'
        }
    """, catalog=catalog)
    assert len(got) == 1, got
    assert got[0]["dep_version"] == "2.17.0"
    assert got[0]["dep_version_source"] == "version_catalog"


def test_an_unknown_catalog_accessor_records_nothing(tmp_path):
    """Known-negative for the catalog path: an alias absent from the catalog
    (or a build file read with no catalog loaded at all) must not fabricate a
    dependency from the accessor text — there is no coordinate to record.
    """
    got = _parse(tmp_path, """
        dependencies {
            implementation(libs.does.not.exist)
        }
    """, catalog={"versions": {}, "libraries": {}})
    assert got == [], got


def test_dedup_prefers_a_resolved_version_over_an_empty_one(tmp_path):
    """The real Egeria shape: a downstream module declares `g:a` with no
    version (BOM-managed) while the BOM's own file resolves `g:a`'s version
    via a same-file variable. Both end up keyed identically for dedup
    (`source_file` is just the filename, not the path), so whichever is
    recorded must be the resolved one — not whichever `rglob` happened to
    visit first, which is not a real ordering guarantee.
    """
    (tmp_path / "module").mkdir()
    (tmp_path / "module" / "build.gradle").write_text(textwrap.dedent("""
        dependencies {
            implementation 'org.slf4j:slf4j-api'
        }
    """))
    (tmp_path / "bom").mkdir()
    (tmp_path / "bom" / "build.gradle").write_text(textwrap.dedent("""
        ext { slf4jVersion = '2.0.13' }
        dependencies {
            api("org.slf4j:slf4j-api:${slf4jVersion}")
        }
    """))
    got = DependencyParser().parse(tmp_path, "test_project")
    by_name = {d["dep_name"]: d for d in got}
    assert by_name["org.slf4j:slf4j-api"]["dep_version"] == "2.0.13"
    assert by_name["org.slf4j:slf4j-api"]["dep_version_source"] == "variable_interpolation"


def test_the_real_egeria_checkout_yields_dependencies(tmp_path):
    """Non-vacuity against the actual project this was written for.

    Skipped rather than failed when the checkout is absent — but when it is
    present, zero results means the parser has regressed to the state that
    motivated it.
    """
    import os
    from pathlib import Path

    # Was a single hardcoded /Users/dwolfson path, so this check could only
    # ever run on one machine — everywhere else it skipped, which reads as a
    # pass. Same env-override-then-sibling idiom the egeria-advisor path
    # resolvers use: EGERIA_CHECKOUT wins, else a sibling of the repo root
    # (both the "egeria beside trellis" and "beside trellis's parent"
    # layouts), else skip.
    repo_root = Path(__file__).resolve().parents[3]
    candidates = []
    env_root = os.environ.get("EGERIA_CHECKOUT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates += [repo_root.parent / "egeria", repo_root.parent.parent / "egeria"]

    root = next((c for c in candidates if (c / "build.gradle").exists()), None)
    if root is None:  # pragma: no cover
        pytest.skip(
            "no egeria checkout found (set EGERIA_CHECKOUT, or place one beside "
            f"{repo_root.parent}); tried: {[str(c) for c in candidates]}"
        )

    deps = [d for d in DependencyParser().parse(root, "egeria_git")
            if d["source_file"].rsplit("/", 1)[-1].startswith("build.gradle")]
    assert len(deps) > 50, f"only {len(deps)} parsed from 229 build.gradle files"
    assert all(not d["dep_name"].startswith(":") for d in deps), "project() refs leaked in"
    assert all("$" not in d["dep_version"] for d in deps), "an unresolved variable reached dep_version"
    assert all(d["ecosystem"] == "java" for d in deps)

    # Variable resolution must still work — but asserted as an INVARIANT over
    # this checkout, not as a count.
    #
    # This was `len(versioned) > 50`, measured 2026-09-01 when Egeria's
    # `bom/build.gradle` carried ~80 coordinates versioned through its own `ext`
    # variables. It went red on 2026-09-09 at 36 of 72, and its failure message
    # said "same-file ext-variable resolution appears to have regressed" — which
    # it could not tell. Measured: Egeria's BOM now imports the Spring Boot
    # platform and declares 36 coordinates of its own instead of ~80, so the
    # other 36 are `implementation 'g:a'` with no version segment anywhere in the
    # repo. `_parse_gradle` records those with an empty version on purpose (its
    # own docstring: cve_scan reads an empty version as "we looked and cannot
    # answer", which beats both dropping and guessing). Nothing regressed; a
    # number about someone else's repository moved.
    #
    # The replacement asks the question the count was standing in for: does
    # every coordinate that HAS a knowable version end up with one? A genuine
    # break in `${xVersion}` substitution leaves coordinates unversioned whose
    # declaration plainly carries a version segment, and that fails here however
    # large or small Egeria's BOM happens to be.
    versioned = [d for d in deps if d["dep_version"]]
    assert versioned, (
        "no gradle dependency resolved a version at all — same-file "
        "ext-variable resolution has regressed"
    )
    gradle_text = "\n".join(
        f.read_text(encoding="utf-8", errors="ignore")
        for f in list(root.rglob("build.gradle")) + list(root.rglob("build.gradle.kts"))
    )
    missed = [d["dep_name"] for d in deps
              if not d["dep_version"]
              and re.search(re.escape(d["dep_name"]) + r":[^'\"\s]", gradle_text)]
    assert not missed, (
        f"{len(missed)} coordinate(s) are declared with a version segment and came "
        f"back unversioned, so a version this parser could read was dropped: "
        f"{sorted(missed)[:5]}"
    )
    # Every resolved version must carry provenance other than "declared" or
    # "declared" itself — never empty next to a non-empty dep_version, which
    # would be indistinguishable from a version this code invented.
    assert all(d["dep_version_source"] for d in versioned), (
        "a resolved dep_version with no recorded dep_version_source"
    )
