/* Collapsed result sections (DESIGN-COLLAPSED-RESULT-SECTIONS-2026-11-01).
 *
 * A result section is a heading over a list or table that grows with the resource. It is a native <details> whose
 * <summary> is the heading plus its one-line answer, so collapsing loses no answer, only detail. It starts collapsed
 * when it holds more than 8 rows or a table (owner, 2026-11-01: "8 rows"), open otherwise; a press that just filled
 * it opens it once for that paint. The person's own open/collapsed choice is remembered per person + resource +
 * section (owner: "Per resource").
 *
 * The storage helpers are the Curate scope section's (scope-sources.js readScopePref / writeScopePref), generalised to
 * any section: never throwing, '' when nothing is remembered or storage is unavailable, and the page works either way.
 * scope-sources.js keeps its own copy because its node test loads that file on its own.
 *
 * Pure: no DOM, no imports. */

/** More than this many rows starts a section collapsed. */
export const SECTION_ROWS_OPEN = 8;

/** localStorage key for one section's choice; '' when nobody is signed in or a part is missing. */
export function sectionPrefKey(person, slug, section) {
  return person && slug && section ? `re-next.section.${person}.${slug}.${section}` : '';
}

/** 'open' | 'collapsed' | '' (nothing remembered, or storage unavailable). Never throws. */
export function readSectionPref(storage, person, slug, section) {
  const key = sectionPrefKey(person, slug, section);
  if (!key) return '';
  try {
    const v = storage && storage.getItem(key);
    return v === 'open' || v === 'collapsed' ? v : '';
  } catch { return ''; }
}

/** Remember the choice. Returns whether it was stored; the page works either way. */
export function writeSectionPref(storage, person, slug, section, open) {
  const key = sectionPrefKey(person, slug, section);
  if (!key) return false;
  try { storage.setItem(key, open ? 'open' : 'collapsed'); return true; } catch { return false; }
}

/** Whether a section starts open. A press that just filled it opens it; otherwise the remembered choice wins; otherwise
 *  it is open when it holds no table and `threshold` rows or fewer. */
export function sectionStartsOpen({ rows = 0, hasTable = false, pref = '', justFilled = false, threshold = SECTION_ROWS_OPEN } = {}) {
  if (justFilled) return true;
  if (pref === 'open') return true;
  if (pref === 'collapsed') return false;
  return !hasTable && Number(rows) <= threshold;
}
