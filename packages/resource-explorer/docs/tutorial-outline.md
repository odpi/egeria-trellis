# Resource Explorer tutorial — outline for review

**Status:** outline only, for the project owner's review. No prose and no screenshots are written yet.
**Date:** 2026-10-29.
**Replaces, once written:** the six walks in [`tutorial/`](tutorial/README.md), which stay on `main` until then.

## How to read this outline

- Each part has a goal, what the reader needs first, numbered steps, what the reader sees after each step, and what
  can go wrong.
- Screen words are in quotation marks. Each set of screen words comes from one of four places, marked after it:
  - **(seen)**: read on the running app on 2026-10-28 or 2026-10-29, by the user guide or the merged walks;
  - **(code)**: read in the app's source on 2026-10-29, not seen on a screen. This writer's browser was not signed in.
    The prose writer must check these on the screen;
  - **(definitions)**: read in the survey definitions under `docs/dr-egeria/` on 2026-10-29. The name the screen
    shows must be checked;
  - **(not yet confirmed)**: from the user guide, which says it could not open that screen either.
- Section 8 lists the owner's decisions: the rulings given so far and the questions still open.
- Section 9 plans the video series made from the same material.

**This is the Core tutorial.** It is one of three (owner ruling, 2026-10-29):

- **Getting Started:** install and configure Trellis and Resource Explorer, with the Egeria Quickstart. Not written yet.
- **Core (this one):** use Resource Explorer on data sources, from sign-in to publishing.
- **Advanced:** Automate, adding your own analyses and other extensions, Admin, and more. Not written yet.

---

## 1. The reader and the promise

**Who this is for.** Technical professionals who work with data (owner, 2026-10-29): data scientists (including AI),
data engineers, curators, security specialists, DevOps engineers, architects and developers. The tutorial assumes they
know databases, files and data catalogs, and have never used Resource Explorer or Egeria. The worked example follows a
database, so the prose speaks most directly to a data engineer or curator, with short asides for the others where a
stage matters to them (for example security specialists at Assessment, architects at Analysis).

**What you need first: the Egeria Quickstart.** The Egeria Quickstart is a demonstration environment. It is set up
around Coco Pharmaceuticals, a fictitious company. The prose says this at the very top, in plain words:

- The Quickstart's users, such as Peter Profile and Erin Overview, are made-up people at Coco Pharmaceuticals. None of
  them is a real person.
- Nothing in it is permanent. You may change, publish and delete freely while you learn. Nothing you do touches real
  data.
- Every screenshot was taken on the Quickstart, so your screens should match closely.
- There are two ways to reach it:
  - **The hosted demo** at egeria.pdr-associates.com: register free, no install. Its data is reset every day, so work
    from one day is gone the next. It is sometimes a little behind the latest code, so a screen may differ slightly
    from a screenshot.
  - **Your own Egeria Workspaces** from the egeria-workspaces repository. Resource Explorer (part of Trellis) is
    installed and configured in a separate step today; the Getting Started tutorial covers both.

**What you can do at the end.**

1. Say what Resource Explorer is for and when to use it.
2. Sign in and find your way around the screen.
3. Add a database, a file system and a code repository, and find each one again.
4. Explain the path a data source takes through the stages: cheap, quick questions first, then costlier ones only
   where they are worth it.
5. Run a stage's overview survey, or a narrower one, and read the answers. Tell "nothing was found" from "nobody has
   looked yet".
6. Add what only you know, such as an owner and how sensitive the data is.
7. Ask a question in plain words, and check which stored results the answer came from.
8. Decide what to send to the Egeria catalog, send it, and check that it arrived.
9. Compare several data sources side by side in a work list.
10. Start an investigation, and say how it differs from a work list.

**How long it takes.** About two and a half hours in total, in parts of 10 to 25 minutes. Each part ends at a natural
place to stop. A sign-in lasts about an hour, so you will be asked to sign in again at least once. Part 2 says so
before it happens.

**What the tutorial does not cover.** Installing and configuring (Getting Started). Automate, adding your own
analyses, questions or survey definitions, and Admin (Advanced). The command line. Part 13 points to each.

---

## 2. The parts, in order, and why this order

Every part needs the Quickstart running and needs you signed in, except parts 1 and 13.

| # | Part | Needs data already there | Writes something |
|---|---|---|---|
| 1 | What Resource Explorer is for | no | no |
| 2 | Sign in and find your way around | no | no |
| 3 | Add data sources, and find them again | `coco_pharma`, `loading_bay`, and the OpenLineage repository | yes, in Resource Explorer only |
| 4 | The stages: a funnel from cheap to costly | no | no |
| 5 | Scouting: run the overview survey and read the answers | your database from part 3 | yes, stored results |
| 6 | Discovery, Assessment and Analysis: go further down the funnel | part 5 | yes, stored results |
| 7 | Enrichment: add what only you know | a data source | yes, in Resource Explorer only |
| 8 | Ask in chat, and check the sources | stored results (parts 5, 6) | yes, a stored chat turn |
| 9 | Curate: decide what goes to the catalog | a surveyed data source | yes, in Resource Explorer only |
| 10 | Publish to Egeria, and check that it arrived | part 9 | **yes, in Egeria** |
| 11 | Work lists: compare several data sources | three or more of one kind | yes, in Resource Explorer only |
| 12 | Investigations, and how they relate to work lists | a work list (part 11) | yes, in Resource Explorer |
| 13 | Where to go next | no | no |

**Why this order.**

- **Purpose before buttons (1).** You need to know what problem the tool solves before any screen means anything.
- **Data sources first, investigations later (3, then 12).** Nothing in parts 3 to 10 needs an investigation. You can
  add, survey, curate and publish a data source without one. Leaving investigations until you have something to
  organize keeps the early parts short. It also removes a choice ("Where it lives") that means nothing yet.
- **The funnel before any survey (4 before 5).** The stages are the spine of the screen. They are ordered so the
  cheapest questions come first and costlier ones only where they are worth it. Once you know that, choosing which
  survey to run makes sense.
- **One data source end to end, the others briefly (3 to 10).** You follow one database through every stage. A
  database is the kind of source this reader knows best. A repository and a file system are added in part 3. Later
  parts note briefly how they differ. Part 9 shows all three Curate pages.
- **Read before you change (5 to 7 before 9 and 10).** You learn what was measured before deciding what to publish.
- **Chat after the stages (8 after 5 and 6).** A chat answer is built from stored results. Those exist only after a
  survey has run.
- **Curate, then Publish (9, then 10).** Every Curate choice saves in Resource Explorer only. Egeria changes only when
  you press Publish (seen). Two parts make that rule hard to miss.
- **Work lists, then investigations (11, then 12).** A work list is a bench for comparing several data sources. An
  investigation records why you are looking, and which data sources it covers. A work list leads naturally to an
  investigation: one button is "Start an investigation from this list…" (code). So part 11 hands you to part 12, and
  part 12 can explain the difference with both in front of you.
- **Where next last (13).**

**The setup.** The Egeria Quickstart environment (its fictitious company is Coco Pharmaceuticals), with Resource
Explorer connected to it, a Quickstart user id and password, and the public OpenLineage repository on GitHub.
**Decided (D2, D2a):** the tutorial names its sample, so every reader sees the same screens: the Quickstart's
`coco_pharma` database (the main example), its `loading_bay` folder, and the OpenLineage repository.

---

## 3. The parts

### Part 1 — What Resource Explorer is for

**Goal.** Know what Resource Explorer does, how it relates to Egeria, and when to use it.

**Before you start.** Nothing.

**Content (no steps; two pages, one diagram).** Drawn from the blog series *Egeria Resource Explorer*, part 1,
"Effective Exploration and the Survey Funnel" (pdr-associates.com, 2026-09-18), with the owner's review of
2026-10-29. Use it for substance, written down to the tutorial's reader.

1. **One situation, among many.** A department has eighty databases nobody has written down, a few hundred internal
   repositories, and file shares that have grown for a decade. None of it is in a catalog. Someone is asked which of
   it matters for a new project (an AI project, a migration, a report), and they have a week. *Say plainly that this is
   one example:* others include checking a source the catalog already holds, judging an open-source project before
   adopting it, or simply finding out what is on a file share.
2. **Why a catalog alone does not solve it.** Catalogs are built for a purpose and then the organization moves on: new
   priorities, new platforms, acquisitions. Putting everything into the catalog "just in case" fills it with noise and
   stale entries, and people stop trusting it.
3. **The core idea: looking is separate from cataloging.** To survey a data source, Egeria records only its entry
   point: the database server, the root folder of a file system, the repository's address. It does not have to keep
   the details or the findings. So the catalog knows what has been looked at. If that is all it knows, that is itself
   a sign that more was not useful at the time. You catalog the details only when they prove worth keeping.
4. **What Resource Explorer does, in three things.**
   - You can look at a data source without cataloging its details first.
   - Every answer cites the measurement it came from, so you can check it rather than trust it.
   - What you decide, and why, is recorded, and goes into Egeria when you decide something is worth keeping.
5. **Your questions decide what runs; the funnel keeps it efficient.** You choose which surveys and analyses to run by
   the questions you want answered. The funnel (part 4) orders them from cheap and broad to slow and deep, so you can
   set most candidates aside early. It is a guide to working efficiently, not the only path: you may go straight to a
   later stage's question when that is the one you need answered.
6. **Many points of view.** The same data source matters differently to each of the people in item 8. On the screen,
   each question is tagged with the *perspectives* that care about it, such as Security, Architecture or Data Owner,
   and you can filter by them (part 3d). A perspective is a filter on the questions, not a job title; one person may
   use several. The questions themselves are not fixed: questions, analyses and surveys can be added or removed (the
   Advanced tutorial).
7. **What Resource Explorer is for, and how it fits with Egeria.** Egeria is an open-source metadata catalog: the
   shared record. Resource Explorer is a tool for people to explore the frontier of what Egeria does not yet know
   about, and to update Egeria with the things of interest. It can also explore, as things change, what Egeria already
   knows about. Either way it is guided by your needs and curiosity, it records what you learn, and it lets you store
   and share that knowledge. While you explore, Resource Explorer keeps its working results in its own store; that
   store is not a second catalog and can be rebuilt by running the surveys again. Egeria holds what you publish, and
   Resource Explorer reads from and writes to Egeria always as you.
8. **Who uses it**, each with the question they come with (the owner's list, 2026-10-29; the questions are the
   writer's):
   - *Data scientists, including AI builders:* Which data can I rely on, and what is in it?
   - *Data engineers:* What is this source, how big is it, how is it built, and what depends on it?
   - *Curators:* What is worth cataloging, and is it described well enough to be found and reused?
   - *Security specialists:* Does it hold secrets or known vulnerabilities, and who can read what?
   - *DevOps engineers:* Is it maintained, how is it deployed, and how fast is it changing?
   - *Architects:* What is it made of, and how does it fit with the rest?
   - *Developers:* What interfaces and dependencies does this code have, and is it healthy?
9. **Ask in plain words.** Beside every stage is a chat panel. Ask a question nobody planned for, or a follow-up to an
   answer, in your own words. The answer is built from what the surveys have stored, names the results it used, and,
   when nothing stored can answer, says which survey would (part 8).
10. **Three promises the screen keeps**, one sentence each:
    - nothing reaches Egeria until you press Publish;
    - "nothing found" and "not looked at yet" are always shown differently;
    - every answer can show where it came from.
11. **Still experimental, and growing.** Resource Explorer is experimental. It is evolving with what its users ask
    for and are interested in, so screens and surveys change from release to release, and some things are marked as
    not built yet. Feedback shapes what comes next: say how to give it (the Feedback button on every screen).
12. **The Quickstart reminder:** this tutorial runs in a demonstration environment, the Egeria Quickstart, set up
    around a fictitious company, Coco Pharmaceuticals, with made-up people. Change anything (section 1).

**What can go wrong.** Nothing on screen. The risks are in the writing. Do not explain Egeria's architecture, the
survey engines or where surveys run; one sentence and the glossary are enough. Do not use the blog's insider words
(ODS, outbox, graduate, governance action process, microflow).

---

### Part 2 — Sign in and find your way around

**Goal.** Sign in and know what each area of the screen is for.

**Before you start.** The address of your Resource Explorer, and a Quickstart user id and password (the Getting Started
tutorial names one).

**Steps.**

1. Open the address in a browser.
   - *You see:* a panel headed "Resource Explorer" and "EXPERIMENTAL UI", with "Sign in with your Egeria user id to
     continue.", an "Egeria user id" box, a "Password" box and a "Sign in" button (seen).
   - *Explain "EXPERIMENTAL UI":* it means what it says. Resource Explorer is still experimental and evolving with
     user feedback (part 1, item 11).
2. Type the user id and password, then click **Sign in**.
   - *You see:* the main screen. Your user id and **Sign out** are at the top right (seen).
   - *Explain:* there is no separate Resource Explorer account. Egeria checks your password once and Resource Explorer
     does not keep it. Everything you do from now on is recorded in Egeria under your user id.
3. Look at the strip across the top: "Investigation · 1 Scouting · 2 Discovery · 3 Assessment · 4 Analysis ·
   5 Enrichment · 6 Curate · Understanding · Automate · ▦ Work lists" (seen).
   - *Explain in one line:* 1 to 6 are the stages a data source goes through, left to right (part 4). Work lists and
     Investigation come in parts 11 and 12. Understanding and Automate are for the Advanced tutorial.
4. Look at the left sidebar: **Repos**, **DBs** and **FS** (repositories, databases, file systems) and a filter box
   (seen). Point out that the **INVESTIGATION** picker and **Work lists** can be ignored until parts 11 and 12.
5. Look at the right-hand rail: "ASK scoped to <data source>", a box, **Ask** and **what would answer this?** (seen).
   Part 8 uses it.
6. Look at the header: **Activity** (a log of everything that has run), your user id, **Sign out** (seen).
7. Click **Activity**, read the latest entries, and close it.
   - *You see:* "the most recent 300 entries, latest first", a filter box and **all / ok / error / running** (seen,
     user guide).

**What can go wrong.**

- Wrong password: the panel stays, with a message. (Exact words: not yet confirmed.)
- Egeria is not running: the sign-in fails, because Egeria is what checks the password. (Exact words: not yet
  confirmed.)
- After about an hour: a "Signed out" banner with a **Sign in** button (seen, user guide). Sign in again and carry on;
  nothing you saved is lost.
- On the hosted demo, the data is reset every day. If you stop and come back the next day, the data sources you added
  are gone. Each part says what it needs, so you can add them again; or do parts 3 to 10 in one sitting.
- The header can read "not signed in" while you are signed in, when no investigation is chosen (gap G9). Until
  part 12 no investigation is chosen, so the reader will see this. Tell them to ignore it until the fix lands.

---

### Part 3 — Add data sources, and find them again

**Goal.** Register a database, a file system and a repository, and find each one in the sidebar.

**Before you start.** Part 2. The Quickstart database's host, port, name, and a read-only user name and password. The
Quickstart folder's path (`loading_bay`). The OpenLineage repository's GitHub address. The Getting Started tutorial lists all of these.

**3a. A database.**

1. In the sidebar, click **DBs**, then **＋ Find databases** at the foot of the list (code).
   - *You see:* a "Find databases" dialog: "Run a saved source, or discover on a server once. Nothing is registered
     until you confirm.", with tabs "Saved sources", "Discover on a server" and "From a file" (code).
   - *Explain:* there are several ways to find databases. This tutorial registers one by hand, the simplest.
2. Click **Register one database…** (code).
   - *You see:* "Register one database" and "Test the connection first; Register is available once the test has passed
     for what is typed here. Nothing is sent to Egeria." (code).
3. Fill in **Slug**, **Display name**, **Host**, **Port**, **Database**, and under "Credential", **Username** and
   **Password** (code).
   - *Explain "Slug":* a short name with no spaces that Resource Explorer uses in addresses, for example
     `coco_pharma`. The screen uses the word, so the tutorial must explain it (gap G6).
   - *Explain:* use a read-only account. The first stages read only the database's own catalog: tables, columns, keys,
     comments and row estimates. Resource Explorer asks for more only when a later step needs to read rows, and says
     which step and why (seen, user guide). The password is stored encrypted and never shown again.
   - Leave "Egeria connection (optional)" closed (code). See gap G7.
4. Click **Test connection**.
   - *You see:* the button reads "Testing…", then a result beside it (code).
5. Click **Register**.
   - *You see:* "It is in the database list now. Nothing was sent to Egeria." and **Register another** (code).
6. Close the dialog. Type the display name into the **DBs** filter box and click the row.
   - *You see:* the database's page, and "never surveyed". Nothing has been measured yet; part 5 starts that.

*What can go wrong (database).*

- The test fails: "Connection failed" or the database's own message, and **Register** stays disabled (code).
- Signed out meanwhile: "Sign in to test a connection." (code).

**3b. A file system.**

1. Click **FS**, then **＋ Add a file system** (code).
   - *You see:* "Add a file system" and "Registering stores it here. Nothing is sent to Egeria." (code).
2. Fill in **Slug**, **Display name** and **Mount (local path)** (code).
   - *You see:* beside the path, "◔ checking", then "✓ readable here", or a warning (code).
3. Leave **Canonical mount (blank = the mount)** empty (code). See gap G5.
4. Click **Register**, then find the file system in the **FS** list.

*What can go wrong (file system).*

- "⚠ this server can't read this path · you can still register" or "⚠ this is a file, not a folder · you can still
  register" (code). Explain: registering still works, but no survey can read the folder until the path is right.
- "✕ already registered": that short name is taken (code).

**3c. A repository.**

1. Click **Repos**, then **＋ Find repos** (seen).
   - *You see:* two ways in, a search and "From a list" (seen, user guide).
2. Choose **From a list** and paste the OpenLineage repository's GitHub address.
3. Tick the row and click **Import Selected**.
   - *You see:* the button reads "Importing…", the row shows its own state, and the repository appears in the sidebar
     (seen, user guide). Nothing is registered until you click Import Selected.
   - *Explain:* a new repository reads "not yet surveyed", not zero. Its numbers arrive with its first survey.

**3d. Finding things again.**

1. Use the filter box under **Repos**, **DBs** or **FS**.
2. For repositories, the chips **All**, **New**, **Surveyed** and **Published** narrow the list (seen). ("In scope"
   belongs to part 12.)
3. The **PERSPECTIVE** chips (Data Owner, Steward and others) narrow the questions to who is asking (seen). Leave them
   all off for this tutorial.

---

### Part 4 — The stages: a funnel from cheap to costly

**Goal.** Understand how Resource Explorer is laid out: stages that ask cheap, basic questions first and costlier
ones later, and inside each stage, questions and the surveys that answer them.

**Before you start.** Part 3.

**Content (a short explanation, one diagram, then three steps).**

1. **The funnel.** Surveying a data source costs time and load on the source. A quick count of tables costs seconds; a
   profile of every column's values can take minutes and reads rows. So Resource Explorer arranges its questions in
   stages, from least costly to most:
   - **Scouting:** a fast first look at many data sources. What is it, how big, is it alive?
   - **Discovery:** what the first look already tells us, worked out from what Scouting collected, mostly without
     reading the source again. Is it worth a closer look? Is it already in the catalog?
   - **Assessment:** a closer look that judges the source against criteria, such as security, documentation and
     quality. Slower.
   - **Analysis:** its structure and numbers in detail, such as dependencies and column profiles. Can take minutes.
   - **Enrichment:** what only a person knows: owner, sensitivity, intended use.
   - **Curate:** choose what to publish to Egeria, then publish.
   - *Explain:* you go down the funnel only as far as you need. Many data sources stop at Scouting or Discovery. Only
     the ones worth it get Assessment and Analysis.
   - *Why it saves so much (from blog part 1):* checks differ in cost enormously; later checks build on what earlier
     ones stored; and the saving multiplies, because the expensive stages run only on the data sources that survive
     the cheap ones. One measured example, optional in the prose: on a sample database, Scouting took about 13
     seconds, every Discovery question then answered in under a second from what Scouting stored, and the first stage
     that read rows took about as long as Scouting.
   - *Screening out is not throwing away:* a data source set aside today can be looked at again later.
   - *A guide, not a rule:* the funnel is the efficient order. You may run any stage's survey or analysis directly when
     its question is the one you need answered (part 1, item 5).
   - *Around the funnel, not in it:* Investigation (why you are looking, part 12), Understanding (a view over what has
     been measured so far) and Automate (re-running on a schedule). The last two are for the Advanced tutorial.
2. **Inside a stage.** Every stage has the same tabs: **Questions**, **Survey & analyses**, **By analysis**,
   **Disposition** (seen).
   - **Questions** lists what this stage asks about this data source, and the answers so far.
   - **Survey & analyses** lists the surveys that answer them. A survey is a set of analyses run together. Each
     analysis can also be run on its own.
   - **By analysis** and **Disposition**: one line each, or left out (owner to confirm).
3. **Overview surveys and narrower ones.** Each of the first four stages has an overview survey that runs all the
   stage's analyses for that kind of data source. It is the good place to start, because it shows what the stage can
   tell you. Narrower surveys run a subset, when you want only some answers or want to keep the cost down. For a
   repository's Scouting, for example (definitions):
   - the overview: **Scouting Survey**, nine steps (homepage, language, file inventory, file structure, file
     classification, git statistics, health, manifests, data files);
   - narrower: **Repo Scouting Scan**, four steps (git statistics, health, homepage, language), and **Coarse Profile
     Survey** (seen).
   - The overview surveys by kind are in the table below. Gap G23 lists where they are missing or named unevenly.

**Steps.**

1. Click a data source in the sidebar that the Quickstart has already surveyed (named by Getting Started), then
   **1 Scouting**. Your own new database has nothing to show yet.
2. Click **Survey & analyses** and find the overview survey, then a narrower one.
3. Click **Questions** and read the count line, for example "6 of 6 answered · Scouting", and the key beneath it
   (seen). Read the marks, taught once, as one table:
   - ✓ answered · ∅ nothing found · ◐ partly · ○ not run · ◌ no way to answer yet · ? could not tell · ⚠ needs you ·
     ◔ running · ✕ failed (seen, user guide).
   - *Explain:* ∅ means it was measured and there was nothing. ○ means nobody has run it. They are never the same.

**The overview survey for each stage, by kind** (definitions; screen names to confirm):

| Stage | Repository | Database | File system |
|---|---|---|---|
| Scouting | Scouting Survey (9 steps) | Database Scouting Scan (3 steps) | Folder Survey (seen; how its seven buttons relate is gap G4) |
| Discovery | Repo Discovery Survey (9 steps) | **none found** (gap G23) | to confirm |
| Assessment | Assessment Survey (11 steps) | Database Assessment Survey (1 step) | to confirm |
| Analysis | Analysis Survey | Database Analysis Survey (8 steps); also Database Quality Survey | to confirm |

**What can go wrong.** Slow panes: "5 of 19 answered · 13 still loading" can stay for many seconds without saying what
it waits for (seen, gap G10). Wait until "still loading" goes.

---

### Part 5 — Scouting: run the overview survey and read the answers

**Goal.** Run the Scouting overview survey on your database and read what it found.

**Before you start.** Parts 3 and 4.

**Steps.**

1. Open your database, click **1 Scouting**, then **Survey & analyses**.
   - *You see:* "ANALYSES RUN", "NEED YOU", "LAST SURVEY" (empty for a new database), then "SURVEY DEFINITIONS" and the
     analyses, each with **Run →** (seen on a repository).
2. Find the overview survey (Database Scouting Scan, definitions) and click its run button.
   - *You see:* "◔ running", an entry in **Activity**, and a short notice with **View** when it finishes (seen, user
     guide).
3. Click **Questions**.
   - *You see:* the count rise as answers arrive.
4. Open one question row. Point at **why is this here**, the answer, the source line, **re-run**, **the numbers
   behind this ›** and **ask in chat ›** (seen).
5. Click **the numbers behind this ›** and read them.
6. Find a question this survey did not answer, and read its mark (○ or ◌).

**What can go wrong.**

- The account cannot see everything. Answers then say "measured within what this account can see", and counts read
  "3 of 26", not "3" (seen, user guide).
- A step needs another step first. If it is cheap, it runs and the row says so afterwards. If it is costly, the row
  asks first, with **Run it** or "not now — nothing has run" (not yet confirmed). The prose explains this as the
  funnel at work, without step names (section 5).
- A step needs to read rows and the account cannot. The row offers three choices: run what this account can answer,
  pick a broader account, or ask the database owner (seen, user guide).
- The run fails: "✕" with the reason, and an Activity entry marked error.
- A ✓ beside an answer that says nothing could be checked, for example "not checked: 0 of 65 declared dependenc(ies)
  could be queried" (seen, on a repository). Read the words, not only the mark (gap G13).

*Repository and file system (short notes).* Run the repository's Scouting Survey. Its header then shows numbers such as
stars, forks, contributors and size. "? not read" means a step has not run, not zero (seen). For the file system, run
its Folder Survey. The page then shows whether Egeria can read the folder ("✓ Egeria can read <path>"), counts, the
deepest folders, and files that describe their folder (seen).

---

### Part 6 — Discovery, Assessment and Analysis: go further down the funnel

**Goal.** Run the next stages' overview surveys where they are worth it, and tell a real answer from a gap.

**Before you start.** Part 5.

**Steps.**

1. Click **2 Discovery** and read the questions. "Has this resource already been cataloged in Egeria, and when?",
   "Is there any existing use within our organization?" and "Any known feedback?" (seen).
   - *You see:* where nothing is recorded, "Nothing is recorded for this — a real absence, not an unrun analysis."
     (seen).
   - *Explain:* many Discovery answers come from what Scouting already collected, so there may be nothing to run.
     A database has no Discovery overview survey (gap G23).
2. Decide: is this database worth a closer look? For the tutorial, yes.
3. Click **3 Assessment**, then **Survey & analyses**, and run the overview survey (Database Assessment Survey,
   definitions). Read two answers on **Questions**. (Not yet confirmed on a database.)
4. Click **4 Analysis**. Before running anything, find a row marked ○ and read "Nothing has been measured for this
   yet." (seen).
5. Instead of the overview, run one narrower survey or one analysis with its **run** button. Show how to keep the cost
   down.
   - *Explain:* the Database Analysis Survey has eight steps, and some read rows. You run only what you need.
6. Find a row marked ◌ and read it.
   - *Explain:* ◌ means there is no way to answer it automatically yet. It is not a failure.

**What can go wrong.** Slow panes (gap G10): a database Discovery tab took 9 to 15 seconds in the audit. A survey that
needs to read rows asks for a broader account (part 5).

---

### Part 7 — Enrichment: add what only you know

**Goal.** Record the owner, the sensitivity and the intended use of your database.

**Before you start.** Part 3.

**Steps.**

1. Click **5 Enrichment**, then the **Context** tab.
   - *You see:* "Everything a person has supplied about this resource — each row signed and dated, and each saying what
     it feeds. Nothing here is written to the Egeria until you publish it (Curate)." (seen).
2. Under "What we judge", set **Sensitivity** to "internal", **Owner** to a name, and **Intended use** to "Explore".
   - *You see:* each field signed with your user id and the date, and a line saying what it feeds, for example
     "feeds → Curate (publish record)" (seen).
3. Under "What we record", set **Environment** to "dev".
4. Under "What only you can answer", click **Answer this →** on one question and answer it.
5. Click **RFAs** in the strip.
   - *Explain:* "RFA" stands for "request for action": something a check could not settle, so a person must. Open
     one, read it, and close the drawer (gap G15).

**What can go wrong.**

- The tab reads "Reading what's been supplied…" for several seconds (seen). Wait.
- A field says "feeds → kept in Resource Explorer · no publish step reads it yet" (seen). Explain: it is saved, but
  nothing sends it to Egeria today.

---

### Part 8 — Ask in chat, and check the sources

**Goal.** Ask a question in plain words and check which stored results the answer came from.

**Before you start.** Parts 5 and 6, so there are stored results to answer from.

**Open with one idea (blog part 4, "Surveys Measure, Chat Asks").** Surveys answer the questions someone planned for.
Chat is for everything else: a follow-up, a better explanation of a result, a question nobody planned. Chat answers
only from what surveys have stored, and shows which results it used. When nothing stored can answer, it says which
survey would.

**Steps.**

1. With your database open, look at the rail: "ASK scoped to <your database>" (seen).
   - *Explain:* the question is about this data source. You do not type its name.
2. Type "How many tables have no primary key?" and click **Ask** (or press Ctrl+Enter / ⌘+Enter).
   - *You see:* "Asking…", then "Answering…", then the answer (code).
3. Read the line under the answer, for example "From 3 stored results · …" followed by the analyses it used (code;
   seen by the architect on a file system).
   - *Explain:* the answer is built only from results Resource Explorer has stored. That line names them. If it says
     "No compiled evidence on this answer · source not reported" (code), the answer did not say where it came from:
     treat it with care.
4. Click **ask in chat ›** on a question row and see the question arrive in the rail.
5. Type a question, then click **what would answer this?** instead of Ask.
   - *You see:* which analyses would answer it, which have stored results, and which have never run, with **Run** on
     those (code).
   - *Explain:* this is the funnel again. It shows which survey to run next to answer your question.
6. Click **Was this right?** and mark the answer.

**What can go wrong.**

- No data source chosen: "Select a resource first: a compile reads one resource's stored results." (code). The word
  "compile" is internal (section 5, gap G16).
- The analysis that would answer it never ran: the rail says "N not run" and offers **Run** (code).
- A long run: "Still running after five minutes: stopped watching here. The run itself has not failed." (code).

---

### Part 9 — Curate: decide what goes to the catalog

**Goal.** Choose what Resource Explorer will send to Egeria for your database, then see how the Curate page differs
for a repository and a file system.

**Before you start.** Parts 5 and 7.

**Open with one idea (blog part 3, "Curate: The Stage That Asks for a Decision").** The stages before Curate narrow
down *which data sources* are worth keeping. Curate narrows down *what inside one data source* is worth cataloging
today: perhaps three schemas of a database, not its staging tables. You can come back next week and add more without
surveying again. Curate runs no checks; it asks you for a decision.

**Steps.**

1. Click **6 Curate**.
   - *Explain first (D1):* each kind of data source has its own Curate page, because you choose from different things.
     The structure is the same: what is chosen for publishing, how to find it (tags and group), what people say
     (ratings and notes), and one Publish band at the foot.
   - *You see:* tabs including "Describe and publish" and "Contents" (seen on a database; gap G14).
2. On **Contents**, read "What gets published" and the three chips: "published by RE", "attached N schemas" and
   "cataloged by Egeria N schemas", each with ✓ or "◌ not checked since <time>" (seen).
   - *Explain:* a chip says what Resource Explorer saw the last time it asked Egeria, not what Egeria holds right now.
   - *Explain (design note D1-b, not decision D1 below):* for a database whose schemas a content pack had already
     catalogued in Egeria, "attached" and "cataloged by Egeria" count different schemas until **Link the pack's N
     schemas** on Contents links the pack's schemas to the database. For `coco_pharma` that press was made on
     2026-10-31; after it the database reads "26 members · 22 added".
3. Choose **Include** for one schema and **Leave out** for another.
   - *You see:* the choice saved at once in Resource Explorer. Nothing goes to Egeria yet (seen, user guide).
4. On **Describe and publish**, under "FINDABLE", add a tag, for example "learning".
   - *Explain:* tags are public in Egeria once published, and shared: everyone's "sales" is the same tag.
5. Choose a group, or leave "Ungrouped".
6. Under "WHAT PEOPLE SAY", give a rating and write a note.
   - *You see:* "saved · will publish" beside each (seen, user guide).
   - *Explain:* your rating and notes are published under your user id, for everyone to see. Each person has one
     rating per data source.
7. Open your repository's Curate page, then your file system's.
   - *Repository:* three tabs: "Describe and publish", "Contents" (files and folders, Include or Leave out) and
     "Structure" (its parts and dependencies) (seen, user guide).
   - *File system:* tags, group, ratings and notes, and a Publish that "attaches the root folder only" (seen).

**What can go wrong.**

- "not permitted · <reason>": Egeria's security does not let you change this element (seen, user guide).
- The sentence above the chips and a chip disagree about a count. Known defect (gap G11).
- *File system:* Curate reads "Nothing to review for file systems yet." (seen), although the survey page says "a
  proposal · confirm it on Curate". Gap G8.

---

### Part 10 — Publish to Egeria, and check that it arrived

**Goal.** Send your choices to Egeria and confirm that Egeria holds them.

**Before you start.** Part 9. This is the Quickstart, so publishing is safe: everything can be set up again (D6).

**Steps.**

1. Scroll to the foot of **Describe and publish**, to "PUBLISH TO EGERIA".
2. Read "next publish": what the press will send (seen, user guide).
   - *Explain:* one press sends the survey report, the tags and group, documentation links, and your own rating and
     notes. Never anyone else's.
3. Click **Publish**.
   - *You see:* "◔ publishing…" at once, then a result (seen, user guide).
4. Read the result line, for example "✓ in Egeria · published 10-25 · confirmed" (seen).
   - *Explain:* "confirmed" means Resource Explorer read it back from Egeria afterwards. It is not just "the button was
     pressed".
5. Click **Show in Egeria ▸** (or "Egeria reports" on a database) and read the report rows: "Survey: <name> · surveyed
   … · N annotations" (seen).
   - *Explain "annotation":* one measurement or observation, attached to the data source in Egeria.
6. Go back to **Contents** and read the chips again: "published by RE" now counts this press.
7. Check from Egeria's side: open **Egeria Explorer** from the Quickstart portal, find your database, and see the tag
   and the survey report (owner, 2026-10-29). Egeria Explorer also shows Resource Explorer's questions and
   perspectives as Egeria holds them; one sentence, as a pointer. The exact clicks in Egeria Explorer are to be read
   on the screen.
   - *Explain (design note D1-a):* a database may show as already in Egeria, matched by its endpoint, rather than
     published fresh: Resource Explorer adds to the element Egeria already holds (gap G24).
   - *Explain (design note D1-e, merged as trellis #962, 2026-10-31):* where Egeria already holds a database's
     schemas under another family, Curate's **By analysis** coverage counts them apart. Read on the screen on
     2026-11-01 (coco_pharma, Curate › By analysis): "not yet published · 26: 3 undecided · 22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's · 1 left out by a confirmed proposal".
     The counts and the family name are that screen's, not the reader's: read the words on the screen before
     writing them in.

**What can go wrong.**

- A pop-up says what failed and why, with **Retry** where retrying can help. The failed item keeps "not sent ·
  <reason>" (seen, user guide).
- "◐ behind · N annotations": Egeria holds an older report than the one Resource Explorer has kept. Publishing again
  sends the newer one (seen). Gap G18 on the words.
- "◐ partly committed · 4 queued" (seen). The screen does not say what it means anywhere a reader can find (gap G18).
- Explain once: Egeria rolls forward. Resource Explorer does not undo a publish. Removing a tag removes its link at the
  next press; a corrected note is a new note. On the Quickstart that does not matter; on a real catalog it does.

---

### Part 11 — Work lists: compare several data sources

**Goal.** Put several data sources of one kind side by side against one stage's questions, and narrow them to the
ones worth more work.

**Before you start.** Three or more data sources of one kind, scouted. Repositories are easiest: import three with
**＋ Find repos** (part 3c) and run each one's Scouting Survey, or run one survey across the list (step 5). Use
OpenLineage, Marquez and Egeria (D2c): three related open-source metadata and lineage projects, so the comparison means
something.

**Steps.**

1. In the sidebar, click **Select several…** (seen).
2. Tick OpenLineage, Marquez and Egeria, then click **save as work list…** (seen).
3. Name the list and save it.
   - *You see:* "▦ Work lists" in the strip counts one more (seen).
4. Click **▦ Work lists** and open your list.
   - *You see:* rows of data sources against one stage's questions: "Compare a set of resources as rows x questions"
     (seen).
5. Run one survey across the whole list (seen, user guide).
   - *Explain:* this is the funnel applied to many sources at once. You scout them all cheaply, then go further only
     with the best.
6. Pick a stage and read across the rows.
7. Narrow the list to the ones that matter (**promote**) (not yet confirmed).
   - *You see:* a new, shorter list.

**What can go wrong.**

- A list holds one kind only. Mixed lists are not built (seen, user guide).
- Lists whose names start with `suggested-to-` are not work lists. They hold things other people pointed you at (seen,
  user guide).
- Creating a list writes only to Resource Explorer.

---

### Part 12 — Investigations, and how they relate to work lists

**Goal.** Start an investigation from your shortlist, make it current, and know when to use an investigation and when
to use a work list.

**Before you start.** Part 11.

**First, the difference (one table, then one sentence).**

| | Work list | Investigation |
|---|---|---|
| What it answers | Which of these data sources are worth more work? | Why are we looking, and at which data sources? |
| What it holds | Data sources of one kind | A name, a description, its purposes, and a scope of any kinds |
| What it is for | Comparing side by side, then narrowing | Framing the work: it orders the questions, holds your verdicts and a journal |
| Lifetime | A bench: made, used, narrowed | Open until you close it; closing keeps its findings |

*Say:* a work list helps you choose; an investigation records what you chose and why. A work list can belong to an
investigation: the sidebar shows "WORK LISTS FOR <NAME>" and "OTHER WORK LISTS" (seen).

**Steps.**

1. On your shortlist, click **Start an investigation from this list…** (code).
   - *You see:* a "Start an investigation from this list" dialog, filled in from the list (code).
2. Give it a name, a description, and one or two purposes, for example "Explore" and "Learn".
   - *Explain:* the purposes put the most relevant questions first on every stage. You may type one that is not in the
     list; it is kept, with a small "not in the list" cue (seen, user guide).
3. Leave **Kind** at "Study" (code).
   - *Explain in one line each:* Study, Task, Campaign, Personal, Experiment (code). Personal and Experiment are private
     to you, and a private investigation cannot yet publish a database or a file system (seen, user guide).
4. For **Where it lives**, choose **Ad-hoc**: "Stays local. Nothing is written to Egeria" (code).
   - *Explain:* Ad-hoc is the simplest start, and you can promote it to Egeria later. "Create in Egeria" makes it a
     shared project in Egeria at once (D3). The dialog's default today is "Create in Egeria" (code), so the step
     tells the reader plainly to change it (D3a; gap G3).
5. Create it, then click **Make current** on its page.
   - *You see:* the button reads "Current investigation" (code), the header shows its name, and the sidebar shows
     "SCOPE · 3" (seen, user guide, for another name).
6. Go back to a stage's **Questions** tab and read "ordered by purpose · Explore, Learn · …" (seen). Questions that do
   not serve the purposes are grouped under "NOT AMONG THIS INVESTIGATION'S PURPOSES" (seen).
7. Open your database and click **add to <name>** (seen, user guide).
   - *You see:* "already in <name>'s scope". The scope now mixes kinds, which a work list cannot.
8. Ask a question in the chat rail, then click **add to investigation journal** under the answer. Read "Add this turn
   to the investigation journal?" and click **Add** (code).
   - *You see:* the entry at the foot of the investigation page: your question, the answer as shown, and the stored
     results it drew on (blog part 4).
   - *Explain:* your chat is private to you; nothing is kept in the journal unless you choose it. The entry cannot be
     edited afterwards, so readers can tell what the answer said from what a person thought. You may add your own
     note beside it.
9. Click **Investigation** in the strip to see every investigation: "An investigation is the frame a body of work
   runs inside — why it exists, not what kind of work it is." and the table INVESTIGATION, PURPOSES, STATUS, SCOPE,
   EGERIA (seen). **+ New investigation** starts one from nothing, with the same dialog.

**What can go wrong.**

- No name: the dialog shows a message and creates nothing. (Exact words: code, to confirm.)
- Experiment without a hypothesis: refused, with a message (code).
- Older rows can read "unbound by reset" in the EGERIA column. Explain: "this investigation's link to Egeria was lost
  when Egeria was set up again; it can be linked again" (gap G12).

---

### Part 13 — Where to go next

**Goal.** Know what to read or do after this tutorial.

**Content (no steps).**

- **The Advanced tutorial** (not written yet): Automate (schedules and "notify me"), adding your own analyses,
  questions, purposes and survey definitions, Admin and its settings, and other topics still to be listed. When it
  covers adding your own analyses, it says: installing the package registers it, and a report spec names the
  analysis by its registered name. No environment variable to edit and no Python path in a report spec
  (Q2(b), project owner, 2026-10-31).
- **The Getting Started tutorial** (not written yet): installing and configuring Trellis and Resource Explorer with
  the Egeria Quickstart. Installing the Analytic Library is what makes its analyses available; there is no list of
  modules to configure.
- The [user guide](user-guide.md) for every screen in detail.
- The [admin guide](admin-guide.md) for running Resource Explorer.
- The command line (user guide, section 9).
- Egeria's own documentation for the catalog itself.

---

## 4. Glossary

Plain definitions, one or two sentences each. The tutorial links each term to this list the first time it appears.

| Term | Definition |
|---|---|
| **Resource Explorer** | A web tool that looks at data sources, measures them, records what people know about them, and publishes what it learns to Egeria. |
| **Egeria** | An open-source metadata catalog: the shared record of what data an organization has. Resource Explorer reads from it and writes to it. |
| **Egeria Quickstart** | A demonstration environment of Egeria, set up around a fictitious company, Coco Pharmaceuticals, with made-up users. Nothing in it is permanent. |
| **Coco Pharmaceuticals** | The fictitious company whose data and people fill the Egeria Quickstart. |
| **Egeria Explorer** | A tool in the Quickstart portal for browsing what Egeria holds, including what you published and Resource Explorer's questions and perspectives. |
| **Catalog** | The shared record of data sources and what is known about them. Here, the catalog is Egeria. |
| **Data source (resource)** | Something that holds data: a database, a file system or a code repository. The screen says "resource"; the tutorial says "data source" and notes once that they are the same. |
| **Repository** | A code repository on GitHub. Resource Explorer reads its files and history. |
| **Database** | A PostgreSQL database. Resource Explorer reads its catalog first, and its rows only when you allow it. |
| **File system** | A folder, and everything under it, that this server or Egeria can read. |
| **Register** | Tell Resource Explorer about a data source so it can look at it. Registering writes nothing to Egeria. |
| **Slug** | The short name with no spaces that Resource Explorer gives a data source, used in addresses. You choose it when you register. |
| **Stage** | One of the six steps a data source goes through: Scouting, Discovery, Assessment, Analysis, Enrichment, Curate. |
| **The funnel** | The idea behind the stages: cheap, basic questions first, costlier ones only for the data sources worth them. |
| **Question** | One thing Resource Explorer asks about a data source, such as "Is this repository actively maintained?". Each stage has its own. |
| **Answer** | What Resource Explorer found for a question, with the source it came from. |
| **Analysis** | One check that measures something, such as counting tables or finding files no one has opened. |
| **Survey** | A set of analyses run together on one data source. |
| **Overview survey** | The survey that runs all of a stage's analyses for one kind of data source. The good first run on a stage. |
| **Survey report** | The results of one survey, as Egeria stores them. |
| **Measure** | A number an analysis produced, with its unit and the time it was taken. |
| **Annotation** | One measurement or observation attached to a data source in Egeria. A survey report holds many. |
| **"as of"** | The time a number was read. Numbers from a source that changes carry one. |
| **Nothing found / not run** | "Nothing found" means it was measured and there was nothing. "Not run" means nobody has looked. They are never the same. |
| **Enrichment** | What a person adds that no check can measure: owner, sensitivity, intended use. |
| **Request for action (RFA)** | Something a check could not settle, so a person must. The **RFAs** drawer lists them. |
| **Curate** | Decide what Resource Explorer will send to Egeria. Every choice is saved in Resource Explorer only. |
| **Include / Leave out** | The choice, per schema, table, file or folder, of whether it is published. |
| **Tag** | A public label in Egeria, such as "sales". Everyone's tag of the same name is one tag. |
| **Group** | A set of data sources that belong together, such as one product's. In Egeria a group becomes a collection. |
| **Rating and note** | Your judgment of a data source. Published under your user id; one rating per person. |
| **Publish** | Send your choices to Egeria. Only a person's press publishes, and only that person's own work. |
| **Confirmed (read back)** | Resource Explorer read the published item back from Egeria and found it there. |
| **Work list** | A set of data sources of one kind, laid out side by side against one stage's questions, to compare and narrow them. |
| **Investigation** | A record of why you are looking at a set of data sources: a name, a description and its purposes. |
| **Purpose** | What an investigation is for, such as Explore or Learn. Purposes put the most relevant questions first. |
| **Scope** | The data sources an investigation covers. It can mix databases, file systems and repositories. |
| **Current investigation** | The one you are working in now. Questions are ordered by its purposes, and data sources can be added to its scope in one click. |
| **Journal** | An investigation's running notes, such as chat answers you chose to keep. |
| **Disposition** | Your verdict on a data source within an investigation, such as tracking, using or abandoned. |
| **Perspective** | Who is asking, such as Data Owner or Steward. Choosing one narrows the questions. |
| **Activity** | The log of everything that has run or been published. |
| **Credential** | The user name and password Resource Explorer uses to read a database. Stored encrypted. |
| **Mount (local path)** | Where the folder of a file system is found on this server. |

**Terms the tutorial avoids altogether** (they stay in the user guide): Folio (say "group"), zone, governance zone,
intent (say "stage"), annotation type names (QualityAnnotation, DataClassMatch and others), GUID, Egeria Project (say
"Egeria" or "shared"), valid-value list, data class, reference data, blueprint, component partition, confidence,
Architecture Recovery, Understanding and Automate beyond one line each (both are for the Advanced tutorial), library,
routine, Prefect, compile, extent, canonical mount, survey definition (say "survey"; the screen heading "SURVEY
DEFINITIONS" is explained as "the surveys you can run").

---

## 5. What not to say

**Never in the tutorial.**

- Review and build vocabulary: gate, gate legs, leg 7, finding numbers (F1, F7, F11, F14 …), status-words rounds and
  item numbers, "the pending status-words PR".
- Commit hashes, PR numbers (#899 …), branch names, run ids, activity ids (`3ba02177` …), element GUIDs.
- "Proposal" as jargon. Say "a suggestion you can accept or reject", unless the screen word must be explained (below).
- Names of the marks ("half-moon", "dotted circle"). Show the mark and say what it means.
- "Declared measures", "declaration", "library step", "StepManifest", "entry point", "result kind", "absence
  sentence", "chat extent", "confidence form".
- Analysis ids as names: `fs_permissions`, `postgres_column_profile`, `db_catalogued`. Use what the screen calls them,
  or a plain description.
- Question ids (Q-0108) except in "Where to go next".
- "Classic", "/next", "the reset", "the throwaway", "8813" as a fact about the reader's setup.
- First names of anyone, other than the Quickstart's made-up users. "Owner decision" callouts belong in project
  records, not the tutorial.
- "The architect read…", "this walk did not press…". The tutorial is written for doing, not as a record of what a
  writer could check.

**Words the screen shows, and how to explain them.**

| Screen word | Where | Say |
|---|---|---|
| "EXPERIMENTAL UI" | sign-in panel | Accurate: Resource Explorer is still experimental and evolving with user feedback (owner, 2026-10-29). Say so once, in part 2. |
| "Slug *" | register forms | The short name with no spaces used in addresses. |
| "resource" | everywhere | Means a data source. |
| "SURVEY DEFINITIONS" | Survey & analyses | The surveys you can run for this stage. |
| "RFAs" | strip | "Requests for action": things a person must settle. |
| "ran via Prefect" | Survey & analyses | Leave unmentioned; it names the job runner. (Gap G16.) |
| "Scope: scouting" | Survey & analyses | The surveys shown are the ones for this stage. Not the same as an investigation's scope. |
| "no reader yet" (◌) | Questions | There is no way to answer this automatically yet. |
| "answered automatically" | Questions key | Answered from what is already recorded, with no new check run. (Prose writer to confirm meaning.) |
| "a proposal · confirm it on Curate" | file-system survey | A suggestion found in the files; a person decides if it is right. |
| "derived" | file-system survey | Worked out from the folder walk Resource Explorer already made. |
| "unbound by reset" | Investigations | Its link to Egeria was lost when Egeria was set up again; it can be linked again. |
| "zones: none · everyone visible" | Curate band | Anyone who can use this Egeria can see what you publish. |
| "kept survey" | Publish band | The latest survey Resource Explorer has stored, which the next press sends. |
| "◐ stale 9 of 12 steps …" | Publish band | Some results are older than their refresh rule; re-survey before publishing if you need them fresh. |
| "◐ behind" | Publish band | Egeria holds an older report than the kept one. |
| "From N stored results · …" | chat | The answer came from these stored results; the names after the dot are the analyses. |
| "Compile", "what would answer this?" | chat rail | A check of which analyses could answer your question and which have results. |
| "Ad-hoc" / "🏠 local" | new-investigation dialog / Investigations table | Two words for the same thing: kept in Resource Explorer only. (Gap G3.) |

---

## 6. Screenshots planned

None taken yet. Every screenshot is taken on the Egeria Quickstart, signed in as a Quickstart user, at 1920 × 1080, so the same image serves the written tutorial and the videos (section 9). Existing images
from the merged walks are listed where one may be reused, after a check that the screen has not changed.

| # | Part · step | Screen | Shows | Reuse |
|---|---|---|---|---|
| S1 | 1 | Diagram, not a screenshot | Data source → survey → answers → your knowledge → curate → publish → Egeria | new |
| S2 | 2 · 1 | Sign-in panel | The panel, empty boxes | `01-sign-in-panel.webp` if unchanged |
| S3 | 2 · 3–6 | Whole screen, labelled | Strip, sidebar, main panel, chat rail, header, each numbered | new (labelled version of `01-stage-strip-and-sidebar.webp`) |
| S4 | 2 · 7 | Activity log | The latest entries and the filter buttons | new |
| S5 | 3a · 1 | Find databases dialog | The three tabs and **Register one database…** | new |
| S6 | 3a · 3–4 | Register one database, filled | Fields filled, test passed, Register enabled | new |
| S7 | 3a · 5 | After Register | "It is in the database list now. Nothing was sent to Egeria." | new |
| S8 | 3a · 6 | The new database page | "never surveyed" | new |
| S9 | 3b · 2 | Add a file system | Path field with "✓ readable here" | new |
| S10 | 3c · 3 | Find repos, From a list | One row ticked, Import Selected | new |
| S11 | 3d | Sidebar filters | Filter box and chips | `01-repos-filter-chips.webp` if unchanged |
| S12 | 4 · 1 | Diagram, the funnel | Six stages, cost rising left to right, many sources narrowing to few | new |
| S13 | 4 · 2 | A stage's tabs, labelled | Questions, Survey & analyses, By analysis, Disposition | new |
| S14 | 4 · steps 2 | Survey & analyses, labelled | The overview survey and a narrower one marked | new (`02-survey-and-analyses-docling.webp` as a guide) |
| S15 | 4 · steps 3 | The marks | The key, or a drawn table | new |
| S16 | 5 · 1 | Survey & analyses on a never-surveyed database | The overview survey's run button | new |
| S17 | 5 · 2 | Running | "◔ running" and the notice with View | new |
| S18 | 5 · 3–4 | Questions after the survey, one row labelled | Answer, source line, buttons | new |
| S19 | 5 · note | Repository header tiles | Tiles incl. "? not read" | `02-scouting-questions-docling.webp` if unchanged |
| S20 | 5 · note | File-system survey | Reach line and counts | `04-folder-survey.webp` if unchanged |
| S21 | 6 · 1 | Discovery | "Nothing is recorded for this — a real absence…" | new |
| S22 | 6 · 4 | Analysis, a ○ row | "Nothing has been measured for this yet." | new on the database |
| S23 | 6 · 5 | One analysis run on its own | The row after its run | new |
| S24 | 7 · 1–2 | Context tab | Sensitivity, Owner, Intended use set, signed and dated | new |
| S25 | 7 · 5 | RFAs drawer | One open item | new |
| S26 | 8 · 2–3 | Chat answer | The answer and the "From N stored results" line | new |
| S27 | 8 · 5 | what would answer this? | Analyses with and without results, **Run** | new |
| S28 | 9 · 2 | Contents, the chips | Three chips | new on the reader's database |
| S29 | 9 · 3 | Include / Leave out | One of each | Keep both: the explainer `tutorial-media/curate-decisions.png` and this capture of the real screen (owner, 2026-10-29). Start from the blog's `curate-decision-table-v2.png`; replace it with a newer capture when one is taken. |
| S30 | 9 · 4–6 | Findable and What people say | Tag, group, rating, "saved · will publish" | new |
| S31 | 9 · 7 | Repository Curate page | Its three tabs, labelled with the shared parts | new |
| S32 | 9 · 7 | File-system Curate page | Tags, group, "attaches the root folder only" | `05-curate-file-system.webp` if unchanged |
| S33 | 10 · 2 | Publish band before | "next publish" and **Publish** | new |
| S34 | 10 · 4 | Publish band after | "✓ in Egeria · … · confirmed" | new |
| S35 | 10 · 5 | Egeria reports | Report rows | `03-curate-publish-to-egeria.webp` if unchanged |
| S36 | 10 · 7 | Egeria Explorer | The database with the tag and survey report | new |
| S37 | 11 · 2 | Select several | Three ticked, save as work list… | new |
| S38 | 11 · 4 | A work list | Rows × questions | new |
| S39 | 11 · 7 | The shortlist | After promote | new |
| S40 | 12 · 1 | Start an investigation from this list | The dialog, filled in, Ad-hoc chosen | new |
| S41 | 12 · 5 | The investigation page | "Current investigation", SCOPE · 3 | new |
| S42 | 12 · 6 | Questions ordered by purpose | "ordered by purpose · …" and "NOT AMONG …" | new |
| S43 | 12 · 9 | Investigations table | Every investigation | `01-investigation-stage.webp` if unchanged |

**Diagrams and images to reuse from the blog series** (pdr-associates.com, by the owner's leave, 2026-10-29). Most
carry words the tutorial avoids, so each needs a check, and some a redrawn label, before use.

| Use for | Blog image | Note |
|---|---|---|
| S1 (part 1) and S12 (part 4), the funnel | part 1, `resource-explorer-funnel-v5.png` | **Redrawn for the tutorial, 2026-10-29:** [`tutorial-media/funnel.svg`](tutorial-media/funnel.svg), rendered at 1920 × 1080 as `funnel.png`. Same palette; plain words (no "graduate" or "outbox"); stages described for every kind of data source; chat added; the funnel shown as a guide, not a rule. For the owner's review. |
| Part 1, the screen in use | part 1, `Portal-6.1-overview_2.png` (Assessment question cards) | Shows questions tagged by perspective, each answer naming its analysis. |
| Not for the Core tutorial: what a question is | part 2, `anatomy-of-a-question.png` | Advanced tutorial (owner, 2026-10-29). |
| Part 9, Include / Leave out | part 3, `curate-decision-table-v2.png` | A database's schemas with a decision each. Needed in the Core tutorial (owner, 2026-10-29). **Redrawn as an explainer, 2026-10-29:** [`tutorial-media/curate-decisions.svg`](tutorial-media/curate-decisions.svg), rendered as `curate-decisions.png`: five rows (included and cataloged, left out, a suggestion, undecided and not known) with numbered callouts. It explains the idea; S29 shows the real screen, and both are kept (owner, 2026-10-29). The schema names match `coco_pharma` in the Quickstart (owner, 2026-10-29). |
| Part 10, before Publish | part 3, `curate-commit-summary.png` | "What this commit does". Check it is still the screen; "commit" needs explaining or avoiding. |
| Part 10 · 7, checking in Egeria | part 3, `question-in-egeria-1600.jpg` (a question viewed in Egeria Explorer); Workspaces 6.1, `Portal-6.1.png` (the Quickstart portal home) | Egeria Explorer is the place to look (owner, 2026-10-29); the portal home shows where to open it. |
| Part 8, an answer and its sources | part 4, `p4-fig2a-docling-answer.png` | The answer with its stored results listed above it. |
| Part 8 · 5, what would answer this | part 4, `p4-fig2b-docling-gaps-v3.png` | Analyses with no stored result, each with Run. |
| Part 8, how questions improve | part 4, `part4-fig3-question-loop-v2.png` | The surveys-and-chat loop; a good closing picture for part 8. **Redrawn for the tutorial, 2026-10-29:** [`tutorial-media/question-loop.svg`](tutorial-media/question-loop.svg), rendered at 1920 × 1080 as `question-loop.png`. Same palette as the funnel; no "compile" or "routed"; the short path is tied to part 8 (Run in chat) and the long path to the Advanced tutorial. The question log is not on screen yet (on the backlog, owner, 2026-10-29), so its box says "Planned: not on screen yet". For the owner's review. |
| Part 12 · 8, the journal entry | part 4, `p4-fig2c-docling-journal.png` | Shows the entry's provenance. |
| Not for the Core tutorial | part 4, `part4-fig1-chat-turn-v2.png` and `part4-fig4-compile-budget.png` | Internal mechanics (compile id, manifest, budget). Advanced, if anywhere. |

---

## 7. Gaps and screen defects

For the coordinator. Places where the app makes a step hard to explain, or shows confusing words. None is worked around
in this outline; each is named where it bites.

| # | Gap or defect | Where it bites | What would fix it, or what the writer needs |
|---|---|---|---|
| G1 | No named starter setup. The merged walks used whatever was loaded on the developer's server. | all parts | **Settled by D2 and D6:** the Egeria Quickstart, set up by Getting Started. Named (D2a): `coco_pharma`, `loading_bay`, the OpenLineage repository. |
| G2 | The merged walks pressed nothing that writes, so every step that runs, saves or publishes was described, not shown. That is the main reason they "skip steps". | 3, 5–12 | **Settled by D6:** the Quickstart is throwaway, so the prose writer presses for real. |
| G3 | "Where it lives" defaults to "Create in Egeria". "Ad-hoc" (dialog) and "🏠 local" (table) are two words for one thing. | 12 | Choose one word. The tutorial tells the reader to change the default (D3a); changing the app's default is still worth considering. |
| G4 | A file system's survey page offers seven start buttons (Survey, Measure beside Egeria's survey, Publish these measures to Egeria, Profile data files, Derive, Read narrative files, Match data classes) and no overview survey is marked. A repository's and a database's overview survey is not marked either. | 4, 5 | Mark each stage's overview survey on the screen, and give the file-system buttons an order. |
| G5 | File-system registration says "Mount (local path)" and "Canonical mount (blank = the mount)". The user guide says "host and container path". The survey then reports the path Egeria reads ("/deployments/loading-bay"). A reader cannot tell which path to type. | 3b | One explanation of the paths, on the form. |
| G6 | "Slug *" is a required field on both register forms. Dashes are silently turned into underscores. | 3a, 3b | A plain label ("Short name"), or a hint under the box. |
| G7 | "Egeria connection (optional)" on the database form asks for "Egeria-visible host", "Egeria platform URL" and "View server". Most readers cannot know these. | 3a | Hide it from non-admins, or say when it is needed. |
| G8 | File-system survey says "a proposal · confirm it on Curate"; Curate says "Nothing to review for file systems yet." A dead end. | 5, 9 | Build the review, or remove the pointer. |
| G9 | Header can read "not signed in" while signed in (no investigation chosen). With investigations moved to part 12, the reader sees this for most of the tutorial. | 2–11 | Already queued as a defect. More urgent now. |
| G10 | Slow panes say "still loading" for 9–15 seconds without saying what they wait for (Discovery, Analysis, Enrichment). | 4, 6, 7 | Already queued as a defect. |
| G11 | A published database's header reads "never surveyed · not published to Egeria" while Curate says it is in Egeria; the sentence above the chips and a chip can disagree. | 9 | Already queued as defects. |
| G12 | "unbound by reset" is explained only by reference to an internal event. | 12 | A word that describes the state, not its history. |
| G13 | A ✓ beside an answer that says nothing could be checked ("not checked: 0 of 65 … could be queried"). | 5 | Already queued as a defect. |
| G14 | The user guide says a database's Curate is one page; walk 3 shows tabs ("Describe and publish · Contents · Structure · …") on a database. One of them is out of date. | 9 | Read the screen and correct the guide. |
| G15 | "RFAs" is an unexplained abbreviation in the strip. | 7 | Spell it out on hover, or rename. |
| G16 | Internal words on screen: "ran via Prefect"; "compile" in the chat rail ("a compile reads one resource's stored results"); chat source lines list raw analysis ids ("fs_permissions, fs_activity"). | 5, 8 | Plain words, or analysis display names. |
| G17 | No single named place in Egeria for the reader to check that a publish arrived. | 10 | **Settled (owner, 2026-10-29):** Egeria Explorer. Still worth having: a link from Resource Explorer's "Show in Egeria" that opens Egeria Explorer on the element. |
| G18 | Publish-band words a new user cannot read: "kept survey", "◐ behind · 76 annotations", "◐ stale 9 of 12 steps are stale", "◐ partly committed · 4 queued". | 10 | Plain sentences, or a hover that explains each. |
| G19 | The Admin dialog says "the eight intents"; the word appears nowhere else on screen. | Advanced tutorial | Say "the stages". |
| G20 | The sign-in panel says "EXPERIMENTAL UI". | 2 | **Not a defect (project owner, 2026-10-29):** RE is positioned as still experimental, evolving with user feedback. The tutorial explains the label; nothing to fix. |
| G21 | This writer's browser was not signed in, so the register dialogs, the new-investigation dialog and the chat rail are outlined from the source code (marked "code"). | 3, 8, 12 | The prose writer reads each on the screen, signed in. |
| G22 | Steps whose exact screen words nobody has recorded: a wrong password, Egeria down at sign-in, an empty investigation name, "promote" on a work list, the Assessment tab on a database, a step that asks before running a prerequisite. | 2, 5, 6, 11, 12 | Read on the screen during the prose phase; some need a deliberate error to see. |
| G23 | The overview surveys are uneven across kinds (definitions, 2026-10-29). A database has no Discovery survey. A database's Assessment survey has one step. File-system stage surveys are not yet identified. Names are inconsistent: "Scouting Survey" and "Repo Scouting Scan" are both repository Scouting surveys; "Database Scouting Scan" is the database's overview; "Assessment Survey" and "Analysis Survey" carry no kind in their names, while "Repo Discovery Survey" does. A reader cannot tell from a name which survey is the overview. | 4, 5, 6 | One naming rule (for example "<Kind> <Stage> Survey" for every overview), and the missing overviews either built or explained. |
| G24 | After design note D1-a, a database can show as already in Egeria, matched by its endpoint, rather than published fresh. A reader who expects "published by RE" sees a different state. | 9, 10 | Explain it at part 10, step 7. D1-e (trellis #962) adds a coverage line, read on the screen on 2026-11-01 for coco_pharma as "not yet published · 26: 3 undecided · 22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's · 1 left out by a confirmed proposal"; those counts are that screen's, so read the words on the screen before writing them in. |

---

## 8. Decisions for the owner

| # | Decision | State |
|---|---|---|
| D1 | The main example data source | **Decided (project owner, 2026-10-29):** a database. Set the expectation that each kind's Curate page differs but the structure is the same, and show all three Curate pages (part 9, step 7). |
| D2 | The named sample setup | **Decided (project owner, 2026-10-29):** yes, the Egeria Quickstart, set up by the Getting Started tutorial. |
| D2a | Which database, folder and repository | **Decided (project owner, 2026-10-29):** the `coco_pharma` database, the `loading_bay` folder and the OpenLineage repository. |
| D3 | Ad-hoc or "Create in Egeria" for the tutorial's investigation | **Decided (project owner, 2026-10-29):** Ad-hoc, described as the simplest starting point. Investigations come late (part 12), so the choice is not met until then. |
| D3a | The dialog's default is "Create in Egeria" | **Decided (project owner, 2026-10-29):** the tutorial tells the reader to change it. |
| D4 | How the first survey is started | **Decided (project owner, 2026-10-29):** explain the funnel first (part 4); then run the stage's overview survey to see what the stage offers, and narrower surveys for subsets. Each of the first four stages has one. G23 notes where the definitions are uneven. |
| D5 | Automate in the tutorial | **Decided (project owner, 2026-10-29):** no. Automate, extensibility and Admin go to an Advanced tutorial. Installing and configuring goes to a Getting Started tutorial. This is the Core tutorial. |
| D6 | Publishing during screenshots | **Decided (project owner, 2026-10-29):** the tutorial is based on the Egeria Quickstart, a demonstration environment whose users are all made up. That is stated at the start. Everything can be thrown away, so the tutorial presses, saves and publishes for real. |
| D7 | Work lists and investigations | **Decided (project owner, 2026-10-29):** work lists get their own part (11), and the relationship to investigations is described (part 12). |
| D2c | The two extra repositories for the work list (part 11) | **Decided (project owner, 2026-10-29):** Marquez and Egeria, beside OpenLineage. |
| D2b | Hosted demo or your own Egeria Workspaces | **Settled by the owner's facts (2026-10-29):** both work. The hosted demo is reset daily and is sometimes a little behind the latest code. Your own Workspaces needs Trellis installed and configured in a separate step, which the Getting Started tutorial covers. The tutorial offers both (section 1). |
| D8 | Part 1: what Resource Explorer is for, and who it is for | **Reviewed (project owner, 2026-10-29):** the situation is one example of many; surveying catalogs only the entry point; questions decide what runs and the funnel is a guide, not the only path; questions, analyses and surveys can be changed (Advanced); RE explores the frontier of what Egeria does not know and what it does, guided by your needs, recording and sharing what you learn; chat is in part 1. Users: data scientists (including AI), data engineers, curators, security specialists, DevOps engineers, architects, developers. |
| D9 | Linking a content pack's schemas to RE's database element (design note D1-b; not D1 above) | **Decision (project owner, 2026-10-31):** "yes, database level is what I meant - go". **Done:** pressed 2026-10-31 on `coco_pharma`: 22 added, 26 members (part 9, step 2). |

---

## 9. Video series

**Decided (project owner, 2026-10-29):** the tutorial also becomes a video series, made the same way as the Egeria
Workspaces video (youtube.com/watch?v=igIVACD4b_g): mostly still images, with an AI-generated voice made in Audiate.
The written tutorial and the videos share one set of images and one source of narration, so they are made together,
not one after the other.

### Episodes

About 10 episodes of 3 to 7 minutes. Each one opens with a title slide naming what you will be able to do, and ends
with one slide pointing to the written part for "what can go wrong" and to the next episode.

| # | Episode | Tutorial parts | Images (section 6) | Blog images | About |
|---|---|---|---|---|---|
| V1 | What Resource Explorer is for | 1 | S1 | funnel; Assessment question cards | 5 min |
| V2 | Signing in and a tour of the screen | 2 | S2–S4 | — | 3 min |
| V3 | Adding a database, a file system and a repository | 3 | S5–S11 | — | 6 min |
| V4 | The stages: a funnel from cheap to costly | 4 | S12–S15 | funnel | 5 min |
| V5 | Running surveys, down the funnel | 5, 6 | S16–S23 | — | 7 min |
| V6 | Enrichment: what only you know | 7 | S24–S25 | — | 3 min |
| V7 | Asking in chat, and checking the sources | 8 | S26–S27 | answer with sources; what would answer this; the question loop | 5 min |
| V8 | Curate: deciding what goes to the catalog | 9 | S28–S32 | Curate decision table | 6 min |
| V9 | Publishing, and checking it in Egeria Explorer | 10 | S33–S36 | commit summary; question in Egeria Explorer; Quickstart portal | 5 min |
| V10 | Work lists and investigations | 11, 12 | S37–S43 | journal entry | 7 min |

### Narration

- **The written step is the source.** The prose writer writes each "Explain" line so it can be read aloud as it
  stands. The script for an episode is its parts' steps and "Explain" lines, in order, adjusted for speech.
- **Spoken English, short sentences.** One idea per sentence. No parentheses, no "e.g.", no symbols read aloud.
- **Paraphrase long screen text; let the slide show it.** Say "the screen says nothing is recorded, and that this is a
  real absence", not the whole quoted sentence. Short button names are said as they appear ("click Publish").
- **Marks are named by meaning, never by shape.** Say "the mark for nothing found", not "the empty-set symbol".
- **The section 5 rules apply in full.** No internal vocabulary, no ids, no first names except the Quickstart's
  made-up people.
- **"What can go wrong" stays in the written tutorial.** An episode mentions at most one thing, when the viewer is
  very likely to meet it (for example being signed out after an hour, in V2).

**Pronunciation, set once in Audiate and reused in every episode:** Egeria, Trellis, OpenLineage, Marquez, Coco
Pharmaceuticals, `coco_pharma` (say "coco pharma"), `loading_bay` (say "loading bay"), Peter Profile, Erin
Overview, PostgreSQL, GitHub, Quickstart. The prose writer adds any other name that the voice gets wrong.

### Images, for both uses

- **Capture once, at video size.** Take every screenshot with the browser at 1920 × 1080, so each fills a 16:9 frame.
  The written tutorial uses the same images, scaled down. (The merged walks are 1382 px wide and cannot be reused in
  the videos as they are.)
- **Capture from a local Quickstart at a known version**, not the hosted demo, which is reset daily and can lag the
  latest code. The first slide of each episode, and the written tutorial's front page, name that version.
- **Callouts and zooms are separate layers**, added in the editor, not drawn into the image. The written tutorial uses
  the labelled version; the video can reveal callouts one at a time as the narration reaches them.
- **Before and after as two images.** Anything that changes on screen (a survey running and finishing, Include
  chosen, Publish confirmed) is two stills, not a screen recording.
- **Fix before filming where it can be done.** Open defects that would be filmed into every image: G9 ("not signed
  in" in the header), G10 (slow panes), G13 (✓ beside an answer that checked nothing). If
  they are not fixed first, the narration must not draw attention to them, and the written tutorial explains them.

### Open for the video series

| # | Question | State |
|---|---|---|
| V-a | Who produces the episodes (script assembly, Audiate, editing) | **Decided (project owner, 2026-10-29):** a Claude session prepares a kit per episode (an Audiate-ready script with slide markers, the pronunciation list, the ordered slide list with callouts, the YouTube title, description and chapters, and a caption file to check); the owner voices it in Audiate and edits in Camtasia. |
| V-b | Where they are published | **Decided (project owner, 2026-10-29):** YouTube. Each written part links to its episode. |
| V-c | Whether the blog diagrams are redrawn for 16:9 | In progress: the funnel, the question loop and the Curate decisions are redrawn (section 6). The anatomy of a question goes to the Advanced tutorial. Several carry words the tutorial avoids (section 6). Editable sources exist for some, outside the repo, in the trellis checkout's `Claude outputs/` folder and its root: `anatomy-of-a-question.svg`, `part4-fig3-question-loop.html`, `part4-fig1-chat-turn.html`, `part4-fig4-compile-budget.html`, and `resource-explorer-survey-funnel.pptx` (probably the funnel). |
