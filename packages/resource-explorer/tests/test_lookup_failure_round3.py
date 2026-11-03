"""Round 3 of re/lookup-failure-never-creates (re-review of 4917afe4).

Same rules: found / absent / could-not-check, and only absent creates, clears a pointer or proves anything gone.
Fakes bind real pyegeria signatures; pyegeria exceptions are built the way pyegeria builds them.
"""
from __future__ import annotations


import httpx
import pytest
from pyegeria.core._exceptions import PyegeriaConnectionException, PyegeriaNotFoundException

from tests.test_lookup_failure_never_creates import _FS, GUID, MISS, _Clients, _fake, _fs_surveyor
from tests.test_lookup_failure_round2 import _FS_TWO, _egeria_error

pytestmark = pytest.mark.usefixtures("signed_in_caller")

ODD = "Server busy, try later"          # neither a GUID nor pyegeria's miss sentence


@pytest.fixture()
def _zone_access_open(monkeypatch):
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="lookup-tester")


# ── 1. a file-system publish with skipped files is partial, never "success" ──


@pytest.fixture
def fs_reg(tmp_path, monkeypatch):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    r = ProjectRegistry(db_path=str(tmp_path / "fs.db"))
    r.register_filesystem(FileSystemEntity(slug="fs1", display_name="FS One", local_mount_point="/mnt/fs1",
                                           canonical_mount_point="/data/fs1"))
    r.add_filesystem_survey(fs_slug="fs1", surveyed_at="2026-10-13T00:00:00", survey_data=dict(_FS_TWO),
                            egeria_report_guid="local-only", source="local")
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", r.__dict__) or None)
    return r


def test_db_step_run_metrics_record_lineage_not_checked(tmp_path, monkeypatch):
    from pyegeria import AssetMaker, AutomatedCuration
    from pyegeria.omvs.data_discovery import DataDiscovery
    from pyegeria.omvs.lineage_linker import LineageLinker

    from resource_explorer.registry import DatabaseEntity, ProjectRegistry
    from resource_explorer.surveyors.database.survey_definition_adapter import _publish

    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    db = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432,
                        database_name="shop")
    reg.register_database(db)
    ac, am, dd, ll = _fake(AutomatedCuration), _fake(AssetMaker), _fake(DataDiscovery), _fake(LineageLinker)

    def lookup(name, property_name=None, type_name=None):
        if ".v." in name or name.startswith("v."):
            raise PyegeriaConnectionException()               # the lineage column look-ups
        if name.startswith("PostgreSQL"):
            return GUID                                       # the server and database elements
        return MISS

    from pyegeria import MetadataExpert
    from tests.qn_lookup_bridge import wire_unique_name

    ac.get_guid_for_name.side_effect = lookup
    mx = wire_unique_name(_fake(MetadataExpert), lookup)       # the exact-qualifiedName reads
    am.create_asset.return_value = "report-guid"
    dd.create_annotation.return_value = GUID
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client", lambda *a, **k: _Clients(
        {AutomatedCuration: ac, AssetMaker: am, DataDiscovery: dd, LineageLinker: ll, MetadataExpert: mx}))
    view = {"name": "v", "schema": "public", "qualified_name": "public.v", "dependencies": ["t"],
            "complexity": {"complexity_score": 1, "portability_rating": 1, "node_count": 1, "join_count": 0,
                           "cte_count": 0, "subquery_count": 0},
            "lineage": {"c": {"downstream": [{"name": "x", "source": "t"}]}}}
    schema_info = {"schemas": [{"name": "public", "tables": []}], "total_tables": 0, "total_columns": 0}
    _publish(db, [{"schema_info": schema_info, "views": [view]}], "2026-10-13T00:00:00", reg)
    ll.link_lineage.assert_not_called()
    runs = reg.query_step_runs("db", step_key="egeria_publish", entity_type="database")
    assert runs and runs[0]["metrics"]["lineage_not_checked"] == ["shop.public.v.c"]


# ── 2. native survey: a view server that is down does not make a pointer "gone" ──


def test_check_pointers_server_unavailable_is_unreadable_not_gone(tmp_path, monkeypatch):
    from pyegeria import MetadataExpert

    from resource_explorer.catalog_and_survey import PyegeriaRegistrationPort, check_pointers
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "ptr.db"))
    reg.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432,
                                         database_name="shop", egeria_asset_guid=GUID))
    expert = _fake(MetadataExpert)
    down = _egeria_error(PyegeriaNotFoundException, 404, "OMAG-MULTI-TENANT-404-001",
                         "OMAG-MULTI-TENANT-404-001 The OMAG Server qs-view-serverX is not available")
    expert.get_metadata_element_by_guid.side_effect = lambda *a, **k: (_ for _ in ()).throw(down)
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda *a, **k: _Clients({MetadataExpert: expert}))
    port = PyegeriaRegistrationPort()
    out = check_pointers(reg, port, "database", "db")
    assert out["database"] == "unreadable" and out["database"] != "gone"
    with pytest.raises(PyegeriaNotFoundException):          # the registration's pending-server read: never None
        port.read_element(GUID)
    assert reg.get_database("db").egeria_asset_guid == GUID   # no pointer cleared


def test_check_pointers_genuine_404_is_gone(tmp_path, monkeypatch):
    from pyegeria import MetadataExpert

    from resource_explorer.catalog_and_survey import PyegeriaRegistrationPort, check_pointers
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "ptr.db"))
    reg.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432,
                                         database_name="shop", egeria_asset_guid=GUID))
    expert = _fake(MetadataExpert)
    gone = _egeria_error(PyegeriaNotFoundException, 404, "OMRS-METADATA-COLLECTION-404-001")
    expert.get_metadata_element_by_guid.side_effect = lambda *a, **k: (_ for _ in ()).throw(gone)
    # A gone pointer is followed by one by-name lookup (caution row, owner ruling 2026-10-18); a genuinely deleted
    # element has no name either, so Egeria answers that lookup with the same typed not-found.
    expert.get_metadata_element_by_unique_name.side_effect = lambda *a, **k: (_ for _ in ()).throw(gone)
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda *a, **k: _Clients({MetadataExpert: expert}))
    assert check_pointers(reg, PyegeriaRegistrationPort(), "database", "db")["database"] == "gone"


# ── 3. a fresh report short of its annotations is failed, with a failure row ──


def test_fresh_report_short_of_annotations_is_failed():
    from resource_explorer.catalogue_commit import report_step_words

    state, words = report_step_words({"report_guid": "abcdef12-0", "report_element_guid": "abcdef12-0",
                                      "annotation_count": 76, "annotations_in_egeria": 0}, "")
    assert state == "failed" and "0 of 76 annotations published" in words


# ── 4. odd non-GUID answers are could-not-check, never "" (create) ──────


def test_odd_answer_draft_zone_creates_nothing(monkeypatch):
    from resource_explorer.egeria_identity import ensure_draft_zone_exists
    from tests.test_lookup_failure_round2 import _zone

    expert = _zone(monkeypatch, lambda *a, **k: {"elementHeader": {}})
    out = ensure_draft_zone_exists()
    expert.create_metadata_element.assert_not_called()
    assert out["status"] == "error" and "could not be made" in out["error"]


def test_odd_answer_filesystem_root_creates_nothing():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyorError,
    )

    s = _fs_surveyor(lambda *a, **k: ODD)
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made"):
        s.catalog_and_survey(_FS, {**dict(_FS_TWO), "inventory": []})
    s._automated_curation.create_elem_from_template.assert_not_called()


def test_odd_answer_publisher_sub_resource_creates_nothing(tmp_path):
    from tests.test_lookup_failure_never_creates import _sub_resource_publisher

    pub, _ = _sub_resource_publisher(tmp_path, lambda *a, **k: ODD)
    out = pub.publish_sub_resources("p", "https://github.com/o/p", "asset-guid", ["docs"])
    pub._automated_curation.create_elem_from_template.assert_not_called()
    assert out == {} and "could not be made" in pub.sub_resources_not_checked["docs"]


def test_odd_answer_database_secrets_store_creates_nothing():
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyorError,
    )
    from tests.test_lookup_failure_never_creates import _db_surveyor, _no_config_guid

    s, maker = _db_surveyor(lambda *a, **k: ["not-a-guid"])
    p = _no_config_guid()
    try:
        with pytest.raises(EgeriaDatabaseSurveyorError, match="could not be made"):
            s._ensure_own_secrets_store_guid()
    finally:
        p.stop()
    maker.create_connector_type.assert_not_called()


def test_odd_answer_reference_proposal_creates_nothing():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )
    from tests.test_lookup_failure_never_creates import _designer, _expert, _match

    designer = _designer()
    result = publish_proposed_data_class(designer, _match(), expert=_expert(lambda *a, **k: ODD))
    designer.create_data_class.assert_not_called()
    assert "could not be made" in result.error


def test_shared_guid_reader_three_outcomes():
    from resource_explorer.egeria_absence import guid_from_answer

    assert guid_from_answer(GUID, "q") == GUID
    assert guid_from_answer([{"guid": GUID}], "q") == GUID
    assert guid_from_answer({"elementHeader": {"guid": GUID}}, "q") == GUID
    assert guid_from_answer(MISS, "q") == "" and guid_from_answer("No element found", "q") == ""
    for odd in (None, "", ODD, {}, [], ["not-a-guid"], 7):
        with pytest.raises(RuntimeError, match="could not be made"):
            guid_from_answer(odd, "q")


# ── 5. a child under a folder that could not be checked ─────────────────


def test_child_of_an_unchecked_folder_is_not_checked_not_no_guid(tmp_path):
    from pyegeria import AssetMaker, AutomatedCuration

    from resource_explorer import repo_publish
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.resource_scope import _not_checked_of
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'sub.db'}")
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    pub = EgeriaPublisher(platform_url="https://localhost:9443", view_server="qs-view-server", registry=reg)
    pub._automated_curation = _fake(AutomatedCuration)
    from pyegeria import MetadataExpert
    from tests.qn_lookup_bridge import wire_unique_name

    def lookup(name, *a, **k):
        if name.endswith("::docs"):
            raise PyegeriaConnectionException()
        return MISS

    pub._metadata_expert = wire_unique_name(_fake(MetadataExpert), lookup)
    pub._asset_maker = _fake(AssetMaker)
    pub._connect = lambda *a, **k: None
    reg.catalog_sub_resource("repo", "p", "docs", "folder")
    reg.catalog_sub_resource("repo", "p", "docs/a.md", "file")
    guids = pub.publish_sub_resources("p", "https://github.com/o/p", "asset-guid", ["docs", "docs/a.md"])
    # The same hand-off publish_chosen makes (its _not_checked_of), so the proof rows say what happened.
    repo_publish.record_sub_resource_proofs(reg, "p", "c1", "dana", {"docs": "folder", "docs/a.md": "file"},
                                            ["docs/a.md"], guids, reader=lambda g: None,
                                            not_checked=_not_checked_of(pub))
    pub._automated_curation.create_elem_from_template.assert_not_called()
    assert pub.sub_resources_not_checked["docs/a.md"] == "not checked: its parent folder could not be checked"
    errors = {p["table_name"]: p["detail"]["error"] for p in reg.list_catalogue_commit_proofs("p")}
    assert errors["docs/a.md"].startswith("could not check, not created")
    assert "returned no GUID" not in errors["docs/a.md"]


# ── 6. only a pyegeria cause (or an outbox look-up) is "refused by Egeria's security" ──


def _github_403():
    req = httpx.Request("GET", "https://api.github.com/repos/o/p")
    return httpx.HTTPStatusError("403 Forbidden", request=req, response=httpx.Response(403, request=req))


def _pyegeria_403():
    from pyegeria.core._exceptions import PyegeriaUnauthorizedException

    return _egeria_error(PyegeriaUnauthorizedException, 403, "OPEN-METADATA-SECURITY-403-001",
                         "OPEN-METADATA-SECURITY-403-001 The user dana is not authorized to delete")


def _wrapped(cause):
    try:
        raise RuntimeError("the call failed") from cause
    except RuntimeError as exc:
        return exc


def test_refusal_reads_a_pyegeria_403_cause():
    from resource_explorer.egeria_clients import refusal

    assert refusal(_wrapped(_pyegeria_403())) is not None


def test_refusal_ignores_a_non_pyegeria_403_cause():
    from resource_explorer.egeria_clients import refusal

    assert refusal(_wrapped(_github_403())) is None


@pytest.fixture
def bulk_reg(tmp_path, monkeypatch):
    from resource_explorer.registry import Project, ProjectRegistry

    monkeypatch.setenv("RE_ISSUE_117_BLOCK_OFF", "test-clearance")
    reg = ProjectRegistry(db_path=str(tmp_path / "bulk.db"))
    reg.add(Project(slug="p0", display_name="P0", github_url="https://github.com/o/p0", description=""))
    reg.set_egeria_asset_guid("p0", "guid-0")
    return reg


def _bulk_delete(monkeypatch, bulk_reg, cause):
    from pyegeria import AssetMaker

    from resource_explorer.bulk_ops import delete_in_egeria

    am = _fake(AssetMaker)
    am.delete_asset.side_effect = lambda *a, **k: (_ for _ in ()).throw(_wrapped(cause))
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda *a, **k: _Clients({AssetMaker: am}))
    return delete_in_egeria(bulk_reg, [{"entity_type": "repo", "slug": "p0"}], dry_run=False)


@pytest.mark.usefixtures("_zone_access_open")
def test_bulk_delete_pyegeria_403_cause_is_a_refusal(monkeypatch, bulk_reg):
    r = _bulk_delete(monkeypatch, bulk_reg, _pyegeria_403())
    assert r.details[0]["message"].startswith("refused by Egeria")


@pytest.mark.usefixtures("_zone_access_open")
def test_bulk_delete_github_403_cause_is_not_called_egerias(monkeypatch, bulk_reg):
    r = _bulk_delete(monkeypatch, bulk_reg, _github_403())
    assert not r.details[0]["message"].startswith("refused by Egeria")


# ── 7. annotation_count is RE's count; annotations_in_egeria is Egeria's ──


def test_fs_catalog_counts_are_named_consistently():
    s = _fs_surveyor(lambda *a, **k: MISS)
    s._discovery.create_annotation.side_effect = RuntimeError("500 refused")
    out = s.catalog_and_survey(_FS, {**dict(_FS_TWO), "inventory": []})
    assert out["annotation_count"] == 1 and out["annotations_in_egeria"] == 0
    assert "annotations_built" not in out


def test_db_step_publish_counts_are_named_consistently(monkeypatch):
    from tests.test_lookup_failure_round2 import _db_step_publish

    out = _db_step_publish(monkeypatch, create_answers=[RuntimeError("500 refused")] * 50)
    assert out["annotation_count"] >= 1 and out["annotations_in_egeria"] == 0
    assert "annotations_built" not in out

