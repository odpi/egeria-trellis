/** EA Phase 0, S2: untrusted Markdown is rendered inert, and the feedback buttons tell the truth.
 *
 *  Runs the REAL vendored marked + DOMPurify and the REAL render.js in a jsdom window, the REAL dashTile
 *  (static/dashboards_view.js), and the REAL feedbackClick cut out of the shipped index.html (not
 *  re-typed), so a change to the page is what gets tested. Only fetch and Auth are stubbed.
 *
 *  Run:  node --test advisor/web/static/test/render.test.mjs
 *  jsdom is not an EA dependency: it is resolved from $EA_JSDOM_DIR (a node_modules directory, e.g.
 *  <trellis>/packages/resource-explorer/frontend-build/node_modules after `npm ci` there), or from that
 *  same directory in this or the main trellis checkout. Without it every test is skipped with that
 *  reason, UNLESS EA_RENDER_HARNESS_REQUIRED=1: then the missing jsdom is a failure, because a
 *  security harness that silently skips is not a pass. The gate sets both variables.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, '..');
const REPO = path.resolve(STATIC, '../../../../..');   // trellis root (or a worktree of it)

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
const REQUIRED = process.env.EA_RENDER_HARNESS_REQUIRED === '1';
const SKIP = jsdom ? false : 'jsdom not found (set EA_JSDOM_DIR to a node_modules directory holding jsdom)';

test('the harness can run (jsdom found)', { skip: !REQUIRED && SKIP }, () => {
  assert.ok(jsdom, `EA_RENDER_HARNESS_REQUIRED=1 but ${SKIP}`);
});

const read = (rel) => fs.readFileSync(path.join(STATIC, rel), 'utf8');
const INDEX = read('index.html');

/** Cut a top-level `function name(` / `async function name(` out of index.html, braces balanced. */
function cut(name) {
  const m = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(INDEX);
  assert.ok(m, `function ${name} not found in index.html`);
  let i = INDEX.indexOf('{', m.index), depth = 0;
  for (; i < INDEX.length; i++) {
    if (INDEX[i] === '{') depth++;
    else if (INDEX[i] === '}' && --depth === 0) break;
  }
  return INDEX.slice(m.index, i + 1);
}
function cutConst(name) {
  const m = new RegExp(`^const ${name} = .*;$`, 'm').exec(INDEX);
  assert.ok(m, `const ${name} not found in index.html`);
  return m[0];
}

function makeWindow({ withPurify = true, withMarked = true } = {}) {
  const dom = new jsdom.JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
  const w = dom.window;
  if (withMarked) w.eval(read('vendor/marked.min.js'));
  if (withPurify) w.eval(read('vendor/purify.min.js'));
  w.eval(read('render.js'));
  return w;
}

/** Every way the rendered HTML could still run script. */
function assertInert(w, html) {
  const box = w.document.createElement('div');
  box.innerHTML = html;
  assert.equal(box.querySelectorAll('script, iframe, object, embed, svg, style, form, input, button').length, 0, html);
  for (const el of box.querySelectorAll('*')) {
    for (const a of el.attributes) {
      assert.ok(!/^on/i.test(a.name), `event attribute ${a.name} survived: ${html}`);
      assert.ok(!/^\s*(javascript|vbscript|data):/i.test(a.value) || (el.tagName === 'IMG' && a.name === 'src' && /^data:image\//i.test(a.value)),
        `dangerous URL in ${a.name}: ${html}`);
      assert.notEqual(a.name, 'style', `inline style survived: ${html}`);
    }
  }
}

const HOSTILE = [
  '<img src=x onerror=alert(1)>',
  '<a href="javascript:alert(1)">click</a>',
  '<script>alert(1)</script>',
  '[click](javascript:alert(1))',
  '[click](JaVaScRiPt:alert(1))',
  '<svg onload=alert(1)><circle/></svg>',
  '<iframe src="https://example.com"></iframe>',
  '<div style="background:url(javascript:alert(1))" onclick="alert(1)">x</div>',
  '<a href="data:text/html,<script>alert(1)</script>">x</a>',
  '<form action="https://evil.example"><input name=q><button>go</button></form>',
  '<details open ontoggle=alert(1)>x</details>',
  '<math><mi xlink:href="javascript:alert(1)">x</mi></math>',
  '```\n</code></pre><img src=x onerror=alert(1)>\n```',
];

test('hostile HTML and Markdown are neutralised', { skip: SKIP }, () => {
  const w = makeWindow();
  for (const src of HOSTILE) {
    const out = w.renderMarkdown(src);
    assertInert(w, out);
    // Escaped text (e.g. inside a code block) may still READ "onerror"; what matters is the DOM above.
    assert.ok(!/<(script|iframe|svg|img[^>]*\son)/i.test(out), `${src} -> ${out}`);
  }
});

test('normal Markdown survives: headings, tables, code, links, the TODO marker', { skip: SKIP }, () => {
  const w = makeWindow();
  const md = [
    '# Title', '## Sub', '',
    'Some **bold** and *em* and `code`.', '',
    '| A | B |', '|---|---|', '| 1 | 2 |', '',
    '```python', 'print("hi")', '```', '',
    '```mermaid', 'graph TD; A-->B', '```', '',
    '```vega-lite', '{"mark": "bar"}', '```', '',
    '- one', '- two', '',
    '[Egeria](https://egeria-project.org) and [local](/static/x.html) and [mail](mailto:a@b.c)', '',
    '<span class="todo-fill">⚠ fill in</span>',
  ].join('\n');
  const box = w.document.createElement('div');
  box.innerHTML = w.renderMarkdown(md);
  assert.equal(box.querySelector('h1').textContent, 'Title');
  assert.equal(box.querySelector('h2').textContent, 'Sub');
  assert.equal(box.querySelectorAll('table td').length, 2);
  assert.ok(box.querySelector('code.language-python'));
  // The fenced-block detection index.html runs after rendering relies on these classes.
  assert.equal(box.querySelector('code.language-mermaid').textContent.trim(), 'graph TD; A-->B');
  assert.deepEqual(JSON.parse(box.querySelector('code.language-vega-lite').textContent), { mark: 'bar' });
  assert.equal(box.querySelectorAll('li').length, 2);
  assert.ok(box.querySelector('span.todo-fill'));
  const [ext, local, mail] = box.querySelectorAll('a');
  assert.equal(ext.getAttribute('href'), 'https://egeria-project.org');
  assert.equal(ext.getAttribute('rel'), 'noopener noreferrer');
  assert.equal(ext.getAttribute('target'), '_blank');
  assert.equal(local.getAttribute('href'), '/static/x.html');
  assert.equal(local.getAttribute('rel'), 'noopener noreferrer');
  assert.equal(local.hasAttribute('target'), false);
  assert.equal(mail.getAttribute('href'), 'mailto:a@b.c');
});

test('without DOMPurify or marked the text is shown as written, never as HTML', { skip: SKIP }, () => {
  for (const opts of [{ withPurify: false }, { withMarked: false }]) {
    const w = makeWindow(opts);
    const out = w.renderMarkdown('<img src=x onerror=alert(1)>\nline 2');
    assert.equal(out, '&lt;img src=x onerror=alert(1)&gt;<br>line 2');
  }
});

test('a dashboard text placement from Egeria renders inert (the real dashTile, static/dashboards_view.js)', { skip: SKIP }, () => {
  const w = makeWindow();
  w.eval([cut('_esc'), cut('preprocessMarkdown'), 'window.preprocessMarkdown = preprocessMarkdown;'].join('\n'));
  w.eval(read('dashboards_view.js'));     // B7a moved the dashboards UI out of index.html
  const sheet = { name: 's', heading: 'S' };
  const p = { kind: 'text', span: '1', emphasis: 'kpi',
    content: '## Note\n<img src=x onerror="window.__pwned=1">\n<script>window.__pwned=2</script>\n[x](javascript:window.__pwned=3)\n<!-- TODO: fill in -->' };
  const tile = w.EADashboards.dashTile(sheet, p);
  w.document.body.appendChild(tile);
  assertInert(w, tile.innerHTML);
  assert.equal(tile.querySelector('h2').textContent, 'Note');
  assert.ok(tile.querySelector('span.todo-fill'), 'preprocessMarkdown marker kept');
  assert.equal(w.__pwned, undefined);
});

function feedbackWindow(fetchImpl) {
  const w = makeWindow();
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.fetch = fetchImpl;
  w.eval([cut('_feedbackAnswerText'), cut('feedbackClick'), 'window.feedbackClick = feedbackClick;'].join('\n'));
  // The shape _applyQueryResult builds: answer body (.msg-answer), then the button row.
  const wrap = w.document.createElement('div');
  wrap.innerHTML = '<div class="text-xs">Assistant</div><div class="msg-answer">The answer text.</div>';
  const fb = w.document.createElement('div');
  fb.dataset.feedbackRow = '1';
  const btn = w.document.createElement('button');
  Object.assign(btn.dataset, { vote: '1', q: 'what is x', qt: 'explanation', intentOverride: 'auto' });
  btn.textContent = '👍';
  btn.addEventListener('click', w.feedbackClick);
  fb.appendChild(btn);
  wrap.appendChild(fb);
  w.document.body.appendChild(wrap);
  return { w, fb, btn };
}
const settle = () => new Promise((r) => setTimeout(r, 0));

test('feedback: success only on res.ok, with the answer text read at click time', { skip: SKIP }, async () => {
  let sent = null;
  const { fb, btn } = feedbackWindow(async (url, init) => { sent = JSON.parse(init.body); return { ok: true, status: 200, json: async () => ({ status: 'ok' }) }; });
  btn.click();
  assert.equal(fb.textContent, 'Sending…', 'the click is acknowledged at once');
  await settle(); await settle();
  assert.equal(fb.textContent, '👍 Thanks!');
  assert.equal(sent.response_text, 'The answer text.');
  assert.equal(sent.vote, 1);
  assert.equal(sent.intent_override, 'auto');
});

test('feedback: a server refusal shows "Not recorded" with the reason, and Retry restores the buttons', { skip: SKIP }, async () => {
  let calls = 0;
  const { fb, btn } = feedbackWindow(async () => { calls++; return { ok: false, status: 500, json: async () => ({ detail: 'could not record your feedback: disk full' }) }; });
  btn.click();
  await settle(); await settle();
  assert.match(fb.textContent, /^Not recorded: HTTP 500: could not record your feedback: disk full/);
  assert.ok(!fb.textContent.includes('Thanks'));
  const retry = [...fb.querySelectorAll('button')].find((b) => b.textContent === 'Retry');
  retry.click();
  assert.ok(fb.contains(btn), 'the vote buttons are back');
  btn.click();
  await settle(); await settle();
  assert.equal(calls, 2);
});

test('feedback: a network failure is shown, not swallowed', { skip: SKIP }, async () => {
  const { fb, btn } = feedbackWindow(async () => { throw new Error('Failed to fetch'); });
  btn.click();
  await settle(); await settle();
  assert.match(fb.textContent, /^Not recorded: Failed to fetch/);
});

// ── Admin link: shown only when /api/auth/me says is_admin === true; fails closed ─────────────

function adminWindow(fetchImpl) {
  const w = makeWindow();
  w.Auth = { getHeaders: () => ({ Authorization: 'Bearer t' }) };
  w.fetch = fetchImpl;
  // The real header markup and the real refreshAdminLink, cut from index.html.
  const link = /<a id="admin-link"[\s\S]*?<\/a>/.exec(INDEX);
  assert.ok(link, 'admin-link markup not found in index.html');
  w.document.body.innerHTML = link[0];
  const seq = /^let _adminCheckSeq = 0;$/m.exec(INDEX);
  assert.ok(seq, '_adminCheckSeq not found in index.html');
  w.eval([seq[0].replace('let ', 'var '), cut('refreshAdminLink'), 'window.refreshAdminLink = refreshAdminLink;'].join('\n'));
  return { w, link: w.document.getElementById('admin-link') };
}
const meResponse = (status, body) => async () => ({
  ok: status >= 200 && status < 300, status,
  json: async () => { if (body instanceof Error) throw body; return body; },
});

test('admin link: hidden in the page markup before any check', { skip: SKIP }, () => {
  const { link } = adminWindow(async () => { throw new Error('not called'); });
  assert.equal(link.hidden, true);
  assert.equal(link.getAttribute('href'), '/admin');
});

test('admin link: shown only for is_admin === true', { skip: SKIP }, async () => {
  const { w, link } = adminWindow(meResponse(200, { authenticated: true, is_admin: true, user_id: 'erinoverview' }));
  assert.equal(await w.refreshAdminLink(), true);
  assert.equal(link.hidden, false);
});

test('admin link: hidden (fail closed) for every other answer', { skip: SKIP }, async () => {
  const cases = {
    'is_admin false': meResponse(200, { authenticated: true, is_admin: false }),
    'signed out': meResponse(200, { authenticated: false, is_admin: false }),
    'is_admin missing (pre-S1 server)': meResponse(200, { authenticated: true, user_id: 'x' }),
    'is_admin "true" (a string)': meResponse(200, { authenticated: true, is_admin: 'true' }),
    'is_admin 1': meResponse(200, { authenticated: true, is_admin: 1 }),
    'null body': meResponse(200, null),
    'unparseable JSON': meResponse(200, new SyntaxError('bad json')),
    '401': meResponse(401, { detail: 'login_required', is_admin: true }),
    '500 that says admin': meResponse(500, { is_admin: true }),
    'network error': async () => { throw new TypeError('Failed to fetch'); },
  };
  for (const [name, fetchImpl] of Object.entries(cases)) {
    const { w, link } = adminWindow(fetchImpl);
    link.hidden = false;                       // even if it was showing, a bad answer hides it
    assert.equal(await w.refreshAdminLink(), false, name);
    assert.equal(link.hidden, true, name);
  }
});

test('admin link: a slow earlier "admin" answer cannot override a later sign-out', { skip: SKIP }, async () => {
  let release;
  const answers = [
    () => new Promise((r) => { release = () => r({ ok: true, status: 200, json: async () => ({ is_admin: true }) }); }),
    async () => ({ ok: true, status: 200, json: async () => ({ authenticated: false, is_admin: false }) }),
  ];
  const { w, link } = adminWindow(() => answers.shift()());
  const first = w.refreshAdminLink();          // signed in as admin, answer delayed
  await w.refreshAdminLink();                  // then signed out
  release();
  await first;
  assert.equal(link.hidden, true);
});
