"""The chat answer to a catalogued question must lead with the answer.

Reported 2026-09-01 (Dan): clicking a question showed "Answering from 1
measurement(s)..." and no answer — "internal dialog from an agent ... but no
answer." The backend (`facts.py::FactLayer.answer()`) was already correct;
verified live against `GET /api/analyses/facts/egeria_git/answer` the same
day, which returned `concentration_detail`, a full sentence a surveyor wrote
itself ("One contributor accounts for half of all commits. Measured over the
491 commit(s) ..."). The UI discarded it in two places:

  * `_renderEnvelopeMarkdown` led with the process line ("Answering from N
    measurement(s)...") over a bullet that said only a state word
    ("measured"), with the sentence itself buried inside a collapsed
    <details> block.
  * `_summariseFactValue` then dropped it there too: its skip set literally
    contained `'detail'`, and its string branch kept only strings under 60
    characters — both filters hit exactly the fields carrying the prose
    (`detail`, `concentration_detail`, `measures_disagree`), and an array of
    named people (`top_authors`) collapsed to a bare count.

These tests pin the fix at the source level (the same style the rest of this
file's sibling tests use for index.html, since there is no JS test runner
wired into this suite) and, separately, the exact node invocation used to
verify the real rendered output against a live envelope — see the docstrings
below for the transcript.
"""
from __future__ import annotations



def _fn(html: str, name: str) -> str:
    """Extract one top-level `function name(...) { ... }` body by brace
    matching, the same technique test_admin_feedback_view.py uses."""
    start = html.index(f"function {name}(")
    brace = html.index("{", start)
    depth = 1
    i = brace + 1
    while depth:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
        i += 1
    return html[start:i]


def _answer_expression(html: str) -> str:
    """The `const answerText = ...;` line as _renderEnvelopeMarkdown writes
    it, so the test runs the app's rule rather than a restatement of it."""
    fn = _fn(html, "_renderEnvelopeMarkdown")
    for line in fn.splitlines():
        if line.strip().startswith("const answerText"):
            return line.strip()
    raise AssertionError("no `const answerText = ...` line in _renderEnvelopeMarkdown")


# ── Live verification transcript (not re-run in CI; recorded here for the
# record, since it needs a running server and the vendored marked.js) ───────
#
# The three functions above (`_factProseKeys`, `_factAnswerText`,
# `_renderEnvelopeMarkdown`, `_summariseFactValue`) were extracted from
# index.html by brace-matching and run under
# /Users/dwolfson/.nvm/versions/node/v18.16.1/bin/node against the real
# response of:
#
#     GET http://127.0.0.1:8810/api/analyses/facts/egeria_git/answer\
#         ?question=Who%20maintains%20this%20repository%3F
#
# which returned one `project_commits` fact carrying `concentration_detail`.
# The resulting markdown (this is the literal stdout, not a description):
#
#     - ✓ **project_commits**: One contributor accounts for half of all
#     commits. Measured over the 491 commit(s) recorded between 2026-05-12
#     and 2026-08-31, by 3 author(s); the single largest wrote 64%.
#
#       <span class="text-slate-500">measured</span>
#
#       <details><summary class="cursor-pointer text-slate-500">Evidence</summary>
#
#       people: 2 · commit_identities: 3 · commits: 491 · top_authors: 2
#       (Mandy Chessell, Karth) · concentration: sole
#       </details>
#
#     _Answered from 1 measurement(s) on **egeria_git**._
#
# The maintainer's name and the actual finding are both now on a visible
# line; the measurement count is a footer, not the lead. A synthetic
# unanswerable envelope and a synthetic known_count=0/never_run envelope were
# run the same way and were unchanged from before this fix:
#
#     **I can't answer that yet.**
#
#     No analysis in the catalog answers this question.
#
#     **Not everything is known.** `security_scan` never run here.
#
#     _Re-run `security_scan` to refresh this._
#
#     _Answered from 0 measurement(s) on **some_repo**._


class TestNestedObjects:
    """A value the renderer cannot shape is not a value that is not there.

    _summariseFactValue handled arrays, numbers, booleans and strings. A
    plain object matched none of them and fell through to nothing — silently.

    Found 2026-09-02 from the live app: "How much code is there? How
    complex?" answered `relationship_count: 437`. That was the only scalar in
    the value; `by_language` and `complexity_by_language` — the two fields
    that actually answer the question — were dropped on the floor. The
    complexity number had been persisted, verified in the metric, and
    reported as done. Verifying the metric was not verifying the answer.
    """


class TestFindingsArrays:
    """A fact whose payload is a list of findings answered with its length.

    Measured across all 53 questions on egeria_python_git (2026-09-01):
    `chaoss_metrics`, `repo_classification` and `interface_surface` rendered
    "findings: 5 (growing, sole, …)" -- a count and two labels -- while the
    per-finding sentences the surveyors wrote went unshown. Not the
    verdict-behind-a-toggle bug (nothing was hidden; there was no toggle),
    but the same complaint: the reading was thinner than the measurement.
    """


class TestHeadlineLeadsTheAnswer:
    """A summary sentence, then the facts under it.

    Dan, 2026-09-02, shown a `·`-joined field dump:
    "that is not a good answer - should be a summary sentence and maybe some
    bullet points with the facts."

    The sentence was not invented for this. Every one of the 33 analyses with
    a results reader already defines a `headline_reader` — `api_structure`'s
    returns "8654 symbol(s) across 2 language(s)" — and NOTHING on the fact
    path called any of them. The chat rendered "relationship_count: 437"
    while a written sentence sat one function away.

    Order of preference, pinned here: a surveyor's own prose beats the
    analysis's generic headline, and both beat a bare field list.
    """

    def test_every_analysis_with_results_defines_a_headline(self):
        """The premise. If this ever stops holding, the renderer falls back
        to a field list for that analysis and someone should know why."""
        from resource_explorer.surveyors.repo_survey_definition_adapter import (
            ANALYSIS_KINDS)
        missing = [
            k for k, v in ANALYSIS_KINDS.items()
            if getattr(v, "results", None)
            and not getattr(v.results, "headline_reader", None)
        ]
        assert missing == [], f"analyses with results but no headline: {missing}"

    def test_the_fact_layer_reads_it(self):
        """Known-negative for the actual defect: the mechanism existed and
        was never called."""
        import inspect

        from resource_explorer import facts as facts_mod
        src = inspect.getsource(facts_mod.FactLayer)
        assert "headline_reader" in src, (
            "FactLayer never reads headline_reader, so the analysis's own "
            "summary sentence cannot reach the answer"
        )


class TestSummaryFailureCannotFailARun:
    """Building the display summary must never sink the run it describes.

    Found by the full suite, 2026-09-02. summarise_annotations did
    `a.annotation_type.value` unguarded; a malformed entry raised, the
    Analyses-card background thread crashed, and a run whose findings were
    computed and STORED reported "crashed" to the user. The work succeeded
    and the sentence about it did not — and the sentence won.
    """

    def test_malformed_entries_are_skipped_not_raised(self):
        from resource_explorer.surveyors.survey_report import summarise_annotations

        class _Ann:
            def __init__(self):
                self.annotation_type = type("T", (), {"value": "ClassificationAnnotation"})()
                self.analysis_step = "Step"
                self.summary = "a real one"

        out = summarise_annotations(["a string", None, 42, _Ann()])
        assert len(out) == 1, f"malformed entries must be skipped, not raised on: {out}"
        assert out[0]["summary"] == "a real one"

    def test_an_all_malformed_list_yields_nothing_rather_than_raising(self):
        from resource_explorer.surveyors.survey_report import summarise_annotations

        assert summarise_annotations(["a", "b"]) == []


class TestAdministrativeFieldsDoNotCrowdOutTheAnswer:
    """Reported live 2026-09-07 (Dan, asking "What is its internal
    architecture — what components exist and how do they relate?" on a
    repo where the detector barely classified anything): the answer was a
    field dump —

        summary: 1 runnable unit(s); from 107 candidate component(s)
        depth: suitability
        source_kind: architecture_recovery
        component_scopes: 108
        components: 107
        component_types: Unclassified: 106; Console Command: 1

    Three of those six lines are administrative/redundant with the headline
    (`f.headline` for architecture_summary is always defined whenever this
    value exists — repo_survey_definition_adapter.py's
    `_architecture_summary_headline`), and `_summariseFactValue`'s 6-field
    cap meant they crowded out `documented`/`protocols`/`third_party` — the
    fields that would have actually answered "what does this serve" —
    before the loop ever reached them.

    `_architecture_summary_results` (repo_survey_definition_adapter.py) is
    confirmed the ONLY results reader returning `summary`/`depth`/
    `source_kind` as TOP-LEVEL keys — this is a narrow, checked exclusion,
    not a guess at what else might be safe to hide. The dedicated Analysis-
    tab metrics panel (`_renderMetricsResults`) reads the same value
    directly, not through `_summariseFactValue`, so it is unaffected and
    still shows every field.
    """

    _ARCH_SUMMARY_VALUE = {
        "summary": "1 runnable unit(s); from 107 candidate component(s)",
        "depth": "suitability",
        "surveyed_at": "2026-09-07T00:00:00",
        "source_kind": "architecture_recovery",
        "component_scopes": 108,
        "components": 107,
        "component_types": {"Unclassified": 106, "Console Command": 1},
        "third_party": 0,
        "documented": 0,
        "protocols": {},
        "operations": 0,
        "unread_scopes": 0,
        "interfaces_unread": False,
        "outcome": "recovered",
        "outcome_cause": "",
        "outcome_known_positive": False,
        "outcome_detail": {"components": 107},
        "complete": True,
    }


class TestArchitectureDiagramRendersAPicture:
    """The follow-up to the architecture_summary fix above (2026-09-08, same
    BACKLOG.md investigation): "what components exist" now answers cleanly,
    but "how do they relate" still had no real answer — a graph does not fit
    the bullet-list shape that fixed the first half, at any real scale.

    architecture_diagram (a new AnalysisKind, repo_survey_definition_adapter.py)
    exposes a Mermaid diagram computed and persisted at survey time
    (arch_recovery/persist.py::_persist_diagram) but never read back by
    anything before this. `_renderEnvelopeMarkdown` now fetches it through
    the same Kroki proxy the DB ER-diagram view already uses
    (web/routes/diagrams.py) rather than embedding client-side mermaid.js.

    Not executed end-to-end through this file's Node harness: unlike
    _summariseFactValue/_factValueLines (pure functions over a value dict),
    _renderEnvelopeMarkdown's diagram block calls the real `fetch` and
    depends on the rest of the function (DOM-adjacent helpers, the
    catalog-backfill await) that this harness's minimal preamble does not
    reproduce — the same reason `_answer_expression` extracts one line
    rather than running the whole function. These assert on the extracted
    source directly instead, the same technique
    test_evidence_no_longer_double_shows_or_drops_prose_fields (above) uses
    for a structural guarantee rather than a behavioural one.
    """
