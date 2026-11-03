# DESIGN — A configurable path from optional analysis results to Egeria annotations (options, 2026-10-21)

From the design session, on the owner's ask of 2026-10-21 through the RE
coordinator. His words:

> "Not all of the analyses and surveys are necessarily tied to a question,
> yet they remain as runnable actions that a user can choose to invoke. In
> some cases this is to provide more information to better answer chat
> questions. In other cases it may be to support local dashboards. However,
> the point is that there may be optional analyses that a user chooses to
> execute that do not currently have a path to saving as annotations in
> Egeria. Perhaps we should consider a dynamically configurable path for
> other analysis results to be saved as Egeria annotations in Curation?
> This might generalize the current discussion?"

Options with their costs, not a ruling. Facts read from main on
2026-10-21.

## (a) Inventory: which runnable analyses have no route to an Egeria annotation

The analysis catalogue (`configdata/analysis_catalog.yaml`, read by
`analysis_catalog_reader.py`) is where an analysis declares the
annotation types it produces, and the publisher records which types a
run published. Today it holds **39 entries, all for repositories**: 37
declare annotation types (ClassificationAnnotation and
ResourceMeasureAnnotation mostly, with QualityScore, RequestForAction,
DataClass and SchemaAnalysis for the ones Egeria has native types for);
two declare none, `repo_profile_refresh` and `rag_ingestion`, and both
are honest: one is a plan, the other an index.

**Every database and file-system analysis is absent from the catalogue**,
so none has a declared route, whatever its code does:

| Runnable, no declared route | Kind | Publishes today? |
|---|---|---|
| `postgres_schema_and_stats`, `postgres_operations`, `postgres_column_profile`, `postgres_nested_columns`, `sql_analysis`, `credential_capability` | database steps | schema and stats through the catalogue commit and the native-survey read-back; the rest local only |
| `db_derived`'s seven (relationship graph, grain, conventions, subject and coverage signals, preliminary fit, classification) | database derived | local only |
| `db_change_rates`, `db_fingerprint`, `schema_diff`, `grant_change`, `database_table_activity`, `statistics_freshness`, `hub_tables` | database derived and library steps | the three library steps publish `ResourceMeasureAnnotation`s through the engine host; the others local only |
| `postgres_value_profile`, `propose_quality_checks`, `run_quality_checks` | quality slice | Q2 adds the routes (value profile and quality annotations, the request for action) |
| `filesystem_inventory` (and the Files brief's twelve to come) | file system | file types as folder-survey annotations; counts through the step-2 publish; the rest local only |
| The repository two above | repository | none, by design |

So the question is not one gap but a class: **anything outside the
repository catalogue has no declaration, and anything local-only has no
press.** That is what makes the owner's "configurable path" the right
generalisation.

## (b) What "configurable" means: two options and a hybrid

**Option 1, a declaration per analysis.** Each analysis declares, in the
catalogue it already has (extended to every kind), the annotation type it
produces and a property map from its result fields to that type's
properties, following Egeria's types first (the follow-Egeria's-lead
rule): a `ResourceProfileAnnotation` for a profile, a
`DataClassAnnotation` for a class match, a `RequestForActionAnnotation`
for a finding a person must act on, `ResourceMeasureAnnotation` for a
measure, with `analysisStep` and `producingStep` named. Publishing is
then one generic publisher that reads the declaration.
- Cost: a declaration per analysis, about twenty-five to write for
  databases and files; a mapping language small enough to test (field,
  property, unit, as-of); the generic publisher; one committed test per
  declaration that a fixture result produces the declared annotation.
- Gain: every analysis publishes the same way, the Portal and the
  Resource Locator read native types, and the declaration is the
  documentation of what RE puts in Egeria.

**Option 2, the interim already ruled for the skeleton.** Any local
result publishes as a `ResourceMeasureAnnotation` whose
`additionalProperties` carry the result's fields verbatim, with
`producingStep` naming the analysis. No per-analysis work.
- Cost: nothing to declare; one publisher.
- Loss: Egeria readers see a bag of properties under one type; nothing
  native (no data class, no request for action) unless the analysis is
  later re-declared; and it tempts "publish everything", which the
  two-channel ruling and the honesty rules both resist.

**The hybrid the design session would recommend if asked:** Option 2 as
the default route for any analysis with no declaration, clearly labelled
on the row ("published as a measure · no native Egeria type declared"),
and Option 1 as the upgrade an analysis earns when a person wants it
read natively; the catalogue entry is the switch. The repository
catalogue already works this way in spirit (37 declared, two not), so
the change is extending the catalogue to every kind and adding the
generic publisher for both routes.

## (c) Where a person chooses: Curate, under the publish rulings

The place is Curate's first tab ("Describe and publish"), the
single-publish-per-page ruling: local results appear as rows under a
"Local results" band, each with the two-part selector the Enrichment
facts already use, **Publish | Keep in RE**, saved as a signed choice
and carried by the page's one Publish press, which acknowledges the
press, validates by the read-back of the report, and shows the
persistent cue (the publish-ack ruling). The row says what will be
written ("as ResourceMeasureAnnotation · 14 measures · producing step
db_change_rates · as of 10-21") before the press, and "published · read
back <time>" after, with proof rows per annotation as everywhere.

A result that changes after publishing reads "changed since published",
never re-sent silently. A result from an analysis that cannot publish
(the plan, the index) shows no selector and says why.

The owner's phrase "dynamically configurable" is met at two levels: the
person chooses per result on Curate; the catalogue declaration decides
how it is written. Neither requires code per analysis once the two
routes exist.

## (d) Relation to the two-channel ruling and the quality verdict

- **Annotations versus facts (2026-10-06):** a local result published
  this way is always an annotation on the resource, the survey channel.
  It never becomes a classification on the element or a relationship;
  those are facts and come only through Curate's verdict path. The
  selector on a local result therefore offers Publish, never "make this
  a fact".
- **The quality slice** is the worked example: value profile and check
  results publish as annotations (Q2); an accepted check is a verdict
  that becomes a `DataQualityRule` fact (Q5). A generalised local-result
  publish covers the first half for every analysis; the second half
  stays a per-kind design, because a fact needs a type Egeria has.
- **Proposals are annotations** by the same ruling, so an analysis that
  proposes (a data-class match, a layout signal) publishes its proposals
  as annotations and the confirmation stays local until a fact path
  exists for that kind.

## (e) Local dashboards as a consumer of local-only results

Local dashboards (set aside, to be resumed) read RE's registry, not
Egeria, so a result need not be published to be charted. The honest line
on a dashboard tile is the same as on a Curate row: "local · not
published" or "published · read back <time>". That lets a person run an
optional analysis for a dashboard or a chat answer without any Egeria
write, which is the owner's first two cases, and publish it later from
Curate when it earns its place. The only design rule is that a tile
never implies Egeria holds a number Egeria does not hold.

## (f) What is upstreamable to Egeria, for the Egeria lead

- The declaration format (annotation type plus property map per
  analysis) is the same information a native survey action service
  carries in its `SurveyAnnotationType` enum; if RE's declaration file
  and Egeria's enums shared a shape, a Java service and an RE step would
  describe themselves the same way, and the Portal could render both from
  one reader.
- Missing native types RE would otherwise fake with a measure: a
  watermark or coverage annotation for data scope in time, a change-rate
  annotation (writes per interval with two as-ofs), a duplicate-rows
  annotation. These are candidates for Egeria's survey types rather than
  RE-only names.
- The Lovelace-style "publish from an engine host under a request type"
  path already fits; nothing new is needed there.

## Costs side by side

| | Option 1, declared | Option 2, interim | Hybrid |
|---|---|---|---|
| Work before the first optional analysis publishes | catalogue for every kind + mapping language + generic publisher + one declaration | one publisher | one publisher + the catalogue extension; declarations as wanted |
| What Egeria readers see | native types | one type, a property bag | native where declared, labelled bag elsewhere |
| Honesty on the row | type named | "no native type declared" | both, by declaration |
| Risk | mapping drift as analyses change (a test per declaration) | publish-everything | the bag becomes permanent for analyses nobody upgrades |

## If the owner wants a slice

1. Extend the analysis catalogue to database and file-system analyses
   (declaration or `annotation_types: []` for each, so the inventory in
   (a) becomes a file, not a note).
2. The generic publisher with both routes and the Curate "Local results"
   band under the single-publish ruling.
3. Declare the first three natively: `db_change_rates`
   (`ResourceMeasureAnnotation` with interval and two as-ofs),
   `data_class_match` (`DataClassAnnotation`), `fs_layout_signals`
   (`ClassificationAnnotation`), as the pattern for the rest.
