"""Brief Z2 (2026-10-10): zone access on the write routes that checked only the sign-in.

Brief Z made curation rights follow Egeria's zones through `workflows.curate.curation_access`. These are the
routes it had not reached yet: the misc repository routes in `web/routes/egeria.py`, the file-system routes, the
database survey/native-survey/Survey-Definition starts, documentation sources, the repository commit and
sub-resource catalog, the bulk linkage actions, and the database commit PER PART (each schema element already in
Egeria). Every one asks the same function, `workflows.curate.resource_access` -> `curation_access`.

No Egeria anywhere: the access reads are faked at the client-construction boundary (`tests/zone_fakes.py`), and
those fakes are bound to the real pyegeria signatures so a call real pyegeria would reject fails here too. What a
route does after the check is replaced by a sentinel, so "allowed" means the check passed and the route went on
to its write, and "refused" means it stopped before it.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resource_explorer.registry import DatabaseEntity, FileSystemEntity, Project, ProjectRegistry

sys.path.insert(0, str(Path(__file__).parent))

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

USER = "garygeeke"
SECURED = "sales-zone"

from tests.zone_fakes import AVAILABLE, FakeEgeria, install  # noqa: E402,F401
from tests.zone_fakes import element as _element  # noqa: E402


# ── the fakes bind the real pyegeria signatures ─────────────────────────────────────────────────────────

def _real(cls_path: str, name: str) -> inspect.Signature:
    module, _, cls = cls_path.rpartition(".")
    return inspect.signature(getattr(getattr(__import__(module, fromlist=[cls]), cls), name))


REAL = {
    "get_metadata_element_by_guid": _real("pyegeria.omvs.metadata_expert.MetadataExpert",
                                          "get_metadata_element_by_guid"),
    "get_security_access_control": _real("pyegeria.omvs.security_officer.SecurityOfficer",
                                         "get_security_access_control"),
    "get_user_account": _real("pyegeria.omvs.security_officer.SecurityOfficer", "get_user_account"),
}


class FaithfulEgeria(FakeEgeria):
    """`zone_fakes.FakeEgeria`, with every call bound against the installed pyegeria method first."""

    def get_metadata_element_by_guid(self, *a, **kw):
        REAL["get_metadata_element_by_guid"].bind(self, *a, **kw)
        return super().get_metadata_element_by_guid(*a, **kw)

    def get_security_access_control(self, *a, **kw):
        REAL["get_security_access_control"].bind(self, *a, **kw)
        return super().get_security_access_control(*a, **kw)

    def get_user_account(self, *a, **kw):
        REAL["get_user_account"].bind(self, *a, **kw)
        return super().get_user_account(*a, **kw)


def test_the_fake_rejects_what_real_pyegeria_rejects():
    """A guard on the fake itself (passes before and after Brief Z2 by design)."""
    fake = FaithfulEgeria()
    with pytest.raises(TypeError):
        fake.get_security_access_control("Quickstart platform")           # control_name is required
    with pytest.raises(TypeError):
        fake.get_user_account(user_id="x")                                 # platform_name is required


def _install(monkeypatch, zones=()) -> FaithfulEgeria:
    fake = FaithfulEgeria()
    install(monkeypatch, zones=zones)
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", lambda: fake)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: fake)
    return fake


# ── the app, with the real check; only who is signed in is set ──────────────────────────────────────────

class Reached(Exception):
    """The route passed its access check and went on to its write."""


def _reached(*a, **k):
    raise Reached()


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "z2.db"))
    assert str(tmp_path) in r.database_url                                    # never the shared registry
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    r.set_egeria_asset_guid("p", "g-p")
    r.register_database(DatabaseEntity(slug="d", display_name="d", db_type="postgresql", host="h", port=5432,
                                       database_name="d", db_user="u", db_password="pw", egeria_asset_guid="g-d"))
    r.register_filesystem(FileSystemEntity(slug="f", display_name="f", local_mount_point="/tmp",
                                           egeria_asset_guid="g-f"))
    return r


@pytest.fixture
def web(registry, monkeypatch):
    """`who["identity"]` False is a request with no caller identity (what `curation_access` calls anonymous)
    while the route's own author lookup still finds a name, so the curation decision itself is what refuses."""
    who = {"user_id": USER, "role": "user", "identity": True}
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    claims = lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}",  # noqa: E731
                              "role": who["role"]}
    monkeypatch.setattr("resource_explorer.auth.get_current_user", claims)
    from resource_explorer import auth as _auth

    real_identity = _auth.identity_from_claims
    monkeypatch.setattr("resource_explorer.auth.identity_from_claims",
                        lambda c: real_identity(c) if who["identity"] else None)
    from resource_explorer.web.routes import catalogue_scope, curate, egeria, projects

    for mod in (catalogue_scope, curate, egeria, projects):
        monkeypatch.setattr(mod, "get_current_user", claims, raising=False)
    from resource_explorer.web.app import app

    return TestClient(app), who


@pytest.fixture
def decisions(monkeypatch):
    """Every decision `curation_access` makes, in order (proves the route asked THE function)."""
    from resource_explorer.workflows import curate as wc

    seen: list = []
    real = wc.curation_access

    def spy(*a, **k):
        d = real(*a, **k)
        seen.append(d)
        return d
    monkeypatch.setattr(wc, "curation_access", spy)
    return seen


# ── every route: (name, method, path, body, element guid or "" when the route checks it as not in Egeria,
#    {patch target: replacement}) ──────────────────────────────────────────────────────────────────────

LINK = {"status": "stale"}
ROUTES = [
    ("repo publish-report", "post", "/api/egeria/p/publish-report", {}, "g-p",
     {"resource_explorer.repo_publish.publish_report": _reached}),
    ("repo forget-links", "post", "/api/egeria/p/forget-links", None, "g-p",
     {"resource_explorer.repo_publish.forget_links": _reached}),
    ("repo reset", "post", "/api/egeria/p/reset", None, "g-p",
     {"resource_explorer.registry.ProjectRegistry.clear_egeria_registration": _reached}),
    ("repo linkage resolve", "post", "/api/egeria/linkage/repo/p/resolve", {"action": "republish"}, "",
     {"resource_explorer.registry.ProjectRegistry.get_egeria_linkage": lambda self, et, s: LINK,
      "resource_explorer.registry.ProjectRegistry.clear_egeria_registration": _reached}),
    ("filesystem publish", "post", "/api/filesystems/f/publish", {}, "g-f",
     {"resource_explorer.registry.ProjectRegistry.get_latest_filesystem_survey": _reached}),
    ("filesystem survey publish", "post", "/api/filesystems/f/survey", {"mode": "hybrid", "publish": True}, "g-f",
     {"resource_explorer.registry.ProjectRegistry.update_filesystem_status": _reached}),
    ("database survey via Egeria", "post", "/api/databases/d/survey", {"use_egeria": True}, "g-d",
     {"resource_explorer.registry.ProjectRegistry.update_database_status": _reached}),
    ("database native survey run", "post", "/api/native-surveys/database/d/run",
     {"process_qualified_name": "PostgreSQLSurvey::survey-postgres-database"}, "g-d",
     {"resource_explorer.native_survey_run.port_session": _reached}),
    ("database native register", "post", "/api/native-surveys/database/d/register", {}, "g-d",
     {"resource_explorer.native_survey_run.port_session": _reached}),
    ("database Survey Definition run", "post", "/api/survey-definitions/database/d/run",
     {"survey_definition_ref": "SurveyDefinition::x"}, "g-d",
     {"resource_explorer.registry.ProjectRegistry.has_assigned_egeria_project": lambda self, et, s: True,
      "resource_explorer.activity_logger.log_survey": _reached}),
    ("database doc-source add", "post", "/api/doc-sources/database/d", {"url": "https://docs.example/x"}, "g-d",
     {"resource_explorer.registry.ProjectRegistry.add_doc_source": _reached}),
    ("database doc-source remove", "delete", "/api/doc-sources/database/d/s1", None, "g-d",
     {"resource_explorer.registry.ProjectRegistry.get_doc_source":
      lambda self, et, s, sid: {"id": sid, "egeria_external_ref_guid": "ref-1"},
      "resource_explorer.registry.ProjectRegistry.remove_doc_source": _reached}),
    ("repo curate commit", "post", "/api/projects/p/curate/commit", {}, "g-p",
     {"resource_explorer.curate_plan.build_plan": _reached}),
    ("repo sub-resources catalog", "post", "/api/projects/p/sub-resources/catalog", {}, "g-p",
     {"resource_explorer.resource_scope.chosen_locators": lambda reg, slug: ["src/a.py"],
      "resource_explorer.resource_scope.publish_chosen": _reached}),
]
# The database catalog commit's own route check (the database element) is Brief Z's and has its tests there; its
# per-part check is Brief Z2's, in tests/test_zone_access_database_commit_parts.py.
IDS = [r[0] for r in ROUTES]


def _zone(fake, guid: str, grant: list[str]) -> None:
    """The element (or, checked as not in Egeria, RE's configured zone) in SECURED, whose control grants `grant`."""
    if guid:
        fake.elements[guid] = _element(guid, [SECURED])
    fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": grant}}


def _call(client, method, path, body):
    kw = {"json": body} if body is not None else {}
    return getattr(client, method)(path, **kw)


def _patch(monkeypatch, patches):
    for target, fn in patches.items():
        monkeypatch.setattr(target, fn)


@pytest.mark.parametrize("name,method,path,body,guid,patches", ROUTES, ids=IDS)
class TestEveryRoute:
    def test_no_caller_identity_is_denied(self, web, monkeypatch, decisions, name, method, path, body, guid, patches):
        client, who = web
        _install(monkeypatch)
        _patch(monkeypatch, patches)
        who["identity"] = False
        r = _call(client, method, path, body)               # never Reached
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("sign in to curate")
        assert decisions and decisions[-1].basis == "anonymous"

    def test_a_zoned_element_with_a_grant_is_allowed(self, web, monkeypatch, decisions,
                                                     name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch, zones=[] if guid else [SECURED])
        _zone(fake, guid, [USER])                             # the user's own id is on the list
        _patch(monkeypatch, patches)
        with pytest.raises(Reached):
            _call(client, method, path, body)
        assert decisions[-1].allowed and decisions[-1].basis == "zone_granted"
        assert ("control", SECURED) in fake.calls

    def test_a_zoned_element_without_a_grant_is_refused_with_its_word(self, web, monkeypatch, decisions,
                                                                      name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch, zones=[] if guid else [SECURED])
        _zone(fake, guid, ["dbas"])
        _patch(monkeypatch, patches)
        r = _call(client, method, path, body)
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith(f"zone {SECURED} does not grant {USER} ")
        assert r.json()["detail"].endswith(" in Egeria")

    def test_an_unreadable_check_denies_with_the_reason(self, web, monkeypatch, decisions,
                                                        name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch, zones=[] if guid else [SECURED])
        _zone(fake, guid, [USER])
        if guid:
            fake.fail_element = guid                          # the element read fails
        else:
            fake.fail_control = SECURED                       # the control read fails
        _patch(monkeypatch, patches)
        r = _call(client, method, path, body)
        assert r.status_code == 403, r.text
        assert r.json()["detail"].startswith("could not check access in Egeria (")

    def test_a_portal_curator_is_allowed_whatever_the_zones(self, web, monkeypatch, decisions,
                                                             name, method, path, body, guid, patches):
        client, who = web
        fake = _install(monkeypatch, zones=[] if guid else [SECURED])
        _zone(fake, guid, ["dbas"])
        who["role"] = "curator"
        _patch(monkeypatch, patches)
        with pytest.raises(Reached):
            _call(client, method, path, body)
        assert decisions[-1].basis == "portal_role"
        assert fake.calls == []                               # nothing read for an override


# ── the bulk linkage actions: per item, the rest proceed ────────────────────────────────────────────────

class TestBulk:
    def test_delete_in_egeria_says_not_permitted_before_the_issue_117_block(self, registry, monkeypatch,
                                                                            signed_in_caller):
        from resource_explorer import bulk_ops
        from resource_explorer.catalogue_gateway import ISSUE_117_WORDS

        fake = _install(monkeypatch)
        monkeypatch.delenv("RE_ISSUE_117_BLOCK_OFF", raising=False)
        _zone(fake, "g-d", ["dbas"])
        out = bulk_ops.delete_in_egeria(registry, [{"entity_type": "database", "slug": "d"},
                                                   {"entity_type": "repo", "slug": "p"}])
        d = {x["slug"]: x for x in out.details}
        assert d["d"]["result"] == "skipped"
        assert d["d"]["message"] == f"not permitted · zone {SECURED} does not grant test-caller delete in Egeria"
        assert d["p"] == {"entity_type": "repo", "slug": "p", "result": "failed", "message": ISSUE_117_WORDS}

    def test_resolve_all_skips_the_item_not_permitted_and_resolves_the_rest(self, registry, monkeypatch,
                                                                             signed_in_caller):
        from resource_explorer import bulk_ops

        fake = _install(monkeypatch, zones=[SECURED])
        fake.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}
        monkeypatch.setattr(ProjectRegistry, "get_egeria_linkage", lambda self, et, s: LINK)
        resolved = []
        monkeypatch.setattr(bulk_ops, "_resolve_one", lambda reg, et, s, a: resolved.append(s))
        # the database is open: its owner granted the caller on its own private zone (RE's configured zones
        # differ per resource only by the private investigation; here both resources share SECURED)
        monkeypatch.setattr("resource_explorer.workflows.curate.configured_zones_for",
                            lambda reg, et, s: [SECURED] if et == "repo" else [])
        out = bulk_ops.resolve_all(registry, [{"entity_type": "repo", "slug": "p"},
                                              {"entity_type": "database", "slug": "d"}], "republish")
        d = {x["slug"]: x for x in out.details}
        assert d["p"]["result"] == "skipped" and d["p"]["message"].startswith(f"not permitted · zone {SECURED}")
        assert d["d"]["result"] == "ok" and resolved == ["d"]


# ── the CLI asks the same function ──────────────────────────────────────────────────────────────────────

class TestCli:
    def test_the_cli_publish_paths_ask_resource_access(self, registry, monkeypatch, signed_in_caller):
        import typer

        from resource_explorer.cli import main as cli
        from resource_explorer.cli import session as cli_session

        fake = _install(monkeypatch)
        _zone(fake, "g-d", ["dbas"])
        asked = []
        from resource_explorer.workflows import curate as wc

        real = wc.resource_access
        monkeypatch.setattr(wc, "resource_access", lambda *a, **k: asked.append(a[1:4]) or real(*a, **k))
        monkeypatch.setattr(cli_session, "require_and_activate", lambda console: None)
        with pytest.raises(typer.Exit) as exc:
            cli._require_resource_write(registry, "database", "d")
        assert exc.value.exit_code == 1
        assert asked == [("database", "d", "publish")]
        assert cli._resource_write_refusal(registry, "repo", "p") == ""         # p is not zoned: open

    def test_the_a2a_surface_has_no_write_to_guard(self):
        """The A2A tools are read-only (the Brief Z2 inventory): none reaches a publish, outbox or run enqueue, so
        there is no A2A write for the check to stand in front of. A tripwire (passes before and after by design):
        it fails the day an A2A tool gains a write, which must then ask `resource_access` like every other door."""
        import resource_explorer.agents.tools as tools

        src = inspect.getsource(tools)
        for word in ("EgeriaPublisher", "enqueue_outbox", "enqueue_run", ".publish(", "create_", "delete_"):
            assert word not in src, word


# ── run-time: an automatic publish asks again, as the person who asked for the run ──────────────────────

def test_an_analysis_auto_publish_is_skipped_for_a_requester_not_permitted(registry, monkeypatch, signed_in_caller):
    from resource_explorer.workflows import curate as wc

    fake = _install(monkeypatch)
    _zone(fake, "g-p", ["dbas"])
    assert wc.person_write_refusal(registry, "repo", "p") == \
        f"not permitted · zone {SECURED} does not grant test-caller update in Egeria"


def test_a_run_nobody_asked_for_proceeds_only_with_no_zone_in_use(registry, monkeypatch):
    """Round 2 (HIGH 1): no requester (a schedule with no recorded creator, an unattributed queue row) never runs on
    the service account's rights: open with no zone in use, refused with zones in use."""
    from resource_explorer.workflows import curate as wc

    fake = _install(monkeypatch)
    assert wc.person_write_refusal(registry, "repo", "p") == ""             # g-p has no zone: open
    _zone(fake, "g-p", ["dbas"])
    assert wc.person_write_refusal(registry, "repo", "p") == \
        f"not permitted · {wc.NO_REQUESTER_WORDS}, and zone {SECURED} is in use"
