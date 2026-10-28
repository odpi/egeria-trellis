# DESIGN — Publishing Enrichment and Curate to Egeria: annotations and facts (2026-10-06)

Design session note, from the owner's statement of 2026-10-06 and the day's
live evidence. Status: design for discussion; nothing here is built. Companion
notes: `BRIEF-ENRICHMENT-E3-OBSERVATION-STATES.md` and its implemented note,
`BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md`, `NOTE-ANNOTATION-ENVELOPE-ATTRIBUTES.md`,
`DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md` (A3, A7, R5, R9, R10),
`evidence/INCIDENT-117-ARCHIVE-CASCADE-2026-10-06.md`.

## 1. The owner's rule

Two kinds of publishing exist and must stay distinct on screen, in code and in
Egeria:

1. **Survey publishing.** A survey run produces a survey report and annotations;
   RE publishes the report whole (owner decision 2026-10-06).
2. **Curate publishing.** In Curate a person publishes finer-grained resources
   and the things related to them.

The owner's intention for each: **Enrichment creates additional annotations in
Egeria related to the top-level resource. Curate adds facts with the right
relationships around the Egeria asset, not annotations.** An annotation is a
measured value with an honesty envelope. A fact is a relationship or a
classification that the rest of the ecosystem reads without knowing RE exists.
This matches Egeria's own pattern: surveys write annotations, stewardship turns
them into metadata. RE's Enrichment and Curate are that stewardship step made
explicit.

Two further principles from the same day: Egeria **rolls forward, never undoes**
(a correction is a new event, never an erasure); and anchored versus
non-anchored elements **each have a role and consequences**, to be worked
through with the Egeria lead, so this note does not prefer one.

## 2. What RE holds today, and where each thing should go

Read from `web/routes/context.py` (`EnrichmentField`, `ContextData`,
`PROPOSING_ANALYSES`) and the Curate scope and commit modules.

| RE record | Kind in RE | Today in Egeria | Intended channel | Egeria form |
|---|---|---|---|---|
| Survey measurements (every analysis) | annotation with envelope | survey report + annotations, whole | survey | as now |
| Context *observation* fields: licence (proposed by `license_classification`), environment, retention | observation: proposed / confirmed / overridden / survey-now-disagrees | not published | **Enrichment → annotation on the asset** | an annotation of a type per field, `AssociatedAnnotation` to the asset, carrying the envelope (resultState, reason, producingRun, measuredAt) and the person's state (confirmed by whom, when; overridden value) |
| Context *judgement* fields: sensitivity, criticality, intended use, actual use, owner, notes, purpose | judgement: author, date, evidence on screen | owner only (Ownership on the database, built in slice B) | **Curate → fact** once a person confirms; until then **Enrichment → annotation** marked as a judgement | Ownership (built), Confidentiality / Criticality / Confidence governance classifications, a description or purpose property on the asset; notes as a Comment or NoteLog |
| Human-supplied question answers (dependencies, cost, skills, monitoring, security, governance, estate fit) | answer with author, date | not published | **Enrichment → annotation** (they are testimony, not measurements, but they are not facts about Egeria's graph either) | one annotation type per question group, `AssociatedAnnotation` |
| Doc sources (README, licence file, policy document) | source with probe state | not published | **Curate → fact** | `ExternalReference` linked to the asset |
| Term match proposals (glossary terms on tables and columns) | proposal: proposed → confirmed | not published; the E3 states exist | proposed: **Enrichment → annotation** (`SemanticAnnotation`); confirmed: **Curate → fact** | `SemanticAssignment` to the `GlossaryTerm` |
| Logical schema match proposals | proposal | not published | same pattern | `SchemaTypeImplementation` or the relationship the Egeria lead names |
| Data classes per column | measurement | not published | survey annotation; confirmed: **Curate → fact** | `DataClassAssignment` |
| Dependencies and relations between resources (repo informs database) | proposed → confirmed (Find/Integrate design §13) | not published | proposed: annotation; confirmed: **Curate → fact** | a dependency relationship from the type list the Egeria lead will supply; never lineage |
| Catalog scope and commit (schemas, tables) | scope record + proof rows | schema elements + catalog targets (slice B) | Curate (built) | as built |
| Investigation binding, group membership | RE record | project binding exists | Curate → fact where Egeria has the type | `ProjectScope` / collection membership (built for investigations) |

The rule that falls out: **proposed is always an annotation; confirmed becomes a
fact when Egeria has a type for it, and stays an annotation when it does not.**
A judgement a person made is a fact about that person's opinion, and Egeria's
governance classifications exist to hold exactly that, so judgements go to
facts on confirmation too.

## 3. How a publish works, for both channels

The same mechanics the catalog commit uses, with the proof-row rules learned on
2026-10-06 applied from the start:

1. **One publish per confirmed row, idempotent, one-way.** The row's identity
   in Egeria is deterministic (annotation qualifiedName from the asset,
   the field and the proposing run; a relationship from its two ends and
   type). A second publish finds the existing element by GUID first, by
   name second, and reuses it (never a second element; the SurveyReport 409
   of 2026-10-06 is the case).
2. **A proof row per element, by GUID, written only after a read of that GUID
   returns what the row claims.** The row stores Egeria's full response.
   Adoption after a create error is its own row kind and is shown first.
3. **Status words derive from proof rows:** "sent · waiting for Egeria",
   "published · read back <when>" (annotations), "confirmed in Egeria · read
   back <when>" (facts), "not published · <Egeria's sentence>".
4. **Roll-forward only.** A changed judgement publishes a new version of the
   classification or a new annotation with the previous one marked
   superseded; nothing is deleted. An override is a new annotation whose
   envelope says `overridden` and names the measured value.
5. **No archive or delete from these channels at all**, so the ISSUE-117 block
   does not apply to them and they can ship while it holds.
6. **Zones and licences:** none written unless `EXPLORER_PUBLISH_ZONES` is
   configured, as slice B; a confirmed licence observation publishes as the
   `License` classification only in the Curate channel, after a person
   confirms it, because it changes what others may do.
7. **Profile:** which annotation types publish by default and which need a
   person's confirmation is the publication profile of R9 (one default,
   teams copy it). This note's table is the default profile's first content.

## 4. What the person sees

- The Context tab gets one more line per row in the Egeria lane, using the
  designer's cue vocabulary (2026-10-06): ◔ "sent", ✓ "published · read back",
  with the sentence one gesture away.
- The Enrichment stage header gets a "Publish to Egeria →" control for the
  stage's confirmed rows, and "Publish again" after (verbs ruling: Publish for
  anything sent to Egeria).
- Curate rows that confirm a proposal publish the fact as part of the
  confirmation ("Save" records the choice; the row then reads "sent · waiting
  for Egeria" and "confirmed in Egeria · read back"), so a person never has
  to publish a fact separately from confirming it. Whether this is one press
  or two is a designer question (below).
- Proposed rows never publish from a person's press; the proposal annotation
  goes with the survey's own publish.

## 5. Dependencies and questions

**For the Egeria lead (Mandy).** (1) Annotation types for Context observations
and question answers: extend the survey annotation types (she said yes on
2026-10-05) or reuse `ResourceMeasureAnnotation` with a property naming the
field. (2) The relationship type list for dependencies between resources
(promised). (3) Whether a judgement should be a governance classification
(Confidentiality, Criticality, Confidence, Retention) or an annotation until a
steward acts, in her model. (4) Anchoring of annotations published outside a
survey report: anchored to the asset (today's shape) means an archive of the
asset takes them, which is the intended lifecycle; confirm.

**For the designer.** (1) One press or two: does confirming a proposal publish
the fact, or does Curate get a stage-level "Publish to Egeria" like
Enrichment? (2) The Egeria lane on Context rows: the same cue vocabulary, or a
single stage line? (3) Where a superseded judgement shows (history on the row,
or the Egeria lane only).

**For the owner.** Which first: Context observations as annotations (small,
proves the annotation channel on coco_pharma's licence row), or term
assignments as facts (proves the fact channel, needs the term-match proposals
to be produced first, which they are not yet).

## 6. Slices, in order

- **P1 Enrichment annotations.** Confirmed Context observations and question
  answers publish as annotations on the asset with the envelope; proof rows by
  GUID; the stage control; the Egeria lane. Gate on coco_pharma's licence row
  and one question answer, read back in Egeria by GUID.
- **P2 Curate facts, first three.** Ownership (already built, brought under the
  same rules), Confidentiality from the sensitivity judgement, `ExternalReference`
  from doc sources. Gate: confirm each on coco_pharma, read back the
  classification or relationship by GUID.
- **P3 Term assignments.** Needs the term-match proposals (Curate for databases,
  designer reply of 2026-10-01) and a glossary; the fact channel for
  `SemanticAssignment`, using the same read-first rule proven in the restore.
- **P4 Dependencies.** After the relationship-type list arrives.

Each slice records its mapping rows in the implemented note, so §2's table is
kept true by the builder, not by this note.
