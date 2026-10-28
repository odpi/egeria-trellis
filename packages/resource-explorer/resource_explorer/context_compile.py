"""Compile a context for a question about one resource — the adoption gate.

Closes the loop opened in Phase 0. `question_catalog_reader.get_questions()`
already resolves Purpose + Perspective to questions and to the `analysis_ids`
that answer them, and already returns that chain as a `derivation`. Here those
analysis ids become the SECTIONS of a ContextSpec, the registry's materialised
findings become the packer's candidates, and the packer decides what fits.

**Nothing is run here.** Resolvers read stored analysis results only. An analysis
that has not run yet produces no candidate, and the packer reports it as a gap
rather than the context quietly lacking it — which is the whole point of
`availability: inline | queued` (docs/context-compilation-design.md §20). A
compile must never block on a survey.

**The derivation travels with the answer.** It is the explanation with content —
"this section is here because your Purpose is Certify, which ranked Q17, which
dispatches security_scan" — and it is what makes the manifest legible rather
than a list of sizes.
"""
from __future__ import annotations

import json
import logging
import logging
from dataclasses import dataclass

from trellis_artifact_tree.model import Rung
from trellis_context import Candidate, ContextSpec, Pointer, Section, pack

log = logging.getLogger(__name__)

#: Kept small on purpose: an instructions section that grows into a system
#: prompt is a prompt template wearing a spec's clothes.
#:
#: The refusal wording is deliberately the ORIGINAL loose one. A one-shape
#: template ("reply in exactly this shape and add nothing else: 'The stored
#: analyses do not cover X. <analysis> would answer it; it <state>.'") was
#: tried in experiment run3-20260909 and over-triggered on the 8B answering
#: model: 107 of 156 compiled answers became pure refusals, including ones
#: where the analysis that answers the question was packed at FULL with no
#: gaps, and the <state> slot was filled by invention ("ran and found
#: nothing" for a packed analysis) in 7 of the run's 8 missing-result
#: claims. docs/experiments/compiled-vs-rag.md, run 3.
#:
#: The 'structure only' sentence was replaced 2026-09-10 (audit
#: docs/experiments/audits/2026-09-09-run4-unsupported-claims-vs-packed-text.md,
#: pattern A): telling the model that a section "is not a result" did not stop
#: it narrating `architecture_recovery`'s field names as an architecture, and
#: for the section that WOULD have answered the question (row 16,
#: `dependency_analysis`) it left the model with nothing but names, so it
#: answered from a neighbouring section instead. The rung now carries real
#: first entries, so the instruction it needs is about partiality, not about
#: non-results.
#: The "name the actual items" clause was added 2026-09-16 after a live
#: failure: asked "what languages does this repo use?" with a
#: `language_file_classification` section carrying a real `by_type` breakdown
#: (Python, Markdown, etc. with counts), the model answered with the file/byte
#: totals instead of naming a single language — the evidence had the answer
#: and the answer never used it. Unlike the instructions this file already
#: A/B-tests, this one has NOT yet been measured against the rung it displaces
#: (docs/experiments/compiled-vs-rag.md's harness); the same before/after
#: comparison this file's other comments describe should be run before
#: trusting the cost is worth it, not just the fix.
_INSTRUCTIONS = (
    "Answer using only the evidence below. Every section states which analysis "
    "produced it. A section marked 'abridged' shows only the first few entries "
    "of each list or mapping — its counts and entries are partial, so do not "
    "report an abridged list as complete. When evidence names specific items "
    "(languages, files, authors, etc.) with values, name them in the answer "
    "rather than only a total. If the evidence does not answer the "
    "question, say so and name what is missing — do not infer from absence."
)
#: A sentence asking yes/no answers to carry their evidence line was added
#: here by PR #38 (run 8) and removed after run 9's within-run A/B
#: (2026-09-12): it cost one evidence section a rung in 135 of 156 compiles
#: (instructions are a required section, so 202 characters of instruction
#: are 202 characters less evidence), and even where packing was identical
#: it made the 8B answerer terser everywhere (supported claims -0.58 paired,
#: median answer 114 vs 180 chars) for a gain on two of three CVE rows. It
#: survives as INSTRUCTION_VARIANTS["yesno_line"] so a later A/B can retest
#: it, e.g. with a larger model. The general lesson: every instruction
#: character is paid for in evidence, so an instruction has to beat the
#: rung it displaces.

#: The same instructions at the packer's SUMMARY rung, for budgets too small
#: to carry the template. Instructions are required, so without a shorter
#: rung a tight budget fails the compile outright instead of degrading — the
#: one section that used to be exempt from the ladder now climbs it too.
_INSTRUCTIONS_SHORT = (
    "Answer only from the evidence below; name the analysis behind each point. "
    "'Abridged' sections show first entries only. Name specific items and "
    "values from evidence, not only totals. If it does not answer, say "
    "which analysis would and whether it has run. Do not infer from absence."
)

#: The barest form, for a budget too small even for the short one. Added
#: 2026-09-10 with the coverage line: instructions are REQUIRED, so every
#: character added to the top of that section is a budget below which the
#: whole compile raises instead of degrading. A third rung is what keeps the
#: ladder's promise for the one section that cannot be dropped.
_INSTRUCTIONS_BARE = (
    "Answer only from the evidence below; 'abridged' sections are partial. "
    "Do not infer from absence."
)

#: Named instruction variants, so an experiment can put two instruction
#: wordings in ONE run over the same stored state -- the within-run A/B that
#: run 8 (2026-09-12) showed is the only design that attributes anything
#: here: between two runs the Automate scheduler re-surveys the repos and
#: the catalog moves, so run-over-run comparisons carry three variables.
#: The variant changes the instructions candidate's text, so it is part of
#: the compile id without any further bookkeeping; the manifest names it.
#: "default" is production (plain, since run 9); "yesno_line" adds run 8's
#: sentence, so it can be retested without editing production wording.
_YESNO_SENTENCE = (
    "If the question asks yes or no, give the yes or no and then the evidence "
    "line it rests on — the analysis, the value, and any coverage limit or caveat "
    "shown beside it; a bare yes or no is not an answer. "
)
_YESNO_SENTENCE_SHORT = "A yes/no answer must state the evidence line and its coverage limit. "
_LIST_RULE = (
    "A list marked 'N of M shown' is partial: give its total and what is "
    "shown, and never present the shown items as the whole list. "
)
INSTRUCTION_VARIANTS: dict[str, tuple[str, str]] = {
    "default": (_INSTRUCTIONS, _INSTRUCTIONS_SHORT),
    # The prose half of the designer's rule "prose never enumerates", as a
    # VARIANT: run 9 measured that 202 characters of instruction cost one
    # evidence section a rung in 135 of 156 compiles, so a sentence enters
    # production only after a within-run A/B says it beats the rung.
    "list_rule": (
        _INSTRUCTIONS.replace("If the evidence does not answer the question",
                              _LIST_RULE + "If the evidence does not answer the question"),
        _INSTRUCTIONS_SHORT,
    ),
    "yesno_line": (
        _INSTRUCTIONS.replace("If the evidence does not answer the question",
                              _YESNO_SENTENCE + "If the evidence does not answer the question"),
        _INSTRUCTIONS_SHORT.replace("If it does not answer",
                                    _YESNO_SENTENCE_SHORT + "If it does not answer"),
    ),
}

#: How many evidence sections a compile packs, counted after ranking over the
#: analyses that HAVE a candidate -- a gap does not spend a slot. Ranking
#: still orders and never excludes at the DERIVATION level — every catalog
#: question that reaches an analysis stays in `derivation`, and the sections
#: past the cap are reported in the manifest as `deferred`, with their rank
#: and weight, rather than silently absent. What the cap changes is what
#: competes for the budget.
#:
#: Measured 2026-09-09 on the three experiment repos, 6 questions each, at
#: budget 6000 (scripts/experiment_compiled_vs_rag.py's budget):
#:
#:   cap    sections  FULL share  top-3 ranked at FULL  used (median)
#:   none   26.0      22%         43 / 54               5985
#:   12     11.9      88%         52 / 54               5610
#:   8       8.0      90%         53 / 54               4368
#:
#: Uncapped, ~26 sections share 6000 chars, the budget pins at its ceiling in
#: every compile, and four in five sections sit at SUMMARY — which the audit
#: found the model narrating as if it were the answer (cause B/C: "649
#: component(s) exist… documented in the architecture summary"). Twelve is
#: the largest cap at which the ranked sections reach FULL while the budget,
#: not the cap, still decides the last sections in. The value is a knob, not
#: a law: `compile_context(max_sections=...)` overrides it, 0 disables it.
MAX_EVIDENCE_SECTIONS = 12


logger = logging.getLogger(__name__)

#: What a state means for a section with nothing to pack. The distinction the
#: compiler could not previously make -- and did not need to invent, because
#: surveyors/result_status.py already carries it and facts.py already applies
#: it. "A measured zero and a never-run are the same number and opposite
#: answers" (facts.py). Inventing a parallel vocabulary here would have been the
#: mistake that put four retired RE perspectives beside Egeria's twelve.
_GAP_PHRASING = {
    "never_run": "has not run",
    "nothing_found": "ran and found nothing — a real zero, not a missing result",
    "not_established": "ran · result not attributable",
    "partial": "ran over only part of what it covers",
}

#: The sentence offered on hover beside a gap's short cue word. The cue stays short
#: ("ran · result not attributable"); the explanation is on demand.
_GAP_EXPLANATION = {
    "not_established": "it ran, but nothing shows this analysis produced the result",
}


@dataclass(frozen=True)
class CompiledContext:
    text: str
    manifest: dict
    derivation: list[dict]
    #: Content hash of everything the packer saw: spec id and version, budget,
    #: target model, and every candidate's rungs. Two compiles over the same
    #: materialised state get the same id, which is the replayability contract
    #: (context-compilation-design.md §9) made checkable — and the key that
    #: conversation turns and feedback carry so a rating can be traced back to
    #: the exact context the model was given (§13). Also present as
    #: manifest["compile_id"] so callers holding only the manifest have it.
    compile_id: str = ""


def _compile_id(spec, candidates: dict, budget: int) -> str:
    """blake2b over the packer's inputs, in a canonical order.

    Hashes rung TEXT, not provenance timestamps: a re-read of the same stored
    result at a later `fetched_at` is the same compile. Provenance still
    travels in the manifest; it just does not change identity.
    """
    import hashlib
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{spec.spec_id}|{spec.version}|{budget}|{spec.target_model}|".encode())
    for key in sorted(candidates):
        h.update(f"[{key}]".encode())
        for rung, text in sorted(candidates[key].rungs.items(), key=lambda kv: str(kv[0])):
            h.update(f"{rung}:".encode())
            h.update(hashlib.blake2b(str(text).encode(), digest_size=8).digest())
    return h.hexdigest()


#: No single finding may take more than this share of a section's FULL rung.
#: Findings are written for whatever consumer the surveyor had in mind, and some
#: of those are not prose: a rendered diagram, a serialised graph, a file listing.
#: One of them can consume a whole section's budget and crowd out every other
#: check in the same analysis, which is a worse context than omitting it.
#: Truncation is marked, never silent — an elided finding that looked complete
#: would be the failure this module keeps finding in other forms.
MAX_FINDING_CHARS = 1200

#: Below this, a section's findings are too slight to be trusted as the whole
#: story, and the analysis's own results reader is consulted as well. Set at the
#: same order as the near-empty tree threshold and for the same reason: it marks
#: "this says almost nothing", not "this is short".
THIN_FINDINGS_CHARS = 200

#: Words too common to carry relevance on either side of _question_relevance's
#: overlap -- without this, "results"/"survey" (present in nearly every
#: question a Resource Explorer user asks, because that is what this tool
#: does) would inflate every catalog entry's score roughly equally, which is
#: the same as not scoring at all.
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "it", "of", "to", "and", "or", "for",
    "on", "in", "at", "this", "that", "what", "how", "does", "do", "show",
    "me", "all", "with", "about", "results", "result", "survey",
    # Measured 2026-09-09 over the 52 repo catalog questions: "repository"
    # appears in 13 of them and "there" in 9, so a paraphrase like "what
    # languages are in this repository?" overlapped a quarter of the catalog
    # on "repository" alone. Same reason "results"/"survey" are here.
    "repository", "repositories", "repo", "there", "already", "has",
})


#: Words that name the same thing as a catalog analysis's own vocabulary. "Are there outstanding CVEs?" is the
#: catalog's question for cve_scan, so an asker who says "known security vulnerabilities" shared no token with it:
#: cve_scan ranked on catalog position alone, fell below the section cap and was deferred (walk 2026-10-12).
#: Applied to BOTH sides of the overlap, so the entries that already say "vulnerabilities" keep matching.
_TOKEN_ALIASES = {
    "vulnerability": "cve", "vulnerabilities": "cve", "vulnerable": "cve", "cves": "cve",
    "advisory": "cve", "advisories": "cve",
}


def _tokens(text: str) -> set[str]:
    import re
    return {_TOKEN_ALIASES.get(t, t) for t in re.findall(r"[a-z0-9]+", text.lower())}


def _question_relevance(question: str, catalog_question: str, analysis_ids: list[str]) -> float:
    """How much a catalog entry's own question and analysis ids overlap the
    free-text question actually asked, as a fraction of the asked question's
    own (stopword-stripped) tokens. Deterministic word-overlap, not an
    embedding or model call — the packer must never trigger one (§20).

    Measured 2026-08-31: asking "documentation survey results" packed
    repository_health / architecture_doc_lens / language_file_classification
    ahead of documentation_coverage itself, then dropped documentation_coverage
    as a gap entirely — `question` was accepted by compile_context() and never
    read again; every catalog entry weighed the same regardless of what was
    asked, decaying only by its arbitrary position in the YAML. The model,
    given evidence that did not answer the question, fell back to
    vector_search and answered from Egeria's OWN documentation about its
    Survey Framework feature — a keyword collision on "survey", not an answer
    about the repository's documentation.

    Analysis ids are folded into the candidate token set alongside the
    catalog's own question phrasing: "documentation_coverage" tokenizes to
    "documentation"/"coverage", which is the one thing an asker is most
    likely to have said even when the catalog's own phrasing ("How well
    documented is it?") shares no token with it.

    A score, not a filter: Purpose already establishes that this system
    ranks what it is uncertain of rather than excluding it (see
    question_catalog_reader.get_questions()'s own docstring). This returns
    0.0 for no overlap, not None or an exclusion — the caller decides what a
    zero means for ranking.
    """
    import re
    # The catalog's own wording is a full match by definition -- checked
    # before stopwords, because "What does this repository do?" is made of
    # nothing else and stripped to no tokens at all, so the verbatim catalog
    # question scored 0.0 against itself and the compile reported "no
    # catalog question matches" (acceptance run over the run-4 audit rows,
    # 2026-09-10). Punctuation and case do not carry meaning here.
    if re.sub(r"[^a-z0-9]+", " ", question.lower()).strip() == \
            re.sub(r"[^a-z0-9]+", " ", catalog_question.lower()).strip():
        return 1.0
    q_tokens = _tokens(question) - _STOPWORDS
    if not q_tokens:
        return 0.0
    candidate_tokens = _tokens(catalog_question)
    for aid in analysis_ids:
        candidate_tokens |= _tokens(aid.replace("_", " "))
    candidate_tokens -= _STOPWORDS
    return len(q_tokens & candidate_tokens) / len(q_tokens)


def _clip(text: str, analysis_id: str, check: str) -> str:
    if len(text) <= MAX_FINDING_CHARS:
        return text
    return (
        text[:MAX_FINDING_CHARS].rstrip()
        + f"\n  […truncated {len(text) - MAX_FINDING_CHARS} chars — read "
          f"{analysis_id}/{check} directly for the whole thing]"
    )


def _findings_to_rungs(findings: list[dict], analysis_id: str) -> dict[Rung, str]:
    """Three rungs from stored findings, all free — no summariser involved.

    FULL is the findings themselves. SUMMARY is one line per check. IDENTIFIERS
    is the check names: enough for a reader to know the analysis ran and what it
    looked at, when the budget cannot afford more.
    """
    if not findings:
        return {}
    full = [f"## {analysis_id}"]
    summary = [f"## {analysis_id}"]
    names = []
    for f in findings:
        check = f.get("check_name") or "?"
        label = f.get("label") or ""
        text = _clip((f.get("summary") or "").strip(), analysis_id, check)
        names.append(check)
        summary.append(f"- {check}: {label}" if label else f"- {check}")
        full.append(f"- {check}: {label}\n  {text}" if text else f"- {check}: {label}")
    return {
        Rung.FULL: "\n".join(full),
        Rung.SUMMARY: "\n".join(summary),
        Rung.IDENTIFIERS: f"## {analysis_id}\nchecks: " + ", ".join(sorted(set(names))),
    }


#: Keys that describe a run rather than carry a result. Kept identical to the
#: set inside facts._has_content (tests/test_context_compile.py pins that the
#: two functions agree on an envelope-only dict).
_ENVELOPE_KEYS = frozenset({"_status", "surveyed_at", "detail", "scoped_to", "run_outcomes"})


def _has_content(results) -> bool:
    """Whether a results dict says anything, as opposed to merely existing.

    Every reader returns a dict, so truthiness is useless here: `cve_scan`
    answers `{"findings": []}` and `dependency_analysis` answers
    `{"by_ecosystem": {}, "total": 0}`. Both are dicts, both are true, and both
    mean nothing was found. Treating them as candidates would replace a wrong
    gap with a wrong section — an empty heading asserting the analysis had
    something to say.

    A measured zero and a never-run are still not distinguished here; the
    readers do not carry that distinction (see result_status.py, which exists
    because it matters elsewhere). What this decides is narrower: is there
    anything to pack.
    """
    if not isinstance(results, dict):
        return bool(results)
    for key, value in results.items():
        # Envelope keys describe the RUN, not a result. `{"_status": {"state":
        # "never_run", ...}}` is a reader saying "nothing yet" in the
        # result_status.py convention, and until 2026-09-13 this function
        # counted that dict as content -- so a never-run analysis whose reader
        # follows the convention was PACKED as a section headed "_status:
        # state=never_run" instead of judged as a gap. Found by #46's CI: a
        # new envelope-returning reader (dependency_support) displaced
        # documentation_coverage under the 12-section cap and a test lost the
        # word "readme". Same key set as facts._has_content, which has
        # exempted these since it was written; the docstring above, written
        # when "the readers do not carry that distinction", is now wrong for
        # architecture_diagram, architecture_recovery and dependency_support.
        if key in _ENVELOPE_KEYS:
            continue
        # Each type is decided exactly once. An earlier version put the
        # numeric test in an `elif ... and value` and then had a catch-all
        # `elif value is not None`, so a zero failed the numeric branch and was
        # caught by the fallback: `{"by_ecosystem": {}, "total": 0}` read as
        # content and packed dependency_analysis as an empty section claiming
        # it had something to say. bool is checked first because it is a
        # subclass of int.
        if isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, (int, float)):
            if value:
                return True
        elif isinstance(value, (list, dict, str)):
            if len(value) > 0:
                return True
        elif value is not None:
            return True
    return False


#: How much of a container the FULL rung shows before it says how much it is
#: holding back. Truncation is always marked — an elided list that looked
#: complete is the same failure this module keeps finding in other forms.
MAX_FULL_LIST_ITEMS = 20      # lists of scalars, rendered inline
MAX_FULL_DICT_ITEMS = 10      # lists of mappings, rendered one block each
#: How many entries of each list/mapping the abridged (SUMMARY) rung carries.
ABRIDGED_ENTRIES = 3


def _scalar(value, limit: int = 200) -> str:
    """One value as a reader sees it — including the empties, said out loud.

    `None` and `""` are rendered as words rather than as nothing at all: a
    blank after a colon reads as a measured emptiness, which is exactly the
    reading this module exists to prevent.
    """
    if value is None:
        return "(none)"
    if isinstance(value, str) and not value.strip():
        return "(empty)"
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _compact(value, limit: int = 120) -> str:
    """A nested container squeezed onto one line, for depths past the first.

    A list leads with its total: the clipped JSON that follows is a prefix,
    and a prefix without its total is what the prose channel turns into
    "here are some of them" (designer's note, 2026-09-12, §2 -- prose never
    enumerates; a list-shaped fact is its total plus what is shown)."""
    if isinstance(value, (list, dict)):
        if not value:
            return "(empty list)" if isinstance(value, list) else "(empty mapping)"
        text = _scalar(json.dumps(value, default=str, sort_keys=True), limit)
        if isinstance(value, list):
            return f"{len(value)} item(s): {text}"
        return text
    return _scalar(value, limit)


#: Keys that identify an entry rather than describe it. Alphabetical order put
#: `blueprint`/`candidate_blueprints`/`confidence`/`depth` in front of `name`
#: for architecture_recovery's components — four fields about a thing whose
#: identity did not fit on the line. Generic, not per-analysis: no shape
#: knowledge lives here, only the observation that a reader needs to know WHICH
#: entry before anything else about it.
_IDENTITY_KEYS = ("name", "id", "key", "title", "label", "check_name",
                  "cluster_name", "dep_name", "path", "file", "slug", "kind")


def _entry_summary(item, limit: int = 140) -> str:
    """One list entry on one line: `k=v, k=v` for a mapping, the value itself
    otherwise. Identity fields lead."""
    if isinstance(item, dict):
        lead = [k for k in _IDENTITY_KEYS if k in item]
        rest = [k for k in sorted(item) if k not in lead]
        pairs = [f"{k}={_scalar(item[k], 40)}" for k in (lead + rest)[:4]]
        return _scalar(", ".join(pairs), limit)
    return _scalar(item, limit)


def _status_line(status) -> str:
    """`_status` (surveyors/result_status.attach) kept as a plain line, not a
    bullet: it describes the RUN, not a field of the result, and the whole
    reason it exists is that "measured and empty" must not read like "never
    looked"."""
    if not isinstance(status, dict):
        return f"_status: {_scalar(status)}"
    parts = [f"{k}={status[k]}" for k in ("state", "outcome", "cause", "hint")
             if status.get(k)]
    return "_status: " + (", ".join(parts) if parts else _compact(status))


def _render_entry(key: str, value, depth: int = 0) -> list[str]:
    """One top-level field as flat `- key: value` lines.

    Nested mappings flatten one level (`- detail.forks: 4745`) so a number and
    its name stay on the same line. A container one level down is rendered by
    the same rules under its dotted key — `dependency_analysis` keeps its whole
    payload under `by_ecosystem.<eco>`, and compacting that onto one clipped
    line would have hidden every dependency in the section a dependency
    question ranks first (run-4 audit, row 16). Deeper than that is compacted:
    the rung is evidence, not a serialisation.
    """
    if isinstance(value, dict):
        if not value:
            return [f"- {key}: (empty mapping)"]
        lines: list[str] = []
        for k in sorted(value):
            sub = value[k]
            if isinstance(sub, (list, dict)) and depth < 1:
                lines.extend(_render_entry(f"{key}.{k}", sub, depth + 1))
            else:
                lines.append(f"- {key}.{k}: {_compact(sub)}")
        return lines
    if isinstance(value, list):
        # The total sits on the OPENING line, beside the items, never as a
        # trailing "… and 22 more". Measured 2026-09-12 (designer's note §2):
        # with the elision at the bottom the model wrote "here are some of
        # them" and the answer carried two numbers that disagreed (32 in the
        # sentence, 10 on the page) with nothing saying which was the list.
        # "10 of 32 shown" on the line the model reads first makes the
        # sentence it can write "32 components, 10 shown here", which is the
        # count plus provenance; the pane holds the items.
        n = len(value)
        if n == 0:
            return [f"- {key}: (empty list)"]
        if not any(isinstance(v, dict) for v in value):
            shown = ", ".join(_scalar(v, 60) for v in value[:MAX_FULL_LIST_ITEMS])
            if n > MAX_FULL_LIST_ITEMS:
                return [f"- {key} ({MAX_FULL_LIST_ITEMS} of {n} shown): {shown}"]
            return [f"- {key} ({n} total): {shown}"]
        head = (f"- {key}: {n} item(s), {min(n, MAX_FULL_DICT_ITEMS)} of {n} shown here"
                if n > MAX_FULL_DICT_ITEMS else f"- {key}: {n} item(s), all shown")
        lines = [head]
        for i, item in enumerate(value[:MAX_FULL_DICT_ITEMS], 1):
            lines.append(f"  - item {i} of {n}:")
            if isinstance(item, dict):
                lines.extend(f"    - {k}: {_compact(item[k], 200)}" for k in sorted(item))
            else:
                lines.append(f"    - {_scalar(item)}")
        return lines
    return [f"- {key}: {_scalar(value)}"]


def _list_extents(results: dict) -> dict[str, dict]:
    """For each list-valued field a reader returned (top level, and one level
    down under a dotted key, mirroring _render_entry), its total and how many
    entries each rung shows -- so a UI can say "M" by reading the manifest
    rather than recounting the text (the /next session's rail sentence,
    2026-09-13). Keys match the field names as rendered."""
    out: dict[str, dict] = {}

    def _record(key: str, value: list) -> None:
        n = len(value)
        cap = MAX_FULL_LIST_ITEMS if not any(isinstance(v, dict) for v in value) else MAX_FULL_DICT_ITEMS
        out[key] = {"total": n, "shown": {"FULL": min(n, cap), "SUMMARY": min(n, ABRIDGED_ENTRIES)}}

    for key in sorted(k for k in results if k != "_status"):
        value = results[key]
        if isinstance(value, list):
            _record(key, value)
        elif isinstance(value, dict):
            for k, sub in value.items():
                if isinstance(sub, list):
                    _record(f"{key}.{k}", sub)
    return out


def _full_lines(results: dict) -> list[str]:
    lines: list[str] = []
    for key in sorted(k for k in results if k != "_status"):
        lines.extend(_render_entry(key, results[key]))
    if "_status" in results:
        lines.append(_status_line(results["_status"]))
    return lines


def _abridged_lines(results: dict) -> list[str]:
    """Scalars with their values, then each container's first few entries.

    The rung this replaced named containers `(list)`/`(mapping)` and showed no
    entry at all, on the reasoning that a COUNT gets narrated as a value. The
    run-4 audit found the cost of that: with nothing to read, the model read
    the field names instead (`blueprints, interfaces, documentation` narrated
    as an architecture) or borrowed content from the section next door. Real
    entries with an explicit `(first 3 of 62)` marker give it something true
    to say and say plainly that it is a prefix.
    """
    scalars: list[str] = []
    containers: list[tuple[str, object]] = []
    for key in sorted(k for k in results if k != "_status"):
        value = results[key]
        if isinstance(value, (list, dict)):
            containers.append((key, value))
        else:
            scalars.append(f"- {key}: {_scalar(value, 120)}")
    lines = list(scalars)
    for key, value in containers:
        n = len(value)
        if n == 0:
            lines.append(f"- {key}: "
                         + ("(empty list)" if isinstance(value, list) else "(empty mapping)"))
            continue
        if n > ABRIDGED_ENTRIES:
            marker = f"(first {ABRIDGED_ENTRIES} of {n})"
        else:
            marker = f"({n} entr{'y' if n == 1 else 'ies'}, complete)"
        lines.append(f"- {key}: {marker}")
        if isinstance(value, dict):
            for k in sorted(value)[:ABRIDGED_ENTRIES]:
                lines.append(f"  - {k}: {_compact(value[k], 80)}")
        else:
            for item in value[:ABRIDGED_ENTRIES]:
                lines.append(f"  - {_entry_summary(item)}")
    if "_status" in results:
        lines.append(_status_line(results["_status"]))
    return lines


def _with_headline(rungs: dict[Rung, str], headline: dict | None) -> dict[Rung, str]:
    """The analysis's own one-sentence verdict, as the first line under the
    heading of every rung that has room for it.

    The knowledge is NOT re-derived here: `REPO_ANALYSIS_HEADLINE_MAP`'s
    functions already own each shape's meaning, including its coverage —
    `_cve_scan_headline` answers "none in 0 of 61 declared dependenc(ies)"
    with tone `warn`, which is exactly the sentence the model needed and
    invented the opposite of (run-4 audit, row 08).
    """
    if not headline or not headline.get("label"):
        return rungs
    tone = headline.get("tone")
    line = f"headline: {headline['label']}" + (f" ({tone})" if tone else "")
    out = {}
    for rung, text in rungs.items():
        if rung is Rung.IDENTIFIERS:
            out[rung] = text
            continue
        head, sep, rest = text.partition("\n")
        out[rung] = f"{head}\n{line}{sep}{rest}" if head.startswith("## ") else f"{line}\n{text}"
    return out


def _results_to_rungs(results: dict, analysis_id: str) -> dict[Rung, str]:
    """Three rungs from an analysis's own results reader.

    The findings table is one of several places results live, and for most
    analyses it is the wrong one. Measured on egeria_git 2026-08-29: eleven
    analyses were reported as gaps and seven of them had real stored data —
    repository_health scoring 85.8, api_structure holding 3,232 Java classes —
    because each keeps its results in its own table (project_stats,
    project_code_symbols, project_file_type_counts) and only
    `project_analysis_findings` was consulted. `docs/granularity-pass.md` §1.2
    had already measured that 12 analyses have no finding `kind` at all.

    The shapes are heterogeneous by design, so the rungs are structural rather
    than field-aware: FULL is the payload flattened to one `- key: value` line
    per field, SUMMARY is the same fields with only the first few entries of
    each list/mapping (marked as such), IDENTIFIERS names the parts. A reader
    that knew each shape would be a fourth place to keep that knowledge in
    sync — which is why the per-analysis HEADLINE function, not this, supplies
    the one field-aware sentence a section gets (see compile_context).

    FULL was a fenced ```json dump until 2026-09-10. The run-4 audit's pattern
    B is what changed it: `cve_scan`'s `{"checked": 0, "unqueryable": 61,
    "findings": []}` was read as "the CVE scan found no vulnerabilities", and
    `repository_health`'s nested `detail` produced "65964 stars, forks,
    watchers" from `stars: 65964` / `forks: 4745`. Flat, one fact per line,
    with nesting spelled out as `detail.forks`, removes the two structural
    invitations to that: a wall of braces to skim, and sibling numbers whose
    keys are visually far from their values.

    ONE exception, and it is a shape, not an analysis: `{"findings": [...]}`
    with `check_name`/`label`/`summary` per item is the same finding shape
    `_findings_to_rungs` already formats well from the findings table itself
    -- `_documentation_results`, `_license_results`,
    `_repo_classification_results` and others return exactly this ("same
    uniform finding shape" is their own recurring comment). Measured
    2026-08-31: documentation_coverage's SUMMARY read
    "- findings: 4 item(s)\n- _status: 5 key(s)", and the model narrated
    that structural summary verbatim as "5 key(s) with 4 item(s) found" --
    real per-check content, reduced to two counts, then repeated back as if
    it meant something. Recognizing this one recurring shape and reusing
    `_findings_to_rungs`' own formatting is not the field-aware special-
    casing above rules out; it is not re-deriving from `_status` or any
    other field this function still treats structurally.
    """
    if not _has_content(results):
        return {}

    findings = results.get("findings")
    if isinstance(findings, list) and findings and all(
        isinstance(f, dict) and "check_name" in f for f in findings
    ):
        rungs = dict(_findings_to_rungs(findings, analysis_id))
        other = sorted(k for k in results if k != "findings")
        if other:
            rungs[Rung.FULL] += "\n(also: " + ", ".join(other) + ")"
        return rungs

    keys = sorted(results)
    full = f"## {analysis_id}\n" + "\n".join(_full_lines(results))
    abridged = (
        f"## {analysis_id}\n"
        f"(abridged: first entries only; read {analysis_id} for the rest)\n"
        + "\n".join(_abridged_lines(results))
    )
    rungs = {
        Rung.FULL: full,
        Rung.IDENTIFIERS: f"## {analysis_id}\nreports: " + ", ".join(keys),
    }
    # The middle rung has to BE a middle: a section whose abridged form is
    # nearly as long as its FULL one buys the packer no choice, and paying a
    # partial-evidence warning for a rung that saves nothing is the worst of
    # both. Let the packer pick FULL or IDENTIFIERS in that case.
    if len(abridged) <= len(full) * 0.8:
        rungs[Rung.SUMMARY] = abridged
    return rungs


#: Endpoint lines the model sees per rung. The FULL cap is generous (a service
#: has tens of endpoints, not thousands; egeria_git has 1,596 and is cut with
#: the extent said); SUMMARY is a visible prefix.
MAX_ENDPOINTS_FULL = 120
MAX_ENDPOINTS_SUMMARY = 20


def _with_endpoints(rungs: dict[Rung, str], registry, slug: str):
    """Add the endpoint LIST to interface_surface's rungs.

    The findings give the model counts ("1869 registration(s) found"), and
    the 1869 is mostly test mocks; the endpoints themselves are in
    project_code_markers (resource_explorer/endpoints.py). Returns (rungs,
    extents): extents is `{"endpoints": {"total", "shown"}}` for
    manifest["lists"], only when there is a list to be partly shown.
    A reader that fails adds a note-line, not silence."""
    from resource_explorer.endpoints import read_endpoints, render_lines

    spec = _spec_endpoints(rungs, registry, slug)
    if spec is not None:
        return spec
    es = read_endpoints(registry, slug)
    if es is None:
        return rungs, {}
    if not es.measured and not rungs:
        return rungs, {}
    heading = ("endpoint list (non-test code registrations from the code markers; derived from code, not a "
               "published contract; it replaces the registration counts in the findings above, which include "
               "test-path mocks)")
    full = "\n".join([heading] + render_lines(es, MAX_ENDPOINTS_FULL))
    summ = "\n".join([heading] + render_lines(es, MAX_ENDPOINTS_SUMMARY))
    out = dict(rungs)
    out[Rung.FULL] = (out[Rung.FULL] + "\n" + full) if Rung.FULL in out else f"## interface_surface\n{full}"
    out[Rung.SUMMARY] = (out[Rung.SUMMARY] + "\n" + summ) if Rung.SUMMARY in out else f"## interface_surface\n{summ}"
    out.setdefault(Rung.IDENTIFIERS, "## interface_surface\nchecks: endpoints")
    extents = {}
    if es.measured:
        n = es.total
        extents["endpoints"] = {"total": n, "shown": {
            "FULL": min(n, MAX_ENDPOINTS_FULL), "SUMMARY": min(n, MAX_ENDPOINTS_SUMMARY)}}
    return out, extents


def _spec_endpoints(rungs: dict[Rung, str], registry, slug: str):
    """interface_surface's endpoint block from the best DESCRIBED source (a committed spec, else a registered
    running instance), saying which; None when only code (or nothing) is known, so the code path above runs.
    The code-derived routes are then a one-line comparison, not a second list competing with the first."""
    from resource_explorer.api_reference.compare import compare_interfaces
    from resource_explorer.api_reference.derived import derive_from_code
    from resource_explorer.api_reference.service import best_spec_interface, render_spec_lines, spec_heading
    from resource_explorer.endpoints import read_endpoints

    iface = best_spec_interface(registry, slug)      # stored reads; unreadable parts are logged and skipped there
    if iface is None:
        return None
    derived = derive_from_code(registry, slug)
    others = [compare_interfaces(iface, derived)] if derived is not None else []

    def block(limit: int) -> str:
        lines = [spec_heading(iface)] + render_spec_lines(iface, limit, others)
        es = read_endpoints(registry, slug)
        kinds: dict = {}
        for e in (es.rows if es else []):
            if e.kind != "http_api":
                kinds[e.kind] = kinds.get(e.kind, 0) + 1
        if kinds:
            lines.append("- other registrations in code (not part of the HTTP API): "
                         + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        return "\n".join(lines)

    out = dict(rungs)
    full, summ = block(MAX_ENDPOINTS_FULL), block(MAX_ENDPOINTS_SUMMARY)
    out[Rung.FULL] = (out[Rung.FULL] + "\n" + full) if Rung.FULL in out else f"## interface_surface\n{full}"
    out[Rung.SUMMARY] = (out[Rung.SUMMARY] + "\n" + summ) if Rung.SUMMARY in out else f"## interface_surface\n{summ}"
    out.setdefault(Rung.IDENTIFIERS, "## interface_surface\nchecks: endpoints")
    n = len(iface.operations)
    return out, {"endpoints": {"total": n, "shown": {"FULL": min(n, MAX_ENDPOINTS_FULL),
                                                      "SUMMARY": min(n, MAX_ENDPOINTS_SUMMARY)}}}


MAX_CLASSES_FULL, MAX_CLASSES_SUMMARY = 60, 15
MAX_FUNCTIONS_FULL, MAX_FUNCTIONS_SUMMARY = 40, 10


def _with_symbols(rungs: dict[Rung, str], registry, slug: str, question: str):
    """Add the symbol lists to api_structure's rungs: the question-matched
    block first (classes the question names with their methods, or the
    symbols under a module it names, or one line saying nothing matched),
    then the public classes and top-level functions with N-of-M extents.
    Counts alone ("5,937 symbols") gave the model nothing to list from
    (resource_explorer/code_symbols.py)."""
    from resource_explorer.code_symbols import TYPE_KINDS, left_out_tail, list_line, read_symbols, render_matched

    ss = read_symbols(registry, slug)
    if ss is None or not ss.measured:
        return rungs, {}
    classes = [s for s in ss.rows if s.kind in TYPE_KINDS]
    funcs = [s for s in ss.rows if s.kind == "function" and not s.parent_class]
    heading = ("symbol lists (public, non-test symbols from the stored code symbols; ordered by file then line; "
               "public is inferred from naming)")
    matched = render_matched(question, ss, ss.rows)
    tail = left_out_tail(ss)

    def block(nc: int, nf: int) -> str:
        return "\n".join([heading] + matched + list_line("classes", classes, nc, tail)
                         + list_line("top-level functions", funcs, nf))

    out = dict(rungs)
    full, summ = block(MAX_CLASSES_FULL, MAX_FUNCTIONS_FULL), block(MAX_CLASSES_SUMMARY, MAX_FUNCTIONS_SUMMARY)
    out[Rung.FULL] = (out[Rung.FULL] + "\n" + full) if Rung.FULL in out else f"## api_structure\n{full}"
    out[Rung.SUMMARY] = (out[Rung.SUMMARY] + "\n" + summ) if Rung.SUMMARY in out else f"## api_structure\n{summ}"
    out.setdefault(Rung.IDENTIFIERS, "## api_structure\nreports: classes, top-level functions")
    extents = {
        "classes": {"total": len(classes), "shown": {"FULL": min(len(classes), MAX_CLASSES_FULL),
                                                      "SUMMARY": min(len(classes), MAX_CLASSES_SUMMARY)}},
        "functions": {"total": len(funcs), "shown": {"FULL": min(len(funcs), MAX_FUNCTIONS_FULL),
                                                      "SUMMARY": min(len(funcs), MAX_FUNCTIONS_SUMMARY)}},
    }
    return out, extents


#: Which analyses have a real, addressable view to point at. Deliberately
#: narrow: Backlog "a compiled answer should be able to POINT at a view" notes
#: deep-linking to a perspective/scope is the *prerequisite* work, and today
#: only the architecture card has perspective tabs (`_archTabsHtml` in
#: index.html) worth linking into. Add an entry here once a view exists to
#: point at — the compiler side needs no other change, since `Pointer` is
#: already resource/analysis-agnostic.
_POINTABLE_VIEWS = {"architecture_recovery": "architecture"}


def _pointer_for(analysis_id: str, slug: str, provenance: tuple[dict, ...]) -> Pointer | None:
    """A link to where this analysis's own view lives, alongside its prose.

    `as_of` comes from the analysis's own `surveyed_at`, not compile time —
    the same fact-vs-read distinction `_provenance` already draws (§10):
    the pointer should say when the view's *data* is from, not when this
    context happened to be compiled.
    """
    view = _POINTABLE_VIEWS.get(analysis_id)
    if view is None:
        return None
    surveyed_at = next((p.get("surveyed_at") for p in provenance if p.get("surveyed_at")), "")
    return Pointer(resource_slug=slug, view=view, as_of=surveyed_at or "")


def _provenance(findings: list[dict], analysis_id: str) -> tuple[dict, ...]:
    """When the fact was true, and where it came from.

    `surveyed_at` is the analysis run's own timestamp, not the compile's — an
    old fact and a stale read are different things, and collapsing them is what
    the envelope exists to prevent (§10)."""
    return tuple(
        {"analysis_id": analysis_id, "check": f.get("check_name"),
         "surveyed_at": f.get("surveyed_at")}
        for f in findings
    )


def _judge_gap(fact_layer, slug: str, analysis_id: str) -> dict:
    """Why this section has nothing, in the vocabulary that already exists.

    Fail-soft to a bare name: an unjudged gap is still worth reporting, and a
    fact layer that cannot answer must not cost the whole compile.
    """
    gap = {"key": analysis_id, "reason": "no candidate — resolver produced nothing yet"}
    try:
        fact = fact_layer.fact(slug, analysis_id)
    except Exception:
        logger.debug("no fact for %s/%s", slug, analysis_id, exc_info=True)
        return gap
    phrasing = _GAP_PHRASING.get(fact.state)
    if not phrasing:
        return gap
    gap["state"] = fact.state
    gap["reason"] = phrasing
    if fact.state in _GAP_EXPLANATION:
        gap["explanation"] = _GAP_EXPLANATION[fact.state]
    if fact.last_run_at:
        gap["last_run_at"] = fact.last_run_at
    if fact.can_run:
        gap["can_run"] = list(fact.can_run)
    return gap


#: How the question catalog says a question is answered, when the answer is
#: not a stored analysis. These are the kinds whose `analysis_ids` are empty
#: BY DECLARATION — the catalog is not silent about them, the compiler was.
_COVERAGE_PHRASING = {
    "human": "human input",
    "direct": "a direct field",
    "chart": "a chart",
    "gap": "nothing yet (gap)",
}


#: How much of the catalog's caveat reaches the instructions at each rung.
#: The Rationale/Source column runs 46-913 characters (median 172, measured
#: 2026-09-11); the longest are histories, and the instructions section is
#: required, so a caveat must never be what pushes a tight budget into
#: failure. Clipped text is marked, never silent.
CAVEAT_CHARS_FULL = 400
CAVEAT_CHARS_SHORT = 160


def _clip_caveat(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + " […]"


def _with_caveat(rungs: dict[Rung, str], caveat: str) -> dict[Rung, str]:
    """The matched question's caveat INSIDE the section it qualifies, under
    the headline, at FULL (400 chars) and SUMMARY (160), never IDENTIFIERS.

    Beside the value, not in a preamble. A caveat about declared-only CVE
    coverage that sits three sections above the cve_scan numbers is help
    text; the same line under `## cve_scan` is the number's own limit --
    the shape the /next design round built for, applied to the prompt
    (dwolfson-c6, 2026-09-11). The preamble keeps the caveat only when the
    matched question maps to no packed section at all.
    """
    if not caveat:
        return rungs
    out = {}
    for rung, text in rungs.items():
        if rung is Rung.IDENTIFIERS:
            out[rung] = text
            continue
        line = "caveat: " + _clip_caveat(
            caveat, CAVEAT_CHARS_FULL if rung is Rung.FULL else CAVEAT_CHARS_SHORT)
        lines = text.split("\n")
        at = 1 if lines and lines[0].startswith("## ") else 0
        if len(lines) > at and lines[at].startswith("headline: "):
            at += 1
        out[rung] = "\n".join(lines[:at] + [line] + lines[at:])
    return out


def _caveat_lines(entry: dict | None, relevance: float) -> tuple[str, str, str]:
    """The catalog's own statement of what an answer to this question can and
    cannot claim, as two lines for the instructions (FULL and SUMMARY rungs)
    and the verbatim text for the manifest.

    The question catalog's Rationale/Source column carries exactly the
    caveat the run-5/6 CVE rows needed and did not get from the evidence
    alone: "DECLARED dependencies only, so a zero is 'none found in what we
    can see'"; secret_scan "never claims 'no secrets'". Until 2026-09-11 it
    lived in the CSV and the /next UI; the compiled instructions are the one
    place the answering model reads, so it goes here too -- for the MATCHED
    question only, since a caveat about the wrong question is noise.
    """
    if entry is None or relevance <= 0.0:
        return "", "", ""
    text = (entry.get("rationale") or "").strip()
    if not text:
        return "", "", ""
    return (
        f"Caveat for this question, from the catalog: {_clip_caveat(text, CAVEAT_CHARS_FULL)}\n",
        f"Caveat: {_clip_caveat(text, CAVEAT_CHARS_SHORT)}\n",
        text,
    )


def _coverage(entry: dict | None, relevance: float) -> tuple[dict, str, str]:
    """What the catalog itself says answers this question, and — when that is
    not a stored analysis — one line at the top of the evidence saying so.

    An empty `gaps` list means "no section we offered was missing", which is
    not the same as "the question is covered". Three of the run-4 audit's five
    inventions are exactly that difference: nothing in the pack addressed what
    was asked, nothing said so, and the model filled the void from the repo
    name, an invented survey tier, and global star counts.
    """
    if entry is None or relevance <= 0.0:
        return ({"kind": "none", "question": "", "note": ""},
                ("Coverage: no catalog question matches what you asked, so no stored "
                 "analysis answers it; the sections below are the nearest-ranked "
                 "evidence, not an answer.\n"),
                "Coverage: no stored analysis answers this question.\n")
    answering = entry.get("answering") or {}
    kind = answering.get("kind") or "unknown"
    cov = {"kind": kind, "question": entry.get("question", ""),
           "note": answering.get("note", "")}
    phrase = _COVERAGE_PHRASING.get(kind)
    if phrase is None:
        # analysis / mixed / partial: a stored analysis does answer it, and the
        # sections below are that answer. The kind is recorded; nothing is said.
        return cov, "", ""
    return cov, (
        f'Coverage: the question catalog answers "{cov["question"]}" by {phrase}, '
        f"not by stored analyses; the sections below are the nearest-ranked "
        f"evidence, not an answer.\n"
    ), f"Coverage: the catalog answers this by {phrase}, not by stored analyses.\n"


def compile_context(
    registry,
    slug: str,
    question: str,
    *,
    resource_type: str = "repo",
    purposes: list[str] | None = None,
    perspectives: list[str] | None = None,
    budget: int = 8000,
    target_model: str = "",
    session_id: str | None = None,
    max_sections: int | None = None,
    instructions_variant: str = "default",
) -> CompiledContext:
    """Build, resolve and pack a context for `question` about resource `slug`.

    `resource_type` selects which resource type's question/analysis catalog
    and fact-layer maps this compile reads from ('repo' | 'database' |
    'filesystem', default "repo" for backward compatibility with existing
    callers). Before this was threaded through, every compile silently read
    the repo catalog and repo analyses regardless of what kind of resource
    `slug` actually named — a database- or filesystem-scoped chat question
    always got repo-shaped evidence (`foss_scorecard`, `chaoss_metrics`, ...)
    and reported the real answer, sitting in a database analysis like
    `row_count_snapshot`, as a gap.

    Every successful compile is recorded through `registry.record_compile`
    (fail-soft: a registry without it, or a write that fails, costs the caller
    nothing but the persistence). `session_id` is the chat/CLI session the
    compile served, when there is one; it lands on the row, not in the hash.
    `max_sections` caps how many ranked evidence sections compete for the
    budget (default MAX_EVIDENCE_SECTIONS; 0 means no cap); the rest are
    listed in the manifest as `deferred`. `instructions_variant` selects a
    wording from INSTRUCTION_VARIANTS (experiments only; production is
    "default") and is named in the manifest.
    """
    if instructions_variant not in INSTRUCTION_VARIANTS:
        raise ValueError(f"unknown instructions_variant {instructions_variant!r}; "
                         f"expected one of {sorted(INSTRUCTION_VARIANTS)}")
    instr_full, instr_short = INSTRUCTION_VARIANTS[instructions_variant]
    from resource_explorer.surveyors.question_catalog_reader import get_questions

    entries = get_questions(
        resource_type, perspectives=perspectives or None, purposes=purposes or None,
    )

    # Rank matters: Purpose ORDERS, so the analyses reached by the highest-ranked
    # questions become the heaviest sections. Nothing is excluded by ranking --
    # a low-ranked analysis is a light section, not an absent one.
    #
    # `question` relevance is folded into rank too, not just Purpose/Perspective
    # -- see _question_relevance's docstring for the reported failure this
    # fixes. Without it, a compile with no Perspective chips set (the common
    # case) ranks every one of the ~50 catalog questions by nothing but their
    # position in the YAML, regardless of what was actually asked.
    weights: dict[str, float] = {}
    derivation: list[dict] = []
    from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

    # `egeria_publish` is an ACTION, not an analysis — it writes to Egeria and
    # has no results to pack, so it can only ever appear as a permanent gap
    # asserting a missing result that will never exist. The scheduler already
    # excludes action == "publish" for the same reason; this is the same
    # exclusion, one layer up. Question 5 of the catalog references it, which
    # is how it reaches here at all.
    _actions = {a["id"] for a in get_analyses(resource_type, include_egeria_live=False)
                if a.get("action") == "publish"}

    # Fact-layer maps for THIS resource type, looked up once via the same
    # per-type adapter FactLayer itself uses (resource_explorer/facts.py's
    # `_map`) rather than importing repo's REPO_ANALYSIS_RESULTS_MAP /
    # REPO_ANALYSIS_HEADLINE_MAP unconditionally, which is what made the
    # results-reader/headline fallback below repo-only regardless of
    # `resource_type`. A resource type that declares no results/headline map
    # degrades to `{}` here, same as FactLayer does -- not an error, just
    # nothing extra to offer beyond the findings table.
    from resource_explorer.surveyors.survey_definition_executor import (
        SurveyDefinitionExecutorError,
        get_adapter,
    )

    try:
        _adapter = get_adapter(resource_type)
    except SurveyDefinitionExecutorError:
        _adapter = None
    _results_map = (
        _adapter.analysis_results_map() if _adapter and _adapter.analysis_results_map else {}
    ) or {}
    _headline_map = (
        _adapter.analysis_headline_map() if _adapter and _adapter.analysis_headline_map else {}
    ) or {}

    #: The best-matching catalog entry OVER THE WHOLE CATALOG, including the
    #: entries the loop below skips for having no analysis to dispatch to.
    #: Those are the ones that matter here: the catalog has already recorded
    #: that this question is answered by a human, a direct field, a chart, or
    #: not yet at all. Skipping them left the compile with no way to say so,
    #: and the run-4 audit's pattern C is what fills that silence — "surveyed
    #: at Tier 1", "a documentation platform", organizational adoption read
    #: off global GitHub counts, with `gaps: []` in every case because no
    #: analysis was ever mapped to the need.
    best_entry, best_relevance = None, 0.0

    for position, entry in enumerate(entries):
        d = entry.get("derivation") or {}
        ids = [i for i in (d.get("analysis_ids") or []) if i not in _actions]
        entry_relevance = _question_relevance(question, entry["question"], ids)
        if entry_relevance > best_relevance:
            best_entry, best_relevance = entry, entry_relevance
        if not ids:
            continue
        # Weight decays with rank but never reaches zero.
        relevance = entry_relevance
        # relevance dominates position by design: a question that names what
        # it wants ("documentation") must outrank an unrelated question that
        # merely sits earlier in the YAML. Position still breaks ties among
        # equally (ir)relevant entries and keeps every entry's weight above
        # zero -- nothing is excluded, same rule Purpose already follows.
        #
        # ADDITIVE, not a ratio. The first form, (1 + 20r) / (1 + 0.1p),
        # divided relevance by position too, so a full match (r = 1) at
        # catalog position 30 weighed 5.25 and a one-word match (r = 0.25)
        # at position 0 weighed 6.0 — measured 2026-09-09 with "What
        # languages and file types are in this repository?", which packed
        # foss_scorecard and repository_health at FULL and the language
        # analysis at SUMMARY. With the positional term bounded by 1.0 and
        # each 0.05 of relevance worth as much, any better match outranks
        # any worse one whatever their positions, and position still orders
        # the equally relevant.
        weight = 1.0 / (1 + position * 0.1) + relevance * 20.0
        for analysis_id in ids:
            weights[analysis_id] = max(weights.get(analysis_id, 0.0), weight)
        derivation.append({
            "question_id": entry.get("id", ""),
            "question": entry["question"],
            "matched_purposes": d.get("matched_purposes", []),
            "matched_perspectives": d.get("matched_perspectives", []),
            "analysis_ids": ids,
            # `rank` is this entry's position in get_questions()'s own
            # Purpose-ordered catalog list -- NOT its position in this
            # `derivation` list once sorted below. A low catalog rank with
            # high relevance now sorts earlier here while keeping a
            # numerically later `rank`; a reader wanting "why did this pack
            # first" wants list order, and a reader wanting "where does the
            # catalog itself rank this" wants `rank` -- they can diverge on
            # purpose. (S4 review, 2026-08-31: confirmed worth calling out
            # explicitly rather than leaving both readings equally plausible.)
            "rank": position,
            "relevance": round(relevance, 2),
        })

    # Sort by weight, not by catalog rank, before packing -- the manifest and
    # "why these?" panel should read as "this is why these ranked first",
    # which is now relevance-then-position, not the YAML's own order. See the
    # `rank` comment above: this reorders the LIST, not the `rank` field.
    derivation.sort(key=lambda d2: -max((weights.get(a, 0.0) for a in d2["analysis_ids"]), default=0.0))

    coverage, coverage_line, coverage_line_short = _coverage(best_entry, best_relevance)
    caveat_line, caveat_line_short, coverage["caveat"] = _caveat_lines(best_entry, best_relevance)
    # The sections the matched question's caveat qualifies: the analyses the
    # catalog maps that question to. The caveat goes INSIDE those sections
    # (see _with_caveat); it stays in the preamble only if none of them ends
    # up with a candidate.
    caveat_ids: set[str] = set()
    if coverage["caveat"] and best_entry is not None:
        caveat_ids = {i for i in ((best_entry.get("derivation") or {}).get("analysis_ids") or [])
                      if i not in _actions}

    sections = [Section("instructions", role="instructions", required=True, weight=1.0)]
    candidates: dict[str, Candidate] = {}
    ranked = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    cap = MAX_EVIDENCE_SECTIONS if max_sections is None else max_sections
    deferred: list[dict] = []
    # The cap counts sections WITH EVIDENCE, and a gap costs nothing. The
    # first version sliced the ranking to `cap` entries before resolving any,
    # so an analysis that turned out to be a gap had already spent a slot:
    # on a sparsely surveyed repo eleven of twelve slots went to gaps and the
    # one analysis with real findings sat at rank 13, "deferred". Found by
    # #46's CI against an empty registry, diagnosed by dwolfson-4c
    # (2026-09-13). The cap=12 sweep that chose the value was on repos where
    # every cited analysis had run, so it never showed there. Now the walk
    # continues down the ranking until `cap` analyses have a candidate;
    # gaps found on the way are still reported as gaps (they are information
    # -- "should exist, has nothing behind it"); what remains below the
    # point where the cap is reached is deferred unresolved.
    packed_count = 0
    # Failures the compile survived but the caller must be able to see.
    extra_notes: list[str] = []
    #: Per packed section, each list field's total and shown-per-rung
    #: (`manifest["lists"]`), from the reader's results before rendering.
    list_extents: dict[str, dict] = {}
    for rank, (analysis_id, weight) in enumerate(ranked):
        if cap > 0 and packed_count >= cap:
            deferred.append({"key": analysis_id, "weight": round(weight, 3), "rank": rank,
                             "reason": f"below the section cap of {cap}"})
            continue
        findings = registry.query_findings(slug, analysis_id)
        rungs = _findings_to_rungs(findings, analysis_id)
        provenance = _provenance(findings, analysis_id)

        # The findings table holds results for a MINORITY of analyses. Where it
        # is empty OR THIN, ask the analysis's own results reader — the same one
        # the UI renders from — before concluding there is nothing stored.
        # Without this, a gap means "not in project_analysis_findings" while
        # claiming to mean "never run", which is the confident-wrong-answer
        # shape this whole spec exists to avoid.
        #
        # "or thin" is the correction. An earlier version fell back only when
        # `rungs` was empty, so ANY whole-resource finding — however slight —
        # suppressed the reader entirely. dwolfson-59 hit it exactly: a single
        # diagram finding written at whole-resource scope replaced an analysis's
        # real results with a picture, and the section got worse than before the
        # finding existed. They fixed the trigger at source; this fixes the
        # fragility, which would otherwise wait for the next analysis to write
        # one thin whole-resource row.
        #
        # The rule is now about evidence, not precedence: consult both when the
        # findings are slight, and keep whichever says more.
        if len(rungs.get(Rung.FULL, "")) < THIN_FINDINGS_CHARS:
            entry = _results_map.get(analysis_id)
            reader = entry[0] if entry else None
            if reader is not None:
                try:
                    results = reader(registry, slug)
                except Exception as exc:
                    # A reader that raises is not evidence of absence. Say so in
                    # the notes rather than letting it look like a clean gap.
                    log.warning("results reader for %s failed on %s: %s",
                                analysis_id, slug, exc)
                    results = None
                if results is not None:
                    from_reader = _results_to_rungs(results, analysis_id)
                    extents = _list_extents(results) if isinstance(results, dict) else {}
                    # Keep whichever says more. Overwriting unconditionally was
                    # safe only while this ran solely on empty findings; now
                    # that a THIN finding also reaches here, a reader with less
                    # to say must not displace the little that was real.
                    if len(from_reader.get(Rung.FULL, "")) > len(rungs.get(Rung.FULL, "")):
                        # The analysis's own headline goes first, ahead of the
                        # fields it summarises. Same fail-soft discipline as
                        # the reader above: a headline that raises costs this
                        # section its verdict line, not the compile.
                        headline = None
                        hl = _headline_map.get(analysis_id)
                        if hl is not None:
                            try:
                                headline = hl(registry, slug)
                            except Exception as exc:
                                log.warning("headline reader for %s failed on %s: %s",
                                            analysis_id, slug, exc)
                                # Visible in the manifest, not only in a log:
                                # a section missing its verdict line must be
                                # distinguishable from one that never had one.
                                extra_notes.append(
                                    f"headline for {analysis_id} unavailable "
                                    f"({type(exc).__name__}); section packed "
                                    f"without its verdict line")
                        rungs = _with_headline(from_reader, headline)
                        provenance = ({"analysis_id": analysis_id,
                                       "check": None, "surveyed_at": None},)
                        if extents:
                            list_extents[analysis_id] = extents

        if analysis_id == "interface_surface":
            rungs, ep_extent = _with_endpoints(rungs, registry, slug)
            if ep_extent:
                list_extents.setdefault(analysis_id, {}).update(ep_extent)
            if rungs and not provenance:
                provenance = ({"analysis_id": analysis_id, "check": None, "surveyed_at": None},)

        if analysis_id == "api_structure":
            rungs, sym_extent = _with_symbols(rungs, registry, slug, question)
            if sym_extent:
                list_extents.setdefault(analysis_id, {}).update(sym_extent)
            if rungs and not provenance:
                provenance = ({"analysis_id": analysis_id, "check": None, "surveyed_at": None},)

        if rungs and analysis_id in caveat_ids:
            rungs = _with_caveat(rungs, coverage["caveat"])
        sections.append(Section(analysis_id, role="evidence", weight=weight))
        if rungs:
            candidates[analysis_id] = Candidate(
                analysis_id, rungs, provenance=provenance,
                pointer=_pointer_for(analysis_id, slug, provenance),
            )
            packed_count += 1
        # No rungs => no candidate => the packer records a gap. Deliberately not
        # skipped here: a section the derivation says should exist, with nothing
        # behind it, is information -- and it does not count against the cap.

    # The instructions candidate is built last because its text depends on
    # where the caveat went. The coverage line (and the caveat, when it has no
    # section to live in) ride INSIDE this candidate rather than being
    # appended to the packed text afterwards, so they are part of what
    # `_compile_id` hashes: two compiles of the same question over the same
    # stored state stay one compile, and a compile whose coverage changed is
    # a different one.
    placed = sorted(caveat_ids & set(candidates))
    coverage["caveat_placed_in"] = placed if placed else (["instructions"] if coverage["caveat"] else [])
    if placed:
        caveat_line = caveat_line_short = ""
    candidates["instructions"] = Candidate(
        "instructions",
        {Rung.FULL: coverage_line + caveat_line + instr_full,
         Rung.SUMMARY: coverage_line_short + caveat_line_short + instr_short,
         Rung.IDENTIFIERS: _INSTRUCTIONS_BARE})

    spec = ContextSpec(
        spec_id=f"adoption-gate:{slug}", version=1,
        sections=tuple(sections), target_model=target_model,
    )
    packed = pack(spec, candidates, budget)
    m = packed.manifest
    from resource_explorer.facts import FactLayer

    _facts = FactLayer(registry, resource_type=resource_type)
    compile_id = _compile_id(spec, candidates, budget)
    compiled = CompiledContext(
        text=packed.text(),
        compile_id=compile_id,
        manifest={
            "compile_id": compile_id,
            "resource_type": resource_type,
            "spec_id": m.spec_id, "budget": m.budget, "used": m.used,
            "headroom": m.headroom, "packed": list(m.packed),
            "dropped": list(m.dropped),
            # Ranked below the cap: never offered to the packer, so neither
            # packed, dropped nor a gap. Listed so "why these?" can show what
            # was left out and where it ranked.
            "deferred": deferred,
            # {section key: {list field: {"total": M, "shown": {"FULL": N,
            # "SUMMARY": N'}}}} for every packed reader-derived section --
            # the "N of M shown" the text carries, as data, so the rail can
            # say M without recounting (pick N by packed[i]["rung"]).
            "lists": {k: v for k, v in list_extents.items() if k in candidates},
            # What the catalog says answers this question at all. `gaps` says
            # which offered sections had nothing; this says whether the
            # question was ever a question stored analyses answer.
            "coverage": coverage,
            "instructions_variant": instructions_variant,
            # Judged, not merely listed. The packer knows only that a section
            # had no candidate; the fact layer knows whether that is a zero or
            # an absence, and they are opposite answers to the same question.
            "gaps": [_judge_gap(_facts, slug, g["key"]) for g in m.gaps],
            "notes": list(m.notes) + extra_notes,
        },
        derivation=derivation,
    )
    record = getattr(registry, "record_compile", None)
    if callable(record):
        try:
            record(compile_id, slug, question, compiled.manifest, compiled.derivation,
                   session_id=session_id)
        except Exception as exc:
            # Persistence is an instrument, not the product: a compile the
            # caller can use must never be lost to a failed bookkeeping write.
            # But the failure must be VISIBLE in what the caller gets, not
            # only in a log nobody reads: the manifest says the compile was
            # not recorded, so feedback that cites this compile_id can be
            # told apart from feedback that cites a persisted one.
            log.warning("could not record compile %s for %s", compile_id, slug, exc_info=True)
            compiled.manifest["notes"].append(
                f"compile not recorded ({type(exc).__name__}); feedback citing "
                f"{compile_id} will not resolve to a stored manifest"
            )
            compiled.manifest["recorded"] = False
        else:
            compiled.manifest["recorded"] = True
    return compiled


def build_epistemological_matrix(
    registry: ProjectRegistry,
    slugs: list[str],
    analyses: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """Build an epistemological comparison matrix across multiple resource slugs.

    Surfaces survey coverage and asymmetry explicitly using FactLayer states
    (measured, nothing_found, never_run, partial, not_established).
    Returns (markdown_table, matrix_records).
    """
    from resource_explorer.facts import (
        FactLayer,
        MEASURED,
        NOTHING_FOUND,
        NEVER_RUN,
        PARTIAL,
        NOT_ESTABLISHED,
    )

    if not slugs:
        return "", []

    slug_types: dict[str, str] = {}
    for s in slugs:
        norm = registry._normalize_slug(s)
        if registry.get(norm):
            slug_types[norm] = "repo"
        elif registry.get_database(norm, allow_unreadable=True):
            slug_types[norm] = "database"
        elif registry.get_filesystem(norm):
            slug_types[norm] = "filesystem"
        else:
            slug_types[norm] = "repo"

    normalized_slugs = [registry._normalize_slug(s) for s in slugs]

    if not analyses:
        # Default core set covering repos, databases, and general health
        analyses = [
            "security_scan",
            "license_classification",
            "dependencies",
            "project_stats",
            "ci_quality",
            "repo_conventions",
            "architecture_recovery",
            "schema_inventory",
            "data_profiling",
            "data_quality",
        ]

    matrix_records: list[dict] = []
    lines: list[str] = []

    # Markdown table header
    header_cols = ["Dimension"] + [f"{s} ({slug_types[s]})" for s in normalized_slugs]
    lines.append("| " + " | ".join(header_cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(header_cols)) + " |")

    for analysis_id in analyses:
        row_slugs: dict[str, dict] = {}
        row_values: list[str] = []
        has_any_data = False

        for s in normalized_slugs:
            rtype = slug_types[s]
            fl = FactLayer(registry, resource_type=rtype)
            fact = fl.fact(s, analysis_id)
            state = fact.state
            headline = fact.headline or fact.note

            display_state = state.upper()
            if state == MEASURED:
                display_state = f"KNOWN ({headline[:35]}...)" if headline and len(headline) > 35 else f"KNOWN ({headline})" if headline else "KNOWN"
                has_any_data = True
            elif state == NOTHING_FOUND:
                display_state = "KNOWN (0 / None found)"
                has_any_data = True
            elif state == NEVER_RUN:
                display_state = "NEVER_RUN"
            elif state == PARTIAL:
                display_state = f"PARTIAL ({headline[:25]})" if headline else "PARTIAL"
                has_any_data = True
            elif state == NOT_ESTABLISHED:
                display_state = "NOT_ESTABLISHED"

            row_slugs[s] = {
                "state": state,
                "display": display_state,
                "headline": headline,
                "is_known": fact.is_known,
            }
            row_values.append(display_state.replace("|", "/"))

        # Format dimension name
        dim_name = analysis_id.replace("_", " ").title()
        matrix_records.append({
            "analysis_id": analysis_id,
            "dimension": dim_name,
            "slugs": row_slugs,
        })
        lines.append(f"| {dim_name} | " + " | ".join(row_values) + " |")

    markdown_table = "\n".join(lines)
    return markdown_table, matrix_records


def compile_multi_resource_context(
    registry: ProjectRegistry,
    slugs: list[str],
    question: str,
    budget: int = 8000,
    perspectives: list[str] | None = None,
    purposes: list[str] | None = None,
    target_model: str = "default",
    session_id: str = "",
    instructions_variant: str = "default",
    max_sections_per_resource: int | None = None,
) -> CompiledContext:
    """Compile structured comparative context across two or more resources.

    Partitions the prompt token budget proportionally across resources and
    generates an epistemological asymmetry matrix comparing survey coverage.
    """
    if not slugs:
        raise ValueError("At least one resource slug is required for context compilation.")

    normalized_slugs = [registry._normalize_slug(s) for s in slugs]

    # Single-resource fast path
    if len(normalized_slugs) == 1:
        slug = normalized_slugs[0]
        rtype = "repo"
        if registry.get_database(slug, allow_unreadable=True):
            rtype = "database"
        elif registry.get_filesystem(slug):
            rtype = "filesystem"
        return compile_context(
            registry=registry,
            slug=slug,
            question=question,
            budget=budget,
            resource_type=rtype,
            perspectives=perspectives,
            purposes=purposes,
            target_model=target_model,
            session_id=session_id,
            instructions_variant=instructions_variant,
            max_sections=max_sections_per_resource,
        )

    # Multi-resource compilation
    matrix_md, matrix_records = build_epistemological_matrix(registry, normalized_slugs)

    # Allocate budget: reserve 20% for matrix, preamble, and cross-resource synthesis
    reserved_budget = max(200, int(budget * 0.20))
    evidence_budget = max(100, budget - reserved_budget)
    per_resource_budget = max(100, evidence_budget // len(normalized_slugs))

    compiled_list: list[CompiledContext] = []
    slug_types: dict[str, str] = {}

    for slug in normalized_slugs:
        rtype = "repo"
        if registry.get_database(slug, allow_unreadable=True):
            rtype = "database"
        elif registry.get_filesystem(slug):
            rtype = "filesystem"
        slug_types[slug] = rtype

        c = compile_context(
            registry=registry,
            slug=slug,
            question=question,
            budget=per_resource_budget,
            resource_type=rtype,
            perspectives=perspectives,
            purposes=purposes,
            target_model=target_model,
            session_id=session_id,
            instructions_variant=instructions_variant,
            max_sections=max_sections_per_resource,
        )
        compiled_list.append(c)

    # Assemble combined text
    text_parts: list[str] = [
        "=== MULTI-RESOURCE EVIDENCE COMPILATION ===",
        f"Target Resources: {', '.join(normalized_slugs)}\n",
        "## Epistemological Coverage & Asymmetry Matrix",
        "The following table compares survey findings and knowledge states across resources.",
        "A state of 'KNOWN' indicates measured facts; 'NEVER_RUN' indicates unmeasured dimensions (do not assume absence).",
        matrix_md,
        "",
    ]

    for slug, c in zip(normalized_slugs, compiled_list):
        text_parts.append(f"=== RESOURCE EVIDENCE: {slug} ({slug_types[slug]}) ===")
        text_parts.append(c.text)
        text_parts.append("")

    combined_text = "\n".join(text_parts).strip()

    # Create multi-resource compile ID
    id_input = f"{':'.join(normalized_slugs)}|{budget}|" + "|".join(c.compile_id for c in compiled_list)
    import hashlib

    multi_compile_id = hashlib.sha256(id_input.encode("utf-8")).hexdigest()[:16]

    total_used = sum(c.manifest.get("used", 0) for c in compiled_list) + len(matrix_md) // 4
    all_gaps = []
    for slug, c in zip(normalized_slugs, compiled_list):
        for g in c.manifest.get("gaps", []):
            gap_copy = dict(g)
            gap_copy["resource_slug"] = slug
            all_gaps.append(gap_copy)

    all_notes = []
    for slug, c in zip(normalized_slugs, compiled_list):
        for n in c.manifest.get("notes", []):
            all_notes.append(f"[{slug}] {n}")

    combined_derivation = []
    for slug, c in zip(normalized_slugs, compiled_list):
        for d in c.derivation:
            d_copy = dict(d)
            d_copy["resource_slug"] = slug
            combined_derivation.append(d_copy)

    manifest = {
        "compile_id": multi_compile_id,
        "mode": "multi_resource",
        "slugs": normalized_slugs,
        "budget": budget,
        "used": total_used,
        "headroom": max(0, budget - total_used),
        "coverage_matrix": matrix_records,
        "per_resource": {slug: c.manifest for slug, c in zip(normalized_slugs, compiled_list)},
        "gaps": all_gaps,
        "notes": all_notes,
        "instructions_variant": instructions_variant,
    }

    record = getattr(registry, "record_compile", None)
    if callable(record):
        try:
            record(
                multi_compile_id,
                ",".join(normalized_slugs),
                question,
                manifest,
                combined_derivation,
                session_id=session_id,
            )
            manifest["recorded"] = True
        except Exception as exc:
            log.warning("could not record multi-compile %s: %s", multi_compile_id, exc)
            manifest["notes"].append(f"compile not recorded ({type(exc).__name__})")
            manifest["recorded"] = False

    return CompiledContext(
        text=combined_text,
        compile_id=multi_compile_id,
        manifest=manifest,
        derivation=combined_derivation,
    )
