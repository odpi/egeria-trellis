"""Phase 6 — changing a classification, and moving Egeria with it.

`docs/investigation-classification-and-zoning-design.md` §5, the owner's point 7.

The tests are weighted toward TIGHTENING (shared -> private) because that is the
direction where a failure is invisible: the artifacts are already public, every
one has to move, and anything missed stays readable **while RE's own UI says
private** — Phase 3's filter is local and does not consult Egeria at all.

Loosening fails safely by comparison: something stays private that should be
shared, which the person who asked can see.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.investigation_reclassifier import (
    LATERAL, LOOSEN, TIGHTEN, InvestigationReclassifier, direction_of,
)

# See test_investigation_routes.py's identical pytestmark / conftest.py's
# mock_egeria_client_connections docstring — this file reaches the same
# unmocked marker-application client construction via EgeriaInvestigationPublisher.
pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections")


@contextmanager
def _as(user_id: str):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    reset = current_caller.set(
        CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
    try:
        yield
    finally:
        current_caller.reset(reset)


@contextmanager
def _egeria(zones_by_guid: dict, *, enforced=True, accept=True, kind_swap=True,
            kind_calls=None):
    """Stand in for Egeria's zone calls, tracking what each element holds.

    Also stubs the KIND swap, which is a separate Egeria round trip (declassify
    + classify + read back). These tests are about zones and ordering; that the
    swap is attempted, verified and reported SEPARATELY is covered explicitly by
    `test_the_kind_swap_is_verified_and_reported_separately` and
    `test_a_failed_kind_swap_does_not_claim_an_exposure`, so stubbing it here
    hides nothing.
    """
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors import investigation_reclassifier as rc

    real_kind = rc.InvestigationReclassifier._move_kind_classification

    def _stub(self, guid, f, t, h):
        if kind_calls is not None:
            kind_calls.append((guid, f, t))
        return (True, "") if kind_swap else (False, "stubbed failure")

    rc.InvestigationReclassifier._move_kind_classification = _stub
    before = (ident.current_zones, ident.set_zone_membership, ident._private_zone_state)
    ident._private_zone_state = {"status": "exists", "enforced": enforced,
                                 "zone": ident.private_zone(), "control_present": True}
    ident.current_zones = lambda guid, *a, **k: list(zones_by_guid.get(guid, []))

    def _set(guid, zones, **k):
        if not accept(guid) if callable(accept) else not accept:
            return False
        zones_by_guid[guid] = list(zones)
        return True

    ident.set_zone_membership = _set
    try:
        yield zones_by_guid
    finally:
        ident.current_zones, ident.set_zone_membership, ident._private_zone_state = before
        rc.InvestigationReclassifier._move_kind_classification = real_kind


def _inv(reg, name, cls, user="alice", **kw):
    with _as(user):
        return reg.create_investigation(name, project_classification=cls, **kw)


def _bind(reg, slug, guid="proj-1"):
    reg.set_investigation_egeria_project(slug, {
        "status": "linked", "egeria_project_guid": guid,
        "egeria_project_qualified_name": f"Project::{slug}"})


# ── direction ──────────────────────────────────────────────────────────────

def test_direction_is_about_visibility_not_alphabetical_order():
    P = ProjectRegistry.PRIVATE_CLASSIFICATIONS
    assert direction_of("Task", "PersonalProject", P) == TIGHTEN
    assert direction_of("PersonalProject", "Task", P) == LOOSEN
    assert direction_of("Task", "Campaign", P) == LATERAL
    assert direction_of("PersonalProject", "Experiment", P) == LATERAL, (
        "both are private — nothing about visibility changes")


# ── tightening: the dangerous direction ────────────────────────────────────

def test_tightening_moves_the_project_and_every_published_report(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Going Private", "Task")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    reg.record_egeria_survey("r1", "2026-09-08T00:00:00", "report-1")
    reg.record_egeria_survey("r1", "2026-09-08T01:00:00", "report-2")
    _bind(reg, inv["slug"])

    from resource_explorer.egeria_identity import private_zone
    zones = {"proj-1": ["egeria-runtime"], "report-1": ["egeria-runtime"],
             "report-2": ["egeria-runtime"]}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert res.direction == TIGHTEN
    assert res.ok, res.errors
    assert res.project_rezoned
    assert sorted(res.reports_moved) == ["report-1", "report-2"]
    assert zones["report-1"] == [private_zone(), "alice"]
    assert res.still_public == []
    assert reg.get_investigation(inv["slug"])["project_classification"] == "PersonalProject"


def test_a_report_that_cannot_be_moved_leaves_the_classification_unchanged(tmp_path):
    """The core safety property, and the reason local comes last.

    An artifact RE cannot pull out of the publish zones stays PUBLIC. Recording
    the classification anyway would make every RE screen say "private" over
    something Egeria is still serving to everyone — the disagreement with the
    reassuring half visible.
    """
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Stuck Report", "Task")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    reg.record_egeria_survey("r1", "2026-09-08T00:00:00", "report-stuck")
    _bind(reg, inv["slug"])

    zones = {"proj-1": ["egeria-runtime"], "report-stuck": ["egeria-runtime"]}
    with _egeria(zones, accept=lambda guid: guid != "report-stuck"):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert not res.ok
    assert res.local_applied is False, "RE would claim private over a public artifact"
    assert reg.get_investigation(inv["slug"])["project_classification"] == "Task"
    assert [u["guid"] for u in res.reports_unmovable] == ["report-stuck"]
    assert "report-stuck" in res.still_public
    assert any("still readable by everyone" in e for e in res.errors)


def test_an_unmovable_report_says_why_rather_than_just_failing(tmp_path):
    """"Egeria said no" sends someone hunting. Moving OUT of a zone needs PUBLISH
    on the ORIGINAL zones, which RE's account does not hold for the deployment's
    publish zones — confirmed live as OPEN-METADATA-SECURITY-403-005."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Why Stuck", "Task")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    reg.record_egeria_survey("r1", "2026-09-08T00:00:00", "report-x")
    _bind(reg, inv["slug"])

    with _egeria({"proj-1": ["egeria-runtime"], "report-x": ["egeria-runtime"]},
                 accept=lambda g: g != "report-x"):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    reason = res.reports_unmovable[0]["reason"]
    assert "egeria-runtime" in reason and "PUBLISH" in reason, reason


def test_tightening_is_refused_when_the_private_zone_is_not_enforced(tmp_path):
    """Same rule as a publish. An unenforced zone is an IGNORED zone, which is
    public — moving artifacts into it would look like protection and be none."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "No Zone", "Task")
    _bind(reg, inv["slug"])
    zones = {"proj-1": ["egeria-runtime"]}
    with _egeria(zones, enforced=False):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert not res.ok
    assert res.local_applied is False
    assert zones["proj-1"] == ["egeria-runtime"], "nothing may be moved"
    assert any("not confirmed to be enforced" in e for e in res.errors)


def test_tightening_an_ownerless_investigation_is_refused(tmp_path):
    """Zoning to an empty owner yields `[private_zone]` alone — readable by
    NOBODY, including the person whose investigation it is. Refusing beats making
    someone's work inaccessible to themselves."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "No Owner", "Task")
    _bind(reg, inv["slug"])
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET created_by = '' WHERE slug = ?",
                     (inv["slug"],))
    with _egeria({"proj-1": ["egeria-runtime"]}):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")
    assert not res.ok
    assert any("nobody to make it private to" in e for e in res.errors)


# ── loosening and lateral ──────────────────────────────────────────────────

def test_loosening_moves_everything_into_the_publish_zones(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Going Public", "PersonalProject")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    reg.record_egeria_survey("r1", "2026-09-08T00:00:00", "report-1")
    _bind(reg, inv["slug"])

    from resource_explorer.egeria_identity import private_zone, publish_zones
    zones = {"proj-1": [private_zone(), "alice"], "report-1": [private_zone(), "alice"]}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "Task")

    assert res.direction == LOOSEN
    assert res.ok, res.errors
    assert zones["report-1"] == publish_zones()


def test_a_lateral_change_moves_nothing(tmp_path):
    """Task -> Campaign is a real change with no visibility consequence. Re-zoning
    would be pointless work, and the connector rejects a no-op zone change
    anyway, so it would surface as a permissions error."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Lateral", "Task")
    _bind(reg, inv["slug"])
    # Starts in the DRAFT zone, deliberately. The first version of this test
    # started in `egeria-runtime`, which is what `publish_zones()` returns — so a
    # lateral change that wrongly re-zoned would have been a no-op and the test
    # passed a sabotage run that removed the guard entirely. The starting zone
    # has to differ from the target for "nothing moved" to mean anything.
    from resource_explorer.egeria_identity import draft_zone, publish_zones

    assert draft_zone() not in publish_zones(), "test premise: the two must differ"
    zones = {"proj-1": [draft_zone()]}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "Campaign")
    assert res.direction == LATERAL
    assert res.ok
    assert zones["proj-1"] == [draft_zone()], "a lateral change moved zones"
    assert res.still_public == []


def test_a_local_only_investigation_reclassifies_without_touching_egeria(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Local Only", "Task")
    zones: dict = {}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")
    assert res.ok
    assert zones == {}, "an unpromoted investigation wrote to Egeria"
    assert reg.get_investigation(inv["slug"])["project_classification"] == "PersonalProject"
    # An investigation with nothing in Egeria has nothing exposed. The first
    # version named "the investigation Project" here regardless — a false alarm,
    # and one that would teach people to ignore the field that exists to be read
    # when it is NOT empty. Found by the live test, not by these stubs.
    assert res.still_public == [], res.still_public


# ── vocabulary and idempotence ─────────────────────────────────────────────

def test_moving_to_experiment_requires_a_hypothesis(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "To Experiment", "Task")
    res = InvestigationReclassifier(reg).reclassify(inv["slug"], "Experiment")
    assert not res.ok
    assert any("requires a hypothesis" in e for e in res.errors)

    res = InvestigationReclassifier(reg).reclassify(
        inv["slug"], "Experiment", hypothesis="caching halves wall-clock")
    assert res.ok, res.errors
    assert reg.get_investigation(inv["slug"])["hypothesis"] == "caching halves wall-clock"


def test_moving_away_from_experiment_clears_the_hypothesis(tmp_path):
    """It is meaningless on the new classification and `_initial_classifications`
    would not send it — keeping it would be a stored value that silently never
    reaches Egeria."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "From Experiment", "Experiment", hypothesis="h")
    res = InvestigationReclassifier(reg).reclassify(inv["slug"], "Task")
    assert res.ok, res.errors
    assert reg.get_investigation(inv["slug"])["hypothesis"] == ""


def test_an_element_already_in_the_target_zones_is_not_an_error(tmp_path):
    """Egeria REJECTS a no-op zone change (OMAG-SERVER-SECURITY-403-005), so a
    re-run must not report a permissions failure for work already done."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Already There", "Task")
    _bind(reg, inv["slug"])
    from resource_explorer.egeria_identity import private_zone
    zones = {"proj-1": [private_zone(), "alice"]}
    with _egeria(zones, accept=False):   # any write would fail
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")
    assert res.ok, res.errors
    assert res.project_rezoned


def test_an_unverifiable_move_is_not_counted_as_moved(tmp_path):
    """`current_zones` returns [] when it could not tell. That is not proof of
    success, and treating it as one is how a tightening would report clean over
    artifacts nobody checked."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Unverifiable", "Task")
    _bind(reg, inv["slug"])
    from resource_explorer import egeria_identity as ident
    before = (ident.current_zones, ident.set_zone_membership, ident._private_zone_state)
    ident._private_zone_state = {"status": "exists", "enforced": True,
                                 "zone": ident.private_zone(), "control_present": True}
    ident.current_zones = lambda guid, *a, **k: []       # cannot tell, ever
    ident.set_zone_membership = lambda guid, zones, **k: True
    try:
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")
    finally:
        ident.current_zones, ident.set_zone_membership, ident._private_zone_state = before
    assert not res.ok
    assert res.project_rezoned is False
    assert res.local_applied is False


def test_never_asked_triggers_a_zone_check_rather_than_refusing(tmp_path, monkeypatch):
    """The zone state is per-process and its bootstrap runs in the WORKER role.
    A web or CLI process that never ran it would refuse every tightening while
    the zone was healthy — a self-inflicted outage that reads exactly like the
    real failure. Found by a live run in a fresh process, not by these stubs."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors import investigation_reclassifier as rc

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Fresh Process", "Task")
    _bind(reg, inv["slug"])

    # This test patches `ident` directly rather than using `_egeria`, so the
    # KIND swap would otherwise make a real Egeria call. It is about the zone
    # check, not the metadata half.
    monkeypatch.setattr(rc.InvestigationReclassifier, "_move_kind_classification",
                        lambda self, guid, f, t, h: (True, ""))

    asked = []

    def _fake_ensure(*a, **k):
        asked.append(True)
        ident._private_zone_state = {"status": "exists", "enforced": True,
                                     "zone": ident.private_zone(), "control_present": True}
        return ident._private_zone_state

    before = (ident.current_zones, ident.set_zone_membership, ident._private_zone_state)
    zones = {"proj-1": [ident.draft_zone()]}
    ident._private_zone_state = None                     # never asked
    ident.current_zones = lambda guid, *a, **k: list(zones.get(guid, []))
    ident.set_zone_membership = lambda guid, z, **k: zones.__setitem__(guid, list(z)) or True
    monkeypatch.setattr(ident, "ensure_private_zone_exists", _fake_ensure)
    try:
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")
    finally:
        ident.current_zones, ident.set_zone_membership, ident._private_zone_state = before

    assert asked, "an unknown zone state refused instead of checking"
    assert res.ok, res.errors


def test_a_member_whose_surveys_cannot_be_listed_blocks_a_tightening(tmp_path):
    """"We could not look" is not "there was nothing".

    If a member's published surveys cannot be enumerated, its reports are never
    checked and never moved. Logging and continuing — which this did first —
    means the tightening moves what it found, reports success, applies the
    classification, and leaves that member's reports PUBLIC while RE shows the
    investigation as private.

    That is the precise failure this whole feature exists to prevent,
    reproduced inside the code meant to prevent it. Prompted by the
    no-silent-success ratchet, which did not flag this site but made me look at
    it.
    """
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Unreadable Member", "Task")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    _bind(reg, inv["slug"])

    def _boom(slug):
        raise RuntimeError("registry unavailable")

    reg.get_egeria_surveys = _boom

    zones = {"proj-1": ["egeria-runtime"]}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert not res.ok
    assert res.local_applied is False, (
        "the classification was applied over a member whose reports were never checked")
    assert reg.get_investigation(inv["slug"])["project_classification"] == "Task"
    assert res.reports_unmovable, "an unreadable member vanished from the report"
    assert "r1" in res.reports_unmovable[0]["guid"]
    assert res.still_public, "nothing told the user what is still readable"


def test_an_unreadable_member_does_not_block_a_loosening(tmp_path):
    """The asymmetry, kept. Loosening leaves something PRIVATE that should be
    shared — annoying and visible — so an unreadable member is reported but does
    not have to stop the change the way it must on a tightening."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="r1", display_name="r1", github_url="https://github.com/o/r1",
                    description=""))
    inv = _inv(reg, "Loosen Unreadable", "PersonalProject")
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", "r1")
    _bind(reg, inv["slug"])
    reg.get_egeria_surveys = lambda slug: (_ for _ in ()).throw(RuntimeError("nope"))

    from resource_explorer.egeria_identity import private_zone
    zones = {"proj-1": [private_zone(), "alice"]}
    with _egeria(zones):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "Task")

    assert res.local_applied is True, "a loosening was blocked by a reporting problem"
    assert res.reports_unmovable, "the unreadable member was still not reported"
    assert res.still_public == [], "still_public means nothing on a loosening"


# ── the Egeria metadata half, and the Investigation marker ─────────────────

def test_only_the_kind_classification_is_removed(tmp_path, monkeypatch):
    """The project owner added an Egeria classification called `Investigation`
    (2026-09-08) as an orthogonal MARKER — "this Project is an investigation" —
    that coexists with the kind (PersonalProject / Task / ...) and does not drive
    zones.

    So a change of kind must remove the OLD KIND BY NAME and nothing else. A
    blanket "strip the classifications" would take the marker with it, along with
    `Anchors`, `Ownership` and `ZoneMembership`.

    EXECUTED, not read. The first version of this test grepped the source for
    `PROJECT_CLASSIFICATIONS` and passed a sabotage run that removed the guard
    entirely — the string survives elsewhere in the module. Same weakness that
    let a broken classification read-back pass five tests earlier in this work.
    """
    from resource_explorer.surveyors import investigation_reclassifier as rc

    removed, added = [], []

    class _FakeME:
        def __init__(self, *a, **k):
            pass

        # `apply_identity` authenticates the client it is handed.
        def create_egeria_bearer_token(self, *a, **k):
            pass

        def set_bearer_token(self, *a, **k):
            pass

        def declassify_metadata_element(self, guid, name, body=None):
            # Reproduces the REAL pyegeria behaviour measured 2026-09-08
            # (ISSUE-93): `body` is declared Optional and `.model_dump()` is
            # called on it unconditionally, so omitting it raises and the
            # classification is silently left in place. Without this the fake
            # is more forgiving than the system it stands for, and a sabotage
            # run that dropped the explicit body passed.
            if body is None:
                raise AttributeError("'NoneType' object has no attribute 'model_dump'")
            removed.append(name)

        def classify_metadata_element(self, guid, name, body=None):
            added.append((name, (body or {}).get("properties")))

        def get_metadata_element_by_guid(self, guid, **k):
            # The measured payload shape: kinds live under
            # elementHeader.projectKinds, and the marker sits there beside them.
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [
                                          {"classificationName": n} for n in
                                          ([x[0] for x in added] + ["Investigation"])]}}

    class _FakePM(_FakeME):
        def get_project_by_guid(self, guid, **k):
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": n}
                                                       for n in [x[0] for x in added]]}}

    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", _FakeME)
    monkeypatch.setattr("pyegeria.ProjectManager", _FakePM)
    ok, why = rc.InvestigationReclassifier(None)._move_kind_classification(
        "g1", "Task", "PersonalProject", "")

    assert ok, why
    assert removed == ["Task"], f"removed {removed} — the Investigation marker must survive"
    assert "Investigation" not in removed
    assert added and added[0][0] == "PersonalProject"


def test_a_marker_is_never_removed_even_if_it_is_the_current_kind_string(tmp_path, monkeypatch):
    """`Investigation` is not in PROJECT_CLASSIFICATIONS, so even a row whose
    stored classification somehow said `Investigation` must not cause the marker
    to be stripped — the removal is gated on the kind vocabulary, not on
    whatever the local row happens to hold."""
    from resource_explorer.surveyors import investigation_reclassifier as rc

    removed = []

    class _FakeME:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def set_bearer_token(self, *a, **k):
            pass

        def declassify_metadata_element(self, guid, name, body=None):
            # Reproduces the REAL pyegeria behaviour measured 2026-09-08
            # (ISSUE-93): `body` is declared Optional and `.model_dump()` is
            # called on it unconditionally, so omitting it raises and the
            # classification is silently left in place. Without this the fake
            # is more forgiving than the system it stands for, and a sabotage
            # run that dropped the explicit body passed.
            if body is None:
                raise AttributeError("'NoneType' object has no attribute 'model_dump'")
            removed.append(name)

        def classify_metadata_element(self, guid, name, body=None):
            pass

        def get_metadata_element_by_guid(self, guid, **k):
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": "Task"}]}}

    class _FakePM(_FakeME):
        def get_project_by_guid(self, guid, **k):
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": "Task"}]}}

    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", _FakeME)
    monkeypatch.setattr("pyegeria.ProjectManager", _FakePM)
    rc.InvestigationReclassifier(None)._move_kind_classification(
        "g1", "Investigation", "Task", "")
    assert removed == [], f"removed {removed} — a non-kind classification was stripped"


def test_the_kind_swap_is_verified_and_reported_separately(tmp_path, monkeypatch):
    """Executed, not read. Zones and the kind fail independently: a failed kind
    swap is a metadata inconsistency (Egeria says Task, RE says Personal), not an
    exposure — so it is reported without pretending visibility is wrong."""
    from resource_explorer.surveyors import investigation_reclassifier as rc

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Kind Swap", "Task")
    _bind(reg, inv["slug"])

    calls = []
    zones = {"proj-1": ["egeria-runtime"]}
    # Observed through the helper's own hook rather than a second patcher on the
    # same attribute. Doing both left the helper's stub installed permanently:
    # `_egeria.__exit__` restored the real method, then monkeypatch's teardown
    # put the STUB back, because that is what it had recorded. Every later test
    # in the file then got the stub — `test_both_kinds_at_once...` passed alone
    # and failed in suite, which is the signature of exactly this.
    with _egeria(zones, kind_calls=calls):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert calls == [("proj-1", "Task", "PersonalProject")]
    assert res.egeria_kind_changed is True
    assert res.ok, res.errors


def test_a_failed_kind_swap_does_not_claim_an_exposure(tmp_path, monkeypatch):
    """It must be an error — the two systems now disagree — but it must NOT read
    as "something is public". Zones went first and are correct."""
    from resource_explorer.surveyors import investigation_reclassifier as rc

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _inv(reg, "Kind Swap Fails", "Task")
    _bind(reg, inv["slug"])
    zones = {"proj-1": ["egeria-runtime"]}
    with _egeria(zones, kind_swap=False):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "PersonalProject")

    assert res.egeria_kind_changed is False
    assert res.project_rezoned is True, "zones are the safety property and went first"
    assert res.still_public == [], "a stale classification is not an exposure"
    assert any("metadata inconsistency, not an" in e for e in res.errors)


def test_both_kinds_at_once_is_reported_as_a_failure(monkeypatch):
    """Adding the new kind is not enough — the old one must be GONE.

    Egeria will happily carry both, and `_confirm_classification` only asks
    whether one named classification is present. So a declassify that silently
    did nothing produced a Project claiming to be both `Task` and
    `PersonalProject`, and the check passed. Seen live 2026-09-08, caused by
    pyegeria raising when `declassify_metadata_element` is called without a body
    (logged as ISSUE-93) — the call is now made with one, and this asserts the
    outcome rather than trusting it.
    """
    from resource_explorer.surveyors import investigation_reclassifier as rc

    class _FakeME:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def set_bearer_token(self, *a, **k):
            pass

        def declassify_metadata_element(self, guid, name, body=None):
            pass                       # silently does nothing, as the bug did

        def classify_metadata_element(self, guid, name, body=None):
            pass

    class _FakePM(_FakeME):
        def get_project_by_guid(self, guid, **k):
            # BOTH kinds present — the state the bug produced.
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": "Task"},
                                                       {"classificationName": "PersonalProject"}]}}

    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", _FakeME)
    monkeypatch.setattr("pyegeria.ProjectManager", _FakePM)
    ok, why = rc.InvestigationReclassifier(None)._move_kind_classification(
        "g1", "Task", "PersonalProject", "")

    assert ok is False
    assert "both kinds" in why, why


def test_the_reason_names_why_the_old_kind_survived(monkeypatch):
    """`both kinds` says WHAT is wrong; this says why, and the two causes need
    opposite responses.

    A declassify that RAISES is a code problem — pyegeria rejecting the request
    body is what ISSUE-93 is, and the explicit body in `_move_kind_classification`
    is the workaround for it. A declassify that returns cleanly and leaves the
    classification in place is a platform problem. The handler used to only log
    the exception, so both produced the same sentence and a reader had to go
    into this function to tell them apart.
    """
    from resource_explorer.surveyors import investigation_reclassifier as rc

    class _FakeME:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def set_bearer_token(self, *a, **k):
            pass

        def declassify_metadata_element(self, guid, name, body=None):
            raise AttributeError("'NoneType' object has no attribute 'model_dump'")

        def classify_metadata_element(self, guid, name, body=None):
            pass

    class _FakePM(_FakeME):
        def get_project_by_guid(self, guid, **k):
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": "Task"},
                                                       {"classificationName": "PersonalProject"}]}}

    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", _FakeME)
    monkeypatch.setattr("pyegeria.ProjectManager", _FakePM)
    ok, why = rc.InvestigationReclassifier(None)._move_kind_classification(
        "g1", "Task", "PersonalProject", "")

    assert ok is False
    assert "both kinds" in why, why
    assert "AttributeError" in why, "the cause must reach the caller, not only the log"
    assert "model_dump" in why, why


def test_a_clean_declassify_that_did_nothing_says_that_instead(monkeypatch):
    """The other cause, and it must NOT be described as an error — the call
    succeeded and Egeria kept the classification anyway, which points at the
    platform rather than at this code."""
    from resource_explorer.surveyors import investigation_reclassifier as rc

    class _FakeME:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def set_bearer_token(self, *a, **k):
            pass

        def declassify_metadata_element(self, guid, name, body=None):
            pass                       # no error, no effect

        def classify_metadata_element(self, guid, name, body=None):
            pass

    class _FakePM(_FakeME):
        def get_project_by_guid(self, guid, **k):
            return {"elementHeader": {"guid": guid, "type": {"typeName": "Project"},
                                      "projectKinds": [{"classificationName": "Task"},
                                                       {"classificationName": "PersonalProject"}]}}

    monkeypatch.setattr("pyegeria.omvs.metadata_expert.MetadataExpert", _FakeME)
    monkeypatch.setattr("pyegeria.ProjectManager", _FakePM)
    ok, why = rc.InvestigationReclassifier(None)._move_kind_classification(
        "g1", "Task", "PersonalProject", "")

    assert ok is False
    assert "reported no error" in why, why
    assert "Error" not in why.split("reported no error")[0], (
        "nothing raised, so the reason must not name an exception")
