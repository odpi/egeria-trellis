"""Q4 port order step 6 (docs/design-notes/DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md): the rest of the
functional-type inference, its two stored results, and the nineteen family C types whose rules read it.

Covered: the vendored files and every pin; the inference statement by statement over hand-made profile rows (a label is the
one label the set holds, and a column the measures cannot settle says why instead of getting a default type); the table
period and type; that the profile stores both results and an old row reads NULL; the selection rules (a numeric column is a
real "no" for a text rule, a text column is `not_established` and says what is missing); the defaults and the rendered
measures. Nothing connects to a surveyed database or to Egeria.
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from resource_explorer.surveyors.database.quality import catalogue as cat
from resource_explorer.surveyors.database.quality import functional_infer as fi
from tests.test_value_profile_quality_checks import (  # noqa: F401 - fixtures and helpers reused
    SLUG,
    _by_key,
    _profile,
    _propose,
    _rows,
    entity,
    registry,
)

RUN = "2026-10-29T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _no_live_database_or_egeria(monkeypatch):
    """Nothing here reaches a database or Egeria: a real connection or a real gateway fails the test."""
    import psycopg2

    from resource_explorer.surveyors.database.quality import egeria_rules

    def refuse(*a, **k):
        raise AssertionError("a test reached a live service")

    monkeypatch.setattr(psycopg2, "connect", refuse)
    monkeypatch.setattr(egeria_rules, "egeria_gateway", refuse)


def _row(column, general_type, **kw):
    base = dict(schema_name="s", table_name="t", column_name=column, general_type=general_type, state="measured",
                record_ct=1000, value_ct=1000, distinct_value_ct=900, null_value_ct=0, zero_length_ct=None,
                filled_value_ct=None, surveyed_at=RUN, min_value=1.0, max_value=1000.0, avg_value=500.0,
                stdev_value=288.0, zero_value_ct=0, fractional_sum=None, min_length=None, max_length=None,
                avg_length=None, boolean_true_ct=None, date_days_present=None, before_5yr_date_ct=None,
                before_20yr_date_ct=None, future_date_ct=None, min_date=None, max_date=None, catalog_type="integer")
    base.update(kw)
    return base


def _infer(*rows):
    return fi.infer_batch(list(rows))


def _one(row):
    return _infer(row)[("s", "t", row["column_name"])]


# ── vendored files and pins ─────────────────────────────────────────────────────────────────────────────────────

def test_the_two_upstream_files_are_vendored_unchanged_and_pinned():
    for name, sha in fi.VENDORED_SQL_SHA256.items():
        assert cat.sha256_text((fi.PROFILING_DIR / name).read_text(encoding="utf-8")) == sha
    # 629 lines upstream, the first statement clears both labels, the last is the "END OF QUERY" marker
    text = fi.FUNCTIONAL_DATATYPE_SQL.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 629 and "--- END OF QUERY ---" in text


def test_the_nineteen_are_vendored_cat_types_and_their_selection_is_pinned():
    catalogue = cat.load_catalogue()
    assert len(cat.STEP_6_TEST_TYPES) == 19 and set(cat.STEP_6_TEST_TYPES) <= set(catalogue)
    for name in cat.STEP_6_TEST_TYPES:
        assert catalogue[name].run_type == "CAT" and not catalogue[name].is_table_comparison
        assert cat.PREDICATES[name].fragment_sha256 == cat.sha256_text(catalogue[name].selection_criteria), name


def test_the_default_ports_are_pinned_to_their_vendored_text():
    catalogue = cat.load_catalogue()
    for name in ("Dec_Trunc", "Incr_Avg_Shift", "Alpha_Trunc", "LOV_Match", "Pattern_Match", "Valid_Month"):
        assert cat.DEFAULT_PORTS[name][0] == cat.sha256_text(catalogue[name].default_parm_values), name


# ── the inference, statement by statement ───────────────────────────────────────────────────────────────────────

def test_a_sequential_integer_is_a_sequence_and_a_unique_id_by_mask_is_an_id_unique():
    assert _one(_row("order_no", "N", distinct_value_ct=1000)).label == "Sequence"
    assert _one(_row("customer_id", "N", distinct_value_ct=1000)).label == "ID-Unique"       # ID mask, then 538-565
    assert _one(_row("customer_sk", "N", distinct_value_ct=1000)).label == "ID-Unique-SK"
    assert _one(_row("region_id", "N", distinct_value_ct=40)).label == "ID-Group"            # ID mask, 4 % unique


def test_the_numeric_tail_is_discrete_for_an_integer_and_measurement_for_a_decimal():
    assert _one(_row("qty", "N", distinct_value_ct=40, min_value=-5.0, max_value=100.0)).label == "Measurement Discrete"
    amount = _row("amount", "N", catalog_type="numeric", numeric_scale=2, fractional_sum="12.50", distinct_value_ct=400)
    assert _one(amount).label == "Measurement"
    assert _one({**amount, "column_name": "pct_off", "min_value": 0.0, "max_value": 100.0}).label == "Measurement Pct"


def test_a_measurement_that_might_be_a_spike_is_not_called_a_measurement():
    # min_value 0 and most values zero could be a sparse column; the percentiles that would settle it are not taken
    sparse = _row("refunds", "N", catalog_type="double precision", min_value=0.0, max_value=900.0, zero_value_ct=800,
                  distinct_value_ct=120, avg_value=40.0, stdev_value=90.0)
    result = _one(sparse)
    assert result.label is None and "Measurement Spike" in result.possible
    assert "percentile_25/50/75" in result.needed and result.why.startswith("type not inferred · ")
    # all positive values cannot be 'mostly zero': the spike is ruled out without the percentiles
    positive = _row("price", "N", catalog_type="double precision", min_value=0.5, max_value=900.0, zero_value_ct=0,
                    distinct_value_ct=120)
    assert _one(positive).label == "Measurement"


def test_a_text_column_the_text_shape_measures_cannot_settle_says_why_and_gets_no_default_type():
    name = _one(_row("description", "A", catalog_type="text", min_length=3, max_length=200, avg_length=40.0,
                     distinct_value_ct=800))
    assert name.label is None and "ID-Secondary" in name.possible and "Description" in name.possible
    assert "UNKNOWN" not in name.possible or name.label is None
    assert "a text column" in name.why and "sampled text shape was not measured" in name.why


def test_names_decide_before_any_measure_is_read():
    assert _one(_row("last_updated_by", "A", catalog_type="text")).possible >= {"Process User"}
    assert _one(_row("created_user", "N", catalog_type="integer")).label == "System User"      # last statement wins
    assert _one(_row("flag", "N", catalog_type="smallint", distinct_value_ct=2, min_value=0.0, max_value=1.0)
                ).label == "Boolean"
    assert _one(_row("active", "B", catalog_type="boolean", distinct_value_ct=2)).label == "Boolean"


def test_too_few_values_is_tbd_and_a_constant_is_a_constant():
    assert _one(_row("x", "N", value_ct=3, distinct_value_ct=3)).label == fi.TBD
    assert _one(_row("k", "N", distinct_value_ct=1, min_value=7.0, max_value=7.0)).label == "Constant"
    assert _one(_row("e", "N", record_ct=0, value_ct=0, distinct_value_ct=0)).label == fi.TBD


def test_a_date_column_follows_the_q6_labels_and_the_transactional_gate():
    hist = _row("opened", "D", catalog_type="date", before_20yr_date_ct=900, future_date_ct=0, before_5yr_date_ct=900,
                min_date="1990-01-04", max_date="2026-10-02", date_days_present=500)
    assert _one(hist).label == "Historical Date"
    fut = _row("due", "D", catalog_type="date", before_20yr_date_ct=0, future_date_ct=990, before_5yr_date_ct=0,
               min_date="2026-11-01", max_date="2027-06-30", date_days_present=100)
    assert _one(fut).label == "Future Date"
    # within five years, a little in the future: the Transactional branches that need weeks/months present are unknown
    live = _row("created", "D", catalog_type="date", before_20yr_date_ct=0, future_date_ct=0, before_5yr_date_ct=100,
                min_date="2020-01-03", max_date="2026-10-02", date_days_present=900)
    result = _one(live)
    assert result.label is None and "Transactional Date" in result.possible
    assert {"date_weeks_present", "date_months_present"} <= set(result.needed)


def test_a_process_timestamp_name_prefixes_whatever_the_date_case_decides():
    row = _row("update_date", "D", catalog_type="date", before_20yr_date_ct=0, future_date_ct=990, before_5yr_date_ct=0,
               min_date="2026-11-01", max_date="2027-06-30", date_days_present=100)
    assert _one(row).label == "Process Future Date"


def test_id_fk_follows_a_unique_id_of_the_same_name_in_another_table():
    parent = _row("customer_no", "N", table_name="customer", distinct_value_ct=1000)           # Sequence -> not an ID
    unique = _row("order_id", "N", table_name="orders", distinct_value_ct=1000)                # ID-Unique
    child = _row("order_id", "N", table_name="lines", distinct_value_ct=300, record_ct=5000, value_ct=5000)
    out = _infer(parent, unique, child)
    assert out[("s", "orders", "order_id")].label == "ID-Unique"
    assert out[("s", "lines", "order_id")].label == "ID-FK"


def test_a_column_that_was_not_profiled_has_no_inference():
    assert _infer(_row("a", "N", state="not_permitted")) == {}


# ── what a selection rule may ask ───────────────────────────────────────────────────────────────────────────────

def test_label_in_is_true_false_or_unknown_never_a_silent_no():
    possible = frozenset({"Code", "Category", "Measurement Pct"})
    assert cat.label_in(possible, lambda v: v in ("Code", "Category", "Measurement Pct")) is True
    assert cat.label_in(possible, lambda v: v == "Zip") is False
    assert cat.label_in(possible, lambda v: v == "Code") is None
    assert cat.label_in(None, lambda v: True) is None
    assert cat.label_in(frozenset({None}), lambda v: True) is False        # NULL satisfies nothing


def test_effective_uses_the_stored_label_and_refuses_to_decide_for_an_old_row():
    inferred = _one(_row("qty", "N", distinct_value_ct=40, min_value=-5.0, max_value=100.0))
    assert fi.effective({"functional_data_type": "Measurement"}, inferred)[0] == frozenset({"Measurement"})
    # NULL stored but the measures now settle it: profiled before the inference existed
    possible, why = fi.effective({"functional_data_type": None}, inferred)
    assert possible is None and "profiled before type inference existed" in why
    text = _one(_row("note", "A", catalog_type="text", min_length=3, max_length=200, avg_length=40.0))
    possible, why = fi.effective({"functional_data_type": None}, text)
    assert possible == text.possible and why.startswith("type not inferred · ")
    assert fi.effective({}, None)[0] is None


# ── the table period and type ───────────────────────────────────────────────────────────────────────────────────

def test_a_table_with_one_measured_profile_is_cumulative_as_testgen_has_it():
    # Decision (project owner, 2026-10-29): "keep TestGen's cumulative". Pinned: a first profile is cumulative.
    assert fi.SINGLE_PROFILE_IS_CUMULATIVE is True
    assert fi.table_period([1000]) == ("cumulative", "")
    assert fi.table_period([None, 1000]) == ("cumulative", "")           # a run that did not measure it is skipped


def test_a_table_is_cumulative_or_window_by_whether_its_count_ever_fell():
    assert fi.table_period([])[0] is None and "0 measured profile runs" in fi.table_period([])[1]
    assert fi.table_period([None])[0] is None
    assert fi.table_period([1000, 1200, 1500])[0] == "cumulative"
    assert fi.table_period([1000, 800, 900])[0] == "window"
    assert fi.table_period([1000, None, 1100])[0] == "cumulative"           # an unmeasured run is skipped, not a drop to 0


def test_the_single_profile_rule_can_be_turned_off_by_its_one_constant(monkeypatch):
    monkeypatch.setattr(fi, "SINGLE_PROFILE_IS_CUMULATIVE", False)
    period, why = fi.table_period([1000])
    assert period is None and "at least 2" in why


def test_the_table_type_needs_every_column_label_and_the_period_alone_still_answers_a_like():
    labelled = [fi.Inference(frozenset({v})) for v in ("ID-Unique", "ID-Unique-SK", "Constant", "Attribute")]
    assert fi.table_kind(labelled) == ("bridge", "")
    unsettled = [*labelled, fi.Inference(frozenset({"Code", "Category"}))]
    kind, why = fi.table_kind(unsettled)
    assert kind is None and "1 of 5 columns" in why
    assert fi.functional_table_type("cumulative", None) == "cumulative"       # '%cumulative%' is still answerable
    assert fi.functional_table_type("window", "domain") == "window-domain"
    assert fi.functional_table_type(None, "domain") is None


# ── storing it ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_two_columns_exist_on_a_fresh_registry_and_are_added_to_an_old_one(tmp_path):
    from resource_explorer.registry import ProjectRegistry

    columns = {c for _t, c, _ty in ProjectRegistry.VALUE_PROFILE_COLUMNS}
    assert {"functional_data_type", "functional_table_type"} <= columns
    reg = ProjectRegistry(db_path=str(tmp_path / "fresh.db"))
    with reg._conn() as conn:
        assert {"functional_data_type", "functional_table_type"} <= set(reg._get_table_columns(conn, "database_value_profiles"))
    # an old table: drop the two columns' presence by building the table without them, then migrate
    old = tmp_path / "old.db"
    reg2 = ProjectRegistry(db_path=str(old))
    del reg2
    con = sqlite3.connect(old)
    con.execute("ALTER TABLE database_value_profiles DROP COLUMN functional_data_type")
    con.execute("ALTER TABLE database_value_profiles DROP COLUMN functional_table_type")
    con.commit()
    con.close()
    reg3 = ProjectRegistry(db_path=str(old))
    with reg3._conn() as conn:
        assert {"functional_data_type", "functional_table_type"} <= set(reg3._get_table_columns(conn, "database_value_profiles"))


def test_a_profile_stores_the_label_and_leaves_null_what_it_cannot_settle(registry, entity):
    _profile(registry, entity)
    rows = _rows(registry, "database_value_profiles")
    labels = {k: r["functional_data_type"] for k, r in rows.items()}
    assert labels["sales", "orders", "order_id"] is not None                      # an integer id with many values
    note = rows["sales", "orders", "note"]
    assert note["functional_data_type"] is None                                   # text: needs profile slice 2
    # one profile run is 'cumulative' (TestGen's); the type half waits for every column to have a label
    measured = {k: r for k, r in rows.items() if r["state"] == "measured"}
    assert all(r["functional_table_type"].startswith("cumulative") for r in measured.values())
    orders = {r["functional_table_type"] for k, r in measured.items() if k[:2] == ("sales", "orders")}
    assert orders == {"cumulative"}                      # a text column has no label, so only the period is known


def test_two_profiles_give_the_table_a_period(registry, entity):
    _profile(registry, entity, at="2026-10-20T10:00:00+00:00")
    _profile(registry, entity, at="2026-10-21T10:00:00+00:00")
    rows = _rows(registry, "database_value_profiles")
    assert {r["functional_table_type"] for k, r in rows.items() if k[:2] == ("sales", "orders")
            and r["state"] == "measured"} <= {"cumulative", "cumulative-domain", "cumulative-bridge", "cumulative-summary",
                                              "cumulative-transaction", "cumulative-entity"} | {None}
    assert any(r["functional_table_type"] for r in rows.values())


# ── the selection rules ─────────────────────────────────────────────────────────────────────────────────────────

def _sel(name, **kw):
    return cat.PREDICATES[name].evaluate(kw)


def test_a_numeric_measurement_is_selected_for_the_shift_checks_and_a_text_column_is_a_real_no():
    measure = dict(general_type="N", distinct_value_ct=400, column_name="amount", functional_data_type="Measurement")
    for name in ("Avg_Shift", "Incr_Avg_Shift"):
        assert _sel(name, **measure) is True
        assert _sel(name, **{**measure, "functional_data_type": "Measurement Spike"}) is False
        assert _sel(name, **{**measure, "column_name": "geo_latitude"}) is False
        assert _sel(name, **{**measure, "general_type": "A"}) is False
        # a label the measures could not settle keeps the answer unknown
        assert _sel(name, **{**measure, "functional_data_type": None, "fdt_possible": frozenset({"Measurement",
                                                                                                "Measurement Spike"})}) is None


def test_unique_pct_reads_not_established_for_a_text_column_that_could_be_a_measurement_pct():
    row = dict(distinct_value_ct=300, functional_data_type=None,
               fdt_possible=frozenset({"Code", "Measurement Pct", "Description"}))
    assert _sel("Unique_Pct", **row) is None
    assert _sel("Unique_Pct", **{**row, "fdt_possible": frozenset({"Code", "Description"})}) is True
    assert _sel("Unique_Pct", **{**row, "distinct_value_ct": 8}) is False
    assert _sel("Unique_Pct", distinct_value_ct=300, functional_data_type="Measurement") is False


def test_the_zip_and_month_rules_say_no_when_the_label_is_ruled_out_and_unknown_when_it_is_not():
    assert _sel("Valid_US_Zip", functional_data_type="Zip") is True
    assert _sel("Valid_US_Zip", functional_data_type="Code") is False
    assert _sel("Valid_US_Zip", functional_data_type=None, fdt_possible=frozenset({"Zip", "Code"})) is None
    assert _sel("Valid_Month", functional_data_type="Period Month") is True


def test_the_cumulative_checks_need_a_table_period_and_name_what_they_cannot_get():
    base = dict(functional_data_type="Transactional Date", date_days_present=500, future_date_ct=0, value_ct=1000)
    assert _sel("Distinct_Date_Ct", **base, functional_table_type="cumulative-transaction") is True
    assert _sel("Distinct_Date_Ct", **base, functional_table_type="window-transaction") is False
    assert _sel("Distinct_Date_Ct", **base, functional_table_type=None) is None
    # Weekly and Monthly also read date_weeks/months_present, which the profile does not take
    for name in ("Weekly_Rec_Ct", "Monthly_Rec_Ct"):
        assert _sel(name, **base, functional_table_type="cumulative-transaction") is None
        missing = cat.not_established_inputs(name, {**base, "functional_table_type": "cumulative-transaction"})
        assert "date_weeks_present" in missing or "date_months_present" in missing
    decimal = dict(general_type="N", min_value=1.0, value_ct=100, functional_table_type="cumulative")
    assert _sel("Dec_Trunc", fractional_sum="12.5", **decimal) is True
    assert _sel("Dec_Trunc", fractional_sum=None, **decimal) is False                       # not a decimal column
    assert _sel("Dec_Trunc", fractional_sum=None, general_type="N", min_value=None, value_ct=100,
                functional_table_type="cumulative") is None                                    # a row from before profile 1b


def test_the_text_types_wait_for_the_measures_only_profile_slice_2_takes():
    code = dict(distinct_value_ct=6, value_ct=300, functional_data_type="Code")
    for name in ("LOV_Match", "Distinct_Value_Ct"):
        assert _sel(name, **code) is None
        assert "frequent_values" in cat.not_established_inputs(name, code)
    assert _sel("Pattern_Match", functional_data_type="Attribute", distinct_value_ct=30) is None
    assert "frequent_patterns" in cat.not_established_inputs("Pattern_Match",
                                                             dict(functional_data_type="Attribute", distinct_value_ct=30))
    assert "numeric_ct" in cat.not_established_inputs("Alpha_Trunc", dict(
        general_type="A", max_length=10, min_length=1, avg_length=5.0, value_ct=10, distinct_value_ct=30,
        functional_data_type="Code", functional_table_type="cumulative"))


def test_min_val_and_the_variability_checks_read_the_profile_1b_measures():
    n = dict(general_type="N", functional_data_type="Measurement", distinct_value_ct=400, value_ct=1000, min_value=1.5,
             max_value=900.0, avg_value=300.0, stdev_value=50.0, zero_value_ct=0)
    assert _sel("Min_Val", **n) is True
    assert _sel("Variability_Increase", **n) is True and _sel("Variability_Decrease", **n) is True
    assert _sel("Variability_Increase", **{**n, "value_ct": 400}) is False          # value_ct = distinct_value_ct
    assert _sel("Variability_Increase", **{**n, "stdev_value": 0}) is False
    assert _sel("Variability_Increase", **{**n, "avg_value": None, "min_value": None}) is None   # a pre-1b row
    outlier = {**n, "stdev_value": 50.0}
    assert _sel("Outlier_Pct_Above", **outlier) is True and _sel("Outlier_Pct_Below", **outlier) is True
    assert _sel("Outlier_Pct_Above", **{**outlier, "avg_value": 0.0}) is None        # a zero mean is a division error


# ── defaults and the rendered measure ───────────────────────────────────────────────────────────────────────────

def test_default_parameters_come_from_the_profile_and_name_what_is_missing():
    catalogue = cat.load_catalogue()
    n = dict(value_ct=1000, distinct_value_ct=400, avg_value=300.0, stdev_value=50.0, min_value=1.5, fractional_sum="12.5")
    avg = cat.default_params(catalogue["Avg_Shift"], n, 10)
    assert (avg["baseline_value_ct"], avg["baseline_avg"], avg["baseline_sd"], avg["threshold_value"]) == (1000, 300.0,
                                                                                                         50.0, 0.5)
    incr = cat.default_params(catalogue["Incr_Avg_Shift"], n, 10)
    assert incr["baseline_sum"] == 300000.0 and incr["threshold_value"] == 2
    assert cat.default_params(catalogue["Dec_Trunc"], n, 10)["threshold_value"] == 13       # ROUND half away from zero
    assert cat.default_params(catalogue["Min_Val"], n, 10)["baseline_value"] == 1.5
    with pytest.raises(cat.ParameterError, match="frequent_values"):
        cat.default_params(catalogue["LOV_Match"], n, 10)
    with pytest.raises(cat.ParameterError, match="baseline_sd"):
        cat.default_params(catalogue["Avg_Shift"], {**n, "stdev_value": None}, 10)


def test_every_family_c_measure_renders_with_validated_values_and_no_unfilled_placeholder():
    catalogue = cat.load_catalogue()
    params = {"baseline_ct": 10, "baseline_value_ct": 900, "baseline_value": 1.5, "threshold_value": 2,
              "baseline_avg": 3.5, "baseline_sd": 1.25, "baseline_sum": 31.5, "baseline_unique_ct": 700}
    for name in cat.STEP_6_TEST_TYPES:
        if name in ("LOV_Match", "Pattern_Match", "Valid_Month"):
            continue
        sql = cat.render_measure(catalogue[name], "col", params, "2026-10-29 00:00:00")
        assert "{" not in sql and "<%" not in sql, name
    assert cat.render_measure(catalogue["Min_Val"], "col", params, "2026-10-29 00:00:00").count("1.5") == 1


def test_a_baseline_that_is_not_a_number_is_refused_for_min_val():
    with pytest.raises(cat.ParameterError, match="baseline_value must be a number"):
        cat.render_measure(cat.load_catalogue()["Min_Val"], "col", {"baseline_value": "1; DROP TABLE x"}, "2026-10-29 00:00:00")


def test_family_c_measures_parse_as_postgres():
    import sqlglot

    catalogue = cat.load_catalogue()
    params = {"baseline_ct": 10, "baseline_value_ct": 900, "baseline_value": 1.5, "threshold_value": 2,
              "baseline_avg": 3.5, "baseline_sd": 1.25, "baseline_sum": 31.5, "baseline_unique_ct": 700}
    for name in cat.STEP_6_TEST_TYPES:
        if name in ("LOV_Match", "Pattern_Match", "Valid_Month"):
            continue
        sqlglot.parse_one("SELECT " + cat.render_measure(catalogue[name], "col", params, "2026-10-29 00:00:00") + " FROM t",
                          read="postgres")


# ── end to end over the fixture profile ─────────────────────────────────────────────────────────────────────────

def test_propose_names_why_a_text_column_has_no_unique_pct_and_does_not_flood_a_number_with_text_rules(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    rows = _by_key(registry, "database_quality_proposals")
    # the integer id is judged by the rules; none of the text-only rules is "not established" for it
    ids = {k: r for k, r in rows.items() if k.startswith("sales.orders.order_id::")}
    for name in ("Alpha_Trunc", "LOV_Match", "Valid_US_Zip", "Valid_Month", "Distinct_Value_Ct"):
        assert f"sales.orders.order_id::{name}" not in ids
    note = rows.get("sales.orders.note::Unique_Pct")
    if note is not None:
        assert note["state"] in ("proposed", "not_established")
        if note["state"] == "not_established":
            assert "type not inferred" in note["reason"]
    # a stored label is what the rules read: set it NULL on the stored row and the rule says so, by name
    assert all(r["state"] != "proposed" or r["test_type"] != "Dec_Trunc" for r in rows.values())


def test_fractional_sum_text_compares_as_a_decimal_not_a_float():
    assert cat._fractional_positive({"fractional_sum": "0.000000001", "general_type": "N", "min_value": 1.0,
                                     "value_ct": 5}) is True
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")


# ── the exact percentiles and Measurement Spike (owner, 2026-10-29: "add the columns") ──────────────────────────

import re as _re  # noqa: E402

from resource_explorer.surveyors.database.quality import percentiles as pct  # noqa: E402
from resource_explorer.surveyors.database.quality.value_profile import ProfileBounds  # noqa: E402
from tests.test_value_profile_quality_checks import BOUNDS, FakeConn, PgError  # noqa: E402


class PctConn(FakeConn):
    """A FakeConn that also answers the percentile statement: `trios` are the (p25, p50, p75) of each number column, in
    the order the statement lists them; `fail` is raised instead."""

    def __init__(self, *a, trios=None, fail=None, **kw):
        super().__init__(*a, **kw)
        self.trios, self.fail = trios or [], fail

    def execute_query_isolated(self, sql, params=()):
        if "PERCENTILE_CONT" not in sql:
            return super().execute_query_isolated(sql, params)
        self.statements.append(sql)
        assert self.in_read_only, "the percentiles were read outside a read-only transaction"
        if self.fail:
            raise self.fail
        row = {}
        for alias, i, p in _re.findall(r"AS (c(\d+)_pct_(\d+))", sql):
            row[alias] = self.trios[int(i)][{"25": 0, "50": 1, "75": 2}[p]]
        return [row]


def test_the_percentile_statement_is_the_vendored_one_pinned_and_quotes_its_names():
    assert pct.pin_matches() and len(pct.vendored_lines()) == 3
    sql, aliases = pct.build_statement("s", "t", ["a", 'we"ird'])
    assert sql.count("PERCENTILE_CONT") == 6 and 'ORDER BY "we""ird"::NUMERIC' in sql and sql.endswith('FROM "s"."t"')
    assert aliases["c1_pct_75"] == ('we"ird', 2)
    import sqlglot

    sqlglot.parse_one(sql, read="postgres")


def test_percentiles_are_measured_per_table_and_stored(registry, entity):
    conn = PctConn(profile=__import__("tests.test_value_profile_quality_checks", fromlist=["PROFILE"]).PROFILE,
                   trios=[(10.0, 20.0, 35.5)])
    out, _ = _profile(registry, entity, conn=conn)
    rows = _rows(registry, "database_value_profiles")
    order_id = rows["sales", "orders", "order_id"]
    assert (order_id["percentile_25"], order_id["percentile_50"], order_id["percentile_75"]) == (10.0, 20.0, 35.5)
    # only number columns are asked, and only once per table
    assert sum("PERCENTILE_CONT" in s for s in conn.statements) == 1
    assert rows["sales", "orders", "note"]["percentile_50"] is None and out["value_profile"]["percentiles_not_measured"] == []


def test_a_table_that_cannot_give_percentiles_keeps_every_other_measure_and_says_why(registry, entity):
    timeout = PgError("canceling statement due to statement timeout")
    conn = PctConn(profile=__import__("tests.test_value_profile_quality_checks", fromlist=["PROFILE"]).PROFILE,
                   fail=timeout)
    out, _ = _profile(registry, entity, conn=conn)
    order_id = _rows(registry, "database_value_profiles")["sales", "orders", "order_id"]
    assert order_id["state"] == "measured" and order_id["value_ct"] == 1000          # nothing else was lost
    assert order_id["percentile_50"] is None                                         # not measured, never 0
    (gap,) = out["value_profile"]["percentiles_not_measured"]
    assert gap["table_name"] == "orders" and gap["columns"] == ["order_id"] and "percentiles not measured" in gap["reason"] \
        or "exceeded" in gap["reason"]


def test_the_percentile_budget_is_its_own_and_a_spent_one_reads_nothing():
    conn = PctConn(trios=[(1.0, 2.0, 3.0)])
    found, reason = pct.measure_table(conn, "s", "t", ["a"], ProfileBounds(1, percentile_budget_seconds=0))
    assert found == {} and "exceeded" in reason and not conn.statements
    assert ProfileBounds(1).percentile_budget_seconds == 300.0 and ProfileBounds(1).table_budget_seconds == 300.0


def _num(name="refunds", **kw):
    return _row(name, "N", catalog_type="double precision", distinct_value_ct=120, **kw)


def test_with_the_percentiles_a_spike_is_settled_exactly():
    sparse_extreme = _num(min_value=0.0, max_value=900.0, zero_value_ct=800, avg_value=40.0, stdev_value=90.0,
                          percentile_25=0.0, percentile_50=0.0, percentile_75=0.0)
    assert _one(sparse_extreme).label == "Measurement Spike"
    sparse_calm = _num(min_value=0.0, max_value=900.0, zero_value_ct=800, avg_value=0.0, stdev_value=0.0,
                       percentile_25=0.0, percentile_50=0.0, percentile_75=0.0)
    assert _one(sparse_calm).label != "Measurement Spike" and _one(sparse_calm).label is not None
    not_sparse = _num(min_value=-5.0, max_value=900.0, zero_value_ct=10, avg_value=40.0, stdev_value=90.0,
                      percentile_25=2.0, percentile_50=30.0, percentile_75=60.0)
    assert _one(not_sparse).label == "Measurement"                       # a zero count could not decide this: exact now
    mostly_null = _num(min_value=-5.0, max_value=900.0, record_ct=10000, value_ct=300, zero_value_ct=0, avg_value=500.0,
                       stdev_value=90.0, percentile_25=1.0, percentile_50=3.0, percentile_75=700.0)
    assert _one(mostly_null).label == "Measurement Spike"                # < 5 % filled, and the mean is far from the median


def test_without_the_percentiles_the_old_bound_still_applies_and_otherwise_it_says_so():
    undecided = _num(min_value=-5.0, max_value=900.0, zero_value_ct=800, avg_value=40.0, stdev_value=90.0)
    result = _one(undecided)
    assert result.label is None and "Measurement Spike" in result.possible and "percentile_25/50/75" in result.needed


def test_the_spike_evidence_is_a_declared_choice_with_room_for_more(monkeypatch):
    assert set(fi.SPIKE_KINDS) >= {"upstream_ratio", "absolute", "percent_change"}
    assert fi.SPIKE_EVIDENCE == {"kind": "upstream_ratio", "limit": None}               # TestGen's own, by default
    row = _num(min_value=0.0, max_value=900.0, zero_value_ct=800, avg_value=10.0, stdev_value=30.0,
               percentile_25=0.0, percentile_50=0.0, percentile_75=0.0)
    assert _one(row).label == "Measurement Spike"                                       # (75-25)=0 > 20 is false; 10 > 0 is true
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "absolute", "limit": 50})
    assert _one(row).label != "Measurement Spike"                                       # a 10-unit mean is not a spike in absolute terms
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "absolute", "limit": 5})
    assert _one(row).label == "Measurement Spike"
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "percent_change", "limit": 100})
    assert _one(row).label == "Measurement Spike"                                       # a zero median with a non-zero mean
    steady = _num(min_value=1.0, max_value=900.0, record_ct=10000, value_ct=300, avg_value=105.0, stdev_value=9.0,
                  percentile_25=95.0, percentile_50=100.0, percentile_75=110.0)
    assert _one(steady).label != "Measurement Spike"                                    # 5 % from the median, limit 100 %
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "percent_change", "limit": 2})
    assert _one(steady).label == "Measurement Spike"                                    # the same column, a tighter percent


def test_a_new_kind_is_one_registered_function_and_an_unknown_kind_is_refused(monkeypatch):
    monkeypatch.setitem(fi.SPIKE_KINDS, "always", lambda p25, p50, p75, avg, limit: True)
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "always", "limit": None})
    row = _num(min_value=0.0, max_value=900.0, record_ct=10000, value_ct=300, avg_value=5.0, stdev_value=9.0,
               percentile_25=1.0, percentile_50=3.0, percentile_75=4.0)
    assert _one(row).label == "Measurement Spike"
    monkeypatch.setattr(fi, "SPIKE_EVIDENCE", {"kind": "no_such_kind", "limit": None})
    with pytest.raises(KeyError):
        _one(row)


# ── publishing the nineteen as Egeria DataQualityRules (owner, 2026-10-29) ─────────────────────────────────────────

import dataclasses  # noqa: E402

from resource_explorer.surveyors.database.quality import egeria_rules as er  # noqa: E402
from tests.test_quality_q5_data_quality_rule import (  # noqa: E402,F401
    ASSET_GUID,
    COLUMN_GUID,
    FakeGateway,
    _element_qn,
    _gateway,
    _published,
)
from tests.test_value_profile_quality_checks import _accept  # noqa: E402

_PARAMS = {"baseline_ct": 10, "baseline_value_ct": 900, "baseline_value": 1.5, "threshold_value": 2,
           "baseline_avg": 3.5, "baseline_sd": 1.25, "baseline_sum": 31.5, "baseline_unique_ct": 700, "severity": "warning"}


@pytest.mark.parametrize("name", cat.STEP_6_TEST_TYPES)
def test_each_of_the_nineteen_maps_to_a_data_quality_rule(name):
    test_type = cat.load_catalogue()[name]
    props = er.rule_properties(f"sales.orders.amount::{name}", test_type, _PARAMS)
    assert props["class"] == "DataQualityRuleProperties" and props["method"] == name
    assert props["comparisonOperator"] == cat.MUST_BE[test_type.test_operator]
    assert props["thresholdValues"] == ["2"] and props["severity"] == "warning" and props["qualityEngine"] == "resource-explorer"
    assert "expression" not in props                                          # RE never stores runnable SQL
    if "baseline_avg" in test_type.default_parm_columns or name in ("Avg_Shift", "Incr_Avg_Shift", "Outlier_Pct_Above",
                                                                    "Outlier_Pct_Below"):
        assert props["additionalProperties"]["baselineAvg"] == "3.5"


def test_an_accepted_step_6_check_is_published_read_back_and_linked_with_no_live_egeria(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    key = "sales.orders.order_id::Unique_Pct"
    proposal = _by_key(registry, "database_quality_proposals")[key]
    assert proposal["state"] == "proposed"
    _accept(registry, key, "Unique_Pct", **{k: v for k, v in proposal["params_json"].items() if k != "severity"},
            severity="warning")
    gateway = _gateway(entity)
    out = er.publish_database_rules(registry, _published(entity), gateway, recorded_by="steward")
    assert out["counts"] == {"created": 1}
    (create,) = gateway.made("create")
    sent = create[1]
    assert sent["method"] == "Unique_Pct" and sent["comparisonOperator"] == "mustBeLessThan"       # '>=' fails the check
    assert sent["thresholdValues"] == ["0.5"] and sent["qualityDimension"] == "uniqueness"
    assert gateway.links == {(COLUMN_GUID, "rule-1"), (ASSET_GUID, "rule-1")}
    assert registry.latest_quality_rule_proofs(SLUG)[key]["outcome"] == "created"
    # nothing about this reached a real Egeria: the gateway is the in-memory one
    assert isinstance(gateway, FakeGateway)


def test_a_published_step_6_rule_edited_in_egeria_is_the_one_the_run_uses(registry, entity):
    _profile(registry, entity)
    _propose(registry)
    key = "sales.orders.order_id::Unique_Pct"
    proposal = _by_key(registry, "database_quality_proposals")[key]
    _accept(registry, key, "Unique_Pct", **{k: v for k, v in proposal["params_json"].items() if k != "severity"},
            severity="warning")
    gateway = _gateway(entity)
    er.publish_database_rules(registry, _published(entity), gateway, recorded_by="steward")
    gateway.rules["rule-1"].properties["thresholdValues"] = ["0.2"]
    again = er.publish_database_rules(registry, _published(entity), gateway, recorded_by="steward")
    assert again["counts"] == {"edited_in_egeria": 1}
    assert not gateway.made("update")                                         # Egeria wins: RE does not overwrite it
