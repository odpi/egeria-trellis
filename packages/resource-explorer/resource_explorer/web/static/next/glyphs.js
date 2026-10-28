/* The one glyph table — Resource Explorer's `/next` UI.
 *
 * REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §1: before this module, three
 * places each declared their own glyph-to-meaning mapping and disagreed with
 * each other about what a glyph meant --
 *
 *   `GLYPH`      (app.js, Questions rows)          -- ◐ meant "ran, not at
 *                                                      this level"; ○ meant
 *                                                      two different things
 *   `CELL`       (worklist.js, the comparison grid) -- ◐ meant "partial";
 *                                                      ○'s two meanings were
 *                                                      told apart by colour
 *                                                      alone, which breaks
 *                                                      the "colour is never
 *                                                      the only channel" rule
 *   `factGlyph`  (app.js, members rail / run history) -- a third, smaller
 *                                                      table
 *
 * This module is the single source. `GLYPH`, `CELL` and `factGlyph` are now
 * thin views over `STATES` below (see app.js and worklist.js) rather than
 * independent declarations -- there is exactly one glyph-to-meaning mapping
 * in the codebase, and it lives here.
 *
 * SHAPE: each entry is keyed by STATE -- a specific, named condition such as
 * "answered" or "no-surveyor" -- never by glyph. Several states legitimately
 * share a glyph (that's the "family" column in the reply's table): the glyph
 * names the family, meaning what a reader can do with this at a glance, and
 * `word` names the exact state for `title`/`aria-label`. Nothing is merged
 * in the data or on screen this way -- only the glyphs are grouped, and per
 * the reply, colour is still never the only channel carrying a state's
 * meaning (see the `tone` values, which match the STATE_TONE roles app.js
 * already used before this module existed).
 *
 * Only the states actually read by a caller today are included, plus the
 * handful named in the reply's own table (marked RESERVED below) that G2
 * (contents board) and G3 (schema-inventory tree) are blocked on this
 * module to define -- the dispatch explicitly holds both until this lands,
 * "since they both reference the exact glyph set you're defining here."
 *
 * A NOTE ON ⚠: per the reply, this glyph now means ONLY "needs a person".
 * A disagreement between two measurements uses `≠`; a failure uses `✕`.
 * Before this change, app.js's `openRunsList` (run-history step rows) used
 * ⚠ for "this step's status was not ok", which is an error, not something
 * that needs a person -- fixed alongside this table, see app.js.
 *
 * THIS MODULE HAS NO IMPORTS, deliberately -- every one of its several
 * consumers (app.js, worklist.js, stages/curate.js, and whichever of G2/G3's
 * new surfaces come next) can import it with no risk of a cycle.
 *
 * COLOUR RULE (GLYPH-STATE-TONE-COLOR-FIX-IMPLEMENTED.md, designer reply to
 * ASK-DESIGNER-ENRICHMENT-STAGE-IA.md §5, accepted 2026-09-29): the accent
 * colors controls, never states. `text-accent-ink`/`text-accent-on-dark`
 * mean "you can click this" -- reserved for actual controls -- and must
 * never appear as a state's `tone` here or in app.js's `STATE_TONE`. This
 * module's own §1.3 of the G1 doc flagged `partial` as an unresolved
 * inconsistency between this table and `STATE_TONE`; that and two more
 * accent-for-state uses (`human`/`needs-lens`, `running`) were fixed
 * together. `tests/test_next_state_tone_no_accent.py` guards this
 * structurally.
 */

/**
 * A pinned symbol-font stack for the four glyphs the reply names as an
 * off-Mac risk: ◐ ◌ ∅ ◔ are not reliably present in default system fonts
 * outside macOS (the reply: "In a headless Linux browser, ◐ came out as a
 * sliver until I set the font explicitly"). The whole vocabulary is pinned
 * to the same stack rather than only those four, so a reader never sees one
 * glyph rendered in the body serif and its neighbour in a fallback symbol
 * font -- a state vocabulary should look like one alphabet.
 *
 * Chosen over inline SVG (the reply's other option): these are plain BMP
 * symbols (U+2600-27BF, U+25A0-25FF) that a real symbol font renders
 * correctly once one is reachable, and the states these glyphs annotate
 * already carry their word in `title`/`aria-label` (see `glyphSpan` below),
 * so a tofu box is a legibility issue, not a meaning-loss issue, if every
 * font in the stack is somehow absent. Noto Sans Symbols 2 is the broadest
 * coverage available as a web-safe reference (not vendored -- this stack is
 * pure CSS `font-family` fallback, resolved against whatever the host has
 * installed, same as the rest of `/next`'s type system).
 *
 * Wired into Tailwind as the `font-glyph` utility -- see
 * frontend-build/tailwind-next.config.js's `fontFamily.glyph` -- rather than
 * hand-authored here, so it goes through the same generated-CSS pipeline as
 * every other font role in this UI (`font-body`, `font-heading`, `font-mono`).
 * This constant exists for the one call site (`glyphSpan`, `legendSpan`)
 * that needs the class name as a string, and as documentation for why that
 * class exists.
 */
export const GLYPH_FONT_CLASS = 'font-glyph';

/**
 * STATES: the one glyph table.
 *
 * `glyph`  -- the mark itself
 * `family` -- what the reply calls the family; several states can share one
 * `word`   -- the exact state, for `title`/`aria-label` and legend text
 * `tone`   -- the Tailwind text-color utility for this state's paper ground
 *             (chrome-ground variants stay in app.js's STATE_TONE, which
 *             still exists for the two-grounds cases GLYPH-keyed lookups
 *             need; the tone here is what a caller wants for a bare paper
 *             span, e.g. the worklist grid and the contents-board legend)
 */
export const STATES = {
  // ── ✓ measured ──────────────────────────────────────────────────────
  answered:  { glyph: '✓', family: 'measured', word: 'answered',  tone: 'text-state-ok' },
  automatic: { glyph: '✓', family: 'measured', word: 'answered automatically', tone: 'text-state-ok' },
  measured:  { glyph: '✓', family: 'measured', word: 'measured',  tone: 'text-state-ok' },

  // ── ∅ measured nothing ──────────────────────────────────────────────
  nothing:        { glyph: '∅', family: 'measured-nothing', word: 'nothing found', tone: 'text-state-ok' },
  // RESERVED for G3's tree overview (§3.2/§3.3 of the reply): a schema
  // counted and genuinely empty, as distinct from `not_measured` below.
  measured_zero:  { glyph: '∅', family: 'measured-nothing', word: '0', tone: 'text-state-ok' },
  // G3 (re/empty-state-split, landed 2026-09-27/28): `_SCHEMA_SHORTFALL_
  // LABELS`' own two classification strings for this family -- `empty` is
  // the counted-zero case (same family as `measured_zero` above, its own
  // word because G3's own label text is exactly "empty", not "0"); `no_tables`
  // is a schema with no tables at all, which is also a genuine, counted
  // absence rather than an unmeasured one.
  empty:      { glyph: '∅', family: 'measured-nothing', word: 'empty', tone: 'text-state-ok' },
  no_tables:  { glyph: '∅', family: 'measured-nothing', word: 'no tables', tone: 'text-state-ok' },

  // ── ◐ measured, within a limit ──────────────────────────────────────
  partial:         { glyph: '◐', family: 'limited', word: 'partial', tone: 'text-state-warn' },
  // RESERVED for G2/G3: the reply's own new cases for this family.
  // `structure_only`'s key and word already matched G3's own
  // `_SCHEMA_SHORTFALL_LABELS` entry (`structure_only: 'structure only'`)
  // when this was first written -- confirmed, not just assumed, against
  // G3's landed branch (re/empty-state-split).
  scoped:          { glyph: '◐', family: 'limited', word: 'within credential scope', tone: 'text-state-warn' },
  structure_only:  { glyph: '◐', family: 'limited', word: 'structure only', tone: 'text-state-warn' },
  partly_readable: { glyph: '◐', family: 'limited', word: 'partly readable', tone: 'text-state-warn' },

  // ── ⊘ ended by a decision -- NEUTRAL: a cancel is neither a success nor a fault ──
  cancelled: { glyph: '⊘', family: 'not-run', word: 'cancelled', tone: 'text-ink-muted' },

  // ── ○ not run, and can be ───────────────────────────────────────────
  unrun: { glyph: '○', family: 'not-run', word: 'not run', tone: 'text-state-warn' },

  // ── ○ optional, not done -- NEUTRAL (EGERIA-REGISTRATION: registering with Egeria is a choice) ──
  // Same family as `unrun` so the glyph reads "not done"; the tone is muted, not the warn colour, and the
  // word says it is not a fault. Used where nothing is wrong and nothing is owed.
  optional: { glyph: '○', family: 'not-run', word: 'not registered', tone: 'text-ink-muted' },

  // ── ◌ can't be answered here yet ────────────────────────────────────
  // `no-surveyor` MOVES here from ○ per the reply -- it used to share a
  // glyph with `unrun`, told apart (in worklist.js's CELL) by colour alone,
  // which is exactly the thing the reply's palette rule forbids.
  'no-surveyor': { glyph: '◌', family: 'no-answer-here', word: 'no reader yet', tone: 'text-state-gap' },
  // A count of things still awaiting a choice (Contents tab "33 undecided"): ✓ would say the opposite.
  undecided_count: { glyph: '◌', family: 'no-answer-here', word: 'undecided', tone: 'text-ink-muted' },
  // RESERVED for G2/G3.
  no_reader:               { glyph: '◌', family: 'no-answer-here', word: 'no reader yet', tone: 'text-state-gap' },
  containment_undeclared:  { glyph: '◌', family: 'no-answer-here', word: "this engine's grouping isn't declared yet", tone: 'text-state-gap' },

  // ── ? tried, couldn't establish (distinct from ∅ -- genuinely unknown,
  //    not counted-and-empty) ──────────────────────────────────────────
  unknown: { glyph: '?', family: 'not-established', word: 'could not read', tone: 'text-ink-muted' },
  // RESERVED for G2/G3. `not_measured` and `no_access` use G3's own exact
  // label text (`_SCHEMA_SHORTFALL_LABELS`, re/empty-state-split) rather
  // than a paraphrase, so a schema row's title/aria-label matches its
  // visible word one-for-one.
  not_established: { glyph: '?', family: 'not-established', word: 'not established', tone: 'text-ink-muted' },
  // A registration that existed: Egeria answered that the element RE had stored is no longer there. NO GLYPH
  // (designer 2026-10-16, Q10): the ? family means "couldn't establish", and this WAS established -- Egeria
  // answered. The words carry it.
  gone:            { glyph: '', family: 'established', word: 'no longer in Egeria', tone: 'text-ink-muted' },
  not_measured:    { glyph: '?', family: 'not-established', word: 'rows not measured', tone: 'text-ink-muted' },
  no_access:       { glyph: '?', family: 'not-established', word: 'no access', tone: 'text-ink-muted' },

  // ── ⚠ needs a person -- ONLY this meaning now ───────────────────────
  // tone: text-ink, not text-accent-ink -- this is a full-strength
  // instruction to the reader, neither a warning nor a finding, and the
  // accent color is reserved for controls (see the module docstring).
  human: { glyph: '⚠', family: 'needs-person', word: 'needs you', tone: 'text-ink' },
  // G2 (REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md §2.4): `preliminary_fit`'s
  // `no_requirement_declared` verdict is a real, measured answer -- no lens
  // was supplied, so fit is not a question this credential can settle -- but
  // it is a statement ABOUT THE LENS, not about the resource
  // (`compute_preliminary_fit`'s own docstring). A tick reads as "this was
  // answered"; the honest state says a person still needs to supply the
  // missing lens. Same family/glyph as `human` (still "needs a person"),
  // its own word because "needs you" alone does not say what is needed.
  'needs-lens': { glyph: '⚠', family: 'needs-person', word: 'needs a person: declare a lens', tone: 'text-ink' },

  // ── as today ─────────────────────────────────────────────────────────
  // tone: text-ink-muted, not text-accent-ink -- see the module docstring.
  running:      { glyph: '◔', family: 'running',      word: 'running',      tone: 'text-ink-muted' },
  error:        { glyph: '✕', family: 'failed',       word: 'error',        tone: 'text-state-warn' },
  // §17.1 of app.js's original GLYPH: a proposal is a decision point, not a
  // state of the world, so it earns its own mark rather than borrowing
  // `unrun`'s ○ or `human`'s ⚠.
  proposal:     { glyph: '⏵', family: 'proposal',     word: 'proposal',     tone: 'text-state-warn' },
  unclassified: { glyph: '·', family: 'unclassified', word: 'unclassified', tone: 'text-ink-muted' },

  // ── the catalogue commit's states on a schema or table row (Curate slice B) ──
  // Each of these is only ever drawn from a persisted proof row (the catalogue
  // commit's read-back, or an outbox row): see catalogue_commit.derive_commit_state.
  // They reuse the families above instead of inventing a seventh.
  catalogued:       { glyph: '✓', family: 'measured', word: 'published', tone: 'text-state-ok' },
  attached_waiting: { glyph: '◔', family: 'running',  word: 'attached, waiting', tone: 'text-ink-muted' },
  queued:           { glyph: '◔', family: 'running',  word: 'queued', tone: 'text-ink-muted' },
  catalogue_failed: { glyph: '✕', family: 'failed',   word: 'failed', tone: 'text-state-warn' },
  // `deleted` has NO glyph (designer addendum 2026-10-06): ∅ keeps one meaning, "nothing found". A soft
  // delete in Egeria is the word 'deleted' and a lowered row, nothing else.
  archived:         { glyph: '□', family: 'stored',   word: 'archived', tone: 'text-ink-muted' },

  // □ stored -- not one of the reply's merged families (it names a fifth
  // condition the design brief doesn't cover: "results exist, not yet
  // read"), so it keeps its own glyph. Still declared here, and only here,
  // so nothing outside this module declares a glyph of its own.
  stored: { glyph: '□', family: 'stored', word: 'has results · not read yet', tone: 'text-ink-muted' },
};

// G3's `_SCHEMA_SHORTFALL_LABELS` (re/empty-state-split) declares TWO more
// classification strings this module deliberately does NOT give a glyph:
// `staging` ("staging (by name)") and `views_only`. Both are structural
// descriptors of a schema's CONTENTS (its tables are named like staging
// tables; it holds only views), not a claim about whether that content was
// successfully measured -- the axis every state above is about. Giving them
// a glyph from this table would either reuse one of the above (misdescribing
// them as "limited" or "not established" when nothing failed to measure) or
// invent a sixth family the reply's own table never asked for. They render
// as plain text in the tree, same as the table-kind words (`table`/`view`/
// `matview`) already do.
const FALLBACK = STATES.unclassified;

/** The full entry for a state, or the `unclassified` fallback for an
 *  unrecognized one -- never `undefined`, so every caller can read
 *  `.glyph`/`.tone`/`.word` unconditionally. */
export function stateEntry(state) {
  return STATES[state] || FALLBACK;
}

export function glyphOf(state) { return stateEntry(state).glyph; }
export function toneOf(state) { return stateEntry(state).tone; }
export function wordOf(state) { return stateEntry(state).word; }

/** Minimal, dependency-free HTML-attribute escaping -- this module has no
 *  imports (see the module docstring), so it does not reach for app.js's or
 *  format.js's `esc()`. Every `word` above is a literal string this module
 *  itself wrote, so this only guards against a future entry that isn't. */
function escAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * A self-labelling glyph span: the mark, the pinned font stack, and its word
 * in both `title` (hover) and `aria-label` (screen reader) -- "every glyph
 * must be screen-reader-and-hover legible, not just a bare Unicode
 * character" (dispatch). Safe to nest inside an ancestor that already
 * carries its own `title` (e.g. a grid `<td>`); the innermost `title` wins
 * on hover and the `aria-label` here is what a screen reader announces for
 * the glyph itself rather than the whole cell.
 */
export function glyphSpan(state, extraClass = '') {
  const s = stateEntry(state);
  const cls = [s.tone, GLYPH_FONT_CLASS, extraClass].filter(Boolean).join(' ');
  return `<span class="${cls}" title="${escAttr(s.word)}" aria-label="${escAttr(s.word)}">${s.glyph}</span>`;
}

/** One legend entry (glyph + word), for the count-line-as-legend pattern
 *  the reply asks every surface to use -- Questions' `KEY ✓ answered 4 · …`,
 *  the contents board's `7 analyses · ✓ 5 measured · …`, the tree's
 *  `8 schemas · ◐ 2 structure only · …`. Callers format the surrounding
 *  count text; this returns just the labelled glyph for one state. */
export function legendSpan(state, extraClass = '') {
  return glyphSpan(state, extraClass);
}
