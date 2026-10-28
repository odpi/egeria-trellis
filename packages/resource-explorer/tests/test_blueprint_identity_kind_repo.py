"""A SolutionBlueprint's Egeria identity is KIND + REPOSITORY, never the root cluster's name.

Architect's ruling 2026-10-08 (docs/design-notes/DESIGN-BLUEPRINT-NAMING-WHAT-IT-REPRESENTS.md §2a):
`SolutionBlueprint::<type>::<slug>::<kind>[::<identifier>]`, the identifier a person's and only for a second
blueprint of one kind; the two older forms (carrying the cluster name) are adopted, never created. Recording
fakes for Egeria; a temp SQLite registry; nothing live.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer import blueprint_kinds as bk
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.arch_recovery.blueprint_materializer import (
    BlueprintAmbiguous,
    BlueprintIdentifierNeeded,
    BlueprintMaterializationError,
    BlueprintMaterializer,
)

# Brief I: a blueprint or port is written by a queued publish: RE's daemon on the requester's behalf.
pytestmark = pytest.mark.usefixtures("as_daemon")

NEW = "11111111-1111-1111-1111-111111111111"
OLD = "22222222-2222-2222-2222-222222222222"
G_WEB = "44444444-4444-4444-4444-444444444444"
ROOT = "OMAG-Server-Platform"
LEGACY_556 = f"SolutionBlueprint::repo::egeria_git::Deployment Blueprint::{ROOT}"
LEGACY_PRE = f"SolutionBlueprint::repo::egeria_git::deployment::{ROOT}"
SENTENCE = "a Deployment Blueprint already exists for egeria_git · give this one an identifier"
HELD = "a Deployment Blueprint already exists for egeria_git (held by {}) · give this one an identifier"


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


DISPLAY = "Egeria Deployment Blueprint"


def _element(qn, guid, display=DISPLAY, props=None, type_name="SolutionBlueprint"):
    return {"elementHeader": {"guid": guid, "type": {"typeName": type_name}},
            "properties": {"qualifiedName": qn, "displayName": display, "additionalProperties": props or {}}}


def _m(reg, found=None, search_error=None):
    """`found`: qn -> guid, or qn -> {"guid", "display", "props", "type"} for an element with a displayName and
    provenance. The recording fake answers the search AND the read-back by guid."""
    m = BlueprintMaterializer(platform_url="https://fake", registry=reg)
    m._solution_architect = MagicMock()
    m._automated_curation = MagicMock()
    m._connect = MagicMock()
    searched = []
    entries = {qn: (v if isinstance(v, dict) else {"guid": v}) for qn, v in (found or {}).items()}

    def find(qn, property_name=None, type_name=None):
        searched.append((qn, property_name, type_name))
        if search_error:
            raise search_error
        return [entries[qn]["guid"]] if qn in entries else []

    def read(guid):
        for qn, e in entries.items():
            if e["guid"] == guid:
                return _element(qn, guid, e.get("display", DISPLAY), e.get("props"), e.get("type", "SolutionBlueprint"))
        return "no elements"

    m._automated_curation.get_guid_for_name.side_effect = find
    m._solution_architect.get_solution_blueprint_by_guid.side_effect = read
    m._solution_architect.create_solution_blueprint.return_value = NEW
    m.searched = searched
    return m


def _created(m):
    return [c[0][0]["properties"] for c in m._solution_architect.create_solution_blueprint.call_args_list]


def _make(m, cluster=ROOT, **kw):
    return m.materialize_blueprint_element("repo", "egeria_git", "deployment", cluster,
                                           display_name="Egeria Deployment Blueprint", **kw)


def _activity(reg):
    return [a["summary"] for a in reg.list_activity(entity_type="repo", entity_slug="egeria_git")]


def test_a_deployment_blueprint_is_kind_and_repository_with_the_owners_identifier():
    m = _m(MagicMock(get_materialized_blueprint=MagicMock(return_value=None)))
    out = _make(m)
    props = _created(m)[0]
    assert out["qualified_name"] == props["qualifiedName"] == "SolutionBlueprint::repo::egeria_git::deployment"
    assert props["identifier"] == "EGERIA-GIT-DEPLOYMENT"


def test_a_second_blueprint_of_a_kind_without_an_identifier_is_refused_and_creates_nothing(reg):
    first = _m(reg)
    _make(first, cluster=ROOT)
    m = _m(reg)
    with pytest.raises(BlueprintIdentifierNeeded) as exc:
        _make(m, cluster="web")
    assert str(exc.value) == HELD.format(ROOT)       # the cache row's cluster, read at refusal time
    assert _created(m) == [] and m.searched == []
    m._connect.assert_not_called()


def test_a_second_blueprint_with_an_identifier_creates_the_fifth_segment_and_never_the_cluster_name(reg):
    _make(_m(reg), cluster=ROOT)
    m = _m(reg)
    out = _make(m, cluster="web", identifier="  Servers ")
    props = _created(m)[0]
    assert out["qualified_name"] == "SolutionBlueprint::repo::egeria_git::deployment::Servers"
    assert props["identifier"] == "EGERIA-GIT-DEPLOYMENT-SERVERS"
    assert "web" not in props["qualifiedName"]
    # both blueprints are cached, each under its own identity, the cluster name kept on the row
    rows = reg.get_materialized_blueprints("repo", "egeria_git")
    assert {r["qualified_name"] for r in rows.values()} == {
        "SolutionBlueprint::repo::egeria_git::deployment",
        "SolutionBlueprint::repo::egeria_git::deployment::Servers"}
    assert rows["deployment::web"]["cluster_name"] == "web"


def test_an_identifier_already_in_use_is_refused(reg):
    _make(_m(reg), cluster=ROOT)
    _make(_m(reg), cluster="web", identifier="Servers")
    m = _m(reg)
    with pytest.raises(BlueprintMaterializationError, match="already used"):
        _make(m, cluster="db", identifier="Servers")
    assert _created(m) == []


def test_a_row_for_a_cluster_that_no_longer_exists_is_taken_over_not_a_second_blueprint(reg):
    _make(_m(reg), cluster=ROOT)
    m = _m(reg)
    out = _make(m, cluster="renamed", live_clusters={"renamed"})
    assert out["qualified_name"] == "SolutionBlueprint::repo::egeria_git::deployment"
    assert set(reg.get_materialized_blueprints("repo", "egeria_git")) == {"deployment::renamed"}


@pytest.mark.parametrize("legacy", [LEGACY_556, LEGACY_PRE], ids=["556-form", "pre-556-form"])
def test_a_legacy_named_blueprint_is_adopted_not_duplicated_and_the_owner_is_told(reg, legacy):
    m = _m(reg, found={legacy: OLD})
    out = _make(m)
    assert out == {"status": "already_materialized", "guid": OLD, "qualified_name": legacy}
    assert _created(m) == []
    assert _activity(reg) == [f"adopted legacy-named blueprint {OLD} · delete it in Egeria to recreate "
                              "under the new name"]
    row = reg.list_activity(entity_type="repo", entity_slug="egeria_git")[0]
    assert row["entity_name"] == DISPLAY and row["entity_location"] == legacy    # on the repository's feed
    assert reg.get_materialized_blueprint("repo", "egeria_git", "deployment", ROOT)["qualified_name"] == legacy
    _make(_m(reg, found={legacy: OLD}))                      # a re-accept (cache hit) logs nothing more
    with reg._conn() as conn:                                # a cleared row, adopted again by search
        conn.execute("DELETE FROM architecture_materialized_blueprints")
    _make(_m(reg, found={legacy: OLD}))
    assert len(_activity(reg)) == 1                          # logged once per adoption, not per hit


def test_the_new_name_is_searched_first_and_wins_over_a_legacy_one(reg):
    m = _m(reg, found={"SolutionBlueprint::repo::egeria_git::deployment": NEW, LEGACY_556: OLD})
    assert _make(m)["guid"] == NEW and not any("legacy" in x for x in _activity(reg))


@pytest.mark.parametrize("failing", ["search", "legacy-search"])
def test_an_unreadable_search_still_raises_and_creates_nothing(reg, failing):
    m = _m(reg)
    if failing == "search":
        m._automated_curation.get_guid_for_name.side_effect = RuntimeError("503")
    else:
        def find(qn, **kw):
            if qn == LEGACY_PRE:
                raise RuntimeError("503")
            return []
        m._automated_curation.get_guid_for_name.side_effect = find
    with pytest.raises(BlueprintMaterializationError, match="nothing was created"):
        _make(m)
    assert _created(m) == []


def test_a_cached_legacy_row_is_read_through_the_mapping_and_adopted(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", ROOT, LEGACY_556, OLD)
    m = _m(reg)
    out = _make(m)
    assert out == {"status": "already_materialized", "guid": OLD, "qualified_name": LEGACY_556}
    m._connect.assert_not_called()
    assert _created(m) == [] and _activity(reg) == []    # logged when adopted, not on every cache hit


def test_a_legacy_row_does_not_count_as_a_blueprint_of_the_kind_for_a_different_cluster(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", ROOT, LEGACY_556, OLD)
    m = _m(reg)
    assert _make(m, cluster="web")["qualified_name"] == "SolutionBlueprint::repo::egeria_git::deployment"


def test_no_qualified_name_created_contains_a_cluster_root_name(reg):
    ms = [_m(reg), _m(reg)]
    _make(ms[0], cluster=ROOT)
    _make(ms[1], cluster="OMAG-Server-Platform-2", identifier="second")
    for m in ms:
        for props in _created(m):
            assert "OMAG" not in props["qualifiedName"] and "OMAG" not in props["identifier"]


@pytest.mark.parametrize("bad", ["a::b", "   ", "-lead", "x" * 65, "semi;colon"])
def test_a_bad_identifier_is_refused_before_anything_happens(reg, bad):
    m = _m(reg)
    with pytest.raises(BlueprintMaterializationError, match="nothing was created"):
        _make(m, identifier=bad)
    assert _created(m) == [] and m.searched == []


def test_identifier_validation_trims():
    assert bk.validate_identifier("  Runtimes ") == "Runtimes" and bk.validate_identifier("") == ""


def test_the_accept_pane_is_told_to_ask_only_when_another_live_cluster_holds_the_identity(reg):
    from resource_explorer.registry import Project
    from resource_explorer.surveyors.repo_survey_definition_adapter import _candidate_blueprints_results
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    reg.upsert_finding("egeria_git", "architecture_blueprints", [
        {"check_name": "candidate_blueprint", "label": n,
         "detail": {"name": n, "perspective": "deployment", "members": [], "children": [], "parent": "",
                    "oversized": False}} for n in (ROOT, "web")], surveyed_at="2026-10-01T00:00:00")
    ids = lambda: {b["cluster_name"]: b["identity"] for b in _candidate_blueprints_results(reg, "egeria_git")}  # noqa: E731
    assert not ids()[ROOT]["needs_identifier"] and not ids()["web"]["needs_identifier"]
    _make(_m(reg), cluster=ROOT)
    got = ids()
    assert not got[ROOT]["needs_identifier"]            # its own blueprint: nothing to ask
    assert got["web"]["needs_identifier"] and got["web"]["sentence"] == HELD.format(ROOT)


# ── provenance: the primary adoption rule; the displayName is the fallback ───────────────────────────────

BASE = "SolutionBlueprint::repo::egeria_git::deployment"


def _prov(cluster, **extra):
    return {"re_entity_type": "repo", "re_slug": "egeria_git", "re_kind": "deployment",
            "re_cluster_key": cluster, "re_version": "1", **extra}


def _activity_rows(reg):
    return reg.list_activity(entity_type="repo", entity_slug="egeria_git")


def test_a_created_blueprint_carries_the_provenance_keys_and_re_identifier_only_when_given(reg):
    first = _m(reg)
    _make(first, cluster=ROOT)
    assert _created(first)[0]["additionalProperties"] == {
        "recoveredBy": "architecture_recovery", "re_entity_type": "repo", "re_slug": "egeria_git",
        "re_kind": "deployment", "re_cluster_key": ROOT, "re_version": "1"}
    second = _m(reg)
    _make(second, cluster="web", identifier="Servers")
    extra = _created(second)[0]["additionalProperties"]
    assert extra["re_identifier"] == "Servers" and extra["re_cluster_key"] == "web"


def test_a_cleared_row_with_this_clusters_provenance_is_adopted_and_the_row_rewritten(reg):
    m = _m(reg, found={BASE: {"guid": OLD, "display": "renamed since", "props": _prov(ROOT)}})
    out = _make(m)
    assert out == {"status": "already_materialized", "guid": OLD, "qualified_name": BASE}
    assert _created(m) == []
    row = reg.get_materialized_blueprint("repo", "egeria_git", "deployment", ROOT)
    assert (row["guid"], row["qualified_name"]) == (OLD, BASE)
    assert any("adopted blueprint" in s and OLD in s for s in _activity(reg))


def test_provenance_naming_another_cluster_refuses_and_attaches_nothing(reg):
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov("web")}})
    with pytest.raises(BlueprintIdentifierNeeded) as exc:
        _make(m, cluster=ROOT)
    assert str(exc.value) == (f"an element named {BASE} already exists in Egeria for another cluster (web) "
                              "· give this one an identifier")
    assert _created(m) == [] and reg.get_materialized_blueprints("repo", "egeria_git") == {}
    assert any("refused to adopt" in s and OLD in s for s in _activity(reg))


def test_provenance_naming_another_cluster_with_an_identifier_given_says_a_different_one(reg):
    m = _m(reg, found={f"{BASE}::Servers": {"guid": OLD, "props": _prov("web")}})
    with pytest.raises(BlueprintMaterializationError, match="different identifier"):
        _make(m, cluster=ROOT, identifier="Servers")
    assert _created(m) == []


def test_no_provenance_and_a_lost_row_adopts_only_for_the_cluster_whose_displayName_it_is(reg, tmp_path):
    elem = {"guid": OLD, "display": DISPLAY}
    # A's row is lost and A accepts: its displayName is the element's -> adopted
    a = _m(reg, found={BASE: elem})
    assert _make(a, cluster=ROOT)["guid"] == OLD and _created(a) == []
    # a fresh registry: B accepts, its displayName differs -> refused, nothing attached or created
    b_reg = ProjectRegistry(db_path=str(tmp_path / "b.db"))
    b = _m(b_reg, found={BASE: elem})
    with pytest.raises(BlueprintIdentifierNeeded) as exc:
        b.materialize_blueprint_element("repo", "egeria_git", "deployment", "web",
                                        display_name=DISPLAY + " · web")
    assert str(exc.value) == (f"an element named {BASE} already exists in Egeria and RE cannot tell which cluster "
                              "it is for · give this one an identifier")
    assert _created(b) == []


def test_no_provenance_with_another_clusters_row_recording_the_name_is_refused_even_if_the_name_matches(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", "web", BASE, G_WEB)
    m = _m(reg, found={BASE: {"guid": G_WEB, "display": DISPLAY}})
    with pytest.raises(BlueprintIdentifierNeeded):
        _make(m, cluster=ROOT, live_clusters={"web"})
    assert _created(m) == []


def test_a_renamed_cluster_whose_old_name_no_longer_exists_adopts_and_rekeys(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", ROOT, BASE, OLD)
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
    out = _make(m, cluster="OMAG-renamed", live_clusters={"OMAG-renamed"})
    assert out == {"status": "already_materialized", "guid": OLD, "qualified_name": BASE}
    assert _created(m) == []
    m._solution_architect.update_solution_blueprint.assert_not_called()      # nothing written to Egeria
    assert set(reg.get_materialized_blueprints("repo", "egeria_git")) == {"deployment::OMAG-renamed"}
    assert any(f"re-keyed from '{ROOT}'" in x and OLD in x for x in _activity(reg))
    proofs = [p for p in reg.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "rekey"]
    assert len(proofs) == 1 and proofs[0]["element_guid"] == OLD
    assert proofs[0]["detail"] == {"entity_type": "repo", "old_cluster_key": ROOT,
                                   "old_key_source": "registry row",
                                   "new_cluster_key": "OMAG-renamed", "guid": OLD}


def test_a_split_with_one_acceptance_adopts_once_and_the_other_half_is_refused_afterwards(reg):
    live = {"alpha", "beta"}                      # one group re-clustered into two; the old name is gone
    props = {BASE: {"guid": OLD, "props": _prov(ROOT)}}
    first = _m(reg, found=props)
    assert _make(first, cluster="beta", live_clusters=live)["guid"] == OLD    # beta alone pressed adopts
    later = _m(reg, found=props)                  # alpha pressed afterwards: beta now holds the row
    with pytest.raises(BlueprintIdentifierNeeded):
        _make(later, cluster="alpha", live_clusters=live)
    assert _created(first) == [] and _created(later) == []
    assert set(reg.get_materialized_blueprints("repo", "egeria_git")) == {"deployment::beta"}


def test_two_accepted_halves_in_one_batch_yield_one_adopter_first_by_name(reg):
    live = {"alpha", "beta"}
    props = {BASE: {"guid": OLD, "props": _prov(ROOT)}}
    batch = {"alpha", "beta"}
    second = _m(reg, found=props)
    with pytest.raises(BlueprintIdentifierNeeded, match=r"another cluster \(alpha\)"):
        _make(second, cluster="beta", live_clusters=live, batch=batch)
    first = _m(reg, found=props)
    assert _make(first, cluster="alpha", live_clusters=live, batch=batch)["guid"] == OLD
    assert _created(first) == [] and _created(second) == []
    assert set(reg.get_materialized_blueprints("repo", "egeria_git")) == {"deployment::alpha"}


#: The architect's ruling (A): claimants are the clusters ACCEPTED in the run, so an earlier-sorting cluster
#: that nobody accepted does not block the one that was pressed. Flip this constant to pin another ruling.
UNACCEPTED_EARLIER_CLUSTER_BLOCKS = False


def test_an_unaccepted_earlier_cluster_does_not_block_the_pressed_one(reg):
    live = {"alpha", "beta"}                      # alpha sorts first but nobody accepted it
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
    if UNACCEPTED_EARLIER_CLUSTER_BLOCKS:
        with pytest.raises(BlueprintIdentifierNeeded):
            _make(m, cluster="beta", live_clusters=live)
    else:
        assert _make(m, cluster="beta", live_clusters=live)["guid"] == OLD


def test_a_second_rename_records_the_previous_key_from_the_registry_row(reg):
    props = {BASE: {"guid": OLD, "props": _prov(ROOT)}}          # the element's key is never rewritten
    _make(_m(reg, found=props), cluster="alpha", live_clusters={"alpha"})
    _make(_m(reg, found=props), cluster="gamma", live_clusters={"gamma"})
    details = [p["detail"] for p in reg.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "rekey"]
    assert [(d["old_cluster_key"], d["old_key_source"], d["new_cluster_key"]) for d in details] == [
        (ROOT, "element property", "alpha"), ("alpha", "registry row", "gamma")]


def test_a_proof_that_cannot_be_written_is_surfaced_not_just_logged(reg):
    props = {BASE: {"guid": OLD, "props": _prov(ROOT)}}
    m = _m(reg, found=props)
    m._registry.append_catalogue_commit_proof = MagicMock(side_effect=RuntimeError("proof table down"))
    out = _make(m, cluster="alpha", live_clusters={"alpha"})
    assert out["status"] == "adopted_unproven" and "proof table down" in out["proof_error"]
    assert out["guid"] == OLD
    assert any("UNPROVEN" in x and "proof table down" in x for x in _activity(reg))
    # no registry at all: the proof cannot be written, and that is said, not swallowed
    m2 = _m(None, found=props)
    out2 = m2._record_rekey_proof("repo", "egeria_git", "deployment", ROOT, "alpha", BASE, OLD, "element property")
    assert "no registry" in out2


def test_the_state_readers_ignore_a_rekey_row(reg):
    """`derive_commit_state` treats ANY proof row on a slug as "something was committed" (its header), which is
    true of the existing blueprint `shape` rows too and moot for a repository slug. So the pinned property is:
    a `rekey` row changes nothing beyond what a shape row already does, and `publish_state` is unchanged."""
    from resource_explorer.catalogue_commit import derive_commit_state
    from resource_explorer.registry import Project
    from resource_explorer.repo_publish import publish_state
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    view = {"schemas": []}
    reg.append_catalogue_commit_proof("egeria_git", proof="shape", node_kind="blueprint_shape",
                                      table_name="deployment::a", element_guid=OLD, detail={"shape": "container"})
    before = (derive_commit_state(reg, "egeria_git", view), publish_state(reg, "egeria_git"))
    reg.append_catalogue_commit_proof("egeria_git", proof="rekey", node_kind="blueprint_shape",
                                      table_name="deployment::a", element_guid=OLD, detail={"x": 1})
    after = (derive_commit_state(reg, "egeria_git", view), publish_state(reg, "egeria_git"))
    assert before == after


# ── a claim around adoption ──────────────────────────────────────────────────────────────────────────────


def test_a_second_caller_that_passes_the_check_while_the_first_holds_the_claim_is_refused(reg):
    props = {BASE: {"guid": OLD, "props": _prov(ROOT)}}
    live = {"alpha", "beta"}
    second = _m(reg, found=props)
    first = _m(reg, found=props)
    seen = {}
    real = first._automated_curation.get_guid_for_name.side_effect

    def search_then_interleave(qn, **kw):
        out = real(qn, **kw)
        if "refused" not in seen:                 # the second press arrives mid-adoption
            with pytest.raises(BlueprintMaterializationError, match="another press is adopting") as exc:
                _make(second, cluster="beta", live_clusters=live)
            seen["refused"] = str(exc.value)
            seen["claim_still_held"] = not reg.take_claim(f"blueprint-claim::{BASE}", "intruder")
        return out

    first._automated_curation.get_guid_for_name.side_effect = search_then_interleave
    assert _make(first, cluster="alpha", live_clusters=live)["guid"] == OLD
    assert seen["claim_still_held"] is True                    # the refused caller did not release it
    assert _created(second) == [] and second.searched == []
    assert len([p for p in reg.list_catalogue_commit_proofs("egeria_git") if p["proof"] == "rekey"]) == 1
    assert reg.take_claim(f"blueprint-claim::{BASE}", "later")  # released by the holder in its finally


def test_the_claim_is_released_when_the_holder_fails(reg):
    m = _m(reg, search_error=RuntimeError("503"))
    with pytest.raises(BlueprintMaterializationError):
        _make(m)
    assert reg.take_claim(f"blueprint-claim::{BASE}", "next")


def test_the_other_cluster_being_live_or_unknown_still_refuses(reg):
    for live in ({"web", ROOT}, None):
        m = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
        with pytest.raises(BlueprintIdentifierNeeded, match=r"for another cluster \(OMAG-Server-Platform\)"):
            _make(m, cluster="web", live_clusters=live)
        assert _created(m) == []
    # a live other cluster with NO provenance on its element is refused by the displayName rule too
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", ROOT, BASE, OLD)
    other = _m(reg, found={BASE: {"guid": OLD, "display": DISPLAY}})
    with pytest.raises(BlueprintIdentifierNeeded):                 # the live holder's row blocks it
        _make(other, cluster="web", live_clusters={"web", ROOT})


def test_without_provenance_a_stale_other_row_does_not_block_a_matching_displayName(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", ROOT, BASE, OLD)
    m = _m(reg, found={BASE: {"guid": OLD, "display": DISPLAY}})
    assert _make(m, cluster="OMAG-renamed", live_clusters={"OMAG-renamed"})["guid"] == OLD
    assert _created(m) == []


def test_a_refusal_is_logged_once_per_element_and_an_adoption_only_after_the_row_is_recorded(reg):
    for _ in range(3):
        m = _m(reg, found={BASE: {"guid": OLD, "props": _prov("web")}})
        with pytest.raises(BlueprintIdentifierNeeded):
            _make(m, cluster=ROOT, live_clusters={ROOT, "web"})
    assert sum("refused to adopt" in x for x in _activity(reg)) == 1
    ok = _m(reg, found={BASE: {"guid": OLD, "props": _prov(ROOT)}})
    ok._registry = MagicMock(get_materialized_blueprint=MagicMock(return_value=None),
                             get_materialized_blueprints=MagicMock(return_value={}),
                             record_materialized_blueprint=MagicMock(side_effect=RuntimeError("db down")),
                             list_activity=reg.list_activity, write_activity=reg.write_activity)
    with pytest.raises(RuntimeError):
        _make(ok)
    assert not any("adopted blueprint" in x for x in _activity(reg))


# ── the search is typed and verified ─────────────────────────────────────────────────────────────────────


def test_the_search_is_restricted_to_qualified_name_and_the_blueprint_type(reg):
    m = _m(reg)
    _make(m)
    assert m.searched[0] == (BASE, ["qualifiedName"], "SolutionBlueprint")


class _Multi(Exception):
    pass


def test_more_than_one_hit_is_ambiguous_not_a_search_failure(reg):
    m = _m(reg, search_error=_Multi("Multiple elements found for supplied name!"))
    with pytest.raises(BlueprintAmbiguous, match="ambiguous"):
        _make(m)
    assert _created(m) == []
    m2 = _m(reg)
    m2._automated_curation.get_guid_for_name.side_effect = lambda qn, **kw: [OLD, NEW]
    with pytest.raises(BlueprintAmbiguous, match="ambiguous"):
        _make(m2)
    assert _created(m2) == []


def test_a_hit_of_another_type_or_another_qualified_name_is_not_adopted_and_nothing_is_created(reg):
    wrong_type = _m(reg, found={BASE: {"guid": OLD, "type": "SolutionComponent"}})
    with pytest.raises(BlueprintMaterializationError, match="not that SolutionBlueprint"):
        _make(wrong_type)
    other_qn = _m(reg)
    other_qn._automated_curation.get_guid_for_name.side_effect = lambda qn, **kw: [OLD]
    other_qn._solution_architect.get_solution_blueprint_by_guid.side_effect = lambda g: _element("x::other", g)
    with pytest.raises(BlueprintMaterializationError, match="not that SolutionBlueprint"):
        _make(other_qn)
    assert _created(wrong_type) == [] and _created(other_qn) == []


# ── displaced rows, and identifiers that give the same Egeria identifier ────────────────────────────────


def test_re_keying_a_row_names_the_cluster_it_displaced(reg):
    out = reg.record_materialized_blueprint("repo", "egeria_git", "deployment", "web", BASE, OLD)
    assert out["displaced"] == []
    out = reg.record_materialized_blueprint("repo", "egeria_git", "deployment", "db", BASE, OLD)
    assert out["displaced"] == ["web"]
    m = _m(reg, found={BASE: {"guid": OLD, "props": _prov("web")}})
    _make(m, cluster="web", live_clusters={"web"})   # rewrites web's row, displacing db's
    assert any("displaced" in s and "'db'" in s for s in _activity(reg))


@pytest.mark.parametrize("first,second", [("a b", "a-b"), ("Servers", "servers"), ("x.y", "X_Y")])
def test_two_identifiers_that_give_the_same_egeria_identifier_are_refused(reg, first, second):
    _make(_m(reg), cluster=ROOT)
    _make(_m(reg), cluster="web", identifier=first)
    m = _m(reg)
    with pytest.raises(BlueprintMaterializationError, match="already used"):
        _make(m, cluster="db", identifier=second)
    assert _created(m) == []


def test_a_claim_this_process_does_not_hold_is_never_released():
    registry = MagicMock(get_materialized_blueprint=MagicMock(return_value=None),
                         take_claim=MagicMock(return_value=None))     # not a real "taken"
    m = _m(registry)
    m._registry = registry
    _make(m)
    registry.release_claim.assert_not_called()


def test_with_no_holder_the_sentence_stays_exactly_the_ruled_one():
    assert bk.identifier_needed_sentence("deployment", "egeria_git") == SENTENCE
    assert bk.identifier_needed_sentence("deployment", "egeria_git", "") == SENTENCE


def test_the_holder_comes_from_the_cache_row_not_from_the_pressed_clusters_provenance(reg):
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", "web", BASE, G_WEB)
    # the pressed cluster's element claims yet another key; the sentence still names the row's cluster
    m = _m(reg, found={BASE: {"guid": G_WEB, "props": _prov("somebody-else")}})
    with pytest.raises(BlueprintIdentifierNeeded) as exc:
        _make(m, cluster=ROOT, live_clusters={ROOT, "web"})
    assert str(exc.value) == HELD.format("web")


# ── review round: live holder, release failure, claim age, the route's JSON ──────────────────────────────


def test_a_live_holder_row_is_never_rekeyed_from_even_when_asked_directly(reg):
    """`_identity_clash` refuses this earlier on the materialize path; `_decide_adoption` must still never
    re-key from a LIVE holder (the sentence may name it, the re-key may not take from it)."""
    reg.record_materialized_blueprint("repo", "egeria_git", "deployment", "web", BASE, G_WEB)
    m = _m(reg)
    found = {"guid": OLD, "display_name": DISPLAY, "additional": _prov(ROOT)}     # ROOT is gone, web is live
    with pytest.raises(BlueprintIdentifierNeeded) as exc:
        m._decide_adoption("repo", "egeria_git", "deployment", "alpha", BASE, "", DISPLAY, found,
                           live_clusters={"alpha", "web"})
    assert str(exc.value) == HELD.format("web")
    assert not any(p["proof"] == "rekey" for p in reg.list_catalogue_commit_proofs("egeria_git"))


def test_a_release_that_raises_never_masks_the_outcome_and_is_written_down(reg):
    m = _m(reg)
    real = reg.release_claim
    reg.release_claim = MagicMock(side_effect=RuntimeError("db gone"))
    try:
        out = _make(m)
    finally:
        reg.release_claim = real
    assert out["status"] == "materialized"
    assert any("could not release the blueprint claim" in x and "db gone" in x for x in _activity(reg))
    with reg._conn() as conn:                                   # the failed release left the claim to expire
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'blueprint-claim::%'")
        conn.execute("DELETE FROM architecture_materialized_blueprints")
    m2 = _m(reg, search_error=RuntimeError("503"))              # and an exception is not replaced by it
    reg.release_claim = MagicMock(side_effect=RuntimeError("db gone"))
    try:
        with pytest.raises(BlueprintMaterializationError, match="nothing was created"):
            _make(m2)
    finally:
        reg.release_claim = real


def test_a_refused_press_states_the_claims_age_and_expiry_as_fact(reg):
    from datetime import datetime, timedelta
    key = f"blueprint-claim::{BASE}"
    assert reg.take_claim(key, "someone")
    m = _m(reg)
    with pytest.raises(BlueprintMaterializationError, match="another press is adopting this blueprint right now"):
        _make(m)
    old = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    with reg._conn() as conn:
        conn.execute("UPDATE app_settings SET updated_at = ? WHERE key = ?", (old, key))
    with pytest.raises(BlueprintMaterializationError) as exc:
        _make(_m(reg))
    text = str(exc.value)
    assert "claimed 10 minutes ago" in text and "may be stale" in text and "expires at" in text
    assert "right now" not in text and _created(m) == []


def test_an_unproven_adoption_is_returned_by_the_workflow_for_the_route(tmp_path, monkeypatch):
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.workflows import curate
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    reg.upsert_finding("egeria_git", "architecture_blueprints", [{
        "check_name": "candidate_blueprint", "label": "core",
        "detail": {"name": "core", "perspective": "deployment", "members": [], "children": [], "parent": "",
                   "oversized": False}}], surveyed_at="2026-10-01T00:00:00")

    class Fake:
        def __init__(self, registry=None):
            pass

        def materialize_blueprint_element(self, *a, **k):
            return {"status": "adopted_unproven", "proof_error": "RuntimeError: proof table down",
                    "guid": OLD, "qualified_name": BASE}

        def blueprint_member_guids(self, guid):
            return None

        def resolve_member_guids(self, *a, **k):
            return {}, []

        def resolve_child_blueprint_guids(self, *a, **k):
            return {}, []

    monkeypatch.setattr("resource_explorer.surveyors.arch_recovery.blueprint_materializer.BlueprintMaterializer", Fake)
    monkeypatch.setattr("resource_explorer.egeria_outbox.enqueue_blueprint_members", lambda *a, **k: [])
    out = curate.materialize_blueprint_if_accepted(reg, "repo", "egeria_git", "deployment", "core", "accepted")
    assert out["status"] == "adopted_unproven" and out["adopted_unproven"] is True
    assert out["proof_error"] == "RuntimeError: proof table down"
