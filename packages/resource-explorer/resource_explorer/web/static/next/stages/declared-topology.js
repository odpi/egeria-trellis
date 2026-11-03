/* Declared deployment topology ("Ports & Wires"), the FIRST AND ONLY caller of the generic graph renderer.
 *
 * Rebuilt from Classic's `_renderDeploymentInterfaces`. This file is an ADAPTER: it maps the architecture-recovery
 * `interfaces` payload (one entry per deployment) onto the renderer's input, once per deployment, and mounts the
 * result. The renderer (graph-renderer.js) knows nothing about deployments.
 *
 * What Classic said, and this keeps saying on the surface (not in a hover):
 *   - from deployment artifacts only (Dockerfile EXPOSE, compose ports/expose/depends_on, OpenAPI);
 *   - an arrow is a DECLARED STARTUP DEPENDENCY, not data flow;
 *   - nothing declared means nothing was declared, not that nothing exists;
 *   - deployments are NEVER mixed: one graph per deployment (egeria-workspaces declares 18);
 *   - a protocol is shown only when an artifact stated one, never inferred from a port number.
 *
 * TODO(declared-extensibility): every field below marked so is a declaration the analysis catalogue does not carry
 * yet (result_kind, the edge-meaning caption, the absence sentence, the as-of source). The adapter supplies them
 * here; when the catalogue declares them, delete the constants and read them from the declaration.
 */
import { renderByKind } from '/static/next/graph-renderer.js';
import { localTime } from '/static/next/format.js';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// TODO(declared-extensibility): result_kind is not declared in analysis_catalog.yaml yet.
export const RESULT_KIND = 'graph';
// TODO(declared-extensibility): the caption (what an edge means) belongs to the declaration.
export const EDGE_MEANING = 'Arrows are declared startup dependencies, not data flow.';
export const SOURCES_SENTENCE = 'From deployment artifacts only (Dockerfile EXPOSE, compose ports/expose/depends_on, OpenAPI).';
// TODO(declared-extensibility): the absence sentence belongs to the declaration.
export const ABSENCE_SENTENCE = 'Nothing declared means nothing was declared — not that nothing exists.';
export const NO_WIRES_SENTENCE = 'No startup dependency was declared in this deployment.';
export const NO_DEPLOYMENTS_SENTENCE = 'No deployment artifact declared a port or a startup dependency in the last architecture recovery run.';

/** One deployment -> one renderer input. Protocol appears only if the port states one. */
export function deploymentGraphSpec(dep, asOf) {
  const nodes = (dep.components || []).map((c) => ({
    id: c.name,
    label: c.name,
    kind: 'component',
    notes: (c.ports || []).length
      ? c.ports.map((pt) => ({
        text: `${pt.port}${pt.protocol ? `/${pt.protocol}` : ''}`,
        title: pt.summary || `${pt.port}${pt.protocol ? `/${pt.protocol}` : ''}`,
      }))
      : [{ text: 'no declared port' }],
  }));
  const edges = (dep.wires || []).map((w) => ({
    from: w.source, to: w.target, label: w.integration_style || '', kind: 'depends_on',
  }));
  const artifacts = dep.artifact_count != null
    ? ` ${dep.artifact_count} deployment artifact${dep.artifact_count === 1 ? '' : 's'} read.` : '';
  return {
    nodes,
    edges,
    caption: `${EDGE_MEANING} ${SOURCES_SENTENCE}${artifacts}`,
    as_of: asOf,
    absence_sentence: `${edges.length ? '' : `${NO_WIRES_SENTENCE} `}${ABSENCE_SENTENCE}`,
    group: { id: dep.name, title: dep.name },
  };
}

/** The whole section for one architecture_recovery results payload. A payload with a status (never run) is
 *  "not measured", worded from the payload; an empty deployment list is "measured, nothing declared". */
export function declaredTopologyHtml(results) {
  const head = '<div class="text-caps uppercase tracking-caps text-ink-muted">Declared deployment topology</div>';
  if (!results) return '';
  if (results._status) {
    return `<div data-declared-topology="not-measured">${head}
      <div data-topology-not-measured class="text-caveat text-ink-muted">Not measured: ${esc(results._status.hint || 'architecture recovery has not run.')}</div></div>`;
  }
  const deployments = results.interfaces || [];
  if (!deployments.length) {
    return `<div data-declared-topology="none">${head}
      <div data-topology-none class="text-caveat text-ink-muted">${esc(NO_DEPLOYMENTS_SENTENCE)} ${esc(ABSENCE_SENTENCE)}</div></div>`;
  }
  // TODO(declared-extensibility): as-of should come from the declaration's as-of source; today it is the recovery run's.
  const asOf = results.surveyed_at ? `the architecture recovery run of ${localTime(results.surveyed_at) || results.surveyed_at}` : '';
  return `<div data-declared-topology="graphs">${head}
    <div class="mt-s1">${deployments.map((d) => renderByKind(RESULT_KIND, deploymentGraphSpec(d, asOf))).join('')}</div></div>`;
}

const CACHE_MS = 60000;
const cache = new Map();            // slug -> { at, promise }

/** Read and draw into `host`. `fetchResults(slug)` is injected. A failed read says so; it never draws an empty
 *  graph. `stale()` drops a late answer for a resource no longer in view. The answer is kept for a minute, since
 *  the By-analysis pane re-draws its cards on every board that settles. */
export async function mountDeclaredTopology(host, slug, { fetchResults, stale = () => false }) {
  if (!host) return;
  host.innerHTML = '<p data-topology-reading class="text-caveat text-ink-muted">reading the declared deployment topology…</p>';
  try {
    let hit = cache.get(slug);
    if (!hit || Date.now() - hit.at > CACHE_MS) {
      hit = { at: Date.now(), promise: fetchResults(slug) };
      cache.set(slug, hit);
    }
    const results = await hit.promise;
    if (stale()) return;
    host.innerHTML = declaredTopologyHtml(results);
  } catch (err) {
    cache.delete(slug);
    if (stale()) return;
    host.innerHTML = `<p data-topology-error class="text-caveat text-state-warn">The declared deployment topology could not be read: ${esc(err && err.message ? err.message : 'unknown error')}</p>`;
  }
}

export function clearTopologyCache() { cache.clear(); }
