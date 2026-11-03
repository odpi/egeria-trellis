# Data quality checks in Resource Explorer, and what it takes from TestGen

**Status:** live guide. Written 2026-10-28, brought up to date 2026-10-30 against `origin/main` at `aacdfaff`.

This guide is for people who use Resource Explorer (RE) to check the quality of a PostgreSQL database. It covers:

- measuring the values in each column (the value profile);
- reviewing the quality checks RE proposes;
- running the checks you accept;
- reading the results in Egeria;
- publishing accepted checks to Egeria as data quality rules;
- checking many databases at once, or on a schedule.

---

## What RE takes from TestGen

DataKitchen's **DataOps TestGen** is an open-source data-quality tool (Apache-2.0). It comes with a catalog of test types. For each type, the catalog says:

- what to measure;
- when the check is worth proposing;
- what threshold to start from;
- the SQL that runs it.

**RE uses that catalog as data. It does not run TestGen.**

- RE keeps a copy of TestGen's test-type definitions (release 5.134.0). It runs them itself, with its own survey steps, against your database.
- RE does not call or connect to TestGen. Nothing of TestGen runs: not its engine, its database, its scheduler or its screens. RE has no TestGen connection setting.
- In RE and in Egeria these features are called **Value profile** and **Quality checks**. The word "TestGen" shows up in only three places: one command-line command, the origin of imported proposals, and the description of a check (see below).
- Every result comes from one engine, RE's own.

### Where a check says it comes from

A check's **detailed description** says where its idea came from. The short names on screen do not.

- **"Implements TestGen test type <id>"** means RE runs TestGen's own definition, unchanged.
- **"Inspired by TestGen's test type <id>"** means RE proposed the check itself, or the definition has changed since it was copied.

The same words go into the description of the data quality rule in Egeria.

### How much of TestGen is ported

TestGen has 89 test types. **RE runs 68 of them.** The others are not ported:

- checks across two tables, and checks you write as your own SQL;
- checks TestGen only offers for manual use;
- trend monitors (volume and freshness over time), which have a design but are not built;
- a few checks that need data TestGen collects over several runs.

The ported types fall into these groups:

| group | what they check |
|---|---|
| Basic checks | missing values, required columns, unique values, future dates, earliest date, row-count change |
| Recency | a date column that should be kept up to date; dates in the future |
| Baseline match | whether a table has drifted from the profile it was accepted against. You turn this on per table |
| Duplicate rows | whole rows that appear twice |
| Schema drift | columns that appeared, disappeared or changed type between surveys. These read earlier surveys and run nothing against your database |
| Column behavior | numbers that shift, outliers, values that do not match a list or a pattern, gaps in dates |
| Text shape | email format, street address, US state, odd characters, leading spaces |
| Hashes | an exact fingerprint of a column's values. **Off by default** (see "Optional settings") |

The full list of 89, and what each one needs, is in `INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md` (see "Where this comes from").

### Does RE give the same answers as TestGen?

It was checked. In October 2026 the RE and TestGen results were compared on the same two databases, restored from a dump so nothing changed during the run. TestGen's own release 5.134.0 was used.

The two reports are kept in the repo: [calibration-adventureworks-2026-10-29.md](design-notes/evidence/calibration-adventureworks-2026-10-29.md) and [calibration-coco_pharma-2026-10-30.md](design-notes/evidence/calibration-coco_pharma-2026-10-30.md).

- **adventureworks:** 1,150 checks proposed by both. The profile numbers agree on every count. Of 1,150 results compared, 1,145 agree and 5 disagree. All 5 are duplicate-row checks on tables with an XML column. TestGen reports an error. RE leaves the XML column out of the comparison, says so, and passes.
- **coco_pharma:** 2,184 checks proposed by both. Of the results compared, all 2,184 agree and none disagree.
- **Where RE does not decide:** a check that needs a measure RE does not take yet says "not established" and names what is missing. It never reads as a pass. This affects pattern matches, list-of-values checks and month or week gaps.
- **Known difference:** RE proposes the "Valid characters" check for every text column, though TestGen marks that type inactive. This is an open question.
- **Not covered:** the sampling rule (the databases were too small), the baseline-match and trend checks, and hash values.

Each result is checked for pass, warning or fail. The measured values behind them were not compared one by one.

---

## Before you start

You need:

- **A registered PostgreSQL database** with a stored credential that can read it. Quality checks work for PostgreSQL only.
- **A stored schema inventory**, from any earlier survey of the database.
- **To be signed in**, to accept or reject a check, to watch a table, or to publish. A decision needs a person to record it.
- **The database's schema committed to Egeria** (the commit on **Contents**), if you want results and rules linked to its tables and columns. RE never creates those itself.

---

## 1. Run the quality survey

Everything starts from one Survey Definition, the **DatabaseQualitySurvey** ("Database Quality Survey"). It has three steps, run in order:

| step | what it does |
|---|---|
| **Postgres Value Profile** | measures every column across the whole table: counts, distinct values, nulls, empty strings, dates, text length, numbers. These are measured counts, not estimates |
| **Propose Quality Checks** | reads the profile it just took and proposes the checks that fit. It reads nothing from your database |
| **Run Quality Checks** | runs the checks a person has **accepted** against your database |

To run it:

- **In the screens:** open the database, go to **Survey & analyses**, and press Survey on **DatabaseQualitySurvey**. The same steps also appear as two analyses, **Value Profile** and **Quality Checks**.
- **From the command line:**
  ```bash
  uv run resource-explorer database survey-definition <slug> --survey-definition DatabaseQualitySurvey
  ```
- **From Egeria:** with RE's engine host turned on and the definition committed in Egeria, Egeria can start the same survey.

The first run profiles the database and proposes checks. Its last step then says "no accepted checks: accept proposals in Curate first; nothing was run". That is expected. No check runs until a person accepts it.

---

## 2. Look at the profile

Every database has a **Value profile** tab on its stage tabs, next to Schema Inventory and Table preview. It shows what the profile measured for each column, table by table. The Schema Inventory also links each table to its profile. A column that was not measured says why. It never shows a zero in place of "not measured".

How the profile is taken:

- **One query per table**, in a read-only transaction. It reads the whole table, with no sampling and no row-count limit.
- **Each table gets 5 minutes.** A table that takes longer is shown as "profile incomplete · exceeded 5 min · counts not measured". It gets no proposals. The limit is `VALUE_PROFILE_TABLE_BUDGET_SECONDS`.
- **Times are UTC.** Profile reads and check runs both use UTC.
- **A missing measure is never a zero.**
  - A view, materialized view or foreign table is *not applicable*.
  - A table RE may not read is *not permitted*.
  - A table that ran out of time is *not measured*.
- **The profile is sent to Egeria** with the survey report, one profile annotation per column. A measure that has not changed is linked to the new report, not written again.
- **The settings are read-only** under **Admin → Value profile settings**. Each shows its value, whether it came from the environment, and what it means.

### Optional settings

- **Value hashes** (`VALUE_PROFILE_HASHES`, off by default) take an exact fingerprint of each column's values. They are needed for the hash-comparison checks. They cost more time, so they are off until you turn them on. With them off, those checks say "not established" and tell you why.
- **Frequent values and text shape** are measured by sampling the text columns, for the checks that need them. The check's result says when it rests on a sample.

---

## 3. Review the proposed checks: Accept | Reject

Open the database's **Curate** page and the **Structure** tab. The **Quality checks** section lists what was proposed.

- **The top line** gives counts (proposed, accepted, rejected, retired, not established) and says when the proposals were made and from which profile.
- **Three filters** narrow the list: test type, dimension and state.
- **Each table is a group you can open.** The columns are Decision, Column ("whole table" for a table check), Test type, Dimension, Parameters and Profile of.

**The Decision cell is a two-part selector, Accept | Reject, one check at a time.** There is no "accept all", by design.

- **"accepted · <you> · <date>" appears only after RE has read your decision back.** While it saves, the cell says "recording…".
- **You can change your mind.** Decisions are kept as a history. The latest one wins.
- **Accept stores the check's settings as they are now.** They become the check's baseline, so a later profile does not quietly move the goalposts.
- **Accept is switched off when the profile could not evaluate the check.** The row says why. Reject still works.
- **A rejected check is not proposed again.**
- **A decision stays in RE** until you publish (see section 5).

**Not built yet:**

- changing a check's settings before you accept it;
- a way to **retire** an accepted check, though "retired" is a state RE can show;
- trend monitors for volume and freshness.

### Watch a table for drift

The **baseline-match** checks fail when a table moves away from the profile you accepted. They compare the row count and, for each column, the distinct, null and empty-string counts, the dates, and (from the 1b measures) lengths, sums and similar numbers.

**They are off for every table until you turn them on** (owner decision, 2026-10-26). Each table group has a **Watch for drift** button. It becomes **Stop watching** once the table is watched. From the command line:

```bash
uv run resource-explorer database quality-watch <slug> <schema.table> [--off] [--note "why"]
```

- **The checks are proposed on the next run.** Run the survey again, then accept them as usual.
- **Stopping only stops new proposals.** A match you already accepted keeps running.
- **An emptied table fails these checks.** Here a value that cannot be measured counts as a failure, because an emptied table has moved from its baseline.
- **The choice is kept in RE only.** Nothing goes to Egeria.

### Recency

**Recency** proposes for a date column that looks like it is kept up to date. All four must be true:

- its latest value is not in the future;
- its latest value is within about two months of the profile;
- its name is not a file date;
- TestGen's own guess at the column's role is neither "Future Date" nor "Schedule Date".

If the profile lacks the count it needs, Recency says "not established" and names the missing input.

### Duplicate rows and schema drift

- **Duplicate rows** looks for rows that appear twice. It has its own 5-minute limit per table. Columns that cannot be compared, such as XML, are left out and named.
- **Schema drift** compares the last two surveys: columns added, removed or changed. It runs nothing against your database.
- **Schema conformance** is vendored but never proposes yet, because RE has no declared schema to compare with.

### Bringing in checks from a TestGen installation

If your organization already runs TestGen, you can bring its test definitions in as **proposals**, never as decisions. The import works from a file and is available only on the command line:

```bash
uv run resource-explorer database quality-import-testgen <slug> --export testgen-export.json --dry-run
uv run resource-explorer database quality-import-testgen <slug> --export testgen-export.json
```

- **The file** comes from `scripts/export_testgen.py` in the `egeria-workspaces-fs` repository. RE matches the file's project to your database by its slug.
- **What comes in:** only active tests of a type RE has ported.
- **What is skipped, and counted:** types RE has not ported, inactive tests, checks RE already proposes, and checks you have already decided.
- **What you see:** imported rows have the origin `testgen`. They appear in the Quality checks section, where you accept or reject them as usual.
- **Results are not imported.** RE runs the checks you accept itself.

TestGen's own data (its results and profiles) is not read live. Reading an existing TestGen installation directly is a **deferred idea**: see the next section.

#### Not planned yet: using an existing TestGen as a data source

An existing TestGen holds profiles and results RE could reuse instead of measuring again. The design is written (`DESIGN-TESTGEN-DEPLOYMENT-AS-DATA-CHANNEL-2026-10-30.md`). **The project owner deferred it to the backlog on 2026-10-30.** Nothing in this guide depends on it, and RE does not read a TestGen database.

---

## 4. Run the accepted checks, and read the results

**Run the DatabaseQualitySurvey again.** Its last step runs every accepted check.

- **Each check runs against its accepted baseline**, not the latest profile.
- **Each statement has 60 seconds** (`VALUE_PROFILE_STATEMENT_TIMEOUT_SECONDS`).
- **Each result has a state:**
  - *pass*, *warning*, *fail* or *logged*, when the check reached a conclusion;
  - *not established*, when the measure is undefined (for example on an empty table). RE does not copy TestGen's "an undefined measure passes";
  - *error*, when the query failed or timed out;
  - *not permitted*, *not measured* or *not applicable*, when it could not run;
  - "column or table gone from the inventory", when the target has disappeared.

**In Egeria, each check the run evaluated becomes one `QualityAnnotation`** in the survey report, whatever the result:

- `annotationType` is "Database Quality Check".
- `qualityDimension` is the dimension word (see section 7).
- `qualityScore` is 100 for a pass and 0 for a fail. A check that could not conclude has no score, so it never looks like a failure.
- `additionalProperties` carries the check key, state and reason, test type, column, severity, measure, threshold and baseline date.
- The annotation is linked to its column or table in Egeria when that element exists there. Elements that do not exist are counted, not treated as errors.
- If the check has a published rule (section 5), the annotation also names the rule and is linked to it. Otherwise `ruleSource` is `unpublished`.

---

## 5. Publish accepted checks as Egeria data quality rules

Accepting a check is local. **Publishing** writes it to Egeria as a **`DataQualityRule`**.

### The Publish control

On **Curate → Structure**, the Quality checks section has a **Publish** control for the database's rules.

- **Before you press it,** it says what will happen: how many accepted checks are waiting, and which are already in Egeria.
- **One press publishes every waiting accepted check.** The press runs as you, once, and is switched off while it runs.
- **After the press,** each check's row says what happened:
  - *created* or *unchanged*, when the rule is in Egeria;
  - "not in Egeria · commit its schema in Contents", with a link to Contents, when the table or column is not in Egeria yet;
  - *refused*, with Egeria's own sentence.
- **It is switched off, with a sentence saying why,** when nobody is signed in, nothing is accepted, the database is not in Egeria, the waiting list could not be read, or a press is running. A read that fails is said, never shown as "nothing to publish".

From the command line or scripts, the same press is `POST /api/quality-checks/{slug}/publish-rules`. `GET /api/quality-checks/{slug}/rules` reports each check's state in Egeria.

### The rules for rules

These are the project owner's decisions of 2026-10-28.

- **One accepted check becomes one rule.** It is named `<database qualified name>::Quality::<check key>`.
- **Each rule stands on its own.** It is a reusable governance rule and is not anchored to the database. Rules tied to a data contract are a later piece (`DESIGN-QUALITY-Q5-ANCHORING-AND-DATA-CONTRACTS.md`).
- **A rule is linked from the column** (or the table, for a table check) **and from the database asset.**
  - A check whose table or column is not in Egeria gets no rule. It never falls back to the database.
- **No zones.** RE adds no zone to a rule.
- **Rejecting after publishing retires the rule.** It is set to DEPRECATED and unlinked. It is never deleted. Changing a check's threshold or severity updates the same rule.
- **Egeria wins.** If someone edits a rule in Egeria, RE does not overwrite it. The next run uses Egeria's values and says so.
- **Each write is read back.** A mismatch is reported as "unverified" and left unlinked.
- **Older results are not back-linked.** Annotations from before the rule existed stay as they were.

---

## 6. Check many databases, or check on a schedule

On a database **work list**, the button **quality checks across this list…** opens **Quality checks across this list**. It shows, per database, a price before anything runs:

- **Size** says "size unknown · never analysed" when RE does not know it, never 0.
- **Time** comes from earlier runs, or says "not measured yet".
- Nothing runs until you confirm.

**Run on N** queues one run per database, through the same queue and credential checks as a single press. It reports each outcome: queued, skipped, not permitted, cannot run, or has its own schedule.

**Schedule** puts the list on a schedule.

- **A scheduled run skips a database whose inventory has not changed since its last quality run, and says so.** A press by a person never skips.
- A member with its own schedule keeps it.
- The run is made as the person who set the schedule, and publish rights are checked for each member each time.

**A failed scheduled run does not yet reach a person.** Some of the rules here are still marked "assumed, owner to confirm" in the design note (`DESIGN-QUALITY-Q3-BATCH-AND-SCHEDULE.md`).

---

## 7. Dimension words: what Egeria is told

Each check carries a quality **dimension**. Egeria accepts these words: accuracy, completeness, conformity, consistency, coverage, timeliness and uniqueness.

| checks | TestGen dimension | Egeria word |
|---|---|---|
| Missing values, Required, Row count change | Completeness | `completeness` |
| Unique | Uniqueness | `uniqueness` |
| Future date, Recency | Timeliness | `timeliness` |
| the baseline-match checks | Consistency | `consistency` |
| Min_Date and other Validity checks | **Validity** | **`accuracy`**, with `originalDimension: Validity` |

**Validity has no exact Egeria word.** The project owner ruled on 2026-10-20 that "conformity" is not the same thing and "accuracy" is closer. So Validity is published as `accuracy`, and `additionalProperties.originalDimension` keeps the word "Validity" until Egeria has a value for it. This applies to rules and to annotations. Curate shows `accuracy` in the Dimension column and filter.

---

## Quick path

1. Survey the database once, so it has a schema inventory. Commit its schema to Egeria if you want linked results.
2. Run **DatabaseQualitySurvey** from Survey & analyses.
3. Look at the **Value profile** tab if you want to see the numbers.
4. Open **Curate → Structure → Quality checks**. Accept or reject each proposal. Press **Watch for drift** on tables whose shape should hold still.
5. If you already run TestGen, import its checks with `quality-import-testgen` and review them too.
6. Run **DatabaseQualitySurvey** again. Your accepted checks run. The results reach Egeria as QualityAnnotations.
7. Press **Publish** to write the accepted checks to Egeria as data quality rules.
8. Optional: put a work list on a quality schedule.

## Where this comes from

The design notes, indexed in [design-notes/README.md](design-notes/README.md):

- `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` (the first slice and decisions D1–D6)
- `DESIGN-VALUE-PROFILE-SCREEN-2026-10-30.md` (the Value profile tab)
- `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md`
- `DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md` (the port order and how it is checked)
- `DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md` (publishing rules)
- `DESIGN-QUALITY-Q3-BATCH-AND-SCHEDULE.md`
- `INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md` (all 89 types)
- `INTEGRATION-TESTGEN-RE-EGERIA.md` (diagrams, dated 2026-10-20)
- `DESIGN-TESTGEN-DEPLOYMENT-AS-DATA-CHANNEL-2026-10-30.md` (deferred)

The copied catalog's provenance is `resource_explorer/configdata/quality/testgen/PROVENANCE.md`.
