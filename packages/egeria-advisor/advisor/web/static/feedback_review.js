/* Admin → Feedback Review, over the trellis-feedback store (Phase 2, A-F1).
 *
 * Lists EA's answer items from /api/feedback/v2/items and triages them with POST /api/feedback/v2/triage.
 * Each triage is an append-only event with a status and a note.
 *
 * Every row carries its item's own id (data-item-id), and its Save button posts THAT id, captured when the
 * row is built. There is no list position anywhere: the retired PATCH /api/feedback/extended/{idx} keyed
 * triage by line index and could land on the wrong record once a line was skipped.
 *
 * Every empty or failed state says why. All text is set with textContent, never innerHTML.
 *
 *   window.FeedbackReview.load(container, { headers, vote, status })
 */
(function () {
  'use strict';

  const STATUSES = [
    ['new', 'New', 'text-sky-400'], ['triaged', 'Triaged', 'text-violet-400'],
    ['known_issue', 'Known issue', 'text-amber-400'], ['actioned', 'Actioned', 'text-emerald-400'],
    ['deferred', 'Deferred', 'text-slate-400'], ['not_an_issue', 'Not an issue', 'text-slate-500'],
  ];
  const ADMIN_ONLY = 'Admin only: sign in as an admin, or set the admin token';
  const LIMIT = 500;

  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'text') el.textContent = v;
      else if (k === 'class') el.className = v;
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids) if (kid !== null && kid !== undefined) el.append(kid);
    return el;
  }

  async function reason(r) {
    let d = null;
    try { d = await r.json(); } catch { d = null; }
    const detail = d && (d.detail || d.message);
    if (r.status === 403) return ADMIN_ONLY;
    if (detail) return typeof detail === 'string' ? detail : JSON.stringify(detail);
    return `HTTP ${r.status}`;
  }

  function stateLine(t) {
    if (!t || !t.events) return 'No triage yet.';
    const who = t.actor ? ` by ${t.actor}` : '';
    const when = t.updated_at ? ` at ${String(t.updated_at).slice(0, 19).replace('T', ' ')}` : '';
    const note = t.note ? `: ${t.note}` : '';
    return `${t.events} event(s); latest ${t.status}${who}${when}${note}`;
  }

  /** The routed intent and the UI selector (A-F1 N2). From N2 on, intent_label is the routed intent and
   *  extra.intent_selected the selector; before N2, intent_label was the selector and extra.query_type
   *  the routed intent. The presence of intent_selected tells the two apart. */
  function intents(item) {
    const extra = item.extra || {};
    if ('intent_selected' in extra) return { selected: extra.intent_selected || 'auto', routed: item.intent_label || '—' };
    return { selected: item.intent_label || 'auto', routed: extra.query_type || '—' };
  }

  /** Ids of listed votes a later vote by the same user on the same answer replaces (A-F1 N1): one judgement
   *  per answer, the latest, as /api/feedback/analysis counts it. Only votes carrying a session and an
   *  exchange id can be matched; the rest stand alone. */
  function supersededIds(items) {
    const latest = new Map();
    const key = i => {
      const ex = (i.extra || {}).exchange_id;
      return ex && i.session_id ? `${i.user_id || ''}\u0000${i.session_id}\u0000${ex}` : null;
    };
    const later = (a, b) => `${a.created_at || ''}\u0000${a.id}` > `${b.created_at || ''}\u0000${b.id}`;
    for (const i of items) {
      const k = key(i);
      if (k && (!latest.has(k) || later(i, latest.get(k)))) latest.set(k, i);
    }
    return new Set(items.filter(i => { const k = key(i); return k && latest.get(k) !== i; }).map(i => i.id));
  }

  function row(item, headers, superseded) {
    const id = item.id;                      // the ONLY triage target this row ever posts
    const t = item.triage || { status: 'new', events: 0 };
    const extra = item.extra || {};
    const vote = item.vote === 1 ? '👍' : item.vote === -1 ? '👎' : item.vote === 0 ? '😐' : '—';
    const select = h('select', { class: 'text-xs bg-slate-700 border border-slate-600 rounded px-2 py-1 w-full' });
    for (const [value, label] of STATUSES) select.append(h('option', { value, text: label, selected: value === t.status }));
    const note = h('textarea', { rows: '2', placeholder: 'Note for this change…',
      class: 'w-full text-xs bg-slate-900/60 border border-slate-700 rounded px-2 py-1 text-slate-300 resize-y' });
    const state = h('div', { class: 'text-xs text-slate-500 mt-1', 'data-triage-state': true, text: stateLine(t) });
    const save = h('button', { class: 'text-xs mt-1 px-2 py-0.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-200',
      text: 'Save' });
    save.addEventListener('click', async () => {
      save.disabled = true;
      state.textContent = 'Saving…';
      try {
        const r = await fetch('/api/feedback/v2/triage', {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...headers() },
          body: JSON.stringify({ ref_id: id, status: select.value, note: note.value }),
        });
        if (!r.ok) { state.textContent = `Not saved: ${await reason(r)}`; state.className = 'text-xs text-rose-300 mt-1'; return; }
        const data = await r.json();
        state.className = 'text-xs text-slate-500 mt-1';
        state.textContent = stateLine(data.triage);
        note.value = '';
      } catch (e) {
        state.textContent = `Not saved: ${(e && e.message) || e}`;
        state.className = 'text-xs text-rose-300 mt-1';
      } finally {
        save.disabled = false;
      }
    });
    const query = h('td', { class: 'px-3 py-2 text-xs' },
      h('div', { class: 'font-medium text-slate-200 max-w-xs', 'data-query': true, text: item.query_text || '' }),
      h('div', { class: 'text-slate-500 mt-0.5',
        text: `${item.perspective || '—'} · selected ${intents(item).selected} → routed ${intents(item).routed} [${item.routing_agent || '—'}]`
          + (item.user_id ? ` · ${item.user_id}` : ' · anonymous') }));
    if (item.response_text) {
      query.append(h('details', { class: 'mt-1' },
        h('summary', { class: 'cursor-pointer text-slate-500 hover:text-slate-300', text: 'Response ▸' }),
        h('pre', { class: 'mt-1 text-xs text-slate-400 whitespace-pre-wrap max-h-40 overflow-y-auto bg-slate-900/60 rounded p-2',
          text: item.response_text })));
    }
    if (superseded) {
      query.append(h('div', { class: 'text-amber-300/80 mt-0.5', 'data-superseded': true,
        text: 'Superseded: this user voted again on the same answer later; only the latest vote is counted.' }));
    }
    return h('tr', { class: `border-t border-slate-700/50 align-top${superseded ? ' opacity-60' : ''}`, dataset: { itemId: id } },
      h('td', { class: 'px-3 py-2 text-sm', text: vote }),
      h('td', { class: 'px-3 py-2 text-xs text-slate-400 whitespace-nowrap',
        text: item.created_at ? String(item.created_at).slice(0, 19).replace('T', ' ') : '—' }),
      query,
      h('td', { class: 'px-3 py-2 w-40' }, select),
      h('td', { class: 'px-3 py-2 min-w-[220px]' }, note, save, state));
  }

  async function load(container, opts) {
    const headers = (opts && opts.headers) || (() => ({}));
    const vote = (opts && opts.vote) || 'all';
    const status = (opts && opts.status) || 'all';
    // Don't rebuild while an admin is typing a note: the refresh would wipe it.
    if (container.contains(document.activeElement) && document.activeElement.tagName === 'TEXTAREA') return;
    const q = new URLSearchParams({ kind: 'answer', limit: String(LIMIT) });
    if (status !== 'all') q.set('status', status);
    let data;
    try {
      const r = await fetch(`/api/feedback/v2/items?${q}`, { headers: headers() });
      if (!r.ok) { container.replaceChildren(h('div', { class: 'p-4 text-xs text-slate-500', text: `Feedback not read: ${await reason(r)}` })); return; }
      data = await r.json();
    } catch (e) {
      container.replaceChildren(h('div', { class: 'p-4 text-xs text-slate-500', text: `Feedback not read: ${(e && e.message) || e}` }));
      return;
    }
    const items = (data.items || []).filter(i => vote === 'all' || String(i.vote) === vote);
    if (!items.length) {
      const why = !data.total ? 'No votes have been recorded yet.'
        : `No votes match this filter (${data.total} in all).`;
      container.replaceChildren(h('div', { class: 'p-4 text-xs text-slate-500', text: why }));
      return;
    }
    const head = h('tr', { class: 'border-b border-slate-700 text-xs text-slate-500 uppercase tracking-wider' },
      ...['', 'Time', 'Query / Routing', 'Status', 'Note'].map(t => h('th', { class: 'text-left px-3 py-2', text: t })));
    const replaced = supersededIds(data.items || []);
    const body = h('tbody', {}, ...items.map(i => row(i, headers, replaced.has(i.id))));
    const shown = data.matched > (data.items || []).length
      ? `${items.length} shown; ${data.matched} match the status filter, newest ${LIMIT} listed (${data.total} in all)`
      : `${items.length} of ${data.total} votes`;
    container.replaceChildren(
      h('table', { class: 'w-full text-sm' }, h('thead', {}, head), body),
      h('div', { class: 'px-3 py-2 text-xs text-slate-500 border-t border-slate-700/50', text: shown }));
  }

  window.FeedbackReview = { load, STATUSES, intents, supersededIds };
})();
