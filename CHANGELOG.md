# Changelog

All notable changes to this repository (Resource Explorer, Egeria Advisor and the shared `trellis-*` packages)
are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases are named after the
Egeria release they ship with. Pull requests are `#N` in this repository.

## [Unreleased]

## [6.3 Tech Preview] - 2026-11-19

First recorded release, a Tech Preview released with Egeria 6.3. It covers everything merged since the October 2026 Egeria newsletter, which introduced
Resource Explorer and Egeria Advisor as early previews. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the narrative.

### Added

#### Resource Explorer
- **Publish to Egeria**, with zones, and a check that the results arrived: Resource Explorer places what it created in
  zones, but not the elements it only refers to; private members are published only as their owner; a Publish button
  explains any failure in plain language (#644; see the PRs for follow-ups).
- **Surveys run by Egeria's engine host**: one card per Survey Definition with a run line, history and Cancel
  (#729, #735); engine-host steps and two staleness measures (#658, #663, #699); an OpenLineage run event per step (#646).
- **Database analysis**: per-table heap and index sizes (#582); survey flags, materialized views and activity (#627);
  value profiles and quality checks with sampling settings at every scope, column-profile verdicts kept locally and
  sent by Publish, and a scheduled quality survey across a work list (#759, #767, #772, #880, #893, #894).
- **Set steps**: a `schema_comparison` step (#952) and a set-steps section on work-list pages (#944).
- **Declared extensibility**: a step's declaration, the Curate publish table and the new questions a step adds
  (#885, #886, #916, #919).
- **Files**: Egeria's CSV survey read, Parquet profile and Delta Lake layout signal, narrative passages searched by chat
  (#868, #912, #977).
- **Open data**: publish a single dataset and check it arrived (#946); a repository known only by its DOI shows what is known about it (#982).
- **Locator screen** and chat citations on screen (#913); catalog resource type, and Egeria Overview questions as rows (#1002).
- **Chat**: the first index of a repository is asked about first, with per-repository consent (#777); Ollama native
  endpoint (#779); Phoenix tracing (#745); markdown rendering in the chat rail and journal preview (#855, #861).
- **Compile**: an as-of date on every evidence section (#1021, #1024).
- **Next UI**: cards and tiles with At a glance and Survey & analyses (#776, #778, #782); a Look control with Dark, Light
  and Paper (#765); every data table resizes (#942); a generic graph renderer, deployment topology first (#831).
- **Tutorial** parts 1 to 6 (#969, #970, #972, #975, #983, #990).
- **Feedback with a closed loop** using `trellis-feedback`: ratings carry a cause and evidence, admin triage, a
  diagnosis can be routed to a gap (#1012, #1013, #1017, #1018, #1019, #1031, #1032, #1034).
- **Re-index**: rebuild, swap and roll back the document index (#1037, #1038, #1039, #1040, #1041).
- **Survey steps on Prefect workers**: the Resource Explorer image has a `prefect-worker` role, and each step goes to a
  worker running the same commit. The status banner reports workers from Prefect's own status, and says separately
  when the Prefect server can't be reached or the pool is missing (#1091). The configured pool (`PREFECT_WORK_POOL`)
  decides where a step goes, and Resource Explorer names any of its workers that are running on a different pool (#1092).
- **File-system survey bundle** (#1114): file-system questions answer from the runs that answer them (C1); the
  survey card tells the truth and updates without a reload (C2, TR-70); Publish says what it does and re-publishes
  without duplicates, and Curate reviews the proposals (C3). The data-class send to Egeria is built but off, behind
  `RE_FS_DATA_CLASS_PUBLISH`.
- **Per-step Prefect dispatch** (#1110): `PREFECT_DISPATCH_STEPS` takes step keys, `heavy` or `all` (default: none);
  each step's placement (worker, in-process, or fallback with its reason) is recorded and shown in `/prefect/status`.
- **Recovery after an Egeria reset**: a new Alignment step, *re-attach by name*, reconnects Resource Explorer's records
  to Egeria using the qualified names it stored, with a dry run first. Alignment Apply now writes an activity entry for
  each step it applies (#1087). The recovery is one ordered procedure in the operations guide (#1088).

#### Egeria Advisor
- **Dashboards in Egeria**: lists and shows Egeria-held sheets as the signed-in user (#1042), then reads and authors
  them at parity with the Portal (#1067, #1068); Used by shows display names (#1070);
  large tables scroll inside their tile and a shortened table says what it left out (#1084); a folder that is empty
  or can't be read says why (#1086).
- **Report specs in Egeria**: syncs report specs held in Egeria, retiring the Phase-1 loader (#1033); publishes and
  withdraws specs, restricted per user, with Used by (#1060).
- **Perspectives from Egeria**, with an explanation when there are none (#1059).
- **A single admin check** from `trellis-auth` that denies access when in doubt (#1050); the Admin link is shown only to administrators (#1052).
- **Feedback** through the shared `trellis-feedback` library (#1069).
- **Confirmation before Egeria Advisor changes anything**: a Dr.Egeria action from chat is validated first and then
  needs your confirmation, and so does running a plan. Commands that aren't safe to repeat ask you to acknowledge a
  re-run (#1091).
- **Dashboard table tiles** are drawn by the Portal's own table renderer, with resizable columns (#1089).
- **Report-spec authoring on pyegeria's authoring API** (#1104, #1105, #1106), on by default (`EA_SPEC_AUTHORING_V2`,
  `EA_SPEC_ELICITOR_FIXES`). Chat and Execute Spec give the same answer, read with the signed-in user's own client:
  - **Ordinal ranking** (TR-6): a property with an ordered scale, such as importance, is ranked by its scale. A capped
    preview is re-read (up to `RANK_READ_ROWS`, 500) before ranking, and "ranked all N" appears only on a confirmed end.
  - **A sort from the question's words** (C03, #1105): "highest / lowest <attribute>" or "most / least <adjective>"
    sorts on a real attribute of the type; a "narrower than your question" banner is shown only for real gaps.
  - **Content-status questions in one search** (TR-7 phase 2): every status asked for, in one type-scoped search;
    one-value properties name the values not searched; an invalid status is refused first.
  - Counts say "at least N", and no list is called ranked or complete, unless Egeria confirmed the end.
- **Drafts have owners** (#1106): another user's draft can't be edited, generated or executed (`NOT_YOUR_DRAFT`), and
  chat drafts land in the user's own folder.
- **`/health` shows the report-spec authoring switches** (`spec_authoring`), as booleans (#1106).

#### Studio
- **Studio**, a new application for authoring report specs, reports and dashboard sheets, with forms, preview and
  lifecycle, built on pyegeria's authoring API. It has no language model of its own; AI-assisted authoring comes from
  Egeria Advisor, shown as a panel. Started with `packages/studio/scripts/start-studio.sh`, with its own CI workflow
  (#1095).

- **Short-lived sessions with a rotating refresh** (TR-10, #1113): `STUDIO_JWT_TTL_MINUTES` (default 15) and
  `STUDIO_SESSION_MAX_HOURS` (default 8); each refresh re-checks the Egeria sign-in, a replayed refresh token revokes
  the session, and sign-out ends refresh.
- **`make up` (`trellis-up`)** starts Resource Explorer, Egeria Advisor and Studio, checks ports first, names an app
  that fails with its first error line and log, and writes the shared JWT secret to all three `.env` files (TR-47/48,
  #1111).

#### Shared packages
- **`trellis-feedback`** 0.1 to 0.3: a shared feedback store with append-only triage (#1012 onwards).
- **`trellis-ui`**, a shared UI package; Egeria Advisor gets the three looks (#1062).
- **`trellis-accel`**, choosing a compute device at run time; work in progress (#1073; design note #1072).
- **The Analytic Library moved out** as `egeria-trellis-analytics` 0.2.0 on PyPI (#996).

### Changed
- **Report-spec authoring is on by default**: `EA_SPEC_AUTHORING_V2=0` turns it all off, `EA_SPEC_ELICITOR_FIXES=0`
  only the elicitor fixes; `EA_SPEC_STATUS_MERGE` stays off unless set (#1106).
- **Service-account settings renamed**: `RE_DAEMON_USER_ID` / `RE_DAEMON_USER_PASSWORD` (TR-37, #1108) and
  `EA_DAEMON_USER_ID` / `EA_DAEMON_USER_PASSWORD` (TR-34, #1109); the old names are deprecated aliases that warn.
- **A whole-definition Prefect run is one run** with one `surveyed_at`, and fetches a repository once (F-AB-1/2,
  #1110). A step declared `executes_at: prefect` now goes to a worker in a whole-definition run (#1110).
- **RE's default Prefect work pool is `resource-explorer-pool`** (TR-45, #1111).
- **Install docs** match the code: the public `odpi/egeria-trellis` clone, the real Egeria variable names, each app's
  own service account, building EA's knowledge base first, and Studio as the third app (#1111).
- The plaintext database-credential fallback is retired; credentials come only from the secrets store (#823).
- Reports say "published" rather than the old not-yet-published wording (#618, #619).
- A held rebuild is "held", not done, and admins can cancel it (#1053, #1054).
- A private context keeps its question in Admin until it is explicitly re-routed (#1031).
- Private resources never offer an Egeria-dispatched survey (#991).
- The Paper look is kept for one release alongside the Portal's Dark and Light (#763).
- A zone with no access control is private, matching Egeria (#1043).
- Egeria Advisor feedback: a changed vote supersedes the earlier one for that answer, and the recorded intent is the
  one the question was routed to rather than the selector (#1078).
- Egeria Advisor drops its unread `EMBEDDING_DEVICE` setting (#1080).
- Clearing stale contexts and investigations keeps the stored qualified names and clears only the Egeria GUID, so they
  can be re-attached (#1087). The cleanup after an Egeria reset keeps the pointers that still resolve (#1097).

### Fixed
- **File-system surveys** (#1114): a NUL byte no longer aborts the narrative read; binary files are skipped and named,
  and a failed store keeps the previous index (TR-69). The `e164_phone` pattern no longer matches dates, IPs, decimals,
  IDs or epochs (TR-75).
- **A dispatched step can't hang** (TR-62a, #1115): per-request GitHub timeouts, a bound on the repository download, a
  rate-limit wait past 60 s fails the step and names the reset time, and a 30 s heartbeat shows in the timeout line.
- **A cancelled step writes nothing** (TR-62b, #1115): Resource Explorer asks Prefect to cancel, the flow checks its
  state before every write, and readers refuse a result from a cancelled run.
- **A cancel Prefect accepts is never reported as failed** (TR-62c, #1116), including a run cancelled before it started.
- **The Prefect worker script advertises its commit** again; a `pipefail` check always failed before (#1108).
- **`.env.example` comments moved off the value lines**, so an empty setting isn't read as its comment (TR-46, #1111).
- **`resource-explorer add`** exits 1 with "Nothing was added." when it adds nothing (TR-46, #1111).
- **Egeria Advisor** says plainly when its knowledge base isn't built (TR-49); the indexer refuses a disabled or
  unknown collection and fails when every file fails (TR-50/51); DocAgent and ExamplesAgent report the sources they
  retrieved (TR-51c) (#1111).
- **The status banner has a close box**; a changed condition shows it again (#1108).
- **`trellis-context`**: `pack()` admits required sections before optional ones (odpi/egeria-trellis#121, thanks to @NopeCharon).
- **`trellis-querycache`**: an unavailable Redis is read as a cache miss (odpi/egeria-trellis#122, thanks to @NopeCharon).
- **Survey runs** (#1100):
  - only one run of a survey definition can be queued or running on a resource at a time. Another request is refused
    with 409, naming the run already in progress and how to cancel it, and nothing is queued (TR-1);
  - cancelling a queued run also closes its activity entry, which used to stay "running" (TR-2);
  - if a run can't be queued, its activity entry is closed as an error (TR-1).
- **Survey steps on Prefect** (#1093, #1094): once a Prefect flow run may exist for a step, that step is not also run
  locally, and its result is read from Resource Explorer's registry. A step output that can't be stored exactly fails
  the run, so nothing is published from a damaged result.
- **Egeria Advisor's Dashboards tab keeps you signed in**, and report parameter names are converted to snake case before a report runs (#1091).
- **Egeria sign-in errors are recognised by error class and HTTP code**, not by message text, so a refused sign-in
  (sign in again) is no longer confused with Egeria refusing a signed-in user (TR-22, #1100).
- **Identity and access**: ambient identity, caller-identity rounds, private investigation attach, no forged person
  actions from the CLI, constant-time token compare, non-JWT tokens refused at login, a dead token answered the same
  way everywhere (#579, #587, #640, #665, #685, #827, #997).
- A short password never corrupts the publish word (#621).
- **Clearer results**: a database that hasn't been surveyed shows as "not established"; a value that couldn't be
  determined is never shown as 0; a partial failure on a reused report counts as a failure; a shortened list says so
  (#616, #625, #783, #769).
- **Publish**: ExternalReference and LineageMapping links are made once and not repeated when you publish again;
  checking collection membership reads every page (#678, #682, #631).
- **Work lists**: a refused add is not "maybe attached"; Publish never hands the drain a blank lookup (#632, #633).
- Heal verifies Egeria, not its own ledger (#1003); foundations no longer creates the content pack's Perspectives (#1007).
- **Chat**: engine-less sessions, views excluded from table counts, a scoped question that names another resource
  asks which one (#744, #746, #643).
- PostgreSQL temporary schemas are excluded from the schema count (#973).
- Static files are served no-cache (#998).
- Open-data connection errors say what to do (#1009).
- Rebuild refuses only a PDF regression (#1058); Docling runs on CPU instead of Apple MPS (#1055).
- **Egeria Advisor**: feedback no longer blocks the event loop (#1044); Dr.Egeria runs as the signed-in user (#1045);
  calls with neither token nor password are refused (#1046); strict mermaid rendering (#1047); throwaway test schema
  (#1051); the runtime image includes `trellis-feedback` (#1074); an Egeria 5xx or a dropped connection at Portal
  sign-in is "unreachable", not "rejected" (#1077); a saved-query count tile shows no member table under the
  number (#1081).

### Security
- **Egeria Advisor answers as the signed-in user** (TR-33, #1106, #1107): chat and Execute Spec build a per-request
  client from the user's own token, never the service account; with no token, the reply asks you to sign in.
- **No built-in Egeria Advisor account** (TR-34, #1109): the default account is removed, and the service paths refuse
  in words without one. A user's request never reaches an MCP child, which can only run as the service account.
- **Studio refresh tokens rotate**; a replayed one revokes the session (TR-10, #1113).
- **The `trellis-auth` token check never signs in with a configured password.** It checks the token as the token's
  own user, so a token Egeria has refused can no longer pass by signing in again as the service account (TR-24,
  #1100).
- **The docs probe and CVE scan check TLS certificates with certifi** (through httpx), so they no longer trust the
  emSign ECC Root CA - G3 that some system certificate stores still hold (TR-15, #1100).
- **No built-in database password** (TR-12, TR-25, #1096): Resource Explorer and Egeria Advisor no longer fall back
  to the well-known password. Without `PGVECTOR_PASSWORD` they refuse to start and name the missing setting, never
  its value. The Resource Explorer image has no database URLs or password built in.
- See *Fixed › Identity and access* and *Changed* (credential fallback retired, zone rule).
- The certifi floor excludes the distrusted emSign ECC G3 root (#989).

### Dependencies
- pyegeria floor raised from 6.1.15 to 6.2.1, then step by step to 6.3.0 (#593, #651, #728, #806, #819, #834, #853,
  #951, #959, #974, #987, #1005, #1028, #1036, #1061, #1071, #1076, #1082, #1083, #1090, #1091, #1095, #1099, #1101).
- About 20 Dependabot bumps from the odpi side (notebook, jupyterlab, mlflow, aiohttp, authlib, pypdf and others),
  via the sync (#112), and patch updates from the 2026-11-16 Dependabot review (#1098).

### CI and tests
- The ea_rebase A/B run evidence is out of the repository and git-ignored (TR-56, #1112).
- A new `scripts.yml` workflow runs `scripts/tests` and fails on 0 collected (#1111).
- Resource Explorer tests on main and PRs only; job timeout raised to 60, then 90 minutes; stale image runs cancelled;
  PR images built only when their inputs change; ea-tests cover querycache; builder and runtime image stages copy the same workspace packages
  (#884, #601, #891, #670, #1075, #1063, #1079).
- Tests no longer write tracked files, and fail if a check passes without doing anything; a pyegeria contract test; the spec-sync test double answers an
  empty page the way Egeria does (#595, #1048, #1056, #1085). Database tests fail rather than skip when a database is
  required (#1096); Egeria Advisor tests show its shared report-spec sync can't pick up a user's private draft (#1103).
- Dr.Egeria templates synced (#91, #103, #104, #639, #858, #874, #1004); odpi sync and DCO remediation (#108, #981, #112, odpi/egeria-trellis#125, #126); sync back from odpi (#1117).

### Docs
- **No default database password** (TR-27, #1102): the QUICKSTART, READMEs, Resource Explorer admin
  guide and deployment docs say where `PGVECTOR_PASSWORD` comes from (`compose-configs/shared-infra/.env` with Egeria
  Workspaces, or one you generate for a standalone install), and Egeria Advisor's `.env.example` lists it.
- User and admin guide rounds; design notes reorganised into exchange, implemented and rulings (#683, #688, #695).
- Design notes: files, open data, quality, extensibility, local dashboards, OSI metric exchange, feedback extraction,
  closed loop, re-index, hardware acceleration (#785, #786, #889, #939, #978, #984, #1011, #1016, #1035, #1072).

[Unreleased]: https://github.com/dwolfson/trellis/compare/v6.3-tech-preview...HEAD
[6.3 Tech Preview]: https://github.com/dwolfson/trellis/compare/b74b07f9...v6.3-tech-preview
