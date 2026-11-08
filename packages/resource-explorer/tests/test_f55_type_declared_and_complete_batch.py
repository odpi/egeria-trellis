"""F55 (+ the W64 residual): the engine declaration is checked per GovernanceActionType, and a partial batch is
completed from the Admin press.

(a) The engine host keeps a per-request-type presence map (`refresh_type_presence`, every Nth sweep); a row whose
    type is absent reads not runnable with the type's own words and the document that declares it.
(b) `check_and_heal(only="engine-host")` on a PRESENT canary runs the documents with no ok run row whose own created
    elements are verified absent (fake lookups here), skips and names the rest, and refuses when a lookup cannot
    answer. Egeria is never contacted: the pyegeria boundary is autospecced at `egeria_clients._build`.
"""
from __future__ import annotations

import json

import pytest

from resource_explorer import bootstrap as bs
from resource_explorer import engine_host
from tests.test_dr_egeria_runner import IDENT, Fakes as RunnerFakes, _rows, reg, root  # noqa: F401 - fixtures
from tests.test_engine_host import ENGINE_GUID, Fakes as HostFakes, _action, _host, enabled, reg as host_reg  # noqa: F401
from tests.test_engine_host_self_heal import OPT, _canary, _engine_batch
from tests.test_native_survey_run import TECH, registry  # noqa: F401 - the fixture

GAT_GUID = "0a1b2c3d-0000-4000-8000-00000000a001"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(engine_host, "_engine_answer", {"present": None, "at": ""})
    monkeypatch.setattr(engine_host, "_type_answers", {})
    monkeypatch.setattr(engine_host, "_declaring_cache", {})
    with bs._status_lock:
        bs._status.clear()
    yield
    with bs._status_lock:
        bs._status.clear()


# ── (a) the per-type presence map ──────────────────────────────────────────────

def _by_name(missing: set[str]):
    def lookup(qualified_name):
        if qualified_name == engine_host.ENGINE_QUALIFIED_NAME:
            return ENGINE_GUID
        if qualified_name in missing:
            return "No elements found"
        return GAT_GUID
    return lookup


def test_the_sweep_records_each_declared_type_once_every_nth_sweep(host_reg, enabled, monkeypatch):
    fakes = HostFakes(monkeypatch, [])
    fakes.expert.get_metadata_guid_by_unique_name.side_effect = _by_name({"ResourceExplorerSurvey::re-change-rates"})
    host = _host(host_reg)
    try:
        host.sweep_once()
        first = [c.args[0] for c in fakes.expert.get_metadata_guid_by_unique_name.call_args_list
                 if c.args[0] != engine_host.ENGINE_QUALIFIED_NAME]
        for _ in range(engine_host.TYPE_PRESENCE_EVERY_SWEEPS - 1):
            host.sweep_once()
        between = [c.args[0] for c in fakes.expert.get_metadata_guid_by_unique_name.call_args_list
                   if c.args[0] != engine_host.ENGINE_QUALIFIED_NAME]
        host.sweep_once()
        after = [c.args[0] for c in fakes.expert.get_metadata_guid_by_unique_name.call_args_list
                 if c.args[0] != engine_host.ENGINE_QUALIFIED_NAME]
    finally:
        host.close()
    supported = engine_host.supported_request_types()
    assert sorted(first) == sorted(engine_host.governance_action_type_qn_for(rt) for rt in supported), \
        "one unique-name read per declared request type on the first sweep"
    assert between == first, "no type lookup between the Nth sweeps"
    assert len(after) == 2 * len(first), "looked up again on the Nth sweep"
    assert engine_host.type_declared("re-db-derived") is True
    if "re-change-rates" in supported:
        assert engine_host.type_declared("re-change-rates") is False
    assert engine_host.type_declared("re-no-such-type") is None, "never looked up: not known"


def test_an_unreadable_type_lookup_leaves_the_type_not_known(host_reg, enabled, monkeypatch, caplog):
    import logging

    fakes = HostFakes(monkeypatch, [])
    engine_host.record_type_answer("re-db-derived", True, at="2026-11-04T10:00:00+00:00")

    def lookup(qualified_name):
        if qualified_name == engine_host.ENGINE_QUALIFIED_NAME:
            return ENGINE_GUID
        raise RuntimeError("view server busy")

    fakes.expert.get_metadata_guid_by_unique_name.side_effect = lookup
    host = _host(host_reg)
    try:
        with caplog.at_level(logging.INFO, logger="resource_explorer.engine_host"):
            host.sweep_once()
    finally:
        host.close()
    assert engine_host.type_declared("re-db-derived") is True, "the earlier verified answer stands"
    assert engine_host.type_declared("re-hub-tables") is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def _rows_by_qn(registry):
    from resource_explorer import native_survey_run as nsr

    return {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}


def test_a_row_whose_type_is_absent_reads_not_runnable_with_the_documents_name(registry, monkeypatch):
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    engine_host.record_type_answer("re-db-derived", False)
    row = _rows_by_qn(registry)["ResourceExplorerSurvey::re-db-derived"]
    assert row["runnable"] is False
    assert row["cannot_run_reason"] == ("not declared in this Egeria: the engine is, but its 're-db-derived' action type "
                                        "is not (engine-host document resource-explorer-survey-engine.md) · Admin › Repair › Declare…")
    assert nsr.TYPE_NOT_DECLARED.startswith("not declared in this Egeria: the engine is, but its ")
    # The library step's document is its own file.
    assert engine_host.declaring_document("re-change-rates") == "library/change_rates.md"
    assert engine_host.declaring_document("re-no-such-type") == ""


def test_a_type_not_yet_looked_up_or_present_leaves_the_row_offered(registry, monkeypatch):
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    assert _rows_by_qn(registry)["ResourceExplorerSurvey::re-db-derived"]["runnable"] is True, "not known is not absent"
    engine_host.record_type_answer("re-db-derived", True)
    assert _rows_by_qn(registry)["ResourceExplorerSurvey::re-db-derived"]["runnable"] is True
    # The engine's own absence is still said first, in its own words.
    engine_host.record_engine_answer(False)
    engine_host.record_type_answer("re-db-derived", False)
    assert _rows_by_qn(registry)["ResourceExplorerSurvey::re-db-derived"]["cannot_run_reason"] == nsr.ENGINE_NOT_DECLARED


def test_a_mixed_process_is_refused_by_its_re_steps_type(registry, monkeypatch):
    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    engine_host.record_type_answer("re-database-staleness", False)
    # The library step is installed where the engine host runs (else "not installed" is said first, as before).
    real = engine_host.supported_request_types
    monkeypatch.setattr(engine_host, "supported_request_types",
                        lambda: {**real(), "re-database-staleness": "database_staleness"})
    row = _rows_by_qn(registry)["GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived"]
    assert row["runnable"] is False
    assert "'re-database-staleness' action type is not" in row["cannot_run_reason"]
    assert "library/database_staleness.md" in row["cannot_run_reason"]


def test_the_press_is_refused_before_any_egeria_call(registry, monkeypatch):
    from resource_explorer import native_survey_run as nsr

    monkeypatch.setenv("RE_ENGINE_HOST_ENABLED", "true")
    engine_host.record_engine_answer(True)
    engine_host.record_type_answer("re-db-derived", False)

    class NoPort:
        def __getattr__(self, name):
            raise AssertionError(f"the press reached Egeria ({name})")

    with pytest.raises(nsr.NativeSurveyCannotRun, match="action type is not"):
        nsr.submit_native_survey(registry, NoPort(), "database", "adventureworks",
                                 "ResourceExplorerSurvey::re-db-derived", technology_type=TECH)


# ── (b) completing a partial batch from the page ──────────────────────────────

X_QN = "ResourceExplorerSurvey::re-x"     # library/x.md creates its OWN element, so the two can be verified apart


def _two_document_batch(root):
    (root / "engine-host" / "library").mkdir()
    (root / "engine-host" / "library" / "x.md").write_text(
        (root / "plain" / "only.md").read_text().replace("ResourceExplorerSurvey::re-db-derived", X_QN))
    _engine_batch(root, files=("decl.md", "library/x.md"))


def _lookups(monkeypatch, answers: dict):
    """A fake `created_element_present`: by qualified name, True/False/None; records what was asked."""
    asked = []

    def fake(qualified_name, expert=None):
        asked.append(qualified_name)
        return answers.get(qualified_name)

    monkeypatch.setattr(bs, "created_element_present", fake)
    return asked


def test_a_present_canary_completes_the_documents_with_no_ok_row_whose_elements_are_absent(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    RunnerFakes(monkeypatch)
    runner.run_document("engine-host/decl.md", force=True, identity=IDENT, registry=reg)    # act A: document 1 applied
    # RE-HEAL-LEDGER-TRUST decision 1: the applied document is verified too (present: skipped), not trusted.
    asked = _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, X_QN: False})
    seen = []
    real = runner.run_document

    def spy(path, **kw):
        seen.append((str(path).split("dr-egeria/")[-1], kw.get("heal_waiver", "")))
        return real(path, **kw)

    monkeypatch.setattr(runner, "run_document", spy)
    out = bs.check_and_heal(root, only="engine-host", requested_by="dan")["batches"]["engine-host"]
    assert out["action"] == "completed", out
    assert out["reason"].startswith("completed the batch: ran library/x.md · skipped 1 verified present: decl.md · "
                                    "1 document run"), out
    assert [p for p, _w in seen] == ["engine-host/library/x.md"], "the applied document is not run again"
    assert seen[0][1].startswith("completing the engine-host batch: canary present, 1 document(s) whose created "
                                 "elements were verified absent")
    assert "pressed by dan" in seen[0][1]
    assert asked == ["ResourceExplorerSurvey::re-db-derived", X_QN], "one lookup per created element of EVERY document"
    rows = [r for r in _rows(reg) if json.loads(r["detail"]).get("document") == "engine-host/library/x.md"]
    assert len(rows) == 1 and rows[0]["status"] == "ok"
    assert bs.already_declared_words(bs.discover_batches(root)[0], registry=reg).endswith("2 of 2 documents applied")
    # Pressed again, both now present: nothing left to run, and nothing is written.
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, X_QN: True})
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("a completed batch is never re-run"))
    again = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert again["action"] == "present", again
    assert "· verified present · 2 of 2 documents applied · nothing left to run" in again["reason"], again


def test_a_pending_document_whose_elements_are_present_is_skipped_and_named(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    RunnerFakes(monkeypatch)
    runner.run_document("engine-host/decl.md", force=True, identity=IDENT, registry=reg)
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": True, X_QN: True})  # x.md: a hand run, no row here
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("present elements are never re-created"))
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "present"
    assert out["reason"].startswith("already declared · content applied on 20")
    assert "nothing left to run" in out["reason"]
    assert "verified present" in out["reason"]
    assert "skipped 2 verified present: decl.md, library/x.md" in out["reason"]


def test_a_lookup_that_cannot_answer_refuses_the_whole_pass(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _lookups(monkeypatch, {})                                   # None for everything: undetermined
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("undetermined is never absent"))
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "present"
    assert out["reason"].startswith("already declared · no run of its first document is recorded here · NOT verified · "
                                    "0 of 2 documents applied")
    assert ("not completed: could not verify decl.md's elements in Egeria (ResourceExplorerSurvey::re-db-derived could "
            "not be looked up) · nothing run") in out["reason"]
    assert _rows(reg) == []


def test_a_completion_that_fails_part_way_says_where_it_stopped(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    RunnerFakes(monkeypatch)
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": False, X_QN: False})

    def boom(path, **kw):
        raise RuntimeError("Egeria answered 500")

    monkeypatch.setattr(runner, "run_document", boom)
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "completion_failed"
    assert out["reason"] == "completing the batch stopped at decl.md: not run · Egeria answered 500"
    assert bs.get_status(root)["batches"]["engine-host"]["last_heal_result"] == out["reason"]


def test_a_run_in_progress_of_any_document_refuses_the_completion(root, reg, monkeypatch):
    from resource_explorer import dr_egeria_runner as runner

    _two_document_batch(root)
    _canary(monkeypatch, True)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    _lookups(monkeypatch, {"ResourceExplorerSurvey::re-db-derived": False})
    monkeypatch.setattr(runner, "run_in_progress", lambda path: {"id": "live-1", "ts": "T"} if path.name == "x.md" else None)
    monkeypatch.setattr(runner, "run_document", lambda *a, **k: pytest.fail("nothing runs beside a live run"))
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "present" and "a run of library/x.md is in progress (activity live-1 since T)" in out["reason"]


def test_a_missing_canary_still_heals_the_whole_batch_as_before(root, reg, monkeypatch):
    """F55 touches only the present-canary branch: a verified-missing canary is F54's heal, every document."""
    _two_document_batch(root)
    _canary(monkeypatch, False)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    monkeypatch.setattr(bs, "created_element_present", lambda *a, **k: pytest.fail("the heal needs no element lookup"))
    RunnerFakes(monkeypatch)
    out = bs.check_and_heal(root, only="engine-host")["batches"]["engine-host"]
    assert out["action"] == "healed" and "2 documents run" in out["reason"]
