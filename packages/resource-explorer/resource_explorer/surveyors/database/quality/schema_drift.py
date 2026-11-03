"""The schema-drift checks: Match_Column_Ct, Match_Column_Type, Schema_Conformance and Schema_Drift. Zero-fetch.

DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md section 5, Q4 port order step 5. TestGen runs these as METADATA types
over its own tables (`data_column_chars`, `data_structure_log`). RE has no such tables; it has stored surveys. So each
check READS what RE already measured and runs no query against the surveyed database:

- **Schema_Drift** reads `db_derived.derive_schema_diff` (the column half of `schema_diff`: added, dropped, retyped
  columns between the two newest surveys) and the two surveys' table lists (the table half, as `db_change_rates`
  reads them: `schema_churn`). It re-derives no column delta.
- **Match_Column_Ct** and **Match_Column_Type** compare the newest survey with a baseline taken from a survey when
  the check was proposed (`baseline_survey_at`, kept in the parameters): upstream's `threshold_value` and
  `general_type`.
- **Schema_Conformance** compares with a declared schema (an ODCS contract), which RE does not hold. It never
  proposes (upstream has no selection rule for it either) and never concludes.

**A first survey is not "no drift".** With one survey, or no survey since the baseline, there is nothing to compare,
and every check says `not_established` with that sentence. Upstream's equivalent is a window with no earlier test
run, which "yields null and nothing is counted"; RE says so instead of counting nothing.

**Where this departs from upstream** (the note, section 5): Schema_Drift's window is the two newest stored surveys,
not "since the previous test run"; it is one check per table, not one per schema (upstream's rows are per table
too); and the baselines come from the stored survey, not from TestGen's profile run.

The words for each case are in `WORDS`, in one place, so the project owner's ruling on them is one edit.
"""
from __future__ import annotations

from dataclasses import dataclass

from resource_explorer.surveyors.database.quality.catalogue import (
    MUST_BE,
    ParameterError,
    TestType,
    as_number,
)
from resource_explorer.surveyors.database.quality.general_type import general_type

#: The sentence of each drift case. PROPOSED to the project owner (a table in the PR); the owner decides.
WORDS = {
    "first_survey": ("Only one survey of this database is stored, so there is nothing to compare it with. This is not "
                     "a finding that the schema is unchanged."),
    "no_survey": "No stored survey lists this database's columns, so there is nothing to compare.",
    "no_newer_survey": ("No survey has been stored since the baseline ({baseline}), so there is nothing to compare "
                        "with yet. This is not a finding that the table is unchanged."),
    "no_baseline": "The accepted check does not say which survey its baseline came from, so it cannot be compared.",
    "unchanged": "No column was added, dropped or retyped between the surveys of {old} and {new}.",
    "added": "{n} column{s} added since {old}: {names}.",
    "dropped": "{n} column{s} dropped since {old}: {names}.",
    "retyped": "{n} column{s} retyped since {old}: {names}.",
    "table_added": "Table added since {old}: it has {n} column{s}.",
    "table_dropped": "Table dropped since {old}: it had {n} column{s}.",
    "table_unknown": "The table is in neither of the two newest surveys ({old}, {new}), so its drift is not known.",
    "count_held": "{n} columns, as at the baseline; compared with the survey of {new}.",
    "count_changed": "{n} columns in the survey of {new}, {baseline_ct} at the baseline.",
    "count_gone": "The table is not in the survey of {new} (0 columns); {baseline_ct} at the baseline.",
    "type_held": ("General type {g} as at the baseline; compared with the survey of {new}. A change of database type "
                  "inside the same general type is Schema_Drift's to report."),
    "type_changed": "General type {g} in the survey of {new}, {baseline_g} at the baseline.",
    "type_gone": "The column is not in the survey of {new}; its general type was {baseline_g} at the baseline.",
    "no_declared_schema": ("Resource Explorer holds no declared schema (a data contract) for this table, so there is "
                           "nothing to conform to."),
}


def _plural(n: int) -> str:
    return "" if n == 1 else "s"


@dataclass(frozen=True)
class Snapshot:
    surveyed_at: str
    source: str
    columns: list[dict]

    def by_table(self) -> dict[tuple[str, str], dict[str, dict]]:
        out: dict[tuple[str, str], dict[str, dict]] = {}
        for c in self.columns:
            out.setdefault((c["schema_name"], c["table_name"]), {})[c["column_name"]] = c
        return out


def snapshots(registry, slug: str, limit: int = 2) -> list[Snapshot]:
    """The newest surveys that carry column rows, newest first (at most `limit`), chosen as `derive_schema_diff`
    chooses them: `db_derived._snapshot_keys`, which leaves out a survey marked invalid."""
    from resource_explorer.surveyors.database.db_derived import _snapshot_keys

    found: list[Snapshot] = []
    for surveyed_at, source in _snapshot_keys(registry, slug):
        columns = registry.query_detail_rows("database_columns", slug, surveyed_at, source)
        if not columns:
            continue
        found.append(Snapshot(surveyed_at, source, columns))
        if len(found) == limit:
            break
    return found


def current_inventory(registry, slug: str) -> tuple[str | None, dict[tuple[str, str], int]]:
    """(the newest survey's stamp, columns per table in it), for propose's baselines. (None, {}) when none is stored."""
    latest = snapshots(registry, slug, limit=1)
    if not latest:
        return None, {}
    return latest[0].surveyed_at, {k: len(v) for k, v in latest[0].by_table().items()}


def default_params(test_type: TestType, profile: dict) -> dict:
    """The parameters a proposal starts with. `profile` is the table's (or column's) profile row with
    `column_ct` and `inventory_surveyed_at` added by propose from `current_inventory`.

    Match_Column_Ct (`gen_Match_Column_Ct.sql`): `threshold_value` is the table's column count. Match_Column_Type
    (`default_parm_values` `general_type`): `baseline_value` is the column's general type. Both also keep the survey
    they were taken from (`baseline_survey_at`), which the run needs to know whether anything newer exists."""
    params: dict = {"severity": test_type.severity}
    if test_type.test_type == "Schema_Drift":
        return params
    at = profile.get("inventory_surveyed_at")
    if not at:
        raise ParameterError("no stored survey lists this database's columns, so there is no baseline to take")
    params["baseline_survey_at"] = at
    if test_type.test_type == "Match_Column_Ct":
        if profile.get("column_ct") is None:
            raise ParameterError("the newest survey does not list this table, so its column count is not known")
        params["threshold_value"] = profile["column_ct"]
    elif test_type.test_type == "Match_Column_Type":
        if not profile.get("general_type"):
            raise ParameterError("the column's general type is not known")
        params["baseline_value"] = profile["general_type"]
    else:
        raise ParameterError(f"{test_type.test_type} has no default parameters here")
    return params


class Drift:
    """The stored surveys a run compares, read once for all the drift checks of a run."""

    def __init__(self, registry, slug: str):
        self.snaps = snapshots(registry, slug)
        self.diff = None
        if len(self.snaps) >= 2:
            from resource_explorer.surveyors.database.db_derived import derive_schema_diff

            self.diff = derive_schema_diff(registry, slug)

    @property
    def latest(self) -> Snapshot | None:
        return self.snaps[0] if self.snaps else None

    def newer_than(self, baseline_at: str) -> bool:
        from resource_explorer.surveyors.database.db_derived import _parse_ts

        latest, base = _parse_ts(self.latest.surveyed_at), _parse_ts(baseline_at)
        if latest is None or base is None:
            return self.latest.surveyed_at > baseline_at
        return latest > base


def _not_established(row: dict, sentence: str) -> None:
    row.update(status="not_established", reason=sentence)


def evaluate(drift: Drift, row: dict, test_type: TestType, params: dict, fail_status: dict) -> None:
    """Conclude one drift check's result row from the stored surveys. `fail_status`: severity -> the status a failing
    check gets (`run_checks.FAIL_STATUS`)."""
    name = test_type.test_type
    severity = params.get("severity")
    if severity not in fail_status:
        row["reason"] = f"severity must be one of {sorted(fail_status)}, got {severity!r}"
        return
    row["severity"] = severity
    fail = fail_status[severity]
    schema, table, column = row["schema_name"], row["table_name"], row["column_name"]
    if name == "Schema_Conformance":
        row["comparison_operator"] = ""
        _not_established(row, WORDS["no_declared_schema"])
        return
    if name == "Schema_Drift":
        row["comparison_operator"] = MUST_BE[">"]
        row["threshold_values_json"] = [0]
        _evaluate_drift(drift, row, (schema, table), fail)
        return
    row["comparison_operator"] = MUST_BE["<>"]
    baseline_at = params.get("baseline_survey_at")
    if drift.latest is None:
        _not_established(row, WORDS["no_survey"])
        return
    if not baseline_at:
        _not_established(row, WORDS["no_baseline"])
        return
    if not drift.newer_than(baseline_at):
        _not_established(row, WORDS["no_newer_survey"].format(baseline=baseline_at))
        return
    new = drift.latest.surveyed_at
    tables = drift.latest.by_table()
    if name == "Match_Column_Ct":
        try:
            baseline_ct = int(as_number("threshold_value", params.get("threshold_value")))
        except ParameterError as exc:
            row["reason"] = str(exc)
            return
        row["threshold_values_json"] = [baseline_ct]
        count = len(tables.get((schema, table), {}))
        row["measure"] = count
        if count == baseline_ct:
            row.update(status="pass", reason=WORDS["count_held"].format(n=count, new=new))
        else:
            words = "count_gone" if count == 0 else "count_changed"
            row.update(status=fail, reason=WORDS[words].format(n=count, new=new, baseline_ct=baseline_ct))
        return
    baseline_g = params.get("baseline_value")
    if not isinstance(baseline_g, str) or not baseline_g:
        row["reason"] = f"baseline_value must be a general type, got {baseline_g!r}"
        return
    row["threshold_values_json"] = [baseline_g]
    current = tables.get((schema, table), {}).get(column)
    if current is None:
        row.update(status=fail, measure=0, reason=WORDS["type_gone"].format(new=new, baseline_g=baseline_g))
        return
    g = general_type(current.get("base_type") or current.get("data_type"))
    row["measure_text"] = g
    if g == baseline_g:
        row.update(status="pass", measure=1, reason=WORDS["type_held"].format(g=g, new=new))
    else:
        row.update(status=fail, measure=0,
                   reason=WORDS["type_changed"].format(g=g, new=new, baseline_g=baseline_g))


def _evaluate_drift(drift: Drift, row: dict, table_key: tuple[str, str], fail: str) -> None:
    if len(drift.snaps) < 2 or drift.diff is None:
        _not_established(row, WORDS["first_survey"] if drift.snaps else WORDS["no_survey"])
        return
    if drift.diff.get("state") != "measured":
        _not_established(row, drift.diff.get("explanation") or WORDS["first_survey"])
        return
    new_snap, old_snap = drift.snaps
    new_tables, old_tables = new_snap.by_table(), old_snap.by_table()
    old, new = old_snap.surveyed_at, new_snap.surveyed_at
    schema, table = table_key
    in_new, in_old = table_key in new_tables, table_key in old_tables
    if not in_new and not in_old:
        _not_established(row, WORDS["table_unknown"].format(old=old, new=new))
        return
    if in_new != in_old:
        n = len((new_tables if in_new else old_tables)[table_key])
        row.update(status=fail, measure=n,
                   reason=WORDS["table_added" if in_new else "table_dropped"].format(old=old, n=n, s=_plural(n)))
        return
    prefix = f"{schema}.{table}."
    added_set, dropped_set = set(drift.diff["columns_added"]), set(drift.diff["columns_dropped"])
    retyped = {r["qualified_name"]: r for r in drift.diff["columns_retyped"]}
    added = sorted(c for c in new_tables[table_key] if prefix + c in added_set)
    dropped = sorted(c for c in old_tables[table_key] if prefix + c in dropped_set)
    changed = sorted(c for c in new_tables[table_key] if prefix + c in retyped)
    row["measure"] = len(added) + len(dropped) + len(changed)
    if not row["measure"]:
        row.update(status="pass", reason=WORDS["unchanged"].format(old=old, new=new))
        return
    parts = []
    for key, columns, names in (("added", added, added), ("dropped", dropped, dropped),
                                ("retyped", changed, [f"{c} ({retyped[prefix + c]['old_type']} → "
                                                      f"{retyped[prefix + c]['new_type']})" for c in changed])):
        if columns:
            parts.append(WORDS[key].format(n=len(columns), s=_plural(len(columns)), old=old, names=", ".join(names)))
    row.update(status=fail, reason=" ".join(parts))
