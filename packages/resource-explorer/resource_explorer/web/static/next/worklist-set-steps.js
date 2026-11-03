/* The work list's "set steps" section (brief 2026-10-30 Part C; owner: "use the work list's members for set steps").
 *
 * A set step takes the whole list as its subject: one run, a result row per member, one roll-up for the list. The section
 * lists what GET /api/work-lists/{slug}/set-steps offers, each with its priced line (the sum of the members'; a time not
 * measured yet says so and is never 0), and a Run button per offer. One press runs once (the button is disabled and says
 * "running…" at once), then the outcome is told: the roll-up line, then every member's row, including the members the step
 * skipped, each with its reason. The button is disabled, with the sentence why, when the list has no members, no member the
 * step measures, or nobody is signed in.
 *
 * Pure builders (`setStepsHtml`, `outcomeHtml`, `disabledReason`) plus `mountSetSteps`, which wires them to the two
 * routes. The tables are real `<table>`s with a header row and a `data-rc-id`, so colresize.js's `watchTables` makes their
 * columns resizable like every other /next table. Every string from the server is escaped.
 */
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

export const WORDS = {
  heading: 'Set steps',
  intro: 'A set step takes this whole list as one subject: one run, a result for each member, and one roll-up for the list.',
  noMembers: 'This list has no members, so there is nothing for a set step to run on.',
  noOffers: 'No installed set step measures the kinds of resource in this list.',
  noEligible: (n) => `None of the ${n} member${n === 1 ? '' : 's'} is a kind this step measures, so there is nothing for it to run on.`,
  signIn: 'Sign in to run a set step: it runs as you.',
  running: 'running…',
  run: 'Run',
  reading: 'reading the set steps…',
  failedRead: (m) => `The set steps could not be read: ${m}`,
  failedRun: (m) => `The run did not complete: ${m}`,
  skippedTitle: 'skipped: not a kind this step measures',
};

/** Why a Run button is disabled, as a sentence, or '' when it may be pressed. */
export function disabledReason({ memberCount, offer, signedIn }) {
  if (!memberCount) return WORDS.noMembers;
  const priced = offer && offer.price ? offer.price.priced : 0;
  if (!priced) return WORDS.noEligible(memberCount);
  if (!signedIn) return WORDS.signIn;
  return '';
}

const th = (t) => `<th class="px-2 py-[2px] text-left font-normal text-caps uppercase tracking-caps text-ink-muted">${esc(t)}</th>`;
const td = (t, title = '') => `<td class="px-2 py-[2px] text-ink"${title ? ` title="${esc(title)}"` : ''}>${esc(t)}</td>`;

/** The priced line: the sum in words, then one row per member. An unmeasured time is words, never a zero. */
export function priceHtml(step, price) {
  if (!price) return '';
  const rows = (price.rows || []).map((r) => `<tr data-price-row="${esc(r.entity_slug)}"${r.can_run === false && r.time == null ? ' data-skipped="true"' : ''}>
      ${td(r.entity_slug)}${td(r.entity_type || '')}${td(r.words || '')}</tr>`).join('');
  return `<div data-set-price="${esc(step)}" class="mt-s1 text-provenance text-ink-muted">${esc(price.words || '')}</div>
    <div class="mt-s1 max-w-full overflow-x-auto" data-set-price-table>
      <table data-rc-id="wl-set-price" class="w-full border-collapse text-caveat">
        <thead><tr>${th('Member')}${th('Kind')}${th('Price')}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

const stateOf = (rec) => (rec.additionalProperties || {}).resultState || '';
const reasonOf = (rec) => (rec.additionalProperties || {}).resultStateReason || rec.explanation || '';

/** The outcome of one run: the roll-up line, then a row per member (skipped members included, with their reasons). */
export function outcomeHtml(out) {
  if (!out) return '';
  const recs = out.step_annotations || [];
  const roll = recs.find((r) => r.scope === out.set);
  const members = recs.filter((r) => r.scope !== out.set);
  const rollLine = roll
    ? `<div data-set-rollup class="mt-s2 text-answer text-ink"><span class="text-caps uppercase tracking-caps text-ink-muted">${esc(out.set)}</span> · ${esc(roll.summary || stateOf(roll))}${
      reasonOf(roll) && stateOf(roll) !== 'measured' ? ` · <span class="text-ink-muted">${esc(reasonOf(roll))}</span>` : ''}</div>`
    : '<div data-set-rollup class="mt-s2 text-caveat text-state-warn">The run returned no roll-up for the list.</div>';
  const rows = members.map((r) => {
    const st = stateOf(r);
    const skipped = st === 'not_applicable';
    return `<tr data-member-row="${esc(r.scope)}"${skipped ? ` data-skipped="true" title="${esc(WORDS.skippedTitle)}"` : ''}>
      ${td(r.scope)}${td(st.replace(/_/g, ' '))}${td(r.summary || '')}${td(skipped || st === 'not_established' ? reasonOf(r) : '', reasonOf(r))}</tr>`;
  }).join('');
  const counts = `${out.per_member} member result${out.per_member === 1 ? '' : 's'} and ${out.rollups} roll-up, one run`;
  return `<div data-set-outcome class="mt-s2"><div class="text-provenance text-ink-muted">${esc(counts)}</div>${rollLine}
    <div class="mt-s1 max-w-full overflow-x-auto" data-set-results-table>
      <table data-rc-id="wl-set-results" class="w-full border-collapse text-caveat">
        <thead><tr>${th('Member')}${th('State')}${th('Result')}${th('Why')}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

/** One offer: its name and description, the priced line, a Run button (or why it is disabled), and the outcome. */
export function offerHtml(offer, { memberCount, signedIn, run }) {
  const why = disabledReason({ memberCount, offer, signedIn });
  const phase = (run && run.phase) || 'idle';
  const running = phase === 'running';
  const disabled = running || !!why;
  const button = `<button data-set-run="${esc(offer.step)}" ${disabled ? 'disabled' : ''} ${running ? 'aria-busy="true"' : ''}
      class="rounded-sm border px-2 py-[2px] bg-transparent ${disabled ? 'cursor-not-allowed border-rule text-ink-muted' : 'cursor-pointer border-accent text-accent-ink'}"
      ${why ? `title="${esc(why)}"` : ''}>${running ? WORDS.running : WORDS.run}</button>`;
  const tail = phase === 'done' ? outcomeHtml(run.result)
    : phase === 'error' ? `<div data-set-error class="mt-s1 text-caveat text-state-warn">${esc(WORDS.failedRun(run.error))}</div>` : '';
  return `<div data-set-step="${esc(offer.step)}" class="mt-s3">
    <div class="flex flex-wrap items-baseline gap-s3"><span class="text-name">${esc(offer.display_name || offer.step)}</span>${button}
      ${why && !running ? `<span data-set-why class="text-caveat text-ink-muted">${esc(why)}</span>` : ''}</div>
    ${offer.description ? `<div class="mt-s1 max-w-[80ch] text-provenance text-ink-muted">${esc(offer.description)}</div>` : ''}
    ${priceHtml(offer.step, offer.price)}${tail}</div>`;
}

/** The whole section for one read of the offers. `model` = { memberCount, offers, signedIn, runs: {step: {phase, result, error}} }. */
export function setStepsHtml(model) {
  const head = `<div class="text-caps uppercase tracking-caps text-ink-muted">${WORDS.heading}</div>`;
  if (model.error) return `<div data-set-steps="error">${head}<div class="text-caveat text-state-warn">${esc(WORDS.failedRead(model.error))}</div></div>`;
  if (!model.memberCount) return `<div data-set-steps="empty">${head}<div class="text-caveat text-ink-muted">${esc(WORDS.noMembers)}</div></div>`;
  if (!(model.offers || []).length) return `<div data-set-steps="none">${head}<div class="text-caveat text-ink-muted">${esc(WORDS.noOffers)}</div></div>`;
  return `<div data-set-steps="offers">${head}<div class="text-provenance text-ink-muted">${esc(WORDS.intro)}</div>${
    model.offers.map((o) => offerHtml(o, { memberCount: model.memberCount, signedIn: model.signedIn, run: (model.runs || {})[o.step] })).join('')}</div>`;
}

const json = async (res) => {
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((body && body.detail) || `${res.status} ${res.statusText || ''}`.trim());
  return body;
};

/** Read the offers into `host` and wire the Run buttons. `signedIn()` says whether anyone is signed in; `fetchImpl` is
 *  injected for tests. A press runs once: the button is disabled and reads "running…" before the request is sent. */
export async function mountSetSteps(host, { slug, signedIn = () => false, fetchImpl = (...a) => fetch(...a), stale = () => false }) {
  if (!host) return null;
  const model = { memberCount: 0, offers: [], signedIn: !!signedIn(), runs: {}, error: '' };
  const paint = () => { host.innerHTML = setStepsHtml({ ...model, signedIn: !!signedIn() }); };
  host.innerHTML = `<div data-set-steps="reading" class="text-caveat text-ink-muted">${esc(WORDS.reading)}</div>`;
  try {
    const got = await json(await fetchImpl(`/api/work-lists/${encodeURIComponent(slug)}/set-steps`));
    if (stale()) return null;
    model.memberCount = got.members || 0;
    model.offers = got.offers || [];
  } catch (err) {
    if (stale()) return null;
    model.error = err.message;
  }
  paint();
  host.addEventListener('click', async (e) => {
    const btn = e.target.closest && e.target.closest('[data-set-run]');
    if (!btn || btn.disabled) return;
    const step = btn.dataset.setRun;
    if ((model.runs[step] || {}).phase === 'running') return;
    model.runs[step] = { phase: 'running' };
    paint();                                              // "running…" shows before anything is sent
    try {
      const result = await json(await fetchImpl(`/api/work-lists/${encodeURIComponent(slug)}/set-steps/${encodeURIComponent(step)}/run`,
        { method: 'POST' }));
      model.runs[step] = { phase: 'done', result };
    } catch (err) {
      model.runs[step] = { phase: 'error', error: err.message };
    }
    if (!stale()) paint();
  });
  return { model, repaint: paint };
}
