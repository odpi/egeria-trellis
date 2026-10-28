# Standards and practices for the analytic envelope and readers: adopt, study, or decline (R20, 2026-10-07)

Design session note, the deliverable R20 named in
`packages/resource-explorer/docs/design-notes/DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`
§6 (owner's comment 13). Written from the design session's working knowledge
of each standard; where a claim about a standard's exact field names matters
for a build, the builder reads the standard's own specification first.
Decisions here are recommendations for the owner, not rulings.

The thing being compared against is the **annotation envelope**
(`NOTE-ANNOTATION-ENVELOPE-ATTRIBUTES.md`): `resultState` (MEASURED,
NOT_ESTABLISHED, NOT_APPLICABLE, with "nothing found" a MEASURED value),
`resultStateReason`, `producingRun`, `measuredAt`, `producerVersion`,
`scope` (WHOLE, PARTIAL with reason), and the rule that every state word on
screen derives from these, carried in the interim in
`ResourceMeasureAnnotation.additionalProperties`.

| Standard | What it is | Where it would apply | Verdict | Why |
|---|---|---|---|---|
| **Egeria survey annotation types** (`SurveyReport`, `ResourceMeasureAnnotation`, `DataClassAnnotation`, `SchemaAnalysisAnnotation`, `RequestForAction`, the `Annotation` base with `annotationStatus`) | Egeria's own model for what a survey says about a resource | the result of every step; the report RE publishes whole | **Adopt** (already the case: A1) | They are the backplane's vocabulary; a step's output is an annotation or nothing reaches the ecosystem. Gap: no result state or scope; the envelope fills it in `additionalProperties` until the leads extend the types, which they said they would. `annotationStatus` (NEW, REVIEWED, APPROVED, ACTIONED, INVALID, IGNORED, OTHER) is the stewardship lifecycle and maps onto RE's proposed → confirmed → overridden, so Curate's confirmation should set it rather than invent a parallel field. |
| **OpenLineage** (run and dataset events; facets such as `schema`, `dataSource`, `dataQualityMetrics`, `columnLineage`, `sourceCode`, `processing_engine`, `nominalTime`) | an open event model for runs reading and writing datasets | the step's run (start, complete, fail), the datasets it read, provenance (`producingRun` ↔ run id, `producerVersion` ↔ `processing_engine`), and the cataloguer's own lineage (Egeria already ingests OpenLineage events) | **Adopt for the run and provenance facets; map, do not copy, for results** | Egeria consumes OpenLineage, so emitting a run event per step gives Egeria the run's provenance for free and makes the RE engine host's runs look like any other engine's. Its data-quality facet is counts without states, so a result's envelope stays ours and the facet carries the measured numbers only. |
| **Great Expectations** (expectation suites, validation results with `success`, `observed_value`, `unexpected_count`, `exception_info`, and `meta`) | declarative data-quality expectations and their results | quality-type steps (nulls, ranges, uniqueness, freshness) and the shape of a quality result | **Study, adopt the result shape's distinctions** | Its result cleanly separates "the check ran and failed" (`success: false`) from "the check could not run" (`exception_info`), which is exactly MEASURED-with-a-finding versus NOT_ESTABLISHED; `observed_value` is the measured value; `meta` is where a provenance envelope goes. Worth copying the distinction and the vocabulary for quality steps; not worth adopting the engine, which assumes its own execution model and store. |
| **MLflow** (runs, params, metrics, artifacts; the model registry; model cards as artifacts) | experiment tracking and a model registry | model resources, if RE catalogs them (a future kind), and the general "a run produced these metrics with these parameters" record | **Study** | The run/metric/param triple is the same shape as a step run with an envelope, and its model registry's stage lifecycle is a possible pattern for `producerVersion` and retirement. Nothing to adopt until model resources exist; keep the field names in view so a model-kind step is not designed against a different vocabulary. |
| **Prefect** (deployments, flows, tasks, work pools, parameters, result persistence, states) | the executor RE already uses for steps | step scheduling, retries, the worker's environment, result storage | **Adopt where a step is scheduled; the state vocabulary stays ours** | Already the executor (A6). A Prefect deployment spec is the right form for "this step, on this schedule, in this pool"; Prefect's run states (Scheduled, Running, Completed, Failed, Crashed) are execution states and must not be confused with result states: a Completed run can hold a NOT_ESTABLISHED result. The step_runs table already records the executor label; the honest rule from `reference_re_step_runs_executor_label` stands (a label proves an attempt, not a worker). |
| **papermill** (parameterised notebooks, injected parameters cell, output notebook as the record) | run a notebook with parameters and keep the executed copy | the notebook authoring form for steps and routines (R1's third form); the skeleton's notebook host for the routine | **Adopt for notebook steps** | It makes a notebook a function with parameters and a durable output, which is what a step needs; the executed notebook is the raw output kept beside the annotations (R8). The library's notebook template is a papermill notebook from the start. |
| **OpenMetadata test definitions** (test cases, test suites, results with `testCaseStatus` Success, Failed, Aborted, Queued) | another catalog's data-quality model | comparison only | **Decline, note the vocabulary** | `Aborted` as a status beside Failed is the same distinction Great Expectations makes and the envelope makes; useful as confirmation that the three-way split is the common practice, not as something to adopt from a competing catalog. |
| **W3C PROV** (entity, activity, agent; `wasGeneratedBy`, `used`, `wasAttributedTo`) | the provenance ontology | the vocabulary behind `producingRun`, `measuredAt`, and the pack's per-item provenance (P4) | **Adopt the words, not the serialisation** | Egeria's lineage and OpenLineage both descend from this model; naming the pack's provenance fields with PROV's verbs keeps a context pack legible to tools that know PROV without adding a format. |
| **JSON-LD / schema.org `Dataset`** | linked-data serialisation; the common dataset vocabulary | the context pack's graph shape (P-note §3) | **Study for the pack's graph shape** | A pack for an external consumer is easier to consume as JSON-LD with schema.org `Dataset` fields than as Egeria's native JSON; Egeria's types map onto it for the fields that overlap. Not before the first tabular and document shapes exist. |

## What this changes in the working assumptions

- **R5 (the envelope):** unchanged in fields; gains the rule that Curate's
  confirmation sets Egeria's `annotationStatus` rather than a parallel
  field, and that a quality step's result distinguishes "ran and failed"
  from "could not run" as Great Expectations does.
- **R7 (readers):** one generic reader per annotation type, as before; the
  reader also understands `annotationStatus` so a REVIEWED or ACTIONED
  annotation reads as confirmed.
- **New R21 (run events):** every step run emits an OpenLineage run event
  (start, complete or fail, with the datasets read, `processing_engine` =
  the RE engine and its version) so Egeria holds the run's provenance the
  same way for RE's engine as for its own.
- **R1 (authoring):** the notebook form is a papermill notebook.
- **Decline list:** OpenMetadata's model; MLflow until model resources
  exist.

## Order

1. R21's run event in the skeleton's wrapper (it is a few fields and Egeria
   already listens).
2. `annotationStatus` on Curate confirmations when the Enrichment-and-Curate
   publishing slice P1 is built.
3. The quality-result distinction when the first quality step is written.
4. JSON-LD for the pack's graph shape after the tabular and document shapes.

## OpenLineage run events (R21, built)

Every step run emits an OpenLineage run event from
`step_cost_observer.observe()` (`openlineage.begin` at entry, `openlineage.finish`
in its `finally`), the one place the local loop, the orchestrator, an accepted
prerequisite and the Prefect task all pass through, so built-in and external
steps get events alike. Code: `resource_explorer/observability/openlineage.py`.

**Off by default; nothing is sent to Egeria unless the owner sets it.**

| Variable | Meaning |
|---|---|
| `RE_OPENLINEAGE_TRANSPORT` | `off` (default), `file`, or `http` |
| `RE_OPENLINEAGE_DIR` | `file`: events are appended to `<dir>/openlineage-events.jsonl` |
| `RE_OPENLINEAGE_URL` | `http`: the URL each event is POSTed to |
| `RE_OPENLINEAGE_TOKEN` | `http`: optional bearer token |
| `RE_OPENLINEAGE_TIMEOUT_S` | `http`: per-event timeout, default 2 |

**Turning it on for Egeria.** Egeria's Runtime Manager forwards an OpenLineage
event posted to an Integration Daemon to the connectors that registered a
listener for open lineage events (egeria-python `RuntimeManager.publish_open_lineage_event`).
The URL to set, with the daemon's GUID from `get_integration_daemons` / the
Runtime Manager:

```
RE_OPENLINEAGE_TRANSPORT=http
RE_OPENLINEAGE_URL=<platform_url>/servers/<view_server>/api/open-metadata/runtime-manager/integration-daemons/<daemon_guid>/open-lineage-events/publish-event
RE_OPENLINEAGE_TOKEN=<view-server bearer token>
```

The body is the bare event JSON (`publish-event`; there is also a
`publish-event-string` form taking `{"class":"StringRequestBody","string":...}`).
It only does something if the daemon has an OpenLineage-listening connector
(for example the OpenLineage Event Receiver / Lineage Integrator) configured.
This was read from the pyegeria source and has **not** been called; the first
live run is the owner's to clear.

**Shape.** `run.runId` is a fresh UUID per step run, also stored on the
`step_runs` row at `metrics.openlineage.run_id` (with `transport`, `emitted`,
`failed`, `last_error`, so a lost event is visible). `job` is
`resource-explorer` / `<survey|analysis>.<step_key>`. `inputs` is the resource
surveyed, from an allow-list (a repo `https://github.com` + `owner/repo`, a
database `postgres://host:port` + database name), never credentials; schema and
table are not known at step level. A step that raised, or that caught its own
error and called `openlineage.note_failure`, ends in FAIL with a scrubbed
`errorMessage`. Delivery uses a bounded timeout; a failure is counted and logged
and never fails or stalls the step.

```json
{"eventType":"COMPLETE","eventTime":"2026-10-13T09:15:04.210Z",
 "producer":"https://github.com/odpi/egeria-trellis/tree/main/packages/resource-explorer",
 "schemaURL":"https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
 "run":{"runId":"3f0e6c1e-6a52-4c0f-9d0b-1c1f0b0e7a11","facets":{
   "nominalTime":{"_producer":"…","_schemaURL":"https://openlineage.io/spec/facets/1-0-0/NominalTimeRunFacet.json#/$defs/NominalTimeRunFacet",
                  "nominalStartTime":"2026-10-13T09:15:02.870Z","nominalEndTime":"2026-10-13T09:15:04.210Z"},
   "processing_engine":{"_producer":"…","_schemaURL":"https://openlineage.io/spec/facets/1-1-1/ProcessingEngineRunFacet.json#/$defs/ProcessingEngineRunFacet",
                  "name":"resource-explorer","version":"0.1.0","openlineageAdapterVersion":"none"}}},
 "job":{"namespace":"resource-explorer","name":"analysis.postgres_schema_and_stats"},
 "inputs":[{"namespace":"postgres://localhost:5432","name":"coco_pharma"}],"outputs":[]}
```

What Egeria does with it: the listening connector turns the run into a
lineage process/run record with the database as the dataset read, so the run's
provenance sits beside the lineage of Egeria's own engines. `outputs` is empty
for now: a result's envelope stays RE's own.
