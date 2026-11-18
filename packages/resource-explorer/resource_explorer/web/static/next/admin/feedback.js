/* Admin → Feedback — both feedback stores, badged by origin.
 *
 * Full port of classic's `loadAdminFeedbackPanel` against the same GET
 * /api/curate/feedback (see curate.py's `list_all_feedback` — it already
 * combines resource_feedback and the page-level `feedback` table, each row
 * carrying `source: "page" | "resource"`, precisely because a 2026-09-01
 * incident found a page-level submission rendering as "no feedback" when the
 * pane simply never read that store).
 *
 * The route is admin-gated (`_require_admin` in curate.py, matching
 * routes/feedback.py's `X-Admin-Token` check) because it can surface contact
 * fields on the resource side. This module reproduces classic's gate exactly
 * — same sessionStorage key (`re_admin_token`), same header — rather than
 * inventing a second mechanism: entering the token in either surface
 * authenticates both. This is NOT routed through re-api.js's `request()`,
 * because a 403 here is a distinct, expected state (not authenticated yet)
 * that must render its own explanation rather than the generic ApiError
 * path — an unauthenticated view and a genuinely empty store must not look
 * alike.
 */
import { esc } from '/static/next/app.js';

const TOKEN_KEY = 're_admin_token';

const filter = { entity_type: '', category: '', source: '', status: '', cause: '' };
let _host = null;

function authHeaders() {
  const t = sessionStorage.getItem(TOKEN_KEY) || '';
  return t ? { 'X-Admin-Token': t } : {};
}

function ratingHtml(r) {
  if (r === null || r === undefined) {
    return '<span class="text-ink-muted" title="No rating given">—</span>';
  }
  return `<span class="text-state-warn">${'★'.repeat(r)}<span class="text-ink-muted">${'★'.repeat(Math.max(0, 5 - r))}</span></span>`;
}

function sourceBadge(source) {
  if (source === 'answer') {
    return '<span class="rounded-sm border border-state-warn px-[6px] py-[1px] text-provenance text-ink" title="A rating of one answer: a chat vote, or a Questions-checklist verdict. Kept with the question, the answer and who rated it">answer</span>';
  }
  return source === 'page'
    ? '<span class="rounded-sm border border-rule-strong px-[6px] py-[1px] text-provenance text-ink" title="feedback table — the page-level widget, reachable from anywhere in the app">page</span>'
    : '<span class="rounded-sm border border-accent-deep px-[6px] py-[1px] text-provenance text-accent-ink" title="resource_feedback table — recorded from a resource\'s Curate tab">resource</span>';
}

/** +1 / 0 / -1, as the thumbs the person pressed said it. */
const VOTE_WORDS = { 1: 'helpful', 0: 'partly right', '-1': 'not helpful' };

/** An answer row's message cell: the vote, the question, any comment, and the answer folded away (it can be long). */
function answerMessageHtml(r) {
  const who = r.user_id ? ` · ${esc(r.user_id)}` : ' · anonymous';
  // A turn that was not stored (or too far back) keeps the text the browser sent, not the stored exchange.
  const sent = r.extra?.text_from === 'client'
    ? ' · <span class="text-state-warn" title="This question and answer are what the browser sent with the vote, not read from the stored chat exchange">text as sent</span>'
    : '';
  const answer = r.response_text
    ? `<details class="mt-[2px]"><summary class="cursor-pointer text-provenance text-ink-muted">answer</summary>
        <div class="mt-[2px] max-h-[12rem] overflow-auto whitespace-pre-wrap text-provenance text-ink-muted">${esc(r.response_text)}</div></details>`
    : '';
  return `<div><span class="text-ink">${esc(VOTE_WORDS[String(r.vote)] || String(r.vote))}</span>${who}${sent}</div>
    <div class="text-ink">Q: ${esc(r.query_text || '')}</div>
    ${r.message ? `<div class="text-ink-muted">${esc(r.message)}</div>` : ''}${answer}`;
}

function tokenGateHtml() {
  return `
    <h3 class="m-0 font-heading text-name font-normal text-ink">Admin credential required</h3>
    <div class="my-s3 h-px bg-rule"></div>
    <p class="max-w-[60ch] text-answer text-ink">This view reads submitted feedback, which can
      include contact details, so it is gated. <strong>This is not an empty list</strong> — it is
      a view you are not yet authenticated for.</p>
    <div class="mt-s3 flex max-w-[420px] gap-s2">
      <input data-token-input type="password" placeholder="Admin token"
        class="flex-1 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
      <button type="button" data-token-connect
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-caveat text-accent-ink">Connect</button>
    </div>
    <p class="mt-s2 max-w-[60ch] text-provenance text-ink-muted">Held in sessionStorage for this
      tab only, and shared with <code>/admin/feedback</code> and the current UI's Admin → Feedback.</p>`;
}

function chip(label, key, value) {
  const active = filter[key] === value;
  return `<button type="button" data-filter-key="${esc(key)}" data-filter-val="${esc(value)}"
    class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
      ${active ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}"
    >${esc(label)}</button>`;
}

/** Status words as an admin reads them. The vocabulary itself comes from the server (`data.statuses`). */
const STATUS_WORDS = {
  new: 'new', triaged: 'triaged', known_issue: 'known issue', actioned: 'actioned',
  deferred: 'deferred', not_an_issue: 'not an issue',
};
const statusWord = (s) => STATUS_WORDS[s] || s;

/** Why an answer failed (docs/feedback-closed-loop-design.md §2.1), as an admin reads it. The vocabulary and its
 *  precedence order come from the server (`data.causes`, `data.cause_chain`). */
const CAUSE_WORDS = {
  catalog_gap: 'catalog gap', missing_evidence: 'missing evidence', stale: 'stale', analysis_defect: 'analysis defect',
  wrong_evidence: 'wrong evidence', over_compressed: 'over-compressed', reasoning: 'reasoning',
  unclear_question: 'unclear question', not_an_issue: 'not an issue', undiagnosed: 'undiagnosed',
};
const causeWord = (c) => CAUSE_WORDS[c] || c;

/** The diagnosis controls of a rated answer: its cause and an optional secondary cause (measurement only, later in
 *  the chain), and the stored evidence behind the answer on demand. A suggestion is shown with its rule and never
 *  saved by itself: "Use" only sets the menu, and the admin's Save records it. */
function causeHtml(r, causes, chain) {
  const t = r.triage || {};
  const opts = (list, current, blank) => [`<option value=""${current ? '' : ' selected'}>${blank}</option>`,
    ...list.map((c) => `<option value="${esc(c)}"${c === current ? ' selected' : ''}>${esc(causeWord(c))}</option>`)].join('');
  const shown = t.cause
    ? `<div class="text-provenance text-ink-muted">cause: <span class="text-ink">${esc(causeWord(t.cause))}</span>${
      t.secondary_cause ? ` (+ ${esc(causeWord(t.secondary_cause))})` : ''}</div>`
    : '<div class="text-provenance text-ink-muted">not diagnosed</div>';
  return `${shown}
    <div class="mt-[2px] flex flex-wrap items-center gap-[6px]">
      <select data-triage-cause data-current="${esc(t.cause || '')}" aria-label="Cause" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink">${opts(causes, t.cause || '', '— cause —')}</select>
      <select data-triage-secondary data-current="${esc(t.secondary_cause || '')}" aria-label="Secondary cause" title="Measurement only: a later stage also at fault. Nothing routes on it." class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink">${opts(chain, t.secondary_cause || '', '— also —')}</select>
    </div>
    <details data-diagnosis class="mt-[2px]"><summary class="cursor-pointer text-provenance text-ink-muted">evidence</summary><div data-diagnosis-body class="max-w-[60ch] whitespace-normal text-provenance text-ink-muted">Loading…</div></details>`;
}

/** The evidence panel: the suggestions with their rules, the analyses the answer is charged to, and the stored
 *  compile (read as stored, never re-compiled). */
function diagnosisHtml(d) {
  if (!d.compile) return `<div data-no-compile>${esc(d.no_compile || 'No stored compile.')}</div>`;
  const m = d.compile.manifest || {};
  const sugg = (d.suggestions || []).map((s, i) => `<div data-suggestion="${esc(s.cause)}">${i === 0 ? 'Suggested' : 'Also signalled'}:
    <span class="text-ink">${esc(causeWord(s.cause))}</span> — ${esc(s.rule)}
    <button type="button" data-use-cause="${esc(s.cause)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink-muted hover:text-ink">Use</button></div>`).join('')
    || (() => {
      // Only the causes that were judged may be said to be absent: "signals no stale section" is false comfort when
      // no section declared an as-of date to judge by.
      // A cause judged for some sections only (`partial`) is still listed; its Not judged line names the rest.
      const skipped = new Set((d.not_judged || []).filter((n) => !n.partial).map((n) => n.cause));
      const judged = ['catalog_gap', 'missing_evidence', 'stale', 'over_compressed'].filter((c) => !skipped.has(c));
      return `<div>Nothing suggested: the manifest signals none of ${esc(judged.map(causeWord).join(', '))}. The cause is a reading of the answer.</div>`;
    })();
  const notJudged = (d.not_judged || []).map((n) => `<div data-not-judged="${esc(n.cause)}">Not judged:
    <span class="text-ink">${esc(causeWord(n.cause))}</span> — ${esc(n.why)}</div>`).join('');
  const charged = (d.charge_set || []).map((c) => `<span class="font-mono text-ink">${esc(c.analysis_id)}</span> ${esc(c.words)}`).join('; ')
    || 'no analysis (a catalog gap, or nothing was offered)';
  // Closed-loop step 2a (Q2): a missing-evidence or analysis-defect diagnosis raises one gap, on the analysis picked
  // here; the first in the charge set's order unless another is picked.
  const picker = (d.charge_set || []).length > 1
    ? `<div class="mt-[2px]">Gap goes to: <select data-route-analysis aria-label="Analysis the gap is raised on" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink">${
      d.charge_set.map((c) => `<option value="${esc(c.analysis_id)}">${esc(c.analysis_id)} (${esc(c.words)})</option>`).join('')}</select>
      <span>used for missing evidence and analysis defect</span></div>`
    : '';
  const keys = (list, f) => (list || []).map(f).join(', ') || 'none';
  return `${sugg}${notJudged}
    <div class="mt-[2px]">Charged to: ${charged}</div>${picker}
    <div class="mt-[2px]">Coverage: ${esc((m.coverage || {}).kind || 'unknown')}${(m.coverage || {}).question ? ` — “${esc(m.coverage.question)}”` : ''}</div>
    <div>Packed: ${esc(keys(m.packed, (p) => `${p.key} · ${p.rung}`))}</div>
    <div>Gaps: ${esc(keys(m.gaps, (g) => `${g.key} · ${g.reason || g.state || 'not judged'}`))}</div>
    <div>Dropped: ${esc(keys(m.dropped, (x) => x.key))} · Deferred: ${esc(keys(m.deferred, (x) => x.key))}</div>
    <details class="mt-[2px]"><summary class="cursor-pointer">compile ${esc(d.compile.compile_id)} · what the model was given</summary>
      <div class="mt-[2px] max-h-[12rem] overflow-auto whitespace-pre-wrap">${esc(d.compile.text || '(no text stored)')}</div></details>`;
}

/** Rated answers by cause, and by the analyses the diagnosed ones are charged to (closed-loop design §3.1, §8 Q6).
 *  Thumbs-down only, the votes the loop is about. A fetch that fails says so rather than showing zeros. */
/** Which votes the summary counts: thumbs-down by default, the votes the loop is about. "Partly right" is its own
 *  choice, since a partly-right answer is also a failure worth diagnosing. */
let summaryVote = '-1';
const SUMMARY_VOTES = [['-1', 'thumbs-down'], ['0', 'partly right'], ['all', 'all votes']];

async function causeSummaryHtml() {
  let data;
  try {
    const res = await fetch(`/api/feedback/causes?vote=${encodeURIComponent(summaryVote)}`, { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    return `<p class="mt-s3 text-caveat text-state-warn" data-cause-summary-error>Could not load the counts by cause: ${esc(err.message)}</p>`;
  }
  const counts = data.counts || {};
  const line = [...(data.causes || []), data.undiagnosed || 'undiagnosed']
    .filter((c) => counts[c]).map((c) => `${esc(causeWord(c))} <span class="tnum text-ink">${counts[c]}</span>`).join(' · ')
    || `no ${esc((SUMMARY_VOTES.find(([v]) => v === summaryVote) || [])[1] || 'votes')} yet`;
  const toggle = SUMMARY_VOTES.map(([v, label]) => `<button type="button" data-summary-vote="${v}"
    class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
      ${v === summaryVote ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}">${label}</button>`).join('');
  const rows = (data.by_analysis || []).map((a) => `<tr class="border-b border-rule" data-cause-analysis="${esc(a.analysis_id)}">
    <td class="py-[3px] pr-s4 font-mono text-ink">${esc(a.analysis_id)}</td>
    <td class="tnum py-[3px] pr-s4 text-ink">${a.total}</td>
    <td class="py-[3px] text-ink-muted">${esc(Object.entries(a.causes).map(([c, n]) => `${causeWord(c)} ${n}`).join(', '))}</td></tr>`).join('');
  return `<div class="mt-s3" data-cause-summary>
    <h4 class="m-0 mb-[2px] font-heading text-subtab font-normal text-ink">Rated answers by cause</h4>
    <div class="mb-[2px] flex flex-wrap items-center gap-[6px]">${toggle}</div>
    <p class="m-0 text-caveat text-ink-muted" data-cause-counts>${line} <span class="text-provenance">(${esc(data.populations?.counts || 'all time')})</span></p>
    ${rows ? `<table class="mt-[2px] text-caveat"><thead><tr class="text-caps uppercase tracking-caps text-ink-muted">
      <th class="pr-s4 text-left font-normal">Analysis</th><th class="pr-s4 text-left font-normal">Diagnosed</th>
      <th class="text-left font-normal">Causes</th></tr></thead><tbody>${rows}</tbody></table>` : ''}
    <p class="mt-[2px] max-w-[65ch] text-provenance text-ink-muted">By analysis: ${esc(data.populations?.by_analysis || 'the diagnosed answers')}.
      An answer is counted against every analysis it depended on: packed, absent, dropped, or deferred though the
      catalog names it for the question.
      ${data.uncharged ? `${data.uncharged} diagnosed answer${data.uncharged === 1 ? ' is' : 's are'} charged to no analysis (a catalog gap, an unclear question, or no stored compile).` : ''}
      ${data.limited ? `Read from the newest ${data.read} rated answers only.` : ''}</p></div>`;
}

/** The triage cell of a page or answer item: its status and who set it, a status control with a note, and its
 *  history on demand. Every change is a new event in trellis-feedback (append-only): the note belongs to that
 *  change and nothing earlier is overwritten. */
/** The causes charged to one analysis the admin picks (closed-loop step 2a, Q2; 2c adds stale's schedule proposal).
 *  Only the gap causes can be re-routed. */
const CHARGED_CAUSES = new Set(['missing_evidence', 'analysis_defect', 'stale']);
const REROUTE_CAUSES = new Set(['missing_evidence', 'analysis_defect']);

/** What the last Save said about a route that is not stored (a cause no consumer takes yet, or a failure to retry),
 *  by item id; shown once on the next render. */
const routeNotes = new Map();

/** Where each diagnosis was routed (closed-loop step 2a), and any note from the last Save. */
function routesHtml(r) {
  const lines = (r.routes || []).map((x) => `<div data-route="${esc(x.cause)}" class="max-w-[32ch] whitespace-normal text-provenance text-ink-muted">${esc(x.words)}${
    REROUTE_CAUSES.has(x.cause) && (x.consumer === 'gap' || x.consumer === 'none')
      ? ` <button type="button" data-reroute="${esc(x.cause)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink-muted hover:text-ink">Re-route</button>`
      : ''}</div>`);
  const note = routeNotes.get(String(r.id));
  if (note) {
    routeNotes.delete(String(r.id));
    lines.push(`<div data-route-note class="max-w-[32ch] whitespace-normal text-provenance text-ink-muted">${esc(note)}</div>`);
  }
  return lines.join('');
}

function triageHtml(r, statuses, causes = [], chain = []) {
  const t = r.triage || {};
  const current = r.triage_status || t.status || 'new';
  const by = t.actor ? ` · ${esc(t.actor)}` : '';
  const when = t.updated_at ? ` · ${esc(t.updated_at.slice(0, 16).replace('T', ' '))}` : '';
  const last = t.note ? `<div class="max-w-[28ch] whitespace-normal text-provenance text-ink-muted">“${esc(t.note)}”</div>` : '';
  const options = statuses.map((s) => `<option value="${esc(s)}"${s === current ? ' selected' : ''}>${esc(statusWord(s))}</option>`).join('');
  // Page and answer items are trellis-feedback items; Curate rows are triaged by reference (their own route).
  const src = r.source === 'resource' ? 'resource_feedback' : 'item';
  return `<div data-triage="${esc(r.id)}" data-triage-source="${src}">
    <div><span class="text-ink">${esc(statusWord(current))}</span><span class="text-provenance text-ink-muted">${by}${when}</span></div>
    ${last}
    <div class="mt-[2px] flex flex-wrap items-center gap-[6px]">
      <select data-triage-status data-current="${esc(current)}" aria-label="Status" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink">${options}</select>
      <input data-triage-note type="text" placeholder="Note (optional)" aria-label="Note"
        class="w-[16ch] rounded-sm border border-rule-strong bg-transparent px-[6px] py-[1px] text-caveat text-ink" />
      <button type="button" data-triage-save
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-caveat text-accent-ink">Save</button>
    </div>
    ${r.source === 'answer' && causes.length ? causeHtml(r, causes, chain) : ''}
    ${r.source === 'answer' ? `<div data-route-default class="max-w-[32ch] whitespace-normal text-provenance text-ink-muted"></div>${routesHtml(r)}` : ''}
    <div data-triage-error class="text-provenance text-state-warn"></div>
    ${t.events ? `<details data-triage-history class="mt-[2px]"><summary class="cursor-pointer text-provenance text-ink-muted">history (${esc(String(t.events))})</summary><div data-history-body class="text-provenance text-ink-muted">Loading…</div></details>` : ''}
  </div>`;
}

const LOOK_NAMES = [['dark', 'Dark'], ['light', 'Light'], ['paper', 'Paper']];

/** Which look people use, from the activity rows each change writes (RULING-THEMES-PORTAL-DARK-LIGHT.md section 1).
 *  Says what the numbers cannot know: someone who never touched the control is on Dark and leaves no row. A fetch
 *  that fails is said to have failed, not shown as zeros. */
async function lookCountsHtml() {
  let data;
  try {
    const res = await fetch('/api/feedback/look-counts', { headers: authHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    return `<p class="mt-s3 text-caveat text-state-warn" data-look-counts-error>Could not load the look counts: ${esc(err.message)}</p>`;
  }
  const rows = LOOK_NAMES.map(([id, name]) => {
    const c = (data.looks || {})[id] || { people: 0, switched_away: 0 };
    return `<tr class="border-b border-rule" data-look-row="${id}">
      <td class="py-[3px] pr-s4 text-ink">${name}</td>
      <td class="tnum py-[3px] pr-s4 text-ink">${c.people}</td>
      <td class="tnum py-[3px] text-ink">${c.switched_away}</td></tr>`;
  }).join('');
  return `<div class="mt-s3" data-look-counts>
    <h4 class="m-0 mb-[2px] font-heading text-subtab font-normal text-ink">Which look people use</h4>
    <table class="text-caveat"><thead><tr class="text-caps uppercase tracking-caps text-ink-muted">
      <th class="pr-s4 text-left font-normal">Look</th>
      <th class="pr-s4 text-left font-normal">People on it now</th>
      <th class="text-left font-normal">Switched away from it</th></tr></thead>
      <tbody>${rows}</tbody></table>
    <p class="mt-[2px] max-w-[65ch] text-provenance text-ink-muted">Counts people who have changed the look at least once
      (<span class="tnum">${data.people_who_changed || 0}</span> people, <span class="tnum">${data.changes || 0}</span> changes).
      Anyone who never touched the control is on Dark and leaves no row, so Dark's number is a floor, not a total.
      Comments sent from the look menu are in the list below, subject "Look: ...".</p></div>`;
}

/** What has been typed or chosen in each row and not saved yet, so a reload (another row's Save, a filter) does not
 *  throw it away. `except` is the row just saved, whose draft is now in the store. */
function drafts(except) {
  const out = {};
  _host?.querySelectorAll('[data-triage]').forEach((cell) => {
    const id = cell.dataset.triage;
    if (id === except) return;
    const d = {};
    cell.querySelectorAll('[data-triage-status], [data-triage-cause], [data-triage-secondary]').forEach((el) => {
      if (el.value !== el.dataset.current) d[[...el.attributes].find((a) => a.name.startsWith('data-triage-')).name] = el.value;
    });
    const note = cell.querySelector('[data-triage-note]')?.value;
    if (note) d['data-triage-note'] = note;
    if (Object.keys(d).length) out[id] = d;
  });
  return out;
}

function restoreDrafts(saved) {
  Object.entries(saved).forEach(([id, d]) => {
    const cell = [..._host.querySelectorAll('[data-triage]')].find((c) => c.dataset.triage === id);
    if (!cell) return;
    Object.entries(d).forEach(([attr, value]) => {
      const el = cell.querySelector(`[${attr}]`);
      if (el) el.value = value;
    });
  });
}

async function reload({ saved = null } = {}) {
  if (!_host) return;
  const kept = drafts(saved);
  const params = new URLSearchParams();
  if (filter.entity_type) params.set('entity_type', filter.entity_type);
  if (filter.category) params.set('category', filter.category);
  if (filter.source) params.set('source', filter.source);
  if (filter.status) params.set('status', filter.status);
  if (filter.cause) params.set('cause', filter.cause);

  let res;
  try {
    res = await fetch(`/api/curate/feedback?${params}`, { headers: authHeaders() });
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load feedback: ${esc(err.message)}</p>`;
    return;
  }
  if (res.status === 403) {
    _host.innerHTML = tokenGateHtml();
    bindTokenGate();
    return;
  }
  if (!res.ok) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load feedback: HTTP ${res.status}</p>`;
    return;
  }
  const data = await res.json();

  const rows = data.feedback || [];
  const counts = data.counts || { resource: { total: 0, resources: 0 }, page: { total: 0 } };
  const total = (counts.resource?.total || 0) + (counts.page?.total || 0) + (counts.answer?.total || 0);
  const types = [...new Set(rows.filter((r) => r.source !== 'page').map((r) => r.entity_type).filter(Boolean))].sort();
  const cats = [...new Set(rows.map((r) => r.category).filter(Boolean))].sort();
  const statuses = data.statuses || ['new'];
  const causes = data.causes || [];
  const chain = data.cause_chain || [];

  let empty;
  if (data.filtered) {
    empty = `<p class="py-s4 text-center text-caveat text-ink-muted">No feedback matches this filter.
      Clear it to see all ${total} (${counts.page?.total || 0} page, ${counts.resource?.total || 0} resource, ${counts.answer?.total || 0} answer).</p>`;
  } else if (total === 0) {
    empty = `<p class="py-s4 text-center text-caveat text-ink-muted">No feedback has been left anywhere yet.<br>
      <span class="text-ink-muted">No page-level widget report, no resource's Curate tab row, and no rated answer.</span></p>`;
  } else {
    empty = '<p class="py-s4 text-center text-caveat text-ink-muted">Nothing rendered for this page, though feedback exists — try Refresh.</p>';
  }

  const body = rows.length ? `<table class="w-full text-left">
    <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">When</th>
      <th class="pb-s2 pr-s3 font-normal">Source</th>
      <th class="pb-s2 pr-s3 font-normal">Resource / Page</th>
      <th class="pb-s2 pr-s3 font-normal">Category</th>
      <th class="pb-s2 pr-s3 font-normal">Rating</th>
      <th class="pb-s2 pr-s3 font-normal">Message</th>
      <th class="pb-s2 font-normal">Status</th>
    </tr></thead>
    <tbody>${rows.map((r) => {
      const isPage = r.source === 'page';
      const isAnswer = r.source === 'answer';
      let context;
      if (isAnswer) {
        context = r.slug
          ? `<span class="text-ink-muted">${esc(r.entity_type || '')}</span> <span class="font-mono text-ink">${esc(r.slug)}</span>`
          : '<span class="text-ink-muted" title="No resource was in scope when this answer was rated">no resource</span>';
      } else {
        context = isPage
          ? (r.page ? `<span class="font-mono text-ink">${esc(r.page)}</span>` : '<span class="text-ink-muted" title="No page recorded with this submission">—</span>')
          : `<span class="text-ink-muted">${esc(r.entity_type || '')}</span> <span class="font-mono text-ink">${esc(r.entity_slug || '')}</span>`;
      }
      const status = triageHtml(r, statuses, causes, chain);
      return `<tr class="border-b border-rule hover:bg-paper-surface">
        <td class="whitespace-nowrap py-s2 pr-s3 font-mono text-provenance text-ink-muted">${esc((r.created_at || '').slice(0, 16).replace('T', ' '))}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${sourceBadge(r.source)}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${context}</td>
        <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(r.category || '—')}</td>
        <td class="whitespace-nowrap py-s2 pr-s3">${ratingHtml(r.rating)}</td>
        <td class="max-w-[32ch] py-s2 pr-s3 text-caveat text-ink">${isAnswer ? answerMessageHtml(r) : esc(r.message || '')}</td>
        <td class="whitespace-nowrap py-s2 align-top text-caveat">${status}</td>
      </tr>`;
    }).join('')}</tbody></table>` : empty;

  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">💬 Feedback</h3>
    <p class="mt-[2px] max-w-[65ch] text-caveat text-ink-muted">
      ${counts.page?.total || 0} page item${(counts.page?.total || 0) === 1 ? '' : 's'} (the widget on
      any page) + ${counts.resource?.total || 0} resource item${(counts.resource?.total || 0) === 1 ? '' : 's'}
      across ${counts.resource?.resources || 0} resource${(counts.resource?.resources || 0) === 1 ? '' : 's'}
      (a resource's Curate tab) + ${counts.answer?.total || 0} rated answer${(counts.answer?.total || 0) === 1 ? '' : 's'}
      (chat votes and checklist verdicts) = ${total} total. Shown together, badged by origin — not merged.
      Checklist verdicts left before 2026-11-09 are page items whose message starts "[agree]", "[partly]" or "[disagree]".
    </p>
    ${await lookCountsHtml()}
    ${causes.length ? await causeSummaryHtml() : ''}
    <div class="my-s3 flex flex-wrap items-center gap-[6px]">
      ${chip('All sources', 'source', '')}
      ${chip('Page', 'source', 'page')}
      ${chip('Resource', 'source', 'resource')}
      ${chip('Answer', 'source', 'answer')}
      <span class="px-[2px] text-ink-muted">|</span>
      ${chip('All resource types', 'entity_type', '')}
      ${types.map((t) => chip(t, 'entity_type', t)).join('')}
      ${cats.length ? '<span class="px-[2px] text-ink-muted">|</span>' : ''}
      ${cats.length ? chip('Any category', 'category', '') : ''}
      ${cats.map((c) => chip(c, 'category', c)).join('')}
    </div>
    <div class="mb-s3 flex flex-wrap items-center gap-[6px]">
      ${chip('Any status', 'status', '')}
      ${statuses.map((s) => chip(statusWord(s), 'status', s)).join('')}
    </div>
    ${causes.length ? `<div class="mb-s3 flex flex-wrap items-center gap-[6px]">
      ${chip('Any cause', 'cause', '')}
      ${[...causes, data.undiagnosed || 'undiagnosed'].map((c) => chip(causeWord(c), 'cause', c)).join('')}
      ${filter.cause ? '<span class="text-provenance text-ink-muted">Only rated answers are diagnosed, so a cause filter shows answers only.</span>' : ''}
    </div>` : ''}
    ${limitedHtml(data)}
    <div class="max-h-[46vh] overflow-auto rounded-sm border border-rule">${body}</div>
    ${goneHtml(data.gone || [])}`;

  restoreDrafts(kept);
  bindFilters();
  bindTriage();
}

/** Curate feedback rows deleted after they were triaged (Q7): their history is kept, and shown here, until an
 *  admin clears it. Deleting the row on its resource's Curate tab never removes it. */
/** A status filter reads the newest N Curate rows and filters after (their status is held by reference). When that
 *  read came back full, older rows with this status may be missing; say so above the list. */
function limitedHtml(data) {
  if (!data.resource_limited) return '';
  return `<p class="mb-s3 max-w-[65ch] text-provenance text-state-warn" data-resource-limited>Resource items: only the
    newest ${esc(String(data.resource_read))} were checked for this status, so older ones with it may be missing.</p>`;
}

function goneHtml(gone) {
  if (!gone.length) return '';
  return `<div class="mt-s3" data-gone-list>
    <h4 class="m-0 text-caps uppercase tracking-caps text-ink-muted">Item gone (${gone.length})</h4>
    <p class="mt-[2px] max-w-[65ch] text-provenance text-ink-muted">Resource feedback that was triaged and has since
      been deleted. Its status history is kept until you clear it.</p>
    ${gone.map((g) => {
      const t = g.triage || {};
      return `<div class="mt-[2px] flex flex-wrap items-center gap-[6px] text-caveat" data-gone="${esc(g.ref_id)}">
        <span class="font-mono text-ink-muted">${esc(g.source)} ${esc(g.ref_id)}</span>
        <span class="text-ink">last: ${esc(statusWord(t.status || 'new'))}</span>
        ${t.actor ? `<span class="text-ink-muted">· ${esc(t.actor)}</span>` : ''}
        ${t.note ? `<span class="text-ink-muted">“${esc(t.note)}”</span>` : ''}
        <button type="button" data-gone-clear
          class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[1px] text-caveat text-ink-muted hover:text-ink">Clear history</button>
        <span data-gone-error class="text-provenance text-state-warn"></span>
      </div>`;
    }).join('')}
  </div>`;
}

/** Save one status change: PATCH /api/feedback/{id} with the status and note, then reload so the row, its counts
 *  and any active status filter all agree with the store. A refusal is said on the row, not swallowed. */
function bindTriage() {
  if (!_host) return;
  _host.querySelectorAll('[data-gone]').forEach((rowEl) => {
    rowEl.querySelector('[data-gone-clear]')?.addEventListener('click', async (e) => {
      const button = e.currentTarget;
      button.disabled = true;
      try {
        const res = await fetch(`/api/feedback/resource_feedback/${encodeURIComponent(rowEl.dataset.gone)}`,
          { method: 'DELETE', headers: authHeaders() });
        if (!res.ok) {
          let detail = `HTTP ${res.status}`;
          try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
          throw new Error(detail);
        }
      } catch (err) {
        rowEl.querySelector('[data-gone-error]').textContent = `Not cleared: ${err.message}`;
        button.disabled = false;
        return;
      }
      reload();
    });
  });
  _host.querySelectorAll('[data-triage]').forEach((cell) => {
    const id = cell.dataset.triage;
    // A Curate row is triaged by reference through its own route; a library item by its id.
    const url = cell.dataset.triageSource === 'resource_feedback'
      ? `/api/feedback/resource_feedback/${encodeURIComponent(id)}`
      : `/api/feedback/${encodeURIComponent(id)}`;
    const error = cell.querySelector('[data-triage-error]');
    cell.querySelector('[data-triage-save]')?.addEventListener('click', async (e) => {
      const button = e.currentTarget;
      const triageStatus = cell.querySelector('[data-triage-status]').value;
      const note = cell.querySelector('[data-triage-note]').value.trim();
      const payload = { triage_status: triageStatus, note };
      // A diagnosis is sent only when it changed: re-sending the current cause with every status change would
      // record a fresh diagnosis each time, and the history would no longer say when the cause was decided.
      const causeSel = cell.querySelector('[data-triage-cause]');
      const secondSel = cell.querySelector('[data-triage-secondary]');
      if (causeSel && (causeSel.value !== causeSel.dataset.current || secondSel.value !== secondSel.dataset.current)) {
        payload.cause = causeSel.value;
        payload.secondary_cause = secondSel.value;
        // Sent only when the admin actually picked: an untouched picker shows the first charged analysis, which is
        // not stale's default, so sending it would override the server's choice.
        const picked = cell.querySelector('[data-route-analysis]');
        if (picked && picked.dataset.touched && payload.cause) payload.analysis_id = picked.value;
      }
      // Nothing changed: an event with the same status, no note and no new cause would only add a line to the history.
      if (triageStatus === cell.querySelector('[data-triage-status]').dataset.current && !note && !('cause' in payload)) {
        error.textContent = 'Nothing to save: change the status or the cause, or add a note.';
        return;
      }
      button.disabled = true;
      error.textContent = '';
      let res;
      try {
        res = await fetch(url, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify(payload),
        });
      } catch (err) {
        error.textContent = `Not saved: ${err.message}`;
        button.disabled = false;
        return;
      }
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
        error.textContent = `Not saved: ${detail}`;
        button.disabled = false;
        return;
      }
      // A stored route is listed on the row after the reload; one that is not stored (not routed yet, or a failure
      // to retry) is said once.
      try {
        const route = (await res.json()).route;
        if (route && (route.state === 'skipped' || route.state === 'failed' || route.state === 'already')) {
          routeNotes.set(String(id), route.words);
        }
      } catch { /* no body: nothing to say */ }
      reload({ saved: id });
    });
    bindDiagnosis(cell, id);
    cell.querySelector('[data-triage-history]')?.addEventListener('toggle', async (e) => {
      const box = e.currentTarget;
      if (!box.open || box.dataset.loaded) return;
      const body = box.querySelector('[data-history-body]');
      try {
        const res = await fetch(url, { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const item = await res.json();
        box.dataset.loaded = '1';
        body.innerHTML = (item.history || []).map((ev) => `<div>${esc((ev.created_at || '').slice(0, 16).replace('T', ' '))}
          · <span class="text-ink">${esc(statusWord(ev.status))}</span>${ev.cause ? ` · cause ${esc(causeWord(ev.cause))}${
          ev.secondary_cause ? ` (+ ${esc(causeWord(ev.secondary_cause))})` : ''}` : ''}${ev.actor ? ` · ${esc(ev.actor)}` : ''}${
          ev.note ? ` — ${esc(ev.note)}` : ''}</div>`).join('') || 'No changes recorded.';
      } catch (err) {
        body.textContent = `Could not load the history: ${err.message}`;
      }
    });
  });
}

/** Load the evidence panel once, whoever asks first (opening it, picking a charged cause, or Re-route). */
async function loadDiagnosis(cell, id) {
  const box = cell.querySelector('[data-diagnosis]');
  if (!box) return null;
  if (!box._diagnosis) {
    box._diagnosis = (async () => {
      const body = box.querySelector('[data-diagnosis-body]');
      try {
        const res = await fetch(`/api/feedback/${encodeURIComponent(id)}/diagnosis`, { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const d = await res.json();
        box.dataset.loaded = '1';
        body.innerHTML = diagnosisHtml(d);
        body.querySelectorAll('[data-use-cause]').forEach((b) => b.addEventListener('click', () => {
          cell.querySelector('[data-triage-cause]').value = b.dataset.useCause;
          showDefault(cell, id);
        }));
        body.querySelector('[data-route-analysis]')?.addEventListener('change', (ev) => {
          ev.currentTarget.dataset.touched = '1';
          showDefault(cell, id);
        });
        return d;
      } catch (err) {
        body.textContent = `Could not load the evidence: ${err.message}`;
        box._diagnosis = null;
        return null;
      }
    })();
  }
  return box._diagnosis;
}

/** Before Save, say which analysis a missing-evidence or analysis-defect diagnosis will charge (the architect's P2:
 *  a Save cannot move a route afterwards, so the default must not be silent). */
async function showDefault(cell, id) {
  const line = cell.querySelector('[data-route-default]');
  const cause = cell.querySelector('[data-triage-cause]')?.value || '';
  if (!line) return;
  if (!CHARGED_CAUSES.has(cause) || cause === cell.querySelector('[data-triage-cause]').dataset.current) {
    line.textContent = '';
    return;
  }
  const d = await loadDiagnosis(cell, id);
  const charge = (d && d.charge_set) || [];
  // The server's default: for stale, the first section the manifest found stale; otherwise the first charged.
  const staleSections = ((d && d.suggestions) || []).find((s) => s.cause === 'stale')?.sections || [];
  const fallback = (cause === 'stale' && charge.find((c) => staleSections.includes(c.analysis_id))) || charge[0];
  const picked = cell.querySelector('[data-route-analysis]');
  const verb = cause === 'stale' ? 'Will propose a schedule for' : 'Will charge';
  if (!charge.length) {
    line.textContent = `${verb}: no analysis (nothing is charged), so nothing is routed.`;
  } else if (picked && picked.dataset.touched && picked.value !== fallback.analysis_id) {
    line.textContent = `${verb}: ${picked.value}.`;
  } else {
    line.textContent = `${verb}: ${fallback.analysis_id} (default)${charge.length > 1 ? '; pick another in the evidence panel' : ''}.`;
  }
}

function bindDiagnosis(cell, id) {
  cell.querySelector('[data-diagnosis]')?.addEventListener('toggle', (e) => {
    if (e.currentTarget.open) loadDiagnosis(cell, id);
  });
  cell.querySelector('[data-triage-cause]')?.addEventListener('change', () => showDefault(cell, id));
  cell.querySelectorAll('[data-reroute]').forEach((b) => b.addEventListener('click', async () => {
    const error = cell.querySelector('[data-triage-error]');
    const box = cell.querySelector('[data-diagnosis]');
    const d = await loadDiagnosis(cell, id);
    const picker = cell.querySelector('[data-route-analysis]');
    if (!d || !picker) {
      error.textContent = d ? 'Only one analysis is charged, so there is nowhere else to route it.'
        : 'Could not load the evidence to pick an analysis from.';
      return;
    }
    if (box && !box.open) {
      box.open = true;
      error.textContent = 'Pick the analysis in the evidence panel, then press Re-route again.';
      return;
    }
    b.disabled = true;
    try {
      const res = await fetch(`/api/feedback/${encodeURIComponent(id)}/routes/${encodeURIComponent(b.dataset.reroute)}/reroute`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ analysis_id: picker.value }),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
        throw new Error(detail);
      }
    } catch (err) {
      error.textContent = `Not re-routed: ${err.message}`;
      b.disabled = false;
      return;
    }
    reload({ saved: id });
  }));
}

function bindFilters() {
  if (!_host) return;
  _host.querySelectorAll('[data-summary-vote]').forEach((b) => b.addEventListener('click', () => {
    summaryVote = b.dataset.summaryVote;
    reload();
  }));
  _host.querySelectorAll('[data-filter-key]').forEach((b) => b.addEventListener('click', () => {
    // Setting entity_type has no meaning for page-sourced rows; clearing an
    // active page-only source filter here would silently show rows that
    // don't support the entity_type facet — leave source alone and let the
    // empty state say so, same as classic.
    filter[b.dataset.filterKey] = b.dataset.filterVal;
    reload();
  }));
}

function bindTokenGate() {
  if (!_host) return;
  const connect = _host.querySelector('[data-token-connect]');
  const input = _host.querySelector('[data-token-input]');
  if (!connect || !input) return;
  const submit = () => {
    const t = input.value.trim();
    if (!t) return;
    sessionStorage.setItem(TOKEN_KEY, t);
    reload();
  };
  connect.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
}

export async function renderFeedback(host) {
  _host = host;
  filter.entity_type = '';
  filter.category = '';
  filter.source = '';
  filter.status = '';
  filter.cause = '';
  summaryVote = '-1';
  await reload();
}
