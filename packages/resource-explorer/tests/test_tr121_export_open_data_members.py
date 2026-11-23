"""TR-121 (Architect's ruling; restore is out of scope): export_inventory exports the open_data rows its investigation
members point at, and refuses, in words, a member whose dataset cannot be exported, never writing it dangling.

Since TR-54 A1 an import adds an `open_data` member to the investigation's Folio. The export wrote those members but no
open_data rows, so every such export failed verify ("references unknown open_data ...") and a restore would have
recreated members pointing at nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_open_data import FakeTransport, _chosen, _register_demo, reg, store  # noqa: E402,F401  (fixtures are shared)

from resource_explorer import inventory_export as ie  # noqa: E402
from resource_explorer.open_data import service  # noqa: E402


def _with_repo(reg_):
    from resource_explorer.registry import Project
    reg_.add(Project(slug="r1", display_name="R1", github_url="https://github.com/o/r1"))


def _import_into(store_, reg_, inv: str, n: int = 2) -> list[str]:
    portal = _register_demo(store_)
    t = FakeTransport()
    res = service.run_search(store_, portal["slug"], terms="dataset", pages=1, transport=t)
    names = [r["dataset_id"] for r in res["rows"]][:n]
    service.import_datasets(store_, _chosen(portal["slug"], *names), work_list_name="w", transport=t,
                            egeria_lookup=lambda _i: None, imported_by="tester", investigation=inv)
    return sorted(d["slug"] for d in store_.list_datasets())


def _members(payload, inv):
    return next(i for i in payload["investigations"] if i["slug"] == inv)["members"]


def test_the_datasets_members_point_at_are_exported_and_verify_is_clean(store, reg):
    _with_repo(reg)
    inv = reg.create_investigation("Water study")["slug"]
    slugs = _import_into(store, reg, inv)
    p = ie.export_inventory(reg, generated_at="t")
    assert sorted(r["slug"] for r in p["open_data"]) == slugs
    assert {(m["entity_type"], m["entity_slug"]) for m in _members(p, inv)} == {("open_data", s) for s in slugs}
    assert p["manifest"]["open_data"] == len(slugs) and p["manifest"]["refused_members"] == 0
    assert ie.verify(p) == []


def test_an_exported_row_is_the_stored_row(store, reg):
    _with_repo(reg)
    inv = reg.create_investigation("Water study")["slug"]
    (slug,) = _import_into(store, reg, inv, n=1)
    row = ie.export_inventory(reg, generated_at="t")["open_data"][0]
    stored = store.get_dataset(slug)
    assert row["identifier"] == stored["identifier"] and row["portal_slug"] == stored["portal_slug"]
    assert row["title"] == stored["title"] and "record_json" in row                # as stored, ready to re-insert


def test_a_member_whose_dataset_is_gone_is_refused_in_words_not_written(store, reg):
    _with_repo(reg)
    inv = reg.create_investigation("Water study")["slug"]
    (slug,) = _import_into(store, reg, inv, n=1)
    folio = reg.get_or_create_working_set(inv)["slug"]
    reg.add_working_set_member(folio, "open_data", "gone-dataset")
    p = ie.export_inventory(reg, generated_at="t")
    assert ("open_data", "gone-dataset") not in {(m["entity_type"], m["entity_slug"]) for m in _members(p, inv)}
    (refused,) = p["refused_members"]
    assert refused["investigation"] == inv and refused["entity_slug"] == "gone-dataset"
    assert "not in the open_data table" in refused["reason"] and "left out" in refused["reason"]
    assert p["manifest"]["refused_members"] == 1
    problems = ie.verify(p)
    assert any("refused at export" in m and "gone-dataset" in m for m in problems)
    assert not any("references unknown" in m for m in problems)                     # nothing written dangling


def test_a_dataset_no_member_points_at_stays_out(store, reg):
    _with_repo(reg)
    _import_into(store, reg, "", n=1)                                              # work list only, no investigation
    p = ie.export_inventory(reg, generated_at="t")
    assert p["open_data"] == [] and p["manifest"]["open_data"] == 0


def test_verify_catches_a_hand_edited_dangling_open_data_member(store, reg):
    _with_repo(reg)
    inv = reg.create_investigation("Water study")["slug"]
    _import_into(store, reg, inv, n=1)
    p = ie.export_inventory(reg, generated_at="t")
    p["open_data"] = []
    p["manifest"] = ie._manifest(p)
    assert any("references unknown open_data" in m for m in ie.verify(p))


def test_the_format_version_says_the_shape_changed():
    assert ie.FORMAT_VERSION == 2
