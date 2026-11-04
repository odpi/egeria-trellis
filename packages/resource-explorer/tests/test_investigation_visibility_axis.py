"""F56 — an investigation's visibility is its own axis; "Private investigation" is a preset (Option D).

BRIEF-F56-INVESTIGATION-VISIBILITY-AXIS-2026-11-04 §2.5. Until F56 the KIND decided who could see an investigation
(`PersonalProject`/`Experiment` were "private"), which named a visibility Egeria's kinds never say and made every
reclassify a silent visibility change. Now `visibility ∈ creator-only | shared` is stored on the row, the kind only
supplies its default at creation, and the list filter, the zoning, the ownerless refusal and the engine host's no-claim
rule (Q-O3) all read the column.

The pairs that prove it are the crossed ones: a SHARED PersonalProject (listed, claimable, published in the publish
zones) and a CREATOR-ONLY StudyProject (hidden, zoned to its creator, never claimed). Temp SQLite; Egeria is stubbed.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.usefixtures("mock_egeria_client_connections", "as_daemon")

CO, SH = ProjectRegistry.VISIBILITY_CREATOR_ONLY, ProjectRegistry.VISIBILITY_SHARED


@contextmanager
def _as(user_id: str):
    from resource_explorer.a2a_auth import CallerIdentity, current_caller
    reset = current_caller.set(CallerIdentity(user_id=user_id, egeria_token=None, auth_source="app-jwt"))
    try:
        yield
    finally:
        current_caller.reset(reset)


def _reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def _create(reg, name, kind, user="alice", **kw):
    with _as(user):
        return reg.create_investigation(name, project_classification=kind, **kw)


def _with_repo(reg, inv, slug):
    reg.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/o/{slug}", description=""))
    ws = reg.get_or_create_working_set(inv["slug"])
    reg.add_working_set_member(ws["slug"], "repo", slug)


# ── defaults by kind, at creation only ─────────────────────────────────────

@pytest.mark.parametrize("kind,expected", [
    ("PersonalProject", CO), ("Experiment", CO), ("StudyProject", SH), ("Task", SH), ("Campaign", SH),
])
def test_each_kind_gets_its_default_visibility_at_create(tmp_path, kind, expected):
    reg = _reg(tmp_path)
    extra = {"hypothesis": "h"} if kind == "Experiment" else {}
    inv = _create(reg, f"A {kind}", kind, **extra)
    assert inv["visibility"] == expected
    assert ProjectRegistry.default_visibility_for(kind) == expected


def test_an_explicit_visibility_overrides_the_kinds_default(tmp_path):
    reg = _reg(tmp_path)
    shared_personal = _create(reg, "Shared personal", "PersonalProject", visibility=SH)
    mine_study = _create(reg, "My study", "StudyProject", visibility=CO)
    assert shared_personal["visibility"] == SH
    assert mine_study["visibility"] == CO


def test_an_unknown_visibility_is_refused(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError, match="unknown visibility"):
        _create(reg, "Bad", "Task", visibility="private")


def test_creator_only_needs_a_signed_in_creator(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError, match="creator-only investigation needs an owner"):
        _create(reg, "Nobody's", "StudyProject", user="", visibility=CO)
    # A shared PersonalProject has nobody to be visible to only, so it needs no owner.
    assert _create(reg, "Anyone's", "PersonalProject", user="", visibility=SH)["visibility"] == SH


# ── the "Private investigation" preset ─────────────────────────────────────

def test_the_preset_stores_local_creator_only_and_no_kind(tmp_path):
    reg = _reg(tmp_path)
    inv = _create(reg, "Just looking", "", egeria_binding="local", visibility=CO)
    assert inv["project_classification"] == ""
    assert inv["egeria_binding"] == "local"
    assert inv["visibility"] == CO
    assert inv["is_private_investigation"] is True


def test_no_kind_is_allowed_only_while_local(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(ValueError, match="a kind is required"):
        _create(reg, "Headed for Egeria", "", egeria_binding="egeria")


def test_private_investigation_names_only_local_and_creator_only(tmp_path):
    """F56 §2.4: the words "private investigation" mean local + creator-only, nothing else."""
    reg = _reg(tmp_path)
    in_egeria_mine = _create(reg, "Personal in Egeria", "PersonalProject")
    local_shared = _create(reg, "Local shared", "Task", egeria_binding="local")
    assert in_egeria_mine["visibility"] == CO and in_egeria_mine["is_private_investigation"] is False
    assert local_shared["is_private_investigation"] is False


# ── the list filter follows visibility ─────────────────────────────────────

def test_the_list_filter_follows_visibility_not_kind(tmp_path):
    reg = _reg(tmp_path)
    _create(reg, "Shared personal", "PersonalProject", visibility=SH)
    _create(reg, "Hidden study", "StudyProject", visibility=CO)
    with _as("bob"):
        names = {i["display_name"] for i in reg.list_investigations()}
    assert "Shared personal" in names
    assert "Hidden study" not in names
    with _as("alice"):
        assert {"Shared personal", "Hidden study"} <= {i["display_name"] for i in reg.list_investigations()}


def test_entity_back_references_follow_visibility(tmp_path):
    reg = _reg(tmp_path)
    inv = _create(reg, "Hidden study", "StudyProject", visibility=CO)
    _with_repo(reg, inv, "r-hidden")
    with _as("bob"):
        assert reg.find_entity_investigations("repo", "r-hidden") == []
    with _as("alice"):
        assert [r["investigation_slug"] for r in reg.find_entity_investigations("repo", "r-hidden")] == [inv["slug"]]


# ── zoning and the engine host's Q-O3 rule follow visibility ───────────────

def test_zoning_owner_follows_visibility(tmp_path):
    """`private_owner_for_entity` is what the publisher's zoning, the private publish and the engine host's
    no-claim rule (Q-O3) all ask. A shared PersonalProject's resource has no owner to zone to (and is claimable);
    a creator-only StudyProject's resource is its creator's (and is never claimed)."""
    reg = _reg(tmp_path)
    shared_personal = _create(reg, "Shared personal", "PersonalProject", visibility=SH)
    _with_repo(reg, shared_personal, "r-shared")
    hidden_study = _create(reg, "Hidden study", "StudyProject", visibility=CO)
    _with_repo(reg, hidden_study, "r-mine")
    assert reg.private_owner_for_entity("repo", "r-shared") == ""
    assert reg.private_owner_for_entity("repo", "r-mine") == "alice"


def test_the_publisher_zones_by_visibility(tmp_path):
    from resource_explorer.surveyors.egeria_investigation_publisher import is_private_investigation

    reg = _reg(tmp_path)
    shared_personal = _create(reg, "Shared personal", "PersonalProject", visibility=SH)
    hidden_study = _create(reg, "Hidden study", "StudyProject", visibility=CO)
    assert is_private_investigation(shared_personal) is False
    assert is_private_investigation(hidden_study) is True


def test_the_engine_host_leaves_a_creator_only_studys_resource_unclaimed(tmp_path):
    """Q-O3 through the engine host's own check: it asks `private_owner_for_entity` for each target."""
    reg = _reg(tmp_path)
    hidden_study = _create(reg, "Hidden study", "StudyProject", visibility=CO)
    _with_repo(reg, hidden_study, "r-mine")
    shared_personal = _create(reg, "Shared personal", "PersonalProject", visibility=SH)
    _with_repo(reg, shared_personal, "r-shared")
    # The engine host's predicate, verbatim (engine_host.py: `if private_slug and
    # self.registry.private_owner_for_entity(kind, private_slug)` -> "private_target", unclaimed).
    assert bool(reg.private_owner_for_entity("repo", "r-mine")) is True
    assert bool(reg.private_owner_for_entity("repo", "r-shared")) is False


def test_the_ownerless_refusal_follows_visibility(tmp_path):
    from resource_explorer.surveyors.egeria_investigation_publisher import ownerless_private_words

    reg = _reg(tmp_path)
    personal_shared = _create(reg, "Ownerless shared personal", "PersonalProject", visibility=SH)
    study_mine = _create(reg, "Ownerless creator-only study", "StudyProject", visibility=CO)
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET created_by = ''")
    a = reg.get_investigation(personal_shared["slug"])
    b = reg.get_investigation(study_mine["slug"])
    assert ownerless_private_words(a) == ""
    words = ownerless_private_words(b)
    assert words == ("not published · this study is creator-only but has no recorded creator, so there is "
                     "nobody to zone it to")
    assert "private investigation" not in words


# ── promotion asks for the kind and keeps (or sets) the visibility ─────────

def test_promotion_requires_a_kind_for_an_undecided_one(tmp_path):
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher, apply_promotion_choice,
    )

    reg = _reg(tmp_path)
    inv = _create(reg, "Just looking", "", egeria_binding="local", visibility=CO)
    with pytest.raises(ValueError, match="choose a kind"):
        apply_promotion_choice(inv, {"visibility": CO})
    res = EgeriaInvestigationPublisher(reg, project_manager=object(), collection_manager=object()).promote(
        inv["slug"], choice={"visibility": CO})
    assert not res.project_guid
    assert any("choose a kind" in e for e in res.errors)
    # And with no choice at all, a local one is refused with words that say how to publish it.
    res = EgeriaInvestigationPublisher(reg, project_manager=object(), collection_manager=object()).promote(
        inv["slug"])
    assert any("created with no Egeria Project on purpose" in e for e in res.errors)


def test_promotion_choice_keeps_visibility_unless_changed(tmp_path):
    from resource_explorer.surveyors.egeria_investigation_publisher import apply_promotion_choice

    reg = _reg(tmp_path)
    inv = _create(reg, "Just looking", "", egeria_binding="local", visibility=CO)
    kept = apply_promotion_choice(inv, {"project_classification": "StudyProject"})
    assert (kept["project_classification"], kept["visibility"], kept["egeria_binding"]) == (
        "StudyProject", CO, "egeria")
    shared = apply_promotion_choice(inv, {"project_classification": "Task", "visibility": SH})
    assert shared["visibility"] == SH
    with pytest.raises(ValueError, match="requires a hypothesis"):
        apply_promotion_choice(inv, {"project_classification": "Experiment"})
    exp = apply_promotion_choice(inv, {"project_classification": "Experiment", "hypothesis": "h"})
    assert exp["hypothesis"] == "h"


class _PM:
    """Enough of ProjectManager for promote's Project create (no members, so no linking)."""
    def __init__(self):
        self.bodies = []

    def create_project(self, body=None, **_):
        self.bodies.append(body)
        return "proj-new"

    def get_project_by_guid(self, guid, **_):
        kind = ((self.bodies[-1] or {}).get("initialClassifications") or {})
        return {"elementHeader": {"projectKinds": [{"classificationName": k} for k in kind]}}


class _CM:
    def create_collection(self, body=None, **_):
        return "coll-new"

    def __getattr__(self, _name):
        return lambda *a, **k: None


def test_promoting_a_private_investigation_zones_it_to_its_creator(tmp_path, monkeypatch):
    """The owner's read-back step, stubbed: kind asked, Project created with that classification, zoned per
    visibility (creator-only -> [private zone, owner])."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors.egeria_investigation_publisher import EgeriaInvestigationPublisher

    reg = _reg(tmp_path)
    inv = _create(reg, "Just looking", "", egeria_binding="local", visibility=CO)
    zoned = {}
    monkeypatch.setattr(ident, "_private_zone_state", {"status": "exists", "enforced": True,
                                                       "zone": ident.private_zone(), "control_present": True})
    monkeypatch.setattr(ident, "set_zone_membership", lambda guid, zones, **k: zoned.setdefault(guid, zones) or True)
    pm = _PM()
    with _as("alice"):
        res = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=_CM()).promote(
            inv["slug"], choice={"project_classification": "StudyProject"})
    assert res.project_guid == "proj-new"
    assert "StudyProject" in (pm.bodies[0].get("initialClassifications") or {})
    assert res.private_zoned is True
    assert zoned["proj-new"] == [ident.private_zone(), "alice"]
    # Nothing is stored by promote itself: the route records the choice once a Project exists.
    assert reg.get_investigation(inv["slug"])["project_classification"] == ""


# ── reclassification changes the kind only ─────────────────────────────────

def test_a_local_reclassify_keeps_visibility(tmp_path):
    from resource_explorer.surveyors.investigation_reclassifier import InvestigationReclassifier

    reg = _reg(tmp_path)
    inv = _create(reg, "Experiment", "Experiment", hypothesis="h")
    with _as("alice"):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "StudyProject")
    assert res.ok, res.errors
    assert res.as_dict()["visibility_kept"] is True
    with _as("alice"):
        after = reg.get_investigation(inv["slug"])
    assert (after["project_classification"], after["visibility"]) == ("StudyProject", CO)


def test_a_promoted_reclassify_keeps_visibility_moves_no_zone_and_swaps_the_kind(tmp_path, monkeypatch):
    """Experiment -> StudyProject that stays creator-only: LATERAL. The Project's zones are untouched, its kind
    classification IS swapped in Egeria (until F56 a lateral change swapped nothing), and RE keeps creator-only."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors import investigation_reclassifier as rc

    reg = _reg(tmp_path)
    inv = _create(reg, "Experiment", "Experiment", hypothesis="h")
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-1", "egeria_project_qualified_name": "Project::x"})
    kind_calls, zone_calls = [], []
    monkeypatch.setattr(rc.InvestigationReclassifier, "_move_kind_classification",
                        lambda self, g, f, t, h: kind_calls.append((g, f, t)) or (True, ""))
    monkeypatch.setattr(ident, "set_zone_membership", lambda *a, **k: zone_calls.append(a) or True)
    with _as("alice"):
        res = rc.InvestigationReclassifier(reg).reclassify(inv["slug"], "StudyProject")
        after = reg.get_investigation(inv["slug"])
    assert res.direction == rc.LATERAL
    assert res.ok, res.errors
    assert kind_calls == [("proj-1", "Experiment", "StudyProject")]
    assert zone_calls == []
    assert (after["project_classification"], after["visibility"]) == ("StudyProject", CO)


def test_a_failed_lateral_kind_swap_leaves_the_investigation_as_it_was(tmp_path, monkeypatch):
    from resource_explorer.surveyors import investigation_reclassifier as rc

    reg = _reg(tmp_path)
    inv = _create(reg, "Task", "Task")
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-1", "egeria_project_qualified_name": "Project::x"})
    monkeypatch.setattr(rc.InvestigationReclassifier, "_move_kind_classification",
                        lambda self, g, f, t, h: (False, "refused"))
    res = rc.InvestigationReclassifier(reg).reclassify(inv["slug"], "Campaign")
    assert not res.ok and not res.local_applied
    assert reg.get_investigation(inv["slug"])["project_classification"] == "Task"


def test_changing_only_the_visibility_in_the_dialog_tightens(tmp_path):
    from resource_explorer.surveyors.investigation_reclassifier import TIGHTEN, InvestigationReclassifier

    reg = _reg(tmp_path)
    inv = _create(reg, "Study", "StudyProject")
    with _as("alice"):
        res = InvestigationReclassifier(reg).reclassify(inv["slug"], "StudyProject", visibility=CO)
        after = reg.get_investigation(inv["slug"])
    assert res.direction == TIGHTEN
    assert res.ok, res.errors
    assert after["visibility"] == CO
    with _as("alice"):
        same = InvestigationReclassifier(reg).reclassify(inv["slug"], "StudyProject", visibility=CO)
    assert same.errors == ["already classified StudyProject, creator-only"]


def test_set_investigation_classification_keeps_visibility_by_default(tmp_path):
    reg = _reg(tmp_path)
    inv = _create(reg, "Personal", "PersonalProject")
    with _as("alice"):
        assert reg.set_investigation_classification(inv["slug"], "Task")["visibility"] == CO
        assert reg.set_investigation_classification(inv["slug"], "Task", visibility=SH)["visibility"] == SH


# ── the edit form's PATCH ──────────────────────────────────────────────────

def test_visibility_is_editable_until_promoted(tmp_path):
    reg = _reg(tmp_path)
    inv = _create(reg, "Study", "StudyProject")
    with _as("alice"):
        assert reg.update_investigation(inv["slug"], visibility=CO)["visibility"] == CO
    reg.set_investigation_egeria_project(inv["slug"], {
        "status": "linked", "egeria_project_guid": "proj-1", "egeria_project_qualified_name": "Project::x"})
    with _as("alice"), pytest.raises(ValueError, match="use Reclassify"):
        reg.update_investigation(inv["slug"], visibility=SH)


# ── the migration and its backfill ─────────────────────────────────────────

def test_the_backfill_marks_the_old_private_kinds_creator_only_once(tmp_path):
    """A registry from before F56 (no `visibility` column) gains it on init: PersonalProject and Experiment rows
    read creator-only — exactly the old rule, so nothing changes on day one — and every other row reads shared.
    A second init is a no-op: it never re-applies the backfill over a later choice."""
    db = str(tmp_path / "t.db")
    reg = ProjectRegistry(db_path=db)
    for name, kind in [("p", "PersonalProject"), ("e", "Experiment"), ("s", "StudyProject"), ("t", "Task")]:
        _create(reg, name, kind, **({"hypothesis": "h"} if kind == "Experiment" else {}))
    with reg._conn() as conn:
        conn.execute("ALTER TABLE investigations DROP COLUMN visibility")
    assert "visibility" not in reg._get_table_columns(reg._conn().__enter__(), "investigations")

    reg2 = ProjectRegistry(db_path=db)
    with reg2._conn() as conn:
        rows = {r["display_name"]: r["visibility"]
                for r in conn.execute("SELECT display_name, visibility FROM investigations").fetchall()}
    assert rows == {"p": CO, "e": CO, "s": SH, "t": SH}

    with reg2._conn() as conn:
        conn.execute("UPDATE investigations SET visibility = 'shared' WHERE display_name = 'p'")
    reg3 = ProjectRegistry(db_path=db)
    with reg3._conn() as conn:
        assert conn.execute("SELECT visibility FROM investigations WHERE display_name = 'p'").fetchone()[0] == SH


def test_a_fresh_registry_has_the_column_with_its_default(tmp_path):
    reg = _reg(tmp_path)
    with reg._conn() as conn:
        assert "visibility" in reg._get_table_columns(conn, "investigations")
        conn.execute("INSERT INTO investigations (slug, display_name, created_at) VALUES ('raw', 'raw', 'now')")
        assert conn.execute("SELECT visibility FROM investigations WHERE slug = 'raw'").fetchone()[0] == SH


# ── served words ───────────────────────────────────────────────────────────

def test_the_classifications_route_serves_the_visibility_vocabulary():
    import asyncio

    from resource_explorer.web.routes.investigations import list_classifications

    body = asyncio.run(list_classifications())
    assert [v["name"] for v in body["visibilities"]] == [CO, SH]
    assert body["visibilities"][0]["consequence"] == (
        "creator-only: only you see it in Resource Explorer; published zoned to you")
    assert body["default_visibility_by_kind"] == {
        "PersonalProject": CO, "Task": SH, "StudyProject": SH, "Campaign": SH, "Experiment": CO}
    preset = body["private_preset"]
    assert preset["label"] == "Private investigation — mine, not in Egeria"
    assert (preset["egeria_binding"], preset["visibility"], preset["project_classification"]) == ("local", CO, "")
