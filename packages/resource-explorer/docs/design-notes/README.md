# Design notes: an index by status

**Status:** index, hand-maintained. Last checked against `origin/main` at `aacdfaff` on 2026-10-30.

This folder holds Resource Explorer's design record: proposals, the notes written as work was built,
the evidence behind decisions, and the exchange with the designer. This index groups the notes in
the root of the folder by **where the work stands on main**, not by what each note's own status line
says. Several of those lines were written before the work merged. Where a note's status line was
contradicted by main on 2026-10-28, it gained a dated "Status refreshed" line, and the original line
was kept.

For the user-facing guides, see [`../README.md`](../README.md).

## How the folder is laid out

| where | what | count (2026-10-30) |
|---|---|---:|
| [`implemented/`](implemented/) | finished-work records (`*-IMPLEMENTED.md`), plus shipped notes that an `*-IMPLEMENTED` note cites | 174 |
| [`evidence/`](evidence/) | audits, probes, measurements and their data files | 62 |
| [`exchange/`](exchange/) | answered asks, replies, reviews, specs and briefs, moved out of the root 2026-10-15 | 59 |
| [`rulings/`](rulings/) | rulings that later notes build on | 3 |
| [`wireframes/`](wireframes/), [`screenshots/`](screenshots/) | the designer's exports | |
| this folder's root | open design notes, asks and replies still in play, and the records below | |

The conventions come from `DESIGN-NOTES-MOVE-IMPLEMENTED.md`:

- **Cite a note by its bare name** (`see SOME-NOTE-IMPLEMENTED.md`), never by its path, so a later
  move between subfolders cannot break the citation. `tests/test_design_note_references_resolve.py`
  fails on any citation from code, tests, scripts or CI that does not resolve.
- **A note moves to `implemented/`** when it is an `*-IMPLEMENTED.md` record, or when it is a shipped
  note with a citing `*-IMPLEMENTED` twin. A design note whose work merged without such a twin stays
  in the root, and is listed under "Built" below.
- **Asks, replies, reviews and rulings stay in the root** while they are in play. Answered ones go to
  `exchange/`.

## Built: the work is on main

Each of these notes stays in the root because it has no `*-IMPLEMENTED` twin.

Quality and TestGen (the user guide is [`../testgen-integration-guide.md`](../testgen-integration-guide.md)):

- [DESIGN-QUALITY-B1A-BASELINE-MATCH.md](DESIGN-QUALITY-B1A-BASELINE-MATCH.md): baseline match on the measures RE profiles, opt-in per table, and the TestGen-only import (PR #887)
- [DESIGN-QUALITY-Q6-RECENCY.md](DESIGN-QUALITY-Q6-RECENCY.md): Recency, the date branch of the functional-type inference, and Future_Date_1Y (PR #880)
- [DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md](DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md): accepted checks published as Egeria `DataQualityRule`s (PR #892), with the Publish control on Curate (PR #938)
- [DESIGN-QUALITY-Q3-BATCH-AND-SCHEDULE.md](DESIGN-QUALITY-Q3-BATCH-AND-SCHEDULE.md): the quality survey across a work list, priced first, and on a schedule (PR #894)
- [DESIGN-QUALITY-PROFILE-1B.md](DESIGN-QUALITY-PROFILE-1B.md): profile 1b, the numeric, length, boolean and date-day measures, and the baseline checks that use them (PR #893)
- [DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md](DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md): duplicate rows and the schema-drift checks (PR #910)
- [DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md](DESIGN-QUALITY-STEP-6-FUNCTIONAL-TYPE-INFERENCE.md): the rest of the column-type inference and the 19 checks that read it (PR #918)
- [DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md](DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md): sampled text shape and the checks that read it
- [DESIGN-QUALITY-STEP-7B-TEXT-INFERENCE.md](DESIGN-QUALITY-STEP-7B-TEXT-INFERENCE.md): the text branches of the column-type inference
- [DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md](DESIGN-QUALITY-STEP-8-HASHES-AND-FREQUENT-VALUES.md): value hashes (off by default), frequent values, and the three query checks (PR #923)
- [DESIGN-VALUE-PROFILE-SCREEN-2026-10-30.md](DESIGN-VALUE-PROFILE-SCREEN-2026-10-30.md): the Value profile tab (PRs #930 and #931)
- [DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md](DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md): the first slice and decisions D1 to D6, with the 2026-10-30 addendum
- [DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md](DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md): the quality slice plan. Q2 to Q6 and B1a are merged
- [DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md](DESIGN-QUALITY-Q4-PORT-PLAN-AND-CALIBRATION.md): the port plan on the TestGen inventory. 68 of 89 types are ported; the monitors are not

Declared extensibility (the reference is [`../extending-with-analyses.md`](../extending-with-analyses.md); the how-to is the [tutorial](../../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md)):

- [DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md](DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md): one declaration, three consumers. The parent design
- [DECLARED-EXTENSIBILITY-SLICE1-PR1-GAPS.md](DECLARED-EXTENSIBILITY-SLICE1-PR1-GAPS.md): slice 1, the declaration (PR #885)
- [DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md](DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md): slice 1, the Curate publish table (PR #886)
- [DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md](DECLARED-EXTENSIBILITY-SLICE2-CHAT-COMPILE.md): slice 2, Chat reads the declaration (PR #899)
- [DESIGN-STEP-MANIFESTS-MERGED-AT-READ-TIME.md](DESIGN-STEP-MANIFESTS-MERGED-AT-READ-TIME.md): installed step manifests merge into the analysis catalog when it is read

Files and work lists:

- [BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC.md](BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC.md), with the build notes [FILES-STEP-6-DERIVED-STEPS-BUILD-NOTE.md](FILES-STEP-6-DERIVED-STEPS-BUILD-NOTE.md) and [FILES-STEPS-7-8-BUILD-NOTE.md](FILES-STEPS-7-8-BUILD-NOTE.md): the Files analyses and RE's own file-system survey report (PRs #905 and after)
- [DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS.md](DESIGN-WORK-LISTS-WINNOWING-AND-MIXED-KINDS.md): work lists as a winnowing bench. The first slices are built; the set-steps section arrived in PR #944

Engine host, Curate and the /next UI:

- [DESIGN-PUBLISHING-COLUMN-VERDICTS.md](DESIGN-PUBLISHING-COLUMN-VERDICTS.md): Curate's Publish sends the stored per-column verdicts
- [DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md](DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md): engine host on the Governance Engine view service (the designer's words, Q4, are still open)
- [DESIGN-COORDINATED-RUN-CREDENTIAL.md](DESIGN-COORDINATED-RUN-CREDENTIAL.md): one-run credential for Egeria-coordinated runs (the designer's words, Q3, are still placeholders)
- [SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md](SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md): Egeria runs RE's own survey definitions (PR #708)
- [SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md](SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md): one mixed Egeria process, a native step chained to an RE step (PR #699)
- [EXECUTION-MODES-HYBRID-CLARIFICATION.md](EXECUTION-MODES-HYBRID-CLARIFICATION.md): "hybrid" folded into `executes_at: egeria-adaptive`
- [PLAN-PREFECT-OR-ALTERNATIVE.md](PLAN-PREFECT-OR-ALTERNATIVE.md): the orchestration decision. The per-run engine choice is recorded in `ENGINE-CHOICE-IMPLEMENTED.md`
- [DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md](DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md): the interface ladder's missing rung. Its status line says "design only", and code cites it. It stays in the root by an owner decision recorded in `DESIGN-NOTES-MOVE-IMPLEMENTED.md`

## In progress: built in part, or waiting on a decision

- [DESIGN-QUALITY-Q5-ANCHORING-AND-DATA-CONTRACTS.md](DESIGN-QUALITY-Q5-ANCHORING-AND-DATA-CONTRACTS.md): where a published rule is anchored, and reading and testing against Egeria's data contracts. The owner accepted its recommendations. The Egeria-side build is not done
- [DESIGN-QUALITY-B1D-MONITORS.md](DESIGN-QUALITY-B1D-MONITORS.md): trend monitors (volume and freshness) as simple stated rules. Design accepted, not built
- [INTEGRATION-TESTGEN-RE-EGERIA.md](INTEGRATION-TESTGEN-RE-EGERIA.md) and [INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md](INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md): reference for the quality notes above. The inventory still says "Validity maps to Conformity"; the owner's later ruling is Accuracy (see the Q5 note)
- [SPEC-PYEGERIA-DATA-QUALITY-RULE.md](SPEC-PYEGERIA-DATA-QUALITY-RULE.md): the pyegeria convenience API that Q5 would use. Not implemented
- [EGERIA-ANNOTATION-TYPES-WANTED.md](EGERIA-ANNOTATION-TYPES-WANTED.md): annotation types library steps emit that the engine host cannot publish, and what we ask of Egeria
- [DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md](DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md): the 2026-10-02 brainstorm that started it. Several of its rules are answered by the lifecycle note below
- [SLICE-ANALYTIC-LIBRARY-STEPS-NEXT-FIVE.md](SLICE-ANALYTIC-LIBRARY-STEPS-NEXT-FIVE.md): the next Analytic Library steps Egeria can dispatch
- [DESIGN-OPEN-DATA-SEARCH-AND-SURVEY.md](DESIGN-OPEN-DATA-SEARCH-AND-SURVEY.md): the open-data search and survey process. OD1 is built (PR #911); the later parts are partly built
- [DESIGN-FILES-REMOTE-REACH.md](DESIGN-FILES-REMOTE-REACH.md): file systems RE reaches that Egeria cannot
- [DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md](DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md): RE as an engine host. Steps 3 and 4 and case 3 are built
- [DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md](DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md): the Artifacts view. Slice 1's columns are merged (PR #700)
- [DESIGN-CURATE-BY-ANALYSIS.md](DESIGN-CURATE-BY-ANALYSIS.md): Curate's "By analysis". Curation coverage is built (PR #628)
- [DESIGN-CURATE-NESTING-SELECTION-AND-DEPENDENCY-KINDS.md](DESIGN-CURATE-NESTING-SELECTION-AND-DEPENDENCY-KINDS.md): Curate for repositories. Explicit selection is built (PR #550)
- [DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md](DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md): the owner's hand-built blueprints as the bar. Parts 0, 6 and 6a are built (`BLUEPRINT-CONTAINER-SHAPE-IMPLEMENTED.md`)
- [DESIGN-ENRICHMENT-AND-CURATE-PUBLISHING-TO-EGERIA.md](DESIGN-ENRICHMENT-AND-CURATE-PUBLISHING-TO-EGERIA.md): publishing Enrichment and Curate to Egeria
- [DESIGN-ARCHIVE-RESTORE-WRAPPERS-ISSUE-159.md](DESIGN-ARCHIVE-RESTORE-WRAPPERS-ISSUE-159.md): archive and restore (the ISSUE-159 position)
- [PLAN-FINISH-REPOS.md](PLAN-FINISH-REPOS.md): finish `/next` for repositories. Most items have `*-IMPLEMENTED` records
- [PLAN-CARDS-AND-TILES.md](PLAN-CARDS-AND-TILES.md): cards and tiles across /next. C1 is built (PR #782)
- [PLAN-ANALYSIS-GAPS-REPOS-AND-DATABASES.md](PLAN-ANALYSIS-GAPS-REPOS-AND-DATABASES.md): closing the repository and database analysis gaps

## Added since 2026-10-28: designs, reviews and evidence

These are designs, reviews and evidence from 2026-10-29 and 2026-10-30. Most are not built yet:

- [DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md](DESIGN-ANALYTIC-LIFECYCLE-2026-10-30.md): where steps and routines live and how they are installed, versioned and retired. Design only; the decisions are in its section 7
- [REVIEW-EXTENSIBILITY-VS-WORKSPACES-603-2026-10-30.md](REVIEW-EXTENSIBILITY-VS-WORKSPACES-603-2026-10-30.md): the review of the egeria-workspaces extensibility proposal (#603) against RE's own designs
- [EVAL-PORTAL-EXTENSIBILITY-VS-RE-2026-10-30.md](EVAL-PORTAL-EXTENSIBILITY-VS-RE-2026-10-30.md): the Portal's extension model against RE's, and how to keep them in step
- [DESIGN-LOCAL-DASHBOARDS-DECLARED-ANALYTICS.md](DESIGN-LOCAL-DASHBOARDS-DECLARED-ANALYTICS.md): local dashboards from declared analytics and report specs
- [DESIGN-TESTGEN-DEPLOYMENT-AS-DATA-CHANNEL-2026-10-30.md](DESIGN-TESTGEN-DEPLOYMENT-AS-DATA-CHANNEL-2026-10-30.md): reading an existing TestGen installation's profiles and results. **Deferred to the backlog by the project owner, 2026-10-30**, with its slices TG1 to TG3 (see `Backlog.md`)
- [AUDIT-USER-GUIDE-2026-10-28.md](AUDIT-USER-GUIDE-2026-10-28.md): the user guide read against the running Explorer. A record, not a plan
- [DESIGN-RESOURCE-COMPARISON-QUESTIONS-2026-10-30.md](DESIGN-RESOURCE-COMPARISON-QUESTIONS-2026-10-30.md): comparison questions for Chat: is database 1 the same as database 2, a subset or a superset
- [DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md](DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md): D1, adopting an existing database element in Egeria by its endpoint, and reading Egeria's duplicate links. Built in two slices: D1-a, the plan-only resolver (#954), and D1-b, the database-level "Link the pack's N schemas" press (#960; pressed 2026-10-31, 22 added, 26 members; undo in [D1B-DATABASE-LINK-PRESS-UNDO.md](D1B-DATABASE-LINK-PRESS-UNDO.md))
- [DESIGN-ANALYTIC-LIBRARY-SPLIT-2026-10-30.md](DESIGN-ANALYTIC-LIBRARY-SPLIT-2026-10-30.md): what would move if the Analytic Library were split out of trellis, and how versions and loading would work
- [SUMMARY-EXTENSIBILITY-AND-EGERIA-OBJECTS-2026-11-03.md](SUMMARY-EXTENSIBILITY-AND-EGERIA-OBJECTS-2026-11-03.md): one-place summary of analytic extensibility and local dashboards, with diagrams of the Egeria objects each fact maps to and the new ones we may need. A summary of the notes above; it decides nothing
- [REVIEW-FILES-STATE-2026-10-30.md](REVIEW-FILES-STATE-2026-10-30.md): a read-only review of Files in the running Explorer
- LS5 spike, in [`evidence/`](evidence/): [two RE analyses as Analytic Library steps, 2026-10-31](evidence/SPIKE-RESULT-RE-ANALYSES-AS-LIBRARY-STEPS-2026-10-31.md): timings and equivalence of the library twins against the native analyses. Closed by the project owner; recommendation "convert as touched"; twins not merged
- Calibration reports, in [`evidence/`](evidence/): [adventureworks, 2026-10-29](evidence/calibration-adventureworks-2026-10-29.md) and [coco_pharma, 2026-10-30](evidence/calibration-coco_pharma-2026-10-30.md): RE's quality results compared with TestGen's on the same data

## Proposals: designed, not built

- [DESIGN-AMUNDSEN-MIGRATION-ASSESSMENT.md](DESIGN-AMUNDSEN-MIGRATION-ASSESSMENT.md): Amundsen users and RE/Egeria
- [DESIGN-BROADER-QUESTIONS-WITHOUT-CONTENT-INGESTION.md](DESIGN-BROADER-QUESTIONS-WITHOUT-CONTENT-INGESTION.md): broader questions without ingesting content
- [DESIGN-EGERIA-SUGGESTIONS-AND-RE-OWN-CATALOGUING.md](DESIGN-EGERIA-SUGGESTIONS-AND-RE-OWN-CATALOGUING.md): suggestions to Egeria, and RE cataloguing on its own (decision deferred)
- [DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md](DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md): the Find and Integrate purposes, and the data lens
- [DESIGN-MCP-SERVER-AND-A2A-GAPS.md](DESIGN-MCP-SERVER-AND-A2A-GAPS.md): an MCP server over RE's engine
- [DESIGN-OPEN-DATA-RESOURCE-KIND-BRAINSTORM.md](DESIGN-OPEN-DATA-RESOURCE-KIND-BRAINSTORM.md): an open-data resource kind (brainstorm)
- [DESIGN-OPTIONAL-ANALYSES-TO-EGERIA-ANNOTATIONS.md](DESIGN-OPTIONAL-ANALYSES-TO-EGERIA-ANNOTATIONS.md): a configurable path from optional results to annotations (options)
- [DESIGN-RAG-CODE-INGESTION-OPTIONAL.md](DESIGN-RAG-CODE-INGESTION-OPTIONAL.md): optional RAG ingestion of code
- [DESIGN-REPO-TO-RUNTIME-COMPONENT-LINKS.md](DESIGN-REPO-TO-RUNTIME-COMPONENT-LINKS.md): linking built components to runtime components (discussion)
- [NOTE-ANNOTATION-ENVELOPE-ATTRIBUTES.md](NOTE-ANNOTATION-ENVELOPE-ATTRIBUTES.md): the annotation envelope RE needs from Egeria
- [REQUIREMENTS-RE-DATA-CHANNELS.md](REQUIREMENTS-RE-DATA-CHANNELS.md): how RE reaches data (draft requirements)
- [SPEC-REPORT-ACTS.md](SPEC-REPORT-ACTS.md): the three acts on a report
- [SPEC-RESOURCE-BROWSER-PORTAL-APP.md](SPEC-RESOURCE-BROWSER-PORTAL-APP.md): a shop-for-data app on Egeria alone

## Rulings, asks and replies in play

Rulings: [DESIGN-BLUEPRINT-NAMING-WHAT-IT-REPRESENTS.md](DESIGN-BLUEPRINT-NAMING-WHAT-IT-REPRESENTS.md),
[DESIGN-FILE-TYPES-AS-ANNOTATIONS.md](DESIGN-FILE-TYPES-AS-ANNOTATIONS.md),
[RULING-OWNER-FACT-VS-OWNERSHIP-CLASSIFICATION.md](RULING-OWNER-FACT-VS-OWNERSHIP-CLASSIFICATION.md),
[RULING-PUBLISH-NOT-CATALOG.md](RULING-PUBLISH-NOT-CATALOG.md),
[RULING-RUNS-REGISTRATION-ARCHITECT.md](RULING-RUNS-REGISTRATION-ARCHITECT.md),
[RULING-THEMES-PORTAL-DARK-LIGHT.md](RULING-THEMES-PORTAL-DARK-LIGHT.md). The two `DESIGN-` names are rulings
despite their prefix. Older rulings are in [`rulings/`](rulings/).

Asks to the designer, with the reply where one exists:

| ask | reply |
|---|---|
| [ASK-DESIGNER-CANCEL-REPORT-KEPT.md](ASK-DESIGNER-CANCEL-REPORT-KEPT.md) | not yet |
| [ASK-DESIGNER-CATALOGUING-AT-SCALE.md](ASK-DESIGNER-CATALOGUING-AT-SCALE.md) | [REPLY-DESIGNER-CATALOGUING-AT-SCALE.md](REPLY-DESIGNER-CATALOGUING-AT-SCALE.md) |
| [ASK-DESIGNER-CURATE-SPLIT-BY-JOB.md](ASK-DESIGNER-CURATE-SPLIT-BY-JOB.md) | [REPLY-DESIGNER-CURATE-SPLIT-BY-JOB.md](REPLY-DESIGNER-CURATE-SPLIT-BY-JOB.md) |
| [ASK-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md](ASK-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md) | [REPLY-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md](REPLY-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md) |
| [ASK-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20.md](ASK-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20.md) | [REPLY-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20.md](REPLY-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20.md) |
| [ASK-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md](ASK-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md) | [REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md](REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md) |
| [ASK-DESIGNER-RUNS-REGISTRATION.md](ASK-DESIGNER-RUNS-REGISTRATION.md) | [REPLY-DESIGNER-RUNS-REGISTRATION.md](REPLY-DESIGNER-RUNS-REGISTRATION.md) |
| [ASK-DESIGNER-STATE-CUES-ADDENDUM.md](ASK-DESIGNER-STATE-CUES-ADDENDUM.md) | [REPLY-DESIGNER-STATE-CUES-ADDENDUM.md](REPLY-DESIGNER-STATE-CUES-ADDENDUM.md) |
| [ASK-DESIGNER-TABLE-PREVIEW-2026-10-19.md](ASK-DESIGNER-TABLE-PREVIEW-2026-10-19.md) | [REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md](REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md) |
| [ASK-DESIGNER-WORDS-ROUND-2026-10-18.md](ASK-DESIGNER-WORDS-ROUND-2026-10-18.md) | not yet |

Other replies and reviews: [REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md](REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md),
[REPLY-DESIGNER-CURATE-SCOPE-ROUND-2.md](REPLY-DESIGNER-CURATE-SCOPE-ROUND-2.md) (their asks are in `exchange/`),
[REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md](REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md),
[REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md](REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md).

## Records and reference

- [TIMELINE.md](TIMELINE.md): one dated block per note as it lands
- [INBOX.md](INBOX.md): the designer's inbox: what design said, and whether it was built
- [DEFERRED-REGISTER.md](DEFERRED-REGISTER.md): what was deliberately deferred, and why
- [WORKING-RULES-STANDING-CLEARANCES.md](WORKING-RULES-STANDING-CLEARANCES.md): standing clearances, hard gates, and how sessions report
- [HANDOFF-DESIGN-SESSION-2026-10-16.md](HANDOFF-DESIGN-SESSION-2026-10-16.md): handoff from the design and gate session
- [PLAN-OPEN-FRONTS-2026-10-14.md](PLAN-OPEN-FRONTS-2026-10-14.md): the open fronts after the owner's backlog review
- [BACKLOG-TRIAGE-2026-09-30.md](BACKLOG-TRIAGE-2026-09-30.md): backlog triage
- [CLASSIC-VS-NEXT-PARITY-2026-09-30.md](CLASSIC-VS-NEXT-PARITY-2026-09-30.md) and [INVENTORY-CLASSIC-TO-NEXT.md](INVENTORY-CLASSIC-TO-NEXT.md): Classic versus `/next`
- [AUDIT-PYEGERIA-6.2.2.md](AUDIT-PYEGERIA-6.2.2.md), [AUDIT-PYEGERIA-6.2.3.md](AUDIT-PYEGERIA-6.2.3.md): trellis on each pyegeria release
