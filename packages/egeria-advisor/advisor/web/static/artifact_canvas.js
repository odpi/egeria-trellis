/**
 * artifact_canvas.js — Generic ArtifactCanvas component
 *
 * Renders a split-view canvas panel alongside the chat for any structured
 * artifact that benefits from iterative refinement. The canvas shows items
 * as draggable cards with expandable fields, per-item narrative text, and
 * live sync back to the server.
 *
 * Usage:
 *   const canvas = new ArtifactCanvas({
 *     panelId:      'plan-canvas-panel',     // section element ID
 *     handleId:     'resize-chat-canvas',    // resize handle element ID
 *     cardsId:      'pcanvas-cards',         // scrollable card container ID
 *     titleId:      'pcanvas-title',         // title span element ID
 *     modeButtonId: 'pcanvas-mode-btn',      // basic/advanced toggle (optional)
 *
 *     // Data adapter — called by canvas to fetch and push data
 *     adapter: {
 *       fetch(id):         Promise<{title, items, meta}>
 *       patch(id, items):  Promise<void | {warnings?: string[], items?: Array}>
 *                          // optional return lets the server surface validation
 *                          // corrections; see onSyncWarnings below
 *       fieldUrl(type):    string   // URL for /api/templates/{type}/fields
 *     },
 *
 *     // Called when adapter.patch() returns non-empty `warnings` (optional)
 *     onSyncWarnings(warnings): void,
 *
 *     // Called when adapter.fetch() throws during refresh() (optional) — without
 *     // this the failure is console-only and the canvas silently stays empty
 *     onRefreshError(id, error): void,
 *
 *     // Item adapter — called to extract display properties from an item
 *     itemAdapter: {
 *       getType(item):        string    // e.g. item.action
 *       getDisplayName(item): string    // primary label
 *       getParams(item):      Object    // known key-value chips
 *       getNarrative(item):   string
 *       setNarrative(item, v): void
 *       makeNew(typeName):    item      // factory for Add step
 *     },
 *   });
 *
 *   canvas.open(id)     — show panel and load artifact
 *   canvas.close()      — hide panel
 *   canvas.refresh(id)  — reload and re-render
 */
class ArtifactCanvas {

  constructor(opts) {
    this._opts        = opts;
    this._id          = null;   // artifact ID (e.g. draft_id)
    this._data        = null;   // full payload from adapter.fetch()
    this._items       = [];     // adapter-normalised item list
    this._dragSrcIdx  = null;
    this._mode        = localStorage.getItem('ea_canvas_mode') || 'basic';
    // When opts.autoSync === false, edits mark the canvas dirty instead of
    // persisting immediately — the caller must call flush() explicitly
    // (e.g. from a Save button). Used for document-backed artifacts where
    // every "sync" is a full-document PUT, not a cheap incremental PATCH.
    this._dirty       = false;
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  async open(id) {
    if (!id) return;
    this._id    = id;
    this._dirty = false;
    this._fieldsCache = {}; // Clear cache on opening new draft
    const { panelId, handleId } = this._opts;
    const panel  = document.getElementById(panelId);
    const handle = document.getElementById(handleId);
    if (!panel || !handle) return;
    panel.classList.remove('hidden');
    panel.classList.add('flex');
    handle.classList.remove('hidden');
    if (window._applyCanvasWidth) window._applyCanvasWidth();
    this._syncModeButton();
    await this.refresh(id);
  }

  close() {
    this._id   = null;
    this._data = null;
    this._items = [];
    const { panelId, handleId } = this._opts;
    const panel  = document.getElementById(panelId);
    const handle = document.getElementById(handleId);
    if (panel)  { panel.classList.add('hidden');  panel.classList.remove('flex'); }
    if (handle) { handle.classList.add('hidden'); }
  }

  async refresh(id) {
    if (!id) return;
    try {
      this._data  = await this._opts.adapter.fetch(id);
      this._items = this._data.items || [];
    } catch (e) {
      console.error('ArtifactCanvas: refresh failed for', id, e);
      if (this._opts.onRefreshError) this._opts.onRefreshError(id, e);
      return;
    }
    this._render();
  }

  async addItem() {
    const doAdd = async (name, keyOverride) => {
      if (!name?.trim()) return;
      const newItem = this._opts.itemAdapter.makeNew(name.trim(), keyOverride);
      this._items.push(newItem);
      await this._sync();
      this._render();
    };
    // Custom handler supplied by the canvas (e.g. ReportSpecCanvas passes a column picker)
    if (typeof this._opts.addItemFn === 'function') {
      this._opts.addItemFn(doAdd);
      return;
    }
    // Default: Dr. Egeria command picker modal for Plan canvas
    if (typeof openCmdPicker === 'function') {
      openCmdPicker(doAdd);
    } else {
      const typeName = prompt('Command name (e.g. "Create Project", "Create Glossary Term"):');
      doAdd(typeName);
    }
  }

  toggleMode() {
    this._mode = this._mode === 'basic' ? 'advanced' : 'basic';
    localStorage.setItem('ea_canvas_mode', this._mode);
    this._syncModeButton();
    // Reload any open field sections
    document.querySelectorAll(`#${this._opts.cardsId} .fields-section:not(.hidden)`).forEach(sec => {
      const card = sec.closest('[data-ac-idx]');
      if (!card) return;
      const idx = parseInt(card.dataset.acIdx);
      if (!isNaN(idx) && this._items[idx]) {
        this._loadFields(card, this._items[idx], idx);
      }
    });
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  _render() {
    const { cardsId, titleId } = this._opts;
    const container = document.getElementById(cardsId);
    const titleEl   = document.getElementById(titleId);
    if (!container) return;

    if (titleEl && this._data) {
      titleEl.textContent    = this._data.title || '';
      titleEl.dataset.metaId = this._data.meta?.id || '';
    }

    // Let subclass or adapter enrich the toolbar (e.g. show/hide Execute)
    if (this._opts.onRender) this._opts.onRender(this._data);

    container.innerHTML = '';
    if (!this._items.length) {
      container.innerHTML =
        '<p class="text-xs text-slate-500 text-center py-6">No steps yet — describe what you want in the chat.</p>';
      return;
    }
    this._items.forEach((item, idx) => container.appendChild(this._buildCard(item, idx)));
  }

  _buildCard(item, idx) {
    const { itemAdapter } = this._opts;
    const type      = itemAdapter.getType(item);
    const dn        = itemAdapter.getDisplayName(item);
    const params    = itemAdapter.getParams(item);
    const narrative = itemAdapter.getNarrative(item);

    const paramChips = Object.entries(params)
      .filter(([, v]) => v)
      .map(([k, v]) => `<span class="text-slate-500 text-xs">✓ ${_acEsc(k)}: <em>${_acEsc(v)}</em></span>`)
      .join('');

    const statusColor = dn ? 'bg-emerald-500' : 'bg-amber-400';
    const statusTitle = dn ? 'Name set' : 'Display Name missing';

    const card = document.createElement('div');
    card.className = 'pcanvas-card bg-slate-900 rounded-lg border border-slate-700/60 overflow-hidden select-none shrink-0';
    card.dataset.acIdx = idx;
    card.draggable = true;

    // ── Note card — free-form text with no command UI ──────────────────────
    if (type === '_note') {
      card.classList.add('border-amber-800/30');
      card.innerHTML = `
        <div class="card-header flex items-center gap-1.5 px-2.5 py-2 cursor-grab active:cursor-grabbing">
          <span class="text-slate-500 text-base leading-none shrink-0 select-none">≡</span>
          <span class="text-slate-500 text-xs w-5 shrink-0 text-right">${idx + 1}.</span>
          <span class="text-amber-400/80 text-xs font-semibold flex-1">📝 Note</span>
          <button class="remove-btn text-slate-500 hover:text-red-400 text-xs px-1 transition-colors" title="Remove">✕</button>
        </div>
        <div class="px-2.5 pb-2.5">
          <textarea class="narrative-input w-full bg-amber-950/20 text-slate-300 text-sm rounded p-2
                           resize-none border border-amber-800/30 focus:outline-none focus:border-amber-500/50
                           transition-colors placeholder-slate-600"
            rows="3" placeholder="Section heading, context, or explanatory text for the plan document…"
            data-ac-idx="${idx}">${_acEsc(narrative)}</textarea>
        </div>
      `;
      card.querySelector('.remove-btn').addEventListener('click', async e => {
        e.stopPropagation();
        this._items.splice(idx, 1);
        await this._sync();
        this._render();
      });
      let _noteTimer;
      card.querySelector('.narrative-input').addEventListener('input', e => {
        clearTimeout(_noteTimer);
        _noteTimer = setTimeout(async () => {
          this._opts.itemAdapter.setNarrative(this._items[idx], e.target.value);
          await this._sync();
        }, 800);
      });
      this._attachDragHandlers(card, idx);
      return card;
    }

    card.innerHTML = `
      <div class="card-header flex items-center gap-1.5 px-2.5 py-2 cursor-grab active:cursor-grabbing">
        <span class="text-slate-500 text-base leading-none shrink-0 select-none">≡</span>
        <span class="text-slate-500 text-xs w-5 shrink-0 text-right">${idx + 1}.</span>
        <span class="ac-type text-violet-300 text-xs font-semibold flex-1 truncate">${_acEsc(type)}</span>
        <span class="w-2 h-2 rounded-full shrink-0 ${statusColor}" title="${statusTitle}"></span>
        <button class="expand-btn flex items-center gap-1.5 text-slate-400 hover:text-violet-200 px-2 py-1 rounded hover:bg-slate-700/60 transition-colors" title="Expand / collapse fields">
          <span class="expand-arrow text-lg leading-none">▾</span><span class="text-xs font-medium">Fields</span>
        </button>
        <button class="remove-btn text-slate-500 hover:text-red-400 text-xs px-1 transition-colors" title="Remove">✕</button>
      </div>
      <div class="px-2.5 pb-1.5 flex flex-col gap-0.5">
        <span class="ac-dn text-slate-200 text-sm font-medium">${dn ? _acEsc(dn) : '<span class="text-slate-500 italic">name TBD</span>'}</span>
        ${paramChips ? `<div class="flex flex-wrap gap-x-3 gap-y-0.5">${paramChips}</div>` : ''}
      </div>
      <div class="narrative-wrap px-2.5 pb-2">
        <textarea class="narrative-input w-full bg-slate-800/60 text-slate-400 text-xs rounded p-1.5
                         resize-none border border-slate-700/50 focus:outline-none focus:border-violet-500/40
                         transition-colors placeholder-slate-600"
          rows="2" placeholder="Rationale, instructions, or notes…"
          data-ac-idx="${idx}">${_acEsc(narrative)}</textarea>
      </div>
      <div class="fields-section hidden px-2.5 pb-2.5 flex flex-col gap-1.5 overflow-y-auto" style="max-height:15rem"></div>
    `;

    // ── Drag-and-drop ──────────────────────────────────────────────────────
    this._attachDragHandlers(card, idx);

    // ── Remove ─────────────────────────────────────────────────────────────
    card.querySelector('.remove-btn').addEventListener('click', async e => {
      e.stopPropagation();
      if (!confirm(`Remove "${type}"?`)) return;
      this._items.splice(idx, 1);
      await this._sync();
      this._render();
    });

    // ── Expand ─────────────────────────────────────────────────────────────
    card.querySelector('.expand-btn').addEventListener('click', e => {
      e.stopPropagation();
      const fs  = card.querySelector('.fields-section');
      const btn = card.querySelector('.expand-btn');
      const open = !fs.classList.contains('hidden');
      fs.classList.toggle('hidden', open);
      const arrow = btn.querySelector('.expand-arrow');
      if (arrow) arrow.textContent = open ? '▾' : '▴';
      if (!open) this._loadFields(card, item, idx);
    });

    // ── Narrative autosave ──────────────────────────────────────────────────
    let narrativeTimer;
    card.querySelector('.narrative-input').addEventListener('input', e => {
      clearTimeout(narrativeTimer);
      narrativeTimer = setTimeout(async () => {
        this._opts.itemAdapter.setNarrative(this._items[idx], e.target.value);
        await this._sync();
      }, 800);
    });

    return card;
  }

  // ── Drag-and-drop wiring (shared between command cards and note cards) ────

  _attachDragHandlers(card, idx) {
    card.addEventListener('dragstart', e => {
      this._dragSrcIdx = idx;
      card.classList.add('drag-ghost');
      e.dataTransfer.effectAllowed = 'move';
    });
    card.addEventListener('dragend', () => {
      card.classList.remove('drag-ghost');
      document.querySelectorAll('.pcanvas-card').forEach(c => c.classList.remove('dragging-over'));
    });
    card.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      document.querySelectorAll('.pcanvas-card').forEach(c => c.classList.remove('dragging-over'));
      if (this._dragSrcIdx !== idx) card.classList.add('dragging-over');
    });
    card.addEventListener('dragleave', () => card.classList.remove('dragging-over'));
    card.addEventListener('drop', async e => {
      e.preventDefault();
      card.classList.remove('dragging-over');
      if (this._dragSrcIdx === null || this._dragSrcIdx === idx) return;
      const [moved] = this._items.splice(this._dragSrcIdx, 1);
      this._items.splice(idx, 0, moved);
      this._dragSrcIdx = null;
      await this._sync();
      this._render();
    });
  }

  // ── Field expansion ────────────────────────────────────────────────────────

  async _loadFields(card, item, idx) {
    const section  = card.querySelector('.fields-section');
    const type     = this._opts.itemAdapter.getType(item);
    const fieldUrl = this._opts.adapter.fieldUrl(type, this._mode);
    section.innerHTML = '<p class="text-xs text-slate-500 p-1">Loading fields…</p>';

    this._fieldsCache = this._fieldsCache || {};
    let fields = this._fieldsCache[fieldUrl];
    if (!fields) {
      try {
        const r = await fetch(fieldUrl, { headers: (typeof Auth !== 'undefined' ? Auth.getHeaders() : {}) });
        if (r.ok) {
          fields = (await r.json()).fields || [];
          this._fieldsCache[fieldUrl] = fields;
        }
      } catch { /* skip */ }
    }

    const existingValues = this._opts.itemAdapter.getFieldValues(item);
    section.innerHTML = '';
    if (!fields.length) {
      section.innerHTML = '<p class="text-xs text-slate-500">No template fields found.</p>';
      return;
    }

    fields.forEach(f => {
      const rawVal     = existingValues[f.name];
      const val        = Array.isArray(rawVal) ? rawVal.join(',') : String(rawVal || '');
      const hasOptions = Array.isArray(f.valid_values) && f.valid_values.length > 0;
      const isMulti    = !!f.multi_select && hasOptions;

      const row = document.createElement('div');
      row.className = 'flex flex-col gap-0.5';

      if (isMulti) {
        // Render as a row of checkboxes; value is comma-separated string
        const selected = new Set(val.split(',').map(s => s.trim()).filter(Boolean));
        // If "ALL" is selected (or nothing), treat all as checked
        const allSelected = selected.has('ALL') || selected.size === 0;

        const lbl = document.createElement('label');
        lbl.className = 'text-xs text-slate-500 flex items-center gap-1';
        lbl.textContent = f.name + (f.required ? ' *' : '');
        row.appendChild(lbl);

        const checkRow = document.createElement('div');
        checkRow.className = 'flex flex-wrap gap-x-3 gap-y-0.5';

        const syncChecks = async () => {
          const checked = Array.from(checkRow.querySelectorAll('input[type=checkbox]'))
            .filter(c => c.checked).map(c => c.value);
          const newVal = checked.length === 0 || checked.includes('ALL') ? 'ALL' : checked.join(',');
          this._opts.itemAdapter.setFieldValue(this._items[idx], f.name, newVal);
          await this._sync();
          this._updateCardSummary(card, this._items[idx]);
        };

        f.valid_values.forEach(opt => {
          const cbLbl = document.createElement('label');
          cbLbl.className = 'flex items-center gap-1 text-xs text-slate-300 cursor-pointer';
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.value = opt;
          cb.className = 'accent-violet-500';
          cb.checked = opt === 'ALL' ? allSelected : (selected.has(opt) && !allSelected);
          cb.addEventListener('change', () => {
            // If ALL is toggled on, uncheck others; if other is toggled, uncheck ALL
            if (opt === 'ALL' && cb.checked) {
              checkRow.querySelectorAll('input[type=checkbox]').forEach(c => {
                c.checked = c.value === 'ALL';
              });
            } else if (opt !== 'ALL' && cb.checked) {
              const allCb = checkRow.querySelector('input[value="ALL"]');
              if (allCb) allCb.checked = false;
            }
            syncChecks();
          });
          cbLbl.appendChild(cb);
          cbLbl.appendChild(document.createTextNode(opt));
          checkRow.appendChild(cbLbl);
        });
        row.appendChild(checkRow);
      } else {
        const listId = hasOptions ? `dl-${Math.random().toString(36).slice(2)}` : '';
        row.innerHTML = `
          <label class="text-xs text-slate-500 flex items-center gap-1">
            ${_acEsc(f.name)}
            ${f.required ? '<span class="text-amber-400">*</span>' : ''}
          </label>
          <input type="text" value="${_acEsc(val)}"
            ${hasOptions ? `list="${listId}"` : ''}
            class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-200
                   focus:outline-none focus:border-violet-500/60 transition-colors"
            placeholder="${_acEsc(f.description || '')}" />
          ${hasOptions ? `<datalist id="${listId}">${f.valid_values.map(v => `<option value="${_acEsc(v)}"></option>`).join('')}</datalist>` : ''}
        `;
        let t;
        row.querySelector('input').addEventListener('input', e => {
          clearTimeout(t);
          t = setTimeout(async () => {
            this._opts.itemAdapter.setFieldValue(this._items[idx], f.name, e.target.value);
            await this._sync();
            this._updateCardSummary(card, this._items[idx]);
          }, 600);
        });
      }
      section.appendChild(row);
    });
  }

  _updateCardSummary(card, item) {
    const dn   = this._opts.itemAdapter.getDisplayName(item);
    const dnEl = card.querySelector('.ac-dn');
    if (dnEl) dnEl.innerHTML = dn ? _acEsc(dn) : '<span class="text-slate-500 italic">name TBD</span>';
    const dot = card.querySelector('.w-2.h-2.rounded-full');
    if (dot) dot.className = `w-2 h-2 rounded-full shrink-0 ${dn ? 'bg-emerald-500' : 'bg-amber-400'}`;
  }

  // ── Sync ──────────────────────────────────────────────────────────────────

  async _sync() {
    if (!this._id) return;
    if (this._opts.autoSync === false) {
      this._dirty = true;
      if (this._opts.onDirtyChange) this._opts.onDirtyChange(true);
      return;
    }
    try {
      const result = await this._opts.adapter.patch(this._id, this._items);
      this._applySyncResult(result);
    } catch (e) {
      console.warn('ArtifactCanvas: sync failed', e);
    }
  }

  /** Explicitly persist pending edits — for autoSync: false canvases (e.g. a Save button). */
  async flush() {
    if (!this._id || !this._dirty) return;
    const result = await this._opts.adapter.patch(this._id, this._items);
    this._applySyncResult(result);
    this._dirty = false;
    if (this._opts.onDirtyChange) this._opts.onDirtyChange(false);
  }

  /**
   * Handle a generic { warnings, items } result from adapter.patch(). Adapters
   * that don't return this shape (e.g. ReportSpecCanvas) are unaffected — result
   * is falsy/shape-mismatched and this is a no-op.
   */
  _applySyncResult(result) {
    if (!result) return;
    if (Array.isArray(result.warnings) && result.warnings.length && this._opts.onSyncWarnings) {
      this._opts.onSyncWarnings(result.warnings);
    }
    if (Array.isArray(result.items)) {
      this._items = result.items;
      this._render();
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  _syncModeButton() {
    const btn = this._opts.modeButtonId
      ? document.getElementById(this._opts.modeButtonId)
      : null;
    if (btn) btn.textContent = this._mode === 'basic' ? 'Basic' : 'Advanced';
  }
}

function _acEsc(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
