"""Registering a database's SERVER with Egeria, optionally (catalog_and_survey.py).

Recording fakes only: a fake Egeria port that records every call and the placeholders it
was given, a scripted engine-action sequence, and a fake read-back. No live Egeria, no live
registry. The rules under test:

* the placeholders are exactly Egeria's, mapped from the stored record, and NO password is in
  anything recorded (requests, logs, activity rows, proof rows);
* every write is followed by a read of that GUID before a pointer is stored;
* adopt by qualifiedName: never a second server for the next database on the same host:port;
* a stale pointer is detected by a read and said plainly; a failed read is not "gone";
* a database never registered is a NEUTRAL state, not an error;
* Egeria's own sentence is shown verbatim, plus one line when it reads as a connection problem.
"""
from __future__ import annotations

import json
import logging

import pytest

from resource_explorer import catalog_and_survey as cas
from resource_explorer import native_survey_run as nsr
from resource_explorer.catalogue_gateway import database_qualified_name, server_qualified_name
from resource_explorer.registry import DatabaseEntity, ProjectRegistry
from resource_explorer.surveyors import technology_type_processes as ttp
from tests.test_native_survey_run import CATALOG_QN, FakePort, ann

TECH = "PostgreSQL Relational Database"
PASSWORD = "S3cretPW!-do-not-leak"
SERVER_SURVEY_QN = "PostgreSQLSurvey::survey-postgres-server"
SERVER_NAME = "host.docker.internal:5432"
SQN = server_qualified_name(SERVER_NAME)
DQN = database_qualified_name(SERVER_NAME, "adventureworks")
SERVER_GUID = "55555555-0000-0000-0000-000000000001"
DB_GUID = "dddddddd-0000-0000-0000-000000000001"
SURVEY_ACTION = "ffffffff-0000-0000-0000-000000000001"


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.register_database(DatabaseEntity(
        slug="adventureworks", display_name="AdventureWorks", db_type="postgresql",
        host="localhost", port=5432, database_name="adventureworks",
        egeria_host="host.docker.internal", db_user="dwolfson", db_password=PASSWORD))
    reg.register_database(DatabaseEntity(
        slug="sibling", display_name="Sibling", db_type="postgresql",
        host="localhost", port=5432, database_name="sibling",
        egeria_host="host.docker.internal", db_user="dwolfson", db_password=PASSWORD))
    return reg


class RegPort(FakePort):
    """FakePort plus the registration surface. Records every call and every body."""

    def __init__(self):
        super().__init__()
        self.assets = set()
        self.elements: dict[str, str] = {}          # guid -> qualifiedName
        self.created_servers: list[dict] = []
        self.processes: list[tuple[str, dict]] = []
        self.survey_actions: dict[str, list[str]] = {}
        self.create_error: Exception | None = None
        self.process_error: Exception | None = None
        self.read_element_override = "unset"
        self.find_error: Exception | None = None
        self.hide_from_find: set[str] = set()      # names find_element cannot see (read-by-GUID still can)
        self.on_first_absent = None                # called once, when the server lookup is about to answer ABSENT

    def have(self, guid, qn):
        self.elements[guid] = qn
        self.assets.add(guid)

    def find_element(self, qn):
        self.calls.append(("find_element", qn))
        if self.find_error:
            raise self.find_error
        hit = None
        if qn not in self.hide_from_find:
            for g, name in self.elements.items():
                if name == qn:
                    hit = cas.ElementBack(g, name)
        if hit is None and qn == SQN and self.on_first_absent:
            cb, self.on_first_absent = self.on_first_absent, None
            cb()                                    # someone else registers it BEFORE this caller sees "absent"
        return hit

    def read_element(self, guid):
        self.calls.append(("read_element", guid))
        if self.read_element_override != "unset":
            return self.read_element_override
        qn = self.elements.get(guid)
        return cas.ElementBack(guid, qn) if qn else None

    def create_server(self, placeholders):
        self.calls.append(("create_server", dict(placeholders)))
        if self.create_error:
            raise self.create_error
        self.created_servers.append(dict(placeholders))
        self.have(SERVER_GUID, SQN)
        return SERVER_GUID

    def initiate_process(self, qn, params):
        self.calls.append(("initiate_process", qn, dict(params)))
        if self.process_error:
            raise self.process_error
        self.processes.append((qn, dict(params)))
        guid = f"99999999-0000-0000-0000-{len(self.processes):012d}"
        self.actions[guid] = ("IN_PROGRESS", "")
        return guid

    def read_process(self, guid):
        self.calls.append(("read_process", guid))
        status, message = self.actions[guid]
        return nsr.ActionRead(status=status, message=message)

    def survey_actions_for(self, guid):
        self.calls.append(("survey_actions_for", guid))
        return list(self.survey_actions.get(guid, []))


def register(registry, port, slug="adventureworks"):
    return cas.register_with_egeria(registry, port, "database", slug, technology_type=TECH,
                                    submitted_by="dan")


def pointer(registry, slug="adventureworks"):
    return registry.get_database(slug).egeria_asset_guid


# ── the one function that says whether a kind has a separate server element ──

class TestServerElementDecision:
    def test_postgresql_has_one(self):
        assert ttp.server_has_separate_element("database", TECH) is True

    def test_a_kind_the_config_does_not_cover_is_none_never_a_guess(self):
        assert ttp.server_has_separate_element("filesystem", "File System Directory") is None
        assert ttp.server_has_separate_element("database", "SQLite Embedded") is None

    def test_a_kind_marked_coincident_is_false_and_says_it_is_not_built(self, tmp_path, monkeypatch):
        cfg = tmp_path / "t.yaml"
        cfg.write_text("technology_types:\n  - name: X\n    entity_type: database\n"
                       "    separate_server_element: false\n    processes: []\n")
        wiring = ttp._load_wiring(cfg)
        assert wiring[("database", "X")].separate_server is False
        entity = DatabaseEntity(slug="a", display_name="a", db_type="sqlite", host="h", port=1,
                                database_name="d", db_user="u")
        # a coincident kind says the single-step variant is not built; it never falls into the server path
        monkeypatch.setattr(cas, "server_has_separate_element", lambda *a: False)
        assert cas.wiring_reason("database", "X", entity) == cas.SINGLE_STEP_NOT_BUILT

    def test_the_row_for_an_unwired_kind_says_this_kind_is_not_wired_yet(self, registry, monkeypatch):
        entity = registry.get_database("adventureworks")
        assert "not wired yet" in cas.wiring_reason("database", "SQLite Embedded", entity)

    def test_a_record_missing_what_the_template_needs_names_what(self):
        entity = DatabaseEntity(slug="a", display_name="a", db_type="postgresql", host="", port=5432,
                                database_name="d", db_user="")
        reason = cas.wiring_reason("database", TECH, entity)
        assert "host" in reason and "database user" in reason or "user" in reason


# ── the placeholders ─────────────────────────────────────────────────────────

class TestPlaceholders:
    def test_the_server_placeholders_are_exactly_egeria_server_template_list(self, registry):
        ph = cas.server_placeholders(registry.get_database("adventureworks"))
        # PostgresPlaceholderProperty.getPostgresServerPlaceholderPropertyTypes()
        assert set(ph) == {"hostIdentifier", "portNumber", "serverName", "description",
                           "versionIdentifier", "resourceName", "secretsStorePathName",
                           "secretsCollectionName"}
        assert ph["hostIdentifier"] == "host.docker.internal" and ph["portNumber"] == "5432"
        assert ph["serverName"] == SERVER_NAME == ph["resourceName"]
        assert ph["secretsCollectionName"] == "adventureworks::PostgreSQL Secret"

    def test_the_database_placeholders_are_exactly_egeria_database_template_list(self, registry):
        ph = cas.database_placeholders(registry.get_database("adventureworks"))
        # PostgresPlaceholderProperty.getPostgresDatabasePlaceholderPropertyTypes()
        assert set(ph) == {"hostIdentifier", "portNumber", "serverName", "versionIdentifier",
                           "databaseName", "databaseDescription", "secretsStorePathName",
                           "secretsCollectionName"}
        assert ph["databaseName"] == "adventureworks" and ph["serverName"] == SERVER_NAME

    def test_there_is_no_password_or_user_placeholder(self, registry):
        for ph in (cas.server_placeholders(registry.get_database("adventureworks")),
                   cas.database_placeholders(registry.get_database("adventureworks"))):
            assert PASSWORD not in json.dumps(ph)
            assert not {"databasePassword", "databaseUserId", "clearPassword", "userId"} & set(ph)


# ── the press ────────────────────────────────────────────────────────────────

class TestRegister:
    def test_it_creates_the_server_reads_it_back_and_only_then_stores_the_pointer(self, registry):
        port = RegPort()
        register(registry, port)
        names = [c[0] for c in port.calls]
        assert names.index("create_server") < names.index("read_element")
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) == SERVER_GUID
        created = port.created_servers[0]
        assert created == cas.server_placeholders(registry.get_database("adventureworks"))

    def test_a_server_that_reads_back_wrong_stores_no_pointer(self, registry):
        port = RegPort()
        port.read_element_override = cas.ElementBack(SERVER_GUID, "PostgreSQL Server::someone-else")
        with pytest.raises(nsr.NativeSurveyError, match="not 'PostgreSQL Server::host.docker"):
            register(registry, port)
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) is None
        assert pointer(registry) == ""

    def test_a_server_that_does_not_read_back_stores_no_pointer(self, registry):
        port = RegPort()
        port.read_element_override = None
        with pytest.raises(nsr.NativeSurveyError, match="no server with the GUID"):
            register(registry, port)
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) is None

    def test_the_server_survey_is_submitted_on_the_server_with_the_native_machinery(self, registry):
        port = RegPort()
        register(registry, port)
        init = [c for c in port.calls if c[0] == "initiate"]
        assert init == [("initiate", SERVER_SURVEY_QN, "serverToSurvey", SERVER_GUID)]
        runs = registry.list_native_survey_runs("database", "adventureworks", SERVER_SURVEY_QN)
        assert runs and runs[0]["engine_action_guid"].startswith("eeeeeeee")

    def test_the_database_process_gets_the_database_placeholders_and_is_recorded(self, registry):
        port = RegPort()
        out = register(registry, port)
        (qn, params), = port.processes
        assert qn == CATALOG_QN
        assert params == cas.database_placeholders(registry.get_database("adventureworks"))
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        assert run["engine_action_guid"] == out["database"]["process_instance_guid"]
        assert run["submitted_by"] == "dan"
        # the database is NOT pointed at until Egeria shows it and it is read back
        assert pointer(registry) == ""

    def test_when_egeria_shows_the_database_it_is_read_back_by_guid_before_the_pointer(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.have(DB_GUID, DQN)
        port.survey_actions[DB_GUID] = [SURVEY_ACTION]
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        before = len(port.calls)
        state = cas.refresh_registration_run(registry, port, run, entity_type="database",
                                             slug="adventureworks")
        after = [c for c in port.calls[before:]]
        assert [c[0] for c in after].index("read_element") >= 0
        assert pointer(registry) == DB_GUID
        assert state["state"] == cas.REGISTERED
        # the survey Egeria's process started now shows under the ordinary database-survey row
        sruns = registry.list_native_survey_runs("database", "adventureworks", cas.DATABASE_SURVEY_QN)
        assert [r["engine_action_guid"] for r in sruns] == [SURVEY_ACTION]
        # reading again records nothing twice
        cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
        assert len(registry.list_native_survey_runs(
            "database", "adventureworks", cas.DATABASE_SURVEY_QN)) == 1

    def test_a_database_found_by_name_that_does_not_read_back_stores_no_pointer(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.have(DB_GUID, DQN)
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        port.read_element_override = None            # found by name, but the GUID read says there is none
        state = cas.refresh_registration_run(registry, port, run, entity_type="database",
                                             slug="adventureworks")
        assert pointer(registry) == "" and state["state"] == cas.AWAITING_REGISTRATION
        row = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        assert "database read failed" in row["engine_action_read_error"]

    def test_ambiguous_survey_actions_are_not_chosen_among(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.have(DB_GUID, DQN)
        port.survey_actions[DB_GUID] = [SURVEY_ACTION, "ffffffff-0000-0000-0000-000000000002"]
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
        assert registry.list_native_survey_runs("database", "adventureworks", cas.DATABASE_SURVEY_QN) == []

    def test_a_completed_process_with_no_database_in_egeria_is_awaiting_not_registered(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        state = cas.refresh_registration_run(registry, port, run, entity_type="database",
                                             slug="adventureworks")
        assert state["state"] == cas.AWAITING_REGISTRATION and pointer(registry) == ""


class TestAdoptByQualifiedName:
    def test_an_existing_server_is_adopted_not_created(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        out = register(registry, port)
        assert out["server"]["how"] == "adopted" and port.created_servers == []

    def test_the_next_database_on_the_same_server_makes_no_second_server(self, registry):
        port = RegPort()
        register(registry, port, "adventureworks")
        register(registry, port, "sibling")
        assert len(port.created_servers) == 1
        assert [n for g, n in port.elements.items() if n.startswith("PostgreSQL Server::")] == [SQN]

    def test_an_existing_database_is_adopted_and_no_process_is_submitted(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        port.have(DB_GUID, DQN)
        out = register(registry, port)
        assert out["database"]["how"] == "adopted" and port.processes == []
        assert pointer(registry) == DB_GUID

    def test_registering_twice_does_not_resubmit_while_in_flight(self, registry):
        port = RegPort()
        register(registry, port)
        with pytest.raises(nsr.NativeSurveyBusy):
            register(registry, port)
        assert len(port.processes) == 1


class TestFailureSaysEgeriasSentence:
    def test_a_refused_process_submission_is_egerias_sentence_and_writes_no_database_pointer(self, registry):
        port = RegPort()
        port.process_error = refusal("OMAG-GOVERNANCE-400-011 The process is not recognized")
        with pytest.raises(nsr.NativeSurveyError) as exc:
            register(registry, port)
        assert "OMAG-GOVERNANCE-400-011 The process is not recognized" in str(exc.value)
        assert pointer(registry) == ""
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        assert run["engine_action_guid"] == "" and "not recognized" in run["submit_error"]
        # what was proven before the failure stays proven: the server was read back and pointed at
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) == SERVER_GUID

    def test_a_failed_server_create_says_egerias_sentence_and_writes_nothing(self, registry):
        port = RegPort()
        port.create_error = RuntimeError("OMAG-REPOSITORY-500 template boom")
        with pytest.raises(nsr.NativeSurveyError, match="template boom"):
            register(registry, port)
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) is None
        assert port.processes == []

    def test_egerias_sentence_is_not_cut(self, registry):
        port = RegPort()
        long = "x" * 900 + " FATAL: role \"surveyor\" does not exist"
        port.process_error = refusal(long)
        with pytest.raises(nsr.NativeSurveyError) as exc:
            register(registry, port)
        assert str(exc.value).endswith('role "surveyor" does not exist')

    def test_a_server_survey_refused_does_not_undo_the_registration(self, registry):
        port = RegPort()
        port.initiate_error = RuntimeError("survey service not recognized")
        out = register(registry, port)
        assert "not recognized" in out["server_survey_error"]
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) == SERVER_GUID


# ── a connection problem: Egeria's sentence verbatim plus one line ───────────

class TestReachNote:
    def _failed(self, message):
        return {"state": nsr.FAILED, "message": message, "error": ""}

    def test_a_refused_connection_gets_the_one_extra_line(self):
        state = self._failed("Connection to host.docker.internal:5432 refused")
        assert cas.reach_note_for(state) == (
            "Egeria connects from its own platform; RE can still survey this database itself")

    def test_a_non_connection_failure_gets_none(self):
        assert cas.reach_note_for(self._failed("The annotation store rejected a duplicate")) == ""

    def test_a_row_carries_egerias_sentence_unchanged_and_the_note(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        register(registry, port)
        sruns = registry.list_native_survey_runs("database", "adventureworks", SERVER_SURVEY_QN)
        guid = sruns[0]["engine_action_guid"]
        sentence = "Connection refused (UnknownHostException host.docker.internal)"
        port.actions[guid] = ("FAILED", sentence)
        nsr.refresh_resource(registry, port, "database", "adventureworks")
        rows = {r["qualified_name"]: r
                for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        row = rows[SERVER_SURVEY_QN]
        assert row["run"]["message"] == sentence
        assert row["reach_note"] == cas.REACH_NOTE
        # RE's own survey is unaffected: the database-survey row is as it was, not run, no note
        assert rows[cas.DATABASE_SURVEY_QN]["reach_note"] == ""
        assert rows[cas.DATABASE_SURVEY_QN]["run"]["state"] == nsr.NOT_RUN


# ── never registered: neutral; stale: said plainly ───────────────────────────

class TestNeutralAndStale:
    def test_a_never_registered_database_is_neutral_not_an_error(self, registry):
        rows = {r["qualified_name"]: r
                for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        survey = rows[cas.DATABASE_SURVEY_QN]
        assert survey["neutral"] is True and survey["stale"] is False
        assert survey["cannot_run_reason"] == cas.NOT_REGISTERED_WORDS
        assert "optional" in survey["cannot_run_reason"]
        assert survey["run"]["state"] == nsr.NOT_RUN and survey["run"]["error"] == ""
        assert survey["register"]["available"] is False       # the one control is on the catalog row
        catalog = rows[CATALOG_QN]
        assert catalog["run"]["state"] == cas.NOT_REGISTERED
        assert catalog["register"]["available"] is True
        assert not catalog["in_flight"]
        server = rows[SERVER_SURVEY_QN]
        assert server["neutral"] and server["cannot_run_reason"] == cas.SERVER_NOT_REGISTERED_WORDS

    def test_a_pointer_egeria_says_is_gone_is_stale_and_offers_the_control(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        port = RegPort()
        pointers = cas.check_pointers(registry, port, "database", "adventureworks")
        assert pointers["database"] == "gone"
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(
            registry, "database", "adventureworks", TECH, pointers=pointers)}
        survey = rows[cas.DATABASE_SURVEY_QN]
        assert survey["stale"] is True and survey["neutral"] is False
        assert survey["cannot_run_reason"] == cas.STALE_WORDS == (
            "the Egeria asset RE had stored no longer exists")
        assert survey["register"]["available"] and survey["register"]["label"] == cas.REGISTER_AGAIN_LABEL
        # Designer Q10: "no longer in Egeria · was registered … as <old guid>", the old GUID kept in the words.
        assert survey["gone_line"].startswith("no longer in Egeria · was registered") and DB_GUID[:8] in survey["gone_line"]
        assert rows[CATALOG_QN]["gone_line"] == survey["gone_line"]
        assert rows[CATALOG_QN]["register"]["label"] == cas.REGISTER_AGAIN_LABEL
        assert rows[CATALOG_QN]["register"]["start_again"] is False         # nothing exists to duplicate: one press
        assert rows[CATALOG_QN]["stale"] is True and rows[CATALOG_QN]["run"]["state"] == cas.NOT_REGISTERED

    def test_a_failed_read_is_unreadable_never_gone(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        port = RegPort()

        def boom(guid):
            raise RuntimeError("Egeria is unreachable")
        port.asset_exists = boom
        pointers = cas.check_pointers(registry, port, "database", "adventureworks")
        assert pointers["database"] == "unreadable" and "unreachable" in pointers["error"]
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(
            registry, "database", "adventureworks", TECH, pointers=pointers)}
        assert rows[cas.DATABASE_SURVEY_QN]["stale"] is False

    def test_a_pointer_that_resolves_is_not_stale(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        port = RegPort()
        port.assets.add(DB_GUID)
        assert cas.check_pointers(registry, port, "database", "adventureworks")["database"] == "resolves"

    def test_running_a_survey_on_a_gone_asset_is_the_stale_error_and_submits_nothing(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        port = RegPort()
        with pytest.raises(nsr.NativeSurveyStale, match="no longer exists"):
            nsr.submit_native_survey(registry, port, "database", "adventureworks",
                                     cas.DATABASE_SURVEY_QN, technology_type=TECH)
        assert not [c for c in port.calls if c[0] == "initiate"]

    def test_the_old_sentence_and_the_old_locked_wording_are_gone_from_the_source(self):
        import pathlib
        root = pathlib.Path(nsr.__file__).parent
        text = "".join(p.read_text() for p in (root / "native_survey_run.py", root / "catalog_and_survey.py",
                                                root / "web/routes/native_surveys.py"))
        text += (root / "configdata/technology_type_processes.yaml").read_text()
        assert "Publish the resource to Egeria again" not in text
        assert "does not collect those template" not in text
        assert "RE cannot run this one" not in text


# ── credentials never travel ─────────────────────────────────────────────────

class TestNoPasswordAnywhere:
    def test_the_password_is_in_no_request_no_log_no_proof_row(self, registry, caplog):
        caplog.set_level(logging.DEBUG)
        port = RegPort()
        port.process_error = refusal(f"jdbc failed for password {PASSWORD}")
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        recorded = json.dumps(port.calls, default=str)
        assert PASSWORD not in recorded
        assert PASSWORD not in caplog.text
        with registry._conn() as conn:
            dump = json.dumps([dict(r) for r in conn.execute("SELECT * FROM step_runs").fetchall()],
                              default=str)
            settings = json.dumps([dict(r) for r in conn.execute("SELECT * FROM app_settings").fetchall()],
                                  default=str)
        assert PASSWORD not in dump and PASSWORD not in settings

    def test_an_absent_collection_with_a_writable_path_is_filled_by_the_existing_projection(
            self, registry, tmp_path, monkeypatch):
        path = str(tmp_path / "s.omsecrets")
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: path)
        port = RegPort()
        register(registry, port)
        from resource_explorer import omsecrets_store
        assert omsecrets_store.has_collection("adventureworks::PostgreSQL Secret", path=path)
        assert PASSWORD not in json.dumps(port.calls, default=str)

    def test_an_absent_collection_that_cannot_be_filled_refuses_with_the_exact_reason(
            self, registry, tmp_path, monkeypatch):
        path = str(tmp_path / "s.omsecrets")
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: path)
        monkeypatch.setattr("resource_explorer.omsecrets_reproject.reproject", lambda *a, **k: [])
        port = RegPort()
        with pytest.raises(cas.RegistrationRefused, match="no credentials for this database"):
            register(registry, port)
        assert port.calls == []


# ── the databases a server survey found ──────────────────────────────────────

class TestDiscoveredDatabases:
    def _complete_server_survey(self, registry, port):
        port.have(SERVER_GUID, SQN)
        register(registry, port)
        guid = registry.list_native_survey_runs("database", "adventureworks", SERVER_SURVEY_QN)[0][
            "engine_action_guid"]

        def ann(i, name, type_="Capture Database Measurements"):
            return {"guid": f"a{i}", "annotation_type": type_, "summary": "s",
                    "detail": {"qualified_name": f"Annotation::{type_}::{name}::"
                               "11111111-2222-3333-4444-555555555555",
                               "resource_properties": {"Database Name": name} if i != 3 else {}}}
        port.finish(guid, report="rep-s", annotations=[
            ann(1, "adventureworks"), ann(2, "sibling"), ann(3, "unknown_db"),
            {"guid": "a9", "annotation_type": "Capture Database Table Measurements", "summary": "t",
             "detail": {"qualified_name": "Annotation::T::notadb::11111111-2222-3333-4444-555555555555"}}])
        nsr.refresh_resource(registry, port, "database", "adventureworks")

    def test_names_are_exactly_what_egeria_reported_and_only_from_database_annotations(self, registry):
        port = RegPort()
        self._complete_server_survey(registry, port)
        rows = {r["qualified_name"]: r
                for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        found = {d["name"]: d for d in rows[SERVER_SURVEY_QN]["discovered"]}
        assert set(found) == {"adventureworks", "sibling", "unknown_db"}      # not "notadb"
        # one RE has registered and Egeria has no asset for: found, not yet cataloged, with the control
        assert found["sibling"]["state"] == "found" and found["sibling"]["re_slug"] == "sibling"
        assert found["sibling"]["control"] == cas.REGISTER_LABEL
        # one RE does not have: reported, no control (the slice does not widen into registering it)
        assert found["unknown_db"]["re_slug"] == "" and found["unknown_db"]["control"] == ""

    def test_a_database_with_an_egeria_pointer_is_cataloged(self, registry):
        port = RegPort()
        registry.set_database_egeria_guid("sibling", DB_GUID)
        self._complete_server_survey(registry, port)
        rows = {r["qualified_name"]: r
                for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        found = {d["name"]: d for d in rows[SERVER_SURVEY_QN]["discovered"]}
        assert found["sibling"]["state"] == "cataloged" and found["sibling"]["control"] == ""

    def test_before_a_complete_server_survey_nothing_is_listed_as_found(self, registry):
        rows = {r["qualified_name"]: r
                for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        assert rows[SERVER_SURVEY_QN]["discovered"] == []


# ── the sweep and the poll leave a finished registration alone ───────────────

class TestSweep:
    def test_a_registered_database_costs_no_egeria_call_on_a_sweep(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.have(DB_GUID, DQN)
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        nsr.refresh_run(registry, port, run, entity_type="database", slug="adventureworks")
        port.calls.clear()
        # the server survey is still in flight; the registration is finished and is not re-read
        nsr.sweep_in_flight(registry, port)
        assert not [c for c in port.calls if c[0] == "read_process"]


# ═══ review round: duplicates, credentials, wording ═══════════════════════════

from resource_explorer.registry import ProjectRegistry  # noqa: E402


class FakeExpert:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def get_metadata_element_by_unique_name(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.result

    def get_metadata_element_by_guid(self, guid):
        self.calls.append(guid)
        if self.error:
            raise self.error
        return self.result


from pyegeria.core._globals import NO_ELEMENT_FOUND, NO_ELEMENTS_FOUND  # noqa: E402  -- pyegeria's REAL constants

ABSENT_ANSWER = NO_ELEMENTS_FOUND      # what a by-name / by-GUID miss actually returns


def real_port(expert):
    p = cas.PyegeriaRegistrationPort()
    p._enter = lambda: None
    p._get_expert = lambda: expert
    return p


def typed_not_found():
    from pyegeria.core._exceptions import PyegeriaNotFoundException
    return PyegeriaNotFoundException.__new__(PyegeriaNotFoundException)


class TestAbsentIsOnlyTypedOrExact:
    def test_a_typed_not_found_is_absent(self):
        assert real_port(FakeExpert(error=typed_not_found())).find_element(SQN) is None

    def test_the_exact_no_element_found_answer_is_absent(self):
        assert real_port(FakeExpert(result=ABSENT_ANSWER)).find_element(SQN) is None

    @pytest.mark.parametrize("sentence", ["user erinoverview not found", "type PostgreSQL Server not found",
                                          "search index not found", "404 gateway"])
    def test_any_other_error_refuses_with_egerias_sentence(self, sentence):
        with pytest.raises(Exception) as exc:
            real_port(FakeExpert(error=RuntimeError(sentence))).find_element(SQN)
        assert sentence in str(exc.value)

    def test_a_404_api_exception_by_code_is_absent_but_404_in_a_timeout_message_is_not(self):
        from pyegeria.core._exceptions import PyegeriaAPIException
        gone = PyegeriaAPIException.__new__(PyegeriaAPIException)
        gone.related_http_code = 404
        assert real_port(FakeExpert(error=gone)).read_element(DB_GUID) is None
        timeout = PyegeriaAPIException.__new__(PyegeriaAPIException)
        timeout.related_http_code = 504
        with pytest.raises(Exception):
            real_port(FakeExpert(error=timeout)).read_element(DB_GUID)
        guid_404 = "0a404b3c-1111-2222-3333-444444444404"
        with pytest.raises(Exception, match="timed out"):
            real_port(FakeExpert(error=TimeoutError(f"read of {guid_404} timed out"))).read_element(guid_404)
        unauthorized = PyegeriaAPIException.__new__(PyegeriaAPIException)
        unauthorized.related_http_code = 401
        with pytest.raises(Exception):
            real_port(FakeExpert(error=unauthorized)).find_element(SQN)

    def test_any_other_string_answer_refuses(self):
        with pytest.raises(nsr.NativeSurveyError, match="entity type not found"):
            real_port(FakeExpert(result="entity type not found")).find_element(SQN)

    def test_read_element_by_guid_follows_the_same_rule(self):
        assert real_port(FakeExpert(error=typed_not_found())).read_element(DB_GUID) is None
        assert real_port(FakeExpert(result=ABSENT_ANSWER)).read_element(DB_GUID) is None
        with pytest.raises(Exception, match="user x not found"):
            real_port(FakeExpert(error=RuntimeError("user x not found"))).read_element(DB_GUID)

    def test_asset_exists_reads_a_non_raising_no_element_found_as_absent(self):
        assert real_port(FakeExpert(result=ABSENT_ANSWER)).asset_exists(DB_GUID) is False
        assert real_port(FakeExpert(result={"elementGUID": DB_GUID})).asset_exists(DB_GUID) is True
        with pytest.raises(nsr.NativeSurveyError):
            real_port(FakeExpert(result="something odd")).asset_exists(DB_GUID)

    def test_an_unrelated_not_found_error_creates_no_server(self, registry):
        port = RegPort()
        port.find_error = RuntimeError("user erinoverview not found")
        with pytest.raises(nsr.NativeSurveyError, match="user erinoverview not found"):
            register(registry, port)
        assert port.created_servers == [] and port.processes == []


class TestCreatedButNotConfirmed:
    def _press_with_failed_readback(self, registry, port):
        port.hide_from_find = {SQN}
        port.read_element_override = None
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)

    def test_the_created_guid_is_recorded_and_no_server_pointer_is_stored(self, registry):
        port = RegPort()
        self._press_with_failed_readback(registry, port)
        assert len(port.created_servers) == 1
        assert registry.get_setting(cas.server_unconfirmed_key(SERVER_NAME)) == SERVER_GUID
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) is None

    def test_the_row_says_created_not_yet_confirmed_and_offers_the_press(self, registry):
        port = RegPort()
        self._press_with_failed_readback(registry, port)
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        assert "created, not yet confirmed: press to confirm" in rows[CATALOG_QN]["notes"]
        assert rows[CATALOG_QN]["register"]["available"] is True

    def test_the_second_press_adopts_that_guid_and_creates_nothing(self, registry):
        port = RegPort()
        self._press_with_failed_readback(registry, port)
        port.read_element_override = "unset"            # Egeria now reads it back
        out = register(registry, port)
        assert len(port.created_servers) == 1
        assert out["server"]["how"] == "adopted" and out["server"]["guid"] == SERVER_GUID
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) == SERVER_GUID
        assert not registry.get_setting(cas.server_unconfirmed_key(SERVER_NAME))

    def test_an_unconfirmed_guid_under_another_name_is_refused_and_never_recreated(self, registry):
        port = RegPort()
        self._press_with_failed_readback(registry, port)
        port.read_element_override = "unset"
        port.elements[SERVER_GUID] = "PostgreSQL Server::derived-differently"
        with pytest.raises(nsr.NativeSurveyError, match="derived-differently"):
            register(registry, port)
        assert len(port.created_servers) == 1
        assert registry.get_setting(cas.server_pointer_key(SERVER_NAME)) is None

    def test_an_unconfirmed_guid_that_truly_is_gone_may_be_created_again(self, registry):
        port = RegPort()
        self._press_with_failed_readback(registry, port)
        port.read_element_override = "unset"
        port.elements.pop(SERVER_GUID)                   # Egeria: typed not-found on the GUID
        port.hide_from_find = set()
        register(registry, port)
        assert len(port.created_servers) == 2


def age_run(registry, minutes=20):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).replace(tzinfo=None).isoformat()
    with registry._conn() as conn:
        conn.execute("UPDATE step_runs SET surveyed_at = ? WHERE step_key LIKE ?", (old, "%CreateAndSurvey%"))


class TestNoSecondDatabaseProcess:
    def _complete_but_unreadable(self, registry, port):
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
        cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
        return run

    def test_after_the_window_a_press_does_not_start_a_second_process(self, registry):
        port = RegPort()
        self._complete_but_unreadable(registry, port)
        age_run(registry)
        with pytest.raises(nsr.NativeSurveyBusy) as exc:
            register(registry, port)
        assert "Start again" in str(exc.value) and "99999999-0000-0000-0000-000000000001" in str(exc.value)
        assert len(port.processes) == 1

    def test_an_explicit_start_again_is_allowed_and_says_nothing_else(self, registry):
        port = RegPort()
        self._complete_but_unreadable(registry, port)
        age_run(registry)
        cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                 submitted_by="dan", start_again=True)
        assert len(port.processes) == 2

    def test_the_claim_is_taken_before_the_submit_so_a_crash_between_leaves_it(self, registry):
        port = RegPort()
        registry.set_setting(cas.claim_key("adventureworks"), "2026-10-08T00:00:00")   # a press that died
        with pytest.raises(nsr.NativeSurveyBusy, match="Start again"):
            register(registry, port)
        assert port.processes == []

    def test_a_claim_is_atomic_insert_if_absent(self, registry):
        assert cas.take_claim(registry, "adventureworks") is True
        assert cas.take_claim(registry, "adventureworks") is False

    def test_a_refused_submission_releases_the_claim(self, registry):
        port = RegPort()
        port.process_error = refusal("OMAG refused")
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        port.process_error = None
        register(registry, port)
        assert len(port.processes) == 1

    def test_a_failed_process_releases_the_claim_and_may_be_pressed_again(self, registry):
        port = RegPort()
        register(registry, port)
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        port.actions[run["engine_action_guid"]] = ("FAILED", "boom")
        cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
        register(registry, port)
        assert len(port.processes) == 2

    def test_the_stalled_row_says_what_the_earlier_run_is_and_offers_start_again(self, registry):
        port = RegPort()
        run = self._complete_but_unreadable(registry, port)
        age_run(registry)
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert row["register"]["available"] and row["register"]["label"] == cas.START_AGAIN_LABEL
        assert run["engine_action_guid"] in " ".join(row["notes"])
        # Designer Q10: the confirm names the risk, not just "press again".
        assert "hasn't finished" in row["register"]["confirm"]
        assert "second database element in Egeria" in row["register"]["confirm"]

    def test_a_database_found_by_name_needs_no_claim_and_resolves_one(self, registry):
        port = RegPort()
        registry.set_setting(cas.claim_key("adventureworks"), "x")
        port.have(SERVER_GUID, SQN)
        port.have(DB_GUID, DQN)
        register(registry, port)
        assert cas.take_claim(registry, "adventureworks") is True


class TestServerConnectionCredentials:
    def test_the_creator_is_recorded_and_a_second_database_row_says_whose_credentials(self, registry):
        port = RegPort()
        register(registry, port, "adventureworks")
        assert registry.get_setting(cas.server_cred_key(SERVER_NAME)) == "adventureworks"
        register(registry, port, "sibling")
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "sibling", TECH)}
        assert any("uses adventureworks's credentials" in n for n in rows[SERVER_SURVEY_QN]["notes"])
        assert any("uses adventureworks's credentials" in n for n in rows[CATALOG_QN]["notes"])
        first = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        assert not any("uses" in n for n in first[CATALOG_QN]["notes"])

    def test_a_different_user_name_is_said_plainly_and_a_secret_is_never_read(self, registry):
        port = RegPort()
        register(registry, port, "adventureworks")
        sib = registry.get_database("sibling")
        sib.db_user = "someone_else"
        with registry._conn() as conn:
            conn.execute("UPDATE databases SET db_user = ? WHERE slug = 'sibling'", ("someone_else",))
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "sibling", TECH)}
        text = " ".join(rows[SERVER_SURVEY_QN]["notes"])
        assert "dwolfson" in text and "someone_else" in text and "differ" in text
        assert PASSWORD not in json.dumps(rows, default=str)

    def test_an_adopted_server_RE_did_not_create_says_it_does_not_know(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        register(registry, port)
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        assert any("not recorded" in n and "credentials" in n for n in rows[SERVER_SURVEY_QN]["notes"])


class TestControlsAndProjection:
    @pytest.mark.parametrize("state_fixture", ["failed", "submit_failed"])
    def test_the_control_shows_after_a_failure(self, registry, state_fixture):
        port = RegPort()
        if state_fixture == "submit_failed":
            port.process_error = refusal("refused by egeria")
            with pytest.raises(nsr.NativeSurveyError):
                register(registry, port)
        else:
            register(registry, port)
            run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
            port.actions[run["engine_action_guid"]] = ("FAILED", "boom")
            cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert row["register"]["available"] is True

    def test_a_projection_write_is_reported_in_the_response(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: str(tmp_path / "s.omsecrets"))
        out = register(registry, RegPort())
        assert out["projected"]["written"] is True and "adventureworks::PostgreSQL Secret" in out["projected"]["collection"]

    def test_a_projection_that_fails_shows_its_reason_and_creates_nothing(self, registry, tmp_path, monkeypatch):
        from resource_explorer import omsecrets_reproject as rp
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: str(tmp_path / "s.omsecrets"))
        monkeypatch.setattr(rp, "reproject", lambda *a, **k: [rp.Outcome(
            slug="adventureworks", collection="c", status=rp.ERROR, message="OSError: Permission denied")])
        port = RegPort()
        with pytest.raises(cas.RegistrationRefused, match="Permission denied"):
            register(registry, port)
        assert port.calls == []

    def test_no_secrets_path_is_said_on_the_row_and_in_the_response(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: "")
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        assert "secrets path not configured: RE cannot check Egeria's credentials" in rows[CATALOG_QN]["notes"]
        assert register(registry, RegPort())["projected"]["checked"] is False


class TestReachNoteWording:
    @pytest.mark.parametrize("text", ['FATAL: role "surveyor" does not exist', "password authentication failed",
                                      "FATAL: something"])
    def test_a_role_or_password_failure_is_not_a_reach_problem(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == ""

    @pytest.mark.parametrize("text", ["Connection refused", "connect timed out", "Network is unreachable",
                                      "No route to host", "Connection reset by peer", "UnknownHostException x"])
    def test_connection_class_failures_are(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == cas.REACH_NOTE


class TestYamlErrorLogging:
    def test_a_broken_file_logs_class_and_line_never_the_text(self, tmp_path, caplog):
        from resource_explorer import omsecrets_store
        p = tmp_path / "bad.omsecrets"
        p.write_text("secretsCollections:\n  x:\n    secrets:\n      clearPassword: FAKEPW-123: [unclosed\n")
        caplog.set_level(logging.DEBUG)
        omsecrets_store._load(str(p))
        assert "FAKEPW-123" not in caplog.text and "clearPassword" not in caplog.text
        assert "YAMLError" in caplog.text or "ScannerError" in caplog.text or "ParserError" in caplog.text
        assert "line" in caplog.text


class TestAbsentAnswersAreEgeriasOwnConstants:
    @pytest.mark.parametrize("const", [NO_ELEMENTS_FOUND, NO_ELEMENT_FOUND])
    @pytest.mark.parametrize("variant", [lambda x: x, str.lower, str.upper, lambda x: x + ".", lambda x: " " + x + " "])
    def test_both_of_pyegerias_constants_are_absent_in_any_case_with_a_trailing_period(self, const, variant):
        assert nsr.is_exact_absent(variant(const)) is True
        port = real_port(FakeExpert(result=variant(const)))
        assert port.find_element(SQN) is None and port.read_element(DB_GUID) is None
        assert port.asset_exists(DB_GUID) is False

    def test_the_accepted_strings_contain_pyegerias_constants(self):
        accepted = nsr.absent_answers()
        for const in (NO_ELEMENTS_FOUND, NO_ELEMENT_FOUND):
            assert const.strip().rstrip(".").lower() in accepted

    def test_a_lookalike_is_still_not_absent(self):
        for text in ("No elements found for user x", "element not found", "No element found in index"):
            assert nsr.is_exact_absent(text) is False

    def test_a_genuinely_absent_server_is_created_when_egeria_answers_the_real_plural(self, registry):
        """The end-to-end shape of the bug: find_element goes through the REAL port against an expert that
        answers pyegeria's real miss string, and the press then creates the server."""
        class Port(RegPort):
            def find_element(self, qn):
                self.calls.append(("find_element", qn))
                found = real_port(FakeExpert(result=NO_ELEMENTS_FOUND)).find_element(qn) if qn not in self.elements.values() else None
                if found is None and qn in self.elements.values():
                    g = next(g for g, n in self.elements.items() if n == qn)
                    return cas.ElementBack(g, qn)
                return found
        port = Port()
        out = register(registry, port)
        assert out["server"]["how"] == "created" and len(port.processes) == 1


def stalled_run(registry, port):
    """A process Egeria read as COMPLETED whose database is not readable, past RE's local window."""
    register(registry, port)
    run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
    port.actions[run["engine_action_guid"]] = ("COMPLETED", "")
    cas.refresh_registration_run(registry, port, run, entity_type="database", slug="adventureworks")
    age_run(registry)
    return run


class TestStartAgainAndServerClaim:
    def test_a_press_during_a_live_run_keeps_the_claim(self, registry):
        port = RegPort()
        register(registry, port)
        assert cas.claim_held(registry, "adventureworks")
        with pytest.raises(nsr.NativeSurveyBusy):
            cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                     start_again=True)
        assert cas.claim_held(registry, "adventureworks"), "stall protection must survive a refused press"
        assert len(port.processes) == 1

    def test_start_again_asks_egeria_and_refuses_while_the_process_is_still_active(self, registry):
        port = RegPort()
        run = stalled_run(registry, port)
        port.actions[run["engine_action_guid"]] = ("IN_PROGRESS", "")      # Egeria says it is running again
        with pytest.raises(nsr.NativeSurveyBusy, match="IN_PROGRESS"):
            cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                     start_again=True)
        assert cas.claim_held(registry, "adventureworks") and len(port.processes) == 1

    def test_start_again_releases_only_when_egeria_says_the_run_is_not_active(self, registry):
        port = RegPort()
        stalled_run(registry, port)
        cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                 start_again=True)
        assert len(port.processes) == 2

    def test_start_again_with_an_unreadable_process_refuses_with_egerias_sentence(self, registry):
        port = RegPort()
        stalled_run(registry, port)
        port.read_process = lambda guid: (_ for _ in ()).throw(RuntimeError("view server unreachable"))
        with pytest.raises(nsr.NativeSurveyBusy, match="view server unreachable"):
            cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                     start_again=True)
        assert cas.claim_held(registry, "adventureworks")

    def test_two_presses_on_the_same_server_create_it_once(self, registry):
        """The second press arrives while the first has created nothing yet confirmed: it holds the server claim."""
        port = RegPort()
        assert cas.take_server_claim(registry, SERVER_NAME) is True
        with pytest.raises(nsr.NativeSurveyBusy, match="server"):
            register(registry, port)
        assert port.created_servers == []

    def test_the_server_claim_is_released_on_a_confirmed_read_back(self, registry):
        port = RegPort()
        register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is True

    def test_the_server_claim_is_kept_when_the_create_is_unconfirmed(self, registry):
        port = RegPort()
        port.hide_from_find = {SQN}
        port.read_element_override = None
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is False
        port.read_element_override = "unset"
        register(registry, port)                         # adopts the recorded GUID; resolves the claim
        assert cas.take_server_claim(registry, SERVER_NAME) is True

    def test_a_refused_create_releases_the_server_claim(self, registry):
        port = RegPort()
        port.create_error = typed("PyegeriaAPIException", related_http_code=400)
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is True

    def test_start_again_clears_a_server_claim_left_by_a_crash(self, registry):
        port = RegPort()
        cas.take_server_claim(registry, SERVER_NAME)
        cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                 start_again=True)
        assert len(port.created_servers) == 1


class TestLowItems:
    def test_a_later_refusal_still_says_the_secrets_file_was_reprojected(self, registry, tmp_path, monkeypatch):
        monkeypatch.setattr("resource_explorer.omsecrets_store.local_path", lambda: str(tmp_path / "s.omsecrets"))
        port = RegPort()
        port.create_error = RuntimeError("template boom")
        with pytest.raises(nsr.NativeSurveyError) as exc:
            register(registry, port)
        assert "template boom" in str(exc.value) and "re-projected the secrets file (a local write)" in str(exc.value)

    def test_the_credential_note_uses_the_collection_name_function(self, registry, monkeypatch):
        monkeypatch.setattr("resource_explorer.omsecrets_store.secrets_collection_name", lambda slug: f"COLL<{slug}>")
        port = RegPort()
        register(registry, port, "adventureworks")
        notes = cas.server_credential_notes(registry, registry.get_database("sibling"))
        assert "COLL<adventureworks>" in notes[0] and "::PostgreSQL Secret" not in notes[0]

    @pytest.mark.parametrize("text", ["could not connect to server", "Unable to connect to host"])
    def test_could_not_connect_gets_the_note(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == cas.REACH_NOTE


# ═══ third review round ═══════════════════════════════════════════════════════

class TestServerRaceIsClosed:
    def test_a_completing_between_bs_absence_check_and_bs_claim_creates_exactly_one_server(self, registry):
        """The real interleaving: B has seen the server ABSENT; A then registers it completely (creates,
        verifies, releases the claim); B then takes the freed claim. B must re-check and adopt."""
        port = RegPort()
        fired = []

        def a_completes():
            fired.append(1)
            register(registry, port, "sibling")                       # A: the whole press

        port.on_first_absent = a_completes
        out = register(registry, port, "adventureworks")               # B
        assert fired == [1]
        assert len(port.created_servers) == 1
        assert out["server"]["how"] == "adopted"

    def test_the_recheck_also_reads_the_unconfirmed_record(self, registry, monkeypatch):
        """Between B's unconfirmed-record read (empty) and B's claim, A creates the server and records its
        GUID but its read-back is not visible by name yet. B must read the record again, not create."""
        port = RegPort()
        real_take = cas.take_server_claim

        def take_after_a_recorded_it(reg, name, holder=""):
            port.have(SERVER_GUID, SQN)
            port.hide_from_find = {SQN}                       # not visible by name yet
            reg.set_setting(cas.server_unconfirmed_key(SERVER_NAME), SERVER_GUID)
            return real_take(reg, name, holder)

        monkeypatch.setattr(cas, "take_server_claim", take_after_a_recorded_it)
        out = register(registry, port)
        assert port.created_servers == [] and out["server"]["how"] == "adopted"


class TestAmbiguousCreateFailuresKeepTheClaim:
    @pytest.mark.parametrize("make", [
        lambda: TimeoutError("read timed out"),
        lambda: ConnectionError("connection dropped"),
        lambda: OSError("network is unreachable"),
        lambda: __import__("httpx").ReadTimeout("slow"),
        lambda: typed("PyegeriaTimeoutException"),
        lambda: typed("PyegeriaConnectionException"),
    ])
    def test_a_timeout_or_transport_error_keeps_the_claim_and_offers_start_again(self, registry, make):
        port = RegPort()
        port.create_error = make()
        with pytest.raises(nsr.NativeSurveyError, match="may or may not|may not"):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is False
        rows = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}
        cat = rows[CATALOG_QN]
        assert cat["register"]["label"] == cas.START_AGAIN_LABEL and cat["register"]["start_again"]
        assert any("no answer" in n.lower() or "may or may not" in n for n in cat["notes"])
        with pytest.raises(nsr.NativeSurveyBusy):
            register(registry, port)                                   # the next press does not duplicate
        assert port.created_servers == []

    def test_a_cancellation_keeps_the_claim_and_propagates(self, registry):
        import asyncio
        port = RegPort()
        port.create_error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is False

    @pytest.mark.parametrize("name,attrs", [
        ("PyegeriaAPIException", {"related_http_code": 400}),
        ("PyegeriaAPIException", {"related_http_code": "404"}),
        ("PyegeriaUnauthorizedException", {"related_http_code": 401}),
        ("PyegeriaUnauthorizedException", {"related_http_code": 403}),
        ("PyegeriaAPIException", {"response_code": 400}),
        ("PyegeriaInvalidParameterException", {"response": None}),
    ])
    def test_a_genuine_4xx_or_pre_request_refusal_releases_the_claim(self, registry, name, attrs):
        port = RegPort()
        port.create_error = typed(name, **attrs)
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is True

    @pytest.mark.parametrize("name,attrs", [
        ("PyegeriaAPIException", {"related_http_code": 500}),
        ("PyegeriaAPIException", {"related_http_code": 503}),
        ("PyegeriaAPIException", {"related_http_code": None}),
        ("PyegeriaAPIException", {}),
        ("PyegeriaInvalidParameterException", {"response": object()}),
        ("PyegeriaInvalidParameterException", {"response": None, "e": __import__("json").JSONDecodeError("x", "y", 0)}),
    ])
    def test_a_5xx_a_missing_code_or_a_post_response_decode_error_keeps_the_claim(self, registry, name, attrs):
        port = RegPort()
        port.create_error = typed(name, **attrs)
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        assert cas.take_server_claim(registry, SERVER_NAME) is False


def refusal(message, code=400):
    """A typed Egeria refusal (a 4xx API exception) whose str() is `message`, as pyegeria's would be."""
    from pyegeria.core._exceptions import PyegeriaAPIException

    class _Refusal(PyegeriaAPIException):
        def __init__(self, msg):
            Exception.__init__(self, msg)
            self.related_http_code = code

        def __str__(self):
            return self.args[0]

    return _Refusal(message)


def typed(name, **attrs):
    import pyegeria.core._exceptions as ex
    cls = getattr(ex, name)
    e = cls.__new__(cls)
    for k, v in attrs.items():
        setattr(e, k, v)
    return e


class TestStartAgainDoesNotClearAnotherDatabasesServerClaim:
    def test_a_live_claim_held_by_another_database_survives_start_again(self, registry):
        port = RegPort()
        assert cas.take_server_claim(registry, SERVER_NAME, "sibling") is True        # sibling is mid-create
        with pytest.raises(nsr.NativeSurveyBusy):
            cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                     start_again=True)
        assert cas.take_server_claim(registry, SERVER_NAME) is False
        assert port.created_servers == []

    def test_a_claim_held_by_this_database_is_cleared_by_its_own_start_again(self, registry):
        port = RegPort()
        assert cas.take_server_claim(registry, SERVER_NAME, "adventureworks") is True
        cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                 start_again=True)
        assert len(port.created_servers) == 1

    def test_a_claim_with_no_recorded_holder_is_cleared_only_when_no_other_database_is_in_flight(self, registry):
        port = RegPort()
        cas.take_server_claim(registry, SERVER_NAME)
        cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                 start_again=True)
        assert len(port.created_servers) == 1


class TestEarlierRunSelection:
    def test_the_newest_row_with_a_guid_is_checked_not_just_the_newest_row(self, registry):
        port = RegPort()
        register(registry, port)                                       # process A, in flight
        registry.record_native_survey_submission("database", "adventureworks", CATALOG_QN,
                                                 "2999-01-01T00:00:00", submit_error="a later refusal")
        with pytest.raises(nsr.NativeSurveyBusy, match="still running"):
            cas.register_with_egeria(registry, port, "database", "adventureworks", technology_type=TECH,
                                     start_again=True)
        assert cas.claim_held(registry, "adventureworks") and len(port.processes) == 1

    def test_a_claim_with_no_process_guid_says_re_cannot_check(self, registry):
        registry.set_setting(cas.claim_key("adventureworks"), "2026-10-08T00:00:00")
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert row["register"]["start_again"] is True
        assert cas.NO_RECORD_WORDS == (
            "RE has no record of the earlier process, so it cannot check whether it is still running.")
        assert cas.NO_RECORD_WORDS in row["register"]["confirm"]

    def test_a_claim_with_a_guid_names_the_process_in_the_confirm(self, registry):
        port = RegPort()
        run = stalled_run(registry, port)
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert run["engine_action_guid"] in row["register"]["confirm"]


class TestReachNoteExcludesAuthWording:
    @pytest.mark.parametrize("text", ["failed to connect: password authentication failed",
                                      "could not connect to server: FATAL: role \"x\" does not exist",
                                      "unable to connect: authentication failed for user x"])
    def test_a_connect_phrase_with_auth_wording_gets_none(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == ""

    @pytest.mark.parametrize("text", ["failed to connect", "cannot connect", "can't connect"])
    def test_failed_cannot_cant_connect_alone_are_not_enough(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == ""

    @pytest.mark.parametrize("text", ["could not connect to server", "unable to connect to host"])
    def test_could_not_or_unable_to_connect_still_get_it(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == cas.REACH_NOTE


class TestFourthReviewLows:
    def test_the_no_answer_note_is_not_shown_when_a_guid_was_returned_but_not_confirmed(self, registry):
        port = RegPort()
        port.hide_from_find = {SQN}
        port.read_element_override = None
        with pytest.raises(nsr.NativeSurveyError):
            register(registry, port)
        notes = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]["notes"]
        assert cas.CREATED_UNCONFIRMED_WORDS in notes and cas.SERVER_NO_ANSWER_WORDS not in notes

    def test_another_databases_claim_is_named_and_start_again_is_not_offered_for_it(self, registry):
        assert cas.take_server_claim(registry, SERVER_NAME, "sibling") is True
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert row["register"]["start_again"] is False
        assert any("sibling" in n and "Start again" in n for n in row["notes"])
        with pytest.raises(nsr.NativeSurveyBusy) as exc:
            register(registry, RegPort())
        assert "sibling" in str(exc.value) and "on sibling" in str(exc.value)
        own = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "sibling", TECH)}[CATALOG_QN]
        assert own["register"]["start_again"] is True

    def test_the_stale_pending_branch_does_not_release_another_presss_live_claim(self, registry):
        port = RegPort()
        registry.set_setting(cas.server_unconfirmed_key(SERVER_NAME), SERVER_GUID)   # Egeria: gone
        assert cas.take_server_claim(registry, SERVER_NAME, "sibling") is True
        with pytest.raises(nsr.NativeSurveyBusy):
            register(registry, port)
        assert cas.server_claim_holder(registry, SERVER_NAME) == "sibling"
        assert port.created_servers == []

    @pytest.mark.parametrize("text", ["Egeria refused the template request", "refused by the validator"])
    def test_a_refused_that_is_not_a_connection_gets_no_note(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == ""

    @pytest.mark.parametrize("text", ["Connection refused", "Connection to host.docker.internal:5432 refused"])
    def test_a_connection_refused_still_does(self, text):
        assert cas.reach_note_for({"state": nsr.FAILED, "message": text, "error": ""}) == cas.REACH_NOTE


class TestDatabaseProcessSubmitFailures:
    """Egeria may accept the CreateAndSurvey process and the client still see an error: only a genuine
    pre-write refusal may release the database claim."""

    @pytest.mark.parametrize("make", [
        lambda: TimeoutError("read timed out"),
        lambda: __import__("httpx").ReadTimeout("slow"),
        lambda: typed("PyegeriaTimeoutException"),
        lambda: typed("PyegeriaConnectionException"),
        lambda: typed("PyegeriaAPIException", related_http_code=500),
        lambda: typed("PyegeriaAPIException", related_http_code=503),
        lambda: typed("PyegeriaAPIException"),
        lambda: typed("PyegeriaAPIException", related_http_code=None),
        lambda: typed("PyegeriaInvalidParameterException", response=object()),
        lambda: typed("PyegeriaInvalidParameterException", response=None,
                      e=__import__("json").JSONDecodeError("x", "y", 0)),
    ])
    def test_an_ambiguous_failure_keeps_the_claim_and_is_unresolved(self, registry, make):
        port = RegPort()
        port.process_error = make()
        with pytest.raises(cas.RegistrationUnresolved) as exc:
            register(registry, port)
        assert "may or may not" in str(exc.value) and cas.START_AGAIN_LABEL in str(exc.value)
        assert cas.claim_held(registry, "adventureworks")
        run = registry.list_native_survey_runs("database", "adventureworks", CATALOG_QN)[0]
        assert "created or not yet known" in run["submit_error"] and run["engine_action_guid"] == ""

    def test_a_re_press_inside_the_window_makes_no_second_process_call(self, registry):
        port = RegPort()
        port.process_error = TimeoutError("read timed out")
        with pytest.raises(cas.RegistrationUnresolved):
            register(registry, port)
        port.process_error = None
        before = len([c for c in port.calls if c[0] == "initiate_process"])
        with pytest.raises(nsr.NativeSurveyBusy):
            register(registry, port)
        assert len([c for c in port.calls if c[0] == "initiate_process"]) == before == 1

    def test_the_row_offers_start_again_after_an_ambiguous_failure(self, registry):
        port = RegPort()
        port.process_error = TimeoutError("read timed out")
        with pytest.raises(cas.RegistrationUnresolved):
            register(registry, port)
        row = {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}[CATALOG_QN]
        assert row["register"]["start_again"] is True

    @pytest.mark.parametrize("make", [
        lambda: typed("PyegeriaAPIException", related_http_code=400),
        lambda: typed("PyegeriaUnauthorizedException", related_http_code=403),
        lambda: typed("PyegeriaInvalidParameterException", response=None),
    ])
    def test_a_genuine_refusal_releases_the_claim(self, registry, make):
        port = RegPort()
        port.process_error = make()
        with pytest.raises(nsr.NativeSurveyError) as exc:
            register(registry, port)
        assert not isinstance(exc.value, cas.RegistrationUnresolved)
        assert not cas.claim_held(registry, "adventureworks")
        port.process_error = None
        register(registry, port)                         # may be pressed again at once
        assert len(port.processes) == 1


def _api_exc(cls_name="PyegeriaAPIException", code=500, msg_id=""):
    """A real-shaped Egeria failure: the class pyegeria raises, the HTTP code, and the message id Egeria's
    FFDC body carries in `additional_info` (the shape observed live 2026-10-16)."""
    import pyegeria.core._exceptions as ex
    e = Exception.__new__(getattr(ex, cls_name))
    Exception.__init__(e, "Egeria error")
    e.related_http_code = code
    e.context, e.pyegeria_code, e.message, e.system_action, e.user_action = {}, "CLIENT-ERROR", "Egeria refused", "", ""
    e.response = None
    e.additional_info = {"exceptionErrorMessageId": msg_id} if msg_id else {}
    return e


class TestNotVisibleIsAbsenceOfAPointer:
    """Architect ruling 2026-10-16 section 3: for a GUID RE holds as a pointer, 400-010 is `gone`."""

    NOT_VISIBLE = "OMAG-REPOSITORY-HANDLER-400-010"

    def test_a_soft_deleted_database_reads_as_gone(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        port = real_port(FakeExpert(error=_api_exc(msg_id=self.NOT_VISIBLE)))
        out = cas.check_pointers(registry, port, "database", "adventureworks")
        assert out["database"] == "gone" and out["error"] == ""

    def test_the_typed_not_found_is_still_gone(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        out = cas.check_pointers(registry, real_port(FakeExpert(error=typed_not_found())), "database", "adventureworks")
        assert out["database"] == "gone"

    @pytest.mark.parametrize("exc", [
        _api_exc(msg_id="OMAG-REPOSITORY-HANDLER-500-001"),            # a 500 with another id
        _api_exc(msg_id=""),                                           # a 500 with no id
        _api_exc("PyegeriaUnauthorizedException", 401, "OMAG-REPOSITORY-HANDLER-400-010"),   # sign-in refusal
        TimeoutError("read timed out"),
    ])
    def test_everything_else_stays_unreadable(self, registry, exc):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        out = cas.check_pointers(registry, real_port(FakeExpert(error=exc)), "database", "adventureworks")
        assert out["database"] == "unreadable"

    def test_not_visible_does_not_widen_the_shared_absence_answer(self):
        from resource_explorer.egeria_absence import UNREADABLE, is_absent
        assert is_absent(_api_exc(msg_id=self.NOT_VISIBLE)) == UNREADABLE     # never licenses a create


# ── designer Q9: registration states derive from proof rows ──────────────────

def _rows(registry):
    return {r["qualified_name"]: r for r in nsr.native_survey_rows(registry, "database", "adventureworks", TECH)}


def _fail_server_survey(registry, port, sentence):
    sruns = registry.list_native_survey_runs("database", "adventureworks", SERVER_SURVEY_QN)
    port.actions[sruns[0]["engine_action_guid"]] = ("FAILED", sentence)
    nsr.refresh_resource(registry, port, "database", "adventureworks")


class TestRegistrationStatesFromProof:
    def _registered(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        port.have(DB_GUID, DQN)
        register(registry, port)
        return port

    def test_registered_alone_says_egeria_has_not_connected_yet(self, registry):
        self._registered(registry)
        row = _rows(registry)[CATALOG_QN]
        assert row["run"]["state"] == cas.REGISTERED
        assert row["reach"] == {"state": "not_yet", "words": "Egeria hasn't connected yet"}

    def test_a_refused_survey_says_couldnt_connect_and_names_the_address_egeria_tried(self, registry):
        port = self._registered(registry)
        _fail_server_survey(registry, port, "Connection to localhost:5442 refused")
        rows = _rows(registry)
        assert rows[CATALOG_QN]["reach"] == {"state": "failed", "words": "Egeria couldn't connect",
                                             "address": "localhost:5442"}
        tried = rows[SERVER_SURVEY_QN]["tried"]
        assert tried["address"] == "localhost:5442" and tried["own_machine"] is True
        assert tried["words"] == "Egeria tried localhost:5442, and localhost is Egeria's own machine"

    def test_without_an_address_in_the_sentence_the_stored_host_and_port_are_named(self, registry):
        port = self._registered(registry)
        _fail_server_survey(registry, port, "Connection refused (UnknownHostException)")
        tried = _rows(registry)[SERVER_SURVEY_QN]["tried"]
        assert tried["address"] == "host.docker.internal:5432" and tried["own_machine"] is False
        assert "own machine" not in tried["words"]            # a fact about localhost, not a guess about Docker

    def test_a_credential_failure_is_not_a_reach_failure(self, registry):
        port = self._registered(registry)
        _fail_server_survey(registry, port, 'password authentication failed for user "x"')
        rows = _rows(registry)
        assert rows[SERVER_SURVEY_QN]["tried"] == {}
        assert rows[CATALOG_QN]["reach"]["state"] == "not_yet"

    def test_only_a_completed_survey_with_its_report_proves_the_reach(self, registry):
        port = self._registered(registry)
        guid = registry.list_native_survey_runs("database", "adventureworks", SERVER_SURVEY_QN)[0]["engine_action_guid"]
        port.actions[guid] = ("COMPLETED", "")             # the action ended, but no report is stored yet
        nsr.refresh_resource(registry, port, "database", "adventureworks")
        assert _rows(registry)[CATALOG_QN]["reach"]["state"] == "not_yet"
        port.finish(guid, report="rrrrrrrr-0000-0000-0000-000000000001", annotations=[ann(1)])
        nsr.refresh_resource(registry, port, "database", "adventureworks")
        assert _rows(registry)[SERVER_SURVEY_QN]["run"]["state"] == nsr.COMPLETE
        assert _rows(registry)[CATALOG_QN]["reach"] == {"state": "connected", "words": "Egeria has connected"}


class TestHeldAddressWillChange:
    def _registered_and_read_back(self, registry):
        port = RegPort()
        port.have(SERVER_GUID, SQN)
        port.have(DB_GUID, DQN)
        register(registry, port)
        out = cas.check_pointers(registry, port, "database", "adventureworks")
        assert out["database"] == "resolves"
        return port

    def test_the_read_back_records_the_address_egeria_holds(self, registry):
        self._registered_and_read_back(registry)
        assert cas.address_state(registry, "database", "adventureworks", registry.get_database("adventureworks")) == {
            "current": SERVER_NAME, "held": SERVER_NAME, "will_change": False}
        assert _rows(registry)[CATALOG_QN]["register"]["available"] is False

    def test_a_changed_egeria_host_is_will_change_until_a_read_back_shows_the_new_address(self, registry):
        port = self._registered_and_read_back(registry)
        registry.update_egeria_host("database", "adventureworks", "localhost")
        row = _rows(registry)[CATALOG_QN]
        assert row["address"] == {"current": "localhost:5432", "held": SERVER_NAME, "will_change": True}
        assert row["register"]["available"] is True and row["register"]["label"] == cas.REGISTER_AGAIN_LABEL
        assert row["register"]["start_again"] is False
        # Egeria now holds it under the new address (a read-back of the new element): the state clears.
        port.have(DB_GUID, database_qualified_name("localhost:5432", "adventureworks"))
        cas.check_pointers(registry, port, "database", "adventureworks")
        assert _rows(registry)[CATALOG_QN]["address"]["will_change"] is False

    def test_a_database_never_read_back_has_no_held_address_so_no_claim_of_change(self, registry):
        registry.set_database_egeria_guid("adventureworks", DB_GUID)
        registry.update_egeria_host("database", "adventureworks", "localhost")
        row = _rows(registry)[CATALOG_QN]
        assert row["address"]["held"] == "" and row["address"]["will_change"] is False
