/* "Already up to date" and Run anyway (PI-063).
 *
 * The server declines a user-started repo analysis whose last run is still fresh and answers
 * `{status:'skipped', reason:'already-fresh', detail, activity_id:null}` (routes/projects.py). Before this, every
 * caller read `started.activity_id` off that answer and went on to poll `null`. This is the one place that
 * recognises the answer, says it, and offers the run again with `?force=true`.
 *
 * The sentence is the server's own (`detail`, which already names the age and what a re-run costs); this module
 * adds only the state word. */
import { esc } from '/static/next/app.js';
import { openDialog } from '/static/next/worklist.js';

/** True when the server declined the run because the last one is still fresh. */
export function isFreshnessSkip(started) {
  return !!started && started.status === 'skipped';
}

/** The word for the control that was pressed: it did not run, and says why. */
export const UP_TO_DATE_WORD = 'up to date';

/** Open the choice. `onForce` is called with no arguments and must start the forced run (and may throw: the
 *  message is shown in the dialog). Returns the dialog element. */
export function offerRunAnyway(started, { slug, analysisId, onForce }) {
  const el = openDialog('Already up to date', `${slug} · ${analysisId}`);
  const body = el.querySelector('#wl-detail-body');
  const sentence = started?.detail || 'This analysis ran recently and nothing has changed since.';
  body.innerHTML = `
    <p data-run-anyway-detail class="max-w-[70ch] text-answer text-ink">
      <span class="text-state-ok" aria-hidden="true">✓</span> ${esc(sentence)}</p>
    <div class="mt-s3 flex flex-wrap items-center gap-s2">
      <button type="button" data-run-anyway
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Run anyway</button>
      <button type="button" data-act="close"
        class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Leave it</button>
      <span data-run-anyway-state class="text-caveat text-ink-muted"></span>
    </div>`;
  const btn = body.querySelector('[data-run-anyway]');
  const state = body.querySelector('[data-run-anyway-state]');
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = 'Starting…';
    try {
      await onForce();
      el.remove();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Run anyway';
      state.textContent = `Not started: ${err.message}`;
    }
  });
  return el;
}
