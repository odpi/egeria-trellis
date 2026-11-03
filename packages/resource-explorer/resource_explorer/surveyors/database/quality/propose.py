"""`propose_quality_checks`: the vendored selection rules over the latest value profile. Zero-fetch.

DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md §5. Opens no connection: it reads the latest `database_value_profiles`
batch and the current verdicts, and writes one `database_quality_proposals` batch. For each (column, test type)
the ported rule (`catalogue.PREDICATES`) gives one of three answers:

- true: a proposal, with the catalogue's default parameters evaluated against the profile;
- false: nothing stored, only counted (`not_applicable_ct`);
- NULL (an input was not measured): `not_established`, listed by column with the inputs that were missing.
  Never a silent false. A column the profile marked `not_applicable` (a view or foreign table) is counted as
  not applicable instead: there is nothing to check, rather than something we could not measure.

Row_Ct_Pct is proposed by RE itself (origin `re`) for every table with `record_ct > 0`.

The Table Comparison types (B1a and B1b, `Match_*`) are considered only for a table a person opted in
(`table_comparison.watched_tables`), or for a check that already has a verdict, so an opt-out never hides a decision.
A table that is not opted in is counted (`not_opted_in_ct`), not proposed.

A TestGen-only proposal (origin `testgen`, written by `testgen_import`) is not re-derived from the profile, so each run
carries the previous batch's forward, its state set from the verdicts like any other, unless this run's own rule
decided the same check (`testgen_import.merge`).

Two rules read an instant as well as the profile: Recency reads the profile's own (its `surveyed_at`, TestGen's
`run_date`), and Future_Date_1Y reads `{AS_OF_DATE}`, the instant checks are generated, which is this run's
`surveyed_at` (passed to the rule as `as_of_date`).

Verdicts (append-only, latest per key wins) shape what a proposal becomes:
- accepted: the row is `accepted`, and `reason` says whether the new profile drifted from the accepted
  baseline. Drift is a fact for the reviewer; nothing is re-applied.
- rejected: `suppressed`, until the column's general_type changes or the selection outcome flips (a propose run
  after the rejection in which the rule did not select it). "Rejected" means "not for this column".
- retired, or no verdict: proposed afresh. An open proposal is superseded by the next batch; nothing is deleted.
"""
from __future__ import annotations

from datetime import UTC, datetime

from resource_explorer.surveyors.database.quality import (
    dupe_rows,
    functional_infer,
    schema_drift,
    table_comparison,
)
from resource_explorer.surveyors.database.quality.catalogue import (
    PREDICATES,
    RUN_METADATA,
    ParameterError,
    TestType,
    catalogue_ref,
    check_key,
    default_params,
    load_catalogue,
    not_established_inputs,
)
from resource_explorer.surveyors.database.quality.general_type import general_type

#: What a run of this step leaves on the database in Egeria (design: a dispatched step never makes an empty report).
CHECK_NAME = "quality_proposals"
ITEM_KEY = "proposals"

PROPOSALS_TABLE = "database_quality_proposals"
PROFILE_TABLE = "database_value_profiles"
ANALYSIS_STEP = "propose_quality_checks"

#: Parameters compared between the accepted check and the new profile's defaults to report drift.
BASELINE_KEYS = ("baseline_ct", "baseline_value_ct", "baseline_unique_ct", "baseline_value", "baseline_sum",
                 "baseline_sd")
#: Types whose baseline is their threshold_value (gen_Match_Row_Ct.sql: `record_ct AS threshold_value`).
THRESHOLD_IS_BASELINE = frozenset({"Match_Row_Ct", "Match_Column_Ct"})


#: The words for a text column whose sampled text shape is not in the profile row (port step 7): a row profiled before
#: the measures existed, or a column whose read did not finish (the run summary's `text_shape_not_measured` says which).
TEXT_SHAPE_NOT_READ = ("its sampled text shape was not measured (profiled before text shape was taken, or this "
                       "column's read did not finish): run the value profile again")


#: The words for a column whose value hashes are not in the profile row (port step 8): they are off by default.
HASHES_NOT_READ = ("value hashes were not taken (they are off by default: set VALUE_PROFILE_HASHES=true and run the "
                   "value profile again) or this column's hash read did not finish (the run summary's "
                   "hashes_not_measured says which)")


def _not_established_reason(test_type: str, profile: dict) -> str:
    state = profile.get("state") or "measured"
    if state != "measured":
        why = profile.get("reason") or "no reason recorded"
        return f"not profiled ({state}: {why}), so its selection rule could not be evaluated"
    missing = not_established_inputs(test_type, profile)
    # The functional-type inputs say why in their own words ("type not inferred · ..."), not as a bare NULL.
    words = [profile.get("fdt_why") or "type not inferred" if m == "functional_data_type"
             else profile.get("ftt_why") or "table type not inferred" if m == "functional_table_type"
             else TEXT_SHAPE_NOT_READ if m == "sample_value_ct"
             else HASHES_NOT_READ if m == "hash_state" else m
             for m in missing]
    if len(words) != len(missing) or words != missing:
        plain = [w for w, m in zip(words, missing, strict=True) if w == m]
        said = [w for w, m in zip(words, missing, strict=True) if w != m]
        parts = ([f"reads {', '.join(plain)}, which the profile did not measure"] if plain else []) + said
        return "its selection rule " + "; ".join(parts) if plain else "; ".join(said)
    if profile.get("record_ct") == 0:
        return f"the table was empty when profiled, so {', '.join(missing) or 'an input'} is undefined"
    if profile.get("value_ct") == 0 and missing and set(missing) <= {"min_date", "max_date"}:
        # Measured, and the column held no value: its date bounds do not exist. Not "the profile did not measure".
        return f"the column had no values when profiled, so {', '.join(missing)} is undefined"
    return f"its selection rule reads {', '.join(missing) or 'an input'}, which the profile did not measure"


def _drift(accepted: dict, current: dict, test_type: str = "") -> str:
    changes = []
    keys = BASELINE_KEYS + (("threshold_value",) if test_type in THRESHOLD_IS_BASELINE else ())
    for key in keys:
        before, after = accepted.get(key), current.get(key)
        if before is not None and after is not None and str(before) != str(after):
            changes.append(f"{key} {before} -> {after}")
    if not changes:
        return "no drift from the accepted baseline"
    return "the profile has drifted from the accepted baseline: " + "; ".join(changes)


class _Batches:
    """The proposal keys of earlier batches, read once each (for the rejected-verdict flip rule)."""

    def __init__(self, registry, slug: str):
        self.registry, self.slug = registry, slug
        self.batches = registry.list_detail_batches(PROPOSALS_TABLE, slug)
        self._keys: dict[str, set[str]] = {}

    def keys(self, batch: str) -> set[str]:
        if batch not in self._keys:
            self._keys[batch] = {r["check_key"] for r in
                                 self.registry.query_detail_rows(PROPOSALS_TABLE, self.slug, batch)}
        return self._keys[batch]

    def flipped_since(self, key: str, since: str) -> bool:
        """True when a batch after `since` did not carry `key`: the rule did not select it then."""
        return any(key not in self.keys(b) for b in self.batches if b > since)


def build_proposal_annotation(slug: str, *, run_id: str, proposed_ct: int, columns_profiled: int,
                              accepted_ct: int = 0, suppressed_ct: int = 0, not_established_ct: int = 0,
                              review_link: str = "", proposed_keys=()):
    """The ONE annotation a run of `propose_quality_checks` leaves on the database. Pure.

    N > 0 proposed: a `RequestForActionAnnotation`, "review N proposed quality checks in Resource Explorer", carrying
    the count, the run id and RE's link to the review place (`review_link`, empty when `RE_PUBLIC_URL` is not
    configured: the key is then left out, never a guessed localhost).
    0 proposed: a `ResourceMeasureAnnotation`, "0 checks proposed · N columns profiled · no selection rule matched".
    That sentence is the design's and is kept whenever no rule left a check standing; if accepted or suppressed
    checks remain the tail names them rather than claim no rule matched, and checks whose rule could not be
    evaluated (not established) are counted after it.

    `proposed_keys`: the batch's proposed check keys. The request carries their digest (`proposedChecksDigest`, a
    sha256 of the sorted keys), so the profile publish reshape re-links it only when the same checks are proposed,
    not merely as many (round 2, architect ruling 2026-10-24: the request re-links when unchanged)."""
    import hashlib

    from resource_explorer.surveyors.survey_report import (
        RequestForActionAnnotation,
        ResourceMeasureAnnotation,
    )

    counts = {"proposedChecks": str(proposed_ct), "columnsProfiled": str(columns_profiled),
              "acceptedChecks": str(accepted_ct), "suppressedChecks": str(suppressed_ct),
              "notEstablishedChecks": str(not_established_ct)}
    if proposed_ct > 0:
        # The design's words, verbatim, whatever N is.
        request = f"review {proposed_ct} proposed quality checks in Resource Explorer"
        detail = {"runId": run_id, **counts,
                  "proposedChecksDigest": hashlib.sha256("\n".join(sorted(proposed_keys)).encode()).hexdigest()}
        if review_link:
            detail["reviewLink"] = review_link
        return RequestForActionAnnotation(
            summary=f"{proposed_ct} quality checks proposed for {slug}",
            analysis_step=ANALYSIS_STEP, annotation_type_name="Proposed Quality Checks",
            check_name=CHECK_NAME, item_key=ITEM_KEY, label="proposed",
            action_requested=request, action_target_name=slug,
            explanation=(f"Resource Explorer proposed {proposed_ct} quality checks in run {run_id}. "
                         + (f"Review them at {review_link}." if review_link else
                            "Review them in Resource Explorer's Curate tab for this database.")),
            additional_properties=detail)
    standing = [f"{n} {word}" for n, word in ((accepted_ct, "accepted"), (suppressed_ct, "suppressed")) if n]
    # The design's sentence stands whenever no rule left a check standing. If one did (an accepted or a suppressed
    # check), "no selection rule matched" would be false, so the tail names them instead. A check whose rule could not
    # be evaluated is added in either case, as a count the reader can see.
    tail = "no selection rule matched" if not standing else f"no new check to propose ({', '.join(standing)})"
    if not_established_ct:
        tail += f" · {not_established_ct} not established"
    summary = f"0 checks proposed · {columns_profiled} columns profiled · {tail}"
    return ResourceMeasureAnnotation(
        summary=summary, analysis_step=ANALYSIS_STEP, annotation_type_name="Proposed Quality Checks",
        check_name=CHECK_NAME, item_key=ITEM_KEY, label="measured",
        explanation=summary,
        resource_properties={"runId": run_id, **counts},
        additional_properties={"producingStep": ANALYSIS_STEP, "resultState": "measured", "measuredAt": run_id,
                               "producingRun": run_id})


def run_propose(registry, slug: str, *, row_ct_pct_threshold: float, surveyed_at: str | None = None,
                review_link: str = "") -> dict:
    surveyed_at = surveyed_at or datetime.now(UTC).isoformat()
    profile = registry.query_detail_rows(PROFILE_TABLE, slug)
    if not profile:
        return {"quality_proposals": {
            "surveyed_at": surveyed_at, "state": "not_established",
            "reason": "no value profile stored: run the value profile first; nothing was proposed",
        }}
    profile_surveyed_at = profile[0]["surveyed_at"]
    # The most frequent values of the batch's eligible text columns (port step 8b), from the child table of the same
    # batch, as TestGen's `frequent_values` JSON ({"values": [{"value", "ct"}], "other": {...}}).
    frequent: dict[tuple, dict] = {}
    for fv in registry.query_detail_rows("database_value_profile_frequent_values", slug):
        if fv["surveyed_at"] != profile_surveyed_at:
            continue
        entry = frequent.setdefault((fv["schema_name"], fv["table_name"], fv["column_name"]), {"values": []})
        if fv.get("value") is None:
            entry["other"] = {"distinct_ct": fv.get("other_distinct_ct"), "ct": fv.get("value_ct")}
        else:
            entry["values"].append({"value": fv["value"], "ct": fv["value_ct"]})
    profile = [{**r, "frequent_values": frequent.get((r["schema_name"], r["table_name"], r["column_name"]))}
               for r in profile]
    catalogue = load_catalogue()
    verdicts = registry.get_quality_verdicts(slug)
    batches = _Batches(registry, slug)
    watched = table_comparison.watched_tables(registry, slug)
    # What the Dupe_Rows key and the drift baselines read: the stored inventory, in column order, and the newest survey.
    inventory_at, column_counts = schema_drift.current_inventory(registry, slug)
    table_columns: dict[tuple[str, str], list[tuple[str, str]]] = {}
    profiled_types = {(r["schema_name"], r["table_name"], r["column_name"]): r.get("general_type") for r in profile}
    for c in registry.query_detail_rows("database_columns", slug):
        key_ = (c["schema_name"], c["table_name"])
        gtype = profiled_types.get((*key_, c["column_name"])) or general_type(c.get("base_type") or c.get("data_type"))
        table_columns.setdefault(key_, []).append((c["column_name"], gtype))

    # The functional-type inference over the stored measures, for the labels a column could be (functional_infer.effective)
    # and the table periods; a stored label is used as it is.
    catalog_types = {(c["schema_name"], c["table_name"], c["column_name"]): c.get("base_type") or c.get("data_type")
                     for c in registry.query_detail_rows("database_columns", slug)}
    positions = {(c["schema_name"], c["table_name"], c["column_name"]): c.get("ordinal_position")
                 for c in registry.query_detail_rows("database_columns", slug) if c.get("ordinal_position") is not None}
    inferred = functional_infer.infer_batch(functional_infer._inference_rows(profile, catalog_types, None, positions))
    record_series = registry.value_profile_record_counts(slug)

    def with_inference(prow: dict) -> dict:
        possible, why = functional_infer.effective(prow, inferred.get(
            (prow["schema_name"], prow["table_name"], prow["column_name"])))
        extra = {"fdt_possible": possible, "fdt_why": why, "ftt_why": ""}
        if prow.get("functional_table_type") is None:
            period, period_why = functional_infer.table_period(record_series.get((prow["schema_name"], prow["table_name"]), []))
            extra["ftt_why"] = ("table type not inferred · " + (period_why or "profiled before table types were stored: "
                                "run the value profile again" if period else period_why))
        return {**prow, **extra}

    rows: list[dict] = []
    not_applicable_ct = 0
    not_opted_in_ct = 0

    def consider(test_type: TestType, prow: dict, column: str, general_type: str) -> None:
        nonlocal not_applicable_ct, not_opted_in_ct
        key = check_key(prow["schema_name"], prow["table_name"], column, test_type.test_type)
        if (test_type.is_table_comparison and (prow["schema_name"], prow["table_name"]) not in watched
                and key not in verdicts):
            # Not opted into Table Comparison: not asked for, so not proposed (and not "the rule said no").
            not_opted_in_ct += 1
            return
        if prow.get("state") == "not_applicable" and key not in verdicts:
            # A view or foreign table: the profile does not apply, so neither does any check on it.
            not_applicable_ct += 1
            return
        prow = with_inference(prow)
        selected = PREDICATES[test_type.test_type].evaluate({**prow, "as_of_date": surveyed_at})
        params: dict = {}
        state, reason = "proposed", ""
        if selected is None:
            state, reason = "not_established", _not_established_reason(test_type.test_type, prow)
        elif selected:
            table_key = (prow["schema_name"], prow["table_name"])
            try:
                if test_type.test_type == "Dupe_Rows":
                    params = dupe_rows.default_params(test_type, {**prow, "table_columns": table_columns.get(table_key)})
                elif test_type.run_type == RUN_METADATA:
                    params = schema_drift.default_params(test_type, {
                        **prow, "column_ct": column_counts.get(table_key), "inventory_surveyed_at": inventory_at})
                else:
                    params = default_params(test_type, prow, row_ct_pct_threshold)
                params["profile_surveyed_at"] = profile_surveyed_at
            except ParameterError as exc:
                state, reason = "not_established", str(exc)

        verdict = verdicts.get(key)
        kind = (verdict or {}).get("verdict")
        if kind == "accepted":
            accepted = verdict.get("params_json") if isinstance(verdict.get("params_json"), dict) else {}
            if selected is False:
                reason = "accepted; the selection rule no longer selects it in this profile"
            elif state == "not_established":
                reason = f"accepted; this profile could not re-evaluate it: {reason}"
            else:
                reason = f"accepted; {_drift(accepted, params, test_type.test_type)}"
            state = "accepted"
        elif kind == "rejected" and selected is not False:
            earlier = registry.get_quality_proposal(verdict.get("proposal_id")) or {}
            if earlier.get("check_key") != key:
                earlier = {}
            type_changed = bool(earlier.get("general_type")) and earlier.get("general_type") != general_type
            # "Since" is the batch the person judged, a scan time like every batch it is compared with; the
            # verdict's own wall-clock time is the fallback only when that proposal cannot be found.
            since = earlier.get("surveyed_at") or verdict.get("created_at") or ""
            if type_changed or batches.flipped_since(key, since):
                pass  # proposed afresh: the column is no longer what was rejected
            else:
                state = "suppressed"
                reason = f"rejected {verdict.get('created_at', '')[:10]} by {verdict.get('decided_by') or 'unknown'}"
        elif selected is False:
            not_applicable_ct += 1
            return

        rows.append({
            "check_key": key, "schema_name": prow["schema_name"], "table_name": prow["table_name"],
            "column_name": column, "test_type": test_type.test_type, "origin": test_type.origin,
            "general_type": general_type, "profile_surveyed_at": profile_surveyed_at, "params_json": params,
            "catalogue_ref": catalogue_ref(test_type), "state": state, "reason": reason,
        })

    # A type with no selection rule that RE does not propose itself (Schema_Conformance) is defined by a person.
    proposable = [t for t in catalogue.values() if not t.manual_only]
    column_types = [t for t in proposable if not t.is_table_check]
    table_types = [t for t in proposable if t.is_table_check]
    tables: dict[tuple[str, str], list[dict]] = {}
    for prow in profile:
        tables.setdefault((prow["schema_name"], prow["table_name"]), []).append(prow)
        for test_type in column_types:
            consider(test_type, prow, prow["column_name"], prow.get("general_type") or "")
    for (_schema, _table), prows in sorted(tables.items()):
        # The table's record_ct is on every measured column's row; an unprofiled table has none.
        table_row = next((r for r in prows if r.get("state") == "measured"), prows[0])
        for test_type in table_types:
            consider(test_type, table_row, "", "")

    from resource_explorer.surveyors.database.quality import testgen_import

    previous = registry.query_detail_rows(PROPOSALS_TABLE, slug)
    carried = testgen_import.carry_forward(previous, verdicts)
    rows = testgen_import.merge(rows, carried)
    registry.write_detail_rows(PROPOSALS_TABLE, slug, surveyed_at, rows=rows)
    states: dict[str, int] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
    not_established = {}
    for row in rows:
        if row["state"] == "not_established":
            path = f"{row['schema_name']}.{row['table_name']}" + (f".{row['column_name']}" if row["column_name"] else "")
            not_established.setdefault(path, []).append({"test_type": row["test_type"], "reason": row["reason"]})
    columns_profiled = sum(1 for r in profile if r.get("state") == "measured")
    annotation = build_proposal_annotation(
        slug, run_id=surveyed_at, proposed_ct=states.get("proposed", 0), columns_profiled=columns_profiled,
        accepted_ct=states.get("accepted", 0), suppressed_ct=states.get("suppressed", 0),
        not_established_ct=states.get("not_established", 0), review_link=review_link,
        proposed_keys=[r["check_key"] for r in rows if r["state"] == "proposed"])
    return {"annotations": [annotation], "quality_proposals": {
        "surveyed_at": surveyed_at,
        "state": "measured",
        "profile_surveyed_at": profile_surveyed_at,
        "proposed_ct": states.get("proposed", 0),
        "accepted_ct": states.get("accepted", 0),
        "suppressed_ct": states.get("suppressed", 0),
        "not_established_ct": states.get("not_established", 0),
        "not_applicable_ct": not_applicable_ct,
        "not_opted_in_ct": not_opted_in_ct,
        "tables_opted_in": len(watched),
        "testgen_carried_ct": sum(1 for r in rows if r.get("origin") == testgen_import.ORIGIN),
        "not_established_by_column": not_established,
    }}
