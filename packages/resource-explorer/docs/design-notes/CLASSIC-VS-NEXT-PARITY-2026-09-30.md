# Classic vs `/next`: what Classic can do that `/next` cannot (and the reverse)

**Date:** 2026-09-30
**Read against:** `origin/main` at `fffcc3a0` (code read directly; nothing run).
**Kind of document:** an inventory, for the project owner to review and decide what to
slice next. **It makes no recommendation, ranks nothing, and proposes no fix.** Every
row is *present*, *absent* or *partial*, and "partial" says in one line what is missing.

Paths used below (all under `packages/resource-explorer/`):

| short | file |
|---|---|
| `CL` | `resource_explorer/web/static/index.html` (Classic, one 18,648-line file) |
| `N` | `resource_explorer/web/static/next/` (`/next`; `N/app.js` is `next/app.js`) |
| `RA` | `resource_explorer/web/static/re-api.js` (the only API surface `/next` uses) |
| `DN` | `docs/design-notes/` |

---

## 0. Method, and how far to trust it

**Source of truth is the code.** `INVENTORY-CLASSIC-TO-NEXT.md` (2026-09-17) records, in
its own words, that counting vocabulary between the two files gave wrong answers three
times ("occurrence counting cannot distinguish wired from present"). So this inventory
was built the other way round:

1. Classic's actions were enumerated from its handlers: every `onclick`/button, every
   modal (`showXModal` / `submitX`), every `fetch(` call site (about 220 of them), and
   the render function behind each stage and sub-tab.
2. Each was looked for in `/next` by **reading the code that would own it** (the stage
   file, the dialog module, the sidebar/header code in `N/app.js`) and by checking
   whether `RA` exports a wrapper for the route. A control with no route wrapper in
   `RA` and no direct `fetch` in `N/` cannot call that route.
3. Where a row says "covered by a design note", the note was opened and read, not
   matched by keyword. Where it says **no note found**, that means keyword searches of
   `docs/design-notes/**/*.md` (recursive: the root plus `implemented/` and `evidence/`), `docs/Backlog.md` and `docs/*.md` on the control's own
   vocabulary returned nothing relevant. It is absence of evidence, not proof.
4. **Nothing was run.** No server, no registry, no database was touched, per the task's
   rules. So statements about runtime behaviour (what an error looks like on screen)
   are from reading the code path, and say so.

**Status words**

- **present**: `/next` has an equivalent that does the same job. Where it lives
  somewhere other than Classic's location, the location column says where.
- **partial**: some of the job is there. The Notes cell says, in one line, what is missing.
- **absent**: no equivalent control exists in `/next`.

**Resource kinds.** Sections 1-3 are the actions that belong to one kind (repository,
database, filesystem). Section 4 is every action Classic offers identically on all three
kinds (curation, context, scheduling, survey definitions). Section 5 is kind-independent
chrome (investigations, RFAs, chat, activity, admin, header). Section 6 is the reverse
direction. Section 7 is the tally and the verification of the five findings the task
seeded.

**Caveat on "Classic offers it for all three kinds".** Classic's Questions, Disposition
and Dashboard sub-tabs, and its per-analysis Run, are repo-only (`_qdTabsApply()`,
CL:1998; `_runAnalysisCatalogCard`, CL:5016). For databases and filesystems Classic's
analysis-card Run opens the whole-resource Survey modal instead. Those are listed in
section 6 because `/next` does more there, not less.

---

## 1. Repository

### 1a. Sidebar and registry

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-01 | Show the repo list | Chip "Repos" (`setResourceTypeFacet('repo')`, CL:2663) | present | Sidebar chip "Repos" (`renderSidebar`, N/app.js:1939) | n/a | Default in both. |
| R-02 | Filter list by text | Filter box (`_setProjectFilterText`, CL:5409); matches name, URL, description | present | Sidebar filter box | n/a | `Backlog.md` (2026-09-30) records that /next's box does not clear when the resource-type chip changes. |
| R-03 | Filter by lifecycle | Chips In scope / All / New / Surveyed / Published (`_setProjectLifecycleFilter`, CL:5816) | present | `SCOPE_CHIPS`, N/app.js:1760 | n/a | "In scope" is disabled without a current investigation in both. |
| R-04 | Filter by disposition | Disposition chips (`_setDispositionFilter`, CL:5509) | present | "Disposition" facet chips, `renderSidebar` | n/a | |
| R-05 | Show / hide hidden repos | "Show hidden (n)" toggle | present | `show-hidden` in `renderSidebar` | n/a | |
| R-06 | Select mode: all shown / none / done, group-level tick | `_toggleSelectMode`, `_toggleGroupSelected` (CL:5594, 5616) | present | `select-mode`, `sel-all`, `sel-none`, group checkbox | n/a | |
| R-07 | Bulk add / remove from current investigation | `_addSelectedToInvestigation`, `_removeSelectedFromInvestigation`; `POST/DELETE /api/investigations/{x}/members` | present | `sel-scope-add`, `sel-scope-remove` (`bulkScope`, N/app.js:2374) | n/a | |
| R-08 | Bulk hide | `_hideSelected`; `POST /api/discovery/working-set` | present | `sel-hide` | n/a | |
| R-09 | Bulk delete (unregister and drop local data) | `_deleteSelected` (CL:5707); `DELETE /api/projects/{slug}` | present | `sel-delete` then `confirmBulkDelete`/`bulkDelete` (N/app.js:2458, 2480) | n/a | Classic makes you type DELETE in a prompt (CL:5715); /next uses an inline confirm/cancel and dispatches by resource type through `removeEntity`. Classic's select mode is repo-only. |
| R-10 | Add or remove one repo from the investigation | Per-row button (`_toggleInvestigationMember`, CL:5747) | partial | Bulk only (R-07); Investigation stage `inv-add-member` | no note found | No per-row toggle in the /next sidebar. |
| R-11 | Assign one repo to a group | Per-row button, modal (`openAssignGroupModal`, CL:5951); `POST /api/projects/{slug}/group` | partial | Admin, Groups, resource picker + group picker (`admin/groups.js`) | Yes: `admin/groups.js` header and `GROUPS-ADMIN-IMPLEMENTED.md` say the per-row entry point was deliberately not built | The capability exists; the per-resource control does not. |
| R-12 | Hide / unhide one repo | Per-row eye button (`toggleWorkingSetHidden`, CL:5979) | present | Resource header `hide`/`unhide` (N/app.js:2943, wired at 3236) + bulk | n/a | The header is rendered only on some tabs (see D-10). |
| R-13 | Open repo on GitHub | Per-row link | present | Sidebar link and header "GitHub" link | n/a | |
| R-14 | Compare 2+ repos then ask chat about them together | Shift-click row (`toggleCompare`, CL:6045); chat prefixes "a and b: ..." (CL:14329) | absent | Chat rail is scoped to one resource | no note found | Work lists (`worklist.js`) compare cohorts as a questions-by-resources grid; that is a different mechanism, not a chat shortcut. |
| R-15 | Collapse / expand a group (remembered; a filter force-expands) | `_toggleGroupCollapsed` (CL:5316) | present | `toggleGroupCollapsed` (N/app.js:1870) | n/a (`SIDEBAR-GROUP-COLLAPSE-IMPLEMENTED.md`) | Separate localStorage key from Classic. |

### 1b. Finding and importing repos (Scouting, Search sub-tab in Classic)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-20 | Search GitHub (keyword, min stars, language, license, pushed-after, org, topic, archived, forks) | "Search" (`searchScoutRepos`, CL:16221); `POST /api/discovery/search` | present | Sidebar "+" then `openFindReposDialog` (`discovery-import.js`) | n/a | `NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md`. |
| R-21 | Load a repo list from a file or URL list | File input (`_loadRepoListFromFile`, CL:16348); `POST /api/discovery/from-list` | present | Same dialog, "From a list" | n/a | |
| R-22 | Download inventory CSV | "Download inventory" (`_downloadInventory`, CL:16308); `GET /api/discovery/inventory.csv` | present | Same dialog | n/a | |
| R-23 | Import selected candidates (with a group) | "Import selected" (`importSelectedScoutRepos`, CL:16618); `POST /api/discovery/import` | present | Same dialog | n/a | |
| R-24 | Set a disposition on a candidate before importing | Per-row buttons (`_setScoutDisposition`, CL:16580) | present | Same dialog | n/a | |
| R-25 | Run a saved discovery source | Source chips (`runDiscoverySource`, CL:16136); `POST /api/discovery/sources/{x}/run` | present | Admin, Discovery Sources, Run (preview, then import) | `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` | Lives in Admin, not on the Find dialog; the quick-run chips are not on the Find dialog. |
| R-26 | Quick-add a foundation's curated list as a source | `_quickAddListSource` (CL:16099) | present | Admin, Discovery Sources, "Quick add" | `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` | |
| R-27 | Save the current search as a named source | `_saveCurrentSearchAsSource` (CL:16156); `POST /api/discovery/sources` | present | Admin, Discovery Sources, Add, Search tab (preview, then save) | `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` | No "save" button on the Find dialog itself. |
| R-28 | Pre-filter the search by foundation | Foundation chips (`_applyFoundationPrefilter`, CL:16091); `GET /api/discovery/foundations` | absent | none | Named as not built: `NEXT-DISCOVERY-IMPORT-SEARCH-IMPLEMENTED.md`, "What was deliberately not built". No plan to build it. | `RA` has no wrapper for `/foundations`. |
| R-29 | Edit / reset the GitHub API base URL (GitHub Enterprise) | "[edit]", Save, "Reset to default" (`_saveGithubSource`, CL:16185); `/api/discovery/github-base-url` | absent | none | Named as not ported: `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` ("not a way to add a discovery source at all"); no other home proposed | |

### 1c. Repo header, Scouting overview, report, Egeria panel

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-30 | Select a repo and see where it stands | Row click (`selectProject`, CL:5992) | present | Resource header (`resourceHeaderHtml`, N/app.js:2839): name, links, surveyed/published line | n/a | |
| R-31 | Scouting overview tiles: Website, Language, Stars, Forks, Contributors, Last pushed, Size, Security and analysis, Deployments | Scouting, Survey tab (`renderScoutingOverview`, CL:11324); `GET /api/projects/{slug}/scouting-overview` | partial | Header shows GitHub link, "Project site" and surveyed/published state | no note found | /next fetches the overview but renders only homepage and publish fields; no stars/forks/contributors/size/language/security/deployments tile. |
| R-32 | Stale Egeria catalog link: banner with three repair choices | Banner (`_egeriaLinkActionsHtml`, CL:17226); `POST /api/egeria/linkage/{type}/{slug}/resolve` | partial | Header sentence "published, but the Egeria link is stale" (N/app.js:2880) | Admin, Egeria Links is a named deferral (`ITEM-5-ADMIN-IMPLEMENTED.md`; `admin/index.js:73`) | The warning shows; the repair choices do not. |
| R-33 | Scouting signal tile row | `_loadScoutingOverviewStats` (CL:11463); `GET /api/projects/{x}/survey-results/summary?phase=scouting` | partial | By analysis boards carry the same measurements | no note found | No at-a-glance tile row. |
| R-34 | Jump to Disposition from the overview | "Manage disposition" link | present | Header `disposition` button; Disposition sub-tab | n/a | |
| R-35 | Full survey report (metrics, file types, data files, dependencies by ecosystem, survey history, diff banner) | "View full report" (`loadSurveyReport`, CL:12867); `GET /api/egeria/{slug}/survey-report`, `/diff` | partial | The same measurements appear as By analysis boards; no report view; no diff banner | no note found | Survey History table and "Changes since last run" banner are absent (see R-40). |
| R-36 | "Ask about this" (prefill chat) | Button (`askAboutSurvey`, CL:13108) | absent | Chat rail exists, no prefill shortcut | no note found | |
| R-37 | Catalog selected file types as Egeria elements | Checkboxes + "Catalog selected" (`catalogSelected`, CL:13115); `POST /api/egeria/{slug}/catalog-elements` | absent | none | no note found | `egeria-integration.md` and `surveyor-reference.md` describe it for Classic only. |
| R-38 | List the repo's Egeria SurveyReports, refresh, expand annotations | "Egeria Surveys" (`loadEgeriaSurveys`, CL:6787); `GET /api/egeria/{slug}/egeria-surveys` and `/{guid}/annotations` | absent | Native-survey rows exist only for database and filesystem | no note found | |
| R-39 | Understanding charts: Stars, Commits, Languages, Health, File Types | Chart tabs (`showChart`, CL:6411); `GET /api/stats/{slug}/charts/{kind}` | present | Understanding stage (`stages/understanding.js`, `REPO_CHARTS`) | n/a | /next adds weekly commits, top committers, survey history (section 6). |
| R-40 | Survey-history trend chart (files over time) | `loadRepoSurveyHistoryChart` (CL:13197); `GET /api/stats/{slug}/charts/survey_history` | present | Understanding, "Survey history" chart | n/a | The "Changes since last run" diff banner (`GET /api/egeria/{slug}/diff`) is absent. |
| R-41 | Egeria tab: registered?, GUID, survey-history table, expand annotations | `showEgeria`/`renderEgeriaPanel` (CL:6510, 6528); `GET /api/egeria/{slug}/status` | partial | Header publish-state line | no note found | No survey-report table, no annotation expansion, no quality radar. |
| R-42 | Publish survey to Egeria (with optional governance zones) | "Publish survey" (`publishSurvey`, CL:6597); `POST /api/egeria/{slug}/publish` | absent | none. Indirect only: Curate "Catalogue" commit (`stages/curate.js`) and survey definitions that auto-publish (`planSurveyRun`, N/app.js:4759) | no note found | `N/app.js:2892` tells the user to "publish again from the Analysis pane", but no publish control exists in `/next`'s Analysis pane. |
| R-43 | Reset cached Egeria GUIDs and survey history | "Reset" (`resetEgeria`, CL:6641); `POST /api/egeria/{slug}/reset` | absent | none | no note found. Admin, Egeria Alignment (`admin/resync.js`) reconciles drift store-wide; it has no per-repo clear | |

### 1d. Questions, disposition, dashboard (repo-only in Classic)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-50 | Questions checklist for the stage | Questions sub-tab (`loadScoutingQuestions`, CL:11549); `GET /api/projects/{x}/scouting-questions` | present | Questions sub-tab (default), `loadPane` (N/app.js:6745) | n/a | Row actions in /next: evidence, diagram, copy as evidence, run/re-run, "numbers behind this", notify me. |
| R-51 | Click a question to answer it in chat | Row click (`_answerQuestionInChat`, CL:11701) | absent | none | no note found | |
| R-52 | "Why is this question here" disclosure | "why?" (`_toggleDerivation`, CL:11622) | partial | Row provenance line (`provenanceLine`) names answering analyses | no note found | No "shown because perspective / serves purpose" disclosure. |
| R-53 | Set disposition (6 values; optional reason) | Buttons, prompt for reason (`_setProjectDisposition`, CL:12353); `POST /api/discovery/disposition` | present | Header `disposition`; Disposition sub-tab (`loadDispositionPane`, N/app.js:3608) | n/a | /next asks for a reason when reversing a terminal verdict. |
| R-54 | Disposition history | `GET /api/discovery/disposition-history` | present | Disposition sub-tab trail | n/a | |
| R-55 | Stage Dashboard (trend charts, grouped result cards) | Dashboard sub-tab (`_loadSurveyResultsPanel`, CL:8717) | present | By analysis sub-tab (`loadByAnalysisPane`, N/app.js:6342) | n/a | Renamed; same data. |
| R-56 | Open one analysis's latest results with trend | "Results" on a card (`toggleAnalysisResults`, CL:3382); `GET /api/projects/{x}/analyses/{id}/results`, `/trend` | present | By analysis cards; measurement detail with history (`openMeasurementDetail`) | n/a | |

### 1e. Survey, analysis and sub-resource actions

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-60 | Run one analysis | Card "Run" (`runAnalysisCatalogCard`, CL:5088); `POST /api/projects/{slug}/analyses/{id}/run` | present | Survey & analyses row "run" (N/app.js:4711); Questions row run/re-run (`rerun`, N/app.js:8146) | n/a | |
| R-61 | Run in the background (do not wait for the Egeria publish) | "Run in background" (`publish=background`) | present | Questions row run-choice popover "Background" / "Run and wait" (`openRunChoice`, N/app.js:8073) | n/a | Not offered on Survey & analyses rows, which always wait. |
| R-62 | "Already up to date": Run anyway (force) | Toast with "Run anyway" (`_handleFreshnessSkip`, CL:5061); `?force=true` | absent | none | no note found | Backend returns `status: "skipped"` when `gate_user_runs` is on (`routes/projects.py:783-795`); `RA.runAnalysis` has no force and `/next` has no `skipped` handling. |
| R-63 | Prerequisite proposal: accept / decline | `acceptPrerequisiteProposal` (CL:3499); `POST /api/prerequisites/run` | present | `checkPrerequisitePlan` / `acceptPrerequisiteProposal` (N/app.js:8113, 7727) | n/a | |
| R-64 | Run all surveys for a stage | "Run all <stage> surveys" (`_runStageBatch`, CL:5214); `POST /api/projects/{slug}/analyses/stage/{stage}/run` | absent | none (work lists run one analysis across many resources, the transpose) | no note found (Backlog.md item 14 describes the Classic button only) | |
| R-65 | Sub-resources: run `sub_resource_survey`, filter, select, catalog (with publish checkbox), run a scoped analysis, uncatalog | Analysis, Sub-Resources (`loadAnalysisSubResourcesView`, CL:12521) | present | `sub_resource_survey` row, "select and catalog" (`stages/analysis.js`) | n/a (`RULING-SUBRESOURCES-PLACEMENT.md`) | |
| R-66 | Architecture diagram in results | `renderPendingArchDiagrams` (CL:4818); `POST /api/diagrams/mermaid` | present | By analysis diagram card; Curate component diagram (Kroki) | n/a | |

### 1f. Curate and Automate that are repo-only

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| R-70 | Accept / reject / retype a recovered component, with a note | Verdict buttons (`_archSubmitVerdict`, CL:4504); `POST /api/curate/component-verdicts/repo/{slug}` | present | Curate component tree, branch verdicts (`postBranchVerdicts`, `stages/curate.js`) | n/a (`ITEM-3-CURATE-IMPLEMENTED.md`) | |
| R-71 | Accept / reject a blueprint | `_archSubmitBlueprintVerdict` (CL:10340); `POST /api/curate/blueprint-verdicts/repo/{slug}` | present | Curate blueprint list (`postBlueprintVerdict`) | n/a | |
| R-72 | "Publish missing component(s) and accept" on a blueprint | `_curatePublishMissingComponents` (CL:10273) | partial | /next accepts the blueprint and queues members; shows a "not yet confirmed linked" count | `ITEM-3-CURATE-IMPLEMENTED.md`, "What was deliberately not built" (membership confirmation tracking) | No pre-step that publishes the unmet components. |
| R-73 | Jump from a blueprint to its components and back | `_curateJumpToComponent`, `_curateJumpToBlueprint` (CL:4401-4402) | absent | none: Curate's section nav is page-level anchors only (`stages/curate.js:60-71`, whose comment says it is "a narrower and separate thing" than Classic's cross-reference jump) | no note found | |
| R-74 | Schedule a survey for one repo (Automate, Surveys sub-tab) | `_saveSurveySchedule` (CL:9852); `POST /api/schedules/repo/{slug}` | absent | none | Yes: `ITEM-4-AUTOMATE-IMPLEMENTED.md`, "What's still open" names the Surveys sub-tab | Classic's tab is repo-only. |

---

## 2. Database

### 2a. Sidebar, servers and registration

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| D-01 | Show the database list | Chip "DBs" (CL:2663) | present | Sidebar chip "DBs" | n/a | Lists load on first switch. |
| D-02 | Register a database server | "Register server" (`showRegisterServerModal`, CL:6971); `POST /api/db-servers/register` | present | Sidebar "+" then `openFindDbServersDialog`, "Register a server" (`db-server-discovery.js`) | n/a | |
| D-03 | Test connection before registering | "Test" in the modal (`testServerConnectionModal`, CL:7039); `POST /api/db-servers/_test-inline` | present | Same dialog, "Test" | n/a | |
| D-04 | Test a registered server | Server row lightning button (`testServerConnection`, CL:6302); `POST /api/db-servers/{slug}/test` | present | Server row "Test" | n/a | |
| D-05 | Discover and add databases on a server | Server row search button (`showDiscoverModal`, `addSelectedDatabases`, CL:7090, 7151); `POST /api/db-servers/{slug}/discover`, `/add-database` | present | Server row "Discover", "Add Selected" | n/a | |
| D-06 | Remove a server (and its linked databases) | Server row trash (`removeServer`, CL:6349); `DELETE /api/db-servers/{slug}` | present | Server row "Remove" | n/a | |
| D-07 | Server detail page (connection, Egeria fields, registered databases and their survey state) | Click server row (`selectServer`/`loadServerDetail`, CL:6196, 6227) | partial | Server row inside the dialog: type, host, group, database names | no note found | No Egeria URL / view server / user fields, no per-database "surveyed" state. |
| D-08 | Register one database directly (slug, host, port, database, group, credentials, Egeria fields) | "Register database" (`showRegisterDbModal`, `submitRegisterDb`, CL:6897, 6917); `POST /api/databases/register` | absent | none (`RA` has no wrapper) | no note found | `/next` can only add a database through a registered server's Discover. |
| D-09 | Edit a registered database (change credentials, host, etc.) | **None in Classic either.** Backend `PATCH /api/databases/{slug}/credentials` (`routes/databases.py:471`) and CLI `database update-credentials` exist | absent | none | Partly: `Backlog.md` 2026-09-30 entries on `update-credentials` (test before save; password as CLI argument); `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md` s.1 and `REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` s.7.1 describe a "provide broader credentials" prompt as design direction, not specced | Absent in both UIs; the only route to change a stored credential is the CLI / API. Classic's register route rejects an existing slug ("already exists"). |
| D-10 | Remove one database | Sidebar trash (`removeDatabase`, CL:6394); `DELETE /api/databases/{slug}` | partial | (a) Sidebar select mode, "delete" works for databases (`removeEntity` to `removeDatabase`). (b) Resource header `remove` is broken for databases | Not recorded | Header `remove` calls `removeProject(slug)` for every resource type (N/app.js:3277), so for a database it sends `DELETE /api/projects/{dbslug}`, which the route 404s ("Project not found", `routes/projects.py:1289-1296`); had it succeeded it would filter `state.projects`, not `state.databases`. On the Schema Inventory tab the header is rendered but `bindResourceHeader()` is never called (N/app.js:3402-3418), so `remove`, `hide` and `disposition` there do nothing. Classic also swallows a non-OK delete silently (CL:6402). |
| D-11 | Run a survey from the sidebar row | Row button (`showSurveyDbModal`, CL:6165) | absent | none (no per-row actions in /next) | no note found | See D-20. |

### 2b. Database report (Classic: Scouting, Survey; `loadDbSurveyReport`, CL:13255)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| D-20 | **Survey modal with credentials**: run a whole-database survey, optionally override username/password, optionally "Try Egeria first (hybrid)" with Egeria URL/server; confirm before hybrid | "Re-survey" / "Run First Survey" / any analysis-card Run (`showSurveyDbModal`, `submitSurveyDb`, CL:7174, 7202); `POST /api/databases/{slug}/survey` | absent | none. `RA` has no wrapper for `/survey`. A database survey in /next is a Survey Definition run (D-30) using stored credentials | Partly: design direction only. `REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` s.7.1 and `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md` s.1 propose a credential prompt driven by the capability probe; no note specifies a control. The hybrid confirm dialog was added 2026-09-30 (`FALSE-ZERO-PUBLISH-HOTFIX-IMPLEMENTED.md`), Classic only | No field anywhere in /next to enter or override a database credential. |
| D-21 | Catalog and survey in Egeria (database not yet cataloged): modal with DB and Egeria credentials | "Catalog and Survey in Egeria" (`showPublishDbModal`, `submitPublishDb`, CL:13730, 13772); `POST /api/databases/{slug}/publish` | absent | A "Catalog and Survey" native-survey row is listed but locked ("RE cannot run this one", `stages/native-surveys.js`) | Yes: `Backlog.md` "Wire Catalog and Survey for one-click execution (2026-09-30)" | Classic's route publishes RE's latest *measured* local snapshot to Egeria and creates assets; /next's native row would submit an Egeria engine action. Different mechanisms. |
| D-22 | **Re-survey in Egeria** (database already cataloged) | Header button (`showPublishDbModal` with the "Re-survey" wording, CL:13370); same route | partial | "Egeria's own surveys" rows on Survey & analyses; "Survey PostgreSQL Database" row, run, poll, report (`stages/native-surveys.js`, `RA.runNativeSurvey`) | `BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md`, `NATIVE-EGERIA-SURVEY-LAUNCH-IMPLEMENTED.md` | Same Egeria-side effect (a native survey on the existing asset), different route. Does not publish RE's local snapshot, and takes no credential override (uses the stored one; shows a "credentials_note"). |
| D-23 | Last surveyed, and a source badge (Egeria / Egeria+Local / Local) | Header line (CL:13335-13355) | partial | Header line "surveyed N ago" and publish state | no note found | No source badge. |
| D-24 | "Changes since last run" banner (schemas, tables, columns deltas; tables added/removed) | `loadDbDiffBanner` (CL:13167); `GET /api/databases/{slug}/diff` | absent | none | Design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Change over time" (schema-diff timeline, Understanding); `DB-CHANGE-RATES-DELIVERY-IMPLEMENTED.md` (comparators). No plan to build the banner | |
| D-25 | Metric cards: schemas / tables / columns | CL:13380-13390 | partial | Schema Inventory per-schema counts; By analysis headlines | no note found | No three-number summary card. |
| D-26 | Schema and Tables accordion: expand schema, table, columns (type, nullable, default, primary key, foreign-key target, description) | `toggleSchemaBlock`, `toggleTableBlock` (CL:13712, 13720) | partial | Schema Inventory sub-tab (`loadSchemaInventoryPane`, N/app.js:3392; `tableHtml`) | no note found for the missing columns (`SLICE-22-SCHEMA-INVENTORY-VIEW-IMPLEMENTED.md` is the note that built the tab) | /next shows name, type, nullable, a PK/FK mark (no referenced table), comment, rows/bytes, schema shortfall labels, and a filter; no default-value column, no foreign-key target. |
| D-27 | Top Tables chart and ranked table (rows, size, last analyzed, pending changes) | CL:13480-13530 (Plotly) | absent | none | Design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Inventory" (treemap) | |
| D-28 | Understanding charts for a database: Schemas, Tables, Col Types | Chart tabs (`showDbChart`, CL:6443); `GET /api/stats/databases/{slug}/schema_distribution`, `/table_sizes`, `/column_types` | absent | Understanding for a database draws no chart; it lists the repo chart kinds, each marked "does not apply" or (survey history) "not wired up yet" (`stages/understanding.js:26-48`, `loadChartsPane`) | The gap is named in code (`understanding.js` header: the routes return plain arrays, not Plotly figures); design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2. No build plan | The three database routes exist server-side; `RA` has no wrapper for them. |
| D-29 | Database Views and Lineage sub-tab (view list, complexity, portability, SQL, dependencies, lineage flowchart) | `switchDbSubTab('views')` (CL:18195), `buildMermaidLineageForView` (CL:18225) | absent | none | Design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Lineage"; `multi-resource-questions-design.md` | The relationship (foreign-key) graph in /next By analysis is a different artefact. |
| D-30 | **Survey history table with a "show invalid" toggle** (date, schemas, tables, columns, source; invalid rows muted with reason and when marked) | `_dbSurveyHistoryTableHtml`, `toggleDbInvalidSurveys` (CL:13279, 13311); `GET /api/databases/{slug}/surveys[?include_invalid=true]` | absent | None. Nearest: "Runs on this resource" dialog (`openRunsList`, N/app.js:5207), which lists survey activity entries with step status | `DATABASE-SURVEY-HISTORY-INVALID-VIEW-IMPLEMENTED.md` is Classic-only and never mentions `/next`. No /next plan found | /next has no equivalent view of survey rows. `RA` has no wrapper for `/surveys` or `include_invalid`. The Runs dialog reads the activity log, which does not show registry rows or invalid markers. |
| D-31 | Schema metric trend chart (tables, schemas, columns over runs) | CL:13225; `GET /api/stats/databases/{slug}/survey_history` | absent | Understanding tile "Survey history" says "not wired up yet" | Design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Change over time"; gap named in code (`understanding.js` `CHART_GAP_ROUTE`). No build plan | |
| D-32 | Egeria SurveyReports list for the database, refresh, expand annotations (grouped, confidence, DRAFT badge, quality radar) | "Egeria Surveys" (`loadEgeriaSurveys`, `toggleEgeriaAnnotations`, CL:6787, 6831); `GET /api/databases/{slug}/egeria-surveys`, `/{guid}/annotations` | partial | Report of a native survey RE launched: annotation list in a dialog (`openReport`, `stages/native-surveys.js`) | no note found for the missing parts (`NATIVE-EGERIA-SURVEY-LAUNCH-IMPLEMENTED.md` built the part that exists) | Only reports from runs RE launched; no list of all reports, no refresh, no grouping, no content-status badge, no radar. |
| D-33 | Active classification rules (Data Classes) block | `toggleActiveRulesBlock` (CL:6688); `GET /api/egeria/rules/dataclasses` | absent | none | no note found | |

### 2c. Running surveys on a database (Discovery / Assessment / Analysis / Enrichment Survey tabs)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| D-40 | List Survey Definitions that suit this database | Survey panel (`_loadSurveyPanel`, CL:8284); `GET /api/survey-definitions/database/{slug}/candidates` | present | Survey & analyses sub-tab, `loadSurveyPane` (N/app.js:4360) | n/a | |
| D-41 | Run a Survey Definition on a database | "Run" opens modal (`showSurveyDefinitionRunModal`, `submitSurveyDefinitionRun`, CL:9010, 9091); `POST /api/survey-definitions/database/{slug}/run` | present | Row "run" / "re-run": plan dialog then `launchSurvey` (N/app.js:4759, 4823) | n/a | Sends no credential; the executor falls back to the database's stored one (commit `573b22e7`). See D-42. |
| D-42 | **Enter or override DB username/password for this run; "remember for this session"** | Credential fields in the run modal (CL:1004-1036; `_surveyDefSavedCreds`) | absent | none (`RA.runSurveyDefinition` posts only `survey_definition_ref`) | Design direction only (see D-20) | Same gap as D-20. |
| D-43 | **A failing credential is shown correctly** | Modal: per-step red status, aggregated errors in the red error line, a toast, an Activity entry, "Retry" (CL:9140-9275) | partial | After the run, `launchSurvey` polls, then reloads the pane; the definition row shows a warning glyph, a button opens the "Last run" errors dialog (`lastRunHtml`, N/app.js:3976; handler 4502). "Runs on this resource" shows step status. `credential_capability` banner on the header (`resourceHeaderHtml`) says what the credential can see | no note found for the completion-time message. Adjacent: `PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md` (engine note on the row); `REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md` (credential banner) | By code reading, not exercised. Differences: the launch note does not show the failure; it appears on the reloaded row, one click away, and only if `last_run_errors` was recorded. A per-analysis run (`rerun`, `renderAnalysesIndexSection`) discards the finished entry returned by `pollActivity` and shows only a start failure ("The run could not be started") or a glyph from `last_run_status`; the error text is not shown. |
| D-44 | Engine choice (Prefect or local), shown only when Prefect is reachable | Select in the run modal (CL:9045-9062); body `engine` | absent | none; the engine used is shown on the row after the run (`engineNoteHtml`) | no note found (default is Prefect: `PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md`) | |
| D-45 | After a run: step list with engine badges, report GUID, "View in Egeria Catalog" link, annotations list | Modal result (CL:9150-9250) | partial | Row last-run line, errors dialog, "Runs on this resource" step list with declared-vs-received (`openRunsList`) | no note found for the missing pieces (`PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md` covers the engine line) | No Egeria portal link; annotations not shown inline. |
| D-46 | "Not cataloged in Egeria: Catalog now, then retry" | `catalogAndRetrySurveyDefinition` (CL:9274); `POST /api/databases/{slug}/publish` | absent | none | Related, not the same control: `Backlog.md` "Wire Catalog and Survey for one-click execution" (see D-21) tracks the native route for a never-cataloged database | |
| D-47 | Analysis-card Run on a database | Card Run opens the whole-database Survey modal (`_runAnalysisCatalogCard`, CL:5016) | present | Per-analysis run `POST /api/databases/{slug}/analyses/{id}/run` (`RA._runAnalysisPath`) | n/a | /next does more here (section 6). |

---

## 3. Filesystem

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| F-01 | Show the filesystem list | Chip "FS" | present | Sidebar chip "FS" | n/a | |
| F-02 | **Register a filesystem** (slug, name, local mount, canonical mount, description, group, Egeria URL/server/user/password) | "Register filesystem" (`showRegisterFsModal`, `submitRegisterFs`, CL:14698, 14717); `POST /api/filesystems/` | absent | Sidebar "+" on FS opens a stub: "Register a filesystem path" with an "Open in the current UI" link (N/app.js:2327) | Named, not designed: `Backlog.md` "MEDIUM: /next has no real Search/Discover screen" says filesystems are "not yet investigated"; `FIND_TITLE` comment says classic's FS registration flow "is not this file's scope yet". No build plan | An honest link-out, not a control. |
| F-03 | Remove a filesystem | Sidebar x button (`removeFilesystem`, CL:14682); `DELETE /api/filesystems/{slug}` | partial | Sidebar select mode, "delete" works; header `remove` has the same defect as D-10 | Not recorded | See D-10. |
| F-04 | Run walk and survey from the sidebar row | Row button (`showSurveyFsModal`, CL:14762) | absent | none | no note found | See F-05. |
| F-05 | Run a directory walk and profile (optionally hybrid with Egeria URL/server/user/password) | "Re-run Survey" / "Run Directory Walk and Profile" (`submitSurveyFs`, CL:14784); `POST /api/filesystems/{slug}/survey` | partial | Survey Definitions for filesystems run from Survey & analyses (`filesystem` adapter exists) | no note found | No walk-and-survey modal, no Egeria fields. `RA._runAnalysisPath` documents that a per-analysis run has no filesystem route; a filesystem analysis row "run" calls the repo route and 404s. |
| F-06 | Publish / re-publish to Egeria (modal with Egeria URL/server/user/password) | "Publish to Egeria" / "Re-publish" (`submitPublishFs`, CL:14854); `POST /api/filesystems/{slug}/publish` | absent | none | no note found | |
| F-07 | Check reachability from the Egeria engine host; sentence with outcome | "Check Reachability" (`checkFsReachability`, CL:15212); `POST/GET /api/filesystems/{slug}/reachability` | absent | none | Built for Classic only (`RESOURCE-REACHABILITY-IMPLEMENTED.md`, "lives in the classic UI"); design: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Reachability and cost". No /next build plan | |
| F-08 | Filesystem report: metrics cards, file classification, data-file types breakdown | `renderFilesystemSurveyReport` (CL:14919) | absent | none; answers appear only as Questions rows / By analysis | Design only: `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` s.2 "Inventory" (treemap) | |
| F-09 | Data File Profile: per-file accordion with column profile (null %, types) | CL:15090-15160 | absent | none | `DB-FS-STRUCTURED-TABLES-IMPLEMENTED.md` covers the storage; `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md` "Column contents" the card design. No /next build plan | |
| F-10 | Egeria SurveyReports list for the filesystem, refresh, annotations | `loadEgeriaSurveys('/api/filesystems/...')`; `GET /api/filesystems/{slug}/egeria-surveys` | partial | Native-survey rows and report dialog also serve filesystem (`routes/native_surveys.py` `_ENTITY_TYPES`) | `NATIVE-EGERIA-SURVEY-LAUNCH-IMPLEMENTED.md` | Same limits as D-32. |
| F-11 | List Survey Definitions and run one on a filesystem | Survey panel, `POST /api/survey-definitions/filesystem/{slug}/run` | present | Survey & analyses | n/a | No credential fields apply to filesystems. |

---

## 4. Actions Classic offers identically on repo, database and filesystem

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| C-01 | Assign the selected resource to a group | Curate, General, select + Save (`saveCurateGroup`, CL:10469); `POST /api/projects/{slug}/group` | partial | Admin, Groups, resource picker (all three kinds; `admin/groups.js`) | Yes: `GROUPS-ADMIN-IMPLEMENTED.md` | No control on the resource itself. |
| C-02 | Add / remove a tag | Curate, General (`addCurateTag`, `removeCurateTag`, CL:10487, 10504); `POST/DELETE /api/curate/tags/{type}/{slug}` | absent | none (no `RA` wrapper) | no note found. **A message in `/next` says otherwise** (see Notes) | `stages/curate.js:194-205` (`nonRepoCurateHtml`) tells the user "tags, feedback and curator notes ... are reachable from the resource header regardless of type"; the resource header (`resourceHeaderHtml`) has only Open Investigation, disposition, hide, remove. |
| C-03 | Resource-level feedback (rating + category) | Curate, General (`addCurateFeedback`, CL:10513); `POST /api/curate/feedback/{type}/{slug}` | absent | none (per-answer feedback exists, a different thing: section 6) | no note found | Same on-screen claim as C-02. |
| C-04 | Curator notes: add, delete | `addCurateNote`, `deleteCurateNote` (CL:10531, 10547); `POST /api/curate/notes/{type}/{slug}`, `DELETE /api/curate/notes/{id}` | absent | none | no note found | Same on-screen claim as C-02. |
| C-05 | Context form (environment, sensitivity, owning team, steward, location, backup, purpose, notes) | Enrichment, Context (`loadContextPanel`, `saveContextForm`, CL:9362, 9450); `POST /api/context/{type}/{slug}` | partial | Enrichment, Context sub-tab (`stages/context.js`): judgements (sensitivity, criticality, intended use, actual use, owner) and observations (licence, environment, retention) | Yes: `REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` s.0.3 lists classic's fixed fields (`org_owner`, `responsible_steward`, ...) as "classic's `/` form only"; no plan to port | No controls for owning team, steward, geographic location, backup status, purpose, free-text notes. |
| C-06 | Schedule a survey definition or analysis (manual / daily / weekly / monthly) | Card "Schedule" (`_showScheduleForm`, `_submitSchedule`, CL:3148, 3164); `POST /api/schedules/{type}/{slug}` | partial | Only inside the Notify dialog: daily / weekly for an analysis (`openNotifyDialog`, N/app.js:7821) | `ITEM-4-AUTOMATE-IMPLEMENTED.md` (Surveys sub-tab still open) | No standalone schedule control; no monthly or manual; not available for survey definitions. |
| C-07 | Run a scheduled item now | "Run now" (`runScheduleNow`, CL:8106); `POST /api/schedules/{type}/{slug}/{id}/run` | present | Automate, Schedules, `data-run-sched` (`stages/automate.js`) | n/a | |
| C-08 | Notify me when an analysis changes (create subscription) | Card "Notify me" (`_createSubscriptionFromCard`, CL:3131); `POST /api/automate/subscriptions` | present | Questions row "notify me" then `openNotifyDialog` | `ITEM-4-AUTOMATE-IMPLEMENTED.md` | On question rows, not on definition cards. |
| C-09 | List subscriptions, enable / disable, filter to the selected resource | Automate, Subscriptions (`loadAutomateSubscriptionsPanel`, `_toggleSubscription`, CL:9620, 9704) | present | Automate, Subscriptions (`stages/automate.js`) | n/a | |
| C-10 | List all schedules; delete one | Automate, Schedules (`_deleteAutomateSchedule`, CL:9959); `DELETE /api/schedules/{type}/{slug}/{id}` | present | Automate, Schedules (`data-delete-sched`) | n/a | |
| C-11 | See why a scheduled run errored | "error" link (`_showScheduleErrorDetail`, CL:18077) | absent | none (`last_run_activity_id` is not read in `stages/automate.js`) | no note found | |
| C-12 | Automate, Surveys sub-tab (schedule per survey definition for the selected repo) | `loadAutomateSurveysPanel` (CL:9726) | absent | none | Yes: `ITEM-4-AUTOMATE-IMPLEMENTED.md` | Same as R-74. |
| C-13 | Perspective filter on Survey / Questions | Chips | present | Perspective row | n/a | |

---

## 5. Kind-independent chrome

### 5a. Investigations (Classic: header "Investigation" tab, `loadInvestigationsPanel`, CL:10625)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-01 | List investigations (active; include closed) | `GET /api/investigations/?include_closed=` | present | Investigation stage (`stages/investigation.js`) | n/a | |
| G-02 | Create (name, why, kind) | `_createInvestigation` (CL:10931); `POST /api/investigations/` | present | `inv-new` | n/a | |
| G-03 | Make current ("Use") | `_setCurrentInvestigation` (CL:10590) | present | `inv-make-current`; sidebar Investigation selector | n/a | Same localStorage key in both. |
| G-04 | Suspend / resume / close | CL:11082-11103 | present | `inv-suspend`, `inv-reopen`, `inv-close` | n/a | |
| G-05 | Add / remove members across kinds | Members list; `POST/DELETE /api/investigations/{x}/members` | present | `inv-add-member`; per-member remove | n/a | |
| G-06 | Per-member disposition inside an investigation | `GET/POST /api/investigations/{x}/dispositions` | present | Investigation detail | n/a | |
| G-07 | Next-steps offers | `_loadNextSteps` (CL:10883) | present | Investigation detail | n/a | |
| G-08 | Bind to an existing Egeria Project (search) | Picker (`_openInvestigationEgeriaPicker`, CL:10856); `PUT /api/investigations/{x}/egeria-project` | present | `inv-bind` | n/a | |
| G-09 | Create the Egeria Project ("create in Egeria") | `_promoteInvestigation` (CL:10959); `POST /api/investigations/{x}/promote` | present | `inv-promote` | n/a | |
| G-10 | Session-wide "Egeria Project context" badge and picker; per-resource project context required before a publish (HTTP 428 gate) | Header badge (`_openSessionEgeriaProjectPicker`, CL:2408); `/api/project-context/{type}/{slug}`, `/search/candidates` | absent | none | Classic-only design: `discovery-automate-project-context-plan.md`. No /next plan found | /next has no publish gate because it has no publish control (R-42). |

### 5b. RFA drawer

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-20 | Open the drawer; badge count | Header "RFAs" (`toggleRfaDrawer`, CL:5273) | present | Header RFA button (`next/rfa.js`) | n/a | |
| G-21 | List open RFAs, this resource or all, show completed | `loadRfaPanel` (CL:7532) | present | Drawer toggles "this resource only", "show completed" | n/a | |
| G-22 | Defer / reassign / complete / reopen | `submitRfaAction` (CL:7929); `PATCH /api/activity/rfas/{id}` | present | Drawer actions (`updateRfaAction`) | n/a | |
| G-23 | Dismiss as not applicable / won't do, with reason | `_showRfaDismissForm`, `submitRfaDismiss` (CL:7809, 7831); `POST /api/activity/rfas/{id}/dismiss` | absent | Dismissed RFAs are shown with their reason; no way to dismiss | Yes: `ITEM-10-RFA-IMPLEMENTED.md`, "What is explicitly out of scope" | |
| G-24 | Restore a dismissal | `restoreRfaDismissal` (CL:7849); `POST .../dismissals/{id}/clear` | absent | none | Yes: `ITEM-10-RFA-IMPLEMENTED.md` | |
| G-25 | Record a free-text note on an RFA | `saveRfaNote` (CL:7951); `PATCH /api/activity/rfas/{id}/notes` | absent | none (a resolution note is captured on Complete only) | Yes: `ITEM-10-RFA-IMPLEMENTED.md` | |

### 5c. Chat

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-30 | Open chat; ask a question; streamed answer | Header "Chat" (`toggleChatPanel`, `sendQuery`, CL:5259, 14315); `POST /api/query/stream` | present | Ask rail (`next/chat.js`, `askStream`) | `ITEM-9-CHAT-IMPLEMENTED.md` | |
| G-31 | Thumbs up/down on an answer | `recordFeedback` (CL:14558); `POST /api/query/feedback` | present | Rail vote (also posts to `/api/feedback/answer`) | n/a | |
| G-32 | "Compile": what evidence would answer this, and what is missing | Compile button (`compileContext`, CL:13852); `POST /api/context/compile` | partial | "open the compile" on an answered turn (`openCompile`, `chat.js`) | no note found for the pre-ask button (`ASSESSMENT-CHAT.md` s.3(a) built "open the compile") | No standalone pre-ask compile button. |
| G-33 | Run a missing analysis from the compile's gap list | `_runGapAnalysis` (CL:14058) | absent | none | no note found | |
| G-34 | Run or schedule an analysis from a chat answer | `_chatRunAnalysis`, `_chatSubmitSchedule` (CL:11812, 11860) | absent | none | no note found | |
| G-35 | Confirm a suggested alias ("term resolves to repo") | `confirmAlias` (CL:14593); `POST /api/aliases/` | absent | alias suggestion shown as a sentence only (`chat.js:529`) | no note found for the confirm action (`ITEM-9-CHAT-IMPLEMENTED.md` lists only the richer symbol/compare table UI as not built) | |
| G-36 | Chat panel open state remembered | `pe_chat_panel_open` | absent | none | Named, no plan: `INVENTORY-CLASSIC-TO-NEXT.md` s.9 | |

### 5d. Activity

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-40 | View the operation log, expand a row, copy GUIDs | `loadActivityLog` (CL:7317); `GET /api/activity/` | present | Header "Activity" (`stages/activity.js`) | `ITEM-6-ACTIVITY-IMPLEMENTED.md` | |
| G-41 | Advanced filter (entity type, intent, operation, status, since) | Filter form | partial | Text filter + status chips | `ITEM-6-ACTIVITY-IMPLEMENTED.md`, "What I deliberately left out" | |
| G-42 | Clear the (client-side) log | `clearActivityLog` (CL:7502) | absent | none | Yes: same note (deliberate; Classic's clears only a client copy) | |
| G-43 | Unread badge; jump to an entry from a result toast | `_openActivityEntry` (CL:4998) | absent | none | Yes: same note | |

### 5e. Admin (Classic gear menu, eleven panes)

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-50 | Annotation Types: browse, register, edit, delete | CL:18093-18176; `/api/analyses/annotation-types` | present | Admin, Annotation Types (`admin/annotation_types.js`) | `ADMIN-REGISTRIES-IMPLEMENTED.md` | /next adds usage counts before delete. |
| G-51 | Groups: create, delete, group suggestions | `createAdminGroup`, `deleteAdminGroup`, `applyGroupSuggestion` (CL:16662-16716) | present | Admin, Groups (`admin/groups.js`) | `GROUPS-ADMIN-IMPLEMENTED.md` | |
| G-52 | Discovery Sources: list, add (search / list / quick-add), refresh, delete | CL:17617-18062 | present | Admin, Discovery Sources (`admin/discovery_sources.js`) | `DISCOVERY-SOURCES-ADMIN-IMPLEMENTED.md` | Preview-then-apply. |
| G-53 | Question Catalog (browse) | CL:17400 | present | Admin, Question Catalog | `ITEM-5-ADMIN-IMPLEMENTED.md` | /next also writes (section 6). |
| G-54 | Egeria Alignment (resync scan and apply repairs; private-zone card) | CL:17654-17880; `/api/egeria/resync/*` | present | Admin, Egeria Alignment (`admin/resync.js`) | `RECONCILE-ADMIN-IMPLEMENTED.md` | |
| G-55 | Egeria Links: list stale links, resolve one, bulk resolve | CL:17127-17340; `/api/egeria/linkage/*` | absent | Admin tab renders a named-deferral page with a link to Classic (`admin/index.js:73`) | Yes: `ITEM-5-ADMIN-IMPLEMENTED.md` (named deferral) | |
| G-56 | Publish Queue (outbox): list, retry a failed element | CL:16747-16780; `GET /api/outbox/`, `POST /api/outbox/{id}/retry` | absent | Named-deferral page (`admin/index.js:79`) | Yes: `ITEM-5-ADMIN-IMPLEMENTED.md` | |
| G-57 | Repair: rename, change GitHub URL, enable a collection, repoint / drop a membership | CL:16856-17127; `/api/admin/repair/*` | present | Admin, Repair (`admin/repair.js`) | `RECONCILE-ADMIN-IMPLEMENTED.md` | Repository-only in both. |
| G-58 | Prefect: status, flow runs, cancel | CL:17517-17606 | present | Admin, Prefect | `ITEM-5-ADMIN-IMPLEMENTED.md` | |
| G-59 | Feedback list (admin token gate) | CL:15545 | present | Admin, Feedback | `ITEM-5-ADMIN-IMPLEMENTED.md` | |
| G-60 | Logs viewer | CL:15354 | present | Admin, Logs | `ITEM-5-ADMIN-IMPLEMENTED.md` | |

### 5f. Header and shell

| ID | Action | Classic control / route | `/next` status | `/next` location | Covered by existing design note? | Notes |
|---|---|---|---|---|---|---|
| G-70 | Sign in / sign out | Login overlay, logout (`auth.js`) | present | Same `auth.js` overlay; `logout-btn` | n/a | |
| G-71 | Connection details popover (who am I, which Egeria) | `_showWhoamiInfo` (CL:2303); `GET /api/egeria/whoami` | absent | Header shows the user name only | no note found | |
| G-72 | Backend-health banner with "Retry now" | `checkBackendHealth` (CL:1599); `GET /health/ready` | absent | none | no note found | |
| G-73 | Bootstrap banner with "Run bootstrap now" | `_refreshBootstrapBanner`, `_runBootstrapNow` (CL:1635, 1683); `/api/bootstrap/status`, `/run` | absent | none | no note found | |
| G-74 | Feedback button and modal | Draggable button (`openFeedbackModal`, CL:18587); `POST /api/feedback` | present | `next/feedback.js` | `ITEM-8-FEEDBACK-IMPLEMENTED.md` | |
| G-75 | Resource and stage in the URL, restored on load | `_restoreResourceFromUrl` (CL:1725) | present | `readUrl` / `writeUrl` | n/a | |

---

## 6. Reverse direction: what `/next` has that Classic lacks

Checked the other way round: for each `/next` capability below, `index.html` was searched
for its route or vocabulary (counts of zero are noted), so "Classic lacks it" is verified.

| ID | `/next` capability | Where in `/next` | Classic check |
|---|---|---|---|
| X-01 | **Work lists**: save a selection as a work list, a resources-by-questions matrix, run an analysis across a list, narrow, publish to Egeria | `worklist.js`; sidebar `sel-worklist`; `RA.createWorkList`, `enqueueBatch`, `publishWorkList` | no `work-list` anywhere in `index.html` (0 hits) |
| X-02 | **Questions checklist, By analysis and Disposition for databases and filesystems** | `loadPane`, `loadByAnalysisPane`, `loadDispositionPane`, entity-generic routes | Classic gates all three to repos (`_qdTabsApply`, CL:1998; `_loadSurveyResultsPanel('repo', ...)`) |
| X-03 | **By analysis boards**: grouped results, findings, diagrams, relationship graph (Graphviz via Kroki), members rail, "numbers behind this" | `loadByAnalysisPane`, `openMembers` | Classic has the Dashboard (R-55) but no relationship graph (0 hits for `kroki`/`graphviz`) and no members rail |
| X-04 | **Per-answer feedback** (agree / partly / disagree + comment) landing in the gaps collection; chat votes join it | `next/feedback.js`; `RA.submitAnswerFeedback` | `/api/feedback/answer` not used by Classic (0 hits) |
| X-05 | **Disposition journal, records and depth offer**: dated verdict trail, written journal, saved reports with markdown/CSV export, "add to work list / raise RFA / note in journal" on a record, correction of a record, the catalogue-depth offer | `loadDispositionPane`, `renderRecords`, `renderJournalWrite`, `renderDepthOffer` | `/journal`, `/records`, `depth-offer`: 0 hits in Classic |
| X-06 | **Price before you run**: run cost line in the run-choice popover, "Plan a run" dialog with what moved last time, declared-vs-received reconciliation per run, analyses index with price and sort | `openRunChoice`, `planSurveyRun`, `reportPlanMovement`, `declaredVsReceivedHtml`, `renderAnalysesIndexSection` | `/cost` and declared-vs-received: 0 hits |
| X-07 | **Egeria-native surveys** for database and filesystem: run, poll proof-derived state, read report | `stages/native-surveys.js`; `/api/native-surveys` | `native-surveys`: 0 hits in Classic |
| X-08 | **Schema Inventory** with schema classification (system folded; no access / structure only / staging shortfall labels), estimate labels, live filter that force-opens matches | `loadSchemaInventoryPane` | Classic has an accordion (D-26) but no classification, no filter; `schema-inventory`: 0 hits |
| X-09 | **Credential-capability banner** on a database ("connected as X, sees n of m schemas, SELECT on k of m relations") | `resourceHeaderHtml` | `credential_capability`: 0 hits |
| X-10 | **Enrichment Context**: signed, dated judgements and observations with an "evidence moved" review flag; inline answers to the catalog's human questions; documentation sources (declare, probe, recheck, remove) for database and filesystem; the investigation's lens | `stages/context.js`, `stages/enrichment.js`, `RA.getDocSources`... | `doc-sources`: 0 hits |
| X-11 | **Curate review and commit**: component tree, branch selection and verdicts, curation plan, "Catalogue" commit with a run record, catalogue-depth offer | `stages/curate.js`; `RA.getCuratePlan`, `curateCommit` | `curate/commit`, `curate/plan`: 0 hits |
| X-12 | **Investigation**: edit, reclassify, relink members, sync with Egeria, unbind | `stages/investigation.js` (`inv-edit`, `inv-reclassify`, `inv-relink`, `inv-sync`, `inv-unbind`) | `reclassify`, `relink-members`, `sync-egeria`: 0 hits |
| X-13 | **Admin writes Classic lacks**: Question Catalog add/retire (append-only); Annotation Type usage count before delete | `admin/question_catalog.js`, `admin/annotation_types.js` | Classic's Question Catalog is read-only by its own comment |
| X-14 | **Per-analysis run for databases** (`POST /api/databases/{slug}/analyses/{id}/run`) | `RA._runAnalysisPath` | Classic's database analysis Run opens the whole-survey modal |
| X-15 | **Sidebar operations on databases and filesystems**: hide, disposition, group select, bulk delete, bulk scope, work list, filters by disposition | `renderSidebar`, `selectActionsHtml` | Classic's database and filesystem rows have only Survey and Remove |
| X-16 | **Understanding** also offers weekly commits and top committers for repos | `REPO_CHARTS` (`RA`) | Classic has five repo tabs |
| X-17 | **Shell**: text size 100/112/125 %, resizable seams with remembered widths, sidebar drawer under 780 px, "what the marks mean" key, stage numbering | `wireTextSize`, `initSeams`, `wireSidebarDrawer` | none in Classic |
| X-18 | **Run honesty**: the engine that actually ran a definition is persisted and shown on its row, including a Prefect fallback with its reason | `engineNoteHtml` | Classic shows the engine only in the post-run modal |
| X-19 | **Analyses index**: "what it does" popover, "N questions" popover linking to the asking stage, "never run first" / "by cost" sort | `analysisIndexRowHtml` | no equivalent in Classic cards |

---

## 7. Tally, and the five seeded findings checked

### 7a. Counts (statuses read from the tables above)

Tallied by script from the status column of sections 1-5 (one row per Classic action).
A row counts once, under the kind it was listed in; section 4 (C) is the actions Classic
offers identically on all three kinds, and section 5 (G) is kind-independent chrome.

| Kind | Actions | present | partial | absent |
|---|---|---|---|---|
| Repository (R) | 58 | 36 | 9 | 13 |
| Database (D) | 33 | 9 | 9 | 15 |
| Filesystem (F) | 11 | 2 | 3 | 6 |
| All three kinds (C) | 13 | 5 | 3 | 5 |
| Kind-independent chrome (G) | 44 | 27 | 2 | 15 |
| **Total** | **159** | **79** | **26** | **54** |

Reverse direction (section 6): **19** `/next` capabilities that Classic lacks (X-01 to X-19).
These are not counted in the table above; they have no Classic action to be present or
absent against.

**For the 80 rows that are partial or absent, what the design notes say.** Three buckets,
assigned by reading the cited notes, applied to the part that is missing:

- **A, a note records it as a deliberate deferral or a tracked follow-up.**
- **B, design or direction exists, but no note plans to build it** (a spec row, a design
  direction, a backlog entry about a different route to the same end).
- **C, no note found.** Genuinely unplanned as far as the notes show.

| Kind | partial + absent | A | B | C |
|---|---|---|---|---|
| Repository | 22 | 6 | 0 | 16 |
| Database | 24 | 1 | 10 | 13 |
| Filesystem | 9 | 0 | 4 | 5 |
| All three kinds | 8 | 3 | 1 | 4 |
| Chrome | 17 | 8 | 2 | 7 |
| **Total** | **80** | **18** | **17** | **45** |

Reading the table: the database and filesystem gaps are mostly *designed but unscheduled*
(bucket B: the representation specs and the credential direction exist), while the
repository gaps are mostly *not discussed at all* (bucket C). That is a fact about the
notes, not a statement about importance, which this document does not judge.


### 7b. The five findings seeded for this inventory

**1. Survey modal with credentials. Confirmed absent (D-20, D-42).** Classic's modal
(`showSurveyDbModal`/`submitSurveyDb`, `POST /api/databases/{slug}/survey`) takes a
username, password (or reuses the stored one), an Egeria-hybrid toggle and Egeria
URL/server. `/next` has no field that accepts a database credential anywhere; its Survey
Definition run posts only `survey_definition_ref` (`RA.runSurveyDefinition`) and relies on
the stored credential.

**2. Publish and "Re-survey in Egeria". Publish confirmed absent; re-survey partial
(D-21, D-22, R-42, F-06).** Classic's `POST .../publish` pushes RE's latest measured local
snapshot to Egeria and starts a native survey. `/next` has no publish control for any
resource kind. Its "Egeria's own surveys" rows submit an Egeria engine action instead
(same Egeria-side effect as Classic's "Re-survey" when the database is already cataloged;
not the same route, and it does not publish RE's snapshot). "Catalog and Survey" is listed
but locked; `Backlog.md` (2026-09-30) already tracks wiring it. Separately, the
`publish_stale` message at `N/app.js:2892` points the user at a "publish again from the
Analysis pane" control that does not exist.

**3. Survey history with invalid rows. Confirmed: `/next` has no equivalent (D-30).**
`_dbSurveyHistoryTableHtml` / `toggleDbInvalidSurveys` read `GET /api/databases/{slug}/surveys`
(`?include_invalid=true`). `RA` has no wrapper for either, and `include_invalid` appears
nowhere in `next/`. The nearest thing, "Runs on this resource" (`openRunsList`), reads the
activity log (survey operations and their step status), not the registry's survey rows, so
it cannot show schema/table/column counts per run or invalid markers. The commit that added
the Classic view (`b1ef4d1d`) is on main; its note never mentions `/next`.

**4. Register / edit a database; the remove button. Verified against current code (D-08,
D-09, D-10).**

- *Register:* Classic can register a database directly (`POST /api/databases/register`);
  `/next` can only add one via a registered server's Discover. Absent.
- *Edit:* **no edit action exists in Classic either.** A stored credential can be changed
  only through the CLI (`database update-credentials`) or `PATCH /api/databases/{slug}/credentials`.
  Classic's register route refuses an existing slug. So "missing edit" is a gap in both
  UIs, and `Backlog.md` already records two follow-ups on `update-credentials`.
- *Remove:* Classic's sidebar trash works and calls `DELETE /api/databases/{slug}`
  (it swallows a non-OK response silently). In `/next` there are two remove paths and they
  differ. The sidebar's select-mode "delete" works for databases (`removeEntity`). The
  resource header's `remove` button is the broken one: its handler calls `removeProject(slug)`
  whatever the resource type (`N/app.js:3277`), so on a database it sends
  `DELETE /api/projects/{dbslug}` and gets a 404, and on the **Schema Inventory** tab the
  header is drawn but `bindResourceHeader()` is never called (`N/app.js:3402-3418`), so the
  button does nothing at all. Schema Inventory is the tab that was added on 2026-09-27, which
  fits the report. No design note found that records this defect. (The same handler would
  delete a *repo* of the same slug if a database and a repo ever shared one; not tested.)

**5. A failing credential during a Survey Definition run. Partial (D-43).** Classic shows
it in the run modal itself: per-step red statuses, the aggregated errors, a toast and an
Activity entry, with Retry. In `/next`, by code reading (not run): `launchSurvey`
polls the activity entry, then reloads the pane; the definition's row gets a warning glyph
and a button that opens the last-run errors (`lastRunHtml`, `N/app.js:3976`), provided
`last_run_errors` was recorded. The launch note itself does not show the failure.
For a per-analysis run (`renderAnalysesIndexSection`, `rerun`) the finished entry that
`pollActivity` returns is not inspected: only a failure to *start* produces text. After a
failed completion, a Survey & analyses row redraws with an error glyph from `last_run_status`
(no message); a Questions row is simply re-read (`loadAnswer`) and shows its previous state. The header's
credential-capability banner states what the stored credential can see, which is the
design's intended prompt for broader credentials (`REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY.md`
s.7.1), but nothing offers to supply one (D-20, D-42).

### 7c. Two things found that are not in the seeded list

- **Messages in `/next` that describe controls that do not exist.** `stages/curate.js:194-205`
  says tags, feedback and curator notes "are reachable from the resource header regardless of
  type" (C-02 to C-04: they are not reachable anywhere in `/next`); `N/app.js:2892` says
  "publish again from the Analysis pane" (R-42: no such control). Both are on-screen
  statements about capability, the case `RULING-CLASSIC-AND-NEXT.md` s.2 separates from a
  deferral ("a divergence that turns into a false statement"). Recorded here, not changed.
- **Relationship to the earlier inventory.** `INVENTORY-CLASSIC-TO-NEXT.md` (2026-09-17)
  is organised by the owner's ten points and, by its own account, was built from a grep and
  then rebuilt. Several of its "absent" items have since shipped (Admin, Activity, RFA drawer,
  Automate, Investigation, work lists, chat), so its status for those is out of date. This
  document is a per-action inventory; it is not a replacement for that note's judgements.
