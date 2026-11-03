/** C1 walk fixes (2026-10-20): tile figures fit at every text size, charts go single-column, the sidebar's Find
 *  button wraps, and the Look menu opens inside the viewport. jsdom does no layout, so the layout rules are asserted
 *  as the CSS/class rules that produce them, and the rem-based sizes are evaluated at the three text steps. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const NEXT = new URL('../../resource_explorer/web/static/next/', import.meta.url);
const read = (p) => fs.readFileSync(new URL(p, NEXT), 'utf8');
const CONFIG = fs.readFileSync(new URL('../tailwind-next.config.js', import.meta.url), 'utf8');
const ROOT_PX = { A: 16, 'A+': 17.9, 'A++': 20 };   // index.html: html { font-size } at data-textsize unset / 112 / 125
const cls = (el) => [...el.classList];

test('the text-size steps are the ones this test evaluates at', () => {
  const idx = read('index.html');
  assert.match(idx, /html \{ font-size: 16px; \}/);
  assert.match(idx, /data-textsize="112"\] \{ font-size: 17\.9px; \}/);
  assert.match(idx, /data-textsize="125"\] \{ font-size: 20px; \}/);
});

test('figure token is 24px at the smallest step (was 30), in rem so it scales with the text size', () => {
  const m = CONFIG.match(/figure: \['([\d.]+)rem'/);
  assert.ok(m, 'figure token in rem');
  assert.equal(parseFloat(m[1]), 1.5);
  for (const [name, px] of Object.entries(ROOT_PX)) {
    const fig = parseFloat(m[1]) * px;
    assert.ok(fig >= 24 && fig <= 30, `${name}: figure is ${fig}px, still the big number but under the old 30px at the base step`);
  }
});

test('a figure never wraps and is capped by its container: nowrap, a cqi cap, a size-container row', () => {
  const css = read('themes.css');
  assert.match(css, /\[data-ui-tile-row\]\s*\{[^}]*container-type:\s*inline-size/);
  assert.match(css, /\[data-ui-tile\] \[data-tile-figure\]\s*\{[^}]*white-space:\s*nowrap[^}]*font-size:\s*min\(1\.5rem,\s*14cqi\)/);
});

test('tiles: flex-wrap row, content-sized tiles with a 9rem floor, not stretched, no fixed width', async () => {
  const { document } = makeDomEnvironment();
  const m = await import('/static/next/ui-cards.js');
  const d = document.createElement('div');
  d.innerHTML = m.tileRowHtml([1, 2, 3, 4, 5].map((n) => m.tileHtml({ label: 'x', figure: String(n), opens: 'o' })));
  const row = d.querySelector('[data-ui-tile-row]');
  for (const c of ['flex', 'flex-wrap', 'items-start']) assert.ok(cls(row).includes(c), c);
  assert.ok(!cls(row).some((c) => /grid|items-stretch/.test(c)));
  for (const t of d.querySelectorAll('[data-ui-tile]')) {
    assert.ok(cls(t).includes('min-w-[9rem]'));
    assert.ok(!cls(t).some((c) => /^w-|^basis-|^flex-1|^grow/.test(c)), 'no fixed or stretching width');
  }
  const fig = d.querySelector('[data-tile-figure]');
  assert.ok(cls(fig).includes('whitespace-nowrap'));
  const floorPx = Object.entries(ROOT_PX).map(([k, px]) => `${k}:${9 * px}px`);
  assert.deepEqual(floorPx, ['A:144px', 'A+:161.1px', 'A++:180px']);
});

async function glance(run) {
  const { document } = makeDomEnvironment();
  const g = await import('/static/next/stages/understanding-glance.js');
  const env = (x) => ({ database: 'adv', run, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, ...x });
  const html = g.databaseHeadlineHtml({
    dist: env({ schemas: ['a'], table_counts: [5], column_counts: [20] }),
    sizes: env({ tables: ['a'], ranked: [{ name: 'a', row_count: 7063, size_bytes: 1 }], row_counts: [7063],
      totals: { tables: 1, rows: 7063, rows_tables: 1, size_bytes: 5.4 * 1048576, size_tables: 1 } }),
    types: env({ types: ['int'], counts: [1], column_count_total: 1 }),
  });
  const d = document.createElement('div');
  d.innerHTML = html;
  return d;
}

test('a date in a tile: the date is the figure, the time moves to the sub line with who ran it', async () => {
  const d = await glance({ run_id: 1, surveyed_at: '2026-10-19T00:30:50', source: 'local', surveyed_as: 'egeria_admin' });
  const t = d.querySelector('[data-tile-key="surveyed"]');
  assert.equal(t.querySelector('[data-tile-figure]').textContent.replace(/[●\s]/g, ''), '10-19');
  assert.equal(t.querySelector('[data-tile-sub]').textContent.trim(), "00:30 · RE's own survey, as egeria_admin");
});

test('a figure with a unit: "5.4" is the figure and "MB" is small beside it', async () => {
  const d = await glance({ run_id: 1, surveyed_at: '2026-10-19T00:30:50', source: 'local' });
  const f = d.querySelector('[data-tile-key="size"] [data-tile-figure]');
  const unit = f.querySelector('[data-tile-unit]');
  assert.equal(unit.textContent, 'MB');
  assert.equal(f.textContent.replace(/[●\s]/g, ''), '5.4MB');
  assert.match(read('themes.css'), /\[data-tile-unit\]\s*\{[^}]*font-size:\s*0\.5em/);
});

test('Contents charts: one column when two would leave a chart under 32rem, by CSS, not a width check', () => {
  const src = read('stages/understanding.js');
  assert.match(src, /repeat\(auto-fit,minmax\(min\(100%,32rem\),1fr\)\)/);
  assert.match(src, /tabbed\n    \? 'style="grid-template-columns:repeat\(auto-fit,minmax\(min\(100%,32rem\)/, 'the tabbed layout takes the 32rem floor');
  // 32rem at the three steps: a card narrower than twice this holds one chart at the full card width.
  assert.deepEqual(Object.values(ROOT_PX).map((px) => Math.round(32 * px)), [512, 573, 640]);
  // Plotly refits when the plot's own box changes (the grid dropping to one column), not only on window resize.
  assert.match(src, /new ResizeObserver\(refit\)\.observe\(plot\)/);
  assert.match(src, /window\.addEventListener\('resize', refit\)/);
});

test('sidebar top row: chips and the Find button wrap rather than overrun the centre pane', () => {
  const app = read('app.js');
  assert.match(app, /data-sidebar-top-row class="mb-s2 flex min-w-0 flex-wrap items-center/);
  assert.match(app, /ml-auto min-w-0 max-w-full whitespace-normal/);
});

test('Look menu: anchored to the control\'s LEFT edge, above the panes, clamped into the viewport, with every choice', async () => {
  const env = makeDomEnvironment();
  const host = env.document.createElement('span');
  env.document.body.appendChild(host);
  const look = await import('/static/next/look.js');
  look.wireLook(host);
  const menu = host.querySelector('[data-look-menu]');
  assert.ok(cls(menu).includes('left-0') && !cls(menu).includes('right-0'));
  assert.ok(cls(menu).some((c) => /^z-\[(\d+)\]$/.test(c) && Number(c.match(/\d+/)[0]) >= 80), 'above the sidebar and centre pane');
  // Open it, with a viewport that would push it off the right edge: it is shifted back inside.
  Object.defineProperty(env.window, 'innerWidth', { value: 400, configurable: true });
  menu.getBoundingClientRect = () => ({ left: 300, right: 620, width: 320 });
  host.querySelector('[data-look-toggle]').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.equal(menu.hidden, false);
  assert.equal(menu.style.transform, 'translateX(-228px)', '620 -> 392, an 8px margin (8px margin)');
  const text = menu.textContent.replace(/\s+/g, ' ');
  for (const w of ['Dark', 'Light', 'Paper', 'Tell us ›']) assert.ok(text.includes(w), w);
  assert.equal(menu.querySelectorAll('[data-look-option]').length, 3);
  // and off the left edge: shifted right
  menu.getBoundingClientRect = () => ({ left: -90, right: 230, width: 320 });
  host.querySelector('[data-look-toggle]').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  host.querySelector('[data-look-toggle]').dispatchEvent(new env.window.MouseEvent('click', { bubbles: true }));
  assert.equal(menu.style.transform, 'translateX(98px)');
});
