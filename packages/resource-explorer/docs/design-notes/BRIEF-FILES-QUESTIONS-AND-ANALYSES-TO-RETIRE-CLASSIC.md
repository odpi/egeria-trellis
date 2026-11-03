# BRIEF — Files: the questions and analyses that finish the survey, so Classic can retire (2026-10-20)

From the design session at the owner's request ("we need to add more
questions and analyses to the Files surveys so we can finish that off and
retire the classic interface"). File system, never "file share".

## 1. Where Files stands

- **Questions.** Two of the 95 are the file system's own (Q-0094 what
  kinds of files and how much; Q-0095 recognisable tabular files), and
  Q-0011, Q-0056, Q-0066, Q-0069, Q-0070, Q-0080, Q-0081 and Q-0087 name
  `database;filesystem` but their answering analyses are database ones;
  on a file system most of them answer nothing today.
- **Analyses.** One registry step, `filesystem_inventory`
  (`surveyors/filesystem/survey_definition_adapter.py:301,335`), fed by
  `LocalFileSystemSurveyor` (walk: counts, bytes, extensions, formats,
  access/modify/create times, inaccessible paths; `_profile_data_files`:
  per-file column profiles for tabular files under a size limit, pandas
  required) and the hybrid and Egeria surveyors. File types already
  publish as Egeria's folder-survey annotations (file types note).
- **Classic-only screens** (parity note §3): register a file system with
  its Egeria fields (F-02), walk-and-survey modal (F-04, F-05), publish
  (F-06), reachability check (F-07), the survey report with metric cards
  and type breakdown (F-08), the per-file data profile accordion (F-09),
  and the Egeria reports list (F-10, partial). F-11 (run a definition) is
  present on /next.

- **Live state (read-only, 2026-10-20):** no file system is registered on
  8813 (`GET /api/filesystems/` is empty), so no survey rows, profiles or
  Egeria reports exist for any file system, and nothing can be gated on
  stored data today. The parity inventory of 2026-10-11 (`~/.re-gate/`,
  PI ids) has register (PI-022), per-row survey (PI-087), walk and profile
  (PI-091, partial), publish (PI-092), reachability (PI-093), the report
  (PI-094) and per-file profiles (PI-095) missing; list, remove, Egeria
  reports and run-a-definition done. The 10-11 hold ("file systems after
  Alpha") is superseded by the owner's 2026-10-20 decision to build the
  file-system block on /next before retiring Classic.

Classic retires when a person never needs it for a file system: register,
survey, read the answers, publish, and see what Egeria holds, all on
/next, with the questions answered from data the survey stores. Because
nothing is registered, the first slice is registration plus one walk on a
real local path, and every analysis below is gated on that walk's rows.

## 1a. What Egeria provides, read from its source (origin/main, 2026-10-20)

The first draft of this brief did not inventory Egeria's own file survey
services; the owner asked, and this section corrects the build order
below. Egeria's `file-survey-connectors` module ships three survey action
services, and the follow-Egeria's-lead rule applies: where Egeria
measures something, RE runs Egeria's survey and reads it back rather
than measuring it again; RE's own analyses are distinct types on top.

| Egeria service | Analysis steps (annotation types) | What it measures |
|---|---|---|
| **Folder Survey Service** (`FolderSurveyService`, request parameter `analysisLevel`) | Capture File Counts (`FileDirectoryMetric`: fileCount, totalFileSize, subDirectoryCount, readable/writeable/executable counts, symLinkFileCount, hiddenFileCount, fileNameCount, fileExtensionCount, fileTypeCount, assetTypeCount, deployedImplementationTypeCount, unclassifiedFileCount, inaccessibleFileCount, lastFileCreationTime, lastFileModificationTime); Profile File Extensions; Profile File Types; Profile Asset Types; Profile Deployed Implementation Types; Profile File Names to External Log (`ResourceProfileLogAnnotation`); Missing File Reference Data; Inaccessible Files (as `RequestForAction`) | the whole tree's counts, bytes, permissions as counts, times, and the classification of every file by Egeria's file reference data (`FileClassifier`: extension, file type, asset type, deployed implementation type), with a request for action when reference data is missing |
| **File Survey Service** (`FileSurveyService`) | Extract File Properties (`FileMetric` per file: fileName, pathName, fileExtension, fileType, deployedImplementationType, encoding, assetTypeName, canRead, canWrite, canExecute, symLink, hidden, creationTime, lastModifiedTime, fileSize, recordCount); Derive Schema From Data | one file's properties and, for data files, its schema |
| **CSV Survey Service** (`CSVSurveyService`) | Derive Schema From Data: `DataField` schema attributes per column plus a `ResourceProfileAnnotation` per column | column names, types and value profiles of a CSV |

RE already publishes file types as these folder-survey annotations (the
file-types note) and reads native survey reports back for file systems
(`routes/native_surveys.py`). So, against the twelve questions:

- **Egeria answers, RE reads back:** F2 (file kinds, better than RE's
  extension map because Egeria classifies against reference data and
  names what it could not classify), the counts half of F1, the counts
  half of F9 (readable, writeable, executable, hidden, symlinks), F3 for
  CSV files (schema and column profiles), inaccessible paths, and the
  folder-level last-created and last-modified times.
- **Egeria does not measure, RE derives:** per-folder bulk and depth
  (F1), change since the last walk (F5), duplicates by hash (F6), the
  per-file age distribution (F8; Egeria gives the folder's latest times
  only), owner, group and world-readable (F9; Egeria gives counts, not
  who), layout signals (F10), documentation inside (F11), the price
  (F12), and the data-class match over profiles (F7, which can run over
  Egeria's CSV `DataField`s as its input).
- **RE's walk shrinks to what Egeria does not keep:** per-file rows per
  run with path, size, mtime, owner, group, mode and a content hash under
  a limit, so that F5, F6, F8 and F9 are zero-fetch derivations. Where
  Egeria's File Survey Service already extracts a file's properties, RE's
  row stores the report GUID it came from, never a second measurement.

The first slice therefore becomes: register the file system on /next,
attach its root as an Egeria folder asset, run the reachability probe,
and let its outcome choose the lever (`DESIGN-FILES-REMOTE-REACH.md`
§2, ruled 2026-10-21): where Egeria can read the canonical mount, run
Egeria's Folder Survey at the chosen `analysisLevel` through the
native-survey port RE already has and read it back; where only RE can
read the path (true of the owner's gate path, which Egeria's container
cannot see), RE's walk leads and publishes the same folder-survey
annotation types under its own SurveyReport, marked "surveyed by
Resource Explorer · Egeria could not reach the path". Only then run RE's
walk extensions for what Egeria does not keep. The CSV Survey Service runs per data file on request, as
the Egeria lever for F3, with RE's pandas profile kept for formats Egeria
has no service for (Parquet, JSON lines, Excel), labelled as RE's.

## 2. The questions to add (file system only, each with its analysis)

Numbered as proposals; the catalog assigns ids on the append.

| # | Question | Stage | Answering analysis | Stored today? |
|---|---|---|---|---|
| F1 | What is the shape of this file system: how deep, how wide, where does the bulk sit? | Scouting | `filesystem_inventory` (depth, breadth, the ten largest folders by bytes) | partly: totals yes, per-folder bytes and depth to add to the walk |
| F2 | What kinds of files are here, by count and by bytes, and which are data files? | Scouting | `filesystem_inventory` (formats summary, data-file classification) | yes (Q-0094) |
| F3 | Which tabular or structured files are here, with what columns and types? | Discovery | `file_profiles` (today's `_profile_data_files`, promoted to its own step so it can be run alone and priced) | yes, as part of inventory |
| F4 | Is this file system reachable by Egeria's engine host, or only locally? | Discovery | `resource_reachability` (built for Classic, F-07) | yes (Classic) |
| F5 | What has changed since the last walk: new, modified, removed files, and growth? | Discovery | `fs_change_rates` (two walks differenced on path, size and mtime) | no: needs the per-file rows kept per run |
| F6 | Which files are duplicates of each other, or of files in another registered file system? | Analysis | `fs_duplicates` (size plus content hash for files under a limit; cross-resource by hash) | no: hash column to add |
| F7 | Which files or folders contain personal or sensitive data? | Analysis | `data_class_match` on file profiles (reuses the database analysis over `file_profiles` columns, plus filename rules) | partly: data classes exist; profiles need the column rows kept |
| F8 | Which files has nobody touched for a long time, and which are hot? | Analysis | `fs_activity` (from access and modify times already walked; "can't tell" when atime is off) | yes, times are walked; the analysis is new |
| F9 | Who owns or can read what, and are there world-readable sensitive files? | Analysis | `fs_permissions` (owner, group, mode from stat; joined to F7) | no: stat fields to add to the walk |
| F10 | Does this file system contain a recognisable structure: a project, a dataset layout, an archive, backups? | Discovery | `fs_layout_signals` (README, manifest, schema files, dated folders, backup patterns) | no |
| F11 | What do the narrative files inside it (README, INDEX, ABOUT, NOTES, a data dictionary, a codebook, LICENSE, CHANGELOG) say the contents are, folder by folder? | Discovery | `folder_narratives` (§2a), sought proactively, read as evidence, proposals never facts | no for file systems |
| F12 | How much would it cost to survey fully, and which parts are not worth it? | Discovery | the launcher's cost line from F1's bytes and file counts (the price-before-you-run rule, X-06) | partly |

### 2a. Narrative files, sought proactively (owner, 2026-10-20)

The owner's point: README-type files often carry the most valuable text
about a folder's contents, so the survey should look for them rather than
wait for a person to open them. `folder_narratives` is a zero-fetch step
over the walk's rows (one read of each candidate file, size-capped):

- **Candidates, by name and type, per folder:** README, READ_ME, INDEX,
  ABOUT, NOTES, MANIFEST, CONTENTS, CHANGELOG, LICENSE, COPYING,
  CITATION, a data dictionary or codebook by name (`*dictionary*`,
  `*codebook*`, `*schema*`, `*layout*`) and any `.md`, `.rst` or `.txt`
  file whose name matches the folder's or a sibling data file's name.
  Egeria's file classifier already types them as documents; the step
  reads the classification, not the extension.
- **What it extracts, each with the file's path and modified time as its
  evidence:** the folder's stated description (title and first
  paragraph); the declared contents (lists of files, columns or fields
  the text names, matched against the walk's rows and the profiles, so
  "described in the README" appears on the file and on the column); the
  licence named (answers Q-0012 and Q-0030 at folder level); a stated
  owner or contact (a proposal for Q-0003); a stated source or
  provenance; dates the text gives for collection or coverage (feeds
  DataScope as a proposal, Q-0056).
- **How it is shown:** on the folder row, "described by README.md ·
  modified 10-14 · open"; on the Describe tab of Curate, the description
  as a proposal "stated by README.md" that a person confirms; on a
  column, "described in codebook.txt § Variables" with the doc link of
  the Artifacts design. Nothing from a file becomes a fact without a
  signature; the two-channel rule holds.
- **Publishing:** the narrative file is catalogued as a document asset
  beside the data it describes, linked by Egeria's external-reference or
  documentation relationship, so the Resource Locator and the answering
  app can quote it with its source; the extracted statements publish as
  annotations marked "stated by <file>" until confirmed.
- **Beyond file systems:** the same step serves open data (a portal's
  data dictionary and codebook, §6 of the open-data brainstorm) and
  repositories (the ingestion-rules note's documentation shape), one
  implementation, three callers.
- **Where it sits in the order:** with the walk extensions (step 1 of §3),
  because it needs only the walk's rows and one read per candidate, and
  because it gives the Describe tab something to say on the first visit.

Questions that already apply (`*` kinds) and need no new analysis on a
file system: Q-0002, Q-0003, Q-0005, Q-0012 (licence from a LICENSE file,
F10 supplies it), Q-0038, Q-0039, Q-0054 (becomes F4), Q-0055 (DataScope
from the mount's host and the walk's dates, the data-scope note).

Database questions that keep `filesystem` in their kinds but answer
nothing there today: Q-0011, Q-0056, Q-0069, Q-0070, Q-0081, Q-0087 lose
`filesystem` from their kinds until an analysis answers them on files;
Q-0066 (backups) stays, answered by F10's backup patterns plus the human
half; Q-0080 stays, answered by F7.

## 3. Analyses, in build order

0. **The reachability fork, then the lever** (no new analysis): the
   registration attaches the root folder and records the canonical mount
   as Egeria's container path, checked against the deployment's mounted
   prefixes at attach and verified by the first Egeria-led survey's
   outcome (no probe launches a survey; `DESIGN-FILES-REMOTE-REACH.md`
   §2); the probe's outcome names the surveyor. Noise directories the
   walk skips (`.git`, `node_modules`, `.venv`) are counted and named as
   "not walked", never silent; the Folder Survey's `analysisLevel` is
   printed on the report.
   Egeria-reachable: Egeria's Folder Survey with `analysisLevel`, read
   back, fills F2, the counts of F1 and F9, and the inaccessible list.
   RE-only: `filesystem_inventory` publishes those same annotation types
   itself under its own report. In both cases RE's own measurements are
   only what §1a says Egeria does not keep. The gate registers two file
   systems, the owner's path (RE-led) and the coco data lake at its
   container path (Egeria-led), so the fork is proven in one walk.
1. **Walk extensions** (one step, one PR): per-folder bytes and depth,
   owner/group/mode, content hash under a size limit, per-file rows kept
   per run with a run id, each row naming the Egeria report it duplicates
   nothing from; plus `folder_narratives` (§2a) over those rows. These
   feed F1, F5, F6, F8, F9 and F11.
2. **`file_profiles` as its own step** with its cost line: Egeria's CSV
   Survey Service per CSV file, RE's pandas profile for the formats Egeria
   has no service for, both stored as column rows per run with their
   source named. Feeds F3, F7.
3. **`fs_change_rates`, `fs_duplicates`, `fs_activity`, `fs_permissions`**:
   four zero-fetch derived steps over the kept rows, each a library-style
   step with a nothing-to-derive guard ("one walk only", "no hashes",
   "access times not recorded on this mount", "no stat fields"), each
   publishing as Egeria's folder-survey annotation types where one fits
   (follow Egeria's lead: `ResourceMeasureAnnotation`,
   `ResourceProfileAnnotation`, `RequestForActionAnnotation` for a
   world-readable sensitive file).
4. **`fs_layout_signals`** and the section-chunked docs ingestion of the
   narrative files found in step 1: F10 and the long-form half of F11.
5. **`data_class_match` over file profiles**: F7, with the same
   proposal-only rule as databases.

### 3a. One Folder Survey definition that mixes Egeria's steps and RE's (owner, 2026-10-21)

The owner: "we can write our own survey that mixes steps Egeria executes
and that we execute". The mechanism exists: RE's survey definitions are
Egeria governance action processes whose steps carry `executes_at`, and
the engine-host work (step 4's mixed process, case 3) lets Egeria
dispatch RE's steps. Ruling on the shape and the order:

- **One definition, `FileSystemSurvey`**, authored in Dr.Egeria like the
  others. Its first step is RE's `fs_reach` (the probe of
  `DESIGN-FILES-REMOTE-REACH.md` §2), which completes with one of two
  guards, `egeria-reachable` or `re-only`. Two next-step links route on
  the guard: `egeria-reachable` to Egeria's `FileSurvey::survey-folder`
  at the container path, `re-only` to RE's `re-folder-survey` walk (the
  skip-steps variant of §4's backlog, producing the same annotation
  types). Both join, on `survey-completed`, into the common RE steps:
  walk extensions (per-folder bytes and depth, per-file rows),
  `folder_narratives`, `file_profiles`, `fs_layout_signals`,
  `data_class_match`, and `fs_change_rates` on a repeat. The fork picks
  steps per file system inside one run, as Egeria's process model
  intends, instead of one lever for the whole survey.
- **Order.** It needs the RE steps to exist, so it comes after step 3's
  walk extensions and after `folder_narratives` and `file_profiles` are
  steps. It ships first **RE-coordinated** (the executor RE uses today
  for the Database Analysis Survey, where `executes_at: egeria` steps
  already run beside RE's), which needs no engine-host wiring; the same
  definition becomes **Egeria-coordinated** through case 3 once that
  gate passes, behind the "coordinated by Egeria" control.
- **Honesty.** Each step's report names its surveyor; the definition's
  manifest says which branch ran and why ("re-only: Egeria's mounts do
  not include this path"); the guards are declared `producedGuards` on
  `fs_reach`, and the reconciler checks the two links as it checks every
  authored definition.

## 4. The /next screens that retire Classic for files

Only what the parity rows name, in the Publish-ruling shape:

- Register a file system on /next (F-02, PI-022): slug, name, mount, canonical
  mount, group; the Egeria fields come from the platform configuration,
  never typed per resource.
- The survey press with its cost line (F-04, F-05) and the per-analysis
  run route for file systems (the 404 noted at F-05).
- The report on Understanding: At a glance (F1, F2 as the metric cards
  and treemap the multi-resource spec drew), Contents (the folder tree
  with the Include | Leave out selector and depth, the same module as
  databases, directories as parts), Over time (F5, F8).
- Data-file profiles (F-09) as rows under Contents, opened per file.
- Publish (F-06) through the same module: parts are directories, depth is
  folder then file, detail is the profile facets; Egeria's folder
  cataloguing as the lever; proof rows as everywhere.
- Reachability (F-07) as a line on the resource header, not a modal.
- Egeria reports list (F-10) as on databases.

## 5. Gate, by use

**Path (owner, 2026-10-20): `/Users/dwolfson/localGit/egeria-v6`**, the
live checkout of every repository, worktree and runtime volume. Three
cautions follow from that, and the step-1 brief carries them with a test:

1. The tree holds secrets (`.env` files, `.omsecrets`, runtime volumes
   with platform data, credential files) and git internals. The walk
   records names, sizes and times only; `folder_narratives` reads only
   its candidate files; `file_profiles` reads only tabular data files
   under the cap; nothing else is read or published. Egeria's Folder
   Survey classifies and counts everything, which is fine. Its "Profile
   File Names to External Log" step cannot be turned off (read from
   source, 2026-10-21): on every Egeria-led survey Egeria writes a
   file-name CSV inside its own container, which RE never reads, stores
   or shows, and the report says so. The owner's path is RE-led, so no
   file-name list of it is written anywhere; the coco data lake is demo
   data.
2. The bulk sits in git object stores, `node_modules` and the docker
   volumes. That is the honest answer to F1 and a good first test;
   `analysisLevel` and the walk's limits are set so the first survey
   finishes in minutes, and the gate records the wall time.
3. The registered root is the owner's working tree: registration and
   survey write nothing into the path (no markers, no caches there),
   proven by a test; the second walk for F5 follows the owner's ordinary
   work, nothing staged.

The owner registers that path on /next (RE-led, since Egeria's container
cannot see it) and the coco data lake at its container path (Egeria-led),
runs both surveys with their price shown, sees each report name its
surveyor, reads F1 to F3 on Understanding with the README-stated
descriptions on the folders that have one (F11), sees a recognised
layout (F10) and a data-class match on a profiled file (F7), publishes
two directories and reads them back in Egeria, and never opens Classic.
A second walk after a day's work shows F5 honestly. The flagged
world-readable file and the age distribution (F8, F9) are the second
slice's gate.

## 6. The owner's picks (2026-10-20) and what stays open

**First slice: F1, F2, F3, F5, F7, F10, F11.** In build terms: register
on /next and attach the root folder; Egeria's Folder Survey read back
(F2, the counts of F1); the walk extensions with per-folder bulk and
depth and per-file rows per run (F1, F5); `folder_narratives` (F11, and
the licence and provenance it finds); `file_profiles` with Egeria's CSV
survey plus RE's profile for other formats (F3); `fs_layout_signals`
(F10); `data_class_match` over the profiles (F7); `fs_change_rates` on
the second walk (F5). Hashes, duplicates, age distribution, owner and
mode (F6, F8, F9) and the price line (F12) follow.

- [ ] Hash limit and whether cross-file-system duplicate detection is in
      the second slice.
- [ ] The CSV lister (file systems arrive hand-entered or by CSV today):
      in this brief or the discovery-sources one.
