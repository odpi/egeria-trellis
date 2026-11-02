# Part 4 — The stages: a funnel from cheap to costly

The next weekly drop sets Peter's deadline, and three data sources need attention before then. So the time has to go
where it counts. This part explains how Resource Explorer helps you spend it: cheap questions first, costly ones only
where they are worth it.

**Before you start.** [Part 3](03-add-data-sources.md): you are signed in as Peter, and the trial's three sources
are registered. This part takes about fifteen minutes. You only read in this part; it changes nothing.

*Screens read on 2026-11-02.*

## The funnel

Surveying a data source costs time, and it puts load on the source. A quick count of a database's tables takes
seconds. A profile of every column's values can take minutes, and it reads the rows. So Resource Explorer arranges its
questions in six [stages](glossary.md#stage), from least costly to most:

![The time before the next drop: a diagram titled "Cheap questions first, costly ones only where they are worth it". Six stages form a funnel, narrowing from 1. Scouting at the top to 6. Curate at the bottom. Beside each is what it asks: a fast first look; worked out from what Scouting found; judged against criteria; structure and numbers in detail; what only a person knows; your decision. Egeria, the shared catalog, sits to the left; every stage can read from it, and Curate publishes to it. To the right, chat at any stage, and Understanding and Automate around the funnel.](images/04-stages/S12-funnel.webp)

*The funnel. Most data sources stop early; only a few go all the way.*

1. **Scouting:** a fast first look at many data sources. What is it, how big is it, is it alive?
2. **Discovery:** what the first look already tells you. It is worked out from what Scouting collected, mostly
   without reading the source again. Is it worth a closer look? Is it already in the catalog?
3. **Assessment:** a closer look that judges the source against criteria, such as security, documentation and
   quality. Slower.
4. **Analysis:** its structure and numbers in detail, such as dependencies and column profiles. This can take
   minutes.
5. **Enrichment:** what only a person knows: the owner, how sensitive it is, what it is meant for.
6. **Curate:** choose what to publish to Egeria, then publish.

You go down [the funnel](glossary.md#the-funnel) only as far as you need. Many data sources stop at Scouting or
Discovery. Only the ones worth it get Assessment and Analysis.

**Why this saves so much time.** Checks differ enormously in what they cost. Later checks build on what earlier ones
stored, so they do not repeat the work. And the saving multiplies, because the costly stages run only on the data
sources that survive the cheap ones.

**Setting a source aside is not throwing it away.** A data source you stop looking at today can be looked at again
later.

**A guide, not a rule.** The funnel is the efficient order, not the only one. When a later stage's question is the
one you need answered, run that stage's survey directly.

**Around the funnel, not in it.** Three more items in the strip are not stages. **Investigation** records why you are
looking (part 12). **Understanding** shows what has been measured so far, over time, and **Automate** runs checks
again on a schedule. Those two are for the Advanced tutorial.

For Peter, the funnel means a cheap look at all three sources first. `coco_pharma` holds the figures, so it is the one
most likely to need the deeper stages.

## Inside a stage

Every stage has the same tabs:

- **Questions** lists what this stage asks about this data source, and the answers so far.
- **Survey & analyses** lists the [surveys](glossary.md#survey) that answer the questions. A survey is a set of
  [analyses](glossary.md#analysis) run together. You can also run each analysis on its own.
- **By analysis** shows the stage's results one analysis at a time, under "SURVEY RESULTS, BY ANALYSIS".
- **Disposition** records your verdict on the data source, such as "tracking", "using" or "abandoned". It keeps the
  history of your verdicts, and a journal of why the source matters.

A database has three more tabs after these: "Schema Inventory", "Value profile" and "Table preview". They show what
is inside it.

Each of the first four stages has an [overview survey](glossary.md#overview-survey). It runs all of the stage's
analyses for that kind of data source. The overview is the place to start, because it shows what the stage can tell
you. Narrower surveys run part of it, when you want only some of the answers, or want to keep the cost down.

The screen does not mark which survey is the overview. Use this table:

| Stage | Repository | Database | File system |
|---|---|---|---|
| Scouting | "Scouting Survey" (9 steps) | "Database Scouting Scan" (3 steps) | The "Folder survey" card: its **Survey** button |
| Discovery | "Repo Discovery Survey" (9 steps) | none yet (gap G23) | The same "Folder survey" card |
| Assessment | "Assessment Survey" (14 steps) | none shown on this stage yet (gap G23) | The same "Folder survey" card |
| Analysis | "Analysis Survey" (11 steps) | "Database Analysis Survey" (8 steps) | The same "Folder survey" card |

A file system has one "Folder survey" card, the same on every stage. Hover over any of its buttons to see what it
reads, what it stores and what it publishes. Part 5 uses it.

## Steps

1. **Open a database that has already been surveyed, then click 1 Scouting.**

   Click **DBs** in the sidebar, then a database that the Getting Started tutorial surveyed. The screenshots show the
   Quickstart's `coco_pharma` database, which the screen calls "coco_pharma @ local-docker". The `coco_pharma` you
   registered in part 3 (`coco_pharma_trial` in the screenshots) has nothing to show yet; part 5 surveys it.

   ![A stage on coco_pharma: the database's Scouting stage, with four numbered areas. 1, the tabs: Questions, Survey & analyses, By analysis, Disposition, Schema Inventory, Value profile, Table preview. 2, the count line "5 of 9 answered · Scouting". 3, the key: answered 4, nothing found 1, partial 1, not run 1, needs you 1, no reader yet 1. 4, one question row: "What is this resource, and what is it for?" with the mark for nothing found, the perspective chips Consumer, Data Expert and Architecture, the answer "No description recorded for this database yet." and "Nothing is recorded for this — a real absence, not an unrun analysis."](images/04-stages/S13-a-stage-labelled.webp)

   *The Scouting stage of the coco_pharma database. The numbers match the next two steps.*

   You see the stage's tabs (1), with **Questions** open.

2. **Click Survey & analyses, and find the overview survey and a narrower one.**

   Under "SURVEY DEFINITIONS", the screen lists the surveys you can run for this stage. "Scope: scouting" means the
   surveys shown are the ones for this stage. On the database, Scouting has one: "Database Scouting Scan".

   A repository shows the difference between an overview and a narrower survey more clearly. Click **Repos**, then
   `openlineage`, and its **Survey & analyses** tab. Under "SCOUTING · 3" are three surveys.

   ![The overview survey and a narrower one, on the OpenLineage repository's Scouting stage. Under "SURVEY DEFINITIONS · GIT REPOSITORY" and "Scope: scouting", the heading "SCOUTING · 3" lists Coarse Profile Survey (7 steps), Repo Scouting Scan (4 steps), labelled "narrower", and Scouting Survey (9 steps), labelled "overview". Its description begins "Everything Scouting does, in one run". Each survey has a "Re-run…" button.](images/04-stages/S14-survey-and-analyses-labelled.webp)

   *OpenLineage's Scouting surveys. "Scouting Survey" is the overview; "Repo Scouting Scan" is a narrower one.*

   - "Scouting Survey", 9 steps, is the overview. Its description begins "Everything Scouting does, in one run".
   - "Repo Scouting Scan", 4 steps, is narrower: "Fast, GitHub-API-only pass — is this repo active enough to look
     closer at?"
   - "Coarse Profile Survey", 7 steps, is another narrow one. It downloads the repository once and profiles what is
     in it.

   Each survey shows its steps and how many of them fetch from the source. Fewer fetches cost less. Do not press
   **Run…** or **Re-run…** yet: part 5 runs a survey.

3. **Go back to the database, click Questions, and read the count line and the key.**

   The count line (2) reads "5 of 9 answered · Scouting". Under it, the key (3) counts the answers by their mark:
   "answered 4", "nothing found 1", "partial 1", "not run 1", "needs you 1", "no reader yet 1". Your counts may
   differ.

   Each question row (4) shows a mark, the question, the [perspectives](glossary.md#perspective) that care about it,
   and the [answer](glossary.md#answer). Under the answer, a source line says what it came from, with **evidence**.

   Every answer carries one of these marks:

   ![How to read an answer: a table of nine marks. ✓ answered: it was measured and checked; "answered automatically" is answered from what is already recorded. ∅ nothing found: it was measured, and there was nothing. ◐ partial. ○ not run: nobody has looked yet. ◌ no reader yet. ? could not tell. ⚠ needs you. ◔ running. ✕ failed. The footer reads: "∅ nothing found and ○ not run are never the same: one is an answer, the other means nobody has looked."](images/04-stages/S15-marks.webp)

   | Mark | Word | Means |
   |---|---|---|
   | ✓ | answered | It was measured and checked. "answered automatically" means it was read from what is already recorded, with no new check. |
   | ∅ | nothing found | It was measured, and there was nothing. This is an answer. |
   | ◐ | partial | Measured, but only part of the picture. |
   | ○ | not run | Nobody has looked yet. |
   | ◌ | no reader yet | There is no way to answer this automatically yet. |
   | ? | could not tell | Resource Explorer could not read it. Not the same as "no". |
   | ⚠ | needs you | A person has to act or answer. |
   | ◔ | running | Under way, or waiting its turn. |
   | ✕ | failed | It failed. The word beside it says why. |

   ∅ and ○ are never the same. In the screenshot, "What is this resource, and what is it for?" has ∅: it was
   checked, and no description is recorded. The screen says so: "Nothing is recorded for this — a real absence, not
   an unrun analysis." The next question has ○: nobody has run what would answer it yet.

## What can go wrong

- **A slow pane.** Some stages show "still loading" in the count line for many seconds, without saying what they are
  waiting for (gap G10). Wait until "still loading" goes before you read the counts.
- **Your new data source shows nothing.** A data source you registered in part 3 has no answers until it is
  surveyed. Most of its questions show ○. That is expected; part 5 runs its first survey.

**Next:** [Part 5 — Scouting: run the overview survey and read the answers](05-scouting.md). Peter asks
whether `coco_pharma` is alive, and what is in it.
