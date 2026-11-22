"""TR-128: the Data Class Match card counted `established` (matched + no_match + unmatched_patterned) as "columns
matching a data class"; _rank could cut a real match in favour of no_match rows (6 of 12 cut on egeria_v6); and a Send to
a file system not yet published to Egeria answered 502 (a server fault) when it is a conflict the person can resolve.
No Egeria: stored run rows and the fakes the neighbouring file-system tests use.
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.filesystem import file_class_match as fcm
from tests.fs_egeria_fake import FakeEgeria, install  # noqa: F401
from tests.library_step_install import install_steps  # noqa: F401
from tests.test_c3_fs_publish_and_curate import _class_run, _confirmed_setup  # noqa: F401
from tests.test_files_walk_extensions import _probe_row, _register, _tree, api, registry  # noqa: F401  (fixtures reused)


# ── (a) the card counts matches, not established verdicts ───────────────────────────────────────────────────────

def _run(verdicts: dict, **extra) -> dict:
    return {"columns": sum(verdicts.values()), "files": 3, "verdicts": verdicts,
            "established": sum(verdicts.get(v, 0) for v in ("matched", "partial_match", "no_match", "unmatched_patterned")),
            "privacy_relevant": 0, "catalog": {"available": True}, **extra}


def test_the_card_counts_only_matched_and_partial_match_as_matching_a_data_class():
    words = fcm.run_words(_run({"matched": 3, "partial_match": 1, "no_match": 5, "unmatched_patterned": 2, "not_sampled": 4}))
    assert "4 columns match a data class" in words                 # not the 11 'established' ones


def test_a_run_with_only_no_match_and_proposals_says_no_column_matches_not_that_they_do():
    words = fcm.run_words(_run({"no_match": 7, "unmatched_patterned": 2}))
    assert "0 columns match a data class" in words and "9 columns match" not in words


def test_the_count_is_read_from_the_verdicts_so_runs_stored_before_this_change_read_right():
    old = _run({"matched": 1, "no_match": 9})
    old.pop("matching", None)                                       # a stored run has no `matching` key
    assert "1 column match a data class" in fcm.run_words(old)


def test_a_real_run_counts_what_matched(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    registry.add_filesystem_file_columns(fs.slug, "2026-11-22T09:00:00", [
        {"file_path": "hr/payroll.csv", "column_name": "email", "ordinal": 0, "declared_type": "", "inferred_general_type": "object",
         "record_ct": 24, "value_ct": 24, "null_ct": 0, "distinct_ct": 24, "sample_ratio": 1.0,
         "stats_source": "resource-explorer", "state": "measured"}])
    from tests.test_files_class_match import catalog
    out = fcm.run_file_class_match(registry.get_filesystem(fs.slug), registry, catalog_loader=catalog())
    matched = sum(1 for m in out["matches"] if m["verdict"] in ("matched", "partial_match"))
    assert f"{matched} column" in fcm.run_words(out) and out["matching"] == matched


# ── (b) _rank never cuts a real match for a no_match ────────────────────────────────────────────────────────────

def _d(verdict, confidence, column, *, established=True, privacy=False):
    return {"verdict": verdict, "confidence": confidence, "column": column, "file_path": "f.csv",
            "established": established, "privacy_relevant": privacy}


def test_matched_and_partial_match_rank_before_no_match_whatever_the_confidence():
    rows = [_d("no_match", 100, "n1"), _d("no_match", 100, "n2"), _d("partial_match", 80, "p"),
            _d("matched", 96, "m"), _d("unmatched_patterned", 70, "u")]
    order = [d["column"] for d in sorted(rows, key=fcm._rank)]
    assert order.index("m") < order.index("n1") and order.index("p") < order.index("n1")
    assert order.index("m") < order.index("n2") and order.index("p") < order.index("n2")


def test_a_cut_at_the_cap_keeps_every_real_match(monkeypatch):
    monkeypatch.setattr(fcm, "MAX_KEPT", 3)
    rows = ([_d("no_match", 100, f"n{i}") for i in range(10)]
            + [_d("matched", 96, "m1"), _d("matched", 97, "m2"), _d("partial_match", 80, "p1")])
    kept = [d["column"] for d in sorted(rows, key=fcm._rank)[:fcm.MAX_KEPT]]
    assert set(kept) == {"m1", "m2", "p1"}


def test_privacy_relevant_rows_still_come_first():
    rows = [_d("matched", 96, "m"), _d("unmatched_patterned", 70, "u", privacy=True)]
    assert [d["column"] for d in sorted(rows, key=fcm._rank)][0] == "u"


# ── (c) a Send to a file system that is not published answers 409, not 502 ──────────────────────────────────────

@pytest.mark.usefixtures("signed_in_caller")
def test_sending_data_classes_for_an_unpublished_file_system_is_a_409_the_person_can_act_on(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _confirmed_setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    registry.set_filesystem_egeria_guid("walkme", "")
    assert not registry.get_filesystem("walkme").egeria_asset_guid
    r = client.post("/api/filesystems/walkme/review/publish-data-classes", headers=headers)
    assert r.status_code == 409, r.text
    assert "publish the file system to Egeria first" in r.json()["detail"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_published_file_system_still_sends(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _confirmed_setup(registry, tmp_path)
    install(monkeypatch, FakeEgeria())
    assert client.post("/api/filesystems/walkme/review/publish-data-classes", headers=headers).status_code == 200
