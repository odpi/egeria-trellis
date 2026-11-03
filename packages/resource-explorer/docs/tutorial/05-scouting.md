# Part 5 — Scouting: run the overview survey and read the answers

Erin's first question is about the database: is `coco_pharma` alive, and what is in it? Peter answers it with the
cheapest stage, Scouting. He runs its [overview survey](glossary.md#overview-survey) once and reads what comes back.
Then he takes the same first look at OpenLineage and at the `loading_bay` folder.

**Before you start.** [Part 3](03-add-data-sources.md) and [part 4](04-the-stages.md): you are signed in as Peter,
the three sources are registered, and you know what the marks mean. This part takes about twenty minutes. It is
the first part that runs anything. The database survey and the repository survey keep their results in Resource
Explorer. The file system's survey is different: Egeria runs it, so it writes to Egeria (section 5c).

*Screens read on 2026-11-02.* The screenshots show `coco_pharma_trial` and `loading_bay_trial`, as in part 3. Use
`coco_pharma` and `loading_bay` on yours. The sidebar in some screenshots lists work lists that a new Quickstart
does not have; ignore them (gap G25).

## 5a. The database

1. **Open your database, click 1 Scouting, then Survey & analyses.**

   Click **DBs** in the sidebar, then `coco_pharma`. Under "SURVEY DEFINITIONS", the heading "SCOUTING · 1" lists
   one survey, "Database Scouting Scan": "3 steps · none fetch · runs 5 analyses". It is the database's overview
   survey (part 4). "none fetch" means it reads only the database's own catalog, not its rows, so it is quick.

   Beside it are two buttons. **Run…** runs it here. **Run · coordinated by Egeria →** asks Egeria to run it, and
   Egeria first needs the database published to it ("Egeria has not been given this database · register it to run
   Egeria's own survey · optional"). Leave that one for now.

2. **Click Run…, read the dialog, and click Run 3 step(s).**

   ![Is coco_pharma alive? The "Database Scouting Scan" dialog over the Scouting stage. It reads "This runs 3 step(s) against coco_pharma_trial." and "it has not yet run on this resource". Two boxes are unticked: "Try Egeria first (hybrid)", greyed, with "not published to Egeria · publish it on Curate first", and "Use different credentials for this run". Then "Nothing runs until you confirm.", and the buttons "Run 3 step(s)" and "Cancel".](images/05-scouting/S16-run-dialog.webp)

   *"Nothing runs until you confirm."*

   The dialog says what will run and against what: "This runs 3 step(s) against coco_pharma". Leave both boxes
   unticked. "Try Egeria first (hybrid)" is not available until the database is in Egeria. "Use different
   credentials for this run" is for an account other than the read-only one you registered in part 3.

3. **Watch it run.**

   ![coco_pharma being surveyed: the "Database Scouting Scan" card now reads "running · started just now" with the mark ◔, its button has become "Re-run…", and a line below it says "Launched GovActionProcess::DatabaseScoutingSurvey. It runs asynchronously — its results appear in Dashboard and in the question rows as each step lands, not when this line changes."](images/05-scouting/S17-running.webp)

   *◔ "running · started just now".*

   The card shows ◔ "running · started just now", and **Run…** becomes **Re-run…**. The line under the card says
   the results "appear … in the question rows as each step lands". It names the survey by its internal name,
   "GovActionProcess::DatabaseScoutingSurvey" (gap G16).

   On the Quickstart this took a few seconds. When it finishes, **Activity** in the header has a new entry,
   "Database Scouting Scan: 3 steps: all ran".

4. **Click Questions, and read the count line and the header.**

   Before the survey, the count line read "1 of 9 answered · Scouting" (part 3). It now reads "5 of 9 answered ·
   Scouting". The header reads "surveyed just now · local survey · not published to Egeria". "local survey" means
   Resource Explorer ran it and kept the results. Nothing went to Egeria.

   Under it, a line says what the survey could see: which account it connected as, how many of the 30 schemas that
   account can see, and how many tables it can read ("sees 28 of 30 schemas, SELECT on 261 of 266 relations"). Your
   numbers depend on your account. Every count on this page is "within what this account can see".

5. **Read one answered question.**

   ![What is in coco_pharma: three answered question rows. "How big is this database — schemas, tables, views, columns, rows and bytes?" answers "30 schema(s), 29 with tables, 28 visible to this credential", with a source line and the links evidence, copy as evidence, re-run, the numbers behind this ›, and notify me. "Which schemas carry the data, and which are system, empty or staging?" answers "aus_inventory 16 tables". "Is this database alive — writes since the statistics were reset, last vacuum or analyse, and is anything reading it?" answers "34,207 writes and 2,989,434 reads since statistics collection began (never reset)."](images/05-scouting/S18-answers.webp)

   *Is it alive? "34,207 writes and 2,989,434 reads since statistics collection began."*

   Each row has the same parts:

   - **The mark and the question.** ✓ means it was measured and checked.
   - **why is this here**, which opens to say why the question is asked.
   - **The answer**, in plain words. "How big is this database" gives the schemas with tables, 29, and the schemas
     this account can see, 28. "Is this database alive" answers "34,207 writes and 2,989,434 reads since
     statistics collection began (never reset)." So yes: something writes to it and something reads it.
   - **A note in blue** under some answers, with a limit on the answer. Here: "row counts for the other 5 are
     planner estimates, not exact", because the account can read 261 of the 266 tables.
   - **The source line**: which analyses answered, and when ("schema_inventory, row_count_snapshot · run just
     now"). Then **evidence**, **copy as evidence**, **re-run**, **the numbers behind this ›** and **notify me**.
   - **Was this right?**, with a thumbs-up, a dash and a thumbs-down. Part 9 uses it.
   - **ask in chat ›**, beside the question. Part 8 uses it.

   The source line names analyses by their internal ids, such as "schema_inventory" (gap G16).

6. **Click the numbers behind this › on "How big is this database".**

   A table opens under the answer, with each number the answer is built from: "relation count 266", "column count
   1,916", "schema total 30", "schemas visible 28", "base table count 263", "view count 3", and so on. Under it:
   "Read from schema_inventory", and when it ran. Click the link again to close it.

   ![The numbers behind "How big is this database": a table under the answer. relation count 266, column count 1,916, schemas with tables 29, schema total 30, schemas visible 28, base table count 263, view count 3, materialized view count 0, foreign table count 0, catalog only table count 5. Under it: "Read from schema_inventory, run 3 minutes ago".](images/05-scouting/S18b-numbers-behind.webp)

   *The numbers behind the answer, and where they were read.*

7. **Find a question this survey did not answer, and read its mark.**

   Not every row has ✓. In the screenshots:

   - "What is this resource, and what is it for?" still shows ∅ "nothing found": no description is recorded. That
     is an answer.
   - "Who owns this resource (accountable owner), and who administers it?" shows ○ "not yet run". Part of it needs a
     person, and part needs an analysis this survey does not run. Part 7 adds what only a person knows.
   - "What explicit license does this resource use…" shows ⚠ "needs you", with **Answer this →**.
   - "What engine and version is this…" shows ◌ "no reader yet": "No mechanism exists for this yet — no analysis
     produces it."
   - "What can the survey credential see, and what is it blind to…" shows ◐ "partial".

   ○ and ∅ are never the same (part 4). ○ means nobody has looked yet; ∅ means somebody looked and found nothing.

**What can go wrong.**

- **The account cannot see everything.** That is normal for a read-only account. The header says how much it sees, and
  the answers say "within this credential's visibility". Counts are not wrong; they are counts of what this account
  can see. A broader account may see more ("Broader access may reveal more.").
- **A ✓ whose words say part of it was not measured.** "Is this database alive" has ✓, and under it: "statistics_
  freshness has not run, so this answer is partial", with **run** beside "statistics_freshness · not yet run". Read
  the words under a mark, not only the mark (gap G13). Running that one analysis is part 6's kind of step.
- **The run fails.** The card shows ✕ with the reason, and the Activity entry says what failed. (Not shown here.)
- **A step needs another step first, or needs to read rows the account cannot read.** The row says so and offers a
  choice before anything runs. (Not shown here: the Scouting survey did not need either on the Quickstart; gap G22.)

## 5b. The repository

Scouting a repository works the same way: **Repos**, `openlineage`, **1 Scouting**, **Survey & analyses**, and the
overview survey is "Scouting Survey" (part 4). Its run reads GitHub and downloads the repository, so it takes longer
than the database's.

On the Quickstart, OpenLineage had been surveyed before, so the screenshot shows a stored survey and nothing was run
again.

![OpenLineage, first look: the repository's Scouting stage. The header reads "OpenLineage/OpenLineage", "surveyed 27d ago · not published to Egeria", and tiles: WEBSITE openlineage.io, LANGUAGE java (measured), STARS 2654, FORKS 526, CONTRIBUTORS 185, LAST PUSHED 52d ago, SIZE 80.6 MB, SECURITY "? not read", DEPLOYMENTS 1220 · 55d ago. Under them, "SCOUTING SIGNAL 149,433 lines of code across 1,577 source files, 2,915 files in total · Health 95/100", and "6 of 6 answered · Scouting".](images/05-scouting/S19-openlineage-header.webp)

*OpenLineage's header after a Scouting survey. "? not read" is not zero.*

The header shows what the survey collected as tiles: stars, forks, contributors, when it was last pushed, its size.
"SECURITY ? not read" means the step that reads it has not run. It does not mean "no security problems". The age
in the header ("surveyed 27d ago") tells you how old these numbers are; **Re-run…** on the survey refreshes them.

## 5c. The file system

A file system has one survey card, "FOLDER SURVEY", the same on every stage (part 4). On `loading_bay`, Egeria
leads the survey: the card says "led by Egeria's Folder Survey". That means Egeria reads the folder, and Egeria
keeps the result. So this section writes to Egeria, and it needs one step first.

1. **Open your file system, click 1 Scouting, then Survey & analyses, and read the card's first line.**

   It reads "◔ expected reachable by Egeria · under /deployments/loading-bay · unverified". This is the canonical
   mount from part 3: the path where Egeria sees the folder. "unverified" means Egeria has not read it yet.

   Hover over a button to read what it does before pressing it. **Survey** says: "Runs the folder survey (Egeria's
   Folder Survey leads when Egeria can read the path). Walks every file; stores the walk here. Publishing is a
   separate press."

2. **Click 6 Curate, find PUBLISH TO EGERIA, and click Publish.**

   Egeria's survey works on a folder that Egeria already knows about. A new file system is not in Egeria yet, so
   give it the folder first. Under "PUBLISH TO EGERIA", the line reads "○ root folder not attached". The button is
   **Publish**, and beside it: "attaches the root folder only". It sends the folder and nothing else: no tags, notes
   or ratings.

   Click **Publish** once.

   ![loading_bay in Egeria: the "PUBLISH TO EGERIA" band on the Curate stage, reading "✓ root folder attached · read back 17:27 · asset e65b2af1-cf7e-4d0e-9cf7-bbdd32b60465 copy", the "Publish" button with "attaches the root folder only", and under "Egeria reports" one row, "Survey Report for e65b2af1-…", taken after step 3.](images/05-scouting/S20a-root-folder-attached.webp)

   *"✓ root folder attached". The report under it appears after step 3.*

   The line changes to "✓ root folder attached · read back" and a time. "read back" means Resource Explorer asked
   Egeria afterwards and Egeria has it.

   If you skip this step, **Survey** in step 3 does nothing that you can see (gap G29).

3. **Go back to 1 Scouting › Survey & analyses, and click Survey once.**

   Egeria runs its Folder Survey; on the Quickstart it took a few seconds. Reload the page to see the result.

   ![loading_bay surveyed: the FOLDER SURVEY card reads "✓ Egeria can read /deployments/loading-bay · verified 2026-11-02 · led by Egeria's Folder Survey", with Check again and Survey, then "Measure beside Egeria's survey". The FILES line reads "136 files · 57.3 MB · 10 types · surveyed by Egeria's Folder Survey · 2026-11-02". Below are the closed sections with one line each: "Walk measures · not measured yet", narrative files "not read yet", data-file profiles "not profiled yet", personal or sensitive data "not matched yet", and "DERIVED FROM THE WALK · 5 steps · 5 not yet run".](images/05-scouting/S20-folder-survey.webp)

   *"✓ Egeria can read /deployments/loading-bay": the folder survey's first look.*

4. **Read the summary lines.**

   - The first line is now "✓ Egeria can read /deployments/loading-bay · verified 2026-11-02".
   - "FILES · 136 files · 57.3 MB · 10 types · surveyed by Egeria's Folder Survey".
   - Each longer section has one line beside its heading, so you can read the card without opening anything:
     "Walk measures · not measured yet", narrative files "not read yet", data-file profiles "not profiled yet".
     These are ○ in words: nobody has looked yet. Later parts run them.

5. **Open FILES.**

   ![The FILES section open: "surveyed by Egeria's Folder Survey · 2026-11-02", "published · report 7b0d50a2", "analysis level: top level only", and a note that Egeria writes a file-name CSV that Resource Explorer never reads. Then Files 136, Folders 18, Total size 57.3 MB, File types 10, Unclassified 12, Last modified 2026-10-28, Inaccessible 0, and lists of FILE TYPES (Markdown File 57, CSV File 36, Microsoft Excel Spreadsheet File 12 …) and FILE EXTENSIONS.](images/05-scouting/S20b-files-open.webp)

   *What Egeria's Folder Survey counted.*

   It lists what Egeria counted: 136 files in 18 folders, the last modified on 2026-10-28, none inaccessible, and the
   file types: 36 CSV files, 12 Excel files, 57 Markdown files and more. "published · report 7b0d50a2" is the survey
   report Egeria stored.

   "analysis level: top level only" means this survey counts; it does not list each file. The hospitals' weekly
   files, and a week a hospital sent again, appear when a walk lists the files, in a later part.

   The folder also holds the `Sustainability Files` folder and NAICS reference data. Those belong to the
   Sustainability project and its own tutorials; this tutorial reads the `sample-data` hospital folders.

The card has more buttons than these, and each appears only when it has something to do: **Check again**,
**Measure beside Egeria's survey**, **Profile data files**, **Read narrative files**, **Derive**, **Match data
classes** and the publish buttons for each. Later parts use some of them.

Two things on the screen can mislead:

- Under "EGERIA'S OWN SURVEYS", the row "Survey Folder" also turns ✓, with **Run in Egeria →**. It is the same run
  as the card's **Survey**, shown twice. Do not press it as well; it would survey the folder again.
- The file system's header still reads "never surveyed · not published to Egeria" after these steps (gap G11).

**What can go wrong.**

- **Survey does nothing.** The root folder is not attached. Do step 2, then press **Survey** again (gap G29).
- **"Egeria cannot read …"** on the first line. The canonical mount is not a path Egeria can see. Check it against
  Getting Started; part 3b, step 3. (Not shown here.)

## What you have now

A first look at all three sources. `coco_pharma` is alive, and you know its size within what your account can
see. OpenLineage has its numbers. `loading_bay` has its file counts, and its root folder and one survey report are
in Egeria. Several questions still read ○: nobody has looked yet.

**Next:** [Part 6 — Discovery, Assessment and Analysis: go further down the
funnel](06-further-down.md). Peter asks whether `coco_pharma` is already in the catalog.
