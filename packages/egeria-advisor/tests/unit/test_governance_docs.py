import re
import pytest
from pathlib import Path
from advisor.governance_docs import DocumentManager

@pytest.fixture
def mock_doc_manager(tmp_path, monkeypatch):
    # Mock _load_paths to return folders inside tmp_path
    paths = {
        "inbox": tmp_path / "inbox",
        "outbox": tmp_path / "outbox",
        "trash": tmp_path / "trash",
        "versions": tmp_path / "versions",
    }
    monkeypatch.setattr("advisor.governance_docs._load_paths", lambda: paths)
    
    # Initialize the document manager
    dm = DocumentManager()
    return dm

def test_document_lifecycle(mock_doc_manager):
    dm = mock_doc_manager
    title = "Test Plan"
    content = "# Test Plan\n\n## Command Sequence\n\n- action: test"
    
    # 1. Create plan in inbox
    doc_id = dm.create(title, content)
    assert doc_id is not None
    assert doc_id.endswith("_test_plan")
    
    # Verify it exists in inbox
    assert dm.folder_of(doc_id) == "inbox"
    loaded = dm.load(doc_id)
    assert "Test Plan" in loaded
    
    # 2. Move to outbox (execution simulation)
    outcome = "## Outcome\n\n**Status:** Completed\n\nSuccess"
    outbox_doc_id = dm.move_to_outbox(doc_id, outcome)
    assert outbox_doc_id is not None
    assert outbox_doc_id.startswith(doc_id)
    assert "_executed_" in outbox_doc_id
    
    # Verify folders
    assert dm.folder_of(doc_id) is None
    assert dm.folder_of(outbox_doc_id) == "outbox"
    
    # Check outbox contents have outcome appended
    outbox_content = dm.load(outbox_doc_id)
    assert outcome in outbox_content
    
    # 3. Check versioning is consolidated under original doc_id
    # Trigger a version save on original_doc_id
    dm._save_version(outbox_doc_id, "version content 1")
    versions = dm.list_versions(doc_id)
    assert len(versions) == 1
    
    # Also list versions using outbox_doc_id and get the same list
    versions_from_outbox = dm.list_versions(outbox_doc_id)
    assert len(versions_from_outbox) == 1
    assert versions_from_outbox[0]["version_file"] == versions[0]["version_file"]
    
    # Load version
    loaded_version = dm.load_version(doc_id, versions[0]["version_file"])
    assert loaded_version == "version content 1"
    
    # Recover version
    loaded_version_outbox = dm.load_version(outbox_doc_id, versions[0]["version_file"])
    assert loaded_version_outbox == "version content 1"
    
    # 4. Recover to inbox
    recovered_doc_id = dm.move_to_inbox(outbox_doc_id)
    assert recovered_doc_id == doc_id
    assert dm.folder_of(outbox_doc_id) is None
    assert dm.folder_of(doc_id) == "inbox"
    
    # Content in inbox should be stripped of the outcome section
    inbox_content = dm.load(doc_id)
    assert "Outcome" not in inbox_content
    
    # 5. Trash and restore
    # Inbox restore:
    assert dm.delete(doc_id) is True
    assert dm.folder_of(doc_id) == "trash"
    assert dm.restore_from_trash(doc_id) is True
    assert dm.folder_of(doc_id) == "inbox"
    
    # Outbox restore:
    # Execute again
    outbox_doc_id2 = dm.move_to_outbox(doc_id, outcome)
    assert dm.delete(outbox_doc_id2) is True
    assert dm.folder_of(outbox_doc_id2) == "trash"
    assert dm.restore_from_trash(outbox_doc_id2) is True
    assert dm.folder_of(outbox_doc_id2) == "outbox"

def test_restore_version(mock_doc_manager):
    dm = mock_doc_manager
    doc_id = dm.create("Test Restore", "# Test Restore\n\nInitial Content")
    
    # Save a version
    dm._save_version(doc_id, "# Test Restore\n\nVersion Content")
    versions = dm.list_versions(doc_id)
    assert len(versions) == 1
    version_file = versions[0]["version_file"]
    
    # Execute the plan so it moves to outbox
    outbox_doc_id = dm.move_to_outbox(doc_id, "## Outcome\n\nExecuted")
    assert dm.folder_of(outbox_doc_id) == "outbox"
    
    # Restore version using outbox doc_id
    assert dm.restore_version(outbox_doc_id, version_file) is True
    
    # Verify the document is back in the inbox under the original ID, with original content
    assert dm.folder_of(outbox_doc_id) is None
    assert dm.folder_of(doc_id) == "inbox"
    # ...and, since the plan ran, a previously_executed marker: restoring an older text does not undo that it
    # ran, so executing it again is a re-run (test_plan_recovered_rerun.py)
    from advisor.governance_docs import _PREV_EXEC_RE, previously_executed
    restored = dm.load(doc_id)
    assert previously_executed(restored).endswith(f"· as {outbox_doc_id}")
    assert _PREV_EXEC_RE.sub("", restored) == "# Test Restore\n\nVersion Content"
