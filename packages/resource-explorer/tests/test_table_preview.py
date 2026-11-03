"""Table preview (REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19): the typed query, masking, the session-only credential,
the three refusals, the audit row, and one end-to-end pass through the route with a fake connection that binds the
real signatures. No live database, no Egeria."""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from unittest import mock

import psycopg2
import pytest
from fastapi.testclient import TestClient
from psycopg2 import errors, sql

from resource_explorer import preview_credential_holder as holder
from resource_explorer import table_preview as tp
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors.database import column_matching as cm
from resource_explorer.surveyors.database import connection as conn_mod
from resource_explorer.surveyors.database.column_profile_step import column_match_rows
from resource_explorer.surveyors.database.sampling import SampleProvenance
from resource_explorer.surveyors.database.connection import (
    EngineCapabilities,
    PostgreSQLConnection,
    PreviewDenied,
    PreviewError,
    PreviewStale,
    PreviewTimedOut,
    PreviewUnsupported,
)

PASSWORD = "pw-not-a-real-secret-93"
VALUE = "ROW-VALUE-SENTINEL-77"
SURVEYED = "2026-10-06T10:00:00"


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────

def _flat(composed):
    out = []
    for part in getattr(composed, "seq", [composed]):
        out.extend(_flat(part) if isinstance(part, sql.Composed) else [part])
    return out


class FakeCursor:
    def __init__(self, log, rows=None, raises=None):
        self.log, self.rows, self.raises = log, rows or [], raises

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params=None):
        self.log.append(("execute", q))
        if self.raises is not None and not isinstance(q, str) and "LIMIT" in "".join(
                p.string for p in _flat(q) if isinstance(p, sql.SQL)):
            raise self.raises

    def fetchall(self):
        return self.rows


class FakePgConn:
    def __init__(self, rows=None, raises=None):
        self.log, self.rows, self.raises = [], rows, raises

    def rollback(self):
        self.log.append(("rollback", None))

    def cursor(self):
        return FakeCursor(self.log, self.rows, self.raises)


def _pg(rows=None, raises=None):
    c = PostgreSQLConnection("h", 1, "d", "u", "p")
    c._conn = FakePgConn(rows, raises)
    return c


# ── the typed query ──────────────────────────────────────────────────────────────────────────────────────────

class TestBuildPreviewQuery:
    def test_names_are_identifiers_and_limit_a_literal_int(self):
        q = PostgreSQLConnection.build_preview_query("sales", "orders", ["id", "email"], 20,
                                                     {"id": "integer", "email": "integer"})
        parts = _flat(q)
        idents = [p.strings for p in parts if isinstance(p, sql.Identifier)]
        assert idents == [("id",), ("id",), ("email",), ("email",), ("sales",), ("orders",)]   # each cut column: name + alias
        assert [p.wrapped for p in parts if isinstance(p, sql.Literal)] == [501, 501, 20]

    def test_hostile_names_stay_inside_identifiers(self):
        evil = 'x"; DROP TABLE t; --'
        q = PostgreSQLConnection.build_preview_query(evil, evil, [evil], 100)
        text = "".join(p.string for p in _flat(q) if isinstance(p, sql.SQL))
        assert "DROP" not in text and ";" not in text
        assert all(p.strings == (evil,) for p in _flat(q) if isinstance(p, sql.Identifier))

    @pytest.mark.parametrize("bad", [0, 7, 21, 1000, "20; DROP TABLE t", None, 20.5])
    def test_only_the_two_counts_are_accepted(self, bad):
        with pytest.raises(ValueError):
            PostgreSQLConnection.build_preview_query("s", "t", ["a"], bad)

    def test_no_selectable_column_selects_a_constant_not_a_masked_value(self):
        q = PostgreSQLConnection.build_preview_query("s", "t", [], 20)
        assert "".join(p.string for p in _flat(q) if isinstance(p, sql.SQL)).startswith("SELECT 1 FROM")

    def test_the_only_text_entry_points_take_no_sql(self):
        import inspect
        params = inspect.signature(PostgreSQLConnection.preview_rows).parameters
        assert list(params) == ["self", "schema", "table", "columns", "limit", "timeout_ms", "column_types"]
        assert not hasattr(PostgreSQLConnection, "preview_sql")


class TestPreviewRows:
    def test_read_only_transaction_first_then_timeout_then_the_query_then_rollback(self):
        c = _pg(rows=[(1, "a")])
        out = c.preview_rows("s", "t", ["id", "name"], 20, column_types={"id": "integer", "name": "text"})
        assert out == [{"id": 1, "name": "a"}]
        log = c._conn.log
        assert log[0] == ("rollback", None)
        assert log[1] == ("execute", "SET TRANSACTION READ ONLY")
        timeout = log[2][1]
        assert [p.wrapped for p in _flat(timeout) if isinstance(p, sql.Literal)] == [10000]
        assert "statement_timeout" in "".join(p.string for p in _flat(timeout) if isinstance(p, sql.SQL))
        assert log[-1] == ("rollback", None)

    def test_no_columns_gives_empty_dicts_per_row(self):
        assert _pg(rows=[(1,), (1,)]).preview_rows("s", "t", [], 20) == [{}, {}]

    def test_timeout(self):
        with pytest.raises(PreviewTimedOut) as ei:
            _pg(raises=errors.QueryCanceled("canceling statement due to statement timeout")).preview_rows(
                "s", "t", ["a"], 20)
        assert ei.value.sentence == "no rows shown · the query ran longer than 10 s and was stopped · nothing was read"

    def test_no_read_permission_quotes_the_first_line_only(self):
        exc = errors.InsufficientPrivilege("permission denied for table orders\nDETAIL: more")
        with pytest.raises(PreviewDenied) as ei:
            _pg(raises=exc).preview_rows("s", "orders", ["a"], 20)
        assert ei.value.sentence == "permission denied for table orders"

    def test_stale_catalogue(self):
        with pytest.raises(PreviewStale):
            _pg(raises=errors.UndefinedColumn('column "x" does not exist')).preview_rows("s", "t", ["x"], 20)

    def test_a_data_error_never_echoes_its_message(self):
        exc = errors.InvalidTextRepresentation(f'invalid input syntax for type integer: "{VALUE}"')
        with pytest.raises(PreviewError) as ei:
            _pg(raises=exc).preview_rows("s", "t", ["a"], 20)
        assert VALUE not in ei.value.sentence and VALUE not in str(ei.value)

    def test_base_engine_refuses_with_words(self):
        class Other(conn_mod.DatabaseConnection):
            connect = execute_query = get_schema_info = get_statistics = close = lambda self, *a, **k: None
        with pytest.raises(PreviewUnsupported):
            Other().preview_rows("s", "t", ["a"], 20)
        assert Other().capabilities.row_preview is False
        assert PostgreSQLConnection("h", 1, "d", "u", "p").capabilities.row_preview is True


# ── fixtures: registry + route ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "preview.db"))
    r.register_database(DatabaseEntity(
        slug="coco", display_name="Coco", db_type="postgresql", host="h", port=5432, database_name="coco",
        db_user="surveyor", db_password="surveyor-pw"))
    r.write_detail_rows("database_tables", "coco", SURVEYED, rows=[
        {"schema_name": "sales", "table_name": "orders", "table_type": "BASE TABLE", "row_count": 5,
         "size_bytes": 1, "state": "measured"},
        {"schema_name": "sales", "table_name": "v_orders", "table_type": "VIEW", "row_count": None,
         "size_bytes": None, "state": "measured"},
    ])
    cols = [("id", 1), ("email", 2), ("note", 3), ("qty", 4), ("odd", 5)]
    r.write_detail_rows("database_columns", "coco", SURVEYED, rows=[
        {"schema_name": "sales", "table_name": t, "column_name": n, "ordinal_position": i, "data_type": "text",
         "base_type": "text", "is_nullable": 1, "is_primary_key": 0, "foreign_key_json": None, "description": "",
         "state": "measured"}
        for t in ("orders", "v_orders") for n, i in cols])

    r.write_detail_rows("database_column_matches", "coco", SURVEYED, rows=real_match_rows({
        "id": "no_match", "email": "matched", "note": "not_sampled", "odd": "unmatched_patterned"}))
    # "qty" has no verdict at all
    return r



# ── column-match rows from the REAL producer ─────────────────────────────────────────────────────────────────

_PROV = SampleProvenance(strategy="random", sample_rows=25, total_rows=25, sampled_at=SURVEYED)
_EMAIL_CLASS = cm.KnownDataClass(
    guid="g-email", qualified_name="DataClass::Email", display_name="Email Address",
    specification=r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", data_type="string", match_property_names=("email",))
_ZIP_CLASS = cm.KnownDataClass(guid="g-zip", qualified_name="DataClass::Zip", display_name="Zip", specification=r"\d{5}")
_EMAILS = [f"person{i}@example.org" for i in range(25)]
_WORDS = "mercury venus earth mars jupiter saturn uranus neptune ceres pluto haumea makemake".split() * 2
_COLOURS = cm.KnownValidValueSet(guid="v-col", qualified_name="VVS::Colours", display_name="Colours",
                                 values=("red", "green", "blue"))


def produce_match(column: str, verdict: str) -> dict:
    """The `ColumnMatch.as_dict()` of a column that `column_matching`'s real functions give `verdict` to."""
    base = dict(schema_name="sales", table_name="orders", column_name=column, provenance=_PROV)

    def dc(values, classes=(_EMAIL_CLASS,), column_type="text"):
        return cm.data_class_match(**base, column_type=column_type, known_classes=list(classes), sampled_values=values)

    if verdict == "matched":
        m = dc(_EMAILS)
    elif verdict == "no_match":
        m = dc(_WORDS)
    elif verdict == "unmatched_patterned":
        m = dc(_EMAILS, classes=(_ZIP_CLASS,))
    elif verdict == "inconclusive":
        m = dc(_EMAILS[:3])
    elif verdict == "no_candidates":
        m = dc(_EMAILS, classes=())
    elif verdict == "not_sampled":
        m = dc(None)
    elif verdict == "not_applicable":
        m = dc(_EMAILS, column_type="bytea")
    elif verdict == "partial_match":
        m = cm.reference_data_match(**base, column_type="text", known_sets=[_COLOURS],
                                    distinct_values=["red", "green", "purple"], distinct_count=3, total_rows=30)
        m.question = "data_class_match"          # the stored-row filter reads this question; the verdict is real
    else:
        raise AssertionError(verdict)
    assert m.verdict == verdict, (verdict, m.verdict)
    return m.as_dict()


def real_match_rows(verdicts: dict) -> list[dict]:
    """`database_column_matches` rows exactly as the column-profile step stores them (`column_match_rows`)."""
    return column_match_rows([produce_match(c, v) for c, v in verdicts.items()])


def _by(decisions):
    return {d["name"]: d for d in decisions}


class TestMasking:
    def test_each_reason_and_the_one_shown_column(self, registry):
        d = _by(tp.masking_decisions(registry, "coco", "sales", "orders", ["id", "email", "note", "qty", "odd"]))
        assert d["id"]["masked"] is False and d["id"]["reason"] is None
        assert (d["email"]["reason"], d["email"]["tag"]) == (tp.REASON_PII, "masked · Email Address")
        assert d["email"]["hover"].startswith("column profile matched the class Email Address")
        assert "PII" not in d["email"]["hover"] and "run 10-06" in d["email"]["hover"]
        assert (d["note"]["reason"], d["note"]["tag"]) == (tp.REASON_NOT_PROFILED, "masked · not profiled")
        assert "can't say it's safe" in d["note"]["hover"]
        assert d["qty"]["reason"] == tp.REASON_NOT_PROFILED       # never profiled at all
        assert d["odd"]["reason"] == tp.REASON_UNDECIDED          # read, could not decide: not "not profiled"

    def test_a_confidential_classification_masks_a_shown_column(self, registry):
        registry.save_context("database", "coco", {"enrichment": {
            "sensitivity": {"value": "confidential", "author": "dwolfson", "set_at": "2026-10-12T09:00:00"}}})
        d = _by(tp.masking_decisions(registry, "coco", "sales", "orders", ["id", "email"]))
        assert d["id"]["reason"] == tp.REASON_CONFIDENTIAL and d["id"]["tag"] == "masked · confidential"
        assert d["id"]["hover"] == "Confidentiality classification · set by dwolfson 10-12 in Enrichment"
        assert d["email"]["reason"] == tp.REASON_PII                # a finding outranks the classification

    @pytest.mark.parametrize("level", ["public", "internal", "unknown", ""])
    def test_a_low_classification_does_not_mask(self, registry, level):
        registry.save_context("database", "coco", {"enrichment": {"sensitivity": {"value": level}}})
        assert tp.masking_decisions(registry, "coco", "sales", "orders", ["id"])[0]["masked"] is False

    def test_a_table_with_no_stored_profile_masks_everything(self, registry):
        d = tp.masking_decisions(registry, "coco", "sales", "v_orders", ["id", "email"])
        assert all(x["masked"] and x["reason"] == tp.REASON_NOT_PROFILED for x in d)

    def test_counts_count_each_column_once(self, registry):
        c = tp.masking_counts(tp.masking_decisions(registry, "coco", "sales", "orders",
                                                   ["id", "email", "note", "qty", "odd"]))
        assert c == {"total": 5, "masked": 4, "pii": 1, "confidential": 0, "not_profiled": 2, "undecided": 1}


class TestCredentialHolder:
    def test_per_person_and_database_and_never_in_text(self):
        holder.clear_all()
        holder.put("dan", "s1", "coco", "dan_ro", PASSWORD)
        assert holder.held_user("dan", "s1", "coco") == "dan_ro"
        assert holder.held_user("mandy", "s1", "coco") is None and holder.held_user("dan", "s1", "other") is None
        assert holder.held_user("dan", "s2", "coco") is None          # another sign-in is asked again
        cred = holder.take("dan", "s1", "coco")
        assert PASSWORD not in repr(cred) and PASSWORD not in str(cred)
        assert PASSWORD not in repr(holder._HOLDER)
        holder.clear_user("dan")
        assert holder.take("dan", "s1", "coco") is None

    def test_expires_at_the_session_end_and_the_ttl(self):
        holder.clear_all()
        now = [1000.0]
        h = holder._Holder(clock=lambda: now[0])
        h.put("dan", "s", "coco", "u", PASSWORD, not_after=1100.0)
        now[0] = 1099.0
        assert h.take("dan", "s", "coco")
        now[0] = 1100.0
        assert h.take("dan", "s", "coco") is None
        h.put("dan", "s", "coco", "u", PASSWORD, ttl=5)
        now[0] = 1106.0
        assert h.take("dan", "s", "coco") is None

    def test_only_table_preview_reads_the_holder(self):
        import pathlib
        root = pathlib.Path(tp.__file__).parent
        users = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                       if "preview_credential_holder" in p.read_text() and p.name != "preview_credential_holder.py")
        assert users == ["table_preview.py", "web/routes/auth.py", "web/routes/table_preview.py"]


# ── the route, end to end ────────────────────────────────────────────────────────────────────────────────────

class Spy:
    """A fake connection that binds the REAL signatures (autospec of PostgreSQLConnection and of
    database_connection), recording what it was asked and never touching a database."""

    def __init__(self, rows=None, raises=None):
        self.asked, self.opened, self.types = [], [], []
        self.rows, self.raises = rows, raises
        self.conn = mock.create_autospec(PostgreSQLConnection, instance=True, spec_set=True)
        self.conn.capabilities = EngineCapabilities(row_preview=True)
        self.conn.preview_rows.side_effect = self._preview
        self.opener = mock.create_autospec(conn_mod.database_connection, side_effect=self._open)

    def _open(self, db_entity, credentials, connect_timeout=None):
        self.opened.append((db_entity.slug, credentials["user"], connect_timeout))
        return contextlib.nullcontext(self.conn)

    def _preview(self, schema, table, columns, limit, *, timeout_ms=10000, column_types=None):
        self.types.append(column_types)
        self.asked.append((schema, table, list(columns), limit))
        if self.raises:
            raise self.raises
        return [{c: f"{VALUE}-{c}" if c != "id" else 1 for c in columns} for _ in range(self.rows or 3)]


def _make_env(registry, monkeypatch, caplog, *, patch_identity):
    holder.clear_all()
    getattr(tp, "throttle_reset", lambda: None)()
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    if patch_identity:
        monkeypatch.setattr("resource_explorer.auth.get_current_user",
                            lambda request: {"user_id": "dan", "exp": 4102444800})
    # the credential check connects: stand in for it with a signature-bound fake
    from resource_explorer import credential_check
    checks = mock.create_autospec(credential_check.check_database_credential, side_effect=lambda db, u, p: None)
    monkeypatch.setattr(credential_check, "check_database_credential", checks)
    spy = Spy()
    monkeypatch.setattr(conn_mod, "database_connection", spy.opener)
    # nothing may write a credential anywhere
    monkeypatch.setattr(ProjectRegistry, "update_database_credentials",
                        lambda *a, **k: pytest.fail("credential written to the registry"))
    monkeypatch.setattr("resource_explorer.omsecrets_store.write_credential",
                        lambda *a, **k: pytest.fail("credential written to the secrets store"))
    caplog.set_level(logging.DEBUG)
    from resource_explorer.web.app import app
    return type("Env", (), {"client": TestClient(app), "spy": spy, "registry": registry, "checks": checks})


@pytest.fixture
def env(registry, monkeypatch, caplog):
    return _make_env(registry, monkeypatch, caplog, patch_identity=True)


@pytest.fixture
def real_env(registry, monkeypatch, caplog):
    """The same, but with NO patched identity: the app's real `get_current_user` reads a real signed token."""
    return _make_env(registry, monkeypatch, caplog, patch_identity=False)


def _token(user="dan"):
    from resource_explorer.auth import create_access_token
    return create_access_token(user_id=user, egeria_token="egeria-not-real")


def _as(token):
    return {"Authorization": f"Bearer {token}"}


def _hold(env, user="dan_ro"):
    r = env.client.put("/api/databases/coco/preview/credential", json={"user": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r


def _preview(env, **body):
    return env.client.post("/api/databases/coco/preview", json={"schema": "sales", "table": "orders", **body})


def _everything_stored(registry, tmp_path):
    return "\n".join(sqlite3.connect(str(tmp_path / "preview.db")).iterdump())


class TestRoute:
    def test_signed_out_is_401(self, env, monkeypatch):
        monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
        assert env.client.get("/api/databases/coco/preview/credential").status_code == 401
        assert _preview(env).status_code == 401

    def test_no_credential_held_is_409_and_runs_nothing(self, env):
        r = _preview(env)
        assert r.status_code == 409 and "no credential of yours" in r.json()["detail"]
        assert env.spy.opened == []

    def test_holding_tests_first_names_the_user_never_the_password(self, env):
        r = _hold(env)
        assert r.json() == {"held": True, "user": "dan_ro"}
        env.checks.assert_called_once()
        g = env.client.get("/api/databases/coco/preview/credential").json()
        assert g["held"] and g["user"] == "dan_ro" and PASSWORD not in str(g)

    def test_a_refused_credential_holds_nothing(self, env):
        from resource_explorer.credential_check import CredentialCheckError
        env.checks.side_effect = CredentialCheckError("credential", "Credential not saved: the server refused it")
        r = env.client.put("/api/databases/coco/preview/credential", json={"user": "x", "password": PASSWORD})
        assert r.status_code == 400 and "not kept" in r.json()["detail"] and PASSWORD not in r.text
        assert env.client.get("/api/databases/coco/preview/credential").json()["held"] is False

    def test_forget_and_sign_out_drop_it(self, env):
        _hold(env)
        env.client.delete("/api/databases/coco/preview/credential")
        assert env.client.get("/api/databases/coco/preview/credential").json()["held"] is False

    def test_preview_rows_masks_by_default_and_never_selects_a_masked_column(self, env, tmp_path):
        _hold(env)
        r = _preview(env, limit=20)
        assert r.status_code == 200
        out = r.json()
        assert out["outcome"] == "rows" and out["row_count"] == 3 and out["as_user"] == "dan_ro"
        assert env.spy.asked == [("sales", "orders", ["id"], 20)]            # only the unmasked column was read
        assert env.spy.opened == [("coco", "dan_ro", 5)]
        assert list(out["rows"][0]) == ["id"] and VALUE not in str(out["columns"])
        assert [c["tag"] for c in out["columns"] if c["masked"]] == [
            "masked · Email Address", "masked · not profiled", "masked · not profiled", "masked · undecided"]
        assert out["counts"]["masked"] == 4 and out["kind"] == "table"
        assert out["egeria"] == {"state": "not_built"}

    def test_audit_row_is_written_before_the_rows_and_read_back(self, env):
        _hold(env)
        out = _preview(env).json()
        rec = out["recorded"]
        row = env.registry.get_activity(rec["id"])
        assert row["operation"] == "preview" and rec["at"] == row["ts"] and rec["state"] == "recorded"
        import json
        d = json.loads(row["detail"])
        assert d["who"] == "dan" and d["db_user"] == "dan_ro" and d["table"] == "orders" and d["rows"] == 3
        assert d["masked"] == ["email", "note", "qty", "odd"] and d["revealed"] == []

    def test_reveal_one_column_selects_it_and_records_its_own_row(self, env):
        _hold(env)
        out = _preview(env, reveal=["email"]).json()
        assert env.spy.asked[-1][2] == ["id", "email"]
        assert next(c for c in out["columns"] if c["name"] == "email")["revealed"] is True
        assert out["rows"][0]["email"].startswith(VALUE)
        import json
        d = json.loads(env.registry.get_activity(out["recorded"]["id"])["detail"])
        assert d["action"] == "reveal" and d["revealed"] == ["email"]
        rows = [r for r in env.registry.list_activity(operation="preview") if '"credential"' not in r["detail"]]
        assert len(rows) == 1                                                   # a reveal is one row, not two

    def test_a_revealed_column_is_not_remembered_by_the_next_preview(self, env):
        _hold(env)
        _preview(env, reveal=["email"])
        out = _preview(env).json()
        assert env.spy.asked[-1][2] == ["id"] and not any(c["revealed"] for c in out["columns"])

    def test_unknown_reveal_limit_and_table_are_refused_before_any_query(self, env):
        _hold(env)
        assert _preview(env, reveal=["nope"]).status_code == 422
        assert _preview(env, limit=21).status_code == 422
        assert env.client.post("/api/databases/coco/preview",
                               json={"schema": "sales", "table": "x; DROP"}).status_code == 404
        assert env.spy.asked == []

    @pytest.mark.parametrize("exc,outcome", [
        (PreviewDenied("permission denied for table orders"), "no_read"),
        (PreviewTimedOut("no rows shown · the query ran longer than 10 s and was stopped · nothing was read"),
         "timeout"),
    ])
    def test_refusals_keep_their_words_and_are_recorded_as_errors(self, env, exc, outcome):
        _hold(env)
        env.spy.raises = exc
        out = _preview(env).json()
        assert out["outcome"] == outcome and out["sentence"] == exc.sentence and "rows" not in out
        assert env.registry.get_activity(out["recorded"]["id"])["status"] == "error"

    def test_an_empty_table_is_its_own_outcome(self, env):
        _hold(env)
        env.spy.rows = 0
        env.spy.rows = None
        env.spy._preview = None
        env.spy.conn.preview_rows.side_effect = lambda *a, **k: []
        out = _preview(env).json()
        assert out["outcome"] == "empty" and out["row_count"] == 0

    def test_a_view_is_previewable_and_says_what_it_is(self, env):
        _hold(env)
        out = env.client.post("/api/databases/coco/preview", json={"schema": "sales", "table": "v_orders"}).json()
        assert out["kind"] == "view" and out["outcome"] == "rows"

    def test_another_engine_refuses_with_words(self, env, registry):
        registry.register_database(DatabaseEntity(slug="my", display_name="My", db_type="mysql", host="h",
                                                  port=3306, database_name="m"))
        g = env.client.get("/api/databases/my/preview/credential").json()
        assert g["engine_supported"] is False and "PostgreSQL only" in g["unsupported"]
        assert env.client.put("/api/databases/my/preview/credential",
                              json={"user": "u", "password": PASSWORD}).status_code == 409

    def test_if_re_cannot_record_the_rows_are_not_shown(self, env, monkeypatch):
        _hold(env)
        monkeypatch.setattr(ProjectRegistry, "write_activity", lambda self, e: (_ for _ in ()).throw(OSError("disk")))
        r = _preview(env)
        assert r.status_code == 503 and VALUE not in r.text

    def test_a_connect_failure_is_an_outcome_with_the_password_scrubbed(self, env):
        _hold(env)
        env.spy.opener.side_effect = psycopg2.OperationalError(f'FATAL: password authentication failed {PASSWORD}')
        out = _preview(env).json()
        assert out["outcome"] == "credential_refused" and PASSWORD not in str(out)


class TestNothingLeaks:
    def test_credential_and_row_values_reach_no_row_no_log_no_trace(self, env, caplog, tmp_path):
        _hold(env)
        _preview(env, reveal=["email"])
        _preview(env)
        env.client.delete("/api/databases/coco/preview/credential")
        stored = _everything_stored(env.registry, tmp_path)
        assert PASSWORD not in stored and VALUE not in stored
        assert PASSWORD not in caplog.text and VALUE not in caplog.text

    def test_a_validation_error_does_not_echo_the_password(self, env):
        r = env.client.put("/api/databases/coco/preview/credential", json={"user": ["bad"], "password": PASSWORD})
        assert r.status_code == 422 and PASSWORD not in r.text


# ── fix round 1 (Opus security review, 2026-10-20) ───────────────────────────────────────────────────────────

class TestMaskingFromTheRealProducer:
    """Finding 1 (critical). Every row here comes from `column_matching`'s real functions through the column-profile
    step's own row builder, so the fixture cannot drift from the producer again: the matched verdicts do NOT set
    `privacy_relevant`, and the old rule (`matched` AND `privacy_relevant`) showed a matched PII column in clear."""

    def _decide(self, registry, tmp_path, verdict, mutate=None):
        m = produce_match("c", verdict)
        if mutate:
            mutate(m)
        rows = column_match_rows([m])
        registry.write_detail_rows("database_column_matches", "coco", "2026-10-07T10:00:00", rows=rows)
        return tp.masking_decisions(registry, "coco", "sales", "orders", ["c"])[0]

    def test_the_producer_really_leaves_privacy_relevant_off_a_matched_column(self):
        m = produce_match("c", "matched")
        assert m["verdict"] == "matched" and m["matched_display_name"] == "Email Address"
        assert m["privacy_relevant"] is False           # the fact the old rule overlooked

    def test_a_matched_column_is_masked_as_pii_with_the_class_name(self, registry, tmp_path):
        d = self._decide(registry, tmp_path, "matched")
        assert d["masked"] is True and d["reason"] == tp.REASON_PII
        assert d["tag"] == "masked · Email Address" and "PII" not in d["hover"]
        assert d["hover"].startswith("column profile matched the class Email Address")

    def test_a_partial_match_is_masked_as_pii_too(self, registry, tmp_path):
        d = self._decide(registry, tmp_path, "partial_match")
        assert d["masked"] is True and d["reason"] == tp.REASON_PII and d["tag"] == "masked · Colours"
        # a matched class that is not privacy-relevant must not be called PII: masked, and named neutrally
        assert d["hover"].startswith("column profile matched the class Colours") and "PII" not in d["hover"]

    @pytest.mark.parametrize("verdict,reason", [
        ("unmatched_patterned", tp.REASON_UNDECIDED), ("inconclusive", tp.REASON_UNDECIDED),
        ("no_candidates", tp.REASON_UNDECIDED), ("not_sampled", tp.REASON_NOT_PROFILED),
        ("not_applicable", tp.REASON_NOT_PROFILED)])
    def test_every_other_real_verdict_stays_masked(self, registry, tmp_path, verdict, reason):
        d = self._decide(registry, tmp_path, verdict)
        assert d["masked"] is True and d["reason"] == reason

    def test_no_match_is_the_only_verdict_that_shows_a_column(self, registry, tmp_path):
        assert self._decide(registry, tmp_path, "no_match")["masked"] is False

    @pytest.mark.parametrize("unknown", ["some_future_verdict", "NO_MATCH", "match", "No_Match "])
    def test_a_verdict_nobody_has_heard_of_stays_masked(self, registry, tmp_path, unknown):
        d = self._decide(registry, tmp_path, "no_match", mutate=lambda m: m.update(verdict=unknown))
        assert d["masked"] is True

    def test_a_matched_email_column_never_leaves_the_database_through_the_route(self, env):
        _hold(env)
        out = _preview(env).json()
        assert env.spy.asked == [("sales", "orders", ["id"], 20)]
        email = next(c for c in out["columns"] if c["name"] == "email")
        assert email["masked"] is True and email["reason"] == "pii"
        assert "email" not in out["rows"][0] and "email" not in env.spy.asked[0][2]


class TestSignOutDropsTheCredential:
    """Finding 2. With a REAL signed token and no patched identity, the logout POST reaches `clear_session`."""

    def test_logout_with_the_bearer_drops_what_that_sign_in_held(self, real_env):
        tok = _token()
        r = real_env.client.put("/api/databases/coco/preview/credential", headers=_as(tok),
                                json={"user": "dan_ro", "password": PASSWORD})
        assert r.status_code == 200
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(tok)).json()["held"]
        assert real_env.client.post("/api/auth/logout", headers=_as(tok)).status_code == 200
        assert holder.held_user("dan", holder.session_key(None, tok), "coco") is None
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(tok)).json()["held"] is False

    def test_logout_without_a_bearer_drops_nothing_and_says_so_by_doing_nothing(self, real_env):
        tok = _token()
        real_env.client.put("/api/databases/coco/preview/credential", headers=_as(tok),
                            json={"user": "dan_ro", "password": PASSWORD})
        real_env.client.post("/api/auth/logout")                                  # what the old auth.js sent
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(tok)).json()["held"]


class TestCredentialBelongsToTheSignIn:
    """Finding 3. The key is (user id, session key, database): a new login or another device is asked again."""

    def test_another_sign_in_of_the_same_person_does_not_see_it(self, real_env):
        a, b = _token(), _token()
        import time
        time.sleep(1.1)                                  # tokens carry iat in whole seconds
        b = _token()
        assert a != b
        real_env.client.put("/api/databases/coco/preview/credential", headers=_as(a),
                            json={"user": "dan_ro", "password": PASSWORD})
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(a)).json()["held"]
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(b)).json()["held"] is False
        r = real_env.client.post("/api/databases/coco/preview", headers=_as(b), json={"schema": "sales", "table": "orders"})
        assert r.status_code == 409 and "no credential of yours" in r.json()["detail"]
        assert real_env.spy.opened == []
        assert real_env.client.post("/api/databases/coco/preview", headers=_as(a),
                                    json={"schema": "sales", "table": "orders"}).status_code == 200

    def test_signing_out_of_one_does_not_drop_the_other(self, real_env):
        import time
        a = _token()
        time.sleep(1.1)
        b = _token()
        for t in (a, b):
            real_env.client.put("/api/databases/coco/preview/credential", headers=_as(t),
                                json={"user": "dan_ro", "password": PASSWORD})
        real_env.client.post("/api/auth/logout", headers=_as(a))
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(a)).json()["held"] is False
        assert real_env.client.get("/api/databases/coco/preview/credential", headers=_as(b)).json()["held"]

    def test_session_key_is_the_jti_else_a_digest_and_never_the_token(self):
        assert holder.session_key({"jti": "abc"}, "tok") == "jti:abc"
        k = holder.session_key({}, "tok-secret-value")
        assert k.startswith("sha256:") and "tok-secret-value" not in k and len(k) == len("sha256:") + 64
        assert holder.session_key({}, "") == ""

    def test_an_expired_entry_goes_and_is_swept_on_the_next_put(self):
        now = [1000.0]
        h = holder._Holder(clock=lambda: now[0])
        h.put("dan", "s1", "coco", "u", PASSWORD, ttl=5)
        now[0] = 1010.0
        h.put("mandy", "s2", "coco", "u", PASSWORD, ttl=5)
        assert repr(h) == "<preview credential holder: 1 held>"


class TestCredentialAttempts:
    """Finding 4. Every PUT is an RE activity row; five refusals in ten minutes lock the pair for ten minutes; a
    wrong password and an unknown user get the same sentence."""

    @staticmethod
    def _put(env, user="dan_ro"):
        return env.client.put("/api/databases/coco/preview/credential", json={"user": user, "password": PASSWORD})

    @staticmethod
    def _attempts(env):
        import json
        return [json.loads(r["detail"]) for r in env.registry.list_activity(operation="preview")
                if json.loads(r["detail"]).get("action") == "credential"]

    @staticmethod
    def _refuse(env, message):
        from resource_explorer.credential_check import CredentialCheckError
        env.checks.side_effect = CredentialCheckError("credential", message)

    def test_every_attempt_is_a_row_with_who_user_and_outcome_and_never_the_password(self, env, tmp_path):
        from resource_explorer.credential_check import CredentialCheckError
        assert self._put(env).status_code == 200
        self._refuse(env, "Credential not saved: password authentication failed")
        self._put(env, "dan_bad")
        env.checks.side_effect = CredentialCheckError("unreachable", "Credential not saved: could not reach h:5432")
        self._put(env, "dan_ro")
        got = sorted((a["db_user"], a["outcome"], a["who"]) for a in self._attempts(env))
        assert got == [("dan_bad", "refused", "dan"), ("dan_ro", "held", "dan"), ("dan_ro", "unreachable", "dan")]
        assert PASSWORD not in _everything_stored(env.registry, tmp_path)
        statuses = {r["status"] for r in env.registry.list_activity(operation="preview")}
        assert statuses == {"ok", "error"}

    def test_a_wrong_password_and_an_unknown_user_get_the_same_sentence(self, env):
        self._refuse(env, 'Credential not saved: password authentication failed for user "dan_ro"')
        wrong = self._put(env, "dan_ro")
        self._refuse(env, 'Credential not saved: role "nobody" does not exist')
        unknown = self._put(env, "nobody")
        assert wrong.status_code == unknown.status_code == 400
        assert wrong.json() == unknown.json() == {"detail": tp.REFUSED_SENTENCE}

    def test_five_refusals_lock_the_pair_for_ten_minutes_then_it_opens(self, env, monkeypatch):
        now = [10_000.0]
        monkeypatch.setattr(tp._THROTTLE, "_clock", lambda: now[0])
        self._refuse(env, "Credential not saved: password authentication failed")
        for _ in range(5):
            assert self._put(env).status_code == 400
            now[0] += 1
        asked = env.checks.call_count
        r = self._put(env)
        assert r.status_code == 429 and r.json() == {"detail": tp.THROTTLED_SENTENCE}
        assert env.checks.call_count == asked                       # a locked pair is not even tested
        assert self._attempts(env)[0]["outcome"] == "throttled"     # newest first: the refusal itself is a row
        now[0] += 590
        assert self._put(env).status_code == 429
        now[0] += 10
        env.checks.side_effect = lambda db, u, p: None
        assert self._put(env).status_code == 200

    def test_the_lock_is_per_person_and_database(self, env, monkeypatch, registry):
        self._refuse(env, "Credential not saved: password authentication failed")
        for _ in range(5):
            self._put(env)
        assert self._put(env).status_code == 429
        monkeypatch.setattr("resource_explorer.auth.get_current_user",
                            lambda request: {"user_id": "mandy", "exp": 4102444800})
        assert self._put(env, "mandy_ro").status_code == 400          # someone else, another db user: refused, not locked
        assert self._put(env).status_code == 429                      # the same db user is the global bucket's

    def test_a_success_does_not_reset_the_count_the_reviewers_cycle_locks_by_the_second_pass(self, env):
        """N1. Four wrong guesses for `postgres`, then the correct `alice_ro`, repeated: the success must not
        clear the failures, so the fifth failure of the second cycle locks the pair."""
        from resource_explorer.credential_check import CredentialCheckError

        def check(db, user, password):
            if user != "alice_ro":
                raise CredentialCheckError("credential", "Credential not saved: password authentication failed")

        env.checks.side_effect = check
        codes = []
        for _cycle in range(2):
            codes += [self._put(env, "postgres").status_code for _ in range(4)]
            codes.append(self._put(env, "alice_ro").status_code)
        assert codes[:5] == [400, 400, 400, 400, 200]
        # the first wrong guess of cycle two is the fifth failure; the very next request is locked
        assert codes[5] == 400
        assert codes[6] == 429, codes

    def test_the_lock_counts_failures_across_every_database_user_for_the_person(self, env):
        self._refuse(env, "Credential not saved: password authentication failed")
        codes = [self._put(env, f"guess{i}").status_code for i in range(6)]
        assert codes == [400] * 5 + [429]

    def test_several_re_accounts_cannot_multiply_the_budget_for_one_database_user(self, env, monkeypatch):
        """N1. A global bucket per (database, db_user) across persons, with the same limits."""
        self._refuse(env, "Credential not saved: password authentication failed")
        codes = []
        for who in ("p1", "p2", "p3", "p4", "p5", "p6", "p7"):
            monkeypatch.setattr("resource_explorer.auth.get_current_user",
                                lambda request, who=who: {"user_id": who, "exp": 4102444800})
            codes.append(self._put(env, "postgres").status_code)
        assert codes == [400] * 5 + [429, 429], codes
        # a different database user is a different bucket
        monkeypatch.setattr("resource_explorer.auth.get_current_user",
                            lambda request: {"user_id": "p8", "exp": 4102444800})
        assert self._put(env, "someone_else").status_code == 400

    def test_one_throttled_row_per_lockout_not_one_per_refused_request(self, env):
        self._refuse(env, "Credential not saved: password authentication failed")
        for _ in range(5):
            self._put(env)
        for _ in range(8):
            assert self._put(env).status_code == 429
        outcomes = [a["outcome"] for a in self._attempts(env)]
        assert outcomes.count("throttled") == 1 and outcomes.count("refused") == 5

    def test_a_refused_attempt_records_only_a_plausible_role_name(self, env, tmp_path):
        self._refuse(env, "Credential not saved: password authentication failed")
        self._put(env, "dan_ro")
        self._put(env, "dan:hunter2 secret!")                 # a pasted "user:password"
        self._put(env, "x" * 64)                              # plausible characters, but too long for a role name
        users = sorted(a["db_user"] for a in self._attempts(env))
        assert users == sorted(["dan_ro", "(not a valid user name)", "(not a valid user name)"])
        stored = _everything_stored(env.registry, tmp_path)
        assert "hunter2" not in stored and "x" * 64 not in stored

    def test_the_credential_body_is_bounded_and_the_422_never_echoes_the_values(self, env):
        long_user, long_pw = "u" * 129, "p" * 1025
        r = env.client.put("/api/databases/coco/preview/credential", json={"user": long_user, "password": "ok"})
        assert r.status_code == 422 and long_user not in r.text
        r = env.client.put("/api/databases/coco/preview/credential", json={"user": "dan_ro", "password": long_pw})
        assert r.status_code == 422 and long_pw not in r.text
        assert env.checks.call_count == 0
        assert env.client.put("/api/databases/coco/preview/credential",
                              json={"user": "u" * 128, "password": "p" * 1024}).status_code == 200


    def test_if_the_attempt_cannot_be_recorded_nothing_is_kept(self, env, monkeypatch):
        monkeypatch.setattr(ProjectRegistry, "write_activity", lambda self, e: (_ for _ in ()).throw(OSError("disk")))
        r = self._put(env)
        assert r.status_code == 503 and PASSWORD not in r.text
        assert env.client.get("/api/databases/coco/preview/credential").json()["held"] is False


class TestThrottleReservesBeforeConnecting:
    """N2. The slot is taken, counted as a provisional failure, before the connect; then settled."""

    def test_thirty_concurrent_puts_make_at_most_five_attempts(self, env, monkeypatch):
        import threading
        import time as _time
        from resource_explorer.credential_check import CredentialCheckError

        started = []
        lock = threading.Lock()

        def slow_refusal(db, user, password):
            with lock:
                started.append(user)
            _time.sleep(0.15)                                  # every other thread arrives while this one connects
            raise CredentialCheckError("credential", "Credential not saved: password authentication failed")

        env.checks.side_effect = slow_refusal
        monkeypatch.setattr(tp, "_record_credential_attempt", lambda *a, **k: None)
        results = []

        def go():
            try:
                tp.hold_credential(env.registry, _FakeDb(), "dan", "s", "postgres", "x")
            except tp.PreviewRefusal as exc:
                results.append(exc.status)

        threads = [threading.Thread(target=go) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(started) <= 5, len(started)
        assert sorted(results) == [400] * len(started) + [429] * (30 - len(started))

    def test_a_success_gives_its_slot_back_but_a_refusal_keeps_it(self):
        th = tp._Throttle(clock=lambda: 100.0)
        ok = th.reserve("dan", "coco", "alice_ro")
        assert ok.allowed
        th.settle(ok, failed=False)
        for i in range(5):
            r = th.reserve("dan", "coco", f"g{i}")
            assert r.allowed
            th.settle(r, failed=True)
        assert not th.reserve("dan", "coco", "g9").allowed


class _FakeDb:
    slug = "coco"
    display_name = "Coco"
    host = "h"
    port = 5432
    db_type = "postgresql"


class TestConnectFailuresAreRecorded:
    """Finding 5. A failed connect has its audit row, and fails closed like the other paths."""

    @pytest.mark.parametrize("exc,outcome", [
        (psycopg2.OperationalError("FATAL: password authentication failed"), "credential_refused"),
        (psycopg2.OperationalError("could not connect to server: Connection refused"), "unreachable")])
    def test_the_row_is_written_with_zero_rows_and_an_error_status(self, env, exc, outcome):
        import json
        _hold(env)
        env.spy.opener.side_effect = exc
        out = _preview(env).json()
        assert out["outcome"] == outcome and out["recorded"]["state"] == "recorded"
        row = env.registry.get_activity(out["recorded"]["id"])
        d = json.loads(row["detail"])
        assert row["status"] == "error" and d["outcome"] == outcome and d["rows"] == 0 and d["db_user"] == "dan_ro"

    def test_if_re_cannot_record_a_failed_connect_the_answer_is_503(self, env, monkeypatch):
        _hold(env)
        env.spy.opener.side_effect = psycopg2.OperationalError("FATAL: password authentication failed")
        monkeypatch.setattr(ProjectRegistry, "write_activity", lambda self, e: (_ for _ in ()).throw(OSError("disk")))
        assert _preview(env).status_code == 503


class TestNonDriverErrorsAfterConnecting:
    """Finding 6. A non-psycopg2 error after the connect is not a connect failure and never sends its text."""

    def test_preview_rows_turns_any_other_exception_into_a_fixed_sentence(self):
        for exc in (ValueError(f"bad value {VALUE}"), UnicodeDecodeError("utf-8", b"\xff", 0, 1, VALUE),
                    RuntimeError(VALUE)):
            with pytest.raises(PreviewError) as ei:
                _pg(raises=exc).preview_rows("s", "t", ["a"], 20)
            assert ei.value.sentence == "the database answer could not be read"
            assert VALUE not in str(ei.value) and ei.value.__cause__ is None and ei.value.__suppress_context__

    def test_the_route_labels_it_an_error_row_not_unreachable_and_sends_no_text(self, env):
        import json
        _hold(env)
        env.spy.conn.preview_rows.side_effect = RuntimeError(f"surprise {VALUE}")
        out = _preview(env).json()
        assert out["outcome"] == "error" and out["sentence"] == "the database answer could not be read"
        assert VALUE not in str(out) and "unreachable" not in str(out)
        row = env.registry.get_activity(out["recorded"]["id"])
        assert row["status"] == "error" and json.loads(row["detail"])["outcome"] == "error"
        assert VALUE not in row["summary"] + row["detail"]

    def test_a_failure_while_closing_the_connection_is_not_a_connect_failure_either(self, env):
        _hold(env)

        class Closing:
            def __enter__(self_inner):
                return env.spy.conn

            def __exit__(self_inner, *a):
                raise RuntimeError(f"close failed {VALUE}")
        env.spy.opener.side_effect = lambda *a, **k: Closing()
        out = _preview(env).json()
        assert out["outcome"] == "error" and VALUE not in str(out)


class TestSlugHandlingMatchesAcrossRoutes:
    """Finding 9. All four routes resolve the database and key on `db.slug`."""

    def test_a_credential_held_through_one_spelling_is_found_through_another(self, env):
        r = env.client.put("/api/databases/COCO/preview/credential", json={"user": "dan_ro", "password": PASSWORD})
        assert r.status_code == 200
        assert env.client.get("/api/databases/coco/preview/credential").json()["held"] is True
        assert env.client.get("/api/databases/COCO/preview/credential").json()["held"] is True
        assert env.client.delete("/api/databases/Coco/preview/credential").status_code == 200
        assert env.client.get("/api/databases/coco/preview/credential").json()["held"] is False

    def test_delete_of_an_unknown_database_is_404(self, env):
        assert env.client.delete("/api/databases/nope/preview/credential").status_code == 404


class TestLargeValuesAreCutInTheDatabase:
    """Finding 10. The cut is in the SQL, composed from Identifier and Literal only."""

    @staticmethod
    def _text(q):
        return "".join(p.string for p in _flat(q) if isinstance(p, sql.SQL))

    def test_a_text_like_column_is_selected_as_left_501(self):
        q = PostgreSQLConnection.build_preview_query("s", "t", ["note", "doc"], 20, {"note": "text", "doc": "jsonb"})
        parts = _flat(q)
        assert self._text(q).count("left(") == 2 and "::text" in self._text(q)
        assert [p.wrapped for p in parts if isinstance(p, sql.Literal)].count(501) == 2

    def test_an_unknown_or_missing_type_is_cut_as_well(self):
        for types in (None, {}, {"c": ""}, {"c": "some_domain_type"}, {"c": "text[]"}):
            q = PostgreSQLConnection.build_preview_query("s", "t", ["c"], 20, types)
            assert "left(" in self._text(q)

    def test_bytea_leaves_the_database_as_its_length_only(self):
        q = PostgreSQLConnection.build_preview_query("s", "t", ["blob"], 20, {"blob": "bytea"})
        parts = _flat(q)
        assert "octet_length(" in self._text(q) and "left(" not in self._text(q)
        assert [p.wrapped for p in parts if isinstance(p, sql.Literal) and isinstance(p.wrapped, str)] == ["<", " bytes>"]

    def test_the_catalogue_type_does_not_decide_the_fetch_size(self):
        """N4. A column catalogued as small may have become text since the survey: every column is cut."""
        types = {"a": "timestamp(6)", "b": "INTEGER", "n": "integer", "d": "date", "u": "uuid"}
        q = PostgreSQLConnection.build_preview_query("s", "t", list(types), 20, types)
        assert self._text(q).count("left(") == len(types) and "octet_length" not in self._text(q)
        assert [p.wrapped for p in _flat(q) if isinstance(p, sql.Literal)].count(501) == len(types)

    def test_no_request_input_enters_the_statement_except_as_identifier_or_literal(self):
        evil = "x'; DROP TABLE t; --"
        q = PostgreSQLConnection.build_preview_query(evil, evil, [evil, "b"], 20, {evil: evil, "b": "bytea"})
        assert "DROP" not in self._text(q) and "'" not in self._text(q)
        literals = [p.wrapped for p in _flat(q) if isinstance(p, sql.Literal)]
        assert evil not in literals

    def test_hostile_names_stay_inside_identifiers_with_the_cut_in_place(self):
        evil = 'x"; DROP TABLE t; --'
        q = PostgreSQLConnection.build_preview_query(evil, evil, [evil], 100, {evil: "text"})
        text = self._text(q)
        assert "DROP" not in text and ";" not in text
        assert all(p.strings == (evil,) for p in _flat(q) if isinstance(p, sql.Identifier))

    def test_only_identifiers_literals_and_fixed_fragments_make_up_the_statement(self):
        q = PostgreSQLConnection.build_preview_query("s", "t", ["a", "b"], 20, {"a": "text", "b": "bytea"})
        assert all(isinstance(p, (sql.SQL, sql.Identifier, sql.Literal)) for p in _flat(q))

    def test_the_route_passes_the_catalogue_types_to_the_connection(self, env):
        _hold(env)
        _preview(env, reveal=["email"])
        assert env.spy.types[-1] == {"id": "text", "email": "text"}

    def test_the_display_cut_is_still_500_with_a_mark(self):
        assert tp._cell("x" * 501) == "x" * 500 + "…" and tp._cell("x" * 500) == "x" * 500


class TestOneNewRevealPerRequest:
    """Finding 11. The baseline is the last audit row of the same person's preview of this table."""

    def test_two_new_columns_in_one_request_are_refused_before_any_query(self, env):
        _hold(env)
        r = _preview(env, reveal=["email", "note"])
        assert r.status_code == 422 and "one column at a time" in r.json()["detail"]
        assert env.spy.asked == []

    def test_one_at_a_time_climbs_and_a_fresh_preview_starts_again(self, env):
        _hold(env)
        assert _preview(env, reveal=["email"]).status_code == 200
        assert _preview(env, reveal=["email", "note"]).status_code == 200      # one NEW beyond the last audit row
        assert _preview(env, reveal=["email", "note", "qty"]).status_code == 200
        assert _preview(env).status_code == 200                                 # a plain preview records no reveal
        assert _preview(env, reveal=["email", "note"]).status_code == 422       # two new again
        assert _preview(env, reveal=["note"]).status_code == 200

    def test_another_person_has_their_own_baseline(self, env, monkeypatch):
        _hold(env)
        _preview(env, reveal=["email"])
        monkeypatch.setattr("resource_explorer.auth.get_current_user",
                            lambda request: {"user_id": "mandy", "exp": 4102444800})
        _hold(env)
        assert _preview(env, reveal=["email", "note"]).status_code == 422


class TestRecordInEgeriaHook:
    """`record_in_egeria` is a named extension point: by default it makes no Egeria call and reports a plain state."""

    def test_default_hook_makes_no_egeria_call_and_mutates_nothing(self):
        import copy
        import socket
        entry = {"id": 7, "operation": "preview", "ts": "2026-10-20T00:00:00Z"}
        before = copy.deepcopy(entry)
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network call")), \
                mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network call")):
            out = tp.record_in_egeria(entry)
        assert out == {"state": "not_built"} and entry == before

    def test_hook_state_is_reported_as_a_state_never_as_success(self, env):
        _hold(env)
        out = _preview(env).json()
        assert out["egeria"]["state"] == "not_built" and out["egeria"]["state"] not in ("recorded", "ok", "success")
        assert out["recorded"]["by"] == "RE"            # RE's own audit row is the record

    def test_hook_doc_states_it_is_an_extension_point(self):
        for text in (tp.__doc__, tp.record_in_egeria.__doc__):
            flat = text.replace("\n", " ").lower()
            assert "hook" in flat and "writes nothing to egeria" in flat


# ── fix round 3 (L1, L2, L3, L6) ───────────────────────────────────────────────────────────────────────────────

class TestReadOnlyDoesNotDependOnAutocommit:
    def test_autocommit_on_is_refused_before_any_statement(self):
        c = _pg(rows=[(1,)])
        c._conn.autocommit = True
        with pytest.raises(PreviewError) as ei:
            c.preview_rows("s", "t", ["id"], 20)
        assert "read-only" in ei.value.sentence
        assert c._conn.log == []                       # no rollback, no SET, no SELECT ran

    def test_autocommit_off_still_runs(self):
        c = _pg(rows=[(1,)])
        c._conn.autocommit = False
        assert c.preview_rows("s", "t", ["id"], 20) == [{"id": 1}]


class TestFunctionsAreSchemaQualified:
    def test_left_and_octet_length_are_pg_catalog_qualified(self):
        q = PostgreSQLConnection.build_preview_query("s", "t", ["a", "b"], 20, {"a": "text", "b": "bytea"})
        text = "".join(p.string for p in _flat(q) if isinstance(p, sql.SQL))
        assert "pg_catalog.left(" in text and "pg_catalog.octet_length(" in text
        import re as _re
        assert not _re.search(r"(?<![\w.])left\(", text) and not _re.search(r"(?<![\w.])octet_length\(", text)


class TestColumnCap:
    def test_the_cap_is_200(self):
        assert tp.MAX_PREVIEW_COLUMNS == 200

    def test_columns_past_the_cap_are_not_selected_and_are_counted(self, env, monkeypatch):
        _hold(env)
        monkeypatch.setattr(tp, "MAX_PREVIEW_COLUMNS", 3)
        out = _preview(env).json()
        assert len(out["columns"]) == 3 and out["columns_not_shown"] == 2
        assert len(env.spy.asked[-1][2]) <= 3
        assert out["counts"]["total"] == 5                  # the count line still speaks for the whole table

    def test_under_the_cap_nothing_is_hidden(self, env):
        _hold(env)
        assert _preview(env).json()["columns_not_shown"] == 0

    def test_a_reveal_of_a_column_past_the_cap_is_refused(self, env, monkeypatch):
        _hold(env)
        monkeypatch.setattr(tp, "MAX_PREVIEW_COLUMNS", 1)
        assert _preview(env, reveal=["email"]).status_code == 422


class TestFailingHookNeverBlocksTheRows:
    def test_a_hook_that_raises_gives_state_failed_and_still_returns_rows(self, env, monkeypatch, caplog):
        _hold(env)
        monkeypatch.setattr(tp, "record_in_egeria", mock.Mock(side_effect=RuntimeError(f"boom {VALUE}")))
        r = _preview(env)
        assert r.status_code == 200
        out = r.json()
        assert out["outcome"] == "rows" and out["row_count"] == 3 and out["egeria"] == {"state": "failed"}
        assert out["recorded"]["state"] == "recorded"
        assert VALUE not in caplog.text and "RuntimeError" in caplog.text

    def test_a_hook_that_raises_on_the_error_and_empty_paths_too(self, env, monkeypatch):
        _hold(env)
        monkeypatch.setattr(tp, "record_in_egeria", mock.Mock(side_effect=RuntimeError("x")))
        env.spy.conn.preview_rows.side_effect = lambda *a, **k: []
        r = _preview(env)
        assert r.status_code == 200 and r.json()["outcome"] == "empty" and r.json()["egeria"] == {"state": "failed"}
        env.spy.conn.preview_rows.side_effect = PreviewDenied("permission denied for table orders")
        r = _preview(env)
        assert r.status_code == 200 and r.json()["egeria"] == {"state": "failed"}

    def test_a_hook_that_answers_nonsense_is_failed_not_trusted(self, env, monkeypatch):
        _hold(env)
        monkeypatch.setattr(tp, "record_in_egeria", lambda entry: "done")
        assert _preview(env).json()["egeria"] == {"state": "failed"}
