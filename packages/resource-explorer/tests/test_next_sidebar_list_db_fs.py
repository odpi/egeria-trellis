"""Sidebar lists databases/filesystems in /next (re/next-sidebar-list-db-fs,
2026-09-22), and groups + multi-select bulk actions for them (re/multiselect-
groups-dbfs, 2026-09-23).

A prior agent unblocked /next's Survey pane for databases/filesystems
(PR #215, test_next_db_fs_gate_removal.py) but flagged, without fixing, that
the sidebar itself never called `listDatabases()`/`listFilesystems()` --
both already existed and worked in re-api.js, but nothing in app.js imported
or called them. Clicking the "DBs"/"FS" chips just showed a static
"Databases and filesystems · not built in /next" message with a link out to
classic, even though the chips looked like they should do something. The
only way to view a database/filesystem in /next was to hand-edit the URL
(`?type=db&resource=<slug>`).

That first slice made the sidebar fetch and render clickable rows for db/fs,
but deliberately left select-mode, disposition and group-collapse memory as
"repo-only in /next" (`nonRepoRowsHtml()`'s own header comment named exactly
this as a separate slice, not this one). This second slice is that slice:
the project owner confirmed (see the PR this test file ships with) that
groups and multi-select bulk actions are wanted for ALL resource types, not
just repos.

The audit behind this change found groups and disposition were ALREADY
generic end-to-end on the backend (`registry.py`'s `set_project_group`/
`set_database_group`/`set_filesystem_group`, `POST /{slug}/group`'s
dispatch, and PR #223's disposition generalization) -- the gap was purely
`/next`'s own `renderSidebar()`, gated to `state.resourceType === 'repo'`,
plus two missing fields (`working_set_hidden`/`is_published`) on
`DatabaseSummary`/`FileSystemSummary` that this file also pins. Rather than
keep a second, parallel `nonRepoRowsHtml()` renderer permanently behind an
apology banner, the grouped/select-mode list `renderSidebar()` already built
for repos is now the ONE renderer for all three resource types, driven by
`visibleRows()` (dispatches to `visibleProjects()` for repos,
`visibleNonRepoRows()` -- new -- for databases/filesystems).

No browser verification with a signed-in session is asserted by these tests
-- see test_next_discovery_import_search.py's identical rationale. These
tests grep/slice the concatenated source, the established pattern for
/next JS modules without a browser. Live browser verification of this slice
is documented in the PR description, not encoded as a test here.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
API = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "re-api.js"
ROUTES = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "routes"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _api():
    return API.read_text(encoding="utf-8")


def _worklist():
    return (NEXT / "worklist.js").read_text(encoding="utf-8")


def _databases_route():
    return (ROUTES / "databases.py").read_text(encoding="utf-8")


def _filesystems_route():
    return (ROUTES / "filesystems.py").read_text(encoding="utf-8")


class TestReApiAlreadyExposedTheseCalls:
    """Confirms the premise: the API helpers this slice wires up already
    existed and worked before this change -- the gap was purely that
    app.js never called them."""

    def test_list_databases_and_filesystems_exist(self):
        src = _api()
        assert "export const listDatabases = () => get('/api/databases/');" in src
        assert "export const listFilesystems = () => get('/api/filesystems/');" in src


class TestAppJsNowImportsAndUsesBothCalls:
    def test_imports_list_databases_and_filesystems(self):
        src = _app()
        start = src.index("} from '/static/re-api.js';")
        head = src[:start]
        assert "listDatabases" in head
        assert "listFilesystems" in head

    def test_ensure_resource_list_loaded_calls_both(self):
        src = _app()
        start = src.index("async function ensureResourceListLoaded(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "listDatabases()" in body
        assert "listFilesystems()" in body
        # Cached behind a loaded flag, not re-fetched unconditionally.
        assert "state.databasesLoaded" in body
        assert "state.filesystemsLoaded" in body


class TestStaticNotBuiltMessageIsGone:
    """The old blanket stub this task exists to replace."""

    def test_old_stub_message_is_gone(self):
        src = _app()
        assert "Databases and filesystems · not built in /next" not in src


class TestFetchOnSwitchBehaviour:
    """Switching the sidebar's Repos/DBs/FS chip used to just re-render from
    whatever was already fetched (repos only, fetched once in start()) --
    nothing fetched on switch, for any resource type, before this slice."""

    def test_type_chip_click_calls_switch_resource_type(self):
        src = _app()
        start = src.index("el.querySelectorAll('button[data-type]')")
        end = src.index("\n", start)
        line = src[start:end]
        assert "switchResourceType(b.dataset.type)" in line

    def test_switch_resource_type_fetches_and_reselects(self):
        src = _app()
        start = src.index("async function switchResourceType(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "ensureResourceListLoaded(type)" in body
        # Re-selects a resource of the NEW type when the old selection
        # doesn't belong to it -- otherwise selectedSlug can point at a
        # repo while resourceType says 'db'.
        assert "currentResourceRows()" in body
        assert "state.selectedSlug = rows[0]?.slug || null" in body

    def test_switch_resource_type_clears_selection_and_select_mode(self):
        # A selection is per-resource-type: `state.selected` holds bare
        # slugs, so a repo slug surviving a switch to 'db' would let a bulk
        # action fire against `state.databases` rows that don't exist, or
        # silently no-op / mis-target a database that happens to share a
        # slug. Added alongside select-mode going generic (this slice) --
        # before that, select-mode was unreachable outside 'repo' so this
        # gap was latent rather than live.
        src = _app()
        start = src.index("async function switchResourceType(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "state.selected.clear()" in body
        assert "state.selectMode = false" in body

    def test_start_fetches_the_url_named_type_on_boot(self):
        # ?type=db&resource=<slug> on a cold load must not leave
        # state.databases empty forever -- start() only ever fetched repos.
        src = _app()
        start = src.index("async function start() {")
        end = src.index("\nAuth.init(start)")
        body = src[start:end]
        assert "ensureResourceListLoaded(state.resourceType)" in body


class TestGroupsAndSelectModeAreNoLongerRepoOnly:
    """The core of this slice: `renderSidebar()`'s grouped list, select-mode
    toggle and disposition facets are now ONE code path for all three
    resource types, not a repo-only block plus an apology-banner sibling."""

    def test_no_separate_non_repo_row_renderer_remains(self):
        # nonRepoRowsHtml() and its private filteredNonRepoRows() helper are
        # gone -- `visibleRows()`/`visibleNonRepoRows()` plus the ONE grouped
        # list in renderSidebar() replace them.
        src = _app()
        assert "function nonRepoRowsHtml()" not in src
        assert "function filteredNonRepoRows()" not in src
        assert "state.resourceType !== 'repo' ? nonRepoRowsHtml()" not in src

    def test_select_mode_toggle_is_unconditional(self):
        src = _app()
        start = src.index("function renderSidebar() {")
        end = src.index("\nfunction bindSidebar() {", start)
        body = src[start:end]
        assert "data-act=\"select-mode\"" in body
        # The old repo-only gate and its stale placeholder copy are gone.
        assert "state.resourceType === 'repo' ? `" not in body
        assert "Select-mode bulk actions (scope, disposition, work lists, delete) are repo-only in /next." not in body
        assert "dispositions exist for repositories only" not in body

    def test_visible_rows_dispatches_by_resource_type(self):
        src = _app()
        assert "function visibleRows() {" in src
        assert "function visibleNonRepoRows() {" in src
        start = src.index("function visibleRows() {")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "visibleProjects()" in body
        assert "visibleNonRepoRows()" in body

    def test_visible_non_repo_rows_filters_on_hidden_and_disposition(self):
        # These fields are new on DatabaseSummary/FileSystemSummary (see
        # TestBackendSummariesGrowNewFields below) -- this pins that the
        # frontend actually reads them once they exist, not just that the
        # backend serializes them.
        src = _app()
        start = src.index("function visibleNonRepoRows() {")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "working_set_hidden" in body
        assert "dispositionFacet" in body

    def test_rows_render_clickable_slug_buttons_for_every_type(self):
        # Both repo and non-repo rows now share the same data-slug button --
        # the old data-nonrepo-slug wiring (and its own click handler) is
        # gone since there is no longer a second row shape.
        src = _app()
        assert "data-nonrepo-slug" not in src
        start = src.index("function renderSidebar() {")
        end = src.index("\nfunction bindSidebar() {", start)
        body = src[start:end]
        assert 'data-slug="${esc(p.slug)}"' in body

    def test_bulk_scope_actions_use_api_entity_type(self):
        src = _app()
        start = src.index("async function bulkScope(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "apiEntityType(state.resourceType)" in body
        # W1-A: the investigation is a parameter (the picker passes the one it
        # just chose), defaulting to the current one, so "add" still scopes
        # into the current investigation when nobody passes one.
        assert "invSlug = state.investigation" in src[start - 40:start + 120]
        assert "addInvestigationMember(invSlug, entityType, slug)" in body

    def test_bulk_hide_uses_api_entity_type(self):
        src = _app()
        start = src.index("async function bulkHide(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "apiEntityType(state.resourceType)" in body

    def test_bulk_disposition_handles_non_repo_via_set_entity_disposition(self):
        src = _app()
        start = src.index("async function bulkDisposition(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "setEntityDisposition(entityType, slug, disposition)" in body
        # Repos keep the github_url-keyed endpoint they always used.
        assert "setDisposition(p.github_url, disposition)" in body

    def test_bulk_remove_dispatches_by_entity_type(self):
        # Renamed from bulkDelete (REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md
        # §4: one word, "Remove"); same dispatch.
        src = _app()
        start = src.index("async function bulkRemove(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "removeEntity(entityType, slug)" in body

    def test_save_selection_as_work_list_passes_entity_type(self):
        src = _app()
        start = src.index("async function saveSelectionAsWorkList(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "entityType: apiEntityType(state.resourceType)" in body

    def test_header_hide_action_no_longer_hardcodes_repo(self):
        # The single-resource "hide" (now the resource menu's, moved out of
        # bindResourceHeader() by REPLY-DESIGNER-RESOURCE-CONTROLS-PLACEMENT.md)
        # used to call
        # setWorkingSetHidden('repo', ...) unconditionally -- silently
        # hiding the wrong row's preference whenever a database/filesystem
        # happened to share a slug with a repo, since resource_working_set
        # is keyed on (entity_type, entity_slug).
        src = _app()
        start = src.index("async function toggleHiddenFromMenu()")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "setWorkingSetHidden('repo'," not in body
        assert "setWorkingSetHidden(apiEntityType(state.resourceType), slug, hiding)" in body


class TestReApiAndWorklistGrowEntityTypeSupport:
    def test_remove_entity_dispatches_by_type(self):
        src = _api()
        assert "export const removeDatabase = " in src
        assert "export const removeFilesystem = " in src
        assert "export const removeEntity = " in src
        start = src.index("export const removeEntity = ")
        end = src.index(";", start)
        body = src[start:end]
        assert "removeDatabase(slug)" in body
        assert "removeFilesystem(slug)" in body
        assert "removeProject(slug)" in body

    def test_filesystem_delete_keeps_its_trailing_slash(self):
        # filesystems.py registers DELETE at "/{slug}/", not "/{slug}" --
        # dropping the slash 404s. Pinned here because it is exactly the
        # kind of one-character mismatch a "generalize the three deletes"
        # refactor could silently introduce.
        src = _api()
        start = src.index("export const removeFilesystem = ")
        end = src.index(";", start)
        assert "/${encodeURIComponent(slug)}/`" in src[start:end]

    def test_create_work_list_accepts_entity_type(self):
        src = _api()
        start = src.index("export const createWorkList = ")
        end = src.index(";", start)
        body = src[start:end]
        assert "entityType = 'repo'" not in body   # required, never defaulted
        assert "entity_type: requireKind('createWorkList', entityType)" in body

    def test_save_as_work_list_forwards_entity_type(self):
        src = _worklist()
        start = src.index("export async function saveAsWorkList(")
        end = src.index("\n}", start)
        body = src[start:end]
        assert "entityType = 'repo'" not in body   # required, never defaulted
        assert "entityType" in body[body.index("createWorkList("):]


class TestBackendSummariesGrowNewFields:
    """The one real backend gap the audit found: `DatabaseSummary`/
    `FileSystemSummary` already carried `disposition` and `group_slug`
    (both wired end-to-end before this PR -- see PR #223 and the groups
    registry methods), but neither carried `working_set_hidden` or
    `is_published`, so /next's shared `lifecycleMark()`/hide toggle had
    nothing to read. `registry.py`'s `is_working_set_hidden()` was already
    entity-type-generic; this just calls it from one more place."""

    def test_database_summary_has_working_set_hidden_and_is_published(self):
        src = _databases_route()
        assert "working_set_hidden: bool = False" in src
        assert "is_published: bool = False" in src
        assert 'working_set_hidden=registry.is_working_set_hidden("database", db.slug)' in src

    def test_filesystem_summary_has_working_set_hidden_and_is_published(self):
        src = _filesystems_route()
        assert "working_set_hidden: bool = False" in src
        assert "is_published: bool = False" in src
        assert 'registry.is_working_set_hidden("filesystem", fs.slug)' in src


class TestApiEntityTypeStillGuardsTheOnlySharpEdge:
    """'db' vs 'database' isn't a risk for the read-only list calls, but is
    for every write path this slice adds -- every bulk-action test above
    already pins `apiEntityType(state.resourceType)` at its call site; this
    just confirms the helper itself is untouched."""

    def test_api_entity_type_helper_still_present_and_unchanged(self):
        src = _app()
        assert "export function apiEntityType(resourceType) {" in src
        assert "resourceType === 'db' ? 'database' : resourceType" in src
