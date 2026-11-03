# Curate for repositories: publish from the existing survey, the commit summary, dependency kinds, blueprints, zones, rules wording — implemented

Branch `re/curate-repo-publish-and-blueprints`. Brief: `BRIEF-CURATE-REPO-PUBLISH-DEPENDENCIES-BLUEPRINTS.md`
(sections 1 to 6, 8 and 9; section 7, RAG ingestion of code, is a backlog item and was not built).
Not run against a live Egeria or the shared registry: every Egeria call is faked in tests; the items below marked
UNVERIFIED LIVE need one throwaway-element check before the gate.

## Section 5 first, because the brief said "read first"

Materialization stamps `[draft_zone()]` (`resource-explorer-draft`) on a public resource's Draft `SolutionBlueprint` and
`SolutionComponent`, or the private zones for a private investigation. It does NOT put a public element in
`PRIVATE_ZONE`. So accept with nothing configured must CLEAR the draft zone or the element stays in the draft zone;
accept is not "the content-status change alone". `promote_to_publish_zones` now reads the element's zones strictly
(`egeria_identity.read_zones`: an unreadable answer raises, never `[]`), calls the documented
`clear_zone_membership` (body `DeleteClassificationRequestBody`), reads again, and only then says
"accepted · zones left to Egeria · everyone visible". UNVERIFIED LIVE: the clear call itself.

* `publish_zones()` and `DEFAULT_PUBLISH_ZONES` are removed. A second write path used them: investigation LOOSENING
  (`investigation_reclassifier`). It follows the same rule (configured zones, else clear).
* The queued Next-UI accept (`materialize_components` in `run_queue.py`) never promoted at all; it now does, and a
  promotion proof row (`catalogue_commit_proofs`, `node_kind` `component_promotion` / `blueprint_promotion`) carries the
  words the Curate rows show.
* Accept clears ONLY RE's own stamp: an unconfigured accept leaves any other zone alone ("zones left as they are"), since an adopted element may belong to someone else; the configured branch reads the zones back before it says "accepted · zone X".
* Never strip a foreign zone: the unconfigured clear removes the whole classification, so it runs only when every zone is RE's own (draft, private, the owner's); with a foreign zone beside them the zones are set to the foreign ones alone (read back); foreign only is untouched. The configured branch writes the configured zones UNIONED with any foreign ones (RE's draft zone goes, nothing else), read back; union chosen over "leave and say so" because accept must still publish to the configured zone and the union costs the foreign zone nothing.
* RE's draft zone still stamps what RE creates (the 2026-09-04 design); the configured-only rule governs where accept
  moves things, and an unconfigured accept removes the draft zone.

## Section 1: where the survey comes from

No completed repository survey was persisted anywhere (`SurveyResult` was in memory; the findings tables are lossy), so
"publish the latest completed survey already in the registry" had nothing to read. The orchestrator now keeps each
completed step's annotations in `app_settings` (`repo_survey_step::<slug>::<step>` and an index), newest per step,
`surveyors/survey_snapshot.py`; a failed or skipped step keeps its old result, a step that ran and found nothing is an
empty result, a scoped run is never kept. No DDL. `report_published` records `surveyed_at`, `steps`, `annotation_count`
and `reused` in its `detail` JSON. `POST /api/egeria/{slug}/resurvey` runs the survey and nothing else. The Curate commit
publishes the kept survey; its box (off by default) re-surveys exactly the stale steps first.
Migration: every repository surveyed before this build has no kept survey, so its first publish returns "no survey to publish yet · run the first survey".

## Section 2: the table

Rows and counts follow the selection before the press; after it the state column is read from the commit's proof
summary (`GET /curate/commits/{id}` returns `proof_summary`). A proof row per sub-resource sent is written by GUID after a
read (one read per element; slow for hundreds). The "lines you confirmed under what it is" row has no separate Egeria
write today (the commit sends the asset, the report, classifications and sub-resources), so its state says "recorded on
the commit", not a check. File types and blueprints are separate presses (the band's file-types button; accepting a
blueprint) and their rows say so.

## Sections 3, 4, 6, 8, 9

3. Dependencies: one table (`dependency_table.py`); runtime rows come from the architecture-interfaces wires (compose
   `depends_on` and environment references). Dockerfile `FROM`, Helm values and connection strings are not extracted by
   any surveyor today, so they are not rows. Confirmations: `app_settings`, append-only.
4. Blueprint kind names: `blueprint_kinds.py`. A blueprint written under the old qualifiedName slot is ADOPTED, not
   renamed (no rename call exists). Build and Logical are listed as not yet drawn.
6. Wording: the label strings are asserted, plus a scan of the Next scripts for any bare "rule".
8. The credential word is true only where it is: a zero-fetch analysis reads "needs no credential". The optional
   override slice is built: in-process on RE's engine, never the queue or Prefect, scrubbed, not retried.
9. Definition coverage is derived from `re_analysis_step` through `analysis_source_steps`.

## Update 2026-10-08: no default draft zone

The project owner ruled that DRAFT is a content status, not a zone. The section above describes the earlier behaviour
(materialization stamping `resource-explorer-draft`); it no longer holds by default. `egeria_identity.draft_zone()` now
returns `None` unless `EXPLORER_DRAFT_ZONE` is set, and `draft_zones()` returns `[]` or that one zone. Every writer
(materialiser, publisher, referenced-asset stamp, `stamp_published`) sends NO `ZoneMembership` call when the list is empty;
ownership is still stamped. The private zone is unchanged and still wins for a private investigation. The worker runs
`ensure_draft_zone_exists()` only when a draft zone is configured, and logs `draft zone: none (default)` or the
configured name at start. The configured-only rule for `EXPLORER_PUBLISH_ZONES` stands. Accept with nothing configured
finds no zone on an unzoned element and reports "already unzoned" without a clear call.

Reason: nothing in the security directory defined `resource-explorer-draft`, so every RE-created element sat in a zone no
rule named. UNVERIFIED LIVE: what zones the platform gives an unzoned create (a blueprint created without RE zones came
out in `egeria-runtime`, which only the runtimeManager group may edit). That is to be answered by a throwaway create after
the Egeria reset; RE does not decide it by stamping an undefined zone.

