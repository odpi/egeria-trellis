"""Source-level pins for the /next resource header's `remove` button
(parity inventory D-10 / F-03, CLASSIC-VS-NEXT-PARITY-2026-09-30.md).

Defect: the header's remove handler called `removeProject` for EVERY kind, so
a database sent `DELETE /api/projects/{dbslug}` (repo-only route, 404), and
the Schema Inventory pane drew the header without ever calling
`bindResourceHeader()`, so the button did nothing there.

No JS runner in CI; these pin the fix at the source level, the same pattern
as test_next_resource_header_publish_note.py. Not a browser verification.
"""
from __future__ import annotations

from pathlib import Path

from tests.test_next_resource_header_publish_note import _fn_decl

NEXT_DIR = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT_DIR / "app.js"
RE_API = NEXT_DIR.parent / "re-api.js"


def _bind_src() -> str:
    return _fn_decl(APP_JS.read_text(), "export function bindResourceHeader(")


def _remove_click_src() -> str:
    """The removal commit. It left bindResourceHeader() for the resource menu on
    the top-bar name (REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md): the same
    dispatch by kind, now in commitResourceRemoval()."""
    return _fn_decl(APP_JS.read_text(), "async function commitResourceRemoval(")


def _remove_panel_src() -> str:
    return _fn_decl(APP_JS.read_text(), "function openRemovePanel(")


def _confirm_src() -> str:
    return _fn_decl(APP_JS.read_text(), "export function removeConfirmationHtml(")


class TestRemoveDispatchesByKind:
    def test_handler_uses_removeEntity_with_translated_type(self):
        src = _remove_click_src()
        assert "removeEntity(entityType, slug)" in src
        # the panel translates the type once and hands it to the commit
        assert "apiEntityType(state.resourceType)" in _remove_panel_src()
        assert "commitResourceRemoval(entityType, slug)" in _remove_panel_src()

    def test_handler_no_longer_hardwires_the_repo_route(self):
        # known negative: the unfixed handler called removeProject directly
        assert "removeProject(" not in _remove_click_src()

    def test_removeEntity_routes_each_kind_to_its_own_delete(self):
        api = RE_API.read_text()
        start = api.index("export const removeEntity")
        body = api[start:start + 300]
        assert "removeDatabase(slug)" in body and "removeFilesystem(slug)" in body
        assert "/api/databases/" in api and "method: 'DELETE'" in api

    def test_the_right_list_is_pruned_not_always_projects(self):
        src = _remove_click_src()
        assert "state[listKey] = state[listKey].filter" in src
        assert "state.projects = state.projects.filter" not in src
        assert "'databases'" in src and "'filesystems'" in src


class TestSchemaInventoryBindsTheHeader:
    def test_schema_inventory_pane_calls_bindResourceHeader(self):
        src = _fn_decl(APP_JS.read_text(), "async function loadSchemaInventoryPane(")
        assert "resourceHeaderHtml(slug)" in src      # header is drawn...
        assert "bindResourceHeader();" in src          # ...and now bound

    def test_known_negative_other_pane_without_header_is_not_flagged(self):
        # the deferred (non-db) branch draws no header, so needs no binding
        src = _fn_decl(APP_JS.read_text(), "async function loadSchemaInventoryPane(")
        head = src[:src.index("const slug = state.selectedSlug")]
        assert "resourceHeaderHtml" not in head


class TestConfirmationNamesWhatItDoesAndDoesNotTouch:
    def test_database_wording(self):
        src = _confirm_src()
        db = src[src.index("entityType === 'database'"):src.index("entityType === 'filesystem'")]
        assert "database" in db and "survey records" in db
        assert "Not touched" in db and "source database itself" in db
        assert "published to Egeria" in db and "server registration" in db
        assert "cannot be undone" in db

    def test_filesystem_wording(self):
        src = _confirm_src()
        fs = src[src.index("entityType === 'filesystem'"):]
        assert "files on disk" in fs and "Not touched" in fs

    def test_repo_wording_kept(self):
        src = _confirm_src()
        assert "GitHub repository" in src and "local survey data" in src

    def test_database_text_does_not_call_it_a_repo(self):
        # known negative: the old text was repo-flavoured for every kind
        src = _confirm_src()
        db = src[src.index("entityType === 'database'"):src.index("entityType === 'filesystem'")]
        assert "repo" not in db.lower()

    def test_handler_renders_the_per_kind_helper(self):
        assert "removeConfirmationHtml(entityType, slug)" in _remove_panel_src()
