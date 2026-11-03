/* The stage-specific leading tabs: ids, entries and the dormant gate (Brief S0).
 *
 * Curate gets "Describe and publish" / "Contents" / "Structure"; Understanding gets "At a glance" / "Contents"
 * / "Over time". They are the Enrichment "Context" precedent generalised: SUB_TABS entries with `stages`, put at
 * the head of the strip.
 *
 * `STAGE_TABS_KINDS` lists, per stage, the resource kinds ('repo' | 'db' | 'filesystem') that show the tabs.
 * Curate shows them for repositories (S1) and databases (S2); the file-system slice (S3) adds
 * 'filesystem', Understanding's slice (S4) lists its own. A kind not listed keeps the pane it has always had.
 * A test sets the gate with `setStageTabsKinds`.
 *
 * A LEAF module (no imports): app.js needs these at evaluation time, to build SUB_TABS, and must not wait on
 * curate-tabs.js, which imports app.js back. */

/** Per stage, the resource kinds that show the leading tabs. EMPTY = dormant for that stage. */
export const STAGE_TABS_KINDS = { curate: ['repo', 'db'], understanding: ['repo', 'db'] };

/** Test hook: set the kinds that show a stage's tabs. */
export function setStageTabsKinds(stage, kinds) {
  if (!(stage in STAGE_TABS_KINDS)) throw new Error(`no stage tabs for ${stage}`);
  STAGE_TABS_KINDS[stage] = [...kinds];
}

/** The tab ids of each stage, first = the one the stage always opens on. Reserved ids; they appear in URLs
 *  as `?stage=curate&tab=curate_contents`. */
export const STAGE_TAB_IDS = {
  curate: ['curate_describe', 'curate_contents', 'curate_structure'],
  understanding: ['understanding_glance', 'understanding_contents', 'understanding_over_time'],
};

/** The SUB_TABS entries (app.js spreads these in after `context`). Static labels stay the bare names. */
export const STAGE_TAB_ENTRIES = [
  { id: 'curate_describe', label: 'Describe and publish', does: 'Describe this resource and publish its report', built: true, stages: ['curate'], stageTab: true },
  { id: 'curate_contents', label: 'Contents', does: 'Choose what is published to Egeria', built: true, stages: ['curate'], stageTab: true },
  { id: 'curate_structure', label: 'Structure', does: 'What it is made of and how it relates', built: true, stages: ['curate'], stageTab: true },
  { id: 'understanding_glance', label: 'At a glance', does: 'What changed since the last run, and the headline numbers', built: true, stages: ['understanding'], stageTab: true },
  { id: 'understanding_contents', label: 'Contents', does: 'What is inside this resource', built: true, stages: ['understanding'], stageTab: true },
  { id: 'understanding_over_time', label: 'Over time', does: 'How this resource changes across runs', built: true, stages: ['understanding'], stageTab: true },
];

export const isStageTabId = (id) => STAGE_TAB_ENTRIES.some((t) => t.id === id);

/** Does `stage` show its leading tabs for this resource kind? */
export function stageTabsOn(stage, kind) {
  return (STAGE_TABS_KINDS[stage] || []).includes(kind);
}

/** The tab a stage opens on. `fallback` is what the stage opens on without the gate. */
export function stageTabDefault(stage, kind) {
  return stageTabsOn(stage, kind) ? STAGE_TAB_IDS[stage][0] : null;
}

/** Which tab each Curate section lives on once the split lands. jumpToCurateSection switches to this tab
 *  before it opens, loads and scrolls to the section. */
export const TAB_OF_SECTION = {
  'curate-sec-what-it-is': 'curate_structure',
  'curate-sec-made-of': 'curate_structure',
  'curate-sec-blueprints': 'curate_structure',
  'curate-sec-relates': 'curate_structure',
  'curate-sec-what-holds': 'curate_contents',
  'curate-sec-writes': 'curate_contents',
};

export const tabOfSection = (sectionId) => TAB_OF_SECTION[sectionId] || null;

