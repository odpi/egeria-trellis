/** Guard: no native dialog anywhere under static/next/.
 *
 *  An embedded browser pane declines window.prompt / confirm / alert silently, so the press does nothing
 *  (2026-10-13: "select all the opea repos and save as work list ... no ability to define a new worklist").
 *  Use confirm-dialog.js: inPagePrompt, inPageConfirm, inPageNotice. Comments may still mention the old calls. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const NEXT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../resource_explorer/web/static/next');
const NATIVE = /(?<![\w$.])(?:(?:window|globalThis|self)\.)?(?:prompt|confirm|alert)\s*\(|\b(?:window|globalThis|self)\.(?:prompt|confirm|alert)\b/;

export function nativeDialogCalls(dir = NEXT_DIR) {
  const found = [];
  const walk = (d) => {
    for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, ent.name);
      if (ent.isDirectory()) { if (ent.name !== 'fonts') walk(full); continue; }
      if (!/\.(js|mjs|html)$/.test(ent.name)) continue;
      let inBlock = false;
      fs.readFileSync(full, 'utf8').split('\n').forEach((line, i) => {
        const t = line.trim();
        if (inBlock) { if (t.includes('*/')) inBlock = false; return; }
        if (t.startsWith('/*')) { if (!t.includes('*/')) inBlock = true; return; }
        if (t.startsWith('//') || t.startsWith('*') || t.startsWith('<!--')) return;
        const code = line.replace(/\s\/\/.*$/, '');
        if (NATIVE.test(code)) found.push(`${path.relative(dir, full)}:${i + 1}: ${t.slice(0, 120)}`);
      });
    }
  };
  walk(dir);
  return found;
}

test('no window.prompt / confirm / alert (or a bare call) remains under static/next/', () => {
  assert.deepEqual(nativeDialogCalls(), []);
});

test('the scan itself catches each form (so a clean result means something)', () => {
  for (const bad of ['window.prompt("x")', 'x = window.confirm(`y`)', 'if (!confirm("z")) return;', 'alert("w")', 'globalThis.alert(1)']) {
    assert.match(bad, NATIVE, bad);
  }
  for (const ok of ['inPagePrompt({})', 'await inPageConfirm({})', 'obj.confirm(1)', 'el.dataset.confirm', 'data-act="confirm"', 'confirmed = true']) {
    assert.doesNotMatch(ok, NATIVE, ok);
  }
});
