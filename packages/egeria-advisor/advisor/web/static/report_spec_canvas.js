/**
 * report_spec_canvas.js — Report Spec Canvas built on ArtifactCanvas
 *
 * Adapts ArtifactCanvas for report spec drafts:
 *   data shape: report spec draft (columns + three parameter categories)
 *   sync:       PATCH /api/reports/drafts/{id}/columns
 *   fields:     GET  /api/templates/Column/fields
 */

// ── Parameter section definitions ──────────────────────────────────────────
const _PARAM_SECTIONS = [
  {
    id: 'content_filters',
    label: 'Content Filters',
    open: true,
    fields: [
      { key: 'search_string',              label: 'Search string',       type: 'text',   placeholder: '*' },
      { key: 'starts_with',                label: 'Starts with',         type: 'checkbox' },
      { key: 'ends_with',                  label: 'Ends with',           type: 'checkbox' },
      { key: 'ignore_case',                label: 'Ignore case',         type: 'checkbox' },
      { key: 'metadata_element_type',      label: 'Element type',        type: 'text',   placeholder: 'e.g. dataHub' },
      { key: 'metadata_element_subtypes',  label: 'Subtypes',            type: 'text',   placeholder: 'comma-separated subtypes' },
      { key: 'limit_results_by_status',    label: 'Status filter',       type: 'select',
        options: ['', 'ACTIVE', 'DRAFT', 'DEPRECATED', 'PROPOSED', 'APPROVED', 'DELETED'] },
      { key: 'governance_zone_filter',     label: 'Governance zone',     type: 'text',   placeholder: 'zone name' },
      { key: 'anchor_type_name',           label: 'Anchor type',         type: 'text',   placeholder: 'e.g. Asset' },
      { key: 'anchor_domain',              label: 'Anchor domain',       type: 'text',   placeholder: 'domain name' },
    ],
  },
  {
    id: 'shape_defaults',
    label: 'Shape Defaults',
    open: false,
    fields: [
      { key: 'sequencing_property',        label: 'Sort field',          type: 'text',   placeholder: 'display_name' },
      { key: 'sequencing_order',           label: 'Sort order',          type: 'select', options: ['', 'ASC', 'DESC'] },
      { key: 'graph_query_depth',          label: 'Graph depth',         type: 'number', placeholder: '0' },
      { key: 'max_mermaid_node_count',     label: 'Max diagram nodes',   type: 'number', placeholder: '50' },
      { key: 'skip_relationships',         label: 'Skip relationships',  type: 'checkbox' },
      { key: 'include_only_relationships', label: 'Only relationships',  type: 'text',   placeholder: 'comma-separated rel types' },
    ],
  },
  {
    id: 'performance_hints',
    label: 'Performance Hints',
    open: false,
    fields: [
      { key: 'page_size',               label: 'Page size',          type: 'number', placeholder: '100' },
      { key: 'start_from',              label: 'Start from',         type: 'number', placeholder: '0' },
      { key: 'relationship_page_size',  label: 'Rel. page size',     type: 'number', placeholder: '50' },
      { key: 'as_of_time',              label: 'As of time',         type: 'text',   placeholder: 'ISO timestamp' },
      { key: 'effective_time',          label: 'Effective time',     type: 'text',   placeholder: 'ISO timestamp' },
    ],
  },
];

// Debounce helper
function _debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// PATCH a single parameter category
async function _patchReportParams(draftId, category, values) {
  try {
    await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}/columns`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() },
      body: JSON.stringify({ [category]: values }),
    });
  } catch (e) {
    console.warn('patchReportParams failed', e);
  }
}

// Render editable Spec Info (target_type, heading) above param sections
function _renderSpecInfo(meta, draftId) {
  const container = document.getElementById('rcanvas-params');
  if (!container) return;

  const debouncedPatch = _debounce(async () => {
    const ttEl = container.querySelector('[data-spec-key="target_type"]');
    const hdEl = container.querySelector('[data-spec-key="heading"]');
    const afEl = container.querySelector('[data-spec-key="action_function"]');
    const peEl = container.querySelector('[data-spec-key="perspectives"]');
    const quEl = container.querySelector('[data-spec-key="questions"]');
    const body = {};
    if (ttEl && ttEl.value.trim()) body.target_type = ttEl.value.trim();
    if (hdEl && hdEl.value.trim()) body.heading = hdEl.value.trim();
    if (afEl) body.action_function = afEl.value.trim();
    if (peEl) body.perspectives = peEl.value.split(',').map(s => s.trim()).filter(Boolean);
    if (quEl) body.questions = quEl.value.split(',').map(s => s.trim()).filter(Boolean);
    if (Object.keys(body).length) {
      try {
        await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}/columns`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() },
          body: JSON.stringify(body),
        });
      } catch (e) { console.warn('spec info patch failed', e); }
    }
  }, 600);

  const details = document.createElement('details');
  details.className = 'border-b border-slate-700/50';
  details.open = true;
  details.innerHTML = `<summary class="px-3 py-1.5 text-xs font-semibold text-slate-400 hover:text-slate-200 cursor-pointer select-none list-none flex items-center gap-1"><span class="text-slate-500 text-[10px]">▸</span> Spec Info</summary>`;

  const body = document.createElement('div');
  body.className = 'px-3 pb-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 items-center';

  [
    { key: 'target_type',     label: 'Target type',     value: meta.target_type || '',     placeholder: 'e.g. Collection, Glossary' },
    { key: 'heading',         label: 'Heading',         value: meta.answers?.Heading || '', placeholder: 'Report title' },
    { key: 'action_function', label: 'Action function', value: meta.action_function || '', placeholder: 'e.g. GlossaryManager.find_glossaries' },
    { key: 'perspectives',    label: 'Perspectives',    value: (meta.perspectives || []).join(', '), placeholder: 'e.g. Developer, Data Steward' },
    { key: 'questions',       label: 'Questions',       value: (meta.questions || []).join(', '),    placeholder: 'e.g. what glossaries are defined?, list glossaries' },
  ].forEach(f => {
    const lbl = document.createElement('label');
    lbl.className = 'text-[11px] text-slate-500 whitespace-nowrap';
    lbl.textContent = f.label;
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'text-[11px] bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-violet-500 w-full';
    inp.placeholder = f.placeholder;
    inp.value = f.value;
    inp.dataset.specKey = f.key;
    inp.addEventListener('input', debouncedPatch);
    body.appendChild(lbl);
    body.appendChild(inp);
  });

  details.appendChild(body);
  container.insertAdjacentElement('afterbegin', details);
  details.insertAdjacentElement('afterend', _renderSavedQuerySource(meta, draftId));
}

// Route B (brief B6, N6): a SavedQuery as the spec's source -> analytic_function: saved_query
// with {name, mode, output, params}. The server checks the shape (400 with words); pyegeria checks
// the values at run time, and a Smart Collection refuses values other than its stored ones (P-3).
const _SMART_COLLECTION_NOTE = "A Smart Collection's cached answer stands only for the values its last " +
  "refresh recorded; a different value is refused (Egeria re-runs the stored request itself). Use live " +
  "mode to run it with other values.";

function _renderSavedQuerySource(meta, draftId) {
  const sq = meta.saved_query || null;
  const wrap = document.createElement('details');
  wrap.className = 'border-b border-slate-700/50';
  wrap.open = !!sq;
  const sum = document.createElement('summary');
  sum.className = 'px-3 py-1.5 text-xs font-semibold text-slate-400 hover:text-slate-200 cursor-pointer select-none list-none';
  sum.textContent = 'Source: ' + (sq ? 'saved query' : 'find method (action function)');
  wrap.appendChild(sum);
  const box = document.createElement('div');
  box.className = 'px-3 pb-2 flex flex-col gap-1 text-[11px] text-slate-300';
  const field = (label, el) => {
    const row = document.createElement('label');
    row.className = 'grid grid-cols-[6rem_1fr] gap-2 items-center';
    const t = document.createElement('span'); t.className = 'text-slate-500'; t.textContent = label;
    row.append(t, el); return row;
  };
  const cls = 'bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 text-slate-200';
  const use = document.createElement('input'); use.type = 'checkbox'; use.checked = !!sq;
  const name = document.createElement('input'); name.className = cls; name.placeholder = 'the SavedQuery qualifiedName'; name.value = sq?.name || '';
  const mode = document.createElement('select'); mode.className = cls;
  [['cached', 'cached (its Results Set, as of the last refresh)'], ['live', 'live (run now, as the viewer)']]
    .forEach(([v, t]) => { const o = document.createElement('option'); o.value = v; o.textContent = t; o.selected = (sq?.mode || 'cached') === v; mode.appendChild(o); });
  const output = document.createElement('select'); output.className = cls;
  [['count', 'count (a KPI)'], ['list', 'list (the elements)']]
    .forEach(([v, t]) => { const o = document.createElement('option'); o.value = v; o.textContent = t; o.selected = (sq?.output || 'count') === v; output.appendChild(o); });
  const params = document.createElement('textarea'); params.className = cls + ' font-mono'; params.rows = 2;
  params.placeholder = '{"status": "ACTIVE"}  (values for the query\'s declared parameters)';
  params.value = sq?.params && Object.keys(sq.params).length ? JSON.stringify(sq.params) : '';
  const note = document.createElement('div'); note.className = 'text-slate-500'; note.textContent = _SMART_COLLECTION_NOTE;
  const msg = document.createElement('div'); msg.className = 'text-rose-300';
  const save = _debounce(async () => {
    msg.textContent = '';
    let body;
    if (!use.checked) body = { saved_query: null };
    else {
      let p = {};
      if (params.value.trim()) {
        try { p = JSON.parse(params.value); } catch { msg.textContent = 'Params must be a JSON object, e.g. {"status": "ACTIVE"}.'; return; }
      }
      body = { saved_query: { name: name.value.trim(), mode: mode.value, output: output.value, params: p } };
      if (!body.saved_query.name) { msg.textContent = 'Name the saved query (its qualifiedName).'; return; }
    }
    try {
      const r = await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}/columns`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() }, body: JSON.stringify(body),
      });
      if (!r.ok) { const d = await r.json().catch(() => ({})); msg.textContent = d.detail || `Not saved: HTTP ${r.status}`; return; }
      sum.textContent = 'Source: ' + (use.checked ? 'saved query' : 'find method (action function)');
    } catch (e) { msg.textContent = `Not saved: ${e.message}`; }
  }, 600);
  [use, name, mode, output, params].forEach(el => el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' ? 'change' : 'input', save));
  box.append(field('Use a saved query', use), field('Name', name), field('Mode', mode), field('Output', output),
    field('Params', params), note, msg);
  wrap.appendChild(box);
  return wrap;
}

// Render all three parameter sections into #rcanvas-params
function _renderParamSections(meta, draftId) {
  const container = document.getElementById('rcanvas-params');
  if (!container) return;
  container.innerHTML = '';

  _renderSpecInfo(meta, draftId);

  _PARAM_SECTIONS.forEach(section => {
    const values = meta[section.id] || {};

    const details = document.createElement('details');
    details.className = 'border-b border-slate-700/50 last:border-0';
    if (section.open) details.open = true;

    const summary = document.createElement('summary');
    summary.className =
      'px-3 py-1.5 text-xs font-semibold text-slate-400 hover:text-slate-200 cursor-pointer ' +
      'select-none list-none flex items-center gap-1';
    summary.innerHTML =
      `<span class="text-slate-500 text-[10px]">▸</span> ${section.label}`;
    details.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'px-3 pb-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 items-center';

    const debouncedPatch = _debounce(() => {
      // Collect current values from all inputs in this section
      const updated = {};
      section.fields.forEach(f => {
        const el = body.querySelector(`[data-param-key="${f.key}"]`);
        if (!el) return;
        if (f.type === 'checkbox') {
          if (el.checked) updated[f.key] = true;
        } else {
          const v = el.value.trim();
          if (v !== '' && v !== (f.placeholder || '')) {
            updated[f.key] = f.type === 'number' ? Number(v) : v;
          }
        }
      });
      _patchReportParams(draftId, section.id, updated);
    }, 600);

    section.fields.forEach(f => {
      const lbl = document.createElement('label');
      lbl.className = 'text-[11px] text-slate-500 whitespace-nowrap';
      lbl.textContent = f.label;

      let input;
      if (f.type === 'select') {
        input = document.createElement('select');
        input.className =
          'text-[11px] bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 ' +
          'text-slate-200 focus:outline-none focus:border-violet-500 w-full';
        (f.options || []).forEach(opt => {
          const o = document.createElement('option');
          o.value = opt; o.textContent = opt || '(any)';
          if (String(values[f.key] ?? '') === opt) o.selected = true;
          input.appendChild(o);
        });
      } else if (f.type === 'checkbox') {
        input = document.createElement('input');
        input.type = 'checkbox';
        input.className = 'accent-violet-500';
        input.checked = !!values[f.key];
      } else {
        input = document.createElement('input');
        input.type = f.type || 'text';
        input.className =
          'text-[11px] bg-slate-700 border border-slate-600 rounded px-1.5 py-0.5 ' +
          'text-slate-200 placeholder-slate-500 focus:outline-none focus:border-violet-500 w-full';
        input.placeholder = f.placeholder || '';
        const cur = values[f.key];
        if (cur !== undefined && cur !== null) input.value = String(cur);
      }

      input.dataset.paramKey = f.key;
      input.addEventListener('change', debouncedPatch);
      input.addEventListener('input', debouncedPatch);

      body.appendChild(lbl);
      body.appendChild(input);
    });

    details.appendChild(body);
    container.appendChild(details);
  });
}

// ── ArtifactCanvas adapter ──────────────────────────────────────────────────

let _canvasActiveDraftId = null;

const _reportAdapter = {
  async fetch(draftId) {
    _canvasActiveDraftId = draftId;
    const r = await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}`, { headers: Auth.getHeaders() });
    if (!r.ok) throw new Error(`Report draft ${draftId} not found`);
    const spec = await r.json();
    // Derive supported output formats from column restrictions
    const _ALL_FMTS = ['TABLE','LIST','MERMAID','REPORT','JSON'];
    const cols = spec.columns || [];
    let supportedFormats = _ALL_FMTS;
    if (cols.length) {
      const restricted = cols
        .map(c => (c.formats || 'ALL').toUpperCase())
        .filter(f => f !== 'ALL');
      if (restricted.length) {
        // union of all restricted sets — formats any column supports
        const union = new Set();
        restricted.forEach(f => f.split(',').map(s => s.trim()).forEach(s => union.add(s)));
        supportedFormats = _ALL_FMTS.filter(f => union.has(f));
        if (!supportedFormats.length) supportedFormats = _ALL_FMTS;
      }
    }

    return {
      title: spec.answers?.Heading || spec.title || 'Untitled Report Spec',
      items: cols,
      meta: {
        id: draftId,
        doc_id: spec.doc_id,
        answers: spec.answers || {},
        action_function: spec.action_function,
        saved_query: spec.saved_query || null,
        target_type: spec.target_type,
        content_filters:   spec.content_filters   || { search_string: '*' },
        shape_defaults:    spec.shape_defaults     || {},
        performance_hints: spec.performance_hints  || { page_size: 100, start_from: 0 },
        supportedFormats,
      },
    };
  },

  async patch(draftId, items) {
    await fetch(`/api/reports/drafts/${encodeURIComponent(draftId)}/columns`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...Auth.getHeaders() },
      body: JSON.stringify({ columns: items }),
    });
  },

  fieldUrl(action, mode) {
    if (_canvasActiveDraftId) {
      return `/api/templates/${encodeURIComponent(action)}/fields?draft_id=${encodeURIComponent(_canvasActiveDraftId)}`;
    }
    return `/api/templates/${encodeURIComponent(action)}/fields`;
  },
};

function _deriveColumnNameFromPath(path) {
  if (!path) return '';
  const parts = path.split('.');
  let leaf = parts[parts.length - 1].replace(/\[\]/g, '');
  
  // Standardize common leaf fields
  const leafMap = {
    displayName: 'Name',
    qualifiedName: 'Qualified Name',
    guid: 'GUID',
    description: 'Description',
    status: 'Status',
    typeName: 'Type'
  };
  
  // Convert camelCase/snake_case/kebab-case to Space Separated Title Case
  function toTitleCase(str) {
    str = str.replace(/([a-z])([A-Z])/g, '$1 $2');
    str = str.replace(/[_-]/g, ' ');
    return str.replace(/\b\w/g, c => c.toUpperCase());
  }
  
  function depluralize(str) {
    const lower = str.toLowerCase();
    if (lower.endsWith('ies')) return str.slice(0, -3) + 'y';
    if (lower.endsWith('s') && !lower.endsWith('ss')) return str.slice(0, -1);
    return str;
  }
  
  const leafLabel = leafMap[leaf] || toTitleCase(leaf);
  
  const contexts = [];
  for (let i = 0; i < parts.length - 1; i++) {
    const clean = parts[i].replace(/\[\]/g, '');
    if (['properties', 'elementHeader', 'relatedElement', 'relationshipProperties'].includes(clean)) {
      continue;
    }
    contexts.push(depluralize(toTitleCase(clean)));
  }
  
  if (contexts.length === 0) return leafLabel;
  return contexts.join(' ') + ' ' + leafLabel;
}

const _reportItemAdapter = {
  getType(col) {
    return 'Column';
  },

  getDisplayName(col) {
    return col.name || 'Unnamed Column';
  },

  getParams(col) {
    const result = {};
    if (col.key) result['Key'] = col.key;
    const fmt = col.format;
    if (fmt !== undefined && fmt !== false && fmt !== 'False' && fmt !== '') {
      result['Apply formatting'] = String(fmt);
    }
    if (col.detail_spec) result['Detail Spec'] = col.detail_spec;
    if (col.formats && col.formats !== 'ALL') result['Output types'] = col.formats;
    return result;
  },

  getNarrative(col) {
    return col.description || '';
  },

  setNarrative(col, v) {
    col.description = v;
  },

  getFieldValues(col) {
    const fmt = col.format;
    return {
      'Name': col.name || '',
      'Key': col.key || '',
      'Apply formatting': (fmt === true || fmt === 'True') ? 'True' : (fmt && fmt !== 'False') ? String(fmt) : '',
      'Detail Spec': col.detail_spec || '',
      'Output types': col.formats || 'ALL',
    };
  },

  setFieldValue(col, name, v) {
    if (name === 'Name') {
      col.name = v;
    } else if (name === 'Key') {
      col.key = v;
      if (!col.name || col.name === 'New Column' || col.name.trim() === '') {
        const derived = _deriveColumnNameFromPath(v);
        col.name = derived;
        
        // Find the active fields-section (not hidden) and update the Name input field directly in DOM
        const activeSection = document.querySelector('.fields-section:not(.hidden)');
        if (activeSection) {
          const labels = activeSection.querySelectorAll('label');
          for (const label of labels) {
            if (label.textContent.trim().startsWith('Name')) {
              const input = label.nextElementSibling;
              if (input && input.tagName === 'INPUT') {
                input.value = derived;
                // Dispatch event so the input's listener in ArtifactCanvas saves and syncs the Name change
                input.dispatchEvent(new Event('input'));
              }
              break;
            }
          }
        }
      }
    } else if (name === 'Apply formatting') {
      if (!v || v === 'False' || v === 'false') {
        col.format = false;
      } else if (v === 'True' || v === 'true') {
        col.format = true;
      } else {
        col.format = v;
      }
    } else if (name === 'Detail Spec') {
      col.detail_spec = v || null;
    } else if (name === 'Output types') {
      col.formats = v || 'ALL';
    }
  },

  makeNew(typeName, keyOverride) {
    const name = typeName || 'New Column';
    return {
      name,
      key: keyOverride || name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, ''),
      format: false,
      detail_spec: null,
      formats: 'ALL',
    };
  }
};

const ReportSpecCanvas = (() => {
  let _canvas = null;
  let _draftId = null;

  function _ensureCanvas() {
    if (_canvas) return _canvas;
    _canvas = new ArtifactCanvas({
      panelId: 'report-canvas-panel',
      handleId: 'resize-chat-report-canvas',
      cardsId: 'rcanvas-cards',
      titleId: 'rcanvas-title',
      modeButtonId: 'rcanvas-mode-btn',
      adapter: _reportAdapter,
      itemAdapter: _reportItemAdapter,
      addItemFn(doAdd) {
        // Open the column-name modal instead of the Dr.Egeria command picker
        if (typeof openAddColumnModal === 'function') {
          openAddColumnModal((name, key) => doAdd(name, key));
        } else {
          const name = prompt('Column display name (e.g. "Description", "Owner"):');
          if (name?.trim()) doAdd(name.trim());
        }
      },
      onRender(data) {
        const docId = data?.meta?.doc_id;
        const titleEl = document.getElementById('rcanvas-title');
        if (titleEl) titleEl.dataset.docId = docId || '';

        // Render the three parameter sections
        if (data?.meta && _draftId) {
          _renderParamSections(data.meta, _draftId);
        }

        // Sync format selector to a format this spec actually supports
        const fmtSel = document.getElementById('rcanvas-format-select');
        if (fmtSel && data?.meta?.supportedFormats) {
          const supported = data.meta.supportedFormats;
          if (!supported.includes(fmtSel.value)) {
            fmtSel.value = supported[0] || 'TABLE';
          }
        }

        // Toggles Generate vs. Execute buttons
        const activeId = docId || data?.meta?.id;
        document.getElementById('rcanvas-generate-btn')?.classList.remove('hidden');
        document.getElementById('rcanvas-execute-btn')?.classList.toggle('hidden', !activeId);
        fmtSel?.classList.toggle('hidden', !activeId);
        document.getElementById('rcanvas-limit-select')?.classList.toggle('hidden', !activeId);
      },
    });
    return _canvas;
  }

  async function open(draftId) {
    _draftId = draftId;
    if (typeof setContext === 'function') {
      setContext({ task: 'report_spec_elicitor', draft_id: draftId, phase: 'confirm_action' });
    }
    await _ensureCanvas().open(draftId);
    if (typeof PlanCanvas !== 'undefined' && PlanCanvas.close) PlanCanvas.close();
  }

  function close() {
    const closedId = _draftId;
    _draftId = null;
    if (typeof setContext === 'function' && typeof _ctx !== 'undefined' && _ctx?.draft_id === closedId) {
      setContext(null);
    }
    if (_canvas) _canvas.close();
    closeSchemaExplorer();
  }

  async function refresh(draftId) {
    await _ensureCanvas().refresh(draftId || _draftId);
  }

  async function addColumn() {
    await _ensureCanvas().addItem();
  }

  const explorerStyles = `
  #schema-explorer-modal details summary::marker,
  #schema-explorer-modal details summary::-webkit-details-marker {
    display: none;
  }
  #schema-explorer-modal details[open] > summary span {
    transform: rotate(90deg);
  }
  `;

  // Attribute paths, types and error text come from Egeria and the server: escape them for both
  // element text and attribute values, and never build onclick strings from them.
  function _escHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _buildSchemaTree(schemaList) {
    const tree = {};
    schemaList.forEach(item => {
      const path = item.attribute_path || '';
      const type = item.data_type || 'string';
      if (path === 'Error' || path === 'Info') return;
      
      const parts = path.split('.');
      let current = tree;
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (i === parts.length - 1) {
          current[part] = { _path: path, _type: type };
        } else {
          if (!current[part]) current[part] = {};
          current = current[part];
        }
      }
    });
    return tree;
  }

  function _renderTreeHtml(node, name = '') {
    if (node._path) {
      const path = node._path;
      const type = node._type;
      const derivedName = _deriveColumnNameFromPath(path);
      return `
        <div class="flex items-center justify-between py-1 px-2 hover:bg-slate-800/40 rounded transition-colors text-xs text-slate-300">
          <span class="font-mono text-slate-400">${_escHtml(name)} <span class="text-[10px] text-violet-400/80">(${_escHtml(type)})</span></span>
          <button class="px-1.5 py-0.5 rounded bg-violet-700 hover:bg-violet-600/80 text-[10px] text-white font-semibold transition-colors"
            data-add-path="${_escHtml(path)}" data-add-name="${_escHtml(derivedName)}">＋ Add</button>
        </div>
      `;
    }
    
    let html = `
      <details class="pl-2 border-l border-slate-800/60 my-0.5" open>
        <summary class="cursor-pointer select-none text-xs font-semibold text-slate-400 hover:text-slate-200 py-0.5 outline-none list-none flex items-center gap-1">
          <span class="text-slate-500 text-[10px] transition-transform inline-block">▸</span> ${_escHtml(name)}
        </summary>
        <div class="flex flex-col gap-0.5 pl-1.5 mt-0.5">
    `;
    for (const key in node) {
      html += _renderTreeHtml(node[key], key);
    }
    html += `
        </div>
      </details>
    `;
    return html;
  }

  function openSchemaExplorer() {
    if (!_draftId) return;
    
    let modal = document.getElementById('schema-explorer-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'schema-explorer-modal';
      modal.className = 'fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-[2000] flex items-center justify-center p-4';
      
      const styleTag = document.createElement('style');
      styleTag.textContent = explorerStyles;
      document.head.appendChild(styleTag);
      
      modal.innerHTML = `
        <div class="bg-slate-900 border border-slate-800 rounded-lg shadow-2xl w-full max-w-lg max-h-[80vh] flex flex-col overflow-hidden">
          <div class="px-4 py-3 border-b border-slate-800 flex items-center justify-between bg-slate-900/50">
            <h3 class="text-sm font-semibold text-slate-200 flex items-center gap-1.5">
              <span class="text-violet-500 text-base">📊</span> Schema Explorer
            </h3>
            <button data-explorer-close class="text-slate-500 hover:text-slate-200 text-sm transition-colors">✕</button>
          </div>
          <div class="p-4 overflow-y-auto flex-1 flex flex-col gap-2" id="schema-explorer-content">
            <p class="text-xs text-slate-500">Loading schema attributes from Egeria...</p>
          </div>
          <div class="px-4 py-3 border-t border-slate-800/60 bg-slate-900/30 flex justify-end">
            <button data-explorer-close class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-xs font-semibold text-slate-300 transition-colors">Close</button>
          </div>
        </div>
      `;
      modal.querySelectorAll('[data-explorer-close]').forEach(b => b.addEventListener('click', closeSchemaExplorer));
      // One delegated listener for every "+ Add" button the tree renders; the path and name travel in
      // data-* attributes (escaped above), read back as plain strings.
      modal.querySelector('#schema-explorer-content').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-add-path]');
        if (btn) addColumnFromExplorer(btn.dataset.addPath, btn.dataset.addName);
      });
      document.body.appendChild(modal);
    }
    
    modal.classList.remove('hidden');
    const content = document.getElementById('schema-explorer-content');
    content.innerHTML = '<p class="text-xs text-slate-400 p-2 italic animate-pulse">Running speculative discovery at depth=5 against Egeria...</p>';
    
    fetch(`/api/reports/drafts/${encodeURIComponent(_draftId)}/schema`, { headers: Auth.getHeaders() })
      .then(async r => {
        const text = await r.text();
        if (!r.ok) {
          let errText = '';
          try {
            const errJson = JSON.parse(text);
            errText = errJson.detail || errJson.message;
          } catch {
            errText = text;
          }
          throw new Error(errText || `HTTP ${r.status} ${r.statusText}`);
        }
        if (!text.trim()) return [];
        return JSON.parse(text);
      })
      .then(schemaList => {
        if (!schemaList || schemaList.length === 0 || (schemaList.length === 1 && schemaList[0].attribute_path === 'Error')) {
          content.innerHTML = `
            <div class="p-3 bg-red-950/20 border border-red-900/40 rounded flex flex-col gap-1.5 text-xs text-red-400">
              <span class="font-semibold">Discovery failed</span>
              <span>${_escHtml((schemaList?.[0]?.data_type) || 'No schema attributes returned. Please make sure Egeria has active data and is connected.')}</span>
            </div>
          `;
          return;
        }
        
        if (schemaList.length === 1 && schemaList[0].attribute_path === 'Info') {
          content.innerHTML = `
            <div class="p-3 bg-amber-950/20 border border-amber-900/40 rounded flex flex-col gap-1.5 text-xs text-amber-400">
              <span class="font-semibold">No data found</span>
              <span>Egeria returned no sample elements to inspect. Cannot dynamically discover schema attributes until an element is created.</span>
            </div>
          `;
          return;
        }
        
        const tree = _buildSchemaTree(schemaList);
        let html = '<div class="flex flex-col gap-1 pr-1">';
        for (const rootKey in tree) {
          html += _renderTreeHtml(tree[rootKey], rootKey);
        }
        html += '</div>';
        content.innerHTML = html;
      })
      .catch(err => {
        console.error("openSchemaExplorer failed:", err);
        content.innerHTML = `
          <div class="p-3 bg-red-950/20 border border-red-900/40 rounded flex flex-col gap-1.5 text-xs text-red-400">
            <span class="font-semibold">API Error</span>
            <span>Failed to fetch schema metadata from Advisor backend: ${_escHtml(err)}</span>
          </div>
        `;
      });
  }

  function closeSchemaExplorer() {
    const modal = document.getElementById('schema-explorer-modal');
    if (modal) modal.classList.add('hidden');
  }

  async function addColumnFromExplorer(key, name) {
    if (!_canvas) return;
    
    // Check if the column is already present to prevent duplicates
    const currentItems = _canvas._items;
    const exists = currentItems.some(col => col.key === key);
    if (exists) {
      alert(`Column with key "${key}" is already in the report spec.`);
      return;
    }
    
    // Create new column and add it to canvas
    const newCol = {
      name: name,
      key: key,
      format: false,
      detail_spec: null,
      formats: 'ALL',
    };
    
    currentItems.push(newCol);
    await _canvas._sync();
    _canvas._render();
  }

  return { open, close, refresh, addColumn, openSchemaExplorer, closeSchemaExplorer, addColumnFromExplorer };
})();
