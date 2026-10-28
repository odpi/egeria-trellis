/** The header names the signed-in user in full: never clipped, never wrapped (2026-10-16, 'rover'). */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../../resource_explorer/web/static/next/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../../resource_explorer/web/static/next/app.js', import.meta.url), 'utf8');

test('the #whoami button cannot shrink, clip or wrap its text', () => {
  const tag = html.match(/<button id="whoami"[^>]*>/s)[0];
  const cls = tag.match(/class="([^"]*)"/)[1].split(/\s+/);
  assert.ok(cls.includes('shrink-0'), 'a flex row must not squeeze the user id');
  assert.ok(cls.includes('whitespace-nowrap'));
  for (const bad of ['truncate', 'overflow-hidden', 'text-ellipsis']) assert.ok(!cls.includes(bad), bad);
});

test('the header writes the whole user id, with no slicing', () => {
  const line = app.match(/\$\('whoami'\)\.textContent =\s*\n?\s*\([^;]*;/s)[0];
  assert.ok(!/slice|substring|substr|\.\.\./.test(line), line);
  assert.match(line, /state\.me\.user_id/);
});
