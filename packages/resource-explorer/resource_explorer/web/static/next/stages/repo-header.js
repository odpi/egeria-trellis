/* The repository header's scouting tiles and its stale-link banner (parity PI-050, PI-051, PI-052).
 *
 * Pure render functions, no import of app.js, so the header can call them and a test can load them alone.
 *
 * TILES (PI-050): Website, Language, Stars, Forks, Contributors, Last pushed, Size, Security, Deployments,
 * from `GET /api/projects/{slug}/scouting-overview`. A stat GitHub was never asked for is "not read" with
 * the same `?` glyph the rest of the app uses for "not established"; it is NEVER drawn as 0. The server
 * names those stats in `stats_unread` (a stored 0 is a measurement and stays 0). A page that has no
 * `stats_unread` at all (an older server) draws every numeric tile as not read rather than guessing.
 *
 * SIGNAL (PI-051): the one-line verdicts of the scouting-tagged analyses, from
 * `GET /survey-results/summary?phase=scouting`. "No scouting results yet" and "could not read the signal"
 * are different statements and are drawn differently.
 *
 * STALE LINK (PI-052): the banner offers three repairs, all of them `POST /api/egeria/linkage/repo/{slug}/
 * resolve`:
 *   republish  forget the dead pointer, re-run from the stored tables (no fetch), publish a new SurveyReport
 *   resurvey   forget the dead pointer, re-survey from scratch, publish
 *   discard    forget the dead pointer and stop (Resource Explorer's registry only)
 * None of the three archives or deletes anything in Egeria; the destructive bulk routes (`delete-local`,
 * `delete-in-egeria`) are not reachable from here. Republish and resurvey WRITE a new report to Egeria, so
 * they ask first; discard writes nothing to Egeria.
 */
import { stateEntry } from '/static/next/glyphs.js';
import { LINK_ACTIONS } from '/static/next/link-actions.js';
import { ago, PUBLISHED_EARLIER_WORD, PUBLISHED_EARLIER_SENTENCE } from '/static/next/format.js';

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const NOT_READ_WORD = 'not read';
/** The word when the repo has no GitHub figures at all: imported, never surveyed. */
export const NOT_SURVEYED_WORD = 'not yet surveyed';
export const ZERO_PROVES_NOTHING_SENTENCE = 'Zero here is also what a repository that was never asked looks like, so it reads as not read. A non-zero value would show.';
export const NOT_READ_SENTENCE = 'GitHub has not been asked for this yet, or did not answer. Run a scouting scan to read it.';

/** `? not read`, a muted cue; the sentence is on hover. */
export function notReadCue(title = NOT_READ_SENTENCE, word = NOT_READ_WORD) {
  return `<span data-unread class="text-ink-muted" title="${esc(title)}"><span class="font-glyph" aria-hidden="true">${stateEntry('not_established').glyph}</span> ${word}</span>`;
}

function prettyHost(url) {
  try { return new URL(url).host.replace(/^www\./, ''); } catch { return String(url); }
}

/** Enabled/total from GitHub's security_and_analysis object. A value is either the string 'enabled' or an
 *  object with a `status`. Null when the object is empty (never read, or no admin access). */
export function securityCounts(sa) {
  const entries = Object.entries(sa && typeof sa === 'object' ? sa : {}).filter(([, v]) => v);
  if (!entries.length) return null;
  const on = entries.filter(([, v]) => (typeof v === 'object' ? v.status : v) === 'enabled').length;
  return { on, total: entries.length, detail: entries.map(([k, v]) => `${k}: ${typeof v === 'object' ? v.status : v}`).join(', ') };
}

const tile = (key, label, bodyHtml, title = '') =>
  `<div data-scouting-tile="${esc(key)}" class="min-w-0 rounded-sm border border-rule px-2 py-[2px]"${title ? ` title="${esc(title)}"` : ''}>`
  + `<div class="text-provenance uppercase text-ink-muted">${esc(label)}</div>`
  + `<div class="tnum truncate text-caveat text-ink">${bodyHtml}</div></div>`;

/** The documentation link beside the homepage link, with a short cue and no sentence:
 *  "docs ✓" (linked), "docs unreachable" (linked), "no docs found", "docs not checked".
 *  The server decides the cue (overview.docs_cue); '' means the step has never run, so draw nothing. */
export function docsHtml(ov) {
  const cue = ov && ov.docs_cue;
  if (!cue) return '';
  const hover = ov.docs_url
    ? `${ov.docs_url}${ov.docs_status ? ` · HTTP ${ov.docs_status}` : ''}${ov.docs_source ? ` · from ${ov.docs_source}` : ''}`
    : '';
  const word = ov.docs_url
    ? `<a href="${esc(ov.docs_url)}" target="_blank" rel="noopener noreferrer" class="text-accent-ink underline">${esc(cue)} ↗</a>`
    : `<span class="text-ink-muted">${esc(cue)}</span>`;
  return ` · <span data-docs-cue="${esc(ov.docs_state || '')}"${hover ? ` title="${esc(hover)}"` : ''}>${word}</span>`;
}

/** The tile row. `ov` is the scouting overview; returns '' when there is none (the header renders without it). */
export function scoutingTilesHtml(ov) {
  if (!ov) return '';
  const unread = new Set(Array.isArray(ov.stats_unread) ? ov.stats_unread : [
    'primary_language', 'stars', 'forks', 'contributors', 'last_pushed_at', 'repo_size_kb',
    'security_and_analysis', 'deployments_count']);
  // No GitHub row at all: say "not yet surveyed" (the first scouting survey fetches it), never zeros.
  const never = ov.stats_fetched === false;
  const cue = (title) => (never ? notReadCue(title, NOT_SURVEYED_WORD) : notReadCue(title));
  const num = (key, v) => (unread.has(key) ? cue() : esc(String(v)));
  const sec = securityCounts(ov.security_and_analysis);
  const dep = ov.deployments_count || 0;
  const tiles = [
    // Website is derived by the Homepage survey step, not a GitHub stat: no homepage is "none found".
    tile('website', 'Website', ov.homepage
      ? `<a href="${esc(ov.homepage)}" target="_blank" rel="noopener noreferrer" class="text-accent-ink underline">${esc(prettyHost(ov.homepage))} ↗</a>${docsHtml(ov)}`
      : `<span class="text-ink-muted">none found</span>${docsHtml(ov)}`, ov.homepage || 'No project website found'),
    tile('language', 'Language', unread.has('primary_language') ? cue() : esc(ov.primary_language)),
    tile('stars', 'Stars', num('stars', ov.stars)),
    tile('forks', 'Forks', num('forks', ov.forks)),
    tile('contributors', 'Contributors', num('contributors', ov.contributors_count)),
    tile('last_pushed', 'Last pushed', unread.has('last_pushed_at') ? cue() : esc(ago(ov.last_pushed_at) || ov.last_pushed_at),
      unread.has('last_pushed_at') ? '' : ov.last_pushed_at),
    tile('size', 'Size', unread.has('repo_size_kb') ? cue(ZERO_PROVES_NOTHING_SENTENCE) : `${esc((ov.repo_size_kb / 1024).toFixed(1))} MB`),
    tile('security', 'Security', unread.has('security_and_analysis') || !sec
      ? cue('GitHub shows security settings only to admins of the repository, or they have not been read yet.')
      : `${sec.on}/${sec.total} enabled`, sec ? sec.detail : ''),
    tile('deployments', 'Deployments', unread.has('deployments_count') ? cue(ZERO_PROVES_NOTHING_SENTENCE)
      : (dep ? `${dep}${ov.latest_deployment_at ? ` · ${esc(ago(ov.latest_deployment_at))}` : ''}` : '0'),
    ov.latest_deployment_environment ? `Latest: ${ov.latest_deployment_environment}${ov.latest_deployment_ref ? ` @ ${ov.latest_deployment_ref}` : ''}` : ''),
  ];
  return `<div data-scouting-tiles class="mt-s2 grid grid-cols-3 gap-s1 sm:grid-cols-5 lg:grid-cols-9">${tiles.join('')}</div>`;
}

/** The scouting signal chips. `sig` is `{tiles}` on success, `{error}` when the read failed, null before the read. */
export function scoutingSignalHtml(sig) {
  if (!sig) return '';
  let body;
  if (sig.error) {
    body = `<span data-signal="error" class="text-ink-muted" title="${esc(sig.error)}"><span class="font-glyph" aria-hidden="true">${stateEntry('unknown').glyph}</span> could not read</span>`;
  } else if (!(sig.tiles || []).length) {
    body = '<span data-signal="none" class="text-ink-muted">no scouting results yet</span>';
  } else {
    body = sig.tiles.map((t) => {
      const at = `data-signal-chip="${esc(t.status || '')}" title="${esc(t.analysis_name || '')}"`;
      if (t.status === 'ok') return `<span ${at} class="text-state-ok">${esc(t.label)}</span>`;
      if (t.status === 'warn' || t.status === 'gap') return `<span ${at} class="text-state-warn">${esc(t.label)}</span>`;
      return `<span ${at} class="text-ink">${esc(t.label)}</span>`;
    }).join('<span class="text-ink-muted"> · </span>');
  }
  return `<div data-scouting-signal class="mt-s1 text-provenance"><span class="uppercase text-ink-muted">Scouting signal</span> ${body}</div>`;
}

/* ── the stale-link banner ─────────────────────────────────────────────── */

export const REPAIR_CHOICES = LINK_ACTIONS.map((a) => ({
  action: a.id, label: a.label, sentence: a.does, writes: a.writes, confirm: true,
}));

/** The banner, or '' when the link is not stale. `ov.published_state === 'published_earlier'` adds the
 *  post-reset word beside the stale cue. */
export function staleLinkBannerHtml(ov) {
  if (!ov || !ov.egeria_link_stale) return '';
  const e = stateEntry('gone');
  const earlier = ov.published_state === 'published_earlier'
    ? ` · <span title="${esc(PUBLISHED_EARLIER_SENTENCE)}">${esc(PUBLISHED_EARLIER_WORD)}</span>` : '';
  const guid = ov.egeria_link_stale_guid
    ? ` <span class="font-mono text-provenance" title="The element Resource Explorer remembered">${esc(ov.egeria_link_stale_guid)}</span>` : '';
  const buttons = REPAIR_CHOICES.map((c) =>
    `<button type="button" data-link-repair="${c.action}" title="${esc(c.sentence)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">${esc(c.label)}</button>`).join(' ');
  return `<div data-stale-link-banner class="mt-s2 flex flex-wrap items-baseline gap-s2 text-caveat">
    <span class="text-ink-muted" title="Egeria no longer has the element Resource Explorer recorded for this resource. Every choice forgets the dead pointer first."><span class="font-glyph" aria-hidden="true">${e.glyph}</span> link stale${earlier}${guid}</span>
    ${buttons}
    <span data-link-repair-status class="text-provenance text-ink-muted" aria-live="polite"></span>
  </div>`;
}

/** Wire the banner's buttons. `resolve(action)` is the API call; `confirmWrite(choice)` asks before every choice
 *  (two write to Egeria, the third deletes RE's own publish-history rows); `onDone(result, error)` re-reads the header (after a failure too, with the error). All three are injected. */
export function bindStaleLinkBanner(root, { resolve, confirmWrite, onDone }) {
  const host = root.querySelector('[data-stale-link-banner]');
  if (!host) return;
  const status = host.querySelector('[data-link-repair-status]');
  host.querySelectorAll('[data-link-repair]').forEach((btn) => btn.addEventListener('click', async () => {
    const choice = REPAIR_CHOICES.find((c) => c.action === btn.dataset.linkRepair);
    if (!choice || btn.disabled) return;
    // Every choice asks: two write a new report to Egeria, and the third deletes RE's publish history rows.
    if (choice.confirm && !(await confirmWrite(choice))) return;
    host.querySelectorAll('[data-link-repair]').forEach((b) => { b.disabled = true; });
    status.textContent = choice.writes ? 'working…' : 'forgetting…';
    try {
      const r = await resolve(choice.action);
      status.textContent = (r && r.next_step) || 'done';
      if (onDone) await onDone(r);
    } catch (err) {
      host.querySelectorAll('[data-link-repair]').forEach((b) => { b.disabled = false; });
      status.textContent = `not done: ${err && err.message ? err.message : 'could not reach the server'}`;
      // Every repair clears the dead pointer BEFORE it publishes, so after a failure the link is already gone and
      // a second press would answer 404. Re-read, so the header shows the state that is really there.
      if (onDone) await onDone(null, err);
    }
  }));
}
