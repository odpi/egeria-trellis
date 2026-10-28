"""A look-up that could not be made is never "not there, so create".

AUDIT-PYEGERIA-6.2.2.md, "Not in scope, but worth knowing": several create paths caught a RAISED by-name
look-up (Egeria unreachable, 401/403, 500, timeout, several hits) and went on to create, with only Egeria's
duplicate-qualifiedName refusal standing between them and a duplicate. A look-up has three outcomes
(`egeria_absence`): found, absent (pyegeria's "No elements found", or a typed not-found), and could-not-check.
Only absent may create.

One end-to-end test per write path. Each fakes ONLY the pyegeria client boundary, with `create_autospec(...,
spec_set=True)` so every call binds the real pyegeria signature. The fake look-up raises a real pyegeria
exception; the test asserts the create was never called and the caller says the look-up could not be made.
Each has a companion: a real miss still creates.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import create_autospec, patch

import pytest
from pyegeria.core._exceptions import PyegeriaConnectionException

pytestmark = pytest.mark.usefixtures("signed_in_caller")

MISS = "No elements found"          # pyegeria.core._globals.NO_ELEMENTS_FOUND: the one real miss
GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _fake(cls):
    """A client double that binds the real pyegeria method signatures."""
    return create_autospec(cls, instance=True, spec_set=True)


def _unreachable(*_a, **_k):
    raise PyegeriaConnectionException()


class _Clients:
    """The `EgeriaClients.of(cls)` boundary, returning one autospec double per pyegeria class."""

    user_id = "test-caller"

    def __init__(self, by_class):
        self.by_class = by_class

    def of(self, cls):
        return self.by_class[cls]


# ── 1. database surveyor: RE's own secrets store graph ──────────────────

def _db_surveyor(lookup):
    from pyegeria import AutomatedCuration, ConnectionMaker

    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    s._automated_curation = _fake(AutomatedCuration)
    s._automated_curation.get_guid_for_name.side_effect = lookup
    maker = _fake(ConnectionMaker)
    maker.create_connector_type.return_value = "ct-guid"
    maker.create_endpoint.return_value = "ep-guid"
    maker.create_connection.return_value = "conn-guid"
    maker.create_asset.return_value = "asset-guid"
    s._clients = lambda: _Clients({ConnectionMaker: maker})
    return s, maker


def _no_config_guid():
    p = patch("resource_explorer.config.get_config")
    cfg = p.start()
    cfg.return_value.egeria.secrets_store_guid = ""
    cfg.return_value.egeria.secrets_store_path_name = "/deployments/secrets/re.omsecrets"
    return p


def test_database_secrets_store_failed_lookup_creates_nothing(caplog):
    from resource_explorer.surveyors.database.egeria_database_surveyor import (
        EgeriaDatabaseSurveyorError,
    )

    s, maker = _db_surveyor(_unreachable)
    p = _no_config_guid()
    try:
        with pytest.raises(EgeriaDatabaseSurveyorError, match="could not be made") as info:
            s._ensure_own_secrets_store_guid()
        # The step that calls it is non-fatal by design: no secret saved, nothing created, words logged.
        assert s._save_database_secret("db1", "u", "p") == ("", "")
    finally:
        p.stop()
    assert "nothing was created" in str(info.value)
    for create in (maker.create_connector_type, maker.create_endpoint, maker.create_connection, maker.create_asset):
        create.assert_not_called()
    s._automated_curation.save_client_side_secret.assert_not_called()
    assert "could not be made" in caplog.text


def test_database_secrets_store_real_miss_still_creates():
    s, maker = _db_surveyor(lambda *a, **k: MISS)
    p = _no_config_guid()
    try:
        assert s._ensure_own_secrets_store_guid() == "asset-guid"
    finally:
        p.stop()
    maker.create_connector_type.assert_called_once()
    maker.create_endpoint.assert_called_once()
    maker.create_connection.assert_called_once()
    maker.create_asset.assert_called_once()


def test_database_report_adoption_says_the_lookup_failed_not_cannot_be_read():
    """The report's 409-then-adopt path: a look-up that raised is not 'no report with that name'."""
    from pyegeria import AssetMaker

    s, _ = _db_surveyor(_unreachable)
    s._asset_maker = _fake(AssetMaker)
    s._asset_maker.create_asset.side_effect = RuntimeError(
        "409 OMAG-COMMON-409-001 SurveyReport::x is not available for use")
    out = s._publish_survey_report({"class": "NewElementRequestBody"}, "SurveyReport::x", [], "db1", "t")
    assert out["report_guid"] == "" and out["reused"] is False
    assert "could not be made" in out["report_error"]
    assert "could be read" not in out["report_error"]


# ── 2. file-system surveyor: the root folder element ────────────────────

def _fs_surveyor(lookup):
    from pyegeria import AssetMaker, AutomatedCuration
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyor,
    )

    s = EgeriaFileSystemSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    s._automated_curation = _fake(AutomatedCuration)       # connect() returns early once this is set
    s._automated_curation.get_guid_for_name.side_effect = lookup
    s._automated_curation.create_folder_element_from_template.return_value = GUID
    s._asset_maker = _fake(AssetMaker)
    s._asset_maker.create_asset.return_value = "report-guid"
    s._discovery = _fake(DataDiscovery)
    s._discovery.create_annotation.return_value = "ann-guid"
    return s


_FS = SimpleNamespace(slug="fs1", display_name="FS One", local_mount_point="/mnt/fs1",
                      canonical_mount_point="/data/fs1", description="", egeria_asset_guid="")
_FS_DATA = {"inventory": [], "total_files": 0, "total_data_files": 0, "total_size": "0 B",
            "total_size_bytes": 0, "surveyed_at": "2026-10-13T00:00:00"}


def test_filesystem_failed_lookup_creates_nothing():
    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import (
        EgeriaFileSystemSurveyorError,
    )

    s = _fs_surveyor(_unreachable)
    with pytest.raises(EgeriaFileSystemSurveyorError, match="could not be made") as info:
        s.catalog_and_survey(_FS, dict(_FS_DATA))
    assert "nothing was created" in str(info.value)
    s._automated_curation.create_folder_element_from_template.assert_not_called()
    s._asset_maker.create_asset.assert_not_called()


def test_filesystem_real_miss_still_creates():
    s = _fs_surveyor(lambda *a, **k: MISS)
    out = s.catalog_and_survey(_FS, dict(_FS_DATA))
    s._automated_curation.create_folder_element_from_template.assert_called_once()
    assert out["filesystem_guid"] == GUID


def test_filesystem_found_element_is_adopted_not_created():
    """`_UUID_RE` was never defined on this surveyor, so a found GUID raised AttributeError and the old
    blanket except read it as absent: every found root folder was re-created."""
    s = _fs_surveyor(lambda *a, **k: GUID)
    out = s.catalog_and_survey(_FS, dict(_FS_DATA))
    s._automated_curation.create_folder_element_from_template.assert_not_called()
    assert out["filesystem_guid"] == GUID


# ── 3. doc sources: the ExternalReference ───────────────────────────────

def _doc_clients(lookup, by_guid=None):
    from pyegeria import AutomatedCuration, ExternalReferences
    from pyegeria.omvs.metadata_expert import MetadataExpert

    ac, refs, expert = _fake(AutomatedCuration), _fake(ExternalReferences), _fake(MetadataExpert)
    ac.get_guid_for_name.side_effect = lookup
    refs.create_external_reference.return_value = GUID
    refs.link_external_reference.return_value = "link-guid"
    expert.get_metadata_element_by_guid.side_effect = by_guid or (lambda *a, **k: {"elementHeader": {"guid": GUID}})
    return _Clients({AutomatedCuration: ac, ExternalReferences: refs, MetadataExpert: expert}), refs


_SOURCE = {"url": "https://docs.example.com/dict", "label": "Dict", "source_type": "data_dictionary"}


def test_doc_source_failed_lookup_creates_nothing():
    from resource_explorer.doc_source_egeria import publish_doc_source

    clients, refs = _doc_clients(_unreachable)
    result = publish_doc_source(dict(_SOURCE), "asset-guid", clients=clients)
    refs.create_external_reference.assert_not_called()
    assert result["ok"] is False
    assert "could not be made" in result["error"]


def test_doc_source_known_ref_unverifiable_creates_nothing():
    """`ref_guid_exists` on a timeout used to say False ("gone"), so a fresh reference was created."""
    from resource_explorer.doc_source_egeria import publish_doc_source

    clients, refs = _doc_clients(lambda *a, **k: MISS, by_guid=_unreachable)
    result = publish_doc_source(dict(_SOURCE), "asset-guid", clients=clients, known_ref_guid=GUID)
    refs.create_external_reference.assert_not_called()
    refs.link_external_reference.assert_not_called()
    assert result["ok"] is False
    assert "could not be made" in result["error"]


def test_doc_source_real_miss_still_creates():
    from resource_explorer.doc_source_egeria import publish_doc_source

    clients, refs = _doc_clients(lambda *a, **k: MISS)
    result = publish_doc_source(dict(_SOURCE), "asset-guid", clients=clients)
    refs.create_external_reference.assert_called_once()
    assert result["ok"] is True and result["ref_guid"] == GUID


# ── 4. outbox: the pre-create look-up, through the drain ────────────────

@pytest.fixture()
def outbox_db(tmp_path):
    from resource_explorer.registry import Project, ProjectRegistry

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'ob.db'}")
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    return reg


@pytest.fixture()
def _zone_access_open(monkeypatch):
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)


def _publisher(lookup):
    from pyegeria import AutomatedCuration

    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    pub = EgeriaPublisher(platform_url="https://localhost:9443", view_server="qs-view-server")
    pub._automated_curation = _fake(AutomatedCuration)
    pub._automated_curation.get_guid_for_name.side_effect = lookup
    return pub


def _drain(db, lookup):
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.egeria_outbox import OutboxClients, drain_outbox

    discovery = _fake(DataDiscovery)
    discovery.create_annotation.return_value = GUID
    summary = drain_outbox(db, OutboxClients(discovery=discovery), _publisher(lookup)._find_element_guid,
                           run_id="r1")
    return summary, discovery


@pytest.mark.usefixtures("_zone_access_open", "as_daemon")
def test_outbox_failed_lookup_creates_nothing_and_retries(outbox_db):
    outbox_db.enqueue_outbox_element("repo", "p", "annotation", "Annotation::p::t::0", {"class": "X"},
                                     run_id="r1")
    summary, discovery = _drain(outbox_db, _unreachable)
    discovery.create_annotation.assert_not_called()
    row = outbox_db.list_outbox_elements(run_id="r1")[0]
    assert summary["failed"] == 1 and summary["done"] == 0
    assert row["status"] == "failed"                       # an ordinary attempt: retried per policy, not dead
    assert "look-up" in row["last_error"] and "could not be made" in row["last_error"]
    assert not row.get("egeria_guid")


@pytest.mark.usefixtures("_zone_access_open", "as_daemon")
def test_outbox_real_miss_still_creates(outbox_db):
    outbox_db.enqueue_outbox_element("repo", "p", "annotation", "Annotation::p::t::0", {"class": "X"},
                                     run_id="r1")
    summary, discovery = _drain(outbox_db, lambda *a, **k: MISS)
    discovery.create_annotation.assert_called_once()
    assert summary["done"] == 1


def test_outbox_no_destructive_kind_reaches_the_pre_create_lookup():
    """The destructive policy is untouched by this change: every destructive kind resolves itself, so the
    pre-create look-up (and its new could-not-check failure) never runs for one. Pinned so a new destructive
    kind that does reach it is noticed."""
    from resource_explorer import egeria_outbox as ob

    reaching = [k for k in ob._CREATORS if k not in ob._SELF_RESOLVING_KINDS]
    assert reaching and not [k for k in reaching if ob.is_destructive_outbox_kind(k)]


# ── 5. reference catalog: DRAFT Data Class / Valid Value Set proposals ──

def _match(question="data_class_match"):
    from tests.test_postgres_column_profile import _proposal_match

    return _proposal_match(question)


def _designer(lookup):
    from pyegeria.omvs.data_designer import DataDesigner

    d = _fake(DataDesigner)
    d.get_guid_for_name.side_effect = lookup
    d.create_data_class.return_value = GUID
    return d


def _ref_manager(lookup):
    from pyegeria.omvs.reference_data import ReferenceDataManager

    r = _fake(ReferenceDataManager)
    r.get_guid_for_name.side_effect = lookup
    r.create_valid_value_definition.return_value = GUID
    return r


def test_reference_data_class_failed_lookup_creates_nothing():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )

    designer = _designer(_unreachable)
    result = publish_proposed_data_class(designer, _match())
    designer.create_data_class.assert_not_called()
    assert result.published is False and result.created is False
    assert "could not be made" in result.error
    # Typed: the look-up asks only for the type it is about to create.
    assert designer.get_guid_for_name.call_args.kwargs.get("type_name") == "DataClass"


def test_reference_data_class_real_miss_still_creates():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )

    designer = _designer(lambda *a, **k: MISS)
    result = publish_proposed_data_class(designer, _match())
    designer.create_data_class.assert_called_once()
    assert result.created is True and result.published is True


def test_reference_value_set_failed_lookup_creates_nothing():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_valid_value_set,
    )

    ref = _ref_manager(_unreachable)
    result = publish_proposed_valid_value_set(ref, _match("reference_data_match"))
    ref.create_valid_value_definition.assert_not_called()
    assert result.published is False and "could not be made" in result.error
    assert ref.get_guid_for_name.call_args.kwargs.get("type_name") == "ValidValueDefinition"


def test_reference_value_set_member_failed_lookup_creates_no_member():
    """The set is found; each member's look-up fails. No member is created, and the result says so."""
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_valid_value_set,
    )

    calls = []

    def lookup(name, *a, **k):
        calls.append(name)
        if len(calls) == 1:
            return GUID                                    # the set itself
        raise PyegeriaConnectionException()

    ref = _ref_manager(lookup)
    result = publish_proposed_valid_value_set(ref, _match("reference_data_match"))
    ref.create_valid_value_definition.assert_not_called()
    assert result.member_guids == []
    assert sorted(result.members_not_checked) == ["CLOSED", "NEW", "OPEN"]
    assert result.as_dict()["members_not_checked"]


def test_reference_value_set_real_miss_still_creates():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_valid_value_set,
    )

    ref = _ref_manager(lambda *a, **k: MISS)
    result = publish_proposed_valid_value_set(ref, _match("reference_data_match"))
    assert ref.create_valid_value_definition.call_count == 1 + 3       # the set and its three members
    assert result.created is True and result.published is True


# ── shared: the annotation create loop the surveyors and publisher use ──

def test_annotation_loop_failed_lookup_creates_nothing():
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.annotation_props import publish_annotations
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    discovery = _fake(DataDiscovery)
    ann = ResourceMeasureAnnotation(summary="s", analysis_step="x", confidence=90, resource_properties={})
    guids = publish_annotations(discovery, _publisher(_unreachable)._find_element_guid, [ann], "rep", "Annotation::p::t")
    discovery.create_annotation.assert_not_called()
    assert guids == [None]


def test_annotation_loop_real_miss_still_creates():
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.annotation_props import publish_annotations
    from resource_explorer.surveyors.survey_report import ResourceMeasureAnnotation

    discovery = _fake(DataDiscovery)
    discovery.create_annotation.return_value = GUID
    ann = ResourceMeasureAnnotation(summary="s", analysis_step="x", confidence=90, resource_properties={})
    publish_annotations(discovery, _publisher(lambda *a, **k: MISS)._find_element_guid, [ann], "rep",
                        "Annotation::p::t")
    discovery.create_annotation.assert_called_once()



# ── 6. repo publisher: sub-resource folders and files (same shape, found in the sweep) ──

def _sub_resource_publisher(tmp_path, lookup):
    from pyegeria import AutomatedCuration

    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'sub.db'}")
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    reg.catalog_sub_resource("repo", "p", "docs", "folder")
    pub = EgeriaPublisher(platform_url="https://localhost:9443", view_server="qs-view-server", registry=reg)
    pub._automated_curation = _fake(AutomatedCuration)
    pub._automated_curation.get_guid_for_name.side_effect = lookup
    pub._automated_curation._async_get_template_guid_for_technology_type.return_value = GUID
    pub._automated_curation.create_elem_from_template.return_value = GUID
    pub._connect = lambda *a, **k: None                    # the clients are the doubles above
    return pub, reg


def test_repo_sub_resource_failed_lookup_creates_nothing(tmp_path):
    pub, reg = _sub_resource_publisher(tmp_path, _unreachable)
    out = pub.publish_sub_resources("p", "https://github.com/o/p", "asset-guid", ["docs"])
    pub._automated_curation.create_elem_from_template.assert_not_called()
    assert out == {}
    assert "could not be made" in pub.sub_resources_not_checked["docs"]
    assert not reg.list_sub_resources("repo", "p")[0].get("egeria_guid")


def test_repo_sub_resource_real_miss_still_creates(tmp_path):
    pub, _ = _sub_resource_publisher(tmp_path, lambda *a, **k: MISS)
    out = pub.publish_sub_resources("p", "https://github.com/o/p", "asset-guid", ["docs"])
    pub._automated_curation.create_elem_from_template.assert_called_once()
    assert out == {"docs": GUID}


# ── 7. arch recovery: a component whose image maps to a content-pack element (found in the sweep) ──

def _component_fixture(tmp_path, monkeypatch, pack_lookup):
    from pyegeria import AutomatedCuration
    from pyegeria.omvs.solution_architect import SolutionArchitect

    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.surveyors.arch_recovery import admission as adm

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'arch.db'}")
    reg._init_schema()
    reg.add(Project(slug="egeria_git", display_name="egeria_git", github_url="https://github.com/o/e"))
    reg.upsert_finding("egeria_git", "architecture_recovery", [{
        "check_name": "component", "label": "x",
        "detail": {"name": "egeria-main", "slug": "em", "type": "Third Party Process", "admission": adm.SHIPPED,
                   "image": "odpi/egeria-platform", "admission_evidence": "image odpi/egeria-platform"}}],
        surveyed_at="2026-10-08T00:00:00", scope_locator="src/x")
    architect, curation = _fake(SolutionArchitect), _fake(AutomatedCuration)
    architect.get_solution_components_by_name.side_effect = pack_lookup
    architect.create_solution_component.return_value = GUID
    curation.get_guid_for_name.return_value = MISS
    from pyegeria import ClassificationExplorer
    from pyegeria.omvs.metadata_expert import MetadataExpert

    clients = _Clients({SolutionArchitect: architect, AutomatedCuration: curation,
                        MetadataExpert: _fake(MetadataExpert), ClassificationExplorer: _fake(ClassificationExplorer)})
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client", lambda *a, **k: clients)
    return reg, architect


@pytest.mark.usefixtures("as_daemon")
def test_content_pack_failed_lookup_creates_no_component(tmp_path, monkeypatch):
    """A failed content-pack search used to read as "no pack element", and RE's own component was created
    beside the pack's under ANOTHER qualifiedName: Egeria's duplicate refusal cannot catch that one."""
    from resource_explorer.workflows.curate import materialize_component_if_accepted

    reg, architect = _component_fixture(tmp_path, monkeypatch, _unreachable)
    out = materialize_component_if_accepted(reg, "repo", "egeria_git", "src/x", "accepted")
    architect.create_solution_component.assert_not_called()
    assert out["status"] == "error" and "could not be made" in out["error"]
    assert "PyegeriaConnectionException" in out["error"]          # the search itself, not the connect


@pytest.mark.usefixtures("as_daemon")
def test_content_pack_real_miss_still_creates_the_component(tmp_path, monkeypatch):
    from resource_explorer.workflows.curate import materialize_component_if_accepted

    reg, architect = _component_fixture(tmp_path, monkeypatch, lambda *a, **k: MISS)
    out = materialize_component_if_accepted(reg, "repo", "egeria_git", "src/x", "accepted")
    assert out["status"] in ("materialized", "already_materialized"), out
    assert out["guid"] == GUID
    architect.create_solution_component.assert_called_once()
