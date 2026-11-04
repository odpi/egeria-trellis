# Resource Explorer tutorial — outline for review

**Status:** the outline the prose is written from. Prose is being written one part at a time in
[`tutorial/`](tutorial/README.md#the-core-tutorial); parts 1 to 6 are written.
**Date:** 2026-11-01.
**Changes:**
- 2026-10-29: the outline, with decisions D1–D9.
- 2026-10-31: Q2(b) (how added analyses are found) in part 13, and design note D1 in parts 9 and 10.
- 2026-11-01: one scenario followed through every part and screenshot (D10); Open data left to the Sustainability
  documents; drift since 10-29; the D1-e coverage sentence as read on screen.
- 2026-11-02: part 1 written. Screenshots at 1600 × 900, not 1920 × 1080 (sections 6 and 9); narration drafted with
  each part (section 9); the glossary moved to [`tutorial/glossary.md`](tutorial/glossary.md) (section 4); S1 drawn.
- 2026-11-02: part 2 written; S2–S4 taken. Read on screen: the strip's stages are joined by "›"; **Chat** carries a
  panel icon, not "Chat ×"; the PERSPECTIVE row added to the tour; the Activity panel's words; G9 not seen.
- 2026-11-02: part 4 written; S12 re-rendered at 1600 × 900, S13–S15 taken or drawn. Read on screen: the overview
  survey table (names and step counts); a database's three extra tabs; By analysis and Disposition described; a
  database's Assessment shows no survey of its own (G23). The optional measured example (13 seconds) left out: not
  read on screen.
- 2026-11-02: part 3 written; story slugs `coco_pharma_trial` and `loading_bay_trial` registered, OpenLineage's
  existing row reused (owner §3). Read on screen: the dialog words (3a–3d); the canonical mount is
  `/deployments/loading-bay` (owner and Architect); S5 and S9 not taken (G25, G26); G26–G28 added.
- 2026-11-02: part 5 written; S16, S17, S19, S20, S20a and S20b taken. **S18 and S18b are held, to be re-shot after
  F46** (the schema total counts PostgreSQL's temporary schemas; ruling by the Architect, 2026-11-02), once F46 merges
  and `coco_pharma_trial` is surveyed again. Later the same day: S18 and S18b re-shot after F46 merged (#973) and
  `coco_pharma_trial` was surveyed again; part 5 now gives the schema total, 30. Read on screen: the database's run dialog and running card; the answers
  after one Scouting survey; the folder survey needs the root folder attached first (Curate › Publish, "attaches the
  root folder only"), then Egeria leads the survey and writes a survey report. G11, G16 and G28 extended; G29 and G30
  added. Reader-facing `8813` replaced: the tutorial's address is `http://localhost:8810/` (project owner, via the
  Coordinator, 2026-11-02); walks 1–6 now say their screens were read "on a test server".
- 2026-11-03: part 6 written; S21, S21b, S22, S23a and S23 taken. Read on screen: Discovery answers "already
  catalogued?" with ∅, and finds the trial database a likely copy of the Quickstart's `coco_pharma` (the same
  database registered twice). Assessment answers four questions with nothing run on it; the Database Assessment
  Survey is offered on Analysis, not Assessment, and adds nothing there until W43 (the Architect, 2026-11-02), so
  part 6 runs no Assessment survey. Step 6 runs one analysis on its own, Table Statistics Freshness (pressed once
  on the project owner's word, 2026-11-03T15:00:31Z), turning two partial answers whole. G31 to G34 added; G13
  and G23 extended.
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

**The story you follow (D10).** You are **Peter Profile**, an information analyst at Coco Pharmaceuticals. You sign
in as Peter, so the screen shows your name. The **Teddy Bear Drop Foot** clinical trial is running. Three hospitals,
Oak Dene, Old Market and Hampton, send weekly measurement files. The files land in the `loading_bay` folder and are
loaded into the `coco_pharma` database. The trial's data is in good order: when a hospital finds an error, it sends
that week again and the original is kept (the `.restatement.csv` files you will see). What is new is an auditing
requirement from the regulator: an outsider must be able to see that the data is in good order, without asking the
team. Each reported figure traces to the file it came from, and superseded files are visibly retired. **Erin
Overview**, the information architect, asks you to make the trial's three sources ones the catalog can vouch for
**before the next weekly drop**: the database, the drop folder, and **OpenLineage**, a lineage project the team is
evaluating to record how files move from drop to database. OpenLineage is a candidate tool, not a data source, which
is why it gets its own parts (11 and 12). The people you can turn to: **Tessa Tube** (lead researcher), **Callie
Quartile** (data scientist, who reuses the data) and **Tanya Tidie** (the trial's data steward, accountable for
provenance).

- The people are Egeria's documented Coco Pharmaceuticals characters, all fictional. The prose links to Egeria's own
  pages about them (egeria-project.org, Coco Pharmaceuticals) and does not retell their biographies. The data is the
  Quickstart's.
- The story never supplies a result. Where a beat needs a number or a name, the prose writer reads it on the screen,
  marked here "(read on screen)".
- *For the writer:* no sentence may suggest the trial team was careless. The restated weeks are evidence of care;
  the new requirement is about letting an outsider see it (D10, section 5).

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

**The story, part by part.** Each part answers one question that Erin's request raises (section 1, "The story you
follow"). Each part opens with its beat in one line, and every screenshot is the screen Peter sees at that beat.

| # | The beat |
|---|---|
| 1 | Why not just catalog everything the trial touches? |
| 2 | Get in, as Peter. |
| 3 | Register the three: `coco_pharma`, `loading_bay` and OpenLineage. |
| 4 | How to spend the time before the next drop: the funnel. |
| 5 | Is `coco_pharma` alive, and what is in it? |
| 6 | Is it already catalogued? Is it worth a closer look? What does the closer look show? |
| 7 | What only you know: Tanya Tidie as owner, the sensitivity, the intended use "clinical-trial reporting". |
| 8 | Erin's follow-up questions, asked in chat. |
| 9 | Which schemas are worth cataloguing today? |
| 10 | Publish, and show Erin it is in Egeria. |
| 11 | OpenLineage beside Marquez and Egeria: which one to choose for lineage? |
| 12 | Record the whole thing as an investigation Erin can read. |
| 13 | Next week's drop, and what comes after. |

**The setup.** The Egeria Quickstart environment (its fictitious company is Coco Pharmaceuticals), with Resource
Explorer connected to it, a Quickstart user id and password, and the public OpenLineage repository on GitHub.
**Decided (D2, D2a):** the tutorial names its sample, so every reader sees the same screens: the Quickstart's
`coco_pharma` database (the main example), its `loading_bay` folder, and the OpenLineage repository. They are the
trial's three sources in the story: where the figures are held, where the hospitals' files arrive, and the candidate
tool that would record the path between them (D10).

---

## 3. The parts

### Part 1 — What Resource Explorer is for

**The beat.** Erin has asked Peter to make the Drop Foot trial's three sources ones the catalog can vouch for. The
first question is why that is not simply "catalog everything the trial touches".

**Goal.** Know what Resource Explorer does, how it relates to Egeria, and when to use it.

**Before you start.** Nothing.

**Content (no steps; two pages, one diagram).** Drawn from the blog series *Egeria Resource Explorer*, part 1,
"Effective Exploration and the Survey Funnel" (pdr-associates.com, 2026-09-18), with the owner's review of
2026-10-29. Use it for substance, written down to the tutorial's reader.

1. **One situation, among many.** A department has eighty databases nobody has written down, a few hundred internal
   repositories, and file shares that have grown for a decade. None of it is in a catalog. Someone is asked which of
   it matters for a new project (an AI project, a migration, a report), and they have a week. *Say plainly that this is
   one example:* others include checking a source the catalog already holds, judging an open-source project before
   adopting it, or simply finding out what is on a file share. *Then land on the one this tutorial follows:* at Coco
   Pharmaceuticals, a new auditing requirement means an outsider must be able to see where each figure of the Drop
   Foot trial came from. Three sources need to be ones the catalog can vouch for before the next weekly drop (section
   1, "The story you follow"). This situation has all three of the examples above in it: a database the catalog may
   already hold, a folder the catalog does not describe yet, and an open-source project to judge before adopting.
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
    around a fictitious company, Coco Pharmaceuticals, with made-up people. Change anything (section 1). The story
    (Peter, Erin and the Drop Foot trial) is the Quickstart's own: its people and its data, so the screens match it.

**What can go wrong.** Nothing on screen. The risks are in the writing. Do not explain Egeria's architecture, the
survey engines or where surveys run; one sentence and the glossary are enough. Do not use the blog's insider words
(ODS, outbox, graduate, governance action process, microflow).

---

### Part 2 — Sign in and find your way around

**The beat.** Before the next drop, Peter needs to get in. Signed in as Peter, the screen shows Peter's name.

**Goal.** Sign in and know what each area of the screen is for.

**Before you start.** The address of your Resource Explorer, and Peter Profile's Quickstart user id and password
(the Getting Started tutorial gives both).

**Steps.**

1. Open the address in a browser.
   - *You see:* a panel headed "Resource Explorer" and "EXPERIMENTAL UI", with "Sign in with your Egeria user id to
     continue.", an "Egeria user id" box, a "Password" box and a "Sign in" button (seen).
   - *Explain "EXPERIMENTAL UI":* it means what it says. Resource Explorer is still experimental and evolving with
     user feedback (part 1, item 11).
2. Type Peter's user id and password, then click **Sign in**.
   - *You see:* the main screen. Peter's user id and **Sign out** are at the top right (seen; Peter's id read on
     screen).
   - *Explain:* there is no separate Resource Explorer account. Egeria checks your password once and Resource Explorer
     does not keep it. Everything you do from now on is recorded in Egeria under your user id.
3. Look at the strip across the top: "Investigation · 1 Scouting · 2 Discovery · 3 Assessment · 4 Analysis ·
   5 Enrichment · 6 Curate · Understanding · Automate · ▦ Work lists", and at its right end **RFAs** with a count
   and **Chat** (seen; confirm at screenshot width).
   - *Explain in one line:* 1 to 6 are the stages a data source goes through, left to right (part 4). Work lists and
     Investigation come in parts 11 and 12. Understanding and Automate are for the Advanced tutorial. **RFAs** comes in
     part 7. **Chat** opens and closes the chat rail (step 5); it carries a panel icon, not "Chat ×" (read on screen 2026-11-02).
4. Look at the left sidebar: **Repos**, **DBs**, **FS** and **Open data** (repositories, databases, file systems,
   open data portals) and a filter box (seen). Point out that the **INVESTIGATION** picker and **Work lists** can be
   ignored until parts 11 and 12.
   - *Say, in one sentence:* Open data is covered by the Sustainability tutorials (not written yet), not here.
5. Look at the right-hand rail: "ASK scoped to <data source>", a box, **Ask** and **what would answer this?** (seen).
   Part 8 uses it. The rail is where chat lives; **Chat** in the strip shows or hides it (seen; confirm at screenshot
   width).
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
  part 12 no investigation is chosen, so the reader will see this. Tell them to ignore it until the fix lands. The
  prose writer re-checks this with no investigation chosen before writing it in.

---

### Part 3 — Add data sources, and find them again

**The beat.** Erin named three sources. Before Resource Explorer can look at any of them, Peter registers each one:
`coco_pharma`, `loading_bay` and OpenLineage.

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
3. **Canonical mount:** the folder's path inside Egeria's container, `/deployments/loading-bay`; Getting Started lists
   it (owner and Architect, 2026-11-02; the form's label reads "CANONICAL MOUNT (BLANK = THE MOUNT)"). See gap G5.
4. Click **Register**, then find the file system in the **FS** list.
   - *Say, in one line:* inside `loading_bay` you will also see a `Sustainability Files` folder and NAICS reference
     data. Those belong to Coco Pharmaceuticals' Sustainability project, which has its own tutorials. This tutorial
     reads the `sample-data` hospital folders, where the Drop Foot trial's weekly files arrive.

*What can go wrong (file system).*

- "⚠ this server can't read this path · you can still register" or "⚠ this is a file, not a folder · you can still
  register" (code). Explain: registering still works, but no survey can read the folder until the path is right.
- "✕ already registered": that short name is taken (code).

**3c. A repository.**

1. Click **Repos**, then **＋ Find repos** (seen).
   - *You see:* "Find and import candidate repos", with the tabs "🔍 Search GitHub" and "📄 From a list" (read on screen
     2026-11-02).
2. Choose **📄 From a list**, paste the OpenLineage repository's GitHub address and click **Load pasted list** (read on
   screen 2026-11-02). On 8813 it reported "1 already registered", and the row's box could not be ticked, so the story
   reuses the existing `openlineage` row (owner §3).
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
4. With **FS** chosen, click **🔎 Find by passages**: it finds file systems by what their narrative files say (seen).
   Type a question, for example about the Drop Foot trial, and read the file systems that come back, each with the
   passage that answered and the file it came from (code). (Owner to confirm: one step here, or a pointer to the
   Advanced tutorial.)
   - *Explain:* a passage is what a file says, not something measured, so the screen marks it as a proposal (code).
     A file system whose narrative files have not been read yet is counted as not read, not as "no match" (code);
     part 5 reads them (**Read narrative files**). Until then, `loading_bay` reads as not read (read on screen).

---

### Part 4 — The stages: a funnel from cheap to costly

**The beat.** The next weekly drop sets the deadline, so Peter needs to spend the time where it counts: the
funnel.

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
   - **By analysis** and **Disposition**: one line each (read on screen 2026-11-02): "SURVEY RESULTS, BY ANALYSIS"
     shows the stage's results one analysis at a time; Disposition holds the verdicts (undecided, tracking,
     investigating, recommended, using, abandoned, ignored), their history and a journal.
   - A database has three more tabs: "Schema Inventory", "Value profile", "Table preview" (read on screen 2026-11-02).
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

**The overview survey for each stage, by kind** (read on screen 2026-11-02, Survey & analyses on `openlineage`,
coco_pharma and `loading_bay`):

| Stage | Repository | Database | File system |
|---|---|---|---|
| Scouting | Scouting Survey (9 steps); also Repo Scouting Scan (4), Coarse Profile Survey (7) | Database Scouting Scan (3 steps) | Folder survey card: **Survey** is the overview (G4) |
| Discovery | Repo Discovery Survey (9 steps); also Repo Architecture Discovery (4) | **none** (gap G23) | the same Folder survey card |
| Assessment | Assessment Survey (14 steps); also Compliance Survey (14) | **none shown on this stage** (gap G23): "Database Assessment Survey" exists, named on its analyses' rows, but is not listed | the same Folder survey card |
| Analysis | Analysis Survey (11 steps) | Database Analysis Survey (8 steps) | the same Folder survey card |

Every repository stage also offers "Full Survey (all steps)", 43 steps, under "OTHER STAGES".

**What can go wrong.** Slow panes: "5 of 19 answered · 13 still loading" can stay for many seconds without saying what
it waits for (seen, gap G10). Wait until "still loading" goes.

---

### Part 5 — Scouting: run the overview survey and read the answers

**The beat.** Erin's first question about the database: is `coco_pharma` alive, and what is in it?

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
stars, forks, contributors and size. "? not read" means a step has not run, not zero (seen). For the file system
(`loading_bay`, where the hospitals' weekly files arrive), the "Folder survey" card does the work:

- **Press Survey.** It is the overview. The card has up to eleven presses (Check again, Survey, Publish this walk to
  Egeria, Measure beside Egeria's survey, Publish these measures to Egeria, Profile data files, Read Egeria's reports,
  Publish these profiles to Egeria, Derive, Read narrative files, Match data classes); each appears only when it has
  something to do (code). The tutorial presses Survey and names the rest in one sentence.
- **Hover a press to read what it does** before pressing it: each says what it reads, what it stores and whether it
  publishes, for example "Walks every file; stores the walk here. Publishing is a separate press." (code).
- **Read the summary lines.** The card shows whether Egeria can read the folder ("✓ Egeria can read <path>"), counts,
  the deepest folders, and files that describe their folder (seen). Its long sections (Files, Walk measures, Data-file
  profiles, Personal or sensitive data, Derived from the walk) start closed, each with a one-line summary beside its
  heading, so the card answers before you open anything (code).
- **Open a section for detail.** Open Files and look for a `.restatement.csv`: a week a hospital sent again, kept beside
  the original (read on screen). The card remembers which sections you opened (code).
- *Say, in one line, as in part 3b:* the `Sustainability Files` folder and the NAICS reference data in the walk belong
  to the Sustainability project and its own tutorials; this tutorial reads the `sample-data` hospital folders.

---

### Part 6 — Discovery, Assessment and Analysis: go further down the funnel

**The beat.** Erin asked whether `coco_pharma` is already in the catalog. If it is worth going deeper, Peter needs to
know what the deeper look shows.

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

**The beat.** Some of what the auditors will ask, no survey can measure: who answers for the data, how sensitive it
is, and what it is used for. Peter records what Tanya Tidie, the trial's data steward, says.

**Goal.** Record the owner, the sensitivity and the intended use of your database.

**Before you start.** Part 3.

**Steps.**

1. Click **5 Enrichment**, then the **Context** tab.
   - *You see:* "Everything a person has supplied about this resource — each row signed and dated, and each saying what
     it feeds. Nothing here is written to the Egeria until you publish it (Curate)." (seen).
2. Under "What we judge", set **Sensitivity** to "confidential", **Owner** to "Tanya Tidie", and **Intended use** to
   "clinical-trial reporting", typed in its "or in your own words" box (code).
   - *Explain:* these are what Tanya, as the trial's data steward, told Peter. No survey could have measured them.
     Intended use also offers a list of purposes to pick from; the tutorial types its own words instead (code).
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

**The beat.** Erin has follow-up questions that no survey planned for. Peter asks them in chat, and checks what each
answer rests on.

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

**The beat.** Before the next drop, Peter needs to decide which of `coco_pharma`'s schemas are worth cataloguing
today, and which can wait.

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
   "cataloged by Egeria N schemas", each with ✓ or "◌ not checked since <MM-DD HH:MM>" (seen; the stamp form
   seen on Curate › By analysis; confirm on the Contents chips).
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
   - *File system:* tags, group, ratings and notes, and a Publish that "attaches the root folder only" (seen). It
     also has a table headed "Publish · declared measures": one row per measure the folder survey offers to publish,
     each with **Publish** or **Keep in RE**, and a count line, for example "0 chosen to publish" (seen). There is
     no press on the table itself: the rows chosen go to Egeria with the folder survey's own Publish (part 5), and
     each is read back (code). Explain "declared measures" in plain words, as the glossary does. The old sentence
     "Nothing to review for file systems yet." still shows beside it (seen; gap G8).

**What can go wrong.**

- "not permitted · <reason>": Egeria's security does not let you change this element (seen, user guide).
- The sentence above the chips and a chip disagree about a count. Known defect (gap G11).
- *File system:* Curate still reads "Nothing to review for file systems yet." (seen), beside the measures table,
  although the survey page says "a proposal · confirm it on Curate". Gap G8.

---

### Part 10 — Publish to Egeria, and check that it arrived

**The beat.** Erin needs to see the result in the catalog itself, not in Resource Explorer. Peter publishes and checks
that Egeria holds it.

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

**The beat.** Erin asked whether OpenLineage is the right lineage project for recording how files move from drop to
database. Peter sets it beside Marquez and Egeria to compare.

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

**The beat.** Erin wants to read what was looked at, what was chosen and why, in one place. Peter records the work as
an investigation.

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

**The beat.** Next week's drop arrives. Peter knows what to do with it, and where to read further.

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
- Egeria's own documentation for the catalog itself, and its Coco Pharmaceuticals pages for the people and the Drop
  Foot trial.
- **Next week's drop** (the story's last beat): when the hospitals' next files land in `loading_bay`, Peter goes back
  to the parts needed, not to the start: the Folder survey card's **Survey** again (part 5), a look at what changed,
  and Publish (part 10). Setting that up to run on a schedule is Automate, in the Advanced tutorial.
- **The Sustainability documents** (planned, not written yet): Coco Pharmaceuticals' Sustainability project, with
  open data portals (the **Open data** sidebar kind and Admin › Open data), the sites' operational data in files and
  tables, and the analytics over them. The `Sustainability Files` folder and the NAICS reference data seen in
  `loading_bay` belong there.

---

## 4. Glossary

**Moved, 2026-11-02:** the glossary is now [`tutorial/glossary.md`](tutorial/glossary.md), the page each part links
to the first time a term appears. Change a definition there; there is one copy. Each term has an anchor (for example
`glossary.md#survey`).

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
- "Declared measures" as the tutorial's own words (it is a screen heading: explain it, table below), "declaration",
  "library step", "StepManifest", "entry point", "result kind", "absence sentence", "chat extent", "confidence form".
- Analysis ids as names: `fs_permissions`, `postgres_column_profile`, `db_catalogued`. Use what the screen calls them,
  or a plain description.
- Question ids (Q-0108) except in "Where to go next".
- "Classic", "/next", "the reset", "the throwaway", "8813" as a fact about the reader's setup.
- First names of anyone, other than the Quickstart's made-up users. "Owner decision" callouts belong in project
  records, not the tutorial.
- Any wording that the trial team was careless. The restated weeks are evidence of care; the new auditing
  requirement is about letting an outsider see it (D10).
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
| "Publish · declared measures" | Curate, file system | The measurements the survey offers to publish; you choose which. (The owner may rename the screen word later.) |
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

Every screenshot is taken on the Egeria Quickstart, signed in as Peter Profile, at 1600 × 900 (owner, 2026-11-02: the
screen the captures are taken on is 1728 × 1117, so 1920 × 1080 is wider than it), so the same image serves the written
tutorial and the videos (section 9). Existing images
from the merged walks are listed where one may be reused, after a check that the screen has not changed.

Every screenshot is a screen Peter sees at one beat of the story (section 2, D10), so the same names, counts and
stamps recur from part to part. Take them in order, in one Quickstart, without resetting between parts.

| # | Part · step | Story beat | Screen | Shows | Reuse |
|---|---|---|---|---|---|
| S1 | 1 | why not catalog everything the trial touches | Diagram, not a screenshot | Data source → survey → answers → your knowledge → curate → publish → Egeria | **Drawn, 2026-11-02:** [`tutorial-media/look-then-catalog.svg`](tutorial-media/look-then-catalog.svg), rendered at 1600 × 900 as `tutorial/images/01-what-for/S01-look-then-catalog.webp`. Same palette as the funnel. |
| S2 | 2 · 1 | Peter gets in | Sign-in panel | The panel, empty boxes | **Taken, 2026-11-02:** `tutorial/images/02-sign-in/S02-sign-in-panel.webp`. The user id box held "peterprofile": the Quickstart on 8813 sets a default sign-in user. |
| S3 | 2 · 3–6 | Peter's screen, Peter's name at top right | Whole screen, labelled | Strip, sidebar, main panel, chat rail, header, each numbered | new (labelled version of `01-stage-strip-and-sidebar.webp`) **Taken, 2026-11-02:** `S03-peters-screen.webp` and `S03-peters-screen-labelled.webp`, six numbered areas (header, strip, perspectives, sidebar, main panel, chat rail). |
| S4 | 2 · 7 | what has run so far | Activity log | The latest entries and the filter buttons | **Taken, 2026-11-02:** `S04-activity.webp`. |
| S5 | 3a · 1 | registering `coco_pharma` | Find databases dialog | The three tabs and **Register one database…** | **Not taken (2026-11-02):** the Saved sources tab lists the owner's dev server with its credential user name (G26); the other two tabs do not show **Register one database…**. Re-shoot after a saved-sources hide. The prose quotes the dialog. |
| S6 | 3a · 3–4 | `coco_pharma`, filled in | Register one database, filled | Fields filled, test passed, Register enabled | **Taken 2026-11-02, before the test:** `S06-register-one-database.webp`, password empty, "○ not tested". The test and Register were pressed with the password typed by the owner, so the passed state was not captured. Re-shoot after W38 (sidebar visible). |
| S7 | 3a · 5 | `coco_pharma` registered, nothing in Egeria | After Register | "It is in the database list now. Nothing was sent to Egeria." | **Taken 2026-11-02:** `S07-registered.webp`. Re-shoot after W38. |
| S8 | 3a · 6 | `coco_pharma`, never surveyed | The new database page | "never surveyed" | **Taken 2026-11-02:** `S08-never-surveyed.webp`, "1 of 9 answered · Scouting". Re-shoot after W38. |
| S9 | 3b · 2 | registering `loading_bay` | Add a file system | Path field with "✓ readable here" | **Not taken (2026-11-02):** the mount path on 8813 starts with a real account's home folder (G25). "✓ readable here" was read on screen. |
| S10 | 3c · 3 | importing OpenLineage | Find repos, From a list | One row ticked, Import Selected | **Taken 2026-11-02:** `S10-from-a-list.webp`, the row "✓ registered" and not tickable (the existing row reused). Re-shoot after W38. |
| S11 | 3d | the three, found again | Sidebar filters | Filter box and chips | **Taken 2026-11-02:** `S11-filter.webp` ("lineage" in the filter) and `S11b-find-by-passages.webp` (new). Re-shoot after W38. |
| S12 | 4 · 1 | the time before the next drop | Diagram, the funnel | Six stages, cost rising left to right, many sources narrowing to few | **Taken, 2026-11-02:** `funnel.svg` rendered at 1600 × 900 as `tutorial/images/04-stages/S12-funnel.webp`. |
| S13 | 4 · 2 | a stage on `coco_pharma` | A stage's tabs, labelled | Questions, Survey & analyses, By analysis, Disposition | **Taken, 2026-11-02:** `S13-a-stage.webp` and `-labelled`: the tabs, the count line, the key, one row (∅). On 8813 the database's slug is `localhost_docker_coco_pharma`. |
| S14 | 4 · steps 2 | the overview survey for `coco_pharma` | Survey & analyses, labelled | The overview survey and a narrower one marked | **Taken, 2026-11-02, on `openlineage`, not the database:** the database's Scouting has only one survey, so it cannot show an overview beside a narrower one. `S14-survey-and-analyses.webp` and `-labelled`. |
| S15 | 4 · steps 3 | how to read an answer | The marks | The key, or a drawn table | **Drawn, 2026-11-02:** [`tutorial-media/marks.svg`](tutorial-media/marks.svg) as `S15-marks.webp`; the real key is in S13. |
| S16 | 5 · 1 | is `coco_pharma` alive? | Survey & analyses on a never-surveyed database | The overview survey's run button | **Taken 2026-11-02:** `tutorial/images/05-scouting/S16-run-dialog.webp`, the "Database Scouting Scan" dialog, "Nothing runs until you confirm." Re-shoot after W38. |
| S17 | 5 · 2 | `coco_pharma` being surveyed | Running | "◔ running" and the notice with View | **Taken 2026-11-02:** `S17-running.webp`, "◔ running · started just now". No notice with **View** was seen; the Activity entry was read instead. Re-shoot after W38. |
| S18 | 5 · 3–4 | what is in `coco_pharma` (read on screen) | Questions after the survey, one row labelled | Answer, source line, buttons | **Taken 2026-11-02, re-shot after F46 merged (#973):** `S18-answers.webp`, "30 schema(s), 29 with tables, 28 visible to this credential" and "34,207 writes and 2,989,434 reads"; with `S18b-numbers-behind.webp` (schema total 30). Re-shoot after W38. |
| S19 | 5 · note | OpenLineage, first look | Repository header tiles | Tiles incl. "? not read" | **Taken 2026-11-02:** `S19-openlineage-header.webp`, the stored survey ("surveyed 27d ago"); not run again. Re-shoot after W38. |
| S20 | 5 · note | `loading_bay`: the weekly files, summary lines, sections closed | File-system survey | Reach line, the presses, the closed sections with their summary lines | **Taken 2026-11-02:** `S20-folder-survey.webp` after Egeria's Folder Survey; with `S20a-root-folder-attached.webp` (Curate's publish band) and `S20b-files-open.webp` (Files open). Files reads "analysis level: top level only", so no `.restatement.csv` is listed; that moves to the part that walks the files. Re-shoot after W38. |
| S21 | 6 · 1 | is `coco_pharma` already catalogued? | Discovery | "Nothing is recorded for this — a real absence…" | **Taken 2026-11-03:** `tutorial/images/06-further-down/S21-already-catalogued.webp`, ∅ "Not yet published to Egeria"; with `S21b-likely-copy.webp`, "localhost_docker_coco_pharma (likely_copy, column-signature Jaccard 1.00, containment 1.00)". Re-shoot after W38. |
| S22 | 6 · 4 | what has not been looked at yet | Analysis, a ○ row | "Nothing has been measured for this yet." | **Taken 2026-11-03:** `S22-not-measured.webp`, three ○ rows; "Nothing has been measured for this yet." beside "Not yet run." (G32). Re-shoot after W38. |
| S23 | 6 · 5 | the deeper look, one analysis at a time | One analysis run on its own | The row after its run | **Taken 2026-11-03:** `S23a-one-analysis-ran.webp` (the Table Statistics Freshness card, ✓ "just now") and `S23-statistics-current.webp` ("statistics_freshness · measured · 215 of 266 tables (81%)", the partial line gone). Re-shoot after W38. |
| S24 | 7 · 1–2 | Tanya as owner, "clinical-trial reporting" | Context tab | Sensitivity, Owner, Intended use set, signed and dated | new |
| S25 | 7 · 5 | what a person must settle | RFAs drawer | One open item | new |
| S26 | 8 · 2–3 | Erin's follow-up question, and what the answer rests on | Chat answer | The answer and the "From N stored results" line | new |
| S27 | 8 · 5 | which survey would answer Erin | what would answer this? | Analyses with and without results, **Run** | new |
| S28 | 9 · 2 | what Egeria already holds of `coco_pharma` | Contents, the chips | Three chips | new on the reader's database |
| S29 | 9 · 3 | which schemas today | Include / Leave out | One of each | Keep both: the explainer `tutorial-media/curate-decisions.png` and this capture of the real screen (owner, 2026-10-29). Start from the blog's `curate-decision-table-v2.png`; replace it with a newer capture when one is taken. |
| S30 | 9 · 4–6 | making `coco_pharma` findable | Findable and What people say | Tag, group, rating, "saved · will publish" | new |
| S31 | 9 · 7 | OpenLineage's Curate page | Repository Curate page | Its three tabs, labelled with the shared parts | new |
| S32 | 9 · 7 | `loading_bay`'s Curate page and its measures table | File-system Curate page | Tags, group, "attaches the root folder only", the "Publish · declared measures" table | new: the table is not in `05-curate-file-system.webp` |
| S33 | 10 · 2 | what the press will send | Publish band before | "next publish" and **Publish** | new |
| S34 | 10 · 4 | `coco_pharma` confirmed in Egeria | Publish band after | "✓ in Egeria · … · confirmed" | new |
| S35 | 10 · 5 | the survey report Erin can open | Egeria reports | Report rows | `03-curate-publish-to-egeria.webp` if unchanged |
| S36 | 10 · 7 | showing Erin, in Egeria | Egeria Explorer | The database with the tag and survey report | new |
| S37 | 11 · 2 | OpenLineage, Marquez, Egeria | Select several | Three ticked, save as work list… | new |
| S38 | 11 · 4 | the three, side by side | A work list | Rows × questions | new |
| S39 | 11 · 7 | the lineage shortlist | The shortlist | After promote | new |
| S40 | 12 · 1 | recording the work for Erin | Start an investigation from this list | The dialog, filled in, Ad-hoc chosen | new |
| S41 | 12 · 5 | the Drop Foot investigation, current | The investigation page | "Current investigation", SCOPE · 3 | new |
| S42 | 12 · 6 | the questions in the investigation's order | Questions ordered by purpose | "ordered by purpose · …" and "NOT AMONG …" | new |
| S43 | 12 · 9 | every investigation, the Drop Foot one among them | Investigations table | Every investigation | `01-investigation-stage.webp` if unchanged |

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
| G1 | No named starter setup. The merged walks used whatever was loaded on the developer's server. | all parts | **Settled by D2 and D6:** the Egeria Quickstart, set up by Getting Started. Named (D2a): `coco_pharma`, `loading_bay`, the OpenLineage repository. Note: `loading_bay` holds two storylines' files, the Drop Foot trial's (`sample-data`, this tutorial) and the Sustainability project's (`Sustainability Files`, NAICS reference data; its own tutorials). Parts 3b and 5 say so in one line. |
| G2 | The merged walks pressed nothing that writes, so every step that runs, saves or publishes was described, not shown. That is the main reason they "skip steps". | 3, 5–12 | **Settled by D6:** the Quickstart is throwaway, so the prose writer presses for real. |
| G3 | "Where it lives" defaults to "Create in Egeria". "Ad-hoc" (dialog) and "🏠 local" (table) are two words for one thing. | 12 | Choose one word. The tutorial tells the reader to change the default (D3a); changing the app's default is still worth considering. |
| G4 | A file system's "Folder survey" card offers up to eleven presses (Check again, Survey, Publish this walk to Egeria, Measure beside Egeria's survey, Publish these measures to Egeria, Profile data files, Read Egeria's reports, Publish these profiles to Egeria, Derive, Read narrative files, Match data classes). A repository's and a database's overview survey is not marked either. | 4, 5 | **For the file system, settled by hover help (trellis #967, 2026-11-01):** each press says on hover what it reads, stores and publishes, and the overview is **Survey**; part 5 says so. Still open for repositories and databases: mark each stage's overview survey on the screen. |
| G5 | File-system registration says "Mount (local path)" and "Canonical mount (blank = the mount)". The user guide says "host and container path". The survey then reports the path Egeria reads ("/deployments/loading-bay"). A reader cannot tell which path to type. **Amended 2026-11-02:** the tutorial now gives both paths and says not to leave the canonical mount blank (part 3b, step 3). Still open for the label words: "(BLANK = THE MOUNT)" invites the wrong choice. | 3b | One explanation of the paths, on the form. |
| G6 | "Slug *" is a required field on both register forms. Dashes are silently turned into underscores. | 3a, 3b | A plain label ("Short name"), or a hint under the box. |
| G7 | "Egeria connection (optional)" on the database form asks for "Egeria-visible host", "Egeria platform URL" and "View server". Most readers cannot know these. | 3a | Hide it from non-admins, or say when it is needed. |
| G8 | File-system survey says "a proposal · confirm it on Curate"; Curate says "Nothing to review for file systems yet." A dead end. Curate now also shows the "Publish · declared measures" table, but that is for measures, not for the survey's proposals, so this gap is about the sentence only. | 5, 9 | Build the review, or remove the pointer. |
| G9 | Header can read "not signed in" while signed in (no investigation chosen). With investigations moved to part 12, the reader sees this for most of the tutorial. | 2–11 | Already queued as a defect. More urgent now. Re-check with no investigation chosen: on 2026-11-01 it could not be re-checked, because an investigation was chosen in the pane. **Re-checked 2026-11-02** with no investigation chosen: the header read "No investigation" and "peterprofile", not "not signed in". Not seen, so part 2 does not mention it. |
| G10 | Slow panes say "still loading" for 9–15 seconds without saying what they wait for (Discovery, Analysis, Enrichment). | 4, 6, 7 | Already queued as a defect. |
| G11 | A published database's header reads "never surveyed · not published to Egeria" while Curate says it is in Egeria; the sentence above the chips and a chip can disagree. A file system too (read 2026-11-02): after its root folder is attached and Egeria's Folder Survey has run, its header still reads "never surveyed · not published to Egeria". | 5, 9 | Already queued as defects. |
| G12 | "unbound by reset" is explained only by reference to an internal event. | 12 | A word that describes the state, not its history. |
| G13 | A ✓ beside an answer that says nothing could be checked ("not checked: 0 of 65 … could be queried"). **Also 2026-11-03:** "Are this database's planner statistics current?" shows ✓ while its line reads "statistics_freshness has not run, so this answer is partial". | 5, 6 | Already queued as a defect. |
| G14 | The user guide says a database's Curate is one page; walk 3 shows tabs ("Describe and publish · Contents · Structure · …") on a database. One of them is out of date. | 9 | Read the screen and correct the guide. |
| G15 | "RFAs" is an unexplained abbreviation in the strip. | 7 | Spell it out on hover, or rename. |
| G16 | Internal words on screen: "ran via Prefect"; "compile" in the chat rail ("a compile reads one resource's stored results"); chat source lines list raw analysis ids ("fs_permissions, fs_activity"). The Activity panel too (read 2026-11-02): its subtitle "…read from the same log the current UI's Activity tab shows" and its entries ("fs_permissions (per-card run): 1 step: ran"). Part 2 does not quote either. Survey & analyses cards (read 2026-11-02): "produces · ResourceMeasureAnnotation, …" and "GovActionProcess::RepoCoarseProfile · definition history"; the "Who owns this resource" row's developer parenthesis ("MIXED: two halves …"). A running survey (read 2026-11-02): "Launched GovActionProcess::DatabaseScoutingSurvey." The folder survey's **Survey** and Egeria's own "Survey Folder › Run in Egeria →" are one run shown as two buttons; pressing both surveys the folder twice (read 2026-11-02; in the W39 brief). One glyph, two meanings: ⚠ is "needs you" in the key but marks staleness on survey cards ("⚠ ran 70d ago"). | 2, 5, 8 | Plain words, or analysis display names. |
| G17 | No single named place in Egeria for the reader to check that a publish arrived. | 10 | **Settled (owner, 2026-10-29):** Egeria Explorer. Still worth having: a link from Resource Explorer's "Show in Egeria" that opens Egeria Explorer on the element. |
| G18 | Publish-band words a new user cannot read: "kept survey", "◐ behind · 76 annotations", "◐ stale 9 of 12 steps are stale", "◐ partly committed · 4 queued". | 10 | Plain sentences, or a hover that explains each. |
| G19 | The Admin dialog says "the eight intents"; the word appears nowhere else on screen. | Advanced tutorial | Say "the stages". |
| G20 | The sign-in panel says "EXPERIMENTAL UI". | 2 | **Not a defect (project owner, 2026-10-29):** RE is positioned as still experimental, evolving with user feedback. The tutorial explains the label; nothing to fix. |
| G21 | This writer's browser was not signed in, so the register dialogs, the new-investigation dialog and the chat rail are outlined from the source code (marked "code"). | 3, 8, 12 | The prose writer reads each on the screen, signed in. |
| G22 | Steps whose exact screen words nobody has recorded: a wrong password, Egeria down at sign-in, an empty investigation name, "promote" on a work list, the Assessment tab on a database, a step that asks before running a prerequisite. | 2, 5, 6, 11, 12 | Read on the screen during the prose phase; some need a deliberate error to see. |
| G23 | The overview surveys are uneven across kinds (definitions, 2026-10-29). A database has no Discovery survey. A database's Assessment survey has one step. File-system stage surveys are not yet identified. Names are inconsistent: "Scouting Survey" and "Repo Scouting Scan" are both repository Scouting surveys; "Database Scouting Scan" is the database's overview; "Assessment Survey" and "Analysis Survey" carry no kind in their names, while "Repo Discovery Survey" does. A reader cannot tell from a name which survey is the overview. **Re-read on screen 2026-11-02:** a database's Assessment stage lists no survey of its own; the repository Assessment Survey has 14 steps, not 11; the file system has one Folder survey card on every stage. Part 4 gives the reader a table. **Read 2026-11-03:** the Assessment Survey's one question (Q-0085) is tagged Analysis, so the survey is offered on Analysis and adds nothing on Assessment (W43; the project owner chose "Re-tag its questions"). | 4, 5, 6 | One naming rule (for example "<Kind> <Stage> Survey" for every overview), and the missing overviews either built or explained. |
| G24 | After design note D1-a, a database can show as already in Egeria, matched by its endpoint, rather than published fresh. A reader who expects "published by RE" sees a different state. | 9, 10 | Explain it at part 10, step 7. D1-e (trellis #962) adds a coverage line, read on the screen on 2026-11-01 for coco_pharma as "not yet published · 26: 3 undecided · 22 in Egeria under 'Coco PostgreSQL Server 1' · not RE's · 1 left out by a confirmed proposal"; those counts are that screen's, so read the words on the screen before writing them in. |
| G25 | Screenshot hygiene. The Quickstart on 8813 carries the project's own test work: the sidebar's WORK LISTS show "OD1 probe 2026-10-29", "Concurrency test — twelve re…", "dts-treasury-data candidates" and "egeria-understanding arch"; the repository list is 73 long; and the screen landed on a repository marked "abandoned", "Repository is archived". A reader's Quickstart shows none of this. Seen in S03, S13, S14, and in every part 3 shot with the sidebar. Also a file system's mount path that names a real account's home folder (S9 not taken). | 2, 3–13 | **For the owner (proposed 2026-11-02):** hide the test work lists before part 3's captures, or crop the sidebar where it is not the subject. If S03 is re-taken, land on a healthier repository. |
| G26 | Saved database sources are listed to every person with their credential user name: "Find databases" opens on "Saved sources", and on a shared instance shows one person's dev servers and login to everyone. | 3a | A per-person hide (W38's pattern) or an owner-only listing. (Architect, 2026-11-02; to be briefed as W39 once the owner grades it.) |
| G27 | "Find by passages" result line starts mid-sentence: "read, and no passage matched this question. The files may still say it in other words · 4 file systems not read yet, not searched" (read 2026-11-02, with no file system's narratives matching). The words before "read," are missing. | 3d | Name what was read and searched before "read,". |
| G28 | Registering a file system writes no Activity entry; registering a database writes "Registered database <slug>" (read 2026-11-02). Part 2 tells the reader Activity logs everything that has run. The folder survey card's **Check again** writes none either (read 2026-11-02). | 2, 3b, 5 | An Activity entry for every registration and every press. |
| G29 | The folder survey's **Survey**, on a file system Egeria leads but has no asset for, is refused with nothing on screen ("Not published to Egeria yet", 422, seen only in the log; F45). The step that makes it work is on another stage: Curate › PUBLISH TO EGERIA › **Publish**, "attaches the root folder only". A reader has no way to find that. | 5c | W39: say "attach the root folder first" on the card, and disable **Survey** until it is attached. Part 5c tells the reader to attach first. |
| G30 | One survey report, two counts (read 2026-11-02): the folder survey card says "7 annotations on the report; 6 read into RE", while Curate's "Egeria reports" lists the same report with "0 annotations". | 5, 10 | One count, or say what each one counts. |
| G31 | The "LAST SURVEY" tile names the stage's overview survey, not the survey that ran (read 2026-11-02, graded F47). For one run, Assessment showed "Database Scouting Scan" and Analysis showed "Database Assessment Survey", which had never run. | 6 | Name the survey that ran last. |
| G32 | ○ rows say "not run" in two ways (read 2026-11-03, graded W54): "Not yet run. Run: postgres_column_profile." and "Nothing has been measured for this yet. Run: postgres_nested_columns." Only the second has "not yet run" in its source line. | 6 | One phrasing, and the source line on both. |
| G33 | A headline that names the wrong numerator (read 2026-11-03, graded W57): after Table Statistics Freshness ran, the source line reads "statistics_freshness · measured · 215 of 266 tables (81%)". All 266 relations were measured; 215 is the never-analyzed count (215 never analyzed, 48 current, 3 views unknown). | 6 | The headline names its numerator. |
| G34 | Two questions show one shared sentence (read 2026-11-03, graded W58): "How complete and consistent is this database's documentation" (Q-0088) and "How well-modelled is this database" (Q-0090) both show Schema Conventions' summary, "37 of 263 base tables declare no primary key. 54 of 263 base tables have no comment; 32 of 1916 columns do." | 6 | Each question shows the measures it names. |

---

## 8. Decisions for the owner

| # | Decision | State |
|---|---|---|
| D1 | The main example data source | **Decided (project owner, 2026-10-29):** a database. Set the expectation that each kind's Curate page differs but the structure is the same, and show all three Curate pages (part 9, step 7). |
| D2 | The named sample setup | **Decided (project owner, 2026-10-29):** yes, the Egeria Quickstart, set up by the Getting Started tutorial. |
| D2a | Which database, folder and repository | **Decided (project owner, 2026-10-29):** the `coco_pharma` database, the `loading_bay` folder and the OpenLineage repository. **Why these three (D10):** they are the Drop Foot trial's three sources: the database where the figures are held, the folder where the hospitals' weekly files arrive, and the lineage project that would record the path between them. `loading_bay` also holds the Sustainability project's files, which this tutorial leaves to its own (parts 3b, 5). |
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
| D10 | The scenario followed through every part and screenshot | **Decided (project owner, 2026-11-01):** "it is important to set the stage in the beginning with a motivational scenario and then follow that scenario through with the example screenshots that all follow the same scenario". The scenario: the Teddy Bear Drop Foot trial under a **new auditing requirement** from the existing regulator (owner: "Yes, new requirement"), not a lapse and not an additional body; Peter Profile as the reader; Erin Overview asks; Tessa Tube, Callie Quartile and Tanya Tidie as the experts; "before the next weekly drop" as the deadline. **Guardrail:** the trial team is not careless, and no sentence may imply it: the restated weeks are evidence of care (owner: "are you saying that the Coco team is being sloppy …?" — no). The story never invents a result: where a beat needs a number, "(read on screen)". Open data is not in Core: Sustainability "deserves a standalone set of documents and tutorials (which we were already planning to do)" (parts 2, 3b, 5, 13). |

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

**Decision (project owner, 2026-11-02):** each part's narration is drafted with the part, in the same PR, under
[`tutorial/narration/`](tutorial/narration/).

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
Overview, Tessa Tube, Callie Quartile, Tanya Tidie, Drop Foot, Oak Dene, PostgreSQL, GitHub,
Quickstart. The prose writer adds any other name that the voice gets wrong.

### Images, for both uses

- **Capture once, at video size.** Take every screenshot with the browser at 1600 × 900, so each fills a 16:9 frame
  (owner, 2026-11-02; it scales up to a 1080p video frame).
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
