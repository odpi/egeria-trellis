/*
 * Report specs in Egeria, the EA side of brief B6:
 *   - "Used by" filter over the catalog (consumer folders, from the last sync's consumer index);
 *   - "Restricted" section: the zone-restricted specs THIS user can see, read as them (never shared);
 *   - Publish a local spec to Egeria (Public / Restricted to zones), as a Dr.Egeria document run as the
 *     user: Preview -> Validate -> Confirm -> Publish;
 *   - Withdraw a spec from the consumer folder(s), with the same steps.
 *
 * index.html only mounts this: loadReports() calls SpecsPublish.onCatalog(data, catalogEl).
 * Every empty state shows the server's words (it always says why); nothing is rendered as innerHTML
 * from data.
 */
(function () {
  'use strict';

  const ADVISOR = 'ReportSpecFolder::Consumer::EgeriaAdvisor';
  const PORTAL = 'ReportSpecFolder::Consumer::Portal';
  let usedByFilter = '';
  let lastData = null;   // the last /api/reports answer: publish status, restricted rows

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v !== false && v !== null && v !== undefined) el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid) el.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    return el;
  }

  function headers() {
    const base = (window.Auth && Auth.getHeaders) ? Auth.getHeaders() : {};
    return Object.assign({ 'Content-Type': 'application/json' }, base);
  }

  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: headers(), body: JSON.stringify(body || {}) });
    let data = null;
    try { data = await r.json(); } catch { data = null; }
    if (!r.ok) {
      const why = (data && (data.detail || data.message)) || `HTTP ${r.status}`;
      throw new Error(typeof why === 'string' ? why : JSON.stringify(why));
    }
    return data;
  }

  // ── catalog hooks ──────────────────────────────────────────────────────────

  function consumerName(qn) { return (qn || '').split('::').pop() || qn; }

  function renderUsedBy(data, catalogEl) {
    const usedBy = data.used_by || {};
    let bar = document.getElementById('specs-used-by');
    if (!bar) {
      bar = h('div', { id: 'specs-used-by', class: 'px-2 py-1 text-[11px] text-slate-400 flex flex-col gap-0.5' });
      catalogEl.parentNode.insertBefore(bar, catalogEl);
    }
    bar.textContent = '';
    const consumers = usedBy.consumers || [];
    if (usedBy.state === 'ok' || usedBy.state === 'partial') {
      const sel = h('select', { class: 'bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-[11px] text-slate-300',
        'aria-label': 'Filter by consumer folder' },
        h('option', { value: '', text: 'Used by: any (or none)' }),
        consumers.map(c => h('option', { value: c, text: `Used by: ${consumerName(c)}`, selected: c === usedByFilter })));
      sel.addEventListener('change', () => { usedByFilter = sel.value; applyUsedBy(data, catalogEl); });
      bar.appendChild(sel);
    }
    if (usedBy.message) bar.appendChild(h('div', { class: 'text-slate-500', text: usedBy.message }));
    applyUsedBy(data, catalogEl);
  }

  function applyUsedBy(data, catalogEl) {
    const index = (data.used_by || {}).index || {};
    // Restricted rows carry their own consumers (the shared consumer index leaves zoned labels out), as display
    // names (`used_by`, from the server), the same form as the facet's choices and the index's values.
    document.querySelectorAll('[data-restricted-row]').forEach(row => {
      const folders = JSON.parse(row.getAttribute('data-folders') || '[]');
      row.style.display = (!usedByFilter || folders.includes(usedByFilter)) ? '' : 'none';
    });
    catalogEl.querySelectorAll('button[data-spec]').forEach(btn => {
      const name = btn.getAttribute('data-spec');
      const users = index[name] || [];
      btn.closest('[data-spec-row]').style.display = (!usedByFilter || users.includes(usedByFilter)) ? '' : 'none';
      const t = users.length ? `Used by: ${users.map(consumerName).join(', ')}` : '';
      if (t && !(btn.title || '').includes('Used by:')) btn.title = [btn.title, t].filter(Boolean).join(' — ');
    });
  }

  function decorateRows(data, catalogEl) {
    const sources = data.sources || {};
    catalogEl.querySelectorAll('button').forEach(btn => {
      if (btn.hasAttribute('data-spec') || btn.closest('[data-spec-row]')) return;
      const name = (btn.firstChild && btn.firstChild.nodeType === 3) ? btn.firstChild.textContent : btn.textContent;
      const src = (sources[name] || {}).source;
      const row = h('div', { class: 'flex items-center gap-1 group', 'data-spec-row': '' });
      btn.setAttribute('data-spec', name);
      btn.classList.add('flex-1');
      btn.parentNode.insertBefore(row, btn);
      row.appendChild(btn);
      if (src === 'user' || src === 'local') {
        row.appendChild(h('button', { class: 'opacity-0 group-hover:opacity-100 text-[10px] px-1 rounded bg-slate-700 text-slate-300',
          title: `Publish '${name}' to Egeria`, text: 'publish', onclick: () => openPublish(name) }));
      } else if (src === 'egeria') {
        row.appendChild(h('button', { class: 'opacity-0 group-hover:opacity-100 text-[10px] px-1 rounded bg-slate-700 text-slate-300',
          title: `Withdraw '${name}' from the consumer folder`, text: 'withdraw', onclick: () => openWithdraw(name) }));
      }
    });
  }

  function renderRestricted(data, catalogEl) {
    const rs = data.restricted || {};
    let box = document.getElementById('specs-restricted');
    if (!box) {
      box = h('details', { id: 'specs-restricted', class: 'mt-0.5' });
      catalogEl.parentNode.insertBefore(box, catalogEl.nextSibling);
    }
    box.textContent = '';
    const specs = rs.specs || [];
    box.appendChild(h('summary', { class: 'px-2 py-1 rounded text-xs font-semibold text-slate-400 uppercase tracking-wider hover:bg-slate-700/50 select-none',
      text: `Restricted (${specs.length})` }));
    const list = h('div', { class: 'ml-1 mt-0.5 flex flex-col gap-0.5' });
    specs.forEach(s => {
      const zones = (s.zones || []).join(', ');
      const btn = h('button', { class: 'text-left px-2 py-0.5 rounded text-xs hover:bg-slate-700 text-slate-300 flex-1',
        title: `Read from Egeria as you; used by: ${(s.used_by || (s.folders || []).map(consumerName)).join(', ')}`,
        onclick: () => runRestricted(s.label) },
        s.label, h('span', { class: 'ml-1 px-1 rounded text-[10px] bg-rose-900/50 text-rose-300', text: `restricted: ${zones}` }));
      list.appendChild(h('div', { class: 'flex items-center gap-1 group', 'data-restricted-row': '',
        'data-folders': JSON.stringify(s.used_by || s.folders || []) }, btn,
        h('button', { class: 'opacity-0 group-hover:opacity-100 text-[10px] px-1 rounded bg-slate-700 text-slate-300',
          text: 'withdraw', title: `Withdraw '${s.label}'`, onclick: () => openWithdraw(s.label) })));
    });
    if (rs.message) list.appendChild(h('div', { class: 'px-2 text-[11px] text-slate-500', text: rs.message }));
    box.appendChild(list);
  }

  function renderCatalogStatus(data, catalogEl) {
    const st = data.catalog_status || {};
    let el = document.getElementById('specs-catalog-status');
    if (!el) {
      el = h('div', { id: 'specs-catalog-status', class: 'px-2 py-1 text-[11px] text-slate-500' });
      catalogEl.parentNode.insertBefore(el, catalogEl);
    }
    el.textContent = st.message || '';
    el.title = (st.unrunnable || []).map(u => `${u.name}: ${u.reason}`).join('\n')
      + ((st.bundled_only || []).length ? `\nFrom EA's bundled spec files only: ${st.bundled_only.join(', ')}` : '');
  }

  function onCatalog(data, catalogEl) {
    if (!data || !catalogEl) return;
    lastData = data;
    try {
      decorateRows(data, catalogEl);
      renderCatalogStatus(data, catalogEl);
      renderUsedBy(data, catalogEl);
      renderRestricted(data, catalogEl);
      applyUsedBy(data, catalogEl);   // again, now the restricted rows exist
    } catch (e) {
      console.error('SpecsPublish.onCatalog', e);
    }
  }

  // ── dialogs ────────────────────────────────────────────────────────────────

  function modal(title) {
    const close = () => overlay.remove();
    const body = h('div', { class: 'flex flex-col gap-2 text-sm text-slate-200' });
    const overlay = h('div', { class: 'fixed inset-0 z-50 bg-black/60 flex items-center justify-center', role: 'dialog', 'aria-modal': 'true',
      onclick: e => { if (e.target === overlay) close(); } },
      h('div', { class: 'bg-slate-900 border border-slate-700 rounded-lg p-4 w-[min(48rem,95vw)] max-h-[90vh] overflow-auto flex flex-col gap-3' },
        h('div', { class: 'flex justify-between items-center' },
          h('h2', { class: 'font-semibold text-slate-100', text: title }),
          h('button', { class: 'text-slate-400 hover:text-slate-200', text: '✕', 'aria-label': 'Close', onclick: close })),
        body));
    document.body.appendChild(overlay);
    return { body, close };
  }

  function btn(text, onclick, primary) {
    return h('button', { class: `px-3 py-1 rounded text-xs ${primary ? 'bg-cyan-700 hover:bg-cyan-600 text-white' : 'bg-slate-700 hover:bg-slate-600 text-slate-200'}`,
      text, onclick });
  }

  function showResult(area, res) {
    area.textContent = '';
    if (!res) return;
    if (res.steps) area.appendChild(h('ul', { class: 'list-disc ml-5 text-xs text-slate-300' }, res.steps.map(s => h('li', { text: s }))));
    if (res.markdown && res.step === 'preview') {
      area.appendChild(h('div', { class: 'text-xs text-slate-400', text: `The Dr.Egeria document (runs as you; ${res.held ? res.held.how : ''}):` }));
      area.appendChild(h('pre', { class: 'text-[11px] bg-slate-950 border border-slate-800 rounded p-2 whitespace-pre-wrap', text: res.markdown }));
    }
    const r = res.result;
    if (r) {
      const ok = r.success;
      const label = res.step === 'validate' ? (ok ? 'Validated.' : 'Validation found problems.') : (ok ? 'Done.' : 'It did not complete.');
      area.appendChild(h('div', { class: ok ? 'text-emerald-300' : 'text-rose-300', text: label }));
      const msgs = [].concat(r.execution_errors || [], r.validation_errors || []).map(e => e.message).filter(Boolean);
      if (r.auth_required && r.output) msgs.unshift(r.output);
      msgs.forEach(m => area.appendChild(h('div', { class: 'text-xs text-rose-300', text: m })));
      (r.commands_detail || []).forEach(d => area.appendChild(h('div', { class: 'text-xs text-slate-400',
        text: `${d.step}. ${d.command}: ${d.status}${d.message ? ' — ' + d.message : ''}` })));
    }
    if (res.after) area.appendChild(h('div', { class: 'text-xs text-slate-400', text: res.after.message }));
  }

  function stepper(kind, label, bodyFn) {
    // bodyFn() -> the request body for this dialog's current choices (without step/confirm/digest)
    const { body, close } = modal(kind === 'publish' ? `Publish '${label}' to Egeria` : `Withdraw '${label}'`);
    const controls = h('div', { class: 'flex flex-col gap-2' });
    const out = h('div', { class: 'flex flex-col gap-1' });
    const confirm = h('input', { type: 'checkbox', id: 'specs-confirm' });
    const confirmRow = h('label', { class: 'flex items-center gap-2 text-xs text-slate-300' }, confirm,
      kind === 'publish' ? 'I have read the document; write it to Egeria as me.' : 'Remove it from these folders, as me.');
    let digest = null;
    const url = `/api/reports/specs/${encodeURIComponent(label)}/${kind}`;
    async function go(step) {
      out.textContent = step === 'process' ? 'Writing to Egeria…' : 'Working…';
      try {
        const res = await post(url, Object.assign(bodyFn(), { step, confirmed: confirm.checked, digest }));
        if (step === 'preview') digest = res.digest;
        showResult(out, res);
        if (step === 'process' && res.result && res.result.success && typeof window.loadReports === 'function') window.loadReports();
      } catch (e) {
        out.textContent = '';
        out.appendChild(h('div', { class: 'text-rose-300 text-xs', text: e.message }));
      }
    }
    const actions = h('div', { class: 'flex gap-2 flex-wrap' },
      btn('Preview', () => { digest = null; confirm.checked = false; go('preview'); }),
      btn('Validate', () => go('validate')),
      btn(kind === 'publish' ? 'Publish' : 'Withdraw', () => {
        if (!digest) { out.textContent = 'Preview the document first.'; return; }
        if (!confirm.checked) { out.textContent = 'Tick the confirmation first.'; return; }
        go('process');
      }, true),
      btn('Close', close));
    body.append(controls, confirmRow, actions, out);
    return { controls, reset: () => { digest = null; confirm.checked = false; } };
  }

  async function openPublish(label) {
    let visibility = 'public';
    const chosen = new Set();
    const portal = h('input', { type: 'checkbox' });
    const zonesBox = h('div', { class: 'flex flex-col gap-1 ml-5' });
    const { controls, reset } = stepper('publish', label, () => ({
      visibility, zones: visibility === 'restricted' ? [...chosen] : [], also_portal: portal.checked }));
    const restrictedStatus = ((lastData || {}).publish || {}).restricted
      || { enabled: false, message: 'Restricted publish is not yet proven on this platform.' };
    const radio = (value, text) => {
      const off = value === 'restricted' && !restrictedStatus.enabled;
      const r = h('input', { type: 'radio', name: 'specs-visibility', value, checked: value === visibility, disabled: off });
      r.addEventListener('change', () => { visibility = value; zonesBox.style.display = value === 'restricted' ? '' : 'none'; reset(); });
      return h('label', { class: 'flex items-center gap-2 text-xs' }, r, text);
    };
    controls.append(
      h('div', { class: 'text-xs text-slate-400', text: 'Visibility' }),
      radio('public', 'Public (default): every EA user sees it; no zones.'),
      radio('restricted', 'Restricted to zones: only people Egeria grants one of these zones see it.'),
      restrictedStatus.enabled ? null : h('div', { class: 'ml-5 text-xs text-amber-300', 'data-restricted-off': '',
        text: restrictedStatus.message }),
      zonesBox,
      h('div', { class: 'text-xs text-slate-400', text: `Where: ${ADVISOR}` }),
      h('label', { class: 'flex items-center gap-2 text-xs' }, portal, `Also in ${PORTAL}`));
    portal.addEventListener('change', reset);
    zonesBox.style.display = 'none';
    if (!restrictedStatus.enabled) return;   // D5: no zone picker until the zone proof is recorded
    zonesBox.textContent = 'Loading zones…';
    try {
      const r = await fetch('/api/egeria/zones', { headers: headers() });
      let z = null;
      try { z = await r.json(); } catch { z = null; }
      zonesBox.textContent = '';
      if (!r.ok) {
        const why = (z && (z.detail || z.message)) || `HTTP ${r.status}`;
        zonesBox.appendChild(h('div', { class: 'text-xs text-rose-300', text: `Zones could not be read: ${typeof why === 'string' ? why : JSON.stringify(why)}` }));
        return;
      }
      z = z || {};
      const zones = (z.zones || []).map(x => (typeof x === 'string' ? { name: x } : x));
      if (z.state && z.state !== 'ok' && z.message) {
        zonesBox.appendChild(h('div', { class: 'text-xs text-slate-500', text: z.message }));   // A-P's {state, message}
      } else if (!zones.length) zonesBox.appendChild(h('div', { class: 'text-xs text-slate-500',
        text: z.message || z.error || 'Egeria returned no governance zones, so a restricted publish has nothing to pick.' }));
      zones.forEach(zone => {
        const cb = h('input', { type: 'checkbox', value: zone.name });
        cb.addEventListener('change', () => { cb.checked ? chosen.add(zone.name) : chosen.delete(zone.name); reset(); });
        const granted = zone.granted_to ? `granted to ${[].concat(zone.granted_to).join(', ')}`
          : 'Egeria does not say here who holds this zone';
        zonesBox.appendChild(h('label', { class: 'flex items-center gap-2 text-xs' }, cb, zone.name,
          h('span', { class: 'text-slate-500', text: `(${granted})` })));
      });
    } catch (e) {
      zonesBox.textContent = `Zones could not be read: ${e.message}`;
    }
  }

  function openWithdraw(label) {
    const advisor = h('input', { type: 'checkbox', checked: true });
    const portal = h('input', { type: 'checkbox' });
    const { controls, reset } = stepper('withdraw', label, () => ({
      folders: [advisor.checked && ADVISOR, portal.checked && PORTAL].filter(Boolean) }));
    [advisor, portal].forEach(cb => cb.addEventListener('change', reset));
    controls.append(
      h('div', { class: 'text-xs text-slate-400', text: 'Remove it from these consumer folders (the spec element stays in Egeria):' }),
      h('label', { class: 'flex items-center gap-2 text-xs' }, advisor, ADVISOR),
      h('label', { class: 'flex items-center gap-2 text-xs' }, portal, PORTAL),
      h('div', { class: 'text-xs text-slate-500', text: 'Afterwards a sync starts, and the catalog shows it gone once it finishes.' }));
  }

  async function runRestricted(label) {
    const { body } = modal(`${label} (restricted)`);
    body.textContent = 'Running as you…';
    try {
      const res = await post(`/api/reports/restricted/${encodeURIComponent(label)}/run`, { output_format: 'JSON' });
      body.textContent = '';
      if (res.state !== 'ok') { body.appendChild(h('div', { class: 'text-rose-300 text-xs', text: res.message })); return; }
      const r = res.result || {};
      const text = r.kind === 'text' || r.kind === 'absent' ? (r.content || '') : JSON.stringify(r.data !== undefined ? r.data : r, null, 2);
      body.appendChild(h('pre', { class: 'text-[11px] bg-slate-950 border border-slate-800 rounded p-2 whitespace-pre-wrap', text }));
    } catch (e) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'text-rose-300 text-xs', text: e.message }));
    }
  }

  window.SpecsPublish = { onCatalog, openPublish, openWithdraw, runRestricted };
})();
