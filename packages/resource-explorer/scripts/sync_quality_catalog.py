"""Diff the vendored quality-check catalogue against an upstream checkout. Writes nothing.

    uv run python packages/resource-explorer/scripts/sync_quality_catalog.py --check \
        --upstream ~/localGit/egeria-v6/dataops-testgen

The vendored copy lives in `resource_explorer/configdata/quality/testgen/` (see its PROVENANCE.md). For each
vendored test type this compares, against `testgen/template/dbsetup_test_types/test_types_<Name>.yaml` upstream:
`selection_criteria`, `default_parm_columns`, `default_parm_values` and the Postgres `cat_test_conditions` entry
(`measure`, `test_operator`, `test_condition`; for a QUERY or METADATA type, `run_type` and the Postgres
`test_templates` entry), and says whether the whole file is still byte-identical. It checks
that every measure in `postgresql/profile_measures.sql`, the `general_type` CASE, the `logical_type` and `is_decimal`
CASEs of `postgresql/numeric_chars.sql`, every entry of
`postgresql/templated_functions.yaml` and every statement of `profiling/functional_datatype_date.sql` still appear
verbatim upstream, that each vendored generation template (`gen_query_tests/`) is byte-identical upstream, and lists
upstream test types that are not vendored.

Exit status: 0 when nothing a check depends on differs, 1 when something does, 2 when the upstream checkout is
unusable. Adopting a change is a deliberate commit (copy the file, update PROVENANCE.md, re-port any predicate
whose pinned hash the tests then report) — this script never does it.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

from resource_explorer.surveyors.database.quality.catalogue import (
    GENERATION_TEMPLATES_DIR,
    PORTED_TEST_TYPES,
    TEST_TYPES_DIR,
    read_raw,
    templated_functions,
)
from resource_explorer.surveyors.database.quality.functional_type import FUNCTIONAL_DATATYPE_SQL
from resource_explorer.surveyors.database.quality.general_type import (
    vendored_case_text,
    vendored_numeric_cases,
)
from resource_explorer.surveyors.database.quality.value_profile import measure_blocks

UPSTREAM_TEST_TYPES = Path("testgen/template/dbsetup_test_types")
UPSTREAM_PROFILE_SQL = Path("testgen/template/flavors/postgresql/profiling/project_profiling_query.sql")
UPSTREAM_SCHEMA_DDF = Path("testgen/template/flavors/postgresql/data_chars/get_schema_ddf.sql")
UPSTREAM_FUNCTIONS = Path("testgen/template/flavors/postgresql/profiling/templated_functions.yaml")
UPSTREAM_FUNCTIONAL_TYPE = Path("testgen/template/profiling/functional_datatype.sql")
UPSTREAM_GENERATION_TEMPLATES = Path("testgen/template/gen_query_tests")

FIELDS = ("selection_criteria", "default_parm_columns", "default_parm_values")
CONDITION_FIELDS = ("measure", "test_operator", "test_condition")


def _postgres(raw: dict) -> dict:
    return next((c for c in raw.get("cat_test_conditions") or [] if c.get("sql_flavor") == "postgresql"), {})


def _template(raw: dict) -> str:
    return next((t.get("template") for t in raw.get("test_templates") or [] if t.get("sql_flavor") == "postgresql"), "")


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compare_test_type(name: str, upstream: Path) -> tuple[list[str], list[str]]:
    """(differences that matter, notes) for one vendored test type."""
    vendored_path = TEST_TYPES_DIR / f"{name}.yaml"
    upstream_path = upstream / UPSTREAM_TEST_TYPES / f"test_types_{name}.yaml"
    if not upstream_path.exists():
        return [f"{name}: upstream file {upstream_path.relative_to(upstream)} is gone"], []
    ours, theirs = read_raw(vendored_path), read_raw(upstream_path)
    diffs: list[str] = []
    for field in FIELDS:
        if ours.get(field) != theirs.get(field):
            diffs.append(f"{name}.{field}: vendored {ours.get(field)!r} | upstream {theirs.get(field)!r}")
    ours_pg, theirs_pg = _postgres(ours), _postgres(theirs)
    for field in CONDITION_FIELDS:
        if ours_pg.get(field) != theirs_pg.get(field):
            diffs.append(f"{name}.postgresql.{field}: vendored {ours_pg.get(field)!r} | "
                         f"upstream {theirs_pg.get(field)!r}")
    if ours.get("run_type") not in (None, "CAT") or theirs.get("run_type") not in (None, "CAT"):
        # A QUERY or METADATA type has no cat_test_conditions: what runs is the Postgres `test_templates` entry
        # (Dupe_Rows' grouping subselect, the drift checks' reads), and how upstream runs it.
        if ours.get("run_type") != theirs.get("run_type"):
            diffs.append(f"{name}.run_type: vendored {ours.get('run_type')!r} | upstream {theirs.get('run_type')!r}")
        if _template(ours) != _template(theirs):
            diffs.append(f"{name}.postgresql.test_templates: no longer the vendored statement")
    notes = []
    if vendored_path.read_bytes() != upstream_path.read_bytes():
        notes.append(f"{name}: file differs from upstream in fields no check reads")
    return diffs, notes


def compare_sql(upstream: Path) -> list[str]:
    diffs: list[str] = []
    profile_sql = upstream / UPSTREAM_PROFILE_SQL
    if not profile_sql.exists():
        return [f"upstream {UPSTREAM_PROFILE_SQL} is gone"]
    upstream_text = _squash(profile_sql.read_text(encoding="utf-8"))
    for flag, measures in measure_blocks().items():
        for expr, alias in measures:
            if _squash(f"{expr} AS {alias}") not in upstream_text:
                diffs.append(f"profile_measures.sql [{flag}] {alias}: no longer verbatim upstream")
    ddf = upstream / UPSTREAM_SCHEMA_DDF
    if not ddf.exists():
        diffs.append(f"upstream {UPSTREAM_SCHEMA_DDF} is gone")
    else:
        ddf_text = _squash(ddf.read_text(encoding="utf-8"))
        if _squash(vendored_case_text()) not in ddf_text:
            diffs.append("general_type.sql: the general_type CASE is no longer verbatim upstream")
        diffs += [f"numeric_chars.sql CASE {i + 1}: no longer verbatim upstream"
                  for i, case in enumerate(vendored_numeric_cases()) if _squash(case) not in ddf_text]
    diffs += compare_functions(upstream)
    diffs += compare_functional_type(upstream)
    diffs += compare_whole_profiling_files(upstream)
    diffs += compare_percentiles(upstream)
    diffs += compare_text_shape(upstream)
    diffs += compare_step_8_sources(upstream)
    diffs += compare_generation_templates(upstream)
    return diffs


def compare_generation_templates(upstream: Path) -> list[str]:
    diffs = []
    for ours in sorted(GENERATION_TEMPLATES_DIR.glob("*.sql")):
        theirs = upstream / UPSTREAM_GENERATION_TEMPLATES / ours.name
        if not theirs.exists():
            diffs.append(f"upstream {UPSTREAM_GENERATION_TEMPLATES / ours.name} is gone")
        elif theirs.read_bytes() != ours.read_bytes():
            diffs.append(f"gen_query_tests/{ours.name}: differs from upstream")
    return diffs


def compare_functions(upstream: Path) -> list[str]:
    path = upstream / UPSTREAM_FUNCTIONS
    if not path.exists():
        return [f"upstream {UPSTREAM_FUNCTIONS} is gone"]
    import yaml

    theirs = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ours = yaml.safe_load((TEST_TYPES_DIR.parent / "postgresql" / "templated_functions.yaml").read_text()) or {}
    return [f"templated_functions.yaml {name}: no longer verbatim upstream"
            for name in sorted(ours) if theirs.get(name) != ours[name] or name not in templated_functions()]


def compare_whole_profiling_files(upstream: Path) -> list[str]:
    """Step 6 vendors the whole functional-type inference and the table-type stage, unchanged: byte-identical upstream."""
    diffs = []
    for ours in ("functional_datatype.sql", "functional_tabletype_stage.sql"):
        theirs = upstream / "testgen/template/profiling" / ours
        vendored = TEST_TYPES_DIR.parent / "profiling" / ours
        if not theirs.exists():
            diffs.append(f"upstream profiling/{ours} is gone")
        elif theirs.read_bytes() != vendored.read_bytes():
            diffs.append(f"profiling/{ours}: differs from upstream (re-port functional_infer.py)")
    return diffs


def compare_percentiles(upstream: Path) -> list[str]:
    """Step 6's `postgresql/percentiles.sql`: its three PERCENTILE_CONT lines still appear verbatim upstream."""
    from resource_explorer.surveyors.database.quality.percentiles import vendored_lines

    path = upstream / UPSTREAM_PROFILE_SQL
    if not path.exists():
        return [f"upstream {UPSTREAM_PROFILE_SQL} is gone"]
    text = _squash(path.read_text(encoding="utf-8"))
    return [f"percentiles.sql: {line.strip()!r} is no longer verbatim upstream"
            for line in vendored_lines() if _squash(line).rstrip(",") not in text]


def compare_text_shape(upstream: Path) -> list[str]:
    """Step 7's `postgresql/text_shape.sql`: each block still appears verbatim upstream, and still matches its pin."""
    from resource_explorer.surveyors.database.quality.text_shape import pins_match, vendored_blocks

    path = upstream / UPSTREAM_PROFILE_SQL
    if not path.exists():
        return [f"upstream {UPSTREAM_PROFILE_SQL} is gone"]
    text = _squash(path.read_text(encoding="utf-8"))
    return ([f"text_shape.sql block {name}: no longer verbatim upstream"
             for name, block in vendored_blocks().items() if _squash(block) not in text]
            + [f"text_shape.sql block {name}: differs from its pin in text_shape.TEXT_SHAPE_SHA256"
               for name in pins_match()])


def compare_step_8_sources(upstream: Path) -> list[str]:
    """Port step 8: the secondary profiling query is vendored unchanged (byte-identical upstream), and the five upstream
    files the value-hash SQL was written from still hash to the pins in `value_hashes.UPSTREAM_SHA256`."""
    import hashlib

    from resource_explorer.surveyors.database.quality import frequent_values, value_hashes

    diffs = []
    theirs = upstream / "testgen/template/flavors/postgresql/profiling/project_secondary_profiling_query.sql"
    if not theirs.exists():
        diffs.append("upstream project_secondary_profiling_query.sql is gone")
    elif theirs.read_bytes() != frequent_values.SECONDARY_SQL.read_bytes():
        diffs.append("project_secondary_profiling_query.sql: differs from upstream (re-read frequent_values.py)")
    for path, pinned in value_hashes.UPSTREAM_SHA256.items():
        source = upstream / "testgen" / path
        if not source.exists():
            diffs.append(f"upstream {path} is gone (value_hashes.py was written from it)")
        elif hashlib.sha256(source.read_bytes()).hexdigest() != pinned:
            diffs.append(f"upstream {path} changed since value_hashes.py was written from it: re-read it")
    return diffs


def vendored_statements() -> list[str]:
    """The statements of `profiling/functional_datatype_date.sql`, each the text after its `-- upstream lines`
    header (the header comment above them is RE's)."""
    blocks = FUNCTIONAL_DATATYPE_SQL.read_text(encoding="utf-8").split("-- upstream lines ")[1:]
    return [block.split("\n", 1)[1].strip() for block in blocks]


def compare_functional_type(upstream: Path) -> list[str]:
    path = upstream / UPSTREAM_FUNCTIONAL_TYPE
    if not path.exists():
        return [f"upstream {UPSTREAM_FUNCTIONAL_TYPE} is gone"]
    text = _squash(path.read_text(encoding="utf-8"))
    return [f"functional_datatype_date.sql statement {i + 1}: no longer verbatim upstream"
            for i, stmt in enumerate(vendored_statements()) if _squash(stmt) not in text]


def new_test_types(upstream: Path) -> list[str]:
    names = sorted(p.stem.removeprefix("test_types_") for p in (upstream / UPSTREAM_TEST_TYPES).glob("test_types_*.yaml"))
    return [n for n in names if n not in PORTED_TEST_TYPES]


def upstream_commit(upstream: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(upstream), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown (not a git checkout)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", required=True,
                        help="compare only (the only mode: this script never writes)")
    parser.add_argument("--upstream", required=True, type=Path, help="a TestGen checkout")
    args = parser.parse_args(argv)
    upstream = args.upstream.expanduser()
    if not (upstream / UPSTREAM_TEST_TYPES).is_dir():
        print(f"{upstream} is not a TestGen checkout (no {UPSTREAM_TEST_TYPES})", file=sys.stderr)
        return 2

    print(f"upstream: {upstream} @ {upstream_commit(upstream)}")
    diffs: list[str] = []
    notes: list[str] = []
    for name in PORTED_TEST_TYPES:
        d, n = compare_test_type(name, upstream)
        diffs += d
        notes += n
    diffs += compare_sql(upstream)
    for line in diffs:
        print(f"DIFF  {line}")
    for line in notes:
        print(f"note  {line}")
    others = new_test_types(upstream)
    print(f"{len(others)} upstream test type(s) not vendored: {', '.join(others) or 'none'}")
    print("in step with upstream" if not diffs else f"{len(diffs)} difference(s) a check depends on")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
