/** Delete an investigation: the control, the in-place confirm with the name, the two Egeria choices for a promoted
 *  investigation (and one for an unpromoted one), the list after a delete, and the persistent cue when Egeria stops
 *  the delete part-way. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML = fs.readFileSync(path.resolve(HERE, '../../resource_explorer/web/static/next/index.html'), 'utf8');
const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const PREVIEW_OK = { ok: true, blocked: false, blocked_words: '', error: '', project_ours: true, foreign: [], ours: [], left: [], token: '' };

async function setUp({ promoted = false, isMine = true, role = 'user', del, deleteStop = null, preview = PREVIEW_OK } = {}) {
  const calls = [];
  const { document, window } = makeDomEnvironment();
  let deleted = false;
  let serverStop = deleteStop;       // what the server's activity log says about a stopped delete (survives a reload)
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (opts.method === 'DELETE') {
      calls.push(u);
      const out = del ? del(u) : { ok: true, local_deleted: true, choice: u.includes('egeria=true') ? 're_and_egeria' : 're_only', deleted: [], remaining: [], left: [], errors: [] };
      if (out.local_deleted) deleted = true;
      else if (!out.status) serverStop = { ...out, ts: 'now' };   // the server wrote the error row the page reads back
      if (out.status) return { ok: false, status: out.status, json: async () => ({ detail: out.detail }), text: async () => out.detail };
      return ok(out);
    }
    if (/\/delete-preview$/.test(u)) { calls.push(u); return preview.status ? { ok: false, status: preview.status, json: async () => ({ detail: preview.detail }), text: async () => preview.detail } : ok(preview); }
    if (u === '/api/journal/investigation/c360') return ok({ entries: [], suggested_to: [] });
    if (/feedback-state/.test(u)) return ok({ notes: [], to_send: 0 });
    if (/\/members$/.test(u)) return ok([]);
    if (/\/next-steps$/.test(u)) return ok({ steps: [], complete: true });
    if (/\/dispositions$/.test(u)) return ok({});
    if (/\/link-status$/.test(u)) return ok({});
    if (u === '/api/investigations/purposes') return ok({ purposes: [] });
    if (u === '/api/investigations/classifications') return ok({ classifications: [], bindings: [] });
    if (/^\/api\/investigations\/c360$/.test(u)) {
      return ok({ slug: 'c360', display_name: 'Customer 360', status: 'open', description: 'd', project_classification: 'StudyProject',
        purposes: [], visibility: 'shared', is_mine: isMine, egeria_project_guid: promoted ? 'proj-guid' : null,
        egeria_project_qualified_name: promoted ? 'Project::Investigation::c360' : '', delete_stop: serverStop });
    }
    if (/^\/api\/investigations\/\?/.test(u)) return ok(deleted ? [] : []);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, '', '/next');
  document.body.innerHTML = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/)[1].replace(/<script[\s\S]*?<\/script>/g, '');
  const api = await import('/static/re-api.js'); api.clearCache();
  const app = await import('/static/next/app.js');
  const inv = await import('/static/next/stages/investigation.js');
  app.state.stage = 'investigation'; app.state.me = { user_id: 'me', role }; app.state.investigations = [];
  inv.openInvestigationDetail('c360');
  await inv.renderInvestigation();
  await tick();
  return { document, calls, inv };
}

/** Answer the next dialog of `kind` ('confirm' | 'choice') with `pick` (a data attribute suffix) and record its text. */
function answerNext(document, kind, pick) {
  const asked = {};
  const attr = kind === 'confirm' ? 'data-confirm-dialog' : 'data-choice-dialog';
  const obs = new document.defaultView.MutationObserver(() => {
    document.querySelectorAll(`[${attr}]`).forEach((box) => {
      if (box.dataset.answered) return;
      box.dataset.answered = '1';
      asked.title = text(box.querySelector('[data-dialog-title]'));
      asked.message = text(box.querySelector('[data-dialog-message]'));
      asked.buttons = [...box.querySelectorAll('button')].map((b) => text(b));
      asked.disabled = [...box.querySelectorAll('button[disabled]')].map((b) => text(b));
      asked.titles = Object.fromEntries([...box.querySelectorAll('button')].map((b) => [text(b), b.getAttribute('title') || '']));
      box.querySelector(`[${pick}]`).click();
    });
  });
  obs.observe(document.body, { childList: true, subtree: true });
  return asked;
}

test('the owner sees Delete; a stranger does not; an admin or curator does', async () => {
  assert.ok((await setUp({ isMine: true })).document.querySelector('[data-act="inv-delete"]'));
  assert.equal((await setUp({ isMine: false })).document.querySelector('[data-act="inv-delete"]'), null);
  assert.ok((await setUp({ isMine: false, role: 'admin' })).document.querySelector('[data-act="inv-delete"]'));
  assert.ok((await setUp({ isMine: false, role: 'curator' })).document.querySelector('[data-act="inv-delete"]'));
});

test('an unpromoted investigation: an in-place confirm with its name, permanent, nothing in Egeria to delete', async () => {
  const t = await setUp({ promoted: false });
  const asked = answerNext(t.document, 'confirm', 'data-confirm-yes');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.match(asked.title, /Delete 'Customer 360'\?/);
  assert.match(asked.message, /permanent/i);
  assert.match(asked.message, /never promoted/);
  assert.deepEqual(t.calls, ['/api/investigations/c360?egeria=false']);
});

test('a promoted investigation offers the two choices in plain words with what each leaves behind', async () => {
  const t = await setUp({ promoted: true });
  const asked = answerNext(t.document, 'choice', 'data-choice-cancel');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.match(asked.title, /Delete 'Customer 360'\?/);
  assert.match(asked.message, /permanent/i);
  assert.ok(asked.buttons.includes('Delete in Resource Explorer only'));
  assert.ok(asked.buttons.includes('Delete in Resource Explorer, archive in Egeria'));
  assert.match(asked.message, /Project, its working-set Collection and its journal Notes stay\./);
  assert.match(asked.message, /Archives the Project in Egeria \(restorable\); its notes, ratings and comments go with it\./);
  assert.match(asked.message, /nothing is deleted in Resource Explorer/);
  assert.deepEqual(t.calls, ['/api/investigations/c360/delete-preview'], 'Cancel deletes nothing: only the read ran');
});

test('"Resource Explorer only" asks for egeria=false; "and Egeria" asks for egeria=true', async () => {
  const a = await setUp({ promoted: true });
  answerNext(a.document, 'choice', 'data-choice-re');
  a.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.deepEqual(a.calls, ['/api/investigations/c360/delete-preview', '/api/investigations/c360?egeria=false']);

  const b = await setUp({ promoted: true, preview: { ...PREVIEW_OK, token: 'tok1' } });
  answerNext(b.document, 'choice', 'data-choice-both');
  b.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.deepEqual(b.calls, ['/api/investigations/c360/delete-preview', '/api/investigations/c360?egeria=true&confirmed=tok1'],
    'the token of the list the person saw goes back with the delete');
});

test('after a delete the page returns to the investigation list and says what was left in Egeria', async () => {
  const t = await setUp({ promoted: true, del: () => ({ ok: true, local_deleted: true, choice: 're_only', deleted: [], remaining: [], left: [], errors: [] }) });
  answerNext(t.document, 'choice', 'data-choice-re');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(250);
  const content = text(t.document.getElementById('content'));
  assert.match(content, /Investigations/);
  assert.equal(t.document.querySelector('[data-act="inv-delete"]'), null);
  const note = text(t.document.querySelector('[data-inv-deleted-note]'));
  assert.match(note, /Deleted 'Customer 360' in Resource Explorer\./);
  assert.match(note, /Left in Egeria: Project proj-guid/);
});

test('a delete Egeria stopped keeps the page, shows a pop-up and a cue that names what remains', async () => {
  const stop = { ok: false, local_deleted: false, choice: 're_and_egeria', egeria_done: false,
    archived: [{ kind: 'Collection', guid: 'n1', name: 'first', words: 'archived in Egeria · 2026-10-21' }],
    remaining: [{ kind: 'Project', guid: 'n2', name: 'second', why: 'refused by Egeria: OPEN-METADATA-SECURITY-403-020 not yours' }],
    left: [], errors: ['Egeria would not archive the Project second: refused by Egeria: OPEN-METADATA-SECURITY-403-020 not yours'] };
  const t = await setUp({ promoted: true, del: () => stop });
  answerNext(t.document, 'choice', 'data-choice-both');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(250);
  assert.ok(t.document.querySelector('[data-inv-failure-popup]'), 'the failure pop-up');
  const cue = text(t.document.querySelector('[data-inv-delete-stop]'));
  assert.match(cue, /delete not finished/);
  assert.match(cue, /still in Resource Explorer/);
  assert.match(text(t.document.querySelector('[data-inv-delete-removed]')), /Collection n1.*archived in Egeria · 2026-10-21/);
  assert.match(text(t.document.querySelector('[data-inv-delete-remains]')), /Project n2.*OPEN-METADATA-SECURITY-403-020/);
  assert.ok(t.document.querySelector('[data-act="inv-delete"]'), 'the investigation page is still there');
  assert.equal(t.document.querySelector('[data-act="inv-delete"]').disabled, false);
  // the cue outlives a redraw of the page: it is read back from the server's record, not held in this page
  await t.inv.refreshOpenInvestigation('c360');
  await tick();
  assert.match(text(t.document.querySelector('[data-inv-delete-stop]')), /Project n2/);
});

test('a reload shows the stop the activity log remembers, with no delete pressed in this session', async () => {
  const stop = { choice: 're_and_egeria', local_deleted: false, errors: ['Egeria would not archive the Project: refused'],
    archived: [{ kind: 'Collection', guid: 'n1', name: 'first' }], unlinked: [{ kind: 'CollectionMembership', guid: 'asset-1', name: 'crm-repo' }],
    remaining: [{ kind: 'Project', guid: 'proj-guid', why: 'refused by Egeria' }],
    foreign: [{ kind: 'Rating', guid: 'r1', name: '5 stars', author: 'mandy', created: '2026-10-02' }], left: [] };
  const t = await setUp({ promoted: true, deleteStop: stop });
  const cue = text(t.document.querySelector('[data-inv-delete-stop]'));
  assert.match(cue, /delete not finished/);
  assert.match(text(t.document.querySelector('[data-inv-delete-foreign]')), /Rating r1 · 5 stars · by mandy · 2026-10-02/);
  assert.match(text(t.document.querySelector('[data-inv-delete-unlinked]')), /CollectionMembership asset-1/);
  assert.match(text(t.document.querySelector('[data-inv-delete-remains]')), /Project proj-guid/);
});

test('no stop recorded: no cue on the page', async () => {
  const t = await setUp({ promoted: true });
  assert.equal(t.document.querySelector('[data-inv-delete-stop]'), null);
});

test('the Egeria choice says a Project RE did not create is left, and the RE-only choice warns about the collision', async () => {
  const t = await setUp({ promoted: true });
  const asked = answerNext(t.document, 'choice', 'data-choice-cancel');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.match(asked.message, /A Project that Resource Explorer did not create is left in Egeria/);
  assert.match(asked.message, /same name and promote it, it will collide with the Project still in Egeria/);
});

test('the confirm names other people\'s items by type, author and date BEFORE anything is written', async () => {
  const foreign = [
    { kind: 'Rating', guid: 'r1', name: '5 stars', author: 'mandy', created: '2026-10-02' },
    { kind: 'Comment', guid: 'c1', name: 'looks right to me', author: 'olive', created: '2026-10-03' }];
  const t = await setUp({ promoted: true, preview: { ...PREVIEW_OK, foreign, token: 'tokX' } });
  const asked = answerNext(t.document, 'choice', 'data-choice-cancel');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.match(asked.message, /2 items on the Project that other people made\. They are archived with it, and can be restored:/);
  assert.match(asked.message, /- Rating by mandy · 2026-10-02 · 5 stars/);
  assert.match(asked.message, /- Comment by olive · 2026-10-03 · looks right to me/);
  assert.deepEqual(t.calls, ['/api/investigations/c360/delete-preview'], 'only the read ran: nothing deleted or archived');
  assert.deepEqual(asked.disabled, [], 'the person can still go ahead');
});

test('with nothing from others on the Project the confirm says so', async () => {
  const t = await setUp({ promoted: true });
  const asked = answerNext(t.document, 'choice', 'data-choice-cancel');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.match(asked.message, /Nothing on the Project was made by anyone else\./);
});

test('while the archive-cascade block is on the Egeria choice is disabled with the block\'s sentence; RE-only still works', async () => {
  const words = 'Egeria archives the whole database tree when any part of it is archived (archive-cascade block, archiveBeanInRepository) · choice kept, nothing sent';
  const t = await setUp({ promoted: true, preview: { ...PREVIEW_OK, blocked: true, blocked_words: words } });
  const asked = answerNext(t.document, 'choice', 'data-choice-re');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(200);
  assert.deepEqual(asked.disabled, ['Delete in Resource Explorer, archive in Egeria']);
  assert.equal(asked.titles['Delete in Resource Explorer, archive in Egeria'], words);
  assert.ok(asked.message.includes(words), 'the block\'s own sentence is in the confirm');
  assert.deepEqual(t.calls, ['/api/investigations/c360/delete-preview', '/api/investigations/c360?egeria=false']);
});

test('a pressed disabled Egeria choice does nothing', async () => {
  const t = await setUp({ promoted: true, preview: { ...PREVIEW_OK, blocked: true, blocked_words: 'blocked words' } });
  const asked = answerNext(t.document, 'choice', 'data-choice-both');   // clicking a disabled button
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(250);
  assert.deepEqual(t.calls, ['/api/investigations/c360/delete-preview'], 'no delete was sent');
  assert.ok(t.document.querySelector('[data-choice-dialog]'), 'the dialog is still waiting for a real choice');
  t.document.querySelector('[data-choice-cancel]').click();
  assert.ok(asked.disabled.length === 1);
});

test('a preview that could not be read offers no Egeria choice, with the reason', async () => {
  const t = await setUp({ promoted: true, preview: { ...PREVIEW_OK, ok: false, error: 'could not read the Project' } });
  const asked = answerNext(t.document, 'choice', 'data-choice-cancel');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(150);
  assert.deepEqual(asked.disabled, ['Delete in Resource Explorer, archive in Egeria']);
  assert.match(asked.message, /Could not read what is attached to the Project, so archiving is not offered: could not read the Project/);
});

test('a finished archive says what Egeria now holds (restorable)', async () => {
  const t = await setUp({ promoted: true, preview: { ...PREVIEW_OK, token: '' },
    del: () => ({ ok: true, local_deleted: true, choice: 're_and_egeria', archived: [{ kind: 'Project', guid: 'proj-guid', with_project: false }, { kind: 'Note', guid: 'n1', with_project: true }],
      remaining: [], left: [], errors: [] }) });
  answerNext(t.document, 'choice', 'data-choice-both');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(300);
  const note = text(t.document.querySelector('[data-inv-deleted-note]'));
  assert.match(note, /archived it in Egeria \(restorable\)/);
  assert.match(note, /Archived in Egeria: Project proj-guid\./);
  assert.doesNotMatch(note, /Note n1/, 'what rode with the Project is not listed one by one');
});

test('a refusal (403) shows the not-permitted cue on the button with the server\'s words', async () => {
  const t = await setUp({ promoted: false, del: () => ({ status: 403, detail: 'Only the person who created this investigation, or a Portal admin or curator, can delete it.' }) });
  answerNext(t.document, 'confirm', 'data-confirm-yes');
  t.document.querySelector('[data-act="inv-delete"]').click();
  await tick(200);
  const btn = t.document.querySelector('[data-act="inv-delete"]');
  assert.match(text(btn), /not permitted/);
  assert.match(btn.innerHTML, /Portal admin or curator/);
});
