"""TR-89 (Parts A + B, one deploy unit): a git repository is a SourceControlLibrary under a GitHub
SoftwareServerPlatform (CloudPlatform), and every reader says so.

The fake (`fake_egeria_catalogue.FakeRepoEgeria`) knows Egeria's rule: a SourceControlLibrary is a
SoftwareCapability, NOT an Asset, so `find_assets` / `get_asset_by_guid` / `update_asset` / `delete_asset` and an
Asset-end relationship refuse or answer NotFound for it. A repository path still on an Asset API fails here.

Type facts are from the type archives at Egeria 7d0db895 (audit §2); the live proof on a throwaway Egeria
re-verifies them. The platform's `deployedImplementationType` is "GitHub" (project owner, 2026-11-20).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from pyegeria.core._exceptions import PyegeriaNotFoundException as FakeNotFound  # noqa: E402
from fake_egeria_catalogue import FakeRepoEgeria  # noqa: E402

from resource_explorer.registry import Project, ProjectRegistry  # noqa: E402
from resource_explorer.surveyors import egeria_publisher as ep  # noqa: E402
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher, EgeriaPublisherError  # noqa: E402

URL = "https://github.com/docling-project/docling"
QN = f"GitHubRepository::{URL}"
PKG = Path(__file__).resolve().parents[1] / "resource_explorer"


class _Result:
    resource_slug = "docling"
    project_display_name = "docling"
    github_url = URL


class _Settings:
    """Just enough of ProjectRegistry for the publisher's cache reads."""

    def __init__(self):
        self.settings: dict[str, str] = {}
        self.guids: dict[str, str] = {}

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_egeria_asset_guid(self, slug):
        return self.guids.get(slug)

    def set_egeria_asset_guid(self, slug, guid):
        self.guids[slug] = guid

    def clear_egeria_registration(self, slug):
        self.guids.pop(slug, None)

    def get_latest_project_stats(self, slug):
        return None


def _publisher(egeria: FakeRepoEgeria, registry=None) -> EgeriaPublisher:
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._connect = lambda: None                          # the clients below ARE the connection
    pub._asset_maker = egeria
    pub._metadata_expert = egeria
    pub._registry = registry
    pub.zone_names = None
    pub._zone_names = None
    return pub


# ── Part A: the repository is created as a SourceControlLibrary ──────────────────────────────────────────────


def test_a_new_repository_is_a_source_control_library_with_its_own_properties():
    egeria = FakeRepoEgeria()
    guid = _publisher(egeria)._find_or_create_asset(_Result())
    el = egeria.elements[guid]
    assert el["type"] == "SourceControlLibrary"
    p = el["props"]
    # READ BACK from Egeria's side: libraryType survives only because the class was SourceControlLibraryProperties.
    assert p["libraryType"] == "git"
    assert p["deployedImplementationType"] == "GitHub Repository"
    assert p["qualifiedName"] == QN and p["url"] == URL
    extra = p["additionalProperties"]
    assert extra["github_url"] == URL and extra["project_slug"] == "docling"
    assert {"license", "topics", "primary_language"} <= set(extra)
    assert "deployed_implementation_type" not in extra          # a real property now, not a private extension key
    # Asset APIs are not how a repository is made.
    assert egeria.ops("create_asset") == [] or all(c[1][0] != "Asset" for c in egeria.ops("create_asset"))
    assert not any(c[0] == "create_software_capability" and c[1][0] == "Asset" for c in egeria.calls)


def test_the_create_body_names_the_specific_class_or_egeria_drops_library_type():
    egeria = FakeRepoEgeria()
    _publisher(egeria)._find_or_create_asset(_Result())
    body = [c for c in egeria.ops("create_software_capability") if c[1][0] == "SourceControlLibrary"][0][1][1]
    assert body["properties"]["class"] == "SourceControlLibraryProperties"


def test_zones_ride_as_a_classification_never_inside_properties(monkeypatch):
    monkeypatch.setattr(EgeriaPublisher, "_referenced_zones", staticmethod(lambda: ["quarantine", "default"]))
    egeria = FakeRepoEgeria()
    guid = _publisher(egeria)._find_or_create_asset(_Result())
    assert "zoneMembership" not in egeria.elements[guid]["props"]
    assert egeria.elements[guid]["classifications"]["ZoneMembership"]["zoneMembership"] == ["quarantine", "default"]
    platform = egeria.by_qn("SoftwareServerPlatform::github.com")
    assert "zoneMembership" not in egeria.elements[platform]["props"]
    assert egeria.elements[platform]["classifications"]["ZoneMembership"]["zoneMembership"] == ["quarantine", "default"]


def test_the_repository_is_not_owned_by_a_capability_asset_use_edge():
    egeria = FakeRepoEgeria()
    _publisher(egeria)._find_or_create_asset(_Result())
    assert egeria.ops("add_capability_asset_use") == []          # the old OWNS edge is gone
    assert not [r for r in egeria.relationships if r["type"] == "CapabilityAssetUse"]


# ── the GitHub platform ────────────────────────────────────────────────────────────────────────────────────────


def test_github_is_a_cloud_platform_linked_to_the_repository_by_supported_software_capability():
    egeria = FakeRepoEgeria()
    reg = _Settings()
    repo = _publisher(egeria, reg)._find_or_create_asset(_Result())
    platform = egeria.by_qn("SoftwareServerPlatform::github.com")
    assert egeria.elements[platform]["type"] == "SoftwareServerPlatform"
    cloud = egeria.elements[platform]["classifications"]["CloudPlatform"]
    assert cloud["class"] == "CloudPlatformProperties"
    assert cloud["deployedImplementationType"] == ep.GITHUB_PLATFORM_DEPLOYED_IMPLEMENTATION_TYPE
    assert egeria.elements[platform]["props"]["deployedImplementationType"] == \
        ep.GITHUB_PLATFORM_DEPLOYED_IMPLEMENTATION_TYPE
    links = [r for r in egeria.relationships if r["type"] == "SupportedSoftwareCapability"]
    assert [(r["end1"], r["end2"]) for r in links] == [(platform, repo)]
    assert reg.settings["egeria.github_platform_guid"] == platform


def test_the_platform_is_a_singleton_and_each_repository_is_linked_once():
    egeria = FakeRepoEgeria()
    reg = _Settings()
    pub = _publisher(egeria, reg)

    class Other(_Result):
        resource_slug, github_url = "other", "https://github.com/o/other"

    a, b = pub._find_or_create_asset(_Result()), pub._find_or_create_asset(Other())
    pub._link_platform_to_repository(a)                 # a re-run of the link: idempotent
    pub._link_platform_to_repository(a)
    platforms = [g for g, e in egeria.elements.items() if e["type"] == "SoftwareServerPlatform"]
    assert len(platforms) == 1
    links = [(r["end2"]) for r in egeria.relationships if r["type"] == "SupportedSoftwareCapability"]
    assert sorted(links) == sorted([a, b])


def test_the_old_models_cached_guid_is_never_reused():
    egeria = FakeRepoEgeria()
    reg = _Settings()
    old = egeria.seed("SourceControlLibrary", "SourceControlLibrary::github.com")
    reg.settings["egeria.github_source_control_library_guid"] = old
    platform = _publisher(egeria, reg)._find_or_create_github_platform()
    assert platform != old and egeria.elements[platform]["type"] == "SoftwareServerPlatform"


def test_a_cached_platform_guid_is_verified_type_agnostically_and_a_stale_one_is_recreated():
    egeria = FakeRepoEgeria()
    reg = _Settings()
    pub = _publisher(egeria, reg)
    first = pub._find_or_create_github_platform()
    assert pub._find_or_create_github_platform() == first
    assert egeria.ops("find_software_capabilities") == []        # a platform is not found by that call
    del egeria.elements[first]                                   # an Egeria reset
    second = pub._find_or_create_github_platform()
    assert second != first and reg.settings["egeria.github_platform_guid"] == second


def test_a_platform_lookup_that_cannot_be_made_creates_nothing():
    egeria = FakeRepoEgeria()
    egeria.fail_reads = True
    with pytest.raises(EgeriaPublisherError, match="could not be made"):
        _publisher(egeria, _Settings())._find_or_create_github_platform()
    assert egeria.ops("create_asset") == []


def test_the_platform_deployed_implementation_type_is_github_from_one_named_constant():
    assert ep.GITHUB_PLATFORM_DEPLOYED_IMPLEMENTATION_TYPE == "GitHub"
    assert ep.REPO_DEPLOYED_IMPLEMENTATION_TYPE == "GitHub Repository"      # the repository keeps its own
    egeria = FakeRepoEgeria()
    platform = _publisher(egeria)._find_or_create_github_platform()
    assert egeria.elements[platform]["props"]["deployedImplementationType"] == "GitHub"
    assert egeria.elements[platform]["classifications"]["CloudPlatform"]["deployedImplementationType"] == "GitHub"
    # the literal is never scattered: no `deployedImplementationType: "GitHub"` anywhere in the package
    for path in PKG.rglob("*.py"):
        text = path.read_text()
        assert not re.search(r"deployedImplementationType['\"]\s*:\s*['\"]GitHub['\"]", text), path


# ── lookups: exact, type-agnostic, never "absent" on a failed read ─────────────────────────────────────────────


def test_a_sibling_repository_whose_name_this_one_prefixes_is_not_adopted():
    egeria = FakeRepoEgeria()
    sibling = egeria.seed("SourceControlLibrary", QN + "-eval")
    guid = _publisher(egeria)._find_or_create_asset(_Result())
    assert guid != sibling and egeria.elements[guid]["props"]["qualifiedName"] == QN


def test_the_exact_repository_is_adopted_not_duplicated():
    egeria = FakeRepoEgeria()
    mine = egeria.seed("SourceControlLibrary", QN)
    guid = _publisher(egeria)._find_or_create_asset(_Result())
    assert guid == mine
    assert not [c for c in egeria.ops("create_software_capability") if c[1][0] == "SourceControlLibrary"]
    assert egeria.ops("find_assets") == []


def test_another_type_holding_the_name_is_never_adopted_or_duplicated():
    egeria = FakeRepoEgeria()
    egeria.seed("Asset", QN)                  # a pre-TR-89 repository that was not reset
    with pytest.raises(EgeriaPublisherError, match="could not be made"):
        _publisher(egeria)._find_or_create_asset(_Result())
    assert egeria.ops("create_software_capability") == []


def test_a_lookup_that_cannot_be_made_creates_nothing_and_a_cached_guid_is_trusted():
    egeria = FakeRepoEgeria()
    egeria.fail_reads = True
    with pytest.raises(EgeriaPublisherError, match="could not be made"):
        _publisher(egeria)._find_or_create_asset(_Result())
    assert egeria.ops("create_software_capability") == []
    reg = _Settings()
    reg.guids["docling"] = "cached-guid"
    assert _publisher(egeria, reg)._find_or_create_asset(_Result()) == "cached-guid"


def test_a_cached_guid_that_resolves_is_reused_and_a_stale_one_is_cleared_and_recreated(tmp_path):
    egeria = FakeRepoEgeria()
    reg = ProjectRegistry(db_path=str(tmp_path / "r.db"))
    reg.add(Project(slug="docling", display_name="docling", github_url=URL, description=""))
    pub = _publisher(egeria, reg)
    first = pub._find_or_create_asset(_Result())
    assert reg.get_egeria_asset_guid("docling") == first
    assert pub._find_or_create_asset(_Result()) == first
    assert len([g for g, e in egeria.elements.items() if e["type"] == "SourceControlLibrary"]) == 1
    del egeria.elements[first]                        # the Egeria database was reset
    second = pub._find_or_create_asset(_Result())
    assert second != first and reg.get_egeria_asset_guid("docling") == second


# ── _language_forward: the by-GUID read and the update, on the capability calls ─────────────────────────────


FIXTURE = Path(__file__).parent / "fixtures" / "egeria_6_2" / "metadata_expert_by_guid__survey_report_ownership_anchor_guid.json"


def test_the_recorded_by_guid_shape_is_read_as_the_stored_map():
    """The REAL answer of get_metadata_element_by_guid (recorded 6.2): the map is under
    elementProperties.propertyValueMap, not under `properties`."""
    answer = json.loads(FIXTURE.read_text())["answer"]
    qn = answer["elementProperties"]["propertiesAsStrings"]["qualifiedName"]
    stored = EgeriaPublisher._current_additional_properties(answer, qn)
    assert stored == {"requestedBy": "peterprofile", "annotation_count": "283",
                      "engineActionGUID": "5b6138de-3d83-49f9-8426-46949bfa6aab"}
    assert EgeriaPublisher._current_additional_properties(answer, "SomethingElse::x") == stored  # map found: as stored


def test_this_element_read_with_no_additional_properties_is_an_empty_map_and_a_stranger_is_unreadable():
    egeria = FakeRepoEgeria()
    guid = egeria.seed("SourceControlLibrary", QN)
    answer = egeria.get_metadata_element_by_guid(guid)
    assert EgeriaPublisher._current_additional_properties(answer, QN) == {}
    assert EgeriaPublisher._current_additional_properties(answer, "Other::x") is None


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "lang.db"))
    r.add(Project(slug="docling", display_name="docling", github_url=URL, description=""))
    with r._conn() as conn:
        conn.execute("INSERT INTO project_stats (project_slug, fetched_at, primary_language) VALUES (?, ?, ?)",
                     ("docling", "2026-10-19T07:00:00", "Python"))
    return r


def test_language_forward_updates_through_the_capability_call_and_keeps_every_other_key(registry):
    egeria = FakeRepoEgeria()
    guid = egeria.seed("SourceControlLibrary", QN, libraryType="git",
                       additionalProperties={"github_url": URL, "license": "MIT", "topics": "ai",
                                             "primary_language": "HTML"})
    registry.set_egeria_asset_guid("docling", guid)
    pub = _publisher(egeria, registry)
    assert pub._find_or_create_asset(_Result()) == guid
    ups = egeria.ops("update_software_capability")
    assert len(ups) == 1
    body = ups[0][1][1]
    assert body["properties"]["class"] == "SourceControlLibraryProperties" and body["mergeUpdate"] is True
    after = egeria.elements[guid]["props"]
    assert after["additionalProperties"]["primary_language"] == "Python"
    # a key the update does not touch survives it (the whole map is read-merge-written)
    assert after["additionalProperties"]["license"] == "MIT" and after["additionalProperties"]["github_url"] == URL
    assert after["libraryType"] == "git"
    assert egeria.ops("get_asset_by_guid") == [] and egeria.ops("update_asset") == []


# ── the latent native survey refuses in words ──────────────────────────────────────────────────────────────────


def test_a_repository_survey_is_refused_in_words_and_nothing_is_sent():
    egeria = FakeRepoEgeria()
    pub = _publisher(egeria)
    pub._automated_curation = object()                  # any call on it would raise AttributeError
    for call in (lambda: pub.trigger_survey_by_guid("repo-guid"),
                 lambda: pub._initiate_survey("GitHub Repository", "repo-guid")):
        with pytest.raises(EgeriaPublisherError, match="survey engine takes only an Asset as its target; "
                                                      "a repository survey will target its branch folder"):
            call()
    assert egeria.calls == []


# ── R4: a repository's reports read through ReportSubject, not the Asset-only read ─────────────────────────────


def _with_report(egeria, repo_guid):
    report = egeria.seed("SurveyReport", "SurveyReport::GitHubRepo::docling::t1", displayName="Survey: docling",
                         additionalProperties={"annotation_count": "12"})
    egeria.add_relationship("ReportSubject", repo_guid, report)
    return report


def test_a_repositorys_reports_are_read_through_report_subject():
    egeria = FakeRepoEgeria()
    repo = egeria.seed("SourceControlLibrary", QN)
    report = _with_report(egeria, repo)
    rows = _publisher(egeria).get_survey_reports_by_guid(repo)
    assert [r["guid"] for r in rows] == [report]
    assert rows[0]["annotation_count"] == 12 and rows[0]["annotation_count_stated"] is True
    assert rows[0]["display_name"] == "Survey: docling" and rows[0]["surveyed_at"]
    assert egeria.ops("get_asset_by_guid") == []


def test_a_repository_with_no_reports_is_empty_and_one_that_cannot_be_read_is_not():
    from resource_explorer.surveyors.egeria_survey_reader import RepoReportsUnreadable

    egeria = FakeRepoEgeria()
    repo = egeria.seed("SourceControlLibrary", QN)
    pub = _publisher(egeria)
    assert pub.get_survey_reports_by_guid(repo) == []            # measured: there are none
    egeria.fail_reads = True
    with pytest.raises(RepoReportsUnreadable, match="could not read"):
        pub.get_survey_reports_by_guid(repo)                     # not "no reports"


def test_databases_and_file_systems_keep_the_asset_read():
    from resource_explorer.surveyors.egeria_survey_reader import get_survey_reports_by_guid

    egeria = FakeRepoEgeria()
    db = egeria.seed("Asset", "Database::x")
    assert get_survey_reports_by_guid(egeria, db) == []
    assert egeria.ops("get_asset_by_guid")                        # an Asset: the Asset read is still right


def test_the_repo_reports_route_says_could_not_read_not_an_empty_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.surveyors.egeria_survey_reader import RepoReportsUnreadable

    reg = ProjectRegistry(db_path=str(tmp_path / "route.db"))
    reg.add(Project(slug="myproj", display_name="My Project", github_url="https://github.com/test/myproj"))
    reg.set_egeria_asset_guid("myproj", "repo-guid")
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", reg.__dict__) or None)

    def boom(self, guid):
        raise RepoReportsUnreadable("could not read the survey reports of repo-guid")

    monkeypatch.setattr(EgeriaPublisher, "get_survey_reports_by_guid", boom)
    from resource_explorer.web.app import app

    r = TestClient(app).get("/api/egeria/myproj/egeria-surveys")
    assert r.status_code == 502 and "could not read" in r.json()["detail"]


# ── Part B: the readers ───────────────────────────────────────────────────────────────────────────────────────


def _resync(registry, egeria):
    from resource_explorer.egeria_resync import EgeriaResync

    r = EgeriaResync(registry=registry)
    r._clients = {"classification": egeria, "asset": egeria}
    return r


def test_resync_does_not_call_a_live_repository_stale_and_calls_a_reset_one_stale(registry):
    from resource_explorer.egeria_resync import ScanResult

    egeria = FakeRepoEgeria()
    guid = _publisher(egeria, registry)._find_or_create_asset(_Result())
    res = ScanResult()
    finding = _resync(registry, egeria)._scan_assets(res)
    assert finding.items == [] and res.undetermined == []
    del egeria.elements[guid]
    finding = _resync(registry, egeria)._scan_assets(ScanResult())
    assert finding.items == [{"slug": "docling", "guid": guid}]


def test_resync_that_cannot_read_egeria_reports_undetermined_never_stale(registry):
    from resource_explorer.egeria_resync import ScanResult

    egeria = FakeRepoEgeria()
    _publisher(egeria, registry)._find_or_create_asset(_Result())
    egeria.fail_reads = True
    res = ScanResult()
    finding = _resync(registry, egeria)._scan_assets(res)
    assert finding.items == []
    assert [u["ref"] for u in res.undetermined] == ["docling"]
    assert egeria.ops("find_assets") == []


def test_a_republish_then_a_resync_pass_loses_nothing(registry):
    """A and B as ONE unit: the publisher writes a SourceControlLibrary, and the scan that decides what
    `clear_stale_assets` deletes can see one. With A alone (the scan on `find_assets`) this call is stale."""
    from resource_explorer.egeria_resync import ScanResult

    egeria = FakeRepoEgeria()
    guid = _publisher(egeria, registry)._find_or_create_asset(_Result())
    assert egeria.find_assets(search_string=QN) == "No elements found"        # the old scan's blind spot
    assert _resync(registry, egeria)._scan_assets(ScanResult()).items == []
    assert registry.get_egeria_asset_guid("docling") == guid


def test_the_sweep_script_decides_by_guid_and_never_reads_an_error_as_stale():
    sys.path.insert(0, str(PKG.parent / "scripts"))
    import importlib

    sweep = importlib.import_module("sweep_stale_egeria_guids")
    egeria = FakeRepoEgeria()
    live = egeria.seed("SourceControlLibrary", QN)
    stale, ok, unknown = sweep._check_cached(egeria, [("docling", live), ("gone", "no-such-guid")])
    assert ok == [("docling", live)] and stale == [("gone", "no-such-guid")] and unknown == []
    egeria.fail_reads = True
    stale, ok, unknown = sweep._check_cached(egeria, [("docling", live)])
    assert stale == [] and ok == [] and [u[0] for u in unknown] == ["docling"]
    assert egeria.ops("find_assets") == []


@pytest.mark.usefixtures("signed_in_caller")
def test_bulk_delete_of_a_repository_uses_the_element_delete_not_the_asset_one(tmp_path, monkeypatch):
    from resource_explorer import bulk_ops
    from resource_explorer.bulk_ops import delete_in_egeria

    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "test-clearance")
    monkeypatch.setattr(bulk_ops, "_refused", lambda *a, **k: "")
    egeria = FakeRepoEgeria()
    reg = ProjectRegistry(db_path=str(tmp_path / "b.db"))
    reg.add(Project(slug="docling", display_name="docling", github_url=URL, description=""))
    guid = egeria.seed("SourceControlLibrary", QN)
    reg.set_egeria_asset_guid("docling", guid)

    class _Clients:
        def of(self, cls):
            return egeria

    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client", lambda *a, **k: _Clients())
    r = delete_in_egeria(reg, [{"entity_type": "repo", "slug": "docling"}], dry_run=False)
    assert r.succeeded == 1
    assert egeria.ops("delete_asset") == [] and [c[1][0] for c in egeria.ops("delete_metadata_element")] == [guid]
    assert guid not in egeria.elements and not reg.get_egeria_asset_guid("docling")


def test_the_dead_asset_lookup_is_gone():
    from resource_explorer.surveyors.egeria_reader import EgeriaReader

    assert not hasattr(EgeriaReader, "find_asset_guid")


def test_the_fake_knows_a_capability_is_not_an_asset():
    egeria = FakeRepoEgeria()
    scl = egeria.seed("SourceControlLibrary", QN)
    platform = egeria.seed("SoftwareServerPlatform", "SoftwareServerPlatform::x")
    assert egeria.find_assets(search_string="GitHubRepository::") == "No elements found"
    for call in (lambda: egeria.get_asset_by_guid(scl), lambda: egeria.update_asset(scl),
                 lambda: egeria.delete_asset(scl), lambda: egeria.add_capability_asset_use(scl, scl),
                 lambda: egeria.link_software_capability_to_asset(scl, platform)):
        with pytest.raises(FakeNotFound):
            call()


# ── _current_display_name reads a NAME, in the SCL (by-GUID) shape and the old Asset shape ──────────────────────


def test_the_display_name_is_read_from_the_by_guid_shape_of_a_source_control_library():
    egeria = FakeRepoEgeria()
    guid = egeria.seed("SourceControlLibrary", QN, displayName="docling")
    assert EgeriaPublisher._current_display_name(egeria.get_metadata_element_by_guid(guid)) == "docling"


def test_the_display_name_is_read_from_the_recorded_by_guid_fixture():
    answer = json.loads(FIXTURE.read_text())["answer"]
    name = answer["elementProperties"]["propertiesAsStrings"]["displayName"]
    assert EgeriaPublisher._current_display_name(answer) == name and isinstance(name, str)


def test_the_display_name_is_read_from_the_asset_shape_and_absent_or_odd_answers_are_none():
    asset = {"elementHeader": {"guid": "g"}, "properties": {"qualifiedName": "x", "displayName": "folder"}}
    assert EgeriaPublisher._current_display_name(asset) == "folder"
    assert EgeriaPublisher._current_display_name({"properties": {"qualifiedName": "x"}}) is None
    assert EgeriaPublisher._current_display_name("No elements found") is None
    egeria = FakeRepoEgeria()
    nameless = egeria.seed("SourceControlLibrary", QN)
    assert EgeriaPublisher._current_display_name(egeria.get_metadata_element_by_guid(nameless)) is None


# ── TR-116: a repository's displayName is owner/repo, from its URL, whoever registered it ─────────────────────


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/odpi/egeria-python.git", "odpi/egeria-python"),
    ("https://github.com/odpi/egeria-python", "odpi/egeria-python"),
    ("https://github.com/open-policy-agent/opa", "open-policy-agent/opa"),
    ("https://github.com/odpi/egeria-python/", "odpi/egeria-python"),
    ("https://github.com/odpi/egeria-python.git/", "odpi/egeria-python"),
    ("git@github.com:odpi/egeria-python.git", "odpi/egeria-python"),
    ("git@github.com:odpi/egeria-python", "odpi/egeria-python"),
    ("ssh://git@github.com/odpi/egeria-python.git", "odpi/egeria-python"),
    ("https://token@github.com/odpi/egeria-python.git", "odpi/egeria-python"),
    ("https://github.com/odpi/egeria-python/tree/main", "odpi/egeria-python"),
])
def test_every_url_form_gives_owner_slash_repo(url, expected):
    from resource_explorer.github.repo_names import repo_display_name
    assert repo_display_name(url) == expected


@pytest.mark.parametrize("url", ["", "https://github.com/odpi", "https://github.com/", "not a url", "git@github.com:odpi"])
def test_a_url_with_no_owner_and_repo_gives_none_not_a_guess(url):
    from resource_explorer.github.repo_names import repo_display_name
    assert repo_display_name(url) is None


@pytest.mark.parametrize("registered_as", ["egeria-python", "odpi/egeria-python", "Egeria Python"])
@pytest.mark.parametrize("url", ["https://github.com/odpi/egeria-python.git", "git@github.com:odpi/egeria-python.git"])
def test_the_published_name_does_not_depend_on_how_the_repository_was_registered(registered_as, url):
    class R(_Result):
        project_display_name = registered_as
        github_url = url
    egeria = FakeRepoEgeria()
    guid = _publisher(egeria)._find_or_create_asset(R())
    assert egeria.elements[guid]["props"]["displayName"] == "odpi/egeria-python"


def test_a_url_with_no_owner_falls_back_to_the_registered_name():
    class R(_Result):
        github_url = "https://example.test/"
        project_display_name = "kept"
    egeria = FakeRepoEgeria()
    guid = _publisher(egeria)._find_or_create_asset(R())
    assert egeria.elements[guid]["props"]["displayName"] == "kept"


def test_every_repository_publish_path_names_through_the_one_helper():
    """The only place that builds a SourceControlLibrary body is the publisher's create, and it names it by the
    helper; no other module creates one, and the registered display name is no longer the SCL's name."""
    src = (PKG / "surveyors" / "egeria_publisher.py").read_text()
    assert src.count('"class": "SourceControlLibraryProperties"') == 2  # create body + the language update, which sets no name
    assert '"displayName": repo_display_name(result.github_url) or result.project_display_name' in src
    assert not re.search(r'"displayName":\s*result\.project_display_name\s*,\s*\n\s*"description": f"GitHub repository', src)
    makers = [p.name for p in PKG.rglob("*.py")
              if "SourceControlLibraryProperties" in p.read_text() and p.name not in ("egeria_publisher.py",)]
    # Anything else that names the class may only read or describe it, never build a body with a displayName.
    for name in makers:
        text = next(PKG.rglob(name)).read_text()
        assert not re.search(r'"class":\s*"SourceControlLibraryProperties"', text), name
