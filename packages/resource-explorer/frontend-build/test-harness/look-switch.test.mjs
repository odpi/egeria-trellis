/** The look control (Dark / Light / Paper): RULING-THEMES-PORTAL-DARK-LIGHT.md section 1.
 *
 *  Switching sets `data-look` on <html> WITHOUT a reload and fires `re-look-change`; Plotly figures and diagrams
 *  already on screen are repainted by that event; one activity row is asked for per real change; the head snippet
 *  picks the remembered look before first paint and falls back to Dark when storage is unavailable. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

const INDEX = new URL('../../resource_explorer/web/static/next/index.html', import.meta.url);

async function setUp() {
  const env = makeDomEnvironment();
  globalThis.getComputedStyle = env.window.getComputedStyle.bind(env.window);   // tokens() reads it as a bare global
  const posts = [];
  globalThis.fetch = async (url, opts = {}) => { posts.push({ url, body: JSON.parse(opts.body || 'null') }); return { ok: true, json: async () => ({}) }; };
  const host = env.document.createElement('span');
  host.id = 'look';
  env.document.body.appendChild(host);
  await loadAppModule();                                   // app.js: its re-look-change listener is part of what is tested
  const look = await import('/static/next/look.js');       // the same instance app.js imported
  look.wireLook(host);
  return { ...env, host, posts, look };
}

const click = (el) => el.dispatchEvent(new el.ownerDocument.defaultView.MouseEvent('click', { bubbles: true }));

test('choosing Light sets data-look on <html> with no reload, fires re-look-change, and remembers it', async () => {
  const { document, window, host, posts } = await setUp();
  const sentinel = document.createElement('i');
  document.body.appendChild(sentinel);                      // a reload would drop this
  const seen = [];
  document.addEventListener('re-look-change', (e) => seen.push(e.detail));
  window.addEventListener('re-look-change', () => seen.push('window'));   // bubbles up to window too

  assert.equal(host.querySelector('[data-look-current]').textContent, 'Dark', 'Dark when nothing is set');
  click(host.querySelector('[data-look-toggle]'));
  assert.equal(host.querySelector('[data-look-menu]').hidden, false);
  click(host.querySelector('[data-look-option="light"]'));

  assert.equal(document.documentElement.getAttribute('data-look'), 'light');
  assert.deepEqual(seen, [{ look: 'light', previous: 'dark' }, 'window']);
  assert.equal(document.body.contains(sentinel), true, 'the page was not reloaded');
  assert.equal(window.localStorage.getItem('re-next.look'), 'light');
  assert.equal(host.querySelector('[data-look-current]').textContent, 'Light');
  assert.equal(host.querySelector('[data-look-menu]').hidden, true, 'choosing closes the menu');
});

test('each real change asks for exactly one activity row; choosing the current look asks for none', async () => {
  const { host, posts, document } = await setUp();
  click(host.querySelector('[data-look-option="paper"]'));
  click(host.querySelector('[data-look-option="paper"]'));      // already Paper
  click(host.querySelector('[data-look-option="dark"]'));
  assert.deepEqual(posts.map((p) => [p.url, p.body]), [
    ['/api/activity/look-change', { look: 'paper', previous: 'dark' }],
    ['/api/activity/look-change', { look: 'dark', previous: 'paper' }],
  ]);
  assert.equal(document.documentElement.getAttribute('data-look'), 'dark');
});

test('an unknown look changes nothing', async () => {
  const { look, document, posts } = await setUp();
  assert.equal(look.setLook('neon'), false);
  assert.equal(document.documentElement.getAttribute('data-look'), null);
  assert.equal(posts.length, 0);
});

test('the menu offers Dark, Light and Paper with the ruling\'s one-liners and a Tell-us link that opens the feedback form', async () => {
  const { host, document } = await setUp();
  const text = host.querySelector('[data-look-menu]').textContent.replace(/\s+/g, ' ');
  assert.match(text, /Dark the Portal's dark look/);
  assert.match(text, /Light the Portal's light look/);
  assert.match(text, /Paper RE's paper look, kept for this release/);
  assert.match(text, /Which reads best for you\? Tell us ›/);
  const asked = [];
  document.addEventListener('re-open-feedback', (e) => asked.push(e.detail.subject));
  click(host.querySelector('[data-look-option="paper"]'));
  click(host.querySelector('[data-look-toggle]'));
  click(host.querySelector('[data-look-tell]'));
  assert.deepEqual(asked, ['Look: Paper']);
});

test('a change repaints Plotly figures and re-themes diagrams already on screen, once each', async () => {
  const { document, window, host, look } = await setUp();
  const relaid = [];
  window.Plotly = { relayout: (gd, patch) => relaid.push([gd.id, Object.keys(patch)]) };
  const gd = document.createElement('div');
  gd.id = 'chart1';
  gd.className = 'js-plotly-plot';
  document.body.appendChild(gd);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  document.body.appendChild(svg);
  const app = await import('/static/next/app.js');
  app.themeSvgElement(svg, app.tokens());
  assert.equal(svg.querySelectorAll('style[data-re-theme]').length, 1);

  look.setLook('light');
  look.setLook('paper');

  assert.equal(relaid.length, 2, 'one relayout per change');
  assert.equal(relaid[0][0], 'chart1');
  for (const k of ['paper_bgcolor', 'plot_bgcolor', 'colorway', 'font.color', 'xaxis.gridcolor', 'yaxis.tickfont.color']) {
    assert.ok(relaid[0][1].includes(k), `repaints ${k}`);
  }
  assert.equal(svg.querySelectorAll('style[data-re-theme]').length, 1, 're-theming replaces the rules, it does not stack them');
});

function headSnippet() {
  const html = fs.readFileSync(INDEX, 'utf8');
  const m = html.match(/<script>\s*\(function \(\) \{\s*var look = 'dark';[\s\S]*?<\/script>/);
  assert.ok(m, 'index.html carries the before-first-paint snippet in <head>');
  assert.ok(html.indexOf(m[0]) < html.indexOf('href="/static/next/themes.css"'), 'it runs before the stylesheets');
  return m[0].replace(/^<script>/, '').replace(/<\/script>$/, '');
}

test('before first paint: the remembered look is applied; garbage, missing or blocked storage means Dark', () => {
  const code = headSnippet();
  const run = (setup) => {
    const { window } = makeDomEnvironment();
    setup(window);
    window.eval(code);
    return window.document.documentElement.getAttribute('data-look');
  };
  assert.equal(run((w) => w.localStorage.setItem('re-next.look', 'paper')), 'paper');
  assert.equal(run((w) => w.localStorage.setItem('re-next.look', 'light')), 'light');
  assert.equal(run(() => {}), 'dark');
  assert.equal(run((w) => w.localStorage.setItem('re-next.look', 'neon')), 'dark');
  assert.equal(run((w) => Object.defineProperty(w, 'localStorage', { get() { throw new Error('blocked'); } })), 'dark');
});

test('index.html: the control sits beside the text size, and themes.css loads before the Tailwind sheet', () => {
  const html = fs.readFileSync(INDEX, 'utf8');
  assert.ok(html.indexOf('id="look"') > 0 && html.indexOf('id="look"') < html.indexOf('id="textsize"'));
  assert.ok(html.indexOf('href="/static/next/themes.css"') < html.indexOf('href="/static/next/tailwind-next.css"'));
});
