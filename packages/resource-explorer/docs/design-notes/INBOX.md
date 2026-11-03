# Designer inbox

**One writer: the designer session.** This file is written by hand. The
designer reads each `*-IMPLEMENTED.md` and the commits that cite a reply,
then updates the row. Nothing generates it. Implementers never edit it:
signal by writing your `*-IMPLEMENTED.md` (it now lives in `implemented/`)
and naming the designer note it answers, by its bare filename.

**Last reconciled:** 2026-10-01, against `main` at `524f1c7f`, after the
design-notes move. Evidence for every row is an implemented note or a commit
that names the designer note. A row whose only evidence is a commit that
doesn't name the note says **unverified**. `TIMELINE.md` (the coordinator's)
was the cross-check for dates.

**The rule stays: unanswered until proven shipped.** A row with no citation
is *not built*, never *probably done*.

---

## Open: replies with nothing built against them yet

| written | designer note | what it asks for |
|---|---|---|
| 10-01 | `REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md` + `CurateAndUnderstanding` (canvas 16) | Curate's three bands on every kind; authors on tags, feedback and notes; database charts rebuilt on `database_tables`; per-kind chart list |
| 10-01 | `REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md` + `FindAndImport` (canvas 16) | One Find dialog per kind; non-connectable databases listed; CSV rows name a server, never a credential |
| 09-30 | `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` + `WorkListsAndScope`, `InvestigationLensFitPairs` (canvas 15) | Investigation / scope / work list vocabulary; the two actions; the scope grid; the data lens section; fit rows; pair questions |

---

## The ledger

Newest first. "Answered" means the evidence covers the whole note.
"In part" names what's left.

| written | designer note | subject | state |
|---|---|---|---|
| 10-01 | `REPLY-DESIGNER-CURATE-AND-UNDERSTANDING-ALL-KINDS.md` | Curate and Understanding for every kind | **not built yet** |
| 10-01 | `REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md` | Discovery sources for every kind; CSV in and out | **not built yet** |
| 10-01 | `REPLY-DESIGNER-DESIGN-NOTES-REORG.md` | The move; bare note names; INBOX reconcile; TIMELINE lines | answered — `DESIGN-NOTES-MOVE-IMPLEMENTED.md` (bare-name convention and dangling-pointer test adopted); the INBOX reconcile is this pass |
| 09-30 | `REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md` | Work lists vs investigations, questions 1–8 | **not built yet** |
| 09-29 | `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` + `EnrichmentContext` (canvas 14) | The Context tab; feeds-→; observation states | answered — `ENRICHMENT-E0-ROW-ANATOMY-IMPLEMENTED.md`, `ENRICHMENT-E1-CONTEXT-TAB-IMPLEMENTED.md`, `ENRICHMENT-E2-DOC-SOURCES-RESEAT-IMPLEMENTED.md`, `ENRICHMENT-E3-OBSERVATION-STATES-IMPLEMENTED.md`; human-question answers still have no author (§6 item 1) — unverified |
| 09-28 | `REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` + `DatabaseScreens` (canvas 12) | One glyph table; the By-analysis band; the Questions headline; per-schema tree | in part — `G1-GLYPH-CONSOLIDATION-IMPLEMENTED.md`, `G2-QUESTIONS-HEADLINE-SLOT-IMPLEMENTED.md`, `TABLE-COUNT-RENAME-IMPLEMENTED.md`, `BY-ANALYSIS-PROGRESSIVE-AND-GRAPH-IMPLEMENTED.md`; the tree's per-schema overview and the header remedies — unverified |
| 09-28 | `RelationshipGraph` (canvas 13) | The graph from the real edges; "no keys inside", never "isolated" | answered — `RELATIONSHIP-GRAPH-RENDERING-IMPLEMENTED.md`, `RELATIONSHIP-GRAPH-SCHEMA-SELECT-AFFORDANCE-IMPLEMENTED.md` (neither names the drawing; unverified on the per-schema wording) |
| 09-28 | `REVIEW-CURATE-PUBLISH-FRESHNESS.md` | Review of the curate publish-freshness work | landed late (`81031e92`), after `CURATE-PUBLISH-FRESHNESS-IMPLEMENTED.md`; no reply names it — **its findings are unanswered** |
| 09-28 | `REVIEW-MULTI-RESOURCE-REPRESENTATIONS.md` | Review of the multi-resource spec draft | needs no build (a review of a design) |
| 09-27 | `REVIEW-MULTI-RESOURCE-ROUND2.md` | Round 2 of the same | needs no build |
| 09-27 | `REPLY-DRAFT-BADGE.md` | `contentStatus: DRAFT` is the measured/declared axis, not a badge | needs no build (it ruled against building one) |
| 09-25 | `REVIEW-SURVEY-PANE-285.md` | Review of `#285` | answered — `SLICE-16-HONEST-ABSENCE-IMPLEMENTED.md`, `SLICE-17-RUNNABILITY-FROM-CATALOG-IMPLEMENTED.md` |
| 09-25 | `REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md` | One runnable-first list | answered — `41703c55` (no implemented note) |
| 09-24 | `REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md` | The §7 copy checklist | answered — `ae63e872` (items 1, 2, 4–9), `d4740781` (item 3) |
| 09-24 | `REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` | What a credential can see, shown | in part — `#250`–`#257`, `69d9871b` (§7/§8 docs); the §1 model ruling is with the architecture session (`ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md`) |
| 09-24 | `REPLY-SCHEMA-AS-SUB-RESOURCE.md` | A schema is a grain, not a sub-resource | answered — `SCHEMA-AGGREGATION-GRAIN-IMPLEMENTED.md` |
| 09-23 | `RULING-DB-QUESTION-CATALOG-CONSISTENCY.md` | The fact layer takes the resource type | answered — `9e614f38`, `b481c3dc` (no implemented note) |
| 09-22 | `RULING-SUBRESOURCES-PLACEMENT.md` | Sub-Resources configuration lives on Survey & analyses | answered — `d8855c71` (no implemented note) |
| 09-22 | `REVIEW-MULTI-RESOURCE-ROUND3.md` | Round 3 of the multi-resource spec | needs no build |
| 09-21 | `REPLY-RECONCILE-FLAGS.md` | Build the status route; the "scheduled" boolean; where the saved search goes | in part — `RESYNC-STATUS-ROUTE-IMPLEMENTED.md` matches §1 but doesn't name this reply; §2–§3 unverified |
| 09-20 | `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` | Representations for databases and file shares | in part — the phases in `COORDINATOR-BRIEF-MULTI-RESOURCE.md`; §2 "change over time" is still absent (parity D-24, D-28, D-31; see the Curate and Understanding reply) |
| 09-20 | `SPEC-ADMIN-THE-FOUR-GAPS.md` | Admin's four gaps | answered — `ADMIN-REGISTRIES-IMPLEMENTED.md`, `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md`, `GROUPS-ADMIN-IMPLEMENTED.md`, `RECONCILE-ADMIN-IMPLEMENTED.md` |
| 09-18 | `RULING-NAV-GROUPING.md` | Run, frame, cross-cutting; ad-hoc vs bound shown | answered — `NAV-GROUPING-IMPLEMENTED.md` |
| 09-17 | `SPEC-CURATE-SELECTION-AND-BLUEPRINTS.md` | Curate selection and blueprints | answered — `ITEM-3-CURATE-IMPLEMENTED.md` |
| 09-17 | `SPEC-PARITY-INVENTORY-AND-GROUPS.md` | Parity inventory; groups | answered — `GROUPS-ADMIN-IMPLEMENTED.md`, `SIDEBAR-GROUP-COLLAPSE-IMPLEMENTED.md` |
| 09-17 | `RULING-CLASSIC-AND-NEXT.md` | Classic retires only if `/next` earns it; capability may diverge, honesty may not | answered — `VERDICT-QUEUE-AND-CLASSIC-ROW-IMPLEMENTED.md` (primary-selection swap deferred to `docs/Backlog.md`) |
| 09-17 | `REVIEW-VERDICT-RULING.md` | Review of `#107`; the sort comparator; the classic row | answered — `VERDICT-QUEUE-AND-CLASSIC-ROW-IMPLEMENTED.md` |
| 09-17 | `SPEC-PUBLISH-STATE-AFTER-REDEPLOY.md` + `REPLY-PUBLISH-STATE-GO-AHEAD.md` | The fourth publish state | answered — `PUBLISH-STATE-AFTER-REDEPLOY-IMPLEMENTED.md` (shipped `8758f248`) |
| 09-17 | `ComponentTree` (canvas 7) | The branch tree | answered — `ITEM-3-CURATE-IMPLEMENTED.md` |
| 09-17 | `REPLY-RETRACTION-WITHDRAWN.md` | Withdrawing §4 | needs no build |
| 09-16 | `RULING-WHAT-A-VERDICT-IS-ABOUT.md` + `VerdictSubject` | What a verdict is about | answered — `VERDICT-RULING-IMPLEMENTED.md` |
| 09-15 | `SPEC-ACTIONABLE-AND-HONEST.md` + `Actionable` / `WhatWeFound` | Whose absence it is; the four destinations | in part — `#98`–`#104`, `NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md`; **Honest rows** and **Findings that act** — unverified since 09-17 |
| 09-14 | `REPLY-CATALOGUE-IN-LAYERS.md` | Layer 2 = accepted verdicts | answered — `ITEM-3-CURATE-IMPLEMENTED.md`; §4 superseded by `REPLY-RETRACTION-WITHDRAWN.md` |
| 09-14 | `SPEC-THE-STAGE-PAGE.md` + `StagePage` / `FactInPlace` / `AnalysesIndex` | The owner's round | answered — `#93`, `#94`, `#99` |
| 09-14 | `REPLY-PORTS-SCARCITY-CORRECTED.md` | Port scarcity corrected | answered — `#85`, `#86` |
| 09-14 | `SPEC-PORTS-ROUND-ONE.md` + `ComponentReview` / `PortsAndWires` | Ports as a column | answered — `#84`, `#85` † |
| 09-14 | `REPLY-CORRECTION-POPULATION.md` | The whole current list | answered — `#84` |
| 09-14 | `SPEC-REPORT-ACTS.md` + `ReportActs` | The three acts on a report | answered — `#83` † |
| 09-13 | `SPEC-RECORDS-AND-COST-CALLS.md` + `RunChoice` / `DepthOffer` | The re-run choice; the depth offer; the report record | answered — `#81`, `#82` † |
| 09-13 | `REVIEW-RAIL-SENTENCE.md` | Review of `#60` | answered — `#72` |
| 09-13 | `REPLY-COST-LADDER-AND-PUBLISH-STATE.md` | Publish off the ladder | answered — `#70`, `#71`, `#75`, `#76`; `funnel-cost-measured.md` (in `docs/`) † |
| 09-13 | `REPLY-FUNNEL-COST.md` | Rulings on the funnel measurement | answered — `#61`, `#63` † |
| 09-12 | `REPLY-BLANK-RAIL-AND-LIST-ANSWERS.md` | The blank rail; list answers | answered — `#59`, `#60`, `0460ba31` |
| 09-12 | `REVIEW-PROMOTION.md` | Review of `#41` | answered — `7433592f` † |
| 09-12 | `REVIEW-ENRICHMENT-JOURNAL-VENDORED.md` | Review of `#34`–`#39` | answered per the 09-17 ledger; **unverified** † |

† The 09-17 version of this ledger cited an implemented note for these
rows: `PORTS-ROUND-ONE`, `REPORT-ACTS`, `TWO-CALLS-AND-THE-RECORD`,
`FUNNEL-COST`, `FUNNEL-COST-STATUS`, `PROMOTION-AND-RAIL` and
`REVIEW-FIXES`. **None of those seven files was ever committed**, on any
branch. They must have lived in a working folder that never reached the
repo. The rows now cite the PRs and commits instead, and the one row with
no PR is marked unverified. The design notes aren't scanned by the
dangling-pointer test, which is why nothing caught this.

Dates are the ones written in each note. `TIMELINE.md` gives the commit date,
which is later for the notes that landed in the 09-16 bulk commit and for
the three reviews that landed in `81031e92`.

---

## Conventions

- Designer notes land in this folder's root. `REVIEW-*` reviews shipped work,
  `REPLY-*` answers a question, `SPEC-*` is something buildable, and
  `RULING-*` is a decision with consequences rather than a layout.
  Drawings go in `wireframes/` and on the canvas.
- Every designer note names the commit it was read against. If main has
  moved, say so rather than assuming the note is current.
- Implementer replies are `*-IMPLEMENTED.md` in `implemented/`. **Name the
  designer note you answer, by its bare filename**, so this ledger can cite
  you. **Write one even when the work shipped inside a PR raised for
  something else.** Fifteen rows above cite only PRs or commits, and
  several are *unverified* because nothing named the note.
- Refer to any note by its bare filename, never by a path. The
  dangling-pointer test resolves names across the tree
  (`DESIGN-NOTES-MOVE-IMPLEMENTED.md`).
- `TIMELINE.md` is the coordinator's. It gets one line per designer note
  when the note merges, and the designer doesn't write it.
- **Attribute decisions by role, not by first name**, using the greppable
  callout `**Decision (project owner, <date>):** ...` (root `CLAUDE.md`).
- **A designer ruling can be wrong.** Six were on the record by 09-17: the
  greyed `Find repos` tab, port scarcity, the `SoftwareLibrary` type,
  `lines_of_code`, the stale ports board rows, and a retraction the owner
  had already ruled out. Each was caught by measuring against the code. Since
  then the corrections have mostly run the other way: asks whose premise
  the code contradicted (one mechanism for work lists and investigations,
  a derived INBOX, a repo-only discovery pane). Measuring is still the
  fastest correction path, so the errors stay in the ledger where they can
  be read rather than being tidied out.

When an *-IMPLEMENTED note merges, move its ask and design into implemented/; answered exchanges go to exchange/.
