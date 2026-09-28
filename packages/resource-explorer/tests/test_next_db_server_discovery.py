"""Database-server discovery in /next -- the real port of classic's
(index.html) server-side database introspection: register a Postgres
server once, then discover and add the databases actually on it.

Before this, `/next` had NOTHING built for databases -- the "+" find/discover
action fell through to a generic old-UI-link stub for `state.resourceType
=== 'db'`, even though the backend (`web/routes/db_servers.py`) was fully
built with zero consumers in `/next`.

No browser verification with a signed-in session is asserted by these
tests -- see the same static-source-assertion pattern
test_next_discovery_import_search.py established for the analogous repo-side
feature, including its identical `_app()`/module-source helpers.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    src = (NEXT / "app.js").read_text(encoding="utf-8")
    for f in sorted((NEXT / "stages").glob("*.js")):
        src += "\n" + f.read_text(encoding="utf-8")
    return src


def _db_discovery_src():
    return (NEXT / "db-server-discovery.js").read_text(encoding="utf-8")


def _discovery_stage_src():
    return (NEXT / "stages" / "discovery.js").read_text(encoding="utf-8")


def _reapi_src():
    return (NEXT.parent / "re-api.js").read_text(encoding="utf-8")


class TestPlacementIsCorpusLevelNotAStage:
    """Same rule discovery-import.js follows: this is reached from the
    sidebar's find-repos action, not from a per-stage sub-tab."""

    def test_module_exists_standalone(self):
        assert (NEXT / "db-server-discovery.js").exists()

    def test_discovery_stage_module_still_exports_nothing(self):
        src = _discovery_stage_src()
        assert "export {};" in src
        assert "discoverDatabases" not in src
        assert "registerDbServer" not in src

    def test_app_js_wires_find_repos_to_the_new_module_for_databases(self):
        app = _app()
        assert "from '/static/next/db-server-discovery.js';" in app
        assert "openFindDbServersDialog" in app
        block = app[app.index("'find-repos': ()"):]
        block = block[:block.index("},\n") + 3]
        assert "state.resourceType === 'db'" in block
        assert "openFindDbServersDialog();" in block
        # Repos keep their own real port; filesystem still falls through to
        # the old-UI-link stub -- this change must not touch either.
        assert "openFindReposDialog();" in block
        assert "oldUiHref" in block

    def test_find_title_comment_no_longer_calls_databases_unbuilt(self):
        # Regression guard for the stale comment this session's own context
        # named explicitly: FIND_TITLE's header used to admit databases had
        # nothing built. It must now say the opposite.
        app = _app()
        comment = app[app.index("// The find/discover action's title per resource type."):
                       app.index("const FIND_TITLE")]
        assert "db-server-discovery.js" in comment
        assert "not-yet-ported" not in comment or "filesystem" in comment


class TestApiWrappers:
    def test_all_seven_wrappers_present(self):
        api = _reapi_src()
        assert "export const listDbServers = () => get('/api/db-servers/');" in api
        assert "export const registerDbServer = " in api
        assert "export const deleteDbServer = " in api
        assert "export const testDbServer = " in api
        assert "export const testDbServerInline = " in api
        assert "export const discoverDatabases = " in api
        assert "export const addDiscoveredDatabase = " in api

    def test_delete_uses_delete_method(self):
        api = _reapi_src()
        fn = api[api.index("export const deleteDbServer"):]
        fn = fn[:fn.index(";\n")]
        assert "method: 'DELETE'" in fn

    def test_discover_posts_to_the_real_route(self):
        api = _reapi_src()
        fn = api[api.index("export const discoverDatabases"):]
        fn = fn[:fn.index(";\n")]
        assert "/discover" in fn
        assert "post(" in fn

    def test_add_database_sends_query_params_not_json_body(self):
        # web/routes/db_servers.py's add_database_from_server takes
        # database_name/display_name as plain (query) params, not a Pydantic
        # body model -- a JSON-body POST here would silently 422.
        api = _reapi_src()
        fn = api[api.index("export const addDiscoveredDatabase"):]
        fn = fn[:fn.index("\n\n")]
        assert "add-database" in fn
        assert "encodeURIComponent(databaseName)" in fn
        assert "database_name=" in fn

    def test_test_inline_hits_the_pre_registration_route(self):
        api = _reapi_src()
        assert "_test-inline" in api


class TestServerListAndRegistration:
    def test_empty_state_offers_registration(self):
        src = _db_discovery_src()
        assert "No database servers are registered yet" in src
        assert 'data-act="new-server"' in src

    def test_registered_server_row_offers_test_discover_remove(self):
        src = _db_discovery_src()
        assert "data-test=" in src
        assert "data-discover=" in src
        assert "data-remove=" in src

    def test_missing_credentials_are_flagged(self):
        src = _db_discovery_src()
        assert "no credentials stored" in src

    def test_register_form_requires_slug_name_host_user(self):
        src = _db_discovery_src()
        fn = src[src.index("async function submitRegister"):]
        fn = fn[:fn.index("\n}\n")]
        assert "Slug, display name, host, and username are required" in fn

    def test_register_form_fields_match_server_registration_model(self):
        # ServerRegistration (web/routes/db_servers.py), verbatim field
        # names -- a mismatch here silently drops a field server-side.
        src = _db_discovery_src()
        for field in (
            "slug", "display_name", "db_type", "host", "port", "description",
            "db_user", "db_password", "egeria_host", "egeria_url",
            "egeria_server", "egeria_user", "egeria_password", "group_slug",
        ):
            assert f"{field}:" in src

    def test_egeria_section_is_collapsed_by_default(self):
        src = _db_discovery_src()
        assert "showEgeria: false" in src

    def test_inline_test_before_registration_is_wired(self):
        src = _db_discovery_src()
        assert "testDbServerInline" in src
        assert 'data-act="test-inline"' in src

    def test_remove_server_confirms_first(self):
        src = _db_discovery_src()
        fn = src[src.index("async function removeServerRow"):]
        fn = fn[:fn.index("\n}\n")]
        assert "window.confirm" in fn
        assert "deleteDbServer(slug)" in fn


class TestDiscoverAndAddFlow:
    def test_discover_calls_the_real_discover_endpoint(self):
        src = _db_discovery_src()
        fn = src[src.index("async function openDiscover"):]
        fn = fn[:fn.index("\n}\n")]
        assert "discoverDatabases(slug)" in fn

    def test_already_registered_rows_are_disabled_and_pre_checked(self):
        # Classic's own rule (index.html's _fetchDiscoverDatabases): a
        # database already in the databases table shows greyed-out and
        # checked, not silently re-addable.
        src = _db_discovery_src()
        assert "db.is_registered" in src
        assert "disabled checked" in src

    def test_nothing_is_added_until_add_selected_is_pressed(self):
        src = _db_discovery_src()
        assert 'data-act="add-selected"' in src
        assert "addSelected" in src

    def test_add_selected_calls_the_real_endpoint_per_database(self):
        src = _db_discovery_src()
        fn = src[src.index("async function addSelected"):]
        fn = fn[:fn.index("refreshGroupsAndSidebar")]
        assert "addDiscoveredDatabase(slug, db.name)" in fn
        # Loops rather than assuming a single bulk endpoint -- the backend
        # route only ever registers one database name at a time.
        assert "for (const db of chosen)" in fn

    def test_add_selected_reports_partial_failures_not_just_a_count(self):
        # find-absence-as-answer-style check: a failure per database must
        # not be silently swallowed by only counting successes.
        src = _db_discovery_src()
        fn = src[src.index("async function addSelected"):]
        fn = fn[:fn.index("refreshGroupsAndSidebar")]
        assert "failures" in fn
        assert "catch (err)" in fn


class TestSidebarRefreshAfterAdding:
    """The task's item 4: after adding, state.databases and the sidebar must
    reflect the new rows without a full reload."""

    def test_add_selected_refreshes_the_sidebar(self):
        src = _db_discovery_src()
        fn = src[src.index("async function addSelected"):]
        fn = fn[:fn.index("\n}\n")]
        assert "refreshGroupsAndSidebar()" in fn

    def test_register_and_remove_also_refresh_the_sidebar(self):
        # A newly registered server (with its group_slug) and a removed one
        # both change what the sidebar's group facets should show.
        src = _db_discovery_src()
        for fn_name in ("async function submitRegister", "async function removeServerRow"):
            fn = src[src.index(fn_name):]
            fn = fn[:fn.index("\n}\n")]
            assert "refreshGroupsAndSidebar()" in fn

    def test_refresh_helper_actually_refetches_databases_when_loaded(self):
        # Confirms the mechanism this module relies on rather than assuming
        # it: refreshGroupsAndSidebar() in app.js only re-fetches
        # state.databases when state.databasesLoaded is true -- true by
        # construction here, since the "+" action is only reachable once the
        # DBs sidebar chip has been switched to (which sets that flag).
        app = _app()
        fn = app[app.index("export async function refreshGroupsAndSidebar"):]
        fn = fn[:fn.index("\n}\n")]
        assert "state.databasesLoaded ? listDatabases()" in fn
        assert "state.databases = databases.value" in fn


class TestOpenDialogWideModeReused:
    def test_uses_the_wide_dialog_option(self):
        src = _db_discovery_src()
        assert "{ wide: true }" in src


class TestEgeriaHostDefaultsForDocker:
    """Found live, 2026-09-27: `egeria_host` stayed empty by default, so
    `databases.py`'s own runtime fallback (`egeria_host or host`) silently
    used the DB's own "localhost" — wrong whenever Egeria runs in Docker
    (the common case), since "localhost" from inside that container means
    the container itself, never the Mac's Postgres."""

    def test_applies_host_docker_internal_when_db_host_is_localhost(self):
        src = _db_discovery_src()
        assert "applyEgeriaHostDefault" in src
        fn = src[src.index("function applyEgeriaHostDefault"):]
        fn = fn[:fn.index("\n}\n")]
        assert "'host.docker.internal'" in fn
        assert "f.host === 'localhost'" in fn
        assert "f.host === '127.0.0.1'" in fn

    def test_never_overwrites_an_already_set_value(self):
        src = _db_discovery_src()
        fn = src[src.index("function applyEgeriaHostDefault"):]
        fn = fn[:fn.index("\n}\n")]
        assert "if (f.egeria_host) return;" in fn

    def test_runs_on_every_form_read_not_only_at_submit(self):
        src = _db_discovery_src()
        read_fn = src[src.index("function readFormFromDom"):]
        read_fn = read_fn[:read_fn.index("\n}\n")]
        assert "applyEgeriaHostDefault();" in read_fn


class TestNetworkFailureGetsAClearMessage:
    """Found live, 2026-09-27: a stopped/unreachable RE server made the
    Register Database Server dialog show the raw "Failed to fetch" three
    times over ("Could not load registered servers: Failed to fetch",
    "Failed to fetch", "Request failed: Failed to fetch") -- read as a
    database or Egeria problem rather than what it actually was."""

    def test_request_wraps_a_fetch_level_failure_with_a_clear_message(self):
        src = _reapi_src()
        fn = src[src.index("async function request"):]
        fn = fn[:fn.index("\nconst get")]
        assert "catch (err)" in fn
        assert "is not responding" in fn
        assert "window.location.origin" in fn
        # The raw browser message must never reach the caller unwrapped --
        # every caller reads `err.message` and would otherwise show the
        # literal "Failed to fetch" text this fix exists to replace.
        assert "new ApiError(" in fn
