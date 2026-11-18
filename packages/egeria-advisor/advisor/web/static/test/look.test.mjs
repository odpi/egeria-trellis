/** EA's looks (A-U3): switching repaints without a reload, figures are drawn again in the new look, the choice is
 *  remembered under the key RE shares, and EA asks for no activity row and offers no "Tell us".
 *
 *  ea_look.js is loaded as the browser loads it, with its one import (/static/trellis-ui/look.js) pointed at the
 *  real shared file. jsdom is found as render.test.mjs finds it. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');
const SHARED = path.join(REPO, 'packages/trellis-ui/trellis_ui/static');

function findJsdom() {
  const dirs = [process.env.EA_JSDOM_DIR,
    path.join(REPO, 'packages/resource-explorer/frontend-build/node_modules'),
    path.join(REPO, '../trellis/packages/resource-explorer/frontend-build/node_modules')].filter(Boolean);
  for (const d of dirs) {
    if (fs.existsSync(path.join(d, 'jsdom', 'package.json'))) return createRequire(path.join(d, 'x.js'))('jsdom');
  }
  return null;
}
const jsdom = findJsdom();
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';
const read = (rel) => fs.readFileSync(path.join(STATIC, rel), 'utf8');

/** A page like EA's: the header host, one Mermaid diagram and one Vega chart already drawn. */
async function loadPage() {
  const dom = new jsdom.JSDOM('<!doctype html><html><body><header><span data-look-host></span></header></body></html>',
    { url: 'http://ea.test/', runScripts: 'outside-only' });
  const w = dom.window;
  const calls = { init: [], run: [], embed: [], posts: [] };
  w.mermaid = {
    initialize: (cfg) => calls.init.push(cfg.theme),
    run: ({ nodes }) => { for (const n of nodes) { n.setAttribute('data-processed', 'true'); n.innerHTML = '<svg></svg>'; }
      calls.run.push([...nodes].length); },
  };
  w.vegaEmbed = (el, spec, opts) => { calls.embed.push(opts.theme); el.innerHTML = '<canvas></canvas>'; return Promise.resolve(); };
  for (const k of ['window', 'document', 'localStorage', 'CustomEvent', 'MouseEvent', 'HTMLElement', 'Node']) {
    globalThis[k] = k === 'window' ? w : w[k];
  }
  globalThis.fetch = async (url) => { calls.posts.push(url); return { ok: true }; };

  // The module, as the browser sees it, with its import pointed at the shared file (a fresh copy per test).
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ea-look-'));
  fs.copyFileSync(path.join(SHARED, 'look.js'), path.join(tmp, 'look.mjs'));
  fs.writeFileSync(path.join(tmp, 'ea_look.mjs'),
    read('ea_look.js').replace("'/static/trellis-ui/look.js'", "'./look.mjs'"));
  const mod = await import(pathToFileURL(path.join(tmp, 'ea_look.mjs')).href);

  // A diagram and a chart drawn the way index.html draws them, after the module wrapped the libraries.
  const d = w.document.createElement('div');
  d.className = 'mermaid';
  d.textContent = 'graph TD; A-->B';
  w.document.body.appendChild(d);
  w.mermaid.run({ nodes: [d] });
  const chart = w.document.createElement('div');
  w.document.body.appendChild(chart);
  await w.vegaEmbed(chart, { mark: 'bar' }, { theme: 'dark', actions: false });
  return { w, d, chart, calls, mod };
}

const click = (el) => el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));

test('choosing Light: no reload, remembered as trellis.look, and the figures are drawn again in it', { skip: SKIP }, async () => {
  const { w, d, calls } = await loadPage();
  const sentinel = w.document.createElement('i');
  w.document.body.appendChild(sentinel);
  assert.deepEqual(calls.init, ['dark'], 'Mermaid starts in the look on the page (none set: Dark)');
  assert.deepEqual(calls.embed, ['dark']);

  const host = w.document.querySelector('[data-look-host]');
  click(host.querySelector('[data-look-toggle]'));
  click(host.querySelector('[data-look-option="light"]'));

  assert.equal(w.document.documentElement.getAttribute('data-look'), 'light');
  assert.equal(w.document.body.contains(sentinel), true, 'the page was not reloaded');
  assert.equal(w.localStorage.getItem('trellis.look'), 'light', 'the key RE shares');
  assert.deepEqual(calls.init, ['dark', 'default'], 'Mermaid re-initialised in a light theme');
  assert.deepEqual(calls.run, [1, 1], 'the diagram was run again');
  assert.equal(d.getAttribute('data-processed'), 'true');
  assert.deepEqual(calls.embed, ['dark', undefined], 'the chart was embedded again, in Vega\'s light default');
  assert.equal(host.querySelector('[data-look-current]').textContent, 'Light');
});

test('back to Dark draws them dark again; a removed chart is not redrawn', { skip: SKIP }, async () => {
  const { w, chart, calls, mod } = await loadPage();
  chart.remove();
  w.document.documentElement.setAttribute('data-look', 'paper');
  mod.redrawFigures(w.document);
  assert.deepEqual(calls.embed, ['dark'], 'nothing embedded into a chart that left the page');
  assert.equal(calls.init.at(-1), 'default');
  w.document.documentElement.setAttribute('data-look', 'dark');
  mod.redrawFigures(w.document);
  assert.equal(calls.init.at(-1), 'dark');
});

test('EA records no activity row and offers no "Tell us"', { skip: SKIP }, async () => {
  const { w, calls } = await loadPage();
  const host = w.document.querySelector('[data-look-host]');
  const text = host.querySelector('[data-look-menu]').textContent.replace(/\s+/g, ' ');
  for (const name of ['Dark', 'Light', 'Paper']) assert.ok(text.includes(name), name);
  assert.ok(!text.includes('Tell us'), 'no feedback form listens for it in EA');
  assert.equal(host.querySelector('[data-look-tell]'), null);
  click(host.querySelector('[data-look-option="paper"]'));
  assert.deepEqual(calls.posts, [], 'no activity-log POST');
});

for (const page of ['index.html', 'admin.html']) {
  test(`${page}: the look is set before first paint from trellis.look; anything else is Dark`, { skip: SKIP }, () => {
    const html = read(page);
    const m = html.match(/<script>\(function \(\) \{ var l = 'dark';[\s\S]*?<\/script>/);
    assert.ok(m, 'the before-first-paint snippet is in <head>');
    assert.ok(html.indexOf(m[0]) < html.indexOf('tailwindcss'), 'it runs before Tailwind and the stylesheets');
    const code = m[0].replace(/^<script>/, '').replace(/<\/script>$/, '');
    const run = (setup) => {
      const w = new jsdom.JSDOM('<!doctype html><html><body></body></html>', { url: 'http://ea.test/', runScripts: 'outside-only' }).window;
      setup(w);
      w.eval(code);
      return w.document.documentElement.getAttribute('data-look');
    };
    assert.equal(run((w) => w.localStorage.setItem('trellis.look', 'paper')), 'paper');
    assert.equal(run((w) => w.localStorage.setItem('trellis.look', 'neon')), 'dark');
    assert.equal(run((w) => w.localStorage.setItem('re-next.look', 'light')), 'dark', "RE's old key is RE's alone");
    assert.equal(run(() => {}), 'dark');
  });

  test(`${page}: Tailwind reads EA's palette mapping, and the looks load after the page's own <style>`, { skip: SKIP }, () => {
    const html = read(page);
    const at = (s) => { const i = html.indexOf(s); assert.ok(i >= 0, `${page} loads ${s}`); return i; };
    assert.ok(at('tailwindcss') < at('/static/trellis-ui/tokens.js'));
    assert.ok(at('/static/trellis-ui/tokens.js') < at('/static/ea_tailwind.js'));
    assert.ok(!/tailwind\.config\s*=\s*\{\s*darkMode/.test(html), 'the old one-line config is gone');
    const lastStyleInHead = html.lastIndexOf('</style>', html.indexOf('</head>'));
    assert.ok(lastStyleInHead < at('/static/trellis-ui/themes.css'));
    assert.ok(at('/static/trellis-ui/themes.css') < at('/static/ea_looks.css'));
    assert.ok(at('src="/static/ea_look.js') > 0 && /type="module" src="\/static\/ea_look\.js/.test(html));
    assert.ok(html.includes('data-look-host'), 'the header holds the control');
  });
}
