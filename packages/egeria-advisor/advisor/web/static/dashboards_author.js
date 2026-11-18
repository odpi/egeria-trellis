/* Dashboard authoring (brief B7b): "Save to a dashboard", a new sheet, move / remove a placement, delete a
 * sheet. Every change is PREVIEWED (POST /api/dashboards/author/plan: every action it would make, and the
 * Dr.Egeria documents it would run), then CONFIRMED (deleting a sheet asks for its name), then written
 * (POST /api/dashboards/author/apply), as the signed-in user. Nothing is written before the confirm, and
 * a confirm sent twice writes once.
 *
 * Hooks: dashboards_view.js calls decorateSheet / decorateTile / mountSidebar when EADashAuthor is loaded;
 * index.html calls offerSave(wrap, result) on a chat report result. Server text reaches the page only as
 * textContent or through esc().
 */
(function (root) {
  'use strict';

  const OUTPUT_FORMATS = ['DICT', 'TABLE', 'REPORT', 'LIST', 'MERMAID', 'SERIES', 'BAR', 'PIE', 'JSON'];

  function el(tag, cls, text) {
    const e = root.document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function headers() {
    const h = (root.Auth && root.Auth.getHeaders) ? root.Auth.getHeaders() : {};
    return { ...h, 'Content-Type': 'application/json' };
  }
  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: headers(), body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return { state: 'refused', message: r.status === 401 ? 'Sign in to change dashboards: they are written as you.' : (data.detail || `HTTP ${r.status}`) };
    return data;
  }
  function button(text, cls, onClick) {
    const b = el('button', cls || 'px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-100 text-xs', text);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }
  /** GET as the user; {ok, data, why}: why says what went wrong, in the server's words when it gave them
   *  (A-P's {state, message}, FastAPI's detail), never an empty answer passed off as "none". */
  async function getJson(url) {
    try {
      const r = await fetch(url, { headers: headers() });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        return { ok: false, data, why: r.status === 401 ? 'sign in again' : (data.message || data.detail || `HTTP ${r.status}`) };
      }
      if (data.error || data.state === 'read_failed' || data.state === 'failed') {
        return { ok: false, data, why: data.message || data.error };
      }
      return { ok: true, data };
    } catch (e) {
      return { ok: false, data: {}, why: String(e) };
    }
  }

  function refreshAfterWrite(sheetName) {
    if (root.EADashboards) {
      root.EADashboards.loadDashboards(true);
      if (sheetName) root.EADashboards.openDashboard(sheetName, null, true);
    }
  }

  // ── the modal ──────────────────────────────────────────────────────────────
  function modal(title) {
    const back = el('div', 'dash-author-modal fixed inset-0 bg-black/60 flex items-center justify-center z-50');
    const box = el('div', 'bg-slate-800 border border-slate-600 rounded-lg p-4 w-[36rem] max-h-[85vh] overflow-y-auto text-xs text-slate-200');
    box.appendChild(el('div', 'text-sm font-semibold text-slate-100 mb-2', title));
    const body = el('div', 'dash-author-body flex flex-col gap-2');
    box.appendChild(body);
    back.appendChild(box);
    back.addEventListener('click', e => { if (e.target === back) back.remove(); });
    root.document.body.appendChild(back);
    return { back, body, close: () => back.remove() };
  }
  function field(label, input) {
    const row = el('label', 'flex items-center gap-2');
    row.appendChild(el('span', 'w-28 shrink-0 text-slate-400', label));
    row.appendChild(input);
    return row;
  }
  function input(value, placeholder) {
    const i = el('input', 'flex-1 bg-slate-900 border border-slate-600 rounded px-1 py-0.5');
    i.value = value || '';
    if (placeholder) i.placeholder = placeholder;
    return i;
  }
  function select(options, value) {
    const s = el('select', 'bg-slate-900 border border-slate-600 rounded px-1 py-0.5');
    options.forEach(([v, label]) => {
      const o = el('option', null, label);
      o.value = v;
      if (v === value) o.selected = true;
      s.appendChild(o);
    });
    return s;
  }

  /** Show a plan's preview in `body`; on confirm, apply it. Resolves with the apply result, or null. */
  function showPreview(body, plan, onDone) {
    body.innerHTML = '';
    if (plan.state !== 'planned') {
      body.appendChild(el('div', 'dash-author-refused text-amber-400', plan.message || 'Refused.'));
      return;
    }
    body.appendChild(el('div', 'text-slate-300', 'This will be written to Egeria as you:'));
    const list = el('ul', 'dash-author-actions list-disc pl-5 text-slate-300');
    (plan.actions || []).forEach(a => list.appendChild(el('li', null, a)));
    if (plan.report && plan.report.message) list.insertBefore(el('li', 'text-cyan-300', plan.report.message), list.firstChild);
    body.appendChild(list);
    (plan.notes || []).forEach(n => body.appendChild(el('div', 'text-amber-300', n)));
    if (plan.filing) body.appendChild(el('div', `dash-author-filing ${plan.filing.state === 'not_filed' ? 'text-amber-400' : 'text-slate-400'}`, plan.filing.message));
    (plan.dr_egeria || []).forEach(doc => {
      const d = el('details', 'text-slate-400');
      d.appendChild(el('summary', null, 'Dr.Egeria document'));
      d.appendChild(el('pre', 'whitespace-pre-wrap text-[10px] bg-slate-900 p-1 rounded', doc));
      body.appendChild(d);
    });
    let confirmInput = null;
    if (plan.confirm_with) {
      confirmInput = input('', plan.confirm_with);
      body.appendChild(field(`Type "${plan.confirm_with}" to delete`, confirmInput));
    }
    const go = button('Confirm and write', 'dash-author-confirm px-2 py-0.5 rounded bg-emerald-800 hover:bg-emerald-700 text-emerald-100 text-xs', async () => {
      go.disabled = true;
      go.textContent = 'Writing…';
      const out = await post('/api/dashboards/author/apply', { plan_id: plan.plan_id, confirm: confirmInput ? confirmInput.value : true });
      body.innerHTML = '';
      const ok = out.state === 'written' || out.state === 'already_written';
      body.appendChild(el('div', `dash-author-result ${ok ? 'text-emerald-300' : 'text-amber-400'}`,
        ok ? (out.state === 'already_written' ? out.message : `Written: ${(out.actions || []).length} action(s).`) : (out.message || 'Not written.')));
      if (out.filing) body.appendChild(el('div', out.filing.state === 'filed' ? 'text-slate-400' : 'text-amber-400', out.filing.message));
      (out.notes || []).forEach(n => body.appendChild(el('div', 'text-amber-300', n)));
      if (onDone) onDone(out);
    });
    body.appendChild(go);
  }

  async function previewAndConfirm(title, kind, request, onDone) {
    const m = modal(title);
    m.body.appendChild(el('div', 'text-slate-400', 'Previewing…'));
    const plan = await post('/api/dashboards/author/plan', { kind, request });
    showPreview(m.body, plan, onDone);
    return { modal: m, plan };
  }

  // ── zones picker (default public) ─────────────────────────────────────────
  function zonesPicker() {
    const box = el('div', 'dash-author-zones flex flex-col gap-1');
    const vis = select([['public', 'Public (no zones)'], ['zones', 'Restricted to zones']], 'public');
    box.appendChild(field('Visibility', vis));
    const list = el('div', 'pl-28 flex flex-wrap gap-2 hidden');
    box.appendChild(list);
    let loaded = false;
    vis.addEventListener('change', async () => {
      list.classList.toggle('hidden', vis.value !== 'zones');
      if (vis.value !== 'zones' || loaded) return;
      loaded = true;
      list.textContent = 'Reading zones…';
      {
        const got = await getJson('/api/egeria/zones');
        const data = got.data;
        list.textContent = '';
        if (!got.ok) {
          list.appendChild(el('span', 'dash-author-zones-error text-amber-400', `Could not read zones: ${got.why}`));
          return;
        }
        if (!(data.zones || []).length) {
          list.appendChild(el('span', 'text-amber-400', data.message || 'Egeria lists no zones you can use.'));
          return;
        }
        data.zones.forEach(z => {
          const name = typeof z === 'string' ? z : (z.name || z.zoneName || '');
          const lab = el('label', 'flex items-center gap-1');
          const cb = el('input');
          cb.type = 'checkbox';
          cb.value = name;
          lab.appendChild(cb);
          lab.appendChild(el('span', null, name));
          list.appendChild(lab);
        });
      }
    });
    return { box, value: () => vis.value === 'zones' ? [...list.querySelectorAll('input:checked')].map(c => c.value) : [] };
  }

  // ── Save to a dashboard ────────────────────────────────────────────────────
  async function openSave(opts) {
    opts = opts || {};
    const m = modal('Save to a dashboard');
    const body = m.body;
    const listed = await getJson('/api/dashboards');
    const sheets = listed.ok ? listed.data : {};
    if (!listed.ok) {
      // Not "you have no sheets": the list could not be read, so only a new sheet can be chosen here.
      body.appendChild(el('div', 'dash-author-sheets-error text-amber-400', `Could not read your sheets: ${listed.why}`));
    }
    const sheetSel = select([['__new__', 'New sheet…']].concat((sheets.sheets || []).map(s => [s.name, s.heading || s.name])),
      (sheets.sheets || []).length ? sheets.sheets[0].name : '__new__');
    body.appendChild(field('Sheet', sheetSel));
    const newBox = el('div', 'flex flex-col gap-2');
    const newName = input('', 'sheet name');
    const newHeading = input('', 'heading (optional)');
    const newDesc = input('', 'description (optional)');
    newBox.appendChild(field('New sheet name', newName));
    newBox.appendChild(field('Heading', newHeading));
    newBox.appendChild(field('Description', newDesc));
    const zones = zonesPicker();
    newBox.appendChild(zones.box);
    body.appendChild(newBox);
    const toggleNew = () => newBox.classList.toggle('hidden', sheetSel.value !== '__new__');
    sheetSel.addEventListener('change', toggleNew);
    toggleNew();

    const name = input(opts.title || opts.spec || '', 'a unique Report name');
    const spec = input(opts.spec || '', 'report spec');
    const fmt = select(OUTPUT_FORMATS.map(f => [f, f]), (opts.output_format || 'DICT').toUpperCase());
    const search = input(opts.search_string || '', '* (everything)');
    const analytic = input(opts.analytic_params ? JSON.stringify(opts.analytic_params) : '', '{"type_name": "Asset"} (analytic specs only)');
    const span = select([['1', '1'], ['2', '2'], ['full', 'full width']], '1');
    const emphasis = select([['kpi', 'tile'], ['panel', 'panel']], 'kpi');
    [['Report name', name], ['Report spec', spec], ['Output format', fmt], ['Search string', search],
      ['Analytic params', analytic], ['Span', span], ['Emphasis', emphasis]].forEach(([l, i]) => body.appendChild(field(l, i)));
    const err = el('div', 'dash-author-error text-amber-400');
    body.appendChild(err);
    body.appendChild(button('Preview', 'dash-author-preview px-2 py-0.5 rounded bg-cyan-800 hover:bg-cyan-700 text-cyan-100 text-xs', async () => {
      let ap = null;
      if (analytic.value.trim()) {
        try { ap = JSON.parse(analytic.value); } catch (e) { err.textContent = 'Analytic params must be a JSON object.'; return; }
        if (!ap || typeof ap !== 'object' || Array.isArray(ap)) { err.textContent = 'Analytic params must be a JSON object.'; return; }
      }
      const isNew = sheetSel.value === '__new__';
      const request = {
        sheet: isNew ? newName.value.trim() : sheetSel.value,
        new_sheet: isNew ? { heading: newHeading.value.trim(), description: newDesc.value.trim() } : null,
        zones: isNew ? zones.value() : [],
        report: { name: name.value.trim(), spec: spec.value.trim(), output_format: fmt.value,
          params: search.value.trim() && !ap ? { search_string: search.value.trim() } : {}, analytic_params: ap || {} },
        placement: { span: span.value, emphasis: emphasis.value },
      };
      body.innerHTML = '';
      body.appendChild(el('div', 'text-slate-400', 'Previewing…'));
      const plan = await post('/api/dashboards/author/plan', { kind: 'save', request });
      showPreview(body, plan, out => { if (out.state === 'written') refreshAfterWrite(request.sheet); });
    }));
    return m;
  }

  function newSheet() {
    const m = modal('New dashboard sheet');
    const name = input('', 'sheet name');
    const heading = input('', 'heading (optional)');
    const desc = input('', 'description (optional)');
    const zones = zonesPicker();
    [['Name', name], ['Heading', heading], ['Description', desc]].forEach(([l, i]) => m.body.appendChild(field(l, i)));
    m.body.appendChild(zones.box);
    m.body.appendChild(button('Preview', 'dash-author-preview px-2 py-0.5 rounded bg-cyan-800 hover:bg-cyan-700 text-cyan-100 text-xs', async () => {
      const request = { sheet: name.value.trim(), heading: heading.value.trim(), description: desc.value.trim(), zones: zones.value() };
      m.body.innerHTML = '';
      const plan = await post('/api/dashboards/author/plan', { kind: 'create_sheet', request });
      showPreview(m.body, plan, out => { if (out.state === 'written') refreshAfterWrite(request.sheet); });
    }));
    return m;
  }

  // ── hooks ──────────────────────────────────────────────────────────────────
  function decorateSheet(wrap, sheet) {
    const bar = wrap.querySelector('.dash-refresh-all');
    if (!bar || wrap.querySelector('.dash-delete-sheet')) return;
    const del = button('🗑 sheet', 'dash-delete-sheet text-xs px-1 text-slate-500 hover:text-red-300', () =>
      previewAndConfirm(`Delete sheet ${sheet.name}`, 'delete_sheet', { sheet: sheet.name }, out => {
        if (out.state === 'written') { refreshAfterWrite(null); wrap.remove(); }
      }));
    del.title = 'Delete this sheet (asks you to type its name)';
    bar.parentNode.insertBefore(del, bar.nextSibling);
  }

  function decorateTile(tile, sheet, p) {
    if (!p || !p.ref) return;
    const bar = el('div', 'dash-author-tile flex gap-1 justify-end text-[10px] text-slate-500 mt-1');
    const act = (label, title, kind, extra) => {
      const b = button(label, 'hover:text-slate-200', () => previewAndConfirm(title, kind, { sheet: sheet.name, placement: p.ref, ...extra },
        out => { if (out.state === 'written') refreshAfterWrite(sheet.name); }));
      b.title = title;
      bar.appendChild(b);
    };
    act('↑', `Move '${p.ref}' up`, 'move', { direction: 'up' });
    act('↓', `Move '${p.ref}' down`, 'move', { direction: 'down' });
    act('✕', `Remove '${p.ref}' from the sheet (its Report stays)`, 'remove', {});
    tile.appendChild(bar);
  }

  function mountSidebar() {
    const list = root.document.getElementById('dashboard-list');
    if (!list || root.document.querySelector('.dash-new-sheet')) return;
    const b = button('+ New sheet', 'dash-new-sheet text-left px-2 py-1 text-xs text-cyan-300 hover:text-cyan-100', () => newSheet());
    list.parentNode.insertBefore(b, list);
  }

  /** A "Save to a dashboard" button under a chat report result. */
  function offerSave(wrap, result) {
    if (!wrap || !result || result.query_type !== 'report' || !result.report_name || result.run_error) return;
    const b = button('＋ Save to a dashboard', 'dash-offer-save mt-1 text-xs text-cyan-300 hover:text-cyan-100', () =>
      openSave({ spec: result.report_name, title: result.report_name }));
    wrap.appendChild(b);
  }

  root.EADashAuthor = { openSave, newSheet, decorateSheet, decorateTile, mountSidebar, offerSave, showPreview, previewAndConfirm };
})(typeof window !== 'undefined' ? window : globalThis);
