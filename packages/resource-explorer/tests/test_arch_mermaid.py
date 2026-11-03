"""Tests for the publish-time Mermaid renderer (arch_recovery/mermaid.py).

The renderer exists because a curator may decline a proposal *because the fit
is not good enough to use*, and the shape is what tells them that
(`docs/architecture-recovery-report-then-curate.md` §3). So these tests assert
on what the diagram communicates — direction, nesting, provenance, what is
withheld — not merely that a string was produced.
"""
from __future__ import annotations

import pytest

from resource_explorer.surveyors.arch_recovery import mermaid
from resource_explorer.surveyors.arch_recovery.ir import IR, Component, Identity


def _c(slug, name=None, type="Software Service", parent="", depth=0,
       confidence=90, blueprint=""):
    return Component(
        slug=slug, name=name or slug, type=type,
        identity=Identity(method="deployment-unit", value=slug),
        confidence=confidence, parent_slug=parent, depth=depth, blueprint=blueprint,
    )


def _ir(components=(), ports=(), wires=()):
    return IR(target="demo", checkout="/tmp/demo",
              components=list(components), ports=list(ports), wires=list(wires))


class TestDeterminism:
    """This text goes into an annotation republished on every re-derivation. An
    unchanged analysis must render byte-identically or every run reads as a
    change — and the outbox work is trying to converge on this value."""

    def test_identical_input_renders_identically(self):
        ir = _ir([_c("b"), _c("a")], wires=[{"source": "a", "target": "b", "oneWay": True}])
        assert mermaid.render(ir) == mermaid.render(ir)

    def test_component_order_does_not_change_the_output(self):
        a, b = _c("a"), _c("b")
        assert mermaid.render(_ir([a, b])) == mermaid.render(_ir([b, a]))

    def test_wire_order_does_not_change_the_output(self):
        w1 = {"source": "a", "target": "b", "oneWay": True}
        w2 = {"source": "b", "target": "a", "oneWay": True}
        comps = [_c("a"), _c("b")]
        assert mermaid.render(_ir(comps, wires=[w1, w2])) == mermaid.render(_ir(comps, wires=[w2, w1]))

    def test_no_timestamp_or_address_leaks_into_the_output(self):
        out = mermaid.render(_ir([_c("a")]))
        assert "0x" not in out
        assert "2026" not in out


class TestShape:
    def test_starts_with_a_flowchart_header(self):
        assert mermaid.render(_ir([_c("a")])).startswith("flowchart TD")

    def test_a_component_carries_its_type_and_confidence(self):
        out = mermaid.render(_ir([_c("svc", name="Gateway", type="Software Service", confidence=80)]))
        assert "Gateway" in out
        assert "Software Service" in out
        assert "confidence 80%" in out

    def test_typed_and_boundary_only_nodes_label_type_and_confidence(self):
        out = mermaid.render(_ir([
            _c("ui", name="Web UI", type="User Interface", confidence=80),
            _c("edge", name="Edge", type=None, confidence=60),
        ]))
        assert "<small>User Interface · confidence 80%</small>" in out
        assert "<small>type not assigned · boundary only · confidence 60%</small>" in out
        assert "unclassified" not in out
        assert " · 60%" not in out and " · 80%" not in out

    def test_a_parent_becomes_a_subgraph_containing_its_child(self):
        """SolutionComposition nests components natively (design §3.3a), so the
        diagram nests too."""
        ir = _ir([_c("app"), _c("app/api", parent="app", depth=1)])
        out = mermaid.render(ir, max_depth=None)
        assert "subgraph" in out
        assert out.index("subgraph") < out.index(mermaid._nid("app/api"))
        assert "end" in out

    def test_blueprints_group_as_subgraphs_and_a_repo_may_have_several(self):
        """A repo is a storage boundary, not a solution boundary."""
        out = mermaid.render(_ir([_c("a", blueprint="alpha"), _c("b", blueprint="beta")]))
        assert "Blueprint: alpha" in out
        assert "Blueprint: beta" in out

    def test_a_component_with_no_blueprint_is_not_swept_into_one(self):
        out = mermaid.render(_ir([_c("a", blueprint="alpha"), _c("loner")]))
        assert "Blueprint: alpha" in out
        assert mermaid._nid("loner") in out


class TestStructuralNodes:
    """`scope_hierarchy` refuses to emit derived ancestors as components — they
    have no evidence of their own. A diagram that hid the distinction would
    invent the evidence the analysis declined to invent."""

    def test_a_referenced_but_absent_parent_is_marked_grouping_only(self):
        ir = _ir([_c("internal/a", parent="internal", depth=1),
                  _c("internal/b", parent="internal", depth=1)])
        out = mermaid.render(ir, max_depth=None)
        assert "grouping only" in out

    def test_structural_nodes_get_the_dashed_class(self):
        ir = _ir([_c("internal/a", parent="internal", depth=1)])
        out = mermaid.render(ir, max_depth=None)
        assert "classDef structural" in out

    def test_a_structural_node_shows_no_type_or_confidence(self):
        ir = _ir([_c("internal/a", parent="internal", depth=1)])
        out = mermaid.render(ir, max_depth=None)
        grouping_line = [l for l in out.splitlines() if "grouping only" in l][0]
        assert "%" not in grouping_line

    def test_the_renderer_and_persist_agree_on_which_nodes_are_structural(self):
        from resource_explorer.surveyors.arch_recovery import scope_hierarchy
        comps = [_c("internal/a", parent="internal", depth=1), _c("top")]
        assert mermaid._structural_slugs(comps) == set(scope_hierarchy.missing_ancestors(comps))


class TestPortsAndDirection:
    """Direction is what a suitability question turns on — 'serves an API' and
    'calls one' are different answers, and a count collapses them."""

    def test_an_input_port_points_into_its_component(self):
        ir = _ir([_c("svc")], ports=[{"component": "svc", "name": "8080",
                                      "direction": "Input", "protocol": "HTTP"}])
        out = mermaid.render(ir)
        assert f'{mermaid._nid("port::svc::8080")} --> {mermaid._nid("svc")}' in out

    def test_an_output_port_points_out_of_its_component(self):
        ir = _ir([_c("svc")], ports=[{"component": "svc", "name": "out",
                                      "direction": "Output", "protocol": "HTTP"}])
        out = mermaid.render(ir)
        assert f'{mermaid._nid("svc")} --> {mermaid._nid("port::svc::out")}' in out

    def test_an_unknown_direction_uses_a_plain_link_rather_than_guessing(self):
        ir = _ir([_c("svc")], ports=[{"component": "svc", "name": "p",
                                      "direction": "Unknown", "protocol": ""}])
        assert "---" in mermaid.render(ir)

    def test_operation_count_is_shown_when_known(self):
        ir = _ir([_c("svc")], ports=[{"component": "svc", "name": "api",
                                      "direction": "Input-Output", "protocol": "gRPC",
                                      "additionalProperties": {"operationCount": "18"}}])
        assert "18 ops" in mermaid.render(ir)

    def test_a_port_with_no_matching_component_is_omitted_not_reattached(self):
        ir = _ir([_c("svc")], ports=[{"component": "ghost", "name": "p",
                                      "direction": "Input", "protocol": "HTTP"}])
        out = mermaid.render(ir)
        assert mermaid._nid("port::ghost::p") not in out

    def test_an_omitted_port_is_reported_in_the_caption(self):
        ir = _ir([_c("svc")], ports=[{"component": "ghost", "name": "p",
                                      "direction": "Input", "protocol": "HTTP"}])
        assert "not attributable" in mermaid.caption(ir)


class TestWires:
    def test_a_wire_between_two_components_is_drawn(self):
        ir = _ir([_c("a"), _c("b")], wires=[{"source": "a", "target": "b", "oneWay": True}])
        out = mermaid.render(ir)
        assert f'{mermaid._nid("a")} --> {mermaid._nid("b")}' in out

    def test_a_two_way_wire_is_distinguishable_from_a_one_way_one(self):
        ir = _ir([_c("a"), _c("b")], wires=[{"source": "a", "target": "b", "oneWay": False}])
        assert "<-->" in mermaid.render(ir)

    def test_a_wire_endpoint_given_as_a_name_resolves_to_its_component(self):
        """interfaces.propose attributes compose wires by service NAME while
        ports are attributed by component slug."""
        ir = _ir([_c("compose::api", name="api"), _c("compose::db", name="db")],
                 wires=[{"source": "api", "target": "db", "oneWay": True}])
        out = mermaid.render(ir)
        assert f'{mermaid._nid("compose::api")} --> {mermaid._nid("compose::db")}' in out

    def test_an_unresolvable_endpoint_renders_as_external_rather_than_vanishing(self):
        """A missing edge is a different architecture, not a tidier one."""
        ir = _ir([_c("a")], wires=[{"source": "a", "target": "somewhere-else", "oneWay": True}])
        out = mermaid.render(ir)
        assert "outside this analysis" in out
        assert mermaid._nid("somewhere-else") in out

    def test_a_wire_with_neither_end_known_is_dropped(self):
        ir = _ir([_c("a")], wires=[{"source": "x", "target": "y", "oneWay": True}])
        out = mermaid.render(ir)
        assert mermaid._nid("x") not in out

    def test_a_wire_label_is_carried(self):
        ir = _ir([_c("a"), _c("b")],
                 wires=[{"source": "a", "target": "b", "oneWay": True, "label": "depends on"}])
        assert "depends on" in mermaid.render(ir)


class TestConfidenceIsVisible:
    def test_a_low_confidence_component_is_marked(self):
        assert "⚠" in mermaid.render(_ir([_c("weak", confidence=30)]))

    def test_a_high_confidence_component_is_not_marked(self):
        assert "⚠" not in mermaid.render(_ir([_c("strong", confidence=95)]))

    def test_the_caption_counts_the_weak_ones(self):
        assert "1 marked" in mermaid.caption(_ir([_c("weak", confidence=30), _c("ok")]))


class TestProjection:
    def test_depth_limits_what_is_shown_not_what_was_found(self):
        ir = _ir([_c("app"), _c("app/api", parent="app", depth=1)])
        shallow = mermaid.render(ir, max_depth=0)
        assert mermaid._nid("app/api") not in shallow
        assert mermaid._nid("app") in shallow

    def test_the_caption_says_what_was_collapsed(self):
        ir = _ir([_c("app"), _c("app/api", parent="app", depth=1)])
        assert "nested below this level" in mermaid.caption(ir, max_depth=0)

    def test_full_depth_shows_everything(self):
        ir = _ir([_c("app"), _c("app/api", parent="app", depth=1)])
        assert mermaid._nid("app/api") in mermaid.render(ir, max_depth=None)


class TestEscaping:
    @pytest.mark.parametrize("bad", ['a"b', "a[b]", "a{b}", "a\nb", "a\\b"])
    def test_label_characters_that_would_break_mermaid_are_neutralised(self, bad):
        out = mermaid.render(_ir([_c("slug", name=bad)]))
        body = [l for l in out.splitlines() if mermaid._nid("slug") in l][0]
        assert body.count('"') == 2          # exactly the label's own delimiters
        assert "\n" not in body

    def test_slugs_with_punctuation_produce_valid_ids(self):
        out = mermaid.render(_ir([_c("compose::a-b.c/d")]))
        assert "n_compose__a_b_c_d" in out


class TestEmptyInput:
    def test_an_empty_ir_still_renders_a_valid_diagram(self):
        assert mermaid.render(_ir()).startswith("flowchart TD")

    def test_the_caption_of_an_empty_ir_says_zero_rather_than_nothing(self):
        assert "0 component(s)" in mermaid.caption(_ir())


class TestStructuralBlueprintInheritance:
    """A grouping node outside the blueprint its own members belong to splits
    one solution into two visual groups — the wrong answer to the question the
    curator is being asked."""

    def test_a_grouping_node_joins_its_children_s_blueprint(self):
        ir = _ir([_c("internal/a", parent="internal", depth=1, blueprint="alpha"),
                  _c("internal/b", parent="internal", depth=1, blueprint="alpha")])
        out = mermaid.render(ir, max_depth=None)
        bp_at = out.index("Blueprint: alpha")
        grouping_at = out.index("grouping only")
        end_at = out.rindex("end")
        assert bp_at < grouping_at < end_at

    def test_children_spanning_two_blueprints_are_separated_into_them(self):
        """Composition nests components INSIDE a blueprint and does not reach
        across one — a blueprint is a Collection, and membership of it is
        independent of the composition parent. Where they disagree the blueprint
        is the outer container, because a scope-locator grouping node that
        straddles two blueprints is exactly the misleading grouping clustering
        exists to replace."""
        ir = _ir([_c("internal/a", parent="internal", depth=1, blueprint="alpha"),
                  _c("internal/b", parent="internal", depth=1, blueprint="beta")])
        out = mermaid.render(ir, max_depth=None)
        assert "Blueprint: alpha" in out
        assert "Blueprint: beta" in out

    def test_a_grouping_node_left_with_no_children_is_not_drawn(self):
        """It would be a node a reader steps through to reach nothing."""
        ir = _ir([_c("internal/a", parent="internal", depth=1, blueprint="alpha"),
                  _c("internal/b", parent="internal", depth=1, blueprint="beta")])
        assert "grouping only" not in mermaid.render(ir, max_depth=None)

    def test_a_component_inside_its_own_blueprint_does_not_restate_it(self):
        """Noise: the enclosing subgraph already says it."""
        out = mermaid.render(_ir([_c("a", blueprint="alpha")]))
        assert "Blueprint: alpha" in out
        assert "blueprint: alpha" not in out


class TestDiagramIsNoLongerPersistedAtWriteTime:
    """Was `TestPersistedAtPublishTime` — the diagram used to be a record of
    what THIS run proposed, written alongside the evidence it was drawn
    from. Moved to READ time 2026-09-08
    (docs/curated-architecture-answers-design.md §6 items 2-4): a curator's
    verdict now changes what the diagram shows immediately, which a
    snapshot baked in at persist time never could. `persist_ir` no longer
    calls `_persist_diagram` at all — this class pins that, plus the one
    invariant here that was never about the diagram in the first place.
    Content-level coverage (a component's confidence, structural nodes,
    ports/wires, verdict styling) now lives against `mermaid.render`/
    `caption` directly (this file, above) and against the read-time
    reconstruction in tests/test_architecture_diagram_results.py."""

    class _StubRegistry:
        def __init__(self):
            self.findings = []

        def upsert_finding(self, slug, kind, rows, surveyed_at="", scope_locator=""):
            for r in rows:
                self.findings.append({**r, "kind": kind, "scope_locator": scope_locator,
                                      "_ts": surveyed_at})

        def upsert_metric(self, *a, **k):
            pass

        def query_findings_history_raw(self, slug, kind):
            """Real, not a no-op returning []. `persist_ir` reads history to
            decide what to withdraw, and a stub that always answers "no history"
            would let these tests pass while withdrawal was broken."""
            return [{**f, "check_name": f.get("check_name", ""),
                     "detail_json": f.get("detail"), "surveyed_at": f.get("_ts", "")}
                    for f in self.findings if f.get("kind") == kind]

    def _run(self, components, ports=(), wires=()):
        from resource_explorer.surveyors.arch_recovery.persist import persist_ir
        reg = self._StubRegistry()
        persist_ir(reg, "demo", list(components), [], "2026-08-29T00:00:00",
                   ports=list(ports), wires=list(wires))
        return reg

    def _diagrams(self, reg):
        from resource_explorer.surveyors.arch_recovery import persist
        return [f for f in reg.findings if f["kind"] == persist.DIAGRAM_KIND]

    def test_persist_ir_writes_no_diagram_at_all_any_more(self):
        """The regression pin for this refactor: a survey run that would
        very much have produced a diagram under the old write-time design
        (real components, ports, wires all present) still writes none."""
        reg = self._run(
            [_c("a", name="a"), _c("b", name="b")],
            ports=[{"component": "a", "name": "8080", "direction": "Input", "protocol": "tcp"}],
            wires=[{"source": "a", "target": "b", "oneWay": True}],
        )
        assert self._diagrams(reg) == []
        # It did not write nothing at all -- the component rows this
        # reconstruction now reads back from are still there.
        assert any(f["check_name"] == "component" for f in reg.findings)

    def test_no_whole_resource_finding_is_written_under_the_recovery_kind(self):
        """The property that actually protects the compiler, unrelated to
        the diagram specifically -- kept from the class this replaces."""
        from resource_explorer.surveyors.arch_recovery import persist
        reg = self._run([_c("a"), _c("b")])
        whole = [f for f in reg.findings
                 if f["kind"] == persist.KIND and f["scope_locator"] == ""]
        assert whole == []


class TestRendererSizeLimit:
    """Measured 2026-08-29: `genaiexamples` renders to 59,791 characters and
    kroki refuses it (`MaxTextSizeError`, maximum 50,000). One diagram in the
    corpus hits it, so the limit is named and labelled rather than worked
    around by truncation."""

    def test_a_small_diagram_is_under_the_limit(self):
        assert not mermaid.exceeds_renderer_limit(mermaid.render(_ir([_c("a")])))

    def test_a_diagram_over_the_limit_is_detected(self):
        assert mermaid.exceeds_renderer_limit("x" * (mermaid.RENDERER_CHAR_LIMIT + 1))

    def test_the_limit_is_exactly_inclusive(self):
        assert not mermaid.exceeds_renderer_limit("x" * mermaid.RENDERER_CHAR_LIMIT)

    def test_an_oversized_proposal_says_so_in_its_caption(self):
        big = _ir([_c(f"component-number-{i:05d}", name=f"a rather long name {i}")
                   for i in range(1200)])
        assert mermaid.exceeds_renderer_limit(mermaid.render(big, max_depth=None))
        assert "NOT RENDERABLE" in mermaid.caption(big, max_depth=None)

    def test_an_oversized_proposal_is_not_truncated(self):
        """A silently shortened architecture is a different architecture."""
        big = _ir([_c(f"component-number-{i:05d}", name=f"a rather long name {i}")
                   for i in range(1200)])
        out = mermaid.render(big, max_depth=None)
        assert mermaid._nid("component-number-01199") in out

    def test_char_count_and_exceeds_flag_agree_when_oversized(self, monkeypatch):
        """Was against the persisted finding's own `detail.char_count`/
        `exceeds_renderer_limit` fields (removed 2026-09-08 along with
        write-time persistence) -- the same pairing a read-time caller
        builds directly from `render()`'s own output, pinned here instead."""
        monkeypatch.setattr(mermaid, "RENDERER_CHAR_LIMIT", 10)
        out = mermaid.render(_ir([_c("a")]))
        assert mermaid.exceeds_renderer_limit(out) is True
        assert len(out) > 10

    def test_char_count_and_exceeds_flag_agree_when_fine(self):
        out = mermaid.render(_ir([_c("a")]))
        assert mermaid.exceeds_renderer_limit(out) is False
        assert len(out) > 0
