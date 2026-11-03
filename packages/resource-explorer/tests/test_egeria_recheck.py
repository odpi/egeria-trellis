"""Proactive sweep: recheck every cached Egeria GUID instead of waiting for
reactive detection to trip over one.

Egeria's repository was reset independently of RE's registry (see
test_egeria_linkage_divergence.py), which leaves every cached
`egeria_asset_guid` pointing at nothing. Reactive detection (`guard_linkage`)
only records a divergence the next time something actually *uses* the cached
GUID — a survey, a publish. Until then, `Admin > Egeria Links` stays empty
even though every one of those resources is broken, because nothing has
asked Egeria yet. `recheck_all_linkages` closes that gap by asking up front.

The design decision under test that most needs its own coverage: a
*connection* failure (Egeria unreachable, wrong credentials) must never be
recorded as a stale linkage. Only a confirmed "that GUID does not exist"
answer counts — anything else is an ordinary error, kept out of the stale
list, because marking a healthy resource stale sends a human to reconcile a
catalog link that was never actually broken.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resource_explorer.egeria_linkage import recheck_all_linkages
from resource_explorer.registry import DatabaseEntity, Project, ProjectRegistry

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="alive", display_name="Alive Repo",
                    github_url="https://github.com/o/alive", description="",
                    egeria_asset_guid="guid-alive"))
    reg.add(Project(slug="gone", display_name="Gone Repo",
                    github_url="https://github.com/o/gone", description="",
                    egeria_asset_guid="guid-gone"))
    reg.add(Project(slug="unreachable", display_name="Unreachable Repo",
                    github_url="https://github.com/o/unreachable", description="",
                    egeria_asset_guid="guid-unreachable"))
    reg.add(Project(slug="never-published", display_name="Never Published",
                    github_url="https://github.com/o/never-published", description=""))
    return reg


UNKNOWN_GUID_ERROR = Exception(
    "OMRS-REPOSITORY-404-002 The entity identified with guid guid-gone "
    "is not known to the open metadata repository")
CONNECTION_ERROR = Exception("Connection refused")


def _fake_element_client(behaviors: dict):
    """A stand-in for pyegeria's MetadataExpert.

    The sweep probes with `get_metadata_element_by_guid`, not AssetMaker's
    `get_asset_by_guid` — AssetMaker raises NotFound for element types it does
    not serve, including the SourceControlLibrary that EgeriaPublisher creates
    for a repo, so a sweep built on it marked freshly republished repos stale.
    Verified live 2026-08-21.

    Two "absent" shapes are modelled because the real client has both: it raises
    for some misses and returns a plain string sentinel for others, and treating
    the sentinel as a hit would make a missing element read as a successful check.
    """

    class FakeElementClient:
        def __init__(self, *a, **kw):
            pass

        def create_egeria_bearer_token(self, *a, **kw):
            pass

        def set_bearer_token(self, *a, **kw):    # the factory hands the caller's token (Brief I)
            pass

        def get_metadata_element_by_guid(self, guid, *a, **kw):
            outcome = behaviors.get(guid)
            if isinstance(outcome, BaseException):
                raise outcome
            if outcome is not None:
                return outcome          # e.g. a "no elements found" string
            return {"elementHeader": {}, "guid": guid}

    return FakeElementClient


def _patched(behaviors: dict):
    """Patch the client so recheck_all_linkages never touches a live server."""
    return patch("pyegeria.omvs.metadata_expert.MetadataExpert",
                 _fake_element_client(behaviors))


class TestRecheckSweep:

    def test_resolving_guid_clears_a_previously_recorded_stale_row(self, registry):
        # Simulate: 'alive' was marked stale by some earlier failure, but the
        # link has since been fixed (republished, or Egeria recovered).
        registry.mark_egeria_linkage_stale("repo", "alive", "guid-alive", "old failure")
        assert registry.get_egeria_linkage("repo", "alive") is not None

        with _patched({}):  # every guid resolves
            result = recheck_all_linkages(registry, entity_types=["repo"])

        assert registry.get_egeria_linkage("repo", "alive") is None
        row = [d for d in result["details"] if d["slug"] == "alive"][0]
        assert row["result"] == "ok"

    def test_unknown_guid_error_records_a_divergence(self, registry):
        with _patched({"guid-gone": UNKNOWN_GUID_ERROR}):
            result = recheck_all_linkages(registry, entity_types=["repo"])

        assert result["stale"] == 1
        linkage = registry.get_egeria_linkage("repo", "gone")
        assert linkage is not None
        assert linkage["stale_guid"] == "guid-gone"

    def test_connection_error_does_not_mark_anything_stale(self, registry):
        """The important case: Egeria being unreachable must never look like a
        confirmed divergence. Assert the stale table stays empty."""
        with _patched({"guid-unreachable": CONNECTION_ERROR}):
            result = recheck_all_linkages(registry, entity_types=["repo"])

        assert result["errors"] == 1
        assert registry.get_egeria_linkage("repo", "unreachable") is None
        assert registry.list_stale_egeria_linkages() == []

    def test_resources_with_no_cached_guid_are_skipped_not_ok(self, registry):
        with _patched({}):
            result = recheck_all_linkages(registry, entity_types=["repo"])

        # 3 repos have a cached guid (alive, gone-turned-ok-here, unreachable);
        # 'never-published' has none and must not be counted as checked/ok.
        assert result["skipped"] == 1
        assert result["checked"] == 3
        slugs_checked = {d["slug"] for d in result["details"]}
        assert "never-published" not in slugs_checked

    def test_dry_run_writes_nothing(self, registry):
        before = registry.list_stale_egeria_linkages()
        with _patched({"guid-gone": UNKNOWN_GUID_ERROR}):
            result = recheck_all_linkages(registry, entity_types=["repo"], dry_run=True)

        # The probe still ran (counts reflect reality) ...
        assert result["stale"] == 1
        # ... but nothing was written.
        assert registry.list_stale_egeria_linkages() == before == []

    def test_dry_run_does_not_clear_an_existing_stale_row_either(self, registry):
        registry.mark_egeria_linkage_stale("repo", "alive", "guid-alive", "old failure")
        with _patched({}):
            recheck_all_linkages(registry, entity_types=["repo"], dry_run=True)
        assert registry.get_egeria_linkage("repo", "alive") is not None

    def test_counts_add_up(self, registry):
        with _patched({"guid-gone": UNKNOWN_GUID_ERROR,
                       "guid-unreachable": CONNECTION_ERROR}):
            result = recheck_all_linkages(registry, entity_types=["repo"])

        assert result["checked"] == result["ok"] + result["stale"] + result["errors"]
        assert result["ok"] == 1
        assert result["stale"] == 1
        assert result["errors"] == 1

    def test_covers_databases_too(self, registry):
        registry.register_database(DatabaseEntity(
            slug="db1", display_name="DB One", db_type="postgresql",
            host="localhost", port=5432, database_name="db1",
            egeria_asset_guid="guid-db-gone"))

        with _patched({"guid-db-gone": UNKNOWN_GUID_ERROR}):
            result = recheck_all_linkages(registry, entity_types=["database"])

        assert result["checked"] == 1
        assert result["stale"] == 1
        assert registry.get_egeria_linkage("database", "db1") is not None
