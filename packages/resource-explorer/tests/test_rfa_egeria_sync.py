"""Tests for rfa_egeria_sync.py — docs/rfa-egeria-todo-followup.md's "Sync
mechanics". Every pyegeria client is mocked; these tests verify the local
side of the contract (never raises, records success/failure correctly,
selects the right rows for each reconciliation direction) not pyegeria's
real network behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.registry import ProjectRegistry
from resource_explorer.rfa_egeria_sync import (
    _extract_todo_fields,
    reconcile_rfa_actions,
    sync_rfa_action,
    sync_rfa_note,
)


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


def _row(registry, rfa_id="e1::0", **overrides):
    defaults = dict(status="open", activity_status="REQUESTED")
    defaults.update(overrides)
    registry.upsert_rfa_action(rfa_id, "e1", 0, **defaults)
    return registry.get_rfa_action(rfa_id)


class TestSyncRfaAction:
    def test_creates_todo_when_never_synced(self, registry):
        row = _row(registry)
        my_profile = MagicMock()
        my_profile.create_my_todo.return_value = "new-todo-guid"
        metadata_expert = MagicMock()

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, metadata_expert)):
            sync_rfa_action(registry, row)

        my_profile.create_my_todo.assert_called_once()
        my_profile.update_asset.assert_not_called()
        updated = registry.get_rfa_action("e1::0")
        assert updated["egeria_todo_guid"] == "new-todo-guid"
        assert updated["synced_at"]
        assert updated["sync_error"] == ""

    def test_updates_todo_when_already_synced(self, registry):
        # Real bug fixed 2026-08-16 (live verification, round 1):
        # MetadataExpert.update_metadata_element_properties() (the generic
        # metadata-element endpoint) silently no-ops against a
        # ToDoProperties-shaped body — it needs each value individually
        # typed via propertyValueMap instead. Fixed a second time (round 2,
        # same day, per direct guidance): switched to
        # MyProfile.update_asset() instead — a ToDo is an Asset subtype, so
        # the Asset-specific update endpoint applies, confirmed live to
        # accept the same plain, typeName-tagged shape create_my_todo()
        # already uses, with correct partial-update semantics.
        row = _row(registry, activity_status="COMPLETED")
        registry.mark_rfa_synced("e1::0", "existing-guid")
        row = registry.get_rfa_action("e1::0")
        my_profile = MagicMock()
        metadata_expert = MagicMock()

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, metadata_expert)):
            sync_rfa_action(registry, row)

        my_profile.create_my_todo.assert_not_called()
        my_profile.update_asset.assert_called_once()
        args, _ = my_profile.update_asset.call_args
        assert args[0] == "existing-guid"
        properties = args[1]["properties"]
        assert args[1]["class"] == "UpdateElementRequestBody"
        assert properties["class"] == "ToDoProperties"
        assert properties["typeName"] == "ToDo"
        assert properties["activityStatus"] == "COMPLETED"
        assert properties["priority"] == 0

    def test_due_time_only_included_when_set(self, registry):
        row = _row(registry, defer_until="2026-09-01")
        registry.mark_rfa_synced("e1::0", "guid")
        row = registry.get_rfa_action("e1::0")
        my_profile = MagicMock()

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            sync_rfa_action(registry, row)

        _, body = my_profile.update_asset.call_args[0]
        # update_asset accepts a plain, human-readable date string directly
        # (server-side converts to epoch millis) — confirmed live 2026-08-16.
        assert body["properties"]["dueTime"] == "2026-09-01"

    def test_due_time_omitted_when_empty(self, registry):
        row = _row(registry)
        registry.mark_rfa_synced("e1::0", "guid")
        row = registry.get_rfa_action("e1::0")
        my_profile = MagicMock()

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            sync_rfa_action(registry, row)

        _, body = my_profile.update_asset.call_args[0]
        assert "dueTime" not in body["properties"]

    def test_failure_never_raises_and_records_sync_error(self, registry):
        row = _row(registry)
        with patch("resource_explorer.rfa_egeria_sync._get_clients", side_effect=RuntimeError("unreachable")):
            sync_rfa_action(registry, row)  # must not raise

        updated = registry.get_rfa_action("e1::0")
        assert updated["egeria_todo_guid"] == ""
        assert "unreachable" in updated["sync_error"]

    def test_client_construction_error_also_caught(self, registry):
        row = _row(registry)
        my_profile = MagicMock()
        my_profile.create_my_todo.side_effect = RuntimeError("server 500")

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            sync_rfa_action(registry, row)  # must not raise

        updated = registry.get_rfa_action("e1::0")
        assert "server 500" in updated["sync_error"]


class TestSyncRfaNote:
    """Per direct decision (2026-08-16): a note only syncs to Egeria (as an
    ActivityEntry, via a NoteLog anchored on the RFA's own ToDo) once that
    ToDo already exists — no forcing a ToDo into being just to hang a note
    off it."""

    def test_skipped_when_no_todo_guid_yet(self, registry):
        registry.upsert_rfa_note("e1::0", "e1", 0, "a note with nowhere to go yet")
        row = registry.get_rfa_action("e1::0")
        with patch("resource_explorer.rfa_egeria_sync._get_clients") as mock_get_clients:
            sync_rfa_note(registry, row)
        mock_get_clients.assert_not_called()
        assert registry.get_rfa_action("e1::0")["notes_synced_value"] == ""

    def test_skipped_when_notes_empty(self, registry):
        registry.upsert_rfa_action("e1::0", "e1", 0, status="open", activity_status="REQUESTED")
        registry.mark_rfa_synced("e1::0", "todo-guid")
        row = registry.get_rfa_action("e1::0")
        with patch("resource_explorer.rfa_egeria_sync._get_clients") as mock_get_clients:
            sync_rfa_note(registry, row)
        mock_get_clients.assert_not_called()

    def test_creates_notelog_once_then_reuses_it(self, registry):
        registry.upsert_rfa_action("e1::0", "e1", 0, status="open", activity_status="REQUESTED")
        registry.mark_rfa_synced("e1::0", "todo-guid")
        registry.upsert_rfa_note("e1::0", "e1", 0, "first note")
        my_profile = MagicMock()
        my_profile.create_note_log.return_value = "notelog-guid"
        my_profile.make_feedback_qn.return_value = "qn"

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            sync_rfa_note(registry, registry.get_rfa_action("e1::0"))

        my_profile.create_note_log.assert_called_once_with(
            element_guid="todo-guid",
            display_name="RFA notes: e1::0",
            description="Notes recorded against this RFA from Resource Explorer.",
        )
        my_profile.create_note.assert_called_once()
        args, kwargs = my_profile.create_note.call_args
        assert args[0] == "notelog-guid"
        assert kwargs["associated_element"] == "todo-guid"
        assert kwargs["body"]["typeName"] == "ActivityEntry"
        assert kwargs["body"]["description"] == "first note"

        row = registry.get_rfa_action("e1::0")
        assert row["egeria_notelog_guid"] == "notelog-guid"
        assert row["notes_synced_value"] == "first note"

        # Second sync with a NEW note text reuses the cached NoteLog GUID.
        registry.upsert_rfa_note("e1::0", "e1", 0, "second note")
        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            sync_rfa_note(registry, registry.get_rfa_action("e1::0"))
        my_profile.create_note_log.assert_called_once()  # still just once
        assert my_profile.create_note.call_count == 2

    def test_unchanged_note_is_not_re_synced(self, registry):
        registry.upsert_rfa_action("e1::0", "e1", 0, status="open", activity_status="REQUESTED")
        registry.mark_rfa_synced("e1::0", "todo-guid")
        registry.upsert_rfa_note("e1::0", "e1", 0, "same note")
        registry.mark_rfa_note_synced("e1::0", "same note")  # already synced

        with patch("resource_explorer.rfa_egeria_sync._get_clients") as mock_get_clients:
            sync_rfa_note(registry, registry.get_rfa_action("e1::0"))
        mock_get_clients.assert_not_called()

    def test_failure_never_raises_and_records_note_sync_error(self, registry):
        registry.upsert_rfa_action("e1::0", "e1", 0, status="open", activity_status="REQUESTED")
        registry.mark_rfa_synced("e1::0", "todo-guid")
        registry.upsert_rfa_note("e1::0", "e1", 0, "a note")

        with patch("resource_explorer.rfa_egeria_sync._get_clients", side_effect=RuntimeError("unreachable")):
            sync_rfa_note(registry, registry.get_rfa_action("e1::0"))  # must not raise

        row = registry.get_rfa_action("e1::0")
        assert "unreachable" in row["notes_sync_error"]
        assert row["notes_synced_value"] == ""


class TestExtractTodoFields:
    def test_extracts_from_element_header_and_properties(self):
        raw = {
            "elementHeader": {"guid": "g1"},
            "properties": {"activityStatus": "WAITING", "dueTime": "2026-09-01", "priority": 2},
        }
        fields = _extract_todo_fields(raw)
        assert fields == {
            "guid": "g1", "activity_status": "WAITING",
            "due_time": "2026-09-01", "start_time": "", "priority": 2,
        }

    def test_extracts_from_flat_shape(self):
        raw = {"guid": "g2", "activityStatus": "COMPLETED"}
        fields = _extract_todo_fields(raw)
        assert fields["guid"] == "g2"
        assert fields["activity_status"] == "COMPLETED"

    def test_returns_none_when_no_guid_found(self):
        assert _extract_todo_fields({"properties": {"activityStatus": "WAITING"}}) is None

    def test_returns_none_on_malformed_input(self):
        assert _extract_todo_fields(None) is None  # type: ignore[arg-type]


class TestReconcileRfaActions:
    @pytest.fixture(autouse=True)
    def _open_access(self, monkeypatch):
        """Brief Z3: a retried ToDo or note write is re-checked (the no-requester rule); no zone in use here (the
        access reads faked open at the client boundary)."""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent))
        from tests.zone_fakes import open_access

        open_access(monkeypatch)

    def test_retries_unsynced_rows(self, registry):
        _row(registry, rfa_id="e1::0")
        _row(registry, rfa_id="e1::1")

        with patch("resource_explorer.rfa_egeria_sync.sync_rfa_action") as mock_sync, \
             patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(MagicMock(get_my_to_dos=lambda **_: []), MagicMock())):
            reconcile_rfa_actions(registry)

        assert mock_sync.call_count == 2

    def test_retries_unsynced_notes_independent_of_status_retries(self, registry):
        # A note added before any status action (no ToDo yet) — not
        # retried as a note (list_unsynced_rfa_notes requires a ToDo) but
        # IS retried as a status/ToDo action (that's what will eventually
        # create the ToDo the note needs).
        _row(registry, rfa_id="e1::0")
        registry.upsert_rfa_note("e1::0", "e1", 0, "note with no todo yet")

        # A different RFA that already has a ToDo and an un-synced note —
        # should be retried as a note.
        _row(registry, rfa_id="e1::1")
        registry.mark_rfa_synced("e1::1", "todo-guid-1")
        registry.upsert_rfa_note("e1::1", "e1", 1, "pending note")

        with patch("resource_explorer.rfa_egeria_sync.sync_rfa_action") as mock_sync_action, \
             patch("resource_explorer.rfa_egeria_sync.sync_rfa_note") as mock_sync_note, \
             patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(MagicMock(get_my_to_dos=lambda **_: []), MagicMock())):
            reconcile_rfa_actions(registry)

        # e1::0 unsynced (no guid) -> status retry; e1::1 already synced -> not.
        assert mock_sync_action.call_count == 1
        assert mock_sync_action.call_args[0][1]["id"] == "e1::0"
        # e1::1 has a ToDo + a pending note -> note retry; e1::0 has no ToDo yet -> not.
        assert mock_sync_note.call_count == 1
        assert mock_sync_note.call_args[0][1]["id"] == "e1::1"

    def test_read_direction_updates_local_row_on_remote_change(self, registry):
        _row(registry, rfa_id="e1::0", activity_status="REQUESTED")
        registry.mark_rfa_synced("e1::0", "guid-1")

        my_profile = MagicMock()
        my_profile.get_my_to_dos.return_value = [
            {"elementHeader": {"guid": "guid-1"}, "properties": {"activityStatus": "IN_PROGRESS"}},
        ]

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            reconcile_rfa_actions(registry)

        row = registry.get_rfa_action("e1::0")
        assert row["activity_status"] == "IN_PROGRESS"

    def test_read_direction_leaves_unchanged_row_alone(self, registry):
        _row(registry, rfa_id="e1::0", activity_status="WAITING")
        registry.mark_rfa_synced("e1::0", "guid-1")
        before = registry.get_rfa_action("e1::0")["synced_at"]

        my_profile = MagicMock()
        my_profile.get_my_to_dos.return_value = [
            {"elementHeader": {"guid": "guid-1"}, "properties": {"activityStatus": "WAITING"}},
        ]

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            reconcile_rfa_actions(registry)

        after = registry.get_rfa_action("e1::0")["synced_at"]
        assert after == before  # no spurious write when nothing actually changed

    def test_read_direction_ignores_guid_not_in_open_list(self, registry):
        _row(registry, rfa_id="e1::0", activity_status="WAITING")
        registry.mark_rfa_synced("e1::0", "guid-1")

        my_profile = MagicMock()
        my_profile.get_my_to_dos.return_value = []  # e.g. remotely completed/cancelled

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            reconcile_rfa_actions(registry)  # must not raise or wrongly clear local state

        row = registry.get_rfa_action("e1::0")
        assert row["activity_status"] == "WAITING"

    def test_malformed_remote_response_does_not_raise(self, registry):
        _row(registry, rfa_id="e1::0")
        registry.mark_rfa_synced("e1::0", "guid-1")

        my_profile = MagicMock()
        my_profile.get_my_to_dos.return_value = "not a list"

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            reconcile_rfa_actions(registry)  # must not raise

    def test_read_direction_skipped_when_nothing_synced(self, registry):
        _row(registry, rfa_id="e1::0")  # unsynced, no guid

        with patch("resource_explorer.rfa_egeria_sync._get_clients") as mock_get_clients:
            reconcile_rfa_actions(registry)

        # _get_clients is still called once for the write-direction retry
        # (sync_rfa_action itself), but never again for a read pull that
        # has nothing synced to reconcile.
        assert mock_get_clients.call_count == 1

    def test_write_direction_failure_does_not_block_read_direction(self, registry):
        _row(registry, rfa_id="e1::0")  # will be retried (unsynced)
        _row(registry, rfa_id="e1::1", activity_status="WAITING")
        registry.mark_rfa_synced("e1::1", "guid-1")  # already synced

        my_profile = MagicMock()
        my_profile.create_my_todo.side_effect = RuntimeError("boom")
        my_profile.get_my_to_dos.return_value = [
            {"elementHeader": {"guid": "guid-1"}, "properties": {"activityStatus": "IN_PROGRESS"}},
        ]

        with patch("resource_explorer.rfa_egeria_sync._get_clients", return_value=(my_profile, MagicMock())):
            reconcile_rfa_actions(registry)  # must not raise

        assert registry.get_rfa_action("e1::1")["activity_status"] == "IN_PROGRESS"
