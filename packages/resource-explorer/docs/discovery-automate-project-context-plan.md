# Discovery re-scoping, Questions-per-phase, Automate phase, and Project Context — plan

**Status: all 5 parts built and live-verified (2026-08-13). Part 1 (Discovery
re-scoping), Part 2 (new Discovery-tier analyses), Part 3 (Questions-per-phase
reorg), Part 5 (Egeria Project context), Part 4 (Automate — local-first,
per the 2026-08-13 decision below) all shipped. Kept below for reference —
see each part's own section for what actually got built vs. what's still
spec-only (Part 4's real-Egeria-NotificationType piece, see
`docs/automate-notification-manager-pyegeria-spec.md`).**

## Context

Grew out of reviewing what "Repo Full Survey" (the just-shipped Discovery fix) actually is: a
survey chaining *every* currently-registered repo step, which conflates "what an actively-tracked
repo should keep re-running" with "what Discovery needs to help decide whether to pursue a repo in
the first place." That distinction opened up four related design threads, each now grounded in
real standards/precedent rather than invented from scratch, per explicit direction to ground this
in standards and best practices and say so.

## Standards & prior art grounding this plan

Direct input, cross-checked against what's already cited in `docs/dr-egeria/resource_questions.csv`
(where several of these already appear as candidate deep-scan tools for later phases). Named here
explicitly so the UI/docs can say "this check is modeled on X," not present a bespoke heuristic as
if invented from nothing.

- **[OpenSSF Scorecard](https://github.com/ossf/scorecard)** — the single strongest match for Part
  2's whole list. Scorecard's own check taxonomy (`Security-Policy`, `CI-Tests`, `Branch-Protection`,
  `Maintained`, `License`, `Code-Review`, `Vulnerabilities`, `Dependency-Update-Tool`) overlaps
  almost check-for-check with what's proposed below (Security Policy, Automated Build/CI, License —
  already shipped as `license_classification` — Maturity/"Maintained"). Recommendation: name Part
  2's new check_names to visibly parallel Scorecard's own where they overlap (e.g. a
  `security_policy_present` check mirroring Scorecard's `Security-Policy`), and cite Scorecard
  directly in each new surveyor's module docstring, the same way `LicenseClassifierSurveyor`
  already cites SPDX.
- **CHAOSS** (GrimoireLab/Augur, Linux Foundation) — grounds the Maturity metric (Evolution/
  Diversity/Risk categories; release frequency, time-to-first-response, bus factor). Already named
  as a candidate for deep community-velocity scanning in `scouting-questions.csv`.
- **OSV.dev, OWASP Dependency-Track, Trivy/Grype** — already the CVE-scanning candidates named in
  `scouting-questions.csv` for a future `dependency_analysis` deep-scan upgrade; reinforced here,
  not new.
- **SPDX** (ISO/IEC 5962) + **CycloneDX** — the two real SBOM standards. SPDX already grounds
  `license_classification`'s risk-tier lookup (B1); CycloneDX is the dependency/component-origin
  parallel, worth citing alongside SPDX when `dependency_analysis` grows an SBOM-export capability.
- **LicenseFinder, FOSSology, ClearlyDefined** — already named in `scouting-questions.csv` as the
  future deep-scan upgrade path beyond `license_classification`'s current static SPDX-id lookup
  table (B1's own docstring already says as much).
- **Backstage** (CNCF) — a genuine peer/alternative to what RE + Egeria are building (a developer
  portal / software catalog with `Component`/`System`/`Resource` entities and ownership tracking).
  Not being adopted, but its `catalog-info.yaml` convention is real prior art worth detecting
  directly: **new Part 2 check** — does the repo already carry self-describing catalog metadata
  (`catalog-info.yaml`, or similar conventions from other catalogs) — a strong, cheap signal that a
  repo is already integrated into *some* enterprise inventory, valuable "early headlights" context
  for Discovery regardless of which catalog it names.

## Decisions

1. **Automate is a new, 8th canonical intent** (rule-17 change) — not an Admin sub-pane. It likely
   absorbs and retires Admin's existing Schedules monitoring pane rather than sitting alongside it
   (the global "what's scheduled, did it succeed" overview becomes Automate's own view); the
   per-card "⏱ Schedule" action on individual Assessment/Analysis/Discovery cards may stay where it
   is or also move — decide during implementation, not blocking the design.
2. **"Just exploring" stays purely local** — no `PersonalProject` Egeria element is ever
   auto-created for casual exploration. A real Egeria `PersonalProject` is only created if a user
   explicitly asks for one later (e.g. to group several personal explorations under one real,
   catalogable project).
3. **Sequencing** (proposed, lowest-risk/most-proven-pattern first — open to reordering):
   1. Part 1 (Discovery re-scoping) — mechanical, reuses the just-shipped generator tooling.
   2. Part 2 (new Discovery-tier analyses) — same B1-B4 surveyor pattern, now Scorecard-grounded.
   3. Part 3 (Questions per phase) — reuses Scouting's proven UI pattern + existing generic reader.
   4. Part 5 (Project Context) — bounded scope (one table, one header control, one modal); also
      gives Part 4 useful subscriber-identity context once built.
   5. Part 4 (Automate/8th intent) — biggest, most novel surface; benefits from 1-3 already
      shipping more analyses worth watching, and from 5 existing for subscriber context.

## Part 1 — Re-scoping Discovery

Discovery's own Survey Definition should be its own, leaner thing — steps that only need data
*already collected* by Scouting/Profile (file inventory + GitHub stats already in
`project_file_inventory`/`project_stats`), zero new zipball download, zero RAG. "Repo Full Survey"
(all 14 current steps) is better understood as the **Automate phase's default recurring bundle**
(Part 4) or a manual "run everything" convenience on an already-tracked repo — not Discovery's
launch target.

| Phase | Question | Data source | Steps (existing + planned) |
|---|---|---|---|
| Scouting | Is this active enough to look closer at? | GitHub API only | `repo_health`, `repo_language` |
| **Discovery** | Should we pursue this further — early headlights | Already-collected file inventory + stats, no new fetch | New leaner set — Part 2 |
| Assessment | Scored evaluation against criteria | Same data, deeper/scored | `security_scan`, `documentation_coverage`, `license_classification`, `security_features`, `ci_quality` (all shipped) |
| Analysis | Structural facts needing the zipball's actual content | `dependency_analysis`, `api_structure`, `data_file_profiling` (all shipped) |
| Understanding | How is this changing | Trend charts over any of the above |

## Part 2 — New Discovery-tier analyses (from already-collected data only)

Checked each proposal against what's already built to avoid duplicating existing steps:

- **Maturity** — real gap, but `repo_health` (`HealthSurveyor`) already computes activity/
  community/release-cadence/freshness from GitHub stats, so this may be a reframing/extension of
  that rather than a wholly separate metric. Worth grounding in **CHAOSS** (Community Health
  Analytics for Open Source Software) — a real, citable metric model with defined categories
  (Evolution, Diversity, Risk) and concrete metrics (release frequency, time-to-first-response,
  bus factor) rather than an invented scoring formula. Say explicitly in the UI/docs that this is
  CHAOSS-informed, not CHAOSS-certified — we're borrowing the metric *shape*, not implementing
  their full spec.
- **Security Policy** — genuine gap. `repo_security` only checks *file presence* (does
  SECURITY.md exist), never content. New: keyword-scan the first N lines of SECURITY.md/README for
  policy language ("responsible disclosure," "report a vulnerability," a contact email/PGP key) —
  same heuristic-keyword-scan pattern as `CiQualitySurveyor` (B4), explicitly two-tier: this
  heuristic now, a real RAG-confirmed answer once ingestion has happened (Assessment/Analysis
  tier, later).
- **Automated Build** — partial overlap with `ci_quality`'s `ci_runs_build` (CI-*triggered*
  builds). A distinct, independent signal: build-tooling *presence* regardless of CI wiring —
  `Makefile`, `build.gradle`, `pyproject.toml`'s `[build-system]` table, `webpack.config.js`. Keep
  both — a repo can have a Makefile with no CI, or CI with no local build target.
- **Deployment / Docker** — clean new gap: `Dockerfile`, `docker-compose.yml`/`compose.yaml`,
  `Chart.yaml` + `templates/` (Helm), `.dockerignore` as a weak corroborating signal. Same
  `file_exists()`/basename-scan pattern as CODEOWNERS (B3).
- **Documentation/README breadth** — genuine gap. `DocumentationSurveyor` only checks the
  *root* README/CHANGELOG/etc. today, never walks the tree. New: README count across the whole
  repo, a `docs/`-or-synonym folder, raw `.md`/`.txt` count as a density proxy.
- **Existing catalog self-description** — new, from the Backstage precedent (see Standards
  section below): does the repo already carry `catalog-info.yaml` (Backstage) or an equivalent
  self-describing metadata file? A strong, cheap "this repo is already integrated into *some*
  enterprise inventory" signal, independent of which catalog it names.
- **Also worth adding** (roughly ranked): test-directory presence (`tests/`/`spec/`/`__tests__/`)
  as a signal independent of `ci_runs_tests` (a repo can have one without the other); monorepo/
  multi-package detection (multiple `package.json`/`pyproject.toml`/`go.mod` at different paths —
  a real "how complex to integrate" headlight); vendored-dependency detection (`vendor/`,
  `third_party/`, committed `node_modules`) as an integration-risk signal. Lower-confidence/later:
  README badges (build/coverage/license shields) — cheap but noisy/gameable.

Each gets the same `trend_reader` treatment as B1-B4 from day one (a maturity score, doc-file-count,
etc. changing over time is exactly the kind of signal worth tracking, not bolted on later).

## Part 3 — Questions tab, generalized to every phase

Confirmed: the infrastructure is **already phase-agnostic**, not Scouting-specific — despite only
Scouting having a built UI tab today. `QuestionCatalogEntry` (`question_catalog_reader.py`) already
carries independent `asked_at`/`answered_at` Funnel Stage fields, and `docs/dr-egeria/foundations/foundations.md`
already defines 9 Funnel Stage terms, not just one. So this is not a schema change — it's:

1. **Reorganize `docs/dr-egeria/resource_questions.csv`** — move questions whose `Asked At`/
   `Answered At` more honestly belong to Assessment/Analysis/Understanding/etc. (per your note),
   and fold in whatever new questions Part 2's new analyses answer. Likely renamed from
   `scouting-questions.csv` to something phase-neutral (e.g. `funnel-questions.csv`) once it's not
   Scouting-exclusive — flag during implementation, not a big decision now.
2. **Regenerate** `question_catalog.yaml` and the Dr.Egeria markdown doc via the existing
   generator scripts (`csv_to_question_catalog_yaml.py`, `csv_to_dr_egeria_questions.py`) —
   unchanged tooling, just re-run against the reorganized CSV.
3. **Build the Questions sub-tab UI for each other phase**, reusing Scouting's exact existing
   pattern (`loadScoutingQuestions`/`renderScoutingQuestions` et al. in `index.html`) — this is
   mechanical repetition of a proven pattern, not new design.

## Part 4 — Automate phase, grounded in Egeria's real Notification Manager

Confirmed via the [Notification Manager OMVS](https://egeria-project.org/services/omvs/notification-manager/overview/)
docs and pyegeria/Dr.Egeria source: this is real, **Stable**, usable-today Egeria infrastructure —
not something to invent from scratch.

**What's real and already usable via Dr.Egeria** (Governance Officer command family):
- `Create Notification Type` — a catalogable `NotificationType` (a classified
  `GovernanceDefinition`), with real scheduling-relevant attributes already defined: `Notification
  Interval`, `Minimum Notification Interval`, `Next Scheduled Notification`, `Notification Count`.
- `Link Monitored Resource` — attaches a `NotificationType` to the resource being watched (e.g. a
  repo's cataloged asset).
- `Link Notification Subscriber` — attaches a `NotificationType` to a subscriber (an Actor/person).

**What's real but not yet wrapped as convenience** — no `find_notification_type`/list-subscribers
method exists in pyegeria's `NotificationManager` client; finding/listing goes through the generic
`GovernanceOfficer.find_governance_definitions(find_constraints={"metadata_element_type":
"NotificationType"})`. No `Update`/`Detach` Dr.Egeria commands exist yet (only `Create`/`Link` —
the underlying pyegeria `detach_monitored_resource`/`detach_notification_subscriber` methods exist
but aren't exposed as markdown commands).

**What's not confirmed present in this environment**: no `WatchdogAction` implementation found
anywhere in the codebase — the piece that would *automatically* detect a triggering event
(metadata change, threshold crossed) and fire a subscription. This is the one part of Notification
Manager that reads as more aspirational-scaffolding than proven-working here.

**Proposed architecture, matching the Survey Definition precedent exactly** (Egeria = catalog of
record for *what's subscribed to what*; RE = the thing that actually *detects and acts*, same
split as `executes_at: resource-explorer` steps in the Survey Definition work):
- Automate phase lets a user create a real `NotificationType` (e.g. "License risk tier changed"),
  link it to a tracked repo (`Link Monitored Resource`), and link themselves or a team as
  subscriber (`Link Notification Subscriber`) — this bookkeeping lives in Egeria, portable and
  standards-based, same as Survey Definitions.
- The actual **detection** — did the thing this NotificationType describes actually happen — is
  RE's own `scheduler.py` (already runs recurring surveys) comparing this run's findings/metrics
  against the last, since no confirmed-working Egeria watchdog exists to lean on yet.
- The actual **delivery**, for a v1, is RE's own existing RFA mechanism (already has a drawer,
  defer/reassign/complete) rather than inventing a new local notification UI — "something
  interesting turned up" becomes a new RFA, tagged with which `NotificationType`/subscription
  produced it. A real push channel (email/Slack) is future work, not v1.
- **Decided**: Automate is a new 8th canonical intent (Decisions §1 above) — philosophically
  parallel to Curate (sustained *human* attention vs. sustained *machine* attention). Likely
  absorbs Admin's existing Schedules monitoring pane rather than duplicating it.

**Built (2026-08-13), local-first per an explicit follow-up decision**: when it came time to
implement, "Create Notification Type" turned out to have no dedicated pyegeria method — Dr.Egeria's
own command wraps the same generic, untested-for-this-type `create_governance_definition()` body
construction CLAUDE.md rule 12 warns against reimplementing, and RE's backend has never executed
Dr.Egeria markdown live at runtime (only ever an out-of-band, assistant-driven authoring step, e.g.
Survey Definitions). Decided: ship the detection/delivery architecture above exactly as designed —
`notification_subscriptions` (registry.py), `notification_detector.py` (generic latest-two-runs
comparison), `scheduler.py`'s `_check_subscriptions()` (RFA delivery) — fully locally, with
`egeria_notification_type_guid`/`_qualified_name` columns present but empty until a safe pyegeria
convenience API exists. That API is specified, not built, in
`docs/automate-notification-manager-pyegeria-spec.md`. Admin's Schedules pane was **not** absorbed
in this pass — Automate ships as a fully additive, separate view; absorption stays a live option,
not attempted here. `web/routes/automate.py` + a "🔔 Notify me" action on every Assessment/Analysis
card + a global Automate tab (`loadAutomatePanel()`) are all live and tested.

## Part 5 — Project Context ("uber intent")

**A real naming collision, caught before it caused confusion**: RE already has its own `Project`
class (`registry.py:35`) — that's "a registered repo/resource," completely unrelated to what's
being proposed here. Egeria *also* has a real `Project` open-metadata entity type. These must
never be conflated in code or UI. Proposal: always say **"Egeria Project"** in any UI text or new
identifier when referring to this concept — never bare "Project," which in this codebase already
means something else.

**Grounded in Egeria's real model** (confirmed via pyegeria's `ProjectManager` + Dr.Egeria's
`Projects` command family): `PersonalProject`, `Campaign`, `StudyProject`, `Task` are all
**classifications on one generic `Project` entity type**, not separate types — confirmed by
reading the actual body construction, not inferred from naming. Critically, **`PersonalProject`
is a real, already-defined Egeria concept** — its own Dr.Egeria description: *"an informal project
created by a single person to organize part of their work"* — which is close to word-for-word what
you described as the default "personal exploratory project" case. This is a strong sign to use
Egeria's own vocabulary rather than inventing RE-local terminology.

Real, usable mechanisms:
- `find_projects(...)` / `get_projects_by_name(...)` — search existing Egeria Projects.
- `get_linked_projects(actor_guid)` — "projects I am a member of" (no dedicated "my projects"
  method exists; passing the current user's actor GUID here is the real mechanism, confirmed).
- `add_to_project_team(project_guid, actor_guid, ...)` — real membership.
- `Create Personal Project` / `Create Project` / `Create Campaign` / `Create Study Project` —
  all real Dr.Egeria commands, same "Action Author"-adjacent low-risk markdown-generation +
  execution pattern already proven for Survey Definitions.

**Proposed flow** (your description, now mapped onto real Egeria elements):
1. **Default state, per resource**: `unset` — no prompt yet, nothing written to Egeria.
2. **First moment a real Egeria write would happen** (first `EgeriaPublisher.publish` call for
   that resource, not at registration) — if still `unset`, prompt, not before: four options, all
   explicit, none silently assumed —
   - **"Just exploring"** (default/one-click) → local `personal` status. Deliberately *not*
     auto-creating a `PersonalProject` Egeria element for every casual exploration — that would
     spam the catalog. Store `personal` locally only; a real `PersonalProject` element is created
     only if the user later wants one explicitly (e.g. to group several personal explorations).
   - **Pick an existing Egeria Project** — list surfaced via `get_linked_projects(current_actor)`
     (projects you're already a member of), with `find_projects()` as a fallback search.
   - **Type a project name now, correlate later** — `deferred` status, free text stored, real GUID
     linkage resolved in a Curate-tier follow-up (matches your "defer correlating till a
     downstream step" idea exactly).
   - **Explicit skip / decline to associate** — must be its own deliberate button, not a side
     effect of dismissing the prompt (matches the session's existing "never silently default to
     the destructive/uninformative option" convention).
3. **Where it lives in the UI**: not a full 8th `#intent-nav` tab (this isn't "a place you go do
   work," it's cross-cutting identity/context) — proposed as a header-level control, mirroring how
   "Connected as: erinoverview" already works (decoupled from `#intent-nav`, per CLAUDE.md's own
   pattern for RFA/Chat/Activity/Admin). A small "📁 Egeria Project: <name | Personal | Unset>"
   badge, clickable to open the same picker described above at any time, not just at first-publish.

**New table** (name TBD — avoiding `project_*` prefix, which the registry already uses for RE's
own `Project`): something like `entity_egeria_project_context(entity_type, entity_slug, status,
egeria_project_guid, egeria_project_qualified_name, free_text_name, decided_at)`, `status` ∈
`unset | personal | linked | deferred | declined`.

## Not designed here (explicitly deferred)

- Real push notification channels (email/Slack) for Automate — v1 is RFA-only.
- Egeria-side watchdog/automatic trigger detection — no confirmed-working implementation found;
  RE's own scheduler carries this weight for now, matching the Survey Definition precedent.
- CHAOSS metric implementation detail for Maturity — named as the grounding reference, not
  scoped to a specific formula yet.
