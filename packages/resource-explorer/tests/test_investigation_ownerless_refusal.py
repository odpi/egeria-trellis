"""A Personal/Experiment investigation with no owner is never published (design ruling 2026-10-13).

Found live 2026-10-13: `egeria-understanding` is `PersonalProject` with `created_by=''`, and its
Egeria Project (re-promoted after the 2026-10-08 wipe) carries NO ZoneMembership, so a non-owner
reads it. `is_private_investigation` requires an owner, so an ownerless private investigation fell
through to "not private" and was published unzoned: the empty-identity leak class, where a read that
treats '' as "nothing to protect" exposes the thing it should protect.

The ruling: refuse, log it, record a proof row of its own kind, never create an unzoned element and
never fall back to one. The Egeria clients are `create_autospec(..., spec_set=True)` of the real
pyegeria classes, so a call the publisher makes is a call the real client would accept, and "nothing
was sent" is checked on every method.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import create_autospec

import pytest

from resource_explorer.registry import ProjectRegistry

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
def _zone_enforced_and_recorded(zoned: dict):
    """The private zone is enforced, and every zone write is recorded instead of sent."""
    from resource_explorer import egeria_identity as ident

    before_state, before_set = ident._private_zone_state, ident.set_zone_membership
    ident._private_zone_state = {"status": "exists", "enforced": True,
                                 "zone": ident.private_zone(), "control_present": True}
    ident.set_zone_membership = lambda guid, zones, **k: zoned.setdefault(guid, list(zones)) or True
    try:
        yield
    finally:
        ident._private_zone_state, ident.set_zone_membership = before_state, before_set


def _clients():
    from pyegeria import CollectionManager, ProjectManager

    pm = create_autospec(ProjectManager, spec_set=True, instance=True)
    cm = create_autospec(CollectionManager, spec_set=True, instance=True)
    pm.create_project.return_value = "proj-1"
    pm.get_project_by_guid.return_value = {}
    cm.create_collection.return_value = "coll-1"
    return pm, cm


def _sent(mock) -> list[str]:
    """Every pyegeria method the publisher called on this client."""
    return [c[0].split(".")[0].split("(")[0] for c in mock.mock_calls if c[0]]


def _ownerless(reg: ProjectRegistry, name: str, classification: str = "PersonalProject",
               hypothesis: str = "") -> dict:
    """A private investigation whose owner was never recorded — the live row's shape. Created as alice, then
    `created_by` blanked the way the 2026-09-07 ADD COLUMN left every older row."""
    with _as("alice"):
        inv = reg.create_investigation(name, project_classification=classification,
                                       hypothesis=hypothesis)
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET created_by = '' WHERE slug = ?", (inv["slug"],))
    return reg.get_investigation(inv["slug"])


@pytest.mark.parametrize("classification,hypothesis,word", [
    ("PersonalProject", "", "personal"),
    ("Experiment", "h", "experiment"),
])
def test_an_ownerless_private_investigation_is_refused_and_nothing_is_sent(
        tmp_path, classification, hypothesis, word):
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _ownerless(reg, "Legacy Personal", classification, hypothesis)
    assert inv["created_by"] == ""
    pm, cm = _clients()
    zoned: dict = {}

    with _zone_enforced_and_recorded(zoned):
        res = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=cm).promote(inv["slug"])

    pm.create_project.assert_not_called()
    assert _sent(pm) == [], f"sent to Egeria for an ownerless private investigation: {pm.mock_calls}"
    assert _sent(cm) == [], f"sent to Egeria for an ownerless private investigation: {cm.mock_calls}"
    assert zoned == {}
    assert not res.ok and res.project_guid == ""
    words = f"not published · this {word} investigation has no owner, so it cannot be made private"
    assert res.not_published == words
    assert words in res.errors
    assert res.as_dict()["not_published"] == words
    # Its own proof row: the refusal is recorded, not only returned.
    rows = reg.list_activity(entity_type="investigation", entity_slug=inv["slug"])
    assert [r["summary"] for r in rows] == [words]
    assert rows[0]["status"] == "error" and rows[0]["operation"] == "publish"
    assert '"private_without_owner"' in rows[0]["detail"]
    # The local row is untouched: still unbound, still ownerless — the owner decides.
    after = reg.get_investigation(inv["slug"])
    assert after["egeria_project_guid"] == "" and after["created_by"] == ""


def test_with_an_owner_it_publishes_with_the_private_zone_and_the_owner(tmp_path):
    """The companion: the refusal is narrow. An owned Personal investigation is created and zoned
    `[PRIVATE_ZONE, owner]` before anything is anchored to it."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    with _as("alice"):
        inv = reg.create_investigation("Alice Personal", project_classification="PersonalProject")
    assert inv["created_by"] == "alice"
    pm, cm = _clients()
    zoned: dict = {}

    with _zone_enforced_and_recorded(zoned):
        res = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=cm).promote(inv["slug"])

    pm.create_project.assert_called_once()
    assert res.project_guid == "proj-1"
    assert res.not_published == ""
    assert res.private_zoned is True, res.errors
    assert zoned == {"proj-1": [ident.PRIVATE_ZONE, "alice"]}
    assert reg.list_activity(entity_type="investigation", entity_slug=inv["slug"]) == []


def test_the_bound_write_paths_refuse_an_ownerless_private_investigation_too(tmp_path):
    """The live element is already bound. Relink, working-set creation, rename sync and disposition sets all
    write more of a 'private' investigation into an unzoned Project; each refuses and sends nothing."""
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _ownerless(reg, "Bound Legacy")
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_guid = 'proj-live' WHERE slug = ?",
                     (inv["slug"],))
    pm, cm = _clients()
    pub = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=cm)
    words = "not published · this personal investigation has no owner, so it cannot be made private"

    for res in (pub.ensure_working_set(inv["slug"]), pub.relink_members(inv["slug"]),
                pub.sync_project_properties(inv["slug"])):
        assert res.not_published == words and words in res.errors
    assert words in pub.sync_disposition_sets(inv["slug"])["errors"]
    assert _sent(pm) == [] and _sent(cm) == []


def test_a_reclassify_of_a_bound_ownerless_private_investigation_is_refused(tmp_path):
    """Reclassifying moves zones on the bound Project; with no owner there is nobody to zone it to, in either
    direction. Nothing moves, the local classification stays, and the refusal is a proof row."""
    from resource_explorer import egeria_identity as ident
    from resource_explorer.surveyors.investigation_reclassifier import InvestigationReclassifier

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _ownerless(reg, "Reclass Legacy")
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_guid = 'proj-live' WHERE slug = ?",
                     (inv["slug"],))
    moved: list = []
    before = ident.set_zone_membership
    ident.set_zone_membership = lambda guid, zones, **k: moved.append((guid, zones)) or True
    try:
        for to, hyp in (("Task", ""), ("Experiment", "h")):
            res = InvestigationReclassifier(reg).reclassify(inv["slug"], to, hyp)
            assert res.errors == [
                "not reclassified · this personal investigation has no owner, so it cannot be made private"]
            assert not res.local_applied
    finally:
        ident.set_zone_membership = before
    assert moved == []
    assert reg.get_investigation(inv["slug"])["project_classification"] == "PersonalProject"
    assert len(reg.list_activity(entity_type="investigation", entity_slug=inv["slug"])) == 2


def test_an_investigation_cannot_be_created_private_with_no_signed_in_owner(tmp_path):
    """The source: `create_investigation` records `current_user_id()`, which is '' for an anonymous caller
    or a thread that dropped the ContextVar. A Personal investigation created then has no owner from birth."""
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    with _as(""):
        with pytest.raises(ValueError, match="no owner"):
            reg.create_investigation("Anon Personal", project_classification="PersonalProject")
        # A shared kind needs no owner and is still created.
        assert reg.create_investigation("Anon Task", project_classification="Task")["created_by"] == ""


def test_repeated_resync_relink_passes_leave_one_refusal_row(tmp_path):
    """Resync's relink step walks every bound investigation each pass. An ownerless private one is refused every
    time, and the proof row must not grow by one per pass: the latest refusal for this investigation + action with
    the same words is the record, so a second identical pass writes nothing."""
    from resource_explorer.egeria_resync import EgeriaResync

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _ownerless(reg, "Looping Legacy")
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_guid = 'proj-live' WHERE slug = ?",
                     (inv["slug"],))
    words = "not published · this personal investigation has no owner, so it cannot be made private"

    for _ in range(2):
        out = EgeriaResync(registry=reg)._do_relink_investigation_members()
        assert words in out["per_investigation"][inv["slug"]]["errors"]

    rows = reg.list_activity(entity_type="investigation", entity_slug=inv["slug"])
    assert [r["summary"] for r in rows] == [words], rows


def test_a_press_after_a_loop_refusal_still_gets_its_words_and_shares_the_row(tmp_path):
    """A person's press always answers with the words; a repeat of the same refusal for the same action shares
    the one row, and a different action gets its own."""
    from resource_explorer.surveyors.egeria_investigation_publisher import (
        EgeriaInvestigationPublisher,
    )

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    inv = _ownerless(reg, "Pressed Legacy")
    pm, cm = _clients()
    pub = EgeriaInvestigationPublisher(reg, project_manager=pm, collection_manager=cm)
    first, second = pub.promote(inv["slug"]), pub.promote(inv["slug"])
    assert first.not_published and second.not_published == first.not_published
    with reg._conn() as conn:
        conn.execute("UPDATE investigations SET egeria_project_guid = 'proj-live' WHERE slug = ?",
                     (inv["slug"],))
    assert pub.relink_members(inv["slug"]).not_published
    rows = reg.list_activity(entity_type="investigation", entity_slug=inv["slug"])
    assert len(rows) == 2, [r["detail"] for r in rows]     # one for promote, one for relink_members
    assert _sent(pm) == [] and _sent(cm) == []
