/* Row anatomy for a person's input: who + when + the evidence-moved flag.
 *
 * ENRICHMENT-E0-ROW-ANATOMY (docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-
 * STAGE-IA.md §0.3, §6 item 1): a person's input used to live in two stores
 * with two different rules. Enrichment's judgements/observations
 * (`state.enrichment` via `saveEnrichmentField`) always carried who, when,
 * and the "⚠ review — evidence moved: X" flag. The Questions tab's answers
 * to the catalog's human questions (`state.contextAnswers` via
 * `saveQuestionAnswer`) carried only when — no author at all — and broke
 * the reply's own stated rule that "a human-supplied answer carries who and
 * when."
 *
 * The reply's ruling was NOT to merge the two stores (that's a later,
 * separate engineering call) but to give both the SAME ROW ANATOMY, through
 * one shared component, so a reader sees one vocabulary for "a person said
 * this" no matter which store it came from.
 *
 * `personRowLineHtml` is that component's provenance line: it used to be
 * inlined inside `stages/enrichment.js`'s `fieldRowHtml` (the who/when/moved
 * block) — that file now calls this function instead of building the string
 * itself, and `app.js`'s Questions-tab human-answer row calls the SAME
 * function, not a lookalike of its own.
 *
 * THIS MODULE imports `esc` from app.js and app.js imports this module back
 * (for the human-answer row) — a circular ES import, safe here the same way
 * `stages/enrichment.js` already imports `esc`/`$`/... from app.js while
 * app.js imports `renderEnrichment` from it: nothing below runs at
 * module-evaluation time, only inside functions called later, once both
 * modules have finished loading.
 */
import { ago } from '/static/next/format.js';
import { esc } from '/static/next/app.js';

/**
 * who + when + the evidence-moved flag, as one line of muted provenance
 * text (the caller wraps it in whatever container class it likes — this
 * returns inner HTML, not a full element).
 *
 * @param {string} [author] - who supplied this. No author -> no who/when
 *   segment at all (a row with nothing recorded yet), same as both stores'
 *   pre-E0 behaviour for an unset field.
 * @param {string} [whenIso] - server-stamped ISO timestamp: rendered via
 *   `ago()`, same as every other provenance line in `/next`.
 * @param {string[]} [moved] - analysis ids whose evidence has moved since
 *   this was recorded. Empty for a store that doesn't yet track evidence at
 *   all (question answers, today) — that is the SAME anatomy with that one
 *   fact absent, not a different rendering: the flag segment simply doesn't
 *   appear, exactly as it doesn't for an unmoved judgement either.
 * @param {string} [verb] - a plain-text prefix before the author
 *   ("answered by", "confirmed by", …) — literal text, not escaped, so
 *   callers pass a fixed string, never user input. Empty by default, which
 *   reproduces judgements/observations' existing plain "dan · 2d ago" style
 *   unchanged.
 * @param {string} [sourceLine] - already-built, already-escaped HTML for an
 *   observation's "from <source>" prefix or similar; kept generic so this
 *   stays one function for both stores rather than one that only knows
 *   about enrichment fields.
 * @param {string} [suffix] - literal text appended right after the author
 *   ("· interim", for the owner field's interim marker) — not escaped, same
 *   reasoning as `verb`.
 */
export function personRowLineHtml({ author = '', whenIso = '', moved = [], verb = '', sourceLine = '', suffix = '' } = {}) {
  const when = whenIso ? `<span class="tnum">${esc(ago(whenIso))}</span>` : '';
  const who = author
    ? [sourceLine, `${verb ? `${verb} ` : ''}${esc(author)}${suffix}`, when].filter(Boolean).join(' · ')
    : '';
  const movedHtml = (moved && moved.length)
    ? `<span class="text-state-warn">⚠ review — evidence moved: ${esc(moved.join(', '))}</span> · `
    : '';
  return `${movedHtml}${who}`;
}
