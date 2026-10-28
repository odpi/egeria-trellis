/* Explicit selection for a repository's folders and files (brief 2a).
 *
 * THE RULE: selecting a folder never implies publishing what is nested in it. Publishing is of named items,
 * previewed, plus the ancestors needed to hold them. Worthiness is a proposal, never a decision.
 *
 * One component for both places that list candidates (Curate's "what's in it" and the Analysis pane's
 * sub-resource panel), so the two show the same state from the same record. The record is the server's
 * `resource_scope_events` (GET/POST /api/projects/{slug}/scope-events); this file keeps NO selection of its
 * own. Every row's segment is filled from the record's answer, never from the click, and every count in the
 * commit table comes from `view.manifest`.
 *
 * A row is one cell group:
 *   Publish to Egeria?   the two-part selector (the database scope tree's own component, reused) and, under
 *                        it, one cue: saving… / ✕ not saved · cause / saved · you · just now / the state
 *   In Egeria            published · when (read back), the Egeria lane, separate from the choice
 *
 * State is a visible cue plus a SHORT word; the sentence is one gesture away (a <details>). Accent colours
 * are for controls, never states. A press dims the selector and says "saving…" in the same cell; a second
 * press while one is out does nothing (no second POST); a failed write rolls the cell back and says why.
 */
import { ago } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import { getScopeEvents, postScopeEvents } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { selectorHtml } from '/static/next/stages/curate-scope.js';

export const SCOPE_HEAD = 'Publish to Egeria?';
export const CONTAINER_WORDS = 'needed as a container · not an asset of its own';
/** What a tick does, said once above the rows and on each Include button. */
export const TICK_WORDS = 'Include publishes this row only. A folder\u2019s contents and a file\u2019s README are separate rows, published only if you include them; folders needed to hold an included file are published as containers.';
export const SIGN_IN_WORDS = 'sign in to choose — the record needs an author';
const SEGS = [['include', 'Include'], ['leave_out', 'Leave out']];

const cue = (key, word, title = '') => {
  const e = stateEntry(key);
  return `<span class="${e.tone === 'text-state-ok' ? 'text-state-ok' : e.tone === 'text-state-warn' ? 'text-state-warn' : 'text-ink-muted'}" data-cue="${esc(key)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};
const more = (sentence) => (sentence
  ? ` <details data-scope-details class="inline text-provenance text-ink-muted"><summary class="inline cursor-pointer">details</summary> ${esc(sentence)}</details>` : '');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/* ── pure helpers (tested directly) ─────────────────────────────────── */

/** The candidate rows nested somewhere inside a folder row. */
export function insideOf(rows, folder) {
  const prefix = folder.locator === '' ? null : `${folder.locator}/`;
  return rows.filter((r) => r.candidate && r.locator !== folder.locator
    && (prefix === null || r.locator.startsWith(prefix)));
}

/** What a folder's cell says about its contents: how many candidates inside are not included, and which
 *  worthy, still-undecided ones "include its M worthy children" would write. A child a person LEFT OUT is
 *  not touched by it: that choice is theirs. */
export function folderFacts(rows, folder) {
  const inside = insideOf(rows, folder);
  const notSelected = inside.filter((r) => r.choice !== 'include').length;
  const worthy = inside.filter((r) => r.proposed).map((r) => r.locator);
  return { inside: inside.length, notSelected, worthy };
}

/** The events "accept the N proposals" writes: one include per proposed row, marked as a proposal. */
export function acceptEvents(view) {
  const kind = Object.fromEntries(view.rows.map((r) => [r.locator, r.kind]));
  return (view.proposals || []).map((loc) => ({
    locator: loc, kind: kind[loc], choice: 'include', action: 'set', source: 'proposal', proposal_rule: 'worthy' }));
}

/** The rows a filter shows: worthy ones, anything with a choice and every container by default; the rest
 *  when `showAll`. `text` filters by path. */
export function visibleRows(view, { text = '', showAll = false } = {}) {
  const t = (text || '').toLowerCase();
  return view.rows.filter((r) => (showAll || r.proposed || r.choice || r.role === 'container' || r.label === 'worthy' || r.published)
    && (!t || r.locator.toLowerCase().includes(t)));
}

/* ── the controller: one per pane ───────────────────────────────────── */

/** Owns the view read from the record and the transient cues (pending, saved, failed). `onChange` redraws the
 *  pane; `visible()` returns the rows currently shown (bulk acts touch those only). */
export function createScopeController({ slug, me = () => '', onChange = () => {}, visible = null, now = () => Date.now() }) {
  const ctl = {
    slug, view: null, pending: new Set(), notes: new Map(), fails: new Map(), onChange, visible, me, posts: 0,
    async load() {
      const v = await getScopeEvents(slug);
      if (!v || !Array.isArray(v.rows) || !v.manifest) throw new Error('the selection record did not answer');
      ctl.view = v;
      return v;
    },
    rows() { return (ctl.view && ctl.view.rows) || []; },
    /** Write `events` for `keys`. A second press on any key already out does nothing. */
    async write(events, keys = events.map((e) => e.locator)) {
      if (!events.length || keys.some((k) => ctl.pending.has(k))) return false;
      keys.forEach((k) => { ctl.pending.add(k); ctl.fails.delete(k); ctl.notes.delete(k); });
      ctl.onChange();
      ctl.posts += 1;
      try {
        ctl.view = await postScopeEvents(slug, events);        // the server's re-read of the record, after the write
        const who = ctl.me();
        keys.forEach((k) => ctl.notes.set(k, { by: who, at: now() }));
        setTimeout(() => { keys.forEach((k) => ctl.notes.delete(k)); ctl.onChange(); }, 6000).unref?.();
      } catch (err) {
        keys.forEach((k) => ctl.fails.set(k, err.message || String(err)));
      } finally {
        keys.forEach((k) => ctl.pending.delete(k));
        ctl.onChange();
      }
      return true;
    },
    /** Delegated click handler for every control this file draws. */
    handle(btn) {
      const act = btn.dataset.scopeAct;
      const loc = btn.dataset.scopeLoc;
      const rows = ctl.rows();
      const row = rows.find((r) => r.locator === loc);
      if (act === 'set') return ctl.write([{ locator: loc, kind: btn.dataset.scopeKind, choice: btn.dataset.scopeChoice, action: 'set' }]);
      if (act === 'clear') return ctl.write([{ locator: loc, kind: btn.dataset.scopeKind, choice: '', action: 'clear' }]);
      if (act === 'accept') {
        const ev = acceptEvents(ctl.view);
        return ctl.write(ev);
      }
      if (act === 'children' && row) {
        const facts = folderFacts(rows, row);
        const kind = Object.fromEntries(rows.map((r) => [r.locator, r.kind]));
        return ctl.write(facts.worthy.map((l) => ({ locator: l, kind: kind[l], choice: 'include', action: 'set',
          reason: `worthy child of ${loc || '(root)'}` })));
      }
      const shown = ctl.visible ? ctl.visible() : rows;
      if (act === 'include-visible') {
        return ctl.write(shown.filter((r) => r.choice !== 'include')
          .map((r) => ({ locator: r.locator, kind: r.kind, choice: 'include', action: 'set' })));
      }
      if (act === 'clear-visible') {
        return ctl.write(shown.filter((r) => r.choice)
          .map((r) => ({ locator: r.locator, kind: r.kind, choice: '', action: 'clear' })));
      }
      return false;
    },
    /** Bind once on a stable root; later calls only replace which controller answers. */
    bind(root) {
      root.__scopeCtl = ctl;
      if (root.__scopeBound) return;
      root.__scopeBound = true;
      root.addEventListener('click', (ev) => {
        const btn = ev.target.closest && ev.target.closest('[data-scope-act]');
        if (!btn || !root.contains(btn) || btn.disabled) return;
        (root.__scopeCtl || ctl).handle(btn);
      });
    },
  };
  return ctl;
}

/* ── drawing ────────────────────────────────────────────────────────── */

/** The choice cell of one row: selector, then ONE cue under it. */
export function choiceCellHtml(r, ctl) {
  const me = ctl.me();
  const busy = ctl.pending.has(r.locator);
  const off = !me || busy;
  const dis = me ? (busy ? 'disabled aria-busy="true"' : '') : `disabled title="${esc(SIGN_IN_WORDS)}"`;
  const loc = esc(r.locator);
  const kind = esc(r.kind);
  const sel = selectorHtml({
    own: r.choice, explicit: !!r.choice, off, dis, segs: SEGS, ariaLabel: SCOPE_HEAD,
    segAttrs: (choice) => `data-scope-act="set" data-scope-choice="${choice}" data-scope-loc="${loc}" data-scope-kind="${kind}"${choice === 'include' && me ? ` title="${esc(TICK_WORDS)}"` : ''}`,
    clearAttrs: `data-scope-act="clear" data-scope-loc="${loc}" data-scope-kind="${kind}"`,
  });
  const who = (by) => (by && by === me ? 'you' : by);
  const lines = [];
  if (busy) {
    lines.push(`<div data-scope-state-word="saving">${cue('running', 'saving…')}</div>`);
  } else if (ctl.fails.has(r.locator)) {
    const why = ctl.fails.get(r.locator);
    lines.push(`<div data-scope-state-word="failed"><span data-cue="error" class="text-state-warn" title="${esc(why)}">✕ not saved · ${esc(why)}</span></div>`);
  } else if (ctl.notes.has(r.locator)) {
    const n = ctl.notes.get(r.locator);
    lines.push(`<div data-scope-state-word="saved"><span data-scope-saved-note class="text-provenance text-ink-muted" title="saved in Resource Explorer · ${esc(n.by)} · not yet published to Egeria">saved · ${esc(who(n.by))} · just now</span></div>`);
  } else if (r.choice) {
    const tag = r.source === 'proposal'
      ? ` <span data-scope-tag="proposal" class="border border-rule-strong px-[4px] text-provenance text-ink">accepted proposal</span>` : '';
    lines.push(`<div data-scope-state-word="${esc(r.choice)}" class="text-provenance text-ink-muted">${esc(who(r.by))} · ${esc(ago(r.at))}${tag}${
      more(`${r.choice === 'include' ? 'included' : 'left out'} by ${r.by} on ${r.at}${r.source === 'proposal' ? ` · accepted from a proposal (${r.proposal_rule || 'worthy'})` : ''} · saved in Resource Explorer, not a publish`)}</div>`);
  } else if (r.proposed) {
    lines.push(`<div data-scope-state-word="proposed" class="text-provenance text-ink-muted">proposed · worthy · ${esc(r.reason || 'no reason given')}</div>`);
  } else if (r.role !== 'container') {
    lines.push(`<div data-scope-state-word="undecided" class="text-provenance text-ink-muted">${r.cleared ? 'cleared · undecided' : 'not selected'}</div>`);
  }
  // A published row left out: it stays in Egeria; the choice is about FUTURE publishes.
  if (r.choice === 'leave_out' && r.published && !busy) {
    lines.push(`<div data-scope-published-kept class="text-provenance text-ink-muted">left out for future publishes · published earlier · kept in Egeria</div>`);
  }
  if (r.kind === 'folder' && !busy) {
    if (r.choice === 'leave_out' && !r.published) {
      lines.push(`<div data-scope-folder-left class="text-provenance text-ink-muted">left out · children keep their own choice</div>`);
    }
    if (r.choice === 'include') {
      const f = folderFacts(ctl.rows(), r);
      if (f.inside) {
        lines.push(`<div data-scope-folder-only class="text-provenance text-ink-muted">folder only · ${f.notSelected ? `${f.notSelected} inside not selected` : 'everything inside is chosen'}${
          f.worthy.length ? ` · <button type="button" data-scope-act="children" data-scope-loc="${loc}" ${dis} class="${off ? 'opacity-60' : 'cursor-pointer'} bg-transparent p-0 text-accent-ink underline">include its ${f.worthy.length} worthy ${f.worthy.length === 1 ? 'child' : 'children'}</button>` : ''}</div>`);
      } else {
        lines.push(`<div data-scope-folder-only class="text-provenance text-ink-muted">folder only</div>`);
      }
    }
  }
  if (r.role === 'container') {
    lines.push(`<div data-scope-container class="text-provenance text-ink"><span class="font-glyph" aria-hidden="true">○</span> ${esc(CONTAINER_WORDS)}</div>`);
  }
  return `<div data-scope-choice-cell data-scope-locator="${loc}">${sel}${lines.join('')}</div>`;
}

/** The Egeria lane for one row: what was read back, in the Egeria glyph; blank when nothing is there. */
export function egeriaCellHtml(r) {
  if (r.published) {
    return `<div data-scope-egeria>${cue('measured', `published · ${ago(r.published.when) || 'earlier'}`,
      `published to Egeria (GUID ${r.published.guid}); kept there whatever the choice becomes`)}</div>`;
  }
  if (r.choice !== 'include' && r.role === 'container') {
    // A folder chosen for nothing of its own: created only to hold the included file, never as an asset.
    return `<div data-scope-egeria>${cue('unrun', 'holder only · created with the file',
      'created in Egeria only as the folder that holds an included file; not an asset of its own')}</div>`;
  }
  if (r.choice === 'include') {
    return `<div data-scope-egeria>${cue('unrun', 'not published yet')}</div>`;
  }
  return '<div data-scope-egeria></div>';
}

/** The bar above the rows: accept the proposals, include / clear all visible. Acts on what is shown only. */
export function toolbarHtml(ctl, shown) {
  const me = ctl.me();
  const dis = me ? '' : `disabled title="${esc(SIGN_IN_WORDS)}"`;
  const proposals = (ctl.view.proposals || []).length;
  const include = shown.filter((r) => r.choice !== 'include').length;
  const clear = shown.filter((r) => r.choice).length;
  const busy = shown.some((r) => ctl.pending.has(r.locator));
  return `<div data-scope-tick-words class="mb-s1 max-w-[80ch] text-provenance text-ink-muted">${esc(TICK_WORDS)}</div>
  <div data-scope-toolbar class="flex flex-wrap items-baseline gap-s3 text-provenance">
    ${proposals ? `<button type="button" data-scope-act="accept" ${dis} class="${me ? 'cursor-pointer text-accent-ink' : 'opacity-60 text-ink-muted'} bg-transparent p-0 underline">accept the ${proposals} proposal${proposals === 1 ? '' : 's'}</button>` : ''}
    <button type="button" data-scope-act="include-visible" ${dis} ${include ? '' : 'disabled'} class="${me ? 'cursor-pointer text-accent-ink' : 'opacity-60 text-ink-muted'} bg-transparent p-0 underline disabled:cursor-default disabled:opacity-60">include all visible (${include})</button>
    <button type="button" data-scope-act="clear-visible" ${dis} ${clear ? '' : 'disabled'} class="${me ? 'cursor-pointer text-accent-ink' : 'opacity-60 text-ink-muted'} bg-transparent p-0 underline disabled:cursor-default disabled:opacity-60">clear all visible (${clear})</button>
    ${busy ? `<span data-scope-bulk-saving>${cue('running', 'saving…')}</span>` : ''}
  </div>`;
}

/** The listing Curate draws (flex rows). The Analysis pane draws its own table around the same cells. */
export function scopeListHtml(ctl, shown) {
  const head = `<div class="flex items-baseline gap-s2 text-provenance text-ink-muted">
    <span class="min-w-0 flex-1">path</span><span class="w-[3rem] shrink-0">kind</span>
    <span class="w-[22rem] shrink-0" data-scope-choice-head>${esc(SCOPE_HEAD)}</span><span class="w-[10rem] shrink-0">in Egeria</span></div>`;
  const body = shown.map((r) => `<div data-scope-row="${esc(r.locator)}" class="flex items-start gap-s2 border-b border-rule py-[3px] text-caveat${r.choice === 'leave_out' ? ' text-ink-muted opacity-70' : ''}">
      <span class="min-w-0 flex-1 break-all font-mono text-ink">${esc(r.locator) || '(root)'}</span>
      <span class="w-[3rem] shrink-0 text-ink-muted">${esc(r.kind)}</span>
      <div class="w-[22rem] shrink-0">${choiceCellHtml(r, ctl)}</div>
      <div class="w-[10rem] shrink-0">${egeriaCellHtml(r)}</div></div>`).join('');
  return `<div data-scope-list>${toolbarHtml(ctl, shown)}<div class="mt-s1">${head}</div>
    <div class="mt-s1" data-scope-rows style="max-height:20rem;overflow:auto">${body || '<div class="py-s2 text-caveat text-ink-muted">No candidates match.</div>'}</div></div>`;
}

/** A one-line account of the manifest, for the panes that show no table (counts from the record). */
export function manifestLine(m) {
  return `${plural(m.files, 'file', 'files')} · ${plural(m.folders, 'folder', 'folders')} you chose · ${plural(m.containers, 'container', 'containers')}`;
}
