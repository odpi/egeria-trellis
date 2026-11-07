/* The open data questions on a dataset's page (BRIEF-OPEN-DATA-QUESTIONS).
 *
 * Three catalogue questions, each led by an analysis that is declared and not built. A row is therefore ◌ "no reader
 * yet" (never "not run yet": that would claim a runnable analysis), or ◐ partial where the publisher's own record
 * already states a field that answers part of it (the licence and its flags; the modified date and stated cadence).
 * The glyphs are the shared vocabulary (glyphs.js); the stage strip counts per stage, in words, never a bare 0.
 */
import { getOpenDataQuestions } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { glyphSpan } from '/static/next/glyphs.js';

export const NO_READER_WORDS = 'No mechanism exists for this yet';

/** "2 of 3 answered in part · none in full": numbers only where there is one; the rest in words. */
export function questionsCountWords(res) {
  const total = res.total;
  if (res.answered) return `${res.answered} of ${total} answered`;
  if (res.partial) return `none of ${total} answered in full · ${res.partial} in part from the publisher's record`;
  return `none of ${total} answered yet · no reader yet`;
}

export function questionsHtml(res) {
  const strip = res.stages.map((s) => `<span data-od-stage="${esc(s.stage)}" class="mr-s3 text-provenance text-ink-muted">${esc(s.stage)} · ${
    s.partial ? `${s.partial} of ${s.total} in part` : 'no reader yet'}</span>`).join('');
  return `<div data-od-questions-panel class="mt-s3">
    <h5 class="m-0 mb-s2 text-caps uppercase tracking-caps text-ink-muted">Open data questions</h5>
    <p data-od-questions-count class="m-0 mb-s2 text-caveat text-ink">${esc(questionsCountWords(res))}</p>
    <div data-od-stage-strip class="mb-s2">${strip}</div>
    <ul class="m-0 list-none p-0">${res.questions.map((q) => `<li data-od-question="${esc(q.id)}" data-od-state="${esc(q.state)}" class="border-b border-rule py-s2">
      <div class="text-answer text-ink">${glyphSpan(q.state)} <span class="font-mono text-provenance text-ink-muted">${esc(q.id)}</span> ${esc(q.question)}
        <span class="text-provenance text-ink-muted">· ${esc(q.stage)}</span></div>
      ${q.answer ? `<div data-od-answer class="text-caveat text-ink">${esc(q.answer)}</div>` : ''}
      <div data-od-words class="text-provenance text-ink-muted">${esc(q.words)}</div>
    </li>`).join('')}</ul></div>`;
}

export async function mountQuestions(host, slug) {
  if (!host) return;
  try {
    host.innerHTML = questionsHtml(await getOpenDataQuestions(slug));
  } catch (e) {
    host.innerHTML = `<p class="text-state-warn">The questions could not be read: ${esc((e && (e.detail || e.message)) || String(e))}</p>`;
  }
}
