/* S2: the dashboard sheet form. Placements are a Report (kept by its qualified name) or text, laid out on a two-
 * column grid by span (1, 2, full) and emphasis (kpi, panel). The grid below the list is the layout as it will be
 * drawn; order is the list's order. A sheet held in Egeria can't have placements removed or reordered by
 * publishing (Dr.Egeria has no command for it): pyegeria's plan says so, and the form warns when you do it. */
import { api } from '/static/studio_auth.js';
import { h, clear, readJson } from '/static/dom.js';

const csv = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);

export function sheetForm(d, { touch, renderForm }) {
  d.placements = d.placements || [];
  const held = Boolean(d.guid);
  const warn = h('p', { class: 'st-warn', hidden: true },
    'This sheet is held in Egeria. Publishing adds and updates placements but does not remove or reorder the ones ' +
    'already there (Dr.Egeria has no command for it); the publish plan will say which changes are not applied.');
  const structural = () => { if (held) warn.hidden = false; touch(); };

  const field = (label, value, set, multiline) => {
    const el = multiline ? h('textarea', { rows: 3 }) : h('input', {});
    el.value = value ?? '';
    el.addEventListener('input', () => { set(el.value); touch(); drawGrid(); });
    return h('label', { class: 'st-field' }, label, el);
  };
  const choice = (obj, key, opts) => {
    const s = h('select', {}, opts.map((o) => h('option', { value: o }, o)));
    s.value = obj[key] || opts[0];
    s.addEventListener('change', () => { obj[key] = s.value; touch(); drawGrid(); });
    return s;
  };

  const list = h('ol', { class: 'st-placements' });
  const grid = h('div', { class: 'st-grid', 'aria-label': 'Layout' });

  function drawGrid() {
    clear(grid);
    d.placements.forEach((p, i) => {
      const span = p.span === 'full' ? '1 / -1' : p.span === '2' ? 'span 2' : 'span 1';
      grid.append(h('div', { class: `st-cell st-${p.emphasis || 'kpi'}`, style: { gridColumn: span } },
        h('small', { class: 'st-muted' }, `${i + 1} · ${p.report !== null && p.report !== undefined ? 'Report' : 'text'} · ${p.span || '1'} · ${p.emphasis || 'kpi'}`),
        h('div', {}, p.name || p.report || '(unnamed)')));
    });
  }

  function placementEditor(p, i) {
    const isReport = p.report !== null && p.report !== undefined;
    const box = h('li', { class: 'st-card st-placement' });
    const head = h('div', { class: 'st-actions' },
      h('strong', {}, isReport ? 'Report' : 'Text'),
      h('button', { class: 'st-btn st-btn-quiet', title: 'Move up', disabled: i === 0, onclick: () => {
        [d.placements[i - 1], d.placements[i]] = [d.placements[i], d.placements[i - 1]]; structural(); drawList(); } }, '↑'),
      h('button', { class: 'st-btn st-btn-quiet', title: 'Move down', disabled: i === d.placements.length - 1, onclick: () => {
        [d.placements[i + 1], d.placements[i]] = [d.placements[i], d.placements[i + 1]]; structural(); drawList(); } }, '↓'),
      h('button', { class: 'st-btn st-btn-quiet', title: 'Remove', onclick: () => { d.placements.splice(i, 1); structural(); drawList(); } }, 'Remove'));
    box.append(head);
    if (isReport) {
      const ref = h('input', { value: p.report || '', placeholder: 'Report qualified name (or a display name to look up)' });
      const said = h('div', {});
      ref.addEventListener('input', () => { p.report = ref.value; if (!p._customName) p.name = ref.value; touch(); drawGrid(); });
      const find = async () => {
        clear(said).append(h('span', { class: 'st-muted' }, 'Looking it up as you…'));
        const res = await readJson(await api(`/api/reports/lookup?ref=${encodeURIComponent(ref.value.trim())}`));
        clear(said);
        if (!res.ok) { said.append(h('p', { class: 'st-error' }, res.detail)); return; }
        const r = res.data;
        if (r.qualified_name && r.qualified_name !== p.report) {
          p.report = r.qualified_name; ref.value = r.qualified_name;
          if (!p._customName) p.name = r.display_name || r.qualified_name;
          touch(); drawGrid();
        }
        said.append(h('p', { class: 'st-ok' }, `${r.display_name} — runs ${r.spec} (${r.output_format}); found by ${r.by}.`));
        for (const n of r.notes || []) said.append(h('p', { class: 'st-warn' }, n));
      };
      box.append(h('label', { class: 'st-field' }, 'Report', h('div', { class: 'st-row' }, ref,
        h('button', { class: 'st-btn st-btn-quiet', onclick: find }, 'Find'))), said);
      if (p.report && !String(p.report).includes('::')) {
        box.append(h('p', { class: 'st-warn' }, 'This looks like a display name. Use Find to keep the Report by its qualified name.'));
      }
    } else {
      const ta = h('textarea', { rows: 4 }); ta.value = p.text || '';
      ta.addEventListener('input', () => { p.text = ta.value; touch(); });
      box.append(h('label', { class: 'st-field' }, 'Text (markdown)', ta));
    }
    const name = h('input', { value: p.name || '' });
    name.addEventListener('input', () => { p.name = name.value; p._customName = true; touch(); drawGrid(); });
    const persp = h('input', { value: (p.perspectives || []).join(', ') });
    persp.addEventListener('input', () => { p.perspectives = csv(persp.value); touch(); });
    const detail = h('input', { value: p.detail_spec || '' });
    detail.addEventListener('input', () => { p.detail_spec = detail.value || null; touch(); });
    box.append(
      h('label', { class: 'st-field' }, 'Placement name (unique on the sheet)', name),
      h('div', { class: 'st-row' }, h('label', {}, 'Span ', choice(p, 'span', ['1', '2', 'full'])), ' ',
        h('label', {}, 'Emphasis ', choice(p, 'emphasis', ['kpi', 'panel']))),
      h('label', { class: 'st-field' }, 'Perspectives (comma-separated)', persp),
      isReport ? h('label', { class: 'st-field' }, 'Detail spec (drill-down)', detail) : null);
    return box;
  }

  function drawList() {
    clear(list);
    d.placements.forEach((p, i) => list.append(placementEditor(p, i)));
    drawGrid();
  }

  // The form's own _customName flag never reaches the server: studio_drafts.js drops "_" keys before sending.
  drawList();
  return h('div', {},
    field('Name (Dashboard::<name>)', d.name, (v) => { d.name = v; }),
    field('Heading', d.heading, (v) => { d.heading = v; }),
    field('Description', d.description, (v) => { d.description = v; }, true),
    field('Family', d.family, (v) => { d.family = v || null; }),
    warn,
    h('h4', {}, 'Placements'),
    list,
    h('div', { class: 'st-actions' },
      h('button', { class: 'st-btn st-btn-quiet', onclick: () => {
        d.placements.push({ report: '', name: '', span: '1', emphasis: 'kpi', perspectives: [] }); touch(); drawList(); } }, 'Add a Report'),
      h('button', { class: 'st-btn st-btn-quiet', onclick: () => {
        d.placements.push({ text: '', name: `Text ${d.placements.length + 1}`, span: 'full', emphasis: 'panel', perspectives: [] }); touch(); drawList(); } }, 'Add text')),
    h('h4', {}, 'Layout'),
    h('p', { class: 'st-muted' }, 'Two columns; a placement spans 1, 2 or the full width, in list order.'),
    grid);
}

window.StudioSheetForm = sheetForm;
