/* Dependencies, as ONE table with a kind column; every row has TWO ENDS (DESIGN-DEPENDENCY-ROW-TWO-ENDS.md).
 *
 * "You show a source but not a target, so they are not a wire" (owner, 2026-10-07). A row reads
 * "<dependent> <relation> <target>" and carries its kind, its evidence (file:line) and its state.
 * "More extensible": a new kind is a new value the server sends, not a new section here. The table is the
 * Analysis pane's dependencies section and, with `confirmable`, the same table under Curate's "how it
 * relates", where a person confirms the proposed runtime and data rows.
 *
 *   heading   "Dependencies · by kind" -- never "dependencies" alone
 *   counts    "12 build-time · 3 runtime · 1 data"
 *   filter    one chip per kind; "by target" groups the many dependents of one Kafka or Postgres together
 *   columns   kind · dependent · relation · target · evidence · state · drawn
 *   state     a cue plus a SHORT word, from the row: measured / proposed / confirmed / withdrawn, or
 *             "not established" with its reason (never guessed). Accent colours are for controls only.
 *   drawn     how the blueprint draws the row: wire / edge port / outside / environment wire /
 *             cross-blueprint / listed / not drawn. The diagram draws exactly these rows.
 *
 * A kind with no rows says why (the server's sentence). Nothing here says the word the owner ruled out
 * ("lineage" is not what this is). This file draws what the server derived
 * (resource_explorer/dependency_table.py); a state word comes from the row, never from a click.
 */
import { esc } from '/static/next/app.js';
import { stateEntry } from '/static/next/glyphs.js';
import { getDependencyTable, confirmRuntimeDependencies } from '/static/re-api.js';

const COLUMNS = [['kind', 'kind'], ['dependent', 'dependent'], ['relation', 'relation'], ['target_name', 'target'],
  ['evidence', 'evidence'], ['state', 'state'], ['drawn', 'drawn']];
const WIDE = new Set(['dependent', 'target_name', 'evidence']);

const cue = (key, word) => {
  const e = stateEntry(key);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(key)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};
const STATE_CUE = { measured: 'measured', confirmed: 'measured', proposed: 'proposal', withdrawn: 'unrun', 'not established': 'not_established' };

/** Under Curate a check mark beside a row can be read as "accepted" (the designer's rule for that pane:
 *  a check never stands alone). So a MEASURED row there carries its word without the glyph; a row a
 *  person CONFIRMED keeps the check, because that is what the mark means for it. */
const stateHtml = (r, confirmable) => (confirmable && r.state === 'measured'
  ? `<span class="text-ink-muted" data-cue="measured">${esc(r.state_words)}</span>`
  : cue(STATE_CUE[r.state] || 'unrun', r.state_words));

/** A row a person can confirm: a runtime or data row whose two ends are established. */
const confirmableRow = (r) => r.kind !== 'build-time' && r.state !== 'not established';

/** Rows after the chip filter and the sort. Pure. */
export function visibleRows(rows, ui) {
  const kinds = ui.kinds;                    // Set of kinds shown; empty/undefined = all
  let out = rows.filter((r) => !kinds || !kinds.size || kinds.has(r.kind));
  if (ui.sort) {
    const dir = ui.dir === 'desc' ? -1 : 1;
    out = [...out].sort((a, b) => dir * String(a[ui.sort] ?? '').localeCompare(String(b[ui.sort] ?? ''), undefined, { numeric: true }));
  }
  return out;
}

/** The rows grouped by their target (the question a person usually has: who depends on this Kafka?),
 *  the most-depended-on target first. Pure. */
export function groupByTarget(rows) {
  const groups = new Map();
  for (const r of rows) {
    const ref = r.target_ref || `${r.target_type}:${r.target_name}`;
    if (!groups.has(ref)) groups.set(ref, { ref, type: r.target_type, name: r.target_name, rows: [] });
    groups.get(ref).rows.push(r);
  }
  return [...groups.values()].sort((a, b) => b.rows.length - a.rows.length || a.name.localeCompare(b.name));
}

const typeWord = (t) => `<span class="text-provenance text-ink-muted">${esc(t)}</span>`;

const rowHtml = (r, confirmable, me) => {
  const target = `${esc(r.target_name)}${r.target_version ? ` <span class="text-ink-muted">${esc(r.target_version)}</span>` : ''}`;
  const sub = [r.resolution, r.target_guid ? 'in Egeria' : '', r.protocol].filter(Boolean).join(' · ');
  return `<div role="row" data-dep-row="${esc(r.key)}" data-dep-kind="${esc(r.kind)}" class="flex items-start gap-s2 py-[2px] text-caveat">
      <div role="cell" class="w-[8rem] shrink-0 text-ink-muted">${esc(r.kind)}</div>
      <div role="cell" data-dep-dependent class="min-w-0 flex-1 break-words font-mono text-ink">${esc(r.dependent)} ${typeWord(r.dependent_type)}</div>
      <div role="cell" data-dep-relation class="w-[8rem] shrink-0 break-words font-mono text-ink">${esc(r.relation)}</div>
      <div role="cell" data-dep-target class="min-w-0 flex-1 break-words font-mono text-ink"${r.target_guid ? ` title="${esc(r.target_guid)}"` : ''}>${target} ${typeWord(r.target_type)}${sub ? `<div class="text-provenance text-ink-muted">${esc(sub)}</div>` : ''}${r.cross_fact ? `<div data-dep-cross class="text-provenance text-ink-muted">${esc(r.cross_fact)}</div>` : ''}</div>
      <div role="cell" class="min-w-0 flex-1 break-words font-mono text-provenance text-ink-muted">${esc(r.evidence)}</div>
      <div role="cell" data-dep-state="${esc(r.state)}" class="w-[8rem] shrink-0 break-words">${stateHtml(r, confirmable)}</div>
      <div role="cell" data-dep-drawn="${esc(r.drawn || '')}" class="w-[8rem] shrink-0 break-words text-provenance text-ink-muted" title="${esc(r.drawn_words || '')}">${esc(r.drawn || '')}</div>
      ${confirmable ? `<div role="cell" class="w-[7rem] shrink-0">${confirmableRow(r) && me
        ? (r.state === 'confirmed'
          ? `<button type="button" data-dep-withdraw="${esc(r.key)}" title="affects future surveys only; what is already published stays" class="cursor-pointer bg-transparent p-0 text-ink-muted underline">withdraw</button>`
          : `<button type="button" data-dep-confirm="${esc(r.key)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">confirm</button>`) : ''}</div>` : ''}
    </div>`;
};

/** The whole section as HTML. `ui` is `{ kinds: Set, sort, dir, group }`. */
export function dependencyTableHtml(data, ui = {}, { confirmable = false, me = '' } = {}) {
  const counts = data.counts || {};
  const kinds = data.kinds || [];
  const rows = visibleRows(data.rows || [], ui);
  const header = `<div class="flex flex-wrap items-baseline gap-x-s3">
      <span class="text-answer text-ink" data-dep-heading>${esc(data.heading || 'Dependencies · by kind')}</span>
      <span class="text-provenance text-ink-muted" data-dep-counts>${kinds.map((k) => `<span class="tnum">${esc(counts[k] ?? 0)}</span> ${esc(k)}`).join(' · ')}</span>
      <span class="flex flex-wrap gap-[6px]" data-dep-chips>${kinds.map((k) => {
        const on = !ui.kinds || !ui.kinds.size || ui.kinds.has(k);
        return `<button type="button" data-dep-chip="${esc(k)}" aria-pressed="${on ? 'true' : 'false'}"
          class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-caveat ${on ? 'text-ink' : 'text-ink-muted'}">${on ? '● ' : '○ '}${esc(k)}</button>`;
      }).join('')}<button type="button" data-dep-group aria-pressed="${ui.group ? 'true' : 'false'}"
          class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-caveat ${ui.group ? 'text-ink' : 'text-ink-muted'}">${ui.group ? '● ' : '○ '}by target</button></span></div>`;
  const head = `<div role="row" class="flex items-start gap-s2 text-provenance text-ink-muted">${COLUMNS.map(([k, label]) => {
    const active = ui.sort === k;
    return `<div role="columnheader" class="${WIDE.has(k) ? 'min-w-0 flex-1' : 'w-[8rem] shrink-0'}">
      <button type="button" data-dep-sort="${k}" class="cursor-pointer bg-transparent p-0 ${active ? 'text-ink' : 'text-ink-muted'} underline">${esc(label)}${active ? (ui.dir === 'desc' ? ' ▼' : ' ▲') : ''}</button></div>`;
  }).join('')}${confirmable ? '<div role="columnheader" class="w-[7rem] shrink-0"></div>' : ''}</div>`;
  const body = ui.group
    ? groupByTarget(rows).map((g) => `<div data-dep-group-head="${esc(g.ref)}" class="mt-s2 text-caveat text-ink">${esc(g.name)} ${typeWord(g.type)} <span class="text-ink-muted"><span class="tnum">${g.rows.length}</span> ${g.rows.length === 1 ? 'dependent' : 'dependents'}</span></div>`
      + g.rows.map((r) => rowHtml(r, confirmable, me)).join('')).join('')
    : rows.map((r) => rowHtml(r, confirmable, me)).join('');
  // A kind with nothing says why: the server's sentence, or (for the kind it does not explain) nothing is implied.
  const why = (data.runtime_state
    ? `<div data-dep-runtime-state class="mt-s1 text-caveat text-ink-muted">runtime · ${esc(data.runtime_state)}</div>` : '')
    + (data.data_state && !counts.data
      ? `<div data-dep-data-state class="mt-s1 text-caveat text-ink-muted">data · ${esc(data.data_state)}</div>` : '');
  const notDerived = data.not_derived
    ? `<div data-dep-not-derived class="mt-s1 text-provenance text-ink-muted">${esc(data.not_derived)}</div>` : '';
  const deploysOnly = data.deploys_only
    ? `<div data-dep-deploys-only class="mt-s1 text-caveat text-ink">${esc(data.deploys_only)}</div>` : '';
  const none = !rows.length && !(data.rows || []).length
    ? '<div class="text-caveat text-ink-muted">no dependencies are recorded: survey the repository first</div>' : '';
  return `<div data-dependency-table class="min-w-0 max-w-full">${header}${notDerived}${deploysOnly}
    <div role="table" aria-label="${esc(data.heading || 'Dependencies · by kind')}" class="mt-s1 min-w-0 max-w-full overflow-x-auto">${head}${body}</div>${none}${why}
    <div data-dep-status class="mt-s1 text-provenance text-ink-muted"></div></div>`;
}

/* The last table read for each repository, so a pane that redraws often (the By-analysis cards, Curate)
 * reads it once, not once per redraw. A confirmation replaces it with the server's re-read. */
const READ = new Map();

/** Forget what was read for `slug` (or everything): the next mount reads again. */
export function forgetDependencyTable(slug) { if (slug) READ.delete(slug); else READ.clear(); }

/** Mount the table into `host` for `slug`. Never throws into the pane: a failure says so in its own slot. */
export async function mountDependencyTable(host, slug, { confirmable = false, me = '' } = {}) {
  if (!host) return null;
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading the dependencies…</div>';
  let data;
  try {
    if (!READ.has(slug)) READ.set(slug, getDependencyTable(slug));
    data = await READ.get(slug);
  } catch (err) {
    READ.delete(slug);
    if (host.isConnected) host.innerHTML = `<div class="text-caveat text-state-warn">The dependencies could not be read: ${esc(err.message)}</div>`;
    return null;
  }
  const ui = { kinds: new Set(), sort: '', dir: 'asc', group: false };
  const draw = () => {
    if (!host.isConnected) return;
    host.innerHTML = dependencyTableHtml(data, ui, { confirmable, me });
    host.querySelectorAll('[data-dep-chip]').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.depChip;
      const all = new Set(data.kinds || []);
      const cur = ui.kinds.size ? ui.kinds : all;
      const next = new Set(cur);
      if (next.has(k)) next.delete(k); else next.add(k);
      ui.kinds = next.size === 0 || next.size === all.size ? new Set() : next;     // none or all = everything
      draw();
    }));
    host.querySelector('[data-dep-group]')?.addEventListener('click', () => { ui.group = !ui.group; draw(); });
    host.querySelectorAll('[data-dep-sort]').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.depSort;
      ui.dir = ui.sort === k && ui.dir === 'asc' ? 'desc' : 'asc';
      ui.sort = k;
      draw();
    }));
    const act = (verdict) => async (ev) => {
      const b = ev.currentTarget;
      if (b.disabled) return;
      b.disabled = true;                                           // a pressed control ignores a second press
      const label = b.textContent;
      b.textContent = verdict === 'confirmed' ? 'confirming …' : 'withdrawing …';
      try {
        data = await confirmRuntimeDependencies(slug, [b.dataset.depConfirm || b.dataset.depWithdraw], verdict);
        READ.set(slug, Promise.resolve(data));
        draw();
      } catch (err) {
        b.disabled = false; b.textContent = label;
        const st = host.querySelector('[data-dep-status]');
        if (st) st.textContent = err.status === 401 ? 'sign in to confirm — it needs a person' : `not recorded · ${err.message}`;
      }
    };
    host.querySelectorAll('[data-dep-confirm]').forEach((b) => b.addEventListener('click', act('confirmed')));
    host.querySelectorAll('[data-dep-withdraw]').forEach((b) => b.addEventListener('click', act('withdrawn')));
  };
  draw();
  return { redraw: draw, data: () => data };
}
