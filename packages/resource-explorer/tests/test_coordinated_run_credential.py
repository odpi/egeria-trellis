"""One-run credential for Egeria-coordinated runs (docs/design-notes/DESIGN-COORDINATED-RUN-CREDENTIAL.md §3).

One test per threat in §3's table, plus the holder's clear paths and one end-to-end run. Only the pyegeria boundary is
faked (test_engine_host_e4's `FakeEgeria`: every client a `create_autospec(<real class>, spec_set=True)`), the
step runner at its connection boundary (an autospec of the real `postgres_operations` runner), and the read-back
port (an autospec of `EgeriaSurveyPort`). Every value here is invented.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from resource_explorer import native_survey_run as _nsr
from tests.test_engine_host_case3 import (
    ANALYSIS,
    INSTANCE,
    INSTANCE_2,
    TECH,
    _process_action,
    _views,
    _with_credential,
)
from tests.test_engine_host_case3 import (
    PASSWORD as STORED_PASSWORD,
)
from tests.test_engine_host_e4 import (  # noqa: F401 - fixtures and helpers reused
    DB_GUID,
    _rows,
    _run_queue_once,
    _sweep,
    egeria,
    no_connection,
    reg,
)

#: The real start path, bound before any test replaces it, so each route test autospecs the real signature.
_REAL_SUBMIT = _nsr.submit_native_survey
_REAL_IDENTITY_FROM_CLAIMS = __import__("resource_explorer.auth", fromlist=["identity_from_claims"]).identity_from_claims
_REAL_REGISTRY_INIT = __import__("resource_explorer.registry", fromlist=["ProjectRegistry"]).ProjectRegistry.__init__
PKG = Path(__file__).resolve().parent.parent
SRC = PKG / "resource_explorer"
#: Invented. Shorter than 8 characters on purpose: RE redacts a STORED password only from 8 characters, so this proves
#: the one-run scope reaches the redaction (any length), and it has no shape `scrub_text` would recognise.
ONE_RUN_USER = "elevated_reader"
ONE_RUN_PASSWORD = "Zq7#wK!"
#: Invented. What a route test sends: the start route refuses a one-run password shorter than 8 characters (review fix
#: round 1, item 2), so the route tests use a long one.
ROUTE_PASSWORD = "Zq7#wK!route-9"
STARTER = "dan"


@pytest.fixture(autouse=True)
def _empty_holder(monkeypatch):
    """A fresh holder per test, on a clock the test can move."""
    from resource_explorer import run_credential_holder as h

    clock = {"now": 1000.0}
    monkeypatch.setattr(h, "_HOLDER", h._Holder(clock=lambda: clock["now"]))
    monkeypatch.delenv(h.TTL_ENV, raising=False)
    return clock


@pytest.fixture(autouse=True)
def _no_egeria_unless_faked(monkeypatch):
    """The start window reads the process instance's creation time from Egeria. A fresh creation-time cache per test,
    and no real pyegeria client: a test without the `egeria` fixture (which replaces this seam with its autospecs) gets
    a client build that raises, so the read fails and the instance is held, exactly as before the window existed."""
    from resource_explorer import egeria_clients, engine_host

    monkeypatch.setattr(engine_host, "_CREATED", {}, raising=False)
    monkeypatch.delenv("RE_RUN_CREDENTIAL_INITIATE_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("RE_RUN_CREDENTIAL_CLOCK_SKEW_SECONDS", raising=False)

    def _no_build(cls, *a, **k):
        raise ConnectionError(f"no Egeria in this test ({cls.__name__})")

    monkeypatch.setattr(egeria_clients, "_build", _no_build)


@pytest.fixture
def runner(monkeypatch):
    """`postgres_operations`' runner at its connection boundary: an autospec of the real one (the signature is
    bound), returning the shape the real runner returns. Records what it was handed."""
    from resource_explorer.surveyors.database import survey_definition_adapter as db

    real = db._ADAPTER.re_analysis_steps["postgres_operations"]
    fake = create_autospec(real, spec_set=True, return_value={
        "schema_info": {}, "operations": {"privilege_audit": None, "db_activity_signals": None, "db_resilience": None,
                                          "db_external_dependencies": None}})
    monkeypatch.setitem(db._ADAPTER.re_analysis_steps, "postgres_operations", fake)
    return fake


def _creds_handed(fake) -> list[tuple[str, str]]:
    return [(c.kwargs.get("db_user", ""), c.kwargs.get("db_pwd", "")) for c in fake.call_args_list]


def _start_with_credential(reg, egeria) -> str:
    """The coordinated start path (`submit_native_survey`, what the route calls) with a one-run credential."""
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import port_session, submit_native_survey

    egeria.declared.add(ANALYSIS)
    egeria.assets.add(DB_GUID)
    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        state = submit_native_survey(reg, port, "database", "coco_pharma", ANALYSIS, technology_type=TECH,
                                     submitted_by=STARTER, run_credential=(ONE_RUN_USER, ONE_RUN_PASSWORD))
    return state["engine_action_guid"]


def _marker_row(reg, instance: str, *, marker: bool = True, holder: dict | None = None) -> dict:
    from resource_explorer import mixed_process

    extra = mixed_process.expected_steps_record(ANALYSIS)
    if marker:
        extra["run_credential"] = {"user": ONE_RUN_USER, "scope": "this run"}
        if holder is not None:
            extra["run_credential"]["holder"] = holder
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-18T09:00:00", engine_action_guid=instance, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re", declared_extra=extra)
    return next(r for r in reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
                if r["engine_action_guid"] == instance)


def _leak_forms(secret: str) -> list[str]:
    """Every spelling a leak scan looks for: as typed, JSON-escaped, repr-escaped, percent-encoded (a DSN) and
    form-encoded."""
    return sorted({secret, json.dumps(secret)[1:-1], repr(secret)[1:-1], urllib.parse.quote(secret, safe=""),
                   urllib.parse.quote(secret), urllib.parse.quote_plus(secret)})


def _all_rows_json(reg) -> str:
    """The JSON of every row of every table in the test registry."""
    path = reg.database_url.split("sqlite:///", 1)[-1] if "sqlite:///" in reg.database_url else reg.database_url
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        dump = {t: [dict(r) for r in conn.execute(f'SELECT * FROM "{t}"')] for t in tables}
    finally:
        conn.close()
    return json.dumps(dump, default=str)


def _port(views):
    """The read-back boundary: an autospec of the real port RE uses, answering the instance's engine actions."""
    from resource_explorer import catalog_and_survey as cas

    port = create_autospec(cas.PyegeriaRegistrationPort, spec_set=True, instance=True)
    port.read_process_run.return_value = views
    return port


# ── threat: leak via logs, exception text or proof rows ──────────────────────────────────────────────────────

def test_a_step_that_raises_with_the_password_leaves_no_trace_in_logs_rows_or_egeria(reg, egeria, no_connection,
                                                                                    runner, caplog):
    _with_credential(reg)
    instance = _start_with_credential(reg, egeria)
    runner.side_effect = RuntimeError(f"FATAL: could not log in as {ONE_RUN_USER} using {ONE_RUN_PASSWORD} "
                                      f"(echo {ONE_RUN_PASSWORD!r}; dsn postgresql://{ONE_RUN_USER}:"
                                      f"{urllib.parse.quote(ONE_RUN_PASSWORD, safe='')}@db.invalid/coco)")
    _process_action(egeria, "ea-leak", "postgres_operations", instance=instance, next_guards=())
    caplog.set_level(logging.DEBUG)
    assert _sweep(reg).claimed == ["ea-leak"]
    assert _run_queue_once(reg) is not None

    assert _creds_handed(runner) == [(ONE_RUN_USER, ONE_RUN_PASSWORD)]     # it did run with it, and it raised
    [row] = _rows(reg)
    assert row["target"]["outcome"]["guard"] == "survey-failed", row["target"]["outcome"]
    assert "***" in row["target"]["outcome"]["message"]                    # the words are kept, the secret masked
    everything = _all_rows_json(reg)                                       # runs.target, step_runs.declared, activity
    for form in _leak_forms(ONE_RUN_PASSWORD):
        assert form not in everything
        assert form not in caplog.text
        assert all(form not in (r.getMessage() + (r.exc_text or "")) for r in caplog.records)
        assert form not in json.dumps(egeria.actions, default=str)        # the completion Egeria was told
        assert form not in json.dumps(egeria.target_statuses, default=str)
    assert STORED_PASSWORD not in everything


def test_an_error_raised_around_the_step_runner_is_masked_before_it_is_stored_or_sent(reg, egeria, no_connection,
                                                                                    runner, monkeypatch, caplog):
    """The executor itself raises (not the step): the engine host's own words (`_short`) carry the exception text,
    and the outcome is masked before it is stored on the run row or told to Egeria."""
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    _with_credential(reg)
    instance = _start_with_credential(reg, egeria)
    boom = create_autospec(SurveyDefinitionExecutor.run_synthetic_step, side_effect=RuntimeError(
        f"adapter could not open a session for {ONE_RUN_USER}/{ONE_RUN_PASSWORD}"))
    monkeypatch.setattr(SurveyDefinitionExecutor, "run_synthetic_step", boom)
    _process_action(egeria, "ea-boom", "postgres_operations", instance=instance, next_guards=())
    caplog.set_level(logging.DEBUG)
    _sweep(reg)
    _run_queue_once(reg)

    assert boom.call_args.kwargs["db_pwd"] == ONE_RUN_PASSWORD           # it was handed the one-run credential
    [row] = _rows(reg)
    assert "could not run" in row["target"]["outcome"]["message"]
    assert ONE_RUN_PASSWORD not in _all_rows_json(reg)
    assert ONE_RUN_PASSWORD not in json.dumps(egeria.actions, default=str)
    assert ONE_RUN_PASSWORD not in caplog.text


def test_the_holder_never_formats_the_password():
    from resource_explorer import run_credential_holder as h

    h.put("inst-repr", ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    held = h.take("inst-repr")
    for text in (repr(held), str(held), f"{held}", repr(h._HOLDER), str(h._HOLDER), repr(held.ran_as())):
        assert ONE_RUN_PASSWORD not in text
    assert held.ran_as() == {"user": ONE_RUN_USER, "scope": "this run"}


# ── threat: reuse after the run (each clear path) ────────────────────────────────────────────────────────────

def test_the_holder_is_cleared_when_the_run_line_says_the_run_is_over(reg):
    from resource_explorer import mixed_process
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    plan = [st.request_type for st in mixed_process.definition_process(ANALYSIS).steps[:2]]
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-18T09:00:00", engine_action_guid=INSTANCE, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re",
        declared_extra={"expected_steps": plan, "run_credential": {"user": ONE_RUN_USER, "scope": "this run"}})
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)

    # one of two steps done: still running, still held
    going = nsr.refresh_mixed_run(reg, _port(_views(1)), run, entity_type="database", slug="coco_pharma")
    assert going["state"] == "running" and h.take(INSTANCE) is not None
    # both done: the run line's own derivation says complete, and the holder is cleared
    done = nsr.refresh_mixed_run(reg, _port(_views(2)), run, entity_type="database", slug="coco_pharma")
    assert done["state"] == "complete" and h.take(INSTANCE) is None


def test_a_failed_or_cancelled_run_clears_it_too(reg):
    from resource_explorer import mixed_process
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    plan = [st.request_type for st in mixed_process.definition_process(ANALYSIS).steps[:2]]
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-18T09:00:00", engine_action_guid=INSTANCE, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re", declared_extra={"expected_steps": plan})
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    views = _views(1)
    views[0].status = "CANCELLED"
    out = nsr.refresh_mixed_run(reg, _port(views), run, entity_type="database", slug="coco_pharma")
    assert out.get("cancelled") is True and h.take(INSTANCE) is None


def test_cancel_run_clears_it_as_soon_as_egeria_accepts(reg):
    from resource_explorer import mixed_process
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    plan = [st.request_type for st in mixed_process.definition_process(ANALYSIS).steps[:2]]
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-18T09:00:00", engine_action_guid=INSTANCE, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re", declared_extra={"expected_steps": plan})
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    views = _views(1)
    views[0].status = "IN_PROGRESS"                    # Egeria accepted; its read-back does not show CANCELLED yet
    port = _port(views)
    out = nsr.cancel_process_run(reg, port, "database", "coco_pharma", run, by=STARTER)
    assert out["accepted"] is True and out["cancelled"] is False
    port.cancel_process_run.assert_called_once_with(INSTANCE)
    assert h.take(INSTANCE) is None


def test_a_cancel_egeria_did_not_accept_leaves_it_for_the_steps_still_to_run(reg):
    from resource_explorer import mixed_process
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    plan = [st.request_type for st in mixed_process.definition_process(ANALYSIS).steps[:2]]
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", ANALYSIS, "2026-10-18T09:00:00", engine_action_guid=INSTANCE, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re", declared_extra={"expected_steps": plan})
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    views = _views(1)
    views[0].status = "IN_PROGRESS"
    port = _port(views)
    port.cancel_process_run.side_effect = ConnectionError("no route to host")
    out = nsr.cancel_process_run(reg, port, "database", "coco_pharma", run, by=STARTER)
    assert out["accepted"] is False and h.take(INSTANCE) is not None


def test_the_ttl_ends_it_two_hours_from_the_start_and_is_never_unbounded(_empty_holder, monkeypatch):
    from resource_explorer import run_credential_holder as h

    clock = _empty_holder
    h.put("inst-ttl", ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    clock["now"] += 7199
    assert h.take("inst-ttl") is not None
    clock["now"] += 1
    assert h.take("inst-ttl") is None
    # configurable, and a value that is not a finite positive number is the default, never "forever"
    for raw, want in (("60", 60.0), ("0", 7200.0), ("-5", 7200.0), ("inf", 7200.0), ("nan", 7200.0),
                      ("soon", 7200.0), ("", 7200.0)):
        monkeypatch.setenv(h.TTL_ENV, raw)
        assert h.configured_ttl() == want, raw
    with pytest.raises(ValueError):
        h.put("inst-forever", ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER, ttl=float("inf"))


def test_process_exit_clears_it():
    from resource_explorer import run_credential_holder as h

    h.put("inst-exit", ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    h._at_exit()
    assert h.take("inst-exit") is None
    tree = ast.parse((SRC / "run_credential_holder.py").read_text())
    registered = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "register" and getattr(n.func.value, "id", "") == "atexit"]
    assert [getattr(c.args[0], "id", "") for c in registered] == ["_at_exit"]


# ── threat: pick-up by a concurrent run on the same database ─────────────────────────────────────────────────

def test_two_instances_on_one_database_each_step_uses_its_own_credential(reg, egeria, no_connection, runner):
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    _marker_row(reg, INSTANCE)
    _marker_row(reg, INSTANCE_2, marker=False)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    _process_action(egeria, "ea-with", "postgres_operations", instance=INSTANCE, next_guards=())
    _process_action(egeria, "ea-without", "postgres_operations", instance=INSTANCE_2, next_guards=())
    assert sorted(_sweep(reg).claimed) == ["ea-with", "ea-without"]
    _run_queue_once(reg)
    _run_queue_once(reg)

    assert sorted(_creds_handed(runner)) == sorted([(ONE_RUN_USER, ONE_RUN_PASSWORD), ("egeria_user", STORED_PASSWORD)])
    by_action = {r["target"]["engine_action_guid"]: r["target"]["outcome"] for r in _rows(reg)}
    assert by_action["ea-with"]["ran_as"] == {"user": ONE_RUN_USER, "scope": "this run"}
    assert "ran_as" not in by_action["ea-without"]
    # the instance-2 run never says it ran as the elevated login
    other = next(r for r in reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
                 if r["engine_action_guid"] == INSTANCE_2)
    assert "ran_as" not in json.loads(other["declared"])


# ── threat: silent fallback after a restart ──────────────────────────────────────────────────────────────────

def test_after_a_restart_a_step_of_a_run_that_asked_for_one_is_refused_and_never_uses_the_stored_login(
        reg, egeria, no_connection, runner):
    _with_credential(reg)
    _marker_row(reg, INSTANCE)                    # the start recorded the marker; the holder is empty (restarted)
    _process_action(egeria, "ea-after", "postgres_operations", instance=INSTANCE, next_guards=())
    assert _sweep(reg).claimed == ["ea-after"]
    _run_queue_once(reg)

    from resource_explorer import engine_host

    runner.assert_not_called()                    # neither the stored login nor anything else
    done = egeria.actions["ea-after"]["completion"]
    assert done["status"] == "INVALID"
    # A marker with no holder identity proves nothing about why: the neutral words (fix round 1, item 5).
    assert engine_host.RUN_CREDENTIAL_LOST_NEUTRAL in done["message"]
    [row] = _rows(reg)
    assert row["target"]["outcome"]["guard"] == "survey-invalid"
    assert STORED_PASSWORD not in _all_rows_json(reg)


def test_a_run_that_never_asked_for_one_still_uses_the_stored_login(reg, egeria, no_connection, runner):
    _with_credential(reg)
    _marker_row(reg, INSTANCE, marker=False)
    _process_action(egeria, "ea-plain", "postgres_operations", instance=INSTANCE, next_guards=())
    _sweep(reg)
    _run_queue_once(reg)
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]


def test_an_unreadable_marker_is_refused_never_guessed(reg, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setattr(reg, "list_native_survey_runs", create_autospec(reg.list_native_survey_runs,
                                                                         side_effect=OSError("registry gone")))
    held, refusal = engine_host._one_run_credential(reg, {"request_type": "re-postgres-operations",
                                                         "process_instance_guid": INSTANCE, "slug": "coco_pharma",
                                                         "definition": ANALYSIS})
    assert held is None and refusal.startswith(engine_host.RUN_CREDENTIAL_UNKNOWN_SENTENCE)


# ── threat: applied to a native Egeria step ──────────────────────────────────────────────────────────────────

def test_a_native_step_never_reads_the_holder(reg, monkeypatch, runner, no_connection):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    spy = create_autospec(h.take, side_effect=h.take)
    monkeypatch.setattr(h, "take", spy)
    target = {"slug": "coco_pharma", "step_key": "postgres_operations", "request_type": "survey-postgres-database",
              "process_instance_guid": INSTANCE, "definition": ANALYSIS, "engine_action_guid": "ea-native",
              "asset_guid": DB_GUID}
    assert engine_host._one_run_credential(reg, target) == (None, "")
    engine_host._decide_one(reg, target)
    spy.assert_not_called()
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]
    # and the run line says, per step, that a step Egeria runs itself uses the stored credential
    from resource_explorer import native_survey_run as nsr

    row = {"engine_action_guid": INSTANCE, "executor_ref": ANALYSIS, "declared": json.dumps({
        "run_credential": {"user": ONE_RUN_USER, "scope": "this run"},
        "expected_steps": ["survey-postgres-database", "re-db-derived"]})}
    assert nsr.derive_mixed_state(row)["native_steps"] == [1]


# ── threat: someone else supplies or reads it ────────────────────────────────────────────────────────────────

_ALLOWED = {"put", "take", "clear", "SCOPE", "RunCredential", "configured_ttl", "TTL_ENV", "DEFAULT_TTL_SECONDS",
            # fix round 1: facts with no secret in them (items 5 and 6)
            "process_identity", "expired", "starting", "start_pending",
            # fix round 2, F4: also a fact with no secret in it
            "cleared"}


def test_nothing_lists_or_reads_the_holder_but_take_and_only_the_engine_host_takes():
    # The module's surface: no listing, no iteration, no membership, no route.
    tree = ast.parse((SRC / "run_credential_holder.py").read_text())
    holder = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_Holder")
    methods = {n.name for n in holder.body if isinstance(n, ast.FunctionDef)}
    assert methods == {"__init__", "__repr__", "_expire", "_forget", "put", "take", "clear", "_clear_all", "gone",
                       "begin_start", "end_start", "start_pending"}, methods
    public = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")}
    assert public == {"configured_ttl", "RunCredential", "put", "take", "clear", "process_identity", "expired",
                      "cleared", "starting", "start_pending"}, public

    # Every other module: only the three functions and the constants; `take` only in the engine host; `put` only in
    # the coordinated start path; never the private holder; no route reads it.
    uses: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        if path.name == "run_credential_holder.py":
            continue
        text = path.read_text()
        if "run_credential_holder" not in text:
            continue
        mod = ast.parse(text)
        names: set[str] = set()
        for node in ast.walk(mod):
            if isinstance(node, ast.ImportFrom) and node.module == "resource_explorer.run_credential_holder":
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.Attribute) and getattr(node.value, "id", "") == "run_credential_holder":
                names.add(node.attr)
        uses[str(path.relative_to(SRC))] = names
    assert all(names <= _ALLOWED for names in uses.values()), uses
    assert {p for p, n in uses.items() if "take" in n} == {"engine_host.py"}, uses
    assert {p for p, n in uses.items() if "put" in n} == {"native_survey_run.py"}, uses
    assert all(not ({"put", "take", "clear"} & n) for p, n in uses.items() if p.startswith("web/")), uses


def _start_route(monkeypatch, *, who: str | None, user: str = ONE_RUN_USER, pwd: str = ROUTE_PASSWORD,
                 daemon: bool = False, submit_side_effect=None, rows=None):
    """POST the coordinated start with a credential, as `who` (None: nobody signed in). The start path itself is an
    autospec of the real `submit_native_survey`, so the route's gate is all that is under test here."""
    from fastapi.testclient import TestClient

    from resource_explorer import native_survey_run as nsr
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": who or "", "egeria_token": "tok", "role": "user"})
    monkeypatch.setattr("resource_explorer.auth.identity_from_claims",
                        lambda c: _REAL_IDENTITY_FROM_CLAIMS(c) if who else None)
    monkeypatch.setattr("resource_explorer.egeria_clients.is_daemon_user", lambda u: daemon and bool(u))
    from tests.zone_fakes import open_access

    open_access(monkeypatch)
    submit = create_autospec(_REAL_SUBMIT, spec_set=True, side_effect=submit_side_effect,
                             return_value={"engine_action_guid": "pi-9", "state": "submitted"})
    monkeypatch.setattr(nsr, "submit_native_survey", submit)
    monkeypatch.setattr(nsr, "native_survey_rows", rows or (lambda *a, **k: []))
    monkeypatch.setattr("resource_explorer.web.routes.native_surveys._port", lambda: object())
    monkeypatch.setattr(nsr, "port_session", lambda p: __import__("contextlib").nullcontext(p))
    import tempfile

    db = ProjectRegistry.__new__(ProjectRegistry)
    _REAL_REGISTRY_INIT(db, db_path=str(Path(tempfile.mkdtemp()) / "route.db"))
    db.register_database(DatabaseEntity(slug="coco_pharma", display_name="coco_pharma", db_type="postgresql",
                                        host="localhost", port=5442, database_name="coco_pharma",
                                        egeria_asset_guid=DB_GUID))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", db.__dict__) or None)
    from resource_explorer.web.app import app

    resp = TestClient(app).post("/api/native-surveys/database/coco_pharma/run",
                                json={"process_qualified_name": ANALYSIS, "db_user": user, "db_pwd": pwd})
    return resp, submit, db


def test_only_the_signed_in_starter_can_supply_one(monkeypatch):
    resp, submit, _ = _start_route(monkeypatch, who=None)
    assert resp.status_code == 401 and submit.call_count == 0
    resp, submit, _ = _start_route(monkeypatch, who="re-daemon", daemon=True)
    assert resp.status_code == 403 and submit.call_count == 0
    resp, submit, _ = _start_route(monkeypatch, who=STARTER, pwd="")
    assert resp.status_code == 400 and submit.call_count == 0
    assert ROUTE_PASSWORD not in resp.text


def test_the_route_gate_itself_refuses_res_own_service_account(monkeypatch):
    """Sign-in already refuses RE's daemon account (the 403 above comes from there); the start route's own gate refuses
    it too, so a daemon identity that reached the route by another door still cannot supply a credential."""
    from fastapi import HTTPException

    from resource_explorer.web.routes import native_surveys as route
    from tests.zone_fakes import signed_in

    monkeypatch.setattr("resource_explorer.egeria_clients.is_daemon_user", lambda u: u == "re-daemon")
    monkeypatch.setattr("resource_explorer.egeria_clients._token_expiry", lambda token: None)
    from resource_explorer.a2a_auth import current_caller

    token = signed_in("re-daemon")
    try:
        body = route.RunRequest(process_qualified_name=ANALYSIS, db_user=ONE_RUN_USER, db_pwd=ROUTE_PASSWORD)
        with pytest.raises(HTTPException) as refused:
            route._run_credential(body, "re-daemon")
        assert refused.value.status_code == 403
        assert route._run_credential(body, STARTER) == (ONE_RUN_USER, ROUTE_PASSWORD)
        assert route._run_credential(route.RunRequest(process_qualified_name=ANALYSIS), STARTER) is None
        assert ROUTE_PASSWORD not in repr(body)
    finally:
        current_caller.reset(token)


def test_the_start_route_passes_it_down_as_the_signed_in_person_and_logs_no_secret(monkeypatch):
    resp, submit, db = _start_route(monkeypatch, who=STARTER)
    assert resp.status_code == 200, resp.text
    kwargs = submit.call_args.kwargs
    assert kwargs["submitted_by"] == STARTER and kwargs["run_credential"] == (ONE_RUN_USER, ROUTE_PASSWORD)
    assert ROUTE_PASSWORD not in resp.text
    everything = _all_rows_json(db)
    assert ROUTE_PASSWORD not in everything
    with db._conn() as conn:
        details = [json.loads(dict(r)["detail"] or "{}") for r in conn.execute("SELECT detail FROM activity_log").fetchall()]
    assert {"user": ONE_RUN_USER, "scope": "this run"} in [d.get("run_credential") for d in details], details


def test_a_credential_is_offered_only_for_a_coordinated_re_definition(reg, egeria):
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import (
        NativeSurveyCannotRun,
        port_session,
        submit_native_survey,
    )

    egeria.assets.add(DB_GUID)
    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        with pytest.raises(NativeSurveyCannotRun):
            submit_native_survey(reg, port, "database", "coco_pharma", "ResourceExplorerSurvey::re-db-derived",
                                 technology_type=TECH, submitted_by=STARTER,
                                 run_credential=(ONE_RUN_USER, ONE_RUN_PASSWORD))
        egeria.declared.add(ANALYSIS)
        with pytest.raises(NativeSurveyCannotRun):           # nobody signed in started it
            submit_native_survey(reg, port, "database", "coco_pharma", ANALYSIS, technology_type=TECH,
                                 submitted_by="", run_credential=(ONE_RUN_USER, ONE_RUN_PASSWORD))
    assert egeria.process_starts == []


def test_a_failed_start_holds_nothing(reg, egeria, monkeypatch):
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer import run_credential_holder as h
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import NativeSurveyError, port_session, submit_native_survey

    egeria.declared.add(ANALYSIS)
    egeria.assets.add(DB_GUID)

    def refuse(*a, **k):
        raise RuntimeError("Egeria said no")

    egeria._start_process = refuse
    put = create_autospec(h.put, side_effect=h.put)
    monkeypatch.setattr(h, "put", put)
    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port, \
            pytest.raises(NativeSurveyError):
        submit_native_survey(reg, port, "database", "coco_pharma", ANALYSIS, technology_type=TECH,
                             submitted_by=STARTER, run_credential=(ONE_RUN_USER, ONE_RUN_PASSWORD))
    put.assert_not_called()


# ── end to end ───────────────────────────────────────────────────────────────────────────────────────────────

def test_start_with_a_credential_then_claim_then_the_step_runs_with_it_and_the_run_line_says_so(
        reg, egeria, no_connection, runner):
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    instance = _start_with_credential(reg, egeria)
    assert h.take(instance) is not None
    [row] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    declared = json.loads(row["declared"])
    from resource_explorer.run_credential_holder import process_identity

    assert declared["run_credential"] == {"user": ONE_RUN_USER, "scope": "this run", "holder": process_identity()}
    assert nsr.derive_mixed_state(row)["ran_as"] is None              # asked for, not yet used: no "ran as"

    _process_action(egeria, "ea-e2e", "postgres_operations", instance=instance, next_guards=())
    assert _sweep(reg).claimed == ["ea-e2e"]
    _run_queue_once(reg)

    assert _creds_handed(runner) == [(ONE_RUN_USER, ONE_RUN_PASSWORD)]
    assert egeria.actions["ea-e2e"]["completion"]["status"] == "ACTIONED"
    [run_row] = _rows(reg)
    assert run_row["target"]["outcome"]["ran_as"] == {"user": ONE_RUN_USER, "scope": "this run"}
    [row] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    derived = nsr.derive_mixed_state(row)
    assert derived["ran_as"] == {"user": ONE_RUN_USER, "scope": "this run",
                                 "capability": "not assessed for this run's credential"}
    assert derived["run_credential"] == {"user": ONE_RUN_USER, "scope": "this run"}
    assert derived["native_steps"] == [] and derived["credential_lost"] is False
    # the row the page draws carries it (the words are the page's: RUN_CREDENTIAL_WORDS.ranAs)
    listed = next(r for r in nsr.native_survey_rows(reg, "database", "coco_pharma", TECH)
                  if r["qualified_name"] == ANALYSIS)
    assert listed["run"]["ran_as"]["user"] == ONE_RUN_USER and listed["run"]["ran_as"]["scope"] == "this run"
    everything = _all_rows_json(reg)
    assert ONE_RUN_PASSWORD not in everything and STORED_PASSWORD not in everything


# ── fix round 1 ──────────────────────────────────────────────────────────────────────────────────────────────

def _client(monkeypatch):
    """The web app as a signed-in person, for request-validation answers (nothing behind the routes is reached)."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": STARTER, "egeria_token": "tok", "role": "user"})
    from resource_explorer.web.app import app

    return TestClient(app)


@pytest.mark.parametrize("path, missing", [
    ("/api/native-surveys/database/coco_pharma/run", "process_qualified_name"),
    ("/api/survey-definitions/database/coco_pharma/run", "survey_definition_ref"),
    ("/api/databases/coco_pharma/analyses/privilege_audit/run", None),
])
@pytest.mark.parametrize("pwd", [ROUTE_PASSWORD, 12345])
def test_item1_a_422_never_echoes_the_password(monkeypatch, path, missing, pwd):
    """Item 1: the 422 keeps which field and why (`loc`, `msg`, `type`), never `input` or `ctx`. The databases
    override has no required field, so its 422 comes from a password that is not a string."""
    body = {"db_user": ONE_RUN_USER, "db_pwd": pwd}
    if missing is None:
        body["db_pwd"] = 12345 if pwd == ROUTE_PASSWORD else [pwd]
    resp = _client(monkeypatch).post(path, json=body)
    assert resp.status_code == 422, resp.text
    for form in _leak_forms(str(pwd)) + ["12345"]:
        assert form not in resp.text, (form, resp.text)
    errors = resp.json()["detail"]
    assert errors and all(set(e) <= {"loc", "msg", "type"} for e in errors), errors
    if missing:
        assert any(e["loc"][-1] == missing and e["type"] == "missing" for e in errors), errors
    else:
        assert any(e["loc"][-1] == "db_pwd" for e in errors), errors


def test_item2_the_start_route_refuses_a_one_run_password_shorter_than_eight(monkeypatch):
    from resource_explorer import native_survey_run as nsr

    resp, submit, _ = _start_route(monkeypatch, who=STARTER, pwd="Zq7#wK!")       # 7 characters
    assert resp.status_code == 400 and submit.call_count == 0
    assert resp.json()["detail"] == nsr.RUN_CREDENTIAL_TOO_SHORT == "that password is too short to keep out of logs safely"
    assert "Zq7#wK!" not in resp.text
    resp, submit, _ = _start_route(monkeypatch, who=STARTER, pwd="Zq7#wK!8")      # 8: taken
    assert resp.status_code == 200 and submit.call_count == 1


def _start_with(reg, egeria, password: str) -> str:
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason
    from resource_explorer.native_survey_run import port_session, submit_native_survey

    egeria.declared.add(ANALYSIS)
    egeria.assets.add(DB_GUID)
    with port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        state = submit_native_survey(reg, port, "database", "coco_pharma", ANALYSIS, technology_type=TECH,
                                     submitted_by=STARTER, run_credential=(ONE_RUN_USER, password))
    return state["engine_action_guid"]


@pytest.mark.parametrize("password", ["survey", "e", "2024"])
def test_item2_masking_scrubs_the_words_never_the_guards_guids_or_ran_as(reg, egeria, no_connection, runner,
                                                                          password):
    """A short password is a substring of the machine-read fields ("survey" of every guard, "e" of a GUID): only the
    free-text fields are scrubbed."""
    _with_credential(reg)
    instance = _start_with(reg, egeria, password)
    _process_action(egeria, "ea-short", "postgres_operations", instance=instance, next_guards=())
    assert _sweep(reg).claimed == ["ea-short"]
    _run_queue_once(reg)

    assert _creds_handed(runner) == [(ONE_RUN_USER, password)]
    [row] = _rows(reg)
    outcome = row["target"]["outcome"]
    [(report_guid, _)] = egeria.reports()
    assert outcome["guard"] == "survey-completed"
    assert outcome["guards"] == ["survey-completed", "Any"]
    assert outcome["report_guid"] == report_guid
    assert outcome["ran_as"] == {"user": ONE_RUN_USER, "scope": "this run"}
    done = egeria.actions["ea-short"]["completion"]
    assert done["status"] == "ACTIONED" and done["guards"] == ["survey-completed", "Any"]
    assert password not in outcome["message"]


def test_item2_free_text_fields_are_scrubbed_and_machine_fields_are_not():
    from resource_explorer import engine_host

    outcome = {"guard": "survey-failed", "guards": ["survey-failed", "Any"], "report_guid": "ee-2024",
               "ran_as": {"user": "e-user", "scope": "this run"}, "message": "survey e 2024 failed",
               "process_note": "after survey", "listing_error": "survey", "finishing_words": "survey"}
    out = engine_host._scrub_outcome(outcome, "survey")
    assert {k: out[k] for k in ("guard", "guards", "report_guid", "ran_as")} == \
        {k: outcome[k] for k in ("guard", "guards", "report_guid", "ran_as")}
    assert all("survey" not in out[k] for k in ("message", "process_note", "listing_error", "finishing_words"))


def test_item3_a_wrong_password_records_no_ran_as(reg, egeria, no_connection, runner):
    from resource_explorer import native_survey_run as nsr

    _with_credential(reg)
    instance = _start_with_credential(reg, egeria)
    runner.side_effect = RuntimeError(f'FATAL: password authentication failed for user "{ONE_RUN_USER}"')
    _process_action(egeria, "ea-wrong", "postgres_operations", instance=instance, next_guards=())
    _sweep(reg)
    _run_queue_once(reg)

    [row] = _rows(reg)
    assert row["target"]["outcome"]["guard"] == "survey-failed"
    assert "ran_as" not in row["target"]["outcome"]
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    assert "ran_as" not in json.loads(run["declared"])
    derived = nsr.derive_mixed_state(run)
    assert derived["ran_as"] is None and "ran as" not in json.dumps(derived)


def test_item3_a_step_that_raises_records_no_ran_as(reg, monkeypatch, no_connection):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    _with_credential(reg)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    monkeypatch.setattr(SurveyDefinitionExecutor, "run_synthetic_step", create_autospec(
        SurveyDefinitionExecutor.run_synthetic_step, side_effect=RuntimeError("connection refused")))
    out = engine_host._decide_one(reg, {"slug": "coco_pharma", "step_key": "postgres_operations",
                                        "request_type": "re-postgres-operations", "process_instance_guid": INSTANCE,
                                        "definition": ANALYSIS, "engine_action_guid": "ea-raise",
                                        "asset_guid": DB_GUID})
    assert out["guard"] == "survey-failed" and "ran_as" not in out


def test_item4_a_held_credential_step_is_never_stamped_with_the_stored_logins_capability(reg, egeria, no_connection,
                                                                                         runner):
    """The stored login cannot read stats (its probe says so); the step ran as the one-run user, so the stored probe
    says nothing about it: not assessed, never the stored login's shortfall."""
    from resource_explorer import native_survey_run as nsr

    _with_credential(reg, stats_role=False)
    instance = _start_with_credential(reg, egeria)
    _process_action(egeria, "ea-cap1", "postgres_operations", instance=instance, next_guards=())
    _sweep(reg)
    _run_queue_once(reg)

    assert _creds_handed(runner) == [(ONE_RUN_USER, ONE_RUN_PASSWORD)]   # it ran, as the one-run user
    [row] = _rows(reg)
    outcome = row["target"]["outcome"]
    assert outcome["guard"] == "survey-completed", outcome
    assert outcome["capability"] == "not assessed for this run's credential"
    [run] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    assert nsr.derive_mixed_state(run)["ran_as"]["capability"] == "not assessed for this run's credential"
    everything = _all_rows_json(reg)
    assert "connected as egeria_user" not in everything and "needs `stats`" not in everything


def test_item4_the_stored_login_keeps_its_own_capability_gate(reg, egeria, no_connection, runner):
    """Pins existing behaviour: without a one-run credential the stored login's shortfall still refuses the step."""
    _with_credential(reg, stats_role=False)
    _marker_row(reg, INSTANCE, marker=False)
    _process_action(egeria, "ea-cap2", "postgres_operations", instance=INSTANCE, next_guards=())
    _sweep(reg)
    _run_queue_once(reg)
    runner.assert_not_called()
    assert "connected as egeria_user" in egeria.actions["ea-cap2"]["completion"]["message"]


def _refusal(reg, instance: str = INSTANCE) -> str:
    from resource_explorer import engine_host

    held, refusal = engine_host._one_run_credential(reg, {"request_type": "re-postgres-operations",
                                                         "process_instance_guid": instance, "slug": "coco_pharma",
                                                         "definition": ANALYSIS})
    assert held is None
    return refusal


def test_item5_lost_words_timed_out_when_this_process_dropped_it_at_its_ttl(reg, _empty_holder):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _marker_row(reg, INSTANCE, holder=h.process_identity())
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    _empty_holder["now"] += 7200
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_TIMED_OUT
    assert h.take(INSTANCE) is None and h.expired(INSTANCE)


def test_item5_lost_words_neutral_when_this_process_cleared_it_without_a_ttl(reg):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _marker_row(reg, INSTANCE, holder=h.process_identity())
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_NEUTRAL


def test_item5_lost_words_restarted_when_the_same_pid_started_at_another_time(reg):
    from resource_explorer import engine_host

    _marker_row(reg, INSTANCE, holder={"pid": os.getpid(), "started": "2026-10-18T01:02:03.000004+00:00"})
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_RESTARTED


def test_item5_lost_words_another_process_when_the_holder_is_alive(reg):
    from resource_explorer import engine_host

    _marker_row(reg, INSTANCE, holder={"pid": os.getppid(), "started": "2026-10-18T01:02:03.000004+00:00"})
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS


def test_item5_lost_words_neutral_when_the_holder_is_gone_or_unnamed(reg):
    from resource_explorer import engine_host

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    _marker_row(reg, INSTANCE, holder={"pid": gone.pid, "started": "2026-10-18T01:02:03.000004+00:00"})
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_NEUTRAL
    _marker_row(reg, INSTANCE_2)                                    # a marker from before fix round 1
    assert _refusal(reg, INSTANCE_2) == engine_host.RUN_CREDENTIAL_LOST_NEUTRAL


def test_item5_the_start_records_which_process_holds_it_and_the_run_line_carries_the_proven_words(reg, egeria):
    from resource_explorer import engine_host
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    instance = _start_with_credential(reg, egeria)
    [row] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    assert json.loads(row["declared"])["run_credential"]["holder"] == h.process_identity()
    words = f"Resource Explorer did not run re-postgres-operations on coco_pharma: {engine_host.RUN_CREDENTIAL_LOST_RESTARTED}"
    row = {**row, "declared": json.dumps({**json.loads(row["declared"]), "process_run": {
        "words": "step 1 refused", "steps": [{"request_type": "re-postgres-operations", "status": "INVALID",
                                              "message": words}]}})}
    derived = nsr.derive_mixed_state(row)
    assert derived["credential_lost"] is True
    assert derived["credential_lost_words"] == engine_host.RUN_CREDENTIAL_LOST_RESTARTED
    assert instance


def test_item6_a_claim_between_initiate_and_put_is_refused_never_given_the_stored_login(reg, egeria, no_connection,
                                                                                         runner):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    real_start = egeria._start_process
    seen: dict = {}

    def start_then_claim(*a, **k):
        guid = real_start(*a, **k)
        # Egeria has the run and dispatches its first step before the start request has held the credential.
        seen["held_yet"] = h.take(guid)
        seen["outcome"] = engine_host._decide_one(reg, {
            "slug": "coco_pharma", "step_key": "postgres_operations", "request_type": "re-postgres-operations",
            "process_instance_guid": guid, "definition": ANALYSIS, "engine_action_guid": "ea-race",
            "asset_guid": DB_GUID})
        return guid

    egeria._start_process = start_then_claim
    instance = _start_with_credential(reg, egeria)
    assert seen["held_yet"] is None
    runner.assert_not_called()                                   # never the stored login
    assert seen["outcome"]["guard"] == "survey-invalid"
    assert engine_host.RUN_CREDENTIAL_START_PENDING_SENTENCE in seen["outcome"]["message"]
    assert not h.start_pending("coco_pharma", ANALYSIS)          # the mark is left once put + marker are written
    assert h.take(instance) is not None


def test_item6_the_mark_is_left_when_the_start_fails(reg, egeria):
    from resource_explorer import run_credential_holder as h
    from resource_explorer.native_survey_run import NativeSurveyError

    def refuse(*a, **k):
        assert h.start_pending("coco_pharma", ANALYSIS)
        raise RuntimeError("Egeria said no")

    egeria._start_process = refuse
    with pytest.raises(NativeSurveyError):
        _start_with_credential(reg, egeria)
    assert not h.start_pending("coco_pharma", ANALYSIS)


def test_item7_a_marker_write_that_fails_twice_says_started_and_keeps_the_credential(reg, egeria, monkeypatch):
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    monkeypatch.setattr(nsr, "MARKER_RETRY_PAUSE_SECONDS", 0)

    write = create_autospec(reg.record_native_survey_submission_once, side_effect=OSError("registry gone"))
    monkeypatch.setattr(reg, "record_native_survey_submission_once", write)
    from resource_explorer import catalog_and_survey as cas
    from resource_explorer.egeria_clients import Daemon, DaemonReason

    egeria.declared.add(ANALYSIS)
    egeria.assets.add(DB_GUID)
    with nsr.port_session(cas.PyegeriaRegistrationPort(identity=Daemon(DaemonReason.SCHEDULER))) as port:
        state = nsr.submit_native_survey(reg, port, "database", "coco_pharma", ANALYSIS, technology_type=TECH,
                                         submitted_by=STARTER, run_credential=(ONE_RUN_USER, ONE_RUN_PASSWORD))
    assert write.call_count == 2                                 # tried once more
    assert state["engine_action_guid"] == "pi-1"
    assert state["run_credential_marker_error"].startswith("started; one-run credential marker not recorded")
    assert h.take("pi-1") is not None                            # the holder entry is kept
    assert ONE_RUN_PASSWORD not in json.dumps(state)


def test_item7_a_marker_write_that_fails_once_is_retried_and_recorded(reg, egeria, monkeypatch):
    monkeypatch.setattr(_nsr, "MARKER_RETRY_PAUSE_SECONDS", 0)
    real = reg.record_native_survey_submission_once
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("registry blinked")
        return real(*a, **k)

    monkeypatch.setattr(reg, "record_native_survey_submission_once", create_autospec(real, side_effect=flaky))
    _start_with_credential(reg, egeria)
    [row] = reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
    assert json.loads(row["declared"])["run_credential"]["user"] == ONE_RUN_USER


def test_item8_a_definition_step_with_no_instance_guid_is_refused_never_the_stored_login(reg, no_connection, runner):
    from resource_explorer import engine_host

    _with_credential(reg)
    target = {"slug": "coco_pharma", "step_key": "postgres_operations", "request_type": "re-postgres-operations",
              "process_instance_guid": "", "definition": ANALYSIS, "engine_action_guid": "ea-noinst",
              "asset_guid": DB_GUID}
    assert engine_host._one_run_credential(reg, target) == (None, engine_host.RUN_CREDENTIAL_NO_INSTANCE_SENTENCE)
    out = engine_host._decide_one(reg, target)
    runner.assert_not_called()
    assert out["guard"] == "survey-invalid" and "cannot tell which run this step belongs to" in out["message"]
    # A standalone action (no definition) is not a step of a run: the stored login, as before (pins).
    assert engine_host._one_run_credential(reg, {**target, "definition": ""}) == (None, "")


def test_item9_a_refused_first_step_of_a_run_with_no_plan_does_not_clear_it_for_step_2(reg):
    from resource_explorer import native_survey_run as nsr
    from resource_explorer import run_credential_holder as h

    retired = "GovActionProcess::RetiredSurvey"                 # declares no process, stores no step list
    assert reg.record_native_survey_submission_once(
        "database", "coco_pharma", retired, "2026-10-18T09:00:00", engine_action_guid=INSTANCE, submitted_by=STARTER,
        started_as="process", started_by="egeria", started_in="re",
        declared_extra={"run_credential": {"user": ONE_RUN_USER, "scope": "this run"}})
    [run] = reg.list_native_survey_runs("database", "coco_pharma", retired)
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    views = _views(1)
    views[0].status = "INVALID"
    views[0].completion_guards = ["survey-invalid", "Any"]
    out = nsr.refresh_mixed_run(reg, _port(views), run, entity_type="database", slug="coco_pharma")
    assert out["state"] in ("failed", "complete", "partial"), out["state"]   # the line reads as over
    assert h.take(INSTANCE) is not None                                     # and the credential is kept for step 2


# ── fix round 2 ──────────────────────────────────────────────────────────────────────────────────────────────

def _target(instance: str) -> dict:
    return {"slug": "coco_pharma", "step_key": "postgres_operations", "request_type": "re-postgres-operations",
            "process_instance_guid": instance, "definition": ANALYSIS, "engine_action_guid": f"ea-{instance[:4]}",
            "asset_guid": DB_GUID}


def _restart(monkeypatch):
    """A simulated restart: an empty holder, no start in flight in memory, and this process a new one in the same pid."""
    from resource_explorer import run_credential_holder as h

    monkeypatch.setattr(h, "_HOLDER", h._Holder())
    monkeypatch.setattr(h, "_PROCESS", {"pid": os.getpid(), "started": "2026-10-18T23:59:59.000001+00:00"})


def _entry(reg, *, age: float, holder: dict, instance: str = "") -> None:
    """A start entry written by hand (another process, an earlier one, or an old one)."""
    from datetime import datetime, timedelta, timezone

    from resource_explorer import run_credential_start as rs

    at = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age)).isoformat()
    reg.set_setting(rs.start_key("coco_pharma", ANALYSIS),
                    json.dumps([{"nonce": "n-1", "at": at, "holder": holder, "instance": instance}]))


def test_f2_a_start_entry_that_cannot_be_written_is_not_started_and_egeria_is_never_asked(reg, egeria, monkeypatch):
    from resource_explorer import run_credential_holder as h

    started = create_autospec(egeria._start_process, side_effect=egeria._start_process)
    egeria._start_process = started
    monkeypatch.setattr(reg, "set_setting", create_autospec(reg.set_setting, side_effect=OSError("registry gone")))
    with pytest.raises(_nsr.NativeSurveyNotStarted) as refused:
        _start_with_credential(reg, egeria)
    assert str(refused.value).startswith(_nsr.RUN_CREDENTIAL_START_NOT_RECORDED)
    started.assert_not_called()                                   # Egeria was never asked
    assert reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS) == []
    assert not h.start_pending("coco_pharma", ANALYSIS)
    # and the route says 503 "not started"
    resp, submit, _ = _start_route(monkeypatch, who=STARTER, submit_side_effect=refused.value)
    assert resp.status_code == 503, resp.text
    assert _nsr.RUN_CREDENTIAL_START_NOT_RECORDED in resp.json()["detail"]
    assert ROUTE_PASSWORD not in resp.text


def test_f2_the_start_entry_is_written_before_egeria_is_asked_and_removed_once_the_marker_is(reg, egeria):
    from resource_explorer import run_credential_start as rs

    real_start = egeria._start_process
    seen: dict = {}

    def start(*a, **k):
        seen["entries"] = rs.live_starts(reg, "coco_pharma", ANALYSIS)
        return real_start(*a, **k)

    egeria._start_process = start
    _start_with_credential(reg, egeria)
    [entry] = seen["entries"]
    assert entry["instance"] == "" and entry["holder"] == __import__(
        "resource_explorer.run_credential_holder", fromlist=["x"]).process_identity()
    assert set(entry) == {"nonce", "at", "holder", "instance", "age"}            # nothing secret
    assert rs.live_starts(reg, "coco_pharma", ANALYSIS) == []
    assert ONE_RUN_PASSWORD not in _all_rows_json(reg)


def test_f2_a_marker_write_that_fails_then_a_restart_refuses_the_step_never_the_stored_login(
        reg, egeria, no_connection, runner, monkeypatch):
    """Fails on 6765f615: the run had no marker, so after the restart its step was given the stored login."""
    from resource_explorer import engine_host

    monkeypatch.setattr(_nsr, "MARKER_RETRY_PAUSE_SECONDS", 0)
    _with_credential(reg)
    real_write = reg.record_native_survey_submission_once
    monkeypatch.setattr(reg, "record_native_survey_submission_once",
                        create_autospec(real_write, side_effect=OSError("registry gone")))
    instance = _start_with_credential(reg, egeria)
    monkeypatch.setattr(reg, "record_native_survey_submission_once", real_write)      # the registry is back
    _restart(monkeypatch)

    _process_action(egeria, "ea-after", "postgres_operations", instance=instance, next_guards=())
    assert _sweep(reg).claimed == ["ea-after"]
    _run_queue_once(reg)
    runner.assert_not_called()
    done = egeria.actions["ea-after"]["completion"]
    assert done["status"] == "INVALID"
    assert engine_host.RUN_CREDENTIAL_LOST_RESTARTED in done["message"]
    # the entry's word is now the run row's marker: later steps are refused by the row itself
    [row] = [r for r in reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS)
             if r["engine_action_guid"] == instance]
    assert json.loads(row["declared"])["run_credential"]["from"] == "start entry"
    assert STORED_PASSWORD not in _all_rows_json(reg) and ONE_RUN_PASSWORD not in _all_rows_json(reg)


def test_f2_a_start_that_died_before_naming_its_instance_refuses_a_new_instance_after_a_restart(
        reg, no_connection, runner, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    _entry(reg, age=30, holder=h.process_identity())               # written, then the process went
    _restart(monkeypatch)
    out = engine_host._decide_one(reg, _target(INSTANCE))
    runner.assert_not_called()
    assert out["guard"] == "survey-invalid" and engine_host.RUN_CREDENTIAL_LOST_RESTARTED in out["message"]


def test_f2_a_stale_entry_beyond_the_ttl_no_longer_blocks(reg, no_connection, runner):
    """The stale half pins behaviour that already worked (no entry: the stored login); the live half is new."""
    from resource_explorer import engine_host

    _with_credential(reg)
    other = {"pid": os.getppid(), "started": "2026-10-18T01:02:03.000004+00:00"}
    _entry(reg, age=7201, holder=other)
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (None, "")
    engine_host._decide_one(reg, _target(INSTANCE))
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]
    _entry(reg, age=7100, holder=other)                            # inside the TTL: refused, never the stored login
    held, refusal = engine_host._one_run_credential(reg, _target(INSTANCE_2))
    assert held is None and refusal == engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS


def test_f2_an_unreadable_start_entry_is_refused_never_guessed(reg, monkeypatch):
    from resource_explorer import engine_host

    monkeypatch.setattr(reg, "get_setting", create_autospec(reg.get_setting, side_effect=OSError("registry gone")))
    held, refusal = engine_host._one_run_credential(reg, _target(INSTANCE))
    assert held is None and refusal.startswith(engine_host.RUN_CREDENTIAL_UNKNOWN_SENTENCE)


def test_f1_an_older_runs_step_is_claimed_and_runs_while_a_start_is_pending(reg, egeria, no_connection, runner):
    """Fails on 6765f615: the start-pending mark refused every re-* step of the definition, older runs' too."""
    from resource_explorer import run_credential_holder as h
    from resource_explorer import run_credential_start as rs

    _with_credential(reg)
    _marker_row(reg, INSTANCE, marker=False)                       # an older run, listed before the start began
    with h.starting("coco_pharma", ANALYSIS):
        rs.write_start(reg, "coco_pharma", ANALYSIS)
        _process_action(egeria, "ea-old", "postgres_operations", instance=INSTANCE, next_guards=())
        assert _sweep(reg).claimed == ["ea-old"]
        _run_queue_once(reg)
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]
    assert egeria.actions["ea-old"]["completion"]["status"] == "ACTIONED"


def test_f1_the_new_instances_step_waits_unclaimed_and_silently_then_is_claimed_once_the_start_finishes(
        reg, egeria, no_connection, runner, caplog):
    """Fails on 6765f615: the step was claimed and completed survey-invalid with "run this step again"."""
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h
    from resource_explorer import run_credential_start as rs

    _with_credential(reg)
    with h.starting("coco_pharma", ANALYSIS):
        nonce = rs.write_start(reg, "coco_pharma", ANALYSIS)
        _process_action(egeria, "ea-new", "postgres_operations", instance=INSTANCE_2, next_guards=())
        with caplog.at_level(logging.INFO, logger="resource_explorer.engine_host"):
            swept = _sweep(reg)
        assert swept.claimed == [] and swept.skipped.get(engine_host.START_PENDING) == ["ea-new"]
        assert "ea-new" not in caplog.text                         # silent, like not_due
        assert engine_host.START_PENDING in engine_host._SILENT
        assert egeria.actions["ea-new"].get("completion") is None   # still APPROVED, unclaimed
        # the start records its credential and marker, then removes its entry
        h.put(INSTANCE_2, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
        _marker_row(reg, INSTANCE_2, holder=h.process_identity())
        rs.remove_start(reg, "coco_pharma", ANALYSIS, nonce)
    assert _sweep(reg).claimed == ["ea-new"]
    _run_queue_once(reg)
    assert _creds_handed(runner) == [(ONE_RUN_USER, ONE_RUN_PASSWORD)]


def test_f1_the_persisted_entry_alone_holds_the_step_back_and_an_overdue_one_lets_it_be_refused(reg, egeria):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _process_action(egeria, "ea-p", "postgres_operations", instance=INSTANCE_2, next_guards=())
    _entry(reg, age=5, holder=h.process_identity())               # no in-memory mark: the entry decides
    assert _sweep(reg).skipped.get(engine_host.START_PENDING) == ["ea-p"]
    _entry(reg, age=600, holder=h.process_identity())             # overdue: claimed, and refused at run time
    assert _sweep(reg).claimed == ["ea-p"]


def test_f1_a_backstop_refusal_says_the_run_has_to_be_started_again():
    from resource_explorer import engine_host

    words = engine_host.RUN_CREDENTIAL_START_PENDING_SENTENCE
    assert "start the run again" in words and "in a moment" not in words


def test_f1_the_in_memory_mark_is_left_before_the_read_back(reg, egeria, monkeypatch):
    from resource_explorer import run_credential_holder as h

    real = _nsr.refresh_run
    seen: dict = {}

    def read_back(*a, **k):
        seen["pending"] = h.start_pending("coco_pharma", ANALYSIS)
        return real(*a, **k)

    monkeypatch.setattr(_nsr, "refresh_run", create_autospec(real, side_effect=read_back))
    _start_with_credential(reg, egeria)
    assert seen == {"pending": False}


def test_f3_rows_that_cannot_be_read_after_the_start_still_say_started(monkeypatch):
    def broken(*a, **k):
        raise OSError("registry gone")

    resp, submit, _ = _start_route(monkeypatch, who=STARTER, rows=broken)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["engine_action_guid"] == "pi-9" and body["run"]["state"] == "submitted"
    assert body["surveys"] is None
    assert ROUTE_PASSWORD not in resp.text


def test_f4_a_credential_this_process_cleared_says_so(reg):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _marker_row(reg, INSTANCE, holder=h.process_identity())
    h.put(INSTANCE, ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
    h.clear(INSTANCE)
    assert h.cleared(INSTANCE) and not h.expired(INSTANCE)
    assert _refusal(reg) == engine_host.RUN_CREDENTIAL_LOST_CLEARED
    assert "cleared when the run read as over or was cancelled" in engine_host.RUN_CREDENTIAL_LOST_CLEARED
    # one bounded memory for both causes
    for i in range(h._EXPIRED_MEMORY + 5):
        h.put(f"g-{i}", ONE_RUN_USER, ONE_RUN_PASSWORD, STARTER)
        h.clear(f"g-{i}")
    assert len(h._HOLDER._gone) == h._EXPIRED_MEMORY and not h.cleared(INSTANCE)
    # a clear of something never held records nothing
    h.clear("never-held")
    assert not h.cleared("never-held")
    # the run line carries the cleared words
    row = {"engine_action_guid": INSTANCE, "executor_ref": ANALYSIS, "declared": json.dumps({
        "run_credential": {"user": ONE_RUN_USER, "scope": "this run"},
        "process_run": {"words": "x", "steps": [{"request_type": "re-postgres-operations", "status": "INVALID",
                                                  "message": f"did not run: {engine_host.RUN_CREDENTIAL_LOST_CLEARED}"}]}})}
    assert _nsr.derive_mixed_state(row)["credential_lost_words"] == engine_host.RUN_CREDENTIAL_LOST_CLEARED


def test_f5_the_process_identity_is_per_pid_and_computed_lazily(monkeypatch):
    from resource_explorer import run_credential_holder as h

    first = h.process_identity()
    assert first["pid"] == os.getpid() and h.process_identity() == first          # cached
    monkeypatch.setattr(os, "getpid", lambda: first["pid"] + 1000)              # a forked worker
    forked = h.process_identity()
    assert forked["pid"] == first["pid"] + 1000
    assert h.process_identity() == forked


def test_f7_the_marker_retry_pauses(reg, egeria, monkeypatch):
    import time as _time

    real = reg.record_native_survey_submission_once
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("registry blinked")
        return real(*a, **k)

    monkeypatch.setattr(reg, "record_native_survey_submission_once", create_autospec(real, side_effect=flaky))
    sleep = create_autospec(_time.sleep)
    monkeypatch.setattr(_nsr.time, "sleep", sleep)
    _start_with_credential(reg, egeria)
    sleep.assert_called_once_with(0.5)


class _QuotingBody(__import__("pydantic").BaseModel):
    """A route model whose validators quote the value they refuse in their own messages (module level: this file's
    postponed annotations must resolve for FastAPI to read the route's body)."""

    db_user: str
    db_pwd: str

    @__import__("pydantic").field_validator("db_pwd")
    @classmethod
    def _quote_pwd(cls, v):
        if v.startswith("Zq"):
            raise ValueError(f"password {v} is not allowed")
        return v

    @__import__("pydantic").model_validator(mode="after")
    def _quote_whole(self):
        if self.db_user == "quote-it":
            raise ValueError(f"{self.db_user} cannot use {self.db_pwd}")
        return self


def _quoting_route(body: _QuotingBody) -> dict:
    return {}


def test_a_422_never_echoes_a_password_a_validator_quoted_in_its_message():
    """`msg` is withheld for a password-like field and wherever it contains the body's password, so the 422 never
    carries it, whatever a route model's validator says."""
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.testclient import TestClient

    from resource_explorer.web.app import _validation_refused

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, _validation_refused)
    app.post("/x")(_quoting_route)
    client = TestClient(app)
    for body, field in (({"db_user": ONE_RUN_USER, "db_pwd": ROUTE_PASSWORD}, "db_pwd"),
                        ({"db_user": "quote-it", "db_pwd": "plain-secret-9"}, None)):
        resp = client.post("/x", json=body)
        assert resp.status_code == 422, resp.text
        errors = resp.json()["detail"]
        # the validator ran on the body (not a missing parameter), and still nothing echoes the password
        assert all(e["loc"][0] == "body" and e["type"] == "value_error" for e in errors), errors
        if field:
            assert any(e["loc"][-1] == field for e in errors), errors
        for form in _leak_forms(body["db_pwd"]):
            assert form not in resp.text, (form, resp.text)
        assert all(set(e) <= {"loc", "msg", "type"} for e in errors)


# ── verification round 3, M1: only a PROVEN no-start removes the start entry ─────────────────────────────────

class _Answered(Exception):
    """A failure carrying an HTTP status the way pyegeria's exceptions do (`related_http_code`)."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Egeria answered {code}")
        self.related_http_code = code


def _initiate_fails_with(reg, egeria, exc):
    from resource_explorer import run_credential_start as rs
    from resource_explorer.native_survey_run import NativeSurveyError

    def fail(*a, **k):
        raise exc

    egeria._start_process = fail
    with pytest.raises(NativeSurveyError):
        _start_with_credential(reg, egeria)
    return rs.live_starts(reg, "coco_pharma", ANALYSIS)


@pytest.mark.parametrize("exc", [
    RuntimeError("connection reset after the request was sent"),
    _Answered(502),                              # a proxy's 5xx after Egeria may have created the process
    _Answered(500),
], ids=["unknown-error", "proxy-502", "server-500"])
def test_m1_an_initiate_failure_whose_answer_is_unknown_keeps_the_start_entry(reg, egeria, exc):
    # Egeria may have started the run: the entry stays so its re-* steps are refused, never the stored login (Q2).
    assert _initiate_fails_with(reg, egeria, exc), "the start entry was removed on an unknown answer"


def test_m1_a_remote_protocol_error_after_send_keeps_the_start_entry(reg, egeria):
    import httpx

    assert _initiate_fails_with(reg, egeria, httpx.RemoteProtocolError("peer closed connection"))


@pytest.mark.parametrize("make", [
    lambda: _Answered(403),                      # Egeria refused: nothing was started
    lambda: _Answered(400),
    lambda: __import__("httpx").ConnectError("connection refused"),   # the request never reached Egeria
    lambda: __import__("httpx").ConnectTimeout("connect timed out"),
], ids=["refused-403", "refused-400", "connect-error", "connect-timeout"])
def test_m1_a_proven_no_start_removes_the_start_entry(reg, egeria, make):
    assert _initiate_fails_with(reg, egeria, make()) == []


def test_m1_egeria_answering_action_not_initiated_removes_the_start_entry(reg, egeria):
    from resource_explorer import run_credential_start as rs
    from resource_explorer.native_survey_run import NativeSurveyError, NativeSurveyNotInitiated

    egeria._start_process = lambda *a, **k: "Action not initiated"
    with pytest.raises(NativeSurveyError) as failed:      # the submit wraps every initiate failure
        _start_with_credential(reg, egeria)
    assert isinstance(failed.value.__cause__, NativeSurveyNotInitiated)
    assert rs.live_starts(reg, "coco_pharma", ANALYSIS) == []


# ── the start window: only an instance Egeria created during the start's initiate call is held ──────────────────
# Owner, 2026-10-18: "That seems more useable". The fake answers the process instance read with the LIVE shape
# (tests/fixtures/egeria_6_2/process_instance__create_and_survey__by_guid.json, the same route and identity, whose top-level
# keys the 2026-10-18 probe of the 07:39 cancelled Scouting run's instance matched): `versions.createTime` at the top
# level, no `elementHeader`.

_INSTANCE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "egeria_6_2" / \
    "process_instance__create_and_survey__by_guid.json"


def _instance_answer(created: str | None, guid: str = INSTANCE) -> dict:
    """The live answer of `get_metadata_element_by_guid` for a process instance, created at `created` (None: the
    field is missing)."""
    import copy

    answer = copy.deepcopy(json.loads(_INSTANCE_FIXTURE.read_text())["answer"])
    answer["elementGUID"] = guid
    if created is None:
        answer["versions"].pop("createTime")
    else:
        answer["versions"]["createTime"] = created
    return answer


def _entry_at(reg):
    from resource_explorer import run_credential_start as rs

    [entry] = json.loads(reg.get_setting(rs.start_key("coco_pharma", ANALYSIS)))
    return rs.parse_when(entry["at"])


def _created(reg, seconds: float) -> str:
    """Egeria's spelling of a time `seconds` after the start entry's `at`."""
    from datetime import timedelta

    return (_entry_at(reg) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds") + "+00:00"


def _egeria_instances(egeria, answers: dict) -> None:
    """The fake Egeria answers a by-GUID read of each instance in `answers` with that element (set before any client is
    built, so the autospec is wired to it; a later call adds to the same answers); everything else as before."""
    known = getattr(egeria, "instance_answers", None)
    if known is not None:
        known.update(answers)
        return
    egeria.instance_answers = known = dict(answers)
    plain = egeria._element

    def element(guid, *a, **k):
        return known[guid] if guid in known else plain(guid, *a, **k)

    egeria._element = element


def _instance_reads(egeria, guid: str = INSTANCE) -> int:
    return sum(1 for c in egeria.calls if c[:2] == ("MetadataExpert", "get_metadata_element_by_guid"))


_OTHER = {"pid": os.getppid(), "started": "2026-10-18T01:02:03.000004+00:00"}


def test_window_the_live_shape_is_read_from_versions_create_time():
    """Pins the JSON path the probe found (the recorded live answer of the same route)."""
    from datetime import datetime

    from resource_explorer import engine_host

    answer = json.loads(_INSTANCE_FIXTURE.read_text())["answer"]
    assert "elementHeader" not in answer
    assert engine_host._created_of(answer) == datetime(2026, 10, 13, 1, 10, 50, 874000)
    assert engine_host._created_of({"versions": {}}) is None
    assert engine_host._created_of({"versions": {"createTime": "not a time"}}) is None
    assert engine_host._created_of("No elements found") is None


def test_window_1_an_instance_created_ten_minutes_after_the_start_is_an_ordinary_run(
        reg, egeria, no_connection, runner):
    """Fails on 67e8d6ef: any instance with no row before the entry was refused for the whole TTL, and stamped."""
    from resource_explorer import engine_host

    _with_credential(reg)
    _entry(reg, age=1200, holder=_OTHER)
    _egeria_instances(egeria, {INSTANCE: _instance_answer(_created(reg, 600))})
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (None, "")
    out = engine_host._decide_one(reg, _target(INSTANCE))
    assert out["guard"] != "survey-invalid", out
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]      # the stored login: no marker, no stamp
    assert all("run_credential" not in json.loads(r.get("declared") or "{}")
               for r in reg.list_native_survey_runs("database", "coco_pharma", ANALYSIS))
    assert '"from": "start entry"' not in _all_rows_json(reg)


def test_window_2_an_instance_created_five_seconds_after_the_start_is_still_held(reg, egeria, no_connection, runner):
    """Pins behaviour that already worked (held as today), now through the creation time."""
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    _entry(reg, age=300, holder=_OTHER)                                       # another process: refused
    _egeria_instances(egeria, {INSTANCE: _instance_answer(_created(reg, 5)),
                               INSTANCE_2: _instance_answer(_created(reg, 5), INSTANCE_2)})
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (
        None, engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS)
    runner.assert_not_called()
    _entry(reg, age=10, holder=h.process_identity())                          # this process, young: deferred
    _egeria_instances(egeria, {INSTANCE_2: _instance_answer(_created(reg, 5), INSTANCE_2)})
    assert engine_host._one_run_credential(reg, _target(INSTANCE_2)) == (
        None, engine_host.RUN_CREDENTIAL_START_PENDING_SENTENCE)


def test_window_3_an_instance_created_30s_before_the_entry_is_held_within_the_skew(reg, egeria):
    """Pins behaviour that already worked: 30 s of clock skew is inside the 60 s margin."""
    from resource_explorer import engine_host

    _entry(reg, age=300, holder=_OTHER)
    _egeria_instances(egeria, {INSTANCE: _instance_answer(_created(reg, -30))})
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (
        None, engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS)


def test_window_the_edges_follow_the_initiate_window_and_the_skew(reg, egeria):
    """90 s initiate window (three of pyegeria's 30 s timeouts) + 60 s skew: 149 s after is held, 151 s is not; 61 s
    before is not."""
    from resource_explorer import engine_host
    from resource_explorer import run_credential_start as rs

    assert rs.initiate_window_seconds() == 90.0 and rs.clock_skew_seconds() == 60.0
    _entry(reg, age=3000, holder=_OTHER)
    guids = {"a" * 8 + INSTANCE[8:]: 149, "b" * 8 + INSTANCE[8:]: 151, "c" * 8 + INSTANCE[8:]: -61}
    _egeria_instances(egeria, {g: _instance_answer(_created(reg, s), g) for g, s in guids.items()})
    held = {s: engine_host._one_run_credential(reg, _target(g))[1] != "" for g, s in guids.items()}
    assert held == {149: True, 151: False, -61: False}


def test_window_the_env_overrides_widen_it(reg, egeria, monkeypatch):
    from resource_explorer import engine_host
    from resource_explorer import run_credential_start as rs

    monkeypatch.setenv(rs.INITIATE_WINDOW_ENV, "600")
    monkeypatch.setenv(rs.CLOCK_SKEW_ENV, "0")
    _entry(reg, age=3000, holder=_OTHER)
    _egeria_instances(egeria, {INSTANCE: _instance_answer(_created(reg, 590))})
    assert engine_host._one_run_credential(reg, _target(INSTANCE))[1] == engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS
    monkeypatch.setenv(rs.INITIATE_WINDOW_ENV, "garbage")
    assert rs.initiate_window_seconds() == 90.0


@pytest.mark.parametrize("how", ["raises", "missing", "unparsable", "no element"])
def test_window_4_an_unreadable_creation_time_holds_as_today(reg, egeria, no_connection, runner, how):
    """Pins behaviour that already worked: fail closed, never the stored login."""
    from resource_explorer import engine_host

    _with_credential(reg)
    _entry(reg, age=1200, holder=_OTHER)
    if how == "raises":
        egeria.fail["get_metadata_element_by_guid"] = RuntimeError("Egeria unreachable")
    elif how == "missing":
        _egeria_instances(egeria, {INSTANCE: _instance_answer(None)})
    elif how == "unparsable":
        _egeria_instances(egeria, {INSTANCE: _instance_answer("yesterday-ish")})
    # "no element": the fake answers "No elements found" for an instance it does not know
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (
        None, engine_host.RUN_CREDENTIAL_LOST_OTHER_PROCESS)
    runner.assert_not_called()
    assert _instance_reads(egeria) == 1


def test_window_5_two_steps_of_one_instance_read_it_once(reg, egeria, no_connection, runner):
    """Fails on 67e8d6ef (no read at all). The read is the engine host daemon's."""
    from resource_explorer import engine_host

    _with_credential(reg)
    _entry(reg, age=1200, holder=_OTHER)
    _egeria_instances(egeria, {INSTANCE: _instance_answer(_created(reg, 600))})
    first = dict(_target(INSTANCE), engine_action_guid="ea-step1")
    second = dict(_target(INSTANCE), engine_action_guid="ea-step2", step_key="postgres_operations")
    assert engine_host._one_run_credential(reg, first) == (None, "")
    assert engine_host._one_run_credential(reg, second) == (None, "")
    assert engine_host._held_for_start(reg, "coco_pharma", ANALYSIS, INSTANCE) is False
    assert _instance_reads(egeria) == 1
    from tests.test_engine_host_e4 import _daemon_user

    assert egeria.built == [("MetadataExpert", _daemon_user())]                 # read as RE's service account


@pytest.mark.parametrize("case,created,held", [
    ("1: ten minutes after", 600, False), ("2: five seconds after", 5, True), ("3: 30 s before", -30, True),
    ("4: unreadable", None, True)])
def test_window_6_the_sweep_and_the_run_agree(reg, egeria, case, created, held):
    """Case 1 fails on 67e8d6ef; 2-4 pin. One predicate (`_start_hold`): the sweep's silent skip and the run's
    refusal say the same."""
    from resource_explorer import engine_host
    from resource_explorer import run_credential_holder as h

    _entry(reg, age=10, holder=h.process_identity())                  # this process, young: the sweep would defer
    _egeria_instances(egeria, {INSTANCE: _instance_answer(None if created is None else _created(reg, created))})
    assert engine_host._held_for_start(reg, "coco_pharma", ANALYSIS, INSTANCE) is held
    assert engine_host._one_run_credential(reg, _target(INSTANCE)) == (
        None, engine_host.RUN_CREDENTIAL_START_PENDING_SENTENCE if held else "")


def test_window_the_sweep_claims_an_ordinary_run_started_after_a_failed_start(reg, egeria, no_connection, runner):
    """Fails on 67e8d6ef: end to end through the sweep and the run queue, an ordinary run in the 2 h after a failed
    start was claimed, refused, stamped and lost."""
    from resource_explorer import run_credential_holder as h

    _with_credential(reg)
    _entry(reg, age=20, holder=h.process_identity())
    _egeria_instances(egeria, {INSTANCE_2: _instance_answer(_created(reg, 900), INSTANCE_2)})
    _process_action(egeria, "ea-ord", "postgres_operations", instance=INSTANCE_2, next_guards=())
    assert _sweep(reg).claimed == ["ea-ord"]
    _run_queue_once(reg)
    assert _creds_handed(runner) == [("egeria_user", STORED_PASSWORD)]
    assert egeria.actions["ea-ord"]["completion"]["status"] == "ACTIONED"
