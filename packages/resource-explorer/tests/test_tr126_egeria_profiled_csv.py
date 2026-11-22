"""TR-126 (decided by Dan: "TR-126 - ok"): a file Egeria's CSV Survey Service profiled was never opened by Resource
Explorer (EGERIA_NOT_OPENED), so its columns stayed not_sampled and could never be compared with a data class. Its head is
now read FOR MATCHING ONLY: Egeria's profile stays the profile (RE writes nothing to it), and the match says so in words:
"values read by Resource Explorer for matching only; Egeria profiled this file". No Egeria.
"""
from __future__ import annotations

from resource_explorer.surveyors.filesystem import file_class_match as fcm
from resource_explorer.surveyors.filesystem import file_profiles as fp
from tests.test_files_class_match import _col, _profiles, _register, _tree, catalog, registry  # noqa: F401  (fixtures reused)

WORDS = "values read by Resource Explorer for matching only; Egeria profiled this file"


def _lake(tmp_path, *, name="people.csv", rows=24):
    root = _tree(tmp_path)
    (root / "lake").mkdir()
    (root / "lake" / name).write_text("email\n" + "\n".join(f"person{i}@example.com" for i in range(rows)) + "\n")
    return root


def _egeria_profiles(registry, name="people.csv"):
    _profiles(registry, extra=[_col(f"lake/{name}", "email", 0, source="egeria:abcd1234")])


def test_an_egeria_profiled_csv_is_head_sampled_and_matched_on_its_values(registry, tmp_path):
    fs = _register(registry, _lake(tmp_path))
    _egeria_profiles(registry)
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    m = next(x for x in out["matches"] if x["file_path"] == "lake/people.csv")
    assert m["verdict"] == "matched" and m["matched_display_name"] == "Email Address"      # was not_sampled
    assert "head sample of 24 of 24 rows" in m["statement"]
    assert out["file_outcomes"]["egeria_profiled_sampled"] == 1
    assert out["file_outcomes"].get("egeria_profiled", 0) == 0


def test_the_match_and_the_run_say_the_values_were_read_for_matching_only(registry, tmp_path):
    fs = _register(registry, _lake(tmp_path))
    _egeria_profiles(registry)
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    m = next(x for x in out["matches"] if x["file_path"] == "lake/people.csv")
    assert WORDS in m["statement"] and m["read_note"] == WORDS
    assert next(f for f in out["files_detail"] if f["file_path"] == "lake/people.csv")["why"] == WORDS
    plain = next(x for x in out["matches"] if x["file_path"] == "hr/payroll.csv" and x["column"] == "email")
    assert WORDS not in plain["statement"] and "read_note" not in plain         # RE's own files carry no such note
    assert fcm.EGERIA_READ_FOR_MATCHING_WORDS == WORDS


def test_egerias_profile_stays_the_profile_nothing_is_written_to_it(registry, tmp_path):
    fs = _register(registry, _lake(tmp_path))
    _egeria_profiles(registry)
    runs = registry.list_filesystem_file_column_runs("tree")
    before = registry.list_filesystem_file_columns("tree", runs[0])
    fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    after = registry.list_filesystem_file_columns("tree", runs[0])
    assert before == after and registry.list_filesystem_file_column_runs("tree") == runs
    assert all((r.get("stats_source") or "").startswith("egeria:") for r in after if r["file_path"] == "lake/people.csv")


def test_a_secret_looking_name_is_still_never_opened_even_when_egeria_profiled_it(registry, tmp_path, monkeypatch):
    fs = _register(registry, _lake(tmp_path, name="passwords.csv"))
    _egeria_profiles(registry, "passwords.csv")
    opened = []
    real = fp._open_plain
    monkeypatch.setattr(fp, "_open_plain", lambda p: opened.append(str(p)) or real(p))
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    assert not any("passwords.csv" in p for p in opened)
    assert next(x for x in out["matches"] if x["file_path"] == "lake/passwords.csv")["verdict"] == "not_sampled"
    assert out["file_outcomes"]["secret_name"] == 1


def test_an_egeria_profiled_file_that_cannot_be_read_stays_not_sampled_and_says_why(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))                  # lake/people.csv does not exist on disk
    _egeria_profiles(registry)
    out = fcm.run_file_class_match(fs, registry, catalog_loader=catalog())
    m = next(x for x in out["matches"] if x["file_path"] == "lake/people.csv")
    assert m["verdict"] == "not_sampled" and "read_note" not in m
    assert out["file_outcomes"]["egeria_profiled"] == 1 and out["file_outcomes"].get("egeria_profiled_sampled", 0) == 0


def test_the_price_no_longer_says_files_egeria_profiled_are_not_opened(registry, tmp_path):
    _register(registry, _lake(tmp_path))
    _egeria_profiles(registry)
    words = fcm.price(registry, "tree")["words"]
    assert "files Egeria profiled are not opened" not in words
    assert "for matching only" in words


def test_the_not_opened_sentence_is_gone_from_the_module():
    assert not hasattr(fcm, "EGERIA_NOT_OPENED")
