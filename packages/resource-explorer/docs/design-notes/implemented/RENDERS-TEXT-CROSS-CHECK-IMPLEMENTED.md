# `_renders_text` / `readEnvelope` cross-implementation test — implemented

BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §D. Tests-first; no screen gate. Branch
`re/renders-text-cross-check` off `origin/main` (019c2796).

## What was wrong

`facts.py`'s `FactLayer._renders_text` is a Python hand-mirror of
`readEnvelope`'s rendering rungs in `web/static/next/app.js`, with no shared
source between the two. It drifted twice: once the day it was written, and
again in Slice 21a (the Level column shipped, `_headline_for` never read it,
and container-level questions ticked ✓ on resource-level sentences for two
days — caught at a live gate, not by a test). Every pre-existing test
asserted only the Python side.

## Fix

### 1. `readEnvelope` split into its own module

`readEnvelope`, and the private helpers it and the rest of app.js's fact
rendering depend on (`answerHtml`, `prose`, `scalarMeasures`, `factMermaid`,
`cap`, `VERDICT`, and the fact-state constants `MEASURED`/`NOTHING_FOUND`/
`NOT_ESTABLISHED`/`NEVER_RUN`/`NO_READER`/`PARTIAL`), moved out of `app.js`
into a new module, `web/static/next/envelope.js` — following
`APP-JS-SPLIT-IMPLEMENTED.md`'s existing precedent (one module per coherent
piece of app.js) rather than inventing a different shape.

`app.js` cannot be imported whole under node — it runs `Auth.init(start)` and
reads `document.getElementById(...)` at module top level, both of which throw
with no DOM/no `auth.js`. Splitting `readEnvelope` into a DOM-free module
sidesteps that, rather than mocking a DOM.

`esc`/`tnum` stayed in `app.js` (17 other `/next` modules import them from
there; moving them would have meant editing every one of those imports, well
outside this section's scope) and are **passed into** `readEnvelope`/
`answerHtml` as parameters rather than imported by `envelope.js` — the same
pattern `format.js`'s `verdictLineHtml(r, esc)` already uses ("esc is passed
in because this module has no DOM helpers of its own"), and the only way to
avoid a circular `app.js` → `envelope.js` → `app.js` import. `whenMs` has no
such problem (it lives in `format.js`, which neither `app.js` nor
`envelope.js` needs to import back) and is imported normally.

`rowState`, `isFullyAnswered`, `GLYPH`, `STATE_TONE`, `tone` stayed in
`app.js` — none of them reference any of the moved constants/helpers, only
`env.answerable`/`env.level_mismatch`/`entry.kind`.

The three call sites (`readEnvelope(entry, env)` at two spots in the
Questions-row renderer and one in `rowAsMarkdown`) now read
`readEnvelope(entry, env, esc, tnum)`.

### 2. The cross-check test

`tests/test_next_renders_text_cross_check.py`, following the two established
node-from-pytest patterns in this repo (not a third):

- **Module-as-`.mjs`** (`test_next_journal_fidelity.py`'s
  `TestTheFormatter._run`): `envelope.js` and `format.js` are copied into
  `tmp_path` as `.mjs` siblings so node treats them as ES modules, with
  `envelope.js`'s `/static/next/format.js` import rewritten to the local
  copy.
- **String-extracted helpers** (`test_next_enrichment_persistence.py`'s
  `_extract`): `esc`, `tnum`, and `isFullyAnswered` are pulled out of
  `app.js`'s own source text (the same file `_extract`'s docstring already
  explains cannot be imported whole) and inlined into the node script,
  rather than moved into `envelope.js` for the reason above.

Two things are cross-checked per corpus case:

**(a) renders-or-not** — Python's `FactLayer._renders_text(fact)` against
whether `readEnvelope(...).answer` comes out non-empty for an envelope
holding exactly that one fact. `_renders_text` does not itself consult
`fact.is_known` (`test_slice17c_renderable_answer_gate.py`'s `TestRendersText`
calls it directly on facts of any state, "known" or not), and neither does
the per-fact branch inside `readEnvelope`'s sentence loop — only the OUTER
`known = facts.filter(f.is_known)` filter (applied by both `_check_level` and
`readEnvelope` before this logic is ever reached) does. To compare the two
primitives on equal footing, the JS side's envelope forces `is_known: true`
on the fact regardless of its state, mirroring how `test_slice17c` calls
`_renders_text` directly on facts of every state.

**(b) the level verdict** — Python's `FactLayer._check_level(env, question)`
sets `env.level_mismatch`/reads `env.answerable`; `isFullyAnswered(env)` is
the one sanctioned JS accessor for "does this get a checkmark" (its own
docstring: "callers that count or gate on 'answered' must use this, not
`env.answerable` alone"). This half is NOT forced — it carries Python's own
computed `answerable`/`level_mismatch` across and reads them back through
`isFullyAnswered`, since JS has no independent way to compute
`level_mismatch` itself (it needs the analysis catalog's `target_shape`
data, which only `facts.py` reads).

### 3. The corpus — 44 cases

| dimension | values |
|---|---|
| fact state | `measured`, `nothing_found`, `not_established`, `no_reader`, `measured_within_credential_scope` (5) |
| headline | present / absent (2) |
| question level | `resource`, `container`, `member`, `field` (4) |

5 × 2 × 4 = **40** grid cases, plus **4** rung-coverage cases at resource
level (where headline presence alone doesn't distinguish `readEnvelope`'s
rungs 2/3):

1. `measured/prose-only` — `value.detail` set, no headline (rung 2).
2. `measured/scalar-only` — a plain scalar field, no headline/prose (rung 3).
3. `measured/nested-dicts-only-renders-nothing` — the exact `db_resilience`
   shape from `test_slice17c_renderable_answer_gate.py` (every field a
   nested dict; rung 3 skips lists/objects by design, so this renders
   nothing on both sides — the case Slice 17c itself was written to catch).
4. `nothing_found/prose-only` — confirms `NOTHING_FOUND`'s synthesized
   sentence yields to real prose when prose is present, on both sides.

**Total: 44.** `test_corpus_size_is_the_documented_44` asserts this count
directly, so a shrunk corpus — someone "simplifying" the loop and dropping a
dimension — fails the build rather than silently covering less.

`test_python_and_js_agree_on_every_case` runs all 44 through both
implementations and reports every disagreement (not just the first) with the
case name, so a real drift is diagnosable from the failure message alone.

### Verifying the guard actually guards

Per `feedback_checks_weaker_than_they_look.md`: before trusting it, broke the
JS side on purpose. Short-circuited `envelope.js`'s `NOTHING_FOUND` special
case (`if (false && f.state === NOTHING_FOUND && ...)`), reran the test:
failed on exactly the 4 `nothing_found/no-headline/*` cases (one per level),
with the correct diagnostic (`python=True js=False`). Reverted, reran clean.

## Verification

- `uv run pytest tests/test_next_renders_text_cross_check.py -v` — 2 passed
  (corpus-size pin, full cross-check).
- `uv run pytest tests/ -k "next or facts or slice17" -q` — 745 passed, 0
  failed (the app.js/envelope.js split touched no other test's assumptions).
- Full suite: `uv run pytest tests/ -q` — see below.
- `node --check` (via `--input-type=module`) on both `app.js` and
  `envelope.js` — clean.
- **Live render check** (project-owner requirement, relayed via the
  architecture session): served the branch on a second port, 8813
  (`TRELLIS_ANONYMOUS_READ=true uv run resource-explorer web --host
  127.0.0.1 --port 8813 --no-embed-worker`, from the worktree checkout, never
  the shared one at `~/localGit/egeria-v6/trellis`), and opened `/next` in
  the Browser pane:
  - **Repo** (`amundsen`): Questions checklist renders 6 questions with real
    headline/prose/scalar answers and ticks (`✓ answered 4 · ✓ automatic 2`);
    clicked "evidence" on the first row — the evidence rail opened with the
    fact's own headline, measures and provenance. No console errors.
  - **Database** (`laz_local_adventureworks`): Questions checklist renders
    with the same shape — e.g. "How big is this database" answered via the
    rung-3 scalar fallback ("11 schema(s), 10 with tables … · 761,184
    row(s) …"), "Which schemas carry the data" via a headline sentence, both
    with real ✓ ticks. No console errors from the app.js/envelope.js split.
  - One pre-existing, unrelated console error observed on both resources:
    `GET /api/projects/{slug}/scouting-overview → 404` — a repo-scoped
    endpoint the frontend probes regardless of resource type; present before
    this change (confirmed by inspection — nothing in this diff touches that
    endpoint or its caller) and out of this section's scope.
  - By-analysis pane: opened, showed "Reading the dashboards…" and had not
    resolved after several seconds — `loadByAnalysisPane`'s own code comment
    says this read "has cost 109s on Analysis," so this is expected, known,
    pre-existing latency in `getSurveyDashboards()`, an entirely different
    API path from `readEnvelope`'s. Not touched by this change.

Full-suite result: `uv run pytest tests/ -q` from the worktree —
**6607 passed, 104 skipped, 0 failed** in 806.43s (13m26s). The 104 skips are
the pre-existing skips this repo already carries (node-not-installed guards
where relevant, and others unrelated to this change) — node was installed
here, so every `@pytest.mark.skipif(shutil.which("node") is None, ...)` test
in this file and its neighbors ran, not skipped.
