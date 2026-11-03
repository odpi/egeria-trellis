# G1 — one consolidated glyph table (implemented)

**Implements:** `REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` §1 ("One glyph table, in
one module").
**Dispatch item:** G1, from the "Resource Explorer expansion architecture" session's
dispatch plan. First item in a sequence — G2 (By-analysis/contents-board slice) and
G3 (schema-inventory tree, "empty" split) were held until this landed, since both
reference the exact glyph set this module defines.
**Branch:** `re/g1-glyph-consolidation`, off `origin/main` at `b6a578de` (rebased
there mid-task on the architecture session's instruction, to land after PR #320 —
see Coordination below).
**Date:** 2026-09-27/28.

---

## 1 · What changed

### 1.1 · The one table

New module: `resource_explorer/web/static/next/glyphs.js`. It has no imports, so
every consumer — current or future — can import it with no risk of a cycle.

It exports `STATES`, a single object keyed by STATE (never by glyph). Each entry
carries `{ glyph, family, word, tone }`:

- `glyph` — the mark
- `family` — the reply's own grouping (`measured`, `limited`, `not-run`,
  `no-answer-here`, `not-established`, `needs-person`, `running`, `failed`,
  `proposal`, `unclassified`, `stored`)
- `word` — the exact state, for `title`/`aria-label` and legend text — "Every
  state keeps its own word, so nothing is merged in the data or on screen. Only
  the glyphs are grouped."
- `tone` — a default Tailwind text-color role, used by the module's own
  `glyphSpan`/`legendSpan` helpers (new consumers); existing consumers keep their
  own, already-reviewed tone tables (see §1.3 below — this pass consolidates glyph
  MEANING, not colour).

Covers every symbol the dispatch named (✓ ∅ ◐ ○ ◌ ? ⚠ ◔ ✕ ⏵ · plus `□`, which the
reply doesn't name but which existing code (`CELL.stored`) already used for a fifth
condition — "results exist, not yet read" — distinct from every family the reply
does define, so it keeps its own entry rather than being folded into one of the
merged glyphs). Some entries are marked `RESERVED` in a comment — states the reply's
own table names (e.g. `scoped`/`structure_only`/`partly_readable` for ◐,
`not_measured`/`no_access` for `?`) that no current consumer reads yet, but that G2
and G3 are expected to need — declared now so "the exact glyph set" dispatch says G2/
G3 are blocked on is actually complete, not just the subset today's three consumers
happen to use.

### 1.2 · Three consumers become thin views over it

- **`app.js`'s `GLYPH`** — was a literal `{ answered: '✓', unrun: '○', ... }`
  object. Now `Object.fromEntries(GLYPH_KEYS.map((k) => [k, GLYPH_STATES[k].glyph]))`.
- **`worklist.js`'s `CELL`** — was a literal object with its own glyph AND its own
  label per key. Now derives `glyph`/`label` from `GLYPH_STATES`, keeping only
  `tone` declared locally (see §1.3).
- **`app.js`'s `factGlyph`** — was a `switch` returning literal glyphs. Now returns
  `GLYPH_STATES.<state>.glyph`, tone unchanged.

**The specific regression the reply names by example is fixed:** `no-surveyor` used
to share `○` with `unrun` in both `GLYPH` and `CELL`, "told apart by colour alone,"
which the palette's own rule forbids. It now reads `◌` (`no-answer-here` family),
its own glyph, in both.

**Two more disagreements the reply's own table exposed, fixed the same way:**
`GLYPH`'s Questions-tab `LEGEND` array used to hardcode `partial` as "ran, but not
at this level" — the OLD, Questions-only meaning of ◐ — while `CELL`'s legend called
the very same glyph "partial." `LEGEND`'s words now come from `GLYPH_STATES[k].word`
directly (`LEGEND_ORDER` declares only the reading order, not a second vocabulary),
so both surfaces show "partial." Similarly `⚠` is now used ONLY for "needs you" —
see §1.4.

### 1.3 · Tone (colour) was deliberately NOT consolidated

The reply's ask is about glyph *meaning* — "Each entry must carry both the symbol
and its state word" — not about colour. `GLYPH`'s `STATE_TONE` (two-ground,
chrome/paper) and `CELL`'s per-key tone are unchanged from before this pass. One
real inconsistency was found and left alone on purpose: `STATE_TONE.partial` is
`text-accent-ink` while `CELL`'s `partial` tone is `text-state-warn` — different
colours for the same state, on two different surfaces. Fixing it would be a real,
user-visible colour change the designer has not reviewed, so it is flagged here
rather than changed silently. Recommend folding it into whichever of G2/G3 next
touches a shared legend, or a dedicated follow-up.

### 1.4 · `⚠` now means only "needs a person"

Per the reply: "disagreements use `≠`, errors use `✕`." One real, in-code violation
of this was found and fixed: `app.js`'s run-history step list (`openRunsList`) used
`⚠` for any step whose status was not `ok` — that is an error, not something that
needs a person. It now uses `✕` (`GLYPH_STATES.error`), tone unchanged
(`text-state-warn` in both cases, so this is a glyph-only fix, not a colour change).

### 1.5 · A fourth consumer, found by grepping `web/static/next/` for hardcoded glyphs

`stages/curate.js`'s `curateRecordHtml` declared a second, independent
task/step-state glyph map: `{ done: '✓', failed: '✗', running: '◐', skipped: '○',
pending: '○' }`. Its `◐` for "running" directly disagreed with the canonical
table's `◔` — exactly the "no glyph may carry two meanings" bar the dispatch names.
Consolidated by mapping curate's task states onto the existing fact-state vocabulary
(`done→measured, failed→error, running→running, skipped/pending→unrun`) and routing
through `factGlyph` (already imported in this file) rather than importing
`glyphs.js` directly — one fewer place in the codebase that reaches for a state
glyph at all. Every existing `tone` value was preserved exactly (verified by hand,
not just by test); the only visible changes are `◐→◔` for "running" and `✗→✕` for
"failed" (unifying to the canonical multiplication-sign `✕` used everywhere else,
rather than the ballot-x `✗` curate.js happened to use).

**Two more hardcoded glyph maps were found and deliberately left alone**, after
reading them in full:

- `stages/activity.js`'s `STATUS_GLYPH` (operation-log lifecycle: ok / success /
  error / failed / running / queued / pending, using `✗`/`⏳`) — a different axis
  (an *operation's* lifecycle, not a survey *fact's* state), and `⏳` isn't in the
  canonical vocabulary at all. Forcing it through would mean inventing new states
  the reply's table never defines.
- `admin/question_catalog.js`'s `STATUS` (an *answering mechanism* vocabulary:
  direct / analysis / human / mixed / gap / chart, using `●`/`⚙`/`◑`) — a
  genuinely different concept (how a question CAN be answered, not what state an
  answer is in), confirmed by reading `question_catalog.js`'s own header comment.

Both were confirmed with the "Resource Explorer expansion architecture" session
before implementation (see Coordination) and it agreed with leaving them out of
scope.

**Bar, updated per the dispatch's own instruction** ("If you find a fourth consumer,
cover it too and update this bar to four"): **one table, four consumers, no glyph
has two meanings** — `GLYPH`, `CELL`, `factGlyph`, and `curateRecordHtml` (via
`factGlyph`).

### 1.6 · Legend/count-line surfaces read the same table

- Questions tab's `KEY` line (`renderLegend` in `app.js`): words now come from
  `GLYPH_STATES`.
- Work list's `KEY` line (`renderLegend` in `worklist.js`): glyph/label already
  came from `CELL`, which is now itself derived from `GLYPH_STATES` — confirmed by
  the browser check in §3.
- The By-analysis / contents-board legend line the reply describes (*"7 analyses ·
  ✓ 5 measured · ○ 1 not run · ◌ 1 no reader yet"*) doesn't exist yet — it's part
  of G2, which is unblocked by this landing and should read `GLYPH_STATES`
  directly (or via `legendSpan`/`glyphOf`/`wordOf`, the helpers this module
  exports for exactly that).

## 2 · Font-rendering robustness (item 3)

**Chosen: a pinned symbol-font stack via a new Tailwind `font-glyph` utility**,
not inline SVG. `frontend-build/tailwind-next.config.js` gained a `glyph` entry
under `fontFamily`:

```
glyph: [
  '"Noto Sans Symbols 2"', '"Noto Sans Symbols"', '"DejaVu Sans"',
  '"Segoe UI Symbol"', '"Apple Symbols"', 'sans-serif',
],
```

Rebuilt with `npm run build:css:next` from a clean tree (after `npm install` in
`frontend-build/` — this worktree had no `node_modules`, being a fresh worktree;
`package-lock.json` was reverted afterward since an old local npm/node
(node 14.21.3, npm 6.14.18) rewrote it to a lower `lockfileVersion` as a side
effect of `npm install` — that rewrite was NOT intentional and is not part of this
change). Command and result:

```
$ npm run build:css:next
> tailwindcss -c tailwind-next.config.js -i ./input.css -o ../resource_explorer/web/static/next/tailwind-next.css --minify
Done in 635ms.
```

`tailwind-next.css` diff: **+1/-1 lines** (it's a single minified line; one new
selector, `.font-glyph{font-family:Noto Sans Symbols\ 2,Noto Sans Symbols,DejaVu
Sans,Segoe UI Symbol,Apple Symbols,sans-serif}`, was added to the existing
minified output). Verified this is the ONLY change by grepping the diff — no other
selector moved or changed.

**Why the whole vocabulary is pinned, not only the four glyphs the reply names
(◐ ◌ ∅ ◔):** so a reader never sees one glyph in the body serif and its neighbour
in a fallback font — a state vocabulary should read as one alphabet, not four
special cases plus the rest.

The `font-glyph` class was applied at every glyph render site touched by this pass:
the Questions-tab legend and row glyph (`app.js`), the work-list legend and every
grid-cell/list-row glyph (`worklist.js`), the run-history step icons (`app.js`),
and curate's step-state icons (`stages/curate.js`). Most of these sites also
gained `title`/`aria-label` carrying the state's `word` directly on the glyph
`<span>` itself (not only on an ancestor `<td>`/`<button>`, which several sites
already had) — per the dispatch's "every glyph must be screen-reader-and-hover
legible, not just a bare Unicode character."

## 3 · Verification

### 3.1 · Automated

New test: `tests/test_next_one_glyph_table.py`. Same technique
`test_next_renders_text_cross_check.py` already established for this repo (run the
real JS via `node`, extract the relevant source blocks by exact string slice rather
than re-implementing the logic in Python). Six tests:

1. `GLYPH`'s every key resolves to `GLYPH_STATES[key].glyph`.
2. `CELL`'s every key resolves to `GLYPH_STATES[key].glyph` AND `.word` (label).
3. `factGlyph`'s four cases resolve to the matching `GLYPH_STATES` entries.
4. `no-surveyor` is `◌` and is DIFFERENT from `unrun`'s `○`, in both `GLYPH` and
   `CELL` — the specific regression named in the reply, pinned directly.
5. Neither `GLYPH`'s nor `CELL`'s build blocks contain a literal glyph character
   as an object value any more (regex over the extracted source) — guards against
   a second table quietly reappearing.
6. `curate.js` no longer contains the old, independent task-glyph map.

All 6 pass. Also re-ran the full existing `-k next` test sweep (592 tests) after
these changes: one pre-existing test broke on a literal-string assumption
(`test_next_prerequisite_proposal_ui.py`'s `test_glyph_table_has_a_proposal_entry`
searched for the literal text `"const GLYPH = {"`, which no longer exists now that
`GLYPH` is derived) — fixed to check the new derivation shape and to confirm
`glyphs.js` gives `proposal` its own glyph, distinct from `unrun`'s and `human`'s.
After that fix, **592 passed, 0 failed**.

### 3.2 · Served and visually checked

Served on **port 8813** from this worktree (`/Users/dwolfson/localGit/egeria-v6/
trellis-g1-glyphs`, NOT the shared checkout — a new `.claude/launch.json` entry
was added at the user level, `~/.claude/launch.json`, following the pattern the
other concurrent worktree sessions already use there). Opened with a real browser
tool (Claude Browser pane, Chromium on macOS) and checked, with screenshots at
each step:

- **Questions tab, `laz_local_adventureworks`** (a real database resource with a
  mix of states): the `KEY` line reads `✓ answered 4 · ○ not run 2 · ◌ no
  surveyor 3`, and scrolling to the actual rows shows a row marked **◌ no
  surveyor yet** (a faint dotted circle) directly above a row marked **○** (a
  solid circle, "not run") — visually distinct marks, neither a tofu box nor a
  sliver, confirming the fix from §1.2 renders correctly in the live UI, not just
  in the source.
- **Questions tab, `amundsen`** (a repo, all-answered): `KEY ✓ answered 4 · ✓
  answered automatically 2` renders cleanly.
- **By-analysis tab, `laz_local_adventureworks`**: loads (slowly — this is an
  existing, unrelated latency in the dashboard-reads path, not something this
  change touched) and renders counts/rollups without error.
- **A work list** ("Egeria family — scouting"): its own `KEY` line renders all
  nine `CELL` glyphs distinctly — `✓ answered`, `∅ ran, found nothing`, `◐
  partial`, `○ not run`, `⚠ needs you`, `◌ no surveyor`, `· unclassified`, `◔
  running`, `□ has results · not read yet` — each visually distinct, `◌` and `○`
  clearly different marks side by side in the legend itself.
- Browser console: only three pre-existing 404s (`GET /api/projects/{db-slug}/
  scouting-overview`, a speculative endpoint the client tries before falling back
  to the databases API — present before this change, confirmed by reading
  `app.js`'s resource-routing code; unrelated to glyphs). No JavaScript errors,
  no failed module loads — `glyphs.js` itself loaded 200 OK as an ES module
  alongside every other `/static/next/*.js` file.

**What this does and doesn't establish:** this confirms the glyph vocabulary
renders correctly on macOS Chromium, which is what this environment can actually
run. It does **not** by itself confirm rendering on Linux/Windows or in
Firefox/Safari — the reply's own named risk ("In a headless Linux browser, ◐ came
out as a sliver"). The `font-glyph` fallback chain (Noto Sans Symbols 2 → Noto
Sans Symbols → DejaVu Sans → Segoe UI Symbol → Apple Symbols → sans-serif) is
designed to cover that case based on which of those fonts ship on which platforms,
but this pass could not verify it on an actual non-Mac host or browser — saying so
here explicitly rather than asserting a fix this environment cannot prove.

## 4 · Coordination

- Messaged the "Resource Explorer expansion architecture" session before any
  commit, naming every file to be touched and the four-consumer finding.
- That session relayed a base-branch change mid-task: PR #320
  (`re/renders-text-cross-check`, tip `b6a578de`) moved `readEnvelope` and its
  helpers out of `app.js` into a new `envelope.js`, landing physically close to
  the `GLYPH`/`STATE_TONE` block this task edits, though it turned out NOT to
  touch those lines directly (confirmed by diff before proceeding). Rebased onto
  `b6a578de` as instructed rather than `9bda025d`.
  It also confirmed the scope call on `activity.js`/`question_catalog.js` (§1.5)
  and set three checks: rebase onto #320's tip (done), commit `tailwind-next.css`
  exactly as the build tool produces it (done, verified the ONLY diff is the one
  new selector), and a served-page check before reporting (done, §3.2).
- Confirmed by reading the code (not just by the coordinator's relay) that G3's
  named risk area, `app.js`'s `_SCHEMA_SHORTFALL_LABELS` (~lines 3417-3420), is
  well outside every region this task touches. Attempted to message G3 (agent
  `a5788860`) directly after the rebase to re-confirm — that agent id was not
  reachable from this session — so the same confirmation, plus the exact line
  ranges this task edits, was relayed back through the "Resource Explorer
  expansion architecture" session with a request to forward it. No reply had
  arrived as of this push; if G3 finds an actual conflict, the fix is a rebase
  on either side, not a data-loss risk, since neither branch has merged.

## 5 · Files changed

- `packages/resource-explorer/resource_explorer/web/static/next/glyphs.js` (new)
- `packages/resource-explorer/resource_explorer/web/static/next/app.js`
- `packages/resource-explorer/resource_explorer/web/static/next/worklist.js`
- `packages/resource-explorer/resource_explorer/web/static/next/stages/curate.js`
- `packages/resource-explorer/frontend-build/tailwind-next.config.js`
- `packages/resource-explorer/resource_explorer/web/static/next/tailwind-next.css`
  (generated, +1/-1)
- `packages/resource-explorer/tests/test_next_one_glyph_table.py` (new)
- `packages/resource-explorer/tests/test_next_prerequisite_proposal_ui.py` (one
  test updated for the new derivation shape)

## 6 · Status

Implemented, tested (592 next-suite tests + 6 new ones, all green), and visually
verified on macOS Chromium. Not yet merged — per the dispatch, no PR is opened by
this session; the architecture session sequences the merge (after PR #320, per its
own instruction). G2 and G3 are unblocked.
