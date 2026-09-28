# Open stack — working checklist

**Purpose:** the live list of what is outstanding right now, across repos, so it
survives a context reset. Short entries with a pointer; the reasoning lives in
the design docs. Delete an entry when it is done — this is not a history.

**Last updated:** 2026-08-31 (redeploy prep)

---

## 1. Backfill — the analyses that answer nothing yet

**The headline problem.** Code merged, tested, and inert.

| analysis | repos with findings (of 60) | notes |
|---|---|---|
| `supply_chain` | **0** | so all three supply-chain checks on the FOSS scorecard report `unknown` for every repo |
| `foss_scorecard` | 5 | only repos touched during verification |
| `chaoss_metrics` | 8 | " |
| `cii_badge` | 8 | " |

- [x] **Slice of 12 done 2026-08-27** — one repo per language, 2→65k stars,
      5→423 contributors. `supply_chain` 11/12 (the 12th, `awesome_kedro`, is an
      awesome-list with no `.github/workflows` — a real result, not a failure);
      `foss_scorecard`/`chaoss_metrics`/`cii_badge` 12/12. ~18 min, no errors,
      though `openmetadata`'s zipball alone took ~11.
- [x] **Slice judged representative.** All three supply-chain checks
      discriminate (dangerous_workflow 7 pass/4 fail; pinned_dependencies 6
      partial/5 fail; token_permissions 9 partial/2 pass), every language is
      covered, and nothing failed systematically. Scorecard coverage rose from
      5–8 to **10–11 of 16 checks evaluated**, and scores *fell* as it rose
      (openmetadata 8.6→7.0, deep_causality 7.5→7.3) — correct, since fewer
      flattering unknowns are being excluded.
- [x] **Remaining 48 done 2026-08-27** — 96 orchestrator invocations, zero
      failures. ~17 min for step 1, under a minute for step 2.
- [x] **Whole-catalogue coverage:**

      | analysis | before | after |
      |---|---|---|
      | `supply_chain` | 0/60 | **50/60** |
      | `foss_scorecard` | 5/60 | **60/60** |
      | `chaoss_metrics` | 8/60 | **60/60** |
      | `cii_badge` | 8/60 | **60/60** |

      The 10 without `supply_chain` genuinely have no `.github/workflows` —
      confirmed per repo by the presence of `repo_conventions` findings, which
      proves the parse ran.

      Catalogue-wide label spread: `dangerous_workflow` 43 pass / 8 fail;
      `pinned_dependencies` 1 pass / 14 partial / 36 fail; `token_permissions`
      8 pass / 36 partial / 7 fail. Exactly **one** repo in sixty pins every
      GitHub Action to a commit SHA.

- [ ] **Verify the 8 `dangerous_workflow` failures.** Four were hand-checked
      (`data_prep_kit`, `genaieval`). The other four are `docs`, `genaiinfra`,
      `haystack_opea`, `langchain_opea` — and note they share one workflow,
      `pr-path-detection.yml` / `pr-link-path-scan.yaml`, the same OPEA-project
      file copied between repos. So it is likely **one upstream pattern**, not
      four independent findings, and worth reporting upstream once rather than
      four times.

**Verified by hand, not taken on trust:** `data_prep_kit`'s 7 checkout hits were
confirmed earlier from a local clone. `genaieval`'s hit is the *script-injection*
path, which had only synthetic coverage until now — checked against the real
file: `pr-path-detection.yml` interpolates
`${{ github.event.pull_request.head.ref }}`, an attacker-controlled fork branch
name, straight into a shell `run:` block. True positive.

**Also open:** a repo with no `.github/workflows` gets no `supply_chain`
findings at all, by the parser's documented convention (three "fail" rows for a
repo with no CI would be findings about a thing that does not exist). That is
still right — but the card then renders an absence, where "this repo has no
GitHub Actions workflows" is a measured, useful fact. Distinct from the
kedro_kubeflow case just fixed: there we never looked; here we looked and found
none. Worth a stated finding rather than silence.

**Refinement worth logging:** the injection check does not distinguish
`pull_request` from `pull_request_target`. Both are genuine injection, but only
the latter runs with the base repo's secrets, so blast radius differs and the
finding currently reads the same for both.

## 2. ~~Retire the ISSUE-50 workaround~~ — DONE 2026-08-27

`EgeriaDelegatedStepSurveyor` routes through `initiate_gov_action_type()`,
which needs a **pre-authored `GovernanceActionType` per delegated step**. The
pyegeria bug that forced it is fixed: `initiate_engine_action` now takes
`governance_engine_name`. `initiate_and_wait()` already exists in the module.

- [x] Switch the primary trigger path to `initiate_and_wait()`
- [x] Live-verify against a real delegated step
- [x] Drop the per-step `GovernanceActionType` *requirement* — the direct path
      needs no pre-authored element. The probe doc is kept: it documents the
      action-type path, which is still valid and still preferable when a
      `GovernanceActionType` already exists.
- [x] Remove the stale comment saying the direct path is "kept for when
      ISSUE-50 is fixed"

Live-verified: a real engine action on the Stewardship engine reached `COMPLETED` through the
direct path. Also fixed en route: the Prefect tests were not hermetic — they honoured a
configured `PREFECT_API_URL` from `.env`, so they passed in one checkout and failed in another on
ambient environment alone. Shared `ephemeral_prefect` fixture in `tests/conftest.py`.

## 3. RE as an engine host — BLOCKED, and a design already exists

**Correction 2026-08-27.** This section previously said "unblocked, not
started". That was wrong on both halves.

**A complete design already exists:** `docs/survey-execution.md (§7)`
(2026-08-17), grounded in a direct read of Egeria's Java source — the four
execution permutations, the reachability dimension, and case 2 ("RE as a
claiming engine") as the real investment. It is marked **ON HOLD**. Anything
starting here should begin by reading it, not by re-deriving it.

**Two hard gaps, confirmed by reading pyegeria 6.1.5 and by probing the live
platform:**

1. **Nothing can create a `GovernanceEngine` or `GovernanceService` element.**
   pyegeria has no create method and no properties class for either — only
   link/update/detach for the `SupportedGovernanceService` *relationship*,
   which presupposes both elements exist. This is **server-side**:
   `GovernanceConfigurationResource` is read-only, and Egeria itself populates
   engines and services through content-pack archive writers. Not a wrapping
   gap; an Egeria change or archive-based registration is required.
2. **No way to list claimable work — and it is NOT a wrapper gap.**
   *Corrected 2026-08-27 after `egeria-python-07` checked the Java source and
   pushed back.* I had recorded this as "only a wrapper gap; the route exists",
   taking `survey-execution.md (§7)` at its word that
   `.../active-engine-actions` was "the per-engine claimable-actions listing,
   distinct from the requester-side general listing". It is not distinct.

   Verified in `egeria-v6/egeria`: that route is declared in
   `GovernanceContextResource.java:89` and its handler is literally named
   **`getActiveClaimedEngineActions`**, with the Javadoc "engine actions that
   are still in process and that have been **claimed by this caller's userId**.
   This call is used when the caller restarts." It is the same restart-recovery
   operation as `.../engine-actions/active-claimed`, under a misleading URL.
   Live, it 404s on the View Service, which registers only `initiate` and
   `active-claimed` under `governance-engines/{guid}`.

   Enumerating **every** per-engine route in Egeria gives four: two config
   reads, the attach, and those two restart-recovery listings. **There is no
   claimable/unclaimed listing anywhere.** So this is a server-side gap — a
   missing Java endpoint — not a missing Python wrapper.

   The workable approach is the one live probing had already pointed at before
   the brief contradicted it: whole-server `get_active_engine_actions()`,
   filtered client-side by `governanceEngineGUID` and unclaimed status. Costs a
   wider fetch; correctness still rests on `claim` refusing a second claimant.

**The blocker the plan cites is not in any live tracker.** It cites
`ISSUE-51` for a template-created-asset bug where native surveys fail with
"connector is null". `PYEGERIA_ISSUES.md` was renumbered on 2026-08-15 and its
ISSUE-51 is now an unrelated, fixed `fetch_element()` issue. Searching the
canonical tracker for the blocker's own content — "connector is null",
`create_*_element_from_template` — returns nothing. The nearest match is `E1`
in RE's superseded local tracker, which is a *different* failure (a Postgres
repository connector 500 on classification save).

So a blocker that halted a workstream survives only as prose in a design doc,
under a number that now means something else. Before any build here:

- [ ] Re-verify whether the template/Connection failure still reproduces
- [ ] Re-file it in the canonical tracker with its own content, not a number
- [ ] Decide the registration route given gap 1 is server-side
- [x] ~~File the missing `active-engine-actions` wrapper~~ — there is nothing
      to wrap. Raise the missing *endpoint* with whoever owns the Java side, or
      accept whole-server-plus-client-side-filter.
- [x] **The blocker is real and current** — `egeria-python-07` reproduced it
      2026-08-27 and filed it as ISSUE-79 (odpi/egeria-python PR #314). A
      template-created FileFolder survives creation now (E1's 500 no longer
      reproduces) but its native survey fails server-side with a
      `NullPointerException` in `BasicFolderConnector.getFile()` because
      `assetConnector` is null. Two distinct bugs, as suspected — the design
      doc had conflated them. **So the ON HOLD stands, now for a documented,
      reproducible reason instead of a dangling number.**

- [ ] Register RE's steps as request types via `link_supported_governance_service`
- [ ] Implement find → claim → execute → report
- [ ] Decide polling cadence (Kafka is an optimisation only — correctness rests
      on `claim`, not delivery)

**Two live findings, 2026-08-27 — both change the design, neither is in the
design note yet:**

1. **`get_active_claimed_engine_actions` cannot be used to FIND work.** Called
   with the `EgeriaWatchdog` engine's GUID it returns `'No elements found'`
   while three actions are `IN_PROGRESS` on that very engine — at every
   page_size, so it is not paging. Those three are claimed by
   `egeriawatchdogengine`, and the call was made as `erinoverview`. So it is
   caller-scoped: it answers "what have *I* claimed", not "what is available".
   Useful for **recovery after a restart** — reclaiming what we already hold —
   and useless for discovery. Finding claimable work needs
   `get_active_engine_actions` filtered on unclaimed status instead.

2. **RE needs its own engine-host user identity.** It talks to Egeria as one
   fixed service account (`erinoverview`). The watchdog claims as
   `egeriawatchdogengine`. Claims are attributed to the caller, so without a
   distinct identity RE's claims are indistinguishable from any other use of
   that account — and finding (1) means RE could not even list its own claimed
   work apart from anyone else's. This is a deployment prerequisite, not a
   coding detail.

3. **`EgeriaConfig.engine_host` is vestigial.** Declared in `config.py:147`
   with alias `EGERIA_ENGINE_HOST` and read nowhere in the package. It reads
   like engine-host support exists when none does — a deployment engineer
   setting it would reasonably expect an effect. Either wire it during this
   work or delete it; leaving it is a false affordance.

Also noted: `actionStatus` is the field on `get_active_engine_actions`'
payload, while `egeria_delegated_step` found the real key was `activityStatus`
when reading the same concept through `get_metadata_element_by_guid`. The name
differs by endpoint — worth pinning wherever the loop reads status, since that
exact mismatch already caused one false-terminal bug.

**RE-side sketch (does not depend on the pyegeria registration answer):**

*One request type per step, not one generic one.* Egeria routes on
`requestType`, and a governance engine's whole purpose is to advertise what it
can do. 37 step keys today — 34 repo, 2 database, 1 filesystem. Collapsing them
into a single "run an RE step" request type with the step name as a parameter
would hide RE's capabilities from the catalog and put routing in a field Egeria
does not interpret, which defeats registering at all. Per-step is also what
makes `executes_at` collapse into catalog data (design note §4.4): the engine
holding a `SupportedGovernanceService` for `repo_manifest_parse` *is* where it
runs.

*But registering all 37 uniformly would be wrong.* Their infrastructure needs
differ — by `fetch_cost`: 19 need nothing but the RE database, 9 download a
zipball, 3 are api_heavy, 3 make one API call. A remote engine host running a
`download` step needs GitHub credentials and egress; a zero-fetch step needs
neither. So the registered set is a **per-deployment choice**, not a fixed list,
and the registration code should take the set as input rather than enumerating
STEP_REGISTRY.

Design: `docs/survey-execution.md` §4.

## 4a. Full Survey should live only in Automate — blocked on a surface

`RepoFullSurvey` (survey_kind `automate_full`) is appended to **every** stage
tab, so a 34-step deep survey sits one click away on Scouting, whose whole
purpose is the cheap look that gates it.

Attempted 2026-08-27 and reverted unshipped. The stage tabs filter correctly —
restricting the append to `intent === 'automate'` removes it from all four —
but **the Automate view has no survey list to move it to.** `showMainView('automate')`
renders `#automate-view`, which loads subscriptions and schedules and never
fetches Survey Definition candidates. So the change hides Full Survey
everywhere and gives it nowhere to appear, which is what the original
"appended to every stage" comment was avoiding.

- [x] **Automate has a survey list** — built 2026-08-30 (`fd63eed`). Third sub-tab beside 🔔 Subscriptions and
      ⏱ Schedules), then restrict the append to that intent. Both halves, or
      neither — the filter alone loses the feature.

## 4. The granularity collapse — deferred by decision

`analysis_id`'s bundling role duplicates the survey type: 25 of 27 repo analyses
are a single step. Its *results* role is real and stays.

Migration risk was the blocker; that is now moot — Egeria can be wiped and
rebuilt (export/import round-trips 62 resources, bulk republish works). So this
is about the model being right, not about data.

- [x] **Design pass done 2026-08-28** — `docs/repo-analysis-funnel.md (§11)`. It
      recommends a **smaller** change than the one it set out to design, and
      the checklist's own risk note was wrong: `resource_schedules` has **one**
      row, and the whole bundling role is **6 rows** across three tables. The
      74k rows are keyed by finding `kind`, which turns out to be its own
      vocabulary already — 5 kinds are not analysis ids, and 12 analyses have
      no kind at all — so the results half was never the same thing under
      another name and does not move.
- [x] **Widen the schedule target** — done 2026-08-28 (`aeec939`).
      `resource_schedules` gained `target_kind` (`analysis` | `survey`,
      defaulted by ALTER TABLE), `save_schedule` validates it, and
      `scheduler._execute` dispatches a survey target through
      `run_survey_definition`. §4a is no longer blocked on schema.
- [ ] Decide the §6 open question: should an analysis stay independently
      runnable once surveys are schedulable, or become view-only?

Design: `docs/survey-execution.md` §2.

## 5. Smaller / opportunistic

- [x] **Its one reader was fixed 2026-08-27.** `project_contributor_stats` has
      no *meaningful* reader since CHAOSS moved to `project_commits`, but it
      does have one: an agent tool reporting a contributor's tier. It ordered
      `period_start DESC LIMIT 1`, which — given nested trailing windows — is
      the *narrowest* (30-day) window, so it answered "tier over the last
      month" to a question about the contributor. Measured across the
      catalogue: **70 of 347** contributors got a different tier that way,
      every sampled case a core maintainer downgraded to "regular" (Apache
      Polaris, sqlglot), and **271 more** were absent from the 30-day slice, so
      the caller was told the tier was unknown and to "run refresh" about a
      tier already computed. Now `ASC` — widest window. Test pins the
      direction, since the two orderings differ by one keyword and the wrong
      one fails silently.
- [ ] The table itself still misleads by shape. Fix the writer to disjoint
      periods, or retire it and derive tier from `project_commits`. Not urgent
      now that its reader is honest — but the trap remains for the next
      consumer.
- [x] **Checked 2026-08-27, and the finding was wrong.** `needs_republish` billed
      all its members as "previously catalogued that Egeria no longer holds".
      Of the seven, only four were: `docling_eval`, `openlineage`,
      `openmetadata` and `unitycatalog` really were published on 24–25 Aug and
      lost their asset to the redeploy. The other three — `docs`,
      `enterprise_rag`, `genaicomps` — carry only a scout import from 21 Aug
      and were **never catalogued at all**. Telling someone they lost something
      they never had sends them hunting a fault that does not exist, and the
      two want different actions: restoring a known asset versus a first
      publish. The finding now states which is which, from whether a completed
      `catalog` operation exists in the activity log.
- [x] `unitycatalog` published 2026-08-28 — asset
      `8a9b783a-4871-4100-86df-9c705a13849e`, report
      `3c1aa491-8ea4-4110-a6d8-a6c3ca71aec0`.
- [x] **The three never-catalogued repos published 2026-08-28** — `docs`
      (`8e479ad8`), `enterprise_rag` (`92d9db93`), `genaicomps` (`5ab31495`),
      each a distinct asset. The finding is down to three, all of them the
      "lost an asset" kind and all blocked on a Project decision — so the
      remaining count is now exactly the blocked set, which is what the
      publish_ready field was added to make visible.
- [x] **Resolved 2026-08-30.** All three were assigned to the RE Test Project (contexts
      materialised from the investigation they already belonged to) and published:
      `docling_eval` `5eb02266`, `openlineage` `88a2a70e`, `openmetadata` `940abe1f`.
      `needs_republish` is now EMPTY — the whole finding went 7 → 0 on 2026-08-30.
      Originally measured
      2026-08-28: `docling_eval`, `openlineage` and `openmetadata` all carry
      an Egeria Project context of `unset`, and the publish route's Part 5
      gate returns 428 for `unset` unless an investigation supplies one by
      inheritance — none does. So the finding's own advice ("POST
      /api/egeria/{slug}/publish with steps ['repo_health']") is refused for
      exactly the repos it is offered for, and the panel does not say so.
      The three that CAN run it are the three never-catalogued ones (`docs`,
      `enterprise_rag`, `genaicomps`), which are `linked` — the inverse of
      what the wording implies. Either the finding names the gate, or the
      gate's 428 body carries the remedy; deciding which is the work.
- [x] Answered 2026-08-30 — RE Test Project, for all three. Originally: which Egeria Project each of
      the three belongs to. That names a real catalog object and stays a
      human call.
- [x] **Question catalog wired to the analyses that answer it** — 2026-08-28
      (`f0abe27`, `41b5c5f`, `b11536a`). The generator carried
      `KNOWN_ANALYSIS_IDS` as a hand-synced literal of 15 against a real
      catalog of 29, so 14 analyses were unrecognizable to it and a CSV row
      naming one was silently emptied and tagged `unknown` — the reason 18
      analyses had no question. Reads the catalog live now. Three stale GAP
      notes closed, three questions that shared `repository_health` given
      their own sources, eight questions added. 49 questions; 6 analyses
      unreferenced and correctly so (refresh actions, not questions).
- [ ] **9 questions still answer nothing** (6 `gap`, 3 `unknown`). Six need a
      content read nothing does yet — upgrade process, CLA/DCO provenance,
      telemetry detection, AI/ML model-card licensing, secret handling,
      similar-repo search. Three carry authorial uncertainty in the CSV
      ("repo_conventions - RAG read?") and want a decision, not a surveyor.
      This is the concrete shape of §5's agent-based post-ingestion work.
- [x] **Automate can schedule a whole survey** — 2026-08-28 (`fd63eed`). Third
      sub-tab, `GET /api/survey-definitions/definitions` (read from the
      authored documents, so it survives Egeria being down), `target_kind`
      exposed on the schedules route. §4a is done except moving
      `RepoFullSurvey` out of the four stage tabs.
- [ ] **`repo_profile_refresh` is schedulable but invisible to the step map.**
      `REPO_ANALYSIS_STEP_MAP.get("repo_profile_refresh")` is None, so a reader
      checking the map concludes it cannot be scheduled. It can: `scheduler.
      _run_repo_survey` intercepts it earlier on `action == "profile"` and
      chains `language_file_classification` afterwards. Same shape as
      `rag_ingestion`, which is intercepted on its id. Nothing is broken; the
      map simply is not the whole dispatch story and reads as though it were.
      Either the map carries these entries or its name stops implying
      completeness.
- [x] **The schedules route now checks the resource exists** — `a11ddc4`.
      404 per resource kind on POST; DELETE deliberately unguarded, since
      clearing a schedule that points at a deleted resource is what it is for.
      Two existing tests were passing on slugs that had never existed and now
      register their resources.
- [ ] Egeria-side: `_parse_graph` now reads branching definitions, but no
      definition uses a guard yet. Authoring one would exercise the whole chain
      end to end on real data.

---

## 6. Raised 2026-08-31 — the security/survey thread

Everything below came out of one session. Design reasoning lives in
`survey-model.md (§1)` (98608f1); this is the do-list.

### Done, do not redo
- [x] `security_scan` renamed **Security Hygiene** with an honest description (98608f1). Id unchanged.
- [x] `cii_badge` SSH-remote false negative — `git@github.com:...` reported a silver badge as
      `not_registered` (3c71785).
- [x] `ephemeral_prefect` autouse + the import note (a8dd281, e44300e).

### Direct asks, not started

- [x] **Results → Dashboard rename** — done 2026-08-31 (`33d0034`). 5 label strings; the 24
      internal ids kept. The placement comment had called it "where a run's results naturally get
      looked for", which was the conflation stated as a rationale.
- [x] **Admin → Observe → 💬 Feedback** built 2026-08-31. `GET /api/curate/feedback` plus
      `list_all_resource_feedback` / `count_all_resource_feedback`; filter chips by entity type and
      category. Three deliberate choices, each pinned by a test: `entity_type` defaults to empty
      rather than `"repo"` (feedback exists on databases and filesystems too, and a repo default
      would make those look unused); the route returns `filtered` so an empty list can say which
      empty it is; and an absent rating renders `—` rather than zero stars, since the column is
      nullable and "no rating given" is not a judgement of zero.

      Observe is no longer a group of one. The log viewer is the remaining occupant.
- [x] **Admin regrouped** 2026-08-31 — **Configure** (Annotation Types, Groups, Discovery Sources,
      Question Catalog) · **Reconcile** (Egeria Alignment, Egeria Links, Publish Queue, Repair) ·
      **Observe** (Prefect). Grouped by the question a person is asking, not by subsystem: grouping
      by subsystem would put Egeria Alignment beside Annotation Types, which is a taxonomy of the
      code rather than of the errand. Visual only — every button still calls `showMainView()` with
      the same tab id, so routing and the single-active-pane architecture are untouched.
      `test_admin_subnav_grouping.py` guards that no pane is dropped, duplicated, or invented — a
      pane left out of a group does not error, it silently becomes unreachable.

      **Observe holds only Prefect on purpose**: it is the home for the two panes below, and a
      group of one that names a gap beats folding it somewhere it does not belong.

      **Layout corrected same day, from the running app.** The first version was a single row of
      buttons with inline group labels. With eleven panes it wrapped, and a wrapped row left a group
      label stranded mid-line reading as a stray word rather than a heading — grouping only helps if
      the group is visibly whole. Now one native `<select>` per group, matching the two dozen
      already in this file: width no longer scales with the number of panes, the group holding the
      current pane is highlighted and shows it as its selection, and the others show their own name
      as a disabled placeholder. No custom menu, so keyboard and screen-reader behaviour come free.
- [x] **Logging wired** 2026-08-31 — `observability/logging_setup.py`. Root now has a formatted
      console handler (timestamp, level, logger name) plus a bounded in-memory `RingBufferHandler`;
      level from `$RE_LOG_LEVEL`, default INFO. `configure_logging()` is idempotent and called from
      both the CLI and the web app's import, and `uvicorn.run()` is given a matching `log_config`.

      The trap found while writing it, now guarded by a test: `dictConfig` **replaces** root's
      handler list when a `root` key is present, and uvicorn applies its `log_config` *after* our
      setup — so declaring `root` there would tear out the ring buffer, and the viewer would be
      empty under the server and full under the CLI.

- [x] **Log viewer built** 2026-08-31 — Admin → Observe → 📜 Logs, `GET /api/logs/` over the ring
      buffer. Level chips, logger-name prefix filter, opt-in 5s auto-refresh that stops itself when
      the pane is hidden. The three empties say three different sentences: filtered-out (naming the
      total held), genuinely empty (saying the buffer starts empty on restart, so it is not evidence
      nothing happened), and no-records. Levels are a fixed list rather than derived from the
      buffer — deriving them would hide ERROR from the filter exactly when no error has occurred.

      Not durable, by design. If logs need to survive a restart or leave the process, that is a
      different feature (file handler or OTEL export) and this pane should not be mistaken for it.
### Refresh as a survey, not a button

Reframed 2026-08-31: refresh wants to be a **schedulable survey in Automate** that decides whether
a refresh is needed and whether delta or full is cheaper — not a button. Design in
`survey-model.md (§1)` §2b (planner step).

Already built, and this is mostly assembly:
- Four refresh steps, all `queued` and independently schedulable: `repo_profile_refresh`
  (scouting), `manifest_parse`, `rag_ingestion`, `website_ingestion` (analysis).
- The Automate surface to hang it off — survey list beside Subscriptions and Schedules
  (`fd63eed`, 2026-08-30). This was §4a's blocker; it is gone.
- `POST /api/projects/{slug}/refresh` — delta RAG ingestion + query-cache invalidation. Nothing in
  the UI calls it (the `/refresh` hits in `index.html` are Discovery Sources).

Missing:
- [ ] A **Refresh Survey Definition** bundling the four.
- [ ] The **planner step** at the front of it: last SHA vs live head, which tables are populated,
      how stale each is → decide per-step what runs.
- [ ] A **per-step "still current?" check.** Only `IncrementalIndexer.refresh` SHA-gates today; the
      other three redo their work regardless.

Two traps, both already visible in the code:
- **The gate cannot be survey-wide.** `IncrementalIndexer.refresh`'s no-change path still profiles
  data files if that was never run. Unchanged ≠ complete; a survey-level skip would skip work never
  done.
- **Cheap acquisition ≠ cheap compute.** `SourceCache` is keyed on (repo, SHA), so an ungated step
  costs 1.28s warm rather than 22.6s cold — which *hides* a missing gate instead of replacing it.
  The parse/profile/embed work still repeats in full. (Not clearing `SourceCache` on refresh remains
  correct: a stale hit is impossible.)

### The ~30s stage load — fixed 2026-08-31

Two independent causes, neither of which was work — both were waiting.

- [x] **Server: one Egeria round trip per question, in series.**
      `find_candidate_process_guids_by_questions` resolved each question's GUID one after another.
      Ten questions for a stage, **49** for the unscoped `automate_full` lookup (no `phase` means
      every cataloged question), so the two calls together made ~59 serial round trips. Now resolved
      through a small thread pool, order-preserving. Measured on a deterministic harness — 49
      lookups at a fixed 0.2s: **9.8s → 1.4s, 6.8×**.
- [x] **Client: the two fetches were awaited one after the other.** Now `Promise.allSettled`, not
      `all` — the cross-stage fetch was already non-fatal and `all` would let its failure take the
      stage's own surveys down.

`test_candidate_lookup_concurrency.py` pins the properties rather than the timings: input order
preserved, unresolvable questions dropped rather than paired with `None`, the client warmed on the
calling thread, no `await` between the two fetches, and both filters still sent.

**Two things worth keeping from doing it.** The `no_silent_success` ratchet caught a `try/except:
pass` I added to pre-warm the client, and the fix was better than the code it rejected: warm by
resolving the first question on the calling thread, through `resolve_question_guid`'s existing
error handling, so no new silent-failure site exists at all. And the live before/after measurement
had to be abandoned — Egeria became far slower mid-session than when the 24s was measured, so the
deterministic harness is the honest number and the 24s is not comparable to anything measured after
it.

### Catalog honesty — one instance fixed, the class open

- [x] **`documentation_coverage` description fixed** 2026-08-31. It claimed "README completeness…
      inline comment coverage"; it checks which doc collections were ingested plus a hygiene-file
      list, banded by signal count. Name left as-is — see below.
- [ ] **Is "Documentation *Coverage*" still an overclaim?** "Coverage" implies a measured ratio and
      nothing computes one. Weaker than `security_scan`'s case, so not renamed unilaterally; decide
      deliberately rather than leaving it unexamined.
- [x] **Terse descriptions audited** 2026-08-31 — all 15 of the under-200-char repo entries, against
      their implementations. Result: **3 overclaims in 15**, all now fixed (`security_scan`,
      `documentation_coverage`, `api_structure`). The discursive half was not re-audited; every
      entry sampled there states its own limits.

      `api_structure` was the worst of the three: it claimed to *extract* from "Python, JavaScript,
      and OpenAPI". It extracts nothing (it reads `project_code_symbols`, populated during
      ingestion), the language list omitted Java and Go, and **no OpenAPI handling exists anywhere**.

      Judged honest, no change: `license_classification` (tiers match the code exactly),
      `rag_ingestion`, `repo_profile_refresh`, `repository_health`, `language_file_classification`,
      `repo_classification`, `sub_resource_survey`, `egeria_publish`, `ci_quality`,
      `security_features`, `repo_conventions`.

- [x] **`data_file_profiling` — was a real defect, not the nuance it was logged as.** Fixed
      2026-08-31. The columnar paths emitted `null_pct: 0.0` for every column of every
      Parquet/Feather/Arrow file — an unmeasured value rendered as a confident measurement, since
      file metadata carries no null rates. Both UI consumers showed it as a real zero, and one
      (`c.null_pct ?? 0`) would have kept doing so after the profiler was fixed. Now `None`, with
      a `null_summary` saying why, `—` in both UI surfaces, an honest description, and
      `test_data_profiler_absence.py` pinning it in both directions.

      Correcting the earlier entry: row counts are NOT missing for columnar formats — pyarrow reads
      them from metadata. Only null rates were, and the earlier note had that wrong.

- [ ] **Pattern worth acting on: "reads what something else produced" is the recurring lie.**
      Two of the three (`documentation_coverage`, `api_structure`) describe work done during
      ingestion as if this step did it, which also makes an un-ingested repo look like a deficient
      one. That is the same fact-about-us/fact-about-them distinction `cii_badge` keeps explicit.
- [ ] **Consider a ratchet** — an allowlist of audited entries that fails when a new one appears,
      in the shape of the orphaned-slug fix. Claim-verb detection is probably too fuzzy.

### From the design note — nothing built

- [~] **Discovery Security Survey and `Security_Assessment` — investigated and deliberately NOT
      built** (2026-08-31). All seven proposed steps already run: five in the Assessment Survey,
      two in Discovery — and the Assessment Survey already carries `cve_scan` too, so it *is*
      substantially the `Security_Assessment` the note proposed, minus the name. A new survey would
      have added a fourth copy of steps that already run, against a batch documented as
      non-idempotent. The design note's §1/§1b are wrong in the same direction as the earlier
      "retire security_scan" call: both reasoned from the analysis layer without checking what the
      survey layer already does. **Reopen only with a reason the existing surveys cannot serve.**
- [x] **Reducer step and topic-summary annotation** — built and published 2026-08-31 (`368cbe5`).
      `repo_security_summary` runs last in `RepoAssessmentSurvey` (10 steps) and after all its
      inputs in `RepoFullSurvey` (35). Carries coverage and the age of its oldest input, refuses a
      verdict below four inputs, and applies declared precedence. `foss_scorecard` is still an
      undeclared reducer — naming it as one remains open.
- [ ] **Four GAP questions with no analysis at all**: secret handling, telemetry/phone-home
      detection, CLA/DCO provenance, SLA/availability content. Secret scanning is what
      "Security Scan" claimed to do all along.
- [ ] **Per-stage summary dashboards** — deferred by decision, 2026-08-31. Stage *filtering* already
      works (`get_dashboard_stages`, membership not equality); summarising across stages does not.
      Open when defining it: derived membership double-counts, since `security_overview` belongs to
      both Discovery and Assessment.
- [ ] **Do findings need a run identifier?** `project_analysis_findings` has no run column. A run is
      identifiable only by a shared `surveyed_at`, by convention, and `query_findings` does not use
      it that way. Blocks "summarise this run" being a real query. See open question 5.

### Carried, not from today
- [ ] `UNVERIFIED` is computed and never persisted, so query time cannot reach it.
- [ ] `test_local_flow_execution_fallback` failed once on 2026-08-31 and did not reproduce — see
      Backlog "Test reliability" for what is ruled out. Do not treat Prefect teardown noise as a
      reproduction signature.


## 7. The redeploy + database wipe — DONE, verified 2026-08-31

**Outcome: the wipe happened, bootstrap healed it, and everything published that day came back
correctly.** Verified independently and read-only, against live Egeria rather than against RE's
registry:

    QUESTIONS              49/49 resolve
    RepoAssessmentSurvey   10 steps · repo_security_summary after all 8 inputs · terminal
    RepoFullSurvey         35 steps · repo_security_summary after all 8 inputs · rag_ingestion last
    reconciler --dry-run   0 duplicate / 0 stale across all EIGHT definitions

No manual re-authoring was needed. The recovery sections below are kept as the runbook for next
time, not as outstanding work.

**One measurement error worth keeping, because it is this file's own subject matter.** "Has Egeria
been wiped?" was first answered by counting `projects` (60) and `project_analysis_findings`
(68,215) — tables in **RE's own registry**, which an Egeria wipe does not touch by design. Those
counts read identically either side of the event and cannot distinguish the two states at all. The
question is only answerable by something that exists on one side of it:

    projects holding an egeria_asset_guid:  0 of 60   (non-zero before the wipe)
    project_egeria_surveys rows:            0

A correct count of the wrong population. Reach for the field the event actually changes.

---

## 7a. Recovery runbook — kept for next time

**Everything authored into Egeria today is destroyed by a redeploy.** The quickstart platform does
not persist custom-authored elements across restarts (`repo_survey_types.csv` row 2 records a
previous instance of exactly this). Nothing below is a code change — the code is committed and
safe. These are *deployments* that have to happen again.

### What has to come back, and in this order

`_folder_order.json` encodes the order and it is load-bearing: a Survey Definition's
`Link Element To Scope` commands bind to Question terms **by name**, so running definitions before
questions succeeds silently while creating no ScopedBy links at all.

1. **foundations** — glossary, perspectives, funnel stages.
2. **questions** — all 49 from `resource_questions.csv` via `questions/scouting-questions.md`.
3. **survey-definitions** — the repo documents, including `repo_security_summary` in both
   `RepoAssessmentSurvey` (10 steps) and `RepoFullSurvey` (35 steps).
4. **`scripts/reconcile_survey_definition_links.py`** — always, after step 3.
5. **survey-definitions-database** (added Slice 12, 2026-09-26) — the three
   database Survey Definitions (Scouting/Analysis/Assessment), in its own
   directory/canary/batch since `bootstrap.py` is one batch per directory
   and this batch shares no reconciler with the repo one above.
6. **`scripts/reconcile_database_survey_definition_links.py`** — always,
   after step 5, same non-idempotent-link reason as step 4.

`resource_explorer/bootstrap.py`'s `check_and_heal()` does every folder listed in
`_folder_order.json` (1, 2, 3, 5 above) by canary, each followed by its own post-heal reconcile (4,
6), so a wiped platform should self-heal on startup. **Verify rather than assume it did** —
the canary proves a batch *ran*, not that every element inside it exists. That is precisely how 8
of 49 questions went missing today.

### Run the FULL questions document after a wipe, not the subset

`questions/missing-questions-2026-08-31.md` exists because 41 of 49 terms already existed and
re-running the whole document would have re-fired 168 `Link Perspective to Question` commands
against them, with **no reconciler** to clean up after. After a wipe that reasoning inverts: nothing
exists, so nothing can be duplicated, and the full document is both correct and required — the
subset would leave 41 questions missing. **Delete the subset file once the full document has run
against the wiped platform**; keeping it is how someone runs the wrong one.

### Verification after the redeploy — the checks, not the impressions

```
# 49 of 49 questions resolve
uv run python -c "import csv; from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader as R, clear_caches; clear_caches(); r=R(); qs=[x['Question'] for x in csv.DictReader(open('docs/dr-egeria/resource_questions.csv'))]; m=[q for q in qs if not r.resolve_question_guid(q)]; print(f'{len(qs)-len(m)}/{len(qs)} resolve'); [print('  MISSING:', q[:70]) for q in m]"

# 0 duplicate / 0 stale on all 8 definitions
uv run python scripts/reconcile_survey_definition_links.py --dry-run

# both surveys load, reducer after its inputs
uv run pytest tests/test_egeria_live_smoke.py tests/test_survey_definition_graph_walk.py tests/test_security_summary.py -q

# the 12 perspectives exist — added 2026-08-31; the three checks above never looked
uv run python scripts/verify_perspectives.py
```

The perspective check was missing until 2026-08-31. Perspectives are authored by
`foundations.md` in the same batch as the glossary and funnel stages, so they fail the
same way 8 of 49 questions did — a heal reports success and some elements are simply
not there. It compares against `foundations.md` rather than
`egeria-redeploy-baseline-2026-08-26.json`; the two were verified identical that day, so
the snapshot adds nothing the repo does not already hold, and a generator says what
*should* exist where a snapshot only says what once did.

**It exits 2, not 1, when Egeria is unreachable**, and prints that this says nothing
about whether the perspectives exist. Written while the platform was down for this very
redeploy, so its lookup path has never run against a live server — the first real
exercise is your post-redeploy verification. A check that could not tell "not answering"
from "gone" would report 0/12 at exactly the moment that reads as catastrophe.

### What the wipe also destroys, and what it does not

- **Destroyed:** every `project_analysis_findings` row, so every analysis reverts to *never ran* —
  including today's `security_summary` result on `egeria_git`. Also the registry's projects,
  activity log, and pgvector collections.
- **NOT destroyed:** `data/source-cache` (on disk, keyed by commit SHA), and all code, tests and
  documents, which are committed and pushed.
- **In flight and lost:** a `manifest_parse` on `egeria_git` was running to populate dependencies so
  `cve_scan` could run and take the summary from 7/8 to 8/8. Abandoned — see below.

### Unfinished: the security summary is 7 of 8

`cve_scan` has never run on `egeria_git` because the repo has **0 dependency rows**, and cve_scan
scans dependencies already parsed. It correctly declined to report rather than claiming "no CVEs" —
that is the intended behaviour, not a failure. The chain to close it is
`manifest_parse` → `cve_scan` → `security_summary`.

**It cannot be closed out on Egeria at all, and the reason is not the one first assumed.** The
`--reload` bug was real and blocked `manifest_parse` from finishing — fixed by running without it,
after which the parse completed, published 4 annotations, and still produced **0 dependency rows**.

The actual cause is coverage: `dependency_parser.py` reads `pyproject.toml`, `requirements*.txt`,
`setup.py`, `package.json`, `go.mod` and `pom.xml`. **Egeria ships 239 `build.gradle` files and none
of those.** See `Backlog.md`, "No Gradle support in the dependency parser". Until Gradle is parsed,
`cve_scan` cannot run on any Gradle project and `security_summary` is capped at 7 of 8 there.

To see 8 of 8 after the redeploy, pick a repo with a supported manifest — `egeria_python_git`
(pyproject.toml) is the obvious one — rather than Egeria itself.

**Update 2026-09-01: Gradle support landed (`946a1b3`) and it did NOT close this. Measured, not
assumed.** A full survey of `egeria_git` now produces **216 dependency rows** where it produced 0,
so the parser works and the step ordering is fine (`repo_manifest_parse` is position 3,
`repo_cve_scan` 21 — verified, and now guarded by `tests/test_step_execution_order.py`).

`security_summary` still reports:

    input_coverage: partial — "7 of 8 security inputs have run. Never ran: cve_scan."

**The real blocker is one column: every one of the 216 rows has an empty `dep_version`.**

    dep_name='ch.qos.logback:logback-classic'  dep_version=''  ecosystem='java'  source_file='build.gradle'

Egeria's `build.gradle` files declare versions through a version catalog / variables rather than
inline, so the parser recovers the coordinate but not the version. A CVE lookup needs name AND
version, so `cve_scan` declines — correctly. Closing this needs version RESOLUTION (reading
`gradle/libs.versions.toml`, or a dependency lockfile), which is a different and larger job than
parsing `build.gradle`. **`Backlog.md`'s "No Gradle support in the dependency parser" is now done and
is not the thing standing in the way.**

**A second-order problem this exposes, and it is the more general one.** `cve_scan` declining writes
no findings, and `security_summary` reads an empty `query_findings(slug, "cve_scan")` as *never ran*.
So "declined for want of versions" and "never executed" are the same signal downstream — the
`NOTHING_FOUND` / `NEVER_RUN` conflation that `surveyors/result_status.py` exists to prevent, in a
place that does not use it. This is the concrete case for `Backlog.md`'s "No conditional execution of
survey steps": a step that cannot run should record `SKIPPED_BY_DESIGN` **with its reason**, so the
summary can say "cve_scan could not run: no dependency versions available" instead of implying nobody
ever tried.

**Update 2026-09-01 (later same day): version resolution landed, and `security_summary` is now
8 of 8 on `egeria_git`, measured.** `dependency_parser.py`'s Gradle path now resolves a version from
two places, cheapest first, tagging each resolved row with WHERE it came from
(`dep_version_source`, a new `project_dependencies` column) rather than folding it into the same
bucket as a version the manifest wrote literally — that provenance was the actual constraint on this
work, not the resolution itself: a wrongly-substituted version feeding a confident CVE answer is
worse than declining:

  * a same-file `ext { xVersion = '...' }` / Kotlin `val` variable, tagged `"variable_interpolation"`
  * `gradle/libs.versions.toml`, tagged `"version_catalog"`

Egeria's own shape turned out to be case 2 dressed as case 3: `bom/build.gradle` looked like a BOM
worth punting on, but reading it shows every one of its ~90 `constraints { api("g:a:${xVersion}") }`
lines resolves through a same-file `ext` variable — no actual external BOM/platform artifact, no
Gradle invocation needed. Re-measured on the same checkout:

    latest batch: 85 java dependency rows, 81 with a resolved dep_version
    dep_version_source counts: variable_interpolation=80, declared=1, ''=4 (unresolved)

(85, not 216/288 — the earlier counts were summed across every historical ingestion batch this
project ever had, not the current one; `query_dependencies()` already filters to the latest
`indexed_at` batch, which is what `cve_scan` reads.) Running `CveScanSurveyor` against that gave:

    68 advisories across 14 package(s), from 81 of 85 recorded dependencies (java);
    4 dependenc(ies) could not be checked (no pinned version to query)

and `SecuritySummarySurveyor` immediately after:

    Security: concerns — 9 finding(s) of concern, 15 clear, 24 not established,
    across 8 of 8 inputs. Every input has run.

Version-catalog resolution (BOM/platform imports proper, and `gradle/libs.versions.toml`) is
implemented and unit-tested (`tests/test_gradle_dependency_parsing.py`) but unexercised by Egeria
itself — it uses no version catalog (measured: zero `libs.` references across all 229
`build.gradle` files) and no real external BOM artifact once `bom/build.gradle`'s own constraints are
read. A dependency lockfile path was not implemented; none was found in this checkout, and no
project surveyed here has needed it yet.

The `NOTHING_FOUND`/`NEVER_RUN` conflation two paragraphs up is still real and still unaddressed —
this closed the one path that was blocking it on Gradle projects, not the general problem.


## Done 2026-08-26/27 — do not redo

Three Survey Definition hazards (generator overwrite, reconciler deleting
branches, drift scan conflating recovery with coverage); annotation types
published by name; whole-survey sequencing handed to Prefect; the reader
reading branching definitions; four new analyses (FOSS scorecard extension,
CVE scan, CHAOSS, CII badge); pyegeria 6.1.5 upgrade.
