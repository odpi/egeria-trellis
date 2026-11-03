"""TestGen-only tests as RE proposals (`origin` `testgen`): DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md §6.5.

Calibration only (owner, 2026-10-21): TestGen's live runtime publishes nothing and runs on no schedule. Its export
(`scripts/export_testgen.py` in egeria-workspaces-fs, the compare harness's input) can still name a test RE did not
propose. This module turns those into PROPOSALS, never verdicts: a person accepts one in Curate and RE runs it.

The rules, as §6.5 fixes them:

- **Only a type RE has vendored** (`PORTED_TEST_TYPES`). RE runs every result itself, so a proposal it cannot execute
  would be a check that never runs. A TestGen test of an unported type is counted and listed by type, for the
  calibration evidence; it is not a proposal.
- **Verdicts win.** A key with a live rejected verdict is not imported; a live accepted one is a match. A key RE's own
  latest batch already proposes (proposed, accepted or suppressed) is a match too, and RE's row stands.
- **Parameters come from TestGen's test definition**, validated exactly as RE validates its own (the condition's
  value and the measure's placeholders render), so a value that would not run is a not_established row quoting it,
  never a proposal that fails on its first run.
- **Read-only on TestGen.** The input is the export file; nothing here reaches TestGen or its database.

Written as a new proposals batch: the latest batch with the imported rows merged in, so the review (latest batch
wins) shows both. `propose.run_propose` carries open imported rows forward into each later batch (`carry_forward`),
because no selection rule will re-derive them from RE's profile.
"""
from __future__ import annotations

from datetime import UTC, datetime

from resource_explorer.surveyors.database.quality.catalogue import (
    PORTED_TEST_TYPES,
    RUN_CAT,
    SEVERITY,
    ParameterError,
    as_number,
    catalogue_ref,
    check_key,
    load_catalogue,
    render_measure,
    threshold,
)

ORIGIN = "testgen"
PROPOSALS_TABLE = "database_quality_proposals"
#: TestGen's test-definition parameter columns RE's checks read, by the params_json key they fill (same names).
PARAM_COLUMNS = ("baseline_ct", "baseline_unique_ct", "baseline_value", "baseline_value_ct", "threshold_value",
                 "baseline_sum", "baseline_sd", "upper_tolerance")
#: Parameters kept as TestGen's text: a baseline date, and the sums Match_Sum and Match_Fractional_Sum compare exactly
#: (a float would round them). Each is still validated by `threshold` before it can be proposed.
TEXT_PARAMS = frozenset({"baseline_value", "baseline_sum"})
#: RE's own row for a key stands (the import is a match) when it is in one of these states.
RE_DECIDED = frozenset({"proposed", "accepted", "suppressed"})
#: The run date put into a measure while validating it: any well-formed instant will do; nothing is executed here.
_VALIDATE_RUN_DATE = "2000-01-01 00:00:00"


class TestGenImportError(ValueError):
    """The export cannot be imported for this database: the sentence says why."""


def _project(export: dict, slug: str) -> dict:
    projects = [p for p in export.get("projects") or [] if (p.get("re_slug") or p.get("database")) == slug]
    if not projects:
        names = sorted({str(p.get("re_slug") or p.get("database")) for p in export.get("projects") or []})
        raise TestGenImportError(f"the export has no project for {slug!r} (it has: {', '.join(names) or 'none'})")
    if len(projects) > 1:
        raise TestGenImportError(f"the export has {len(projects)} projects for {slug!r}; expected one")
    return projects[0]


def _params(test_type, definition: dict) -> dict:
    """RE's params_json from one TestGen test definition. TestGen keeps its parameters as text; counts become numbers
    here, and everything is validated by `threshold` and `render_measure` before it can be proposed."""
    if test_type.run_type != RUN_CAT:
        # Dupe_Rows' key and the drift checks' baselines are not the parameters below; importing TestGen's definition of
        # them is not built, and the row says so rather than carrying parameters nothing can run.
        raise ParameterError(f"{test_type.test_type} runs as {test_type.run_type}: importing TestGen's definition of "
                             f"it is not built yet")
    params: dict = {"baseline_ct": None, "baseline_value_ct": None, "baseline_value": None, "threshold_value": None}
    for column in PARAM_COLUMNS:
        value = definition.get(column)
        if value in (None, ""):
            continue
        if column in TEXT_PARAMS:
            params[column] = str(value)
        else:
            params[column] = as_number(column, value)
    severity = definition.get("severity") or test_type.default_severity
    if severity not in SEVERITY:
        raise ParameterError(f"TestGen severity {severity!r} is not Fail, Warning or Log")
    params["severity"] = SEVERITY[severity]
    threshold(test_type, params)
    render_measure(test_type, definition.get("column_name") or "column", params, _VALIDATE_RUN_DATE)
    return params


def _general_types(group: dict) -> dict[tuple[str, str, str], str]:
    return {(c.get("schema_name"), c.get("table_name"), c.get("column_name")): c.get("general_type") or ""
            for c in group.get("profile") or []}


def _reason(group: dict, definition: dict, export: dict) -> str:
    definition_id = definition.get("id") or definition.get("test_definition_id")
    return (f"imported from TestGen: test definition {definition_id or '(id not in the export)'} in suite "
            f"{group.get('test_suite') or '(unnamed)'}, test run {group.get('test_run_id') or '(not yet run)'}, "
            f"profile run {group.get('profile_run_id') or '(none)'}; exported {export.get('exported_at') or '?'} "
            f"from TestGen {export.get('testgen_version') or '(version not recorded)'}")


def build_rows(export: dict, slug: str, *, latest: list[dict], verdicts: dict) -> tuple[list[dict], dict]:
    """(proposal rows to add, summary) for one database. Pure: reads only its arguments."""
    project = _project(export, slug)
    catalogue = load_catalogue()
    re_rows = {r["check_key"]: r for r in latest}
    rows: list[dict] = []
    summary = {"imported_ct": 0, "not_established_ct": 0, "matched_re_ct": 0, "matched_accepted_ct": 0,
               "rejected_ct": 0, "inactive_ct": 0, "unported": {}, "table_groups": {}}
    for group in project.get("table_groups") or []:
        summary["table_groups"][group.get("table_group") or "?"] = group.get("tests_state") or "?"
        general_types = _general_types(group)
        for d in group.get("tests") or []:
            name = d.get("test_type") or ""
            if name not in PORTED_TEST_TYPES:
                summary["unported"][name] = summary["unported"].get(name, 0) + 1
                continue
            if (d.get("test_active") or "Y") != "Y":
                summary["inactive_ct"] += 1
                continue
            test_type = catalogue[name]
            schema, table, column = d.get("schema_name") or "", d.get("table_name") or "", d.get("column_name") or ""
            key = check_key(schema, table, column, name)
            if d.get("check_key") and d["check_key"] != key:
                raise TestGenImportError(f"the export's check_key {d['check_key']!r} is not RE's {key!r}: a different "
                                   f"exporter built it")
            verdict = (verdicts.get(key) or {}).get("verdict")
            if verdict == "rejected":
                summary["rejected_ct"] += 1
                continue
            if verdict == "accepted":
                summary["matched_accepted_ct"] += 1
                continue
            if (re_rows.get(key) or {}).get("state") in RE_DECIDED and re_rows[key].get("origin") != ORIGIN:
                summary["matched_re_ct"] += 1
                continue
            state, reason, params = "proposed", _reason(group, d, export), {}
            try:
                params = _params(test_type, d)
                params["profile_surveyed_at"] = group.get("profiled_at") or ""
            except ParameterError as exc:
                state, reason, params = "not_established", f"{exc}; {reason}", {}
            summary["imported_ct" if state == "proposed" else "not_established_ct"] += 1
            rows.append({
                "check_key": key, "schema_name": schema, "table_name": table, "column_name": column,
                "test_type": name, "origin": ORIGIN, "general_type": general_types.get((schema, table, column), ""),
                "profile_surveyed_at": group.get("profiled_at") or "", "params_json": params,
                "catalogue_ref": catalogue_ref(test_type), "state": state, "reason": reason,
            })
    return rows, summary


def _proposal_fields(row: dict) -> dict:
    keep = ("check_key", "schema_name", "table_name", "column_name", "test_type", "origin", "general_type",
            "profile_surveyed_at", "params_json", "catalogue_ref", "state", "reason")
    return {k: row.get(k) for k in keep}


def merge(own: list[dict], imported: list[dict]) -> list[dict]:
    """One batch from RE's own rows and imported rows. RE's row for a key stands when it decided the key (proposed,
    accepted, suppressed); an imported row replaces RE's not_established row for the same key, because TestGen's
    definition carries the parameters RE's profile could not give, and replaces an earlier import of the same key."""
    out = {r["check_key"]: r for r in own}
    for row in imported:
        mine = out.get(row["check_key"])
        if mine is None or mine.get("origin") == ORIGIN or mine.get("state") not in RE_DECIDED:
            out[row["check_key"]] = row
    return list(out.values())


def carry_forward(previous: list[dict], verdicts: dict) -> list[dict]:
    """The previous batch's imported rows, for the next batch, their state set from the verdicts as RE's own are:
    accepted stays accepted, rejected is suppressed, anything else stays as it was imported."""
    out = []
    for row in previous:
        if row.get("origin") != ORIGIN:
            continue
        row = _proposal_fields(row)
        verdict = verdicts.get(row["check_key"]) or {}
        if verdict.get("verdict") == "accepted":
            row["state"] = "accepted"
        elif verdict.get("verdict") == "rejected":
            row["state"] = "suppressed"
            row["reason"] = (f"rejected {str(verdict.get('created_at', ''))[:10]} by "
                             f"{verdict.get('decided_by') or 'unknown'}")
        elif row["state"] in ("accepted", "suppressed"):
            row["state"] = "proposed"  # the verdict that made it so was retired
        out.append(row)
    return out


def import_export(registry, slug: str, export: dict, *, dry_run: bool = False, surveyed_at: str | None = None) -> dict:
    """Import one database's TestGen-only tests as a new proposals batch. Returns the summary, with `surveyed_at`
    (the batch written, or "" on a dry run or when there was nothing to add)."""
    slug = registry._normalize_slug(slug)
    latest = registry.query_detail_rows(PROPOSALS_TABLE, slug)
    verdicts = registry.get_quality_verdicts(slug)
    rows, summary = build_rows(export, slug, latest=latest, verdicts=verdicts)
    summary["surveyed_at"] = ""
    if dry_run or not rows:
        return summary
    surveyed_at = surveyed_at or datetime.now(UTC).isoformat()
    if latest and surveyed_at <= latest[0]["surveyed_at"]:
        raise TestGenImportError(f"the new batch {surveyed_at} would not be later than the latest {latest[0]['surveyed_at']}")
    batch = merge([_proposal_fields(r) for r in latest], rows)
    registry.write_detail_rows(PROPOSALS_TABLE, slug, surveyed_at, rows=batch)
    summary["surveyed_at"] = surveyed_at
    return summary
