# Trellis 6.3 Tech Preview: Resource Explorer and Egeria Advisor

Resource Explorer and Egeria Advisor were introduced as early previews in the October 2026 Egeria newsletter.
Since then, our work on Trellis has focused on:

* Putting what Resource Explorer finds into Egeria: results are published with the right zones and checked once
  they arrive, and surveys run on Egeria's own engine host.
* Being clear about what an answer is based on, including what could not be determined.
* Learning from the people who use the tools, through shared feedback, triage, and a document index that can be
  rebuilt and rolled back.

Egeria Advisor now reads and authors the same dashboards and report specs held in Egeria as the Portal does, and a
new Studio application gives report specs, reports and dashboard sheets a dedicated authoring space.

Both remain a **Tech Preview**: they are changing quickly, and your feedback shapes where they go next. This release
goes with **Egeria 6.3** and needs **pyegeria 6.3.0 or later**, the release that matches Egeria 6.3. For the changes to the Egeria platform itself, see
the [Egeria 6.3 release notes](https://egeria-project.org/release-notes/latest/).

In numbers: 1,343 commits and about 556 merged pull requests across 12 packages between October 9 and November 19.

## Try it

- **Live demo**: you can try both at [egeria.pdr-associates.com](https://egeria.pdr-associates.com) without
  installing anything. Register for free, pick a Coco Pharmaceuticals persona, and open Resource Explorer or Egeria
  Advisor from their tiles on the Portal home page.
- **Run it with Egeria Workspaces**: see [Deploying Trellis with the QuickStart](docs/deploying-trellis-with-quickstart.md)
  and [QUICKSTART.md](QUICKSTART.md). Both applications open from tiles on the Portal home page.
- **Learn Resource Explorer**: work through the six-part [tutorial](packages/resource-explorer/docs/tutorial/).
- **Understand the design**: read about the [Trellis architecture](docs/trellis-architecture.md) and [the journey so far](docs/trellis-journey.md).

---

## Mid October: publishing to Egeria, surveys run by Egeria, and identity

- **Publishing to Egeria, with zones, and checking the result** (#644 and follow-ups). Resource Explorer places what it
  created in zones, but not the elements it only refers to. Private material is published only as its owner, and is
  not published at all if that isn't possible. Links such as ExternalReference and LineageMapping are made once and
  not repeated when you publish again (#678, #682). Checking collection membership now reads every page (#631).
- **Surveys on Egeria's engine host**: survey steps run on Egeria's engine host, with two measures of how stale a
  result is (#658, #663, #699), and each step sends an Open Lineage run event (#646).
- **Database surveys** now report heap and index sizes for each table (#582), as well as flags, materialized views and activity (#627).
- **Identity**: requests run as the person making them (#579, #587); private investigations can be attached (#640);
  the command line can no longer record actions on someone else's behalf (#665); tokens are compared in constant
  time (#685).
- **Clearer results**: a database that hasn't been surveyed shows as "not established", and a value that couldn't be
  determined is never shown as 0 (#616, #625).

## Late October: a new interface, quality checks, files, and chat improvements

- **A new interface** with cards and tiles, an At a glance view, and a Survey & analyses page (#776, #778, #782); a
  choice of Dark, Light or Paper looks (#765); a graph view, starting with deployment topology (#831); and data
  tables you can resize (#942).
- **Value profiles and quality checks**, with sampling settings at every level. Results are kept locally and sent to
  Egeria when you publish, and a quality survey can run across a work list on a schedule (#759, #767, #772, #880,
  #893, #894).
- **One card for each survey definition**, showing its latest run and history, with a Cancel button (#729, #735).
- **Chat**: questions about a repository use that repository's own index first (#777); Ollama's native endpoint is
  supported (#779); Phoenix tracing is available (#745); and markdown displays in the chat panel and journal preview
  (#855, #861).
- **Credentials come only from the secrets store**; the plaintext fallback has been removed (#823). Login accepts
  only JWT bearer tokens (#827).
- **Files**: Egeria's CSV survey, Parquet profiles, a Delta Lake layout check, and searchable text passages for chat
  (#868, #912, #977).
- **Extensibility**: a survey step now declares what it is and which questions it adds (#885, #886, #916, #919).

## Early November: the tutorial, open data, and the Analytic Library on PyPI

- **A six-part tutorial**, from what Resource Explorer is for through to deeper analysis
  (#969, #970, #972, #975, #983, #990).
- **Open data**: a single dataset can be published and checked (#946), and a repository known only by its DOI shows what is known about it (#982).
- **Steps across a set of resources**: `schema_comparison` (#952), and a section for them on work-list pages (#944).
- **The Analytic Library is now on PyPI** as `egeria-trellis-analytics` 0.2.0 (#996).
- **A zone with no access control is private**, as it is in Egeria (#1043); an expired token gets the same response
  everywhere (#997); and the certifi minimum version no longer trusts the emSign ECC G3 root (#989).

## November 8 to 19: feedback, Egeria Advisor on dashboards held in Egeria, and Studio

- **`trellis-feedback`**, a shared feedback library (0.1 to 0.3): when you rate an answer you can say why and attach
  evidence; an administrator reviews the feedback and can trace a problem back to a gap in the documents (#1012,
  #1013, #1017, #1018, #1019, #1031, #1032, #1034).
- **Re-indexing**: the document index can be rebuilt, switched over and rolled back; a rebuild that is on hold says
  so, and administrators can cancel it (#1037 to #1041, #1053, #1054).
- **Every compiled section shows the date of its evidence** (#1021, #1024).
- **Egeria Advisor** reads and authors dashboards held in Egeria, just as the Portal does. Large tables scroll within
  their tile, and a shortened table or a folder that can't be read says why (#1042, #1067, #1068, #1070, #1084,
  #1086). Report specs are synced from, published to and withdrawn from Egeria (#1033, #1060), and perspectives come
  from Egeria (#1059). There is a single admin check that denies access when in doubt (#1050, #1052). Dr.Egeria runs
  as the signed-in user, and calls with no credentials are refused (#1045, #1046). Feedback uses the shared library
  (#1069, #1078). If Egeria is down when you sign in, you're told it is unreachable rather than that you were
  rejected (#1077).
- **Studio** is a new application for authoring report specs, reports and dashboard sheets, with forms, preview and
  lifecycle, built on pyegeria's authoring API. It has no language model of its own: AI-assisted authoring comes
  from Egeria Advisor, shown as a panel (#1095).
- **Resource Explorer fixes** (#1100): a survey definition can have only one run queued or running on a resource at a
  time, and a second request is told which run is in progress and how to cancel it; cancelling a queued run closes its
  activity entry; sign-in errors from Egeria are recognised by their error class and code rather than their wording;
  the token check never signs in with a configured password; and the docs probe and CVE scan check certificates with
  certifi.
- **Survey steps can run on Prefect workers.** The Resource Explorer image can run as a Prefect worker; the configured
  pool decides where each step goes, and the status banner says when no worker can take it (#1091, #1092). A step
  sent to a worker is not also run locally, and a result that can't be stored exactly fails the run rather than
  publishing (#1093, #1094).
- **Recovering after an Egeria reset**: Resource Explorer can reconnect its records to Egeria by the names it stored,
  and keeps the links that still work. The operations guide has the steps in order (#1087, #1088, #1097).
- **Egeria Advisor asks before it changes anything**: actions from chat and plans need your confirmation, and commands
  that aren't safe to repeat ask before a re-run (#1091). Its dashboard tables look and behave like the Portal's (#1089).
- **Report-spec authoring in Egeria Advisor is built on pyegeria's authoring API** (#1104, #1105, #1106). In chat, a question such as "which risks have the highest importance?" is sorted on that property, and a property with an ordered scale is ranked by that
  scale rather than alphabetically. A question about content status searches every status you asked for in one
  search; for properties that take one value it says which values it didn't search, and it refuses an invalid status.
  It doesn't claim a ranked or complete list, and counts say "at least N", unless Egeria confirmed the end of the
  results. Execute Spec gives the same answer as chat. It is on by default: to turn it off, set
  `EA_SPEC_AUTHORING_V2=0`; `EA_SPEC_ELICITOR_FIXES=0` turns off only the question handling (sort and status from
  your words).
- **Egeria Advisor answers as you.** Chat and Execute Spec read Egeria with your own sign-in, never EA's service
  account (#1106, #1107). Tools that can only run as the service account aren't used for your requests; a spec search
  then covers EA's local copy only, and says so (#1109).
- **File-system surveys** (#1114): a binary file no longer stops the narrative read, and the files skipped are named
  (TR-69); the phone-number pattern no longer matches dates, IP addresses and IDs (TR-75). Questions about a file
  system are answered from the runs that answer them; the survey card says what it really knows and updates without
  a reload; Publish says what it will do and can run again without duplicates; Curate reviews the proposals.
- **A survey step can't hang, and a cancelled step writes nothing** (#1115). GitHub requests have timeouts, a
  rate-limit wait past 60 s fails the step and names the reset time, and a timeout says when the step was last heard
  from and what it was doing. A cancel lets the step stop and keeps its results out.
- **A cancel that Prefect accepts is reported as cancelled**, including a run cancelled before it started (#1116).
- **Prefect runs**: a whole survey definition is one run, and a repository is fetched once per run;
  `PREFECT_DISPATCH_STEPS` chooses which steps go to a worker, and the run report says where each step ran (#1110).
- **Getting started is up to date** (#1111): the install docs match the code, `make up` starts Resource Explorer,
  Egeria Advisor and Studio and names any that fails, and Egeria Advisor says plainly when its knowledge base hasn't
  been built yet, with the commands to build it.
- **Studio sessions are short-lived and refresh while you work**, re-checking your Egeria sign-in each time (#1113).
- **Service accounts have their own settings**: `RE_DAEMON_USER_ID` / `RE_DAEMON_USER_PASSWORD` for Resource Explorer
  (#1108) and `EA_DAEMON_USER_ID` / `EA_DAEMON_USER_PASSWORD` for Egeria Advisor (#1109). The status banner has a
  close box (#1108).
- **Work in progress**: `trellis-ui` (#1062), and `trellis-accel`, which chooses a compute device at run time
  (#1073; design #1072).

## Dependencies, CI and documentation

- **pyegeria**: the minimum version rose step by step from 6.1.15 to 6.3.0 (#593 to #1101).
- **CI**: Resource Explorer tests run on main and on pull requests; images are rebuilt only when their inputs change
  (#1075); tests no longer write tracked files and fail if a check passes without doing anything (#595, #1048); and a
  contract test checks the pyegeria calls we rely on (#1056).
- **Documentation**: user and administrator guides, and design notes for files, open data, quality, extensibility,
  local dashboards, OSI metric exchange, feedback, re-indexing and hardware acceleration.

Thank you to everyone who contributed: Dan Wolfson (1,325 commits), @NopeCharon (2), whose fixes make
`trellis-context` pack required sections first and let `trellis-querycache` treat an unavailable Redis as a cache miss
(odpi/egeria-trellis#121, #122), and dependabot (16).

---

## Upgrade notes

- **pyegeria 6.3.0 or later is required** by Resource Explorer, Egeria Advisor, Studio and `trellis-auth` (#1101).
- **Database credentials must be in the secrets store.** The plaintext fallback is gone (#823).
- **A second run of the same survey on the same resource is refused** with 409 while one is queued or running (#1100).
  Cancel the first run, or wait for it to finish.
- **There is no default database password.** Set `PGVECTOR_PASSWORD`: with Egeria Workspaces
  it is in `compose-configs/shared-infra/.env`; for a standalone install, generate your own. An install whose
  `egeria_advisor` role still uses the old well-known password should change it (see the Egeria Workspaces shared-infra
  README, "Setting or changing the egeria_advisor password") (#1102). Without it, Resource Explorer and Egeria Advisor
  refuse to start and say which setting is missing (#1096).
- **Prefect**: steps go to the pool named in `PREFECT_WORK_POOL`, so run your workers on that pool (#1092). Resource
  Explorer's registry gains a `flow_run_results` table, created when Resource Explorer first starts; there is nothing
  to do (#1093).
- **After an Egeria reset**, follow the recovery procedure in the operations guide (§2.0) (#1088).
- **Egeria Advisor's report-spec drafts now belong to the user who started them.** Drafts started before this
  update aren't carried over; start them again (#1106).
- **Service-account settings are renamed.** Resource Explorer reads `RE_DAEMON_USER_ID` / `RE_DAEMON_USER_PASSWORD`
  (#1108) and Egeria Advisor `EA_DAEMON_USER_ID` / `EA_DAEMON_USER_PASSWORD` (#1109). The old names still work for now,
  with a warning. Egeria Advisor no longer has a built-in default account: without one, its service tasks refuse and
  say why. Keep `EGERIA_USER` and `EGERIA_USER_PASSWORD` out of server environments; they are for the command line.
- **Prefect**: the default work pool is now `resource-explorer-pool`, the pool the workers serve (#1111). In a
  whole-definition run, a step declared `executes_at: prefect` now goes to a worker, which must run the same commit;
  dispatch adds 10–15 s a step, so it isn't a speed-up (#1110, #1111). With no worker online the step runs in-process,
  recorded as a fallback.
- **Studio**: `STUDIO_JWT_TTL_HOURS` is replaced by `STUDIO_JWT_TTL_MINUTES` (default 15) and
  `STUDIO_SESSION_MAX_HOURS` (default 8), after which you sign in again (#1113).
- **`.env.example` comments are on their own lines** now. If you copied an older example, check for empty settings
  followed by a comment (`KEY=  # comment`): the comment was read as the value (#1111).
- **Stored database passwords survive a container recreate.** Set `RE_DB_CREDENTIAL_KEY` or
  `TRELLIS_DB_CREDENTIAL_KEY` to a stable value; without either, Resource Explorer derives the key from the host name
  and warns, and passwords stored before a recreate can't be read. Egeria Workspaces now requires
  `TRELLIS_DB_CREDENTIAL_KEY` in its Trellis `.env` and the Prefect worker's `.env.re-worker`; re-enter each stored
  database password once (Egeria Workspaces #894).
- **Only JWT bearer tokens are accepted at login** (#827).
- **Zones**: a zone with no access control is private, as it is in Egeria (#1043). Content in such a zone is visible
  only to its owner.
- **Private resources** no longer offer a survey run by Egeria (#991).
- **The Analytic Library comes from PyPI** (`egeria-trellis-analytics` 0.2.0) instead of the in-repo package (#996).
- **`EMBEDDING_DEVICE` has been removed** from Egeria Advisor's settings; it was never used (#1080).
- **Egeria Advisor runs as its own service account**, `egeriaadvisornpa`, in the Egeria Workspaces quickstart
  (odpi/egeria-workspaces#828).

## Where the detail lives

The full list of changes, by section, is in [CHANGELOG.md](CHANGELOG.md). Design notes are under `docs/`.
