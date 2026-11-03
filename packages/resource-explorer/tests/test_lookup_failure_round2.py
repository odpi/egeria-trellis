"""Round 2 of re/lookup-failure-never-creates (review of 7a565568, plus two live read-only probes).

Same rule as tests/test_lookup_failure_never_creates.py: found / absent / could-not-check, and only absent
creates or proves anything gone. Fakes bind real pyegeria signatures (`create_autospec(..., spec_set=True)`),
and pyegeria exceptions are built the way pyegeria builds them.
"""
from __future__ import annotations

import httpx
import pytest
from pyegeria.core._exceptions import (
    PyegeriaConnectionException,
    PyegeriaException,
    PyegeriaNotFoundException,
    PyegeriaTimeoutException,
    PyegeriaUnauthorizedException,
)

from tests.test_lookup_failure_never_creates import (
    _FS,
    GUID,
    MISS,
    _Clients,
    _fake,
    _fs_surveyor,
    _unreachable,
)

pytestmark = pytest.mark.usefixtures("signed_in_caller")


def _egeria_error(cls, related: int, msg_id: str, message: str = ""):
    """A pyegeria exception exactly as `_async_make_request` raises it for an Egeria-wrapped answer."""
    body = {"class": "VoidResponse", "relatedHTTPCode": related, "exceptionErrorMessageId": msg_id,
            "exceptionErrorMessage": message or f"{msg_id} the platform said so"}
    resp = httpx.Response(200, json=body, request=httpx.Request("POST", "https://localhost:9443/servers/v/api"))
    return cls(resp, {"caller_method": "test"}, additional_info=body)


def _timeout_naming_404():
    exc = PyegeriaTimeoutException(
        {"caller_method": "get_metadata_element_by_unique_name"},
        {"endpoint": "https://localhost:9443/servers/v/api/open-metadata/metadata-expert/by-unique-name/404",
         "error_kind": "timeout", "timeout_seconds": 30},
        httpx.ReadTimeout("timed out"))
    assert "404" in str(exc)                     # the precondition this test is about
    return exc


# ── 8. a view server that is not running is not an absent element ───────

@pytest.mark.parametrize("msg_id", ["OMAG-MULTI-TENANT-404-001", "OMAG-MULTI-TENANT-404-002"])
def test_server_not_available_404_is_unreadable(msg_id):
    from resource_explorer.egeria_absence import UNREADABLE, is_absent

    exc = _egeria_error(PyegeriaNotFoundException, 404, msg_id,
                        f"{msg_id} The OMAG Server qs-view-serverX is not available to service a request")
    assert exc.related_http_code == 404                 # pyegeria's own shape: typed not-found, 404
    assert is_absent(exc) == UNREADABLE


def test_genuine_element_not_found_404_stays_absent():
    from resource_explorer.egeria_absence import ABSENT, is_absent

    exc = _egeria_error(PyegeriaNotFoundException, 404, "OMRS-METADATA-COLLECTION-404-001",
                        "OMRS-METADATA-COLLECTION-404-001 The entity identified with guid x is not known")
    assert is_absent(exc) == ABSENT


# ── 1. catalogue gateway read_element ────────────────────────────────────

@pytest.fixture
def commit_registry(tmp_path):
    from resource_explorer.registry import DatabaseEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "commit.db"))
    reg.register_database(DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="localhost",
                                         egeria_host="host.docker.internal", port=5442, database_name="shop"))
    return reg


def _gateway(registry, unique_name_answer):
    import pyegeria
    from pyegeria.omvs.metadata_expert import MetadataExpert

    from resource_explorer import catalogue_gateway as gw

    g = gw.PyegeriaCatalogueGateway(registry.get_database("db", allow_unreadable=True))
    doubles = {"MetadataExpert": _fake(MetadataExpert), "AssetMaker": _fake(pyegeria.AssetMaker),
               "AutomatedCuration": _fake(pyegeria.AutomatedCuration)}
    doubles["MetadataExpert"].get_metadata_element_by_unique_name.side_effect = unique_name_answer
    doubles["AssetMaker"].get_catalog_targets.return_value = MISS
    g._clients.update(doubles)
    return g, doubles


def _creates(doubles):
    return [c for d in doubles.values() for c in d.mock_calls if c[0].split(".")[0].startswith("create")]


def test_read_element_timeout_naming_404_is_not_absent(commit_registry):
    from resource_explorer.catalogue_gateway import GatewayError

    g, _ = _gateway(commit_registry, lambda *a, **k: (_ for _ in ()).throw(_timeout_naming_404()))
    with pytest.raises(GatewayError):
        g.read_element("PostgreSQL Relational Database::x::shop::sales")


def test_read_element_typed_not_found_is_absent(commit_registry):
    exc = _egeria_error(PyegeriaNotFoundException, 404, "OMRS-METADATA-COLLECTION-404-001")
    g, _ = _gateway(commit_registry, lambda *a, **k: (_ for _ in ()).throw(exc))
    assert g.read_element("PostgreSQL Relational Database::x::shop::sales") is None


def test_attach_on_a_timeout_naming_404_creates_nothing(commit_registry):
    from resource_explorer import catalogue_commit as cc
    from resource_explorer.catalogue_gateway import GatewayError

    g, doubles = _gateway(commit_registry, lambda *a, **k: (_ for _ in ()).throw(_timeout_naming_404()))
    with pytest.raises(GatewayError):
        cc.apply_attach(commit_registry, g, {"slug": "db", "schema": "sales", "database_guid": GUID})
    assert _creates(doubles) == []
    assert commit_registry.list_catalogue_commit_proofs("db") == []


def test_leave_out_on_a_timeout_naming_404_writes_no_gone_proof(commit_registry):
    from resource_explorer import catalogue_commit as cc
    from resource_explorer.catalogue_gateway import GatewayError

    g, _ = _gateway(commit_registry, lambda *a, **k: (_ for _ in ()).throw(_timeout_naming_404()))
    with pytest.raises(GatewayError):
        cc.apply_leave_out(commit_registry, g, {"slug": "db", "schema": "sales"})
    assert [p for p in commit_registry.list_catalogue_commit_proofs("db") if p["proof"] == cc.P_REMOVED] == []


# ── 2. the draft GovernanceZone ──────────────────────────────────────────

def _zone(monkeypatch, answer):
    from pyegeria.omvs.metadata_expert import MetadataExpert

    monkeypatch.setenv("EXPLORER_DRAFT_ZONE", "re-draft")
    expert = _fake(MetadataExpert)
    expert.get_metadata_element_by_unique_name.side_effect = answer
    expert.create_metadata_element.return_value = GUID
    monkeypatch.setattr("resource_explorer.egeria_clients.egeria_client",
                        lambda *a, **k: _Clients({MetadataExpert: expert}))
    return expert


def test_draft_zone_failed_lookup_creates_no_zone(monkeypatch):
    from resource_explorer.egeria_identity import ensure_draft_zone_exists

    expert = _zone(monkeypatch, _unreachable)
    out = ensure_draft_zone_exists()
    expert.create_metadata_element.assert_not_called()
    assert out["status"] == "error" and "could not be made" in out["error"]


def test_draft_zone_real_miss_creates_it(monkeypatch):
    from resource_explorer.egeria_identity import ensure_draft_zone_exists

    expert = _zone(monkeypatch, lambda *a, **k: "No element found")
    assert ensure_draft_zone_exists()["status"] == "created"
    expert.create_metadata_element.assert_called_once()


# ── 3. file-system catalog: one file that could not be checked ──────────

_FS_TWO = {"inventory": [
    {"file_path": "a.csv", "file_name": "a.csv", "format": "CSV", "is_data_file": True},
    {"file_path": "b.csv", "file_name": "b.csv", "format": "CSV", "is_data_file": True}],
    "total_files": 2, "total_data_files": 2, "total_size": "2 B", "total_size_bytes": 2,
    "surveyed_at": "2026-10-13T00:00:00"}


def test_filesystem_one_unchecked_file_is_skipped_and_listed_not_fatal():
    def lookup(name, *a, **k):
        if name.endswith("/a.csv"):
            raise PyegeriaConnectionException()
        return MISS

    s = _fs_surveyor(lookup)
    s._automated_curation.create_elem_from_template.return_value = GUID
    s._automated_curation.create_folder_element_from_template.return_value = GUID
    out = s.catalog_and_survey(_FS, dict(_FS_TWO))
    bodies = [c.args[0] for c in s._automated_curation.create_elem_from_template.call_args_list]
    # the first template call is the root folder (it has no fileName); the data files follow
    assert [b["placeholderPropertyValues"]["fileName"] for b in bodies if "fileName" in b["placeholderPropertyValues"]] == ["b.csv"]
    assert list(out["files_not_checked"]) == ["a.csv"]
    assert "could not be made" in out["files_not_checked"]["a.csv"]
    assert out["file_guids"] == {"b.csv": GUID}


# ── 4a. sub-resource proofs say "could not check" ───────────────────────

def test_sub_resource_not_checked_proof_says_so(tmp_path):
    from pyegeria import AssetMaker, AutomatedCuration

    from resource_explorer import repo_publish
    from resource_explorer.registry import Project, ProjectRegistry
    from resource_explorer.resource_scope import publish_chosen
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    reg = ProjectRegistry(database_url=f"sqlite:///{tmp_path / 'sub.db'}")
    reg._init_schema()
    reg.add(Project(slug="p", display_name="p", github_url="https://github.com/o/p"))
    pub = EgeriaPublisher(platform_url="https://localhost:9443", view_server="qs-view-server", registry=reg)
    pub._automated_curation = _fake(AutomatedCuration)
    pub._automated_curation.get_guid_for_name.side_effect = _unreachable
    pub._asset_maker = _fake(AssetMaker)
    pub._connect = lambda *a, **k: None
    out = publish_chosen(reg, "p", github_url="https://github.com/o/p", asset_guid="asset-guid",
                         curation_id="c1", author="dana", locators=["docs"], publisher=pub)
    pub._automated_curation.create_elem_from_template.assert_not_called()
    rows = [p for p in reg.list_catalogue_commit_proofs("p") if p["proof"] == repo_publish.P_READ_FAILED]
    assert len(rows) == 1
    err = rows[0]["detail"]["error"]
    assert err.startswith("could not check, not created") and "returned no GUID" not in err
    assert out["counts"]["failed"] == 1


# ── 4b. lineage the surveyor could not check is in the result ───────────

def _db_surveyor_for_lineage(lookup):
    from pyegeria import AutomatedCuration
    from pyegeria.omvs.lineage_linker import LineageLinker

    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor

    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    s._automated_curation = _fake(AutomatedCuration)
    s._automated_curation.get_guid_for_name.side_effect = lookup
    linker = _fake(LineageLinker)
    s._clients = lambda: _Clients({LineageLinker: linker})
    return s, linker


def test_lineage_not_checked_is_recorded_and_nothing_linked():
    from resource_explorer.registry import DatabaseEntity

    s, linker = _db_surveyor_for_lineage(_unreachable)
    db = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432, database_name="shop")
    views = [{"schema": "public", "name": "v", "lineage": {"c": {"downstream": [{"name": "x", "source": "t"}]}}}]
    s._publish_column_lineage(db, views)
    linker.link_lineage.assert_not_called()
    assert s.lineage_not_checked == ["shop.public.v.c"]


def test_step_publish_result_carries_lineage_not_checked_empty_without_views(monkeypatch):
    out = _db_step_publish(monkeypatch, create_answers=[])
    assert out["lineage_not_checked"] == []


# ── 6. counts come from the GUIDs Egeria returned ───────────────────────

def test_report_step_words_say_the_count_egeria_returned():
    from resource_explorer.catalogue_commit import report_step_words

    state, words = report_step_words({"report_guid": "abcdef12-0", "report_element_guid": "abcdef12-0",
                                      "annotation_count": 3, "annotations_in_egeria": 4}, "")
    assert state == "done" and "4 annotations published" in words and "built locally: 3" in words


def test_report_step_words_without_a_count_do_not_claim_one():
    from resource_explorer.catalogue_commit import report_step_words

    _, words = report_step_words({"report_guid": "abcdef12-0", "annotation_count": 3}, "")
    assert "3 annotations published" not in words and "could not be counted" in words


def test_filesystem_annotation_count_is_what_egeria_returned():
    s = _fs_surveyor(lambda *a, **k: MISS)
    s._discovery.create_annotation.side_effect = RuntimeError("500 refused")
    out = s.catalog_and_survey(_FS, {**dict(_FS_TWO), "inventory": []})
    # Round 3 naming: annotation_count is RE's count, annotations_in_egeria is what Egeria returned.
    assert out["annotations_in_egeria"] == 0 and out["annotation_count"] == 1


def _db_step_publish(monkeypatch, create_answers):
    from pyegeria import AssetMaker, AutomatedCuration, MetadataExpert
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.registry import DatabaseEntity
    from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor
    from tests.qn_lookup_bridge import wire_unique_name

    s = EgeriaDatabaseSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    s._automated_curation = _fake(AutomatedCuration)
    s._asset_maker = _fake(AssetMaker)
    s._asset_maker.create_asset.return_value = "report-guid"
    # The report as Egeria answers a read by GUID: the annotations that failed to create are not under it, so the
    # read-back counts what is there (none), whatever the creates returned.
    from tests.report_pages import paged_expert
    s._metadata_expert = paged_expert(lambda guid: [])          # the report holds none: paged to the end
    # Every exact-qualifiedName read (the database element) is found, through the unique-name read (real signature:
    # the paged expert is an autospec, so the read binds MetadataExpert's own).
    wire_unique_name(s._metadata_expert, lambda name, *a, **k: GUID)
    s._discovery = _fake(DataDiscovery)
    s._discovery.create_annotation.side_effect = create_answers or [GUID]
    db = DatabaseEntity(slug="db", display_name="Shop", db_type="postgresql", host="h", port=5432, database_name="shop")
    schema_info = {"schemas": [{"name": "s1", "tables": []}], "total_tables": 0, "total_columns": 0}
    return s.publish_step_annotations(db, schema_info, None, "2026-10-13T00:00:00")


def test_db_step_publish_annotation_count_is_what_egeria_returned(monkeypatch):
    out = _db_step_publish(monkeypatch, create_answers=[RuntimeError("500 refused")] * 50)
    assert out["annotation_count"] >= 1          # RE's count (round 3 naming)
    assert out["annotations_in_egeria"] == 0


# ── 7. a security refusal on the look-up goes dead at once, in its words ──

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


def _drain_with_lookup_error(db, exc):
    from tests.test_lookup_failure_never_creates import _drain

    db.enqueue_outbox_element("repo", "p", "annotation", "Annotation::p::t::0", {"class": "X"}, run_id="r1")
    summary, discovery = _drain(db, lambda *a, **k: (_ for _ in ()).throw(exc))
    discovery.create_annotation.assert_not_called()
    return summary, db.list_outbox_elements(run_id="r1")[0]


@pytest.mark.usefixtures("_zone_access_open", "as_daemon")
def test_outbox_lookup_403_is_dead_at_once_with_security_words(outbox_db):
    from resource_explorer.egeria_outbox import REFUSED_BY_SECURITY

    exc = _egeria_error(PyegeriaUnauthorizedException, 403, "OPEN-METADATA-SECURITY-403-001",
                        "OPEN-METADATA-SECURITY-403-001 The user erinoverview is not authorized to read")
    _summary, row = _drain_with_lookup_error(outbox_db, exc)
    assert row["status"] == "dead"
    assert REFUSED_BY_SECURITY in row["last_error"] and "look-up" in row["last_error"]


@pytest.mark.usefixtures("_zone_access_open", "as_daemon")
def test_outbox_lookup_401_keeps_the_identity_suffix(outbox_db):
    exc = _egeria_error(PyegeriaUnauthorizedException, 401, "OMAG-COMMON-401-001",
                        "OMAG-COMMON-401-001 user - `` is not authorized")
    _summary, row = _drain_with_lookup_error(outbox_db, exc)
    assert row["status"] == "failed"
    assert "look-up" in row["last_error"] and "Egeria refused the write for service identity" in row["last_error"]


# ── 9. the residual odd answers are unreadable, not absent ──────────────

def test_content_pack_odd_answer_is_could_not_check(tmp_path, monkeypatch):
    from resource_explorer.workflows.curate import materialize_component_if_accepted
    from tests.test_lookup_failure_never_creates import _component_fixture

    reg, architect = _component_fixture(tmp_path, monkeypatch, lambda *a, **k: {})
    out = materialize_component_if_accepted(reg, "repo", "egeria_git", "src/x", "accepted")
    architect.create_solution_component.assert_not_called()
    assert out["status"] == "error" and "could not be made" in out["error"]


@pytest.mark.parametrize("answer", [{}, [], {"unexpected": 1}])
def test_ref_guid_exists_odd_answer_is_unreadable(answer):
    from resource_explorer.doc_source_egeria import DocSourceLookupError, ref_guid_exists
    from tests.test_lookup_failure_never_creates import _doc_clients

    clients, _ = _doc_clients(lambda *a, **k: MISS, by_guid=lambda *a, **k: answer)
    with pytest.raises(DocSourceLookupError):
        ref_guid_exists(GUID, clients=clients)


def test_ref_guid_exists_miss_string_is_absent():
    from resource_explorer.doc_source_egeria import ref_guid_exists
    from tests.test_lookup_failure_never_creates import _doc_clients

    clients, _ = _doc_clients(lambda *a, **k: MISS, by_guid=lambda *a, **k: "No element found")
    assert ref_guid_exists(GUID, clients=clients) is False


def _designer(answer):
    """(the DataDesigner double that would create, the MetadataExpert double whose unique-name read answers)."""
    from tests.test_lookup_failure_never_creates import _designer as d, _expert

    return d(), _expert(answer)


def _raise(exc):
    def f(*a, **k):
        raise exc
    return f


def test_typed_not_found_is_absent_and_creates():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )
    from tests.test_lookup_failure_never_creates import _match

    designer, expert = _designer(_raise(_egeria_error(PyegeriaNotFoundException, 404, "OMRS-METADATA-COLLECTION-404-001")))
    assert publish_proposed_data_class(designer, _match(), expert=expert).created is True
    designer.create_data_class.assert_called_once()


def test_multiple_elements_found_creates_nothing():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )
    from tests.test_lookup_failure_never_creates import _match

    designer, expert = _designer(_raise(PyegeriaException(context={"issue": "Multiple elements found for supplied name!"})))
    result = publish_proposed_data_class(designer, _match(), expert=expert)
    designer.create_data_class.assert_not_called()
    assert "could not be made" in result.error


def test_none_answer_creates_nothing():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_data_class,
    )
    from tests.test_lookup_failure_never_creates import _match

    designer, expert = _designer(lambda *a, **k: None)
    result = publish_proposed_data_class(designer, _match(), expert=expert)
    designer.create_data_class.assert_not_called()
    assert "Egeria gave no answer" in result.error


# ── 5. proposed sets are created as the type Egeria 6.2 knows ───────────

#: What Egeria 6.2 accepts (live probe, 2026-10-13): `ValidValueSet` / `ValidValuesSet` are not types it knows
#: (OMAG-COMMON-400-018 on findElements, a 500 on a typed look-up). Sets are plain ValidValueDefinitions.
_EGERIA_62_TYPES = {None, "ValidValueDefinition", "DataClass"}


def _strict_ref_manager():
    from pyegeria.omvs.reference_data import ReferenceDataManager

    r = _fake(ReferenceDataManager)

    def create(body):
        type_name = (body.get("properties") or {}).get("typeName")
        if type_name not in _EGERIA_62_TYPES:
            raise PyegeriaException(context={"issue": f"OMAG-COMMON-400-018 The type name {type_name} is not recognized"})
        return GUID

    r.create_valid_value_definition.side_effect = create
    return r


def test_proposed_value_set_is_created_as_a_type_egeria_knows():
    from resource_explorer.surveyors.database.egeria_reference_catalog import (
        publish_proposed_valid_value_set,
    )
    from tests.test_lookup_failure_never_creates import _match

    from tests.test_lookup_failure_never_creates import _expert

    ref = _strict_ref_manager()
    result = publish_proposed_valid_value_set(ref, _match("reference_data_match"), expert=_expert(lambda *a, **k: MISS))
    assert result.error == "" and result.created is True
    assert len(result.member_guids) == 3

