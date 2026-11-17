/* Drawing validate and preview answers. Absence is an answer: every note pyegeria gives is shown, a capped or
 * unconfirmed preview says so, and "found nothing" appears only when pyegeria said the end was confirmed. */
import { h, clear, add } from '/static/dom.js';

function list(title, items, cls) {
  if (!items || !items.length) return null;
  return h('div', { class: 'st-block' }, h('strong', {}, title),
           h('ul', { class: cls || '' }, items.map((t) => h('li', {}, typeof t === 'string' ? t : JSON.stringify(t)))));
}

export function renderValidation(host, v) {
  clear(host);
  if (!v) return;
  add(host,
    h('p', { class: v.ok ? 'st-ok' : 'st-error' }, v.ok ? 'Valid: no errors in what was checked.' : 'Not valid.'),
    list('Errors', v.errors, 'st-error'),
    list('Warnings', v.warnings, 'st-warn'),
    list('Written but not applied yet', v.not_applied, 'st-warn'),
    list('Checked', v.checked),
    list('Not checked', v.not_checked, 'st-muted'),
  );
}

function tableOrData(result) {
  if (result && result.kind === 'table' && window.EgeriaTable) {
    const wrap = h('div', { class: 'st-table-wrap' });
    wrap.append(window.EgeriaTable.render(result, {}));
    return wrap;
  }
  if (result && result.kind === 'table') {
    return h('p', { class: 'st-error' }, 'The table renderer (egeria-table.js) did not load, so the rows are shown as data.');
  }
  return h('pre', { class: 'st-pre' }, JSON.stringify(result, null, 2));
}

function renderSheet(host, p) {
  const sheet = p.result || {};
  add(host, h('p', { class: 'st-muted' }, `Resolved as ${p.viewer || sheet.viewer || 'you'}.`),
      list('Notes', p.notes, 'st-notes'), list('The sheet reader says', sheet.breaks, 'st-warn'));
  const grid = h('div', { class: 'st-grid' });
  for (const pl of sheet.placements || []) {
    const span = pl.span === 'full' ? '1 / -1' : pl.span === '2' ? 'span 2' : 'span 1';
    const cell = h('div', { class: `st-cell st-${pl.emphasis || 'kpi'}`, style: { gridColumn: span } },
      h('strong', {}, pl.title || pl.ref));
    const status = pl.kind === 'text' ? null
      : pl.kind === 'missing' ? ['st-error', `No Report: ${pl.reason || pl.note || 'not resolved'}`]
      : pl.kind === 'restricted' ? ['st-warn', `Restricted: ${pl.reason || ''}`]
      : pl.runnable === false ? ['st-error', `Not runnable: ${pl.refusal || ''}`]
      : pl.runnable === null || pl.runnable === undefined ? ['st-muted', 'Its spec was not resolved: runnable unknown.']
      : pl.error ? ['st-error', pl.error]
      : ['st-ok', `Runs ${pl.spec || '?'}${pl.spec_source ? ` (${pl.spec_source.tier || ''})` : ''}.`];
    if (status) cell.append(h('p', { class: status[0] }, status[1]));
    if (pl.note) cell.append(h('p', { class: 'st-warn' }, pl.note));
    if (pl.kind === 'text') cell.append(h('pre', { class: 'st-pre' }, pl.content || ''));
    if (pl.result) cell.append(tableOrData(pl.result));
    grid.append(cell);
  }
  host.append(grid);
}

export function renderPreview(host, p) {
  clear(host);
  if (!p) return;
  if (p.kind === 'sheet') return renderSheet(host, p);
  const r = p.result;
  const head = [];
  if (p.viewer) head.push(`Run as ${p.viewer}.`);
  if (p.shown !== null && p.shown !== undefined) head.push(`${p.shown} row${p.shown === 1 ? '' : 's'} shown.`);
  if (p.capped) head.push('Capped: there may be more.');
  host.append(h('p', { class: 'st-muted' }, head.join(' ')));
  add(host, list('Notes', p.notes, 'st-notes'));
  if (r && r.kind === 'table' && window.EgeriaTable) host.append(h('div', {}, tableOrData(r)));
  else if (r && r.kind !== 'empty') host.append(tableOrData(r));
  else if (r && r.kind === 'empty' && r.notes && r.notes.length) add(host, list('pyegeria says', r.notes));
}

export function renderFailure(host, title, detail) {
  clear(host);
  host.append(h('p', { class: 'st-error' }, title), h('p', {}, detail || ''));
}
