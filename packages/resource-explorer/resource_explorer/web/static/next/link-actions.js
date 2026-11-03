/* The three ways to repair a stale Egeria link, said once. Admin → Egeria Links (admin/egeria_links.js) and the
 * resource header's stale-link banner (stages/repo-header.js) both read this, so a person sees the same words and the
 * same confirm text on both, and both call the same route (`POST /api/egeria/linkage/{type}/{slug}/resolve`). */
export const LINK_ACTIONS = [
  { id: 'republish', label: 'Republish', writes: true,
    does: 'Clear the broken link, then re-publish the survey results RE already holds. Writes a new SurveyReport to Egeria. Fast, no re-scan.' },
  { id: 'resurvey', label: 'Re-survey', writes: true,
    does: 'Clear the broken link, survey the resource from scratch, then publish. Writes to Egeria and can take minutes.' },
  { id: 'discard', label: 'Discard link', writes: false,
    does: "Remove RE's local publish records for this resource and stop. Writes nothing to Egeria. Survey results and annotations are kept." },
];
