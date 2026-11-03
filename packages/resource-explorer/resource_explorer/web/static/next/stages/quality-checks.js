/* Quality checks on a database's Curate page (Structure tab): proposals grouped by table, each with Accept | Reject.
 *
 * Three rules from the designer's and owner's words:
 *  - every state is a cue ON the element (a glyph and a short word beside the selector), not a sentence elsewhere;
 *  - a press shows "recording…" at once and "accepted · who · date" only when the verdict row is READ BACK matching
 *    the press (the server's `recorded`), never from the click; a failure stays on the row until the next press;
 *  - a check that could not be established has NO selector, and says why on its row.
 * There is no bulk verdict: one press decides one check (the design speaks of one check at a time).
 * Each table's card also carries its Table Comparison opt-in ("watch this table for drift": the baseline-match checks,
 * proposed at the next propose run), recorded and read back the same way.
 *
 * Publish: one control for the database writes the accepted checks to Egeria as rules. Before the press it says what would
 * happen (from RE's own rows, never guessed); a press runs once; after it the state words and each check's outcome come
 * from what the server read back. A check whose schema Egeria does not hold says so and links to Contents.
 *
 * The module draws into one slot and takes its API and the signed-in person as arguments, so it loads without the
 * rest of the app and a test can drive it with a stub. Tables use colresize (report-tables.js). */
import { esc, gridHtml, attachGrids, stamp } from '/static/next/stages/report-tables.js';
import { stateEntry } from '/static/next/glyphs.js';
import { selectorHtml } from '/static/next/stages/curate-scope.js';

export const QC_COLS = {
  id: 'db-quality-checks', chrome: 60,
  columns: [
    // The defaults sum to 740 px beside 60 of chrome, so the table fits the 800 px pane without a wrapped Parameters column.
    { key: 'decision', label: 'Decision', def: 160, min: 130, align: 'left' },
    { key: 'column', label: 'Column', def: 120, min: 80, align: 'left', breakLong: true },
    { key: 'test', label: 'Test type', def: 110, min: 80, align: 'left', breakLong: true },
    { key: 'dimension', label: 'Dimension', def: 80, min: 60, align: 'left' },
    { key: 'params', label: 'Parameters', def: 180, min: 110, align: 'left', breakLong: true },
    { key: 'profile', label: 'Profile of', def: 90, min: 80 },
  ],
};

export const NO_PROPOSALS_SENTENCE =
  'No quality checks have been proposed for this database yet. Run the DatabaseQualitySurvey from Survey & analyses.';

/** A cue's glyph and tone come from the one glyph table; the WORDS are this module's (a verdict is not a survey state). */
const GLYPH_OF = { proposed: 'undecided_count', accepted: 'measured', rejected: 'cancelled', retired: 'cancelled',
  not_established: 'not_established', pending: 'running', failed: 'error' };

const day = (iso) => String(iso || '').slice(0, 10);

/** The cue under a row's selector. `kind` is one of GLYPH_OF's keys; `words` is the short word; `title` the sentence. */
export function cueHtml(kind, words, title = '') {
  const e = stateEntry(GLYPH_OF[kind] || 'unknown');
  // The tone class is a literal in each branch (the stylesheet is built from the literals it can see).
  const open = e.tone === 'text-state-ok' ? '<div class="text-state-ok text-provenance"'
    : e.tone === 'text-state-warn' ? '<div class="text-state-warn text-provenance"' : '<div class="text-ink-muted text-provenance"';
  return `${open} data-qc-cue="${esc(kind)}" title="${esc(title || words)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(words)}</div>`;
}

/** The cue for a row from its persisted fields, the press in flight, or the last failure. Pure. */
export function rowCueHtml(row, { pending = false, failure = '', me = '' } = {}) {
  if (pending) return cueHtml('pending', 'recording…');
  if (failure) return cueHtml('failed', `not recorded · ${failure}`, failure);
  const v = row.verdict;
  if (row.status === 'not_established') {
    return cueHtml('not_established', 'not established', row.reason_words || row.reason || 'no reason recorded');
  }
  if (v && (row.status === 'accepted' || row.status === 'rejected' || row.status === 'retired')) {
    const who = v.decided_by && v.decided_by === me ? 'you' : (v.decided_by || 'unknown');
    return cueHtml(row.status, `${row.status} · ${who} · ${day(v.created_at)}`,
      `${row.status} by ${v.decided_by || 'unknown'} ${v.created_at || ''} · recorded in Resource Explorer · Egeria changes when you press Publish`);
  }
  return cueHtml('proposed', 'proposed', 'no decision recorded yet');
}

/** The Accept | Reject selector for one check, or '' when the check cannot be decided. */
export function verdictSelectorHtml(row, { me = '', pending = false } = {}) {
  if (!row.selectable) return '';
  const off = !me || pending;
  const dis = me ? (pending ? 'disabled aria-busy="true"' : '')
    : 'disabled title="sign in to accept or reject a check · it needs a person to record"';
  const key = esc(row.check_key);
  return selectorHtml({
    own: row.status === 'accepted' || row.status === 'rejected' ? row.status : '', explicit: false, off, dis,
    segs: [['accepted', 'Accept'], ['rejected', 'Reject']], ariaLabel: 'Accept or reject this check',
    segAttrs: (choice) => `data-qc-act="verdict" data-qc-verdict="${choice}" data-qc-key="${key}"`,
    // Accept alone is disabled (with the reason) when the profile gave no parameters to accept; Reject stays.
    segDis: (choice, base) => (choice === 'accepted' && row.accept_blocked && !base
      ? `disabled title="${esc(row.accept_blocked)}"` : base),
    clearAttrs: '',
  });
}

/** The test type in words (TestGen's own short name), the identifier and description behind a title. A type the catalogue
 *  does not know is shown by its identifier: the absence of a name is not made up. */
export function testCellHtml(row) {
  const name = row.test_name || row.test_type;
  // Where the check comes from is said here, in the detailed description, and nowhere else on the row.
  const title = [row.test_description, row.test_name ? row.test_type : '', row.attribution].filter(Boolean).join(' · ');
  return `<span title="${esc(title || row.test_type)}">${esc(name)}</span>`;
}

/** The dimension, or a muted dash when TestGen names none for this test (a title says so). */
export const dimensionCellHtml = (row) => (row.dimension ? esc(row.dimension)
  : '<span class="text-ink-muted" title="TestGen names no data-quality dimension for this test">—</span>');

/** The parameters that have a value, in words: "threshold missing value count 0 · severity error". A long list of values
 *  (the placeholder list) is one summary with the list behind a disclosure. A parameter with no value is not listed. */
export function paramsHtml(row) {
  // A server that sends no `param_items` still gets words: the raw parameters, nulls left out, keys spaced.
  const items = row.param_items || Object.entries(row.params || {})
    .filter(([k, v]) => k !== 'profile_surveyed_at' && v !== null && v !== undefined && v !== '')
    // No JSON on screen: a list reads as its names (a record by its column), anything else as itself.
    .map(([k, v]) => ({ label: k.replace(/_/g, ' '), text: Array.isArray(v)
      ? v.map((x) => (x && typeof x === 'object' ? (x.column || x.name || Object.values(x).join(' ')) : String(x))).join(', ')
      : (v && typeof v === 'object' ? Object.entries(v).map(([a, b]) => `${a.replace(/_/g, ' ')} ${b}`).join(', ') : String(v)) }));
  if (!items.length) return '<span class="text-ink-muted">none set</span>';
  // `title` says why, where the server gives one (a column Duplicate Rows leaves out, and why it cannot be compared).
  const tip = (i) => (i.title ? ` title="${esc(i.title)}"` : '');
  // The separator keeps its dot on the line it ends (a no-break space before it), so a wrapped line never starts with " · " (QS-5).
  return items.map((i) => (i.list
    ? `<details data-qc-param-list class="inline"><summary class="cursor-pointer"${tip(i)}>${esc(i.summary)}</summary>`
      + `<span class="font-mono">${esc(i.list.join(', '))}</span></details>`
    : `<span${tip(i)}>${esc(i.label)} <span class="text-ink">${esc(i.text)}</span></span>`)).join('\u00a0· ');
}

/** The word under a row's decision cue for the rule: what the last press did to this check (`outcomes`), else what the
 *  server says its rule state is (`rules`). Nothing for a check with neither. A check waiting for Contents links there. */
export function ruleCueHtml(row, { outcomes = new Map(), rules = new Map(), canGoContents = false } = {}) {
  const got = outcomes.get(row.check_key) || rules.get(row.check_key);
  if (!got || !got.words) return '';
  const bad = ['refused', 'unverified', 'unreadable', 'duplicate', 'name_taken', 'missing_in_egeria'].includes(got.state);
  const link = got.state === 'not_in_egeria' && canGoContents
    ? ' <button type="button" data-qc-act="contents" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Contents ›</button>' : '';
  return `<div data-qc-rule="${esc(got.state || '')}" class="text-provenance ${bad ? 'text-state-warn' : 'text-ink-muted'}" title="${esc(got.words)}">`
    + `Egeria data quality rule · ${esc(got.words)}${link}</div>`;
}

/** Why the Publish button cannot be pressed now, or '' when it can. Pure; each sentence names what is missing. */
export function publishDisabledReason(pub, me) {
  if (pub.pending) return 'publishing… (a press runs once)';
  if (!me) return 'sign in to publish · it needs a person to record';
  // A press is never refused for what the preview expects ("2 already in Egeria", "nothing accepted", a database with no
  // asset recorded): the server answers each of those in words, and re-confirming is a legitimate publish.
  return '';
}

/** The words before a press. "N unchanged" alone says nothing to a person who wonders what a press does: say it. */
export function publishPreviewWords(preview) {
  const t = preview || {};
  if (t.to_publish || t.to_retire || t.waiting || t.edited || t.retry || !t.unchanged) return t.words || '';
  return `${t.unchanged} data quality rule${t.unchanged === 1 ? '' : 's'} already in Egeria · a press changes nothing`;
}

/** The words after a press, from the server's own response: its state words, the counts it acted on, and when it read back. */
export function publishResultWords(r) {
  const counts = Object.entries(r.counts || {}).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`);
  const base = r.state_words || 'no words returned';
  return [base, ...counts.filter((c) => !base.includes(c)), r.read_at ? `read back ${stamp(r.read_at)}` : ''].filter(Boolean).join(' · ');
}

/** The Publish band: the button, what a press would do (or the sentence for why it cannot be pressed), and after a press
 *  the state words. `pub` = { preview, inEgeria, readError, pending, result, failure }. Pure. */
export function publishBandHtml(pub, { me = '', canGoContents = false } = {}) {
  const why = publishDisabledReason(pub, me);
  const before = pub.readError ? `could not read what a publish would do: ${pub.readError}`
    : pub.preview ? publishPreviewWords(pub.preview) : 'reading what a publish would do…';
  const last = pub.last && pub.last.at ? `last published${pub.last.by ? ` by ${pub.last.by}` : ''} · read back ${stamp(pub.last.at)}` : '';
  const after = pub.pending ? cueHtml('pending', 'publishing…', 'the press is running')
    : pub.failure ? cueHtml('failed', `not published · ${pub.failure}`, pub.failure)
      : pub.result ? cueHtml(pub.result.state === 'published' || pub.result.state === 'retired' ? 'accepted' : 'not_established',
        publishResultWords(pub.result), pub.result.line || '')
        : last ? `<div data-qc-publish-last class="text-provenance text-ink-muted">${esc(last)}</div>` : '';
  const goto = canGoContents && pub.preview && pub.preview.waiting
    ? ' <button type="button" data-qc-act="contents" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Contents ›</button>' : '';
  return `<div data-qc-publish class="mb-s1">
    <div class="flex flex-wrap items-baseline gap-x-s2">
      <button type="button" data-qc-act="publish" ${why ? `disabled title="${esc(why)}"` : ''}${pub.pending ? ' aria-busy="true"' : ''}
        class="border border-rule-strong px-s1 text-provenance text-ink">Publish quality rules</button>
      <span data-qc-publish-line class="text-provenance text-ink-muted">${esc(before)}${goto}</span></div>
    ${why ? `<div data-qc-publish-why class="text-provenance text-ink-muted">${esc(why)}</div>` : ''}
    ${after ? `<div data-qc-publish-after>${after}</div>` : ''}</div>`;
}

/** One check as a grid row. `ui` = { pending:Set, failed:Map, me }. */
export function checkRow(row, ui) {
  const pending = ui.pending.has(row.check_key);
  const failure = ui.failed.get(row.check_key) || '';
  return {
    attrs: `data-qc-row="${esc(row.check_key)}" data-qc-status="${esc(row.status)}"`,
    tone: row.status === 'rejected' ? 'lowered' : '',
    cells: {
      decision: `${verdictSelectorHtml(row, { me: ui.me, pending })}${rowCueHtml(row, { pending, failure, me: ui.me })}`
        + ruleCueHtml(row, { outcomes: ui.outcomes, rules: ui.rules, canGoContents: ui.canGoContents }),
      column: row.column_name ? esc(row.column_name) : '<span class="text-ink-muted">whole table</span>',
      test: testCellHtml(row),
      dimension: dimensionCellHtml(row),
      params: `<span class="text-provenance">${paramsHtml(row)}</span>`,
      profile: esc(stamp(row.profile_surveyed_at)),
    },
    after: row.status === 'not_established' && row.reason
      ? `<div data-qc-reason title="${esc(row.reason)}" class="pb-[2px] pl-s2 text-provenance text-ink-muted">${esc(row.reason_words || row.reason)}</div>`
      : (row.accept_blocked
        ? `<div data-qc-reason data-qc-accept-blocked class="pb-[2px] pl-s2 text-provenance text-ink-muted">${esc(row.accept_blocked)}</div>` : ''),
  };
}

export const WATCH_SENTENCE = 'Table Comparison: the baseline-match checks (row, distinct, null, empty-string, zero and '
  + 'true counts; lowest, highest, sum, spread and fractional sum of numbers; text lengths; earliest and latest date '
  + 'and the number of distinct dates), each failing when the table moves from the profile it was accepted against. '
  + 'Proposed at the next propose run.';

/** A table's Table Comparison line: its state cue and one button. `tc` is the group's `table_comparison`. Pure. */
export function watchLineHtml(g, { me = '', pending = false, failure = '' } = {}) {
  const tc = g.table_comparison || { watched: false };
  const who = tc.by && tc.by === me ? 'you' : (tc.by || 'unknown');
  const cue = pending ? cueHtml('pending', 'recording…')
    : failure ? cueHtml('failed', `not recorded · ${failure}`, failure)
      : tc.watched ? cueHtml('accepted', `watched for drift · ${who} · ${day(tc.at)}`, WATCH_SENTENCE)
        : cueHtml('proposed', 'not watched for drift', WATCH_SENTENCE);
  const dis = !me ? 'disabled title="sign in to opt a table in · it needs a person to record"'
    : (pending ? 'disabled aria-busy="true"' : '');
  return `<div data-qc-watch class="mb-s1 flex flex-wrap items-baseline gap-x-s2">
    <button type="button" data-qc-act="watch" data-qc-schema="${esc(g.schema_name)}" data-qc-table="${esc(g.table_name)}"
      data-qc-watch="${tc.watched ? '0' : '1'}" ${dis} title="${esc(WATCH_SENTENCE)}"
      class="border border-rule-strong px-s1 text-provenance text-ink">${tc.watched ? 'Stop watching' : 'Watch for drift'}</button>
    ${cue}</div>`;
}

export function groupBodyHtml(rows, ui) {
  if (!rows.length) return '<div class="text-caveat text-ink-muted">No checks in this table match the filters.</div>';
  return gridHtml(QC_COLS, 'Proposed quality checks', rows.map((r) => checkRow(r, ui)), `data-rc-id="${QC_COLS.id}"`);
}

const countWords = (c) => [
  c.proposed ? `${c.proposed} proposed` : '', c.accepted ? `${c.accepted} accepted` : '',
  c.rejected ? `${c.rejected} rejected` : '', c.retired ? `${c.retired} retired` : '',
  c.not_established ? `${c.not_established} not established` : '',
].filter(Boolean).join(' · ');

export function groupHeadHtml(g) {
  return `<span class="font-mono text-ink">${esc(g.schema_name)}.${esc(g.table_name)}</span>
    <span data-qc-group-counts class="text-provenance text-ink-muted"> · ${esc(countWords(g.counts))}</span>`;
}

function filterSelect(name, label, options, value) {
  return `<label class="text-provenance text-ink-muted">${esc(label)}
    <select data-qc-filter="${name}" class="ml-[2px] border border-rule-strong bg-transparent text-ink">
      <option value="">all</option>${options.map((o) =>
    `<option value="${esc(o.value)}"${o.value === value ? ' selected' : ''}>${esc(o.label || o.value || 'no dimension')} (${o.count})</option>`).join('')}
    </select></label>`;
}

/** The counts line: what the whole batch holds, how many tables match the filters, and where it came from. */
export function countsLineHtml(d) {
  const filtered = d.applied.test_type || d.applied.dimension || d.applied.state;
  const tables = filtered ? `${d.groups.length} of ${d.tables} tables match` : `${d.tables} tables`;
  return `<div data-qc-counts class="mb-s1 text-caveat text-ink">${esc(countWords(d.counts))}
    <span class="text-ink-muted"> · ${esc(tables)} · proposed ${esc(stamp(d.surveyed_at))} from the value profile of ${esc(stamp(d.profile_surveyed_at))}</span></div>`;
}

export function summaryHtml(d) {
  if (!d.has_proposals) return `<div data-qc-empty class="text-caveat text-ink-muted">${esc(NO_PROPOSALS_SENTENCE)}</div>`;
  const states = d.filters.states.map((s) => ({ value: s.value, label: s.value.replace('_', ' '), count: s.count }));
  return `${countsLineHtml(d)}
    <div data-qc-publish-slot></div>
    <div class="mb-s1 flex flex-wrap items-baseline gap-x-s3 gap-y-[2px]">
      ${filterSelect('test_type', 'Test type', d.filters.test_types, d.applied.test_type)}
      ${filterSelect('dimension', 'Dimension', d.filters.dimensions, d.applied.dimension)}
      ${filterSelect('state', 'State', states, d.applied.state)}</div>
    ${d.groups.length ? '' : '<div data-qc-no-match class="text-caveat text-ink-muted">No checks match these filters.</div>'}`;
}

/** Draw the review into `slot` and wire it. `deps` = { api: { getProposals(slug, params), postVerdict(slug, key, verdict) },
 *  me: string (the signed-in person, '' when nobody is) }. Returns { reload(), destroy() }. */
export function mountQualityChecks(slot, slug, deps) {
  const doc = slot.ownerDocument;
  const canGoContents = typeof deps.openContents === 'function';
  const meNow = () => (typeof deps.me === 'function' ? deps.me() : deps.me) || '';
  const ui = { pending: new Set(), failed: new Map(), me: meNow(), outcomes: new Map(), rules: new Map(), canGoContents };
  const pub = { preview: null, inEgeria: false, readError: '', pending: false, result: null, failure: '', last: null };
  const filters = { test_type: '', dimension: '', state: '' };
  const open = new Set();                         // "schema.table" keys of groups the person has opened
  const rowsOf = new Map();                       // key -> rows as last read
  const loading = new Set();                      // keys whose rows are being read (a toggle must not start a second read)
  let summary = null;
  let token = 0;

  const keyOf = (g) => `${g.schema_name}.${g.table_name}`;
  const groupEl = (key) => [...slot.querySelectorAll('[data-qc-group]')].find((e) => e.dataset.qcGroup === key);

  const watchUi = { pending: new Set(), failed: new Map() };
  const watchHtml = (key) => {
    const g = summary && summary.groups.find((x) => keyOf(x) === key);
    return g ? watchLineHtml(g, { me: ui.me, pending: watchUi.pending.has(key), failure: watchUi.failed.get(key) || '' }) : '';
  };
  const paintRows = (key) => {
    const el = groupEl(key);
    const host = el && el.querySelector('[data-qc-body]');
    if (!host) return;
    host.innerHTML = watchHtml(key) + groupBodyHtml(rowsOf.get(key) || [], ui);
    attachGrids(host, () => QC_COLS);
  };

  async function loadRows(g) {
    const key = keyOf(g);
    const mine = token;
    if (loading.has(key)) return;
    loading.add(key);
    let d = null;
    let failure = null;
    try {
      d = await deps.api.getProposals(slug, { ...filters, schema: g.schema_name, table: g.table_name });
    } catch (err) {
      failure = err;
    } finally {
      loading.delete(key);
    }
    if (mine !== token) return;
    if (failure) {
      const el = groupEl(key);
      if (el) el.querySelector('[data-qc-body]').innerHTML =
        `<div data-qc-error class="text-caveat text-state-warn">The checks for this table could not be read: ${esc(failure.message)}</div>`;
      return;
    }
    rowsOf.set(key, d.groups[0].rows);
    paintRows(key);
  }

  function paintGroups() {
    const host = slot.querySelector('[data-qc-groups]');
    host.innerHTML = summary.groups.map((g) => `<details data-qc-group="${esc(keyOf(g))}"${open.has(keyOf(g)) ? ' open' : ''} class="border-t border-rule py-[3px]">
      <summary class="cursor-pointer">${groupHeadHtml(g)}</summary>
      <div data-qc-body class="mt-s1 min-w-0 max-w-full">${open.has(keyOf(g)) ? '<div class="text-caveat text-ink-muted">Reading the checks…</div>' : ''}</div></details>`).join('');
    host.querySelectorAll('details[data-qc-group]').forEach((el) => {
      const g = summary.groups.find((x) => keyOf(x) === el.dataset.qcGroup);
      el.addEventListener('toggle', () => {
        if (el.open) { open.add(keyOf(g)); if (!rowsOf.has(keyOf(g))) loadRows(g); else paintRows(keyOf(g)); } else open.delete(keyOf(g));
      });
      if (el.open) loadRows(g);
    });
  }

  const paintPublish = () => {
    const host = slot.querySelector('[data-qc-publish-slot]');
    if (host && deps.api.getRules && deps.api.postPublish) { ui.me = meNow(); host.innerHTML = publishBandHtml(pub, { me: ui.me, canGoContents }); };
  };
  const repaintOpenRows = () => open.forEach((k) => { if (rowsOf.has(k)) paintRows(k); });

  /** What a publish would do, from the server's own rows. A failed read is said, never shown as "nothing to publish". */
  async function readRules() {
    if (!deps.api.getRules) return;
    try {
      const d = await deps.api.getRules(slug);
      ui.rules = new Map(Object.entries(d.rules || {}));
      pub.preview = d.preview || null;
      pub.inEgeria = !!d.in_egeria;
      pub.last = d.last_published || null;
      pub.readError = d.preview ? '' : 'the server sent no preview';
    } catch (err) {
      pub.readError = err.message || 'the read failed';
    }
    paintPublish();
    repaintOpenRows();
  }

  async function pressPublish() {
    ui.me = meNow();
    if (pub.pending || publishDisabledReason(pub, ui.me)) return;
    pub.pending = true;                         // set before anything is awaited: a second press does nothing
    pub.failure = '';
    paintPublish();
    try {
      const out = await deps.api.postPublish(slug);
      pub.result = { state: out.state || '', state_words: out.state_words || '', line: out.line || '', counts: out.counts || {}, read_at: out.read_at || '' };
      ui.outcomes = new Map(Object.entries(out.outcomes || {}));
    } catch (err) {
      pub.result = null;
      pub.failure = err.message || 'the publish failed';
    }
    pub.pending = false;
    await readRules();                          // the preview is re-read after the write, never assumed
  }

  async function reload({ keepRows = false } = {}) {
    const mine = ++token;
    if (!keepRows) rowsOf.clear();
    let d;
    try {
      d = await deps.api.getProposals(slug, filters);
    } catch (err) {
      if (mine !== token) return;
      slot.innerHTML = `<div data-qc-error class="text-caveat text-state-warn">The quality checks could not be read: ${esc(err.message)}</div>`;
      return;
    }
    if (mine !== token) return;
    summary = d;
    slot.innerHTML = `<div data-qc-head>${summaryHtml(d)}</div><div data-qc-groups></div>`;
    if (!d.has_proposals) return;
    slot.querySelectorAll('[data-qc-filter]').forEach((sel) => {
      sel.addEventListener('change', () => { filters[sel.dataset.qcFilter] = sel.value; reload(); });
    });
    paintGroups();
    paintPublish();
    readRules();
    if (keepRows) open.forEach((k) => { if (rowsOf.has(k)) paintRows(k); });
  }

  async function press(btn) {
    const key = btn.dataset.qcKey;
    const verdict = btn.dataset.qcVerdict;
    if (ui.pending.has(key)) return;
    ui.pending.add(key);
    ui.failed.delete(key);
    repaintRow(key);
    try {
      const out = await deps.api.postVerdict(slug, key, verdict);
      const check = out && out.check;
      if (out && out.recorded === true && check && check.verdict && check.status === verdict) {
        replaceRow(check);
      } else {
        ui.failed.set(key, 'the verdict could not be read back');
      }
    } catch (err) {
      ui.failed.set(key, err.message || 'the write failed');
    }
    ui.pending.delete(key);
    repaintRow(key);
    refreshCounts();
  }

  function replaceRow(check) {
    for (const [gk, rows] of rowsOf) {
      const i = rows.findIndex((r) => r.check_key === check.check_key);
      if (i >= 0) { rows[i] = check; if (open.has(gk)) paintRows(gk); }
    }
  }
  function repaintRow(key) {
    for (const [gk, rows] of rowsOf) if (rows.some((r) => r.check_key === key) && open.has(gk)) paintRows(gk);
  }
  async function refreshCounts() {
    try {
      const d = await deps.api.getProposals(slug, filters);
      summary = d;
      const head = slot.querySelector('[data-qc-head]');
      if (head) head.querySelector('[data-qc-counts]').outerHTML = countsLineHtml(d);
      d.groups.forEach((g) => {
        const el = groupEl(keyOf(g));
        const c = el && el.querySelector('[data-qc-group-counts]');
        if (c) c.textContent = ` · ${countWords(g.counts)}`;
      });
    } catch (_e) { /* the row's own cue already says what was recorded; the counts catch up on the next read */ }
  }

  async function pressWatch(btn) {
    const key = `${btn.dataset.qcSchema}.${btn.dataset.qcTable}`;
    const watch = btn.dataset.qcWatch === '1';
    if (watchUi.pending.has(key) || !deps.api.postTableComparison) return;
    watchUi.pending.add(key);
    watchUi.failed.delete(key);
    paintRows(key);
    try {
      const out = await deps.api.postTableComparison(slug, btn.dataset.qcSchema, btn.dataset.qcTable, watch);
      const tc = out && out.table_comparison;
      const g = summary && summary.groups.find((x) => keyOf(x) === key);
      if (out && out.recorded === true && tc && tc.watched === watch && g) g.table_comparison = tc;
      else watchUi.failed.set(key, 'the opt-in could not be read back');
    } catch (err) {
      watchUi.failed.set(key, err.message || 'the write failed');
    }
    watchUi.pending.delete(key);
    paintRows(key);
  }

  const onClick = (ev) => {
    const watchBtn = ev.target.closest && ev.target.closest('[data-qc-act="watch"]');
    if (watchBtn && slot.contains(watchBtn) && !watchBtn.disabled) { pressWatch(watchBtn); return; }
    const contents = ev.target.closest && ev.target.closest('[data-qc-act="contents"]');
    if (contents && slot.contains(contents) && canGoContents) { deps.openContents(); return; }
    const publish = ev.target.closest && ev.target.closest('[data-qc-act="publish"]');
    if (publish && slot.contains(publish) && !publish.disabled) { pressPublish(); return; }
    const btn = ev.target.closest && ev.target.closest('[data-qc-act="verdict"]');
    if (btn && slot.contains(btn) && !btn.disabled) press(btn);
  };
  slot.addEventListener('click', onClick);
  reload();
  return { reload, destroy() { token += 1; slot.removeEventListener('click', onClick); } };
}
