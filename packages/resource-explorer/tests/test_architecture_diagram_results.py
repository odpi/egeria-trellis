"""architecture_diagram (repo_survey_definition_adapter.py).

Rewritten 2026-09-08 for the move to READ-TIME rendering (docs/curated-
architecture-answers-design.md §6 items 2-4): `_architecture_diagram_
results` no longer reads a pre-rendered Mermaid blob `arch_recovery/
persist.py::_persist_diagram` used to write at survey time. Instead
`_read_arch_recovery_ir` reconstructs the same `Component`/ports/wires shape
from the `architecture_recovery`/`architecture_interfaces` findings tables
on every read, and `mermaid.render`/`caption` run fresh against it — so a
curator's verdict is reflected without a re-survey.

This file pins: the never-run/live-read shape (unchanged from the earlier
2026-09-08 fix), the two-perspective (detect/coupling) split (also
unchanged in observable behaviour, now sourced differently), and the new
verdict-aware filtering/styling this move exists to enable.
"""
from __future__ import annotations

import json

from tests.recovery_fake_support import RecoveryBulkSurface
from resource_explorer.surveyors.repo_survey_definition_adapter import (
    ANALYSIS_KINDS,
    _architecture_diagram_headline,
    _architecture_diagram_results,
)


def _component_row(scope, slug, name, type_=None, confidence=80, run_label="coupling",
                    blueprint="", perspective="physical", parent_slug="", depth=0,
                    surveyed_at="2026-09-08T00:00:00"):
    """One `check_name="component"` finding row, matching exactly what
    `arch_recovery/persist.py`'s `persist_ir` writes into `detail` (name,
    slug, identity, run_label, blueprint, proposed_by, perspective,
    confidence_level, parent_slug, depth) — the read side reconstructs a
    real `Component` from these same keys, so a field missing here that the
    write side actually sends is a test gap, not a simplification."""
    return {
        "check_name": "component", "label": type_ or "Unclassified",
        "summary": f"{name} ({type_ or 'untyped'})", "confidence": confidence,
        "surveyed_at": surveyed_at,
        "detail_json": json.dumps({
            "name": name, "slug": slug, "type": type_,
            "identity": {"method": "module-path", "value": slug, "deployment_context": ""},
            "run_label": run_label, "files": [], "blueprint": blueprint,
            "proposed_by": [run_label], "perspective": perspective,
            "confidence_level": "Derived", "parent_slug": parent_slug, "depth": depth,
        }),
    }


def _withdrawal_row(surveyed_at="2026-09-08T00:00:00"):
    from resource_explorer.registry import WITHDRAWN_LABEL
    return {"check_name": "component", "label": WITHDRAWN_LABEL,
            "summary": "withdrawn", "confidence": 0, "surveyed_at": surveyed_at,
            "detail_json": json.dumps({})}


def _port_row(component, name, direction="Input", protocol="tcp"):
    return {"check_name": f"port:{name}", "surveyed_at": "2026-09-08T00:00:00",
            "detail_json": json.dumps({"kind": "port", "component": component,
                                       "port": name, "direction": direction,
                                       "protocol": protocol})}


def _wire_row(source, target, one_way=True):
    return {"check_name": f"wire:{target}", "surveyed_at": "2026-09-08T00:00:00",
            "detail_json": json.dumps({"kind": "wire", "source": source,
                                       "target": target, "oneWay": one_way})}


class _Reg(RecoveryBulkSurface):
    """`component_rows_by_scope`: {scope_locator: [row, ...]} — mirrors
    `query_findings_all_runs`' real per-scope shape, ACROSS runs, so a test
    can put two rows (different run_label, or different surveyed_at) under
    one scope the way `repo_arch_detect` and `repo_arch_coupling` really
    do."""

    def __init__(self, component_rows_by_scope=None, interface_rows=(),
                 verdicts=None, blueprint_rows=()):
        self._by_scope = component_rows_by_scope or {}
        self._interface_rows = list(interface_rows)
        self._verdicts = verdicts or {}
        self._blueprint_rows = list(blueprint_rows)

    def _bulk_scopes(self, slug, kind):
        return list(self._by_scope) if kind == "architecture_recovery" else []

    def list_settings_with_prefix(self, prefix):
        # The diagram asks which services are referenced-only (ports on the edge); nothing was reclassified.
        return []

    def query_finding_scopes(self, slug, kind, check_name=None, include_withdrawn=False):
        if kind == "architecture_recovery" and check_name == "component":
            # A scope counts as live only when SOME row at it is not a bare
            # withdrawal -- mirrors the real method's "newest instant"
            # semantics closely enough for these tests (not a full
            # reimplementation of query_finding_scopes' own SQL).
            from resource_explorer.registry import WITHDRAWN_LABEL
            return sorted(s for s, rows in self._by_scope.items()
                         if any(r.get("label") != WITHDRAWN_LABEL for r in rows))
        return []

    def query_findings_all_runs(self, slug, kind, scope_locator):
        if kind == "architecture_recovery":
            return self._by_scope.get(scope_locator, [])
        if kind == "architecture_blueprints":
            return self._blueprint_rows
        return []

    def query_findings(self, slug, kind, scope_locator=""):
        if kind == "architecture_interfaces":
            return self._interface_rows
        return []

    def get_component_verdicts(self, entity_type, entity_slug):
        return self._verdicts


def _verdict(verdict="accepted", target="component"):
    return {"verdict": verdict, "verdict_target": target, "retyped_to": "",
            "note": "", "created_at": "2026-09-08T00:00:00"}


class TestNeverRun:
    def test_no_components_at_all_reports_never_run(self):
        r = _architecture_diagram_results(_Reg(), "acme-widget")
        assert r["_status"]["state"] == "never_run"

    def test_never_run_is_not_content(self):
        """Regression: an earlier version of this reader returned
        {"state": ..., "message": ...} at the top level, which facts.py's
        `_has_content` does NOT exempt as envelope metadata -- combined
        with `live_read=True`, that made a genuinely-absent diagram report
        MEASURED with a fake headline instead of never-run."""
        from resource_explorer.facts import _has_content

        r = _architecture_diagram_results(_Reg(), "acme-widget")
        assert _has_content(r) is False

    def test_only_withdrawn_rows_still_reports_never_run(self):
        reg = _Reg(component_rows_by_scope={"a": [_withdrawal_row()]})
        r = _architecture_diagram_results(reg, "acme-widget")
        assert r["_status"]["state"] == "never_run"


class TestSinglePerspectiveRoundTrip:
    def test_a_component_renders_and_the_caption_states_the_count(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="coupling")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "Alpha" in r["mermaid"]
        assert "1 component(s) shown" in r["caption"]
        assert r["perspective"] == "coupling"
        assert r["other_perspectives_available"] == []

    def test_char_count_and_exceeds_flag_match_the_actual_mermaid(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="coupling")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        assert r["char_count"] == len(r["mermaid"])
        assert r["exceeds_renderer_limit"] is False


class TestTwoPerspectives:
    """detect and coupling propose genuinely different component sets — the
    2026-09-08 fix this file already covered, now sourced from
    `_read_arch_recovery_ir` per run_label instead of two persisted rows."""

    def test_coupling_is_preferred_when_both_are_present(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "DetectOnly", run_label="detect")],
            "svc/b": [_component_row("svc/b", "svc/b", "CouplingOnly", run_label="coupling")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        assert r["perspective"] == "coupling"
        assert "CouplingOnly" in r["mermaid"]
        assert "DetectOnly" not in r["mermaid"]
        assert r["other_perspectives_available"] == ["detect"]

    def test_detect_answers_alone_when_coupling_never_ran(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "DetectOnly", run_label="detect")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        assert r["perspective"] == "detect"
        assert r["other_perspectives_available"] == []

    def test_a_scope_only_the_other_step_currently_proposes_is_excluded(self):
        """detect withdrew a scope coupling still proposes (or never
        touched) -- detect's OWN diagram must not include it, even though
        query_finding_scopes reports the scope as live overall ("revival
        for free")."""
        reg = _Reg(component_rows_by_scope={
            "svc/a": [
                _component_row("svc/a", "svc/a", "Both", run_label="detect",
                               surveyed_at="2026-09-01T00:00:00"),
                _withdrawal_row(surveyed_at="2026-09-08T00:00:00"),
            ],
            "svc/b": [_component_row("svc/b", "svc/b", "CouplingOnly", run_label="coupling")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        # coupling is preferred and svc/a was never coupling's, so it must
        # not appear even though the scope is "live" overall.
        assert r["perspective"] == "coupling"
        assert "Both" not in r["mermaid"]
        assert "CouplingOnly" in r["mermaid"]


class TestVerdictAwareRendering:
    """docs/curated-architecture-answers-design.md §5 decisions, 2026-09-08:
    a rejected component is omitted (not just styled); an accepted/retyped
    one renders normally; anything else (pending) gets the dashed
    `pending` classDef mermaid.py's render() now supports."""

    def test_a_rejected_component_is_omitted_entirely(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Rejected", run_label="coupling")],
                "svc/b": [_component_row("svc/b", "svc/b", "Kept", run_label="coupling")],
            },
            verdicts={"svc/a": _verdict("rejected")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "Rejected" not in r["mermaid"]
        assert "Kept" in r["mermaid"]
        assert "1 component(s) shown" in r["caption"]

    def test_an_accepted_component_carries_no_pending_class(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Accepted", run_label="coupling")],
            },
            verdicts={"svc/a": _verdict("accepted")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "class " not in r["mermaid"] or "pending;" not in r["mermaid"]

    def test_a_pending_component_gets_the_dashed_class(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "Unreviewed", run_label="coupling")],
        })
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "classDef pending stroke-dasharray:4 3;" in r["mermaid"]
        assert "pending;" in r["mermaid"]

    def test_a_retyped_component_is_treated_as_reviewed_not_pending(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Retyped", run_label="coupling")],
            },
            verdicts={"svc/a": _verdict("retyped")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "pending;" not in r["mermaid"]

    def test_a_rejected_blueprint_ungroups_its_members_rather_than_deleting_them(self):
        """The clustering PROPOSAL is declined, not the components -- a
        member's own verdict (or absence of one) is unaffected."""
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Member", run_label="coupling",
                                         blueprint="auth-cluster", perspective="logical")],
            },
            verdicts={"logical::auth-cluster": _verdict("rejected", target="blueprint")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "Member" in r["mermaid"]
        assert "Blueprint: auth-cluster" not in r["mermaid"]

    def test_an_accepted_blueprint_still_groups_its_members(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Member", run_label="coupling",
                                         blueprint="auth-cluster", perspective="logical")],
            },
            verdicts={"logical::auth-cluster": _verdict("accepted", target="blueprint")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "Blueprint: auth-cluster" in r["mermaid"]

    def test_a_component_verdict_does_not_leak_into_a_blueprint_lookup(self):
        """A component verdict recorded at scope_locator "svc/a" must not be
        mistaken for a blueprint verdict at a colliding key string."""
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Member", run_label="coupling",
                                         blueprint="auth-cluster", perspective="logical")],
            },
            verdicts={"logical::auth-cluster": _verdict("rejected", target="component")},
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        # A "rejected" COMPONENT verdict recorded under the blueprint's key
        # string must not ungroup the blueprint -- verdict_target has to
        # actually be checked, not just presence-of-a-row.
        assert "Blueprint: auth-cluster" in r["mermaid"]


class TestPortsAndWiresOnlyReachDetect:
    """Only repo_arch_detect ever passes ports/wires to persist_ir
    (confirmed by reading arch_recovery_coupling.py's own call site) --
    coupling's diagram must never draw interfaces it never proposed."""

    def test_detects_diagram_carries_its_ports(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="detect")],
            },
            interface_rows=[_port_row("svc/a", "8080")],
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "8080" in r["mermaid"]

    def test_couplings_diagram_never_carries_ports(self):
        reg = _Reg(
            component_rows_by_scope={
                "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="coupling")],
            },
            interface_rows=[_port_row("svc/a", "8080")],
        )
        r = _architecture_diagram_results(reg, "acme-widget")
        assert "8080" not in r["mermaid"]


class TestHeadline:
    def test_never_run_has_no_headline(self):
        assert _architecture_diagram_headline(_Reg(), "acme-widget") is None

    def test_the_caption_becomes_the_headline_label(self):
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="coupling")],
        })
        h = _architecture_diagram_headline(reg, "acme-widget")
        r = _architecture_diagram_results(reg, "acme-widget")
        assert h["label"] == r["caption"]

    def test_an_oversized_diagram_warns_instead_of_info(self, monkeypatch):
        from resource_explorer.surveyors.arch_recovery import mermaid

        monkeypatch.setattr(mermaid, "RENDERER_CHAR_LIMIT", 10)
        reg = _Reg(component_rows_by_scope={
            "svc/a": [_component_row("svc/a", "svc/a", "Alpha", run_label="coupling")],
        })
        h = _architecture_diagram_headline(reg, "acme-widget")
        assert h["status"] == "warn"


class TestLiveRead:
    """Regression for a real bug found live 2026-09-08 against
    egeria-workspaces_git: repo_arch_detect/repo_arch_coupling running gets
    recorded under analysis_id "architecture_recovery" (that AnalysisKind's
    own id), never under "architecture_diagram" -- so FactLayer.fact()'s
    run-attribution gate (facts.py, keyed by analysis_id) never sees this id
    as having run, and reports NEVER_RUN even with real data available.
    `live_read=True` is the documented escape hatch for exactly this shape
    (AnalysisKindResults.live_read's own docstring, api_structure's
    2026-09-02 precedent). Unaffected by the move to read-time rendering —
    pinned again here so a future edit can't silently drop it."""

    def test_architecture_diagram_is_declared_live_read(self):
        kind = ANALYSIS_KINDS["architecture_diagram"]
        assert kind.results.live_read is True
