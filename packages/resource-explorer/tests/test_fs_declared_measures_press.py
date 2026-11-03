"""The file-system publish lift (2026-10-29): the folder-survey Publish carries the measures chosen on Curate's table
inside Resource Explorer's own file-system survey report, reads them back by name, and leaves a proof row per measure
that the table's "published · read back <time>" word comes from. Only the Egeria boundary is faked; nothing is written
to Egeria."""
from __future__ import annotations

import pytest

from resource_explorer import publish_table as pt
from tests.test_files2_fix_round_1 import (  # noqa: F401 - fixtures and helpers reused
    _fake_surveyor,
    _re_led_walk,
    api,
    registry,
)

ROW = {"analysis_id": "fs_permissions", "analysis": "File permissions", "measure": "fs_permissions", "locator": "fs_permissions/fs_permissions",
       "value": 134, "value_words": "134 files", "unit": "files", "as_of": "2026-10-29T00:14:46", "value_field": "files",
       "scope": "resource", "confidence": {"form": "measured", "method": "walk"},
       "writes_as": {"route": "generic", "type": "ResourceMeasureAnnotation", "words": pt.GENERIC_WORDS,
                     "writable": True}}


def _declared(rows):
    return {"state": "sent" if rows else "none_chosen", "rows": rows, "annotations": [pt.annotation_for(r) for r in rows]}


def _with_read_back(monkeypatch, found: bool):
    from resource_explorer.surveyors.filesystem import egeria_filesystem_surveyor as mod

    def read_back(self, report_guid, annotations, slug, at):
        """The report read: every annotation found by name, or none of them."""
        return {i: f"ann-{i}" for i in range(len(annotations))} if found else {}

    monkeypatch.setattr(mod.EgeriaFileSystemSurveyor, "_read_back_guids", read_back)


@pytest.mark.usefixtures("signed_in_caller")
def test_a_chosen_measure_rides_in_the_report_and_leaves_a_read_back_proof(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=None)
    _with_read_back(monkeypatch, found=True)
    monkeypatch.setattr(pt, "annotations_for", lambda reg, slug, entity_type="database": _declared([ROW]))
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["published"] is True
    assert body["declared_measures"]["sent"] == 1 and body["declared_measures"]["read_back"] == 1
    assert body["declared_measures"]["words"] == "1 declared measure read back"
    proofs = pt.published_states(registry, "walkme", "filesystem")
    proof = proofs["fs_permissions/fs_permissions"]
    assert proof["proof"] == pt.P_MEASURE_READ_BACK and proof["element_guid"].startswith("ann-")
    assert proof["detail"]["type"] == "ResourceMeasureAnnotation" and proof["detail"]["route"] == "generic"
    assert pt.published_states(registry, "walkme", "database") == {}


@pytest.mark.usefixtures("signed_in_caller")
def test_a_measure_the_report_read_does_not_show_is_sent_not_read_back(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=None)
    _with_read_back(monkeypatch, found=False)
    monkeypatch.setattr(pt, "annotations_for", lambda reg, slug, entity_type="database": _declared([ROW]))
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["declared_measures"]["words"] == "0 of 1 declared measure read back"
    proof = pt.published_states(registry, "walkme", "filesystem")["fs_permissions/fs_permissions"]
    assert proof["proof"] == pt.P_MEASURE_NOT_READ_BACK


@pytest.mark.usefixtures("signed_in_caller")
def test_nothing_chosen_sends_and_records_nothing(api, registry, tmp_path, monkeypatch):
    client, headers = api
    _re_led_walk(client, headers, registry, tmp_path)
    _fake_surveyor(monkeypatch, landed=None)
    body = client.post("/api/filesystems/walkme/folder-survey/publish", headers=headers).json()
    assert body["declared_measures"]["sent"] == 0 and "words" not in body["declared_measures"]
    assert pt.published_states(registry, "walkme", "filesystem") == {}
