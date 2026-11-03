/* One state, one word, everywhere (ENRICHMENT-E3 §4 + its follow-up).
 *
 * A catalogued question whose answer is a Context observation a person
 * records (today: the licence question, on databases) HAS a reader -- the
 * Context row -- so `◌ no reader yet` is false for it. Every surface that
 * states a row's state (the Questions row, the Questions KEY/legend, the
 * markdown copy, the work-list cells, the narrow list and the digest) must
 * say the same word, so they all read THIS one function instead of each
 * deriving its own. Pure: no DOM, no state, no imports.
 *
 *   contextRecordedState(question, entityKind, enrichment)
 *     -> 'answered'  a person has recorded a value on Context (✓)
 *        'human'     nobody has yet; a person supplies it (⚠ needs you)
 *        ''          this question is not one Context records for this kind
 *
 * `entityKind` is the API entity type ('database' / 'repo' / 'filesystem');
 * `enrichment` is that resource's `enrichment` map from GET /api/context, or
 * null when it could not be read (-> '' so the caller keeps its own honest
 * "could not read", never a guessed word).
 */
export const CONTEXT_RECORDED_QUESTIONS = {
  'Under what license or agreement may this resource be used?': { key: 'licence', kinds: ['database'] },
};

export function contextRecordedSpec(question, entityKind) {
  const c = CONTEXT_RECORDED_QUESTIONS[question];
  return c && c.kinds.includes(entityKind) ? c : null;
}

export function contextRecordedState(question, entityKind, enrichment) {
  const c = contextRecordedSpec(question, entityKind);
  if (!c || !enrichment) return '';
  return enrichment[c.key]?.value ? 'answered' : 'human';
}
