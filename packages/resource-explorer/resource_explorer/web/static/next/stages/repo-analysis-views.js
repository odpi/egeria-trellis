/* Two Classic-only results views rebuilt for /next (owner 2026-10-21: "yes - rebuild these in next"):
 * repo_classification and dependency_support, drawn in the repository's Understanding > Contents tab.
 *
 * Each view reads the SAME data Classic read (GET /api/projects/{slug}/analyses/{id}/results, via
 * getAnalysisResults) and the analysis's catalogue entry (GET /api/analyses/repo, via listAnalyses), so
 * slice 1's declaration can turn them into declared users by deleting code (architect ruling 2026-10-21).
 * Where the catalogue entry has no field for something the view needs, the view reads what exists and
 * carries ONE marked TODO(declared-extensibility) for that field -- see catalogueFields().
 *
 * THE RULES: state is a cue on the element (glyph + tone), the words second. "Not yet classified" is a
 * count of names nobody has mapped, never "unsupported". An empty result is a sentence naming what was not
 * done, never a blank panel. Every value is escaped.
 */
import { cue } from '/static/next/stages/repo-manifest.js';

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const NO_DEPENDENCIES_SENTENCE = 'No dependency rows are held for this repository, so there is nothing to assess — run manifest_parse first.';
export const NO_CLASSIFICATION_SENTENCE = 'No classification is held for this repository — run repo_classification.';
export const NO_DEPSUPPORT_SENTENCE = 'No dependency-support assessment is held for this repository — run dependency_support.';

const LOCATIONS = [
  ['in-repo', 'In this repo'], ['sibling-repo', 'In a sibling repo'],
  ['doc-site', 'On the documentation site'], ['not-found', 'Not located'],
];

/** What the catalogue entry declares for this analysis. One TODO per field the entry does not yet carry. */
export function catalogueFields(entry, data) {
  const e = entry || {};
  return {
    name: e.name || '',
    description: e.description || '',
    // TODO(declared-extensibility): unit -- the catalogue entry has no `unit`; these views state counts in words.
    unit: e.unit || '',
    // TODO(declared-extensibility): as_of -- no catalogue `as_of`; read the result's own surveyed_at when it has one.
    asOf: e.as_of || (data && data.surveyed_at) || '',
    // TODO(declared-extensibility): confidence_basis -- no catalogue field; the per-row confidence is shown bare.
    confidenceBasis: e.confidence_basis || '',
    // TODO(declared-extensibility): absence_sentence -- no catalogue field; each view carries its own sentences above.
    absence: e.absence_sentence || '',
  };
}

const headHtml = (kind, f) => `<div data-analysis-head="${esc(kind)}" class="text-provenance text-ink-muted break-words [overflow-wrap:anywhere]">`
  + `${f.name ? `<span class="font-semibold">${esc(f.name)}</span>` : ''}`
  + `${f.asOf ? ` · as of <span class="tnum">${esc(String(f.asOf).replace('T', ' ').slice(0, 16))}</span>` : ''}`
  + `${f.unit ? ` · ${esc(f.unit)}` : ''}</div>`;

/** A sentence for a result with nothing to draw. The server's own `_status` wins when it has one. */
function absenceHtml(kind, data, fallback, f) {
  const st = data && data._status;
  const sentence = (st && st.hint) || f.absence || fallback;
  const key = st && st.state === 'nothing_found' ? 'nothing'
    : st && st.state === 'skipped_by_design' ? 'optional' : 'unrun';
  return `<p data-analysis-absent="${esc(kind)}" class="mt-s2 text-caveat">${cue(key, sentence)}</p>`;
}

/* ── repo_classification ─────────────────────────────────────────────── */

const relDays = (iso) => {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const d = Math.floor((Date.now() - t) / 86400000);
  if (d <= 0) return 'today';
  if (d === 1) return 'yesterday';
  if (d < 60) return `${d}d ago`;
  return `${Math.floor(d / 30)}mo ago`;
};

export function repoClassificationHtml(data, entry = null) {
  const f = catalogueFields(entry, data);
  const findings = (data && data.findings) || [];
  if (!findings.length) return absenceHtml('repo_classification', data, NO_CLASSIFICATION_SENTENCE, f);
  const by = {};
  findings.forEach((x) => { by[x.check_name] = x; });
  const head = headHtml('repo_classification', f);

  const err = by.classification_error;
  if (err) {
    return `<div data-repo-classification="error">${head}<p data-classification-error class="mt-s2 text-caveat">${
      cue('error', err.summary || 'Classification failed')}</p></div>`;
  }

  const out = [];
  const role = by.repo_role;
  if (role) {
    // Multi-valued and ranked: the primary first and marked, the secondaries shown beside it, never hidden.
    const roles = (role.detail && Array.isArray(role.detail.roles) && role.detail.roles.length)
      ? role.detail.roles : [role.label];
    out.push(`<div data-classification-roles class="mt-s2 flex flex-wrap items-center gap-s1">
      <span class="text-provenance uppercase text-ink-muted">Role</span>${roles.map((r, i) => i === 0
    ? `<span data-role-rank="1" class="rounded-sm border border-rule-strong px-2 py-[1px] text-caveat font-semibold text-ink break-words [overflow-wrap:anywhere]" title="primary role">${esc(r)}</span>`
    : `<span data-role-rank="${i + 1}" class="rounded-sm border border-rule px-2 py-[1px] text-caveat text-ink-muted break-words [overflow-wrap:anywhere]" title="secondary role ${i + 1}">${esc(r)}</span>`).join('')}
      ${role.confidence != null && role.confidence !== '' ? `<span data-role-confidence class="text-provenance text-ink-muted">confidence <span class="tnum">${esc(role.confidence)}</span></span>` : ''}</div>`);
    const notes = (role.detail && role.detail.notes) || [];
    if (notes.length) {
      out.push(`<ul data-classification-notes class="m-0 mt-s1 list-none p-0 text-provenance text-ink-muted">${
        notes.map((n) => `<li class="break-words [overflow-wrap:anywhere]">${esc(n)}</li>`).join('')}</ul>`);
    }
  }

  const gate = by.architecture_recovery_gate;
  if (gate) {
    const skipped = gate.label === 'skip';
    const st = gate.detail && gate.detail.result_status;
    const reason = (st && st.hint) || gate.summary || '';
    out.push(`<p data-classification-gate="${skipped ? 'skip' : 'run'}" class="mt-s2 text-caveat">${
      cue(skipped ? 'optional' : 'measured',
        `${skipped ? 'Architecture recovery not applicable' : 'Architecture recovery will run'}${reason ? ` — ${reason}` : ''}`)}</p>`);
  }

  const expected = findings.filter((x) => String(x.check_name || '').startsWith('expected_'));
  if (expected.length) {
    const rows = [];
    LOCATIONS.forEach(([loc, heading]) => {
      expected.filter((x) => x.label === loc).forEach((x) => {
        const kind = (x.detail && x.detail.kind) || x.check_name.replace(/^expected_/, '');
        const when = relDays(x.detail && x.detail.date);
        rows.push(`<tr data-expected="${esc(kind)}" data-location="${esc(loc)}"><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${esc(kind)}</td>`
          + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${cue(loc === 'not-found' ? 'unrun' : 'measured', heading)}</td>`
          + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc((x.detail && x.detail.evidence) || '')}</td>`
          + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc(when)}</td></tr>`);
      });
    });
    // A label outside the four known locations is still shown, as its own word, not dropped.
    expected.filter((x) => !LOCATIONS.some(([loc]) => loc === x.label)).forEach((x) => {
      const kind = (x.detail && x.detail.kind) || x.check_name.replace(/^expected_/, '');
      rows.push(`<tr data-expected="${esc(kind)}" data-location="${esc(x.label)}"><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${esc(kind)}</td>`
        + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${cue('not_established', x.label || 'location not stated')}</td>`
        + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc((x.detail && x.detail.evidence) || '')}</td><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top"></td></tr>`);
    });
    out.push(`<table data-classification-expected class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Expected artifact</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Where</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Evidence</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Seen</th></tr></thead><tbody>${rows.join('')}</tbody></table>`);
  }

  // Findings that are none of the above are still findings: listed, not silently dropped.
  const known = new Set(['repo_role', 'architecture_recovery_gate', 'classification_error']);
  const other = findings.filter((x) => !known.has(x.check_name) && !String(x.check_name || '').startsWith('expected_'));
  if (other.length) {
    out.push(`<table data-classification-other class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Finding</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Value</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Summary</th></tr></thead><tbody>${
      other.map((x) => `<tr data-finding="${esc(x.check_name)}"><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${esc(x.check_name)}</td><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${esc(x.label)}</td><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc(x.summary)}</td></tr>`).join('')}</tbody></table>`);
  }
  return `<div data-repo-classification="rows">${head}${out.join('')}</div>`;
}

/* ── dependency_support ──────────────────────────────────────────────── */

const EGERIA_STATE = {
  present: ['measured', 'in Egeria'], absent: ['partial', 'not in Egeria'],
  unchecked: ['not_established', 'unchecked'], 'no-type': ['not_established', 'no Egeria type yet'],
};

export function dependencySupportHtml(data, entry = null) {
  const f = catalogueFields(entry, data);
  if (!data || (!data.technologies && !data.coverage)) return absenceHtml('dependency_support', data, NO_DEPSUPPORT_SENTENCE, f);
  const cov = data.coverage || {};
  const techs = data.technologies || [];
  const head = headHtml('dependency_support', f);

  if (cov.label === 'no-dependencies') {
    return `<div data-dependency-support="none">${head}<p data-dependency-none class="mt-s2 text-caveat">${cue('unrun', NO_DEPENDENCIES_SENTENCE)}</p></div>`;
  }

  const check = cov.egeria_check || 'skipped';
  const checkCue = check === 'checked' ? cue('measured', 'Egeria checked')
    : check === 'unreachable' ? cue('partial', 'Egeria could not be checked', cov.egeria_check_detail || '')
      : cue('optional', 'Egeria not consulted');
  const top = `<div class="mt-s2 flex flex-wrap items-baseline gap-s2 text-caveat">
    <span data-egeria-check="${esc(check)}">${checkCue}</span>
    ${cov.summary ? `<span data-depsupport-summary class="text-ink break-words [overflow-wrap:anywhere]">${esc(cov.summary)}</span>` : ''}</div>
    ${data.message ? `<p data-depsupport-message class="m-0 mt-s1 text-provenance text-ink-muted">${esc(data.message)}</p>` : ''}`;

  const techBlock = techs.length
    ? `<table data-depsupport-technologies class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Technology</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">In Egeria</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Egeria type</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Indicated by</th></tr></thead><tbody>${
      techs.map((t) => {
        const [key, word] = EGERIA_STATE[t.egeria_state || 'unchecked'] || ['not_established', t.egeria_state];
        const deps = t.dependencies || [];
        return `<tr data-technology="${esc(t.technology)}" data-egeria-state="${esc(t.egeria_state || 'unchecked')}"><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top font-semibold">${esc(t.technology)}</td>`
          + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top">${cue(key, word)}</td><td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc(t.egeria_technology_type || '')}</td>`
          + `<td class="px-s2 py-[2px] text-caveat text-ink break-words [overflow-wrap:anywhere] align-top text-ink-muted">${esc(deps.slice(0, 8).join(', '))}${deps.length > 8 ? ` +${deps.length - 8} more` : ''}</td></tr>`;
      }).join('')}</tbody></table>`
    : `<p data-depsupport-technologies="none" class="mt-s2 text-caveat">${cue('nothing', 'No dependency matched a curated technology')}</p>`;

  // null means "could not derive", which is different from "none".
  let unmatched;
  if (data.unmatched === null || data.unmatched === undefined) {
    unmatched = `<p data-depsupport-unmatched="unknown" class="mt-s2 text-caveat">${cue('unknown', 'The not-yet-classified list could not be derived')}</p>`;
  } else if (!data.unmatched.length) {
    unmatched = `<p data-depsupport-unmatched="0" class="mt-s2 text-caveat">${cue('measured', 'Every dependency matched a curated technology')}</p>`;
  } else {
    const u = data.unmatched;
    unmatched = `<details data-depsupport-unmatched="${u.length}" class="mt-s2"><summary class="cursor-pointer text-caveat">${
      cue('unclassified', `${u.length} ${u.length === 1 ? 'dependency' : 'dependencies'} not yet classified`)} <span class="text-ink-muted">· a curation queue, not a verdict</span></summary>
      <p data-depsupport-unmatched-list class="m-0 mt-s1 max-h-[12rem] overflow-auto text-provenance text-ink-muted break-words [overflow-wrap:anywhere]">${esc(u.join(', '))}</p></details>`;
  }
  return `<div data-dependency-support="rows">${head}${top}${techBlock}${unmatched}</div>`;
}

/* ── mounting ────────────────────────────────────────────────────────── */

const VIEWS = { repo_classification: repoClassificationHtml, dependency_support: dependencySupportHtml };

/** Read the results and the catalogue entry and draw one view into `host`. A failed read says so. `stale()`
 *  drops a late answer for a resource no longer in view. */
export async function mountRepoAnalysisView(host, slug, kind, { fetchResults, fetchCatalogue, stale = () => false }) {
  host.innerHTML = '<p class="mt-s2 text-caveat text-ink-muted">reading…</p>';
  try {
    const [data, catalogEntries] = await Promise.all([
      fetchResults(slug, kind),
      Promise.resolve().then(() => fetchCatalogue('repo')).catch(() => []),
    ]);
    if (stale()) return;
    const entry = (Array.isArray(catalogEntries) ? catalogEntries : []).find((e) => e && e.id === kind) || null;
    host.innerHTML = VIEWS[kind](data, entry);
  } catch (err) {
    if (stale()) return;
    host.innerHTML = `<p data-analysis-error="${esc(kind)}" class="mt-s2 text-caveat text-state-warn">The results could not be read: ${esc(err && err.message ? err.message : 'unknown error')}</p>`;
  }
}
