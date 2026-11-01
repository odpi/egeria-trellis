"""F42 (#953 walk): the Survey card's pointer check on a file system raised `'FileSystemEntity' object has no
attribute 'host'` (server_name_for, via server_pointer from check_pointers) and the route answered 500; the page
swallowed it and the card called the FileFolder a "database asset" (W27). A file system has no server: its check
reads only its folder's pointer, and its Catalog and Survey row names the element a folder asset."""
from __future__ import annotations

import pytest

from resource_explorer import catalog_and_survey as cas
from resource_explorer import native_survey_run as nsr
from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from tests.test_catalog_and_survey import RegPort

FOLDER = "060e5efb-0000-0000-0000-000000000001"


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "f42.db"))
    (tmp_path / "bay").mkdir()
    reg.register_filesystem(FileSystemEntity(slug="loading_bay", display_name="Loading Bay",
                                             local_mount_point=str(tmp_path / "bay"),
                                             canonical_mount_point="/deployments/loading-bay",
                                             egeria_asset_guid=FOLDER))
    return reg


def test_a_file_system_has_no_server_pointer_and_never_reaches_server_name_for(registry):
    entity = registry.get_filesystem("loading_bay")
    assert not hasattr(entity, "host")
    assert cas.server_pointer(registry, entity) == ""


def test_the_pointer_check_on_a_file_folder_reads_the_folder_and_no_server(registry):
    port = RegPort()
    port.assets.add(FOLDER)
    out = cas.check_pointers(registry, port, "filesystem", "loading_bay")
    assert out["database"] == "resolves" and out["server"] == "none" and out["error"] == ""


def test_a_file_folder_is_never_described_as_a_database_asset(registry):
    rows = nsr.native_survey_rows(registry, "filesystem", "loading_bay", "File System Directory")
    pointed = [r for r in rows if r.get("asset_guid")]
    for r in pointed:
        assert r["asset_kind"] == "folder asset", r["qualified_name"]
    assert all(r.get("asset_kind") != "database asset" for r in rows)
    assert nsr._asset_kind("filesystem", registry.get_filesystem("loading_bay")) == "folder asset"


def test_a_database_keeps_its_database_asset_words(tmp_path):
    from resource_explorer.registry import DatabaseEntity
    reg = ProjectRegistry(db_path=str(tmp_path / "db.db"))
    reg.register_database(DatabaseEntity(slug="shop", display_name="shop", host="h", port=5432,
                                         database_name="shop", db_type="postgresql"))
    assert nsr._asset_kind("database", reg.get_database("shop")) == "database asset"
