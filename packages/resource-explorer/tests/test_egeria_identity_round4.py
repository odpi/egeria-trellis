"""Brief I round 4: every element a queued person-action creates carries the requester; a
drain-loop retry stamps the row's author and never the service account; Ownership = the
requester; a stamp that did not land is reported as partial; requestedBy is merged into the
element's CURRENT additionalProperties; a file-system survey publishes only when someone chose to.
Fakes only; nothing reaches Egeria."""
from __future__ import annotations

from contextlib import contextmanager

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401
    choose, fake, registry, world,
)

from resource_explorer import egeria_identity as ident  # noqa: E402
from resource_explorer.egeria_clients import Daemon, DaemonReason, acting_as  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

DAEMON_USER = "re-daemon"


@pytest.fixture(autouse=True)
def _daemon(monkeypatch):
    monkeypatch.setattr("resource_explorer.egeria_clients._daemon_credential", lambda: (DAEMON_USER, "pw"))


@contextmanager
def _queued_for(person):
    """As `run_queue._run_as_requester` runs a queued row: the daemon acts, and the caller ContextVar names the
    requester (Brief Z2: the run re-checks curation access as that person)."""
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id=person, egeria_token=None, auth_source="queued-run",
                                              role="user"))
    try:
        with acting_as(Daemon(DaemonReason.RUN_QUEUE, requested_by=person)) as identity:
            yield identity
    finally:
        current_caller.reset(reset)


def _press_then_queue(w, fake, person="dana"):
    """The press is a person's request (start_commit needs their sign-in); the commit itself runs
    queued, as RE's daemon on their behalf."""
    from test_catalogue_commit import ME, cc

    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    reset = current_caller.set(CallerIdentity(user_id=person, egeria_token="tok", auth_source="app-jwt"))
    try:
        out = cc.start_commit(w["registry"], "db", ME, refresh_now=True, gateway=fake)
    finally:
        current_caller.reset(reset)
    with _queued_for(person):
        return out, cc.execute_commit(w["registry"], out["curation"]["id"], gateway=fake)


# ── 1: the catalog commit's server and database elements carry the requester ─


def test_a_queued_catalog_commit_stamps_the_server_and_database_with_the_requester(world, fake):
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake)
    stamped = {c[1]: (c[2], c[3]) for c in fake.calls if c[0] == "mark_on_behalf"}
    assert stamped[fake.server_guid] == ("dana", "dana")
    assert stamped[fake.db_guid] == ("dana", "dana")


def test_with_a_declared_owner_the_database_keeps_the_owner_step_and_still_records_the_requester(world, fake):
    world["registry"].save_context("database", "db", {"enrichment": {"owner": {"value": "Data Team"}}})
    choose(world, "sales", "catalogue")
    _press_then_queue(world, fake)
    stamped = {c[1]: (c[2], c[3]) for c in fake.calls if c[0] == "mark_on_behalf"}
    assert stamped[fake.db_guid] == ("dana", "")          # requestedBy; Ownership left to the owner step
    assert ("set_owner", fake.db_guid, "Data Team") in fake.calls


def test_a_stamp_that_did_not_land_makes_the_publish_step_partial(world, fake):
    fake.mark_on_behalf_says = "requester not recorded (Ownership: 500 Egeria failed)"
    choose(world, "sales", "catalogue")
    _out, rec = _press_then_queue(world, fake)
    step = next(s for s in rec["steps"] if s["name"] == "publish_elements")
    assert "partial · requester not recorded (Ownership: 500 Egeria failed)" in step["detail"]


# ── 2: a drain-loop retry stamps the row's author, never the service account ──


def _attach_row(reg, by):
    payload = {"slug": "db", "schema": "sales", "database_guid": "dbg", "curation_id": "c1"}
    if by:
        payload["by"] = by
    return reg.enqueue_outbox_element("database", "db", "catalogue_schema_attach",
                                      "PostgreSQL Relational Database Schema::x::shop.sales", payload)


@pytest.mark.parametrize("by,expected", [("dana", ("dana", "dana")), ("", ("", ""))])
def test_a_background_retry_stamps_the_original_author_or_nothing(world, fake, monkeypatch, by, expected):
    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    reg = world["registry"]
    fake.db_guid = "dbg"
    _attach_row(reg, by)
    seen = []
    real = fake.mark_on_behalf

    def spy(guid, requester, owner):
        seen.append((requester, owner))
        return real(guid, requester, owner)
    monkeypatch.setattr(fake, "mark_on_behalf", spy)
    drain_outbox(reg, OutboxClients(catalogue_gateway=fake, registry=reg), lambda qn: "",
                 identity=Daemon(DaemonReason.OUTBOX))
    assert seen == [expected]
    assert all(DAEMON_USER not in pair for pair in seen), "never the service account as owner"


def test_the_helper_never_names_the_daemon_user_as_owner():
    assert ident.on_behalf_of(Daemon(DaemonReason.OUTBOX)).owner == ""
    assert ident.on_behalf_of(Daemon(DaemonReason.OUTBOX, requested_by=DAEMON_USER)) == ident.OnBehalf("", "")
    assert ident.on_behalf_of(Daemon(DaemonReason.RUN_QUEUE, requested_by="dana")) == ident.OnBehalf("dana", "dana")


# ── 4/5: stamps are reported; requestedBy merges into the current map ───────


class _Props:
    def __init__(self, current=None, read_error=None):
        self.current, self.read_error, self.updates = current or {}, read_error, []

    def get_metadata_element_by_guid(self, guid):
        if self.read_error:
            raise self.read_error
        return {"elementHeader": {"guid": guid}, "elementProperties": {"propertyValueMap": {
            "additionalProperties": ident._map_property(self.current)}} if self.current else {}}

    def update_metadata_element_properties(self, guid, body):
        self.updates.append(body)


def _written(props):
    m = props.updates[0]["properties"]["propertyValueMap"]["additionalProperties"]["mapValues"]["propertyValueMap"]
    return {k: v["primitiveValue"] for k, v in m.items()}


def test_requested_by_is_merged_into_the_existing_additional_properties():
    props = _Props(current={"templateKey": "kept", "other": "also kept"})
    assert ident.record_requested_by("g1", ident.OnBehalf("dana", "dana"), client=props) == ""
    assert _written(props) == {"templateKey": "kept", "other": "also kept", "requestedBy": "dana"}


def test_a_failed_read_writes_nothing_and_says_why():
    props = _Props(read_error=RuntimeError("read refused"))
    why = ident.record_requested_by("g1", ident.OnBehalf("dana", "dana"), client=props)
    assert props.updates == [] and "could not be read" in why and "read refused" in why


def test_a_port_whose_stamp_failed_is_partial_not_materialized(monkeypatch):
    from resource_explorer.surveyors.arch_recovery.port_materializer import PortMaterializer

    class ME(_Props):
        def get_metadata_guid_by_unique_name(self, *a, **k):
            return "No elements found"

        def create_metadata_element(self, body):
            return "11111111-1111-1111-1111-111111111111"

    m = PortMaterializer(platform_url="https://localhost:9443")
    m._metadata_expert = ME(read_error=RuntimeError("read refused"))
    m._solution_architect = type("SA", (), {"link_solution_component_port": lambda *a, **k: None})()
    m._connect = lambda: None
    monkeypatch.setattr(ident, "set_ownership_reason", lambda *a, **k: (True, ""))
    with _queued_for("dana"):
        out = m.materialize_port_element("repo", "p", "src/a", "http",
                                         component_guid="22222222-2222-2222-2222-222222222222")
    assert out["status"] == "partial"
    assert out["words"].startswith("partial · requester not recorded (requestedBy:")


def test_a_blueprint_whose_ownership_did_not_land_is_published_as_partial(monkeypatch):
    from resource_explorer import architecture_publish as ap

    monkeypatch.setattr("resource_explorer.workflows.curate.materialize_blueprint_if_accepted",
                        lambda *a, **k: {"status": "materialized", "guid": "g-bp",
                                         "requester_not_recorded": "requester not recorded (Ownership: 403)"})
    monkeypatch.setattr("resource_explorer.workflows.curate.promote_to_publish_zones",
                        lambda guid: {"status": "already_unzoned"})
    monkeypatch.setattr("resource_explorer.workflows.curate.record_promotion", lambda *a, **k: None)
    reg = type("R", (), {"append_catalogue_commit_proof": lambda self, *a, **k: None})()
    status, words, _ = ap._publish_blueprint(reg, "p", {"perspective": "deployment", "cluster_name": "c",
                                                       "key": "deployment::c"})
    assert status == ap.PARTIAL and "requester not recorded (Ownership: 403)" in words


def test_a_component_whose_ownership_did_not_land_is_published_as_partial(monkeypatch):
    from resource_explorer import architecture_publish as ap

    monkeypatch.setattr("resource_explorer.workflows.curate.materialize_component_if_accepted",
                        lambda *a, **k: {"status": "materialized", "guid": "g-c",
                                         "requester_not_recorded": "requester not recorded (Ownership was not set; see the log)"})
    monkeypatch.setattr("resource_explorer.workflows.curate.promote_to_publish_zones",
                        lambda guid: {"status": "already_unzoned"})
    monkeypatch.setattr("resource_explorer.workflows.curate.record_promotion", lambda *a, **k: None)
    status, words, _ = ap._publish_component(object(), "p", "src/a")
    assert status == ap.PARTIAL and words.startswith("partial · requester not recorded")


# ── 6: a file-system survey publishes only when someone chose to ─────────────


def test_a_file_system_with_a_url_is_not_published_unless_asked(tmp_path, monkeypatch):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry
    from resource_explorer.surveyors.filesystem import hybrid_filesystem_surveyor as hfs

    reg = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    reg.register_filesystem(FileSystemEntity(slug="fs", display_name="FS", local_mount_point=str(tmp_path),
                                             egeria_url="https://localhost:9443", egeria_server="v"))
    published = []

    class Local:
        def __init__(self, *a):
            pass

        def run(self):
            return {"surveyed_at": "2026-10-09T00:00:00", "total_files": 0}
    monkeypatch.setattr(hfs, "LocalFileSystemSurveyor", Local)
    monkeypatch.setattr("resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor.EgeriaFileSystemSurveyor",
                        lambda **k: type("E", (), {"catalog_and_survey": lambda self, *a, **kw: published.append(1) or {}})())
    hfs.run_hybrid_filesystem_survey("fs", reg)
    assert published == [], "a URL on the resource is not a choice to publish"
    hfs.run_hybrid_filesystem_survey("fs", reg, force_egeria_publish=True)
    assert published == [1]


@pytest.mark.parametrize("body,chosen", [
    ({}, False), ({"mode": "local"}, False), ({"mode": "hybrid"}, True), ({"mode": "egeria"}, True),
    ({"publish": True}, True), ({"force_publish": True}, True),
])
def test_the_survey_route_publishes_only_on_an_explicit_choice(body, chosen):
    from resource_explorer.web.routes.filesystems import FileSystemSurveyRequest, _publish_chosen

    assert _publish_chosen(FileSystemSurveyRequest(**body)) is chosen


# ── found live on 8813: the platform URL has one source ───────────────────────


def test_with_the_variable_unset_the_database_surveyor_resolves_the_configured_platform(monkeypatch):
    """8813's .env does not set EGERIA_PLATFORM_URL. The config default is the platform; a direct
    env read saw '' and refused ("EGERIA_PLATFORM_URL is not set")."""
    from resource_explorer import config
    from resource_explorer.egeria_clients import allowed_platforms, origin_of
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyor, can_use_egeria,
    )

    monkeypatch.delenv("EGERIA_PLATFORM_URL", raising=False)
    monkeypatch.delenv("EGERIA_VIEW_SERVER", raising=False)
    configured = config.get_config().egeria.platform_url
    s = EgeriaDatabaseSurveyor()
    assert s.platform_url == configured and s.view_server == config.get_config().egeria.view_server
    assert origin_of(s.platform_url) in allowed_platforms()
    assert can_use_egeria() is True
