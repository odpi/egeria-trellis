import os
import tempfile
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from resource_explorer.registry import FileSystemEntity, ProjectRegistry, ProjectStatus
from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor
from resource_explorer.surveyors.filesystem.egeria_filesystem_surveyor import EgeriaFileSystemSurveyor

# Brief I: these tests fake the Egeria clients; the code under test runs as a signed-in caller, as on a route.
pytestmark = pytest.mark.usefixtures("signed_in_caller")


def test_local_filesystem_surveyor_walk():
    # Setup temporary directory inside workspace for scanning
    with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
        # Create a mock CSV file
        csv_path = os.path.join(tmpdir, "sales.csv")
        df_csv = pd.DataFrame({"order_id": [1, 2, 3], "amount": [10.5, 20.0, 15.2], "status": ["paid", "pending", "paid"]})
        df_csv.to_csv(csv_path, index=False)

        # Create a mock subfolder and file
        subfolder = os.path.join(tmpdir, "archive")
        os.makedirs(subfolder)
        parquet_path = os.path.join(subfolder, "log.parquet")
        df_pq = pd.DataFrame({"log_id": [101, 102], "message": ["init", "shutdown"]})
        df_pq.to_parquet(parquet_path, index=False)

        # Create a noise file (e.g. log file)
        log_path = os.path.join(tmpdir, "debug.log")
        with open(log_path, "w") as f:
            f.write("some debug noise")

        # Create FileSystemEntity
        fs_entity = FileSystemEntity(
            slug="test-walk-fs",
            display_name="Test Walk FS",
            local_mount_point=tmpdir,
            canonical_mount_point="file://shared-nfs/test-walk-fs",
            description="Testing local directory walk and profile"
        )

        mock_registry = MagicMock(spec=ProjectRegistry)
        surveyor = LocalFileSystemSurveyor(fs_entity, mock_registry)
        results = surveyor.run()

        # Assertions
        assert results["total_files"] == 3
        assert results["total_data_files"] == 2
        assert "CSV" in results["formats"]
        assert "Parquet" in results["formats"]
        assert results["formats"]["CSV"]["count"] == 1
        assert results["formats"]["Parquet"]["count"] == 1
        
        # Verify inventory
        inventory = results["inventory"]
        assert len(inventory) == 3
        
        csv_info = next(f for f in inventory if f["file_name"] == "sales.csv")
        assert csv_info["is_data_file"] is True
        assert csv_info["format"] == "CSV"
        assert csv_info["col_count"] == 3
        assert csv_info["row_count"] == 3
        assert len(csv_info["columns"]) == 3
        assert csv_info["columns"][0]["name"] == "order_id"
        assert csv_info["columns"][0]["dtype"] in ("int64", "integer", "int")

        pq_info = next(f for f in inventory if f["file_name"] == "log.parquet")
        assert pq_info["is_data_file"] is True
        assert pq_info["format"] == "Parquet"
        assert pq_info["col_count"] == 2
        assert pq_info["row_count"] == 2


@patch("pyegeria.AutomatedCuration")
@patch("pyegeria.AssetMaker")
@patch("pyegeria.omvs.data_discovery.DataDiscovery")
@patch("pyegeria.MetadataExpert")
def test_egeria_filesystem_surveyor(mock_expert_cls, mock_discovery_cls, mock_asset_maker_cls, mock_autoc_cls):
    # Setup mocks
    mock_expert_cls.return_value.get_metadata_element_by_unique_name.return_value = "No elements found"
    mock_autoc = mock_autoc_cls.return_value
    mock_asset_maker = mock_asset_maker_cls.return_value
    mock_discovery = mock_discovery_cls.return_value

    # pyegeria's real miss, for the data files' typed pathName search (egeria_lookup.guid_by_exact_value)
    mock_expert_cls.return_value.find_metadata_elements_with_string.return_value = "No elements found"
    mock_autoc.get_template_guid_for_technology_type.return_value = "fbdd8efd-1b69-474c-bb6d-0a304b394146"
    mock_autoc.create_folder_element_from_template.return_value = "0f0f0f0f-1111-2222-3333-444444444444"
    mock_autoc.create_elem_from_template.return_value = "file-guid-456"
    mock_asset_maker.create_asset.return_value = "report-guid-789"

    fs_entity = FileSystemEntity(
        slug="mock-fs",
        display_name="Mock FS",
        local_mount_point="/tmp/local-mount",
        canonical_mount_point="file://shared-mount",
    )

    survey_data = {
        "total_files": 1,
        "total_data_files": 1,
        "total_size_bytes": 1024,
        "total_size": "1.0 KB",
        "formats": {"CSV": {"count": 1, "size": "1.0 KB"}},
        "inventory": [
            {
                "file_name": "sales.csv",
                "file_path": "sales.csv",
                "is_data_file": True,
                "format": "CSV",
                "file_size": 1024,
                "row_count": 100,
                "col_count": 5,
                "columns": [{"name": "id", "type": "int", "null_rate": 0.0}]
            }
        ]
    }

    mock_registry = MagicMock(spec=ProjectRegistry)

    surveyor = EgeriaFileSystemSurveyor(
        platform_url="http://localhost:9443",
        view_server="qs-view-server",
        user_id="erinoverview",
        user_password="password"
    )
    
    res = surveyor.catalog_and_survey(fs_entity, survey_data, mock_registry)

    # Verify calls
    assert mock_autoc.create_folder_element_from_template.call_args.args == ("file://shared-mount", "Mock FS", "mock-fs")
    assert mock_autoc.create_folder_element_from_template.call_args.kwargs["description"] == \
        "FileSystem root mapping: /tmp/local-mount -> file://shared-mount"

    mock_registry.set_filesystem_egeria_guid.assert_called_once_with("mock-fs", "0f0f0f0f-1111-2222-3333-444444444444")
    
    # Assert return values
    assert res["filesystem_guid"] == "0f0f0f0f-1111-2222-3333-444444444444"
    assert res["report_guid"] == "report-guid-789"
    assert res["file_guids"]["sales.csv"] == "file-guid-456"
    assert res["annotation_count"] == 2  # 1 measure annotation, 1 schema annotation
