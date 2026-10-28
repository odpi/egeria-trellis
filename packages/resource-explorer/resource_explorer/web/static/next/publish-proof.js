/* A run's publish state: a cue plus a short word, never a sentence (the sentence is on demand, in the title).
 *
 * The state comes from the server's `publish_proof`, derived from the run's own outbox rows
 * (registry.publish_proofs), never from which code branch ran or from a `published` flag:
 *   queued     ◔ "queued for publish"     an outbox row of the run exists and is not done (a write, or its read-back)
 *   read_back  ✓ "published · read back"  the run's read-back row is done: Egeria answered every annotation
 *   failed     ✕ "publish failed"         a row of the run is dead; Egeria's words follow, shortened, in full on hover
 * Vocabulary: the designer's reply of 2026-09-13 (REPLY-COST-LADDER-AND-PUBLISH-STATE.md §2). Nothing here ever
 * says "published" for any state but `read_back`. Literal class strings only (the class-coverage check reads them).
 */
/** Local escaping: this module imports nothing, so app.js and the stages can all import it without a cycle. */
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const PUBLISH_WORDS = {
  queued: 'queued for publish',
  read_back: 'published · read back',
  failed: 'publish failed',
};
const WORDS_SHOWN = 90;

/** True while the run's publish has not reached a terminal proof: a list showing it should keep re-reading. */
export function publishStillQueued(proof) {
  return !!proof && proof.state === 'queued';
}

/** The cue and word for one `publish_proof`; '' when there is none (a run that published nothing). */
export function publishProofHtml(proof) {
  if (!proof || !proof.state) return '';
  const words = String(proof.words || '');
  if (proof.state === 'read_back') {
    const at = String(proof.at || '');
    return `<span data-publish-proof="read_back" class="text-provenance" title="${esc(at ? `read back ${at}` : 'read back')}">`
      + '<span class="font-glyph text-state-ok" aria-hidden="true">✓</span> '
      + `<span class="text-ink-muted">${esc(PUBLISH_WORDS.read_back)}</span></span>`;
  }
  if (proof.state === 'failed') {
    const short = words.length > WORDS_SHOWN ? `${words.slice(0, WORDS_SHOWN)}…` : words;
    return `<span data-publish-proof="failed" class="text-provenance" title="${esc(words)}">`
      + '<span class="font-glyph text-state-warn" aria-hidden="true">✕</span> '
      + `<span class="text-state-warn">${esc(PUBLISH_WORDS.failed)}</span>`
      + (short ? ` <span data-publish-proof-words class="text-ink-muted">· ${esc(short)}</span>` : '')
      + '</span>';
  }
  if (proof.state === 'queued') {
    const waiting = Number(proof.waiting || 0);
    return `<span data-publish-proof="queued" class="text-provenance" title="${esc(
      waiting ? `${waiting} Egeria write(s) or read-back still to land · the background drain runs every 15 min` : 'the background drain runs every 15 min')}">`
      + '<span class="font-glyph text-ink-muted" aria-hidden="true">◔</span> '
      + `<span class="text-ink-muted">${esc(PUBLISH_WORDS.queued)}</span></span>`;
  }
  // A proof the server could not read: said as such, never as one of the three.
  return `<span data-publish-proof="unknown" class="text-provenance" title="${esc(words)}">`
    + '<span class="font-glyph text-ink-muted" aria-hidden="true">·</span> '
    + `<span class="text-ink-muted">${esc(String(proof.word || 'publish not established'))}</span></span>`;
}
