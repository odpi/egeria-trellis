"""Phase 5 — zoning private investigations, and refusing to pretend.

`docs/investigation-classification-and-zoning-design.md` §3.

The whole feature turns on one thing being true: **a user who is not the owner
must be DENIED**. Everything else — the zone name, the classification, the
status field — passes just as happily when nothing is enforced at all, because
an unrecognised governance zone is *ignored* by Egeria's security connector
rather than treated as restrictive. So these tests are written around denial and
around refusing to claim protection that has not been established.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from resource_explorer.registry import Project, ProjectRegistry

# Brief I: these tests fake the Egeria clients; the publish under test is the worker's, a declared daemon job.
pytestmark = pytest.mark.usefixtures("as_daemon")


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
def _zone_enforced(flag: bool, status: dict | None = None):
    """Force the module-level private-zone state."""
    from resource_explorer import egeria_identity as ident
    before = ident._private_zone_state
    ident._private_zone_state = status if status is not None else (
        {"status": "created", "zone": ident.private_zone(), "enforced": flag})
    try:
        yield
    finally:
        ident._private_zone_state = before


# ── the zone list itself ───────────────────────────────────────────────────

def test_the_private_zone_list_carries_both_halves():
    """`[secured_zone, userId]`, and both are load-bearing for OPPOSITE reasons.

    The userId entry only ever GRANTS — `validateZoneAccess` returns true on
    `userId.equals(zoneName)` before consulting any list. Denial comes from the
    secured zone, which is what makes `securedZoneCount > 0` and sends everyone
    else to `return false`. A list with only the userId denies nobody.
    """
    from resource_explorer.egeria_identity import private_zone, private_zones

    assert private_zones("alice") == [private_zone(), "alice"]


def test_an_ownerless_private_zone_list_does_not_pretend_to_have_an_owner():
    from resource_explorer.egeria_identity import private_zone, private_zones

    assert private_zones("") == [private_zone()]


def test_enforcement_is_false_until_established():
    """Three different states collapse to False here — never asked, could not
    ask, and asked-and-absent — and only the last is "we checked".

    The asymmetry is the point. Guessing True when the control is missing
    publishes private work into a zone Egeria ignores: world-readable, while
    every RE screen says private. Guessing False only refuses a publish until
    somebody looks.
    """
    from resource_explorer.egeria_identity import private_zone_is_enforced, private_zone_status

    with _zone_enforced(False, {"status": "unknown"}):
        assert private_zone_is_enforced() is False
    with _zone_enforced(False, {"status": "not_authorized", "enforced": False}):
        assert private_zone_is_enforced() is False
    with _zone_enforced(False, {"status": "unconfirmed", "enforced": False}):
        assert private_zone_is_enforced() is False
    with _zone_enforced(True):
        assert private_zone_is_enforced() is True


def test_a_freshly_created_control_is_not_treated_as_enforced_yet():
    """The trap a store read-back cannot see, and the reason this is timed.

    The secrets store and the connector that enforces from it are not the same
    thing: the connector reloads on `refreshTimeInterval`. Measured live
    2026-09-07 — a control written at 02:53:11 read back from the store
    immediately, a privately-zoned element was **still readable by a non-owner
    six minutes later**, and denial began at seven.

    So "I wrote it and read it back" is exactly the kind of evidence that looks
    conclusive and is not. During that window a private publish would land in a
    zone nothing is enforcing.
    """
    import time
    from resource_explorer import egeria_identity as ident

    before = ident._private_zone_state
    try:
        ident._private_zone_state = {
            "status": "created", "zone": ident.private_zone(),
            "control_present": True, "enforced": False,
            "settle_after": time.time() + 300,
        }
        status = ident.private_zone_status()
        assert status["enforced"] is False
        assert status["status"] == "settling"
        assert 0 < status["settling_seconds_remaining"] <= 300
        assert ident.private_zone_is_enforced() is False

        # ...and enforced once the window has passed.
        ident._private_zone_state["settle_after"] = time.time() - 1
        assert ident.private_zone_is_enforced() is True
        assert ident.private_zone_status()["settling_seconds_remaining"] == 0
    finally:
        ident._private_zone_state = before


def test_a_control_already_present_is_trusted_without_waiting():
    """A control that was there before this process started has had at least as
    long as we have been up, so making every worker restart disable private
    publishing for twelve minutes would be caution with no safety in it."""
    from resource_explorer import egeria_identity as ident

    before = ident._private_zone_state
    try:
        ident._private_zone_state = {
            "status": "exists", "zone": ident.private_zone(),
            "control_present": True, "enforced": True,
            "basis": "control was already present when this process first looked",
        }
        assert ident.private_zone_is_enforced() is True
    finally:
        ident._private_zone_state = before


# ── who owns an artifact ───────────────────────────────────────────────────

def _repo(reg, slug):
    reg.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}",
                    description=""))


def test_private_ownership_is_visible_to_the_service_account(tmp_path):
    """The inverse-scoping rule, and the bug it exists to prevent.

    Phase 3's filter HIDES a private investigation from other callers. This
    read must SEE it whoever is asking, because it decides whether to protect
    the artifact. The worker publishes as the service account; if this were
    caller-scoped it would find no private investigation, conclude "not
    private", and publish into the public zones — a leak produced by the
    privacy machinery itself.
    """
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "shared-repo")
    with _as("alice"):
        inv = reg.create_investigation("Alice Private", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "shared-repo")

    for who in ("alice", "bob", ""):
        with _as(who):
            assert reg.private_owner_for_entity("repo", "shared-repo") == "alice", (
                f"private ownership was invisible to {who!r} — artifacts would publish public")


def test_a_hyphenated_slug_still_finds_its_private_owner(tmp_path):
    """Regression, and the reason this file exists rather than a couple of
    asserts bolted onto an existing test.

    The first version of `private_owner_for_entity` called `_normalize_slug`,
    which turns `egeria-python` into `egeria_python` — while
    `working_set_members` stores the slug verbatim, as `find_entity_investigations`
    and `inherited_egeria_project_context` both already assumed. So the lookup
    matched nothing for any repo with a hyphen in its name, which is most of
    them.

    The failure was silent AND it failed open: "no owner found" means "not
    private", so every hyphenated repo in a personal investigation would have
    published into the public zones while RE showed it as private. Review did
    not catch it; a test with a realistic slug did.
    """
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "egeria-python")
    with _as("alice"):
        inv = reg.create_investigation("Hyphen", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "egeria-python")
    assert reg.private_owner_for_entity("repo", "egeria-python") == "alice"


def test_a_shared_investigation_confers_no_private_owner(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "public-repo")
    with _as("alice"):
        inv = reg.create_investigation("Alice Task", project_classification="Task")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "public-repo")
    assert reg.private_owner_for_entity("repo", "public-repo") == ""


def test_an_ownerless_private_investigation_confers_no_owner(tmp_path):
    """A legacy row has no `created_by`, so there is nobody to zone TO. Zoning
    to `""` would produce `[private_zone]` alone — readable by nobody including
    its creator — so it must fall through to a normal publish instead."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "legacy-repo")
    with _as("alice"):
        inv = reg.create_investigation("Legacy", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "legacy-repo")
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET created_by = '' WHERE slug = ?", (inv["slug"],))
    assert reg.private_owner_for_entity("repo", "legacy-repo") == ""


def test_a_closed_private_investigation_still_protects_its_resources(tmp_path):
    """Closing an investigation means the work finished, not that its artifacts
    became public."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "closed-repo")
    with _as("alice"):
        inv = reg.create_investigation("Done", project_classification="Experiment",
                                       hypothesis="h")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "closed-repo")
        reg.close_investigation(inv["slug"])
    assert reg.private_owner_for_entity("repo", "closed-repo") == "alice"


def test_the_owner_is_stable_when_another_investigation_appears(tmp_path):
    """Oldest owner wins. Arbitrary, but STABLE — an artifact must not change
    zone because somebody else created an investigation, and picking the newest
    would let anyone re-home another user's artifacts by adding the repo to
    their own."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    _repo(reg, "contested-repo")
    for user, name in (("alice", "First"), ("bob", "Second")):
        with _as(user):
            inv = reg.create_investigation(name, project_classification="PersonalProject")
            ws = reg.get_or_create_working_set(inv["slug"])
            reg.add_working_set_member(ws["slug"], "repo", "contested-repo")
    assert reg.private_owner_for_entity("repo", "contested-repo") == "alice"


# ── the publish gate ───────────────────────────────────────────────────────

class _Reg:
    def __init__(self, owner=""):
        self._owner = owner
    def private_owner_for_entity(self, entity_type, slug):
        return self._owner
    def get_egeria_asset_guid(self, slug):
        return ""


def _result():
    from resource_explorer.surveyors.survey_report import SurveyResult
    return SurveyResult(resource_slug="r", project_display_name="R",
                        github_url="https://github.com/o/r")


def test_a_private_publish_is_refused_when_the_zone_is_not_enforced():
    """The loud half.

    An unconfirmed control means the zone name is unrecognised, therefore
    ignored, therefore world-readable — while RE's own UI still says private,
    because that filter is local. Publishing anyway cannot be un-done; refusing
    is visible and recoverable, so refusing wins.
    """
    from resource_explorer.surveyors.egeria_publisher import (
        EgeriaConnectionError, EgeriaPublisher,
    )

    pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner="alice"))
    with _zone_enforced(False, {"status": "not_authorized", "enforced": False,
                                "detail": "no operator rights", "remedy": "grant serverOperator"}):
        with pytest.raises(EgeriaConnectionError) as exc:
            pub.publish(_result())
    message = str(exc.value)
    assert "private" in message.lower()
    assert "grant serverOperator" in message, "the remedy must reach the user"
    assert "Nothing was written" in message


def test_a_public_resource_publishes_normally_when_the_private_zone_is_absent():
    """The gate must be narrow. A deployment with no private zone is the normal
    case for everyone not using private investigations, and it must not stop
    ordinary publishing."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner=""))
    with _zone_enforced(False, {"status": "not_authorized", "enforced": False}):
        try:
            pub.publish(_result())
        except Exception as exc:
            assert "private" not in str(exc).lower(), (
                "a public resource was blocked by the private-zone gate")


def test_an_explicit_zone_override_cannot_publicise_a_private_resource():
    """`zone_names` asks where a NORMAL publish should land. It is not
    authorisation to publish somebody's private work into a public zone, and a
    leaked artifact cannot be un-leaked — so privacy wins over the argument."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    from resource_explorer.egeria_identity import private_zone

    pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner="alice"))
    with _zone_enforced(True):
        try:
            pub.publish(_result(), zone_names=["egeria-runtime"])
        except Exception:
            pass  # the fake platform fails later; the zone decision is already made
    assert pub.zone_names == [private_zone(), "alice"], (
        f"an explicit override defeated privacy: {pub.zone_names}")


def test_ownership_follows_the_investigation_owner_not_the_publisher():
    """`Ownership` is what the curate authorisation reads. The worker publishes
    as the service account, so stamping the publishing identity would hand a
    user's private artifact to `erinoverview` — the owner would lose control of
    their own work to a service account."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner="alice"))
    pub._private_owner = "alice"
    stamped = {}

    def _fake_stamp(guid, owner, **kw):
        stamped[guid] = owner
        return {"owner": owner}

    import resource_explorer.egeria_identity as ident
    real_stamp, real_client = ident.stamp_published, ident.classification_client
    ident.stamp_published = _fake_stamp
    ident.classification_client = lambda *a, **k: object()
    try:
        pub._stamp_governance("g1")
    finally:
        ident.stamp_published, ident.classification_client = real_stamp, real_client
    assert stamped == {"g1": "alice"}


# ── curate must not be an un-labelled publish button ───────────────────────

def test_accepting_a_finding_does_not_publicise_a_private_element():
    """Accepting a finding is a judgement about quality. It is not a decision to
    make someone's personal investigation public, and the two must not be the
    same click."""
    from resource_explorer.egeria_identity import private_zone
    from resource_explorer.workflows import curate

    real = curate.__dict__.get("current_zones")
    import resource_explorer.egeria_identity as ident
    before_cz, before_set = ident.current_zones, ident.set_zone_membership
    moved = []
    before_rz, before_cl = ident.read_zones, ident.clear_zone_membership
    ident.current_zones = lambda guid, *a, **k: [private_zone(), "alice"]
    ident.read_zones = lambda guid, *a, **k: [private_zone(), "alice"]
    ident.set_zone_membership = lambda guid, zones, **k: moved.append(zones) or True
    ident.clear_zone_membership = lambda guid, **k: moved.append("clear") or True
    try:
        out = curate.promote_to_publish_zones("g1")
    finally:
        ident.current_zones, ident.set_zone_membership = before_cz, before_set
        ident.read_zones, ident.clear_zone_membership = before_rz, before_cl

    assert out["status"] == "skipped", out
    assert moved == [], "a private element was promoted into the publish zones"


# ── the second publish path ────────────────────────────────────────────────

def test_the_materializer_honours_privacy_too():
    """`arch_recovery/materializer.py` stamps zones independently of the
    publisher, so it is a second chance to leak — exactly the shape anchoring
    (Phase 4) is meant to remove. Until that lands, every path that stamps has
    to ask the same question, and this pins that it does.

    A component materialised from a repo in someone's personal investigation is
    derived from their private work. Born in the draft zone with the publishing
    identity as owner, it would be visible to every curator and owned by
    whoever happened to run the analysis.
    """
    import inspect

    from resource_explorer.surveyors.arch_recovery import materializer

    src = inspect.getsource(materializer)
    assert "private_owner_for_entity" in src, (
        "the materializer stamps zones without asking whether the resource is private")
    assert "private_zone_is_enforced" in src, (
        "the materializer would publish private work into an unenforced zone")


def test_the_materializer_refuses_rather_than_leaking_when_the_zone_is_unenforced(tmp_path):
    """Executed, not read. A source-reading test passed for the classification
    read-back while it was returning "could not tell" on every real call."""
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="priv-repo", display_name="p",
                    github_url="https://github.com/o/p", description=""))
    with _as("alice"):
        inv = reg.create_investigation("Alice M", project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "priv-repo")

    mat = ComponentMaterializer(registry=reg)
    # `_connect` is stubbed to blow up: the refusal must happen BEFORE any
    # Egeria contact, so reaching the platform at all is itself the failure.
    # This is not belt-and-braces — the first version of the guard sat next to
    # the stamping call, by which point the SolutionComponent had already been
    # created, and a sabotage run proved it by creating a real one.
    def _boom():
        raise AssertionError("reached Egeria before refusing a private materialize")
    mat._connect = _boom

    with _zone_enforced(False, {"status": "not_authorized", "enforced": False}):
        out = mat.materialize("repo", "priv-repo", "src/x", name="X")
    assert out and out.get("status") == "skipped", out
    assert "private" in (out.get("reason") or "")


def test_a_public_resource_still_materializes_normally(tmp_path):
    """The guard must be narrow. It reaches `_connect`, which is where a
    non-private materialize legitimately goes."""
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="pub-repo", display_name="p",
                    github_url="https://github.com/o/p", description=""))
    mat = ComponentMaterializer(registry=reg)
    reached = []
    mat._connect = lambda: reached.append(True)
    mat._find_element_guid = lambda qn: "existing-guid"

    with _zone_enforced(False, {"status": "not_authorized", "enforced": False}):
        out = mat.materialize("repo", "pub-repo", "src/y", name="Y")
    assert reached, "a public materialize was blocked by the privacy guard"
    assert out.get("status") == "already_materialized"


def test_never_asked_triggers_a_check_rather_than_a_refusal(monkeypatch):
    """"Nobody has asked yet" is a question, not an answer.

    The zone state is per-process and the bootstrap that fills it runs in the
    WORKER role. A deployment serving the web from a separate process would
    otherwise refuse every private publish while the zone was perfectly
    healthy — a self-inflicted outage that looks exactly like the real failure.
    """
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    asked = []

    def _fake_ensure(*a, **k):
        asked.append(True)
        ident._private_zone_state = {"status": "exists", "zone": ident.private_zone(),
                                     "control_present": True, "enforced": True}
        return ident._private_zone_state

    before = ident._private_zone_state
    monkeypatch.setattr(ident, "ensure_private_zone_exists", _fake_ensure)
    try:
        ident._private_zone_state = None          # never asked
        pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner="alice"))
        pub._require_enforced_private_zone("r")   # must NOT raise
        assert asked, "an unknown zone state refused instead of checking"
    finally:
        ident._private_zone_state = before


def test_a_non_string_owner_is_not_treated_as_private():
    """The value becomes a ZONE NAME. Anything that is not a userId string is a
    registry not answering the question, not an owner.

    Found by nine existing materializer tests going red at once: their registry
    is a `MagicMock`, whose `private_owner_for_entity` returns a truthy Mock, so
    every materialize looked private and was refused. That is a mock artefact —
    but the underlying gap is real, because `validateZoneAccess` compares the
    zone name to the caller's userId with `.equals`, so a non-string would match
    nobody at all while still marking the element private. Unreadable by
    everyone including its owner, and no error anywhere.
    """
    from unittest.mock import MagicMock

    from resource_explorer.surveyors.arch_recovery.materializer import ComponentMaterializer
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    mat = ComponentMaterializer(platform_url="https://fake", registry=MagicMock())
    assert mat._privacy_for("repo", "r") == ("", None)

    pub = EgeriaPublisher(platform_url="https://fake", registry=MagicMock())
    assert pub._resolve_private_owner("r") == ""

    # ...and a whitespace-only owner is no owner either.
    reg = MagicMock(private_owner_for_entity=MagicMock(return_value="   "))
    assert EgeriaPublisher(platform_url="https://fake", registry=reg)._resolve_private_owner("r") == ""


# ── Phase 4: anchoring, and what must NOT be zoned ─────────────────────────

def test_annotations_are_anchored_to_their_report():
    """The leak Phase 5 shipped with, closed by Phase 4.

    Measured live 2026-09-08: every published annotation came back with
    `anchorGUID: None` — its own anchor — and **no ZoneMembership of its own**.
    So a private investigation's SurveyReport was zoned while its annotations,
    which carry the actual findings, were world-readable.

    `parentGUID` alone does not anchor. `isOwnAnchor: False` + `anchorGUID`
    does, and then `validateUserForAnchorMemberRead`'s else branch evaluates the
    ANCHOR's classifications for an element carrying none of its own — verified
    live: the annotation was denied to a non-owner and read by its owner.
    """
    from resource_explorer.surveyors.annotation_props import build_annotation_body
    from resource_explorer.surveyors.survey_report import Annotation, AnnotationType

    ann = Annotation(annotation_type=AnnotationType.CLASSIFICATION,
                     summary="s", analysis_step="step")
    body = build_annotation_body(ann, "Annotation::x", "report-guid-1")
    assert body["isOwnAnchor"] is False
    assert body["anchorGUID"] == "report-guid-1", (
        "an annotation that is its own anchor inherits no zones and is public")


def test_the_shared_repo_asset_is_never_zoned_private():
    """§3.6, and the inverse failure to the annotation leak.

    The asset is `GitHubRepository::<github_url>` — ONE per repo, shared by
    every investigation referencing it. Zoning it private would hide a public
    repository from everyone else because one person added it to a personal
    investigation. A privacy feature that removes other people's access is a
    data-loss feature.

    An investigation zones what it PRODUCED (the report, and its annotations by
    anchor), never what it REFERENCES.
    """
    from resource_explorer.egeria_identity import draft_zones, private_zone
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://fake", registry=_Reg(owner="alice"))
    pub._private_owner = "alice"
    pub.zone_names = [private_zone(), "alice"]
    seen = {}

    import resource_explorer.egeria_identity as ident
    real_stamp, real_client = ident.stamp_published, ident.classification_client
    ident.stamp_published = lambda guid, owner, **kw: seen.setdefault(
        guid, {"owner": owner, "zones": list(kw.get("zones") or [])})
    ident.classification_client = lambda *a, **k: object()
    try:
        pub._stamp_governance("report-guid")                    # produced
        pub._stamp_governance("asset-guid", produced=False)     # referenced
    finally:
        ident.stamp_published, ident.classification_client = real_stamp, real_client

    assert seen["report-guid"]["zones"] == [private_zone(), "alice"]
    assert seen["report-guid"]["owner"] == "alice"
    assert seen["asset-guid"]["zones"] == draft_zones() == [], (
        f"the shared repo asset was zoned {seen['asset-guid']['zones']} — that hides "
        "a public repo from everyone else")
    assert seen["asset-guid"]["owner"] != "alice", (
        "the shared repo asset was handed to one investigation's owner")


def test_privacy_resolution_survives_a_thread_that_lost_the_caller(tmp_path):
    """A raw `threading.Thread` does NOT inherit contextvars, so
    `current_user_id()` is `''` inside one. `asyncio.to_thread` DOES copy the
    context — both measured 2026-09-08, prompted by dwolfson-fe hitting the
    same thing in the chat stream's producer thread, where turns were being
    written as `''`.

    That is the whole reason `private_owner_for_entity` is unscoped. Surveys run
    from the queue and the scheduler in raw threads; had this read been
    caller-scoped it would see no caller, find no private investigation,
    conclude "not private", and publish into the public zones. Every queued
    private survey would leak, and nothing would error.

    RE's investigation routes all use `asyncio.to_thread`, so the Phase 3
    visibility filter keeps the real caller there. This test pins the other
    half: the protection decision must not depend on having one.
    """
    import threading

    from resource_explorer.registry import Project, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="queued-repo", display_name="q",
                    github_url="https://github.com/o/q", description=""))
    with _as("alice"):
        inv = reg.create_investigation("Alice Queued Survey",
                                       project_classification="PersonalProject")
        ws = reg.get_or_create_working_set(inv["slug"])
        reg.add_working_set_member(ws["slug"], "repo", "queued-repo")

    from resource_explorer.registry import current_user_id

    box = {}

    def _worker():
        box["user"] = current_user_id()
        box["owner"] = reg.private_owner_for_entity("repo", "queued-repo")

    t = threading.Thread(target=_worker)
    t.start()
    t.join()

    assert box["user"] == "", "precondition: a raw thread should have lost the caller"
    assert box["owner"] == "alice", (
        "a queued survey could not tell the repo was private — it would publish public")


# ── which platform holds the control ───────────────────────────────────────
#
# `_platform_name` refuses rather than guessing when several platforms are
# catalogued, and before refusing it asks which of them already holds our
# control. That probe can itself fail, and the two outcomes are different
# claims: "the probe ran and none of them holds it" is a fact about Egeria,
# "the probe threw" means we do not know. Both refuse, so nothing here is a
# failure reported as success — but an operator who reads "none holds it" goes
# looking for a missing control instead of a broken Security Officer call.


class _FakeEgeriaCfg:
    view_server = "view-server"
    platform_url = "https://fake"
    user_id = "erinoverview"
    user_password = "secret"


def _two_platforms(monkeypatch, probe):
    """Catalogue two platforms and install `probe` as the SecurityOfficer."""
    import types

    class _FakeTech:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def get_elements(self, type_name, output_format="JSON"):
            return [{"properties": {"displayName": "Quickstart OMAG Server Platform"}},
                    {"properties": {"displayName": "Local OMAG Server Platform"}}]

    monkeypatch.delenv("EXPLORER_EGERIA_PLATFORM_NAME", raising=False)
    monkeypatch.setattr("pyegeria.EgeriaTech", _FakeTech)
    monkeypatch.setattr("resource_explorer.config.get_config",
                        lambda: types.SimpleNamespace(egeria=_FakeEgeriaCfg()))
    monkeypatch.setattr("pyegeria.omvs.security_officer.SecurityOfficer", probe)


def test_a_probe_that_could_not_run_is_not_reported_as_a_missing_control(monkeypatch):
    """Undetermined is not absent.

    The handler around the probe used to only log, so a probe that threw fell
    through to the "none of them holds it" refusal — a positive claim about
    Egeria made on no evidence, and one that sends the reader to the wrong
    problem.
    """
    from resource_explorer import egeria_identity as ident

    class _BrokenOfficer:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            raise ConnectionError("security officer unreachable")

    _two_platforms(monkeypatch, _BrokenOfficer)

    with pytest.raises(RuntimeError) as e:
        ident._platform_name()
    msg = str(e.value)
    assert "unknown, not no" in msg, msg
    assert "ConnectionError" in msg, "the cause must survive into the message"
    assert "security officer unreachable" in msg, msg
    assert "and none holds" not in msg, (
        "a probe that could not run must not claim none of them holds the control")


def test_a_probe_that_ran_and_found_nothing_still_says_so_plainly(monkeypatch):
    """The other side of the same fork: when the probe DOES run and none of the
    platforms holds the control, the refusal is entitled to say so — and must,
    since that is the case where an operator really does need to create one."""
    from resource_explorer import egeria_identity as ident

    class _EmptyOfficer:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def get_security_access_control(self, platform, zone):
            return {}                  # ran fine; nobody holds it

    _two_platforms(monkeypatch, _EmptyOfficer)

    with pytest.raises(RuntimeError) as e:
        ident._platform_name()
    msg = str(e.value)
    assert "none holds" in msg, msg
    assert "unknown, not no" not in msg, msg


def test_the_platform_holding_the_control_is_still_chosen(monkeypatch):
    """The probe's whole purpose, pinned so the error-path work above cannot
    quietly turn a working deployment into a refusal."""
    from resource_explorer import egeria_identity as ident

    class _OneHolderOfficer:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def get_security_access_control(self, platform, zone):
            if platform == "Local OMAG Server Platform":
                return {"associatedSecurityList": {"DEFAULT": ["nobody"]}}
            return {}

    _two_platforms(monkeypatch, _OneHolderOfficer)
    assert ident._platform_name() == "Local OMAG Server Platform"


# ── same displayName, different URL: the 2026-09-23 case ───────────────────
#
# A single quickstart boot can self-register TWICE under the SAME
# displayName (and the same `identifier`) — once per hostname it is
# reachable under (`host.docker.internal` beside `localhost`). Name alone
# cannot disambiguate that, even with EXPLORER_EGERIA_PLATFORM_NAME, since
# both entries share it. `platformURLRoot`, matched against this process's
# own configured `EGERIA_PLATFORM_URL`, can — and unlike the holder-probe
# it works before any control has ever been created.


def _two_identically_named_platforms(monkeypatch, configured_url="https://localhost:9443"):
    import types

    class _FakeEgeriaCfgWithUrl:
        view_server = "view-server"
        platform_url = configured_url
        user_id = "erinoverview"
        user_password = "secret"

    class _FakeTech:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def get_elements(self, type_name, output_format="JSON"):
            return [
                {
                    "properties": {
                        "displayName": "Quickstart OMAG Server Platform",
                        "additionalProperties": {
                            "platformURLRoot": "https://host.docker.internal:9443"},
                    },
                    "elementHeader": {"guid": "guid-docker-internal"},
                },
                {
                    "properties": {
                        "displayName": "Quickstart OMAG Server Platform",
                        "additionalProperties": {
                            "platformURLRoot": "https://localhost:9443"},
                    },
                    "elementHeader": {"guid": "guid-localhost"},
                },
            ]

    monkeypatch.delenv("EXPLORER_EGERIA_PLATFORM_NAME", raising=False)
    monkeypatch.setattr("pyegeria.EgeriaTech", _FakeTech)
    monkeypatch.setattr("resource_explorer.config.get_config",
                        lambda: types.SimpleNamespace(egeria=_FakeEgeriaCfgWithUrl()))


def test_identically_named_platforms_resolve_by_configured_url(monkeypatch):
    """Two catalogued platforms share a displayName — resolved anyway, by
    matching `platformURLRoot` against this process's own connection URL."""
    from resource_explorer import egeria_identity as ident

    _two_identically_named_platforms(monkeypatch)
    name, guid = ident._resolve_platform()
    assert name == "Quickstart OMAG Server Platform"
    assert guid == "guid-localhost", (
        "must resolve to the entry whose platformURLRoot matches "
        "EGERIA_PLATFORM_URL, not an arbitrary one of the identically-named pair")


def test_platform_name_alone_cannot_disambiguate_identically_named_platforms(monkeypatch):
    """`_platform_name()` still returns just the (ambiguous) name — callers
    that need the disambiguated identity must use `_resolve_platform()`."""
    from resource_explorer import egeria_identity as ident

    _two_identically_named_platforms(monkeypatch)
    assert ident._platform_name() == "Quickstart OMAG Server Platform"


def test_no_url_match_falls_back_to_the_holder_probe(monkeypatch):
    """If this process's configured URL matches neither catalogued platform,
    the existing holder-probe fallback still runs rather than skipping straight
    past it. Here the two platforms share a name, so the probe's own by-name
    call genuinely cannot tell them apart either — that is Egeria's real
    limitation, not a bug in the fallback, and refusing is the right answer."""
    from resource_explorer import egeria_identity as ident

    _two_identically_named_platforms(monkeypatch, configured_url="https://elsewhere:9443")

    class _BothLookLikeHoldersOfficer:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):
            pass

        def get_security_access_control(self, platform, zone):
            # Sees only the (identical) name — cannot distinguish the pair.
            return {"associatedSecurityList": {"DEFAULT": ["nobody"]}}

    monkeypatch.setattr("pyegeria.omvs.security_officer.SecurityOfficer",
                        _BothLookLikeHoldersOfficer)

    with pytest.raises(RuntimeError) as e:
        ident._resolve_platform()
    assert "ambiguous" in str(e.value)


def test_ensure_private_zone_exists_passes_the_resolved_guid_through(monkeypatch):
    """`ensure_private_zone_exists` must address the SPECIFIC platform it
    resolved, not fall back to a by-name lookup that is ambiguous by
    construction for two identically-named catalogued platforms."""
    from resource_explorer import egeria_identity as ident

    monkeypatch.setattr(ident, "_resolve_platform",
                        lambda: ("Quickstart OMAG Server Platform", "guid-localhost"))

    calls = []

    class _RecordingOfficer:
        def __init__(self, *a, **k):
            pass

        def create_egeria_bearer_token(self, *a, **k):   # the factory authenticates it (Brief I)
            return "tok-daemon"

        def get_security_access_control(self, platform, zone, **kwargs):
            calls.append(("get", platform, zone, kwargs))
            return {"associatedSecurityList": {"DEFAULT": ["nobody"]}}

        def set_security_access_control(self, platform, body, **kwargs):
            calls.append(("set", platform, kwargs))

    monkeypatch.setattr("pyegeria.omvs.security_officer.SecurityOfficer", _RecordingOfficer)
    ident._private_zone_state = None

    result = ident.ensure_private_zone_exists()

    assert result["status"] == "exists"
    get_call = [c for c in calls if c[0] == "get"][0]
    assert get_call[3] == {"platform_guid": "guid-localhost"}, (
        "the guid must reach the Security Officer client, or the by-name lookup "
        "is exactly as ambiguous as before")
