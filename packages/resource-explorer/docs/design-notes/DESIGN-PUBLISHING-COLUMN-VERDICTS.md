# Design: publishing per-column verdicts and quality results

*(Status refreshed 2026-10-28: built on main 2026-10-20 (commits `b0d246a8`, `46d743fd`, `c7cf74d5`: Curate's Publish sends the stored column-profile verdicts). The original status line is kept below.)*

**Status:** design, not built. Written 2026-10-20 at the owner's request ("Write these publishing needs up
as a design note"), after #750, #759, #766 and #767.

**Builds on:**
- `DESIGN-ENRICHMENT-AND-CURATE-PUBLISHING-TO-EGERIA.md`: the rule that "proposed is always an annotation;
  confirmed becomes a fact";
- `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md`;
- the Curate catalogue commit (`catalogue_commit.py`).

## 0. Where things stand (verified 2026-10-20)

| What | Kept locally | Reaches Egeria |
|---|---|---|
| Data-class and reference-data verdicts (`postgres_column_profile`) | yes, `database_column_matches` (#759) | only through a **Survey Definition** run (#750; live-verified 2026-10-20: 3 DataClassMatch, 3 ColumnValueSample and 3 ReferenceDataMatch read back) |
| Nested JSON/XML profiles (`postgres_nested_columns`) | **no** | only through a Survey Definition run |
| The kept survey (schema, statistics, views) | yes, `database_surveys.survey_data` | through **Curate's Publish** (`catalogue_gateway.publish_local_report` → `publish_local_survey`) |
| Repository health, FOSS scorecard and view-complexity scores | yes | yes, the map in `jsonProperties.qualityScores` (#750); Egeria's own `qualityScore` is left unset and shows 0. The round trip is proven by the strict-bean unit test, **not yet live** |
| Quality checks (`DataQualityRule`) | not built | not built; the live probe of 2026-10-20 proved that the generic create, read-back and GovernedBy link work |

**Column elements.** A catalogue commit attaches one schema-kind catalog target per chosen schema, and
Egeria's JDBC cataloguer then creates that schema's `RelationalTable` and `RelationalColumn` elements.
RE does not create them, and its proof rows (`catalogue_commit_proofs`) record schemas and tables, **not
columns**. Finding a column's GUID therefore means walking the committed schema in Egeria at the time it
is needed: `catalogue_commit._walk_schema` reaches columns through schema → schema type → table →
`NestedSchemaAttribute`. For a schema that was never committed, no column element exists.

## 1. Curate's Publish sends the stored verdicts (build first)

**Problem.** Curate's Publish rebuilds annotations from the kept survey blob, which never contains the
column profile. So data-class and reference-data verdicts reach Egeria only when someone runs a Survey
Definition. The user guide documents this gap.

**Design.**

1. **Read.** Read the latest `database_column_matches` batch for the database.
2. **Rebuild, don't re-measure.** Turn each row back into a `ColumnMatch` (a new
   `ColumnMatch.from_row`, the inverse of `column_profile_step.column_match_rows`, with
   `SampleProvenance.from_dict`). Pass it through the step's own builders: `_data_class_annotation`,
   `_measure_annotation` and `_reference_data_annotations`. A Curate Publish and a Survey Definition run
   then produce identical annotations for the same verdict. One builder, never two.
3. **Keep the verdict's own date.** The verdict batch is usually not the survey being published: a
   person keeps a survey on Monday and profiles on Wednesday. Every verdict annotation carries the
   batch's `surveyed_at` as `measuredAt` in `additionalProperties`. The Publish band's "next publish"
   line names it, for example "data-class verdicts for 42 columns, measured 10-19".
4. **Drop vanished columns, and count them.** A verdict for a column that is not in the kept
   inventory is not sent. The band says so: "3 verdicts for columns no longer in the inventory, not
   sent". It is never a silent drop.
5. **The identity trap: fix it before building.** An annotation's qualified name is
   `{report prefix}::{check_name}::{item_key}`, and republishing the same kept survey reuses its report
   (`publish_local_survey`'s duplicate-name path adopts the existing element). So a *newer* verdict for
   the same column would collide with the old annotation's name. `publish_annotations` would adopt the
   old element, and the new verdict would never reach Egeria. The run would still read as published.
   Fix: verdict annotations published from Curate put the **verdict batch's** `surveyed_at` into
   `item_key` (`{column_path}@{batch}`). A new batch is then a new annotation, and a republish of the
   same batch converges on the same one. This also needs a test that two batches for one kept survey
   produce two annotations, not one adopted twice.
6. **Attach to the column when it exists.** For a column in a committed schema, the publish walks that
   schema once (`_walk_schema`), maps the column's qualified name to its path, and adds
   `AssociatedAnnotation` from the verdict to the `RelationalColumn`. The cataloguer's column
   qualified-name format must be confirmed by a live read before relying on it. For any other column,
   the verdict stays under the report with its `item_key`, which is today's behaviour, and the band
   names how many verdicts were linked to a column.
7. **Requests for action.** The step builds "propose a Data Class" RFAs inline, and the Survey
   Definition path sends them. **Curate's Publish sends them too** (owner, 2026-10-20: "RFAs - yes -
   although work in this area may lead to future changes"). The Purpose gate
   (`investigation-framing-design.md`) is not built; when it is, it replaces both paths' inline
   emission at once, and this rule may change with it.
8. **Identity.** Curate's Publish is a person's act: it runs with the person's own token, and Ownership
   is that person. That is unchanged.
9. **Not covered: nested columns, a future consideration** (owner, 2026-10-20). Nested JSON/XML
   profiles have no local store, so they stay Survey-Definition-only. Giving them one, as #759 did for
   the verdicts, would let Curate's Publish send them too. Their verdicts have a different shape
   (`nested_columns_step.py`: inferred schema, sample provenance), so that is its own table and reader,
   not a reuse of `database_column_matches`.
10. **Superseded batches in a reused report are retired** (review of #783; owner, 2026-10-20: "retire the old
    annotations"). A newer batch published into the same kept survey's report is added beside the older one (names
    ending `@<batch>`). After such a publish, `_retire_superseded_verdicts` reads the report and sets
    `contentStatus: DEPRECATED` on every column-profile verdict (`analysisStep` `DatabasePostgresColumnProfile`)
    whose `jsonProperties.measuredAt` is older than the batch just sent, including verdicts for columns the newer
    batch dropped. They are retired, never deleted: the evidence stays and the change is reversible. It is not a
    one-time sweep: it runs on every publish into a reused report, and finds nothing on a fresh one. A republish
    of the same batch changes nothing. Survey annotations are never touched. The outcome is counted in
    `verdicts.superseded` (`retired`, `already_retired`, `failed`; `state: not_checked` when the report could not be
    read). RE's annotation list shows a DEPRECATED badge; it does not hide them, and "N annotations in Egeria" still
    counts everything the report holds. The engine host now judges a publish on `annotations_this_publish`.

**Tests.** The ones that matter most:
- the rebuild is identical to the step's own build for the same `ColumnMatch`;
- two batches for one kept survey give two annotations;
- vanished columns are counted and not sent;
- a column in a committed schema gets `AssociatedAnnotation` and an uncommitted one does not;
- a Publish with no stored batch sends no verdicts and says "none stored", not zero.

## 2. A confirmed verdict becomes a fact: `DataClassAssignment` (next slice)

The publishing design's rule is that a proposal is an annotation and a person's decision is a fact. A
person confirming "this column is an email address" in Curate creates `DataClassAssignment` from the
`RelationalColumn` to the `DataClass`. A rejection records the person's decision locally and sends
nothing.

- **UI.** The database Structure tab's placeholder section ("needs data classes per column") becomes a
  per-column list with confirm and reject controls. It follows the architecture-component verdict pattern:
  a local verdict row, then Publish writes and reads back.
- **Store.** A verdicts table like `architecture_component_verdicts`, keyed by
  (database, column path, data class). It is append-only, and the latest row wins.
- **Needs a column element.** It works only for committed schemas. For the rest the control says
  "commit this schema in Contents first", and never writes the assignment to the database asset.
- **Draft data classes.** Confirming an `unmatched_patterned` proposal first needs its DRAFT DataClass
  accepted. That is two decisions, made in that order.

## 3. Quality results

- **Egeria's own `qualityScore`.** For annotations with one overall score, RE could fill it: repository
  health is already out of 100, and the FOSS scorecard is 0-10, so ×10. View complexity is not a 0-100
  quality and stays unset. **Wait for the Mandy question Q2** (a status and a nullable score) before
  choosing, because a filled 0 and an unset 0 read alike.
- **Live round trip.** One focused live check: publish one `QualityScoreAnnotation` and read
  `jsonProperties.qualityScores` back. The 2026-10-20 run skipped it because of a harness bug.
- **Quality checks.** `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` §7 and §8 stand. The `GovernedBy`
  target there now follows §0 above and the owner's answer of 2026-10-20: the column element for a committed
  schema. Otherwise the person is asked whether to add the object, shown when the profile found it, and a
  declined object gets no rule.

## 4. Order

1. §1, verdicts in Curate's Publish, including the identity fix and its test.
2. The focused qualityScores live check (§3).
3. §2, confirmed verdicts as `DataClassAssignment` facts.
4. After the review with Mandy: Egeria's `qualityScore`, and the quality-checks publish.

## 5. Owner decisions

1. ~~RFAs from Curate's Publish?~~ **Yes**, for parity, knowing that later work on RFAs may change it.
2. ~~Nested columns?~~ **A future consideration**, recorded in §1 item 9.
3. **Open:** should verdicts in Curate's Publish be opt-in per Publish ("include data-class verdicts"),
   or always on when a batch is stored? Recommended: always on, named in "next publish".
