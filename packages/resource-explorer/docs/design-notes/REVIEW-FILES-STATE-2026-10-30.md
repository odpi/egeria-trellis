# REVIEW — Files on Resource Explorer as it stands (2026-10-30)

Owner's words (coordinator relay): "Files - we need to review and test - and the document". Read-only review, 8813
signed in, 21:30–22:00Z. Note: the serving worktree is at **b1531ee0**; origin/main is at aacdfaff (#944, work-list set
steps UI). #944 touches the work-list page, not Files, so this review holds for both. Sources: the three registered
file systems through the screens and `/api/filesystems/*` reads; the code in wt-relgraph; Egeria read-only (pyegeria);
the brief BRIEF-FILES-QUESTIONS-AND-ANALYSES-TO-RETIRE-CLASSIC (2026-10-20), build notes for steps 6 and 7–8,
DESIGN-FILES-REMOTE-REACH, the user guide §5/§7/§10 and tutorial walk 4. No press anywhere.

## In one page

Files is mostly built and almost untested as a whole. Every step of the brief's build order has code and a card
section: register on /next, the reach fork, Egeria's Folder Survey read back, RE's walk beside it with per-file rows,
narratives found and read in full, data-file profiles (Egeria's CSV survey plus RE's), the six library steps, the data
class match, the price line, chat citations of narrative passages and the locator route. Classic is gone since 10-21,
so there is nothing left to retire; the question is whether /next covers what Classic did, and for files it now does
except two rows (F-08 report cards, F-09 profile per file, both partly covered by the card). Two things are missing
outright: §3a's single `FileSystemSurvey` definition (not authored, in RE or in Egeria: "No Survey Definitions have been
authored for File System Directory yet") and remote reach (designed, slice 1 built as the local fork, SFTP/S3 not
started). Three things are unproven because nobody has walked them end to end since they landed: the RE-led branch on
the owner's path (egeria_v6 was surveyed once on 10-21 and never profiled or derived), the Egeria-led gate file system
(coco_data_lake: Egeria's own folder survey throws a FileException on its container path since 10-21, so the Egeria-led
fork has only ever worked on loading_bay), and publishing the declared file-system measures (the table lists them, the
Publish is wired through the folder survey's press, no proof row exists yet). Today's read also found that on an
Egeria-led file system the questions that Egeria's survey answers (what files, how much, what shape) still show "not
run", because their reader looks only for RE's own inventory run (F41), and the Curate publish table says "no survey has
walked this file system yet" over a file system with 134 walked files (F39). The test walk below (§3) is nine steps on
loading_bay plus the two fork proofs; six of them are presses and three write to Egeria.

## 1. Built, against the brief

| Brief item | Built as | Where seen (loading_bay unless said) | Status |
|---|---|---|---|
| step 0 reach fork + lever | `POST/GET /{slug}/reach`, `attach-root`, `check-mount`; outcomes egeria_reachable / egeria_cannot / re_only; surveyor named on the card | "✓ Egeria can read /deployments/loading-bay · led by Egeria's Folder Survey"; egeria_v6 "only Resource Explorer can read this path"; coco_data_lake "Egeria cannot read … · 2026-10-21" | built; both branches exist, only Egeria-led proven on a real survey |
| step 0 Egeria read-back (F2, F1 counts, inaccessible) | `folder-survey` GET reads Egeria's report (cc91503f), analysis level printed, file-name-CSV sentence | counts 134 files / 17 folders / 57.3 MB / 10 types; FILE TYPES, FILE EXTENSIONS; "1 not read by design (file-name log)" | built |
| step 1 walk extensions + folder_narratives | `walk-extensions` POST ("Measure beside Egeria's survey"), per-file rows per run, hashes under 10 MB, owner/group/mode, not-walked list | WALK MEASURES 2026-10-25; HEAVIEST FOLDERS, BY DEPTH; FOLDERS THAT DESCRIBE THEMSELVES (4, each "a proposal · confirm it on Curate") | built; Curate has no place to confirm the proposal yet ("Nothing to review for file systems yet") |
| step 2 file_profiles | `file-profiles` POST/GET, `read-egeria`, `publish` | "47 files profiled (35 by Egeria, 12 by RE) … published · 61 of 61 annotations in Egeria · report f326e58a"; per-file table with state/source/columns | built and published once (10-26) |
| step 3 four derived steps | fs_change_rates (Q-0102), fs_duplicates (Q-0106), fs_activity (Q-0107), fs_permissions (Q-0108) as library packages; `analyses/{id}/run`; Derive press; "DERIVED FROM THE WALK" | fs_activity measured 10-29 (+ access "not established · access times not recorded on this walk"); fs_permissions measured 10-29 ("134 of 134 world-readable, 131 world-writable"; RFA "nothing found"); change / duplicates / layout "not yet run" | built; 2 of 5 run on one file system |
| step 4 fs_layout_signals + narrative_docs | library step (Q-0104); `narrative-docs` POST/GET, section-chunked index `fs_<slug>_narratives` | layout "not yet run"; NARRATIVE FILES READ IN FULL: "4 files · 12 sections · 12 passages" (10-29) | built |
| step 5 data_class_match over profiles | `class-match` POST/GET, "PERSONAL OR SENSITIVE DATA" | "matches 201 columns of 47 profiled files … not matched yet" | built, never pressed |
| step 6 price line (F12, Q-0109) | `walk_derived.price` | "Egeria's Folder Survey leads here: its run time is Egeria's and is not priced …; RE's last walk beside it saw 134 files"; coco_data_lake "not priced · no walk has measured this file system yet" | built |
| steps 7–8 RE's own fs survey report carrying derived results + `declared_measures` hook | `EgeriaFileSystemSurveyor._publish_step_annotations`; publish table PRESS_KINDS includes filesystem, FS_PRESS_WORDS | Curate › PUBLISH · DECLARED MEASURES lists 10+ rows (inventory ×4, reach, fs_activity ×2, …) "0 chosen to publish"; ResourceProfile rows say "Resource Explorer does not write ResourceProfileAnnotation from a declaration yet" | built for ResourceMeasure; RP/RFA from a declaration not yet (build-note follow-up 7) |
| FU1 chat narratives + locator | `narrative_context.py` (compile section `narratives`, 3 absence wordings); `GET /api/filesystems/by-passages?q=`; #912, #913 | code and tests; not exercised today (a chat question is a journal write) | built, unproven on screen |
| §4 screens: register on /next | `fs-register.js`, "＋ Add a file system" in the sidebar; Egeria fields from configuration | door present (not pressed) | built |
| §4: Egeria reports list (F-10) | EGERIA'S OWN SURVEYS: Survey Folder ✓, Catalog and Survey ✓, "Run again in Egeria →" | present | built |
| Classic retirement | commit 308ac047 (2026-10-21) deleted `static/index.html` and 26 Classic-only routes | `/static` holds only admin-feedback.html, next/, re-api.js | **done** |

Egeria side (read-only): FileFolder `FileFolder::loading_bay:/deployments/loading-bay` (060e5efb) and 6 CSVFile
elements under it; reports cc91503f (folder survey) and f326e58a (file profiles). coco_data_lake: no element found by
name; egeria_v6: none expected (RE-led, root folder attach not checked).

## 2. Missing or unproven

**Missing**
- **§3a `FileSystemSurvey`**: not authored anywhere. No Dr.Egeria document under docs/dr-egeria/survey-definitions
  (only repo-survey-definition-*.md), no GovernanceActionProcess in Egeria matches "FileSystemSurvey" or "Survey", and
  the card says "No Survey Definitions have been authored for File System Directory yet". Build-note follow-up 8 says
  "not started". The pieces it would chain all exist (fs_reach outcomes, Egeria's survey-folder, the walk, profiles,
  narratives, layout, class match, change rates).
- **Remote reach** (DESIGN-FILES-REMOTE-REACH): slice 1 (two local outcomes) built; slice 2 SFTP and slice 3 S3 not
  started; §6 open items (credential model, listing without remote commands, which host is the gate) still unanswered.
- **Readers for Q-0101 (F3), Q-0105 (F11), Q-0109 (F12)**: the steps exist (file_profiles, folder_narratives /
  narrative_docs, the price line) but no catalogue entry names them, so the rows say "no reader yet" (build-note
  follow-up 2; read live: has_data null on all three).
- **Curate for file systems**: "Nothing to review for file systems yet" — the folder-description proposals ("confirm it
  on Curate") have no place to be confirmed; the publish table is the only Curate content.
- **RP/RFA from a declaration**: the publish table refuses ResourceProfile rows ("does not write … from a declaration
  yet"); fs_activity and fs_permissions results therefore publish only inside the walk's own report.

**Unproven (built, never walked end to end on 8813)**
- The RE-led branch: egeria_v6 was registered and surveyed once (10-21, re_only) but never profiled, derived, read or
  published; the brief's gate (§5) names this path as the RE-led half and its cautions (secrets, bulk, nothing written
  into the path) have no recorded run.
- The Egeria-led gate file system: coco_data_lake's reach has been "Egeria cannot read /deployments/coco-data-lake"
  since 10-21 (OMES-SURVEY-ACTION-0018, FileException in folder-survey-service). Either the container path is wrong or
  the mount is missing in the quickstart; until fixed, the Egeria-led fork is proven on loading_bay only.
- Publishing declared file-system measures: 0 chosen, no measure proof row; the Publish is the folder survey's press.
- Second walk / fs_change_rates, fs_duplicates, fs_layout_signals: never run anywhere.
- Chat citing narrative passages, and the locator route: not exercised on screen.

**Defects read today**
- **F39.** Curate publish table on loading_bay: File System Inventory rows say "Not measured: no survey has walked this
  file system yet", while the card shows Egeria's survey (134 files) and RE's walk beside it. The row reads RE's own
  `filesystem_inventory` run, which the Egeria-led branch never makes.
- **F41 (same root, on the Questions tabs).** Q-0094, Q-0100 (Scouting) and Q-0095 (Discovery) read has_data=false on an
  Egeria-led file system: their only analysis is `filesystem_inventory`. Egeria's Folder Survey answered them (counts,
  file types, depth from the walk) but the fact layer does not read the Egeria-led result as that analysis. The brief's
  own rule ("Egeria answers, RE reads back") is not carried into the facts. Fix: `_filesystem_inventory_results` reads
  the folder-survey read-back when the fork is Egeria-led, with the surveyor named.
- **F40 (words).** fs_activity's unit is a template that was never filled: "files not modified for more than Cold Days
  days (cold)" on the publish row; the step's result holds the actual Cold Days value. Say "files not modified for more
  than 365 days".
- **W19.** The WALK MEASURES header says "measured by Resource Explorer · 2026-10-25 · beside Egeria's Folder Survey ·
  measured by Resource Explorer · beside Egeria's Folder Survey" (the sentence twice); the FOLDER SURVEY line says
  "surveyed by Egeria's Folder Survey · 2026-10-25 · surveyed by Egeria's Folder Survey" (same doubling).
- **W20.** FILE TYPES lists "null 11" and FILE EXTENSIONS "null 2": Egeria's unclassified files should read
  "unclassified" (the Unclassified count card already says 11).
- Carry-over: the Resource Reachability row's "generic measure · no native type declared" is honest but it is the
  only analysis without a declared type on the table.

## 3. Test walk (a person follows this; presses and Egeria writes marked)

Resource: **loading_bay** for the Egeria-led path (already registered and surveyed); **egeria_v6** for the RE-led path;
**coco_data_lake** only after its reach is fixed. Read each screen before and after a press; the words below are what
the screen should say.

| # | Step | How | Press? | Egeria write? | Pass words |
|---|---|---|---|---|---|
| 1 | Choose the file system | sidebar FS › Loading bay; URL `?type=filesystem&resource=loading_bay&stage=discovery&tab=survey` | no | no | header "surveyed Nd ago · published to Egeria"; reach "✓ Egeria can read … · led by Egeria's Folder Survey" |
| 2 | Check reach again | FOLDER SURVEY › **Check again** | yes | no (an Egeria read; it may queue an Egeria engine action — treat as a write) | same sentence with today's date; surveyor unchanged |
| 3 | Survey (Egeria-led) | **Survey** | yes | **yes** (Egeria runs survey-folder, writes a report) | "surveyed by Egeria's Folder Survey · <today> · published · report <guid>"; counts refresh; "analysis level: top level only" |
| 4 | Walk beside it | **Measure beside Egeria's survey** | yes | no | WALK MEASURES dated today; "per-file rows kept · N files"; price line shows the walk's time; a second walk makes fs_change_rates derivable |
| 5 | Profile data files | **Profile data files** | yes | **yes** (Egeria's CSV survey per CSV; RE profiles the rest) | "profiled · <today> · N files profiled (a by Egeria, b by RE)"; FILES table states |
| 6 | Derive | **Derive** | yes | no | all five derived steps show a state: fs_change_rates "measured" (two walks) or its guard; fs_duplicates measured or "no hashes"; fs_layout_signals measured or "nothing found" with what it looked for; fs_activity; fs_permissions |
| 7 | Read narratives + match classes | **Read narrative files**, then **Match data classes** | yes, yes | no (reads Egeria's data classes as the signed-in person) | "N files read in full · S sections · P passages"; "matched … of 201 columns" or the per-column reasons; nothing created in Egeria |
| 8 | Questions | each stage's Questions tab | no | no | Scouting Q-0094/Q-0100, Discovery Q-0095/Q-0102/Q-0104, Analysis Q-0106–Q-0108 show ✓ with the step's words; Q-0101/0105/0109 still "no reader yet" (known gap) |
| 9 | Chat | ask "which files has nobody touched for a long time in loading_bay?" and "what do the README files in loading_bay say?" | yes (a journal write in RE) | no | the first cites fs_activity's rows; the second cites "stated by <path> § <section> — proposal" passages; an unread file system says "not read yet" |
| 10 | Publish measures | Curate › Describe › PUBLISH · DECLARED MEASURES: choose a ResourceMeasure row (e.g. inventory `files` once F39 is fixed, or fs_duplicates/fs_change_rates), then FOLDER SURVEY › **Publish these measures to Egeria** | yes | **yes** (RE's own fs survey report + annotations) | row "published as <you> · read back <time>"; the report GUID on the card; a second press re-links, no duplicate |
| 11 | RE-led fork | egeria_v6: steps 3–7 with **Survey** (RE's walk, since Egeria cannot reach the path) | yes | step 3 publishes RE's folder annotations if Publish is pressed | reach "only Resource Explorer can read this path"; report names Resource Explorer; nothing written under /Users/dwolfson/localGit/egeria-v6 (check `git status` is unchanged) |
| 12 | Egeria-led gate fs | coco_data_lake: fix the mount/path first (Egeria's FileException), **Check again**, then steps 3–7 | yes | yes | reach flips to "✓ Egeria can read"; otherwise record the failure sentence as the result |

Presses: 2–7, 9–12. Egeria writes: 3, 5, 10, 11 (if published), 12. Run them with the owner's word; the coordinator
logs each press's clock time so the activity rows can be matched.

## 4. The document

**What the user guide says today (docs/user-guide.md, read 2026-10-28):** §5 "File systems" (lines 404–424) describes
registration (host + container path), the Survey & analyses card section by section with its seven presses, the walk
measures, narratives, profiles, the derived steps and Egeria's own surveys, and says "remote mechanisms are not built".
§7 "A file-system reach" (two lines) and §10's row "Remote reach for file systems". The CLI section lists
`filesystem register/list/survey [--egeria]`. Tutorial walk 4 (docs/tutorial/04-a-file-system.md, 2026-10-29) shows
the same card in four screenshots and ends with two "not built yet" items (Curate review for file systems, remote
reach). Nothing in the guide tells a person the order to press things in, what each press costs, which branch of the
fork they are on and why, or what to do on Curate.

**What a Files section or walk should contain** (plain language, nine steps, the tutorial's house style):
1. What a file system is to RE: a registered root, local or in Egeria's container, and the fork — "if Egeria can see
   the path, Egeria surveys it and RE measures beside; if not, RE does both" — with the two sentences the reach line
   shows and how to fix a wrong container path.
2. Register one (the sidebar door, the four fields, what the Egeria fields come from, what is never written into the
   path).
3. The first survey: the price line before the press, what Egeria's Folder Survey keeps (counts, types, the file-name
   CSV it writes inside its own container and RE never reads), what RE's walk keeps (per-file rows, hashes under
   10 MB, owner/group/mode, not-walked directories).
4. The five derived readings and their honest absences ("one walk only; a change needs two", "no hashes", "access
   times not recorded on this walk", "no stat fields") — the words the screen uses, each explained once.
5. Narratives: folders that describe themselves, the full read, "a proposal, never a fact"; how chat cites them.
6. Data files: who profiles what (Egeria's CSV survey, RE's pandas), the header-looks-like-data case, the class match
   and why it is a proposal.
7. The questions this answers, by stage, with the three still "no reader yet".
8. Publishing: what the folder survey's Publish sends, the declared-measures table, read-back and proof words.
9. Not built yet: the single FileSystemSurvey definition, remote (SFTP/S3) reach, Curate review of file-system
   proposals — each with its design note.
The walk in §3 above is the test script for that section; the screenshots for the tutorial come from running it.
