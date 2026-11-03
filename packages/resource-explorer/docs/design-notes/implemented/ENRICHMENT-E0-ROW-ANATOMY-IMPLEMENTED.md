# Implemented: Enrichment E0 — row anatomy for human-question answers

**Replies to:** `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` §0.3 / §6 item 1
(itself answering `ASK-DESIGNER-ENRICHMENT-STAGE-IA.md`).
**Read against:** `origin/main` at `d2e60e95`; the reply as committed on
`origin/re/design-enrichment-ia` (`e25d0663`, not yet merged to `main` at
the time of this work — see "What I could not find" below).
**Scope:** E0 only — the prerequisite slice the reply's own §6 ordering
names first. The Context tab (a new first sub-tab replacing the current
strip, per the reply's §1) is slice E1, a separate, larger IA restructure,
and is explicitly NOT built here.

---

## The problem

A person's input to a resource lived in two stores with two different
rules (reply §0.3):

| | `state.enrichment` (judgements/observations) | `state.contextAnswers` (human-question answers) |
|---|---|---|
| who | ✓ (`author`, server-stamped) | ✗ — nothing recorded it |
| when | ✓ (`set_at`) | ✓ (`answered_at`) |
| evidence-moved flag | ✓ (judgements) | ✗ |
| entered via | an inline input + save control | `window.prompt()` — one line, blocks the page |

The Questions tab showed *"answered 2d ago · change"* with no author at
all, breaking the reply's own stated rule that "a human-supplied answer
carries who and when." Design's ruling was **not** to merge the two
stores (a later engineering call) but to give both the **same row
anatomy** — who + when + the evidence-moved flag — through one shared
component.

## What changed

### 1. A shared row-anatomy component

New module: `resource_explorer/web/static/next/row-anatomy.js`, exporting
`personRowLineHtml({ author, whenIso, moved, verb, sourceLine, suffix })`
— the who + when + `"⚠ review — evidence moved: X"` line, as one function.

- `stages/enrichment.js`'s `fieldRowHtml` (judgements/observations) used to
  build this line inline; it now calls `personRowLineHtml` instead. No
  wording change for existing rows (`verb` defaults to `''`, reproducing
  the old plain `"dan · 2d ago"` style).
- `app.js`'s `bodyLines()` (`st === 'human'`, the Questions-tab answer row)
  now calls the **same** function, with `verb: 'answered by'`, producing
  *"answered by dan · just now"*.
- `fieldRowHtml` and `rowInner` both gained an `export` keyword (no other
  change) so the render harness can call the real functions — same pattern
  the harness's own README documents for `surveyRowHtml`/`schemaTreeHtml`/etc.

A render-harness test
(`frontend-build/test-harness/human-question-answer-row-anatomy.test.mjs`)
renders a real judgement row and a real question-answer row and asserts
each one's provenance line matches **character for character** what
`personRowLineHtml` itself returns for the equivalent input — not just
"looks similar." This is the proof that they're genuinely one component.

### 2. The author is now recorded and persisted, server-stamped

`resource_explorer/web/routes/context.py`:

- `QuestionAnswer` gained `answered_by: str = ""`.
- New route `PATCH /api/context/{entity_type}/{slug}/answer` (`save_answer`),
  mirroring `save_field`'s existing shape exactly: reads the signed-in
  identity via `get_current_user`, refuses anonymous writes with 401 ("an
  answer with no author is not an answer" — the same standard `save_field`
  already applies to a judgement), and does a read-modify-write on just
  `question_answers` so two people answering different questions on the
  same resource don't clobber each other.
- `question_key()` — a Python port of the client's `questionKey()` slug
  rule (`re-api.js`), so a server-stamped write's key matches what the
  client already computes for a read.

`re-api.js`'s `saveQuestionAnswer` no longer does its own client-side
read-modify-write of the whole context document (`getContext` then
`saveContext` with the answer spliced in, author never asked of anything)
— it now `PATCH`es the new `.../answer` route, same shape as
`saveEnrichmentField`'s `.../field`, and returns the server's stamped
`QuestionAnswer` for the caller to store directly.

Backend tests: `tests/test_answer_question_author.py` (mirrors
`test_enrichment_fields.py`'s coverage of `save_field`) — author-stamping,
the client cannot assert one, anonymous is refused, two answers save
independently, an answer save doesn't touch `enrichment` and vice versa,
round-trips through GET, and re-answering overwrites its own key only.

### 3. `window.prompt()` removed from the answer-a-question flow

`app.js`'s `wireHumanAnswers()` no longer calls `window.prompt()`. The
"Answer this →" / "change" button now toggles `state.editingAnswer` and
re-renders the row with an inline `<textarea>` + save/cancel control — the
same input+save shape `stages/enrichment.js`'s `fieldControlHtml` already
uses for judgements/observations, reused rather than invented fresh.

**Where the control lives, and why:** there is no general Context tab yet
(reply §1's fuller IA restructure — Context as a new first sub-tab — is
slice E1, explicitly out of scope here). So the control opens **on the
question row itself**, within the Questions-tab rendering that already
exists, rather than in a Context editor that doesn't exist yet. This is
the narrower, in-scope reading of the reply's "Context's editor in place
of `window.prompt`" instruction (§0.3): the *mechanism* (inline
input+save, not a popup) is what moved; the row it lives on stays where it
already was until E1 gives it a new home. On the Enrichment stage
specifically, the judgement/observation form and the question rows already
render in the same pane (`#enrichment-form` and `#question-rows` are
siblings in the same `loadPane()` output), so a person editing an
enrichment field and answering a catalog question are already visually
adjacent even without E1's restructure.

Source-text test: `tests/test_no_window_prompt_for_answering_questions.py`
— `window.prompt` is absent from both `wireHumanAnswers()` and
`bodyLines()`'s function bodies (comments stripped first, so the test's own
explanatory prose about the removal doesn't trip the assertion on itself),
the four inline-control hooks are present, and both write paths
(`saveQuestionAnswer`, `saveEnrichmentField`) still exist as distinct calls.

## Gate verification

**Test-level**, not live — see "What I could not verify" below.

`frontend-build/test-harness/human-question-answer-row-anatomy.test.mjs`
renders the real, unmodified `rowInner()` against a fixture answer with
`answered_by: 'dan'` and asserts the rendered DOM contains
*"answered by dan · just now"* (`tnum`-wrapped "just now", matching the
convention every other relative-time render in `/next` uses) — the gate's
exact wording, through the exact function the real page calls. A second
test confirms `window.prompt` never appears in the call path that produces
this row. A third confirms a legacy answer with no `answered_by` (recorded
before this change) degrades to the old plain wording rather than an empty
`"answered by  ·"` slot.

## Tests run

- `frontend-build`: `npm run test:harness` (Node 20 via `nvm use 20` —
  the default `node` on PATH was 14, too old for the loader hook) — **17
  passed, 0 failed**, including the 3 new tests above and all pre-existing
  ones (engine-note persistence, schema-inventory filter, by-analysis
  board timeout/headline/shared-names).
- Python: `uv run pytest tests/` — full suite; see the commit for the
  final pass/fail count captured at commit time.

## What I could not find

The task brief pointed at a saved `REPLY-*-ENRICHMENT-STAGE-IA.md` under
`docs/design-notes/` in `origin/main` / this worktree's starting point,
and none existed there — `git log --all --grep` found it as a real,
already-written commit (`e25d0663`, "Reply: the Enrichment stage's
sub-tabs") on `origin/re/design-enrichment-ia`, not yet merged to `main`.
Read directly from that commit (`git show
e25d0663:packages/resource-explorer/docs/design-notes/REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md`)
rather than assumed or reconstructed. Worth merging that branch (or at
least cherry-picking the reply doc + its wireframe) so the reply is
readable from `main` the normal way, and so this doc's own
"Replies to" pointer resolves for the next reader.

## What I could not verify live

No browser/live-registry verification this session — same reasoning
`UNBUILT-STAGES-IMPLEMENTED.md` recorded for its own change: Egeria is the
identity provider as of the 2026-09-04 runtime plan, and this session does
not hold sign-in credentials to reach `laz_local_adventureworks` through a
real login. The gate scenario is covered at the render-harness level
instead (real DOM, real unmodified functions, fixture data) — see above.
Worth a live check once this branch is reachable from a signed-in session:
answer a human question from the Questions tab on
`laz_local_adventureworks` and confirm *"answered by `<user>` · just now"*
appears with no popup, exactly as the harness test asserts.

## Addendum: the rendered-editor interaction test (PR/CI gap, closed)

PR/CI review of PR #358, before merge, flagged a real coverage gap: the
three tests listed under "Gate verification" above render fixture state and
assert the resulting text, and `tests/test_no_window_prompt_for_answering_
questions.py` proves `window.prompt()` is gone from the source text — but
nothing actually drove the interaction end to end: render a row → click
"Answer this →" → see a textarea appear → type into it → save → confirm the
PATCH fires with the entered text → confirm the row re-renders showing
"answered by `<user>` · just now". A fixture-only test cannot catch a wiring
break in `wireHumanAnswers()` itself (a bad selector, an unattached
listener, a request body missing a field) the way it can catch a wording
regression.

Closed by a fourth test added to `human-question-answer-row-anatomy.test.mjs`
(design's "no exceptions on day two" ruling on the harness rule — every
`/next` fix gets its regression in the render harness, not just its own
fixture-level check). It renders the real `rowInner()` (same exported
function the fixture tests above already use) for an unanswered human
question, attaches the real `wireHumanAnswers()` (newly given the `export`
keyword — same minimal, logic-unchanged pattern this branch's other
exports already use), and drives the full path through real DOM events: a
real `.click()` on the "Answer this →" button, asserting a real `<textarea>`
appears; typing into it and a real `.click()` on the save button; a stubbed
`fetch` asserting the PATCH to `/api/context/repo/<slug>/answer` fires
exactly once with the typed text in its body; and, using a stubbed response
shaped like the server's real author-stamped reply, asserting the row
re-renders with "answered by dan · just now" and the editor closes. No bug
was found while writing it — the editor already worked; this fills the
coverage gap PR/CI named, not a red/green fix.

## Judgment calls, for review

1. **Inline editor placed on the question row itself**, not inside a
   Context editor — see "Where the control lives, and why" above. This is
   the narrowest reading of §6 item 1 that doesn't attempt E1's scope; flag
   if design intended something else for the interim state before E1 lands.
2. **Legacy answers with no `answered_by`** (saved before this change)
   render the old plain `"answered <when>"` wording rather than a
   backfilled or guessed author — consistent with this project's repeated
   rule against inventing values for genuine gaps (`answered_by` defaults
   to `""`, an honest absence).
3. **`personRowLineHtml`'s `verb`/`suffix` params are plain, unescaped
   text** — by design, since only this codebase's own fixed strings
   (`'answered by'`, `' · interim'`) are ever passed, never user input. Not
   a general-purpose HTML builder; flagged in the function's own docstring.
