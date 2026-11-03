"""The root FileFolder is made with every placeholder Egeria's template declares, and a 404-001 that names an unfilled
"~{...}~" placeholder is a broken connection, not a verdict on the path (live test 2026-10-21)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from resource_explorer import file_reach
from resource_explorer.registry import FileSystemEntity, ProjectRegistry

# Quoted from Egeria origin/main (read 2026-10-21):
#  PlaceholderProperty.getFolderPlaceholderPropertyTypes()
#    open-metadata-framework/.../frameworks/openmetadata/controls/PlaceholderProperty.java:678-692
#    -> FILE_SYSTEM_NAME(:198), DIRECTORY_PATH_NAME(:213), DIRECTORY_NAME, DIRECTORY_ADDRESS(:218),
#       VERSION_IDENTIFIER(:104), DESCRIPTION(:96), DEPLOYED_IMPLEMENTATION_TYPE(:138)
#  used by DataAssetTemplateDefinition.FILE_FOLDER_TEMPLATE (core-content-pack/.../DataAssetTemplateDefinition.java:285-301)
FOLDER_PLACEHOLDERS = {"fileSystemName", "directoryPathName", "directoryName", "directoryAddress",
                       "versionIdentifier", "description", "deployedImplementationType"}
#  PlaceholderProperty.getDataFilesPlaceholderPropertyTypes()  (same file :629-642), used by DATA_FILE_TEMPLATE and the
#  CSV / Parquet / Spreadsheet templates (DataAssetTemplateDefinition.java:324-359, 400-435)
DATA_FILE_PLACEHOLDERS = {"fileSystemName", "filePathName", "fileName", "fileType", "fileExtension", "fileEncoding",
                          "fileAddress", "versionIdentifier", "description", "deployedImplementationType"}
FOLDER_TEMPLATE_GUID = "fbdd8efd-1b69-474c-bb6d-0a304b394146"   # FilesTemplateType.java:27
GUID = "11111111-2222-3333-4444-555555555555"
CANON = "/deployments/data-lake"


def _surveyor():
    from pyegeria import AssetMaker, AutomatedCuration, MetadataExpert
    from pyegeria.omvs.data_discovery import DataDiscovery

    from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

    s = EgeriaFileSystemSurveyor(platform_url="https://localhost:9443", view_server="qs-view-server")
    s._automated_curation = create_autospec(AutomatedCuration, instance=True, spec_set=True)
    s._automated_curation.get_guid_for_name.return_value = "No elements found"
    s._metadata_expert = create_autospec(MetadataExpert, instance=True, spec_set=True)
    s._metadata_expert.get_metadata_element_by_unique_name.return_value = "No elements found"
    s._automated_curation.get_template_guid_for_technology_type.return_value = FOLDER_TEMPLATE_GUID
    s._automated_curation.create_elem_from_template.return_value = GUID
    s._asset_maker = create_autospec(AssetMaker, instance=True, spec_set=True)
    s._discovery = create_autospec(DataDiscovery, instance=True, spec_set=True)
    return s


_FS = SimpleNamespace(slug="lake", display_name="Lake", local_mount_point="/Users/x/lake",
                      canonical_mount_point=CANON, description="", egeria_asset_guid="")


def _real_folder_surveyor(sent):
    """The REAL pyegeria 6.2.8 create_folder_element_from_template, with only the HTTP layer faked: the template look-up
    and the POST (`_async_create_elem_from_template`) record what reaches the wire."""

    from pyegeria import AutomatedCuration

    s = _surveyor()
    ac = AutomatedCuration.__new__(AutomatedCuration)

    async def _tmpl(technology_type):
        sent["technology_type"] = technology_type
        return FOLDER_TEMPLATE_GUID

    async def _post(body=None):
        sent["body"] = body
        return GUID

    ac._async_get_template_guid_for_technology_type = _tmpl  # under pyegeria's own async create, and its sync lookup
    ac._async_create_elem_from_template = _post
    s._metadata_expert.get_metadata_element_by_unique_name.side_effect = (
        lambda name=None, property_name="qualifiedName", body=None, **k:
        sent.setdefault("lookups", []).append((name, property_name)) or "No elements found")
    s._automated_curation = ac
    return s


def test_the_root_folder_body_carries_every_placeholder_the_template_declares():
    sent = {}
    s = _real_folder_surveyor(sent)
    assert s._find_or_create_root_folder(_FS) == (GUID, True)
    assert sent["technology_type"] == "File System Directory"
    body = sent["body"]
    ph = body["placeholderPropertyValues"]
    assert body["templateGUID"] == FOLDER_TEMPLATE_GUID and body["isOwnAnchor"] is True
    assert set(ph) == FOLDER_PLACEHOLDERS
    # BOTH go to the canonical mount: the pathName builds the qualifiedName (unchanged), the address is the endpoint
    assert ph["directoryAddress"] == CANON and ph["directoryPathName"] == CANON
    assert ph["fileSystemName"] == "lake" and ph["directoryName"] == "Lake"
    assert all(isinstance(v, str) and v.strip() and "~{" not in v for v in ph.values())   # none empty, none unfilled
    assert ph["deployedImplementationType"] == "File System Directory" and ph["versionIdentifier"] == "1.0"
    # the look-up the attach path keeps is still by the template's qualifiedName, now the unique-name read
    assert sent["lookups"] == [(f"FileFolder::lake:{CANON}", "qualifiedName")]


def test_a_data_file_body_carries_every_placeholder_its_template_declares():
    s = _surveyor()
    s._create_data_file_asset({"file_name": "a.csv", "file_path": "a.csv", "format": "CSV"}, f"{CANON}/a.csv", "lake")
    ph = s._automated_curation.create_elem_from_template.call_args.args[0]["placeholderPropertyValues"]
    assert set(ph) == DATA_FILE_PLACEHOLDERS
    assert all(isinstance(v, str) and v.strip() for v in ph.values())      # an empty value stays a literal ~{x}~
    assert ph["fileAddress"] == f"{CANON}/a.csv" and ph["filePathName"] == f"{CANON}/a.csv"


def test_the_generic_fallback_is_a_template_egeria_has():
    """ae3067c7-... was never an Egeria template; the generic Data File template is FilesTemplateType's 66d8dda9-..."""
    s = _surveyor()
    s._automated_curation.create_elem_from_template.side_effect = [RuntimeError("no"), GUID]
    s._create_data_file_asset({"file_name": "a.csv", "file_path": "a.csv", "format": "CSV"}, "/a.csv", "lake")
    assert s._automated_curation.create_elem_from_template.call_args.args[0]["templateGUID"] == \
        "66d8dda9-00cf-4e59-938c-4b0583596b1e"


# ── reachability ──────────────────────────────────────────────────────────────────────────────────────────────────

BROKEN = ("OMES-SURVEY-ACTION-0018 BASIC-FILE-CONNECTOR-404-001 The folder named ~{directoryAddress}~ in the "
          "Connection object does not exist")
REAL = "OMES-SURVEY-ACTION-0018 BASIC-FILE-CONNECTOR-404-001 The folder named /Users/x does not exist"


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "ph.db"))
    (tmp_path / "lake").mkdir()
    r.register_filesystem(FileSystemEntity(slug="lake", display_name="Lake", local_mount_point=str(tmp_path / "lake"),
                                           canonical_mount_point=CANON))
    return r


def _row(registry, outcome, *, at="2026-10-20T10:00:00+00:00", guid="", code="", **detail):
    registry.record_reachability_check("lake", probed_at=at, outcome=outcome, error_code=code,
                                       error_detail=json.dumps({"path": CANON, **detail}), engine_action_guid=guid)


def _derived(message, guid="ea-1"):
    return {"state": "failed", "message": message, "engine_action_guid": guid}


def test_a_404_001_naming_an_unfilled_placeholder_is_a_broken_connection_and_never_flips(registry):
    _row(registry, "expected_reachable", prefix=CANON)
    assert file_reach.settle_from_run(registry, "lake", _derived(BROKEN, "ea-bad")) == "egeria_connection_broken"
    s = file_reach.fork_for(registry, registry.get_filesystem("lake"))
    assert s.state == file_reach.CONNECTION_BROKEN
    assert s.sentence.startswith("connection broken · placeholder unresolved ~{directoryAddress}~")
    assert "reach stays expected reachable · unverified" in s.sentence
    assert s.surveyor == file_reach.SURVEYOR_EGERIA                      # the lever is not flipped to RE
    assert s.evidence_action_guid == "ea-bad" and "~{directoryAddress}~" in s.why
    assert file_reach.refusal_words(s, for_egeria_lead=True) == ""
    assert "egeria_connection_broken" in {r["outcome"] for r in registry.list_reachability_history("lake")}


def test_a_404_001_with_a_real_path_still_flips(registry):
    _row(registry, "expected_reachable", prefix=CANON)
    assert file_reach.settle_from_run(registry, "lake", _derived(REAL, "ea-real")) == "egeria_cannot_read"
    s = file_reach.fork_for(registry, registry.get_filesystem("lake"))
    assert s.state == file_reach.EGERIA_CANNOT and s.surveyor == file_reach.SURVEYOR_RE


def test_the_already_flipped_coco_data_lake_row_reads_as_broken_without_editing_it(registry):
    """coco_data_lake's stored history: a wrongly flipped egeria_cannot_read row for engine action c1d924b5."""
    _row(registry, "egeria_cannot_read", at="2026-10-21T08:00:00+00:00", guid="c1d924b5",
         code=file_reach.NOT_VISIBLE_CODE, egeria_message=BROKEN)
    s = file_reach.fork_for(registry, registry.get_filesystem("lake"))
    assert s.state == file_reach.CONNECTION_BROKEN and s.surveyor == file_reach.SURVEYOR_EGERIA
    assert "placeholder unresolved ~{directoryAddress}~" in s.sentence
    assert s.evidence_action_guid == "c1d924b5"
    # the stored row is untouched, and settling the same run again writes nothing
    assert [r["outcome"] for r in registry.list_reachability_history("lake")] == ["egeria_cannot_read"]
    assert file_reach.settle_from_run(registry, "lake", _derived(BROKEN, "c1d924b5")) == ""
