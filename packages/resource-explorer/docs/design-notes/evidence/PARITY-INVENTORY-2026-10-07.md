# Classic vs Next parity inventory, 2026-10-07

**Read against** `origin/main` at `03712d7a`. Code read only: no server, Egeria, registry or database was touched. Baseline: `docs/design-notes/CLASSIC-VS-NEXT-PARITY-2026-09-30.md` (read at `fffcc3a0`). Rows only; no recommendations, no design, no ordering beyond the groups.

**Method.** Classic's routes were enumerated mechanically from `index.html` (205 `fetch` call sites, plus the `onclick` handler names). Each route was then looked for in `re-api.js` and `next/`. DONE means Next calls the same route or an equivalent and exposes a control; PARTIAL names what is missing; MISSING means no control or no route call. Rows outside G1-G3 carry forward the 2026-09-30 status with a route-level re-check only, not a full re-read of each Next function. Classic line numbers are omitted because the file changed since the baseline.

**Groups.** G1 = Egeria on a repository. G2 = database registration and credentials, plus generic Curate controls for every resource kind. G3 = database report and Understanding. other = everything else (file-system features are out of scope until G1-G3 are done).

| ID | Feature (Classic wording) | Classic location | Next state | Next location | Writes beyond a read? | Group |
|---|---|---|---|---|---|---|
| PI-001 | Publish survey to Egeria (full or scoped steps; optional zones) | publishSurvey / _publishScopedSteps; POST /api/egeria/{slug}/publish | MISSING | - (no control; app.js resourceHeaderHtml says so in a comment; re-api.js has no wrapper) | Egeria | G1 |
| PI-002 | Egeria tab: registered?, GUID, survey-history table, expand annotations | showEgeria / renderEgeriaPanel; GET /api/egeria/{slug}/status | PARTIAL | next/app.js resourceHeaderHtml (publish-state line only); missing: GUID, history table, annotations | none | G1 |
| PI-003 | List the repo's Egeria SurveyReports, refresh, expand annotations | loadEgeriaSurveys / toggleAnnotations; GET /api/egeria/{slug}/egeria-surveys, /{guid}/annotations | MISSING | - (native-surveys.js serves database/file system only) | none | G1 |
| PI-004 | Catalog selected file types as Egeria elements | catalogSelected; POST /api/egeria/{slug}/catalog-elements | MISSING | - | Egeria | G1 |
| PI-005 | Reset cached Egeria GUIDs and survey history | resetEgeria; POST /api/egeria/{slug}/reset | MISSING | - | registry (clears cached GUIDs and history) | G1 |
| PI-006 | Project-context gate before publish (session badge, per-resource picker, HTTP 428 handling) | _ensureEgeriaProjectContext, _openEgeriaProjectPicker, _setEgeriaProjectContext; /api/project-context/{type}/{slug} | MISSING | - (no wrapper in re-api.js; no publish control for it to gate) | registry | G1 |
| PI-007 | Egeria Project context: bind investigation to an existing Egeria Project | _postEgeriaProjectContext; PUT /api/investigations/{x}/egeria-project | DONE | next/stages/investigation.js inv-bind handler | registry (and Egeria binding record) | G1 |
| PI-008 | "Ask about this" prefill chat from the survey report | askAboutSurvey | MISSING | - | none | G1 |
| PI-009 | Scoped publish of selected analysis steps after a run | _publishScopedSteps; POST /api/egeria/{slug}/publish with steps | MISSING | - (Curate repo commit, next/stages/curate.js curateCommit, writes catalog entities by a different route, /curate/commit) | Egeria | G1 |
| PI-010 | Register a database server (Test, then Register) | showRegisterServerModal / submitRegisterServer, testServerConnectionModal; POST /api/db-servers/register, /_test-inline | DONE | next/db-server-discovery.js submitRegister, testInline | registry (stores the credential) | G2 |
| PI-011 | Test a registered server | testServerConnection; POST /api/db-servers/{slug}/test | DONE | next/db-server-discovery.js runServerTest | none | G2 |
| PI-012 | Discover databases on a server and add selected | showDiscoverModal / addSelectedDatabases; POST /api/db-servers/{slug}/discover, /add-database | DONE | next/db-server-discovery.js runSource, confirmAdd, discoverInline | registry | G2 |
| PI-013 | Remove a server (and its linked databases) | removeServer; DELETE /api/db-servers/{slug} | DONE | next/db-server-discovery.js removeServerRow | registry | G2 |
| PI-014 | Server detail page (connection, Egeria fields, databases and survey state) | selectServer / loadServerDetail | PARTIAL | next/db-server-discovery.js savedHtml; missing: Egeria URL/view server/user fields, per-database surveyed state | none | G2 |
| PI-015 | Register ONE database directly (slug, host, port, database, group, credentials, Egeria fields) | showRegisterDbModal / submitRegisterDb; POST /api/databases/register | MISSING | - (databases arrive only by Discover on a server or a CSV row with server or connection_ref, db-server-discovery.js confirmFile; no wrapper for /databases/register) | registry (stores the credential) | G2 |
| PI-016 | Credential override for a run (username/password, remember for session) | submitSurveyDb (survey-db-user/pwd), submitSurveyDefinitionRun (survey-def-run-user/pwd) | MISSING | - (re-api.js runSurveyDefinition posts only survey_definition_ref; credential.js only marks an unreadable stored credential) | none (credential used for the run) | G2 |
| PI-017 | Survey a database from a modal (whole-database survey, POST /api/databases/{slug}/survey) | showSurveyDbModal / submitSurveyDb | MISSING | - (a database survey runs as a Survey Definition, app.js launchSurvey, stored credential) | registry (survey rows) | G2 |
| PI-018 | Hybrid "Try Egeria first" with Egeria URL, server and confirm dialog | submitSurveyDb hybrid fields + confirm | MISSING | - | Egeria (reads and may publish) | G2 |
| PI-019 | "Catalog now, then retry" after a survey definition fails on an uncataloged database | catalogAndRetrySurveyDefinition; POST /api/catalogue-scope/{slug}/commit (since the 2026-10 change; was /databases/{slug}/publish) | PARTIAL | next/stages/curate-scope.js (Catalog button) and re-api.js postCatalogueCommit; missing: the retry link on a failed run and the auto-offer | Egeria | G2 |
| PI-020 | Show a failing credential correctly (per-step error, retry) | submitSurveyDefinitionRun result panel | PARTIAL | next/app.js launchSurvey, lastRunHtml, openRunsList; error is on the reloaded row, not in the launch note | none | G2 |
| PI-021 | Edit a stored database credential | none in Classic (PATCH /api/databases/{slug}/credentials exists, no Classic control) | MISSING | - (also not in Classic; listed because the owner asked about credentials) | registry | G2 |
| PI-022 | Register a filesystem | showRegisterFsModal / submitRegisterFs; POST /api/filesystems/ | MISSING | next/app.js FIND_TITLE stub opens a link to the current UI | registry | other |
| PI-023 | Assign the selected resource to a group (Curate, General) | saveCurateGroup; POST /api/projects/{slug}/group | DONE | next/stages/curate-bands.js renderFindableBand (assignGroup) | registry | G2 |
| PI-024 | Add / remove a tag (any kind) | addCurateTag, removeCurateTag; POST/DELETE /api/curate/tags/{type}/{slug} | DONE | next/stages/curate-bands.js renderFindableBand (addCurateTag, removeCurateTag) | registry | G2 |
| PI-025 | Tag autocomplete from all tags | loadCurateGeneralPanel; GET /api/curate/tags | DONE | next/stages/curate-bands.js (getCurateAllTags) | none | G2 |
| PI-026 | Resource feedback: rating and category (any kind) | addCurateFeedback; POST /api/curate/feedback/{type}/{slug} | DONE | next/stages/curate-bands.js renderRatingsBand (addCurateFeedback) | registry | G2 |
| PI-027 | Curator notes: add | addCurateNote; POST /api/curate/notes/{type}/{slug} | PARTIAL | next/stages/curate-bands.js renderNotesBand writes to the signed journal (writeJournal, /api/journal/...), not /curate/notes; no add into the Classic notes list | registry (journal) | G2 |
| PI-028 | Curator notes: list, delete | deleteCurateNote; DELETE /api/curate/notes/{id} | DONE | next/stages/curate-bands.js renderClassicNotes (read, delete unsigned only) | registry | G2 |
| PI-029 | Group assign, per-row control in the sidebar | openAssignGroupModal / submitAssignGroup | PARTIAL | next/admin/groups.js and curate-bands.js; no per-row sidebar control | registry | G2 |
| PI-030 | Database report header: last surveyed and source badge | loadDbSurveyReport header | PARTIAL | next/app.js resourceHeaderHtml (surveyed N ago); no source badge | none | G3 |
| PI-031 | Metric cards: schemas, tables, columns | loadDbSurveyReport | PARTIAL | next/app.js loadSchemaInventoryPane per-schema counts; no three-number card | none | G3 |
| PI-032 | Schema and tables accordion with column details | toggleSchemaBlock / toggleTableBlock | PARTIAL | next/app.js loadSchemaInventoryPane; missing: default value column and foreign-key target | none | G3 |
| PI-033 | Chart: Schemas (tables and columns per schema) | showDbChart schema_distribution; GET /api/stats/databases/{slug}/schema_distribution | DONE | next/stages/understanding.js renderDatabaseUnderstanding (getDbChart) | none | G3 |
| PI-034 | Chart: Tables (largest by rows or size) | showDbChart table_sizes | DONE | next/stages/understanding.js (getDbChart, drawSizes) | none | G3 |
| PI-035 | Chart: Column types | showDbChart column_types | DONE | next/stages/understanding.js (getDbChart) | none | G3 |
| PI-036 | Top tables ranked table (rows, size, last analyzed, pending changes) | loadDbSurveyReport top tables block (Plotly) | PARTIAL | next/stages/understanding.js tableSizesExtra; chart only, no ranked table with last analyzed or pending changes (not found in code) | none | G3 |
| PI-037 | Survey-history table with show-invalid toggle (invalid rows muted with reason) | _dbSurveyHistoryTableHtml / toggleDbInvalidSurveys; GET /api/databases/{slug}/surveys[?include_invalid=true] | MISSING | - (no wrapper for /surveys; nearest is app.js openRunsList, which reads the activity log) | none | G3 |
| PI-038 | Schema-metric trend chart (schemas, tables, columns over runs) | loadDbSurveyHistoryChart; GET /api/stats/databases/{slug}/survey_history | DONE | next/stages/understanding.js (getDbChart survey_history, table_growth) | none | G3 |
| PI-039 | Changes since last run banner (database) | loadDbDiffBanner; GET /api/databases/{slug}/diff | DONE | next/stages/understanding.js sinceLastRunHtml, getDatabaseDiff | none | G3 |
| PI-040 | Views and Lineage sub-tab (views, complexity, SQL, dependencies, lineage flowchart) | switchDbSubTab('views'), renderAllDbViewsMermaid, buildMermaidLineageForView | MISSING | - | none | G3 |
| PI-041 | Active classification rules (Data Classes) block | toggleActiveRulesBlock; GET /api/egeria/rules/dataclasses | MISSING | - | none | G3 |
| PI-042 | Egeria SurveyReports for a database: list, refresh, expand annotations | loadEgeriaSurveys (database); GET /api/databases/{slug}/egeria-surveys | PARTIAL | next/stages/native-surveys.js openReport; only reports of runs RE launched; no list, refresh, grouping | none | G3 |
| PI-043 | Run a Survey Definition on a database | showSurveyDefinitionRunModal / submitSurveyDefinitionRun; POST /api/survey-definitions/database/{slug}/run | DONE | next/app.js planSurveyRun, launchSurvey; re-api.js runSurveyDefinition | registry (and Egeria when the definition publishes) | G3 |
| PI-044 | List Survey Definitions that suit a resource | _loadSurveyPanel; GET /api/survey-definitions/{type}/{slug}/candidates | DONE | next/app.js loadSurveyPane (getSurveyCandidates) | none | G3 |
| PI-045 | Engine choice (Prefect or local) in the run modal | showSurveyDefinitionRunModal engine select | MISSING | - (engine shown after the run, app.js engineNoteHtml) | none | G3 |
| PI-046 | After a run: step list, report GUID, link to Egeria catalog, annotations | submitSurveyDefinitionRun result panel | PARTIAL | next/app.js openRunsList (steps, declared vs received); no Egeria link, no inline annotations | none | G3 |
| PI-047 | Understanding charts for a repository: Stars, Commits, Languages, Health, File types | showChart; GET /api/stats/{slug}/charts/{kind} | DONE | next/stages/understanding.js loadChartsPane (REPO_CHARTS, getChart) | none | G3 |
| PI-048 | Repository survey-history trend chart | loadRepoSurveyHistoryChart | DONE | next/stages/understanding.js loadChartsPane (survey_history) | none | G3 |
| PI-049 | Changes since last run banner (repository) | loadRepoDiffBanner; GET /api/egeria/{slug}/diff | MISSING | - | none | G3 |
| PI-050 | Scouting overview tiles (Website, Language, Stars, Forks, Contributors, Last pushed, Size, Security, Deployments) | renderScoutingOverview; GET /api/projects/{slug}/scouting-overview | PARTIAL | next/app.js resourceHeaderHtml (getScoutingOverview: homepage and publish fields only) | none | other |
| PI-051 | Scouting signal tile row | _loadScoutingOverviewStats; GET /survey-results/summary?phase=scouting | PARTIAL | next/app.js loadByAnalysisPane (same measurements as boards; no tile row) | none | other |
| PI-052 | Stale Egeria link banner with three repair choices | _egeriaLinkActionsHtml, _resolveEgeriaLink; POST /api/egeria/linkage/{type}/{slug}/resolve | PARTIAL | next/app.js resourceHeaderHtml shows the warning; no repair choices | none | other |
| PI-053 | Full survey report (metrics, file types, data files, dependencies) | loadSurveyReport; GET /api/egeria/{slug}/survey-report | PARTIAL | next/app.js loadByAnalysisPane (same measurements as boards); no report view | none | other |
| PI-054 | Questions checklist for a stage | loadScoutingQuestions; GET /scouting-questions | DONE | next/app.js loadPane | none | other |
| PI-055 | Answer a question in chat (row click) | _answerQuestionInChat | MISSING | - | none | other |
| PI-056 | "Why is this question here" disclosure | _toggleDerivation | PARTIAL | next/app.js provenanceLine (names answering analyses; no perspective/purpose disclosure) | none | other |
| PI-057 | Set disposition (six values, optional reason) | _setProjectDisposition; POST /api/discovery/disposition | DONE | next/app.js resourceHeaderHtml disposition control, loadDispositionPane (setDisposition) | registry | other |
| PI-058 | Disposition history | loadScoutingDispositionView; GET /disposition-history | DONE | next/app.js loadDispositionPane | none | other |
| PI-059 | Stage Dashboard (trend charts, grouped result cards) | _loadSurveyResultsPanel; GET /survey-results | DONE | next/app.js loadByAnalysisPane | none | other |
| PI-060 | Open one analysis's latest results and trend | toggleAnalysisResults; GET /analyses/{id}/results, /trend | DONE | next/app.js loadByAnalysisPane, openMeasurementDetail | none | other |
| PI-061 | Run one analysis | runAnalysisCatalogCard; POST /api/projects/{slug}/analyses/{id}/run | DONE | next/app.js rerun (re-api.js runAnalysis) | registry (results rows; Egeria if the analysis publishes) | other |
| PI-062 | Run in the background (do not wait for publish) | runAnalysisCatalogCard publish=background | DONE | next/app.js openRunChoice | registry | other |
| PI-063 | "Already up to date": Run anyway (force) | _handleFreshnessSkip; ?force=true | MISSING | - (re-api.js runAnalysis has no force; no skipped handling) | registry | other |
| PI-064 | Prerequisite proposal accept / decline | acceptPrerequisiteProposal; POST /api/prerequisites/run | DONE | next/app.js checkPrerequisitePlan, acceptPrerequisiteProposal | registry | other |
| PI-065 | Run all surveys for a stage | _runStageBatch; POST /analyses/stage/{stage}/run | MISSING | - (work lists run one analysis across many resources, the transpose) | registry | other |
| PI-066 | Sub-resources: run survey, filter, select, catalog, scoped analysis, uncatalog | loadAnalysisSubResourcesView, _runSubResourceSurvey, _submitSubResourceCatalog, _runScopedAnalysis | DONE | next/stages/analysis.js mountSubResourcePanel, submitSubResourceCatalog, loadScopedResults | registry (and Egeria when the catalog box publishes) | other |
| PI-067 | Architecture diagram in results | renderPendingArchDiagrams; POST /api/diagrams/mermaid | DONE | next/app.js (mermaidForKroki), next/stages/curate.js | none | other |
| PI-068 | Accept / reject / retype a recovered component, with a note | _archSubmitVerdict, _archSubmitRetype; POST /curate/component-verdicts/repo/{slug} | DONE | next/stages/curate.js postBranchVerdicts | registry | other |
| PI-069 | Accept / reject a blueprint | _archSubmitBlueprintVerdict; POST /curate/blueprint-verdicts/repo/{slug} | DONE | next/stages/curate.js postBlueprintVerdict | registry | other |
| PI-070 | Publish missing components and accept blueprint | _curatePublishMissingComponents | PARTIAL | next/stages/curate.js postBlueprintVerdict (accepts and queues; no pre-step publishing unmet components) | Egeria | other |
| PI-071 | Jump between blueprint and its components | _curateJumpToComponent / _curateJumpToBlueprint | MISSING | - (curate.js section nav is page-level anchors) | none | other |
| PI-072 | Component search / structural toggle / perspective view | _curateSetComponentSearch, _archToggleStructural, _archShowPerspective | PARTIAL | next/stages/curate.js component tree (getComponentTree, getComponentLeaves); component search and structural toggle: no match in curate.js | none | other |
| PI-073 | Schedule a survey for one repo (Automate, Surveys sub-tab) | _saveSurveySchedule; POST /api/schedules/repo/{slug} | MISSING | - (stages/automate.js has no Surveys sub-tab) | registry | other |
| PI-074 | Perspective filter on Survey / Questions | loadPerspectiveChips, togglePerspective | DONE | next/app.js perspective row | none | other |
| PI-075 | Search GitHub for repos with filters | searchScoutRepos; POST /api/discovery/search | DONE | next/discovery-import.js runSearch | none | other |
| PI-076 | Load a repo list from file or URLs | _loadRepoListFromFile; POST /api/discovery/from-list | DONE | next/discovery-import.js loadFromText | none | other |
| PI-077 | Download inventory CSV | _downloadInventory | DONE | next/discovery-import.js downloadInventory | none | other |
| PI-078 | Import selected candidates into a group | importSelectedScoutRepos; POST /api/discovery/import | DONE | next/discovery-import.js importSelected | registry | other |
| PI-079 | Set a disposition on a candidate before import | _setScoutDisposition | DONE | next/discovery-import.js setRowDisposition | registry | other |
| PI-080 | Run a saved discovery source | runDiscoverySource | DONE | next/admin/discovery_sources.js doRun | registry | other |
| PI-081 | Quick-add a foundation list as a source | _quickAddListSource | DONE | next/admin/discovery_sources.js doQuickAdd | registry | other |
| PI-082 | Save current search as a source | _saveCurrentSearchAsSource | DONE | next/admin/discovery_sources.js doSaveSearch | registry | other |
| PI-083 | Pre-filter the search by foundation | _applyFoundationPrefilter; GET /api/discovery/foundations | MISSING | - | none | other |
| PI-084 | Edit or reset the GitHub API base URL | _saveGithubSource; /api/discovery/github-base-url | MISSING | - (admin/discovery_sources.js header names it as not ported) | registry | other |
| PI-085 | Refresh / delete a discovery source | refreshAdminDiscoverySource, deleteAdminDiscoverySource | DONE | next/admin/discovery_sources.js doRefresh, doDelete | registry | other |
| PI-086 | Remove one database | removeDatabase; DELETE /api/databases/{slug} | DONE | next/app.js commitResourceRemoval, bulkRemove (removeEntity) | registry | other |
| PI-087 | Sidebar per-row Survey button (database, file system) | showSurveyDbModal / showSurveyFsModal row buttons | MISSING | - | registry | other |
| PI-088 | Database list and chip | loadDatabases; setResourceTypeFacet('db') | DONE | next/app.js renderSidebar | none | other |
| PI-089 | Filesystem list and chip | loadFilesystems | DONE | next/app.js renderSidebar | none | other |
| PI-090 | Remove a filesystem | removeFilesystem; DELETE /api/filesystems/{slug} | DONE | next/app.js commitResourceRemoval, bulkRemove | registry | other |
| PI-091 | Run a directory walk and profile (optional hybrid with Egeria) | submitSurveyFs; POST /api/filesystems/{slug}/survey | PARTIAL | next/app.js launchSurvey (filesystem Survey Definition); no walk modal, no Egeria fields | registry | other |
| PI-092 | Publish / re-publish a filesystem to Egeria | submitPublishFs; POST /api/filesystems/{slug}/publish | MISSING | - | Egeria | other |
| PI-093 | Check filesystem reachability from the Egeria engine host | checkFsReachability; POST /filesystems/{slug}/reachability | MISSING | - | Egeria (reads through the engine host) | other |
| PI-094 | Filesystem report: metrics, classification, data-file types | loadFilesystemSurveyReport | MISSING | - (answers appear as Questions rows) | none | other |
| PI-095 | Data file profile: per-file column profile | loadFilesystemSurveyReport profile accordion | MISSING | - | none | other |
| PI-096 | Egeria SurveyReports for a filesystem | loadEgeriaSurveys (filesystem) | PARTIAL | next/stages/native-surveys.js openReport (runs RE launched only) | none | other |
| PI-097 | List and run a Survey Definition on a filesystem | _loadSurveyPanel, submitSurveyDefinitionRun | DONE | next/app.js loadSurveyPane, launchSurvey | registry | other |
| PI-098 | Context form: environment, sensitivity, owning team, steward, location, backup, purpose, notes | loadContextPanel / saveContextForm; POST /api/context/{type}/{slug} | PARTIAL | next/stages/context.js, next/stages/enrichment.js (saveEnrichmentField); no controls for owning team, steward, location, backup, purpose, free-text notes | registry | other |
| PI-099 | Schedule an analysis (manual, daily, weekly, monthly) | _showScheduleForm / saveSchedule; POST /api/schedules/{type}/{slug} | PARTIAL | next/app.js openNotifyDialog (daily, weekly only, inside Notify) | registry | other |
| PI-100 | Toggle a schedule enabled | toggleScheduleEnabled | MISSING | - (automate.js has run-now and delete only) | registry | other |
| PI-101 | Run a scheduled item now | runScheduleNow | DONE | next/stages/automate.js renderSchedules (data-run-sched) | registry | other |
| PI-102 | List all schedules; delete one | loadAutomateSchedulesPanel, _deleteAutomateSchedule | DONE | next/stages/automate.js renderSchedules | registry | other |
| PI-103 | Why a scheduled run errored | _showScheduleErrorDetail | MISSING | - (automate.js shows the error glyph only) | none | other |
| PI-104 | Notify me when an analysis changes | _createSubscriptionFromCard; POST /api/automate/subscriptions | DONE | next/app.js openNotifyDialog | registry | other |
| PI-105 | List subscriptions, enable/disable, filter | loadAutomateSubscriptionsPanel, _toggleSubscription | DONE | next/stages/automate.js renderSubscriptions | registry | other |
| PI-106 | List / create / make current investigations | loadInvestigationsPanel, _createInvestigation, _setCurrentInvestigation | DONE | next/stages/investigation.js renderList, openCreateDialog | registry | other |
| PI-107 | Suspend / resume / close investigation | _suspendInvestigation, _reopenInvestigation, _closeInvestigation | DONE | next/stages/investigation.js (inv-suspend, inv-reopen, inv-close) | registry | other |
| PI-108 | Add / remove members across kinds; per-member disposition; next steps | members, dispositions, _loadNextSteps | DONE | next/stages/investigation.js bindDetail | registry | other |
| PI-109 | Create the Egeria Project from an investigation (promote) | _promoteInvestigation; POST /api/investigations/{x}/promote | DONE | next/stages/investigation.js inv-promote | Egeria (creates a Project) | other |
| PI-110 | Session-wide Egeria Project badge and picker | _openSessionEgeriaProjectPicker | MISSING | - | registry | other |
| PI-111 | RFA drawer: open, badge, list, defer, reassign, complete, reopen | toggleRfaDrawer, loadRfaPanel, submitRfaAction; PATCH /api/activity/rfas/{id} | DONE | next/rfa.js (updateRfaAction) | registry | other |
| PI-112 | RFA dismiss (not applicable / won't do, with reason) | submitRfaDismiss; POST /rfas/{id}/dismiss | MISSING | - (rfa.js header lists it as out of scope) | registry | other |
| PI-113 | Restore an RFA dismissal | restoreRfaDismissal | MISSING | - | registry | other |
| PI-114 | Free-text note on an RFA | saveRfaNote; PATCH /rfas/{id}/notes | MISSING | - | registry | other |
| PI-115 | Chat: ask, streamed answer, thumbs | sendQuery / recordFeedback; POST /api/query/stream, /query/feedback | DONE | next/chat.js askStream, vote | registry (feedback row) | other |
| PI-116 | Compile: what evidence would answer this (pre-ask button) | compileContext; POST /api/context/compile | PARTIAL | next/chat.js openCompile (after an answer only) | none | other |
| PI-117 | Run a missing analysis from the compile gap list | _runGapAnalysis | MISSING | - | registry | other |
| PI-118 | Run or schedule an analysis from a chat answer | _chatRunAnalysis, _chatSubmitSchedule | MISSING | - | registry | other |
| PI-119 | Confirm a suggested alias | confirmAlias; POST /api/aliases/ | MISSING | - (suggestion shown as a sentence only) | registry | other |
| PI-120 | Chat panel open state remembered | pe_chat_panel_open | MISSING | - | none | other |
| PI-121 | View the operation log, expand a row, copy GUIDs | loadActivityLog; GET /api/activity/ | DONE | next/stages/activity.js | none | other |
| PI-122 | Activity advanced filter (entity type, intent, operation, status, since) | loadActivityLog filter form | PARTIAL | next/stages/activity.js (text filter and status chips only) | none | other |
| PI-123 | Activity "Clear" button (clearActivityLog empties only the in-browser list; it does not delete persisted entries) | clearActivityLog | MISSING | - (next/stages/activity.js has no clear control; its header says this is deliberate) | none (client-side only in Classic) | other |
| PI-124 | Activity unread badge; jump to an entry from a toast | _openActivityEntry | MISSING | - | none | other |
| PI-125 | Admin: Annotation Types browse, register, edit, delete | loadAnnotationTypes, saveAnnotationType, deleteAnnotationType | DONE | next/admin/annotation_types.js | registry | other |
| PI-126 | Admin: Groups create, delete, suggestions | createAdminGroup, deleteAdminGroup, applyGroupSuggestion | DONE | next/admin/groups.js | registry | other |
| PI-127 | Admin: Discovery Sources | loadAdminDiscoverySourcesPanel | DONE | next/admin/discovery_sources.js renderDiscoverySources | registry | other |
| PI-128 | Admin: Question Catalog | loadAdminQuestionCatalogPanel | DONE | next/admin/question_catalog.js | registry (Next adds add/retire) | other |
| PI-129 | Admin: Egeria Alignment (resync scan and apply; private zone) | loadAdminResyncPanel, _applyResync | DONE | next/admin/resync.js renderResync | Egeria | other |
| PI-130 | Admin: Egeria Links (list stale, resolve, bulk resolve) | loadAdminEgeriaLinksPanel, _runEgeriaBulkAction | MISSING | next/admin/index.js (named deferral page) | Egeria | other |
| PI-131 | Admin: Publish Queue (outbox list, retry) | loadAdminOutboxPanel, retryOutboxElement; POST /api/outbox/{id}/retry | MISSING | next/admin/index.js (named deferral page) | Egeria (retry republishes) | other |
| PI-132 | Admin: Repair (rename, GitHub URL, enable collection, repoint, drop membership) | loadAdminRepairPanel, submitRepair* | DONE | next/admin/repair.js | registry (and Egeria for collection enable) | other |
| PI-133 | Admin: Prefect status, flow runs, cancel | loadAdminPrefectPanel, cancelAdminPrefectFlowRun | DONE | next/admin/prefect.js | none (cancel stops a flow run) | other |
| PI-134 | Admin: Feedback list | loadAdminFeedbackPanel | DONE | next/admin/feedback.js | none | other |
| PI-135 | Admin: Logs viewer | loadAdminLogsPanel | DONE | next/admin/logs.js | none | other |
| PI-136 | Sign in / sign out | auth.js overlay | DONE | next/index.html (same auth.js), logout-btn | none | other |
| PI-137 | Connection details popover (who am I, which Egeria) | _showWhoamiInfo; GET /api/egeria/whoami | MISSING | - (header shows user name only, app.js #whoami) | none | other |
| PI-138 | Backend-health banner with Retry | checkBackendHealth; GET /health/ready | MISSING | - | none | other |
| PI-139 | Bootstrap banner with Run bootstrap now | _refreshBootstrapBanner, _runBootstrapNow; /api/bootstrap/* | MISSING | - | Egeria (bootstrap writes) | other |
| PI-140 | Feedback button and modal | openFeedbackModal / submitFeedback; POST /api/feedback | DONE | next/feedback.js | registry | other |
| PI-141 | Resource and stage in URL, restored on load | _restoreResourceFromUrl | DONE | next/app.js readUrl, writeUrl | none | other |

## 1. Counts

| Group | DONE | PARTIAL | MISSING | Total |
|---|---|---|---|---|
| G1 | 1 | 1 | 7 | 9 |
| G2 | 9 | 5 | 5 | 19 |
| G3 | 9 | 6 | 5 | 20 |
| other | 50 | 13 | 30 | 93 |
| All | 69 | 25 | 47 | 141 |

## 2. Next-only features with no Classic counterpart

- Work lists: save a selection, resources-by-questions matrix, run across a list, narrow, publish (next/worklist.js; /api/work-lists/*; the publish route writes to Egeria)
- Catalogue scope for a database: declare per-schema and per-table choices, depth, bulk choice, name filter (next/stages/curate-scope.js; /api/catalogue-scope/*; registry only)
- Catalogue commit for a database: manifest, per-node proof steps, resume after reload, commit read-back, hard block on archive and delete of a database tree (next/stages/curate-scope.js; writes to Egeria). Classic's database publish modal was retired for it
- Curate repository plan and commit (what it is, what it holds, what it is made of, manifest) and catalogue-depth offer (next/stages/curate.js; /curate/plan, /curate/commit)
- Egeria-native surveys for databases and file systems: run, poll proof-derived state, read report (next/stages/native-surveys.js)
- Schema Inventory with schema classification, estimate labels and live filter (next/app.js loadSchemaInventoryPane)
- Credential-capability banner on a database (next/app.js resourceHeaderHtml)
- Find databases dialog: three tabs, one candidate table, CSV in and out for databases (next/db-server-discovery.js)
- Resource menu, remove panel, grouped Select bar, add-to-investigation acts and investigation picker (next/app.js, next/investigation-picker.js)
- Resizable table columns and remembered seam widths (next/colresize.js, next/app.js initSeams); text size 100/112/125 percent (wireTextSize)
- Per-answer feedback with comment landing in the gaps collection (next/feedback.js)
- Disposition journal, records, depth offer (next/app.js loadDispositionPane, renderJournalWrite)
- Price before you run, Plan a run dialog, declared-vs-received per run (next/app.js openRunChoice, planSurveyRun, openRunsList)
- Enrichment Context: signed judgements and observations with evidence-moved flag, documentation sources, the investigation lens (next/stages/context.js, enrichment.js)
- Investigation edit, reclassify, relink members, sync with Egeria, unbind (next/stages/investigation.js)
- Admin writes Classic lacks: Question Catalog add/retire, annotation-type usage count (next/admin/*)
- Per-analysis run for databases and file systems (re-api.js runAnalysis entity-generic path)
- Database Understanding extras: table_growth chart, credential-scope notes on every chart (next/stages/understanding.js)
- Repository charts Commits by week and Top committers (next/stages/understanding.js REPO_CHARTS)
- Analyses index with cost, 'what it does' and 'N questions' popovers, sort (next/app.js renderAnalysesIndexSection)
- Engine that actually ran, persisted on the row (next/app.js engineNoteHtml)

## 3. Classic features that write to Egeria or the registry

The Alpha demo criterion is "no path that writes beyond intent". One line each, with the route. Reads are not listed.

- Publish survey to Egeria (scoped steps): `POST /api/egeria/{slug}/publish` (Egeria)
- Catalog selected file types as Egeria elements: `POST /api/egeria/{slug}/catalog-elements` (Egeria)
- Reset cached GUIDs and survey history: `POST /api/egeria/{slug}/reset` (registry)
- Project-context gate writes (set, bind): `POST /api/project-context/{type}/{slug}; PUT /api/investigations/{x}/egeria-project` (registry)
- Register server / register database / add discovered database: `POST /api/db-servers/register, /api/databases/register, /api/db-servers/{slug}/add-database` (registry (stores credentials))
- Whole-database survey with credential override and hybrid mode: `POST /api/databases/{slug}/survey` (registry; Egeria when hybrid)
- Catalog now, then retry (queues the catalogue commit from the stored scope): `POST /api/catalogue-scope/{slug}/commit` (Egeria)
- Run a Survey Definition on repo, database or file system: `POST /api/survey-definitions/{type}/{slug}/run` (registry; Egeria when the definition publishes)
- Run one analysis, run all for a stage, scoped analysis: `POST /api/projects/{slug}/analyses/{id}/run, /analyses/stage/{stage}/run, /sub-resources/analyses/{id}/run` (registry; Egeria when auto-publishing)
- Catalog sub-resources (with publish checkbox): `POST /api/projects/{slug}/sub-resources/catalog` (registry; Egeria when ticked)
- Component and blueprint verdicts, publish missing components: `POST /api/curate/component-verdicts/repo/{slug}, /blueprint-verdicts/repo/{slug}` (registry; Egeria for publish-missing)
- Tags, feedback, curator notes, group: `POST/DELETE /api/curate/tags|feedback|notes/{type}/{slug}; POST /api/projects/{slug}/group` (registry)
- Context form: `POST /api/context/{type}/{slug}` (registry)
- Disposition: `POST /api/discovery/disposition` (registry)
- Hide, bulk delete, unregister: `POST /api/discovery/working-set; DELETE /api/projects|databases|filesystems|db-servers/{slug}` (registry (deletes local data))
- Discovery import, sources, GitHub base URL: `POST /api/discovery/import, /sources, /github-base-url` (registry)
- Investigation create, members, close, suspend, reopen: `POST/DELETE /api/investigations/...` (registry)
- Investigation promote (create Egeria Project): `POST /api/investigations/{x}/promote` (Egeria)
- Schedules and subscriptions: `POST/DELETE /api/schedules/..., /api/automate/subscriptions` (registry)
- RFA defer, reassign, complete, dismiss, restore, note: `PATCH/POST /api/activity/rfas/...` (registry)
- File system register, survey, publish, reachability: `POST /api/filesystems/, /{slug}/survey, /{slug}/publish, /{slug}/reachability` (registry; Egeria for publish and reachability)
- Egeria Alignment apply: `POST /api/egeria/resync/apply` (Egeria)
- Egeria Links resolve (single and bulk): `POST /api/egeria/linkage/{type}/{slug}/resolve` (Egeria)
- Publish Queue retry: `POST /api/outbox/{id}/retry` (Egeria)
- Admin Repair (rename, GitHub URL, collection enable, repoint, drop): `POST/DELETE /api/admin/repair/repos/{slug}/...` (registry; Egeria for collection enable)
- Admin Annotation Types, Groups, Prefect cancel, bootstrap run: `POST/DELETE /api/analyses/annotation-types, /api/projects/groups, /api/prefect/flow-runs/{id}/cancel, POST /api/bootstrap/run` (registry; Egeria for bootstrap)
- Feedback, alias confirm, chat feedback: `POST /api/feedback, /api/aliases/, /api/query/feedback` (registry)
- Prerequisite run: `POST /api/prerequisites/run` (registry)

## 4. What this inventory could not determine from code alone

- Whether any Next control behaves correctly at runtime; nothing was run. Every state is from reading code paths.
- Rows outside G1-G3 were re-checked at route level against `re-api.js` and `next/`, not re-read function by function; their states mostly carry forward the 2026-09-30 baseline.
- Whether Curate offers component search or the structural toggle under other names (grep of curate.js found none; that row is PARTIAL on that basis).
- Whether the top-tables ranked table exists anywhere in Next beyond the chart (searched for last-analyzed and pending-change fields; none found).
- Which Classic routes the backend still serves but Classic no longer calls: `POST /api/databases/{slug}/publish` is no longer called from Classic (its modal was retired).
