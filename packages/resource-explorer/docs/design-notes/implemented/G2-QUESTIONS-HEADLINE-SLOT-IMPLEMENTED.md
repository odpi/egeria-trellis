# G2 — Questions headline slot, one sentence per answering analysis — implemented

**Dispatch:** "Resource Explorer expansion architecture" session's G2, based on
`REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` §2.4 (merged to `main` at `9bda025d`).
**Base:** `re/g1-glyph-consolidation` tip `b53da25d` (verified unmoved before
branching) — G1's `web/static/next/glyphs.js` `STATES` table is imported here,
not reinvented.
**Branch:** `re/g2-questions-headline-slot`, worktree
`/Users/dwolfson/localGit/egeria-v6/trellis-re-g2-headline` (outside the shared
checkout, per the repo's git-hygiene rules).
**PR:** none opened — dispatch is explicit that no PRs go out tonight; the
architecture session batches them.

## The bug

The Questions tab's headline slot for a question backed by several analyses
(`envelope.js`'s `readEnvelope`) joined EVERY backing analysis's one-sentence
headline with `' · '` into a single paragraph. On `laz_local_adventureworks`,
"Could this be in scope for what I am looking for — worth the full pass?" is
backed by `grain_determination`, `subject_signals`, `coverage_signals` and
`preliminary_fit` (in that "inputs, then the analysis that consumes them"
order in `question_catalog.yaml`'s own `analysis_ids`) — joining all four
produced roughly 200 words, with `preliminary_fit`'s own verdict (the actual
answer to the question) buried well into the middle. The row also ticked ✓
on this — a tick on an answer that says "no lens was supplied, so fit is not
a question that has an answer here" reads as answered when the honest
statement is that a person still needs to declare a lens.

## What changed

### 1. One sentence in the headline slot (`envelope.js`)

`readEnvelope` still builds one candidate sentence per known fact exactly as
before (headline → prose → scalar fallback, unchanged), but no longer joins
all of them into `lines.answer`. Instead it picks ONE, via a new exported
`leadAnalysisId(entry)`:

- Reads `entry.note` — the catalog's own "Answering Analysis" column text —
  for the first `analysis_ids` entry actually NAMED there (as a whole word).
  `analysis_ids`' own array order is NOT the same ordering: for the fit
  question it lists `preliminary_fit` LAST, after the three signals it
  consumes, while the note names `preliminary_fit` FIRST ("preliminary_fit
  (design §16.3, §16.5 — zero-fetch comparison of subject_signals,
  coverage_signals and grain_determination's time grain against a supplied
  data requirement …"). This is not unique to the fit question — three other
  MIXED questions in the catalog also have a note that names an id other
  than `analysis_ids[0]` first (`security_scan`/`cii_badge`,
  `security_scan`/`repo_conventions`, `ci_quality`/`repo_conventions`).
- Falls back to `analysis_ids`' own order, then to fact-arrival order, so the
  slot is never left empty just because the preferred lead's own fact isn't
  in the envelope (never run, or dropped by an `is_known` filter upstream).

The OTHER analyses' sentences are not duplicated into a second on-page block.
`provenanceLine`'s existing comment on "the numbers behind this" already
states the intended shape for a multi-analysis row: "the rest via that
analysis's own row on `by_analysis`, not duplicated here." Recede, don't
re-render.

### 2. The fit row's honest state (`app.js`, `glyphs.js`)

`preliminary_fit`'s `no_requirement_declared` verdict (`value.lens_declared
=== false`) is a real, measured answer — but it is a statement about the
LENS, not about the resource (`compute_preliminary_fit`'s own docstring).
Before this, `rowState()` had no way to distinguish it from a genuine
answer: known fact, real headline, `env.answerable` true, no level
mismatch — all identical to `answered`.

- `glyphs.js`'s `STATES` gains one new entry, `needs-lens` (⚠, family
  `needs-person`, word `"needs a person: declare a lens"`) — additive only,
  no existing entry touched, following G1's own "RESERVED for G2/G3" pattern
  for states no consumer read yet.
- `app.js`'s `rowState()` now checks `needsLensDeclaration(env)` (any known
  fact with `analysis_id === 'preliminary_fit'` and `value.lens_declared ===
  false`) before falling into `answered`/`automatic`, returning `needs-lens`
  instead. Registered in `GLYPH_KEYS`, `STATE_TONE`, `LEGEND_ORDER` and
  `STATE_LABEL`.
- `isFullyAnswered(env)` carries the SAME check inlined (not calling
  `needsLensDeclaration`) — `tests/test_next_renders_text_cross_check.py`
  extracts this function's source verbatim and runs it standalone in node,
  so a call to a sibling helper defined elsewhere in the file would be a
  dangling reference there. Both copies are commented as needing to be kept
  in sync by hand, same reasoning `facts.py`'s `_renders_text` docstring
  gives for its own JS mirror.
- The "evidence" and "the numbers behind this" row actions, previously
  gated on `st === 'answered' || 'automatic' || 'partial'`, now also include
  `'needs-lens'` — a `needs-lens` row is still a real, measured answer (the
  design brief's own framing), not a crippled one.

## Order followed

Per §5 of the reply: the glyph module (G1) landed first, then this fix
(§2.4), before any By-analysis slice relies on a headline-equality test that
would otherwise lock in the joined-sentence bug as correct.

## Coordination correction (mid-implementation)

The architecture session caught two issues in the first pass before this was
committed:

1. The first version picked the lead by "first fact that produced a
   sentence" (i.e., effectively `analysis_ids[0]`), which for the fit
   question still leads with `grain_determination` — not what the reply's
   own worked example calls "the actual answer". Fixed by reading
   `entry.note` for the first NAMED id, as described above.
2. `readEnvelope` is inside `BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md` §D's
   cross-check contract with `facts.py`'s `_renders_text`. Addressed by
   extending `tests/test_next_renders_text_cross_check.py` — see below.

## Tests

`tests/test_next_renders_text_cross_check.py`:

- The existing 44-case single-fact grid (`TestRendersTextMatchesReadEnvelope`)
  is untouched — its own `test_corpus_size_is_the_documented_44` still
  passes, and single-fact behaviour is unchanged (with one fact, "the lead"
  is trivially that fact).
- New: a SEPARATE corpus/class, `TestLeadAnalysisSelection`
  (`_lead_selection_corpus`, 3 named cases — not grown into the 44-case grid,
  whose schema is one-`Fact`-per-case and has no room for several facts plus
  a `note`/`analysis_ids` pair):
  - `fit_question/consuming_order/preliminary_fit_leads` — the reply's own
    worked example, `analysis_ids` in the real "inputs then answer" order,
    asserting `preliminary_fit`'s sentence leads.
  - `note_names_no_id/falls_back_to_analysis_ids_order` — the note names none
    of the ids; both the JS and the Python mirror correctly return `None` for
    "what does the note alone say", and the rendered headline still falls
    back to `analysis_ids[0]`'s sentence rather than emptying the slot.
  - `leads_own_fact_missing/falls_through_to_next_producing_fact` — the note
    names `preliminary_fit` first, but the envelope carries no fact for it at
    all; the headline falls through to the next analysis that actually
    produced a sentence.
  - A test-only Python mirror of `leadAnalysisId`, `_lead_analysis_id()`, is
    added to the test file itself — there is no production Python equivalent
    to cross-check against (`facts.py` builds one `Fact` per analysis and
    leaves picking a single headline among several entirely to the JS
    rendering layer; nothing in `facts.py` combines more than one analysis's
    reading). The test asserts the Python mirror AND the real JS
    `leadAnalysisId` both agree with each case's own stated expectation, and
    a separate assertion checks `readEnvelope`'s actual `lines.answer` output
    carries the expected analysis's own headline text (stripped of the
    `tnum()` HTML the render adds), not merely that the two id-pickers agree
    on a name nothing downstream reads.

Full suite, `uv run pytest tests/ -q -rf` (no `-k`, no deselects) from
`packages/resource-explorer`: **6616 passed, 103 skipped, 2 failed** in
757s, on the branch's first rebase attempt onto G1's original tip
`b53da25d`. An earlier report in this doc said "5716 passed" from a run
with `-x` — that flag stops the whole run at the FIRST failure, so it
was an incomplete run, not a real full-suite count (caught by the
architecture session, since it was ~900 short of every other branch's
6605–6620 total that same night). Re-run without `-x`; 6616 is within
that range.

The 2 failures (`tests/test_slice17_questions_tab_level_gate_js.py`'s
`TestPartialIsARealState::test_partial_has_a_glyph_and_a_tone` and
`test_partial_is_in_the_legend_with_its_own_label`) were G1's own CI
failure, not this branch's — confirmed via `git show
b53da25d:.../app.js` before this doc's first version was written. G1
fixed both directly on its branch (tip `f570d3f1`). This branch has
since been rebased onto that fixed tip and the full suite re-run clean:
**6618 passed, 103 skipped, 0 failed**, confirming neither the rebase
nor anything in this branch's own diff reintroduces them.

Separately, the first post-rebase full-suite run on this branch turned
up 7 unrelated failures in `test_generate_database_survey_definition.py`
and `test_survey_definition_generator_guard.py`, all tracing to a single
cause: stray uncommitted mutations already sitting in this worktree
(`docs/dr-egeria/survey-definitions/.generated.json` deleted, two
`database-*`/`repo-*` survey-definition `.md` files edited) — debris
left behind by an earlier interrupted test run in this same worktree,
not part of this branch's own diff and not present in `git log`. The
coordinator restored those three paths to their committed state
(`git checkout -- <path>`, confirmed each path's prior state was
untouched by this branch's own commits first) and reran; both files
pass clean in isolation and as part of the full suite above.

## Served-page verification

Server: `uv run --package resource-explorer resource-explorer web --port
8815` (own worktree, `TRELLIS_ANONYMOUS_READ=true`), `.claude/launch.json`
entry `resource-explorer-g2-headline` added additively (existing entries
untouched).

Opened `/next`, "Continue without signing in", switched to DBs, selected
`laz_local_adventureworks`, Discovery stage, Questions tab. Found "Could
this be in scope for what I am looking for — worth the full pass?":

- **Glyph:** ⚠ (not ✓), tooltip "needs a person: declare a lens". The
  page's own KEY legend line reads "... ⚠ needs a person: declare a lens 2
  · ◌ no surveyor 3" — 2 rows carry this state (the fit question appears
  under two questions in the catalog, both backed by `preliminary_fit`; both
  correctly downgraded).
- **Headline slot:** exactly `preliminary_fit`'s own explanation text,
  starting "NO REQUIREMENT DECLARED — no lens was supplied, so fit is not a
  question that has an answer here. This is NOT a pass and NOT a failure.
  `achievable` states what this resource could satisfy (§16.5 point 2) …" —
  no trace of `grain_determination`/`subject_signals`/`coverage_signals`'s
  own sentences ahead of it. (The text itself is still fairly long — this is
  `preliminary_fit`'s OWN explanation, which folds in its per-container
  rollup at container level per Slice 21b; the fix is that it is now ONE
  analysis's sentence, not four analyses' sentences concatenated.)
- **Provenance line:** `grain_determination, subject_signals,
  coverage_signals, preliminary_fit · run 60m ago · evidence · copy as
  evidence · re-run · the numbers behind this › · 🔔 notify me` — "the
  numbers behind this" present and collapsed by default (no table rendered
  until clicked).

## Follow-up: one sentence means one sentence (live gate on 8813, 2026-09-28)

The served-page verification above was wrong about scope: picking the right
ANALYSIS (§1) was necessary but not sufficient. `lines.answer` still showed
that analysis's ENTIRE multi-sentence explanation — on `laz_local_adventureworks`
the fit row and "Which resources cover similar subjects, grain and period or
places…" (both backed by the same four analyses, both landing on
`preliminary_fit` as lead) each rendered a ~120-word paragraph. The
architecture session's gate on port 8813 caught this; this coordinator picked
it up directly (the G2 subagent had been stopped by an unrelated
infrastructure failure — a safety-verdict service outage, not a task
problem — and retrying immediately would have hit the same wall per its own
error message).

### Fix: `firstSentence()` (`envelope.js`)

New exported helper, applied to `f.headline` and `prose(f)`'s text BEFORE
either is escaped/marked up (so `answerHtml`'s own `VERDICT` regex, matched
at the string's start, is unaffected by where the truncation cuts later in
the string):

- Splits at the first ". " / "! " / "? " followed by a capital letter (an
  ordinary sentence boundary), or at the first " · " rollup separator
  (design's own "lead · N schemas ›" convention), whichever comes first.
- Never cuts inside a parenthetical — paren depth is tracked as the string
  is scanned once.
- Does NOT special-case a numbered/bulleted list's own periods — not needed
  by either row this was built for, both plain prose paragraphs. Documented
  as a known gap in the function's own docstring rather than silently
  handled.
- The full, untruncated text is never lost: `sentenceByAnalysis` (unchanged)
  is the only place a truncated sentence is used (`lines.answer`); "the
  numbers behind this" reads straight from `env`/the By-analysis tab's own
  row, not from this copy.

### Verification

- `tests/test_next_renders_text_cross_check.py`'s new `TestFirstSentenceTruncation`:
  a 5-case corpus (plain two-sentence paragraph, the reported fit-row shape,
  a rollup with a `·` separator, a period inside a parenthetical that must
  NOT split, and a text with no boundary at all that must come back
  byte-for-byte unchanged — the peer's own gate check, "How big is this
  database" has no ". " in it) cross-checked between a Python-only mirror
  (`_first_sentence`, same reasoning as `_lead_analysis_id`'s docstring for
  why there is no production Python equivalent to check against) and the
  real JS via node. Plus `test_the_fit_row_headline_slot_now_shows_one_sentence`,
  an end-to-end case using the reply's own fit-question corpus fixture with
  a multi-sentence headline substituted in, asserting `lines.answer` no
  longer carries the second/third sentences.
- Targeted: `uv run pytest tests/test_next_renders_text_cross_check.py -q -rf`
  — 8 passed (the pre-existing tests plus the new class).
- Broader: `uv run pytest tests/ -k "next or envelope or questions_tab or
  slice17" -q` — 707 passed, 1 skipped.
- Full suite (no `-k`, `-rf`): see commit message for the exact count from
  this run.
- **Live-verified on 8815** (this coordinator's own port — 8813 belongs to
  the architecture session's separate `wt-g1` worktree checkout, not this
  one, so it was left untouched rather than modified out from under that
  session's gate setup): reloaded `/next` → DBs → `laz_local_adventureworks`
  → Discovery → Questions. Both affected rows now read exactly:
  `NO REQUIREMENT DECLARED — no lens was supplied, so fit is not a question
  that has an answer here.` — one sentence, nothing after it. Confirmed via
  `get_page_text` (not a screenshot alone) that no second sentence from
  `preliminary_fit`'s own explanation follows on either row. Also confirmed
  via a standalone node check that the docling-style em-dash example the
  peer named ("9 components recovered — 9 of 107 component paths reviewed by
  hand…") passes through `firstSentence` completely unchanged, since it has
  no ". " boundary — matching the requirement that "How big is this
  database" and similar em-dash rows stay exactly as they were.

## Files touched

- `resource_explorer/web/static/next/glyphs.js`
- `resource_explorer/web/static/next/envelope.js`
- `resource_explorer/web/static/next/app.js`
- `tests/test_next_renders_text_cross_check.py`
- `G2-QUESTIONS-HEADLINE-SLOT-IMPLEMENTED.md` (this file)
