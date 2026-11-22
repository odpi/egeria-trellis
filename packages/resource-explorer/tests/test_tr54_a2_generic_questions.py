"""TR-54 A2, ruling (b): open_data ANSWERS the generic (`*`) questions on its dataset page and stays out of every survey,
coverage, export and import loop. Full promotion to a surveyed kind is TR-119.

Three things are pinned here:
  1. the capability is a named constant, and each of its users is named, so a call site stays excluded on purpose;
  2. the dataset page asks the generic questions that apply to a dataset, says which do not (and why) and leaves those
     out of "N of M answered" -- never as an unanswered row;
  3. with an open_data row in the registry, `*`, coverage_sweep, export_inventory and the batch import give what they
     gave without one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _chosen, _register_demo, reg, store  # noqa: E402,F401  (fixtures are shared)

from resource_explorer import resource_types as rt  # noqa: E402
from resource_explorer.open_data import dataset_questions as dq, service  # noqa: E402
from resource_explorer.surveyors.question_catalog_reader import get_questions  # noqa: E402

PKG = Path(__file__).resolve().parents[1] / "resource_explorer"
NEXT = PKG / "web" / "static" / "next"


def _imported(store, **record):
    portal = _register_demo(store)
    t = FakeTransport()
    res = service.run_search(store, portal["slug"], terms="dataset", pages=1, transport=t)
    name = res["rows"][0]["dataset_id"]
    service.import_datasets(store, _chosen(portal["slug"], name), work_list_name="w", transport=t,
                            egeria_lookup=lambda _i: None, imported_by="tester")
    return store.list_datasets()[0]


# ── 1. the named capability ─────────────────────────────────────────────────

def test_the_capability_is_a_named_constant_and_is_not_a_surveyed_kind():
    assert rt.GENERIC_QUESTION_KINDS == ("open_data",)
    assert not set(rt.GENERIC_QUESTION_KINDS) & set(rt.SURVEYED_RESOURCE_TYPES)
    assert "open_data" not in rt.parse_resource_types("*")
    assert rt.parse_resource_types("*") == ["repo", "database", "filesystem"]       # `*` is unchanged


def test_the_capability_points_at_the_promotion_it_defers():
    src = (PKG / "resource_types.py").read_text(encoding="utf-8")
    at = src[src.index("GENERIC_QUESTION_KINDS"):]
    assert "TR-119" in src[max(0, src.index("GENERIC_QUESTION_KINDS") - 900):src.index("GENERIC_QUESTION_KINDS") + 200]
    assert at.startswith("GENERIC_QUESTION_KINDS")


def test_only_the_named_modules_use_the_capability():
    users = {str(p.relative_to(PKG)) for p in PKG.rglob("*.py") if "GENERIC_QUESTION_KINDS" in p.read_text(encoding="utf-8")}
    assert users == {"resource_types.py", "open_data/dataset_questions.py"}, users


# ── 2. the dataset page ─────────────────────────────────────────────────────

def _generic_ids() -> set[str]:
    return set.intersection(*({e["id"] for e in get_questions(t)} for t in rt.SURVEYED_RESOURCE_TYPES))


def test_every_generic_question_has_a_decision_so_a_new_one_cannot_slip_in_unasked():
    decided = set(dq.GENERIC_APPLIES) | set(dq.GENERIC_DOES_NOT_APPLY)
    assert _generic_ids() == decided, (sorted(_generic_ids() - decided), sorted(decided - _generic_ids()))
    assert not set(dq.GENERIC_APPLIES) & set(dq.GENERIC_DOES_NOT_APPLY)
    assert all(reason.strip() for reason in dq.GENERIC_DOES_NOT_APPLY.values())       # an exclusion says why


def test_the_page_asks_the_applicable_generic_questions_plus_the_three_open_data_ones(store):
    out = dq.build(_imported(store))
    ids = [r["id"] for r in out["questions"]]
    assert set(ids) == set(dq.GENERIC_APPLIES) | {e["id"] for e in get_questions("open_data")}
    assert out["total"] == len(ids) == len(dq.GENERIC_APPLIES) + 3
    assert len(ids) == len(set(ids))
    assert {r["kind"] for r in out["questions"]} == {"generic", "open_data"}
    assert sum(1 for r in out["questions"] if r["kind"] == "open_data") == 3


def test_a_question_that_cannot_apply_is_left_out_of_M_and_is_never_an_unanswered_row(store):
    out = dq.build(_imported(store))
    shown = {r["id"] for r in out["questions"]}
    assert not shown & set(dq.GENERIC_DOES_NOT_APPLY)
    assert {n["id"] for n in out["not_applicable"]} == set(dq.GENERIC_DOES_NOT_APPLY)
    assert all(n["reason"] for n in out["not_applicable"])
    assert out["answered"] == 0 and out["total"] == len(shown)                        # M does not count them


def test_what_the_publishers_record_states_is_partial_and_the_rest_says_no_reader(store):
    d = _imported(store)
    rows = {r["id"]: r for r in dq.build(d)["questions"]}
    for r in rows.values():
        assert r["state"] in ("partial", "no_reader") and r["words"]
        assert (r["state"] == "partial") == bool(r["answer"])                         # an answer is only ever from the record
    blank = dict(d, title="not_reported", publisher="not_reported", licence_id="not_reported", licence={}, record={},
                 modified="not_reported")
    for r in dq.build(blank)["questions"]:
        assert r["state"] == "no_reader" and r["answer"] == ""                        # unreported is never "none" or 0


def test_the_strip_names_every_stage_the_questions_sit_in_in_funnel_order(store):
    out = dq.build(_imported(store))
    stages = [s["stage"] for s in out["stages"]]
    order = ["Scouting", "Discovery", "Assessment", "Analysis", "Enrichment"]
    assert stages == [s for s in order if s in stages] and len(stages) >= 4
    assert sum(s["total"] for s in out["stages"]) == out["total"]                     # a slash stage counts once, under its first


def test_the_page_script_shows_the_kind_and_the_not_applicable_line():
    js = (NEXT / "open-data-questions.js").read_text(encoding="utf-8")
    assert "res.not_applicable" in js and "data-od-not-applicable" in js
    assert "q.kind" in js


# ── 3. the loops still skip an open_data row ────────────────────────────────

def test_the_catalog_sections_are_unchanged_by_an_open_data_row(store, reg):
    before = {t: [e["id"] for e in get_questions(t)] for t in (*rt.SURVEYED_RESOURCE_TYPES, "open_data")}
    _imported(store)
    after = {t: [e["id"] for e in get_questions(t)] for t in (*rt.SURVEYED_RESOURCE_TYPES, "open_data")}
    assert before == after
    assert len(after["open_data"]) == 3                                              # the generic ones are not merged in


def test_coverage_sweep_and_its_known_analyses_still_skip_the_row(store, reg):
    from resource_explorer import coverage_sweep as cs

    before = (cs.known_analyses(reg), cs._resources(reg), cs.coverage_sweep(reg, "license_classification"))
    _imported(store)
    after = (cs.known_analyses(reg), cs._resources(reg), cs.coverage_sweep(reg, "license_classification"))
    assert before == after
    assert "open_data" not in after[0] and all(t != "open_data" for _s, t in after[1])


def test_export_inventory_still_skips_the_row(store, reg):
    from resource_explorer import inventory_export as ie

    before = ie.export_inventory(reg, generated_at="t")
    _imported(store)
    after = ie.export_inventory(reg, generated_at="t")
    assert before == after
    assert set(after["resources"]) == {"repo", "database", "filesystem"}


def test_batch_import_still_refuses_an_open_data_row(store, reg):
    from resource_explorer import batch_io

    _imported(store)
    assert "open_data" not in batch_io.RESOURCE_TYPES
    rows = batch_io.parse_csv_text("resource_type,address\nopen_data,doi:10.5281/zenodo.1\n")
    assert rows and "unknown resource_type 'open_data'" in " ".join(rows[0].errors)
