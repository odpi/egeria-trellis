# Glyph state-tone color fix (implemented)

**Implements:** the designer's reply to `ASK-DESIGNER-ENRICHMENT-STAGE-IA.md`, §5,
accepted by design 2026-09-29.
**Branch:** `re/glyph-state-tone-color-fix`, off `origin/main`.
**Date:** 2026-09-29.

---

## 1 · The rule

**The accent color means "you can click this" (a control), and must never be used
for a STATE.** `text-accent-ink`/`text-accent-on-dark` are reserved for actual
controls; a glyph or state-tone entry using them for a *state* misleads a reader
into treating a fact about the world as something clickable.

## 2 · The three corrected states

| State | File | Was | Now |
|---|---|---|---|
| `partial` | `app.js`'s `STATE_TONE` | `text-accent-ink` | `text-state-warn` (`text-state-warn-on-dark` on chrome) |
| `human` / `needs-lens` | `glyphs.js`'s `STATES`, `app.js`'s `STATE_TONE` | `text-accent-ink` (both files) | `text-ink` (`text-chrome-ink` on chrome) |
| `running` | `glyphs.js`'s `STATES`, `app.js`'s `STATE_TONE` | `text-accent-ink` (both files) | `text-ink-muted` (`text-chrome-muted` on chrome) |

- **`partial`** was the exact inconsistency `G1-GLYPH-CONSOLIDATION-IMPLEMENTED.md`
  §1.3 flagged and deliberately left open: `glyphs.js`'s `STATES.partial.tone` was
  already `text-state-warn`, while `app.js`'s `STATE_TONE.partial` was
  `text-accent-ink`. `app.js` was the one that was wrong; `glyphs.js` needed no
  change for this state.
- **`human`/`needs-lens`** (the ⚠ glyph, "needs a person") were `text-accent-ink`
  in *both* `glyphs.js` and `app.js`. Corrected to `text-ink` in both — a
  full-strength instruction to the reader, neither a warning nor a finding.
- **`running`** (the ◔ glyph) was `text-accent-ink` in *both* files. Corrected to
  `text-ink-muted` in both.

Chrome-ground (dark) values in `app.js`'s two-ground `STATE_TONE` table were paired
using the same convention the table already uses elsewhere: `text-ink` ↔
`text-chrome-ink`, `text-ink-muted` ↔ `text-chrome-muted` (see `unclassified`'s
existing entry, and the `mutedCls` chrome/paper switch earlier in `app.js`).

## 3 · Where the rule now lives

`glyphs.js`'s header comment states the rule directly — "the accent colors
controls, never states" — next to the module's colour-consolidation history, so a
future edit doesn't drift back into using an accent tone for a state. `app.js`'s
`STATE_TONE` table carries a shorter pointer comment to the same rule.

## 4 · Structural harness test

`frontend-build/test-harness/state-tone-no-accent.test.mjs`, following this
codebase's established render-harness pattern (loads the real `glyphs.js` and
`app.js` module graph via `static-loader.mjs`/`dom-harness.mjs`, not a re-typed
fixture). Three tests:

1. Scans every entry of `glyphs.js`'s `STATES` and asserts none resolve to
   `text-accent-ink`.
2. Scans every entry of `app.js`'s `STATE_TONE` (both `paper` and `chrome`
   grounds) and asserts none resolve to `text-accent-ink`/`text-accent-on-dark`.
3. Pins the three states' corrected values directly, as a fixed-value check on
   top of the structural scan.

This is a structural guard, not just a fixed-value check on the three states
named above: a new state added to either table in the future with an accent tone
fails test 1 or 2 immediately, without needing a dedicated assertion of its own.

`app.js`'s `STATE_TONE` was previously module-private; it now carries an `export`
keyword (no logic change) so the harness test can read it directly, the same
pattern this harness already uses for `surveyRowHtml`/`tableHtml`/
`schemaTreeHtml`/`filterSchemaTree` (see `frontend-build/test-harness/README.md`).

## 5 · Test results

- **Harness suite** (`cd frontend-build && npm run test:harness`, Node v20.11.0
  via `nvm`): 17/17 passed, including the 3 new tests. No existing harness test
  asserted the old accent-ink values for `partial`/`human`/`needs-lens`/`running`,
  so nothing needed updating for the fix itself.
- **Python-side source-text tests**: grepped `tests/` for `text-accent-ink` near
  `partial`/`needs-lens`/`running` — no hits. No Python test asserted the old
  colors for these states.
- **Full suite** (`uv sync --all-packages --extra dev && uv run pytest tests/ -q
  -rf`): see commit/PR notes for the run's pass/fail counts.

## 6 · CSS rebuild

Not needed. This change only reassigns which existing Tailwind utility class each
state maps to (`text-accent-ink` → `text-state-warn`/`text-ink`/`text-ink-muted`,
and their chrome-ground counterparts) — no new class was introduced, and every
class involved was already present in `tailwind-next.css` before this change (used
elsewhere in the same tables).
