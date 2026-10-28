/* What this press does -- the commit summary for a repository (brief section 2, project owner
 * 2026-10-07: "yes, we need the summary for repos").
 *
 * The database Curate pane has "what this commit does" as a short table with the Catalog button at its
 * top right. The repository gets the same table, before the press and after it:
 *
 *   before   one row per mechanism: who acts, what, how many, from what. The numbers equal the
 *            selection (the lines ticked, the folders and files chosen, the file types ticked).
 *   after    the same table gains a state column derived from PROOF ROWS (the server read the
 *            elements back by GUID): sent / published · read back <when> / not published · <sentence>.
 *            The counts there are the proof rows' counts, never the request's.
 *
 * The button reads "Publish N items" (N = the files and the folders you chose plus the folders needed as
 * containers, all COUNTED FROM THE SELECTION RECORD: `scope.manifest`, never from the page). What blocks it is
 * written directly under it; the re-survey box (off by default) sits under the blockers. This file draws; every state word comes from the plan or the proof summary
 * the server derived, never from the click.
 */
import { ago } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import { esc } from '/static/next/app.js';

export const NOTHING_SELECTED_SENTENCE = 'nothing selected · confirm a line under what it is, or choose folders and files';
export const SCOPE_UNREAD_SENTENCE = 'the selection could not be read · reload to try again';
export const NO_SURVEY_SENTENCE = 'no survey to publish yet · run the first survey';
export const SURVEYED_BEFORE_KEPT_SENTENCE = 'surveyed before 10-07 · nothing kept to publish · re-survey to publish';
export const NO_PROJECT_SENTENCE = 'no Egeria project context · bind this investigation to a project, or decline one';
export const UNBOUND_PROJECT_SENTENCE = 'unbound by reset \u00b7 rebind to recreate \u00b7 bind this investigation to a project, or decline one';
export const RESURVEY_BOX_WORDS = 're-survey stale steps first (adds minutes)';

export const cue = (key, word, title = '') => {
  const e = stateEntry(key);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(key)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};
const num = (n) => `<span class="tnum">${esc(n ?? 0)}</span>`;
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** The counts the table shows before a press. Pure. The file, folder and container counts are the selection
 *  record's own (`scope.manifest`, built by the server from the newest event per locator); this function only
 *  adds the lines confirmed under "what it is". With no record read, every count is 0 and `unread` is true. */
export function manifestCounts({ plan, picks, scope, fileTypePicks }) {
  const m = (scope && scope.manifest) || {};
  const candidates = (plan.what_it_is || []).filter((r) => r.candidate);
  const entities = [...picks].filter((k) => candidates.some((r) => r.kind === k)).length;
  const made = ((plan.made_of || [])[0] || {}).detail || {};
  const files = m.files || 0; const folders = m.folders || 0; const containers = m.containers || 0;
  return {
    entities, files, folders, containers, subs: files + folders,
    blueprints: made.blueprints_accepted || 0, blueprintKinds: made.blueprint_kinds || [],
    notConfirmed: candidates.length - entities,
    notSelected: m.not_selected || 0, proposalsNotAccepted: m.proposals_not_accepted || 0, leftOut: m.left_out || 0,
    publishedEarlier: m.published_earlier || 0,
    // N: what "Publish N items" says. A press with only confirmed lines is still allowed (it publishes the
    // repository and its report); `items` counts both so "nothing selected" means neither.
    n: files + folders + containers,
    items: entities + files + folders + containers,
    unread: !scope || !scope.manifest,
  };
}

/** What stands under the button: each reason this press is refused, one line each (with its remedy). */
export function commitBlockers({ plan, counts, me }) {
  const out = [];
  if (!me) out.push({ key: 'signin', text: 'sign in to publish — the record needs an author' });
  if (plan.survey && plan.survey.exists === false) out.push({ key: 'no_survey', text: plan.survey.sentence || (plan.survey.surveyed_before_kept ? SURVEYED_BEFORE_KEPT_SENTENCE : NO_SURVEY_SENTENCE) });
  if (counts.unread) out.push({ key: 'scope_unread', text: SCOPE_UNREAD_SENTENCE });
  else if (counts.items === 0) out.push({ key: 'nothing', text: NOTHING_SELECTED_SENTENCE });
  // `unbound` (after an Egeria reset) gates exactly as `unset` does, with the same two choices, but says why.
  if (plan.project && plan.project.status === 'unset') out.push({ key: 'no_project', text: NO_PROJECT_SENTENCE });
  if (plan.project && plan.project.status === 'unbound') out.push({ key: 'no_project', text: UNBOUND_PROJECT_SENTENCE });
  if (!plan.in_population) out.push({ key: 'population', text: 'not in Curate’s population' });
  return out;
}

/** The state cell of one row after a press, from the server's `proof_summary`. */
function stateCell(id, ps) {
  if (!ps) return '';
  const subs = ps.sub_resources || {};
  if (id === 'report') {
    const r = ps.report || {};
    if (r.state === 'published') return cue('measured', `published · read back ${ago(r.read_at)}`)
      + (r.reused ? ' <span class="text-provenance text-ink-muted">· reused the report already in Egeria</span>' : '');
    if (r.state === 'sent') return cue('running', 'sent · waiting for Egeria');
    if (r.state === 'not published') return cue('error', `not published · ${r.first || 'Egeria gave no sentence'}`)
      + (r.rest ? ` <details class="inline"><summary class="inline cursor-pointer text-ink-muted underline">details</summary><span class="block whitespace-pre-wrap text-provenance text-ink-muted">${esc(`${r.first} ${r.rest}`)}</span></details>` : '');
    return cue('unrun', 'not sent');
  }
  if (id === 'files' || id === 'folders' || id === 'containers') {
    const r = (subs.by_row || {})[id] || {};
    const bits = [];
    if (r.read_back) bits.push(cue('measured', `${r.read_back} read back`));
    if (r.sent) bits.push(cue('running', `${r.sent} sent, not yet read back`));
    if (r.failed) bits.push(cue('error', `${r.failed} not created${r.first_failure ? ` · ${r.first_failure}` : ''}`));
    return bits.length ? bits.join(' · ') : cue('unrun', 'nothing sent');
  }
  if (id === 'entities') return cue('unrun', 'recorded on the commit', 'These lines are kept on the commit record; Egeria receives the survey report and the elements listed beside it.');
  if (id === 'blueprints') return cue('unrun', 'written when you accept each', 'A blueprint is written to Egeria when it is accepted in what it’s made of.');
  return '';
}

/** The table. `ps` is the proof summary of the latest commit (after a press), else null. */
export function repoManifestHtml({ plan, picks, scope, fileTypePicks, ps = null }) {
  const c = manifestCounts({ plan, picks, scope, fileTypePicks });
  const sv = plan.survey && plan.survey.exists ? plan.survey : null;
  const after = !!ps;
  const row = (id, who, what, many, from, state) => `<div role="row" data-manifest-row="${id}" class="flex items-start gap-s2 py-[2px] text-ink">
    <div role="cell" class="w-[7.5rem] shrink-0 break-words font-semibold">${who}</div>
    <div role="cell" class="min-w-0 flex-1 break-words">${what}</div>
    <div role="cell" class="w-[13rem] shrink-0 break-words tnum">${many}</div>
    <div role="cell" class="w-[11rem] shrink-0 break-words text-ink-muted">${from}</div>
    ${after ? `<div role="cell" data-manifest-state="${id}" class="w-[15rem] shrink-0 break-words">${state}</div>` : ''}</div>`;
  const kindNames = c.blueprintKinds.length ? ` (${c.blueprintKinds.map(esc).join(', ')})` : '';
  const head = `<div role="row" class="flex items-start gap-s2 text-provenance text-ink-muted">
    <div role="columnheader" class="w-[7.5rem] shrink-0"></div><div role="columnheader" class="min-w-0 flex-1">what</div>
    <div role="columnheader" class="w-[13rem] shrink-0">how many</div><div role="columnheader" class="w-[11rem] shrink-0">from</div>
    ${after ? '<div role="columnheader" class="w-[15rem] shrink-0">state</div>' : ''}</div>`;
  const rows = [
    row('report', 'RE publishes', 'the repository asset and its survey report',
      sv ? `1 report · ${num(sv.annotations)} annotations` : (plan.survey && plan.survey.surveyed_before_kept ? 'nothing kept' : 'no survey yet'),
      sv ? `survey of ${esc(String(sv.surveyed_at).slice(0, 10))} (${esc(ago(sv.surveyed_at))})` : '—', stateCell('report', ps)),
    row('entities', 'Egeria gets', 'the lines you confirmed under “what it is”',
      `${num(c.entities)} entit${c.entities === 1 ? 'y' : 'ies'}${c.notConfirmed ? ` · ${num(c.notConfirmed)} proposed, not ticked` : ''}`,
      'your confirmations', stateCell('entities', ps)),
    row('files', 'Egeria gets', 'the files you chose', `${num(c.files)} DataFile${c.files === 1 ? '' : 's'}`,
      'your selection', stateCell('files', ps)),
    row('folders', 'Egeria gets', 'the folders you chose', `${num(c.folders)} FileFolder${c.folders === 1 ? '' : 's'}`,
      'your selection', stateCell('folders', ps)),
    row('containers', 'Egeria gets', 'folders needed as containers', `${num(c.containers)} FileFolder${c.containers === 1 ? '' : 's'}`,
      'the files above', stateCell('containers', ps)),
    row('blueprints', 'Blueprints', 'the blueprints you chose to write', `${num(c.blueprints)}${kindNames}`,
      'your verdicts', stateCell('blueprints', ps)),
    row('left_out', 'Left out', 'nothing in Egeria changes',
      `${num(c.notSelected)} not selected · ${num(c.proposalsNotAccepted)} proposal${c.proposalsNotAccepted === 1 ? '' : 's'} not accepted · ${num(c.leftOut)} left out`, '—', ''),
    row('published_earlier', 'Published earlier', 'kept in Egeria', `${num(c.publishedEarlier)}`, 'previous publishes', ''),
  ].join('');
  return `<div data-repo-manifest role="table" aria-label="What this press does" class="min-w-0 max-w-full overflow-x-auto text-caveat">${head}${rows}</div>`;
}

/** The ONE result of a repository commit, read from the record's persisted steps and state (and the proof
 *  summary beside it), never from the code path the click took (owner, 2026-10-11: acknowledge the press and
 *  validate its success; a failure is a pop-up).
 *    running      the record is queued or running                         "◔ publishing…"
 *    confirmed    finished, nothing failed, and the commit's own read-back (the publish_asset step says
 *                 "published", not "sent, not yet read back"; the proof rows hold no report left unread, no
 *                 element sent-but-unread, none failed)                    "✓ published <date> · confirmed"
 *    unconfirmed  finished, nothing failed, the read-back has not shown it "✓ published <date> · not yet confirmed"
 *    failed       a step failed, or the record says failed                  "◐ not finished · <reason>"
 *  `items` are the failed steps, for the pop-up. */
export function commitVerdict(rec, ps = null) {
  if (!rec) return null;
  const steps = rec.steps || [];
  if (rec.state === 'running' || rec.state === 'queued') return { state: 'running', date: '', reason: '', items: [] };
  const firstSentence = (t) => { const x = String(t || '').trim(); return x.split(/\.\s|\n/)[0].slice(0, 200).trim(); };
  const failedSteps = steps.filter((st) => st.state === 'failed');
  const items = failedSteps.map((st) => ({ what: st.name, word: firstSentence(st.detail || st.reason || st.error) || 'failed' }));
  const sub = (ps && ps.sub_resources) || {};
  if (!failedSteps.length && sub.failed) items.push({ what: 'files and folders', word: `${sub.failed} not created${sub.first_failure ? ` · ${sub.first_failure}` : ''}` });
  if (failedSteps.length || rec.state === 'failed' || sub.failed) {
    const reason = items.length ? items[0].word : firstSentence(rec.reason || rec.error) || 'the run did not finish';
    if (!items.length) items.push({ what: 'the commit', word: reason });
    return { state: 'failed', date: '', reason, items };
  }
  const when = String(rec.finished_at || rec.requested_at || '');
  const date = when.length >= 10 ? when.slice(5, 10) : '';
  const asset = steps.find((st) => st.name === 'publish_asset');
  const readBack = !!asset && asset.state === 'done' && /(^|\s)published(\s|$)/.test(String(asset.detail || '')) && !/not yet read back/.test(String(asset.detail || ''));
  // ONE decider for "same result": the server's repo_publish.confirmation(), carried on the proof summary
  // (the report's own outbox run settles it). No proof summary, or none from the server: not confirmed.
  const conf = (ps && ps.confirm) || null;
  const proofOk = !!conf && conf.state === 'confirmed' && !sub.sent && !sub.failed;
  const finished = steps.length > 0 && steps.every((st) => ['done', 'skipped'].includes(st.state));
  const confirmed = finished && readBack && proofOk;
  const word = confirmed ? '' : (conf && conf.state && conf.state !== 'confirmed' && conf.word ? String(conf.word) : '');
  return { state: confirmed ? 'confirmed' : (conf && conf.state === 'differs' ? 'differs' : 'unconfirmed'), date, reason: '', word, items: [] };
}

/** The one result line of a commit record: cue + short word. The steps are behind "details ▸" (curate.js). */
export function commitResultHtml(rec, ps = null) {
  const v = commitVerdict(rec, ps);
  if (!v) return '';
  const body = v.state === 'running' ? cue('running', 'publishing…')
    : v.state === 'failed' ? cue('partial', `not finished · ${v.reason}`)
      : cue(v.state === 'confirmed' ? 'measured' : v.state === 'differs' ? 'partial' : 'queued',
        v.state === 'confirmed' ? `published ${v.date} · confirmed` : (v.word || `published ${v.date} · not yet confirmed`));
  return `<div data-commit-result="${esc(v.state)}" class="text-caveat text-ink">${body}</div>`;
}

/** The commit header line and the numbered steps (after a press). Counts of steps are the record's. */
export function commitHeaderHtml(rec, labelOf = (n) => n) {
  if (!rec) return '';
  const steps = rec.steps || [];
  const running = steps.find((s) => s.state === 'running');
  const doneN = steps.filter((s) => s.state === 'done' || s.state === 'skipped' || s.state === 'failed').length;
  const failed = steps.filter((s) => s.state === 'failed').length;
  const at = running ? steps.indexOf(running) + 1 : Math.min(steps.length, doneN);
  return `<div data-commit-header class="text-caveat text-ink">Publish · commit ${esc(String(rec.id || '').slice(0, 8))} · step ${at} of ${steps.length}${
    running ? ` · running: ${esc(labelOf(running.name))}` : ''} · ${failed} failed</div>`;
}

/** The button's words: "Publish N items →" with N from the record; "Publish →" when only confirmed lines go. */
export const publishLabel = (counts) => (counts.n ? `Publish ${counts.n} item${counts.n === 1 ? '' : 's'} →` : 'Publish →');

/** The panel: the table, the button at its top right, the blockers directly under the button, the box. */
export function repoCommitPanelHtml({ plan, picks, scope, fileTypePicks, me, resurvey, sentence = '', rec = null, ps = null }) {
  const counts = manifestCounts({ plan, picks, scope, fileTypePicks });
  const blockers = commitBlockers({ plan, counts, me });
  const off = blockers.length > 0;
  const sv = plan.survey && plan.survey.exists ? plan.survey : null;
  const staleLine = sv
    ? `<div data-commit-survey-line class="text-right text-caveat text-ink-muted">survey of ${esc(String(sv.surveyed_at).slice(0, 10))} · ${num(sv.stale_steps || 0)} stale step${sv.stale_steps === 1 ? '' : 's'}</div>` : '';
  return `<div data-repo-commit-panel class="min-w-0 max-w-full">
    <div class="text-answer text-ink">What this press does</div>
    <div class="flex flex-wrap items-start justify-between gap-s3">
      <div class="min-w-0 flex-1">${repoManifestHtml({ plan, picks, scope, fileTypePicks, ps })}</div>
      <div data-curate-go-col class="flex shrink-0 flex-col items-end gap-s1" style="width:260px">
        <button type="button" data-curate-go ${off ? 'disabled' : ''} title="${esc(sentence)}"
          class="rounded-sm border border-accent bg-transparent px-3 py-[3px] text-answer text-accent-ink ${off ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}">${publishLabel(counts)}</button>
        <span data-curate-go-hint class="text-right text-provenance text-ink-muted"></span>
        ${blockers.map((b) => `<div data-commit-blocker="${esc(b.key)}" class="text-right text-caveat text-ink">⚠ ${esc(b.text)}</div>`).join('')}
        ${staleLine}
        <label class="flex min-w-0 max-w-full cursor-pointer items-start gap-[4px] break-words text-right text-caveat text-ink">
          <input type="checkbox" data-commit-resurvey ${resurvey ? 'checked' : ''} ${sv && sv.stale_steps ? '' : 'disabled'}>
          ${esc(RESURVEY_BOX_WORDS)}</label>
      </div>
    </div>
  </div>`;
}
