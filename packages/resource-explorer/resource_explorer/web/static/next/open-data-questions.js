/* The open data questions on a dataset's page (BRIEF-OPEN-DATA-QUESTIONS).
 *
 * Three catalogue questions, each led by an analysis. A row follows what is true of that analysis (TR-54 B5): ◌ "no reader yet" while it
 * is not built; ◐ partial where the publisher's own record already states a field that answers part of it (the licence and its flags;
 * the modified date and stated cadence), also while the check is built but not counted as the answer, and while a wired check has not
 * run; ○ "not run" for a wired check that has not run on a dataset whose record says nothing; and, once it has run, what the STORED
 * result says: ✓ answered, ∅ nothing found, ◐ read and not judged, ? not established. Never a bare zero.
 * The glyphs are the shared vocabulary (glyphs.js); the stage strip counts per stage, in words.
 */
import { getOpenDataQuestions } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { glyphSpan } from '/static/next/glyphs.js';

export const NO_READER_WORDS = 'No mechanism exists for this yet';

/** "1 of 3 answered · 1 in part": numbers only where there is one; the rest in words. */
export function questionsCountWords(res) {
  const total = res.total;
  const notRun = (res.questions || []).filter((q) => q.state === 'unrun').length;
  if (res.answered) return `${res.answered} of ${total} answered${res.partial ? ` · ${res.partial} in part` : ''}`;
  if (res.partial) return `none of ${total} answered in full · ${res.partial} in part from the publisher's record`;
  if (notRun) return `none of ${total} answered yet · not run yet`;
  return `none of ${total} answered yet · no reader yet`;
}

/** One stage's words: the answered count where there is one, then the partial one, then why nothing is answered. */
export function stageWords(res, s) {
  const rows = (res.questions || []).filter((q) => String(q.stage).split('/')[0].trim() === s.stage);
  if (s.answered) return `${s.answered} of ${s.total} answered${s.partial ? ` · ${s.partial} in part` : ''}`;
  if (s.partial) return `${s.partial} of ${s.total} in part`;
  return rows.length && rows.every((q) => q.state === 'no_reader') ? `${s.total} · no reader yet` : `${s.total} · not answered yet`;
}

export function questionsHtml(res) {
  const strip = res.stages.map((s) => `<span data-od-stage="${esc(s.stage)}" class="mr-s3 text-provenance text-ink-muted">${esc(s.stage)} · ${esc(stageWords(res, s))}</span>`).join('');
  // The ones that cannot be asked of a dataset are not rows and not in M; they are said once, folded, with why.
  const na = (res.not_applicable || []).length
    ? `<details data-od-not-applicable class="mt-s2"><summary class="cursor-pointer text-provenance text-ink-muted">${res.not_applicable.length} generic questions are not asked of a dataset</summary>
        <ul class="m-0 mt-s2 list-none p-0">${res.not_applicable.map((n) => `<li data-od-na="${esc(n.id)}" class="py-[2px] text-provenance text-ink-muted"><span class="font-mono">${esc(n.id)}</span> ${esc(n.question)} — ${esc(n.reason)}</li>`).join('')}</ul></details>`
    : '';
  return `<div data-od-questions-panel class="mt-s3">
    <h5 class="m-0 mb-s2 text-caps uppercase tracking-caps text-ink-muted">Open data questions</h5>
    <p data-od-questions-count class="m-0 mb-s2 text-caveat text-ink">${esc(questionsCountWords(res))}</p>
    <div data-od-stage-strip class="mb-s2">${strip}</div>
    <ul class="m-0 list-none p-0">${res.questions.map((q) => `<li data-od-question="${esc(q.id)}" data-od-kind="${esc(q.kind || 'open_data')}" data-od-state="${esc(q.state)}" class="border-b border-rule py-s2">
      <div class="text-answer text-ink">${glyphSpan(q.state)} <span class="font-mono text-provenance text-ink-muted">${esc(q.id)}</span> ${esc(q.question)}
        <span class="text-provenance text-ink-muted">· ${esc(q.stage)}${q.kind === 'generic' ? ' · asked of every kind' : ''}</span></div>
      ${q.answer ? `<div data-od-answer class="text-caveat text-ink">${esc(q.answer)}</div>` : ''}
      <div data-od-words class="text-provenance text-ink-muted">${esc(q.words)}</div>
    </li>`).join('')}</ul>${na}</div>`;
}

export async function mountQuestions(host, slug) {
  if (!host) return;
  try {
    host.innerHTML = questionsHtml(await getOpenDataQuestions(slug));
  } catch (e) {
    host.innerHTML = `<p class="text-state-warn">The questions could not be read: ${esc((e && (e.detail || e.message)) || String(e))}</p>`;
  }
}
