"""The words on a proposed quality check: a test type's name, its parameters, and why it was not established."""
from __future__ import annotations

from resource_explorer.quality_words import parameter_items, reason_in_words, name_of_test_type
from resource_explorer.surveyors.database.quality.catalogue import load_catalogue

CATALOGUE = load_catalogue()


def test_a_test_type_is_named_by_testgens_own_short_name():
    assert name_of_test_type(CATALOGUE["Alpha_Trunc"]) == CATALOGUE["Alpha_Trunc"].name_short != "Alpha_Trunc"
    assert name_of_test_type(None) == "", "a type the catalogue does not know has no name; the screen shows its identifier"


def test_a_parameter_without_a_value_is_not_listed():
    entry = CATALOGUE["Row_Ct_Pct"]
    items = parameter_items(entry, {"baseline_ct": None, "baseline_value": None, "threshold_value": 0, "severity": "error",
                                    "profile_surveyed_at": "2026-10-30"})
    assert [i["key"] for i in items] == ["threshold_value", "severity"]
    assert all(i["text"] in ("0", "error") for i in items), "zero is a value, not an absence"


def test_the_default_placeholder_list_is_summarised_as_standard_with_its_count():
    entry = CATALOGUE["Required_No_Placeholder"]
    standard = entry.default_parm_values.split("',")[0].lstrip("'")
    [listed] = [i for i in parameter_items(entry, {"baseline_value": standard}) if i["key"] == "baseline_value"]
    assert listed["summary"].startswith("standard ") and f"· {len(listed['list'])} values" in listed["summary"]
    [custom] = parameter_items(entry, {"baseline_value": ",".join(f"v{i}" for i in range(12))})
    assert not custom["summary"].startswith("standard "), "a list that is not the catalogue's is not called standard"


def test_a_reason_names_the_missing_input_in_a_persons_words():
    assert reason_in_words("its selection rule reads frequent_patterns, which the profile did not measure") == (
        "needs the common text patterns, which this profile did not collect")
    said = reason_in_words("type not inferred · needs date_months_present, date_weeks_present, which the profile does not take")
    assert "date_months_present" not in said and "months that hold dates" in said and "selection rule" not in said


def test_an_unnamed_input_says_the_rule_did_not_name_it():
    said = reason_in_words("its selection rule reads an input, which the profile did not measure")
    assert "does not say which" in said and "selection rule" not in said


def test_a_reason_it_does_not_know_comes_back_unchanged():
    assert reason_in_words("something new") == "something new"
    assert reason_in_words("") == ""


def test_duplicate_rows_columns_are_a_count_and_the_left_out_ones_are_named_not_json():
    """QS-1/QS-2 (read §C, 2026-10-30): "columns to compare [...24 names...]" and `excluded columns [{...}]` were raw."""
    from resource_explorer.surveyors.database.quality.catalogue import load_catalogue

    names = [f"c{i}" for i in range(24)]
    items = parameter_items(load_catalogue()["Dupe_Rows"], {
        "groupby_names": names, "skip_errors": 0, "excluded_columns": [{"column": "rowguid", "general_type": "X"}],
        "severity": "error"})
    [compared] = [i for i in items if i["key"] == "groupby_names"]
    assert compared["summary"] == "24 columns compared (all but rowguid)" and compared["list"] == names
    assert "rowguid is left out" in compared["title"]
    assert [i["key"] for i in items] == ["groupby_names", "skip_errors", "severity"]   # the left-out ones said once
    assert not any("{" in str(i.get("text", "")) or "[" in str(i.get("text", "")) for i in items)
    one = parameter_items(None, {"groupby_names": ["only"]})
    assert one[0]["summary"] == "1 column compared"


def test_type_inference_tails_are_dropped_from_the_person_words():
    """QS-3: "undetermined" and "statements unresolved: Constant" say nothing more than "could not be worked out"."""
    assert reason_in_words("type not inferred · undetermined") == "the column's type could not be worked out"
    assert reason_in_words("type not inferred · statements unresolved: Constant") == "the column's type could not be worked out"
    said = reason_in_words("type not inferred · statements unresolved: Constant · needs date_months_present, which the "
                           "profile does not take")
    assert said == "the column's type could not be worked out · needs the months that hold dates, which this profile does not collect"
