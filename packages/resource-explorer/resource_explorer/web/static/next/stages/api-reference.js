/* The API reference of a repository: operations from up to three sources, each labelled by where it came from.
 *
 *   committed spec     an OpenAPI/Swagger file committed in the repository (read at the survey)
 *   running instance   a document RE fetched, read-only, from a URL a person registered
 *   derived from code  built from the stored route registrations -- NOT a published contract
 *
 * Kind-agnostic on purpose. The payload is `interfaces[]`, each with a `kind` (http_api now), a `source` and
 * `operations[]` (name, group, summary, input_schema ... ; http ones also method + path). Everything here that
 * is HTTP-specific (the method badge, parameters, request body, responses) is drawn only when the operation has
 * it, so an MCP interface (tools / prompts / resources with a name, description and input schema) draws through
 * the same list with its `input_schema` in place of parameters. See `OP_FIELDS` and `inputSchemaHtml`.
 *
 * Own vanilla rendering. No Swagger UI / Redoc: a CDN dependency in a catalog tool that reads private services
 * is the wrong trade for a list of operations and a schema tree, and a vendored copy of either is ~1 MB for
 * far more interaction than this needs.
 *
 * State is shown on the element (a glyph from the one glyph table plus a short word); the full sentence is the
 * `title` and, where the owner asked for it by name, the source label line.
 */
import { ago } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import { state, esc, $, railFrame, railClaim, railStale, ensureRailShowing } from '/static/next/app.js';
import { getApiReference, putApiInstance, refetchApiInstance, clearApiInstance } from '/static/re-api.js';

/** The fields of an operation this drawing knows. A new kind adds fields here, not a second renderer. */
export const OP_FIELDS = ['parameters', 'request_body', 'responses', 'security', 'input_schema', 'handler'];

const GROUP_CAP = 300;
const SCHEMA_PROP_CAP = 60;

const SOURCE_CUE = {
  committed_spec: { state: 'measured', word: 'committed spec' },
  running_instance: { state: 'measured', word: 'running instance' },
  derived_from_code: { state: 'partial', word: 'from code' },
};

/** A source as a cue: glyph + short word, the sentence on demand (title). */
export function sourceCue(source, title = '') {
  const c = SOURCE_CUE[source] || { state: 'unclassified', word: source };
  const e = stateEntry(c.state);
  return `<span data-api-cue="${esc(source)}" class="${e.tone}" title="${esc(title || c.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(c.word)}</span>`;
}

const mutedCue = (state, word, title) => {
  const e = stateEntry(state);
  return `<span class="${e.tone}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};

const num = (n) => `<span class="tnum">${esc(n)}</span>`;

/* ── schemas ────────────────────────────────────────────────────────── */

export function schemaTypeText(s) {
  if (!s || typeof s !== 'object') return '';
  if (s.$ref) return String(s.$ref).split('/').pop();
  if (s.type === 'array') return `${schemaTypeText(s.items) || 'array'}[]`;
  for (const k of ['anyOf', 'oneOf', 'allOf']) {
    if (Array.isArray(s[k]) && s[k].length) {
      const parts = s[k].filter((p) => !(p && p.type === 'null')).map(schemaTypeText).filter(Boolean);
      return parts.length ? parts.join(k === 'allOf' ? ' + ' : ' | ') : k;
    }
  }
  if (Array.isArray(s.enum)) return `enum ${s.enum.slice(0, 6).map(String).join(' | ')}${s.enum.length > 6 ? ' …' : ''}`;
  const t = Array.isArray(s.type) ? s.type.filter((x) => x !== 'null').join(' | ') : (s.type || '');
  return s.format && t ? `${t} (${s.format})` : t;
}

/** Names a schema type text refers to that exist in `schemas` (so they can be opened one level at a time). */
function refNamesIn(s, schemas, out = new Set()) {
  if (!s || typeof s !== 'object') return out;
  if (s.$ref) { const n = String(s.$ref).split('/').pop(); if (schemas[n]) out.add(n); return out; }
  if (s.items) refNamesIn(s.items, schemas, out);
  for (const k of ['anyOf', 'oneOf', 'allOf']) if (Array.isArray(s[k])) s[k].forEach((p) => refNamesIn(p, schemas, out));
  return out;
}

/** One level of a schema: its properties with type, required marker and description; a property that names
 *  another schema gets a button to open it in place. Bounded (SCHEMA_PROP_CAP) and said when cut. */
export function schemaTreeHtml(schemas, name) {
  const sch = (schemas || {})[name];
  if (!sch) return `<div class="text-caps text-chrome-muted">${esc(name)} is not defined in this document</div>`;
  const req = new Set(Array.isArray(sch.required) ? sch.required : []);
  const props = Object.entries(sch.properties || {});
  const head = `<div class="text-caps text-chrome-muted">${esc(schemaTypeText(sch) || 'schema')}${sch.description ? ` · ${esc(String(sch.description).slice(0, 160))}` : ''}</div>`;
  if (!props.length) {
    const alt = ['allOf', 'anyOf', 'oneOf'].find((k) => Array.isArray(sch[k]));
    const refs = [...refNamesIn(sch, schemas)];
    return `<div data-schema-tree="${esc(name)}">${head}${alt ? `<div class="text-caps text-chrome-muted">${esc(alt)}: ${refs.map((r) => schemaOpenBtn(r)).join(' · ') || esc(schemaTypeText(sch))}</div>`
      : '<div class="text-caps text-chrome-muted">no properties listed</div>'}</div>`;
  }
  const rows = props.slice(0, SCHEMA_PROP_CAP).map(([k, v]) => {
    const refs = [...refNamesIn(v, schemas)];
    const typeHtml = refs.length ? refs.map(schemaOpenBtn).join(' ') + (v.type === 'array' ? ' []' : '') : esc(schemaTypeText(v));
    return `<li class="flex flex-wrap items-baseline gap-x-s2 py-[1px] text-caps" data-schema-prop="${esc(k)}">
      <span class="font-mono text-chrome-ink">${esc(k)}</span>
      <span class="text-chrome-muted">${typeHtml}</span>
      ${req.has(k) ? '<span class="text-chrome-ink" title="required">required</span>' : ''}
      ${v && v.description ? `<span class="min-w-0 text-chrome-muted [overflow-wrap:anywhere]">${esc(String(v.description).slice(0, 140))}</span>` : ''}
      <span data-schema-slot></span></li>`;
  }).join('');
  const cut = props.length > SCHEMA_PROP_CAP ? `<li class="text-caps text-chrome-muted">${num(SCHEMA_PROP_CAP)} of ${num(props.length)} properties shown</li>` : '';
  return `<div data-schema-tree="${esc(name)}">${head}<ul class="m-0 list-none p-0 pl-s2">${rows}${cut}</ul></div>`;
}

const schemaOpenBtn = (n) => `<button type="button" data-schema-open="${esc(n)}" class="cursor-pointer bg-transparent p-0 font-mono text-accent-on-dark underline">${esc(n)}</button>`;

/** A schema name as text, or as a button when the document defines it. `Pet[]` opens `Pet`. */
function schemaRef(iface, name) {
  if (!name) return '<span class="text-chrome-muted">none stated</span>';
  const base = name.replace(/\[\]$/, '');
  return (iface.schemas || {})[base]
    ? `${schemaOpenBtn(base)}${name.endsWith('[]') ? ' []' : ''}<span data-schema-slot></span>`
    : `<span class="font-mono text-chrome-ink">${esc(name)}</span>`;
}

/** An operation's own argument schema (an MCP tool's inputSchema, drawn from its JSON schema inline). */
export function inputSchemaHtml(schema) {
  const props = Object.entries((schema && schema.properties) || {});
  if (!props.length) return '<div class="text-caps text-chrome-muted">takes no arguments</div>';
  const req = new Set(Array.isArray(schema.required) ? schema.required : []);
  return `<ul class="m-0 list-none p-0 pl-s2">${props.map(([k, v]) => `<li class="flex flex-wrap items-baseline gap-x-s2 text-caps">
    <span class="font-mono text-chrome-ink">${esc(k)}</span><span class="text-chrome-muted">${esc(schemaTypeText(v))}</span>
    ${req.has(k) ? '<span class="text-chrome-ink">required</span>' : ''}
    ${v && v.description ? `<span class="text-chrome-muted">${esc(String(v.description).slice(0, 140))}</span>` : ''}</li>`).join('')}</ul>`;
}

/* ── one operation ──────────────────────────────────────────────────── */

const sec = (title, html) => `<div class="mt-s1"><div class="text-caps uppercase tracking-caps text-chrome-muted">${esc(title)}</div>${html}</div>`;

export function operationBodyHtml(op, iface) {
  const out = [];
  if (op.description) out.push(`<div class="text-caps text-chrome-ink [overflow-wrap:anywhere]">${esc(String(op.description).slice(0, 600))}</div>`);
  if (op.operation_id) out.push(`<div class="text-caps text-chrome-muted">operation id <span class="font-mono">${esc(op.operation_id)}</span></div>`);
  if (op.parameters && op.parameters.length) {
    out.push(sec('Parameters', `<ul class="m-0 list-none p-0 pl-s2">${op.parameters.map((p) => `<li class="flex flex-wrap items-baseline gap-x-s2 text-caps" data-op-param="${esc(p.name)}">
      <span class="font-mono text-chrome-ink">${esc(p.name)}</span><span class="text-chrome-muted">${esc(p.in)}</span>
      <span class="text-chrome-muted">${esc(p.type || '')}</span>
      <span class="${p.required ? 'text-chrome-ink' : 'text-chrome-muted'}">${p.required ? 'required' : 'optional'}</span>
      ${p.description ? `<span class="min-w-0 text-chrome-muted [overflow-wrap:anywhere]">${esc(String(p.description).slice(0, 140))}</span>` : ''}</li>`).join('')}</ul>`));
  }
  if (op.request_body) {
    const rb = op.request_body;
    out.push(sec('Request body', `<div class="pl-s2 text-caps text-chrome-muted" data-op-body>${rb.required ? 'required' : 'optional'}
      · ${esc((rb.content_types || []).join(', ') || 'content type not stated')} · ${schemaRef(iface, rb.schema_name)}</div>`));
  }
  if (op.input_schema) out.push(sec('Input', inputSchemaHtml(op.input_schema)));
  if (op.responses && op.responses.length) {
    out.push(sec('Responses', `<ul class="m-0 list-none p-0 pl-s2">${op.responses.map((r) => `<li class="flex flex-wrap items-baseline gap-x-s2 text-caps" data-op-response="${esc(r.status)}">
      <span class="font-mono text-chrome-ink">${esc(r.status)}</span><span class="text-chrome-muted">${esc(r.description || '')}</span>
      ${r.schema_name ? schemaRef(iface, r.schema_name) : ''}</li>`).join('')}</ul>`));
  }
  if (iface.kind === 'http_api' && iface.source !== 'derived_from_code') {
    out.push(sec('Security', `<div class="pl-s2 text-caps text-chrome-muted">${op.security && op.security.length
      ? esc(op.security.join(', ')) : (Object.keys(iface.security_schemes || {}).length ? 'none declared for this operation' : 'none declared')}</div>`));
  }
  if (iface.source === 'derived_from_code') {
    out.push(sec('From the code', `<div class="pl-s2 text-caps text-chrome-muted" data-op-code>
      <span class="font-mono text-chrome-ink">${esc(op.handler || '')}</span>${op.handler_signature ? ` <span class="font-mono">${esc(op.handler_signature)}</span>` : ''}
      ${op.location ? `<div class="font-mono">${esc(op.location)}</div>` : ''}
      <div>the code states the method, path and handler; its parameters, bodies, responses and auth are not derived</div></div>`));
  }
  return out.join('') || '<div class="text-caps text-chrome-muted">nothing more stated for this operation</div>';
}

function operationSummaryHtml(op) {
  const method = op.method
    ? `<span data-method="${esc(op.method)}" class="inline-block min-w-[3.6em] shrink-0 rounded-sm border border-chrome-line px-[4px] text-center font-mono text-caps text-chrome-ink">${esc(op.method)}</span>`
    : `<span class="inline-block shrink-0 rounded-sm border border-chrome-line px-[4px] font-mono text-caps text-chrome-muted">${esc(op.kind_detail || op.kind || '')}</span>`;
  const name = op.path || op.name;
  return `<summary class="flex cursor-pointer flex-wrap items-baseline gap-x-s2 text-caps">
    ${method}<span class="font-mono text-chrome-ink [overflow-wrap:anywhere]">${esc(name)}</span>
    ${op.deprecated ? `<span class="text-state-warn-on-dark" title="marked deprecated in the document">${stateEntry('partial').glyph} deprecated</span>` : ''}
    ${op.summary ? `<span class="min-w-0 text-chrome-muted [overflow-wrap:anywhere]">${esc(op.summary)}</span>` : ''}</summary>`;
}

/** Operations grouped by tag (or by file for code), in the document's own order. */
export function groupOps(iface) {
  const order = Object.keys(iface.groups || {});
  const by = new Map();
  for (const op of iface.operations || []) {
    const g = op.group || 'untagged';
    if (!by.has(g)) by.set(g, []);
    by.get(g).push(op);
  }
  const names = [...by.keys()].sort((a, b) => {
    const ia = order.indexOf(a); const ib = order.indexOf(b);
    if (ia >= 0 && ib >= 0) return ia - ib;
    if (ia >= 0) return -1;
    if (ib >= 0) return 1;
    return a.localeCompare(b);
  });
  return names.map((g) => ({ name: g, description: (iface.groups || {})[g] || '', ops: by.get(g) }));
}

export function interfaceHtml(iface, { recheck = false } = {}) {
  const groups = groupOps(iface);
  const noun = iface.noun || 'operations';
  const identity = [iface.spec_version, iface.title ? `“${iface.title}”` : '', iface.version].filter(Boolean).join(' ');
  const base = (iface.base_paths || []).length ? ` · served under ${esc(iface.base_paths.join(', '))}` : '';
  const schemes = Object.entries(iface.security_schemes || {});
  return `<div data-api-interface="${esc(iface.source)}">
    <div data-api-label class="text-caps text-chrome-ink">${sourceCue(iface.source, iface.source_phrase)} · ${esc(iface.source_phrase)}${
      recheck ? ' · <button type="button" data-api-recheck class="cursor-pointer bg-transparent p-0 text-accent-on-dark underline">re-check now</button><span data-api-recheck-status class="text-chrome-muted"></span>' : ''}</div>
    <div class="text-caps text-chrome-muted">${num(iface.count)} ${esc(noun)}${iface.total_operations > iface.count ? ` of ${num(iface.total_operations)}` : ''}${
      identity ? ` · ${esc(identity)}` : ''}${base}${schemes.length ? ` · security ${esc(schemes.map(([k, v]) => `${k} (${v})`).join(', '))}` : ''}</div>
    ${(iface.notes || []).map((n) => `<div class="text-caps text-chrome-muted">${esc(n)}</div>`).join('')}
    <div class="mt-s2 flex flex-col gap-s1">
    ${groups.map((g, gi) => `<details data-api-group="${esc(g.name)}" class="border-b border-chrome-line-soft pb-s1" ${gi < 2 ? 'open' : ''}>
      <summary class="cursor-pointer text-subtab text-chrome-ink"><span class="tnum">${g.ops.length}</span> · ${esc(g.name)}${g.description ? ` <span class="text-caps text-chrome-muted">${esc(g.description)}</span>` : ''}</summary>
      <div class="mt-[2px] flex flex-col gap-[2px] pl-s2" data-api-ops>
        ${g.ops.slice(0, GROUP_CAP).map((op) => `<details data-api-op="${esc(op.key)}">${operationSummaryHtml(op)}<div data-api-op-body class="pl-s2"></div></details>`).join('')}
        ${g.ops.length > GROUP_CAP ? `<div class="text-caps text-chrome-muted">${num(GROUP_CAP)} of ${num(g.ops.length)} shown in this group</div>` : ''}
      </div></details>`).join('')}
    </div></div>`;
}

/* ── comparison ─────────────────────────────────────────────────────── */

export function comparisonHtml(c) {
  const list = (items, empty) => items.length
    ? `<ul class="m-0 list-none p-0 pl-s2">${items.slice(0, 200).map((o) => `<li class="text-caps"><span class="font-mono text-chrome-ink">${esc(o.key)}</span>${o.location ? ` <span class="font-mono text-chrome-muted">${esc(o.location)}</span>` : ''}${o.summary ? ` <span class="text-chrome-muted">${esc(o.summary)}</span>` : ''}</li>`).join('')}${items.length > 200 ? `<li class="text-caps text-chrome-muted">${num(200)} of ${num(items.length)} listed</li>` : ''}</ul>`
    : `<div class="pl-s2 text-caps text-chrome-muted">${esc(empty)}</div>`;
  const files = Object.entries(c.only_b_by_file || {});
  const same = !c.only_a.length && !c.only_b.length && !(c.probable || []).length;
  const e = stateEntry(same ? 'measured' : 'partial');
  return `<details data-api-compare="${esc(c.b.source)}" class="mt-s2 border-t border-chrome-line pt-s1">
    <summary class="cursor-pointer text-caps text-chrome-ink"><span class="${e.tone} font-glyph" aria-hidden="true">${same ? e.glyph : '≠'}</span>
      ${same ? 'agrees with' : 'differs from'} ${esc(c.b.word)} · ${num(c.a.count)} vs ${num(c.b.count)} · ${num(c.both)} match
      ${same ? '' : `· ${num(c.only_a.length)} only here · ${num(c.only_b.length)} only there`}</summary>
    <div class="text-caps text-chrome-muted" data-api-compare-summary>${esc(c.summary)}</div>
    ${(c.probable || []).length ? sec('Probably the same (path prefix differs)', `<ul class="m-0 list-none p-0 pl-s2">${c.probable.map((p) => `<li class="text-caps"><span class="font-mono text-chrome-ink">${esc(p.a)}</span> ≈ <span class="font-mono text-chrome-ink">${esc(p.b)}</span> <span class="text-chrome-muted">prefix ${esc(p.prefix)}</span></li>`).join('')}</ul>`) : ''}
    ${sec(`Only in ${c.a.word}`, list(c.only_a, 'nothing'))}
    ${sec(`Only in ${c.b.word}`, list(c.only_b, 'nothing'))}
    ${files.length ? sec('Only-there, by file', `<ul class="m-0 list-none p-0 pl-s2">${files.map(([f, n]) => `<li class="text-caps"><span class="font-mono text-chrome-ink">${esc(f)}</span> <span class="text-chrome-muted">${num(n)}</span></li>`).join('')}</ul>`) : ''}
  </details>`;
}

/* ── the whole reference ────────────────────────────────────────────── */

export function missingHtml(missing) {
  return (missing || []).map((m) => `<details data-api-missing="${esc(m.source)}" class="text-caps text-chrome-muted">
    <summary class="cursor-pointer">${mutedCue(m.source === 'running_instance' ? 'optional' : 'not_established',
      `no ${({ committed_spec: 'committed spec', running_instance: 'running instance', derived_from_code: 'code routes' })[m.source] || m.source}`, m.reason)}</summary>
    <div class="pl-s2">${esc(m.reason)}</div></details>`).join('');
}

export function referenceHtml(ref, ui = {}) {
  const ifaces = ref.interfaces || [];
  const idx = Math.min(Math.max(ui.pick || 0, 0), Math.max(ifaces.length - 1, 0));
  const chips = ifaces.map((i, n) => `<button type="button" data-api-pick="${n}" aria-pressed="${n === idx}"
      class="wl-chartchip cursor-pointer bg-transparent px-[4px] text-caps text-chrome-muted">${sourceCue(i.source, i.source_phrase)}${
      i.source === 'committed_spec' ? ` <span class="font-mono">${esc(i.origin)}</span>` : ''} ${num(i.count)}</button>`).join(' ');
  const sel = ifaces[idx];
  const cmp = sel ? (idx === 0 ? (ref.comparisons || []) : []) : [];
  const probs = (ref.problems || []).map((p) => (p.kind === 'fragment'
    ? `<div class="text-caps text-chrome-muted" data-api-fragment><span class="font-mono">${esc(p.path)}</span> · ${esc(p.reason)}</div>`
    : `<div class="text-caps text-state-warn-on-dark" data-api-problem>${stateEntry('error').glyph} <span class="font-mono">${esc(p.path)}</span> · ${esc(p.reason)}</div>`)).join('');
  const inst = ref.instance;
  const instLine = inst && inst.status === 'failed'
    ? `<div data-api-instance-failed class="text-caps text-state-warn-on-dark">${stateEntry('error').glyph} last check of ${esc(inst.url)} failed · ${esc(inst.error)}${inst.fetched_at ? ` · showing the copy fetched ${esc(ago(inst.fetched_at))}` : ''}</div>` : '';
  if (!ifaces.length) {
    return `<div data-api-empty class="text-caps text-chrome-ink">${stateEntry('not_established').glyph} nothing is known about this repository’s API yet</div>
      <div class="mt-s1 flex flex-col gap-[2px]">${missingHtml(ref.missing)}</div>${probs}`;
  }
  return `<div class="flex flex-wrap items-baseline gap-x-s2 gap-y-[2px]" data-api-sources>${chips}</div>
    ${instLine}${probs}
    ${interfaceHtml(sel, { recheck: sel.source === 'running_instance' })}
    ${cmp.map(comparisonHtml).join('')}
    ${(ref.missing || []).length ? `<div class="mt-s2 flex flex-col gap-[2px]">${missingHtml(ref.missing)}</div>` : ''}`;
}

/** Wire one rendered reference: source chips, lazy operation bodies, one-level schema opening, re-check. */
export function wireReference(root, ref, ui, { rerender, recheck } = {}) {
  root.querySelectorAll('[data-api-pick]').forEach((b) => b.addEventListener('click', () => {
    ui.pick = Number(b.dataset.apiPick);
    if (rerender) rerender();
  }));
  const iface = (ref.interfaces || [])[Math.min(ui.pick || 0, (ref.interfaces || []).length - 1)];
  if (!iface) return;
  const byKey = new Map((iface.operations || []).map((o) => [o.key, o]));
  root.querySelectorAll('details[data-api-op]').forEach((d) => d.addEventListener('toggle', () => {
    const body = d.querySelector('[data-api-op-body]');
    if (d.open && body && !body.dataset.filled) {
      body.innerHTML = operationBodyHtml(byKey.get(d.dataset.apiOp) || {}, iface);
      body.dataset.filled = '1';
      wireSchemas(body, iface);
    }
  }));
  const rc = root.querySelector('[data-api-recheck]');
  if (rc && recheck) rc.addEventListener('click', recheck);
}

export function wireSchemas(scope, iface) {
  scope.querySelectorAll('[data-schema-open]').forEach((btn) => btn.addEventListener('click', () => {
    const slot = btn.parentElement.querySelector(':scope > [data-schema-slot]') || btn.parentElement;
    const existing = slot.querySelector(':scope > [data-schema-tree]');
    if (existing) { existing.remove(); return; }
    const wrap = document.createElement('div');
    wrap.className = 'mt-[2px] w-full border-l border-chrome-line pl-s2';
    wrap.innerHTML = schemaTreeHtml(iface.schemas, btn.dataset.schemaOpen);
    wrap.dataset.schemaTree = btn.dataset.schemaOpen;
    slot.appendChild(wrap);
    wireSchemas(wrap, iface);
  }));
}

/* ── the registration control (Enrichment's Context, beside "Where it's documented") ─────────────── */

export function instanceControlHtml(ref, status = {}) {
  const inst = ref.instance;
  const url = status.url != null ? status.url : (inst ? inst.url : '');
  const have = (s) => (ref.interfaces || []).find((i) => i.source === s);
  const chip = (s, word) => have(s)
    ? `${sourceCue(s, have(s).source_phrase)} <span class="tnum">${have(s).count}</span>`
    : mutedCue(s === 'running_instance' ? 'optional' : 'not_established', `no ${word}`, (ref.missing || []).find((m) => m.source === s)?.reason || '');
  let state = '';
  if (status.error) state = `<span data-api-instance-state="rejected" class="text-state-warn">${stateEntry('error').glyph} ${esc(status.error)}</span>`;
  else if (status.pending) state = '<span data-api-instance-state="pending" class="text-ink-muted">◔ fetching</span>';
  else if (inst && inst.status === 'failed') state = `<span data-api-instance-state="failed" class="text-state-warn" title="${esc(inst.error)}">${stateEntry('error').glyph} last check failed · ${esc(inst.error)}</span>`;
  else if (inst) state = `<span data-api-instance-state="ok" class="text-state-ok" title="fetched ${esc(inst.fetched_at)}">${stateEntry('measured').glyph} fetched ${esc(ago(inst.fetched_at))}</span>`;
  return `<div class="mb-s1 flex flex-wrap items-baseline gap-x-s3 gap-y-[2px] text-provenance" data-api-sources-line>
      ${chip('committed_spec', 'committed spec')} ${chip('running_instance', 'running instance')} ${chip('derived_from_code', 'code routes')}
    </div>
    <div class="grid grid-cols-[1fr_auto] items-baseline gap-s2">
      <input id="api-instance-url" type="text" value="${esc(url)}" placeholder="running instance OpenAPI URL, e.g. http://localhost:8530/api/openapi.json"
        class="w-full rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink placeholder:text-ink-muted">
      <button type="button" id="api-instance-save" class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Fetch and keep</button>
    </div>
    <div class="mt-[2px] flex flex-wrap items-baseline gap-x-s3 text-provenance" data-api-instance-line>
      ${state}
      ${inst ? '<button type="button" id="api-instance-recheck" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">re-check</button><button type="button" id="api-instance-clear" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">forget</button>' : ''}
      ${!inst && ref.cleared ? `<span data-api-cleared class="text-ink-muted">forgotten by ${esc(ref.cleared.cleared_by || 'unknown')}${ref.cleared.cleared_at ? ` ${esc(ago(ref.cleared.cleared_at))}` : ''}</span>` : ''}
      ${(ref.interfaces || []).length ? '<button type="button" id="api-reference-open" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">open the API reference ›</button>' : ''}
    </div>`;
}

/* ── the pane (opens in the rail's evidence slot, beside the members drill-down) ─────────────────── */

const uiBySlug = new Map();

async function paintReference(slug, ticket) {
  const body = railFrame('API reference', slug, '<div class="text-caps text-chrome-muted">Reading the API reference…</div>', { sub: 'loading' });
  if (!body) return;
  let ref;
  try {
    ref = await getApiReference(slug);
  } catch (err) {
    if (railStale(ticket)) return;
    railFrame('API reference', slug, `<div class="text-caps text-state-warn-on-dark">The API reference could not be read: ${esc(err.message)}</div>`, { sub: 'failed' });
    return;
  }
  if (railStale(ticket)) return;
  const ui = uiBySlug.get(slug) || { pick: 0 };
  uiBySlug.set(slug, ui);
  const best = (ref.interfaces || [])[0];
  const host = railFrame('API reference', slug, '', { sub: best ? esc(best.source_word) : 'nothing known' });
  const draw = () => {
    host.innerHTML = referenceHtml(ref, ui);
    wireReference(host, ref, ui, {
      rerender: draw,
      recheck: async () => {
        const st = host.querySelector('[data-api-recheck-status]');
        if (st) st.textContent = ' · checking…';
        try { await refetchApiInstance(slug); } catch (err) { if (st) st.textContent = ` · ${err.message}`; return; }
        await paintReference(slug, railClaim());
      },
    });
  };
  draw();
}

export async function openApiReference({ slug }) {
  const out = $('rail-evidence');
  if (!out) return;
  ensureRailShowing();
  await paintReference(slug, railClaim());
}

/* ── the registration block in Context ──────────────────────────────── */

export async function renderApiReferenceBlock(slug) {
  const host = $('api-reference-block');
  if (!host) return;
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading what is known about the API…</div>';
  let ref;
  try {
    ref = await getApiReference(slug);
  } catch (err) {
    host.innerHTML = `<div class="text-caveat text-state-warn">Could not read the API reference: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug) return;
  paintBlock(host, slug, ref, {});
}

function paintBlock(host, slug, ref, status) {
  host.innerHTML = instanceControlHtml(ref, status);
  const input = host.querySelector('#api-instance-url');
  const reload = async () => { await renderApiReferenceBlock(slug); };
  host.querySelector('#api-instance-save')?.addEventListener('click', async () => {
    const url = input.value.trim();
    if (!url) return;
    paintBlock(host, slug, ref, { url, pending: true });
    let res;
    try { res = await putApiInstance(slug, url); } catch (err) { paintBlock(host, slug, ref, { url, error: err.message }); return; }
    if (!res.ok) { paintBlock(host, slug, ref, { url, error: res.reason }); return; }
    await reload();
  });
  host.querySelector('#api-instance-recheck')?.addEventListener('click', async () => {
    paintBlock(host, slug, ref, { pending: true });
    try { await refetchApiInstance(slug); } catch (err) { paintBlock(host, slug, ref, { error: err.message }); return; }
    await reload();
  });
  host.querySelector('#api-instance-clear')?.addEventListener('click', async () => {
    try { await clearApiInstance(slug); } catch (err) { paintBlock(host, slug, ref, { error: err.message }); return; }
    await reload();
  });
  host.querySelector('#api-reference-open')?.addEventListener('click', () => openApiReference({ slug }));
}
