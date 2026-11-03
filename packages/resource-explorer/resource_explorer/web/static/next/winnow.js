/* Winnowing a work list: every headline a column, every column a filter.
 *
 * DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §2. Pure functions, no DOM and no fetch, so the bench
 * (worklist.js) and the re-apply press evaluate a filter the same way, and a harness test can hold both to it.
 *
 * THREE BUCKETS, NEVER TWO. A filter splits the rows into kept, dropped and could-not-check. A member whose answer
 * is absent for a criterion (never measured, not read yet, not established, unreadable, a run in flight) is never
 * silently dropped and never silently kept: it goes into the third bucket with the sentence saying why. A member
 * that fails one criterion is dropped even when another could not be checked: that one failure is known.
 *
 * What a column is filtered BY (the Python half, `winnowing.py`, checks the same shapes before a shortlist keeps
 * them):
 *   state     the answer's state word: answered / nothing found / partial
 *   headline  the answer's own sentence, by contains / does not contain
 *   measure:<analysis id>:<value field>
 *             a declared measure (`/api/work-lists/column-types`), by threshold, or by age when it is a date.
 *             Read with the declaration's own path rules (publish_table.resolve): a dotted path, a trailing []
 *             counts a list, run.<field> reads the run.
 */

export const STATE_WORDS = ['answered', 'nothing found', 'partial'];
export const STATE_OPS = ['is', 'is_not'];
export const HEADLINE_OPS = ['contains', 'lacks'];
export const NUMBER_OPS = ['>=', '<=', '>', '<', '='];
export const DATE_OPS = ['older_than_days', 'newer_than_days'];

export const OP_WORDS = {
  is: 'is', is_not: 'is not', contains: 'contains', lacks: 'does not contain',
  '>=': 'at least', '<=': 'at most', '>': 'more than', '<': 'less than', '=': 'exactly',
  older_than_days: 'older than', newer_than_days: 'newer than',
};

/** The cell states (worklist.js `cellState`) that are answers, by the word a state criterion names. */
const ANSWER_WORD = { answered: 'answered', nothing: 'nothing found', partial: 'partial' };

/** The operators a field offers. */
export function opsFor(field) {
  if (field === 'state') return STATE_OPS;
  if (field === 'headline') return HEADLINE_OPS;
  if (String(field).startsWith('measure:')) return [...NUMBER_OPS, ...DATE_OPS];
  return [];
}

/** The fields a column can be filtered by, from its question and the column types read for its kind.
 *  Each: {field, label}. A column with no analysis offers none: there is nothing to filter on. */
export function fieldsFor(q, types) {
  const ids = q.analysis_ids || [];
  if (!ids.length) return [];
  const out = [{ field: 'state', label: 'answer state' }, { field: 'headline', label: 'headline words' }];
  for (const id of ids) {
    for (const m of types?.analyses?.[id]?.measures || []) {
      out.push({ field: `measure:${id}:${m.value_field}`, label: `${m.name} (${m.unit})` });
    }
  }
  return out;
}

/** A declared value path against a fact (publish_table.resolve's rules). `undefined` when nothing is there. */
export function resolvePath(fact, path) {
  const value = (fact && fact.value) || {};
  let p = String(path || '').trim();
  if (!p) return undefined;
  if (p.startsWith('run.')) {
    const run = { ...(value._run || {}) };
    if (!run.surveyed_at && fact.last_run_at) run.surveyed_at = fact.last_run_at;
    const got = run[p.slice(4)];
    return got == null || got === '' ? undefined : got;
  }
  const count = p.endsWith('[]');
  if (count) p = p.slice(0, -2);
  let got;
  if (value && typeof value === 'object' && p in value) got = value[p];
  else {
    got = value;
    for (const part of p.split('.')) {
      if (!got || typeof got !== 'object' || !(part in got)) return undefined;
      got = got[part];
    }
  }
  if (count) return Array.isArray(got) ? got.length : undefined;
  return got == null ? undefined : got;
}

const absent = (reason, words) => ({ present: false, reason, words });

/**
 * What one criterion reads for one member: `{present: true, value, asOf}` or `{present: false, reason, words}`.
 *
 * `cell` is what the bench holds for the member: `{row, per, running, q, cellState, types}` where `row` is
 * `{factsById, error}` (or undefined: not read), `per` the cheap projection by analysis id, `cellState` the
 * bench's own state function, and `types` the column types for the member's kind.
 */
export function readCriterion(c, cell) {
  const { row, per = {}, running, q, cellState, types } = cell;
  const ids = c.analysis_ids || [];
  const decl = (id) => types?.analyses?.[id];
  const notMeasured = () => ids.map((id) => decl(id)?.absence?.not_measured).find(Boolean)
    || `never measured · run ${ids.join(' or ')} first`;
  if (running) return absent('running', 'a run is in flight for it · filter again when it lands');
  if (row?.error) return absent('unreadable', `could not read · ${row.error}`);
  if (!ids.length) return absent('no-reader', 'no analysis answers this question, so there is nothing to filter on');
  const facts = ids.map((id) => row?.factsById?.get(id)).filter(Boolean);
  if (!facts.length) {
    if (ids.some((a) => per[a]?.has_results)) {
      return absent('unread', 'answered but not read yet · wait for the background read, or open its cell');
    }
    if (ids.every((a) => per[a]?.certain_never_run)) return absent('unrun', notMeasured());
    return absent('unknown', 'not read yet');
  }
  const asOf = facts.map((f) => f.as_of || f.last_run_at || '').filter(Boolean).sort()[0] || '';
  if (facts.every((f) => f.state === 'never_run')) return absent('unrun', notMeasured());
  if (c.field === 'state') {
    const word = ANSWER_WORD[cellState(q, row.factsById)];
    if (!word) {
      return facts.some((f) => f.state === 'not_established')
        ? absent('not-established', 'not established · the analysis ran and could not say')
        : absent('unrun', notMeasured());
    }
    return { present: true, value: word, asOf };
  }
  if (c.field === 'headline') {
    const known = facts.filter((f) => ['measured', 'nothing_found', 'partial', 'measured_within_credential_scope']
      .includes(f.state));
    if (!known.length) return absent('not-established', 'not established · the analysis ran and could not say');
    return { present: true, value: known.map((f) => f.headline || '').join(' · '), asOf };
  }
  const [, aid, path] = String(c.field).match(/^measure:([^:]+):(.+)$/) || [];
  const fact = aid ? row.factsById.get(aid) : null;
  if (!fact || fact.state === 'never_run') return absent('unrun', decl(aid)?.absence?.not_measured || notMeasured());
  if (fact.state === 'not_established') {
    return absent('not-established', decl(aid)?.absence?.not_measured || 'not established');
  }
  const v = resolvePath(fact, path);
  if (v === undefined) {
    return fact.state === 'nothing_found'
      ? absent('measured-nothing', decl(aid)?.absence?.measured_nothing || 'measured, and found nothing to count')
      : absent('missing', `measured, but ${path} is not in the result`);
  }
  return { present: true, value: v, asOf: fact.as_of || fact.last_run_at || asOf };
}

const daysSince = (iso, now) => {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : (now - t) / 86400000;
};

/** Does a present value pass? `true` / `false`, or `{absent}` when the value cannot be compared (a date
 *  criterion on something that is not a date, a threshold on something that is not a number). */
export function passes(c, value, now = Date.now()) {
  const op = c.op;
  if (c.field === 'state') return op === 'is' ? value === c.value : value !== c.value;
  if (c.field === 'headline') {
    const has = String(value || '').toLowerCase().includes(String(c.value || '').toLowerCase());
    return op === 'contains' ? has : !has;
  }
  if (DATE_OPS.includes(op)) {
    const d = daysSince(value, now);
    if (d === null) return absent('not-a-date', `${value} is not a date, so its age cannot be compared`);
    return op === 'older_than_days' ? d > Number(c.value) : d < Number(c.value);
  }
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return absent('not-a-number', `${value} is not a number, so it cannot be compared`);
  const t = Number(c.value);
  return { '>=': n >= t, '<=': n <= t, '>': n > t, '<': n < t, '=': n === t }[op] ?? false;
}

/**
 * Split members into the three buckets.
 *
 * `read(criterion, member)` returns what `readCriterion` returns. The result:
 *   kept           [slug]
 *   dropped        [{entity_slug, why}]        why: the first criterion it failed, in words
 *   couldNotCheck  [{entity_slug, words, reason, criterion}]   the first absence, with its sentence
 *   oldestAnswer   the oldest as-of among the answers that decided kept or dropped ('' when none did)
 *   newestAnswer   the newest of them: what an automatic re-apply compares with the cut's applied_at
 */
export function applyFilter(criteria, members, read, now = Date.now()) {
  const out = { kept: [], dropped: [], couldNotCheck: [], oldestAnswer: '', newestAnswer: '' };
  const seen = [];
  for (const m of members) {
    let failed = null;
    let missing = null;
    const used = [];
    for (const c of criteria) {
      const got = read(c, m);
      if (!got.present) { missing = missing || { ...got, criterion: c }; continue; }
      const ok = passes(c, got.value, now);
      if (ok && typeof ok === 'object') { missing = missing || { ...ok, criterion: c }; continue; }
      if (got.asOf) used.push(got.asOf);
      if (!ok) { failed = failed || c; }
    }
    const slug = m.entity_slug;
    const kind = m.entity_type ? { entity_type: m.entity_type } : {};
    if (failed) {
      out.dropped.push({ entity_slug: slug, ...kind, why: criterionWords(failed) });
      seen.push(...used);
    } else if (missing) {
      out.couldNotCheck.push({ entity_slug: slug, ...kind, words: missing.words, reason: missing.reason,
        criterion: missing.criterion });
    } else {
      out.kept.push(slug);
      seen.push(...used);
    }
  }
  const sorted = seen.filter(Boolean).sort((a, b) => Date.parse(a) - Date.parse(b));
  out.oldestAnswer = sorted[0] || '';
  out.newestAnswer = sorted[sorted.length - 1] || '';
  return out;
}

/** A criterion as one kind answers it: a shared column of a list of more than one kind carries each kind's own
 *  analysis ids (`analysis_ids_by_kind`); a member reads its kind's. */
export function criterionFor(c, kind) {
  const ids = c.analysis_ids_by_kind?.[kind];
  return ids ? { ...c, analysis_ids: ids } : c;
}

/**
 * One column set per view (DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS §5): the questions EVERY kind present
 * carries, matched by catalog id (by text when a question has none), in the first kind's order. Each shared
 * column keeps every kind's own entry in `byKind`, so a row is answered by its own kind's analyses; its
 * `analysis_ids` are the union, for display. A question one kind lacks is not in the set: it is not asked of
 * that kind, which is not "could not check".
 */
export function sharedColumns(perKind) {
  const kinds = [...perKind.keys()];
  const key = (q) => q.id || q.question;
  const index = new Map(kinds.map((k) => [k, new Map((perKind.get(k) || []).map((q) => [key(q), q]))]));
  const first = perKind.get(kinds[0]) || [];
  return first.filter((q) => kinds.every((k) => index.get(k).has(key(q)))).map((q) => {
    const byKindQ = Object.fromEntries(kinds.map((k) => [k, index.get(k).get(key(q))]));
    return { ...q, byKind: byKindQ,
      analysis_ids: [...new Set(kinds.flatMap((k) => byKindQ[k].analysis_ids || []))] };
  });
}

/** One criterion in words: `Q-0010 · relation_count at least 10` (winnowing.criterion_words says the same). */
export function criterionWords(c) {
  const field = c.field || '';
  const what = field === 'state' ? 'state' : field === 'headline' ? 'headline' : field.split(':').slice(2).join(':');
  const op = OP_WORDS[c.op] || c.op;
  const shown = field === 'headline' ? `“${c.value}”` : DATE_OPS.includes(c.op) ? `${c.value} days` : `${c.value}`;
  return `${c.question_id || c.question || '?'} · ${what} ${op} ${shown}`;
}

/** The filter a shortlist stores (`winnowing.check_filter`'s shape), from the criteria and the buckets. */
export function storedFilter(criteria, buckets, { parentSlug, parentCount, appliedAt, entityType = '' }) {
  return {
    // A cut made under one kind's chip covers that kind's members only (winnowing.check_filter).
    ...(entityType ? { entity_type: entityType } : {}),
    criteria: criteria.map((c) => ({
      question_id: c.question_id || '', question: c.question, analysis_ids: [...(c.analysis_ids || [])],
      ...(c.analysis_ids_by_kind ? { analysis_ids_by_kind: c.analysis_ids_by_kind } : {}),
      field: c.field, op: c.op, value: c.value, lens_dimension: c.lens_dimension || '',
      ...(c.entity_type ? { entity_type: c.entity_type } : {}),
    })),
    applied_at: appliedAt,
    oldest_answer: buckets.oldestAnswer || '',
    newest_answer: buckets.newestAnswer || '',
    parent_slug: parentSlug,
    parent_count: parentCount,
    kept: [...buckets.kept],
    dropped: buckets.dropped.map((d) => d.entity_slug),
    could_not_check: buckets.couldNotCheck.map((u) => ({
      entity_slug: u.entity_slug, words: u.words, ...(u.entity_type ? { entity_type: u.entity_type } : {}) })),
  };
}

/** What re-applying would change, by slug, before anything changes: `{wouldDrop, wouldJoin, wouldHold}`. A current
 *  member that can no longer be checked is HELD, never dropped (Decision (project owner, 2026-10-28)). */
export function reapplyDiff(currentSlugs, newKept, couldNotCheck = []) {
  const now = new Set(currentSlugs);
  const next = new Set(newKept);
  const unchecked = new Set(couldNotCheck);
  return {
    wouldDrop: [...now].filter((s) => !next.has(s) && !unchecked.has(s)),
    wouldJoin: [...next].filter((s) => !now.has(s)),
    wouldHold: [...now].filter((s) => !next.has(s) && unchecked.has(s)),
  };
}

/** `kept 7 of 23 on <criteria> as of <date>` for a shortlist reopened. */
export function shortlistWords(f) {
  if (!f) return '';
  if (f.unreadable) return f.unreadable;
  const crit = (f.criteria || []).map(criterionWords).join(' and ');
  return `kept ${(f.kept || []).length} of ${f.parent_count ?? '?'}${f.entity_type ? ` ${f.entity_type}` : ''} on ${crit} as of ${
    String(f.applied_at || '').slice(0, 10)}`;
}

/** "Cost before the cut": members with no answer because the analysis never ran, grouped by what would answer
 *  them, so the bench can offer to run it on them first. [{analysisIds, question, slugs}] */
export function unrunGroups(couldNotCheck) {
  const by = new Map();
  for (const u of couldNotCheck) {
    if (u.reason !== 'unrun') continue;
    const ids = (u.criterion && criterionFor(u.criterion, u.entity_type).analysis_ids) || [];
    const key = `${u.entity_type || ''}|${ids.join(',')}`;
    if (!by.has(key)) {
      by.set(key, { analysisIds: ids, question: u.criterion?.question_id || u.criterion?.question || '',
        entityType: u.entity_type || '', slugs: [] });
    }
    by.get(key).slugs.push(u.entity_slug);
  }
  return [...by.values()];
}
