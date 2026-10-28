/* The stage-specific leading tabs, the plumbing that needs the app (Brief S0, DORMANT).
 *
 * The gate, ids and entries live in stage-tabs.js (a leaf). This file holds what needs `state`: the tab-label
 * counts, the running-commit line and its single writer. Nothing here moves a section. The leading tabs always
 * open on tab 1 (owner, 2026-10-11: no landing preference). */

import { state, esc, openCurate } from '/static/next/app.js';
import { stateEntry } from '/static/next/glyphs.js';
import { tabCounts } from '/static/next/stages/curate-counts.js';
import { stageTabsOn } from '/static/next/stages/stage-tabs.js';

/* ── labels ──────────────────────────────────────────────────────────── */

/** The counts a tab label appends: the cue is the glyph + a short word, the full sentence is the tooltip.
 *  Returns '' for a tab with no counts. `ctx` = { kind, view, plan } (state.curate.countCtx). */
export function tabCountsHtml(tabId, ctx) {
  const parts = tabCounts(tabId, ctx);
  if (!parts.length) return '';
  return ` <span data-tab-counts class="text-provenance text-ink-muted">${parts.map((p) => {
    const e = stateEntry(p.cue);
    return `<span data-cue="${esc(p.cue)}" data-count-state="${esc(p.state)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(p.word)}</span>`;
  }).join(' · ')}</span>`;
}

/* ── the running-commit line ─────────────────────────────────────────── */

/** A commit record (repo curation record or database catalogue commit record: an id and its steps) as the
 *  one-line shape. The WORDS come from the record's steps, never from a click. */
export function commitLineFromRecord(kind, rec) {
  if (!rec || !rec.id || !Array.isArray(rec.steps)) return null;
  const steps = rec.steps;
  const running = steps.find((s) => s.state === 'running');
  const failed = steps.filter((s) => s.state === 'failed');
  const done = steps.filter((s) => ['done', 'skipped', 'failed'].includes(s.state)).length;
  const finished = steps.length > 0 && done === steps.length;
  return {
    kind, id: String(rec.id),
    state: failed.length || rec.state === 'failed' ? 'failed' : (finished || rec.state === 'done' ? 'published' : 'running'),
    step: running ? steps.indexOf(running) + 1 : Math.min(steps.length, done),
    steps: steps.length,
    started: rec.requested_at || '',
    finished: rec.finished_at || '',
    readBack: rec.read_back_at || (finished ? (rec.finished_at || '') : ''),
    reason: failed.length ? String(failed[0].reason || failed[0].error || rec.reason || '') : '',
  };
}

/** The line itself. Empty when there is no record. */
export function commitLineHtml(line) {
  if (!line) return '';
  const cue = line.state === 'failed' ? 'error' : line.state === 'published' ? 'measured' : 'running';
  const e = stateEntry(cue);
  const open = cue === 'measured' ? '<span class="text-state-ok"' : cue === 'error' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  const word = line.state === 'running' ? `step ${line.step} of ${line.steps} · running`
    : line.state === 'published' ? `published${line.readBack ? ` · read back ${String(line.readBack).slice(0, 10)}` : ''}`
      : `failed${line.reason ? ` · ${line.reason}` : ''}`;
  return `<div data-curate-commit-line data-commit-line-state="${esc(line.state)}" class="mb-s3 text-caveat text-ink">${open} data-cue="${cue}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span></span> Publish · commit ${esc(line.id.slice(0, 4))} · ${esc(word)} · <button type="button" data-curate-commit-goto="curate_contents" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Contents ›</button></div>`;
}

/** Make the line's "Contents ›" link switch tab. A property handler, so binding it twice (the rail's bind and
 *  setCommitLine's refresh) never doubles the click. */
export function bindCommitLine(root = globalThis.document) {
  root?.querySelectorAll?.('[data-curate-commit-goto]').forEach((b) => {
    b.onclick = () => openCurate({ tab: b.dataset.curateCommitGoto });
  });
}

/** Single writer: store the record's line and, when the rail is on screen, refresh the line under it.
 *  A no-op while the gate is off, so the existing pollers can call it unconditionally. */
export function setCommitLine(line) {
  if (!stageTabsOn(state.stage, state.resourceType) || state.stage !== 'curate') return false;
  state.curate = state.curate || {};
  state.curate.commitLine = line || null;
  const doc = globalThis.document;
  if (!doc) return true;
  const html = commitLineHtml(state.curate.commitLine);
  const old = doc.querySelector('[data-curate-commit-line]');
  if (old) {
    if (html) old.outerHTML = html; else old.remove();
  } else if (html) {
    doc.querySelector('[data-subtab-rail]')?.insertAdjacentHTML('afterend', html);
  }
  bindCommitLine(doc);
  return true;
}
