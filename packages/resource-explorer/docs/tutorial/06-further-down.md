# Part 6 — Discovery, Assessment and Analysis: go further down the funnel

Erin's next question is whether `coco_pharma` is already in the catalog. Peter answers it with Discovery, the cheap
stage that works from what Scouting already collected. Then he decides the database is worth a closer look, and goes
down the [funnel](glossary.md#the-funnel) (part 4) only as far as he needs: he reads Assessment, and on Analysis he
runs one analysis instead of a whole survey.

**Before you start.** [Part 5](05-scouting.md): `coco_pharma` has had its Scouting survey. This part takes about
twenty minutes. Steps 1 to 5 only read. Step 6 runs one analysis, which keeps its result in Resource Explorer and
sends nothing to Egeria.

*Screens read on 2026-11-03.* The screenshots show `coco_pharma_trial`, as in parts 3 and 5. Use `coco_pharma` on
yours. The sidebar lists work lists that a new Quickstart does not have; ignore them (gap G25). Before these
screenshots were taken, a Database Quality Survey had also been run on this database, so three rows on Analysis
(what the columns contain, and the two "Which data-quality checks…" rows) show results that yours will not.

## Steps

1. **Click 2 Discovery, and read the first questions.**

   ![Is coco_pharma already in the catalog? The Discovery stage of coco_pharma_trial. The header reads "surveyed 19h ago · local survey · not published to Egeria". The count line reads "9 of 19 answered · Discovery", with the key: answered 3, answered automatically 2, nothing found 4, partial 4, not run 3, needs a person: declare a lens 2, no reader yet 1. The first row, "Has this resource already been cataloged in Egeria, and when?", has the mark ∅ and answers "Not yet published to Egeria." and "Nothing is recorded for this — a real absence, not an unrun analysis." Below it, "Is there any existing use within our organization?" answers "Registered, but not assigned to a group; not in scope for any investigation.", and "Any known feedback?" answers "No feedback recorded yet."](images/06-further-down/S21-already-catalogued.webp)

   *Discovery, before anything is run on it.*

   The count line reads "9 of 19 answered · Discovery". Nothing has been run on this stage yet: Discovery works
   out most of its answers from what Scouting stored, so many rows are answered already.

   The first row asks Erin's question: "Has this resource already been cataloged in Egeria, and when?" It shows ∅
   and "Not yet published to Egeria." Under it: "Nothing is recorded for this — a real absence, not an unrun
   analysis." This is an answer. Resource Explorer looked, and found nothing.

   The next two rows answer in the same way: "Registered, but not assigned to a group; not in scope for any
   investigation." and "No feedback recorded yet."

2. **Scroll to "Is this a copy, subset or superset of a resource we already know".**

   ![A likely copy: three Discovery rows. "Is this a copy, subset or superset of a resource we already know — structurally, by schema or file signature?" has the mark ◐ and answers "Closest of 11 comparable database(s): localhost_docker_coco_pharma (likely_copy, column-signature Jaccard 1.00, containment 1.00)." with "Answered for the database as a whole, not schema by schema." Below it, "Are there views, materialised views, functions or triggers that encode business logic?" has ○ and "Nothing has been measured for this yet. Run: sql_analysis." Then "Which tables would a consumer start with?" lists five tables, starting with coco_hrim.job_assignment (336,101 reads, commented).](images/06-further-down/S21b-likely-copy.webp)

   *The database is not in the catalog, but its structure matches one Resource Explorer already knows.*

   The answer names the closest of the databases Resource Explorer has surveyed: "localhost_docker_coco_pharma
   (likely_copy, column-signature Jaccard 1.00, containment 1.00)". That is the Quickstart's own `coco_pharma`, the
   one the Getting Started tutorial surveyed. Every column signature matches. So the trial database is not in the
   catalog yet, but it looks like a copy of one that Resource Explorer already knows. On the Quickstart it is more
   than a copy: in part 3 you registered the same database a second time.

   The mark is ◐, partial. The match is by column signatures: the structure, not the values.

   Further down, "Which tables would a consumer start with?" names five tables, by how often they are read.

3. **Decide whether the database is worth a closer look.**

   For Peter, yes. The trial's figures are in it, and Erin needs to know whether they can be trusted. Many data
   sources stop here; this one goes on.

4. **Click 3 Assessment, and read the answers.**

   The count line reads "4 of 9 answered · Assessment", and nothing has been run on this stage either. The four ✓
   answers are worked out from what earlier surveys stored:

   - "How complete and consistent is this database's documentation": "37 of 263 base tables declare no primary key.
     54 of 263 base tables have no comment; 32 of 1916 columns do."
   - "How well-modelled is this database": the same figures, from the same stored analyses.
   - "Is this database maintained and healthy": "34,207 writes and 2,989,434 reads since statistics collection
     began (never reset)."
   - "Is this database resilient": "primary; no replicas; WAL archiving off; no backup tool detected."

   Three rows show ◌ "no reader yet", and the license row shows ⚠ "needs you": a person records the license, in
   Enrichment (part 7). One row shows ○: the reference-data question, which an Analysis step answers ("Run:
   postgres_column_profile").

   The stage has no survey of its own to run. Its **Survey & analyses** tab lists Egeria's own surveys and, under
   "OTHER STAGES", the other stages' overviews. The database's "Database Assessment Survey" is offered on the
   Analysis stage instead (gap G23).

5. **Click 4 Analysis, and find a row that nobody has looked at yet.**

   ![What has not been looked at yet: three Analysis rows marked ○. "Which columns conform to a known Data Class, with what confidence, and which look like a class we do not have yet?" and "Which low-cardinality columns conform to a known reference-data set, and which should become one?" each read "Not yet run. Run: postgres_column_profile." "Which columns hold semi-structured data (JSON, JSONB, XML, arrays or hstore)…?" reads "Nothing has been measured for this yet. Run: postgres_nested_columns." with "nested_column_profile · not yet run". Each has a run link.](images/06-further-down/S22-not-measured.webp)

   *○: nothing has been measured yet.*

   The count line reads "4 of 17 answered · Analysis". Find "Which columns hold semi-structured data…". It shows ○
   and "Nothing has been measured for this yet." The next words name what would answer it: "Run:
   postgres_nested_columns." Two rows above it say the same thing in other words: "Not yet run. Run:
   postgres_column_profile." (gap G32).

   ○ is not ∅. ∅ in step 1 was an answer. ○ means nobody has looked yet.

6. **Find a partial answer, and run only the analysis it is missing.**

   On **Questions**, find "Are this database's planner statistics current?". Its answer is there: "Of 263 tables:
   215 never analyzed, 0 past autovacuum's analyze threshold, 48 current." But the line under it says
   "statistics_freshness has not run, so this answer is partial". Part 5's "Is this database alive" answer said the
   same. The row already shows ✓ although the answer is partial: read the words, not only the mark (gap
   G13).

   You could run the whole "Database Analysis Survey": "8 steps · none fetch · runs 13 analyses". You need only
   one of them. Click **Survey & analyses**. Under "ANALYSES", each analysis has its own card. Find **Table
   Statistics Freshness**: "2 questions › · not yet run · declared fast", "in Database Analysis Survey · step
   statistics_freshness", and "needs no credential". It reads only what Resource Explorer already stored, so it
   opens no connection to the database.

   Click its **Run →**. There is no dialog: it runs at once.

   ![One analysis run on its own: the ANALYSES list on the Analysis stage. Table Statistics Freshness now has ✓ and reads "2 questions › · just now · via survey · declared fast", "in Database Analysis Survey · step statistics_freshness", "needs no credential", and its button has become "Re-run →". Around it, Schema Conventions, Schema Diff (Columns), Statistics Freshness and Table Change Rates still read "not yet run", each with "Run →".](images/06-further-down/S23a-one-analysis-ran.webp)

   *One analysis, run on its own. The others are still not run.*

   The card now shows ✓ "just now", and **Run →** has become **Re-run →**. **Activity** in the header has a new
   entry, "statistics_freshness (per-card run): 1 step: ran". A run from a card keeps its result in Resource
   Explorer; it publishes nothing.

   Click **Questions** again, and find the same row.

   ![The partial answer made whole: "Are this database's planner statistics current?" with ✓ and the answer "Of 263 tables: 215 never analyzed, 0 past autovacuum's analyze threshold, 48 current." Its source line reads "db_statistics_freshness, statistics_freshness · run just now", and under it "statistics_freshness · measured · 215 of 266 tables (81%) · run just now". The line saying the answer was partial has gone.](images/06-further-down/S23-statistics-current.webp)

   *The same answer, no longer partial.*

   The line saying the answer was partial has gone. In its place: "statistics_freshness · measured · 215 of 266
   tables (81%) · run just now". The 215 here is the never-analyzed count again: all 266 relations were measured,
   and the line's label does not say so (gap G33). The "Is this database alive" row on Scouting has changed in the
   same way, because both rows use this analysis.

   That is the funnel inside a stage: one cheap analysis, run where an answer needed it, instead of a survey of
   eight steps.

7. **Find a row marked ◌, and read it.**

   "Which glossary terms do these columns probably mean?" shows ◌ "no reader yet": "No mechanism exists for this
   yet — no analysis produces it." There is no way to answer it automatically yet. It is not a failure, and running
   another survey will not change it.

## What can go wrong

- **A slow pane.** A stage can show "still loading" in its count line for many seconds (gap G10). Wait until it
  goes before you read the counts.
- **The "LAST SURVEY" tile names the wrong survey.** On **Survey & analyses**, the tile reads "Database Assessment
  Survey" on the Analysis stage, though that survey has not run; on Assessment it reads "Database Scouting Scan" for
  the same run (gap G31). Read the question rows' source lines instead: they say what ran, and when.
- **An analysis needs a broader account.** Some analyses read rows, not only the catalog. If the account you
  registered cannot read a table, the answer says so, as the Scouting answers did in part 5.

## What you have now

Discovery has told Peter that `coco_pharma` is not in the catalog, but that it looks like a copy of one Resource
Explorer already knows. Assessment answered four questions without running anything. On Analysis, one analysis ran
on its own, and turned a partial answer into a whole one.

**Next:** [Part 7 — Enrichment: add what only you know](README.md#the-core-tutorial). Peter records who owns the
database, how sensitive it is, and what it is for.
