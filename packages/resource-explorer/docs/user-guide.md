# Resource Explorer — User Guide

**Last revised:** 2026-08-21

Resource Explorer discovers, surveys, and catalogs information resources using Egeria as the metadata catalog. This guide covers the web UI, the eight user intents, and how to get the most out of each workflow.

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

## What "owned by you" means

Everything you publish to Egeria carries an **owner** — you — and lands in a *draft zone*
(`resource-explorer-draft`) rather than in the catalogue's normal zones. Two consequences:

- **You can curate what you own.** Accepting, rejecting or materializing your own findings needs
  no extra permission. Curating *someone else's* needs a curator or admin role.
- **Accepting publishes it properly.** When you accept a finding, the element moves out of the
  draft zone into the deployment's normal zones. That move is what "accept" actually does in
  Egeria — it is not just a note in Resource Explorer.

---

## Layout

The web UI has three zones:

- **Left sidebar** — select a resource type (Repos / Databases / Filesystems) and choose a specific resource
- **Main panel** — survey report, analysis menu, discovery results, or RFA panel depending on the active intent tab
- **Header** — global navigation: Survey Report | 📋 RFAs | 🔬 Analyses | 📋 Context | 💬 Chat | 📊 Activity

---

## Eight User Intents

The intent tab strip has eight tabs: **Scouting**, **Discovery**, **Assessment**, **Analysis**, **Enrichment**, **Understanding**, **Curate**, **Automate**. This section covers each in turn; Scouting, Assessment, Discovery, and Enrichment are the original four and are documented in the most depth below, Analysis is covered separately (it's where Architecture Recovery lives), and Understanding, Curate, and Automate get a short pointer each.

### Scouting
*Goal: broad inventory across many resources, fast*

Select **Scouting** in the Analyses panel, then click **Run all scouting analyses**. This runs:
- Language and file classification
- Repository health metrics
- Basic dependency detection

Results appear in the Activity Log and are visible in the Survey Report immediately after the run completes.

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
*Goal: make a resource easier to find and more trustworthy to reuse*

Ongoing curatorial work distinct from Enrichment's one-time/periodic context form: search tags, resource-level feedback, and curator notes, so the next person to find this resource can tell it's been looked at and vouched for.

### Automate
*Goal: get notified when an analysis's results change on a future run*

Subscribe to an analysis from its card; when a scheduled re-run produces a materially different result, an RFA shows up in the drawer. Has its own **⏱ Schedules** and **🔔 Subscriptions** sub-tabs.

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

### Egeria Publishing & Integration
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
- **Groups** — group related repos/databases/filesystems together (e.g. everything belonging to one product). Create and delete groups here; assigning an individual resource to a group is done from that resource's own record, not from this list.
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
- **"Connected as" in the header** shows which Egeria service account Resource Explorer is using — it's informational, not a login control. There's no per-user login yet; the account is configured once via `.env` (`EGERIA_USER_ID`/`EGERIA_USER_PASSWORD`), not from the UI. Click the badge for a reminder of this.

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
