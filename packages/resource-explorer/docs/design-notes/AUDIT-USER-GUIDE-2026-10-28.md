# AUDIT — docs/user-guide.md against the running Explorer (2026-10-28)

Owner's words: "go ahead with the audit, I'm signed in" and "continue with the
work based on the audit". Read against 8813 on main f6e63f8f (#899), signed
in as erinoverview in the app's browser pane, surfaces read as text with a
wait after each navigation, plus main's code where a claim is about code.
Verdict words: **true** (the screen says it), **stale** (the screen says
something else), **Classic** (describes the retired Classic UI, #825),
**unbuilt** (describes a design, not a screen), **not verified** (the screen
did not render in the time given, or needs a press).

## 1. Section by section

| Guide section | Verdict | What the screen or the code says |
|---|---|---|
| Status / Last revised 2026-10-14 | stale | Revised today without the date moving; the questions, Curate, Files and Library sections changed since. |
| Starting the web UI (`/next`) | stale | The app serves at `/`; `/next/` answers a JSON "login required" even when signed in. The title still reads "Resource Explorer · /next". |
| Signing in; sessions; ⏻ sign out | true, one defect | Sign-in panel and Sign out present. Defect: with no investigation selected the header's connection-details control reads "not signed in" while `/api/auth/me` says authenticated; with an investigation it reads the user id. |
| From the terminal (login/logout, session words) | not verified | CLI; no screen. Keep, mark as CLI. |
| Who can see your work; zones; draft is a status | true | Curate band reads "zones: none · everyone visible". |
| Investigations: kinds, purposes, 🔒 private, Scope · N, Work lists for X | true | Investigation stage lists purposes, status, scope count and "☁ Egeria / 🏠 local / unbound by reset"; sidebar reads "SCOPE · 13", "WORK LISTS FOR DOCLING FAMILY · 0", "OTHER WORK LISTS · 6". "unbound by reset" is a word the guide does not explain (the 2026-10-23 reset); add it. |
| Work lists versus investigations; Select; promote; suggested-to- | true | "▦ Work lists 6" in the strip; the bench control reads "Compare a set of resources as rows x questions"; "Select several…" in the sidebar. The winnowing columns (#843) are a design, not a screen: do not describe. |
| Saving a chat answer to the journal; Publish my entries | true, changed | "add to investigation journal" exists, but since #841 the entry is derived from the chat turn, not "an editable entry seeded with a citation": rewrite that sentence. |
| Questions: Q-0001 to Q-0095; never reworded | stale | 105 ids, 94 active, Q-0105 the newest; 7 questions WERE reworded on 2026-10-22 (the id is stable, the text changed); 2 retired, 9 merged. The rule to state: the id never changes, the text may; retired and merged rows keep their ids. The screen groups questions by purpose ("ordered by purpose · Explore, Learn · 4 of 6 lead") and marks "NOT AMONG THIS INVESTIGATION'S PURPOSES": neither is in the guide. |
| What the marks mean (table) | true, incomplete | ✓ ∅ ◐ ○ ◌ ? ⚠ ◔ seen; "answered automatically" appears as a distinct key beside "answered"; the Curate chips (✓ / ◌ with "not checked since") and the lanes of cues are missing. |
| Layout: stage strip order; Investigation before; Understanding and Automate cross-cutting | true | Strip reads Investigation · 1 Scouting … 6 Curate · Understanding · Automate · ▦ Work lists; a stage opens sub-tabs "Questions · Survey & analyses · By analysis · Disposition". The sub-tabs are not in the guide. |
| The eight intents (intro) | true | Keep. |
| Scouting: "Select Scouting in the Analyses panel, Run all scouting analyses" | Classic | /next has no Analyses panel and no "Run all" button; Scouting is the Questions tab with per-question "run" and the Survey & analyses tab with the definition's Survey press. The repo header tiles (WEBSITE, LANGUAGE "python measured", STARS, SECURITY "? not read", DEPLOYMENTS, SCOUTING SIGNAL) are what a reader sees and are undocumented. |
| Importing many repositories (Find repos, search / From a list, Import Selected) | true | "＋ Find repos" in the sidebar ("Find and import candidate repos"). |
| Where the docs are (docs ✓ / unreachable / no docs found / not checked) | true | docling reads "no docs found" beside the Project site. |
| Assessment: "open Analyses, choose Assessment intent", ⏱ ⏳ badges, ☁ 🏠 source badges | Classic | Assessment is a Questions tab like the others. |
| Analysis: Architecture Recovery, shapes, proposed_by, confidence | true in substance, Classic in navigation | The content about shapes and confidence stands; the way in is the Analysis stage's questions and Curate › Structure, not an Analyses panel. The Analysis tab on docling showed "0 of 20 answered · 20 still loading" after 4 s: slow, see §2. |
| Discovery: "Use the Chat panel to ask…" | stale | Discovery is a stage with 19 database questions (db_cataloged, db_existing_use, db_feedback, db_surveyed, db_disposition, reach, db_classification …), answered on the page; chat is a rail on every stage. |
| Enrichment: RFAs panel, "Egeria write-back is coming", Context fields table | stale | RFAs are a drawer in the strip ("RFAs 57"); RFA answers DO sync to Egeria (`rfa_egeria_sync`); the Context tab's fields are Environment, Owner, Steward, Sensitivity, Intended use, Actual use, License, Location, Backup status, Retention, Criticality, Notes, plus documentation-source rows (user manual, api reference, data dictionary, …). The guide's seven-row table is the old form. The tab read "Reading what's been supplied…" after 8 s: slow, see §2. |
| Understanding: "charts for a resource's history" | stale | Three tabs: At a glance (tiles and "Since the last run"), Contents, Over time. No Artifacts tab yet (design). |
| Curate: three tabs, one Publish, FINDABLE, ratings, notes, tags, group | true for repositories, half true for databases | Repository: tabs "Describe and publish · Contents ∅ 0 chosen · ◌ 22 undecided · Structure"; FINDABLE, WHAT PEOPLE SAY (ratings, notes with "suggest to"), PUBLISH TO EGERIA. Database: one page (FINDABLE, What gets published with the three chips, Your scope with Depth, the Include/Leave out table) and no "Describe and publish / Contents / Structure" tab strip visible; the quality-checks section was not reached (see §2). The publish table of declared measures (#886) and the chips (#844) are on the database page and undocumented. |
| Automate: subscribe from a card; Schedules and Subs drawers | true, words changed | Tabs "🔔 Subscriptions · ⏱ Schedules · First indexing"; the subscription is created from "🔔 notify me" on a question row, not "from its card"; rows say "● active — but never fires (no schedule)", which the guide should explain. |
| Publishing to Egeria (table; no zone; draft; rolls forward) | true | Keep; add the three chips' words and "published as <identity>". |
| What a database's survey report carries; verdict vocabulary | true | Keep; note the profile reshape (#847/#859): a re-run re-links unchanged annotations and creates only changed ones, card line "N measures published · M changed since <time> · K re-used". |
| Scores in Egeria (qualityScores) | not verified | Code claim; keep with its date. |
| Steps that run by themselves (prerequisites, Run it) | not verified | Needs a press; keep. |
| Live status (badge, toast, banners) | true | "📋 Activity 200+ 9+ new" seen. |
| The Analytic Library (routines, steps, state words, Curation Coverage tile, Egeria-dispatched steps) | true, incomplete | Four database steps are Egeria-dispatched; four file-system steps (fs_change_rates, fs_duplicates, fs_activity, fs_permissions) run through RE's Files host and appear on the file system's Survey & analyses tab under "Derive"; the start-up line counts only the first four (ruled 2026-10-28). The declaration (manifest fields, result kinds, confidence forms, absence sentences) is what "extending" means now; the guide points at extending-with-analyses.md, which must say so. |
| Data quality checks | true | Keep. |
| Files and File Systems: mount translation, "Walk & Data Profiling", "Compensation Layer" | Classic / stale | The file-system Survey & analyses tab reads: FOLDER SURVEY with the reach line ("✓ Egeria can read /deployments/loading-bay · led by Egeria's Folder Survey"), Survey / Measure beside Egeria's survey / Publish these measures / Profile data files / Derive presses, the Egeria file-name CSV disclosure, counts, FILE TYPES, WALK MEASURES ("walked to the end of every folder", "per-file rows kept · 134 files", world-readable, modes, hashes with the 10 MB limit), HEAVIEST FOLDERS, BY DEPTH, FOLDERS THAT DESCRIBE THEMSELVES (narratives as proposals, "confirm it on Curate"). None of that is in the guide; the "Compensation Layer" paragraph matches nothing in the code. |
| Survey Report (☁ 🏠 ⏳ badges, Survey Analyses section, Changes since last run, Survey History chart) | Classic, one survivor | "Since the last run" lives on Understanding › At a glance. The rest is Classic. |
| Database access and credentials (banner, "measured within what this account can see") | true in words | "measured within" and "not yet surveyed" are in the code; the banner itself was not on the coco_pharma header this session (it read "never surveyed · not published to Egeria", see §2). |
| Analyses Panel (intent and perspective filters, ★ recommended, Schedule section) | Classic | Perspectives are a sidebar filter ("PERSPECTIVE · 0 OF 12"); schedules live under Automate. |
| Activity Log (scout / survey / catalog / publish / rfa / refresh) | true in substance | Opens from the header; entry kinds not re-checked. |
| Chat Panel (agents table, intent routing, slug prefix) | stale | The rail reads "ASK · scoped to <resource> · what would answer this?"; since #899 the answer is compiled from the declared extents by question id; the agents table is internal and the slug prefix is replaced by the scope shown on the rail. |

## 2. Defects found while reading (to the status-words queue, not the guide)

1. **Header contradiction on a database.** coco_pharma's header reads "never surveyed · not published to Egeria" while the Curate band on the same page reads "Database element c990f349 in Egeria · published 10-24 00:45 · 4 schemas attached". One of them is wrong; the header derives from a field the reset cleared or the re-catalog did not set.
2. **Two counts for one fact.** The same band says "4 schemas attached · 4 in Egeria" in its sentence and "cataloged by Egeria 0 schemas ◌ not checked since 00:54" in its chip. The sentence predates the chips (#844) and should go.
3. **"not signed in" while signed in.** The connection-details control reads "not signed in" when no investigation is selected and the user id when one is; `/api/auth/me` says authenticated in both cases.
4. **Slow panes.** Discovery on coco_pharma (266 tables) had not rendered after 9 s ("loading…"); Enrichment on docling read "Reading what's been supplied…" after 8 s; Analysis on docling read "20 still loading" after 4 s. Discovery on adventureworks rendered after ~15 s with "5 still loading". A pane that is loading should say what it is waiting for and how many rows are back.
5. **Unknown stage in the URL crashes the pane.** `?stage=admin`, `?stage=activity`, `?stage=worklists` throw `Cannot read properties of undefined (reading 'label')` in `loadPane` (app.js:8924) and leave the main panel blank. A guard ("no stage named <x>") is one line.
6. **"/next" in the title and the guide** after the route moved to `/`.

## 3. What the improved guide becomes

One guide, by the reader's job, each section opening with the screen's own words and ending with "what is not built yet (dated)":

1. **Start here**: sign in, the strip, the sidebar, the chat rail, the three jobs (find and judge a resource; catalog it into Egeria; extend the Explorer).
2. **The words and marks**: the mark table (completed), the chips and lanes, the absence words ("not measured", "nothing found", "could not read", "not permitted", "not applicable", "planned, not built yet"), "as of", confidence badges, "local · not published", "published as <you>".
3. **Investigations, scope and work lists** (as now, plus "unbound by reset" and the journal-from-chat rule).
4. **A resource, stage by stage**: for each of the six run stages what the Questions tab shows and what the Survey & analyses tab runs, with the header tiles; then Understanding (three tabs), Automate (three tabs), Curate (the three tabs for a repository; the one page with chips, depth and the publish table for a database; the file system's root-folder Publish).
5. **Repositories / Databases / File systems**: the kind-specific pages (Find repos; the credential banner and Contents table; the folder survey with reach, walk measures, narratives, derived steps and the price line).
6. **Publishing to Egeria**: what a press sends, read-back and the chips, reports and annotations, identity, the profile re-link rule, scores.
7. **Extending and customising**: see §4.
8. **Admin**: Configure (Value profile settings, Annotation Types, Question Catalog, API fetch addresses, Egeria mounts, Groups), Observe (Logs, Publish Queue, Feedback), Reconcile (Egeria Alignment, Egeria Links, Repair), Prefect, Repository discovery sources.
9. **The CLI**, kept short, with the session words.
10. **Not built yet**, one list with dates: renderers for seven result kinds, dashboards, open data, remote reach, Artifacts, the winnowing bench, mixed-kind lists.

## 4. The extension chapter (shared by the guide and the tutorial)

What a person can add or change **without touching Resource Explorer's code**, each with where it shows up:

| You add or change | How | Where it appears |
|---|---|---|
| An analysis (library step) | a package with a StepManifest and a declaration (result kind, measures with unit and as-of, absence sentences, chat extents, confidence form, optional Egeria type), installed as a `trellis.analytics` entry point | the resource's Survey & analyses tab; its questions' headlines; Chat answers citing its rows; the Curate publish table row with "writes as" |
| A question | a row in resource_questions.csv (or Admin › Question Catalog's add) naming its stage, kinds, purposes, perspectives and answering analysis; the generator refuses a bare step name or a kind with no adapter | the stage's Questions tab, ordered by purpose |
| A perspective or a purpose | Dr.Egeria valid-value lists (purposes suggestive, free text kept) | the sidebar perspective filter; Investigation purposes; "NOT AMONG THIS INVESTIGATION'S PURPOSES" |
| A survey definition | a Dr.Egeria document naming steps and scoped-by questions | the Survey & analyses tab's definitions |
| Settings | Admin › Configure (value-profile share, annotation types, API fetch addresses, Egeria mounts, groups) | the cards' words and thresholds |
| A file-system reach | registration with host and container path (remote mechanisms: designed, not built) | the folder survey's reach line |
| A dashboard | Dr.Egeria Report and Dashboard Sheet (designed for RE in #903; built in the Portal today) | the Portal's local dashboards; RE's Dashboards tab when built |
| An open-data portal | designed (#904), OD1 in build | not yet |

Each row in the tutorial is one illustrated walk, and the first row's walk is the extensibility gate (fs_permissions on loading_bay) once it has run.

## 5. The tutorial's shape

Six walks, each a page of screenshots from 8813 with the words the screen uses, every claim tied to a screenshot taken that day: (1) sign in, pick an investigation, find a repository; (2) judge it: Scouting to Analysis, the marks, chat; (3) catalog a database: credentials, Contents, Curate, Publish, the chips; (4) a file system: register, reach, survey, walk measures, narratives; (5) add your own analysis: the gate walk; (6) add a question and a purpose, change a setting.

## 6. Division of work

- Architect (this session): this audit; §3's structure as the guide's new outline; §4 written in full; the extension walk (5) once the gate has run.
- A Sonnet docs session: the restructuring of the existing text into §3's outline with every "Classic" and "stale" row corrected from §1's third column; the screenshots and walks (1)–(4) and (6); US spelling; the "not built yet" list.
- Review: the architect reads every screenshot's claim against the screen once, as a gate.
- Prerequisite: the §2 defects 1–3 fixed first, or the screenshots show them.
