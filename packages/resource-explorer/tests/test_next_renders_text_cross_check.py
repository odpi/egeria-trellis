"""BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §D: `facts.py`'s `_renders_text` is a
Python hand-mirror of `readEnvelope`'s rendering rungs in
`web/static/next/app.js` (now split into `web/static/next/envelope.js`, see
that module's own header comment). There is no shared source between the two
implementations, and they have drifted twice already — once the day
`_renders_text` was written, and again in Slice 21a (the Level column
shipped, `_headline_for` never read it, and container-level questions ticked
✓ on resource-level sentences for two days, caught at a live gate, not by a
test). Every pre-existing test asserts only the Python side.

This file feeds the SAME corpus of fact envelopes to both implementations,
via node (the pattern `test_next_journal_fidelity.py` and
`test_next_enrichment_persistence.py` already establish for this repo):
copy the target `.js` file to a `.mjs` sibling in `tmp_path` so node treats it
as an ES module (`TestTheFormatter._run` in test_next_journal_fidelity.py),
and extract a function's own source text out of app.js by string-slicing
between its declaration and the next top-level declaration
(`_extract` in test_next_enrichment_persistence.py) for the handful of small
helpers (`esc`, `tnum`, `isFullyAnswered`) that stayed in app.js rather than
moving to envelope.js, since app.js itself cannot be imported whole under
node (it runs `Auth.init(start)` and reads `document.getElementById(...)` at
module top level, both of which throw with no DOM/no auth.js present).

Two things are cross-checked, per corpus case:

  (a) renders-or-not — Python's `FactLayer._renders_text(fact)` against
      whether JS's `readEnvelope(...).answer` comes out non-empty for an
      envelope holding exactly that one fact. `_renders_text` does not
      itself consult `fact.is_known` (`test_slice17c_renderable_answer_
      gate.py`'s `TestRendersText` calls it directly on facts of any state,
      including ones that are not "known"), and neither does the per-fact
      branch inside `readEnvelope`'s own sentence loop -- only the OUTER
      `known = facts.filter(f.is_known)` filter (which both `_check_level`
      and `readEnvelope` apply before ever reaching this per-fact logic)
      does. To compare the two primitives on equal footing, the JS side's
      envelope forces `is_known: true` on the single fact regardless of its
      state, mirroring how `test_slice17c`'s Python tests call
      `_renders_text` directly, bypassing the outer filter, on facts of
      every state.

  (b) the level verdict -- Python's `FactLayer._check_level(env, question)`
      sets `env.level_mismatch`/`env.answerable`; JS's `isFullyAnswered(env)`
      is the one sanctioned way to turn that into "does this get a
      checkmark" (its own docstring: "callers that count or gate on
      'answered' must use this, not `env.answerable` alone"). Here `is_known`
      is NOT forced -- this half exercises the real pipeline shape, where an
      unrenderable fact of a not-known state never reaches `_check_level`'s
      own `known` filter either.

The corpus spans the five fact states BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §D
names (measured, nothing-found, not-established, no-reader, measured-within-
credential-scope) × with/without a headline × the four question levels
(resource, container, member, field) = 40 cases, plus four extra cases that
exercise `readEnvelope`'s rung 2 (prose) and rung 3 (scalar fallback, and the
db_resilience-shaped nested-dict-only value that rung 3 can never render --
`test_slice17c_renderable_answer_gate.py`'s own trigger) at resource level,
where headline alone would not have distinguished them. 44 total. The count
is asserted directly so a shrunk corpus — someone "simplifying" the loop and
quietly dropping a dimension — fails loudly rather than silently reducing
coverage.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from resource_explorer.facts import Envelope, Fact, FactLayer, clear_target_shape_cache
from resource_explorer.surveyors.result_status import (
    MEASURED,
    MEASURED_WITHIN_CREDENTIAL_SCOPE,
    NO_READER,
    NOT_ESTABLISHED,
    NOTHING_FOUND,
)

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"
APP_JS = NEXT / "app.js"
ENVELOPE_JS = NEXT / "envelope.js"
FORMAT_JS = NEXT / "format.js"

STATES = [MEASURED, NOTHING_FOUND, NOT_ESTABLISHED, NO_READER, MEASURED_WITHIN_CREDENTIAL_SCOPE]
LEVELS = ["resource", "container", "member", "field"]

ANALYSIS_ID = "cross_check_analysis"


def _extract(src: str, signature: str) -> str:
    """The text of one top-level function, from its `function`/`export
    function` line up to (not including) the next top-level `function`
    declaration. Same helper as test_next_enrichment_persistence.py's."""
    start = src.index(signature)
    candidates = []
    for marker in ("\nfunction ", "\nexport function ", "\nconst ", "\nasync function "):
        try:
            candidates.append(src.index(marker, start + len(signature)))
        except ValueError:
            pass
    end = min(candidates)
    return src[start:end]


def _grid_corpus() -> list[dict]:
    """The 5 states x 2 headline states x 4 levels grid the brief asks for."""
    cases = []
    for state in STATES:
        for has_headline in (True, False):
            for level in LEVELS:
                cases.append({
                    "name": f"{state}/{'headline' if has_headline else 'no-headline'}/{level}",
                    "state": state,
                    "headline": "A written summary sentence." if has_headline else "",
                    "value": {},
                    "level": level,
                })
    return cases


def _rung_coverage_corpus() -> list[dict]:
    """Rung 2 (prose) and rung 3 (scalar / nested-dict-only) cases, at
    resource level, where headline presence alone does not distinguish the
    rungs `readEnvelope` and `_renders_text` both walk through in order."""
    return [
        {
            "name": "measured/prose-only/resource",
            "state": MEASURED, "headline": "",
            "value": {"detail": "Some real prose, no headline."},
            "level": "resource",
        },
        {
            "name": "measured/scalar-only/resource",
            "state": MEASURED, "headline": "",
            "value": {"table_count": 56},
            "level": "resource",
        },
        {
            "name": "measured/nested-dicts-only-renders-nothing/resource",
            # The exact db_resilience shape from test_slice17c_renderable_
            # answer_gate.py: rung 3 skips every list/object field by
            # design, so this must render NOTHING on both sides.
            "state": MEASURED, "headline": "",
            "value": {
                "replication": {"is_in_recovery": False, "replicas": []},
                "wal_archiving": {"archive_mode": "off"},
            },
            "level": "resource",
        },
        {
            "name": "nothing_found/prose-only/resource",
            # NOTHING_FOUND's own special case only fires when there is
            # NEITHER headline NOR prose -- prose present must win instead
            # of the synthesized "ran and found nothing" sentence.
            "state": NOTHING_FOUND, "headline": "",
            "value": {"summary": "Ran; genuinely nothing matched the filter."},
            "level": "resource",
        },
    ]


def _corpus() -> list[dict]:
    return _grid_corpus() + _rung_coverage_corpus()


@pytest.fixture(autouse=True)
def _clear_shape_cache():
    clear_target_shape_cache()
    yield
    clear_target_shape_cache()


def _fact_layer(monkeypatch) -> FactLayer:
    monkeypatch.setattr(
        "resource_explorer.surveyors.analysis_catalog_reader.get_analyses",
        lambda resource_type, **kwargs: [{"id": ANALYSIS_ID, "target_shape": "whole_resource_only"}],
    )
    fl = FactLayer.__new__(FactLayer)  # skip __init__ -- no registry needed
    fl.resource_type = "database"
    # `_check_level` reaches `_level_specific_headline_exists` -> `_map(...)`
    # for any headlined sub-resource-level case, which reads `_maps_cache`
    # directly -- `__init__` sets it and `__new__` skips `__init__`.
    fl._maps_cache = {}
    return fl


def _python_side(monkeypatch, cases: list[dict]) -> list[dict]:
    """One fresh `FactLayer`/`Envelope` per case -- `_check_level` mutates
    the envelope it is given, so cases must not share one."""
    out = []
    for case in cases:
        fl = _fact_layer(monkeypatch)
        fact = Fact(analysis_id=ANALYSIS_ID, state=case["state"],
                    value=dict(case["value"]), headline=case["headline"])
        renders = fl._renders_text(fact)

        env = Envelope(subject="corpus_test")
        env.facts = [fact]
        fl._check_level(env, {"levels": [case["level"]]})
        fully_answered = bool(env.answerable and not env.level_mismatch)

        out.append({
            "name": case["name"],
            "renders": renders,
            "fully_answered": fully_answered,
            "answerable": env.answerable,
            "level_mismatch": env.level_mismatch,
            "fact_as_dict": fact.as_dict(),
        })
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestRendersTextMatchesReadEnvelope:
    def _js_side(self, py_side: list[dict], tmp_path: Path) -> list[dict]:
        # envelope.js imports whenMs from format.js by absolute /static/...
        # path -- rewritten to a local sibling import so node can resolve it,
        # same trick test_next_enrichment_persistence.py uses for its own
        # format.js copy.
        format_mjs = tmp_path / "format.mjs"
        format_mjs.write_text(FORMAT_JS.read_text(encoding="utf-8"), encoding="utf-8")
        envelope_src = ENVELOPE_JS.read_text(encoding="utf-8")
        envelope_src = envelope_src.replace("/static/next/format.js", "./format.mjs")
        envelope_mjs = tmp_path / "envelope.mjs"
        envelope_mjs.write_text(envelope_src, encoding="utf-8")

        app_src = APP_JS.read_text(encoding="utf-8")
        esc_fn = _extract(app_src, "export function esc(s) {")
        tnum_fn = _extract(app_src, "export function tnum(html) {")
        is_fully_answered_fn = _extract(app_src, "function isFullyAnswered(env) {")

        # Everything the JS side needs per case: the fact (as the wire
        # format actually serializes it), and the two Envelope-level fields
        # `_check_level` computed (`answerable`, `level_mismatch`) -- these
        # are Python's own verdict, carried across so JS's `isFullyAnswered`
        # can be checked against exactly what Python decided, not against a
        # value JS re-derives on its own (it has no way to: computing
        # `level_mismatch` from scratch needs the catalog's target_shape
        # data, which only facts.py reads).
        cases_json = json.dumps([
            {
                "name": p["name"],
                "fact": p["fact_as_dict"],
                "answerable": p["answerable"],
                "level_mismatch": p["level_mismatch"],
            }
            for p in py_side
        ])

        script = f"""
import {{ readEnvelope }} from './envelope.mjs';
{esc_fn.replace('export function esc', 'function esc')}
{tnum_fn.replace('export function tnum', 'function tnum')}
{is_fully_answered_fn}

const cases = {cases_json};
const out = cases.map((c) => {{
  // (a) renders-or-not: is_known forced true so this exercises the SAME
  // state-agnostic per-fact predicate `_renders_text` is (see module
  // docstring) -- the outer known-filter is a separate, already-tested gate.
  const rendersEnv = {{ facts: [{{ ...c.fact, is_known: true }}] }};
  const lines = readEnvelope({{ question: c.name }}, rendersEnv, esc, tnum);
  const renders = !!(lines.answer && lines.answer.length);

  // (b) the level verdict: Python's own answerable/level_mismatch, read
  // back through the one sanctioned JS accessor for "does this get a tick".
  const levelEnv = {{ answerable: c.answerable, level_mismatch: c.level_mismatch }};
  return {{ name: c.name, renders, fullyAnswered: isFullyAnswered(levelEnv) }};
}});
console.log(JSON.stringify(out));
"""
        script_path = tmp_path / "run.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_corpus_size_is_the_documented_44(self):
        """A shrunk corpus must fail loudly, not silently cover less."""
        assert len(_corpus()) == 44

    def test_python_and_js_agree_on_every_case(self, monkeypatch, tmp_path):
        cases = _corpus()
        py_side = _python_side(monkeypatch, cases)
        js_side = self._js_side(py_side, tmp_path)
        assert len(js_side) == len(py_side) == len(cases)

        failures = []
        for case, p, j in zip(cases, py_side, js_side):
            assert j["name"] == case["name"] == p["name"]
            if p["renders"] != j["renders"]:
                failures.append(
                    f"{case['name']}: renders-or-not disagreement -- "
                    f"python={p['renders']!r} js={j['renders']!r}")
            if p["fully_answered"] != j["fullyAnswered"]:
                failures.append(
                    f"{case['name']}: level-verdict disagreement -- "
                    f"python={p['fully_answered']!r} js={j['fullyAnswered']!r}")
        assert not failures, "\n".join(failures)


# ────────────────────────────────────────────────────────────────────────
# G2 (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.4): the Questions
# headline slot now holds exactly ONE sentence for a question backed by
# SEVERAL analyses, chosen by `envelope.js`'s `leadAnalysisId()` -- the
# first `analysis_ids` entry actually named in the catalog's own `note`
# ("Answering Analysis" column) text, not `analysis_ids[0]` itself.
# `question_catalog.yaml`'s own `preliminary_fit` question is the reply's
# worked example: `analysis_ids` lists it LAST (after the three signals it
# consumes), but its `note` names it FIRST -- "preliminary_fit (design
# §16.3, §16.5 -- zero-fetch comparison of ...)". Reading index 0 there
# would lead the headline with `grain_determination`'s sentence (an input),
# not `preliminary_fit`'s own verdict (the reply's own quoted "actual
# answer") -- exactly the bug this corpus pins.
#
# This is a SEPARATE corpus/class from the 44-case grid above rather than a
# growth of it: that grid's own shape is one Fact per case (`_python_side`
# builds exactly one `Fact`), and `test_corpus_size_is_the_documented_44`
# guards that shape not shrinking silently. A lead-selection case needs
# SEVERAL facts and a `note`/`analysis_ids` pair the existing schema has no
# room for, so it gets its own corpus/fixture pair here rather than being
# forced into the single-fact one.
# ────────────────────────────────────────────────────────────────────────


def _lead_analysis_id(analysis_ids: list[str], note: str) -> str | None:
    """Test-only Python mirror of `envelope.js`'s `leadAnalysisId()` -- there
    is no production Python equivalent to cross-check against: `facts.py`
    builds one `Fact` per analysis and leaves picking a single headline
    among several entirely to the JS rendering layer (see `FactLayer.facts`/
    `_headline_for`, neither of which combines more than one analysis's
    reading). Kept in sync by hand with the JS implementation, same
    reasoning `facts.py`'s own `_renders_text` docstring gives for why IT
    has no shared source with `readEnvelope` either -- this is the same
    kind of pair, one level up.
    """
    best: str | None = None
    best_pos: int | None = None
    for analysis_id in analysis_ids:
        m = re.search(rf"\b{re.escape(analysis_id)}\b", note or "")
        if m and (best_pos is None or m.start() < best_pos):
            best, best_pos = analysis_id, m.start()
    return best


#: The reply's own worked example, verbatim from `question_catalog.yaml`'s
#: "Could this be in scope for what I am looking for — worth the full
#: pass?" entry (`analysis_ids` order and `note` text both copied, not
#: paraphrased, so a future edit to either drifts this test rather than
#: silently stops covering the real shape).
_FIT_ANALYSIS_IDS = [
    "grain_determination", "subject_signals", "coverage_signals", "preliminary_fit",
]
_FIT_NOTE = (
    "preliminary_fit (design §16.3, §16.5 — zero-fetch comparison of subject_signals, "
    "coverage_signals and grain_determination's time grain against a supplied data "
    "requirement, with per-input confidence."
)


def _lead_selection_corpus() -> list[dict]:
    """Each case carries TWO different expectations, deliberately kept apart:

    `expected_note_lead` -- what `leadAnalysisId()`/its Python mirror return
    from `(analysis_ids, note)` ALONE, ignoring which facts the envelope
    actually carries. `None` is a real, correct answer here (the note names
    none of the ids), not a failure.

    `expected_answer_analysis` -- which analysis's own headline actually
    ends up in `readEnvelope(...).answer`, after `leadAnalysisId`'s result
    (if any) falls through `analysis_ids`' own order and then fact-arrival
    order for an analysis that actually produced a sentence. The two agree
    when the note names an id AND that id's own fact renders (case 1); they
    diverge exactly in the two fallback cases (2 and 3), which is the point
    of keeping them separate rather than one shared field.
    """
    return [
        {
            "name": "fit_question/consuming_order/preliminary_fit_leads",
            "analysis_ids": _FIT_ANALYSIS_IDS,
            "note": _FIT_NOTE,
            "facts": [
                {"analysis_id": "grain_determination", "state": MEASURED,
                 "headline": "68 of 87 table(s) have a determined grain."},
                {"analysis_id": "subject_signals", "state": MEASURED,
                 "headline": "Subject terms: customer, sales, product."},
                {"analysis_id": "coverage_signals", "state": MEASURED,
                 "headline": "Coverage: 2011-05 through 2014-06."},
                {"analysis_id": "preliminary_fit", "state": MEASURED,
                 "headline": "NO REQUIREMENT DECLARED — no lens was supplied, "
                              "so fit is not a question that has an answer here.",
                 "value": {"lens_declared": False, "verdict": "no_requirement_declared"}},
            ],
            "expected_note_lead": "preliminary_fit",
            "expected_answer_analysis": "preliminary_fit",
        },
        {
            # No id from `analysis_ids` appears in the note at all --
            # `leadAnalysisId` correctly returns nothing, and the slot falls
            # back to `analysis_ids`' own order (index 0) -- the same
            # behaviour this question would have had before this change.
            "name": "note_names_no_id/falls_back_to_analysis_ids_order",
            "analysis_ids": ["repository_health", "foss_scorecard"],
            "note": "Read from the survey's own community metrics.",
            "facts": [
                {"analysis_id": "repository_health", "state": MEASURED,
                 "headline": "Actively maintained."},
                {"analysis_id": "foss_scorecard", "state": MEASURED,
                 "headline": "Scorecard: 7.2 of 10."},
            ],
            "expected_note_lead": None,
            "expected_answer_analysis": "repository_health",
        },
        {
            # The note names `preliminary_fit` first (`leadAnalysisId` is
            # right to say so -- that is what the text says), but the
            # envelope carries no fact for it at all (never run / not known).
            # The slot must still show something: it falls through to the
            # next analysis that actually produced a sentence.
            "name": "leads_own_fact_missing/falls_through_to_next_producing_fact",
            "analysis_ids": ["preliminary_fit", "grain_determination"],
            "note": "preliminary_fit + grain_determination",
            "facts": [
                {"analysis_id": "grain_determination", "state": MEASURED,
                 "headline": "12 of 40 table(s) have a determined grain."},
            ],
            "expected_note_lead": "preliminary_fit",
            "expected_answer_analysis": "grain_determination",
        },
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestLeadAnalysisSelection:
    def _js_side(self, cases: list[dict], tmp_path: Path) -> list[dict]:
        format_mjs = tmp_path / "format2.mjs"
        format_mjs.write_text(FORMAT_JS.read_text(encoding="utf-8"), encoding="utf-8")
        envelope_src = ENVELOPE_JS.read_text(encoding="utf-8")
        envelope_src = envelope_src.replace("/static/next/format.js", "./format2.mjs")
        envelope_mjs = tmp_path / "envelope2.mjs"
        envelope_mjs.write_text(envelope_src, encoding="utf-8")

        app_src = APP_JS.read_text(encoding="utf-8")
        esc_fn = _extract(app_src, "export function esc(s) {")
        tnum_fn = _extract(app_src, "export function tnum(html) {")

        cases_json = json.dumps([
            {
                "name": c["name"],
                "entry": {"question": c["name"], "analysis_ids": c["analysis_ids"], "note": c["note"]},
                "facts": [
                    {**{"value": {}, "note": "", "can_run": [], "evidence_only": False,
                        "last_run_at": "", "provenance": "measured"}, **f, "is_known": True}
                    for f in c["facts"]
                ],
            }
            for c in cases
        ])

        script = f"""
import {{ readEnvelope, leadAnalysisId }} from './envelope2.mjs';
{esc_fn.replace('export function esc', 'function esc')}
{tnum_fn.replace('export function tnum', 'function tnum')}

const cases = {cases_json};
const out = cases.map((c) => {{
  const lead = leadAnalysisId(c.entry);
  const env = {{ facts: c.facts }};
  const lines = readEnvelope(c.entry, env, esc, tnum);
  return {{ name: c.name, lead, answer: lines.answer }};
}});
console.log(JSON.stringify(out));
"""
        script_path = tmp_path / "run_lead.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_corpus_covers_lead_present_and_lead_absent(self):
        """Named cases, not a bare count -- each one documents a distinct
        shape `leadAnalysisId` has to get right (see the corpus's own
        per-case comments)."""
        names = {c["name"] for c in _lead_selection_corpus()}
        assert names == {
            "fit_question/consuming_order/preliminary_fit_leads",
            "note_names_no_id/falls_back_to_analysis_ids_order",
            "leads_own_fact_missing/falls_through_to_next_producing_fact",
        }

    def test_python_mirror_and_js_agree_on_the_lead(self, tmp_path):
        """Both implementations must agree on what the NOTE ALONE says --
        `expected_note_lead`, which is `None` in the two fallback cases
        (see `_lead_selection_corpus`'s own docstring for why that is a
        correct answer, not a gap)."""
        cases = _lead_selection_corpus()
        js_side = self._js_side(cases, tmp_path)
        assert len(js_side) == len(cases)

        failures = []
        for case, j in zip(cases, js_side):
            assert j["name"] == case["name"]
            py_lead = _lead_analysis_id(case["analysis_ids"], case["note"])
            expected = case["expected_note_lead"]
            if py_lead != expected:
                failures.append(
                    f"{case['name']}: python mirror disagrees with the corpus's own "
                    f"expectation -- python={py_lead!r} expected={expected!r}")
            if j["lead"] != expected:
                failures.append(
                    f"{case['name']}: js leadAnalysisId disagrees with the corpus's own "
                    f"expectation -- js={j['lead']!r} expected={expected!r}")
        assert not failures, "\n".join(failures)

    def test_the_headline_slot_actually_uses_the_chosen_lead(self, tmp_path):
        """`leadAnalysisId` picking the right id is necessary but not
        sufficient -- this checks `readEnvelope` actually RENDERS the
        EXPECTED analysis's own headline as `lines.answer` (`
        expected_answer_analysis`, which already accounts for both
        fallbacks), not merely that `leadAnalysisId` agrees with itself.
        `lines.answer` is HTML (`tnum()` wraps numbers in spans), so the
        comparison strips tags rather than doing a raw substring match."""
        cases = _lead_selection_corpus()
        js_side = self._js_side(cases, tmp_path)
        by_name = {c["name"]: c for c in cases}
        for j in js_side:
            case = by_name[j["name"]]
            lead_fact = next(
                f for f in case["facts"] if f["analysis_id"] == case["expected_answer_analysis"])
            plain_answer = re.sub(r"<[^>]+>", "", j["answer"])
            assert lead_fact["headline"] in plain_answer, (
                f"{j['name']}: lines.answer did not carry "
                f"{case['expected_answer_analysis']}'s own headline -- "
                f"answer={j['answer']!r}")


# ────────────────────────────────────────────────────────────────────────
# Live gate follow-up (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.2/§2.4,
# re-checked on 8813 against adventureworks 2026-09-28): `leadAnalysisId`
# picking the right analysis was necessary but not sufficient -- the
# headline slot still showed that analysis's ENTIRE multi-sentence
# explanation (a ~120-word paragraph on the fit row and on the "Which
# resources cover similar subjects…" row), not the one sentence the reply
# asks for. `envelope.js`'s `firstSentence()` is the fix; this cross-checks
# it the same way `leadAnalysisId` is cross-checked above -- there is no
# production Python equivalent (same reasoning `_lead_analysis_id`'s own
# docstring gives), so this is a test-only mirror kept in sync by hand.
# ────────────────────────────────────────────────────────────────────────


def _first_sentence(text: str | None) -> str | None:
    """Test-only Python mirror of `envelope.js`'s `firstSentence()`."""
    if not text:
        return text
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
            i += 1
            continue
        if c == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            if c in ".!?" and i + 1 < n and text[i + 1] == " " and i + 2 < n and text[i + 2].isupper():
                return text[: i + 1]
            if text.startswith(" · ", i):
                return text[:i]
        i += 1
    return text


def _first_sentence_corpus() -> list[dict]:
    return [
        {
            "name": "plain_two_sentence_paragraph",
            "text": "9 components recovered. Nine of 107 component paths were reviewed by hand.",
            "expected": "9 components recovered.",
        },
        {
            "name": "the_reported_fit_row_paragraph",
            # Shortened from the real ~120-word adventureworks paragraph,
            # same shape: a lead sentence, then supporting sentences.
            "text": (
                "NO REQUIREMENT DECLARED — no lens was supplied, so fit is not a "
                "question that has an answer here. A lens names the tables, columns "
                "and time window a caller cares about. Without one, coverage_signals "
                "and subject_signals have nothing to compare against."
            ),
            "expected": (
                "NO REQUIREMENT DECLARED — no lens was supplied, so fit is not a "
                "question that has an answer here."
            ),
        },
        {
            "name": "rollup_lead_then_middot_separator",
            "text": "68 of 68 tables grain-determined · by schema: sales 12, production 27, person 18, ...",
            "expected": "68 of 68 tables grain-determined",
        },
        {
            "name": "period_inside_parenthetical_is_not_a_boundary",
            "text": "Reads as measured (e.g. see pg_stats. Confirmed live). Second real sentence follows.",
            "expected": "Reads as measured (e.g. see pg_stats. Confirmed live).",
        },
        {
            "name": "no_boundary_at_all_returns_the_whole_text_unchanged",
            # The peer's own gate check: "How big is this database" contains
            # no ". " at all, so must come back byte-for-byte unchanged.
            "text": "1,048,576 rows across 68 tables and 91 keys",
            "expected": "1,048,576 rows across 68 tables and 91 keys",
        },
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
class TestFirstSentenceTruncation:
    def _js_side(self, cases: list[dict], tmp_path: Path) -> list[dict]:
        format_mjs = tmp_path / "format3.mjs"
        format_mjs.write_text(FORMAT_JS.read_text(encoding="utf-8"), encoding="utf-8")
        envelope_src = ENVELOPE_JS.read_text(encoding="utf-8")
        envelope_src = envelope_src.replace("/static/next/format.js", "./format3.mjs")
        envelope_mjs = tmp_path / "envelope3.mjs"
        envelope_mjs.write_text(envelope_src, encoding="utf-8")
        cases_json = json.dumps([{"name": c["name"], "text": c["text"]} for c in cases])
        script = f"""
import {{ firstSentence }} from './envelope3.mjs';
const cases = {cases_json};
const out = cases.map((c) => ({{ name: c.name, result: firstSentence(c.text) }}));
console.log(JSON.stringify(out));
"""
        script_path = tmp_path / "run_first_sentence.mjs"
        script_path.write_text(script, encoding="utf-8")
        result = subprocess.run(["node", str(script_path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_corpus_size_is_the_documented_5(self):
        assert len(_first_sentence_corpus()) == 5

    def test_python_mirror_and_js_agree(self, tmp_path):
        cases = _first_sentence_corpus()
        js_side = self._js_side(cases, tmp_path)
        assert len(js_side) == len(cases)
        failures = []
        for case, j in zip(cases, js_side):
            assert j["name"] == case["name"]
            py_result = _first_sentence(case["text"])
            if py_result != case["expected"]:
                failures.append(
                    f"{case['name']}: python mirror disagrees with the corpus's own "
                    f"expectation -- python={py_result!r} expected={case['expected']!r}")
            if j["result"] != case["expected"]:
                failures.append(
                    f"{case['name']}: js firstSentence disagrees with the corpus's own "
                    f"expectation -- js={j['result']!r} expected={case['expected']!r}")
        assert not failures, "\n".join(failures)

    def test_the_fit_row_headline_slot_now_shows_one_sentence(self, tmp_path):
        """End-to-end, not just the helper in isolation: `readEnvelope`'s
        `lines.answer` for the reply's own fit-question corpus case must now
        equal exactly ONE sentence, where before this fix it carried the
        analysis's entire multi-sentence explanation verbatim (the live bug
        the architecture session's gate found on 8813, 2026-09-28)."""
        case = dict(_lead_selection_corpus()[0])
        case["facts"] = [dict(f) for f in case["facts"]]
        multi_sentence = (
            "NO REQUIREMENT DECLARED — no lens was supplied, so fit is not a "
            "question that has an answer here. A lens names the tables, columns "
            "and time window a caller cares about. Without one, coverage_signals "
            "and subject_signals have nothing to compare against."
        )
        for f in case["facts"]:
            if f["analysis_id"] == "preliminary_fit":
                f["headline"] = multi_sentence
        js_side = TestLeadAnalysisSelection()._js_side([case], tmp_path)
        answer = js_side[0]["answer"]
        plain = re.sub(r"<[^>]+>", "", answer)
        assert "A lens names the tables" not in plain, (
            f"lines.answer still carries more than the first sentence: {answer!r}")
        assert "no lens was supplied, so fit is not a question that has an answer here." in plain
