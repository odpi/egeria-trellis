"""Egeria <-> RE divergence: detect a cached GUID that no longer exists.

Reported 2026-07-21: Egeria's repository was reset independently of RE's
registry, and the next filesystem resurvey failed with SERVER_ERROR_500 reaching
the UI verbatim — a Java stack with OMRS-REPOSITORY-404-002 buried in it and
nothing actionable. Retrying could never fix it, and nothing said why.

The design decision under test is "detect, don't auto-resolve": a stale root GUID
makes the whole Egeria-side lineage beneath it unverifiable, so recreating in
place risks duplicating elements and discarding catalog history. So detection
must record and report, and must not clear anything by itself.
"""
from __future__ import annotations

import pytest

from resource_explorer.egeria_linkage import (
    StaleEgeriaLinkageError,
    guard_linkage,
    is_unknown_guid_error,
)
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="zone2-tester")


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="myproj", display_name="My Proj",
                    github_url="https://github.com/o/myproj", description=""))
    return reg


# The shape actually reported: a 500 whose nested server message carries the 404.
REAL_ERROR = (
    "SERVER_ERROR_500 Egeria detected error: * relatedHTTPCode= `500` "
    "* exceptionErrorMessage= `OMRS-REPOSITORY-404-002 The entity identified with "
    "guid abc-123 is not known to the open metadata repository`"
)


# Captured verbatim 2026-08-20 by asking this deployment's live Egeria for a GUID
# that cannot exist (AssetMaker.get_asset_by_guid). Kept in full rather than
# paraphrased: the detector's whole job is to recognise what Egeria really says,
# and a hand-written approximation would let the two drift apart unnoticed.
LIVE_ERROR = """
=> \tCLIENT_ERROR_400
=>\tClient error occurred with status code `https://localhost:9443/servers/qs-view-server/api/open-metadata/asset-maker/assets/00000000-dead-beef-0000-000000000000/retrieve`.
\t* Egeria error information:
\t* relatedHTTPCode= `404`
\t* exceptionClassName= `org.odpi.openmetadata.frameworks.openmetadata.ffdc.InvalidParameterException`
\t* actionDescription= `getMetadataElementByGUID`
\t* exceptionErrorMessage= `OMAG-REPOSITORY-HANDLER-404-007 The Asset entity with unique identifier 00000000-dead-beef-0000-000000000000 is not found for method getMetadataElementByGUID of access service Open Metadata Store Services in open metadata server qs-metadata-store, error message was: OMRS-REPOSITORY-404-002 The entity identified with guid 00000000-dead-beef-0000-000000000000 passed on the getEntityDetail call is not known to the open metadata repository Enterprise`
"""


class TestDetection:

    def test_recognises_the_real_live_error(self):
        """Pinned to what the platform actually returned, not to a paraphrase.
        Note it labels itself CLIENT_ERROR_400 while relatedHTTPCode is 404 —
        one reason the detector keys on Egeria's message codes, not on status."""
        assert is_unknown_guid_error(Exception(LIVE_ERROR))

    def test_recognises_the_error_as_actually_reported(self):
        """Type-based detection would miss this: it arrives as a generic API
        error carrying a 500, with the real 404 code nested inside."""
        assert is_unknown_guid_error(Exception(REAL_ERROR))

    def test_recognises_the_bare_404_codes(self):
        assert is_unknown_guid_error(Exception("OMRS-REPOSITORY-404-007 ..."))
        assert is_unknown_guid_error(Exception("OMAG-COMMON-404-003 ..."))

    def test_an_empty_search_result_is_not_a_divergence(self):
        """The most damaging false positive available. "No elements found" is an
        ordinary empty result; treating it as divergence would send someone to
        reconcile a catalog that was never broken."""
        assert not is_unknown_guid_error(Exception("No elements found"))
        assert not is_unknown_guid_error(Exception("0 results returned for query"))

    def test_ordinary_failures_are_not_divergences(self):
        assert not is_unknown_guid_error(Exception("Connection refused"))
        assert not is_unknown_guid_error(Exception("SERVER_ERROR_500 JDBC-RESOURCE-"
                                                  "CONNECTOR-500-002 missing value for "
                                                  "column metadata_collection_guid"))


class TestGuard:

    def test_records_the_divergence_and_raises_a_named_error(self, registry):
        with pytest.raises(StaleEgeriaLinkageError) as ei:
            with guard_linkage(registry, "repo", "myproj", "My Proj", "abc-123"):
                raise Exception(REAL_ERROR)

        assert ei.value.stale_guid == "abc-123"
        # The message has to name the resolutions; being told what broke without
        # being told what to do is the state this replaces.
        msg = str(ei.value)
        assert "republish" in msg and "re-survey" in msg and "discard" in msg

        row = registry.get_egeria_linkage("repo", "myproj")
        assert row["status"] == "stale" and row["stale_guid"] == "abc-123"

    def test_the_original_error_is_preserved(self, registry):
        """The server-side detail is the only evidence of what Egeria actually
        said — a friendlier message must not replace it."""
        with pytest.raises(StaleEgeriaLinkageError) as ei:
            with guard_linkage(registry, "repo", "myproj", "My Proj", "abc-123"):
                raise Exception(REAL_ERROR)
        assert "OMRS-REPOSITORY-404-002" in str(ei.value.__cause__)

    def test_it_does_not_clear_the_cached_guid(self, registry):
        """Detect, don't auto-resolve. The GUID is kept so a human can see what
        RE had, and so republish can report what it is replacing."""
        registry.set_egeria_asset_guid("myproj", "abc-123")
        with pytest.raises(StaleEgeriaLinkageError):
            with guard_linkage(registry, "repo", "myproj", "My Proj", "abc-123"):
                raise Exception(REAL_ERROR)
        assert registry.get_egeria_asset_guid("myproj") == "abc-123"

    def test_unrelated_failures_pass_straight_through(self, registry):
        with pytest.raises(ValueError):
            with guard_linkage(registry, "repo", "myproj", "My Proj", "abc-123"):
                raise ValueError("something else entirely")
        assert registry.get_egeria_linkage("repo", "myproj") is None

    def test_no_cached_guid_means_nothing_to_detect(self, registry):
        """Without a cached GUID there is no stale linkage — any failure is an
        ordinary one and must not be relabelled."""
        with pytest.raises(Exception) as ei:
            with guard_linkage(registry, "repo", "myproj", "My Proj", ""):
                raise Exception(REAL_ERROR)
        assert not isinstance(ei.value, StaleEgeriaLinkageError)
        assert registry.get_egeria_linkage("repo", "myproj") is None

    def test_a_divergence_raises_an_rfa(self, registry):
        with pytest.raises(StaleEgeriaLinkageError):
            with guard_linkage(registry, "repo", "myproj", "My Proj", "abc-123"):
                raise Exception(REAL_ERROR)
        rfas = [e for e in registry.list_activity(operation="rfa")]
        assert any("stale" in (e.get("summary") or "").lower() for e in rfas)


class TestGuardsMatchWhereCachedGuidsAreUsed:
    """A guard belongs exactly where a cached GUID is consumed — no more.

    The first version of this guarded five paths, on the reading that all five
    used a cached GUID. Two do not: they resolve their Egeria element by name
    every time, so a stale cached GUID cannot break them. Proven live rather than
    by reading — publishing through the database path with a GUID Egeria cannot
    have (00000000-dead-beef-...) raised nothing at all, because that path never
    looks at the cached value.

    An inert guard is not merely useless there: it would relabel an unrelated
    lookup failure as a stale-linkage divergence, marking a healthy entity for
    reconciliation on evidence that has nothing to do with the cached GUID.

    Guarded, because they read the cached GUID first:
      repo publish              — no by-name fallback at all, so a stale GUID is fatal
      filesystem publish_step_annotations — `fs.egeria_asset_guid or _find_element_guid(...)`
                                  (the exact shape of the 2026-07-21 report)
      database catalog_and_survey — uses it as the server element GUID

    Unguarded, because they resolve by name:
      filesystem catalog_and_survey
      database publish_step_annotations
    """

    CONSUMES_CACHED_GUID = [
        ("resource_explorer.surveyors.egeria_publisher", "EgeriaPublisher", "publish"),
        ("resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor",
         "EgeriaFileSystemSurveyor", "publish_step_annotations"),
        ("resource_explorer.surveyors.database.egeria_database_surveyor",
         "EgeriaDatabaseSurveyor", "catalog_and_survey"),
    ]
    RESOLVES_BY_NAME = [
        ("resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor",
         "EgeriaFileSystemSurveyor", "catalog_and_survey"),
        ("resource_explorer.surveyors.database.egeria_database_surveyor",
         "EgeriaDatabaseSurveyor", "publish_step_annotations"),
    ]

    @staticmethod
    def _source(module_path, cls_name, method):
        import importlib
        import inspect

        return inspect.getsource(getattr(getattr(importlib.import_module(module_path),
                                                 cls_name), method))

    @pytest.mark.parametrize("mod,cls,method", CONSUMES_CACHED_GUID)
    def test_paths_using_a_cached_guid_are_guarded(self, mod, cls, method):
        assert "guard_linkage" in self._source(mod, cls, method), (
            f"{cls}.{method} reads a cached Egeria GUID but does not wrap the work in "
            "guard_linkage — a divergence there still surfaces as a raw server error.")

    @pytest.mark.parametrize("mod,cls,method", RESOLVES_BY_NAME)
    def test_by_name_paths_are_not_guarded(self, mod, cls, method):
        """The reverse direction, and the one that caught the real mistake: a
        guard here cannot fire for its stated reason, and could misattribute an
        ordinary lookup failure to the cached GUID."""
        assert "guard_linkage" not in self._source(mod, cls, method), (
            f"{cls}.{method} resolves its Egeria element by name, so a stale cached GUID "
            "cannot break it. Guarding it can only produce false divergences.")

    @pytest.mark.parametrize("mod,cls,method", CONSUMES_CACHED_GUID + RESOLVES_BY_NAME)
    def test_the_classification_still_matches_the_code(self, mod, cls, method):
        """Both lists above are claims about what the code does. If a by-name path
        starts reading `egeria_asset_guid` (or a guarded one stops), the lists are
        stale and the guard placement is wrong — fail here rather than let the
        two drift apart silently."""
        src = self._source(mod, cls, method)
        if "guard_linkage" in src:
            # Two guard shapes exist: a thin front door delegating to `_method`
            # (the surveyors, whose bodies were too long to re-indent), and an
            # inline `with` block (the repo publisher). Check the body that
            # actually does the work in either case.
            try:
                src += self._source(mod, cls, "_" + method)
            except AttributeError:
                pass
            assert ("egeria_asset_guid" in src or "get_egeria_asset_guid" in src), (
                f"{cls}.{method} is guarded but no longer reads a cached GUID — the guard "
                "is now inert and should be removed.")
        else:
            assert "egeria_asset_guid" not in src, (
                f"{cls}.{method} now reads a cached GUID but is not guarded.")


def _by_qn(server_guid):
    """Server and database found by their own qualifiedNames (Brief L round 2: the stored
    egeria_asset_guid is never reused as the server; the server's guid comes from its own lookup)."""
    return lambda qn: server_guid if qn.startswith("PostgreSQL Server::") else "db-guid"


class TestNonFatalHandlersStillRecord:
    """A divergence swallowed by a deliberately non-fatal handler must still be
    recorded.

    Found by running the real thing against live Egeria rather than by reading:
    catalog_and_survey with a GUID Egeria cannot have produced exactly the error
    the detector recognises — and then returned success, with server_survey_guid
    empty and a WARNING in the log nobody reads. The wrapping guard never saw it,
    because the exception never escaped.

    The catalog work genuinely does succeed there, so this stays non-fatal. But
    "non-fatal" must not mean "invisible", or every later run silently skips the
    server survey the same way.
    """

    def test_a_swallowed_divergence_is_recorded_and_does_not_raise(self, registry):
        from unittest.mock import patch

        from resource_explorer.registry import DatabaseEntity
        from resource_explorer.surveyors.database.egeria_database_surveyor import (
            EgeriaDatabaseSurveyor)

        db = DatabaseEntity(slug="mydb", display_name="My DB", host="h", port=5432,
                            database_name="mydb", db_type="postgres",
                            egeria_asset_guid="00000000-dead-beef-0000-000000000000")

        # Only the server survey fails, which is what happened live: the server
        # GUID is the cached one, while the database element is resolved by name
        # and is perfectly valid.
        def _survey(kind, guid):
            if guid == "00000000-dead-beef-0000-000000000000":
                raise Exception(LIVE_ERROR)
            return "db-survey-guid"

        s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
        with patch.object(EgeriaDatabaseSurveyor, "connect"), \
             patch.object(EgeriaDatabaseSurveyor, "_find_by_qualified_name",
                          side_effect=_by_qn("00000000-dead-beef-0000-000000000000")), \
             patch.object(EgeriaDatabaseSurveyor, "_initiate_survey", side_effect=_survey):
            out = s._catalog_and_survey(db, "u", "p", registry=registry,
                                        survey_after_catalog=True)

        # Non-fatal: the method still returns, and the work that did succeed is
        # reported rather than discarded.
        assert out["server_survey_guid"] == ""
        assert out["survey_action_guid"] == "db-survey-guid"
        # But the divergence is no longer invisible.
        row = registry.get_egeria_linkage("database", "mydb")
        assert row and row["stale_guid"] == "00000000-dead-beef-0000-000000000000"

    def test_an_ordinary_survey_failure_is_not_recorded_as_a_divergence(self, registry):
        """Survey initiation fails for plenty of reasons that say nothing about
        the GUID; only the unknown-GUID shape should mark a link stale."""
        from unittest.mock import patch

        from resource_explorer.registry import DatabaseEntity
        from resource_explorer.surveyors.database.egeria_database_surveyor import (
            EgeriaDatabaseSurveyor)

        db = DatabaseEntity(slug="mydb", display_name="My DB", host="h", port=5432,
                            database_name="mydb", db_type="postgres", egeria_asset_guid="real-guid")
        s = EgeriaDatabaseSurveyor.__new__(EgeriaDatabaseSurveyor)
        with patch.object(EgeriaDatabaseSurveyor, "connect"), \
             patch.object(EgeriaDatabaseSurveyor, "_find_by_qualified_name", side_effect=_by_qn("real-guid")), \
             patch.object(EgeriaDatabaseSurveyor, "_initiate_survey",
                          side_effect=Exception("Governance engine is not running")):
            s._catalog_and_survey(db, "u", "p", registry=registry, survey_after_catalog=True)

        assert registry.get_egeria_linkage("database", "mydb") is None


class TestResolveRoutes:
    """The "let a human choose" half. All three actions clear the unusable GUID
    — that is what unblocks the resource — and differ in what happens next."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        db = str(tmp_path / "t.db")

        def _reg(*a, **kw):
            return ProjectRegistry(db_path=db)

        monkeypatch.setattr("resource_explorer.web.routes.egeria.ProjectRegistry", _reg)
        reg = _reg()
        reg.add(Project(slug="myproj", display_name="My Proj",
                        github_url="https://github.com/o/myproj", description=""))
        reg.set_egeria_asset_guid("myproj", "abc-123")
        reg.mark_egeria_linkage_stale("repo", "myproj", "abc-123", "OMRS-REPOSITORY-404-002")
        return TestClient(app), reg

    def test_stale_linkages_are_listed(self, client):
        c, _ = client
        rows = c.get("/api/egeria/linkage/stale").json()
        assert [r["entity_slug"] for r in rows] == ["myproj"]
        assert rows[0]["stale_guid"] == "abc-123"

    def test_discard_clears_the_link_and_keeps_local_data(self, client):
        c, reg = client
        r = c.post("/api/egeria/linkage/repo/myproj/resolve", json={"action": "discard"})
        assert r.status_code == 200
        assert r.json()["asset_guid_cleared"] is True
        # The link is gone from both places it was recorded...
        assert reg.get_egeria_linkage("repo", "myproj") is None
        assert not reg.get_egeria_asset_guid("myproj")
        # ...and the wording says plainly what survived and what did not, because
        # "discard" is the one action a user could reasonably fear. See
        # TestResolveIsHonestAboutWhatItRemoves for why this sentence changed.
        assert "untouched" in r.json()["next_step"].lower()

    def test_resolving_something_that_is_not_stale_is_a_404(self, client):
        c, _ = client
        assert c.post("/api/egeria/linkage/repo/other/resolve",
                      json={"action": "discard"}).status_code == 404

    def test_unknown_action_is_rejected_rather_than_guessed(self, client):
        c, _ = client
        r = c.post("/api/egeria/linkage/repo/myproj/resolve", json={"action": "fix"})
        assert r.status_code == 422
        assert "republish" in r.json()["detail"]

    def test_unknown_entity_type_is_rejected(self, client):
        c, _ = client
        assert c.post("/api/egeria/linkage/nonsense/myproj/resolve",
                      json={"action": "discard"}).status_code == 422


class TestResolveIsHonestAboutWhatItRemoves:
    """`discard` is the action a user is most likely to fear, so its report has
    to be exact.

    It said "RE's local survey results are untouched" while
    clear_egeria_registration was also deleting project_egeria_surveys — the
    record of past publishes and their report GUIDs. Those GUIDs point into the
    repository that no longer has the asset, so nothing of value is preserved by
    keeping them, and the publish history itself survives in the activity log.
    But the sentence was not true of them, and I only noticed by running discard
    against a real record and looking at what disappeared.
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        db = str(tmp_path / "t.db")
        monkeypatch.setattr("resource_explorer.web.routes.egeria.ProjectRegistry",
                            lambda *a, **kw: ProjectRegistry(db_path=db))
        reg = ProjectRegistry(db_path=db)
        reg.add(Project(slug="myproj", display_name="My Proj",
                        github_url="https://github.com/o/myproj", description=""))
        reg.set_egeria_asset_guid("myproj", "abc-123")
        reg.record_egeria_survey("myproj", surveyed_at="2026-08-01T00:00:00",
                                 report_guid="rep-1", annotation_count=3)
        reg.mark_egeria_linkage_stale("repo", "myproj", "abc-123", "gone")
        return TestClient(app), reg

    def test_it_reports_the_publish_records_it_deletes(self, client):
        c, _ = client
        body = c.post("/api/egeria/linkage/repo/myproj/resolve",
                      json={"action": "discard"}).json()

        assert body["surveys_deleted"] == 1
        assert "1 publish record(s) removed" in body["next_step"]

    def test_it_does_not_claim_they_were_untouched(self, client):
        c, _ = client
        step = c.post("/api/egeria/linkage/repo/myproj/resolve",
                      json={"action": "discard"}).json()["next_step"]

        assert "survey results and annotations are untouched" in step
        assert "activity log" in step

    def test_survey_annotations_really_do_survive(self, client):
        """The claim the message makes, checked rather than asserted in prose."""
        c, reg = client
        reg.upsert_metric("myproj", "maturity", {"score": 4},
                          surveyed_at="2026-08-01T00:00:00")

        c.post("/api/egeria/linkage/repo/myproj/resolve", json={"action": "discard"})

        assert reg.query_metrics("myproj", "maturity").get("score") == 4
