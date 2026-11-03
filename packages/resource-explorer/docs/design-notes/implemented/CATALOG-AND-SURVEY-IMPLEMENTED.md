# Register the server with Egeria ("Catalog and Survey") — implemented (2026-10-08)

Source of the work: the project owner's gate on `laz_local_adventureworks` (2026-10-08). Its
Egeria-native survey had failed with "Egeria has no asset with the GUID RE has stored… Publish the
resource to Egeria again" and, before that, `role "surveyor" does not exist`. The owner's rulings,
which shape every part of this:

* "this is not about publishing the details of surveys to Egeria — this is about registering the
  resource with Egeria so that we can run an Egeria based survey on it."
* "It's actually a registration of the database **server** not a database." Surveying a server gives
  its databases; those are then surveyed and cataloged.
* "There may be situations where RE can reach the database but Egeria can't. So registering with
  Egeria should remain optional."
* "Some databases may not distinguish between a database server and a database — but that is a
  subtlety."

The older sentence on the "Catalog and Survey" row, "RE does not collect those template placeholder
properties", was wrong (the Backlog entry "Wire 'Catalog and Survey' for one-click execution" has the
truth): RE has the data; Egeria's processes take it as template **placeholder parameters**, not as the
connection info RE stores. This slice maps one onto the other.

## What the press does (and in what order)

"Register the server with Egeria →" (`POST /api/native-surveys/{type}/{slug}/register`,
`catalog_and_survey.register_with_egeria`). Every write is followed by a read of what it wrote, and a
pointer or proof row is stored only after that read.

| # | Egeria call | Read-back before anything is stored |
|---|---|---|
| 0 | none — checks the `.omsecrets` collection `<slug>::PostgreSQL Secret` exists (existing projection fills it if absent and RE can write the file) | — |
| 1a | `get_metadata_element_by_unique_name(qualifiedName = "PostgreSQL Server::<host:port>")` | — |
| 1b | only if 1a found nothing: `create_elem_from_template` with Egeria's **server template** `542134e6-b9ce-4dce-8aef-22e8daf34fdb` (`isOwnAnchor`, `deepCopy`, the 8 placeholders below) | `get_metadata_element_by_guid(<guid>)`, qualifiedName compared; **then** the server pointer is stored (`app_settings` key `egeria_server_guid::PostgreSQL Server::<host:port>`) |
| 2 | `initiate_gov_action_type("PostgreSQLSurvey::survey-postgres-server", serverToSurvey = <server guid>)` — Egeria's own server survey, through the existing native-survey slice (poll, engine-action read, report read) | engine action read back (`activityStatus`); proof row = the engine-action GUID |
| 3a | `get_metadata_element_by_unique_name(qualifiedName = "PostgreSQL Relational Database::<host:port>::<db>")` | if found: read by GUID, qualifiedName compared, then `databases.egeria_asset_guid` stored (adopted; no process submitted) |
| 3b | only if 3a found nothing: `initiate_gov_action_process("PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess", request_parameters = the 8 database placeholders)` | proof row = the process-instance GUID. The database is read **by qualifiedName and then by GUID** on every refresh; only then is `egeria_asset_guid` stored. The survey engine action the process started is found from the database's own `ActionTarget` relationship (`requestType survey-postgres-database`) and recorded under the ordinary "Survey PostgreSQL Database" row **only when there is exactly one** |

No zone is written (the 2026-10-05 lockout), no `ZoneMembership`, no archive or delete (the ISSUE-117 block
is untouched and ON), nothing in Curate's catalogue commit, and no DDL.

## Phase 1 findings

**How "Survey PostgreSQL Database" is submitted** (the pattern): route `web/routes/native_surveys.py`
(`POST …/run`) → `native_survey_run.submit_native_survey` → `PyegeriaSurveyPort.initiate`
(`AutomatedCuration.initiate_gov_action_type`, action target `serverToSurvey` = the asset GUID) → a
`step_runs` proof row keyed on the engine-action GUID → `refresh_run` reads the action
(`get_metadata_element_by_guid`, `activityStatus`) and, once COMPLETED, the report found by the
`ReportOriginator` relationship. `derive_native_state` is the one place proof becomes words.

**Egeria's source** (read-only, `egeria/open-metadata-resources/.../PostgresPackArchiveWriter.java`,
`ContentPackBaseArchiveWriter.createAndSurveyServerGovernanceActionProcess`):

* Egeria has **two** CreateAndSurvey processes. `PostgreSQLServer:CreateAndSurveyGovernanceActionProcess`
  (step 1 create the server asset from `POSTGRES_SERVER_TEMPLATE`, step 2 `survey-postgres-server`, step 3
  print report) and `PostgreSQLDatabase:CreateAndSurveyGovernanceActionProcess` (step 1 create the database
  from `POSTGRES_DATABASE_TEMPLATE`, step 2 `survey-postgres-database`, step 3 print). Step 1's new asset
  reaches step 2 as the `newAsset` action target. **Neither registers a server alone**: both always survey
  and print afterwards. So the server is registered by Egeria's own server **template** (the same one step 1
  uses) with a read-back, and the server survey is submitted separately; the database is created by the
  database process.
* **The server survey does not create database elements.** `PostgresServerSurveyActionService` lists
  `pg_database`, surveys each (excluding `postgres`) and writes **annotations only**
  (`Capture Database Measurements`, plus schema/table/column ones); its own comment: "the databases are not
  catalogued at this time". So the databases RE shows as *found* come from the report's annotations, as
  Egeria reported them; a found database with no asset is "found, not yet cataloged".
* Server template placeholders (`getPostgresServerPlaceholderPropertyTypes`) and the database template's
  (`getPostgresDatabasePlaceholderPropertyTypes`):

| placeholder | server | database | where RE gets it |
|---|---|---|---|
| `hostIdentifier` | yes | yes | `databases.egeria_host`, else `host` (the host Egeria reaches) |
| `portNumber` | yes | yes | `databases.port` |
| `serverName` | yes | yes | `<egeria host>:<port>` (`catalogue_gateway.server_name_for`) |
| `versionIdentifier` | yes | yes | "not recorded" (RE keeps no version) |
| `description` | yes | — | "PostgreSQL server at <server name>" |
| `resourceName` | yes | — | the server name |
| `databaseName` | — | yes | `databases.database_name` |
| `databaseDescription` | — | yes | `databases.description`, else a sentence naming the database |
| `secretsStorePathName` | yes | yes | `config.egeria.secrets_store_path_name` (the path **inside the engine host**) |
| `secretsCollectionName` | yes | yes | `<slug>::PostgreSQL Secret` |

  There is no user or password placeholder: both templates' connection names the secrets collection and the
  store path, and Egeria's engine host reads `userId` / `clearPassword` from the `.omsecrets` file at survey
  time. A test pins the key sets to Egeria's lists.
* **Where credentials come from and who writes them.** The file is `resource-explorer.omsecrets`
  (`EGERIA_SECRETS_STORE_LOCAL_PATH` host-side, `…_PATH_NAME` container-side), key
  `secretsCollections.<slug>::PostgreSQL Secret.secrets.{userId,clearPassword}`. RE's registry-to-file
  projection writes it (`omsecrets_store.write_credential` at registration / credential change;
  `omsecrets_reproject` at startup, on each resync pass and by CLI). This slice only checks the collection is
  there and, if it is absent and RE can write the file, asks the **existing** projection for that one slug
  (`only_missing`). It never reads a password into a message. The server element's connection uses the same
  collection as the database it was registered from.
* **Where "surveyor" came from.** The user in the collection is `databases.db_user` as stored in RE's registry
  at the time of projection (`omsecrets_reproject._plan`: `user, password = db.db_user, db.db_password`). The
  earlier `FATAL: role "surveyor" does not exist` on the 5432 server therefore means the stored record for
  `laz_local_adventureworks` carried the user `surveyor` while 5432 knows `dwolfson`
  (see the credentials-by-port reference: 5442 accepts `surveyor`). A new registration reads the same record,
  so **it would recreate the same connection** unless the record's user is changed first. This slice changes no
  credential and could not read the shared registry to confirm the stored value; check
  `databases.db_user` for that slug, and Update credentials on it, before pressing.
* **What the stale GUID is.** `fc4e0478-4efe-4a29-bd1a-01b5bcb4a61b` is `databases.egeria_asset_guid` — the
  **database** element's GUID, set by the older publish path (`set_database_egeria_guid`). RE has two kinds
  of pointer: that column (database) and, new here, the server pointer in `app_settings`. The old publish
  never stored a server pointer (its `server_guid` was a local variable). The database element it pointed at
  no longer exists in Egeria (the repository was reset); the sentence "Publish the resource to Egeria again"
  was generated at `native_survey_run.submit_native_survey` (the `asset_exists` check).

## What changed

* `catalog_and_survey.py` (new): the press, the placeholders, the real port (`PyegeriaRegistrationPort`), pointer
  checks, the "Catalog and Survey" row's derivation, the discovered-databases listing, the reach note.
* `native_survey_run.py`: rows grow `neutral`, `stale`, `target`, `register`, `reach_note`, `discovered`; the catalog
  row and server-survey row; stale wording (`NativeSurveyStale`); registration runs refresh and sweep by their own
  derivation (a COMPLETED process has no report to wait for); Egeria's text is scrubbed of the resource's password.
* `technology_type_processes.py` + `configdata/technology_type_processes.yaml`: `separate_server_element`,
  `server_technology_type`, per-process `target`; **`server_has_separate_element(entity_type, technology_type)`**
  (True PostgreSQL; False coincident kinds; None = "this kind is not wired yet").
* Routes `…/register` and `…/check` (read-only pointer check; called by the pane once, only when a pointer is stored).
* `scheduler._sweep_native_surveys` uses the registration port (same port plus the reads a registration run needs).
* Front end: `native-surveys.js`, `re-api.js`, two neutral cues in `glyphs.js` (`optional`, `gone`).
* No registry DDL; `registry.py` untouched. The server pointer uses the existing `app_settings` key/value table.

## Review round (2026-10-08): no duplicate elements

* **Absence is typed or exact, never inferred.** `find_element` / `read_element` / `asset_exists` treat only a
  `PyegeriaNotFoundException` or the exact answer "No element found" as absent. Any other error, or a string that merely
  contains "not found" (a user, a type, an index), refuses with Egeria's sentence and creates nothing.
  The rule matches the shared one being written on re/blueprint-container-shape (`egeria_absence.is_absent`): gone is the
  exact "No element found" answer, a `PyegeriaNotFoundException`, or a `PyegeriaAPIException` whose `related_http_code` is
  404 (the code, never message text, so a GUID containing "404" in a timeout sentence is not gone); unauthorized, transport
  and everything else is unreadable. **Follow-up:** once both branches are merged, the shared helper should replace the
  local check (`PyegeriaSurveyPort._is_not_found`, `is_exact_absent`).
* **Created but not confirmed.** The server GUID the template create returned is recorded (`egeria_server_unconfirmed::…`)
  BEFORE the read-back. If the read-back fails or mismatches, no pointer is stored, the row says "created, not yet
  confirmed: press to confirm", and the next press reads that GUID first and adopts it; a GUID under a different name is
  refused (never re-created); only Egeria's typed not-found on that GUID lets a new create happen.
* **No second database process.** An atomic insert-if-absent claim (`egeria_register_claim::<slug>`, existing `app_settings`
  table, no DDL) is taken before the process is submitted. Any press while a claim is held and unresolved is refused (409),
  saying what the earlier run is (process GUID, Egeria's status, time); the row offers "Start again →", which sends an explicit
  `start_again`. The claim is released when the database is read back and pointed at, when Egeria's own terminal failure word is
  read, when Egeria refuses the submit, or when the database is adopted by name.
* **The shared server's credentials.** The slug whose collection the server element was created with is recorded; every other
  database on that host:port shows "the server's connection uses <first>'s credentials", and, comparing registry `db_user` names
  only (no secret read), says when the user names differ. A server RE did not create says its credentials were not recorded.
* The register control also shows after a failed or refused run and after a stalled awaiting state. The response and the UI say
  when the press re-projected the secrets file (a local write) and show the projection's own reason when it fails; with no
  secrets path configured the row says "secrets path not configured: RE cannot check Egeria's credentials".
* The "connects from its own platform" line is connection-class wording only (refused, timed out, unreachable, no route,
  connection reset, unknown host); a role or password failure gets none.
* `omsecrets_store._load` logs a YAML error's class and line number only, never its text (it can echo the offending line).

## Optional by design

Registering is a press; nothing requires it and nothing nags. A never-registered database shows a **neutral** state
(muted ○ "not registered", "Egeria has not been given this database · register it to run Egeria's own survey ·
optional", no ✕, no warn colour) and RE's own surveys, curation and everything else are unchanged. Only a stored
pointer that **Egeria answers no longer exists** uses the stale wording ("the Egeria asset RE had stored no longer
exists"), and a failed read is "unreadable", never "gone". When an Egeria survey fails and reads as a connection
problem, Egeria's sentence is shown verbatim plus one line: "Egeria connects from its own platform; RE can still
survey this database itself".

## Named follow-ups

1. **Kinds where the server and the database coincide** (a single-file or embedded database, for example).
   `separate_server_element: false` is the config value for them, and `wiring_reason` already answers "the server and
   the database are one thing for this kind, and registering them in one step is not built yet". The single-step
   variant would register **one** element (the database/asset that is also its own server) from that kind's template,
   read it back by GUID, store the one pointer, and run that kind's survey; no server pointer, no server survey, no
   discovered-databases list. Unknown kinds say "this kind is not wired yet". FileDirectory's "Catalog and Survey"
   is in that second group.
2. **An Egeria-side reachability probe** ("can Egeria's platform reach this host:port?") — deliberately not built; the
   reach line only reports after Egeria itself failed.
3. The server's connection reuses the first-registered database's secrets collection; if that database is later
   removed from RE the collection name still resolves in the file but is no longer maintained. A per-server
   collection would fix it.
4. A server survey is stored under the database slug that triggered it, so a second database on the same server shows
   its own server-survey row (it can be run again, a second report).
5. **Unverified live** (no live Egeria was used): the process instance's own `activityStatus` (the code falls back to
   its first engine action found by `ActionRequester`, as Egeria's FVT finds it; no status at all is shown as "last read
   failed", never guessed); and whether `create_elem_from_template` with the server template derives the qualifiedName
   `PostgreSQL Server::<host:port>` on this build (rehearsal 1 evidence says yes; a mismatch makes the read-back refuse and
   store nothing).

6. **Unverified live (added in review):** the zones a template-created server gets. Egeria's template definition passes no
   zone, so RE expects none, but whether the server-side template instantiation adds a ZoneMembership classification is not
   established; RE sends none and writes none, and this must be checked against the configured-only zone rule on the first
   gated press (read the created element's classifications).
7. **Unverified live:** the template-derived qualifiedNames (server `PostgreSQL Server::<host:port>`, database
   `PostgreSQL Relational Database::<host:port>::<db>`); a mismatch now fails safe (recorded GUID, refusal, never a second create).
8. **Unverified live:** whether the process instance carries an `activityStatus` (fallback: its first engine action).

## Verified

`tests/test_catalog_and_survey.py`, `tests/test_catalog_and_survey_routes.py` (recording fakes only), and the harness file
`catalog-and-survey-register.test.mjs`. The owner gates by use on adventureworks.

## Second review round (2026-10-08)

* **HIGH, the absent string.** pyegeria answers a by-name or by-GUID miss with `NO_ELEMENTS_FOUND` ("No elements found",
  plural); the singular `NO_ELEMENT_FOUND` is not returned on those paths. The first round matched only the singular, so a
  genuinely absent server raised instead of being created. `native_survey_run.absent_answers()` (lazy) is now built from pyegeria's
  own constants (both, exact, case-insensitive, trailing period stripped), and every test imports the constants rather than
  typing a literal; one test fails if the accepted set stops containing either constant. The local rule mirrors
  `egeria_absence.is_absent` (#556) for string answers and should switch to that shared helper after #556 merges.
* The released-too-early claim: `start_again` now releases the claims only after the in-flight guard passed AND Egeria was
  asked (`read_process`) whether the earlier process is still ACTIVE (still running, or unreadable, refuses with Egeria's
  sentence). The UI asks once before sending it ("Confirm: start again").
* The server create is under an atomic claim keyed on the server qualifiedName (same insert-if-absent mechanism), released
  on a confirmed read-back, kept while unconfirmed, released if the create is refused, and cleared by "Start again".
* A later refusal still says the secrets file was re-projected; the credential note uses `secrets_collection_name`; the
  reach note again recognises "could not connect / unable to connect" (phrases, not bare "connect", since a JDBC role
  failure names a "Connection").
* **Follow-up for the shared-helper slice:** `catalogue_gateway.read_element` (~652-664) still matches "404", "No element
  found" and "not found" loosely; it is not used by this slice any more and is out of scope here.

## Third review round (2026-10-08)

* **The server race.** After the server claim is taken, `find_element` and the unconfirmed record are read again and the
  server is adopted if either shows it (a real interleaving test: A completes between B's "absent" and B's claim; exactly one
  create). A create that gets **no answer** (timeout, transport error, cancellation) KEEPS the claim and refuses with
  "the server may or may not exist"; the claim is released only on a typed Egeria answer (`PyegeriaAPIException`,
  `PyegeriaInvalidParameterException`). The claim value records its holder; "Start again" on database X leaves a claim held by
  database Y alone, and clears a holder-less claim only when no other database on that server has a registration in flight.
* "Start again" checks the newest run that HAS a process GUID (not just the newest row). With none, the confirm says "RE has no
  record of the earlier process, so it cannot check whether it is still running." The confirm button disarms after a refused or
  failed press and after six seconds.
* The reach note needs "could not / unable to connect" (or refused, timed out, unreachable, ...) and is withheld whenever the
  sentence names a password, authentication, role or permission problem.
* **Unverified live / known risk:** `read_process` reads the process instance's own `activityStatus` and falls back to the
  FIRST engine action only when the instance has none. If a multi-step process instance carries no status and its first
  action is COMPLETED while later steps still run, "Start again" would read it as not active. No safe fix is obvious (the
  process's later steps are linked by follow-on actions that RE has not read live); the safest mitigation today is the
  database-found-by-name adoption, which turns a created database into an adopted one rather than a second process.

## Fourth review round (2026-10-08)

* The server claim is released only for a genuine pre-write refusal: an API or authorization exception whose
  `related_http_code` / `response_code` is 4xx, or a `PyegeriaInvalidParameterException` with no response and no wrapped
  `JSONDecodeError` (pyegeria also raises it after a 200 whose body would not parse). A 5xx, a missing code, a timeout, a
  transport error and a cancellation keep it: a template create is not transactional.
* The "no answer" note is withheld when a GUID was returned but not confirmed ("created, not yet confirmed" says it instead).
  A claim held by another database is named and "Start again" is directed to that database; the stale-pending branch releases
  only the claim this database holds. The "press again to confirm" line clears with the disarm. The reach note's "refused"
  now needs a connection ("Egeria refused the request" gets none).
* **Environment note:** during this round the system `python3` on this machine silently ran nothing; edits were made with the
  repo's venv interpreter. Nothing in the product depends on it.
* Still unverified live: which `related_http_code` Egeria returns for a template create that half-succeeds; whether a
  create the server completed after a client timeout shows up by qualifiedName promptly enough for the re-check.

## Fifth review round (2026-10-08): the database process submit

* The database `CreateAndSurvey` process submit used to release the claim on ANY exception. Egeria may accept the process and
  the client still see a timeout, a 5xx, no code, or a garbled body, so a re-press could start a second process and a second
  database. Now only a genuine pre-write refusal (4xx code, or a pre-request invalid-parameter error with no response) releases
  the claim; anything else keeps it, records the run as "created or not yet known: <Egeria's sentence>", and raises
  `RegistrationUnresolved` with the Start again path. A re-press inside the window makes no second `initiate_gov_action_process` call.
* **Other initiate paths (reported, not changed):** the server survey (`initiate_gov_action_type` via `submit_native_survey`, also
  what the manual `POST /run` on a server row calls) has NO claim: only the read-then-act in-flight guard on the proof rows. Two
  near-simultaneous presses can both pass it and start two surveys, and a survey whose submit timed out after Egeria accepted it is
  recorded as `submit_error` with no GUID, so a re-press starts another. The consequence is a second engine action and a second
  survey report, not a second catalogue element; the launch is documented as not idempotent at Egeria. A claim around the survey
  submit (same insert-if-absent mechanism, same answered/ambiguous rule) is the follow-up if a duplicate report matters.
