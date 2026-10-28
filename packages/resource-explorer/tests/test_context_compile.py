"""Compiling a context for the adoption gate.

Closes the loop Phase 0 opened: the derivation that get_questions() already
computed becomes the section list, and the packer decides what fits.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resource_explorer.context_compile import compile_context


class _NullMock(MagicMock):
    """A registry double whose unconfigured methods return None, not another
    mock. The results readers call a dozen registry methods; left as bare
    MagicMocks those returned mocks, readers built dicts of "<MagicMock
    name='mock.query_metrics().get()'>" values, _has_content counted them as
    content, and compiles packed sections of mock garbage -- one test
    depended on that without knowing (found by dwolfson-4c reproducing
    #46's CI failure, 2026-09-13). A reader handed None either copes or
    raises; the compiler treats both as "nothing stored", which is the
    truth for a registry that holds only what the test put in it."""

    def _get_child_mock(self, **kw):
        child = _NullMock(**kw)
        child.return_value = None
        return child


def _registry(findings_by_kind):
    r = _NullMock()
    r.query_findings.side_effect = lambda slug, kind, *a, **k: findings_by_kind.get(kind, [])
    return r


def _finding(check, label="ok", summary="detail here"):
    return {"check_name": check, "label": label, "summary": summary,
            "surveyed_at": "2026-08-28T00:00:00"}


class TestDerivation:
    def test_the_chain_travels_with_the_answer(self):
        """The explanation with content: which question, which purpose matched,
        which analysis it dispatches to."""
        c = compile_context(_registry({"license_classification": [_finding("license")]}),
                            "x", "q", purposes=["Certify"], budget=4000)
        assert c.derivation
        first = c.derivation[0]
        assert set(first) >= {"question", "matched_purposes", "analysis_ids", "rank"}

    def test_ranking_sets_weight_not_membership(self):
        """Purpose ORDERS and never excludes, so a low-ranked analysis is a
        light section rather than an absent one."""
        c = compile_context(_registry({}), "x", "q", purposes=["Certify"], budget=4000)
        keys = {g["key"] for g in c.manifest["gaps"]}
        assert len(keys) > 3, "low-ranked analyses should still appear, as gaps"


class TestQuestionRelevance:
    """Measured 2026-08-31: "Show me all the documentation survey results for
    egeria" packed repository_health / architecture_doc_lens /
    language_file_classification and dropped documentation_coverage as a gap
    entirely. `question` was accepted by compile_context() and never read
    again -- with no Perspective chips set (the common case), every one of
    the ~50 catalog questions weighed the same, decaying only by its
    arbitrary position in question_catalog.yaml. The model, given evidence
    that did not answer what was asked, fell back to vector_search and
    answered from Egeria's OWN documentation about its Survey Framework
    feature instead -- a keyword collision on "survey", not an answer."""

    def test_a_question_naming_its_topic_outranks_unrelated_ones(self):
        from resource_explorer.context_compile import _question_relevance
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        entries = get_questions("repo", perspectives=None, purposes=None)
        question = "Show me all the documentation survey results for egeria"

        scored = []
        for position, e in enumerate(entries):
            ids = e["answering"]["analysis_ids"]
            if not ids:
                continue
            relevance = _question_relevance(question, e["question"], ids)
            weight = (1.0 + relevance * 20.0) / (1 + position * 0.1)
            scored.append((weight, e["question"], ids))
        scored.sort(key=lambda t: -t[0])

        top_ids = [ids for _, _, ids in scored[:2]]
        assert all("documentation_coverage" in ids for ids in top_ids), (
            f"documentation_coverage should rank first; got {scored[:2]}"
        )

    def test_weight_reaches_the_real_compile(self, monkeypatch):
        """Not just the scoring helper in isolation -- the analysis actually
        outranks an unrelated one in a real compile_context() call. Both
        pack either way at this budget (each is tiny with one finding), so
        membership alone does not distinguish fixed from unfixed -- PACKING
        ORDER does, since packed sections are emitted in weight order and
        repository_health (an unrelated, higher-catalog-position question)
        used to rank ahead of the analysis the question actually named."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        reader_output = {"findings": [
            {"check_name": "readme", "label": "present", "summary": "README present"},
        ]}
        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "documentation_coverage", (lambda reg, slug: reader_output, None))

        # repository_health needs a real finding to pack: until 2026-09-13 it
        # packed anyway, from the fake registry's mock values (see _NullMock).
        c = cc.compile_context(_registry({"repository_health": [_finding("overall", "85", "score 85/100")]}),
                               "egeria",
                               "Show me all the documentation survey results for egeria",
                               budget=1200)
        packed_order = [p["key"] for p in c.manifest["packed"] if p["key"] != "instructions"]
        assert "documentation_coverage" in packed_order, (
            f"documentation_coverage was crowded out; packed={packed_order}"
        )
        assert "repository_health" in packed_order, "test assumes both pack at this budget"
        assert packed_order.index("documentation_coverage") < packed_order.index("repository_health"), (
            f"documentation_coverage should outrank an unrelated question's "
            f"analysis given what was asked; packed order was {packed_order}"
        )

    def test_irrelevant_questions_are_ranked_down_not_excluded(self):
        """Purpose already establishes ranking over exclusion for this
        system; this must not become the first thing that filters instead."""
        from resource_explorer.context_compile import _question_relevance

        assert _question_relevance(
            "Show me all the documentation survey results for egeria",
            "Are there outstanding CVEs?", ["cve_scan"],
        ) == 0.0  # no overlap -- but a 0.0 weight component, not a dropped entry

    def test_no_overlap_returns_zero_not_an_error(self):
        from resource_explorer.context_compile import _question_relevance

        assert _question_relevance("", "How well documented is it?",
                                   ["documentation_coverage"]) == 0.0

    def test_rank_is_catalog_position_not_list_order(self):
        """S4 review, 2026-08-31: `derivation` is now sorted by weight
        (relevance-then-position), but each entry's `rank` field still
        reports its ORIGINAL catalog/Purpose position -- the two can and
        should diverge for a low-catalog-rank, high-relevance entry. Locking
        this in as intended behavior, not an oversight a future pass should
        "fix" by renumbering `rank` to match list order."""
        from resource_explorer.context_compile import compile_context

        c = compile_context(_registry({}), "egeria",
                            "Show me all the documentation survey results for egeria",
                            budget=8000)
        # documentation_coverage's catalog rank (24/25, see the module-level
        # measurement in this class's docstring) is well behind several
        # zero-relevance, position < 24 entries -- so it must now sort near
        # the front of `derivation` while its own `rank` field still reads
        # its true, larger catalog position.
        doc_entries = [d for d in c.derivation if "documentation_coverage" in d["analysis_ids"]]
        assert doc_entries, "documentation_coverage should be reachable from this question"
        list_index = c.derivation.index(doc_entries[0])
        assert doc_entries[0]["rank"] > list_index, (
            "rank should still report catalog position even though list order "
            f"now reflects relevance; rank={doc_entries[0]['rank']}, list_index={list_index}"
        )


class TestGapsAreNotSilence:
    def test_an_analysis_with_no_findings_is_reported(self):
        """The derivation says it answers the question and it has no stored
        result. That is information, not something to omit."""
        c = compile_context(_registry({"repo_conventions": [_finding("a")]}),
                            "x", "q", purposes=["Assess"], budget=4000)
        gap_keys = {g["key"] for g in c.manifest["gaps"]}
        packed_keys = {p["key"] for p in c.manifest["packed"]}
        assert gap_keys and not (gap_keys & packed_keys)

    def test_nothing_is_run_to_fill_a_gap(self):
        """A compile must never block on a survey. Only stored results are
        read."""
        registry = _registry({})
        compile_context(registry, "x", "q", purposes=["Certify"], budget=4000)
        assert registry.query_findings.called
        for name in ("run_analysis", "run_survey", "dispatch"):
            assert not getattr(registry, name).called


class TestBudget:
    def test_instructions_survive_a_tight_budget(self):
        """Required. A context with evidence and no instructions is a different
        task, not a smaller one."""
        c = compile_context(_registry({"repo_conventions": [_finding("a", summary="x" * 500)]}),
                            "x", "q", budget=400)
        assert any(p["key"] == "instructions" for p in c.manifest["packed"])

    def test_evidence_degrades_before_it_disappears(self):
        big = [_finding(f"check{i}", summary="y" * 200) for i in range(6)]
        c = compile_context(_registry({"repo_conventions": big}), "x", "q", budget=900)
        rungs = {p["key"]: p["rung"] for p in c.manifest["packed"]}
        assert rungs.get("repo_conventions") in {"SUMMARY", "IDENTIFIERS"}

    def test_the_ceiling_holds(self):
        big = [_finding(f"c{i}", summary="z" * 400) for i in range(10)]
        for budget in (300, 600, 1200, 5000):
            c = compile_context(_registry({"repo_conventions": big}), "x", "q", budget=budget)
            assert c.manifest["used"] <= budget

    def test_an_impossible_budget_fails_loudly(self):
        with pytest.raises(Exception):
            compile_context(_registry({}), "x", "q", budget=5)


class TestProvenance:
    def test_findings_carry_when_they_were_surveyed(self):
        """An old fact and a stale read are different things."""
        from resource_explorer.context_compile import _provenance

        prov = _provenance([_finding("a")], "repo_conventions")
        assert prov[0]["surveyed_at"] == "2026-08-28T00:00:00"
        assert prov[0]["analysis_id"] == "repo_conventions"


class TestPointerSections:
    """Backlog: "a compiled answer should be able to POINT at a view, not only
    describe it." Only analyses with a real addressable view get one."""

    def test_a_pointable_analysis_gets_a_link_alongside_its_prose(self):
        from resource_explorer.context_compile import _pointer_for

        prov = ({"analysis_id": "architecture_recovery", "check": "detect",
                 "surveyed_at": "2026-08-28T00:00:00"},)
        ptr = _pointer_for("architecture_recovery", "egeria_git", prov)
        assert ptr is not None
        assert ptr.resource_slug == "egeria_git"
        assert ptr.view == "architecture"
        assert ptr.as_of == "2026-08-28T00:00:00"

    def test_an_analysis_with_no_view_gets_none(self):
        from resource_explorer.context_compile import _pointer_for

        assert _pointer_for("license_classification", "egeria_git", ()) is None

    def test_the_pointer_reaches_the_manifest(self):
        c = compile_context(
            _registry({"architecture_recovery": [_finding("detect")]}),
            "egeria_git", "q", budget=4000,
            max_sections=0,  # "q" ranks nothing; the cap is not what this tests
        )
        packed = {p["key"]: p for p in c.manifest["packed"]}
        assert "pointer" in packed["architecture_recovery"]
        assert packed["architecture_recovery"]["pointer"]["view"] == "architecture"


# ── The findings table is not where most results live ────────────────────────
class TestResolvingBeyondTheFindingsTable:
    """A gap meant "not in project_analysis_findings" while claiming to mean
    "never run".

    Measured on egeria_git 2026-08-29: eleven analyses reported as gaps, seven
    of which had real stored data — repository_health scoring 85.8,
    api_structure holding 3,232 Java classes. Each keeps results in its own
    table, and only the generic findings table was consulted.
    docs/granularity-pass.md §1.2 had already measured that 12 analyses have no
    finding `kind` at all, so this was knowable without running anything.
    """

    def test_a_reader_only_analysis_is_packed_not_gapped(self, monkeypatch):
        from resource_explorer import context_compile as cc

        seen = {}

        def fake_reader(registry, slug):
            seen["called"] = slug
            return {"overall": 85.8, "activity": 100.0}

        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "repository_health", (fake_reader, None))

        c = cc.compile_context(_registry({}), "egeria_git", "is this ready to adopt?",
                               budget=8000)
        gaps = {g["key"] for g in c.manifest["gaps"]}
        assert "repository_health" not in gaps, "packed data still reported missing"
        assert seen.get("called") == "egeria_git"
        assert "repository_health" in {e["key"] for e in c.manifest["packed"]}

    def test_a_genuinely_empty_result_stays_a_gap(self, monkeypatch):
        """The fix must not swap a wrong gap for a wrong section. cve_scan
        answers {"findings": []} — a dict, therefore truthy, and meaning
        nothing was found."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "cve_scan", (lambda reg, slug: {"findings": []}, None))
        c = cc.compile_context(_registry({}), "x", "is this ready to adopt?", budget=8000)
        assert "cve_scan" in {g["key"] for g in c.manifest["gaps"]}

    def test_a_reader_that_raises_is_not_evidence_of_absence(self, monkeypatch):
        """It still ends as a gap — there is nothing to pack — but it must not
        take the whole compile down with it."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        def boom(reg, slug):
            raise RuntimeError("table missing")

        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP, "cve_scan", (boom, None))
        c = cc.compile_context(_registry({}), "x", "is this ready to adopt?", budget=8000)
        assert "cve_scan" in {g["key"] for g in c.manifest["gaps"]}

    def test_findings_win_when_both_exist(self, monkeypatch):
        """The findings table is consulted first and its richer per-check rungs
        are kept; the reader is a fallback, not an override."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "license_classification",
                            (lambda reg, slug: {"from": "reader"}, None))
        c = cc.compile_context(
            _registry({"license_classification": [_finding("license_risk_tier")]}),
            "x", "q", purposes=["Certify"], budget=8000)
        assert "license_risk_tier" in c.text, "findings rungs were not used"
        assert '"from": "reader"' not in c.text, "the fallback overrode real findings"


class TestReaderFindingsShapeIsFormattedNotCounted:
    """Measured 2026-08-31: asking the chat "show me documentation coverage
    on Egeria" answered "5 key(s) with 4 item(s) found" -- the model
    narrating _results_to_rungs' generic SUMMARY verbatim.
    _documentation_results (repo_survey_definition_adapter.py) returns
    {"findings": [...], "_status": {...}}, the same finding shape the
    findings table itself uses; the reader-fallback path was reducing it to
    two cardinalities instead of reusing _findings_to_rungs' own per-check
    formatting.

    Tests hit _results_to_rungs directly, not through compile_context: the
    bug is specifically in the SUMMARY rung, and a budget generous enough to
    let the packer choose FULL instead (a full JSON dump, which happens to
    contain the real strings too) would pass without the fix -- caught by
    running these against the unfixed code before adding them."""

    def _reader_output(self):
        return {
            "findings": [
                {"check_name": "readme", "label": "present",
                 "summary": "README.md found at repo root"},
                {"check_name": "changelog", "label": "missing",
                 "summary": "no CHANGELOG file located"},
            ],
            "_status": {"state": "measured", "outcome": "complete"},
        }

    def test_summary_rung_keeps_per_check_content(self):
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        rungs = _results_to_rungs(self._reader_output(), "documentation_coverage")
        summary = rungs[Rung.SUMMARY]

        assert "readme" in summary and "present" in summary
        assert "changelog" in summary and "missing" in summary
        # The exact bug: two cardinalities standing in for the content above.
        assert "item(s)" not in summary
        assert "key(s)" not in summary

    def test_full_rung_also_uses_finding_formatting(self):
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        rungs = _results_to_rungs(self._reader_output(), "documentation_coverage")
        full = rungs[Rung.FULL]

        assert "README.md found at repo root" in full
        assert "no CHANGELOG file located" in full
        # Other top-level keys (here, _status) are noted, not silently dropped.
        assert "_status" in full

    def test_reaches_the_answer_through_compile_context_too(self, monkeypatch):
        """Integration-level check that the fix is actually wired in, at
        whichever rung the packer picks for a real compile."""
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "documentation_coverage",
                            (lambda reg, slug: self._reader_output(), None))
        c = cc.compile_context(_registry({}), "egeria_git", "is this ready to adopt?", budget=8000)
        assert "documentation_coverage" not in {g["key"] for g in c.manifest["gaps"]}
        assert "readme" in c.text

    def test_a_reader_returning_a_genuinely_non_finding_shape_is_unaffected(self):
        """The generic path (key: cardinality) still applies to shapes that
        are not the findings-list envelope -- e.g. repository_health's flat
        metrics dict. This exception is a shape match, not a blanket change
        to every reader's output."""
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        rungs = _results_to_rungs({"overall": 85.8, "activity": 100.0}, "repository_health")
        assert "85.8" in rungs[Rung.FULL]
        assert "key(s)" not in rungs[Rung.FULL]  # no dict/list values here to miscount
        # An all-scalar shape has nothing to abridge, so its abridged form is
        # the same size as FULL and the middle rung is dropped rather than
        # offered — a rung that saves nothing costs the packer a choice and
        # buys a partial-evidence warning it does not need. (This assertion
        # used to read the SUMMARY rung; there no longer is one here.)
        assert Rung.SUMMARY not in rungs
        # A shape that genuinely has no per-check identity keeps a structural
        # summary -- but one that names a container without COUNTING it.
        # This used to assert "2 key(s)" as unchanged behaviour; the audit of
        # run full-20260908 found exactly that count narrated as a value
        # ("5 lines of code by language" from "5 key(s)"), so the count is
        # now the thing the rung must not carry. Scalars stay, as values.
        # The middle rung is now ABRIDGED, not structure-only. This assertion
        # used to require "structure only" in it and `pypi` absent from it.
        # The run-4 audit (docs/experiments/audits/2026-09-09-...md, pattern A)
        # measured the cost of naming a container without showing anything
        # inside it: the model read the FIELD NAMES as findings, or — asked
        # exactly what the structure-only section covered — answered from the
        # section next door. Real first entries with an explicit marker is the
        # replacement, so the values now DO appear at SUMMARY, bounded and
        # labelled as a prefix.
        big = {"by_ecosystem": {f"eco{i}": i for i in range(9)}, "total": 62}
        rungs2 = _results_to_rungs(big, "dependency_analysis")
        summary2 = rungs2[Rung.SUMMARY]
        assert "total: 62" in summary2
        assert "key(s)" not in summary2 and "item(s)" not in summary2
        assert "abridged" in summary2
        assert "(first 3 of 9)" in summary2, summary2
        assert "eco0: 0" in summary2, "an abridged rung must carry real entries"
        assert "eco8" not in summary2, "…and must stop after its first few"


class TestHasContent:
    """The zero case, which a first version got wrong.

    `_has_content` put the numeric test in an `elif ... and value` and followed
    it with a catch-all `elif value is not None`, so a zero failed the numeric
    branch and was caught by the fallback. `{"by_ecosystem": {}, "total": 0}`
    read as content and packed dependency_analysis as an empty section
    asserting it had something to say.
    """

    @pytest.mark.parametrize("value,expected", [
        ({"by_ecosystem": {}, "total": 0}, False),
        ({"findings": []}, False),
        ({"_status": {"state": "never_run", "outcome": "not_run"}}, False),   # envelope only
        ({"_status": {"state": "measured"}, "surveyed_at": "2026-09-13", "detail": {"x": 1}}, False),
        ({"_status": {"state": "measured"}, "total": 3}, True),                # envelope + a result
        ({}, False),
        ({"x": None}, False),
        ({"s": ""}, False),
        ({"n": 0.0}, False),
        ({"ok": False}, False),
        ({"total": 3}, True),
        ({"overall": 85.8}, True),
        ({"ok": True}, True),
        ({"s": "a"}, True),
        ({"items": [1]}, True),
    ])
    def test_truth_table(self, value, expected):
        from resource_explorer.context_compile import _has_content
        assert _has_content(value) is expected


class TestPublishActionsAreNotAnalyses:
    def test_egeria_publish_is_never_a_gap(self):
        """It writes to Egeria and has no results, so it could only ever be a
        permanent gap asserting a result that will never exist. The scheduler
        excludes action == "publish" for the same reason. A catalog question
        references it, which is how it reaches the compile at all."""
        c = compile_context(_registry({}), "x", "is this ready to adopt?", budget=8000)
        assert "egeria_publish" not in {g["key"] for g in c.manifest["gaps"]}
        assert "egeria_publish" not in str(c.derivation)


class TestChatRoutesThroughTheCompiler:
    """The answer path, not just the Evidence pane.

    Previously the agent was told which collections exist and left to search
    them. Now the analyses the question catalog says answer this question are
    resolved from stored results and put in the prompt directly.
    """

    def _agent(self):
        from resource_explorer.agents.conversation_agent import ConversationAgent

        return ConversationAgent()

    def test_gaps_are_named_in_the_prompt(self):
        """From inside a prompt, an analysis that never ran looks exactly like
        one that ran and found nothing. Naming the gaps is what stops the model
        answering 'no CVEs' when no CVE scan has ever run."""
        from unittest.mock import patch

        from resource_explorer.context_compile import CompiledContext

        compiled = CompiledContext(
            text="## repo_conventions\n- a: ok",
            manifest={"gaps": [{"key": "cve_scan"}, {"key": "security_scan"}]},
            derivation=[],
        )
        with patch("resource_explorer.context_compile.compile_context", return_value=compiled), \
             patch("resource_explorer.registry.ProjectRegistry"):
            blocks = self._agent()._compiled_evidence("q", "slug", [])
        joined = "\n".join(blocks)
        assert "cve_scan" in joined and "security_scan" in joined
        # Judged, not flattened. "ran and found nothing" and "has not run" are
        # the same number and opposite answers, so the prompt must not merge
        # them -- and must not claim the stronger one for a gap it cannot
        # support, which an earlier version did for two of three.
        assert "opposite" not in joined  # we state the distinction, not the meta
        assert "has not run has not" in joined

    def test_evidence_reaches_the_prompt(self):
        from unittest.mock import patch

        from resource_explorer.context_compile import CompiledContext

        compiled = CompiledContext(text="## license_classification\n- apache-2.0",
                                   manifest={"gaps": []}, derivation=[])
        with patch("resource_explorer.context_compile.compile_context", return_value=compiled), \
             patch("resource_explorer.registry.ProjectRegistry"):
            joined = "\n".join(self._agent()._compiled_evidence("q", "slug", []))
        assert "license_classification" in joined
        assert "compiled from stored analysis results" in joined

    def test_a_failed_compile_costs_nothing(self):
        """Fail-soft. A compiler that cannot compile must not cost the answer —
        the agent still has its tools and proceeds as it did before."""
        from unittest.mock import patch

        with patch("resource_explorer.context_compile.compile_context",
                   side_effect=RuntimeError("no db")), \
             patch("resource_explorer.registry.ProjectRegistry"):
            assert self._agent()._compiled_evidence("q", "slug", []) == []

    def test_no_perspectives_is_a_real_state_not_a_missing_value(self):
        """Empty chips means no perspective filter, and the compile still runs.
        Treating it as missing would silently skip the whole path."""
        from unittest.mock import patch

        from resource_explorer.context_compile import CompiledContext

        with patch("resource_explorer.context_compile.compile_context",
                   return_value=CompiledContext("x", {"gaps": []}, [])) as m, \
             patch("resource_explorer.registry.ProjectRegistry"):
            self._agent()._compiled_evidence("q", "slug", None)
        assert m.call_args.kwargs["perspectives"] == []


class TestGapsAreJudgedNotListed:
    """A measured zero and a never-run are the same number and opposite
    answers (facts.py). The packer knows only that a section had no candidate;
    the fact layer knows which of the two it is.
    """

    def test_a_real_zero_is_not_called_missing(self):
        from unittest.mock import MagicMock

        from resource_explorer.context_compile import _judge_gap

        fl = MagicMock()
        fl.fact.return_value = MagicMock(
            state="nothing_found", last_run_at="2026-08-28", can_run=["repo_cve_scan"])
        gap = _judge_gap(fl, "x", "cve_scan")
        assert gap["state"] == "nothing_found"
        assert "real zero" in gap["reason"]
        assert gap["can_run"] == ["repo_cve_scan"]

    def test_never_run_says_so(self):
        from unittest.mock import MagicMock

        from resource_explorer.context_compile import _judge_gap

        fl = MagicMock()
        fl.fact.return_value = MagicMock(state="never_run", last_run_at="", can_run=["repo_x"])
        assert _judge_gap(fl, "x", "a")["reason"] == "has not run"

    def test_not_established_is_a_short_cue_with_the_sentence_on_hover(self):
        from unittest.mock import MagicMock

        from resource_explorer.context_compile import _judge_gap

        fl = MagicMock()
        fl.fact.return_value = MagicMock(state="not_established", last_run_at="", can_run=[])
        gap = _judge_gap(fl, "x", "a")
        assert gap["state"] == "not_established", "the internal state name is unchanged"
        assert gap["reason"] == "ran · result not attributable"
        assert "cannot be credited" not in gap["reason"]
        assert gap["explanation"] == "it ran, but nothing shows this analysis produced the result"

    def test_an_unjudgeable_gap_is_still_reported(self):
        """Fail-soft: a fact layer that cannot answer must not cost the compile,
        and an unjudged gap is still worth naming."""
        from unittest.mock import MagicMock

        from resource_explorer.context_compile import _judge_gap

        fl = MagicMock()
        fl.fact.side_effect = RuntimeError("no registry")
        gap = _judge_gap(fl, "x", "a")
        assert gap["key"] == "a" and "resolver produced nothing" in gap["reason"]
        assert "state" not in gap

    def test_the_vocabulary_is_borrowed_not_invented(self):
        """result_status.py already carries these states and facts.py already
        applies them. A parallel vocabulary here is how four retired RE
        perspectives ended up beside Egeria's twelve."""
        from resource_explorer import facts
        from resource_explorer.context_compile import _GAP_PHRASING

        # facts.py is the source of truth for Fact.state: it imports the
        # result_status states AND adds PARTIAL of its own. Asserting against
        # result_status alone was too narrow — and caught me inventing
        # "partial" in the very code this test guards.
        for state in _GAP_PHRASING:
            assert hasattr(facts, state.upper()), f"{state} is not a Fact state"


class TestNoSingleFindingDominates:
    """Findings are written for whatever consumer the surveyor had in mind, and
    some are not prose — a rendered diagram, a serialised graph, a file listing.
    One of them can crowd out every other check in the same analysis.
    """

    def test_a_huge_finding_is_clipped(self):
        from resource_explorer.context_compile import MAX_FINDING_CHARS, _findings_to_rungs
        from trellis_artifact_tree.model import Rung

        findings = [
            {"check_name": "architecture_diagram", "label": "ok", "summary": "M" * 40000},
            {"check_name": "components", "label": "12 found", "summary": "short and useful"},
        ]
        full = _findings_to_rungs(findings, "architecture_recovery")[Rung.FULL]
        assert len(full) < MAX_FINDING_CHARS * 2
        assert "short and useful" in full, "the small finding must survive the big one"

    def test_truncation_is_marked_not_silent(self):
        """An elided finding that looked complete is the failure this module
        keeps finding in other forms."""
        from resource_explorer.context_compile import _findings_to_rungs
        from trellis_artifact_tree.model import Rung

        full = _findings_to_rungs(
            [{"check_name": "diagram", "label": "", "summary": "x" * 5000}], "arch",
        )[Rung.FULL]
        assert "truncated" in full and "arch/diagram" in full

    def test_ordinary_findings_are_untouched(self):
        from resource_explorer.context_compile import _findings_to_rungs
        from trellis_artifact_tree.model import Rung

        body = "a normal finding summary"
        full = _findings_to_rungs(
            [{"check_name": "c", "label": "l", "summary": body}], "a")[Rung.FULL]
        assert body in full and "truncated" not in full


class TestThinFindingsDoNotSuppressTheReader:
    """An earlier version fell back to the results reader only when findings
    were EMPTY, so any whole-resource finding — however slight — replaced an
    analysis's real results. The rule is about evidence, not precedence.
    """

    def _compile(self, findings, reader_results):
        from unittest.mock import MagicMock, patch

        from resource_explorer.context_compile import compile_context

        registry = MagicMock()
        registry.query_findings.side_effect = (
            lambda slug, kind, *a, **k: findings.get(kind, []))
        reader = MagicMock(return_value=reader_results)
        with patch("resource_explorer.surveyors.repo_survey_definition_adapter"
                   ".REPO_ANALYSIS_RESULTS_MAP", {"repo_conventions": (reader, None)}), \
             patch("resource_explorer.facts.FactLayer"):
            return compile_context(registry, "x", "q", budget=8000), reader

    def test_a_slight_finding_does_not_hide_richer_results(self):
        """The exact regression: one whole-resource diagram finding replaced an
        analysis's results with a picture, making the section worse than before
        the finding existed."""
        thin = {"repo_conventions": [
            {"check_name": "diagram", "label": "", "summary": ""}]}
        rich = {"detail": "a" * 3000}
        compiled, reader = self._compile(thin, rich)
        assert reader.called, "a thin finding must not suppress the reader"

    def test_substantial_findings_do_not_call_the_reader(self):
        """The reader is a fallback, not a second opinion on good evidence."""
        fat = {"repo_conventions": [
            {"check_name": f"c{i}", "label": "ok", "summary": "y" * 200}
            for i in range(5)]}
        _, reader = self._compile(fat, {"detail": "z" * 50})
        assert not reader.called

    def test_the_reader_does_not_displace_more_than_it_offers(self):
        """Falling back is not the same as preferring. If the reader says less
        than the thin findings did, the findings stay."""
        from resource_explorer.context_compile import _findings_to_rungs
        from trellis_artifact_tree.model import Rung

        thin = [{"check_name": "c", "label": "l", "summary": "x" * 150}]
        compiled, _ = self._compile({"repo_conventions": thin}, {"detail": ""})
        packed = " ".join(s for s in [compiled.text])
        assert "repo_conventions" in packed


class TestSectionCap:
    """Measured 2026-09-09: uncapped, ~26 sections shared a 6000-char budget,
    the budget pinned at its ceiling in every compile and 78% of packed
    sections sat at SUMMARY (run2, 3164 of 4056). The cap is applied AFTER
    ranking, so what is left out is what ranked lowest for this question."""

    def _many(self):
        # Every catalog analysis has a substantial finding, so with no cap
        # they would all be candidates competing for the budget.
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        ids = set()
        for e in get_questions("repo"):
            ids.update((e.get("derivation") or {}).get("analysis_ids") or [])
        return _registry({i: [_finding("c", summary="w" * 400)] for i in ids})

    def test_default_cap_bounds_what_competes(self):
        from resource_explorer.context_compile import MAX_EVIDENCE_SECTIONS
        c = compile_context(self._many(), "x", "how well documented is it?", budget=6000)
        evidence = [p for p in c.manifest["packed"] if p["role"] == "evidence"]
        offered = len(evidence) + len(c.manifest["dropped"]) + len(c.manifest["gaps"])
        assert offered <= MAX_EVIDENCE_SECTIONS
        assert c.manifest["deferred"], "with every analysis a candidate, some must be deferred"

    def test_deferred_are_the_lowest_ranked_and_say_so(self):
        c = compile_context(self._many(), "x", "how well documented is it?", budget=6000)
        packed = {p["key"] for p in c.manifest["packed"]}
        deferred = c.manifest["deferred"]
        assert all(d["key"] not in packed for d in deferred)
        assert all("cap" in d["reason"] for d in deferred)
        # Deferred sections are ranked below every packed evidence section.
        assert "documentation_coverage" in packed
        assert "documentation_coverage" not in {d["key"] for d in deferred}
        ranks = [d["rank"] for d in deferred]
        assert ranks == sorted(ranks) and min(ranks) >= len(packed) - 1

    def test_cap_is_a_knob_and_zero_disables_it(self):
        reg = self._many()
        capped = compile_context(reg, "x", "q", budget=6000, max_sections=3)
        assert len([p for p in capped.manifest["packed"] if p["role"] == "evidence"]) <= 3
        uncapped = compile_context(reg, "x", "q", budget=6000, max_sections=0)
        assert uncapped.manifest["deferred"] == []
        assert len(uncapped.manifest["packed"]) + len(uncapped.manifest["dropped"]) > 12

    def test_fewer_sections_means_fuller_rungs(self):
        """The point of the cap, stated as an invariant on one fixture: the
        top-ranked section reaches FULL with the cap and does not without it."""
        reg = self._many()
        with_cap = compile_context(reg, "x", "how well documented is it?", budget=6000)
        no_cap = compile_context(reg, "x", "how well documented is it?", budget=6000, max_sections=0)
        def full_share(c):
            ev = [p for p in c.manifest["packed"] if p["role"] == "evidence"]
            return sum(p["rung"] == "FULL" for p in ev) / len(ev)
        assert full_share(with_cap) > full_share(no_cap)
        assert {p["key"]: p["rung"] for p in with_cap.manifest["packed"]}["documentation_coverage"] == "FULL"


class TestRefusalWording:
    def test_instructions_do_not_carry_a_one_shape_template(self):
        """Run3-20260909: a fixed refusal template made the 8B model refuse
        107 of 156 questions, most with the answering analysis packed, and
        invent gap states for packed analyses. The loose wording stays."""
        from resource_explorer.context_compile import _INSTRUCTIONS
        assert "The stored analyses do not cover" not in _INSTRUCTIONS
        assert "exactly this shape" not in _INSTRUCTIONS
        assert "name what is missing" in _INSTRUCTIONS
        # Was "structure only". That rung no longer exists: the middle rung
        # carries real first entries now, so the instruction it needs is about
        # PARTIALITY, not about a section that "is not a result" — wording the
        # run-4 audit found the model ignoring in both directions (narrating
        # field names as findings, and sourcing an answer from the neighbouring
        # section when the relevant one showed nothing).
        assert "abridged" in _INSTRUCTIONS
        assert "do not report an abridged list as complete" in _INSTRUCTIONS
        assert "do not infer from absence" in _INSTRUCTIONS


class TestInstructionsClimbTheLadder:
    def test_a_tight_budget_gets_the_short_form_not_a_failure(self):
        """Used to pin the rung at exactly SUMMARY. It now lands at
        IDENTIFIERS for this budget, because the instructions section grew: it
        carries the coverage line (a question no analysis covers must say so)
        and the abridged-section warning. Rather than trim the wording back to
        fit one budget, instructions gained a third, bare rung — the point of
        the test is that a tight budget DEGRADES instead of raising, and that
        the absence rule survives every rung."""
        c = compile_context(_registry({"repo_conventions": [_finding("a")]}), "x", "q", budget=300)
        rung = {p["key"]: p["rung"] for p in c.manifest["packed"]}["instructions"]
        assert rung in {"SUMMARY", "IDENTIFIERS"}
        assert "Do not infer from absence" in c.text

    def test_a_normal_budget_gets_the_template(self):
        c = compile_context(_registry({"repo_conventions": [_finding("a")]}), "x", "q", budget=6000)
        assert {p["key"]: p["rung"] for p in c.manifest["packed"]}["instructions"] == "FULL"


class TestRelevanceBeatsPosition:
    def test_a_full_match_late_in_the_catalog_outranks_a_partial_match_early(self):
        """Measured 2026-09-09: under the ratio form of the weight, "What
        languages and file types are in this repository?" ranked
        foss_scorecard (from "Is this repository actively maintained?",
        catalog position 0, one shared word) above the language analysis
        (a full match at position ~30). Relevance is additive now."""
        reg = _registry({
            "language_file_classification": [_finding("langs", summary="j" * 300)],
            "foss_scorecard": [_finding("score", summary="k" * 300)],
            "repository_health": [_finding("health", summary="h" * 300)],
        })
        c = compile_context(reg, "x", "What languages and file types are in this repository?",
                            budget=6000)
        order = [p["key"] for p in c.manifest["packed"] if p["role"] == "evidence"]
        assert order[0] == "language_file_classification", order

    def test_repository_is_not_a_relevance_signal(self):
        from resource_explorer.context_compile import _question_relevance
        assert _question_relevance("what is in this repository?",
                                   "Is this repository actively maintained?",
                                   ["repository_health"]) == 0.0


# ── The three defect classes the run-4 audit found in the packed text ─────────
# docs/experiments/audits/2026-09-09-run4-unsupported-claims-vs-packed-text.md


class TestHeadlineComesFirst:
    """Pattern B: a reader-derived section packed as raw JSON with no gloss.

    Row 08: `cve_scan` showed `{"checked": 0, "unqueryable": 61, "findings":
    []}` and the model answered "The CVE scan found no vulnerabilities" — in
    the same pack as `foss_scorecard.vulnerabilities: unknown — No
    vulnerability scan has run`. The sentence it needed already existed:
    `_cve_scan_headline` says "none in 0 of 61 declared dependenc(ies)" with
    tone `warn`. It was simply never packed.
    """

    _CVE = {"advisories": 0.0, "checked": 0.0, "recorded": 61, "scanned": True,
            "unqueryable": 61.0, "packages_affected": 0.0, "findings": [],
            "ecosystems_seen": ["python"]}

    def _compile(self, monkeypatch, headline):
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc

        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP,
                            "cve_scan", (lambda reg, slug: dict(self._CVE), None))
        monkeypatch.setitem(adapter.REPO_ANALYSIS_HEADLINE_MAP,
                            "cve_scan", (lambda reg, slug: headline))
        return cc.compile_context(_registry({}), "docling",
                                  "Are there outstanding CVEs?", budget=8000)

    def _section(self, text, key="cve_scan"):
        block = [b for b in text.split("\n\n") if b.startswith(f"## {key}")]
        assert block, f"{key} not packed: {text[:400]}"
        return block[0]

    def test_the_headline_is_the_first_line_of_the_section(self, monkeypatch):
        c = self._compile(monkeypatch,
                          {"label": "none in 0 of 61 declared dependenc(ies)",
                           "tone": "warn"})
        lines = self._section(c.text).splitlines()
        assert lines[0] == "## cve_scan"
        assert lines[1] == "headline: none in 0 of 61 declared dependenc(ies) (warn)"

    def test_an_empty_findings_list_never_renders_as_a_bare_zero(self, monkeypatch):
        """`findings: []` reads as "measured, and clean". It is not: nothing
        was queryable. The list says so in words, and the headline carries the
        coverage."""
        c = self._compile(monkeypatch, {"label": "none in 0 of 61 declared "
                                                 "dependenc(ies)", "tone": "warn"})
        section = self._section(c.text)
        assert "findings: []" not in section
        assert "findings: (empty list)" in section
        assert "0 of 61" in section

    def test_a_headline_that_raises_costs_the_line_not_the_compile(self, monkeypatch):
        def boom(reg, slug):
            raise RuntimeError("no rows")

        c = self._compile(monkeypatch, None)  # map entry replaced below
        assert "cve_scan" not in {g["key"] for g in c.manifest["gaps"]}
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        monkeypatch.setitem(adapter.REPO_ANALYSIS_HEADLINE_MAP, "cve_scan", boom)
        from resource_explorer import context_compile as cc
        c2 = cc.compile_context(_registry({}), "docling", "Are there outstanding CVEs?",
                                budget=8000)
        assert "headline:" not in self._section(c2.text)
        assert "unqueryable: 61" in self._section(c2.text)
        # And the caller can SEE that the line is missing for a reason --
        # the silent-success ratchet (tests/test_no_silent_success.py) is
        # what caught this being log-only.
        assert any("headline for cve_scan unavailable (RuntimeError)" in n
                   for n in c2.manifest["notes"]), c2.manifest["notes"]


class TestFlatFullRung:
    """Pattern B, the other half. Row 13 read `stars: 65964` and `forks: 4745`
    out of `repository_health`'s nested `detail` block as "65964 stars, forks,
    watchers" — one number smeared across three fields inside a JSON dump."""

    def test_a_nested_dict_flattens_with_dotted_keys(self):
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        rungs = _results_to_rungs(
            {"overall": 99.2, "detail": {"forks": 15474, "stars": 33665,
                                         "subscribers_count": 349}},
            "repository_health")
        full = rungs[Rung.FULL]
        assert "- detail.forks: 15474" in full
        assert "- detail.stars: 33665" in full
        assert "```json" not in full, "the fenced dump is what got misread"

    def test_a_list_of_scalars_is_inline_and_marks_its_truncation(self):
        from resource_explorer.context_compile import MAX_FULL_LIST_ITEMS, _results_to_rungs
        from trellis_artifact_tree.model import Rung

        n = MAX_FULL_LIST_ITEMS + 7
        full = _results_to_rungs({"langs": [f"l{i}" for i in range(n)]}, "x")[Rung.FULL]
        assert "l0, l1" in full
        # The truncation is marked on the OPENING line, as "20 of 27 shown",
        # not as a trailing "… and 7 more" (designer's note 2026-09-12 §2:
        # a trailing elision got laundered into "here are some of them").
        assert "(20 of 27 shown)" in full, "an elided list that looked complete is the bug"

    def test_a_list_of_dicts_is_one_block_per_item(self):
        from resource_explorer.context_compile import MAX_FULL_DICT_ITEMS, _results_to_rungs
        from trellis_artifact_tree.model import Rung

        items = [{"name": f"c{i}", "kind": "module"} for i in range(MAX_FULL_DICT_ITEMS + 3)]
        full = _results_to_rungs({"components": items}, "architecture_recovery")[Rung.FULL]
        assert "- components: 13 item(s), 10 of 13 shown here" in full
        assert "  - item 1 of 13:" in full and "    - name: c0" in full
        assert "… and" not in full          # the total is beside the items, not after them

    def test_status_survives_as_a_plain_line(self):
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        full = _results_to_rungs(
            {"total": 3, "_status": {"state": "nothing_found", "outcome": "no_signal",
                                     "cause": "no_manifest", "hint": "run manifest parse"}},
            "dependency_analysis")[Rung.FULL]
        assert "_status: state=nothing_found" in full
        assert "outcome=no_signal" in full


class TestAbridgedMiddleRung:
    """Pattern A: the structure-only SUMMARY, narrated as a result.

    Row 06 read `architecture_recovery`'s field names (`blueprints`,
    `interfaces`, `documentation`) as an architecture and reported "some
    components partial or unverified" when `partial: False`. Row 16 is the
    mirror: `dependency_analysis` was structure-only, so the model answered a
    dependency question from `foss_scorecard`'s action pinning next door.
    """

    def _results(self):
        return {"component_count": 9, "partial": False, "raw_component_count": 107,
                "components": [{"name": f"c{i}", "files": i} for i in range(62)],
                "documentation": {f"d{i}": f"v{i}" for i in range(11)}}

    def _rungs(self):
        from resource_explorer.context_compile import _results_to_rungs
        return _results_to_rungs(self._results(), "architecture_recovery")

    def test_it_carries_real_entries_with_an_explicit_marker(self):
        from trellis_artifact_tree.model import Rung

        summary = self._rungs()[Rung.SUMMARY]
        assert "(abridged: first entries only" in summary
        assert "(first 3 of 62)" in summary
        assert "name=c0" in summary, "a named container with no entry is the old bug"
        assert "c61" not in summary, "abridged must stop after its first entries"
        assert "(list)" not in summary and "(mapping)" not in summary

    def test_scalars_keep_their_values(self):
        from trellis_artifact_tree.model import Rung

        summary = self._rungs()[Rung.SUMMARY]
        assert "- component_count: 9" in summary
        assert "- partial: False" in summary

    def test_it_is_materially_smaller_than_full(self):
        from trellis_artifact_tree.model import Rung

        rungs = self._rungs()
        assert len(rungs[Rung.SUMMARY]) <= len(rungs[Rung.FULL]) * 0.8

    def test_a_shape_with_nothing_to_abridge_offers_no_middle_rung(self):
        from resource_explorer.context_compile import _results_to_rungs
        from trellis_artifact_tree.model import Rung

        rungs = _results_to_rungs({"total": 62, "by_ecosystem": {"pypi": 62}}, "dependency_analysis")
        assert Rung.SUMMARY not in rungs
        assert {Rung.FULL, Rung.IDENTIFIERS} <= set(rungs)


class TestCoverageOfTheQuestionItself:
    """Pattern C, the most consequential class: 3 of the audit's 5 inventions.

    "What does this repo do?", "surveyed at what tier?" and "any use inside
    our organization?" have no answering analysis at all — and `gaps` was `[]`
    for all three, because a need never mapped to an analysis can never be
    reported as a missing one. The catalog already says so per question; the
    compile just never read that field.
    """

    def _catalog_question(self, kind):
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        for e in get_questions("repo"):
            if e["answering"]["kind"] == kind:
                return e
        return None

    def test_a_human_or_direct_question_says_so_at_the_top(self):
        entry = None
        for kind in ("human", "direct", "gap", "chart"):
            entry = self._catalog_question(kind)
            if entry:
                break
        assert entry, "the catalog should carry at least one non-analysis question"
        c = compile_context(_registry({}), "x", entry["question"], budget=8000)
        assert c.manifest["coverage"]["kind"] == entry["answering"]["kind"]
        assert c.manifest["coverage"]["question"] == entry["question"]
        first = c.text.splitlines()[0]
        assert first.startswith("Coverage: "), c.text[:200]
        assert "not by stored analyses" in first

    def test_an_analysis_backed_question_adds_no_line(self):
        c = compile_context(_registry({"cve_scan": [_finding("cve")]}), "x",
                            "Are there outstanding CVEs?", budget=8000)
        assert c.manifest["coverage"]["kind"] in {"analysis", "mixed", "partial"}
        assert "Coverage:" not in c.text

    def test_a_question_matching_nothing_is_told_so(self):
        """Not silence: "the sections below are the nearest-ranked evidence,
        not an answer" is the sentence that was missing when the model
        answered from the repo name and an invented survey tier."""
        c = compile_context(_registry({}), "x", "zzz qqq wibble", budget=8000)
        assert c.manifest["coverage"]["kind"] == "none"
        assert c.text.splitlines()[0].startswith("Coverage: no catalog question matches")

    def test_the_coverage_line_is_part_of_the_hashed_inputs(self):
        """Deterministic, and identity-bearing: the same question over the
        same stored state is one compile, and a different coverage verdict is
        a different one."""
        reg = _registry({})
        a1 = compile_context(reg, "x", "zzz qqq wibble", budget=8000)
        a2 = compile_context(reg, "x", "zzz qqq wibble", budget=8000)
        b = compile_context(reg, "x", "Are there outstanding CVEs?", budget=8000)
        assert a1.compile_id == a2.compile_id
        assert a1.compile_id != b.compile_id


class TestAnAllStopwordQuestionStillMatchesItself:
    def test_verbatim_catalog_question_is_a_full_match(self):
        """"What does this repository do?" strips to no tokens, so it scored
        0.0 against its own catalog entry and the compile reported no
        coverage at all (found by scripts/check_compiler_audit_rows.py)."""
        from resource_explorer.context_compile import _question_relevance
        assert _question_relevance("What does this repository do?",
                                   "What does this repository do?", []) == 1.0
        assert _question_relevance("what does this repository do",
                                   "What does this repository do?", []) == 1.0

    def test_coverage_is_found_for_it(self, monkeypatch):
        # "What does this repository do?" was the real catalog question this
        # regression was found against, but the 2026-09-21 cross-type
        # rewording (re/db-questions-csv) replaced it with "What is this
        # resource, and what is it for?" -- which contains "resource", a
        # real content token, and no longer strips to nothing. No question
        # in the current catalog does. Rather than assert this edge case
        # against whatever real content happens to exist right now (which is
        # exactly the coupling that broke this test once already), inject a
        # synthetic single-question catalog that still is all-stopword, so
        # the integration path (compile_context -> get_questions -> a real
        # catalog lookup) keeps covering the original bug regardless of what
        # production's CSV contains.
        from resource_explorer.surveyors import question_catalog_reader as qcr

        entry = {
            "question": "What does this repository do?",
            "stage": "Scouting",
            "perspectives": [],
            "answering": {"kind": "direct", "analysis_ids": [], "checks": [], "note": "N/A"},
            "answering_mechanism": "Direct Field",
        }
        loaded = {"repo": [qcr._entry_from_yaml(entry)]}
        fake = lambda: loaded          # noqa: E731
        fake.cache_clear = lambda: None
        monkeypatch.setattr(qcr, "_load", fake)

        c = compile_context(_registry({}), "x", "What does this repository do?", budget=6000)
        assert c.manifest["coverage"]["kind"] == "direct"
        assert c.text.startswith("Coverage:")


class TestCveHeadlineLeadsWithCoverage:
    """Run 5 (2026-09-10): with `headline: none in 0 of 61 declared
    dependenc(ies) (warn)` leading docling's cve_scan section, the model
    answered "No, there are no outstanding CVEs" and the judge agreed. The
    word "none" led; the coverage that reverses it trailed."""

    def _headline(self, checked, recorded, advisories=0):
        from resource_explorer.surveyors import repo_survey_definition_adapter as a
        reg = MagicMock()
        reg.query_findings.return_value = []
        reg.query_metrics.return_value = {"advisories": advisories, "checked": checked,
                                          "packages_affected": 1 if advisories else 0,
                                          "unqueryable": recorded - checked,
                                          "detail": {"recorded": recorded, "scanned": True}}
        return a._cve_scan_headline(reg, "x")

    def test_nothing_checked_says_so_first_and_never_says_none(self):
        h = self._headline(0, 61)
        assert h["label"].startswith("not checked: 0 of 61")
        assert "no CVE result exists" in h["label"]
        assert not h["label"].startswith("none") and h["tone"] == "warn"

    def test_partial_scan_names_what_could_not_be_queried(self):
        h = self._headline(11, 36)
        assert "11 of 36" in h["label"] and "25 could not be queried" in h["label"]
        assert h["tone"] == "warn"

    def test_full_clean_scan_is_the_only_good_tone(self):
        h = self._headline(36, 36)
        assert h["tone"] == "good" and "all 36" in h["label"]

    def test_an_advisory_still_leads_with_the_advisory(self):
        h = self._headline(5, 5, advisories=1)
        assert h["label"].startswith("1 advisory") and h["tone"] == "bad"


class TestTheCatalogCaveatReachesTheInstructions:
    """Runs 5 and 6 (2026-09-10/11): with cve_scan's coverage in the headline,
    the 8B model still answered "No"/"Yes" to "Are there outstanding CVEs?".
    The catalog's Rationale/Source column already says what such an answer
    may claim ("DECLARED dependencies only, so a zero is 'none found in what
    we can see'"); the instructions are where the model reads it."""

    def _questions(self, monkeypatch, rationale):
        from resource_explorer.surveyors import question_catalog_reader as qcr
        real = qcr.get_questions("repo")
        entries = []
        for e in real:
            e = dict(e)
            if e["question"] == "Are there outstanding CVEs?":
                e["rationale"] = rationale
            else:
                e["rationale"] = ""
            entries.append(e)
        monkeypatch.setattr(qcr, "get_questions", lambda *a, **k: entries)

    def test_with_no_section_to_live_in_the_caveat_leads_the_instructions(self, monkeypatch):
        # cve_scan has no stored result here, so it is a gap: the caveat has
        # no section to sit beside and stays in the preamble.
        self._questions(monkeypatch, "DECLARED dependencies only, so a zero is none found in what we can see.")
        c = compile_context(_registry({}), "x", "Are there outstanding CVEs?", budget=6000)
        assert "Caveat for this question, from the catalog: DECLARED dependencies only" in c.text
        assert c.text.index("Caveat") < c.text.index("Answer using only")
        assert c.manifest["coverage"]["caveat"].startswith("DECLARED dependencies only")
        assert c.manifest["coverage"]["caveat_placed_in"] == ["instructions"]

    def test_beside_the_value_when_the_section_is_packed(self, monkeypatch):
        """A caveat that limits a number belongs under that number, not in a
        preamble three sections away (the /next design round's rule, applied
        to the prompt)."""
        self._questions(monkeypatch, "DECLARED dependencies only, so a zero is none found in what we can see.")
        reg = _registry({"cve_scan": [_finding("python:click", "unrated", "1 advisory for click")]})
        c = compile_context(reg, "x", "Are there outstanding CVEs?", budget=6000)
        section = c.text[c.text.index("## cve_scan"):]
        section = section[:section.find("\n## ")] if "\n## " in section else section
        assert section.splitlines()[1].startswith("caveat: DECLARED dependencies only")
        assert "Caveat for this question" not in c.text          # not in the preamble too
        assert c.manifest["coverage"]["caveat_placed_in"] == ["cve_scan"]

    def test_the_caveat_sits_under_the_headline_when_there_is_one(self):
        from resource_explorer.context_compile import _with_caveat
        from trellis_artifact_tree.model import Rung
        rungs = {Rung.FULL: "## cve_scan\nheadline: not checked (warn)\n- checked: 0",
                 Rung.SUMMARY: "## cve_scan\n- checked: 0",
                 Rung.IDENTIFIERS: "## cve_scan\nreports: checked"}
        out = _with_caveat(rungs, "DECLARED only")
        assert out[Rung.FULL].splitlines()[2] == "caveat: DECLARED only"
        assert out[Rung.SUMMARY].splitlines()[1] == "caveat: DECLARED only"
        assert out[Rung.IDENTIFIERS] == rungs[Rung.IDENTIFIERS]

    def test_a_different_question_does_not_get_it(self, monkeypatch):
        self._questions(monkeypatch, "DECLARED dependencies only.")
        c = compile_context(_registry({}), "x", "How well documented is it?", budget=6000)
        assert "Caveat" not in c.text
        assert c.manifest["coverage"]["caveat"] == ""

    def test_a_long_caveat_is_clipped_and_says_so(self, monkeypatch):
        from resource_explorer.context_compile import CAVEAT_CHARS_FULL
        self._questions(monkeypatch, "word " * 300)
        c = compile_context(_registry({}), "x", "Are there outstanding CVEs?", budget=6000)
        line = next(l for l in c.text.splitlines() if l.startswith("Caveat"))
        assert line.endswith("[…]") and len(line) < CAVEAT_CHARS_FULL + 60
        assert c.manifest["coverage"]["caveat"] == ("word " * 300).strip()  # verbatim in the manifest

    def test_the_caveat_is_part_of_the_compile_id(self, monkeypatch):
        self._questions(monkeypatch, "one caveat")
        a = compile_context(_registry({}), "x", "Are there outstanding CVEs?", budget=6000).compile_id
        self._questions(monkeypatch, "another caveat")
        b = compile_context(_registry({}), "x", "Are there outstanding CVEs?", budget=6000).compile_id
        assert a != b

    def test_the_short_rung_carries_a_shorter_caveat(self, monkeypatch):
        self._questions(monkeypatch, "DECLARED dependencies only, so a zero is none found in what we can see. " * 4)
        # 500, not 700. At 700 this passed only because two readers
        # (architecture_summary, architecture_doc_lens) answered an EMPTY
        # registry with top-level `state: never_run`, which _has_content
        # counted as content -- so ~300 chars of bogus sections were packed
        # beside the instructions and squeezed them to SUMMARY. Measured
        # 2026-09-12: instructions alone are 695 chars at FULL and 382 at
        # SUMMARY, so 500 forces the demotion this test is actually about.
        c = compile_context(_registry({}), "x", "Are there outstanding CVEs?", budget=500)
        rung = {p["key"]: p["rung"] for p in c.manifest["packed"]}["instructions"]
        assert rung in {"SUMMARY", "IDENTIFIERS"}
        if rung == "SUMMARY":
            assert "Caveat: DECLARED" in c.text and "Caveat for this question" not in c.text


class TestAYesNoAnswerMustCarryItsEvidenceLine:
    """Run 8's sentence: added by PR #38, removed after run 9 (see
    TestInstructionVariants). What this pins now is that production does
    NOT carry it and the variant does."""

    def test_production_no_longer_carries_the_sentence(self):
        from resource_explorer.context_compile import (
            _INSTRUCTIONS, _INSTRUCTIONS_SHORT, _INSTRUCTIONS_BARE, INSTRUCTION_VARIANTS)
        for text in (_INSTRUCTIONS, _INSTRUCTIONS_SHORT, _INSTRUCTIONS_BARE):
            assert "yes" not in text.lower()
        assert "a bare yes or no is not an answer" in INSTRUCTION_VARIANTS["yesno_line"][0]



class TestInstructionVariants:
    """Run 8 (2026-09-12) could not attribute its sentence because the stored
    state and the catalog moved between runs. Variants let one run carry two
    wordings over the same state; the variant is in the compile id because it
    is in the instructions candidate's text. Run 9 measured the sentence and
    it left production; it stays as a variant."""

    def test_default_is_production_wording_without_the_sentence(self):
        from resource_explorer.context_compile import INSTRUCTION_VARIANTS, _INSTRUCTIONS
        assert INSTRUCTION_VARIANTS["default"][0] == _INSTRUCTIONS
        c = compile_context(_registry({"repo_conventions": [_finding("a")]}), "x", "q", budget=6000)
        assert "a bare yes or no is not an answer" not in c.text
        assert c.manifest["instructions_variant"] == "default"

    def test_the_yesno_variant_adds_only_the_sentence_and_changes_the_id(self):
        from resource_explorer.context_compile import INSTRUCTION_VARIANTS
        full, short = INSTRUCTION_VARIANTS["yesno_line"]
        assert "a bare yes or no is not an answer" in full and "yes/no answer must state" in short
        reg = _registry({"repo_conventions": [_finding("a")]})
        a = compile_context(reg, "x", "q", budget=6000)
        b = compile_context(reg, "x", "q", budget=6000, instructions_variant="yesno_line")
        assert "a bare yes or no is not an answer" in b.text
        assert b.manifest["instructions_variant"] == "yesno_line"
        assert a.compile_id != b.compile_id
        strip = lambda c: c.text[c.text.index("## "):]
        assert strip(a) == strip(b)          # same evidence at this budget; only the instructions differ

    def test_instructions_are_paid_for_in_evidence(self):
        """Run 9: 202 characters of instruction cost one section a rung in
        135 of 156 compiles. Pinned so the next instruction is weighed
        against the rung it displaces rather than assumed free."""
        big = {f"a{i}": [_finding("c", summary="w" * 700)] for i in range(8)}
        reg = _registry(big)
        a = compile_context(reg, "x", "q", budget=3200)
        b = compile_context(reg, "x", "q", budget=3200, instructions_variant="yesno_line")
        full = lambda c: sum(p["rung"] == "FULL" for p in c.manifest["packed"] if p["role"] == "evidence")
        assert full(a) >= full(b)
        assert a.manifest["used"] <= 3200 and b.manifest["used"] <= 3200

    def test_an_unknown_variant_is_an_error_not_a_default(self):
        with pytest.raises(ValueError):
            compile_context(_registry({}), "x", "q", budget=6000, instructions_variant="nope")


class TestAStatusOnlyEnvelopeIsAGapNotASection:
    """#46's CI (2026-09-13): a never-run reader returning the result_status
    envelope was packed as a section headed "_status: state=never_run",
    displaced documentation_coverage under the section cap, and a test lost
    "readme". Diagnosed by dwolfson-4c; the compiler's _has_content lacked the
    envelope exemption facts._has_content has always had."""

    def test_the_two_content_checks_agree_on_an_envelope(self):
        from resource_explorer.context_compile import _has_content as compiler_has
        from resource_explorer.facts import _has_content as facts_has
        env = {"_status": {"state": "never_run", "outcome": "not_run", "reason": "no rows"}}
        assert compiler_has(env) is False and facts_has(env) is False
        real = {"_status": {"state": "measured"}, "total": 62}
        assert compiler_has(real) is True and facts_has(real) is True

    def test_a_never_run_reader_lands_in_gaps_not_packed(self, monkeypatch):
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc
        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP, "dependency_analysis",
                            (lambda reg, slug: {"_status": {"state": "never_run", "outcome": "not_run"}}, None))
        c = cc.compile_context(_registry({}), "x", "What dependencies does this require?", budget=6000)
        assert "dependency_analysis" not in {p["key"] for p in c.manifest["packed"]}
        assert "dependency_analysis" in {g["key"] for g in c.manifest["gaps"]}
        assert "state=never_run" not in c.text


class TestGapsDoNotSpendCapSlots:
    """#46's CI, second finding (dwolfson-4c, 2026-09-13): against an empty
    registry eleven of the twelve cap slots went to gaps and the one analysis
    with findings, ranked 13th, was deferred. The cap must count sections
    with evidence."""

    def test_the_only_analysis_with_findings_is_packed_however_low_it_ranks(self):
        reg = _registry({"documentation_coverage": [_finding("readme", "present", "README.md found")]})
        c = compile_context(reg, "x", "is this ready to adopt?", budget=6000)
        packed = [p["key"] for p in c.manifest["packed"] if p["role"] == "evidence"]
        # architecture_doc_lens and architecture_summary report content even
        # from an empty registry (their readers synthesise defaults -- a
        # reader-side question, not the compiler's), so the assertion is
        # membership, not equality.
        assert "documentation_coverage" in packed
        assert "readme" in c.text
        assert not any(d["key"] == "documentation_coverage" for d in c.manifest["deferred"])
        # Gaps above it in the ranking are still reported; they cost nothing.
        assert len(c.manifest["gaps"]) >= 12

    def test_the_cap_still_bounds_sections_with_evidence(self):
        from resource_explorer.context_compile import MAX_EVIDENCE_SECTIONS
        from resource_explorer.surveyors.question_catalog_reader import get_questions
        ids = set()
        for e in get_questions("repo"):
            ids.update((e.get("derivation") or {}).get("analysis_ids") or [])
        reg = _registry({i: [_finding("c", summary="w" * 400)] for i in ids})
        c = compile_context(reg, "x", "how well documented is it?", budget=6000)
        evidence = [p for p in c.manifest["packed"] if p["role"] == "evidence"] + c.manifest["dropped"]
        assert len(evidence) <= MAX_EVIDENCE_SECTIONS
        assert c.manifest["deferred"]
        # Deferred entries were never resolved: they sit below the point
        # where the cap was reached, in rank order.
        ranks = [d["rank"] for d in c.manifest["deferred"]]
        assert ranks == sorted(ranks)

    def test_a_fake_registry_does_not_leak_mocks_into_sections(self):
        c = compile_context(_registry({}), "x", "is this ready to adopt?", budget=6000)
        assert "MagicMock" not in c.text


class TestAListNeverAppearsWithoutItsTotal:
    """Designer's note "The blank rail, and what a list answer should be"
    (2026-09-12) §2: the elision was honest ("… and 22 more") and the prose
    channel laundered it into "here are some of them", with 32 in the
    sentence and 10 on the page. The total now sits on the opening line of
    every partial list, so the count and its provenance are what the model
    reads first."""

    def _rungs(self, results):
        from resource_explorer.context_compile import _results_to_rungs
        return _results_to_rungs(results, "architecture_recovery")

    def test_a_long_list_of_records_opens_with_n_of_m_shown(self):
        from trellis_artifact_tree.model import Rung
        comps = [{"name": f"c{i}", "path": f"p{i}"} for i in range(32)]
        full = self._rungs({"components": comps})[Rung.FULL]
        lines = full.splitlines()
        head = next(l for l in lines if l.startswith("- components"))
        assert "32 item(s), 10 of 32 shown here" in head
        assert "… and" not in full                       # no trailing elision anywhere
        assert "item 1 of 32:" in full and "item 11 of 32:" not in full

    def test_a_short_list_says_all_shown(self):
        from trellis_artifact_tree.model import Rung
        full = self._rungs({"components": [{"name": "a"}, {"name": "b"}]})[Rung.FULL]
        assert "2 item(s), all shown" in full

    def test_a_long_scalar_list_carries_its_total_on_the_line(self):
        from trellis_artifact_tree.model import Rung
        full = self._rungs({"files": [f"f{i}.py" for i in range(25)]})[Rung.FULL]
        line = next(l for l in full.splitlines() if l.startswith("- files"))
        assert line.startswith("- files (20 of 25 shown):") and "… and" not in line
        short = self._rungs({"files": ["a.py", "b.py"]})[Rung.FULL]
        assert "- files (2 total): a.py, b.py" in short

    def test_a_nested_list_leads_with_its_count(self):
        from resource_explorer.context_compile import _compact
        assert _compact([{"dep_name": "x"}] * 36, 80).startswith("36 item(s): [")

    def test_no_partial_list_anywhere_without_a_total_beside_it(self):
        """The invariant, over a shape with every list form at once."""
        import re
        from trellis_artifact_tree.model import Rung
        results = {"components": [{"name": f"c{i}"} for i in range(15)],
                   "files": [f"f{i}" for i in range(30)],
                   "by_eco": {"py": [{"dep_name": f"d{i}"} for i in range(40)]},
                   "total": 15}
        for rung, text in self._rungs(results).items():
            if rung is Rung.IDENTIFIERS:
                continue
            assert "… and" not in text, rung
            for line in text.splitlines():
                if re.search(r"\bshown\b", line):
                    assert re.search(r"\d+ of \d+ shown", line), line


class TestTheManifestCarriesEveryListsTotal:
    """The rail sentence (/next, 2026-09-13) needs M from the manifest, not
    from re-counting the text: `manifest["lists"][section][field]`."""

    def test_totals_and_shown_per_rung_are_in_the_manifest(self, monkeypatch):
        import resource_explorer.surveyors.repo_survey_definition_adapter as adapter
        from resource_explorer import context_compile as cc
        results = {"components": [{"name": f"c{i}"} for i in range(32)],
                   "by_eco": {"py": [f"d{i}" for i in range(25)]},
                   "total": 32, "_status": {"state": "measured"}}
        monkeypatch.setitem(adapter.REPO_ANALYSIS_RESULTS_MAP, "architecture_recovery",
                            (lambda reg, slug: results, None))
        c = cc.compile_context(_registry({}), "x", "What components exist in this repository, and what kind is each?",
                               budget=6000)
        lists = c.manifest["lists"]["architecture_recovery"]
        assert lists["components"] == {"total": 32, "shown": {"FULL": 10, "SUMMARY": 3}}
        assert lists["by_eco.py"] == {"total": 25, "shown": {"FULL": 20, "SUMMARY": 3}}
        assert "total" not in lists                     # scalars are not lists
        rung = {p["key"]: p["rung"] for p in c.manifest["packed"]}["architecture_recovery"]
        assert rung in lists["components"]["shown"]      # the UI picks N by the packed rung

    def test_only_packed_sections_appear(self, monkeypatch):
        c = compile_context(_registry({"repo_conventions": [_finding("a")]}), "x", "q", budget=6000)
        assert set(c.manifest["lists"]) <= {p["key"] for p in c.manifest["packed"]}


class TestVulnerabilityQuestionBindsToCveScan:
    """Walk 2026-10-12: "...known security vulnerabilities in its dependencies?" compiled without cve_scan. The
    catalog binds cve_scan to "Are there outstanding CVEs?", which shares no token with it, so cve_scan ranked on
    catalog position alone and was deferred below the section cap."""

    QUESTION = "Does this repository have any known security vulnerabilities in its dependencies?"

    def _every_analysis_has_a_result(self):
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses
        ids = [a["id"] for a in get_analyses("repo")]
        return _registry({i: [_finding("c", "ok", "detail " * 40)] for i in ids})

    def test_cve_scan_is_packed_not_deferred(self):
        c = compile_context(self._every_analysis_has_a_result(), "x", self.QUESTION)
        assert "cve_scan" in [p["key"] for p in c.manifest["packed"]]
        assert "cve_scan" not in [d["key"] for d in c.manifest["deferred"]]
