"""_architecture_verdict_coverage / _architecture_verdict_coverage_sentence
(repo_survey_definition_adapter.py) — new 2026-09-08.

docs/curated-architecture-answers-design.md §6 item 1, the first piece of
that design to ship: every architecture chat answer (component list,
summary, diagram) used to read identically whether zero components had ever
been curator-reviewed or all of them had. `_verdict_view` was already
merging a verdict onto each component `_architecture_recovery_results`
returns, but nothing rolled that up into a coverage figure a reader could
see without counting bullets themselves.

This pins the coverage arithmetic itself, and the two places the sentence
is spliced in (`_architecture_recovery_headline`'s label,
`_architecture_diagram_results`'s caption) WITHOUT overriding the existing
answer text the way a `*_detail`-named key would have (see both call
sites' own comments for why that trap applies here).
"""
from __future__ import annotations

import json

from tests.recovery_fake_support import RecoveryBulkSurface
from resource_explorer.surveyors.repo_survey_definition_adapter import (
    _architecture_diagram_results,
    _architecture_recovery_headline,
    _architecture_verdict_coverage,
    _architecture_verdict_coverage_sentence,
)


class _Reg(RecoveryBulkSurface):
    """Enough of ProjectRegistry's surface for the coverage function and
    the two call sites that splice its sentence in."""

    def __init__(self, component_scopes=(), verdicts=None, blueprint_rows=(),
                 recovery_rows_by_scope=None, diagram_rows=()):
        self._component_scopes = list(component_scopes)
        self._verdicts = verdicts or {}
        self._blueprint_rows = list(blueprint_rows)
        self._recovery_rows_by_scope = recovery_rows_by_scope or {}
        self._diagram_rows = list(diagram_rows)

    def _bulk_scopes(self, slug, kind):
        return list(self._recovery_rows_by_scope) if kind == "architecture_recovery" else []

    def list_settings_with_prefix(self, prefix):
        # The diagram asks which services are referenced-only (ports on the edge); nothing was reclassified.
        return []

    def query_finding_scopes(self, slug, kind, check_name=None):
        if kind == "architecture_recovery" and check_name == "component":
            return self._component_scopes
        return []

    def get_component_verdicts(self, entity_type, entity_slug):
        return self._verdicts

    def query_findings_all_runs(self, slug, kind, scope_locator):
        if kind == "architecture_blueprints":
            return self._blueprint_rows
        if kind == "architecture_diagram":
            return self._diagram_rows
        if kind == "architecture_recovery" and scope_locator in self._recovery_rows_by_scope:
            return self._recovery_rows_by_scope[scope_locator]
        return []

    def query_findings(self, slug, kind, scope_locator=""):
        return []

    def get_materialized_components(self, entity_type, entity_slug):
        return {}

    def query_metrics(self, slug, kind, scope_locator=""):
        return {}


def _verdict(verdict="accepted", verdict_target="component"):
    return {"verdict": verdict, "verdict_target": verdict_target,
            "retyped_to": "", "note": "", "created_at": "2026-09-08T00:00:00"}


def _bp_row(perspective, label, surveyed_at="2026-09-01T00:00:00"):
    # detail_json is stored as TEXT in the real table (_json_or_empty calls
    # json.loads on it) -- a raw dict here would silently fail to parse
    # (json.loads(dict) raises TypeError, caught, returns {}), making every
    # row look perspective-less and defeating the dedup key entirely.
    return {
        "check_name": "candidate_blueprint", "label": label,
        "surveyed_at": surveyed_at,
        "detail_json": json.dumps({"perspective": perspective, "name": label}),
    }


class TestComponentCoverage:
    def test_nothing_proposed_is_all_zeros(self):
        cov = _architecture_verdict_coverage(_Reg(), "acme-widget")
        assert cov["components"] == {
            "total": 0, "accepted": 0, "rejected": 0, "retyped": 0,
            "reviewed": 0, "pending": 0,
        }

    def test_unreviewed_components_are_all_pending(self):
        reg = _Reg(component_scopes=["a", "b", "c"])
        cov = _architecture_verdict_coverage(reg, "acme-widget")
        assert cov["components"] == {
            "total": 3, "accepted": 0, "rejected": 0, "retyped": 0,
            "reviewed": 0, "pending": 3,
        }

    def test_mixed_verdicts_are_bucketed_correctly(self):
        reg = _Reg(
            component_scopes=["a", "b", "c", "d", "e"],
            verdicts={
                "a": _verdict("accepted"), "b": _verdict("accepted"),
                "c": _verdict("rejected"), "d": _verdict("retyped"),
                # "e" has no verdict -- stays pending.
            },
        )
        cov = _architecture_verdict_coverage(reg, "acme-widget")["components"]
        assert cov == {"total": 5, "accepted": 2, "rejected": 1, "retyped": 1,
                       "reviewed": 4, "pending": 1}

    def test_a_blueprint_verdict_does_not_leak_into_the_component_bucket(self):
        """Regression: without the verdict_target check, a blueprint verdict
        whose scope_locator string happened to equal a component's scope
        (unlikely, but the two key spaces are not enforced disjoint) would
        silently count as a component verdict too."""
        reg = _Reg(
            component_scopes=["logical::svc"],
            verdicts={"logical::svc": _verdict("accepted", verdict_target="blueprint")},
        )
        cov = _architecture_verdict_coverage(reg, "acme-widget")["components"]
        assert cov["accepted"] == 0
        assert cov["pending"] == 1


class TestBlueprintCoverage:
    def test_nothing_clustered_is_all_zeros(self):
        cov = _architecture_verdict_coverage(_Reg(), "acme-widget")
        assert cov["blueprints_by_perspective"] == {}

    def test_blueprints_are_deduped_by_perspective_and_label(self):
        """Two runs (repo_arch_detect and repo_arch_coupling both cluster
        their own component set) can each write a row for the same
        (perspective, label) -- only the latest should count, the same
        collision class the 2026-09-08 diagram fix addressed."""
        reg = _Reg(blueprint_rows=[
            _bp_row("logical", "auth-service", surveyed_at="2026-08-01T00:00:00"),
            _bp_row("logical", "auth-service", surveyed_at="2026-09-08T00:00:00"),
            _bp_row("physical", "auth-service", surveyed_at="2026-09-08T00:00:00"),
        ])
        cov = _architecture_verdict_coverage(reg, "acme-widget")["blueprints_by_perspective"]
        # Each reading counted on its own -- a cluster only exists within one.
        assert cov["logical"]["total"] == 1
        assert cov["physical"]["total"] == 1

    def test_blueprint_verdict_keys_join_on_perspective_and_label(self):
        reg = _Reg(
            blueprint_rows=[_bp_row("logical", "auth-service")],
            verdicts={"logical::auth-service": _verdict("accepted", verdict_target="blueprint")},
        )
        cov = _architecture_verdict_coverage(reg, "acme-widget")["blueprints_by_perspective"]["logical"]
        assert cov == {"total": 1, "accepted": 1, "rejected": 0, "retyped": 0,
                       "reviewed": 1, "pending": 0}

    def test_a_component_verdict_does_not_leak_into_the_blueprint_bucket(self):
        reg = _Reg(
            blueprint_rows=[_bp_row("logical", "auth-service")],
            verdicts={"logical::auth-service": _verdict("accepted", verdict_target="component")},
        )
        cov = _architecture_verdict_coverage(reg, "acme-widget")["blueprints_by_perspective"]["logical"]
        assert cov["accepted"] == 0
        assert cov["pending"] == 1


class TestCoverageSentence:
    def test_nothing_proposed_is_a_blank_sentence(self):
        assert _architecture_verdict_coverage_sentence(
            {"components": {"total": 0}, "blueprints_by_perspective": {}}) == ""

    def test_zero_total_clause_is_omitted_not_stated_as_zero_of_zero(self):
        cov = {"components": {"total": 5, "accepted": 0, "rejected": 0,
                              "retyped": 0, "reviewed": 0, "pending": 5},
               "blueprints_by_perspective": {}}
        sentence = _architecture_verdict_coverage_sentence(cov)
        assert "cluster" not in sentence
        assert sentence == "0 of 5 component paths reviewed"

    def test_the_full_sentence_names_every_outcome_and_reading(self):
        cov = {"components": {"total": 109, "accepted": 18, "rejected": 6,
                              "retyped": 1, "reviewed": 25, "pending": 84},
               "blueprints_by_perspective": {
                   "logical": {"total": 5, "accepted": 2, "rejected": 0,
                               "retyped": 0, "reviewed": 2, "pending": 3},
               }}
        sentence = _architecture_verdict_coverage_sentence(cov)
        assert sentence == (
            "25 of 109 component paths reviewed (18 accepted, 6 rejected, 1 retyped); "
            "2 of 5 clusters in the logical reading reviewed (2 accepted)"
        )

    def test_each_reading_gets_its_own_clause(self):
        cov = {"components": {"total": 0},
               "blueprints_by_perspective": {
                   "logical": {"total": 12, "accepted": 0, "rejected": 0,
                               "retyped": 0, "reviewed": 0, "pending": 12},
                   "physical": {"total": 3, "accepted": 1, "rejected": 0,
                                "retyped": 0, "reviewed": 1, "pending": 2},
               }}
        sentence = _architecture_verdict_coverage_sentence(cov)
        assert sentence == (
            "0 of 12 clusters in the logical reading reviewed; "
            "1 of 3 clusters in the physical reading reviewed (1 accepted)"
        )

    def test_singular_noun_for_one(self):
        cov = {"components": {"total": 1, "accepted": 0, "rejected": 0,
                              "retyped": 0, "reviewed": 0, "pending": 1},
               "blueprints_by_perspective": {}}
        assert "1 component path" in _architecture_verdict_coverage_sentence(cov)
        assert "1 component paths" not in _architecture_verdict_coverage_sentence(cov)


class TestSplicedIntoAnswers:
    """The sentence must reach the chat answer WITHOUT overriding it —
    both call sites deliberately avoid a `*_detail`-named key, which
    `_factProseKeys` (index.html) would promote to REPLACE `f.headline`
    outright rather than add to it."""

    def test_recovery_headline_states_coverage_alongside_the_count(self, monkeypatch):
        """_architecture_recovery_results' own machinery (structural nodes,
        interfaces, documentation state, blueprint clustering...) is heavy
        enough that it has its own coverage elsewhere -- this test is about
        ONE thing: that the headline splices the coverage sentence onto the
        count rather than replacing it, so a full fake registry for the
        whole reader would test the wrong thing at high cost. Patching the
        reader itself keeps the test aimed at the splice."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as mod

        monkeypatch.setattr(mod, "_architecture_recovery_results", lambda registry, slug: {
            "surveyed_at": "2026-09-08T00:00:00",
            "unverified": [],
            "component_count": 2,
            "verdict_coverage": {
                "components": {"total": 2, "accepted": 1, "rejected": 0,
                               "retyped": 0, "reviewed": 1, "pending": 1},
                "blueprints_by_perspective": {},
            },
        })
        h = _architecture_recovery_headline(_Reg(), "acme-widget")
        assert h is not None
        assert "2 components recovered" in h["label"]
        assert "1 of 2 component paths reviewed" in h["label"]
        assert "1 accepted" in h["label"]

    def test_diagram_caption_states_coverage_alongside_the_description(self):
        """`_architecture_diagram_results` moved to read-time reconstruction
        one commit after this file was first written (docs/curated-
        architecture-answers-design.md §6 items 2-4) -- the diagram is now
        rendered fresh from real component rows, not a persisted blob, so
        this exercises the real shape rather than a stand-in row."""
        row = {
            "check_name": "component", "label": "Software Service",
            "summary": "Alpha (Software Service)", "confidence": 80,
            "surveyed_at": "2026-09-08T00:00:00",
            "detail_json": json.dumps({
                "name": "Alpha", "slug": "a", "type": "Software Service",
                "identity": {"method": "module-path", "value": "a", "deployment_context": ""},
                "run_label": "coupling", "files": [], "blueprint": "",
                "proposed_by": ["coupling"], "perspective": "physical",
                "confidence_level": "Derived", "parent_slug": "", "depth": 0,
            }),
        }
        reg = _Reg(
            component_scopes=["a"],
            recovery_rows_by_scope={"a": [row]},
            verdicts={"a": _verdict("accepted")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "1 component(s) shown" in r["caption"]
        assert "1 of 1 component path reviewed (1 accepted)" in r["caption"]

    def test_diagram_caption_omits_the_blueprint_clause_when_nothing_clustered(self):
        """Coverage's own zero-total omission (TestCoverageSentence above)
        must survive being spliced into the diagram's caption too -- a
        repo with components but no blueprint clustering must not read
        "0 of 0 blueprints reviewed"."""
        row = {
            "check_name": "component", "label": "Software Service",
            "summary": "Alpha (Software Service)", "confidence": 80,
            "surveyed_at": "2026-09-08T00:00:00",
            "detail_json": json.dumps({
                "name": "Alpha", "slug": "a", "type": "Software Service",
                "identity": {"method": "module-path", "value": "a", "deployment_context": ""},
                "run_label": "coupling", "files": [], "blueprint": "",
                "proposed_by": ["coupling"], "perspective": "physical",
                "confidence_level": "Derived", "parent_slug": "", "depth": 0,
            }),
        }
        reg = _Reg(component_scopes=["a"], recovery_rows_by_scope={"a": [row]})
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "cluster" not in r["caption"]
        assert "0 of 1 component path reviewed" in r["caption"]
