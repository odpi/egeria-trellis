# DESIGN — One declaration, three consumers: how user-added analyses render in Understanding, compile into Chat, publish to Egeria, and carry confidence (2026-10-21)

From the design session, from a direct conversation with the owner on
2026-10-21. His general point: ease of extensibility for users adding
questions, analyses, surveys and sources, and what that does to the
design choices for presenting information, in Understanding and in
Chat, including the context compilation steps, and how confidence in a
result is represented as analyses get fuzzier (sensitivity, scope
conformance, matching). His specific answers on the configurable publish
path (the same day) are folded in.

## 0. The position

User extensibility decides the rendering architecture, in one direction.
If users add analyses, surveys, questions and sources, nothing in
Understanding or Chat can be written per analysis by hand; everything
shown must be driven by what the addition declares about itself. Survey
definitions (Dr.Egeria), discovery sources (data), library steps
(manifests) and the question catalogue already work this way. The last
mile is missing: the manifest does not yet say enough for a screen, a
chat answer or an Egeria publish to use it without someone writing code.

The proof, which is the gate of this note: a person adds a step in the
analytics package, declares it, and it appears on Understanding, answers
in Chat, and publishes to Egeria, with no change to RE's code. Until that
test passes, extensibility is a promise.

## 1. What a declaration carries

The `StepManifest` of the Analytic Library (and the catalogue entry for
RE's own analyses, which gains the same fields) declares, beside what it
has today (request type, questions by stable id, perspectives, purpose,
family, credential tier, nothing-to-derive guard):

| Field | Used by | Rule |
|---|---|---|
| `result_kind` | Understanding, Chat, Egeria publish | one of §2's kinds; none fits → `table` and say so |
| `measures[]`: name, unit, as-of source, value field | all three | a number never travels without its unit and as-of |
| `absence[]`: the sentence for not_measured, not_permitted, not_applicable, measured_nothing | all three | declared, never improvised (the find-absence rule as a field) |
| `extents[]`: for Chat, which fields, how many rows, in what order, the truncation sentence, the token cost, the citation shape | Chat | the compiler takes the declared shape and no other |
| `confidence`: the form from §4 and its basis field | all three | a score never travels without its basis |
| `annotation`: the Egeria type and the property map, or absent | Egeria publish | absent means "generic measure, no native type declared" (owner's answer 1) |
| `answers[]`: question ids | Chat, candidates | no question, no quotation; still runnable and rendered |
| `scope`: the level (resource, container, member, field) | all three | the "scoped by" every answer already carries |

Who declares: the person who adds the analysis (owner's answer 2). An
experiment or a temporary analysis runs with the minimum (kind, measures,
absence) and publishes only as a generic measure on explicit intent; a
repeatable one is promoted through the Library's declare step to a full
declaration. That promotion is the second workflow the owner named, and
it is the existing `declare` command, not a new mechanism.

## 2. Result kinds, a fixed small vocabulary

| Kind | What it is | Understanding renders | Chat extent | Egeria type by default |
|---|---|---|---|---|
| `measure` | one or a few numbers with units and as-of | stat tile, delta against the prior run | the numbers as a sentence with as-of | ResourceMeasureAnnotation |
| `series` | one measure over runs or time | line chart | last value, trend word, span | ResourceMeasureAnnotation per point, or the series as one |
| `distribution` | values of one measure across items (tables by size, files by type) | bar chart growing with item count | top N with the rest counted | ResourceProfileAnnotation |
| `list` | items with attributes (endpoints, classes, files) | table with resizable columns, filter, counts labelled | declared columns, N rows, truncation sentence | SchemaAnalysisAnnotation or a list on one annotation |
| `graph` | nodes and edges (relationships, dependencies) | the existing graph renderer, counts labelled | nodes and edges counted, the hubs named | DataFlow or lineage when published as facts; ResourceMeasure for counts |
| `finding` | something wrong or notable, with severity and evidence | finding row with the cue and the sentence | the sentence and its evidence pointer | RequestForActionAnnotation |
| `proposal` | a suggested fact awaiting a verdict (a class, a term, a check, a classification) | proposal row with the verdict selector | "proposed, not confirmed" with its basis | the proposal's own annotation; a fact only after a verdict |
| `table` | anything that fits none of the above | generic table, declared headers | declared columns | ResourceMeasureAnnotation with properties, labelled generic |

Per-kind polish is allowed inside a renderer; nothing is allowed per
analysis. Eight kinds cover every analysis RE has today; a ninth is a
design change, not a code change in one analysis.

## 3. Chat: context compilation driven by declarations

What the compiler does today: it builds extents from each analysis's
manifest lists, stores the extents but not the compiled text, and cites
by section key. The TestGen answer showed the consequence: the model saw
summary lines only and nobody could prove what it saw. Declared, the
compile is five steps, each with a rule:

1. **Route by question.** The question's stable id selects the analyses
   that declare they answer it, in the stage's order. An analysis with no
   declared question is never pulled into an answer, however relevant it
   looks.
2. **Select extents by declaration.** Each analysis contributes exactly
   its declared extents: fields, row count, order, the truncation
   sentence ("12 of 54 routes shown, by request count"). The compiler
   never invents a shape.
3. **Say absences in words.** An analysis that has not run, could not
   run, or measured nothing enters the context as its declared sentence,
   not as silence, so the model cannot mistake "never measured" for
   "nothing there".
4. **Fit a budget and say what was left out.** Each extent declares its
   token cost; the compiler fills the budget in declared priority and
   writes the omissions into the context ("dependency list omitted, 3,100
   tokens, open it"), so an answer never pretends to have seen what it
   dropped.
5. **Persist what the model saw.** The compiled text and the row ids
   behind each extent are stored on the answer, with each extent's as-of,
   so "open what this answer saw" opens exactly those rows at that
   survey, and the live list is a second, labelled thing.

A user-added analysis enters Chat by declaring its question, extents,
absence sentences, cost and confidence form, and nothing else. The
answering app gets its citation shape from the same declaration.

## 4. Confidence: declared by form, never a bare number

The owner's point: answers are qualified by as-of and scope today, and
that is enough for a count; it is not enough for sensitivity, scope
conformance or matching. The rule: **a result's confidence is one of five
forms, declared in the manifest, and a score never travels without its
basis.** The forms:

| Form | What it means | What travels with the result | Egeria |
|---|---|---|---|
| `measured` | an exact read (a count, a date, a size) | as-of, scope, the credential's reach; no score, because none is meaningful | the annotation's as-of; `confidenceLevel` left empty |
| `estimated` | a number from a method with a known error (a catalog estimate, a sample) | the method, the sample ratio or the estimate's age, the bound where one exists ("±5% at this sample") | `confidenceLevel` with the method in `additionalProperties` |
| `inferred` | a match or a classification from rules or models (data class, sensitivity, scope conformance, semantic match) | the score, the basis that produced it ("3 of 4 rules matched: name, format, distinct count"), the evidence count, and whether the score is calibrated or ordinal | `confidenceLevel` 0 to 100 (Egeria's annotation field) plus the basis; for a fact, Egeria's `Confidence` governance classification with its level and basis |
| `judged` | a person's verdict | who, when, on what evidence; confidence is the signature, not a number | the verdict as a fact; author and date |
| `generated` | a model's answer | the extents it drew on, their forms and as-ofs, and the coverage ("answered from 3 of 5 sources; dependency list omitted") | not published; an answer is not an annotation |

Rules that follow:

- **No aggregation into one number.** A chat answer that rests on an
  inferred match and a measured count reports the weakest link, not an
  average: "rests on an inferred sensitivity match, 3 of 4 rules, 0.6".
- **The basis is shown where the score is**, on the row, in the tile,
  in the citation. A score alone is a claim; a score with its rules is
  evidence a person can argue with.
- **Calibration is stated.** An inferred score declares whether 0.8 is a
  calibrated probability or a rule count; most of RE's today are rule
  counts and must say so.
- **Confidence never substitutes for absence.** A rule that did not run
  is "not measured", not a low score.
- **Facts from inferred results** (the owner's sensitivity case): a
  better sensitivity analysis publishes an `inferred` proposal with its
  score and basis; the Confidentiality classification is set only through
  a verdict, with the confirmed basis carried as Egeria's `Confidence`
  classification on the fact. How much of that confirmation can be
  automated (a threshold per class, a trusted analysis) is its own short
  design with a worked case; the owner said it needs more work.
- **Follow Egeria's lead.** `confidenceLevel` on annotations and the
  `Confidence` classification on elements already exist; RE uses them and
  puts the basis beside them rather than inventing a parallel scale.

## 5. The publish table (the owner's answer 3)

On Curate's first tab, under the single-publish and publish-ack rulings,
one table: a row per measure or result, columns analysis, measure, value
with unit, as-of, confidence form with its basis, scope, and "writes as"
(the declared Egeria type, or "generic measure · no native type
declared"); a Publish | Keep in RE selector per row, saved as a signed
choice; the page's one press submits the table, acknowledges, validates
by the report read-back, and leaves the persistent cue. Rows whose kind
is `proposal` do not appear here; they have the verdict selector on the
Structure tab, and a verdict is a fact path, not a publish of a measure.

## 6. Dashboards (owner's answer 4), held for their own note

Results a dashboard displays are local; measures publish separately from
the table above; a tile says "local · not published" or "published · read
back <time>". The dashboard definition should be authored in Dr.Egeria as
the Portal's are, naming the analysis and method behind each tile from
the same declaration; the owner thinks that investment worthwhile and it
needs its own design.

## 7. The gate, by use

A person adds `example-step-x` to the analytics package with a manifest
declaring `result_kind: distribution`, two measures with units, four
absence sentences, one question id, one extent with its cost, and
`confidence: estimated` with a sample ratio; declares it with the
Library's command; runs it on coco_pharma. Then, with no RE code change:
Understanding shows a bar chart with labelled counts and the sample
ratio; Chat answers the declared question citing the step's rows and
stores what it saw; Curate's table offers the two measures, one is
published as a generic measure and reads back; and the never-run state
on a second database shows the declared sentence. The same person then
adds the annotation type to the manifest and the row's "writes as"
changes to the native type on the next publish.

## 8. What this changes and what it does not

- No change to the in-flight Accept | Reject builder or to how Q2 part 2
  publishes; the quality slice already declares its kinds in effect
  (`proposal` for checks, `measure` for profiles, `finding` for failures).
- The extensibility walking skeleton's interim (ResourceMeasureAnnotation
  plus properties) becomes the declared "generic measure" route, labelled.
- The Portal's report specs carry presentation hints for the same reason;
  the manifest and the report spec should share this vocabulary (result
  kinds, confidence forms), one declaration serving the Portal tile too.
- Open: the automation threshold for facts from inferred results; the
  dashboard-definition design; whether the eight kinds need a ninth for
  text (narratives, documentation excerpts), which today render as `list`.
