# "Notify me" entity-type fix — implemented

**Dispatch:** the "Resource Explorer expansion architecture" session's walk-through of Resource
Explorer, item found alongside two other repo-only-assumption bugs fixed the same night
(`re/scouting-overview-repo-only-gate`'s `app.js` `loadPane` fix, and an earlier `'db'`/`'database'`
tab mismatch).
**Branch:** `re/notify-me-entity-type`, off `origin/main` `4cbf8d06`, built in a separate worktree
(`/Users/dwolfson/localGit/egeria-v6/trellis-re-notify-fix`).
**Coordination:** file list confirmed with the "Resource Explorer expansion architecture" session
before writing (no overlap; that session flagged one live neighbor — `re/relationship-graph-rendering`
was mid-edit on `app.js`'s rail Evidence panel / `promoteToPane` region, a different region of the
file from this change's ~6444-6580 — confirmed by inspection, see "Conflict check" below). The
delivery-honesty copy (item 4) went through one real correction cycle with that session — see
"Delivery honesty: what the code actually does" below.

## The bug

Clicking "notify me" on a database resource card failed with `"Repo 'laz_local_adventureworks' not
found"`. Two layers, both required for the fix:

1. **Client** (`re-api.js`'s `createSubscription`, called from `app.js`'s `openNotifyDialog`):
   `createSubscription` itself already took a real `entityType` parameter, but its one caller
   hardcoded the literal `'repo'` — the call's own comment explained why, at the time correctly:
   "entity_type is 'repo' unconditionally: the Questions engine this dialog is attached to is
   itself gated to `state.resourceType === 'repo'`". That gate was lifted separately, the same
   night, by the database/filesystem generalization behind `re/scouting-overview-repo-only-gate`
   (`loadPane()`'s comment: "The stricter repo-only backend gate that used to sit here is gone").
   The call site was never updated to match — a question row can now legitimately be open on a
   database or filesystem resource, and the dialog kept sending `'repo'` regardless.
2. **Server** (`web/routes/automate.py`'s `create_subscription`): even with a correct
   `entity_type`, the existing check only ever validated the repo case —
   `if req.entity_type == "repo" and not registry.get(...)`. For any other `entity_type` it did
   nothing at all: a nonexistent database/filesystem slug would have been silently accepted rather
   than 404ing, once the client bug above was fixed on its own.

The reported 404 message is explained by layer 1 alone: `entity_type: 'repo'` reached the server,
which ran `registry.get('laz_local_adventureworks')` — the repo-only lookup — against a database
slug that was never going to be there.

## The fix

### 1. Client — `resource_explorer/web/static/next/app.js`, `openNotifyDialog()`

Now computes `const entityType = apiEntityType(state.resourceType);` once, at the top of the
dialog, and passes it to `createSubscription(entityType, slug, chosen, label)` — the same
translation (`'db'` → `'database'`, everything else passes through) every other `/next` boundary
crossing already uses (`getQuestions`, `getContext`, `getMeasurements`, etc.).

### 2. Server — `resource_explorer/web/routes/automate.py`, `create_subscription()`

Replaced the repo-only check with a per-type dispatch table, mirroring `schedules.py`'s own
`_RESOURCE_LOOKUP`/`_require_resource` (kept as a second, small dict rather than importing that
one, since the 404 wording differs and this route has no "unknown type" exception message to
share):

```python
_RESOURCE_LOOKUP = {
    "repo": lambda reg, slug: reg.get(slug),
    "database": lambda reg, slug: reg.get_database(slug),
    "filesystem": lambda reg, slug: reg.get_filesystem(slug),
}
_ENTITY_NOUN = {"repo": "Repo", "database": "Database", "filesystem": "Filesystem"}
```

A database slug now 404s against `get_database()` with `"Database '...' not found"`; a filesystem
slug against `get_filesystem()` with `"Filesystem '...' not found"`. An `entity_type` this dict
doesn't know passes through unvalidated — same deliberate exception `schedules.py`'s
`_require_resource` documents: this route has never constrained the vocabulary, and 422ing here
would reject a resource kind added elsewhere before this dict was updated for it.

### 3. Dialog copy — inline scheduling

The dialog's "set ⏱ Schedule for it on this resource in Automate, or this never fires" line sent
the reader to a different stage to do anything about it. It's now an inline action in the same
dialog: a daily/weekly `<select>` + "Set" button, reusing the exact same backend call the per-card
"⏱ Schedule" action and chat's inline scheduling form both already make —
`saveSchedule(entityType, slug, analysisId, cadence, true)`, `POST /api/schedules/{entityType}/{slug}`
— rather than inventing a second scheduling code path. `re-api.js` gained two wrappers this needed
that didn't exist yet: `saveSchedule()` (mirrors classic's `saveSchedule()` in `index.html`) and
`getSchedules(entityType, entitySlug)` (`GET /api/schedules/{entityType}/{slug}`, already used
elsewhere server-side, just not wrapped for `/next`).

Per the architecture session's second requirement, the button is not fire-and-forget: after
`saveSchedule()` resolves, the dialog calls `getSchedules()` and reads back what the server
actually stored (`schedule`, `next_run`) rather than trusting the POST body it just sent, and shows
it (`✓ daily — next run in 22 hours`, using the same `ago()` formatter the rest of `/next` uses) or
the error if the save failed.

### 4. Dialog copy — delivery honesty: what the code actually does

The dialog's old line implied one continuous mechanism: "set a Schedule ... or this never fires."
It didn't say anything about the Egeria half at all. The architecture session's dispatch asked for
both halves stated, including "rfa_egeria_sync only pushes that RFA to Egeria once the resource is
published" — I read `rfa_egeria_sync.py` in full (all 284 lines) plus `scheduler.py`'s
`_check_subscriptions`/`_reconcile_rfa_actions` before writing the copy, and that specific claim
does not hold:

- `scheduler._check_subscriptions()` calls `activity_logger.log_rfa()` unconditionally on a
  detected change — pure local write (an `activity_log` row + an `rfa_actions` row), visible in
  this app's own RFA drawer immediately. This is the "local" half, and it's genuinely
  publish-independent already.
- Separately, `scheduler._reconcile_rfa_actions()` (same background loop, every tick) calls
  `rfa_egeria_sync.reconcile_rfa_actions()`, which calls `sync_rfa_action()` for every unsynced
  row. That function calls `MyProfile.create_my_todo()` **unconditionally** — a bare personal ToDo
  with **no `element_guid`/resource linkage at all** (`add_action_target` is explicitly not called;
  the module's own docstring says why: "today's local RFAs are flattened from `activity_log` rows
  and don't reliably carry a real Egeria annotation GUID to link against"). Nothing in that call
  path reads `entity_type`, `entity_slug`, `registry.get()`/`get_database()`/`get_filesystem()`, or
  `is_published`.

I flagged this discrepancy to the architecture session before writing the copy rather than
encoding an unverified claim into user-facing text. It confirmed: the "only when published"
assumption was wrong, and the code's actual behavior stands. Final copy (verbatim from the
session, now in the dialog):

> Detection runs locally, against this app's own registry — the next time a *scheduled* run of
> that analysis detects a change, it writes an RFA straight into this app's drawer. No schedule
> above means this never fires. Separately, the scheduler's own background loop also creates a
> personal Egeria ToDo for each RFA it delivers — unlinked to `{slug}` itself, whatever this
> resource's publish state — so that half happens whether or not it has ever been published to
> Egeria.

The one publish-gated RFA mechanism that does exist in this codebase is a different, older path:
`EgeriaPublisher`'s `RequestForActionProperties` annotation, attached to a `SurveyReport`
(`docs/Backlog.md` ~line 3404) — but Automate's subscription path doesn't go through
`EgeriaPublisher` at all, only `log_rfa` + `rfa_egeria_sync`. Not touched by this change; noted here
so a future reader doesn't conflate the two mechanisms.

## The 'repo' literal sweep

Asked for separately: grep `/next` client code for other hardcoded `'repo'` string literals in
request bodies, since this bug's class had already shown up twice earlier the same night. Ran a
full sweep of `resource_explorer/web/static/next/**/*.js` and `re-api.js`. Findings, categorized:

**Fixed on this branch (both cheap, same shape as the notify-dialog bug):**

- **`app.js`'s `wireHumanAnswers()`** (~line 5846) — `saveQuestionAnswer('repo', slug, question,
  next.trim())` inside the "human answer" Save button's click handler. Wired inside the same
  Questions-engine pane `openNotifyDialog` hangs off, and the surrounding code already correctly
  calls `getQuestions`/`getContext` with `apiEntityType(state.resourceType)` — this one call was
  missed. Now `saveQuestionAnswer(apiEntityType(state.resourceType), slug, question, next.trim())`.
- **`app.js`'s `start()`** (~line 7135) — `listAnalyses('repo')` in the boot `Promise.allSettled`
  batch. `readUrl()` (called earlier in `start()`) can already set `state.resourceType` from a
  `?type=` URL param before this fetch fires, and `analysis_catalog.yaml`'s `resource_types`
  genuinely differs per entity type (dozens of `["database"]` entries, one `["filesystem"]`,
  distinct from the `["repo"]` ones) — a repo-only fetch here left `state.analyses` (read by
  `trendSupport()` and passed into `openWorkList()`) permanently wrong for a session that starts on
  a non-repo resource. Fixed to `listAnalyses(apiEntityType(state.resourceType))`. Additionally,
  `state.analyses` was otherwise **never** re-fetched after boot — `switchResourceType()` (the
  sidebar's own type-switch handler) refreshed `state.databases`/`state.filesystems` via
  `ensureResourceListLoaded()` but not `state.analyses`, so switching resource type once left it
  stuck on whichever catalog boot happened to fetch. Added
  `state.analyses = await listAnalyses(apiEntityType(type)).catch(() => state.analyses);` to
  `switchResourceType()` right after `ensureResourceListLoaded()`.

**Flagged, not fixed — legitimately repo-only, no live bug (verified, not assumed):**

- `stages/analysis.js:74` — `listAnalyses('repo')` inside `mountSubResourcePanel()`. Only ever
  opened for the `sub_resource_survey` analysis row, which `analysis_catalog.yaml` (line 547)
  declares `resource_types: ["repo"]` only — the server's own `getAnalysesIndex` will never surface
  that row for a database/filesystem resource, so this panel is unreachable with a non-repo slug.
- `admin/question_catalog.js` (lines 180, 273, 286) — `listQuestionCatalog('repo')`. This admin
  panel has no resource-type selector and no reference to `state.resourceType` anywhere in the
  file — a standalone always-repo admin catalog browser, not driven by the sidebar's selection.
  Not reachable with any other type from this UI. Arguably a feature gap (no way to browse the
  db/filesystem question catalog from Admin) rather than a hardcode-vs-live-state mismatch; left
  for a future pass since it's a new capability, not a fix to existing wrong behavior.
- `stages/investigation.js:404` — `slugListRows('repo')` is a local `<datalist>`'s initial default,
  populated from already-fetched `state.projects`/`state.databases`/`state.filesystems` (no server
  call, no `entity_type` in a request body); the surrounding `<select>` lets the user re-pick the
  type. Not the bug pattern.
- `app.js:2927` — `renderDispositionHistory('repo', p.github_url)` inside the DepthOffer flow. Its
  own existing comment states `renderDepthOffer` above already gates on `p?.github_url`, so this
  callback only ever runs for a repo. Verified correct.
- `admin/groups.js:236` — `assignGroup(repoSlug, groupSlug, 'repo')` inside a GitHub-org group
  suggestion handler that iterates `s.repo_slugs` — genuinely GitHub/repo-only.
- `re-api.js`'s many `entityType = 'repo'` default parameters (`getQuestions`, `getAnswer`,
  `saveEnrichmentField`, `getJournal`, `writeJournal`, `assignGroup`, `ask`,
  `submitAnswerFeedback`, `askStream`, `getSurveyCandidates`, `runSurveyDefinition`,
  `getSurveyDashboards`, `getAnalysisTrend`, `getMeasurements`, `getAnalysesIndex`, `getMembers`,
  `promoteMembers`, `getMemberChildren`, `createWorkList`, `enqueueBatch`, `getResourceFacts`,
  `getBulkStates`, `getBulkFacts`, `runAnalysis`, `listRecords`, `actOnRecord`,
  `listQuestionCatalog`) — fallback defaults on the wrapper functions themselves, not call sites.
  Every live caller checked passes `apiEntityType(state.resourceType)` or an equivalent explicit
  value already, aside from the two bugs fixed above.
- `worklist.js` (lines 232, 401, 415, 456, 1464-1465, 1556) — all `wl.entity_type || 'repo'` /
  `grid.workList?.entity_type || 'repo'`, reading the work list's own stored `entity_type` with
  `'repo'` only as a fallback for records that predate the field. Already correct (per the file's
  own comment at line 228-230, from an earlier fix).
- `worklist.js:1915` — `saveAsWorkList(..., entityType = 'repo')` default parameter; its one
  caller (`app.js:2351`) already passes `apiEntityType(state.resourceType)` explicitly.

## Conflict check

`git merge-tree --write-tree origin/main <branch-head>` run clean before push (see commit log for
the exact SHA). `re/scouting-overview-repo-only-gate` and `re/relationship-graph-rendering` are
**not pushed to `origin`** as of this branch's tip — `git branch -r` on the shared checkout shows
neither. The scouting-overview fix is already folded into `origin/main` `4cbf8d06` (verified by
reading `loadPane()`'s own comment, which documents it). The relationship-graph worktree
(`trellis-re-relationship-graph`) has local, uncommitted changes to `app.js` touching the rail
Evidence panel / `promoteToPane` region — a different region of the file from this change's
`openNotifyDialog`/`wireHumanAnswers`/`start`/`switchResourceType` edits (lines ~5528-5850,
~6444-6580, ~7098-7145, ~1975-2003) — confirmed by inspection, not just by line-number distance.
Since that branch has no commits of its own yet (its worktree HEAD still matches `origin/main`),
there is nothing on `origin` for `merge-tree` to conflict against; a real conflict check will be
needed again if/when that branch pushes before this one merges.

## Tests

- `tests/test_next_notify_subscription.py` — updated the now-stale
  `test_entity_type_is_repo_matching_the_repo_only_questions_gate` (renamed
  `test_entity_type_is_translated_not_hardcoded_to_repo`, asserts the translated `entityType`
  variable is used and no `'repo'` literal remains in the `createSubscription` call); updated
  `test_submit_reads_the_checked_radio_when_there_is_a_choice` for the new shared
  `currentAnalysisId()` helper; added `TestReApiSaveScheduleAndGetSchedulesWrappers`,
  `TestNotifyDialogInlineScheduleAction` (5 tests), `TestNotifyDialogDeliveryHonesty` (3 tests,
  including one that specifically asserts the dialog does NOT claim Egeria delivery depends on
  publish state).
- `tests/test_automate_create_subscription_entity_type.py` (new) — route-level tests against a
  real `TestClient`/`ProjectRegistry`, covering success and type-correct 404 for repo/database/
  filesystem, a real repo slug sent as `entity_type: 'database'` still 404ing (type-correctness,
  not just presence), and an unknown `entity_type` passing through unvalidated (matching
  `schedules.py`'s own documented exception).
- Source-level pattern (grep/slice function bodies from concatenated `/next` JS, no browser)
  follows the established convention from `tests/test_next_resource_header_publish_note.py` and
  `tests/test_next_scouting_overview_repo_only_gate.py`, same as this file's own pre-existing
  tests already did.

**Full suite:** `uv run pytest tests/ -q -rf` (from the worktree, `uv sync --all-packages --extra
dev` run first) — see the session's final report for the exact pass/fail counts.

## Live verification

See the session's final report for whether a signed-in live check against the running dev server
was performed (login/logout per `docs/Backlog.md`'s cached-session note), and its outcome.
