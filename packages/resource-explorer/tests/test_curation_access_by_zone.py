"""Brief Z (2026-10-10): curation rights follow Egeria's zones, not RE's own "last decided_by owns it".

The project owner: "rely on Egeria's security model and emulate it - so if zones are used they can control
access - if zones aren't being used then it is open". What broke: the egeria_git Publish plan mixed decisions
by erinoverview and peterprofile, and Publish refused BOTH with a whole-press 403 (each was "not the owner" of
the other's items), and neither could re-decide the other's items either.

No Egeria anywhere: the zone reads are faked at the client-construction boundary (`zone_access._metadata_expert`,
`_security_officer`, `_platform`), and a fake that is reached when no read should happen fails the test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resource_explorer import architecture_publish as ap
from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")

# The deployment's two people (the egeria_git case was erinoverview and peterprofile; erinoverview is RE's own
# service account in a default checkout, which the web refuses to sign in, so a second person stands in for it).
ERIN, PETER = "garygeeke", "peterprofile"
SECURED = "sales-zone"


# ── a fake Egeria at the client boundary (tests/zone_fakes.py) ──────────────────────────────────────────

from tests.zone_fakes import AVAILABLE, FakeEgeria, install  # noqa: E402,F401
from tests.zone_fakes import element as _element  # noqa: E402


@pytest.fixture
def egeria(monkeypatch):
    return install(monkeypatch)


@pytest.fixture
def no_egeria_reads(monkeypatch):
    """With zones not in use and nothing in Egeria, NO read may happen."""
    def boom(*a, **k):
        raise AssertionError("an Egeria access read was made where none was needed")
    monkeypatch.setattr("resource_explorer.zone_access._metadata_expert", boom)
    monkeypatch.setattr("resource_explorer.zone_access._security_officer", boom)
    monkeypatch.setattr("resource_explorer.zone_access._platform", boom)
    monkeypatch.delenv("EXPLORER_DRAFT_ZONE", raising=False)
    monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [])


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P repo", github_url="https://github.com/x/p", description=""))
    return r


def _as(user_id, role="user", source="app-jwt"):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller

    return current_caller.set(CallerIdentity(user_id=user_id, egeria_token="t", auth_source=source, role=role))


@pytest.fixture
def as_erin():
    from resource_explorer.a2a_auth import current_caller

    reset = _as(ERIN)
    yield ERIN
    current_caller.reset(reset)


# ── 1. the emulation itself (OpenMetadataAccessSecurityConnector.validateZoneAccess) ──────────────────


class TestZoneGrants:
    @staticmethod
    def _grants(zones, controls, account=AVAILABLE, owners=None, operations=("UPDATE_PROPERTIES",)):
        from resource_explorer.zone_access import zone_grants

        return zone_grants(ERIN, zones, control_for=controls.get, account_for=lambda: account, owners=owners,
                           operations=operations)

    def test_a_zone_named_after_the_user_grants(self):
        assert self._grants(["resource-explorer-private", ERIN], {}).allowed is True

    def test_a_zone_with_no_control_is_ignored_not_restrictive(self):
        assert self._grants(["decorative"], {}).allowed is True

    def test_a_secured_zone_without_the_user_denies_and_names_the_zone(self):
        v = self._grants([SECURED], {SECURED: {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}},
                         account={**AVAILABLE, "securityGroups": ["hr"]}, owners=[PETER])
        assert v.allowed is False and v.secured == [SECURED]

    def test_a_group_or_role_on_the_account_grants(self):
        controls = {SECURED: {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}}
        assert self._grants([SECURED], controls, {**AVAILABLE, "securityGroups": ["salesTeam"]}, owners=[PETER]).allowed
        assert self._grants([SECURED], controls, {**AVAILABLE, "securityRoles": ["salesTeam"]}, owners=[PETER]).allowed

    def test_the_operation_key_wins_over_default(self):
        controls = {SECURED: {"associatedSecurityList": {"UPDATE_PROPERTIES": ["editors"], "DEFAULT": ["allUsers"]}}}
        assert self._grants([SECURED], controls, owners=[PETER]).allowed is False

    def test_all_users_and_account_type_groups_grant(self):
        assert self._grants([SECURED], {SECURED: {"associatedSecurityList": {"DEFAULT": ["allUsers"]}}}).allowed
        assert self._grants([SECURED], {SECURED: {"associatedSecurityList": {"DEFAULT": ["employeeUsers"]}}},
                            {**AVAILABLE, "userAccountType": "EMPLOYEE"}, owners=[PETER]).allowed

    def test_instance_owner_holds_when_there_is_no_ownership_userids(self):
        """isUserAnOwner (:1260-1286) is TRUE when the element carries no Ownership userIds at all."""
        controls = {SECURED: {"associatedSecurityList": {"DEFAULT": ["instanceOwner"]}}}
        assert self._grants([SECURED], controls, owners=None).allowed is True
        assert self._grants([SECURED], controls, owners=[PETER]).allowed is False

    def test_a_disabled_or_missing_account_denies_a_secured_zone(self):
        controls = {SECURED: {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}}
        assert self._grants([SECURED], controls, None, owners=[PETER]).allowed is False
        assert self._grants([SECURED], controls, {"userAccountStatus": "DISABLED", "securityGroups": ["salesTeam"]},
                            owners=[PETER]).allowed is False


# ── 2. the decision (workflows/curate.curation_access) ────────────────────────────────────────────────


class TestCurationAccess:
    def test_anonymous_is_denied(self, registry, no_egeria_reads):
        from resource_explorer.workflows.curate import BASIS_ANONYMOUS, curation_access

        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_ANONYMOUS) and "sign in" in d.reason

    def test_the_egeria_git_case_mixed_decided_by_no_zones_is_open_to_either(self, registry, no_egeria_reads):
        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.workflows.curate import BASIS_NO_ZONES, curation_access, last_decided_by

        registry.record_component_verdict("repo", "p", "src/a", "accepted", "", "", decided_by=ERIN)
        registry.record_component_verdict("repo", "p", "src/b", "accepted", "", "", decided_by=PETER)
        for who in (ERIN, PETER):
            reset = _as(who)
            try:
                for scope in ("src/a", "src/b"):
                    d = curation_access(registry, "repo", "p", scope)
                    assert (d.allowed, d.reason, d.basis) == (True, "", BASIS_NO_ZONES), (who, scope)
            finally:
                current_caller.reset(reset)
        assert last_decided_by(registry, "repo", "p", "src/b") == PETER      # attribution kept, not a gate

    def test_an_element_in_egeria_with_no_zone_is_open(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_NO_ZONES, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (True, BASIS_NO_ZONES)
        assert egeria.calls == [("element", "g-a")]

    def test_a_zoned_element_granted_to_the_caller_is_allowed(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_ZONE_GRANTED, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [SECURED], owners=[PETER])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
        egeria.accounts[ERIN] = {**AVAILABLE, "securityGroups": ["salesTeam"]}
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.reason, d.basis) == (True, "", BASIS_ZONE_GRANTED)

    def test_a_zoned_element_not_granted_is_not_permitted_with_the_zone_named(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_ZONE_REFUSED, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [SECURED], owners=[PETER])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
        egeria.accounts[ERIN] = {**AVAILABLE, "securityGroups": ["hr"]}
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_ZONE_REFUSED)
        assert d.reason == f"zone {SECURED} does not grant {ERIN} update in Egeria"

    def test_an_unreadable_control_denies_with_the_reason(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_UNREADABLE, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [SECURED])
        egeria.fail_control = SECURED
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_UNREADABLE)
        assert d.reason.startswith("could not check access in Egeria (") and "secrets store unreadable" in d.reason

    def test_no_control_is_not_trusted_when_pyegeria_catch_is_installed(self, registry, egeria, as_erin,
                                                                        monkeypatch):
        """Brief Z round 2 (LOW): pyegeria's `dynamic_catch` may install loguru's `logger.catch`, which returns
        None for a FAILED read: "no control" (= open) and a failure look the same. Decided by asking the
        function itself (`__wrapped__`), not the setting, so it holds whatever the setting says now."""
        import functools

        from resource_explorer.workflows.curate import BASIS_UNREADABLE, curation_access

        class WrappedSO(FakeEgeria):
            pass

        def raw(self, platform, zone, **kw):
            return None

        @functools.wraps(raw)
        def caught(self, *a, **k):          # what logger.catch looks like: wraps, swallows, returns None
            return raw(self, *a, **k)
        WrappedSO.get_security_access_control = caught
        so = WrappedSO()
        monkeypatch.setattr("resource_explorer.zone_access._security_officer", lambda: so)
        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [SECURED])
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_UNREADABLE) and "error catch is installed" in d.reason

    def test_an_unreadable_element_denies_rather_than_reading_as_no_zones(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_UNREADABLE, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.fail_element = "g-a"
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_UNREADABLE) and "platform unreachable" in d.reason

    def test_configured_publish_zones_apply_to_an_element_not_yet_in_egeria(self, registry, egeria, as_erin,
                                                                           monkeypatch):
        from resource_explorer.workflows.curate import BASIS_ZONE_REFUSED, curation_access

        monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [SECURED])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
        d = curation_access(registry, "repo", "p", "src/new")
        assert (d.allowed, d.basis) == (False, BASIS_ZONE_REFUSED)
        assert ("element", "src/new") not in egeria.calls

    def test_a_portal_curator_is_allowed_without_reading_egeria(self, registry, no_egeria_reads):
        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.workflows.curate import BASIS_PORTAL_ROLE, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        for role in ("curator", "admin", "CURATOR"):
            reset = _as(ERIN, role=role)
            try:
                d = curation_access(registry, "repo", "p", "src/a")
                assert (d.allowed, d.basis) == (True, BASIS_PORTAL_ROLE), role
            finally:
                current_caller.reset(reset)

    def test_the_a2a_caller_is_the_one_checked(self, registry, egeria):
        """A2A's middleware publishes its caller (auth_source egeria-token) on the same ContextVar."""
        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.workflows.curate import curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [SECURED, PETER])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["nobody"]}}
        for who, allowed in ((PETER, True), (ERIN, False)):
            reset = _as(who, source="egeria-token")
            try:
                assert curation_access(registry, "repo", "p", "src/a").allowed is allowed, who
            finally:
                current_caller.reset(reset)


# ── 3. the routes: verdicts and Publish ───────────────────────────────────────────────────────────────

from tests.test_architecture_publish_e2e import _seed as _seed_e2e  # noqa: E402
from tests.test_architecture_publish_e2e import world  # noqa: E402,F401 - the fixture

BP = "deployment::a grouping of services"


def _seed(reg, decided=(PETER, PETER, PETER, PETER)):
    """Components A, B (members of blueprint BP) and C (no blueprint), all accepted; `decided` = who decided
    a, b, c and the blueprint (attribution only)."""
    _seed_e2e(reg, "a grouping of services")
    reg.upsert_finding("p", "architecture_recovery", [{
        "check_name": "component", "label": "x",
        "detail": {"name": "C", "slug": "c", "type": "Software Service", "admission": "built"}}],
        surveyed_at="2026-10-09T00:00:00", scope_locator="src/c")
    for scope, who in zip(("src/a", "src/b", "src/c"), decided):
        reg.record_component_verdict("repo", "p", scope, "accepted", "", "", decided_by=who)
    reg.record_component_verdict("repo", "p", BP, "accepted", "", "", verdict_target="blueprint", decided_by=decided[3])


def _zone_the_blueprint(reg, egeria):
    """A and B already in Egeria, and BP too (members still to attach) in a zone that grants ERIN nothing."""
    reg.record_materialized_component("repo", "p", "src/a", "SolutionComponent::repo::p::src/a", "g-a")
    reg.record_materialized_component("repo", "p", "src/b", "SolutionComponent::repo::p::src/b", "g-b")
    reg.record_materialized_blueprint("repo", "p", "deployment", "a grouping of services", "qn-bp", "g-bp")
    egeria.elements["g-bp"] = _element("g-bp", [SECURED], owners=[PETER])
    egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
    plan = ap.publish_plan(reg, "p")
    assert [c["path"] for c in plan["components"]["to_write"]] == ["src/c"]
    assert [b["key"] for b in plan["blueprints"]["to_write"]] == [BP], "the blueprint still has members to attach"


@pytest.fixture
def web(registry, monkeypatch):
    """The real app with the real curation check; only who is signed in is set."""
    who = {"user_id": ERIN}
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", registry.__dict__) or None)
    monkeypatch.setenv("TRELLIS_ANONYMOUS_READ", "true")
    monkeypatch.setattr("resource_explorer.auth.get_current_user",
                        lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}"})
    from resource_explorer.web.app import app
    return TestClient(app), who


class TestRoutes:
    def test_either_user_may_re_decide_the_others_item_with_no_zones(self, web, registry, no_egeria_reads):
        client, who = web
        _seed(registry, decided=(ERIN, PETER, ERIN, PETER))
        who["user_id"] = ERIN
        r = client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["src/b"], "verdict": "accepted"})
        assert r.status_code == 200, r.text
        who["user_id"] = PETER
        r = client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["src/a"], "verdict": "accepted"})
        assert r.status_code == 200, r.text
        r = client.post("/api/curate/blueprint-verdicts/repo/p", json={
            "perspective": "deployment", "cluster_name": "a grouping of services", "verdict": "accepted"})
        assert r.status_code == 200, r.text

    def test_the_egeria_git_publish_is_not_refused_for_either_user(self, web, registry, no_egeria_reads):
        client, who = web
        _seed(registry, decided=(ERIN, PETER, ERIN, PETER))
        who["user_id"] = PETER
        r = client.post("/api/projects/p/architecture/publish")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["queued"] == 4 and out["refused"] == []

    def test_a_refused_verdict_says_why_and_records_nothing(self, web, registry, egeria):
        client, who = web
        _seed(registry)
        _zone_the_blueprint(registry, egeria)
        egeria.elements["g-a"] = _element("g-a", [SECURED], owners=[PETER])
        before = registry.list_component_verdict_history("repo", "p", "src/c")
        r = client.post("/api/projects/p/components/verdicts",
                        json={"scope_locators": ["src/c", "src/a"], "verdict": "rejected"})
        assert r.status_code == 403
        assert r.json()["detail"] == f"zone {SECURED} does not grant {ERIN} update in Egeria"
        assert registry.list_component_verdict_history("repo", "p", "src/c") == before   # nothing half-recorded

    def test_publish_skips_the_item_not_permitted_and_queues_the_rest(self, web, registry, egeria):
        import json as _json

        client, who = web
        _seed(registry)
        _zone_the_blueprint(registry, egeria)
        r = client.post("/api/projects/p/architecture/publish")
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["queued"] == 1
        assert [(x["kind"], x["key"]) for x in out["refused"]] == [("blueprint", BP)]
        target = _json.loads(registry.get_run(out["run_id"])["target"])
        assert target["paths"] == ["src/c"] and target["blueprints"] == []
        assert target["not_permitted"][0]["words"] == f"zone {SECURED} does not grant {ERIN} update in Egeria"

    def test_publish_is_refused_whole_only_when_nothing_is_permitted(self, web, registry, egeria):
        client, who = web
        _seed(registry)
        _zone_the_blueprint(registry, egeria)
        registry.record_materialized_component("repo", "p", "src/c", "SolutionComponent::repo::p::src/c", "g-c")
        r = client.post("/api/projects/p/architecture/publish")
        assert r.status_code == 403
        assert r.json()["detail"] == f"zone {SECURED} does not grant {ERIN} update in Egeria"


# ── 4. end to end: the press, the run, the next read ──────────────────────────────────────────────────


class TestPressToNextRead:
    """Drives enqueue_publish -> run_publish -> publish_plan/last_results with only Egeria's clients faked."""

    def _press_as(self, reg, user, between=None):
        import json as _json

        from resource_explorer.a2a_auth import current_caller
        from resource_explorer.workflows.curate import publish_item_access

        reset = _as(user)
        try:
            out = ap.enqueue_publish(reg, reg.get("p"), ap.publish_plan(reg, "p"), requested_by=user,
                                     authorize=lambda kind, key: _raise_unless(publish_item_access(reg, "p", kind, key)))
        finally:
            current_caller.reset(reset)
        if between:
            between()
        target = _json.loads(reg.get_run(out["run_id"])["target"])
        # The worker runs it as the requester with no token (run_queue._run_as_requester).
        reset = _as(user, source="queued-run")
        try:
            return out, ap.run_publish(reg, "p", target, out["activity_id"])
        finally:
            current_caller.reset(reset)

    def test_mixed_decided_by_no_zones_one_press_by_either_settles_the_plan(self, world, egeria, as_daemon):
        reg, fake, outbox = world
        _seed(reg, decided=(ERIN, PETER, ERIN, PETER))
        out, res = self._press_as(reg, ERIN)
        assert out["refused"] == []
        assert sorted(r["status"] for r in res) == ["done"] * 4
        assert ap.publish_plan(reg, "p")["nothing"] is True
        assert not [c for c in egeria.calls if c[0] in ("control", "account")], "no zone anywhere: nothing secured"

    def test_a_refused_item_is_its_own_result_row_and_the_rest_are_written(self, world, egeria, as_daemon):
        reg, fake, outbox = world
        _seed(reg)
        _zone_the_blueprint(reg, egeria)
        out, res = self._press_as(reg, ERIN)
        by = {r["key"]: r for r in res}
        assert by[BP]["status"] == ap.NOT_PERMITTED
        assert by[BP]["words"] == f"not permitted · zone {SECURED} does not grant {ERIN} update in Egeria"
        assert by["src/c"]["status"] == "done"
        assert outbox == [], "nothing was attached to the refused blueprint"
        last = ap.last_results(reg, "p")
        assert {i["key"]: i["status"] for i in last["items"]} == {BP: "not_permitted", "src/c": "done"}
        # The next read: C is in Egeria; the refused blueprint still waits for someone permitted to publish it.
        plan = ap.publish_plan(reg, "p")
        assert plan["components"]["to_write"] == []
        assert [b["key"] for b in plan["blueprints"]["to_write"]] == [BP]


def _raise_unless(d):
    from resource_explorer.workflows.curate import CurationDenied

    if not d.allowed:
        raise CurationDenied(d.reason)


# ── 5. the CLI uses the same function ─────────────────────────────────────────────────────────────────


def test_the_cli_publish_asks_curation_access(registry, monkeypatch):
    import typer

    from resource_explorer.cli import runs_commands as rc
    from resource_explorer.workflows import curate as wc

    _seed(registry)
    monkeypatch.setattr(rc, "_registry", lambda: registry)
    monkeypatch.setattr("resource_explorer.cli.session.require_and_activate", lambda console=None: None)
    monkeypatch.setattr("resource_explorer.run_queue.requested_by", lambda: ERIN)
    asked = []

    def spy(reg, entity_type, slug, scope, **kw):
        asked.append((entity_type, slug, scope, tuple(kw.get("operations") or ())))
        return wc.AccessDecision(False, f"zone {SECURED} does not grant {ERIN} update in Egeria",
                                 wc.BASIS_ZONE_REFUSED)
    monkeypatch.setattr(wc, "curation_access", spy)
    with pytest.raises(typer.Exit) as exc:
        rc.curate_materialize("p", "src/a", entity_type="repo")
    assert exc.value.exit_code == 3
    assert asked == [("repo", "p", "src/a", ("CREATE", "CLASSIFY", "PUBLISH"))]


# ── 6. round 2 (2026-10-10): every operation, members, run-time re-check, shapes, accounts, databases ──────

STEWARDS = {"associatedSecurityList": {"UPDATE_PROPERTIES": ["allUsers"], "PUBLISH": ["stewards"]}}


class TestRound2:
    def test_a_verdict_needs_update_but_publish_needs_every_operation_its_write_performs(self, web, registry,
                                                                                         monkeypatch):
        """HIGH: a control that lets anyone UPDATE but only stewards PUBLISH lets a non-steward record a
        verdict and refuses that person's Publish (the daemon does the write, so this is the only check)."""
        egeria = install(monkeypatch, zones=[SECURED])
        egeria.controls[SECURED] = STEWARDS
        client, who = web
        _seed(registry)
        r = client.post("/api/projects/p/components/verdicts", json={"scope_locators": ["src/c"], "verdict": "accepted"})
        assert r.status_code == 200, r.text
        r = client.post("/api/projects/p/architecture/publish")
        assert r.status_code == 403
        assert r.json()["detail"] == f"zone {SECURED} does not grant {ERIN} publish (zone change) in Egeria"

    def test_create_is_its_own_operation(self, registry, egeria, as_erin, monkeypatch):
        from resource_explorer.workflows.curate import publish_item_access

        monkeypatch.setattr("resource_explorer.egeria_identity.configured_publish_zones", lambda: [SECURED])
        egeria.controls[SECURED] = {"associatedSecurityList": {"CREATE": ["builders"], "DEFAULT": ["allUsers"]}}
        _seed(registry)
        d = publish_item_access(registry, "p", "component", "src/c")
        assert d.allowed is False and d.reason == f"zone {SECURED} does not grant {ERIN} create in Egeria"

    def test_a_blueprint_member_that_refuses_attach_refuses_the_blueprint(self, registry, egeria, as_erin):
        """MEDIUM: ATTACH is checked on both ends: the blueprint's members and child blueprints too."""
        from resource_explorer.workflows.curate import publish_item_access

        _seed(registry)
        registry.record_materialized_component("repo", "p", "src/b", "qn-b", "g-b")
        egeria.elements["g-b"] = _element("g-b", [SECURED], owners=[PETER])
        egeria.controls[SECURED] = {"associatedSecurityList": {"ATTACH": ["salesTeam"], "DEFAULT": ["allUsers"]}}
        d = publish_item_access(registry, "p", "blueprint", BP)
        assert d.allowed is False
        assert d.reason == f"b: zone {SECURED} does not grant {ERIN} attach in Egeria"
        assert publish_item_access(registry, "p", "component", "src/c").allowed is True

    def test_the_run_rechecks_an_element_the_write_adopted(self, registry, egeria, monkeypatch):
        """MEDIUM: a content-pack component adopted at write time had no GUID at the press; the run checks the
        element it actually resolved before the first write to it (the promotion), and writes nothing."""
        from resource_explorer.a2a_auth import current_caller

        _seed(registry)
        egeria.elements["g-pack"] = _element("g-pack", [SECURED], owners=["contentpack"])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["packStewards"]}}
        monkeypatch.setattr("resource_explorer.workflows.curate.materialize_component_if_accepted",
                            lambda reg, et, slug, path, verdict: {"status": "adopted_content_pack", "guid": "g-pack"})
        promoted = []
        monkeypatch.setattr("resource_explorer.workflows.curate.promote_to_publish_zones",
                            lambda guid: promoted.append(guid) or {"status": "promoted"})
        reset = _as(ERIN, source="queued-run")
        try:
            res = ap.run_publish(registry, "p", {"slug": "p", "paths": ["src/c"], "blueprints": []}, "run-x")
        finally:
            current_caller.reset(reset)
        assert [(r["key"], r["status"]) for r in res] == [("src/c", ap.NOT_PERMITTED)]
        assert res[0]["words"] == f"not permitted · zone {SECURED} does not grant {ERIN} update in Egeria"
        assert promoted == []

    def test_the_run_rechecks_when_a_zone_changed_after_the_press(self, world, egeria, as_daemon):
        """A press that was allowed does not license a write after the element was zoned (TOCTOU)."""
        reg, fake, outbox = world
        _seed(reg)
        _zone_the_blueprint(reg, egeria)
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["allUsers"]}}     # open at the press

        def secure():
            egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["salesTeam"]}}
        out, res = TestPressToNextRead()._press_as(reg, ERIN, between=secure)
        assert out["refused"] == []
        by = {r["key"]: r for r in res}
        assert by[BP]["status"] == ap.NOT_PERMITTED
        assert outbox == []

    def test_a_portal_curator_press_is_honoured_by_the_run(self, world, egeria, as_daemon):
        from resource_explorer.a2a_auth import current_caller

        reg, fake, outbox = world
        _seed(reg)
        _zone_the_blueprint(reg, egeria)
        reset = _as(ERIN, role="curator")
        try:
            out = ap.enqueue_publish(reg, reg.get("p"), ap.publish_plan(reg, "p"), requested_by=ERIN,
                                     authorize=lambda kind, key: None)
        finally:
            current_caller.reset(reset)
        import json as _json
        target = _json.loads(reg.get_run(out["run_id"])["target"])
        assert target["portal_role"] is True
        reset = _as(ERIN, source="queued-run")
        try:
            res = ap.run_publish(reg, "p", target, out["activity_id"])
        finally:
            current_caller.reset(reset)
        assert {r["key"]: r["status"] for r in res}[BP] != ap.NOT_PERMITTED

    def test_an_unknown_or_disabled_account_is_refused_even_where_a_zone_would_grant(self, registry, egeria,
                                                                                    as_erin):
        """MEDIUM: the account is read FIRST for a zoned element (validateZoneAccess :1103)."""
        from resource_explorer.workflows.curate import curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = _element("g-a", [ERIN])               # a zone named after the caller
        egeria.accounts[ERIN] = {**AVAILABLE, "userAccountStatus": "DISABLED"}
        d = curation_access(registry, "repo", "p", "src/a")
        assert d.allowed is False and d.reason == f"the Egeria account of {ERIN} is disabled"
        egeria.accounts[ERIN] = None
        d = curation_access(registry, "repo", "p", "src/a")
        assert d.allowed is False and d.reason == f"Egeria has no account for {ERIN}"


class TestZoneMembershipShapes:
    """MEDIUM: a ZoneMembership RE cannot parse is unreadable (deny), never "no zones" (open)."""

    @staticmethod
    def _zones(cp, header=False):
        from resource_explorer.catalogue_gateway import zones_of_element

        c = [{"classificationName": "ZoneMembership", "classificationProperties": cp}]
        el = {"elementGUID": "g", "elementHeader": {"classifications": c}} if header else {"elementGUID": "g",
                                                                                          "classifications": c}
        return zones_of_element(el)

    def test_the_renderings_egeria_produces_are_read(self):
        assert self._zones({"propertiesAsStrings": {"zoneMembership": "{0=a, 1=b}"}}) == ["a", "b"]
        assert self._zones({"propertiesAsStrings": {"zoneMembership": "[a, b]"}}) == ["a", "b"]
        assert self._zones({"propertyValueMap": {"zoneMembership": {"arrayCount": 0}}}) == []
        assert self._zones({"propertiesAsStrings": {"zoneMembership": "[a]"}}, header=True) == ["a"]

    def test_anything_else_raises(self):
        from resource_explorer.catalogue_gateway import GatewayError

        for cp in ({}, {"propertiesAsStrings": {"zoneMembership": "a,b"}},
                   {"propertyValueMap": {"zoneMembership": {"primitiveValue": "a"}}},
                   {"propertiesAsStrings": {"other": "x"}}):
            with pytest.raises(GatewayError):
                self._zones(cp)

    def test_an_unparsed_zone_denies(self, registry, egeria, as_erin):
        from resource_explorer.workflows.curate import BASIS_UNREADABLE, curation_access

        registry.record_materialized_component("repo", "p", "src/a", "qn", "g-a")
        egeria.elements["g-a"] = {"elementGUID": "g-a", "classifications": [{
            "classificationName": "ZoneMembership", "classificationProperties": {"propertiesAsStrings": {
                "zoneMembership": "sales-zone"}}}]}
        d = curation_access(registry, "repo", "p", "src/a")
        assert (d.allowed, d.basis) == (False, BASIS_UNREADABLE)


class TestResourceRoutes:
    """Database commit/publish, the catalog scope decisions and the repository publish use the same check."""

    @pytest.fixture
    def db(self, web, registry, monkeypatch):
        from resource_explorer.registry import DatabaseEntity
        from resource_explorer.web.routes import catalogue_scope as routes

        who = web[1]
        # The scope routes bind get_current_user at import: patch their own name too.
        monkeypatch.setattr(routes, "get_current_user",
                            lambda request: {"user_id": who["user_id"], "egeria_token": f"tok-{who['user_id']}"})

        registry.register_database(DatabaseEntity(slug="d", display_name="d", db_type="postgresql", host="h",
                                                  port=5432, database_name="d", egeria_asset_guid="g-db"))
        return web

    def _secure_db(self, egeria):
        egeria.elements["g-db"] = _element("g-db", [SECURED])
        egeria.controls[SECURED] = {"associatedSecurityList": {"DEFAULT": ["dbas"]}}

    def test_the_catalog_commit_and_scope_decisions_are_refused_in_a_secured_zone(self, db, egeria):
        client, who = db
        self._secure_db(egeria)
        r = client.post("/api/catalogue-scope/d/commit", json={})
        assert r.status_code == 403 and r.json()["detail"] == f"zone {SECURED} does not grant {ERIN} update in Egeria"
        r = client.put("/api/catalogue-scope/d/depth", json={"depth": "tables"})
        assert r.status_code == 403

    def test_the_database_publish_is_refused_in_a_secured_zone(self, db, egeria):
        client, who = db
        self._secure_db(egeria)
        r = client.post("/api/databases/d/publish", json={})
        assert r.status_code == 403 and SECURED in r.json()["detail"]

# ── 7. round 3 (2026-10-10): the CLI cannot forge a person action; an empty ZoneMembership is unzoned ──────


class TestRound3:
    @pytest.mark.parametrize("kind", ["publish_architecture", "curate_commit", "catalogue_commit"])
    def test_runs_enqueue_refuses_person_actions(self, registry, monkeypatch, kind):
        """MEDIUM: a verbatim target with {"portal_role": true} and any --requested-by would skip the run's
        access re-check or borrow another user's grants. Person actions are refused from `runs enqueue`."""
        import typer

        from resource_explorer.cli import runs_commands as rc

        monkeypatch.setattr(rc, "_registry", lambda: registry)
        with pytest.raises(typer.Exit) as exc:
            rc.runs_enqueue(kind, '{"slug": "p", "paths": ["src/a"], "portal_role": true}', requested_by=PETER)
        assert exc.value.exit_code == 1
        assert registry.list_runs(kind=kind, limit=5) == []

    def test_the_only_portal_role_in_a_target_comes_from_the_verified_caller(self):
        """`portal_role` is written only from the caller ContextVar: enqueue_publish, the database commit's
        selection (Brief Z2) and a queued investigation/work-list row (Brief Z3, `egeria_outbox.requester_fields`)."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "resource_explorer"
        writers = [str(p.relative_to(root)) for p in root.rglob("*.py") if PORTAL_ROLE_WRITE.search(p.read_text())]
        # Brief Z2: both from the caller; Brief Z3: requester_fields; Brief F round 5: curation rows
        # (curation_egeria._enqueue, from the caller, only for the row's own requester)
        assert sorted(writers) == ["architecture_publish.py", "catalogue_commit.py", "curation_egeria.py",
                                   "egeria_outbox.py"]

    def test_an_empty_or_null_zone_array_is_unzoned_not_unreadable(self):
        """LOW: Egeria treats a ZoneMembership with a null or empty array as unzoned (only propertiesAsStrings
        is serialized, and valueAsString of a null array is null)."""
        from resource_explorer.catalogue_gateway import zones_of_element

        def z(cp):
            return zones_of_element({"elementGUID": "g", "classifications": [
                {"classificationName": "ZoneMembership", "classificationProperties": cp}]})
        assert z({"propertiesAsStrings": {"zoneMembership": None}}) == []
        assert z({"propertiesAsStrings": {"zoneMembership": "{}"}}) == []
        assert z({"propertiesAsStrings": {"zoneMembership": "[]"}}) == []
        assert z({"propertyValueMap": {"zoneMembership": None}}) == []
        assert z({"propertyValueMap": {"zoneMembership": {"arrayValues": None}}}) == []
        assert z({"propertyValueMap": {"zoneMembership": {"arrayValues": {"propertiesAsStrings": {}}}}}) == []


#: Brief F round 6: a write of `portal_role` in any form, a dict literal key or a subscript assignment.
PORTAL_ROLE_WRITE = __import__("re").compile(
    r'["\']portal_role["\']\s*:|\[["\']portal_role["\']\]\s*=|\bportal_role\s*=(?!=)|setdefault\(\s*["\']portal_role')


def test_f_r6_the_portal_role_guard_sees_a_subscript_write_too():
    assert PORTAL_ROLE_WRITE.search('payload["portal_role"] = True')
    assert PORTAL_ROLE_WRITE.search("target['portal_role']=x")
    assert PORTAL_ROLE_WRITE.search('{"portal_role": True}')
    assert not PORTAL_ROLE_WRITE.search('payload.get("portal_role")')
    assert PORTAL_ROLE_WRITE.search('enqueue(portal_role=True)')
    assert PORTAL_ROLE_WRITE.search('target.setdefault("portal_role", True)')
    assert not PORTAL_ROLE_WRITE.search('if portal_role == True:')
