/* Retention basis -- the page's copy of resource_explorer/retention_basis.py (a Python test pins the
 * two equal). Stored: `value` = enum name, `note` = free text. An old free-text value is read as
 * PROJECT_LIFETIME with its text as the note (owner ruling 2026-10-08); nothing is rewritten. */
export const RETENTION_BASES = [
  { name: 'UNCLASSIFIED', label: 'Unclassified', ordinal: 0, hint: 'no assessment of the retention requirements' },
  { name: 'TEMPORARY', label: 'Temporary', ordinal: 1, hint: 'temporary, no formal retention requirements' },
  { name: 'PROJECT_LIFETIME', label: 'Project Lifetime', ordinal: 2, hint: 'needed for the lifetime of the referenced project' },
  { name: 'TEAM_LIFETIME', label: 'Team Lifetime', ordinal: 3, hint: 'needed for the lifetime of the referenced team' },
  { name: 'CONTRACT_LIFETIME', label: 'Contract Lifetime', ordinal: 4, hint: 'needed for the lifetime of the referenced contract' },
  { name: 'REGULATED_LIFETIME', label: 'Regulated Lifetime', ordinal: 5, hint: 'defined by the referenced regulation' },
  { name: 'TIMEBOXED_LIFETIME', label: 'Time Boxed Lifetime', ordinal: 6, hint: 'needed for the specified time' },
  { name: 'OTHER', label: 'Other', ordinal: 99, hint: 'another basis' },
];
export const LEGACY_BASIS = 'PROJECT_LIFETIME';

const key = (t) => String(t ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

/** The basis an old stored value already names (enum name, Egeria label or ordinal; any case), or ''. */
export function matchExisting(value) {
  const v = String(value ?? '').trim();
  if (!v) return '';
  const byOrd = RETENTION_BASES.find((b) => String(b.ordinal) === v);
  if (byOrd) return byOrd.name;
  const k = key(v);
  return (RETENTION_BASES.find((b) => key(b.name) === k || key(b.label) === k) || {}).name || '';
}

export function resolveRetention(field) {
  const value = String(field?.value || '').trim();
  const note = String(field?.note || '').trim();
  if (RETENTION_BASES.some((b) => b.name === value)) return { basis: value, note, carried: false };
  if (value) return { basis: matchExisting(value) || LEGACY_BASIS, note: value, carried: true };
  return { basis: '', note, carried: false };
}
