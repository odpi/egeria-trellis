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

export function renderPreview(host, p) {
  clear(host);
  if (!p) return;
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
