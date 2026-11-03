/* Quality slice Q3 on a database work list: the DatabaseQualitySurvey across the list, priced before it starts, and
 * the list put on a schedule (DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md §2.2).
 *
 * Three rules from the design:
 *  - the price is shown before anything is queued, one row per database, and every figure says what it is: a time
 *    is "measured over N run(s)" or "not measured yet", a size is bytes or "size unknown · never analyzed", never 0;
 *  - a member that will not run (no credential, not permitted) says so on its own row, by name; nothing is dropped;
 *  - the schedule skips a database whose inventory did not change since its last quality run, and the panel says so
 *    before the person schedules it. A press does not skip: each row only shows whether it changed.
 *
 * The module draws into one slot and takes its API as an argument, so it loads without the rest of the app and a
 * test can drive it with a stub (as stages/quality-checks.js does). The batch's progress afterwards is the work
 * lists' own run-set view (`onStarted(set_id)`). */
import { esc } from '/static/next/stages/report-tables.js';

export const QUALITY_DEFINITION = 'DatabaseQualitySurvey';
export const WORK_LIST_ENTITY = 'work_list';
export const SKIP_RULE_WORDS =
  'A scheduled run skips each database whose inventory did not change since its last quality run, and says so.';

const cell = (s) => `<td class="px-2 py-[2px] align-top">${s}</td>`;

/** One database's priced row. A row that cannot run shows why instead of its figures. */
export function priceRowHtml(row) {
  const name = `<span class="font-semibold">${esc(row.display_name || row.entity_slug)}</span>`
    + `<div class="font-mono text-ink-muted">${esc(row.entity_slug)}</div>`;
  if (!row.can_run) {
    return `<tr data-slug="${esc(row.entity_slug)}" data-can-run="false">${cell(name)}`
      + `<td colspan="4" class="px-2 py-[2px] text-state-warn" data-blocked>${esc(row.words)}</td></tr>`;
  }
  const change = row.change || {};
  return `<tr data-slug="${esc(row.entity_slug)}" data-can-run="true">${cell(name)}`
    + cell(`<span data-tables>${esc(row.tables?.words || '')}</span>`)
    + cell(`<span data-size>${esc(row.size?.words || '')}</span>`)
    + cell(`<span data-time data-basis="${esc(row.time?.basis || '')}">${esc(row.time?.words || '')}</span>`)
    + cell(`<span data-change data-unchanged="${change.unchanged ? 'true' : 'false'}">${esc(change.words || '')}</span>`)
    + '</tr>';
}

/** The price table, its total and the confirm. Nothing runs until the person presses Run. */
export function priceHtml(price, { listName = '' } = {}) {
  const rows = price.rows || [];
  const n = price.runnable || 0;
  return `<div class="text-[13px]" data-quality-batch>
    <p><span class="font-mono">${esc(price.definition || QUALITY_DEFINITION)}</span> across
      ${listName ? `<span class="font-semibold">${esc(listName)}</span> · ` : ''}<span class="tnum">${rows.length}</span>
      database(s), one run each.</p>
    <table class="mt-s2 w-full border-collapse" data-price-rows>
      <thead class="text-ink-muted text-left"><tr><th class="px-2">Database</th><th class="px-2">Tables</th>
        <th class="px-2">Size</th><th class="px-2">Time</th><th class="px-2">Since its last quality run</th></tr></thead>
      <tbody>${rows.map(priceRowHtml).join('')}</tbody>
    </table>
    <p class="mt-s2" data-total>${esc(price.total?.words || '')}</p>
    ${rows[0]?.budget_words ? `<p class="text-ink-muted" data-budget>${esc(rows[0].budget_words)}.</p>` : ''}
    <p class="mt-s3 text-ink">Nothing runs until you confirm.</p>
    <div class="mt-s3 flex gap-s3 border-t border-rule pt-s2">
      <button type="button" data-act="run" ${n ? '' : 'disabled'}
        class="cursor-pointer rounded-sm border border-accent px-2 py-[2px] text-accent-ink disabled:opacity-50"
        >Run on <span class="tnum">${n}</span></button>
      <button type="button" data-act="close" class="cursor-pointer bg-transparent text-ink-muted underline">Cancel</button>
    </div>
    <div class="mt-s3 border-t border-rule pt-s2" data-schedule>
      <p class="text-ink-muted">${esc(SKIP_RULE_WORDS)}</p>
      <label>Schedule this list
        <select data-cadence class="ml-1"><option value="daily">daily</option>
          <option value="weekly" selected>weekly</option><option value="monthly">monthly</option></select></label>
      <button type="button" data-act="schedule"
        class="ml-s2 cursor-pointer rounded-sm border border-rule px-2 py-[2px]">Schedule</button>
      <span data-schedule-cue class="ml-s2"></span>
    </div>
    <div data-outcome class="mt-s3"></div>
  </div>`;
}

/** What the press did, member by member: queued, cannot run, not permitted, each with its words. */
export function outcomeHtml(out) {
  const members = out.members || [];
  const queued = members.filter((m) => m.outcome === 'queued').length;
  return `<p data-summary>${esc(out.summary || '')}</p>
    <ul class="ml-s3 list-disc">${members.map((m) => `<li data-member="${esc(m.entity_slug)}"
      data-outcome="${esc(m.outcome)}"><span class="font-semibold">${esc(m.display_name || m.entity_slug)}</span>
      · ${esc(m.words)}</li>`).join('')}</ul>
    ${queued ? `<p class="text-ink-muted">Run set <span class="font-mono" data-set>${esc(out.set_id)}</span></p>` : ''}`;
}

/** The schedule's read-back, from what the server stored (its cadence), never from the click. */
export function scheduledWords(saved) {
  return `scheduled ${saved.schedule} · ${QUALITY_DEFINITION} across this list · unchanged databases are skipped`;
}

/** Draw the panel for `workList` into `slot`. `api` = { priceQualityBatch, runQualityBatch, saveSchedule }. */
export async function mountQualityBatch(slot, workList, api, { onStarted = () => {}, onClose = () => {} } = {}) {
  const body = { work_list_slug: workList.slug };
  slot.innerHTML = '<p class="text-ink-muted">Pricing the batch…</p>';
  let price;
  try {
    price = await api.priceQualityBatch(body);
  } catch (err) {
    slot.innerHTML = `<p class="text-state-warn" data-price-failed>Could not price the batch, so nothing was offered
      to run: ${esc(err.message || String(err))}</p>`;
    return;
  }
  slot.innerHTML = priceHtml(price, { listName: workList.display_name || workList.slug });
  const outcome = slot.querySelector('[data-outcome]');
  const runBtn = slot.querySelector('[data-act="run"]');
  slot.querySelector('[data-act="close"]').addEventListener('click', () => onClose());
  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    outcome.innerHTML = '<p class="text-ink-muted">Queueing…</p>';
    try {
      const out = await api.runQualityBatch(body);
      outcome.innerHTML = outcomeHtml(out);
      if (out.set_id) onStarted(out.set_id);
    } catch (err) {
      runBtn.disabled = false;
      outcome.innerHTML = `<p class="text-state-warn" data-run-failed>The batch was not queued:
        ${esc(err.message || String(err))}</p>`;
    }
  });
  const cue = slot.querySelector('[data-schedule-cue]');
  slot.querySelector('[data-act="schedule"]').addEventListener('click', async () => {
    const cadence = slot.querySelector('[data-cadence]').value;
    cue.textContent = 'saving…';
    try {
      const saved = await api.saveSchedule(WORK_LIST_ENTITY, workList.slug, QUALITY_DEFINITION, cadence, true, 'survey');
      cue.textContent = scheduledWords(saved);
      cue.dataset.saved = 'true';
    } catch (err) {
      cue.textContent = `not scheduled · ${err.message || String(err)}`;
      cue.dataset.saved = 'false';
    }
  });
}
