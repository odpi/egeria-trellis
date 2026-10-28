/* The four observation row states (ENRICHMENT-E3, BRIEF-ENRICHMENT-E3-OBSERVATION-STATES.md). One
PURE function over persisted rows -- no DOM,
 * no state, no imports -- so it is table-tested over every combination and
 * a status word on screen can only come from rows that prove it.
 *
 * An OBSERVATION is a fact a survey can measure and a person can also
 * supply (licence). A JUDGEMENT (sensitivity, owner, ...) is never proposed
 * from a survey: this function is only ever asked about observations that
 * have a proposing analysis, and the caller passes `measurement: null` for
 * every kind the analysis does not apply to.
 *
 * Inputs:
 *   field        the person's persisted row, or null/{} if none:
 *                  { value, source, set_at, measured_value, measured_at }
 *                `measured_value`/`measured_at` are what the SERVER stamped
 *                from its own fact layer when the person wrote the row.
 *   measurement  what the survey measures NOW: { value, at } or null.
 *
 * Comparison is on the FACT, not its spelling: values are compared after
 * trimming, collapsing runs of whitespace, and lower-casing (`sameFact`), so
 * "mit", " MIT " and "MIT" are one licence and never a disagreement. Nothing
 * smarter is attempted (no licence-name aliasing): "Apache-2.0" and "Apache
 * License 2.0" differ.
 *
 * Output: { state, measured, stored }
 *   state     'none' | 'proposed' | 'confirmed' | 'overridden' | 'disagrees'
 *   measured  the current measurement's value ('' when none)
 *   stored    the measured value the person's row was made against ('')
 *
 * The disagreement rule compares the LATEST measurement against the STORED
 * measured value, never against the person's own value: a person who
 * overrode "MIT" with "Apache-2.0" must not be flagged when the survey says
 * "MIT" again. Only a measurement that differs from what was known when the
 * person chose raises `disagrees`, and only a person choosing again (which
 * re-stamps the stored measurement) clears it.
 */

/** Compare facts, not spelling accidents: case and runs of whitespace. */
export function sameFact(a, b) {
  const n = (x) => String(x ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
  return n(a) === n(b);
}

function instantMs(s) {
  const t = Date.parse(s || '');
  return Number.isFinite(t) ? t : null;
}

export function observationState({ field = null, measurement = null } = {}) {
  const value = String(field?.value ?? '').trim();
  const measured = String(measurement?.value ?? '').trim();

  if (!value) {
    return { state: measured ? 'proposed' : 'none', measured, stored: '' };
  }

  // What the person's row was made against. A row accepted from the survey
  // (source = the analysis) before E3 stamped anything was, by definition,
  // made against exactly its own value. A row typed with nothing measured
  // has no stored measurement at all.
  let stored = String(field?.measured_value ?? '').trim();
  if (!stored && field?.source && field.source !== 'user') stored = value;

  if (!measured) {
    // Nothing measured now: nothing can disagree. Judge by what was stored.
    return { state: stored && !sameFact(stored, value) ? 'overridden' : 'confirmed', measured, stored };
  }

  const movedOn = stored
    ? !sameFact(stored, measured)
    // Typed with no measurement at the time: any measurement that now exists
    // and differs from the person's value is news to them.
    : !sameFact(value, measured);
  // A measurement older than the one the row was made against is not "later".
  const at = instantMs(measurement?.at);
  const madeAt = instantMs(field?.measured_at);
  const isLater = at === null || madeAt === null || at > madeAt || !stored;

  if (movedOn && isLater && !sameFact(value, measured)) {
    return { state: 'disagrees', measured, stored };
  }
  // The person's value equals what is measured now: agreed, whatever the
  // stored measurement once was. Otherwise it stands against the stored one.
  const against = stored || measured;
  const agreed = sameFact(against, value) || sameFact(measured, value);
  return { state: agreed ? 'confirmed' : 'overridden', measured, stored };
}
