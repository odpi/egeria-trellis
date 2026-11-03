# The first context pack, worked: coco_pharma for a data scientist (2026-10-07)

Design session note, the first instance the product requirements
(`docs/context-as-product-requirements.md`, P1–P13) are checked against.
Nothing built; every value below is read from evidence notes on main
(`MORNING-NOTE-GATE-2026-10-06.md`, the restore and roll-forward notes) or
marked as what a build would have to read. Consumer kind: **data scientist
or data engineer**, the owner's first.

## 1. Subject

The database `localhost_docker_coco_pharma` (Egeria element 17f0a963,
restored 2026-10-06), as the investigation "Egeria Understanding" sees it:
scope signed on 2026-10-05, purpose and lens as the investigation holds
them. The pack is **per subject and per consumer kind** (P1): one pack, for
this database, for a data scientist.

## 2. What the governed record holds for it today (P2: record only)

| Part | Content in the record now | Source | State |
|---|---|---|---|
| Asset | the database element, name, description and version RE supplied, ownership `Jules` | RE's publish, confirmed by the owner's gate | published |
| Schemas | `coco_sus` (30 tables) and `coco_ods` (23 tables) as `DeployedDatabaseSchema` under the database, catalogued by Egeria's cataloguer with tables and columns; `us_sales` as an RE-made schema with 1 table, its target detached | RE's catalog commits, the cataloguer | cataloged; us_sales awaiting re-attach |
| Terms | one `SemanticAssignment`: `us_sales_forecast` → `Sales Forecast` | written at the owner's direction for gate item 3 | confirmed (a person's act) |
| Survey report | RE's report, 76 annotations, published whole | the first commit | published |
| Egeria's own surveys | two engine actions completed (15 and 22 minutes) with their reports and annotations on the database | Egeria | published by Egeria |
| Lineage and dependencies | none confirmed; a repository→database relation is a candidate in the Find/Integrate design | — | not established |
| Zones and licence | no `ZoneMembership` (everyone visible); no `License` classification | the configured-only rule | not established (licence), none (zone) |
| Quality observations | RE's local observations (row counts, column profiles) exist in the ODS, not confirmed or published as annotations on the asset | RE | **not published**, so excluded (P2) with that word |

## 3. The pack as a data scientist would receive it

**Tabular shape** (one row per table, one column per fact; states as words,
never blanks):

| schema | table | columns | rows (last measured) | term | owner | licence | provenance |
|---|---|---|---|---|---|---|---|
| coco_sus | *(30 rows)* | from the cataloguer | not published | nothing found | Jules (database) | not established | cataloguer run of 2026-10-06 13:27Z, element GUIDs |
| coco_ods | *(23 rows)* | from the cataloguer | not published | nothing found | Jules (database) | not established | same |
| us_sales | us_sales_forecast | 11 | not published | Sales Forecast (confirmed 2026-10-06) | Jules (database) | not established | restore read-back 2026-10-06 21:xxZ |

**Document shape** (one document per element for retrieval): a prose
rendering of each table's facts with the same provenance as metadata, so
"what does us_sales_forecast hold" retrieves a document that says it has
eleven columns, is assigned the term Sales Forecast, belongs to a database
owned by Jules, with row counts not published and licence not established.

## 4. The manifest (P6: honesty in the manifest)

```
Pack: coco_pharma · data scientist · v1 · as of <build time> · built for <identity>
Included: 1 database · 3 schemas · 54 tables · 1 term assignment · 1 owner · 1 survey report (76 annotations) · 2 Egeria survey reports
Left out and why:
  26 schemas · undecided in the scope · not cataloged
  1 schema (eu_sales) · left out of the scope by a person
  row counts and column profiles · measured by RE · not published to Egeria
  licence · not established
  lineage and dependencies · not established
Identity: read as <identity>; zones applied by Egeria: none on these elements
```

## 5. What the first instance teaches about the requirements

- **P2 bites immediately and usefully.** The facts a data scientist most
  wants (row counts, column profiles) are the ones RE has and has not
  published, so the pack says "not published" on every row. That is the
  right outcome: it makes the Enrichment-publishing slice P1 the thing
  that fills the pack, and the pack's manifest is the measurement of how
  much is published.
- **P4 provenance is cheap where the record is Egeria's** (cataloguer run,
  element GUIDs) and absent where RE never published, which is the same
  line as P2.
- **P8 fit** has nothing to derive from yet: no "fit for a data scientist"
  mark exists on the resource. The first Curate confirmation of that kind
  is a one-row slice.
- **P12 measured value**: the task to measure against is "answer five
  questions about coco_pharma's sales tables in a notebook with and without
  the pack", scored by whether the answers cite the record and carry the
  state words. Proposed as the first measurement.
- **P13 the chat application** would render the same manifest as a
  paragraph: "I can tell you about 54 tables in three schemas; row counts
  were measured but not published, so I will not quote them."

## 6. Build order for the first pack

1. The packaging routine in the Analytic Library (`context_pack(subject,
   consumer_kind, identity)`), reading Egeria only, producing the tabular
   and document shapes and the manifest; run from a notebook first (the
   consumer's own host).
2. The P1 Enrichment-publishing slice, which turns "not published" into
   facts in the record and is measured by the manifest's change.
3. The pack definition as an Egeria element (P11), after the leads answer.
4. The "fit for a data scientist" Curate mark (P8).

## 7. Questions

**Owner:** the measured task (§5); whether the 26 undecided schemas appear
in the pack as "undecided" rows or are omitted with the count only.

**Egeria leads:** the element type for a pack definition; whether a
built pack version is worth recording in Egeria at all or only its
definition.
