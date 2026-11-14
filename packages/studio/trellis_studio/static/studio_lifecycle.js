/* S3: versions and history, compare with what Egeria holds, and publish / promote / withdraw.
 * The page never sends a plan to run: it sends the same inputs again with the digest it previewed, and the server
 * rebuilds the plan as you and runs it only if nothing changed. Breaking changes (or a held version that couldn't
 * be read) need their own confirmation. "Used by" is said as pyegeria can say it, never as "none" by default. */
import { api } from '/static/studio_auth.js';
import { h, clear, add, readJson } from '/static/dom.js';

const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const keyOf = (d) => (d.kind === 'spec' ? d.label : d.kind === 'report' ? d.display_name : d.name);
let consumers = null;

function changeList(changes) {
  if (!changes || !changes.length) return h('p', { class: 'st-muted' }, 'No changes.');
  return h('ul', {}, changes.map((c) => h('li', { class: c.breaking ? 'st-error' : '' },
    `${c.breaking ? 'BREAKING · ' : ''}${c.area} ${c.name}: ${c.change}`, c.why ? h('span', { class: 'st-muted' }, ` — ${c.why}`) : null)));
}

async function showHistory(host, d) {
  clear(host).append(h('p', { class: 'st-muted' }, 'Reading the history as you…'));
  const res = await readJson(await api(`/api/history?kind=${d.kind}&label=${encodeURIComponent(keyOf(d))}`));
  clear(host);
  if (!res.ok) return host.append(h('p', { class: 'st-error' }, res.detail));
  const hst = res.data;
  if (!hst.read) return host.append(h('p', { class: 'st-warn' }, `History not read: ${hst.why}`));
  add(host, h('p', {}, `Current Egeria version ${hst.current_version ?? '?'}.`),
      hst.why ? h('p', { class: 'st-warn' }, hst.why) : null,
      h('table', { class: 'st-cols' }, h('thead', {}, h('tr', {}, ['Version', 'Updated', 'By'].map((t) => h('th', {}, t)))),
        h('tbody', {}, hst.versions.map((v) => h('tr', {}, h('td', {}, v.version ?? ''), h('td', {}, String(v.update_time || v.create_time || '')),
          h('td', {}, v.updated_by || v.created_by || ''))))));
  if (d.kind === 'spec') host.append(specVersions(d));
}

function specVersions(d) {
  const box = h('div', { class: 'st-block' });
  const go = async () => {
    clear(box).append(h('p', { class: 'st-muted' }, 'Reading published versions…'));
    const res = await readJson(await api(`/api/published-versions?label=${encodeURIComponent(d.label)}`));
    clear(box);
    if (!res.ok) return box.append(h('p', { class: 'st-warn' }, res.detail));
    const vs = res.data.published_versions;
    if (vs.length < 2) return box.append(h('p', { class: 'st-muted' }, `Published versions: ${vs.join(', ') || 'none yet'}.`));
    const a = h('select', {}, vs.map((v) => h('option', { value: v }, `v${v}`)));
    const b = h('select', {}, vs.map((v) => h('option', { value: v }, `v${v}`)));
    b.value = vs[vs.length - 1];
    const out = h('div', {});
    box.append(h('p', {}, `Published versions: ${vs.join(', ')}.`), h('div', { class: 'st-row' }, 'Compare ', a, ' with ', b,
      h('button', { class: 'st-btn st-btn-quiet', onclick: async () => {
        const r = await readJson(await api('/api/diff', json({ label: d.label, a_version: Number(a.value), b_version: Number(b.value) })));
        clear(out).append(r.ok ? changeList(r.data.changes) : h('p', { class: 'st-error' }, r.detail));
      } }, 'Compare')), out);
  };
  box.append(h('button', { class: 'st-btn st-btn-quiet', onclick: go }, 'Published versions'));
  return box;
}

async function compareHeld(host, d) {
  clear(host).append(h('p', { class: 'st-muted' }, 'Comparing with what Egeria holds, as you…'));
  const res = await readJson(await api('/api/diff', json({ draft: d })));
  clear(host);
  if (!res.ok) return host.append(h('p', { class: 'st-error' }, res.detail));
  const r = res.data;
  for (const n of r.notes || []) host.append(h('p', { class: 'st-warn' }, n));
  if (r.against === 'absent') return host.append(h('p', {}, 'Egeria holds nothing by this name that you can see: publishing creates it.'));
  if (r.against === 'unknown') return host.append(h('p', { class: 'st-warn' }, "Couldn't tell what Egeria holds, so there is nothing to compare against."));
  host.append(h('p', {}, `Against Egeria version ${r.held_version ?? '?'}:`), changeList(r.changes));
}

function impactView(i) {
  const used = i.used_by || {};
  return h('div', { class: 'st-block' },
    h('h4', {}, 'Impact'),
    i.breaking === null ? h('p', { class: 'st-warn' }, "What Egeria holds couldn't be read, so breaking changes can't be ruled out.")
      : i.breaking && i.breaking.length ? h('p', { class: 'st-error' }, `${i.breaking.length} breaking change${i.breaking.length === 1 ? '' : 's'}.`) : null,
    i.changes ? changeList(i.changes) : null,
    ...(i.notes || []).map((n) => h('p', { class: 'st-warn' }, n)),
    h('strong', {}, 'Used by'),
    used.available
      ? h('div', {}, h('ul', {}, (used.items || []).map((u) => h('li', {}, `${u.kind} ${u.name}${u.via ? ` (${u.via})` : ''}`))),
          used.complete ? (used.items || []).length ? null : h('p', {}, 'Used by none.') : h('p', { class: 'st-warn' }, 'At least these; the read did not confirm there are no more.'),
          ...(used.notes || []).map((n) => h('p', { class: 'st-warn' }, n)))
      : h('p', { class: 'st-warn' }, used.words || 'Not available.'));
}

function planPanel(d, savedAs) {
  const box = h('div', { class: 'st-block' });
  const isReport = d.kind === 'report';
  const ops = [['publish', 'Publish a new version']];
  if (savedAs) ops.unshift(['promote', 'Promote my draft (make it public)']);
  if (!isReport) ops.push(['withdraw', 'Withdraw from folders']);
  const op = h('select', {}, ops.map(([v, t]) => h('option', { value: v }, t)));
  const folderBoxes = (consumers?.consumers || []).map((c) => h('label', { class: 'st-chip' }, h('input', { type: 'checkbox', value: c }), ` ${c}`));
  const out = h('div', {});
  const inputs = () => {
    const folders = folderBoxes.map((l) => l.querySelector('input')).filter((i) => i.checked).map((i) => i.value);
    if (op.value === 'promote') return { op: 'promote', ref: { kind: d.kind, name: savedAs }, folders };
    if (op.value === 'withdraw') return { op: 'withdraw', kind: d.kind, label: keyOf(d), folders };
    return { op: 'publish', draft: JSON.parse(JSON.stringify(d, (k, v) => (k.startsWith('_') ? undefined : v))), folders };
  };
  const doPlan = async () => {
    clear(out).append(h('p', { class: 'st-muted' }, 'Planning as you…'));
    const req = inputs();
    const res = await readJson(await api('/api/lifecycle/plan', json(req)));
    clear(out);
    if (!res.ok) return out.append(h('p', { class: 'st-error' }, res.detail));
    const { plan, impact, needs_breaking_confirm: needsBreaking } = res.data;
    const confirm = h('input', { type: 'checkbox' });
    const confirmBreaking = h('input', { type: 'checkbox' });
    const said = h('div', {});
    const step = async (name) => {
      clear(said).append(h('p', { class: 'st-muted' }, `${name === 'process' ? 'Running' : 'Validating'} as you…`));
      const r = await readJson(await api('/api/lifecycle/apply', json({ ...req, step: name, digest: plan.digest,
        confirmed: confirm.checked, confirm_breaking: confirmBreaking.checked })));
      clear(said);
      if (!r.ok) return said.append(h('p', { class: 'st-error' }, r.detail));
      const result = r.data.result || {};
      said.append(h('p', { class: result.success === false ? 'st-error' : 'st-ok' },
        result.success === false ? `${name} did not succeed.` : `${name === 'process' ? 'Done' : 'Valid'}: ${result.commands_succeeded ?? '?'} of ${result.commands_total ?? '?'} commands.`),
        h('pre', { class: 'st-pre' }, JSON.stringify(result, null, 2)));
      if (name === 'process') document.dispatchEvent(new CustomEvent('studio:published', { detail: { op: req.op } }));
    };
    add(out,
      h('h4', {}, 'Steps'), h('ol', {}, (plan.steps || []).map((s) => h('li', {}, s))),
      ...(plan.notes || []).map((n) => h('p', { class: 'st-warn' }, n)),
      impactView(impact),
      h('details', {}, h('summary', {}, 'The Dr.Egeria document'), h('pre', { class: 'st-pre' }, plan.markdown || '')),
      h('div', { class: 'st-actions' },
        h('button', { class: 'st-btn st-btn-quiet', onclick: () => step('validate') }, 'Validate with Dr.Egeria'),
        h('label', {}, confirm, ' I have read the steps; run them as me'),
        needsBreaking ? h('label', { class: 'st-error' }, confirmBreaking, ' I accept that consumers may see a breaking change') : null,
        h('button', { class: 'st-btn', onclick: () => step('process') }, 'Run')),
      said);
  };
  box.append(h('h4', {}, 'Publish'), h('div', { class: 'st-row' }, op),
    isReport ? h('p', { class: 'st-muted' }, "A Report isn't filed in a folder: a sheet places it.")
             : h('div', { class: 'st-row' }, 'Folders: ', ...folderBoxes),
    h('button', { class: 'st-btn st-btn-quiet', onclick: doPlan }, 'Plan it'), out);
  return box;
}

export async function lifecyclePanel(d, savedAs) {
  if (!consumers) {
    const r = await readJson(await api('/api/consumers'));
    consumers = r.ok ? r.data : { consumers: [] };
  }
  const hist = h('div', {});
  const cmp = h('div', {});
  return h('div', {},
    h('div', { class: 'st-actions' },
      h('button', { class: 'st-btn st-btn-quiet', onclick: () => showHistory(hist, d) }, 'History'),
      h('button', { class: 'st-btn st-btn-quiet', onclick: () => compareHeld(cmp, d) }, 'Compare with Egeria')),
    hist, cmp, planPanel(d, savedAs));
}

/** Report form: choose follow-latest or a published version of its spec (D14). */
export function pinPicker(d, touch) {
  const box = h('div', { class: 'st-block' });
  const draw = (versions, words) => {
    clear(box);
    const sel = h('select', {}, h('option', { value: '' }, 'Follow the latest published version'),
      (versions || []).map((v) => h('option', { value: v }, `Pin to published version ${v}`)));
    sel.value = d.pinned_version ?? '';
    sel.addEventListener('change', () => { d.pinned_version = sel.value === '' ? null : Number(sel.value); touch(); });
    add(box, h('label', { class: 'st-field' }, 'Spec version (D14)', sel),
        words ? h('p', { class: 'st-warn' }, words) : null,
        h('button', { class: 'st-btn st-btn-quiet', onclick: load }, 'Read the spec\'s published versions'));
  };
  const load = async () => {
    const res = await readJson(await api(`/api/published-versions?label=${encodeURIComponent(d.spec || '')}`));
    if (!res.ok) return draw([], res.detail);
    draw(res.data.published_versions, null);
  };
  draw(d.pinned_version ? [d.pinned_version] : [], null);
  return box;
}
