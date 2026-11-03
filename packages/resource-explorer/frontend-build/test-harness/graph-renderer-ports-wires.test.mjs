/** The generic graph renderer (result_kind = graph) and its first caller, declared deployment topology.
 *  Rules under test: one graph per deployment, never mixed; the meaning of an arrow and the absence sentence said
 *  on the surface for every deployment; a protocol only when an artifact stated one; a long label cut in the box
 *  with all of it in the hover; every string escaped; "not measured" is never drawn as "measured, nothing". */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const { document } = makeDomEnvironment();
const gr = await import('/static/next/graph-renderer.js');
const dt = await import('/static/next/stages/declared-topology.js');
const dom = (html) => { const d = document.createElement('div'); d.innerHTML = html; return d; };
const text = (n) => n.textContent.replace(/\s+/g, ' ').trim();

const LONG = 'a-very-long-service-name-that-would-overrun-its-box-by-a-wide-margin-service';
const fixture = () => ({
  nodes: [{ id: 'web', label: 'web' }, { id: 'db', label: 'db' }, { id: LONG, label: LONG }],
  edges: [{ from: 'web', to: 'db', label: 'compose', kind: 'depends_on' }, { from: 'web', to: LONG, kind: 'depends_on' }],
  caption: 'Arrows are X.', as_of: 'Oct 21', absence_sentence: 'Nothing here means nothing.', group: { id: 'g1', title: 'quickstart' },
});

test('draws every node and edge, with labelled counts, caption, as-of and absence sentence', () => {
  const d = dom(gr.graphHtml(fixture()));
  assert.equal(d.querySelectorAll('[data-graph-node]').length, 3);
  assert.equal(d.querySelectorAll('[data-graph-edge]').length, 2);
  assert.equal(text(d.querySelector('[data-graph-counts]')), '3 nodes · 2 edges');
  assert.equal(text(d.querySelector('[data-graph-caption]')), 'Arrows are X.');
  assert.equal(text(d.querySelector('[data-graph-as-of]')), 'as of Oct 21');
  assert.equal(text(d.querySelector('[data-graph-absence]')), 'Nothing here means nothing.');
  assert.equal(text(d.querySelector('[data-graph-title]')), 'quickstart');
});

test('a long label is cut inside its box and the hover has all of it', () => {
  const d = dom(gr.graphHtml(fixture()));
  const node = [...d.querySelectorAll('[data-graph-node]')].find((n) => n.getAttribute('data-graph-node') === LONG);
  const shown = text(node.querySelector('[data-node-label]'));
  assert.ok(shown.length <= gr.LABEL_CHARS, `${shown.length} > ${gr.LABEL_CHARS}`);
  assert.ok(shown.endsWith('…'));
  assert.equal(text(node.querySelector('title')), LONG);
  // The text sits inside a clip the width of the box, so a wider font still cannot overrun it.
  assert.ok(node.querySelector('clipPath rect'));
  assert.ok(Number(node.querySelector('clipPath rect').getAttribute('width')) <= gr.NODE_W);
  // A short label is untouched.
  assert.equal(gr.cut('web', gr.LABEL_CHARS), 'web');
});

test('layout: dependents left, dependencies right; no two nodes overlap; a cycle does not hang', () => {
  const { pos } = gr.layoutGraph(fixture().nodes, fixture().edges);
  assert.ok(pos.get('db').x > pos.get('web').x);
  const boxes = [...pos.values()];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i]; const b = boxes[j];
    const apart = a.x + gr.NODE_W <= b.x || b.x + gr.NODE_W <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
    assert.ok(apart, 'boxes overlap');
  }
  const cyc = gr.layoutGraph([{ id: 'a' }, { id: 'b' }], [{ from: 'a', to: 'b' }, { from: 'b', to: 'a' }]);
  assert.equal(cyc.pos.size, 2);
});

test('an edge naming a node that is not listed is not drawn, and the count says so', () => {
  const d = dom(gr.graphHtml({ ...fixture(), edges: [{ from: 'web', to: 'ghost', kind: 'depends_on' }] }));
  assert.equal(d.querySelectorAll('[data-graph-edge]').length, 0);
  assert.match(text(d.querySelector('[data-graph-dropped]')), /1 edge named a node that is not in the list/);
});

test('escaping: labels, notes, edge labels, titles and the caption cannot inject markup', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const html = gr.graphHtml({
    nodes: [{ id: 'a"b', label: evil, notes: [{ text: evil, title: evil }] }, { id: 'c', label: 'c' }],
    edges: [{ from: 'a"b', to: 'c', label: evil, kind: evil }],
    caption: evil, as_of: evil, absence_sentence: evil, group: { id: evil, title: evil },
  });
  const d = dom(html);
  assert.equal(d.querySelectorAll('img').length, 0);
  assert.equal(d.querySelectorAll('[onerror]').length, 0);
  assert.ok(text(d.querySelector('[data-graph-caption]')).includes('<img'));
});

test('no nodes: no figure, and the absence sentence is still said', () => {
  const d = dom(gr.graphHtml({ nodes: [], edges: [], caption: 'c', as_of: '', absence_sentence: 'none declared' }));
  assert.equal(d.querySelectorAll('svg').length, 0);
  assert.equal(text(d.querySelector('[data-graph-absence]')), 'none declared');
  assert.equal(d.querySelector('[data-graph-as-of]').getAttribute('data-graph-as-of'), 'unknown');
});

test('keyed by result_kind: graph has a renderer; an unknown kind is said, never drawn empty', () => {
  assert.ok(gr.RESULT_RENDERERS.graph);
  const d = dom(gr.renderByKind('sankey', {}));
  assert.match(text(d), /No renderer for result kind "sankey"/);
});

const dep = (name, comps, wires, artifact_count = 2) => ({ name, artifact_count, components: comps, wires });
const comp = (name, ports = []) => ({ name, ports });
const port = (p, protocol = '', summary = '') => ({ port: p, protocol, summary, direction: '' });

const results = () => ({
  surveyed_at: '2026-10-20T12:00:00', interfaces: [
    dep('quickstart', [comp('egeria', [port('9443', 'https', 'EXPOSE 9443')]), comp('postgres', [port('5432')]), comp('kafka')],
      [{ source: 'egeria', target: 'postgres', integration_style: 'depends_on' }]),
    dep('freshstart', [comp('egeria'), comp('prometheus', [port('9090')])], []),
  ],
});

test('one graph per deployment, never mixed', () => {
  const d = dom(dt.declaredTopologyHtml(results()));
  const graphs = [...d.querySelectorAll('[data-graph]')];
  assert.equal(graphs.length, 2);
  assert.deepEqual(graphs.map((g) => g.getAttribute('data-graph-group')), ['quickstart', 'freshstart']);
  // freshstart has its own egeria node; quickstart's wire is not drawn in it.
  assert.equal(graphs[1].querySelectorAll('[data-graph-edge]').length, 0);
  assert.equal(graphs[0].querySelectorAll('[data-graph-edge]').length, 1);
  assert.equal(graphs[0].querySelectorAll('[data-graph-node]').length, 3);
});

test('every deployment says what an arrow means and what nothing declared means, on the surface', () => {
  const d = dom(dt.declaredTopologyHtml(results()));
  for (const g of d.querySelectorAll('[data-graph]')) {
    assert.match(text(g.querySelector('[data-graph-caption]')), /declared startup dependencies, not data flow/);
    assert.match(text(g.querySelector('[data-graph-caption]')), /Dockerfile EXPOSE/);
    assert.match(text(g.querySelector('[data-graph-absence]')), /not that nothing exists/);
  }
  // The deployment with no wire also says so.
  const fresh = d.querySelectorAll('[data-graph]')[1];
  assert.match(text(fresh.querySelector('[data-graph-absence]')), /No startup dependency was declared in this deployment/);
  assert.match(text(d.querySelectorAll('[data-graph]')[0].querySelector('[data-graph-caption]')), /2 deployment artifacts read/);
  assert.match(text(d.querySelector('[data-graph-as-of]')), /as of the architecture recovery run of /);
});

test('a protocol is shown only when an artifact stated one', () => {
  const d = dom(dt.declaredTopologyHtml(results()));
  const notes = (id) => text([...d.querySelectorAll('[data-graph-node]')].find((n) => n.getAttribute('data-graph-node') === id).querySelector('[data-node-notes]'));
  assert.equal(notes('egeria'), '9443/https');
  assert.equal(notes('postgres'), '5432');            // no "/postgres-ish" guess from the number
  assert.equal(notes('kafka'), 'no declared port');
  assert.equal(dt.deploymentGraphSpec(results().interfaces[0], '').nodes[1].notes[0].text, '5432');
});

test('not measured is not drawn as measured-nothing', () => {
  const nm = dom(dt.declaredTopologyHtml({ _status: { state: 'never_run', hint: 'No architecture recovery yet — run the analysis.' } }));
  assert.match(text(nm), /Not measured: No architecture recovery yet/);
  assert.equal(nm.querySelectorAll('[data-graph]').length, 0);
  const none = dom(dt.declaredTopologyHtml({ interfaces: [], surveyed_at: '2026-10-20T12:00:00' }));
  assert.match(text(none.querySelector('[data-topology-none]')), /No deployment artifact declared a port or a startup dependency/);
  assert.match(text(none.querySelector('[data-topology-none]')), /not that nothing exists/);
  assert.doesNotMatch(text(none), /Not measured/);
});

test('mount: reads once per slug inside a minute, says a failed read, drops a late answer', async () => {
  dt.clearTopologyCache();
  let calls = 0;
  const host = document.createElement('div');
  await dt.mountDeclaredTopology(host, 's1', { fetchResults: async () => { calls++; return results(); } });
  assert.equal(host.querySelectorAll('[data-graph]').length, 2);
  await dt.mountDeclaredTopology(host, 's1', { fetchResults: async () => { calls++; return results(); } });
  assert.equal(calls, 1);
  dt.clearTopologyCache();
  const bad = document.createElement('div');
  await dt.mountDeclaredTopology(bad, 's2', { fetchResults: async () => { throw new Error('boom'); } });
  assert.match(text(bad.querySelector('[data-topology-error]')), /could not be read: boom/);
  assert.equal(bad.querySelectorAll('[data-graph]').length, 0);
  const late = document.createElement('div');
  await dt.mountDeclaredTopology(late, 's3', { fetchResults: async () => results(), stale: () => true });
  assert.equal(late.querySelectorAll('[data-graph]').length, 0);
});

test('wired in both places: the Analysis architecture board and Curate Structure', () => {
  const base = new URL('../../resource_explorer/web/static/next/', import.meta.url);
  const app = readFileSync(new URL('app.js', base), 'utf8');
  const curate = readFileSync(new URL('stages/curate.js', base), 'utf8');
  assert.match(app, /boardId === 'architecture_overview'[^\n]*data-declared-topology-host/);
  assert.match(app, /mountDeclaredTopology\(h, slug/);
  assert.match(curate, /data-curate-ports-wires/);
  assert.match(curate, /mountDeclaredTopology\(/);
});
