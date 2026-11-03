# REPLY — Designer: discovery sources for every resource kind, and CSV in and out (2026-10-01)

To ASK-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md, read against main at
524f1c7f (`web/routes/discovery.py`, `web/routes/db_servers.py`,
`batch_io.py`, `surveyors/database/connection.py`,
`next/discovery-import.js`, `next/db-server-discovery.js`,
`next/admin/discovery_sources.js`). Drawing:
`wireframes/FindAndImport.dc.html`, canvas page 16.

## First: most of this already exists, in three places

The ask describes one repo-only pane. /next has **three discovery
surfaces**, plus a CSV contract that already covers every kind:

| Where | Kind | What it does |
|---|---|---|
| Admin → Discovery Sources | repo | Saved GitHub `search`/`list` configs; Run → candidates; import |
| Sidebar → **Find repos** (`discovery-import.js`) | repo | Ad-hoc GitHub search, **"load from file"** (`POST /discovery/from-list`, CSV or a plain URL list), **inventory CSV export** (`GET /discovery/inventory.csv`); review rows → "Import selected" |
| Sidebar → **Find databases** (`db-server-discovery.js`) | database | Register a Postgres server once, with its credential; **Discover** lists the databases on it (`DiscoveredDatabase`); add selected |
| `batch_io.py` (CLI and `inventory.csv`) | all three | The CSV contract: intent columns read on import, `status_` columns written on export and **ignored on import**; natural key `resource_key(type, address)`; an `ImportPlan` of to-register / already-registered / unsupported-type / invalid / duplicate-in-file |

So a registered database server is already a saved "list" source, with
its credential held where credentials belong. And `batch_io` already
answers most of questions 3 and 4, including the design session's
position on re-import (idempotent by key). It refuses database and
file-share rows on import on purpose: *"Databases need credentials, which
have no business in a shared CSV"*. That rule is right, and the design
below keeps it.

The generalization is mostly **joining what exists**, not building a
second pane.

## 1. One pane or one per kind

**One place to find things, scoped by the kind switcher, as the sidebar's
Find action already is.** It's (b), arrived at from what's already built:
the Find action already exists per kind (Find repos, Find databases), and
the sidebar's `Repos · DBs · FS` switcher is what scopes it. Each kind's
Find dialog gets the same three tabs:

1. **Saved sources**: the places to look again. For repos, GitHub searches
   and lists. For databases, the **registered servers**. For file systems,
   mount roots, once a lister exists. Each row has **Run**.
2. **New search** (repos) / **Discover on a server** (databases) / **List a
   mount root** (file systems): the same thing, run once, with "save as a
   source".
3. **From a file**: a CSV (§3).

Every tab ends in the **same candidate table for that kind**, and the same
confirm.

Not (c), mixed candidates. A repo's deciding facts (stars, last push) and
a database's (size, owner role) don't share columns. A mixed table is
mostly "not reported" cells, and it can't sort on anything that matters.
**Mixing happens at the scope, not in the candidate list**: each kind's
confirm offers **"Add these N to Customer 360"**, the same act the
work-lists reply ruled (§2 there), so one investigation collects repos and
databases from two Find runs.

## 2. What a candidate row carries, per kind

The rule: **the facts a person needs to decide "import this one", and
nothing the source can't supply cheaply before registration.** A fact the
source didn't return reads "not reported by this source", in muted ink,
never blank and never 0.

**Repository** (unchanged columns): name, owner, description, stars,
language, licence, last push, *already registered*, prior verdict.
`DiscoveredRepo` defaults `stars`, `forks` to 0 and `language`, `license`
to "". A from-list row GitHub didn't fully return then shows "0 stars"
instead of "not reported". These fields become optional, and the row says
which.

**Database** (from a server):

| Column | Source | Note |
|---|---|---|
| name | `pg_database.datname` | |
| server | the registered server | |
| size | `pg_database_size` | Needs CONNECT; otherwise "? not readable with this credential" |
| owner role | `datdba` | |
| description | `shobj_description` | "none set" when empty is measured, not "not reported" |
| connect with this credential | `has_database_privilege(…, 'CONNECT')` | **Shown, not filtered** (see below) |
| already registered | `is_registered` | Dimmed, pre-checked, not re-addable |
| prior verdict | keyed by address | Same as repos (see below) |

**Two defects to fix with it:**

- `list_databases()` **filters out** every database the credential can't
  CONNECT to (`WHERE has_database_privilege(...)`). A steward looking at a
  server sees fewer databases than exist and is never told. They should be
  listed with **"? can't connect with this credential"**, which is exactly
  the minimal-privilege gate the owner described in
  `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md`, and importable as a
  candidate that needs a different credential.
- `size_bytes` defaults to 0 and `owner` to "". Both lose the "not read"
  distinction.

**Last activity is not on the candidate row.** Postgres has no "last
written" timestamp. `pg_stat_database` counters need two readings to
become a rate, which is what `db_change_rates` does after registration. The
column header says "activity · after registration". It doesn't guess.

**Verdict before registration.** Repos can carry a verdict before import
because `repo_dispositions` keys on the URL. Databases key on slug, which
doesn't exist until registration. Key on `resource_key("database",
"host:port/name")` so "ignored" is remembered across discover runs, as it
is for repos.

**File system** (from a mount-root listing, when one exists): path,
reachable from the RE host (exists and readable), last modified of the
root, already registered. File counts and sizes: "counted after
registration". Walking a share to count it *is* the survey, and a
candidate list mustn't cost a survey.

**From a CSV:** the columns the file supplied, and "not reported by this
file" for everything else. A file isn't a probe.

**No fit headline on a candidate.** Fit against a data lens needs measured
inputs, and a candidate isn't measured. The headline appears once the
resource is in scope and scouted. Showing a fit for unregistered rows would
mean scoring names, which the lens design rules out ("never by string").

## 3. CSV in

**Every row is a candidate**, through the same preview-then-confirm as a
Run. `batch_io.plan_import` already computes the preview. The screen is
its five counts, each opening its lines:

> **15 rows** · 3 new · 12 already registered · 0 duplicates in the file ·
> 1 invalid (line 9: no address) · 2 of a kind not importable yet

Then the candidate table for the new rows, then the confirm with the
destination: group, and **"add to <investigation>"** or **"add to a work
list"**.

Mismatches:

- **Unknown column**: accepted, and named once: "Ignored columns: region,
  cost_centre". It isn't an error. Spreadsheets carry extra columns.
- **`status_` columns**: ignored, and said once ("12 status_ columns
  ignored — those are written by RE, never read"), as `batch_io` already
  rules.
- **Missing required column** (`resource_type`, `address`): the file is
  refused, naming the missing column and showing the header row found.
- **Row of another kind**: fine, since the format is multi-kind. A row for
  a kind with no import path yet is counted under "of a kind not
  importable yet", with line numbers, not dropped.

**Re-import is idempotent by `resource_key`**, as the design session
proposes and `batch_io` already does. The same file twice gives "15 rows ·
0 new · 15 already registered". Intent columns on an already-registered
row (group, disposition) are **shown as proposed changes**, "3 rows would
change group", and applied only with the confirm, never silently.

**Databases without credentials in the file.** Database rows become
importable when the CSV names **where the credential comes from**, never
the credential itself. It adds one intent column, `server`, naming a
registered db server (or `connection_ref`, naming a secrets entry). A
database row with neither is a candidate in state **"⚠ needs a person:
name a server or a credential"**, and isn't importable until one is chosen
in the preview. That's the owner's demo: register the regional server
once, then one CSV of `database, host:port/name, server=regional-pg` rows
lands them all as candidates. Nobody types a connection string.

## 4. CSV out

**One column contract for every export.** It's `batch_io`'s: intent columns
then `status_` columns. The exports are:

- a **source's candidates** (from the candidate table),
- a **work list**,
- an **investigation's scope**,
- the **inventory** (exists).

**State goes out as words, in `status_` columns, never glyphs:**
`status_in_scope` (in scope / not in scope), `status_fit` ("3 of 6 fit ·
1 doesn't · 1 couldn't check · 1 no reader yet", the same headline as on
screen), and the existing `status_registered`, `status_cataloged`,
`status_last_surveyed_at`. **Verdict (disposition) is already an intent
column** and stays one. Because `status_` is never read back, exporting a
scope and importing it into another investigation is safe: the fit doesn't
travel as an instruction.

Each export's filename names what it is and when: `re-scope-customer-360-2026-10-01.csv`.

## 5. Where it lives

**In the sidebar's Find action, for all of it.** `SPEC-ACTIONABLE-AND-HONEST.md`
point 2 already ruled that finding and importing happens *before any stage
applies*, so it belongs beside the kind switcher, not in Scouting or any
other stage. That ruling covers runs and triage.

- **Admin → Discovery Sources is retired into the Find dialog's "Saved
  sources" tab.** Admin keeps only what's system configuration: the GitHub
  base URL setting (today a section of the same pane) and, for databases, server registration with its stored
  credential. Registering a server is configuration; discovering on it is a
  person's work.
- **"Seed this investigation from a file"** is on the investigation page:
  Scope's "＋ add…" offers "from a file", which opens the same Find dialog
  on its From-a-file tab, destination preset. One dialog, two doors.

## 6. The name

**Rename now: "Repository discovery sources".** It's one string, and it
stops the label promising databases until the slice lands. When the slice
lands and the pane moves into Find, the label becomes **"Saved sources"**,
under a dialog already titled by kind.

## Slices

1. **Databases**: list non-connectable databases with their mark; the
   optional repo facts; verdict keyed by address; the three-tab Find dialog
   for databases (Saved sources = servers); "Add these N to
   <investigation>".
2. **CSV**: the `server`/`connection_ref` column and database import in
   `batch_io`; the five-count preview in the web UI; exports of candidates,
   work list and scope; the investigation's "from a file" door.

The owner's gate passes at the end of slice 2: one CSV of regional
databases with `server=`, five counts, confirm, and they're in scope.

## Left open

1. File-share listing: is "list the subdirectories of a mount root" the
   right source, or do shares only ever arrive by CSV? No lister exists, so
   until one does, the file-share Find dialog has only From a file.
2. Should a saved source remember its last run's candidates, so Run shows
   "4 new since 09-28"? It's useful for the Coco acquisitions, but it's
   storage the ask left undecided.
3. `ASK-CREDENTIAL-GATING-AND-OMSECRETS-REFRESH.md` is addressed to the
   architecture session, not me. The non-connectable-database mark in §2 is
   the screen half of its gating idea.
