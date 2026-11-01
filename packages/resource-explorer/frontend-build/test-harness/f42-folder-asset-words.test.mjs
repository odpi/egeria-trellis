/** F42 / W27: the Catalog and Survey row names the element its pointer holds by the kind the server reads
 *  (`asset_kind`): a file system's FileFolder is a "folder asset", never a "database asset". Pure render. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered, makeDomEnvironment } from './dom-harness.mjs';

ensureLoaderRegistered();
const row = (over) => ({
  qualified_name: 'FileSurvey:CatalogAndSurvey', display_name: 'Catalog and Survey', kind: 'catalog_and_survey',
  target: 'server', run: { state: 'registered', egeria_status: '', message: '', read_at: '' },
  asset_guid: '060e5efb-0000-0000-0000-000000000001', ...over,
});
const { document } = makeDomEnvironment();     // the module reads window at import
const words = (html) => {
  document.body.innerHTML = html;
  return document.body.textContent.replace(/\s+/g, ' ');
};

test('a file folder reads "folder asset", never "database asset"', async () => {
  const { nativeSurveyStatusHtml } = await import('/static/next/stages/native-surveys.js');
  const t = words(nativeSurveyStatusHtml(row({ asset_kind: 'folder asset' })));
  assert.match(t, /registered with Egeria · folder asset 060e5efb/);
  assert.doesNotMatch(t, /database asset/);
});

test('a database keeps "database asset"; a row with no kind says only "asset"', async () => {
  const { nativeSurveyStatusHtml } = await import('/static/next/stages/native-surveys.js');
  assert.match(words(nativeSurveyStatusHtml(row({ asset_kind: 'database asset' }))), /· database asset 060e5efb/);
  const bare = words(nativeSurveyStatusHtml(row({})));
  assert.match(bare, /· asset 060e5efb/);
  assert.doesNotMatch(bare, /database asset/);
});
