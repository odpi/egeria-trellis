/** Structural regression guard for GLYPH-STATE-TONE-COLOR-FIX-IMPLEMENTED.md:
 *  the accent color means "you can click this" (a control) and must never be
 *  used as a STATE tone.
 *
 *  Three real violations were found and fixed by that pass: `partial`
 *  (app.js's STATE_TONE was `text-accent-ink`; glyphs.js's STATES already
 *  had the correct `text-state-warn`), `human`/`needs-lens` (both files were
 *  `text-accent-ink`, corrected to `text-ink`), and `running` (both files
 *  were `text-accent-ink`, corrected to `text-ink-muted`).
 *
 *  This test does not just pin those three states' now-correct values --
 *  it scans every entry of both tables and asserts NONE of them resolve to
 *  an accent tone, on either ground (paper/chrome). That is the structural
 *  guard: a future state added to either table with an accent tone fails
 *  this test immediately, without needing its own dedicated assertion.
 *
 *  Loads the real production modules (glyphs.js's STATES, app.js's
 *  STATE_TONE, both freshly exported for this test -- see this directory's
 *  README "Adding a new render-harness test") rather than re-typing the
 *  tables here, so a future edit to either file is what this test actually
 *  observes.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const ACCENT_TONES = new Set(['text-accent-ink', 'text-accent-on-dark']);

test('no glyphs.js STATES entry uses an accent tone', async () => {
  makeDomEnvironment();
  // loadAppModule() registers the '/static/...' loader hook as a side
  // effect (see dom-harness.mjs's ensureLoaderRegistered) -- calling it
  // first lets glyphs.js be imported directly by its real root-relative
  // specifier below, the same way app.js itself imports it.
  await loadAppModule();
  const glyphs = await import('/static/next/glyphs.js');
  const offenders = Object.entries(glyphs.STATES)
    .filter(([, entry]) => ACCENT_TONES.has(entry.tone))
    .map(([key, entry]) => `${key}: ${entry.tone}`);
  assert.deepEqual(
    offenders,
    [],
    `glyphs.js STATES must never use an accent tone for a state (accent means "you can click this"): ${offenders.join(', ')}`,
  );
});

test('no app.js STATE_TONE entry uses an accent tone, on either ground', async () => {
  makeDomEnvironment();
  const app = await loadAppModule();
  const offenders = [];
  for (const [key, entry] of Object.entries(app.STATE_TONE)) {
    for (const ground of ['paper', 'chrome']) {
      if (ACCENT_TONES.has(entry[ground])) {
        offenders.push(`${key}.${ground}: ${entry[ground]}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `app.js STATE_TONE must never use an accent tone for a state (accent means "you can click this"): ${offenders.join(', ')}`,
  );
});

test('the three states named in GLYPH-STATE-TONE-COLOR-FIX-IMPLEMENTED.md resolve to their corrected tones', async () => {
  makeDomEnvironment();
  const glyphs = await import('/static/next/glyphs.js');
  const app = await loadAppModule();

  assert.equal(glyphs.STATES.partial.tone, 'text-state-warn');
  assert.equal(app.STATE_TONE.partial.paper, 'text-state-warn');

  assert.equal(glyphs.STATES.human.tone, 'text-ink');
  assert.equal(app.STATE_TONE.human.paper, 'text-ink');
  assert.equal(glyphs.STATES['needs-lens'].tone, 'text-ink');
  assert.equal(app.STATE_TONE['needs-lens'].paper, 'text-ink');

  assert.equal(glyphs.STATES.running.tone, 'text-ink-muted');
  assert.equal(app.STATE_TONE.running.paper, 'text-ink-muted');
});
