"""The engine host judges a publish on THIS publish's annotations, not on all a reused report holds (2026-10-20).

On a reused report `annotations_in_egeria` also counts earlier verdict batches, so it can exceed what this publish
sent: "14 of 9 reached Egeria" for a partial failure, or a pass that should have failed.
"""
from __future__ import annotations

import pytest

from resource_explorer import engine_host
from resource_explorer.surveyors.database.egeria_database_surveyor import EgeriaDatabaseSurveyor


class _Registry:
    def get_database(self, slug, allow_unreadable=False):
        return type("Entity", (), {"slug": slug, "egeria_asset_guid": "db-1", "display_name": slug})()


@pytest.fixture
def publish(monkeypatch):
    def run(pub: dict):
        from resource_explorer import egeria_clients

        monkeypatch.setattr(egeria_clients, "current_principal",
                            lambda: type("P", (), {"client_user": "re-engine", "requested_by": "dan"})())
        monkeypatch.setattr(EgeriaDatabaseSurveyor, "__init__", lambda self, **k: None)
        monkeypatch.setattr(EgeriaDatabaseSurveyor, "publish_engine_action_report",
                            lambda self, *a, **k: {"report_guid": "report-1", "governance": {"ownership": True}, **pub})
        return engine_host._publish(_Registry(), {"slug": "coco_ods", "request_type": "survey-db"}, ["a"] * 9)
    return run


def test_a_partial_publish_into_a_reused_report_fails_on_its_own_count(publish):
    out = publish({"reused": True, "annotations_in_egeria": 14, "annotations_this_publish": 7,
                   "annotation_error": "2 of 9 annotations of this publish could not be published"})
    assert out["guard"] == engine_host.SURVEY_FAILED
    assert "7 of 9 reached Egeria" in out["message"]


def test_a_complete_publish_into_a_reused_report_completes(publish):
    out = publish({"reused": True, "annotations_in_egeria": 18, "annotations_this_publish": 9})
    assert out["guard"] == engine_host.SURVEY_COMPLETED


def test_a_reused_report_whose_create_raised_could_not_be_counted(publish):
    out = publish({"reused": True, "annotations_in_egeria": 9, "annotations_this_publish": None,
                   "annotation_error": "SERVER_ERROR_500"})
    assert out["guard"] == engine_host.SURVEY_FAILED and "could not be counted" in out["message"]
