/* Dashboards: Dashboard Sheets held in Egeria, read as the signed-in user (briefs B5, B7a).
 *
 * Moved out of index.html by B7a; index.html keeps the sidebar markup and a mount hook only. This file is
 * B7's from here on.
 *
 * Every read is the viewer's own (their Egeria token): a sheet, Report or spec in a zone they cannot see
 * is not shown, or shows as "restricted: not visible to you", and never runs. "No sheets", "could not
 * read" and "not visible" are three different answers and are said differently.
 *
 * Tiles run by themselves when a sheet opens (at most RUN_CONCURRENCY at a time). The server answers from
 * this viewer's result cache: fresh, or stale with its age while one background run refreshes it. Each
 * tile shows how old its answer is and has ↻ (run now). A report tile has ⚙ (adjust its parameters for
 * this view only, never saved) and, when the placement names one, ↗ (its drill-down).
 *
 * Tile kinds come from the server (advisor/egeria_dashboards.py, _tile):
 *   kpi       one number, or a saved query's answer with its as-of time;
 *   kpis      a few named numbers;
 *   chart     a Vega-Lite spec pyegeria made, embedded with the vendored vega-embed;
 *   trend     metric_trend points: the latest point's display as the headline and a sparkline. A lower
 *             bound ("≥N") is drawn as a hollow marker and is never shown as a bare number (W2);
 *   table     a TABLE placement: pyegeria's structured table, drawn by the shared renderer (trellis-ui's
 *             vendored egeria-table.js, the Portal's own): bullets, joined names, long text with "more",
 *             sub-tables under their row, no column cap, and a footer with "first N of M" / "more may
 *             exist" and every note. Every column can be dragged wider or narrower (double-click resets);
 *   markdown  sanitised with renderMarkdown (render.js), mermaid blocks drawn;
 *   empty / absent  the sentence is the answer, never a 0.
 * Nothing from the server reaches innerHTML except through renderMarkdown(), esc() or EgeriaTable.renderHtml()
 * (which escapes every value itself).
 */
(function (root) {
  'use strict';

  const RUN_CONCURRENCY = 3;
  const SPAN = { '1': 'span 1', '2': 'span 2', '3': 'span 3', 'full': '1 / -1' };
  const OUTPUT_FORMATS = ['TABLE', 'DICT', 'REPORT', 'LIST', 'MERMAID', 'SERIES', 'BAR', 'PIE', 'JSON'];
  const GE = '≥';

  let loaded = false;
  let sheets = {};

  // ── helpers ────────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function headers(json) {
    const h = (root.Auth && root.Auth.getHeaders) ? root.Auth.getHeaders() : {};
    return json ? { ...h, 'Content-Type': 'application/json' } : h;
  }
  function md(text) {
    const pre = typeof root.preprocessMarkdown === 'function' ? root.preprocessMarkdown(text || '') : (text || '');
    return root.renderMarkdown(pre);
  }
  function signInWords(r, fallback) {
    return r.status === 401 ? 'Sign in to see dashboards: they are read from Egeria as you.' : fallback;
  }
  function say(text) {
    if (typeof root.appendMessage === 'function') root.appendMessage('assistant', text);
  }
  function el(tag, cls, text) {
    const e = root.document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function badge(source) {
    if (!source) return '';
    const styles = (typeof _SPEC_SOURCE_STYLE !== 'undefined') ? _SPEC_SOURCE_STYLE : {};   // index.html
    const labels = (typeof _SPEC_SOURCE_LABEL !== 'undefined') ? _SPEC_SOURCE_LABEL : {};
    return `<span class="ml-1 px-1 rounded text-[10px] align-middle ${esc(styles[source] || 'bg-slate-700 text-slate-300')}">${esc(labels[source] || source)}</span>`;
  }
  function ageWords(cache) {
    if (!cache || cache.age_s == null) return '';
    const s = Math.round(cache.age_s);
    const age = s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
    if (cache.state === 'stale') return `answer ${age} old · refreshing`;
    if (cache.state === 'miss' || cache.state === 'refresh') return 'just run';
    return `answer ${age} old`;
  }
  async function postJson(url, body) {
    const r = await fetch(url, { method: 'POST', headers: headers(true), body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    return { r, data };
  }

  // ── list ───────────────────────────────────────────────────────────────────
  async function loadDashboards(refresh) {
    const status = root.document.getElementById('dashboard-status');
    const list = root.document.getElementById('dashboard-list');
    status.className = 'px-3 pb-1 text-xs text-slate-500 shrink-0';
    status.textContent = 'Reading Dashboard Sheets from Egeria…';
    list.innerHTML = '';
    let data;
    try {
      const r = await fetch(`/api/dashboards${refresh ? '?refresh=true' : ''}`, { headers: headers() });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail;
        status.className = 'px-3 pb-1 text-xs text-amber-400 shrink-0';
        status.textContent = signInWords(r, `Could not read Dashboard Sheets: ${detail || r.status}`);
        return;
      }
      data = await r.json();
    } catch (e) {
      status.className = 'px-3 pb-1 text-xs text-red-400 shrink-0';
      status.textContent = `Could not reach the Advisor: ${e}`;
      return;
    }
    loaded = true;
    if (data.state === 'read_failed') {
      status.className = 'px-3 pb-1 text-xs text-red-400 shrink-0';
      status.textContent = data.message;
      return;
    }
    if (data.state === 'no_sheets') {
      status.textContent = data.message;
      if (root.EADashAuthor) root.EADashAuthor.mountSidebar();   // a first sheet can still be made
      return;
    }
    if (root.EADashAuthor) root.EADashAuthor.mountSidebar();
    status.textContent = `${data.sheets.length} sheet${data.sheets.length === 1 ? '' : 's'} visible to ${data.viewer}`;
    if (data.message) {
      // pyegeria's folder notes: a configured folder not found, not visible or only partly read.
      const note = el('div', 'dash-list-notes text-amber-400', data.message);
      status.appendChild(note);
    }
    sheets = {};
    data.sheets.forEach(s => {
      sheets[s.name] = s;
      const btn = el('button', 'text-left px-2 py-1 rounded hover:bg-slate-700 transition-colors text-slate-300');
      btn.innerHTML = `<div class="text-sm">${esc(s.heading)}</div>` +
        `<div class="text-[10px] text-slate-500">${esc(s.name)} · ${Number(s.placement_count)} placement${s.placement_count === 1 ? '' : 's'}` +
        (s.breaks.length ? ' · <span class="text-amber-400">chain breaks</span>' : '') + '</div>';
      btn.title = [s.description, s.breaks.length ? 'Breaks: ' + s.breaks.join('; ') : ''].filter(Boolean).join('\n');
      btn.addEventListener('click', () => openDashboard(s.name, null));
      list.appendChild(btn);
    });
  }

  function ensureLoaded() { if (!loaded) loadDashboards(false); }

  // ── one sheet ──────────────────────────────────────────────────────────────
  async function openDashboard(name, perspective, refresh) {
    const q = new URLSearchParams();
    if (perspective) q.set('perspective', perspective);
    if (refresh) q.set('refresh', 'true');
    let data;
    try {
      const r = await fetch(`/api/dashboards/${encodeURIComponent(name)}${q.toString() ? '?' + q : ''}`, { headers: headers() });
      if (!r.ok) {
        const detail = (await r.json().catch(() => ({}))).detail;
        say(signInWords(r, `Could not read Dashboard Sheet **${name}**: ${detail || r.status}`));
        return null;
      }
      data = await r.json();
    } catch (e) {
      say(`Could not reach the Advisor: ${e}`);
      return null;
    }
    if (data.state !== 'ok') { say(data.message); return null; }
    return renderDashboard(data.sheet, { refresh: !!refresh });
  }

  function renderDashboard(sheet, opts) {
    const messages = root.document.getElementById('messages');
    const wrap = el('div', 'msg-assistant pl-3 pr-2 py-2 rounded-r-lg bg-slate-800/60');
    const summary = sheets[sheet.name] || {};
    const perspectives = summary.perspectives || [];
    let head = '<div class="flex items-center justify-between gap-2 mb-1">' +
      `<div><div class="text-xs font-semibold text-emerald-400">Dashboard · held in Egeria · read as ${esc(sheet.viewer || '')}</div>` +
      `<div class="text-base text-slate-100">${esc(sheet.heading)}</div></div><div class="flex items-center gap-1">`;
    if (perspectives.length) {
      head += '<select class="dash-perspective bg-slate-900 border border-slate-600 rounded text-xs text-slate-300 px-1 py-0.5">' +
        '<option value="">All perspectives</option>' +
        perspectives.map(p => `<option ${p === sheet.perspective ? 'selected' : ''}>${esc(p)}</option>`).join('') + '</select>';
    }
    head += '<button class="dash-refresh-all text-xs px-1 text-slate-400 hover:text-slate-200" title="Read the sheet again and run every tile now">↻ all</button></div></div>';
    if (sheet.description) head += `<div class="text-xs text-slate-400 mb-2">${esc(sheet.description)}</div>`;
    if (sheet.breaks.length) head += `<div class="text-xs text-amber-400 mb-2">Chain breaks: ${esc(sheet.breaks.join('; '))}</div>`;
    wrap.innerHTML = head;
    const sel = wrap.querySelector('.dash-perspective');
    if (sel) sel.addEventListener('change', () => openDashboard(sheet.name, sel.value || null));
    wrap.querySelector('.dash-refresh-all').addEventListener('click', () => openDashboard(sheet.name, sheet.perspective || null, true));

    if (!sheet.placements.length) {
      wrap.appendChild(el('div', 'text-xs text-slate-400',
        `This sheet has no placements${sheet.perspective ? ` for perspective ${sheet.perspective}` : ''}.`));
    }
    const grid = el('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;';
    const runs = [];
    const author = root.EADashAuthor;   // B7b, when loaded: move / remove / delete
    if (author) author.decorateSheet(wrap, sheet);
    sheet.placements.forEach(p => {
      const t = dashTile(sheet, p);
      if (author) author.decorateTile(t, sheet, p);
      grid.appendChild(t);
      if (t._run) runs.push(() => t._run({ refresh: !!(opts && opts.refresh) }));
    });
    wrap.appendChild(grid);
    if (messages) { messages.appendChild(wrap); messages.scrollTop = messages.scrollHeight; }
    wrap._ready = runQueue(runs, RUN_CONCURRENCY);
    return wrap;
  }

  /** Run thunks with at most `limit` in flight; resolves when all have settled. */
  function runQueue(thunks, limit) {
    let i = 0;
    const next = () => (i < thunks.length ? Promise.resolve(thunks[i++]()).catch(() => {}).then(next) : null);
    return Promise.all(Array.from({ length: Math.min(limit, thunks.length) }, next));
  }

  // ── a tile ─────────────────────────────────────────────────────────────────
  function dashTile(sheet, p) {
    const tile = el('div');
    tile.style.gridColumn = SPAN[p.span] || 'span 1';
    const panel = p.emphasis === 'panel';
    tile.className = 'dash-tile rounded border p-2 text-xs ' + (p.kind === 'restricted'
      ? 'border-slate-700 bg-slate-900/40 text-slate-500'
      : panel ? 'border-slate-600 bg-slate-900/70' : 'border-slate-700 bg-slate-900/50');
    tile.dataset.kind = p.kind;
    if (p.kind === 'text') {
      tile.innerHTML = `<div class="msg-body prose prose-invert text-sm max-w-none">${md(p.content || '')}</div>`;
      return tile;
    }
    const title = `<div class="font-semibold text-slate-200 ${panel ? 'text-sm' : ''}">${esc(p.title)}</div>`;
    if (p.kind === 'restricted') {
      tile.innerHTML = title + `<div class="mt-1">🔒 restricted: ${esc(p.reason || 'not visible to you')}</div>`;
      tile.title = `The ${p.restricted_element === 'ReportSpec' ? 'report spec' : 'Report'} this placement shows is in a zone you cannot see.`;
      return tile;
    }
    if (p.kind === 'sheet') {
      tile.innerHTML = title + `<div class="text-[10px] text-slate-500 mt-0.5">${esc(p.note || 'nested sheet')}</div>` +
        '<button class="dash-nested mt-1 px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-100">Open nested sheet →</button>';
      tile.querySelector('.dash-nested').addEventListener('click', () => openDashboard(p.sheet_ref, sheet.perspective || null));
      return tile;
    }
    if (p.kind === 'missing') {
      tile.innerHTML = title + `<div class="mt-1 text-amber-400">missing: ${esc(p.reason || 'the Report is no longer in Egeria')}</div>`;
      return tile;
    }
    const sq = p.saved_query;
    let html = '<div class="flex items-start justify-between gap-1">' + title +
      '<div class="flex gap-1 shrink-0 text-slate-400">' +
      (p.runnable ? '<button class="dash-refresh hover:text-slate-200" title="Run this tile now">↻</button>' +
                    '<button class="dash-adjust hover:text-slate-200" title="Adjust parameters for this view (not saved)">⚙</button>' : '') +
      (p.detail_spec ? `<button class="dash-drill hover:text-slate-200" title="Drill down: ${esc(p.detail_spec)}">↗</button>` : '') +
      '</div></div>' +
      `<div class="text-[10px] text-slate-500 mt-0.5">spec ${esc(p.spec || 'none')}${badge(p.source)}` +
      (sq ? ` · saved query · <span class="dash-sq-mode">${esc(sq.mode)}</span>` : '') +
      ' <span class="dash-age"></span></div>';
    if (p.report && p.report.description) html += `<div class="text-slate-400 mt-1">${esc(p.report.description)}</div>`;
    if (p.error) html += `<div class="text-red-400 mt-1">${esc(p.error)}</div>`;
    else if (!p.runnable) html += `<div class="text-amber-400 mt-1">${esc(p.refusal || 'This spec cannot run here.')}</div>`;
    html += '<div class="dash-adjust-panel hidden mt-1"></div><div class="dash-out mt-1"></div><div class="dash-detail hidden mt-2 border-t border-slate-700 pt-1"></div>';
    tile.innerHTML = html;
    if (p.error || !p.runnable) return tile;

    const state = { overrides: null, format: null };
    tile._run = (o) => runTile(sheet, p, tile, state, o || {});
    tile.querySelector('.dash-refresh').addEventListener('click', () => tile._run({ refresh: true }));
    tile.querySelector('.dash-adjust').addEventListener('click', () => toggleAdjust(sheet, p, tile, state));
    const drill = tile.querySelector('.dash-drill');
    if (drill) drill.addEventListener('click', () => runDetail(sheet, p, tile));
    return tile;
  }

  async function runTile(sheet, p, tile, state, opts) {
    const out = tile.querySelector('.dash-out');
    const age = tile.querySelector('.dash-age');
    out.innerHTML = '<div class="text-slate-500">Running…</div>';
    try {
      const { r, data } = await postJson(`/api/dashboards/${encodeURIComponent(sheet.name)}/placements/${p.position}/run`, {
        perspective: sheet.perspective || null, params: state.overrides, output_format: state.format,
        refresh: !!opts.refresh,
      });
      if (!r.ok) {
        out.innerHTML = `<div class="text-amber-400">${esc(signInWords(r, data.detail || String(r.status)))}</div>`;
        return;
      }
      age.textContent = ageWords(data.cache);
      age.title = data.cache && data.cache.stored_at ? `stored ${new Date(data.cache.stored_at * 1000).toLocaleString()}` : '';
      if (data.state !== 'ok') {
        out.innerHTML = `<div class="text-amber-400">${esc(data.message)}</div>`;
        return;
      }
      out.innerHTML = '';
      if (data.format_note) out.appendChild(el('div', 'text-[10px] text-amber-400', data.format_note));
      renderTileBody(out, data.tile || { kind: 'empty', message: 'No answer.' }, p);
    } catch (e) {
      out.innerHTML = `<div class="text-red-400">Could not reach the Advisor: ${esc(String(e))}</div>`;
    }
  }

  // ── tile bodies ────────────────────────────────────────────────────────────
  function renderTileBody(out, t, p) {
    switch (t.kind) {
      case 'kpi': {
        const box = el('div');
        box.appendChild(el('div', 'dash-kpi text-2xl font-semibold text-slate-100', t.display));
        if (t.capped) box.appendChild(el('div', 'text-[10px] text-amber-400', `a lower bound: the read stopped at ${t.at_least}`));
        if (t.mode || t.as_of) {
          box.appendChild(el('div', 'dash-asof text-[10px] text-slate-500',
            (t.mode === 'live' ? 'live' : 'cached') + (t.as_of ? ` · as of ${t.as_of}` : '')));
        }
        out.appendChild(box);
        if (t.markdown) out.appendChild(markdownBox(t.markdown));
        break;
      }
      case 'kpis': {
        const grid = el('div', 'dash-kpis grid gap-1');
        grid.style.gridTemplateColumns = 'repeat(auto-fit,minmax(70px,1fr))';
        t.items.forEach(i => {
          const c = el('div');
          c.appendChild(el('div', 'text-lg font-semibold text-slate-100', i.display));
          c.appendChild(el('div', 'text-[10px] text-slate-500', i.label));
          grid.appendChild(c);
        });
        out.appendChild(grid);
        break;
      }
      case 'chart': out.appendChild(chartBox(t.spec)); break;
      case 'trend': out.appendChild(trendBox(t)); break;
      case 'table': out.appendChild(tableBox(t.table)); break;
      case 'markdown': out.appendChild(markdownBox(t.markdown)); break;
      case 'empty':
      case 'absent':
        out.appendChild(el('div', `dash-${t.kind} text-slate-400 italic`, t.message));
        break;
      default:
        out.appendChild(el('div', 'text-slate-400', `A ${t.kind} answer EA cannot draw.`));
    }
    if (t.subtitle && t.kind !== 'chart') out.appendChild(el('div', 'text-[10px] text-slate-500', t.subtitle));
    if (t.unmeasured && t.unmeasured.length) {
      const n = el('div', 'dash-unmeasured text-[10px] text-amber-400 mt-1');
      n.textContent = 'Not measured: ' + t.unmeasured.join('; ');
      out.appendChild(n);
    }
  }

  // Large tables scroll inside their tile (both ways, header row kept in view) instead of growing the
  // tile without limit; the twin of the Portal's .tbl-scroll (odpi/egeria-workspaces#833). Every table
  // is wrapped; the CSS (ea_looks.css, .dash-tbl-scroll) leaves a small one laid out as before. Tables
  // with TABLE_WIDE_COLS or more columns keep a minimum column width and scroll sideways rather than
  // wrapping every few characters.
  const TABLE_WIDE_COLS = 5;
  function wrapTable(table) {
    if (table.parentElement && table.parentElement.classList.contains('dash-tbl-scroll')) return;
    const first = table.rows && table.rows[0];
    const cols = first ? first.cells.length : 0;
    const wrap = el('div', 'dash-tbl-scroll');
    if (cols >= TABLE_WIDE_COLS) {
      table.classList.add('dash-tbl-wide');
      table.style.setProperty('--tbl-cols', String(cols));
    }
    table.replaceWith(wrap);
    wrap.appendChild(table);
  }

  // ── structured TABLE (T1) ────────────────────────────────────────────────
  // Drawn by the shared renderer, unchanged from the Portal (trellis-ui/egeria-table.js). EA has no element
  // view, so no link builder: a related element's name is text, its qualified name the tooltip, and the
  // renderer's title says there is no link. The table scrolls inside the tile (ea_looks.css, .dash-etable).
  function tableBox(result) {
    const box = el('div', 'dash-etable');
    if (!root.EgeriaTable) {
      box.appendChild(el('div', 'text-amber-400', 'The table renderer (egeria-table.js) did not load, so this table cannot be drawn.'));
      return box;
    }
    box.innerHTML = root.EgeriaTable.renderHtml(result);
    root.EgeriaTable.attach(box);
    attachTableColumnResize(box);
    // "more" is trimmed where the browser did not cut the text, once the box is laid out
    setTimeout(() => { try { root.EgeriaTable.trim(box); } catch (e) { /* the toggles stay */ } }, 0);
    return box;
  }

  // Every column of a structured table can be resized (owner rule): drag a header cell's right edge,
  // double-click it to reset the table. Scoped to this box: EA's other tables are not touched.
  const COL_EDGE = 6, COL_MIN = 40;
  function nearRightEdge(th, x) {
    const r = th.getBoundingClientRect();
    return r.right - x <= COL_EDGE && r.right - x >= -2;
  }
  function headerAt(box, target) {
    const th = target && target.closest ? target.closest('th') : null;
    return th && box.contains(th) ? th : null;
  }
  function attachTableColumnResize(box) {
    const doc = box.ownerDocument;
    box.addEventListener('mousemove', (e) => {
      const th = headerAt(box, e.target);
      box.style.cursor = th && nearRightEdge(th, e.clientX) ? 'col-resize' : '';
    });
    box.addEventListener('mousedown', (e) => {
      const th = headerAt(box, e.target);
      if (!th || e.button !== 0 || !nearRightEdge(th, e.clientX)) return;
      const table = th.closest('table');
      const cells = Array.from(th.parentElement.cells);
      const widths = cells.map(c => c.getBoundingClientRect().width);
      const total = widths.reduce((a, b) => a + b, 0);
      cells.forEach((c, i) => { c.style.width = widths[i] + 'px'; });
      table.style.tableLayout = 'fixed';
      table.style.width = total + 'px';
      table.style.minWidth = '0';
      const i = cells.indexOf(th), x0 = e.clientX, w0 = widths[i];
      e.preventDefault();
      const move = (ev) => {
        const w = Math.max(COL_MIN, w0 + ev.clientX - x0);
        th.style.width = w + 'px';
        table.style.width = (total - w0 + w) + 'px';
      };
      const up = () => { doc.removeEventListener('mousemove', move); doc.removeEventListener('mouseup', up); };
      doc.addEventListener('mousemove', move);
      doc.addEventListener('mouseup', up);
    });
    box.addEventListener('dblclick', (e) => {
      const th = headerAt(box, e.target);
      if (!th || !nearRightEdge(th, e.clientX)) return;
      const table = th.closest('table');
      Array.from(th.parentElement.cells).forEach(c => { c.style.width = ''; });
      table.style.tableLayout = ''; table.style.width = ''; table.style.minWidth = '';
    });
  }

  function markdownBox(text) {
    const box = el('div', 'msg-body prose prose-invert text-xs max-w-none overflow-x-auto');
    box.innerHTML = md(text || 'No output.');
    box.querySelectorAll('table').forEach(wrapTable);
    box.querySelectorAll('code.language-mermaid').forEach(c => {
      const d = el('div', 'mermaid my-2');
      d.textContent = c.textContent;
      (c.closest('pre') || c).replaceWith(d);
    });
    const nodes = box.querySelectorAll('.mermaid');
    if (nodes.length && root.mermaid && root.mermaid.run) {
      // after insertion: mermaid needs the nodes in the document
      setTimeout(() => { try { root.mermaid.run({ nodes }); } catch (e) { /* the source stays shown */ } }, 0);
    }
    return box;
  }

  /** A Vega-Lite spec that asks to load data from anywhere is refused: a tile's data rides in the spec. */
  function asksForUrl(v) {
    if (Array.isArray(v)) return v.some(asksForUrl);
    if (v && typeof v === 'object') return Object.keys(v).some(k => k === 'url' || asksForUrl(v[k]));
    return false;
  }

  function chartBox(spec) {
    const box = el('div', 'dash-chart');
    box.style.cssText = 'background:#0f172a;padding:6px;border-radius:6px;min-height:80px;';
    if (asksForUrl(spec)) {
      box.textContent = 'Chart not drawn: it asks to load data from a URL.';
      return box;
    }
    if (typeof root.vegaEmbed !== 'function') {
      box.textContent = 'Chart not drawn: the chart library did not load.';
      return box;
    }
    root.vegaEmbed(box, spec, { theme: 'dark', actions: false, renderer: 'svg' })
      .catch(e => { box.textContent = `Chart not drawn: ${e && e.message ? e.message : e}`; });
    return box;
  }

  /** The headline is the latest point's own display ("≥N" for a lower bound). The sparkline plots exact
   *  points as dots on the line and lower bounds as hollow markers at their bound, each titled "≥N";
   *  an unmeasured point is a gap. Every point is listed with its display under the line. */
  function trendBox(t) {
    const box = el('div', 'dash-trend');
    box.appendChild(el('div', 'dash-kpi text-2xl font-semibold text-slate-100', t.latest));
    const pts = t.points.map(pt => ({ pt, y: pt.value != null ? pt.value : (pt.capped && pt.at_least != null ? pt.at_least : null) }));
    const ys = pts.map(x => x.y).filter(y => y != null);
    if (ys.length >= 2) {
      const NS = 'http://www.w3.org/2000/svg';
      const svg = root.document.createElementNS(NS, 'svg');
      svg.setAttribute('viewBox', '0 0 120 30');
      svg.setAttribute('class', 'dash-spark text-cyan-400');
      svg.setAttribute('width', '160');
      svg.setAttribute('height', '40');   // uniform scale: a marker stays a circle
      const mn = Math.min(...ys), mx = Math.max(...ys), rng = (mx - mn) || 1;
      const xy = (i, y) => [(4 + 112 * i / Math.max(1, pts.length - 1)), (26 - 22 * (y - mn) / rng)];
      let d = '', pen = false;
      pts.forEach((x, i) => {
        if (x.y == null || x.pt.capped) { pen = false; return; }   // a bound or a gap is not on the line
        const [a, b] = xy(i, x.y);
        d += `${pen ? 'L' : 'M'}${a.toFixed(1)},${b.toFixed(1)} `;
        pen = true;
      });
      if (d) {
        const path = root.document.createElementNS(NS, 'path');
        path.setAttribute('d', d.trim());
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', 'currentColor');
        path.setAttribute('stroke-width', '1.5');
        svg.appendChild(path);
      }
      pts.forEach((x, i) => {
        if (x.y == null) return;
        const [a, b] = xy(i, x.y);
        const c = root.document.createElementNS(NS, 'circle');
        c.setAttribute('cx', a.toFixed(1)); c.setAttribute('cy', b.toFixed(1)); c.setAttribute('r', '2');
        c.setAttribute('class', x.pt.capped ? 'dash-bound' : 'dash-point');
        c.setAttribute('fill', x.pt.capped ? 'none' : 'currentColor');
        c.setAttribute('stroke', 'currentColor');
        const tt = root.document.createElementNS(NS, 'title');
        tt.textContent = `${x.pt.label}: ${x.pt.display}`;
        c.appendChild(tt);
        svg.appendChild(c);
      });
      box.appendChild(svg);
    }
    const list = el('div', 'dash-trend-points text-[10px] text-slate-500');
    list.textContent = t.points.map(pt => `${pt.label} ${pt.display}`).join(' · ');
    box.appendChild(list);
    if (t.bounds) box.appendChild(el('div', 'text-[10px] text-amber-400', `${t.bounds} of ${t.points.length} points are lower bounds (${GE}N): the count stopped at its page cap`));
    else if (t.inexact) box.appendChild(el('div', 'text-[10px] text-amber-400', `${t.inexact} of ${t.points.length} points are not measured`));
    if (t.points.some(pt => pt.exact == null)) box.appendChild(el('div', 'text-[10px] text-slate-500', 'points not checked for exactness (pyegeria before 6.2.34)'));
    return box;
  }

  // ── ⚙: adjust this view's parameters (never saved) ────────────────────────
  function toggleAdjust(sheet, p, tile, state) {
    const panel = tile.querySelector('.dash-adjust-panel');
    if (!panel.classList.contains('hidden')) { panel.classList.add('hidden'); return; }
    panel.innerHTML = '';
    const params = { ...(p.params || {}), ...(state.overrides || {}) };
    const keys = Object.keys(params);
    if (!p.analytic && !keys.includes('searchString') && !keys.includes('search_string')) keys.unshift('searchString');
    const fmt = el('select', 'dash-adj-format bg-slate-900 border border-slate-600 rounded text-xs px-1');
    const current = state.format || (p.report && p.report.output_format) || 'TABLE';
    OUTPUT_FORMATS.concat(OUTPUT_FORMATS.includes(current) ? [] : [current]).forEach(f => {
      const o = el('option', null, f);
      o.value = f;
      if (f === current) o.selected = true;
      fmt.appendChild(o);
    });
    const row = (label, input) => {
      const r = el('label', 'flex items-center gap-1 mb-0.5');
      r.appendChild(el('span', 'w-24 shrink-0 text-slate-400 truncate', label));
      r.appendChild(input);
      return r;
    };
    panel.appendChild(row('Output format', fmt));
    keys.forEach(k => {
      const v = params[k];
      const inp = el('input', 'dash-adj-param flex-1 bg-slate-900 border border-slate-600 rounded text-xs px-1');
      inp.dataset.key = k;
      inp.value = v == null ? '' : (typeof v === 'string' ? v : JSON.stringify(v));
      panel.appendChild(row(k, inp));
    });
    panel.appendChild(el('div', 'text-[10px] text-slate-500', 'For this view only: not saved. Parameters that are not text are given as JSON.'));
    const actions = el('div', 'flex gap-1 mt-1');
    const apply = el('button', 'px-2 py-0.5 rounded bg-cyan-800 hover:bg-cyan-700 text-cyan-100', 'Run');
    const reset = el('button', 'px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-100', 'Reset');
    actions.appendChild(apply); actions.appendChild(reset);
    panel.appendChild(actions);
    apply.addEventListener('click', () => {
      const changed = {};
      panel.querySelectorAll('.dash-adj-param').forEach(inp => {
        const stored = (p.params || {})[inp.dataset.key];
        const shown = stored == null ? '' : (typeof stored === 'string' ? stored : JSON.stringify(stored));
        if (inp.value !== shown) changed[inp.dataset.key] = inp.value;
      });
      state.overrides = Object.keys(changed).length ? changed : null;
      state.format = fmt.value !== ((p.report && p.report.output_format) || 'TABLE') ? fmt.value : null;
      tile.querySelector('.dash-adjust').classList.toggle('text-cyan-300', !!(state.overrides || state.format));
      tile._run({});
    });
    reset.addEventListener('click', () => {
      state.overrides = null; state.format = null;
      tile.querySelector('.dash-adjust').classList.remove('text-cyan-300');
      panel.classList.add('hidden');
      tile._run({});
    });
    panel.classList.remove('hidden');
  }

  // ── ↗: drill down ──────────────────────────────────────────────────────────
  async function runDetail(sheet, p, tile) {
    const box = tile.querySelector('.dash-detail');
    if (!box.classList.contains('hidden') && box.dataset.loaded) { box.classList.add('hidden'); return; }
    box.classList.remove('hidden');
    box.innerHTML = `<div class="text-slate-500">Running ${esc(p.detail_spec)}…</div>`;
    try {
      const { r, data } = await postJson(`/api/dashboards/${encodeURIComponent(sheet.name)}/placements/${p.position}/detail`,
        { perspective: sheet.perspective || null });
      box.innerHTML = '';
      box.dataset.loaded = '1';
      box.appendChild(el('div', 'text-[10px] font-semibold text-slate-400', `Drill-down: ${p.detail_spec}`));
      if (!r.ok) { box.appendChild(el('div', 'text-amber-400', signInWords(r, data.detail || String(r.status)))); return; }
      if (data.state !== 'ok') { box.appendChild(el('div', 'text-amber-400', data.message)); return; }
      renderTileBody(box, data.tile, p);
    } catch (e) {
      box.textContent = `Could not reach the Advisor: ${e}`;
    }
  }

  root.EADashboards = { loadDashboards, ensureLoaded, openDashboard, renderDashboard, dashTile, renderTileBody, runQueue,
    tableBox,
    isLoaded: () => loaded };
  // index.html's sidebar markup calls these by name.
  root.loadDashboards = loadDashboards;
  root.openDashboard = openDashboard;
})(typeof window !== 'undefined' ? window : globalThis);
