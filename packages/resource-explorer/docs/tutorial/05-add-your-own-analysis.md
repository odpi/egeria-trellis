# Walk 5 — Add your own analysis

*Screens read on 2026-10-29 on a test server, `origin/main` at `afdac240`, except where a line says it was read on `9d40eab4` by the architect session. Resource: `loading_bay`; the user-added steps are `fs_permissions` and `fs_activity`.*

This walk follows two steps that were added without changing Resource Explorer's code, and shows where each one appears. Only what was read on a screen is written here; the rest is under "Not built yet".

## 1. What you add

A library step is a package with a step manifest and a declaration: a result kind, measures (each with a unit and an as-of), absence sentences, chat extents, a confidence form and, optionally, an Egeria type. Installing the package registers it: the package declares a `pyegeria.analytics` entry point, and Resource Explorer and the Egeria Portal both read it, with nothing to configure. A report spec names the analysis by its registered name (`fs_permissions`), never by its Python path. (Steps built before 2026-10-31 declare `trellis.analytics`, which is still read as an alias.) The guide's section 7 has the whole table of what can be added.

What the gate found about "no code change" (the architect's reading of `origin/main` at `7b50ba89` on 2026-10-28): no Resource Explorer code names either step. Two pieces of configuration do: a generated entry in `configdata/analysis_catalog.yaml` and a hand-written question row in `configdata/question_catalog.yaml` (Q-0107 and Q-0108). So "no code change" holds for code, and not yet for configuration. The fix, reading installed steps' manifests at read time so the generated entry is no longer committed (finding F1), is merged on `main` (#915). The Explorer these screens were read on was still running `afdac240`, so no screen in this walk shows it.

## 2. Where it shows up: Survey & analyses, "Derived from the walk"

![The part of the Survey & analyses tab headed "DERIVED FROM THE WALK" for loading_bay. "kept here · sent to Egeria in the walk's own survey report when that walk is published". "File Activity · derived · 2026-10-29" with "● measured · 3 files modified within 7 days; 0 not modified for over 365 days (0 bytes)" and "◌ not established · not measured · access times not recorded on this walk" plus an explanation. "File System Change · not run yet", "Duplicate Files · not run yet", "File System Layout Signals · not run yet". "File Permissions · derived · 2026-10-29" with "● measured · 1 owners, 1 groups; 134 of 134 files world-readable, 131 world-writable" and "○ nothing found · no world-readable file has a secret-looking name". Below, "EGERIA'S OWN SURVEYS" and "Survey Folder".](images/05-own-analysis/05-derived-from-the-walk.webp)

*"File Activity · derived · 2026-10-29 · ● measured · 3 files modified within 7 days; 0 not modified for over 365 days (0 bytes)". "◌ not established · not measured · access times not recorded on this walk". "File Permissions · derived · 2026-10-29 · ● measured · 1 owners, 1 groups; 134 of 134 files world-readable, 131 world-writable · ○ nothing found · no world-readable file has a secret-looking name". The block's first line: "kept here · sent to Egeria in the walk's own survey report when that walk is published".*

Read the three states side by side. A **measured** line carries a number and its basis. A **nothing found** line says it was measured and there was nothing. A **not established** line carries the step's own sentence for what could not be told: access times were not recorded on that walk, so which files were read cannot be told. Nothing here is a zero for something that was not measured.

The step's rows were stored when a run was pressed on 2026-10-29 (activity entries `3ba02177` and `5fda082e`, read by the coordinator). This walk did not press a run.

## 3. A step that has never run

![The foot of the Analysis stage's Questions tab for "Coco data lake" (coco_data_lake). Three rows each carry an empty circle and "Nothing has been measured for this yet.": "Which files are duplicates of each other, or of files in another registered file system?" (fs_duplicates · never run), "Which files has nobody touched for a long time, and which are hot?" (fs_activity · never run) and "Who owns or can read what, and are there world-readable sensitive files?" (fs_permissions · never run), each with "copy as evidence", "run" and "notify me". The page header reads "never surveyed · not published to Egeria", "0 of 9 answered · Analysis"; the key "○ not run 5 · ◌ no reader yet 4".](images/05-own-analysis/05-never-run-coco-data-lake.webp)

*The page header reads "never surveyed · not published to Egeria" and "0 of 9 answered · Analysis". The key reads "○ not run 5 · ◌ no reader yet 4". The Q-0108 row reads "Nothing has been measured for this yet." and "fs_permissions · never run".*

On a file system where the step has not run, the screen says "never run", not zero. The Q-0108 row on `coco_data_lake` reads "Nothing has been measured for this yet." with "fs_permissions · never run" (read on `afdac240`; the architect read the same row on `9d40eab4`). The step's own "not measured" sentence is not used for a step that never ran; the screen uses Resource Explorer's generic words instead (finding F7, closed as "RE's state" on 2026-10-29).

## 4. Curate has no row for the step

![The Curate stage for loading_bay. A "FINDABLE" section with "RE group Ungrouped" and "tags No tags yet." Then "Nothing to review for file systems yet. Group, tags, ratings and notes above and below apply." Then "PUBLISH TO EGERIA · ✓ root folder attached · read back 2026-10-25 23:45 · asset 060e5efb-cf1f-468c-b708-7b8e605ceffc", a "Publish" button with "attaches the root folder only", and "Egeria reports · read 09:22 · 2 reports".](images/05-own-analysis/05-curate-file-system.webp)

*"Nothing to review for file systems yet. Group, tags, ratings and notes above and below apply." "✓ root folder attached · read back 2026-10-25 23:45". "attaches the root folder only".*

For a database a library step has a row in the publish table with "writes as". Today that row reads "generic measure · no native type declared" and its Publish is disabled (walk 3, section 4; findings F11 and F14); the fix is in build. A file system has no publish table, so the two steps have no row to publish from; the reason the measures wait is the one on the Survey tab ("sent to Egeria in the walk's own survey report when that walk is published"). **Publish** here attaches the root folder only; this walk did not press it.

## 5. Chat

Both steps are cited in chat. What the architect read on `9d40eab4` on 2026-10-29, not re-read here and not pressed in this walk: for Q-0108 on `loading_bay` the answer line reads "From 3 stored results · instructions, fs_permissions, fs_activity" and the answer cites the `fs_permissions` analysis; for the access-time question the answer says "access times were not recorded on this walk, so it cannot be determined which files have actually been read".

## Not built yet in this walk

Each item below was read as missing on the gate's screens. Dates are the gate's reading date unless stated.

- **The Questions tab's own figures for a library step** (2026-10-29, on `9d40eab4`): the row reads "fs_activity has no written summary — the figures above are the raw measures." with no figures above it, and "the numbers behind this ›" answers "Unknown analysis". Not re-read on `afdac240`.
- **A publish row on Curate for a file system's steps, with the reason they wait** (2026-10-29, read above on `afdac240`).
- **"writes as" on a file system** (2026-10-29): it exists on a database's publish table only.
- **A library step's own row publishing from a database's table** (2026-10-29): leg 7 of the gate was walked on the throwaway. The library rows are Publish-disabled (F14; the fix is in build). The mechanism was proven on RE's own measure instead: commit `0136c584`, annotation `d3065d8d` read back by its id (4 tables, as of 2026-10-25, ResourceMeasureAnnotation, createdBy peterprofile). A library step's own row: not yet.
- **A step's start-up line that names it** (2026-10-29): the line counts the installed library steps ("4 installed, none missing") and names none.
- **Changing a step's declared Egeria type, read on a screen** (2026-10-29): the rows changed with no Resource Explorer commit, but no file-system screen names the annotation type.
