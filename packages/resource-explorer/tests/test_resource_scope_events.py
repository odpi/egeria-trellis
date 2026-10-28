"""Brief 2a: explicit selection for repositories.

The selection is RE's record (`resource_scope_events`, append-only), saved before and apart from any
publish. These tests pin: the table and its Postgres translation; the append-only record; the view both
panes draw (nothing pre-ticked, worthiness is a proposal, containers are shown, the manifest follows the
record); the two routes; the commit taking its list from the record, not the request; the press publishing
exactly the chosen items, once; and a left-out published row staying published (no delete call, ever).

Temp SQLite only. Nothing here touches the shared registry or Egeria.
"""
from __future__ import annotations

import json
import re
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer import resource_scope
from resource_explorer.registry import ProjectRegistry, RESOURCE_SCOPE_EVENTS_DDL
from tests.test_curate import _keep_a_survey, _seed, client, registry  # noqa: F401  (fixtures)

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


def _survey(registry, extra=()):
    """docs (worthy folder), docs/guide.md and docs/api.md (worthy files), src (worthy folder),
    src/x/y.py (a file that is not worthy), .idea (not worthy folder)."""
    rows = [
        ("docs", "folder", "worthy", "top_level_structural_folder"),
        ("docs/guide.md", "file", "worthy", "well_known_file"),
        ("docs/api.md", "file", "worthy", "well_known_file"),
        ("src", "folder", "worthy", "top_level_structural_folder"),
        ("src/x/y.py", "file", "not_worthy", "plain source"),
        (".idea", "folder", "not_worthy", "generated"),
        *extra,
    ]
    registry.upsert_finding("p", "repo_sub_resource_survey", [
        {"check_name": loc, "label": label, "summary": why, "detail": {"path": loc, "kind": kind}}
        for loc, kind, label, why in rows])


def _row(view, loc):
    return next(r for r in view["rows"] if r["locator"] == loc)


def _event(registry, loc, kind, choice="include", **kw):
    registry.append_resource_scope_event("repo", "p", locator=loc, kind=kind, choice=choice,
                                         action=kw.pop("action", "set"), author=kw.pop("author", "peterprofile"), **kw)


# ── the table ────────────────────────────────────────────────────────────────

def test_an_old_registry_gets_the_table_on_open_and_nothing_else_changes(tmp_path):
    path = str(tmp_path / "old.db")
    r = ProjectRegistry(db_path=path)
    from resource_explorer.registry import Project
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    con = sqlite3.connect(path)
    con.execute("DROP TABLE resource_scope_events")                  # the OLD shape: the table did not exist
    con.commit()
    assert "resource_scope_events" not in {t[0] for t in con.execute("SELECT name FROM sqlite_master")}
    con.close()
    r2 = ProjectRegistry(db_path=path)
    ProjectRegistry(db_path=path)                                     # idempotent: a third open is not an error
    assert r2.current_resource_scope("repo", "p") == {}
    assert r2.get("p") is not None
    con = sqlite3.connect(path)
    assert [c[1] for c in con.execute("PRAGMA table_info(resource_scope_events)")] == [
        "id", "resource_type", "resource_slug", "locator", "kind", "choice", "action", "source",
        "proposal_rule", "reason", "author", "changed_at"]
    assert any(i[1] == "idx_resource_scope_events_locator" for i in con.execute("PRAGMA index_list(resource_scope_events)"))
    con.close()


def test_the_ddl_block_is_additive_and_idempotent_text():
    assert all(s.lstrip().upper().startswith(("CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS")) for s in RESOURCE_SCOPE_EVENTS_DDL)
    joined = " ".join(RESOURCE_SCOPE_EVENTS_DDL).upper()
    assert "ALTER" not in joined and "DROP" not in joined and "INSERT" not in joined and "UPDATE" not in joined
    assert "REFERENCES" not in joined and "FOREIGN" not in joined


def test_the_ddl_survives_the_postgres_translator():
    """No `?` and no `:name` (a colon in a comment or a string would be rewritten), AUTOINCREMENT to SERIAL."""
    from resource_explorer.registry import PostgresCursorWrapper
    t = PostgresCursorWrapper(None)._translate_sql
    for stmt in RESOURCE_SCOPE_EVENTS_DDL:
        assert "?" not in stmt and not re.search(r"(?<!:):[A-Za-z_]", stmt)
        assert "%" not in t(stmt)
    out = t(RESOURCE_SCOPE_EVENTS_DDL[0])
    assert "SERIAL PRIMARY KEY" in out and "AUTOINCREMENT" not in out
    ins = ("INSERT INTO resource_scope_events (resource_type, resource_slug, locator, kind, choice, action, source, "
           "proposal_rule, reason, author, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    assert t(ins).count("%s") == 11 and "?" not in t(ins)
    idx = RESOURCE_SCOPE_EVENTS_DDL[1]
    assert t(idx) == idx


def test_the_registry_has_no_update_or_delete_for_scope_events():
    names = [n for n in dir(ProjectRegistry) if "resource_scope" in n]
    assert sorted(names) == ["append_resource_scope_event", "append_resource_scope_events", "current_resource_scope",
                             "list_resource_scope_events"]
    import inspect
    src = inspect.getsource(ProjectRegistry)
    assert not re.search(r"(UPDATE|DELETE FROM)\s+resource_scope_events", src)


# ── the record ───────────────────────────────────────────────────────────────

def test_the_newest_row_per_locator_is_the_current_choice_and_a_clear_is_a_row(registry):
    _event(registry, "docs", "folder", "include")
    _event(registry, "docs", "folder", "leave_out", author="mandy")
    assert registry.current_resource_scope("repo", "p")["docs"]["choice"] == "leave_out"
    _event(registry, "docs", "folder", action="clear", author="dan")
    cur = registry.current_resource_scope("repo", "p")["docs"]
    assert (cur["choice"], cur["action"], cur["author"]) == ("", "clear", "dan")
    assert [e["choice"] for e in registry.list_resource_scope_events("repo", "p")] == ["include", "leave_out", ""]


def test_a_choice_needs_an_author_and_a_known_choice_and_kind(registry):
    with pytest.raises(ValueError):
        _event(registry, "docs", "folder", author="")
    with pytest.raises(ValueError):
        _event(registry, "docs", "folder", "maybe")
    with pytest.raises(ValueError):
        _event(registry, "docs", "spaceship")
    assert registry.list_resource_scope_events("repo", "p") == []


def test_a_choice_never_writes_the_published_record(registry):
    _survey(registry)
    _event(registry, "docs", "folder")
    assert registry.list_sub_resources("repo", "p") == []


# ── the view ─────────────────────────────────────────────────────────────────

def test_nothing_is_pre_ticked_and_a_worthy_candidate_reads_proposed(registry):
    _survey(registry)
    v = resource_scope.build_view(registry, "p")
    assert all(r["choice"] == "" for r in v["rows"])
    assert _row(v, "docs")["proposed"] is True and _row(v, "docs")["label"] == "worthy"
    assert _row(v, "docs")["reason"] == "top_level_structural_folder"
    assert _row(v, "src/x/y.py")["proposed"] is False
    assert v["manifest"]["items"] == 0 and v["manifest"]["chosen"] == []
    assert sorted(v["proposals"]) == ["docs", "docs/api.md", "docs/guide.md", "src"]
    assert (v["manifest"]["proposals_not_accepted"], v["manifest"]["not_selected"], v["manifest"]["left_out"]) == (4, 2, 0)


def test_a_file_under_an_unincluded_folder_yields_a_container_not_a_chosen_folder(registry):
    _survey(registry)
    _event(registry, "docs/guide.md", "file")
    v = resource_scope.build_view(registry, "p")
    m = v["manifest"]
    assert (m["files"], m["folders"], m["containers"], m["items"]) == (1, 0, 1, 2)
    assert m["container_locators"] == ["docs"] and m["chosen"] == ["docs/guide.md"]
    assert _row(v, "docs")["role"] == "container" and _row(v, "docs")["choice"] == ""


def test_a_container_that_is_not_a_candidate_still_gets_a_row_and_the_synthetic_root_counts(registry):
    _survey(registry)
    _event(registry, "src/x/y.py", "file")                      # src/x is no candidate; src is
    v = resource_scope.build_view(registry, "p")
    assert v["manifest"]["container_locators"] == ["src", "src/x"]
    assert _row(v, "src/x")["role"] == "container" and _row(v, "src/x")["candidate"] is False


def test_the_manifest_equals_the_records_current_choices_and_follows_a_change_without_a_publish(registry):
    _survey(registry)
    _event(registry, "docs", "folder")
    _event(registry, "docs/guide.md", "file")
    _event(registry, "src", "folder", "leave_out")
    m = resource_scope.build_view(registry, "p")["manifest"]
    assert (m["files"], m["folders"], m["containers"], m["left_out"]) == (1, 1, 0, 1)   # docs is chosen, so not a container
    _event(registry, "docs", "folder", action="clear")                                   # a changed choice, no publish
    m = resource_scope.build_view(registry, "p")["manifest"]
    assert (m["files"], m["folders"], m["containers"]) == (1, 0, 1)
    assert registry.list_sub_resources("repo", "p") == []


def test_a_published_row_left_out_stays_published_in_the_view(registry):
    _survey(registry)
    registry.catalog_sub_resource("repo", "p", "docs", "folder")
    registry.set_sub_resource_egeria_guid("repo", "p", "docs", "g-docs")
    _event(registry, "docs", "folder", "leave_out")
    v = resource_scope.build_view(registry, "p")
    r = _row(v, "docs")
    assert r["choice"] == "leave_out" and r["published"]["guid"] == "g-docs"
    assert v["manifest"]["published_earlier"] == 1 and "docs" not in v["manifest"]["chosen"]
    assert registry.list_sub_resources("repo", "p")[0]["egeria_guid"] == "g-docs"


# ── the routes ───────────────────────────────────────────────────────────────

def _post(client, events):
    return client.post("/api/projects/p/scope-events", json={"events": events})


def test_get_is_the_view_and_404s_an_unknown_repository(client, registry):
    _survey(registry)
    r = client.get("/api/projects/p/scope-events")
    assert r.status_code == 200 and r.json()["manifest"]["items"] == 0
    assert client.get("/api/projects/nope/scope-events").status_code == 404


def test_signed_out_post_is_401_and_writes_nothing(client, registry, monkeypatch):
    _survey(registry)
    monkeypatch.setattr("resource_explorer.auth.get_current_user", lambda request: None)
    assert _post(client, [{"locator": "docs", "kind": "folder", "choice": "include"}]).status_code == 401
    assert registry.list_resource_scope_events("repo", "p") == []


def test_including_a_folder_writes_one_event_for_it_and_none_for_its_children(client, registry):
    _survey(registry)
    r = _post(client, [{"locator": "docs", "kind": "folder", "choice": "include"}])
    assert r.status_code == 200, r.text
    evs = registry.list_resource_scope_events("repo", "p")
    assert [(e["locator"], e["choice"], e["author"], e["source"]) for e in evs] == [("docs", "include", "peterprofile", "person")]
    m = r.json()["manifest"]
    assert (m["files"], m["folders"], m["containers"]) == (0, 1, 0)


def test_include_its_worthy_children_writes_one_explicit_event_per_child(client, registry):
    _survey(registry)
    _post(client, [{"locator": "docs", "kind": "folder", "choice": "include"}])
    r = _post(client, [{"locator": "docs/guide.md", "kind": "file", "choice": "include"},
                       {"locator": "docs/api.md", "kind": "file", "choice": "include"}])
    assert [e["locator"] for e in registry.list_resource_scope_events("repo", "p")][1:] == ["docs/guide.md", "docs/api.md"]
    assert (r.json()["manifest"]["files"], r.json()["manifest"]["folders"]) == (2, 1)


def test_accepting_the_proposals_writes_n_events_marked_as_proposals(client, registry):
    _survey(registry)
    props = client.get("/api/projects/p/scope-events").json()["proposals"]
    r = _post(client, [{"locator": l, "kind": _row(resource_scope.build_view(registry, "p"), l)["kind"],
                        "choice": "include", "source": "proposal", "proposal_rule": "worthy"} for l in props])
    assert r.status_code == 200
    evs = registry.list_resource_scope_events("repo", "p")
    assert len(evs) == len(props) == 4
    assert {(e["source"], e["proposal_rule"], e["choice"]) for e in evs} == {("proposal", "worthy", "include")}
    assert r.json()["proposals"] == []                                # nothing left to accept; every one is a filled segment


def test_an_unknown_locator_or_a_wrong_kind_is_refused_and_writes_nothing_of_the_batch(client, registry):
    _survey(registry)
    assert _post(client, [{"locator": "nope", "kind": "file", "choice": "include"}]).status_code == 400
    r = _post(client, [{"locator": "docs", "kind": "folder", "choice": "include"},
                       {"locator": "docs/guide.md", "kind": "folder", "choice": "include"}])
    assert r.status_code == 400 and "is a file" in r.json()["detail"]
    assert registry.list_resource_scope_events("repo", "p") == []


# ── the commit takes its list from the record ────────────────────────────────

def _go(registry, client):
    _seed(registry)
    _keep_a_survey(registry)
    registry.set_disposition("https://github.com/x/p", "using", resource_slug="p")


def test_the_commit_route_ignores_a_sub_resource_list_in_the_request_and_reads_the_record(client, registry):
    _go(registry, client)
    _event(registry, "docs/guide.md", "file")
    r = client.post("/api/projects/p/curate/commit",
                    json={"confirm": ["Endpoint"], "sub_resources": ["docs", ".idea", "src"]})
    assert r.status_code == 200, r.text
    rec = r.json()["curation"]
    assert rec["selection"]["sub_resources"] == ["docs/guide.md"]
    assert rec["manifest"]["contained"]["sub_resources"] == 1
    assert rec["manifest"]["contained"]["containers"] == 1


def test_a_press_with_only_unaccepted_proposals_selects_nothing(client, registry):
    _go(registry, client)
    r = client.post("/api/projects/p/curate/commit", json={"confirm": [], "sub_resources": ["docs", "docs/guide.md"]})
    assert r.status_code == 409 and "nothing selected" in r.json()["detail"]


# ── the publish ──────────────────────────────────────────────────────────────

def _publisher_with_egeria(registry):
    """The real EgeriaPublisher with every Egeria call faked: what exists is a dict, so a second publish finds
    by qualifiedName and creates nothing."""
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
    pub = EgeriaPublisher(platform_url="https://fake", registry=registry)
    pub._automated_curation = MagicMock()
    pub._asset_maker = MagicMock()
    pub._discovery = MagicMock()
    pub._connect = MagicMock()
    exists: dict[str, str] = {}
    pub._find_element_guid = lambda qn: exists.get(qn, "")

    async def _tmpl(type_name):
        return f"template-{type_name}"
    pub._automated_curation._async_get_template_guid_for_technology_type = _tmpl
    created = []

    def _create(body):
        guid = f"g{len(created) + 1}"
        qn = body["replacementProperties"]["qualifiedName"]
        created.append(body)
        exists[qn] = guid
        return guid
    pub._automated_curation.create_elem_from_template.side_effect = _create
    pub._asset_maker.get_asset_by_guid.side_effect = lambda guid, **k: {"guid": guid}
    pub.created = created
    return pub


def test_the_press_sends_exactly_the_chosen_items_with_a_proof_row_per_guid_and_a_second_press_creates_nothing(registry):
    from resource_explorer.curate_plan import Curations
    from resource_explorer.workflows.curate_commit import STEPS, execute_curation
    _survey(registry)
    _keep_a_survey(registry)
    registry.set_egeria_asset_guid("p", "asset-guid")
    _event(registry, "docs/guide.md", "file")
    sel = {"confirm": [], "sub_resources": resource_scope.chosen_locators(registry, "p")}
    pub = _publisher_with_egeria(registry)
    with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", MagicMock(return_value=pub)), \
         patch("resource_explorer.repo_publish.publish_snapshot", return_value={
             "ok": True, "asset_guid": "asset-guid", "report_guid": "r", "read_back": True, "surveyed_at": "2026-10-07T01:00:00",
             "reused": False, "annotation_count": 1}), \
         patch("resource_explorer.repo_publish.resolve_project_context", return_value={"guid": "x"}):
        cid = Curations(registry).create("repo", "p", author="peterprofile", selection=sel, manifest={}, steps=list(STEPS))["id"]
        execute_curation(registry, cid)
        assert len(pub.created) == 2                                 # the file and its one container, nothing else
        proofs = [x for x in registry.list_catalogue_commit_proofs("p") if x["node_kind"] == "sub_resource"]
        assert sorted((x["table_name"], x["element_guid"] != "", x["detail"]["role"]) for x in proofs) == [
            ("docs", True, "container"), ("docs/guide.md", True, "chosen")]
        cid2 = Curations(registry).create("repo", "p", author="peterprofile", selection=sel, manifest={}, steps=list(STEPS))["id"]
        execute_curation(registry, cid2)
    assert len(pub.created) == 2, "a second press reuses by qualifiedName and makes no second asset"
    called = [c[0] for c in pub._asset_maker.method_calls] + [c[0] for c in pub._automated_curation.method_calls]
    assert not [c for c in called if re.search(r"delete|archive|remove|withdraw", c)], called


def test_a_published_row_that_is_left_out_is_never_deleted_and_is_not_in_the_next_press(registry):
    _survey(registry)
    registry.catalog_sub_resource("repo", "p", "docs", "folder")
    registry.set_sub_resource_egeria_guid("repo", "p", "docs", "g-docs")
    _event(registry, "docs", "folder")
    _event(registry, "docs", "folder", "leave_out")
    assert resource_scope.chosen_locators(registry, "p") == []
    # the press sends nothing for it, and no code path in the publisher can remove an element
    from resource_explorer.surveyors import egeria_publisher
    src = open(egeria_publisher.__file__).read()
    assert not re.search(r"def (delete|archive|unpublish|remove)_\w*sub_resource", src)
    assert registry.list_sub_resources("repo", "p")[0]["egeria_guid"] == "g-docs"


def test_the_proof_summary_splits_the_state_by_the_rows_of_the_table(registry):
    from resource_explorer import repo_publish
    from resource_explorer.curate_plan import Curations
    cid = Curations(registry).create("repo", "p", author="peterprofile", selection={}, manifest={}, steps=["sub_resources"])["id"]
    guids = {"docs": "g1", "docs/a.md": "g2", "src": "g3"}
    repo_publish.record_sub_resource_proofs(
        registry, "p", cid, "peterprofile", {"docs": "folder", "docs/a.md": "file", "src": "folder", "src/b.py": "file"},
        ["docs/a.md", "src", "src/b.py"], guids, reader=lambda g: {"guid": g} if g != "g3" else None)
    ps = repo_publish.commit_proof_summary(registry, "p", Curations(registry).get(cid))["sub_resources"]["by_row"]
    assert (ps["files"]["read_back"], ps["files"]["failed"]) == (1, 1)        # a.md read back; b.py got no GUID
    assert (ps["folders"]["sent"], ps["containers"]["read_back"]) == (1, 1)    # src sent, docs read back as a container


def test_one_row_chosen_means_exactly_that_row_no_readme_no_siblings(client, registry):
    """Owner evidence (egeria_workspaces_git): 1 of 31 ticked read as 31 folders. One chosen row is one item,
    the commit's list is exactly it, and no request field can widen it."""
    _go(registry, client)
    _survey(registry, extra=(("compose-configs", "folder", "worthy", "x"), ("compose-configs/README.md", "file", "worthy", "x"),
                             ("coco-workbooks", "folder", "worthy", "x"), ("coco-workbooks/README.md", "file", "worthy", "x")))
    _event(registry, "compose-configs", "folder")
    m = resource_scope.build_view(registry, "p")["manifest"]
    assert (m["files"], m["folders"], m["containers"], m["items"]) == (0, 1, 0, 1)
    r = client.post("/api/projects/p/curate/commit",
                    json={"confirm": [], "sub_resources": ["coco-workbooks", "compose-configs/README.md"], "contained": "all"})
    assert r.status_code == 200, r.text
    assert r.json()["curation"]["selection"]["sub_resources"] == ["compose-configs"]


# ── review round: one transaction, caps, file types, the loop ────────────────

def test_a_batch_is_one_transaction_a_failing_second_row_writes_none(registry):
    good = {"locator": "docs", "kind": "folder", "choice": "include"}
    bad = {"locator": "src", "kind": "folder", "choice": "maybe"}
    with pytest.raises(ValueError):
        registry.append_resource_scope_events("repo", "p", author="dan", events=[good, bad])
    assert registry.list_resource_scope_events("repo", "p") == []
    # and a failure INSIDE the writes (the second insert dies) rolls the first back too
    real = registry._conn
    calls = {"n": 0}

    from contextlib import contextmanager

    @contextmanager
    def flaky():
        with real() as conn:
            orig = conn.execute

            def execute(sql, params=()):
                if "INSERT INTO resource_scope_events" in sql:
                    calls["n"] += 1
                    if calls["n"] == 2:
                        raise RuntimeError("db went away")
                return orig(sql, params)
            conn.execute = execute
            yield conn
    registry._conn = flaky
    with pytest.raises(RuntimeError):
        registry.append_resource_scope_events("repo", "p", author="dan", events=[good, {**good, "locator": "src"}])
    registry._conn = real
    assert registry.list_resource_scope_events("repo", "p") == []


def test_the_route_refuses_long_text_and_file_types_with_a_sentence(client, registry):
    _survey(registry)
    r = _post(client, [{"locator": "docs", "kind": "folder", "choice": "include", "reason": "x" * 501}])
    assert r.status_code == 400 and "reason is too long" in r.json()["detail"]
    r = _post(client, [{"locator": "docs", "kind": "folder", "choice": "include", "proposal_rule": "x" * 101}])
    assert r.status_code == 400 and "proposal_rule is too long" in r.json()["detail"]
    r = _post(client, [{"locator": "docs" * 300, "kind": "folder", "choice": "include"}])
    assert r.status_code == 400 and "locator is too long" in r.json()["detail"]
    # file types are a measurement, not a choice: refused for set AND clear, with the sentence, never a 500
    for action, choice in (("set", "include"), ("clear", "")):
        r = _post(client, [{"locator": "Python", "kind": "file_type", "choice": choice, "action": action}])
        assert r.status_code == 400
        assert r.json()["detail"] == "file types are a measurement, published as Egeria's profile annotations; they are not a choice"
    assert registry.list_resource_scope_events("repo", "p") == []
    with pytest.raises(ValueError):
        registry.append_resource_scope_events("repo", "p", author="dan", events=[{"locator": "Python", "kind": "file_type", "choice": "include"}])


def test_publish_chosen_puts_the_threads_loop_back_as_it_found_it(registry):
    import asyncio
    _survey(registry)
    _event(registry, "docs", "folder")
    pub = MagicMock()
    pub.publish_sub_resources.return_value = {"docs": "g"}
    pub._asset_maker.get_asset_by_guid.return_value = {"guid": "g"}
    mine = asyncio.new_event_loop()
    asyncio.set_event_loop(mine)
    resource_scope.publish_chosen(registry, "p", github_url="u", asset_guid="a", curation_id="", author="dan",
                                  locators=["docs"], publisher=pub)
    assert asyncio.get_event_loop() is mine and not mine.is_closed()
    mine.close()
    asyncio.set_event_loop(None)

    # a worker thread: whatever loop state it had on entry is its state on exit, and the call still worked
    import threading
    seen = {}

    def has_loop():
        try:
            asyncio.get_event_loop()
            return True
        except RuntimeError:
            return False

    def work():
        seen["before"] = has_loop()
        seen["out"] = resource_scope.publish_chosen(registry, "p", github_url="u", asset_guid="a", curation_id="",
                                                    author="dan", locators=["docs"], publisher=pub)
        seen["after"] = has_loop()
    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert seen["after"] == seen["before"] and seen["out"]["guids"] == {"docs": "g"}


def test_a_legacy_file_type_row_is_ignored_everywhere(registry):
    """Nothing writes kind=file_type any more, but a row inserted by hand must not be counted, shown or published."""
    _survey(registry)
    with registry._conn() as conn:
        conn.execute("INSERT INTO resource_scope_events (resource_type, resource_slug, locator, kind, choice, action, source, "
                     "proposal_rule, reason, author, changed_at) VALUES ('repo', 'p', 'Python', 'file_type', 'include', 'set', "
                     "'person', '', '', 'dan', '2026-10-07T00:00:00')")
        conn.execute("INSERT INTO resource_scope_events (resource_type, resource_slug, locator, kind, choice, action, source, "
                     "proposal_rule, reason, author, changed_at) VALUES ('repo', 'p', 'docs', 'file_type', 'leave_out', 'set', "
                     "'person', '', '', 'dan', '2026-10-07T00:00:01')")      # collides with a real candidate's locator
    assert registry.current_resource_scope("repo", "p") == {}
    v = resource_scope.build_view(registry, "p")
    assert "Python" not in [r["locator"] for r in v["rows"]]
    assert v["manifest"]["chosen"] == [] and v["manifest"]["items"] == 0 and v["manifest"]["left_out"] == 0
    assert _row(v, "docs")["choice"] == "" and _row(v, "docs")["proposed"] is True
    assert resource_scope.chosen_locators(registry, "p") == []
    pub = MagicMock()
    pub.publish_sub_resources.return_value = {}
    _event(registry, "docs", "folder")
    resource_scope.publish_chosen(registry, "p", github_url="u", asset_guid="a", curation_id="", author="dan",
                                  locators=resource_scope.chosen_locators(registry, "p"), publisher=pub)
    assert pub.publish_sub_resources.call_args[0][3] == ["docs"], "only the real folder reaches the publisher"


def test_the_rename_sentence_from_the_publisher_still_ends_the_sub_resources_step(registry):
    """File-types merge: publish_chosen must hand the publisher's rename counts to the step message."""
    from resource_explorer.curate_plan import Curations
    from resource_explorer.workflows.curate_commit import STEPS, execute_curation
    _survey(registry)
    _keep_a_survey(registry)
    registry.set_egeria_asset_guid("p", "asset-guid")
    _event(registry, "docs", "folder")
    pub = _publisher_with_egeria(registry)
    real = pub.publish_sub_resources

    def with_renames(*a, **k):            # the real method resets the counts; the publisher sets them as it renames
        out = real(*a, **k)
        pub.rename_counts = {"updated": 2, "failed": 1}
        return out
    pub.publish_sub_resources = with_renames
    with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", MagicMock(return_value=pub)), \
         patch("resource_explorer.repo_publish.publish_snapshot", return_value={
             "ok": True, "asset_guid": "asset-guid", "report_guid": "r", "read_back": True, "surveyed_at": "2026-10-07T01:00:00",
             "reused": False, "annotation_count": 1}), \
         patch("resource_explorer.repo_publish.resolve_project_context", return_value={"guid": "x"}):
        cid = Curations(registry).create("repo", "p", author="dan", selection={"sub_resources": ["docs"]}, manifest={}, steps=list(STEPS))["id"]
        rec = execute_curation(registry, cid)
    step = next(s for s in rec["steps"] if s["name"] == "sub_resources")
    assert step["detail"].endswith(" · 2 names updated · 1 could not be updated"), step


# ── a container-only folder is a holder, never a proposal ────────────────────

_HOLDER = ("open-metadata-implementation", "folder", "worthy", "container_for_worthy_file")
_HOLDER_FILE = ("open-metadata-implementation/README.md", "file", "worthy", "well_known_file")


def test_a_container_only_folder_is_not_a_proposal_and_not_in_accept_all(client, registry):
    _survey(registry, extra=(_HOLDER, _HOLDER_FILE))
    v = resource_scope.build_view(registry, "p")
    h = _row(v, "open-metadata-implementation")
    assert h["proposed"] is False and h["holder_only"] is True and h["label"] == "worthy"
    assert "open-metadata-implementation" not in v["proposals"]
    assert "open-metadata-implementation/README.md" in v["proposals"]            # the file itself still is
    assert v["manifest"]["proposals_not_accepted"] == 5                          # docs, 2 docs files, src, the README
    props = client.get("/api/projects/p/scope-events").json()["proposals"]
    r = _post(client, [{"locator": l, "kind": _row(v, l)["kind"], "choice": "include", "source": "proposal",
                        "proposal_rule": "worthy"} for l in props])
    assert r.status_code == 200
    chosen = {e["locator"] for e in registry.list_resource_scope_events("repo", "p")}
    assert "open-metadata-implementation" not in chosen                          # swept up by accept-all: no
    after = resource_scope.build_view(registry, "p")
    assert after["manifest"]["container_locators"] == ["open-metadata-implementation"]   # still the file's container
    assert _row(after, "open-metadata-implementation")["role"] == "container"
    assert after["manifest"]["folders"] == 2        # docs and src only


def test_including_one_file_does_not_choose_its_holder_folder(registry):
    _survey(registry, extra=(_HOLDER, _HOLDER_FILE))
    _event(registry, "open-metadata-implementation/README.md", "file")
    v = resource_scope.build_view(registry, "p")
    h = _row(v, "open-metadata-implementation")
    assert (h["choice"], h["role"], h["proposed"]) == ("", "container", False)
    assert [e["locator"] for e in registry.list_resource_scope_events("repo", "p")] == [
        "open-metadata-implementation/README.md"]


def test_a_folder_worthy_on_its_own_is_still_proposed_even_when_a_worthy_file_is_inside(registry):
    _survey(registry, extra=(_HOLDER_FILE,))
    v = resource_scope.build_view(registry, "p")
    assert _row(v, "docs")["proposed"] is True and _row(v, "docs")["holder_only"] is False
    assert _row(v, "src")["proposed"] is True


def test_an_already_stored_survey_gets_the_same_rule_without_a_resurvey(registry):
    """The stored shape is unchanged (label 'worthy', reason in the summary); the rule is read at view time."""
    _survey(registry, extra=(_HOLDER, _HOLDER_FILE))
    stored = [f for f in registry.query_findings("p", "repo_sub_resource_survey")
              if f["check_name"] == "open-metadata-implementation"][0]
    assert stored["label"] == "worthy" and stored["summary"] == "container_for_worthy_file"
    assert resource_scope.build_view(registry, "p")["rows"]                       # no re-survey happened
    assert _row(resource_scope.build_view(registry, "p"), "open-metadata-implementation")["proposed"] is False


def test_the_egeria_lane_says_holder_only_in_the_script():
    import pathlib
    js = (pathlib.Path(resource_scope.__file__).parent / "web/static/next/stages/resource-scope.js").read_text()
    assert "holder only · created with the file" in js


def test_the_publish_of_a_file_and_its_holder_is_unchanged(registry):
    """The Egeria writes are those of the chosen file plus its container. This test also passes on origin/main
    before this change (run there: same two qualifiedNames, same proofs)."""
    from resource_explorer.curate_plan import Curations
    from resource_explorer.workflows.curate_commit import STEPS, execute_curation
    _survey(registry, extra=(_HOLDER, _HOLDER_FILE))
    _keep_a_survey(registry)
    registry.set_egeria_asset_guid("p", "asset-guid")
    _event(registry, "open-metadata-implementation/README.md", "file")
    sel = {"confirm": [], "sub_resources": resource_scope.chosen_locators(registry, "p")}
    assert sel["sub_resources"] == ["open-metadata-implementation/README.md"]
    pub = _publisher_with_egeria(registry)
    with patch("resource_explorer.surveyors.egeria_publisher.EgeriaPublisher", MagicMock(return_value=pub)), \
         patch("resource_explorer.repo_publish.publish_snapshot", return_value={
             "ok": True, "asset_guid": "asset-guid", "report_guid": "r", "read_back": True, "surveyed_at": "2026-10-07T01:00:00",
             "reused": False, "annotation_count": 1}), \
         patch("resource_explorer.repo_publish.resolve_project_context", return_value={"guid": "x"}):
        cid = Curations(registry).create("repo", "p", author="peterprofile", selection=sel, manifest={}, steps=list(STEPS))["id"]
        execute_curation(registry, cid)
    names = [b["replacementProperties"]["qualifiedName"] for b in pub.created]
    assert len(names) == 2 and any(n.endswith("open-metadata-implementation") for n in names) \
        and any(n.endswith("open-metadata-implementation/README.md") for n in names), names
    proofs = [x for x in registry.list_catalogue_commit_proofs("p") if x["node_kind"] == "sub_resource"]
    assert sorted((x["table_name"], x["detail"]["role"]) for x in proofs) == [
        ("open-metadata-implementation", "container"), ("open-metadata-implementation/README.md", "chosen")]
