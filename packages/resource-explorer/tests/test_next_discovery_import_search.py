"""Discovery's corpus-level "find and import candidate repos" in /next
(NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md): a real port of classic's
(index.html) GitHub search, `/from-list` bulk loader, and inventory CSV
export -- placed at the sidebar's `find-repos` action rather than inside
`stages/discovery.js`, per SPEC-ACTIONABLE-AND-HONEST.md point 2's ruling
that finding/importing repos is corpus-level work, not a Discovery-stage
affordance.

No browser verification with a signed-in session is asserted by these
tests -- see NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md
for what was checked live. These tests grep/slice the concatenated source,
the established pattern for /next JS modules without a browser -- see
test_next_automate_pane.py / test_next_curate_pane.py's identical `_app()`
helper, reproduced here plus the new standalone module's own source.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _discovery_import_src():
    return (NEXT / "discovery-import.js").read_text(encoding="utf-8")


def _discovery_stage_src():
    return (NEXT / "stages" / "discovery.js").read_text(encoding="utf-8")


def _worklist_src():
    return (NEXT / "worklist.js").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


class TestPlacementIsCorpusLevelNotAStage:
    """The whole point of SPEC-ACTIONABLE-AND-HONEST.md point 2: this does
    not live in stages/discovery.js."""

    def test_discovery_stage_module_still_exports_nothing(self):
        # Discovery has no resource-scoped rendering of its own -- the
        # generic Questions engine already reaches it. A regression here
        # (someone adding search/import UI directly into this file) would
        # silently repeat the mistake the project owner corrected.
        src = _discovery_stage_src()
        assert "export {};" in src
        assert "searchDiscoveryRepos" not in src
        assert "discoverFromList" not in src
        assert "fetchInventoryCsv" not in src

    def test_discovery_import_module_exists_standalone(self):
        assert (NEXT / "discovery-import.js").exists()

    def test_app_js_wires_find_repos_to_the_new_module_for_repos(self):
        app = _app()
        assert "from '/static/next/discovery-import.js';" in app
        assert "openFindReposDialog" in app
        block = app[app.index("'find-repos': ()"):]
        block = block[:block.index("},\n") + 3]
        assert "state.resourceType === 'repo'" in block
        assert "openFindReposDialog();" in block
        # Each other resource type has its own dialog too (a file system's is the
        # register form, Files step 1); no link out to the old UI is left here.
        assert "oldUiHref" not in block


class TestApiWrappers:
    """The three re-api.js functions this feature needs -- two pre-existed
    (search, import), two are new (from-list, CSV export)."""

    def test_search_and_import_already_existed(self):
        api = _reapi_src()
        assert "export const searchDiscoveryRepos = (filters) => post('/api/discovery/search', filters);" in api
        assert "export const importDiscoveredRepos = " in api

    def test_from_list_wrapper_posts_text(self):
        api = _reapi_src()
        assert "export const discoverFromList = (text) => post('/api/discovery/from-list', { text });" in api

    def test_csv_export_wrapper_checks_response_before_treating_it_as_a_file(self):
        # Classic's own _downloadInventory (index.html) comment: a plain
        # <a download> silently saved a 404's JSON error body as a
        # .csv-shaped file. The port must check res.ok before returning
        # anything a caller could hand to a Blob.
        api = _reapi_src()
        assert "export async function fetchInventoryCsv()" in api
        body = api[api.index("export async function fetchInventoryCsv()"):]
        body = body[:body.index("\n}\n")]
        assert "/api/discovery/inventory.csv" in body
        assert "res.ok" in body
        # The failure is raised through the shared helper, which builds the ApiError.
        assert "throw await apiErrorFrom(res" in body
        assert "new ApiError(res.status, detail, path)" in api
        assert "content-disposition" in body.lower()

    def test_csv_export_wrapper_does_not_touch_the_dom(self):
        # re-api.js's own module docstring rule: nothing in this file
        # touches the DOM. The Blob/anchor/click sequence belongs in the
        # caller (discovery-import.js), not here.
        api = _reapi_src()
        body = api[api.index("export async function fetchInventoryCsv()"):]
        body = body[:body.index("\n}\n")]
        assert "document." not in body
        assert "Blob(" not in body
        assert "createObjectURL" not in body


class TestOpenDialogSupportsWideContent:
    def test_wide_option_widens_the_dialog(self):
        src = _worklist_src()
        assert "export function openDialog(title, sub, { wide = false } = {})" in src
        body = src[src.index("export function openDialog"):]
        body = body[:body.index("return el;")]
        assert "max-w-[960px]" in body
        assert "max-w-[640px]" in body

    def test_existing_two_arg_callers_are_unaffected(self):
        # Every pre-existing call site passes exactly (title, sub) -- the
        # new third argument must be optional so none of them need editing.
        app = _app()
        wl = _worklist_src()
        two_arg_calls = [
            l for l in (app + wl).splitlines()
            if "openDialog(" in l and "wide" not in l
        ]
        assert two_arg_calls, "expected at least one pre-existing two-arg call site"


class TestSearchAndListShareOneReviewTable:
    """Classic's two-step shape: search or load-a-list only produces review
    rows; nothing is registered until rows are selected and imported."""

    def test_two_modes_search_and_list(self):
        src = _discovery_import_src()
        assert "mode: 'search'" in src
        assert "view.mode = b.dataset.mode" in src

    def test_search_requires_at_least_one_filter(self):
        src = _discovery_import_src()
        body = src[src.index("async function runSearch"):]
        body = body[:body.index("\n}\n")]
        assert "Enter at least one filter" in body

    def test_search_criteria_match_repo_search_request_fields(self):
        # RepoSearchRequest (web/routes/discovery.py) field names, verbatim
        # -- a mismatch here would silently send an empty/ignored field.
        src = _discovery_import_src()
        for field in (
            "keyword", "min_stars", "language", "license", "pushed_after",
            "org", "topic", "include_archived", "include_forks",
        ):
            assert f"{field}:" in src or f"'{field}'" in src or f'"{field}"' in src

    def test_org_is_a_plain_search_field_not_a_separate_org_import_affordance(self):
        # There is no _expand_org-equivalent standalone "import an org"
        # button -- org is one RepoSearchRequest field like any other,
        # rendered by the same generic `field()`/`input()` helpers as
        # keyword/language/etc.
        src = _discovery_import_src()
        assert "field('org', 'Org', input('org'" in src
        assert "expandOrg" not in src
        # No standalone org-expansion CALL in the code -- the header comment
        # is allowed to mention `_expand_org` in prose (it explains why
        # there's no such affordance here), so only check for an actual
        # function/endpoint reference, not the bare substring.
        assert "/api/discovery/org" not in src
        assert "await _expand_org" not in src
        assert "expandOrgAction" not in src

    def test_from_list_accepts_paste_and_file_upload(self):
        src = _discovery_import_src()
        assert "data-list-file" in src
        assert "data-list-text" in src
        assert "type=\"file\"" in src
        assert 'accept=".csv,.txt,text/csv,text/plain"' in src

    def test_from_list_reports_what_the_file_actually_contained(self):
        # The gap classic's own comment names: a bare repo count hides rows
        # that were read but dropped. Must surface rows_read/skipped/
        # unreachable/expanded_orgs, not just len(repos).
        src = _discovery_import_src()
        fn = src[src.index("function renderListLoadSummary"):]
        fn = fn[:fn.index("\n}\n")]
        for field in ("rows_read", "already_registered", "expanded_orgs", "skipped", "unreachable"):
            assert field in fn

    def test_nothing_is_registered_until_import_is_pressed(self):
        src = _discovery_import_src()
        assert "Nothing is registered yet" in src
        assert "importSelected" in src
        # Rows default unchecked -- classic's own rule ("explicit select-all
        # opts in"), carried over rather than defaulting new rows to checked.
        checkbox_row = [l for l in src.splitlines() if 'data-row="${i}"' in l][0]
        assert "checked" not in checkbox_row.split("view.selected.has")[0]


class TestCsvExportIsWiredToTheRealDownloadPath:
    def test_download_button_present_in_list_mode(self):
        src = _discovery_import_src()
        assert 'data-act="download-inventory"' in src

    def test_download_checks_for_empty_and_builds_a_blob_download(self):
        src = _discovery_import_src()
        fn = src[src.index("async function downloadInventory"):]
        fn = fn[:fn.index("\n}\n")]
        assert "fetchInventoryCsv" in fn
        assert "empty file" in fn
        assert "Blob(" in fn
        assert "a.download = filename" in fn
        assert "revokeObjectURL" in fn


class TestDispositionCanBeSetBeforeImport:
    def test_all_valid_dispositions_are_offered_per_row(self):
        src = _discovery_import_src()
        for d in ("tracking", "investigating", "recommended", "using", "abandoned", "ignored"):
            assert f"{d}:" in src  # DISPOSITION_EMOJI keys

    def test_hidden_dispositions_prompt_for_a_reason(self):
        src = _discovery_import_src()
        fn = src[src.index("async function setRowDisposition"):]
        fn = fn[:fn.index("\n}\n")]
        assert "HIDDEN_DISPOSITIONS.has(disposition)" in fn
        assert "inPagePrompt" in fn

    def test_disposition_write_goes_through_the_shared_endpoint(self):
        src = _discovery_import_src()
        assert "setDisposition(r.html_url, disposition, reason)" in src
