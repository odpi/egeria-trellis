"""`_check_level`'s "This ran; no summary reader exists yet for its
results." note must not appear alongside a `NOTHING_FOUND` fact's own
answer sentence -- the exact contradiction the owner's gate found live
(2026-09-26): a card showed BOTH "This ran; no summary reader exists yet
for its results." AND "This analysis ran and found nothing — a measured
zero, not a gap in coverage."

Cause: `readEnvelope` (`app.js`) renders a synthesized sentence for a
`NOTHING_FOUND` fact with no headline/prose -- "`<analysis_id>` ran and
found nothing." -- ahead of the headline/prose/scalar rungs
`FactLayer._renders_text` checks. `_renders_text` didn't know about that
rung, so it concluded nothing rendered for a headline-less `NOTHING_FOUND`
fact and `_check_level` added its own note on top of an answer that, on
screen, already had one.
"""
from __future__ import annotations

from resource_explorer.facts import Envelope, Fact, FactLayer
from resource_explorer.surveyors.result_status import MEASURED, NOTHING_FOUND


class TestRendersTextRecognizesNothingFound:
    def test_a_nothing_found_fact_with_no_headline_still_renders(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="db_activity_signals", state=NOTHING_FOUND, value={})
        assert fl._renders_text(f) is True

    def test_a_nothing_found_fact_with_a_headline_also_renders(self):
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="x", state=NOTHING_FOUND, value={}, headline="Nothing detected.")
        assert fl._renders_text(f) is True

    def test_a_measured_fact_with_no_renderable_value_still_gates(self):
        """The fix must not accidentally make EVERY fact render -- only
        NOTHING_FOUND gets the free pass, matching readEnvelope's own
        special case for that one state."""
        fl = FactLayer.__new__(FactLayer)
        f = Fact(analysis_id="db_resilience", state=MEASURED, value={
            "replication": {"is_in_recovery": False, "replicas": []},
        })
        assert fl._renders_text(f) is False


class TestCheckLevelDoesNotDoubleReportANothingFoundFact:
    def _fact_layer(self, monkeypatch, catalog):
        monkeypatch.setattr(
            "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
            lambda resource_type, **kwargs: catalog,
        )
        fl = FactLayer.__new__(FactLayer)
        fl.resource_type = "database"
        return fl

    def test_a_nothing_found_analysis_with_no_headline_gets_no_extra_note(self, monkeypatch):
        """The exact live scenario: db_activity_signals/db_resilience
        resolved to NOTHING_FOUND from all-empty operations sub-sections,
        no headline (stale pre-#300 stored data). Must not ALSO carry
        _check_level's "no summary reader" note -- readEnvelope already
        renders "ran and found nothing" for this state on its own."""
        fl = self._fact_layer(monkeypatch, [
            {"id": "db_activity_signals", "target_shape": "whole_resource_only"},
        ])
        env = Envelope(subject="coco_pharma")
        env.facts = [Fact(analysis_id="db_activity_signals", state=NOTHING_FOUND, value={})]
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is False
        assert env.level_note == ""

    def test_a_genuinely_unrenderable_measured_fact_still_gets_the_note(self, monkeypatch):
        """Contrast case: a real MEASURED fact with nothing renderable (the
        original db_resilience bug, slice 17c) must still gate -- the fix
        is scoped to NOTHING_FOUND specifically, not a blanket suppression."""
        fl = self._fact_layer(monkeypatch, [
            {"id": "db_resilience", "target_shape": "whole_resource_only"},
        ])
        env = Envelope(subject="coco_pharma")
        env.facts = [Fact(analysis_id="db_resilience", state=MEASURED, value={
            "replication": {"is_in_recovery": False, "replicas": []},
        })]
        fl._check_level(env, {"levels": ["resource"]})
        assert env.level_mismatch is True
        assert "no summary reader" in env.level_note
