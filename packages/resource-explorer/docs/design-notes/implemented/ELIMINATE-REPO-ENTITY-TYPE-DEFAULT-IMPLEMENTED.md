# re/eliminate-repo-entity-type-default

## Environment confirmation (before any test run)
`uv sync --all-packages --extra dev` run in worktree; `resource_explorer.__file__` =
`/Users/dwolfson/localGit/egeria-v6/trellis-re-eliminate-repo-default/packages/resource-explorer/resource_explorer/__init__.py`
(resolves INTO this worktree). Branched from origin/main 01965c5e.

## What changed

`re-api.js` gains `requireKind(fnName, value, paramName='entityType')`, which throws
`"<fn>: entityType is required"` (`resourceType` for assignGroup / listQuestionCatalog). Every helper below
lost its `= 'repo'` (or `= 'database'`) default and calls it before sending anything. Every omitting
call site now passes its kind. `worklist.js` has `kindOf(wl)`: a work list's own `entity_type` column is
authoritative, and a work list that arrives without one throws `work list 'X' has no entity_type` instead of
being read as a repo.

## Helper table (32 rows; every default removed)

| # | helper (re-api.js) | before | after |
|---|---|---|---|
| 1 | getQuestions | `entityType='repo'` | required, throws if omitted |
| 2 | getAnswer | `entityType='repo'` | required, throws if omitted |
| 3 | saveEnrichmentField | `entityType='repo'` | required, throws if omitted |
| 4 | getJournal | `entityType='repo'` | required, throws if omitted |
| 5 | writeJournal | `entityType='repo'` | required, throws if omitted |
| 6 | assignGroup | `resourceType='repo'` | required, throws if omitted |
| 7 | ask | `entityType='repo'` | required, throws if omitted |
| 8 | submitAnswerFeedback | `entityType='repo'` | required, throws if omitted |
| 9 | askStream | `entityType='repo'` | required, throws if omitted |
| 10 | getSurveyCandidates | `entityType='repo'` | required, throws if omitted |
| 11 | getNativeSurveys | `entityType='database'` | required, throws if omitted |
| 12 | runNativeSurvey | `entityType='database'` | required, throws if omitted |
| 13 | refreshNativeSurveys | `entityType='database'` | required, throws if omitted |
| 14 | getNativeSurveyReport | `entityType='database'` | required, throws if omitted |
| 15 | runSurveyDefinition | `entityType='repo'` | required, throws if omitted |
| 16 | getSurveyDashboards | `entityType='repo'` | required, throws if omitted |
| 17 | listSurveyResultBoards | `entityType='repo'` | required, throws if omitted |
| 18 | getAnalysisTrend | `entityType='repo', accepted but NOT sent` | required; now SENT as `entity_type=` query param (server ignores it, see below) |
| 19 | getMeasurements | `entityType='repo'` | required, throws if omitted |
| 20 | getAnalysesIndex | `entityType='repo'` | required, throws if omitted |
| 21 | getMembers | `entityType='repo', accepted but NOT sent` | required; now SENT as `entity_type=` query param (server ignores it, see below) |
| 22 | promoteMembers | `entityType='repo', accepted but NOT sent` | required; now SENT as `entity_type=` query param (server ignores it, see below) |
| 23 | getMemberChildren | `entityType='repo', accepted but NOT sent` | required; now SENT as `entity_type=` query param (server ignores it, see below) |
| 24 | createWorkList | `entityType='repo'` | required, throws if omitted |
| 25 | enqueueBatch | `entityType='repo'` | required, throws if omitted |
| 26 | getResourceFacts | `entityType='repo', ZERO callers` | DELETED (dead code) |
| 27 | getBulkStates | `entityType='repo'` | required, throws if omitted |
| 28 | getBulkFacts | `entityType='repo'` | required, throws if omitted |
| 29 | runAnalysis | `entityType='repo'` | required, throws if omitted |
| 30 | listRecords | `entityType='repo'` | required, throws if omitted |
| 31 | actOnRecord | `entityType='repo'` | required, throws if omitted |
| 32 | listQuestionCatalog | `resourceType='repo'` | required, throws if omitted |

Count correction: the earlier "31 + 4 native" was a double count. The table has 32 rows = 31 helpers that now
carry `requireKind` (the 4 native-survey helpers included, rows 11-14) + getResourceFacts, which was deleted.
Throw tests: 31, one per helper (`no-default-entity-type.test.mjs`). 
## Internal (non re-api.js) defaults removed

| site | before | after |
|---|---|---|
| app.js wireDispositionPicker / renderRecords / wireRecordActs / renderJournalWrite / renderJournalEntries | `entityType = 'repo'` | no default, `requireKind(...)` first line (all callers already passed it) |
| worklist.js saveAsWorkList | `{ entityType = 'repo' }` | no default (createWorkList throws) |
| worklist.js 96,247,416,432,461,493,1506,1598 | `wl.entity_type \|\| 'repo'` | `kindOf(wl)` hard failure |
| feedback.js `_currentEntityType` | `dataset.entityType \|\| 'repo'` | throws `feedback: entityType is required` |
| app.js renderTopBar (scope-slug) | `apiEntityType(...) \|\| 'repo'` | `requireKind('renderTopBar', ...)` |
| app.js loadWorkingSet | `(m.entity_type \|\| 'repo') === 'repo'` | `requireKind('loadWorkingSet', m.entity_type, 'member.entity_type')` |
| analysis.js:74, admin/groups.js:237, admin/question_catalog.js x3 | literal `'repo'` | stays `'repo'`, explicit, with a one-line comment (surface is repo-only) |
| app.js:3086 runAnalysis (depth offer) | omitted | explicit `'repo'`, comment: renderDepthOffer is repo-only |

## FULL call-site table (every call of every helper; generated by script from the post-change tree, "BEFORE" taken from origin/main by matching file+helper+order)

| helper | call site (post-change line) | kind arg BEFORE | kind arg AFTER |
|---|---|---|---|
| actOnRecord | next/app.js:3807 | entityType | entityType |
| actOnRecord | next/app.js:3876 | entityType | entityType |
| ask | next/chat.js:678 | passes `opts` (which carries `entityType: apiEntityType(state.resourceType)`, chat.js:635/648) | unchanged (kind now REQUIRED; `opts.entityType` supplies it) |
| askStream | next/chat.js:654 | passes `opts` (which carries `entityType: apiEntityType(state.resourceType)`, chat.js:635/648) | unchanged (kind now REQUIRED; `opts.entityType` supplies it) |
| assignGroup | next/admin/groups.js:237 | 'repo' | 'repo' |
| assignGroup | next/admin/groups.js:275 | resourceType | resourceType |
| createWorkList | next/worklist.js:1973 | entityType | entityType |
| enqueueBatch | next/app.js:5111 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| enqueueBatch | next/worklist.js:1563 | **OMITTED -> defaulted to repo** | kindOf(grid.workList) |
| enqueueBatch | next/worklist.js:1719 | **OMITTED -> defaulted to repo** | kindOf(grid.workList) |
| enqueueBatch | next/worklist.js:1804 | **OMITTED -> defaulted to repo** | kindOf(grid.workList) |
| getAnalysesIndex | next/app.js:4659 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getAnalysesIndex | next/app.js:7839 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getAnalysisTrend | next/app.js:4947 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getAnalysisTrend | next/app.js:5005 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getAnalysisTrend | next/worklist.js:1535 | **OMITTED -> defaulted to repo** | entityType |
| getAnswer | next/app.js:7672 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getBulkFacts | next/stages/context.js:276 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getBulkFacts | next/stages/curate.js:871 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getBulkFacts | next/stages/enrichment.js:293 | entityType | entityType |
| getBulkFacts | next/worklist.js:447 | 'repo' | kindOf(wl) |
| getBulkFacts | next/worklist.js:508 | 'repo' | kindOf(grid.workList) |
| getBulkFacts | next/worklist.js:1613 | 'repo' | kindOf(grid.workList) |
| getBulkStates | next/worklist.js:431 | 'repo' | kindOf(wl) |
| getJournal | next/app.js:3907 | entityType | entityType |
| getMeasurements | next/app.js:7973 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getMemberChildren | next/app.js:5671 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getMembers | next/app.js:5592 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| getNativeSurveyReport | next/stages/native-surveys.js:208 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getNativeSurveys | next/app.js:4252 | entityType | entityType |
| getNativeSurveys | next/stages/native-surveys.js:188 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getQuestions | next/app.js:4174 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getQuestions | next/app.js:6458 | entityType | entityType |
| getQuestions | next/app.js:6978 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getQuestions | next/app.js:7011 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getQuestions | next/worklist.js:364 | **OMITTED -> defaulted to repo** | kindOf(wl) |
| getQuestions | next/worklist.js:372 | **OMITTED -> defaulted to repo** | kindOf(wl) |
| getSurveyCandidates | next/app.js:4376 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| getSurveyDashboards | next/app.js:6507 | entityType | entityType |
| getSurveyDashboards | next/app.js:6539 | entityType | entityType |
| listQuestionCatalog | next/admin/question_catalog.js:180 | 'repo' | 'repo' |
| listQuestionCatalog | next/admin/question_catalog.js:273 | 'repo' | 'repo' |
| listQuestionCatalog | next/admin/question_catalog.js:286 | 'repo' | 'repo' |
| listRecords | next/app.js:3688 | entityType | entityType |
| listSurveyResultBoards | next/app.js:6385 | entityType | entityType |
| promoteMembers | next/app.js:5567 | **OMITTED -> defaulted to repo** | apiEntityType(state.resourceType) |
| refreshNativeSurveys | next/stages/native-surveys.js:163 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runAnalysis | next/app.js:3086 | **OMITTED -> defaulted to repo** | 'repo' |
| runAnalysis | next/app.js:4319 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runAnalysis | next/app.js:4725 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runAnalysis | next/app.js:8158 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runAnalysis | next/app.js:8169 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runNativeSurvey | next/stages/native-surveys.js:179 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| runSurveyDefinition | next/app.js:4833 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| saveEnrichmentField | next/stages/enrichment.js:376 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| saveEnrichmentField | next/stages/enrichment.js:393 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| saveEnrichmentField | next/stages/enrichment.js:410 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| saveEnrichmentField | next/stages/enrichment.js:423 | apiEntityType(state.resourceType) | apiEntityType(state.resourceType) |
| submitAnswerFeedback | next/chat.js:390 | entityType | entityType |
| writeJournal | next/app.js:3871 | entityType | entityType |

Reading it: "OMITTED -> defaulted to repo" = the bug class: 16 call sites (enqueueBatch x3, getQuestions x2,
getAnalysisTrend x3, getAnalysesIndex, getBulkFacts curate, getMemberChildren, getMembers, promoteMembers,
runAnalysis depth-offer, + chat.js ask/askStream which pass `opts`, so are fine). The 5 rows showing
`'repo'` BEFORE for worklist.js getBulkStates/getBulkFacts were the `wl.entity_type || 'repo'` fallbacks.
The script reports "60 call sites"; the 2 "MISSING after" rows are the chat.js `opts` rows relabelled above.

## Mixed-kind work lists

Not possible. `routes/work_lists.py` `WorkListCreate.entity_type` is one value per list, and the run
route says "a work list is homogeneous by construction"; the `entity_type` column is authoritative. So
`kindOf(wl)` is the right and only source. Documented, no mixed-kind test.

## SERVER FINDING (follow-up item, NOT fixed here)

These four client helpers now send `entity_type` but the server routes ignore it and are REPO-ONLY:
- `GET  /api/projects/{slug}/analyses/{analysis_id}/trend`   (routes/projects.py `get_analysis_trend`: `registry.get(slug)`, 404 "Project not found", REPO_ANALYSIS_RESULTS_MAP)
- `GET  /api/projects/{slug}/members/{analysis_id}`          (`get_members`)
- `GET  /api/projects/{slug}/members/{analysis_id}/children` (`get_member_children`)
- `POST /api/projects/{slug}/members/{analysis_id}/promote`  (`promote_members`)
A database/filesystem caller of any of these will still 404 as "Project 'X' not found" server-side. The
param is now sent (query string; FastAPI ignores unknown query params), so the server can adopt it later.
getAnalysisTrend from worklist.js is guarded to repo work lists already (worklist.js "isn't tracked yet for ... resources").

## Proof

Tests: `frontend-build/test-harness/no-default-entity-type.test.mjs` (39 tests): 31 per-helper throw tests (error text
exact, and no request is sent), export-table check (getResourceFacts gone), kind-given-still-works, the four
formerly-swallowing helpers now send entity_type, and routing-level work-list pane tests that open the
pane and click Run across: DATABASE grid columns (stub 404s the repo route for db slugs exactly as the real
server does), DATABASE Run across (batch body entity_type=database, facts reads entity_type=database), REPOSITORY
grid + Run across (regression), FILESYSTEM grid, and kind-less work list fails loudly ("has no entity_type", no repo-route call).

Python source-text tests updated to the new contract (4): test_next_db_fs_gate_removal.py x2, test_next_sidebar_list_db_fs.py x2.

### Revert-and-confirm-FAILS (origin/main versions of re-api.js, next/worklist.js, app.js, feedback.js, stages/, admin/ restored; new test kept)
```
not ok 35 - a DATABASE work list reads its Schema Inventory columns from the database route (the bug Dan hit)
    The question columns could not be read: Project 'db_one' not found
not ok 36 - a DATABASE work list "Run across" sends entity_type=database and starts the batch
    Expected values to be strictly equal: (batch entity_type was 'repo', not 'database')
# pass 0   # fail 2   # skipped 37
```
### Restore-and-confirm-PASSES
```
ok 35 - a DATABASE work list reads its Schema Inventory columns from the database route (the bug Dan hit)
ok 36 - a DATABASE work list "Run across" sends entity_type=database and starts the batch
# pass 2   # fail 0   # skipped 37
```

### Full runs
- /next render harness, Node v20.11.0: `node --test test-harness/*.test.mjs` -> tests 157, pass 157, fail 0.
- Python: `pytest tests -k "next or tailwind or frontend or worklist or work_list"` -> 717 passed (the 4 updated tests included).
- Signing: real probe commit `git commit --allow-empty -s -S` -> `%G?` = G (dan.wolfson@pdr-associates.com), probe dropped (soft reset).

## Dan's gate
1. "dts-treasury-data candidates" (database work list), Schema Inventory tab: real columns, not "Project 'X' not found".
2. Same work list: "Run across the rows" starts a batch, and the row-count snapshots appear.
3. A repository work list, Schema Inventory tab: still shows columns correctly (regression check).
