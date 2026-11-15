"""The packer's guarantees.

These are the reason the packer is ordinary code. Every one of them is
unavailable if a model decides what to include, so each is asserted rather than
assumed.
"""
from __future__ import annotations

import pytest
from trellis_artifact_tree.model import Rung

from trellis_context.packer import BudgetError, Candidate, Pointer, pack
from trellis_context.spec import ContextSpec, Section


def spec(*sections, **kw) -> ContextSpec:
    return ContextSpec(kw.pop("spec_id", "s"), kw.pop("version", 1), tuple(sections), **kw)


def cand(key, full=200, summary=None, ident=None) -> Candidate:
    rungs = {Rung.FULL: "F" * full}
    if summary:
        rungs[Rung.SUMMARY] = "S" * summary
    if ident:
        rungs[Rung.IDENTIFIERS] = "I" * ident
    return Candidate(key, rungs)


class TestHardCeiling:
    def test_the_budget_is_never_exceeded(self):
        """Silent truncation at the window boundary defeats every other
        guarantee, which is why this is a hard failure and not a trim."""
        s = spec(Section("a"), Section("b"))
        for budget in range(0, 500, 17):
            out = pack(s, {"a": cand("a", 200), "b": cand("b", 200)}, budget)
            assert out.manifest.used <= budget

    def test_an_unfittable_required_section_raises(self):
        s = spec(Section("must", required=True))
        with pytest.raises(BudgetError, match="does not fit"):
            pack(s, {"must": cand("must", 500)}, 10)

    def test_a_required_section_with_no_candidate_raises(self):
        """A caller asked for it. Returning a context without it is not a
        smaller answer to that request, it is a different one."""
        s = spec(Section("must", required=True), Section("opt"))
        with pytest.raises(BudgetError, match="no candidate"):
            pack(s, {"opt": cand("opt")}, 1000)


class TestRequiredFirst:
    """Required sections are admitted before optional ones, so only a required
    section that cannot fit on its own may fail the compile."""

    def test_an_optional_section_never_starves_a_required_one(self):
        """An optional section declared first used to spend the budget a later
        required one needed, and the compile failed although the required
        section fitted on its own. The optional one is what must go."""
        s = spec(Section("evidence"), Section("instructions", required=True))
        out = pack(s, {"evidence": cand("evidence", 80),
                       "instructions": cand("instructions", 50)}, 100)
        assert [p.key for p in out.sections] == ["instructions"]
        assert [d["key"] for d in out.manifest.dropped] == ["evidence"]

    def test_a_required_group_is_admitted_before_an_earlier_optional_section(self):
        """A group with a required member packs with the required tier: its
        members are admitted together, so the whole group goes first."""
        s = spec(Section("evidence"),
                 Section("a", group="cmp", required=True), Section("b", group="cmp"))
        out = pack(s, {"evidence": cand("evidence", 60),
                       "a": cand("a", 40), "b": cand("b", 40)}, 100)
        assert [p.key for p in out.sections] == ["a", "b"]
        assert [d["key"] for d in out.manifest.dropped] == ["evidence"]

    def test_required_first_admission_keeps_spec_order_in_the_output(self):
        """Admission order changes; the order the context reads in does not."""
        s = spec(Section("evidence"), Section("instructions", required=True))
        out = pack(s, {"evidence": cand("evidence", 40),
                       "instructions": cand("instructions", 50)}, 100)
        assert [p.key for p in out.sections] == ["evidence", "instructions"]
        assert [e["key"] for e in out.manifest.packed] == ["evidence", "instructions"]


class TestMonotonicity:
    def test_more_budget_never_removes_content(self):
        """Catches a large class of allocator bugs cheaply: any reshuffle that
        loses a section as the budget grows is wrong however plausible."""
        s = spec(Section("a", weight=3), Section("b"), Section("c", weight=0.5))
        cands = {"a": cand("a", 300, 60, 10), "b": cand("b", 200, 40),
                 "c": cand("c", 150)}
        seen: set[str] = set()
        for budget in range(0, 1200, 25):
            keys = {p.key for p in pack(s, cands, budget).sections}
            assert seen <= keys, f"budget {budget} lost {seen - keys}"
            seen = keys


class TestDeterminism:
    def test_same_inputs_give_identical_output(self):
        s = spec(Section("a", weight=2), Section("b"), Section("c"))
        cands = {"a": cand("a", 300, 50), "b": cand("b", 200), "c": cand("c", 100)}
        first = pack(s, cands, 400)
        second = pack(s, cands, 400)
        assert first.text() == second.text()
        assert first.manifest == second.manifest

    def test_reclaim_order_does_not_depend_on_dict_order(self):
        """Reclaim retries dropped sections heaviest-first with the key as
        tiebreak, so iteration order cannot leak into the output."""
        s = spec(Section("a"), Section("b"), Section("c"))
        one = {"a": cand("a", 100), "b": cand("b", 100), "c": cand("c", 100)}
        two = {"c": cand("c", 100), "a": cand("a", 100), "b": cand("b", 100)}
        assert pack(s, one, 250).text() == pack(s, two, 250).text()


class TestSymmetry:
    def test_grouped_sections_pack_at_the_same_rung(self):
        """Without this a comparison whose budget runs out mid-pack gives one
        subject full evidence and another a summary, and the answer favours the
        first for a reason nothing in the output reveals."""
        s = spec(Section("left", group="cmp"), Section("right", group="cmp"))
        cands = {"left": cand("left", 300, 40), "right": cand("right", 300, 40)}
        out = pack(s, cands, 200)
        rungs = {p.rung for p in out.sections}
        assert len(rungs) == 1, "members packed at different rungs"

    def test_an_uneven_group_degrades_together(self):
        """One member being larger must pull the WHOLE group coarser, not just
        itself."""
        s = spec(Section("left", group="cmp"), Section("right", group="cmp"))
        cands = {"left": cand("left", 50, 20), "right": cand("right", 400, 20)}
        out = pack(s, cands, 200)
        assert {p.rung for p in out.sections} == {Rung.SUMMARY}

    def test_a_group_member_is_never_reclaimed_alone(self):
        """Reinstating one member from spare budget is exactly the asymmetry
        the group exists to prevent."""
        s = spec(Section("solo", weight=5), Section("l", group="cmp"),
                 Section("r", group="cmp"))
        cands = {"solo": cand("solo", 20), "l": cand("l", 300), "r": cand("r", 300)}
        out = pack(s, cands, 400)
        packed = {p.key for p in out.sections}
        assert not ({"l", "r"} & packed) or {"l", "r"} <= packed


class TestManifest:
    def test_a_missing_candidate_is_a_gap_not_a_silence(self):
        """An analysis that has not run yet is a normal state. The manifest
        says so rather than the context quietly lacking it."""
        s = spec(Section("a"), Section("not_run_yet"))
        out = pack(s, {"a": cand("a", 50)}, 1000)
        assert [g["key"] for g in out.manifest.gaps] == ["not_run_yet"]

    def test_drops_carry_a_reason(self):
        s = spec(Section("a", weight=9), Section("b"))
        out = pack(s, {"a": cand("a", 200), "b": cand("b", 900)}, 250)
        assert out.manifest.dropped
        assert all(d["reason"] for d in out.manifest.dropped)

    def test_headroom_is_reported(self):
        s = spec(Section("a"))
        out = pack(s, {"a": cand("a", 100)}, 500)
        assert out.manifest.headroom == 500 - out.manifest.used


class TestFloor:
    def test_a_section_is_dropped_rather_than_reduced_below_its_floor(self):
        """A section whose IDENTIFIERS rung would be noise sets a floor and is
        dropped instead — less is not always better than nothing."""
        s = spec(Section("a", floor=Rung.SUMMARY))
        c = Candidate("a", {Rung.FULL: "F" * 300, Rung.SUMMARY: "S" * 100,
                            Rung.IDENTIFIERS: "I" * 5})
        out = pack(s, {"a": c}, 50)
        assert not out.sections
        assert out.manifest.dropped[0]["reason"].startswith("no rung at or above SUMMARY")


class TestSpecIdentity:
    def test_target_model_is_identity_bearing(self):
        """Easy to misfile as operational tuning. It is not: the model's window
        sizes the budget, so a different model packs differently."""
        a = spec(Section("x"), target_model="opus")
        b = spec(Section("x"), target_model="haiku")
        assert a.identity() != b.identity()

    def test_as_of_is_identity_bearing(self):
        assert spec(Section("x"), as_of="T1").identity() != spec(Section("x"), as_of="T2").identity()

    def test_metadata_is_not(self):
        a = spec(Section("x"), metadata={"note": "one"})
        b = spec(Section("x"), metadata={"note": "two"})
        assert a.identity() == b.identity()


class TestPointer:
    """A pointer rides alongside its section's prose -- a sibling field, not a
    competing candidate (docs Backlog "a compiled answer should be able to
    point at a view")."""

    def test_a_pointer_reaches_the_packed_section_and_the_manifest(self):
        s = spec(Section("arch"))
        ptr = Pointer("egeria_git", "architecture", perspective="deployment")
        c = Candidate("arch", {Rung.FULL: "F" * 100}, pointer=ptr)
        out = pack(s, {"arch": c}, 1000)
        assert out.sections[0].pointer == ptr
        assert out.manifest.packed[0]["pointer"] == ptr.as_dict()

    def test_the_pointer_text_reaches_the_model_too(self):
        s = spec(Section("arch"))
        ptr = Pointer("egeria_git", "architecture")
        c = Candidate("arch", {Rung.FULL: "hello"}, pointer=ptr)
        out = pack(s, {"arch": c}, 1000)
        assert ptr.render() in out.sections[0].text

    def test_pointer_overhead_counts_against_the_ceiling(self):
        """A pointer is cheap, not free -- it must not let a section sneak
        past the hard ceiling."""
        s = spec(Section("arch"))
        ptr = Pointer("egeria_git", "architecture")
        c = Candidate("arch", {Rung.FULL: "F" * 100}, pointer=ptr)
        budget = 100 + len(ptr.render()) - 1     # one char short with the pointer
        out = pack(s, {"arch": c}, budget)
        assert not out.sections
        assert out.manifest.dropped

    def test_pointer_overhead_is_the_same_at_every_rung(self):
        """The overhead a pointer adds is constant across rungs, so it shifts
        which budgets afford a rung but never favours one rung over another
        the way, say, a per-rung fee would."""
        s = spec(Section("arch"))
        ptr = Pointer("egeria_git", "architecture")
        with_ptr = Candidate("arch", {Rung.FULL: "F" * 50, Rung.SUMMARY: "S" * 10},
                              pointer=ptr)
        without_ptr = Candidate("arch", {Rung.FULL: "F" * 50, Rung.SUMMARY: "S" * 10})
        overhead = len(ptr.render())
        budget = 60
        assert (pack(s, {"arch": with_ptr}, budget + overhead).sections[0].rung ==
                pack(s, {"arch": without_ptr}, budget).sections[0].rung)

    def test_a_section_with_no_pointer_carries_none(self):
        s = spec(Section("a"))
        out = pack(s, {"a": cand("a", 50)}, 1000)
        assert out.sections[0].pointer is None
        assert "pointer" not in out.manifest.packed[0]


class TestUpgradePolicy:
    """Weight governs upgrade PRIORITY, and upgrades happen one rung per round.

    Pinned because it is a policy choice a reader would otherwise assume the
    other way — "weight governs priority" reads like "upgrade the heaviest fully
    first", and that is deliberately not what happens.
    """

    def _setup(self):
        s = spec(Section("heavy", weight=3), Section("light", weight=1))
        c = {
            "heavy": Candidate("heavy", {Rung.FULL: "F" * 400,
                                         Rung.SUMMARY: "S" * 80,
                                         Rung.IDENTIFIERS: "I" * 10}),
            "light": Candidate("light", {Rung.FULL: "F" * 300,
                                         Rung.IDENTIFIERS: "I" * 12}),
        }
        return s, c

    def test_a_light_section_can_overtake_a_heavy_one(self):
        """The consequence of stepping. Not a bug: a policy that cannot starve
        a section is the right default while Perspective weighting is
        unmeasured."""
        s, c = self._setup()
        out = pack(s, c, 500)
        rung = {p.key: p.rung for p in out.sections}
        assert rung["light"] is Rung.FULL
        assert rung["heavy"] is Rung.SUMMARY

    def test_the_heavier_section_still_moves_first(self):
        """With only enough budget for one step, it goes to the heavier."""
        s, c = self._setup()
        out = pack(s, c, 110)
        rung = {p.key: p.rung for p in out.sections}
        assert rung["heavy"] is Rung.SUMMARY
        assert rung["light"] is Rung.IDENTIFIERS

    def test_everything_reaches_full_when_budget_allows(self):
        s, c = self._setup()
        out = pack(s, c, 5000)
        assert {p.rung for p in out.sections} == {Rung.FULL}
