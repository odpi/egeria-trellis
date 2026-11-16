"""EA's EGERIA report-spec tier (brief B3, D3): sync, sources, absence messages, Phase-1 retired.

No live Egeria and no database: a fake client stands in for Egeria, and pyegeria's cache goes to a
temp directory (PYEGERIA_REPORT_SPEC_CACHE_DIR). The real `sync_report_specs` runs against it.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from advisor import egeria_spec_sync as ess

FOLDER = "ReportSpecFolder::Consumer::EgeriaAdvisor"
FOLDER_GUID = "f0000000-0000-0000-0000-000000000001"


def _folder_el():
    return {"elementHeader": {"guid": FOLDER_GUID, "type": {"typeName": "CollectionFolder",
                                                            "superTypeNames": ["Collection"]}},
            "properties": {"qualifiedName": FOLDER, "displayName": "Egeria Advisor"}}


def _spec_el(label: str, guid: str, *, status: str = "ACTIVE", purpose: str = "Collections held for EA",
             annotations: dict | None = None):
    from pyegeria.view.base_report_formats import get_report_registry
    from pyegeria.view.microflow import format_set_to_microflow
    mf = format_set_to_microflow(get_report_registry()["Collections"])
    props = {"qualifiedName": f"ReportSpec::{label}", "displayName": label, "contentStatus": status,
             "purpose": purpose, "description": "Lists collections, published to Egeria Advisor.",
             "microflow": mf.to_json()}
    if annotations is not None:
        props["additionalProperties"] = {"annotations": json.dumps(annotations)}
    return {"elementHeader": {"guid": guid, "versions": {"version": 1},
                              "type": {"typeName": "ReportSpec", "superTypeNames": ["ReportType", "Collection"]}},
            "properties": props}


class FakeEgeria:
    """Only the reads sync_report_specs makes. Records nothing it is not asked; writes nothing."""
    platform_url = "https://fake-egeria:9443"
    view_server = "fake-view-server"
    user_id = "ea-service"

    def __init__(self, *, folder=True, members=()):
        self.folder = folder
        self.members = {m["elementHeader"]["guid"]: m for m in members}

    def get_collections_by_name(self, name, metadata_element_type=None, output_format="JSON"):
        # The folder read before pyegeria's unique-name reads (kept for an older pyegeria, 6.2.41).
        if metadata_element_type == "CollectionFolder" and self.folder and name == FOLDER:
            return [_folder_el()]
        return "No elements found"

    def get_element_by_unique_name(self, qualified_name, *_, **__):
        # Egeria's exact unique-name read (classification explorer), which a newer pyegeria's folder and
        # consumers-root reads use when the client has it. Without it here, pyegeria builds a real
        # ClassificationExplorer on the fake URL, and the folder reads as "couldn't tell" (folder_unreadable).
        # Only EA's folder exists (when `folder`); anything else, the consumers root included, is Egeria's
        # not-found, as the paged read said.
        if self.folder and qualified_name == FOLDER:
            return _folder_el()
        return "No elements found"

    def get_collection_members(self, collection_guid=None, start_from=0, page_size=0,
                               output_format="JSON", **_kwargs):
        # Pages like Egeria (pyegeria >= 6.2.36 walks a folder page by page until an
        # empty page): a page past the end is empty, never the first page again. Empty is
        # the str "No elements found", not [], because that is what live Egeria returns
        # for an empty slice (checked against 6.2.36).
        if collection_guid != FOLDER_GUID:
            return "No elements found"
        members = list(self.members.values())
        page = members[start_from:start_from + page_size] if page_size else members[start_from:]
        return page or "No elements found"

    def get_collection_by_guid(self, guid, output_format="JSON"):
        return self.members.get(guid) or "No elements found"


@pytest.fixture(autouse=True)
def _no_real_explorer(monkeypatch):
    """The fake answers every read; a real ClassificationExplorer would try to reach the fake URL."""
    built = []
    try:
        import pyegeria.omvs.classification_explorer as ce
    except ImportError:
        yield built
        return

    class _Refused:
        def __init__(self, *a, **k):
            built.append(a)
            raise AssertionError("a real ClassificationExplorer was built: the fake lacks a read pyegeria uses")
    monkeypatch.setattr(ce, "ClassificationExplorer", _Refused)
    yield built
    assert not built, "a real ClassificationExplorer was built: the fake lacks a read pyegeria uses"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("PYEGERIA_REPORT_SPEC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv(ess.ADVISOR_FOLDERS_ENV, raising=False)
    monkeypatch.delenv(ess.PYEGERIA_FOLDERS_ENV, raising=False)
    ess._reset_for_tests()
    from pyegeria.view import egeria_spec_cache
    egeria_spec_cache._invalidate_tier()
    egeria_spec_cache.egeria_tier(force=True)
    yield
    ess._reset_for_tests()
    egeria_spec_cache._invalidate_tier()


# ── configuration ──────────────────────────────────────────────────────────────

def test_default_folder_is_eas_consumer_folder():
    assert ess.configured_folders() == [FOLDER]
    assert ess.egeria_held_status()["folders_source"] == "default"


def test_advisor_env_wins_over_pyegeria_env(monkeypatch):
    monkeypatch.setenv(ess.PYEGERIA_FOLDERS_ENV, "ReportSpecFolder::Consumer::Portal")
    assert ess.configured_folders() == ["ReportSpecFolder::Consumer::Portal"]
    monkeypatch.setenv(ess.ADVISOR_FOLDERS_ENV, f"{FOLDER}, ReportSpecFolder::Other")
    assert ess.configured_folders() == [FOLDER, "ReportSpecFolder::Other"]


def test_sync_is_shared_and_never_asks_recursion_pyegeria_lacks():
    kwargs = ess._sync_kwargs()
    assert kwargs["shared"] is True
    assert ("recursive" in kwargs) == ess.supports_recursion()


def test_zone_filter_status_is_stated():
    """B0: pyegeria skips zoned specs from a shared cache itself; EA never asks for public_only and the
    status says how many the last sync skipped (and that none ran, rather than "0", before a sync)."""
    assert "public_only" not in ess._sync_kwargs()
    assert ess._zone_filter_words({}, []) == "zoned specs are skipped from the shared cache; no sync has run yet"
    report = {"skipped": [["A", "zoned: not synced into a shared cache (until B6's public-only read); zones z"],
                          ["B", "not a report spec"]]}
    assert ess._zone_filter_words(report, report["skipped"]) == "zoned specs skipped from the shared cache: 1"
    assert ess._zone_filter_words({"skipped": []}, []) == "zoned specs skipped from the shared cache: 0"


# ── absence messages: never a bare empty list ──────────────────────────────────

def test_no_folder_configured(monkeypatch):
    monkeypatch.setenv(ess.ADVISOR_FOLDERS_ENV, "")
    held = ess.sync_now(client_factory=lambda: FakeEgeria())
    assert held["count"] == 0
    assert held["reason"] == "no_folders"
    assert "no report spec folder is configured" in held["message"]


def test_not_synced_yet_says_so():
    held = ess.egeria_held_status()
    assert held["count"] == 0 and held["reason"] == "not_synced"
    assert "Egeria has not been reached yet" in held["message"]


def test_folder_does_not_exist_yet_says_not_found():
    """Consumer-folders phase 1 has not created the folder yet: the message must say 'not found'."""
    held = ess.sync_now(client_factory=lambda: FakeEgeria(folder=False))
    assert held["state"] == "ok"
    assert held["count"] == 0
    assert held["reason"] == "folder_not_found"
    assert "not found" in held["message"]
    assert FOLDER in held["message"]
    assert "ea-service" in held["message"]  # who could not see it
    assert held["folder_results"] == [{"folder": FOLDER, "result": "not_found", "count": 0,
                                       "detail": held["folder_results"][0]["detail"]}]


def test_folder_with_no_members_says_empty():
    held = ess.sync_now(client_factory=lambda: FakeEgeria(members=[]))
    assert held["reason"] == "folder_empty"
    assert "holds no active report specs" in held["message"]


def test_folder_with_only_draft_specs_says_empty_and_why():
    fake = FakeEgeria(members=[_spec_el("EA Draft Spec", "d0000000-0000-0000-0000-000000000001", status="DRAFT")])
    held = ess.sync_now(client_factory=lambda: fake)
    assert held["reason"] == "folder_empty"
    assert "EA Draft Spec" in held["message"] and "DRAFT" in held["message"]


def test_sync_failure_is_recorded_not_raised():
    def boom():
        raise ConnectionError("Egeria is starting")
    held = ess.sync_now(client_factory=boom)
    assert held["state"] == "failed" and held["reason"] == "sync_failed"
    assert "Egeria is starting" in held["message"]


# ── a held spec: tier, source, search ──────────────────────────────────────────

def test_held_spec_enters_egeria_tier_with_source_and_search_text():
    label = "EA Collections Held"
    fake = FakeEgeria(members=[_spec_el(label, "a0000000-0000-0000-0000-000000000001",
                                        annotations={"questions": ["which collections does EA publish?"],
                                                     "perspectives": ["Data Steward"]})])
    held = ess.sync_now(client_factory=lambda: fake)
    assert held["state"] == "ok"
    assert held["count"] == 1 and held["specs"] == [label]
    assert held["message"] is None and held["reason"] == "ok"
    assert held["folder_results"][0]["result"] == "ok"

    sources = ess.spec_sources()
    assert sources[label]["source"] == "egeria"
    assert sources["Collections"]["source"] == "builtin"

    from advisor.report_pipeline import QuestionSpecIndex
    entries = QuestionSpecIndex()._load_egeria_tier_sources()
    assert (label, ["Data Steward"], "which collections does EA publish?") in entries


def test_held_spec_without_questions_is_searchable_by_purpose():
    label = "EA Collections Plain"
    fake = FakeEgeria(members=[_spec_el(label, "b0000000-0000-0000-0000-000000000001",
                                        purpose="Collections the advisor shows")])
    ess.sync_now(client_factory=lambda: fake)
    from advisor.report_pipeline import QuestionSpecIndex
    texts = [q for name, _, q in QuestionSpecIndex()._load_egeria_tier_sources() if name == label]
    assert "Collections the advisor shows" in texts


def test_egeria_copy_shadowing_a_builtin_is_reported():
    fake = FakeEgeria(members=[_spec_el("Collections", "c0000000-0000-0000-0000-000000000001")])
    ess.sync_now(client_factory=lambda: fake)
    sources = ess.spec_sources()
    assert sources["Collections"] == {"source": "egeria", "shadows": "builtin", "shadowed_by": None}
    assert sources["builtin:Collections"]["shadowed_by"] == "egeria"


def test_ea_registered_spec_shows_as_user():
    from pyegeria.view.base_report_formats import get_report_registry
    from advisor.report_spec_parser import register_report_spec
    register_report_spec("ea-user-spec-test", get_report_registry()["Collections"].model_copy())
    try:
        assert ess.spec_sources()["ea-user-spec-test"]["source"] == "user"
    finally:
        from pyegeria.view import base_report_formats as brf
        brf._RUNTIME_REPORT_FORMATS.pop("ea-user-spec-test", None)
        getattr(brf, "report_specs", {}).pop("ea-user-spec-test", None)


# ── startup never blocks; retries while Egeria is down ─────────────────────────

def test_background_sync_does_not_block_and_retries():
    gate = threading.Event()
    calls = []

    def factory():
        calls.append(1)
        gate.wait(5)
        if len(calls) < 2:
            raise ConnectionError("not up yet")
        return FakeEgeria(folder=False)

    t = ess.start_background_sync(backoff=(0,), sleep=lambda s: None, client_factory=factory)
    assert t.is_alive()  # returned while the first attempt is still waiting on "Egeria"
    gate.set()
    t.join(5)
    assert not t.is_alive()
    assert len(calls) == 2
    assert ess.egeria_held_status()["reason"] == "folder_not_found"


def test_second_sync_while_one_runs_is_refused():
    gate = threading.Event()

    def slow():
        gate.wait(5)
        return FakeEgeria(members=[])

    t = threading.Thread(target=lambda: ess.sync_now(client_factory=slow))
    t.start()
    try:
        for _ in range(100):
            if ess._state["state"] == "running":
                break
            threading.Event().wait(0.01)
        with pytest.raises(ess.SyncBusy):
            ess.sync_now(client_factory=lambda: FakeEgeria())
    finally:
        gate.set()
        t.join(5)


# ── Phase-1 retired ────────────────────────────────────────────────────────────

def test_phase1_loader_is_retired():
    from advisor import report_pipeline
    from advisor.report_pipeline import ReportPipeline
    assert not hasattr(ReportPipeline, "_try_refresh_egeria_specs")
    src = Path(report_pipeline.__file__).read_text()
    assert "load_egeria_report_specs(" not in src
    admin_src = (Path(report_pipeline.__file__).parent / "web" / "admin.py").read_text()
    assert "_try_refresh_egeria_specs" not in admin_src


# ── the catalog API carries sources and the held status ─────────────────────────

def test_reports_api_carries_sources_and_absence_message():
    import asyncio
    from advisor.web.reports import list_reports
    ess.sync_now(client_factory=lambda: FakeEgeria(folder=False))
    data = asyncio.run(list_reports(None, include_dre=False))
    assert data["egeria_held"]["reason"] == "folder_not_found"
    # B6: anonymous -> restricted specs are "sign in", never an empty list; used_by says its state.
    assert data["restricted"]["state"] == "not_signed_in" and data["restricted"]["message"]
    assert data["used_by"]["state"] in ("ok", "partial", "not_read", "unsupported")
    assert data["catalog_status"]["registry"] == "ok"
    assert "not found" in data["egeria_held"]["message"]
    listed = {n for names in data["catalog"].values() for n in names}
    assert listed and set(data["sources"]) == listed
    assert data["sources"]["Collections"]["source"] == "builtin"
