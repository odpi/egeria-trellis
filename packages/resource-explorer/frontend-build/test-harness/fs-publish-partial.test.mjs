// re/lookup-failure-never-creates round 3: a file-system publish or hybrid survey that skipped files (their
// look-up could not be made, so nothing was created for them) is PARTIAL, never a plain success toast.
// Loads the two real handlers (and any helper they call) out of index.html into a plain vm context with
// stubbed fetch/DOM/showToast; no jsdom needed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = readFileSync(new URL('../../resource_explorer/web/static/index.html', import.meta.url), 'utf8');

function fnSource(name) {
  const at = HTML.search(new RegExp(`(async\\s+)?function\\s+${name}\\s*\\(`));
  if (at < 0) return '';
  let i = HTML.indexOf('{', at), depth = 0;
  for (; i < HTML.length; i++) {
    if (HTML[i] === '{') depth++;
    else if (HTML[i] === '}' && --depth === 0) break;
  }
  return HTML.slice(at, i + 1);
}

function sandbox(body) {
  const toasts = [];
  const el = () => ({ value: '', checked: true, disabled: false, textContent: '',
                      classList: { add() {}, remove() {} } });
  const ctx = {
    toasts,
    _surveyingFsSlug: 'fs1', selectedFilesystem: null,
    document: { getElementById: () => el() },
    fetch: async () => ({ ok: true, json: async () => body }),
    hidePublishFsModal() {}, hideSurveyFsModal() {}, loadFilesystems: async () => {}, selectFilesystem() {},
    showToast: (type, message) => toasts.push({ type, message }),
  };
  vm.createContext(ctx);
  for (const name of ['fsPublishOutcome', 'submitPublishFs', 'submitSurveyFs']) {
    const src = fnSource(name);
    if (src) vm.runInContext(src.replace(/^(async\s+)?function\s+(\w+)/, (m, a, n) => `${n} = ${a || ''}function ${n}`), ctx);
  }
  return ctx;
}

const PARTIAL = { status: 'partial', files_not_checked: { 'a.csv': 'could not be made' } };

for (const handler of ['submitPublishFs', 'submitSurveyFs']) {
  test(`${handler}: skipped files make the toast partial, with the count`, async () => {
    const ctx = sandbox(PARTIAL);
    await vm.runInContext(`${handler}()`, ctx);
    assert.equal(ctx.toasts.length, 1);
    assert.notEqual(ctx.toasts[0].type, 'success');
    assert.match(ctx.toasts[0].message, /1 file\(s\) not checked, not created/);
  });

  test(`${handler}: nothing skipped is still a plain success`, async () => {
    const ctx = sandbox({ status: 'ok', files_not_checked: {} });
    await vm.runInContext(`${handler}()`, ctx);
    assert.equal(ctx.toasts[0].type, 'success');
    assert.doesNotMatch(ctx.toasts[0].message, /not checked/);
  });
}
