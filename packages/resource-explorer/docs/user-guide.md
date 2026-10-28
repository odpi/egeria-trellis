# Resource Explorer — User Guide

**Status:** live. Investigations, work lists, private work, scouting, Curate, Publish and live status are current.

**Last revised:** 2026-10-14

Resource Explorer discovers, surveys, and catalogs information resources using Egeria as the metadata catalog. This guide covers the web UI, investigations and work lists, the eight intents, and how to get the most out of each workflow.

---

## Starting the web UI

```bash
uv run resource-explorer web     # → http://localhost:8810
```

## Signing in

Resource Explorer asks you to sign in before it shows anything. Use your **Egeria user id and
password** — Egeria is the identity provider, and there is no separate Resource Explorer account.

Your password is used once, to get an Egeria token, and is never stored. From then on every read
and write RE makes runs *as you*: Egeria's own provenance records your user id, and anything you
publish is owned by you.

**Sessions last about an hour**, because that is how long Egeria's tokens last — and they all end
when the Egeria platform restarts. When yours lapses the sign-in panel reappears; sign in again
and carry on. Opening Resource Explorer from inside the Egeria Portal skips this entirely: the
Portal hands over the token it already holds.

The header shows who you are signed in as; the ⏻ button next to it signs you out.

### From the terminal

The CLI caches a session so you are not asked on every command:

```bash
uv run resource-explorer login              # prompts for your password
uv run resource-explorer logout
```

Commands that read local data — `list`, `ask`, `runs list`, a plain `survey` — work without one.
Commands that reach Egeria — `survey --publish`, `egeria-recheck`, `curate materialize`,
`analysis run --publish` — need a live session and say so in one line if you do not have one:

```
session expired at 14:07, run `resource-explorer login`
```

## Who can see your work, and what "owned by you" means

Everything you publish to Egeria is written **as you**. Egeria's own record shows your user id, and the
elements you create are owned by you. A run that a person starts in the Explorer, or that a schedule starts
later, publishes in the same way, with the same care about whose work it is (see "Private investigations").

**Resource Explorer adds no zone of its own.** Egeria "zones" are the labels a deployment uses to say who
may see or change an element. By default RE sends none, so what you publish follows the deployment's normal
rules. A draft is a *content status* (a draft is something a person has not yet accepted), not a zone. The
older "draft zone" (`resource-explorer-draft`) was retired on 2026-10-08. A deployment can still choose to
configure one; ask whoever runs your Explorer (the admin guide, "Governance zones", says how).

- **Egeria's zones decide who may curate.** Where no governance zone is in use, any signed-in person may
  accept, reject or publish. Where zones are in use, you may change an element only if Egeria's zone security
  would let you update it; a Portal curator or admin role may always. A refused row says "not permitted" and why.
- **Accepting is a decision, not a write.** Accepting or rejecting a finding records your decision in RE.
  Nothing reaches Egeria until someone presses **Publish** (see "Curate and Publish").

---

## Investigations, purposes and work lists

### What an investigation is

An **investigation** says *why* you are looking at a set of resources. It has a name, a description, a kind
(for example Personal, Experiment, Task, Campaign or Study) and one or more **purposes**. Its **scope** is the
set of resources it covers, and the scope can mix repositories, databases and file systems.

The **Investigation** stage, at the left of the stage strip, is where you start one and see its scope. Once an
investigation is *current*, the sidebar shows **Scope · N** and **Work lists for <name> · N** under it, and a
resource that is in the scope says so ("already in <name>'s scope"); otherwise the act reads "add to <name>".

An investigation is a way of framing your work. It is **not** one of the eight intents below. The intents say
what a piece of work does; the investigation says what it is for.

### Purposes

Pick a purpose from the list when you start an investigation (or when you fill in Enrichment's **Intended
use**). The list is kept in Egeria as a valid-value list, so everyone using the same Egeria sees the same
choices. It is **suggestive, not strict**:

- You may type a purpose that is not in the list. It is kept exactly as you wrote it, and it shows a small
  "◌ not in the list" cue instead of an error.
- If Egeria cannot be read, RE shows the last list it read and says how old that copy is; with no copy at all,
  it shows the list that shipped with it, and says so.

### Private investigations

An investigation of kind **Personal** or **Experiment** is **private to its owner**: you.

- In RE, nobody else sees it in their list. It carries a 🔒 mark.
- In Egeria, what it produces (its survey reports and their findings) is written with the zones
  `[resource-explorer-private, <your user id>]`, so Egeria's own security lets only you read it.
- **The work runs as you.** Only the signed-in owner can write a private investigation's reports. If a
  schedule or another person's session reaches one, RE does not write it; the row waits, reading "waits for
  <owner> to publish", and your next Publish sends it.
- The repositories, databases and file systems in a private investigation's scope are shared things. RE does
  not hide them from other people; only what the investigation *produces* is private.
- Today a private investigation can publish **repositories**. Databases and file systems in a private
  investigation are refused with a clear message instead of being published where others could read them.
- Changing a private investigation to a public kind always works. Changing a public one to a private kind can
  stop part-way if something already reached a public zone; the result lists exactly what moved and what did not.

An older private investigation created before RE recorded owners shows a note saying so, and is visible to
everyone until it is recreated.

### Work lists versus investigations

Three words, three jobs:

| Word | What it is |
|---|---|
| **Investigation** | *Why.* Purposes, description, verdicts. |
| **Scope** | *Which resources*, of any kind, that the investigation covers. |
| **Work list** | A *bench*: one kind of resource laid out as rows against one stage's questions, so you can decide which ones belong in a scope. |

To use one: open **Select** in the sidebar, tick some resources of one kind, and press **save as work list…**.
Open the list from **▦ Work lists** in the stage strip. From there you can run one survey across the whole
list, compare the results, narrow the list to a shortlist (**promote**), and then **Add to an investigation…**
or **Start an investigation from this list…**. Publishing a work list is explicit: creating one writes only to
RE.

Lists that start with `suggested-to-` are not work lists. They are inboxes for entries other people pointed
you at from the journal ("suggest to…"), and they have their own **Suggestions** line in the sidebar.

---

## Questions

Resource Explorer organises its checks as a **question catalogue**. Each question has an id,
**Q-0001 to Q-0095**, a funnel stage (Scouting, Discovery, Assessment, Analysis or Enrichment), the resource
types it applies to, and the way it is answered. The questions you see in a stage's checklist are rows from
this catalogue; the checklist shows the question's text.

- A question is never reworded after the fact. To change one, an author adds a new question and retires the old
  one; retired questions stay in the catalogue, dimmed, because past answers refer to them as they were asked.
- Answers you give in Enrichment are saved with the id of the question they answer.
- Administrators browse the catalogue under **Admin → Question Catalog**. The source is
  `docs/dr-egeria/resource_questions.csv`, explained in
  [`dr-egeria/resource_questions_guide.md`](dr-egeria/resource_questions_guide.md).

---

## What the marks mean

RE shows a state as a **mark plus a short word** on the thing itself. Hover (or open the details) for the
full sentence.

| Mark | Reads as | Means |
|---|---|---|
| ✓ | answered, measured, published | It was done and checked. |
| ∅ | nothing found, empty | It was measured and there was nothing. This is an answer, not a gap. |
| ◐ | partial, structure only, within credential scope | Measured, but only part of the picture. |
| ○ | not run | Nobody has run it yet. |
| ◌ | no reader yet, undecided | There is no answer to give here yet. |
| ? | not established, could not read | RE could not tell. Not the same as "no". |
| ⚠ | needs you | A person has to act. |
| ◔ | running, queued | Under way. |
| ✕ | error, failed | It failed; the word says why. |
| □ | stored, archived | Results exist but have not been read, or it was archived. |

In Curate, a choice is **Include** or **Leave out** and is saved in RE the moment you press it. A row that is
left out, or **deleted in Egeria**, is shown lowered in muted ink; "deleted in Egeria" has no mark of its own,
only the word. "Saved · will publish" means RE holds your change and Egeria does not yet; "published 10-11"
means Egeria has it. A draft finding is one nobody has accepted yet.

---

## Layout

The web UI (the **Next** UI, which is what `/` serves unless your administrator chose Classic) has:

- **Stage strip** along the top. The run goes left to right: **Scouting, Discovery, Assessment, Analysis,
  Enrichment, Curate**. **Investigation** (the frame) sits before them, and **Understanding** and **Automate**
  are cross-cutting: Understanding is where you look at a resource over time, and Automate makes a run repeat.
- **Left sidebar**: resources grouped by kind (repositories, databases, file systems), the current
  investigation, its scope and work lists.
- **Main panel**: the selected stage for the selected resource, usually as sub-tabs.
- **Header**: who you are signed in as, and the Activity log.

## The eight intents

Every analysis in the catalogue is tagged with one of **eight intents**: **Scouting, Discovery, Assessment,
Analysis, Enrichment, Understanding, Curate, Automate**. The tags come from the analysis catalogue,
`resource_explorer/configdata/analysis_catalog.yaml`. In the Next UI the intents appear as the stages above.
Automate has no analyses of its own: it holds schedules and subscriptions. This section covers each in turn;
Scouting, Assessment, Discovery and Enrichment are the original four and are documented in the most depth,
Analysis is covered separately (it is where Architecture Recovery lives), and Understanding, Curate and
Automate get a short section each.

### Scouting
*Goal: broad inventory across many resources, fast*

Select **Scouting** in the Analyses panel, then click **Run all scouting analyses**. This runs:
- Language and file classification
- Repository health metrics
- Basic dependency detection

Results appear in the Activity Log and are visible in the Survey Report immediately after the run completes.

#### Importing many repositories at once

Use **＋ Find repos** at the bottom of the repositories sidebar. It has two ways in: a **search** (keywords,
stars, language, licence, organisation, topic) and **From a list** (a CSV with an `address` column, or one
GitHub URL per line; an organisation URL expands to its repositories). Either one only fills a review table.
**Nothing is registered until you tick rows and press Import Selected.**

- **Progress comes from the press.** The button reads *Importing…* and each row shows its own state
  (importing, imported, failed) as the repositories finish. The sidebar fills in as they land, and the batch
  also appears in the Activity log. You can close the dialog; the import carries on.
- **Already-registered repositories are skipped**, and the status line says how many. A repository that another
  batch is importing at that moment is left alone and counted separately.
- **Statistics are deferred.** Import registers a repository at once and does not fetch its GitHub statistics.
  Until the first scouting survey runs it reads **not yet surveyed**, never zero. The survey fetches the numbers.
- You can choose a group for the whole batch, and set a disposition on a row before you import it.

#### Where the docs are

A repository's homepage and its documentation are often different places (GitHub's homepage field is
frequently a marketing site). When the Scouting survey's homepage step has run, the repository header shows
the **Project site** and, beside it, the documentation link with one short cue:

| Cue | Means |
|---|---|
| **docs ✓** | A documentation site was found and it answered. |
| **docs unreachable** | One was found but did not answer when RE looked. |
| **no docs found** | RE looked in the repository and its manifests and found none. |
| **docs not checked** | A link is known but has not been checked yet. |

Nothing is drawn before the homepage step has run: "not measured" is not "no docs found".

### Assessment
*Goal: deep analysis of a specific resource*

Select a resource, open **Analyses**, and choose Assessment intent. Run individual analyses:
- **Dependency Analysis** — full dependency graph with version pins
- **Security Scan** — secrets, vulnerable patterns, exposed credentials
- **Documentation Coverage** — doc ratio by file type
- **Data File Profiling** — column counts, row counts, null rates for CSV/Parquet/etc.
- **API Structure** — detected API endpoints, OpenAPI specs
- **Catalog & Survey in Egeria** — register in Egeria and trigger native survey

Assessment analyses are marked ⏱ (minutes) or ⏳ (async for Egeria-native). Results appear in the Survey Report with a ☁ or 🏠 source badge indicating where the data came from.

### Analysis
*Goal: structural/quantitative analysis of a specific resource — fast, not scored*

Select a resource, open **Analyses**, and choose the Analysis intent. Unlike Assessment, these analyses don't evaluate the resource against criteria — they extract and report structure. This is where **Architecture Recovery** lives.

#### Architecture Recovery

Architecture Recovery reads a repository and proposes a **candidate component partition**: a set of components (each a path prefix within the repo), where the evidence for each one came from, and how confident the tool is. It draws on four kinds of signal — package manifests (`pyproject.toml`, `package.json`), deployment artifacts (`Dockerfile`, `docker-compose*.yml`), code markers found by scanning the source (web-framework routes, entry points, scheduler/worker decorators, and similar), and coupling between modules (who imports whom, what tends to change together).

**This is a proposal, not a published blueprint.** It is one tool's best current read of the repo's structure, with confidence and evidence attached to every claim — not an authoritative statement of what the architecture *is*. Treat a low-confidence or single-source component as a suggestion worth checking, not a fact to build on. If a component looks wrong, that's useful information about the repo (or about the detector), not a bug report waiting to happen.

**Results can be partial.** You can run Architecture Recovery scoped to just one cataloged sub-resource (via the "Cataloged Sub-Resources — Scoped Analysis" row on a repo's report) instead of the whole repo. A scoped run only ever sees the files under that path, so its component set is incomplete *by construction* — not because the detectors missed anything. A whole-repo run of a large monorepo might report 27 components; the same detectors scoped to one package inside it might reasonably report 3. If a component count looks surprisingly small for the size of the repo, check whether the run was scoped to a subtree before concluding the rest of the repo has no structure worth finding.

**Component "shapes."** Components proposed from import and co-change coupling (as opposed to a manifest or a deployment file) are labeled with a shape that explains why the tool believes they're a real boundary:

- **cohesive** — the files in this component mostly import each other and rarely reach outside it. The intuitive case.
- **connective-library** — internal cohesion is low, but that's because many *other* components depend on it, evenly. A shared library legitimately has almost no internal cohesion of its own — it's a hub, not a cluster — and this shape is how the tool tells that apart from noise. (Resource Explorer's own `Core` module is a real example: 27 internal edges against 238 incoming edges from 14 different callers.)
- **connective-orchestrator** — the mirror image: this component reaches out to many others rather than being reached into. Typical of a CLI, a web front end, or anything whose job is to coordinate the rest of the system.

**`proposed_by` tells you how a component was found**, and by how many independent approaches: `manifest` (a package declares an entry point or build system), `deployment` (a Dockerfile or compose service names it), `code marker` (a structural scan found a framework or entry-point signature), or `coupling` (import/co-change analysis inferred a boundary the other three couldn't see, with the shape noted above). A component that shows up under two or more approaches has stronger, independently corroborated evidence than one that shows up under only one — even though the confidence number itself doesn't currently rise just from agreement, so read the `proposed_by` list, not only the percentage, when judging how solid a component is.

**Confidence** is an integer from 0 to 100, shown per component. It reflects how directly the evidence establishes the claim, not how important the component is: a Dockerfile with a declared, human-chosen container name scores higher than one inferred from a bare service key; a manifest with an explicit console entry point scores higher than a package that's merely installable with no entry point; a coupling-derived shape (cohesive, connective-library, connective-orchestrator) generally scores lower than a component backed by a stated deployment or manifest artifact, because it's inferred rather than declared.

### Discovery
*Goal: find resources by what surveys revealed*

Use the **Chat** panel to ask questions based on survey metadata:
- "Find databases with more than 50 tables"
- "Which repos have security annotations?"
- "Show me databases where the steward is unset"

### Enrichment
*Goal: provide human context; answer open RFAs*

Open the **RFAs** panel to see open RequestForAction annotations. These are generated automatically when:
- A survey creates a `RequestForAction` Egeria annotation
- The Context form has blank critical fields (environment, sensitivity, responsible steward, org owner)

Answer RFAs by clicking them and filling in the requested information. Answers are stored locally; Egeria write-back is coming in a future release.

The **Context** panel lets you fill in resource context proactively:

| Field | Why it matters |
|-------|---------------|
| Environment | prod / staging / dev — used for risk scoring |
| Org owner | Which team owns this resource |
| Responsible steward | Who to contact for data questions |
| Sensitivity | Public / Internal / Confidential / Restricted |
| Purpose | What this resource is used for |
| Geographic location | Compliance and data residency |
| Backup status | Recovery planning |

### Understanding
*Goal: visualize trends over time*

Charts for a resource's history — stars, commits, schema growth, and similar — rendered from the **Survey History** data described below. Fast, read-only.

### Curate
*Goal: decide what is published to Egeria, and make a resource easier to find and more trustworthy to reuse*

For a repository or a database, Curate has **three tabs**, split by job. It always opens on the first.

1. **Describe and publish**: describe the resource (tags, group, ratings and notes) and publish its report.
   The one **Publish** button lives at the bottom of this tab.
2. **Contents**: choose what is published. You pick files, folders, schemas and tables by name; each row has an
   **Include | Leave out** choice, saved in RE as soon as you press it. For a database you also choose how deep
   the catalogue goes. Nothing is taken wholesale.
3. **Structure**: what the resource is made of and how it relates: components, blueprints, dependencies and the
   rules. Accepting or rejecting a proposal here is a decision; it writes nothing to Egeria on its own.

Deciding and sending are separate. Everything on these tabs saves in RE first. Egeria changes only when you
press **Publish**, and Publish lists what it is about to write.

**One Publish per page.** **Decision (project owner, 2026-10-11):** "I think there should be one publish button, but each section can have a save or indication that it will be published when the user presses publish." On a repository's first Curate tab (Describe and publish), every section saves in RE only, and shows where it stands as a mark and a short word: **saved · will publish**, **published 10-11**, **not sent · <reason>**, or, for someone else's rating or note, **<person>'s · waiting for them**. The **Publish** band is the last thing on the tab, and one press sends:

1. the survey report;
2. the tags and the group (below);
3. documentation sources not yet in Egeria (as ExternalReferences on the asset);
4. **your own** rating and notes, written in Egeria **as you**. Never anyone else's: another person's rating and notes wait for their own Publish.

The band has three lines. Line 1 is one state: **✓ in Egeria · published 10-11 · confirmed** when Egeria's read-back after the publish shows the same report with the same number of annotations; **published 10-11 · not yet confirmed** when there is no read-back yet; **◐ published 10-11 · Egeria shows N** when the read-back differs. **Show in Egeria ▸** opens the report rows Egeria holds (GUID, annotations, Ask about this). Line 2, **next publish**, names the kept survey (and how much of it is stale) and lists what the press will send. Line 3 holds **Publish** and **Re-survey now**. A press shows **◔ publishing…** at once; if anything fails, a pop-up says what and why (with **Retry** where a retry can help), and the failed item keeps its "not sent" mark after the pop-up closes. The steps of the press are behind **details ▸**.

**Ratings and notes go to Egeria, public, under your name.** **Decision (project owner, 2026-10-11):** "It goes under the user who is logged in and entering the ratings etc. It is also possible for another user to login and evaluate the same resource and put in different feedback." / "Ratings and notes should be public too."

- **One rating per person.** Your newest rating (stars and message) is your Egeria Rating on the asset; a newer one replaces yours there, as Egeria keeps one rating per person per element. Your earlier ratings stay in RE under **earlier ratings ▸**; RE never deletes them. A comment without stars stays in RE ("kept in RE · no rating").
- **Each journal entry is one Note** in the asset's note log ("Notes on <resource>"). Notes are never edited or deleted: a correction is a later entry. Who you suggested an entry to stays in RE (it is a work-list entry, not Egeria content).
- If Egeria refuses or cannot be reached, the item reads **not sent · <reason>** (or **not permitted · <reason>**) and is sent again only by your next Publish.

**Tags and the group go to Egeria** (the Findable band). **Decision (project owner, 2026-10-10):** "tags should be public", "Use Folio", "Yes, removing a tag removes it in Egeria".

- A tag becomes a **public InformalTag** linked to the resource's asset. RE looks for a tag of that name first and uses it, so everyone's "sales" is one tag in Egeria, not one per person. A new tag keeps the capitals you typed.
- Only a person's Publish sends tags and groups; background publishes (scheduled analyses and the like) never do. If you may not make one of the changes waiting, that item reads **not permitted · <reason>** and the rest still go.
- The resource's **RE group** becomes a **Folio** named `Folio::RE::group::<group>`, with the resource as a member. Changing the group moves the membership at the next Publish; choosing "Ungrouped" removes it.
- **Removing a tag removes that tag from the resource in Egeria** at the next Publish, including the same tag added to it directly in Egeria by anyone: Egeria removes every link between the tag and the resource. The tag itself stays in Egeria, and so does a Folio when you leave or delete a group: RE never deletes either. The owner deletes those elements by hand.
- A failed removal is never sent again on its own, not even by a later Publish: press **retry** on it. Changing a tag or the group needs the same curation rights as other Curate decisions (see the admin guide, "Who may curate and publish"); so does deleting a group, which is refused as a whole, naming who, if any member may not leave its Folio.
- Group changes from the CLI (`group assign`, `group unassign`, `group remove`) and from a CSV import need you signed in and take the same check; Egeria's Folio follows at the resource's next Publish.

### Automate
*Goal: get notified when an analysis's results change on a future run*

Subscribe to an analysis from its card; when a scheduled re-run produces a materially different result, an RFA shows up in the drawer. Has its own **⏱ Schedules** and **🔔 Subscriptions** sub-tabs.

---

## Publishing to Egeria

Publishing is always a **person's act**: a button press by someone signed in, and the changes are written as
that person. Nothing publishes itself because you accepted a finding or made a choice.

What a press can send, by where it comes from:

| From | What Egeria receives | Where you press |
|---|---|---|
| The survey you already kept | A survey report on the resource's asset, with its annotations (measurements and observations) | Curate, **Describe and publish**, bottom |
| Tags and group | Public tags and a Folio for the group | Same Publish |
| Documentation sources | External references on the asset | Same Publish |
| Your ratings and notes | Your rating and your notes on the asset, under your name | Same Publish |
| Files, folders, schemas and tables you included | Each becomes its own asset related to the resource | Chosen on **Contents**; a repository's items go with the same Publish, a database has its own commit panel on that tab |

Three things to know:

- **No zone is added by RE.** What you publish follows your deployment's normal rules unless its administrator
  configured a zone. A private investigation's output is the exception (see "Private investigations").
- **Draft is a status, not a place.** A finding you have not accepted is a draft; it is not held in a separate
  zone. The old draft zone was retired on 2026-10-08.
- **Egeria rolls forward.** RE does not undo a publish. Removing a tag removes its link; a corrected note is a
  later note; elements RE created stay in Egeria until the owner deletes them by hand.

After a press, the page shows a short cue (**publishing…**) and then what Egeria reports back (**✓ in Egeria ·
published 10-11 · confirmed**, or a plain statement of what failed). A word on screen comes from what was
read back from Egeria, never from the fact that you pressed a button.

---

## Steps that run by themselves

Some questions can only be answered after an earlier step has run. For example, profiling a database's columns
needs its schema inventory first. When you ask, Resource Explorer works out what is missing and handles it for
you in one of two ways, and **your own choice of tier is the consent**:

- **Cheap and within what you asked for: it just runs.** The row afterwards says what happened, for example
  *Ran postgres_schema_and_stats first, then postgres_column_profile.* If a fresh stored result already
  exists, nothing extra runs.
- **Costlier than what you asked for: it asks first.** The row shows *Answering this needs step → step first*,
  the estimated time (and whether that is measured from earlier runs or only declared), and why it is asking.
  Press **Run it** to go ahead, or **not now — nothing has run** to leave it.

If the account for a database can only see part of it, the choice reads **Run it anyway — and say so**; the
result is then marked as measured within that account's reach, never as the whole database. **ask for broader
access** raises a request to the database owner.

---

## Live status

Things you start keep reporting back without you reloading:

- **Activity badge.** The Activity link in the header shows **N new** when entries are newer than the last one
  you looked at. It checks every 30 seconds. The first time on a browser nothing is flagged, so it does not
  open with a large count.
- **Toast.** When a run or batch finishes (or fails) while you are elsewhere, a small notice appears with the
  summary and a **View** button that opens the entry. It closes itself after a few seconds.
- **Imports and runs.** A batch import follows its own rows and updates the sidebar as each repository lands. A
  question whose analysis is running shows ◔ *running* and fills in when the result is stored.
- **Banners.** If the server or its database stops answering, a banner says so in a few words ("Server not
  responding", "Database unreachable"). If your session lapses a single **Signed out** banner with a
  **Sign in** button replaces the errors each pane would otherwise show.

Every status word comes from rows RE stored, not from the fact that a button was pressed.

---

## The Analytic Library

The **Analytic Library** is a set of small, installable analyses that sit beside Resource Explorer's own. Each
one is either a **routine** (it reads and counts) or a **step** (it can be part of a survey). Every result
carries its own **state word**, one of *measured*, *nothing found*, *not established* or *not applicable*,
so "we looked and found nothing" never looks like "we could not look". The library's guide for people who
write them is [`../../trellis-analytics/README.md`](../../trellis-analytics/README.md).

Where you meet it:

- **Curate → By analysis** (for a database) shows **curation coverage**: how many of the database's schemas are
  published, not yet published, or blocked, with a **which** link that lists the names. The state word sits
  beside the counts. If the count could not be made, you see the state and the reason, not a table of zeros.
- **The Egeria Portal** can show the same numbers as a tile named **Trellis - Curation Coverage** (family
  *Curation*): a bar of published against not yet published, with the result's state as the subtitle and a line
  saying which version produced it and when. An administrator adds it to the Portal (see the library's
  README); it needs no extra work from you.
- **Other steps** an administrator installs show up as extra analyses on the database, tagged with the stage
  they belong to.
- **From Egeria**, once the owner has declared a step to Resource Explorer's engine (for example
  `ResourceExplorerSurvey::re-database-staleness`), starting it on a database in Egeria runs the step in Resource
  Explorer. The results appear as a survey report in Egeria and in the database's Understanding list, beside
  *Resource Explorer: derived checks*.

---

## Files and File Systems

Resource Explorer supports traversing local folders and data files (CSV, Parquet, Excel), profiling their schemas, and cataloging them in Egeria.

### Mount Point Translation
Egeria captures filesystem locations using two distinct mount points to allow separate services to align metadata:
*   **Local Mount Point** — the absolute root path as seen on the disk where the Resource Explorer is running (e.g., `/Users/dwolfson/localGit/data`).
*   **Canonical Mount Point** — the logical path used in Egeria's catalog so that different containerized/remote components agree on the resource's identity (e.g., `file://shared-nfs/data`).

During survey execution, file paths are automatically translated using:
$$\text{canonical\_path} = \text{canonicalMountPoint} + \text{relative\_path\_from\_localMountPoint}$$

### Walk & Data Profiling
Running a survey walks the local directory structure (excluding noise directories like `.git`, `node_modules`, `.venv`) and uses Pandas and PyArrow to profile data schemas. Profiled details include:
*   File format and file sizes.
*   Row and column counts.
*   Detailed schemas showing column names, datatypes, and null percentage rates.

### Egeria Publishing & Integration (file systems)
Publishing a filesystem survey performs direct cataloging:
*   **Compensation Layer:** Since Egeria repository services fail to recognize the default `FileSystem` classification type during standard template instantiation on some platforms, Resource Explorer catalogs the filesystem root folder as a root `DataFolder` asset.
*   Data files are registered as `DataFile` variants (`CSV Data File`, `Parquet Data File`, `Spreadsheet Data File`, or generic `File`) using the computed `canonical_path`.
*   A `SurveyReport` is published under the root asset, containing a `ResourceMeasureAnnotation` (for overall folder stats) and a `SchemaAnalysisAnnotation` for each profiled data file.

---

## Survey Report

The Survey Report shows the current state of the selected resource. Each section has a source badge:
- **☁ Egeria** — data from the Egeria native survey
- **🏠 Local** — data from the local Python/SQL scan
- **⏳ Pending** — analysis triggered; results not yet available

The **Survey Analyses** section at the top of the report lists which analyses ran during the last survey execution and how many annotations each produced — making the survey's composition visible. This aligns with Egeria's model: a survey is a collection of analyses; each analysis produces annotations; the report is all annotations from one execution instance.

If two or more survey runs exist, a **"Changes since last run"** banner appears at the top showing what changed (file counts for repos; schema/table/column deltas for databases).

Below the main report, a **Survey History** chart shows key metrics over time.

---

## Database access and credentials

Resource Explorer surveys a database with **the account you give it**, and
its answers are only as complete as that account can see. Two things follow.

**What to supply when you register a database.** A read-only account that
can see the catalog and statistics. Table data access is not needed yet:
Scouting and Discovery work from the catalog alone (structure, keys,
comments, row estimates, activity), and Resource Explorer asks for a
broader account only when a specific survey needs to read rows, telling you
which one and why. Passwords are encrypted at rest and never shown back.

**The credential banner.** After registration Resource Explorer probes what
the account can see and shows it persistently on the database, for example
*"Connected as egeria_user — sees 6 of 8 schemas; can read 3 of 26 tables;
no write access."* Every answer on that database then says whether it was
measured completely or **measured within what this account can see**. A
count of "3 tables" from an account that can read 3 of 26 is reported as
"3 of 26", never as "3".

**When a survey needs more.** A survey that must read rows (column
profiling, data-class matching, coverage checks) is not run silently with
an account that cannot. Its row says what it needs and offers three
choices: run only the parts the current account can answer, pick a broader
account already registered for this database, or send a request to the
database owner naming the exact grant. Retry appears only when retrying
could change the answer.

**Estimates versus actuals.** Numbers taken from the database's own
statistics are estimates as of the last time the database refreshed them,
and are labelled with that date and how many rows have changed since.
Exact counts come from the later, heavier surveys. See
[`docs/security-model.md`](security-model.md) for the full model,
including how Egeria's own surveys and Resource Explorer's local surveys
choose which account to use.

## Analyses Panel

The Analyses panel lists all available analyses for the selected resource type, filterable by:
- **Intent**: All / Scouting / Discovery / Assessment / Analysis / Enrichment / Understanding / Curate / Automate  *(separate row)*
- **Perspective**: All / DBA / Data Scientist / Steward / Security  *(separate row)*

★ Recommended analyses are highlighted. Each card shows the intent tag, speed (⚡ fast / ⏱ minutes / ⏳ async), and the annotation count from the last survey run.

At the bottom of the panel, the **Schedule** section lets you configure recurring runs:
- Select an interval (manual / daily / weekly / monthly)
- Toggle enabled/disabled per analysis
- Changes are saved immediately and the background scheduler picks them up

---

## Activity Log

The **📊 Activity** link in the header opens the persistent activity log. Every operation writes an entry:
- **scout** / **survey** / **catalog** / **publish** / **rfa** / **refresh**
- Each entry shows status (ok / error / pending), summary, and expandable annotation details

---

## Chat Panel

The Chat panel provides RAG-backed Q&A scoped to the selected resource (or all resources if none is selected). Queries are classified by intent and routed to the appropriate agent:

| Intent | Agent | What it does |
|--------|-------|-------------|
| survey_meta | SurveyMetaAgent | Questions about surveys, schedules, RFAs, and resource context |
| statistical | StatsAgent | GitHub metrics, commit trends |
| comparison | CompareAgent | Side-by-side project comparison |
| examples | ExamplesAgent | Generates runnable Python code |
| code_search | CodeAgent | Searches code collections in pgvector |
| health | HealthAgent | Community health metrics |
| conceptual | DocAgent | Architecture, documentation |
| general | RAG | Searches all relevant collections |

`survey_meta` is checked first; use it for questions like "when was this last surveyed?", "what analyses are scheduled?", "what RFAs are open?", or "what is the sensitive data annotation for this database?".

Prefix your question with the resource slug to scope it: `mydb: how many tables are in the public schema?`

---

## ⚙ Admin

Reachable from the header, not the intent nav — this is system/catalog configuration, not something you do to curate one resource.
- **Annotation Types** — the registry of metadata annotation schemas: what each one means, what properties it carries, and its mapping to Egeria's own property classes. Register, edit, or delete entries here.
- **Groups** — group related repos/databases/filesystems together (e.g. everything belonging to one product). Create and delete groups here; assigning an individual resource to a group is done from that resource's own record, not from this list. Each group with members is a Folio in Egeria (see Curate). Deleting a group removes each member from the Folio at that member's next Publish; the Folio itself stays in Egeria, and the owner deletes it by hand.
- **Schedules** — a monitoring overview, not an editor: every scheduled analysis across every resource, whether its last run succeeded (✓ ok) or failed (⚠ error, click to see the detail), when it last/next runs, and a 🗑️ to remove a stale schedule. To *set or change* a cadence, use the ⏱ Schedule action on the analysis card itself (Assessment/Analysis/Discovery) — this page is for watching everything at once, especially for errors that need follow-up. **Read the caveat shown at the top of this pane** — not every listed analysis is fully implemented yet; cross-check against Discovery's Survey Definitions before assuming a scheduled run does what its name suggests.
- **Discovery Sources** — named, reusable "where do we scout" configs, picked from Scouting's Discover sub-tab instead of re-typing a search every time. Create either a **search**-type source (the same filters the ad-hoc form uses) or a **list**-type source (a pasted set of GitHub URLs — for foundations whose projects live in many separate orgs, or your own enterprise repos). Delete a source here; there's no edit-in-place yet — delete and recreate.

## The RFA drawer

Click **📝 RFAs** in the header to open it. It lists open RequestForAction items — things a survey or the Context form flagged as needing a human answer — grouped by resource, with a badge on the header button showing how many are currently open.

Each item has three response actions, plus a free-text answer field:

- **⏸ Defer** — pick a date to revisit it later. It drops out of the default (open-only) view until then.
- **👤 Reassign** — hand it to someone else by name or email.
- **✓ Complete** — mark it resolved, optionally with a note explaining the resolution.
- **↺ Reopen** — bring a deferred/reassigned/completed item back to open, if needed.
- **✏ Record answer** — a free-text box for capturing the actual answer to whatever the RFA is asking, independent of its response-action status.

Check **Show resolved** at the top of the drawer to see deferred/reassigned/completed items alongside the open ones — they're hidden by default so the drawer reflects what still needs attention, not the full history.

**One thing this doesn't do yet:** none of these response actions write back to Egeria as a native `ToDo` or governance action, or update the RFA's own properties there. They're recorded locally in Resource Explorer only. If your workflow depends on Egeria itself reflecting an RFA's status, that's not there yet — track it separately for now.

## Tips

- **Column widths are resizable.** Drag the thin divider between the sidebar and main content, or between the main content and the chat panel / RFA drawer, to resize. Your chosen widths are remembered across reloads.
- **The feedback button** (bottom-right, 💬 Feedback) is draggable if it's ever in your way — click and drag it anywhere on screen; its new position is remembered.
- **The header shows who you are signed in as.** Everything you do runs as that Egeria user (see "Signing in"); the ⏻ button next to it signs you out.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit chat message |
| `Esc` | Clear chat input |

---

## CLI reference

```bash
# Sign in (needed by anything that writes to Egeria)
uv run resource-explorer login

# Survey a repo
uv run resource-explorer survey my-repo

# Survey and publish to Egeria
uv run resource-explorer survey my-repo --publish

# Register, list, and survey filesystems
uv run resource-explorer filesystem register my-data --local-path /Users/dwolfson/localGit/data --canonical-path file://shared-nfs/data
uv run resource-explorer filesystem list
uv run resource-explorer filesystem survey my-data            # Local walk and profiling
uv run resource-explorer filesystem survey my-data --egeria   # Survey and publish to Egeria

# Interactive chat
uv run resource-explorer chat

# Ask a one-shot question
uv run resource-explorer ask "how many files does my-repo have?"
```
