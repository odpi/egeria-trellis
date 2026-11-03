/* Find a file system by what its narrative files say (the Resource Locator's passage search).
 *
 * Reached from the sidebar's "🔎 Find by passages", offered while the FS chip is on. One question in; the file systems
 * whose pressed narrative files answer it come back ranked by their best passage (`GET /api/filesystems/by-passages`).
 * Each row shows that passage with its citation ("stated by <path> § <section>") and the word "proposal": a narrative
 * passage is what a file says, never a measured fact.
 *
 * Absence is said, never implied: the file systems not read yet (never pressed) are counted and named, a file system
 * whose pressed state could not be read is its own count, and one whose index could not be searched is a failure. Each
 * has its own words from the server (`words`, `not_read_words`, `unknown_words`); this file adds only the lists.
 */
import { esc, selectResourceFromChat } from '/static/next/app.js';
import { openDialog, closeCellDetail } from '/static/next/worklist.js';
import { findFilesystemsByPassages } from '/static/re-api.js';

export const PASSAGE_FINDER_WORDS = {
  title: 'Find a file system by its passages',
  sub: 'Searches the narrative files already read. A passage is a proposal, never a fact.',
  placeholder: 'what do the README files say about…',
  search: 'Search',
  searching: 'Searching…',
  failed: 'could not search · ',
};

const slugList = (slugs) => (slugs && slugs.length
  ? ` <span class="font-mono text-ink-muted">${slugs.map(esc).join(', ')}</span>` : '');

/** The answer as HTML: the ranked file systems, then each absence in its own words. Pure of the DOM. */
export function passageResultsHtml(out) {
  if (!out || typeof out !== 'object') return '';
  const rows = (out.matches || []).map((m) => `<li data-passage-match="${esc(m.slug)}" class="mb-s3">
      <div><button data-act="open-fs" data-slug="${esc(m.slug)}" class="cursor-pointer bg-transparent text-accent-ink underline">${esc(m.display_name || m.slug)}</button>
        <span class="tnum text-ink-muted">· best passage ${esc(Number(m.score).toFixed(2))}</span></div>
      <div class="mt-[2px] text-ink">${esc(m.text)}</div>
      <div class="mt-[2px] text-provenance text-ink-muted"><span data-passage-citation>${esc(m.citation)}</span> ·
        <span data-passage-label title="${esc(m.label || '')}">proposal</span></div>
    </li>`).join('');
  const notes = [
    out.not_read_words ? `<div data-passage-not-read>${esc(out.not_read_words)}${slugList(out.not_read)}</div>` : '',
    out.unknown_words ? `<div data-passage-unknown>${esc(out.unknown_words)}${slugList(out.unknown)}</div>` : '',
    (out.unavailable || []).length
      ? `<div data-passage-unavailable>${out.unavailable.length} could not be searched (index unreachable)${slugList(out.unavailable)}</div>` : '',
  ].join('');
  return `<div data-passage-state="${esc(out.state || '')}">
    <div data-passage-words class="mb-s2 text-ink">${esc(out.words || '')}</div>
    ${rows ? `<ol class="mb-s2 list-none p-0">${rows}</ol>` : ''}
    <div class="text-chip text-ink-muted">${notes}</div></div>`;
}

export function openFindByPassagesDialog() {
  const W = PASSAGE_FINDER_WORDS;
  openDialog(W.title, W.sub, { wide: true });
  const host = document.getElementById('wl-detail-body');
  if (!host) return;
  host.innerHTML = `<div class="flex gap-s2">
      <input data-passage-q type="text" placeholder="${esc(W.placeholder)}" autocomplete="off"
        class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <button data-act="passage-search" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">${esc(W.search)}</button>
    </div><div data-passage-out class="mt-s3"></div>`;
  const q = host.querySelector('[data-passage-q]');
  const out = host.querySelector('[data-passage-out]');
  const btn = host.querySelector('[data-act="passage-search"]');
  let ticket = 0;
  const search = async () => {
    const text = q.value.trim();
    if (!text) return;
    const mine = ++ticket;
    btn.textContent = W.searching;
    out.innerHTML = '';
    let answer;
    try {
      answer = await findFilesystemsByPassages(text);
    } catch (err) {
      if (mine !== ticket) return;
      btn.textContent = W.search;
      out.innerHTML = `<div data-passage-error class="text-state-warn">${esc(W.failed + (err && err.message ? err.message : err))}</div>`;
      return;
    }
    if (mine !== ticket) return;                    // a newer question is the one on screen
    btn.textContent = W.search;
    out.innerHTML = passageResultsHtml(answer);
    out.querySelectorAll('[data-act="open-fs"]').forEach((b) => b.addEventListener('click', () => {
      closeCellDetail();
      selectResourceFromChat('filesystem', b.dataset.slug);
    }));
  };
  btn.addEventListener('click', search);
  q.addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  q.focus();
}
