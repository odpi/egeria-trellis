# Annotation types we want Egeria to support

Status: living list, started 2026-10-29. The owner asked: "we also need to keep track of the new annotation types we
want Egeria to support." This is that list. Add a row when a step emits a type the engine host cannot publish, and
remove it when Egeria has it and the engine host publishes it.

**Why this list exists.** The engine host (`engine_host.py`, `survey_step_host.PUBLISHABLE_ANNOTATION_TYPES`) can put
two annotation types on a SurveyReport: `ResourceMeasureAnnotation` and `ClassificationAnnotation` (as a proposal,
`contentStatus` DRAFT). A library step that emits any other type is **not offered to Egeria**: the start-up/first-use
warning says `library step 'x' not offered to Egeria: emits A, B, of which B cannot be published by the engine host …`
and points here. Nothing is lost for the step itself: it still runs from RE (Survey & analyses, a Survey Definition), its
rows are stored, and the file-system steps still publish through RE's own file-system survey report (which carries
`ResourceProfileAnnotation` and `RequestForActionAnnotation`; Files steps 7-8, #905). What is lost is only **Egeria
dispatching the step** through the engine host.

A step may avoid the gap by declaring `ResourceMeasureAnnotation` (as `fs_permissions` was tried in the 2026-10-29 gate's
leg 8), at the cost of the richer type's shape; that is the author's trade, not a workaround for this list.

## The types

| Type | Steps that emit it | What the step loses meanwhile | The Egeria-side ask |
|---|---|---|---|
| `ResourceProfileAnnotation` | `fs_activity`, `fs_permissions` (primary measure), `fs_layout_signals` | Egeria cannot start these steps through the engine host: no request type is advertised for them, so an Egeria process cannot include them and a governance action cannot target them. Their results reach Egeria only through RE's own file-system survey report, pressed from RE. | Ask whether the report path the engine host publishes through can carry `ResourceProfileAnnotation` for a library step, as RE's own file-system survey report already does (the engine host's own check is the list in `PUBLISHABLE_ANNOTATION_TYPES`; whether Egeria's side needs anything beyond that is for the Egeria owners to say). RE side: add the type to `PUBLISHABLE_ANNOTATION_TYPES` and build it in `LibraryStep.report_annotations` (`walk_derived.report_annotations` already builds it for the file-system report). |
| `RequestForActionAnnotation` | `fs_permissions` (`world_readable_sensitive`) | Same as above for the step as a whole: while one measure needs this type the whole step is refused by the engine host, so a world-readable-sensitive finding cannot be raised by an Egeria-dispatched run. | Same ask for `RequestForActionAnnotation`, plus (the owner's rule that facts and proposals stay separate channels) a statement of how a requested action from a library step is meant to be picked up. |
| `SchemaAnalysisAnnotation`, `ResourcePhysicalStatusAnnotation`, other Egeria survey types | none today | RE's publish table already writes `SchemaAnalysisAnnotation` from a declaration (owner, 2026-10-28); `ResourcePhysicalStatusAnnotation` is for a later slice. No library step needs them from the engine host yet. | Nothing yet. Listed so the next step that needs one has a row to add to. |

## New types (not in Egeria's model at all)

None requested yet. If a step needs a measure shape no existing annotation can carry (a distribution with named bands, a
time series), write the request here first, with the step, the shape and what RE does without it (today: the generic
`ResourceMeasureAnnotation` route with the value under the measure's name, labelled `declaredRoute: generic`).

## How to keep it current

- When a step is added whose manifest names a type outside `PUBLISHABLE_ANNOTATION_TYPES`, add or extend its row.
- The warning prints the step's full list of emitted types and which of them block, so the row can be checked against it.
- When the engine host starts publishing a type, move its row out of the table and say in the commit which steps are
  now offered to Egeria.
