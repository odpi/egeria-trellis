# Ruling: two catalog decisions — and why neither changes what a database row says today

**Replying to:** `ASK-DB-QUESTION-CATALOG-CONSISTENCY.md`
**Read against:** `main` at `346595cc`, with the render path read end to end
**Date:** 2026-09-23 · No drawing.

---

## The answers, in the requested sentences

1. **Neither retire nor gap.** "Who owns this" becomes `mixed` for databases and
   `human` for filesystem, dataset and model, because RE already holds both
   halves of the answer in two places the catalog doesn't know about. And it
   is not a one-off: licence is the same case, already in the audit under a
   different heading.
2. **One bar: `analysis` for all three.** `kind` states the mechanism, and the
   mechanism exists. Whether a results reader is wired is plumbing, and it
   should be worked out at runtime from the map, not written into the CSV by
   hand. But `analysis` isn't an honest label yet either, until one branch in
   `facts.py` stops describing these three as actions.

Both are downstream of §0, which is the most important thing in this reply.

---

## 0 · First: neither decision is visible on a database row today

I read the whole path from the `/next` Questions row down to the fact, because a
catalog ruling is only as honest as the sentence it renders.

- `/next` answers each row via `getAnswer()` → `GET
  /api/analyses/facts/{slug}/answer?entity_type=…` (`re-api.js:245`).
- The route now passes `entity_type` into the **catalog** lookup — its
  docstring explains the fix — but it builds the fact layer as **`FactLayer()`**
  (`analyses.py:479`). The default is `DEFAULT_RESOURCE_TYPE = "repo"`
  (`resource_types.py:62`).
- Separately, the **database adapter declares no `analysis_results_map`**.
  `ResourceTypeAdapter.analysis_results_map` defaults to `None`
  (`survey_definition_executor.py:107`), and only the repo adapter sets it
  (`repo_survey_definition_adapter.py:5097`). `DATABASE_ANALYSIS_RESULTS_MAP`
  exists and is used, but only by By analysis and `question_has_data`, not by
  the fact layer.

The second half is **deliberate and tested**: `test_fact_layer_resource_type_dispatch.py:100`
pins *"A database today: an adapter exists, and it declares no fact maps"*, and
checks that `FactLayer(resource_type="database")` says so honestly. **But the
route never builds that FactLayer**, so the tested honest sentence is never
reached from the screen. A database question is answered from the **repo's**
results map.

**What that means for this ask (from reading the code; I couldn't run it — the
repo's venv is macOS-native):** every database `kind: analysis` row answers out
of a map that holds none of the database's analyses. That includes the 14 that
*do* have readers. #229's eleven `gap`→`analysis` fixes changed a label and
nothing else on screen, and either bar in this ask would too.

**Check with one request:** `GET /api/analyses/facts/<a-surveyed-db>/answer?question=<a
schema_inventory question>&entity_type=database` should come back with facts.
By my reading, today it won't.

**The fix is two lines, and it comes before any relabelling:** pass
`resource_type=entity_type` into `FactLayer` at `analyses.py:479`, and declare
`analysis_results_map=lambda: DATABASE_ANALYSIS_RESULTS_MAP` on the database
adapter. That flips the pinned test on purpose: its docstring says "today".

The route's three other `FactLayer()` calls (`:346` and `:384` in `GET /facts`,
`:446` in `GET /facts/{slug}`) have a related but different problem. Those
routes take no entity type at all, so they aren't dropping one. It's a
signature change rather than a one-word fix, and it only matters if something
calls them for a database. Worth checking; not part of this ruling.

The audit isn't at fault here. Its §1.3 checked the catalog with the catalog
tests, which is what a catalog audit checks. Nothing in it rendered a database
row, so nothing in it could have seen this.

---

## 1 · "Who owns this" — neither retire nor gap

### Retiring is wrong

Retirement means *this question no longer applies*. For a database, "who is
accountable, and who administers it" is one of the questions that matters most.
The Steward, Data Owner and Security perspectives all depend on it. What doesn't
apply is the **mechanism**, and that's a separate thing.

### `gap` is wrong too, because RE already knows both halves

`gap` renders *"No mechanism exists for this yet — no analysis produces it"*
(`facts.py`, `UNANSWERABLE_KINDS`). For a database, both halves already exist:

| half | where RE already has it | what happens to it |
|---|---|---|
| **who administers it** | `pg_database.datdba`, read by `list_databases()` (`connection.py:326`) | **shown** at discovery (`db_servers.py:175`), then **dropped** at registration, since the `databases` table has no owner column. Measured, displayed, discarded |
| **who is accountable** | `ContextData.org_owner` / `responsible_steward` (`context.py`), and `enrichment["owner"]` with its `interim` flag | **written to Egeria** by `curate_plan.py:252`, and never connected to the question. `ContextData`'s own comment says none of its fields appears in the catalog |

That's the two-sources-of-truth shape again: the question and the form record
the same fact and don't know about each other.

So: **database → `mixed`** (a measured admin half plus a human accountable
half). **Filesystem, dataset, model → `human`** for now. Nothing reads POSIX
ownership (I checked), so there's no measured half for them to claim.

**Four types, not two.** The row is `Resource Types: *`, so dataset and model
carry the same broken entry.

**For the admin half, the cheapest source is already open.** `privilege_audit`
now reads `pg_class` (`e5e79ce7`), and `relowner` is on the same row. Read it
**directly**, not through the ACL join, because `connection.py:572` filters
`relacl IS NOT NULL`. That excludes exactly the default-privilege tables, where
only the owner has access, which is the ownership case at its plainest. Keep
`datdba` at registration as well.

### The more visible defect is the rationale, not the note

On a database this row renders `answering_mechanism: Git Statistics` and a
rationale about `project_commits`. The rationale is what gets *"carried to the
UI"* as what the answer can and cannot claim. A database reader is being told
what a git history can prove about their database.

### It's a schema decision, and one tempting fix is a trap

Every option in the ask needs per-type answering, and the CSV has one
`Answering Analysis`, one `Answering Mechanism`, one `Rationale` and one
`Status` per row. So this is a schema question, not a content one.

**Don't split the row into a repo copy and a non-repo copy.** The writer
assumes one row per question text in two places. `add_question`
(`question_catalog_writer.py:175`) rejects duplicate text, and `retire_question`
(`:231`) retires the **first** match. With two rows sharing text, Admin's retire
button retires one and silently leaves the other live, which is the exact
silent-omission shape.

So per-type answering needs a small generator/CSV extension. The generator is
most of the way there: `_restrict_answering_to_type` already keeps only the
in-type analysis ids for each type. What it lacks is per-type
`kind`/mechanism/rationale. I'll leave the exact shape to the implementer.

### It generalizes, and the second case is already in the audit

`_restrict_answering_to_type` auto-downgrades **exactly two rows per non-repo
type**, the same two for database, filesystem, dataset and model:

1. Who owns this resource
2. **What explicit licence does this resource use**

The second is the audit's **§2.6 "bigger design question"**, filed as though it
were a different problem. It's the same shape and gets the same ruling:
`curate_plan.py:253` reads `enrichment["licence"]` on the line after `owner`. In
the code that writes to Egeria, both are Enrichment facts. In the catalog, both
are repo analyses.

**How to find more:** grep the generator's own downgrade text, `not a real
analysis for <type> resources`. Treat it as a **lower bound**, since it only
catches `kind: analysis` rows. I also checked the database section for
repo-native mechanisms. Only ownership is one. "Code Analysis" on
views/functions/triggers is right for a database, because it means SQL parsing.

---

## 2 · The bar: `analysis` for all three — for a different reason than the ask gives

### The `repository_health` precedent doesn't match

`repository_health` has no results reader, but it has a **special-case signal**
(`scouting.py`, `question_has_data`, via `project_stats`) that returns `True`
when data exists. The three siblings have no signal. For an id not in the map,
`question_has_data` falls through to **`False`**, meaning *checked and found
nothing*. Its own docstring distinguishes that from `None` (*nothing to check
with*). So the looser bar can't lean on this precedent; the dispositions are
different.

### `gap` is false for all three

It renders *"No mechanism exists for this yet — no analysis produces it."* All
three run and publish. `data_class_match`'s DataClass annotations are visible in
classic's Egeria pane with the DRAFT badge.

### So `kind` states the mechanism; the plumbing is worked out at runtime

The stricter bar writes plumbing state ("is a reader wired?") into a CSV that
people edit by hand, and nobody edits it the day a reader lands. **That's how
stale gaps get made**, and #229 was a PR to fix eleven of them. A disposition
worked out from `*_ANALYSIS_RESULTS_MAP` at runtime can't go stale, because it
changes the moment the map does.

### But `analysis` isn't honest yet either: one branch describes these three as actions

`fact()`'s no-reader branch (`facts.py:750-758`) was written for
`egeria_publish` and explains itself on those terms:

> *"No results are stored for this analysis — it produces an action rather than
> findings."*

That's false for all three. They produce findings, not actions. The fix uses
data the catalog already carries:

| catalog `action` | the fact's note | `can_run` |
|---|---|---|
| `publish` (e.g. `egeria_publish`) | today's sentence, which is true for it | as today |
| `survey`, no reader | *"Measured by `<id>` and published to Egeria; its results are not readable here yet."* | **empty**: running again can't change what this row shows |

The empty `can_run` matters. `_resource_state_fact`'s own docstring calls
*"run X to find out" when nothing would change the answer* the false offer the
envelope exists to prevent.

Also make `question_has_data` return **`None`** rather than `False` for an id
with no reader. That's the same distinction its docstring already draws for an
empty id list.

---

## 3 · Order

1. **§0 first**: route and adapter. Until then, relabelling changes nothing on
   screen.
2. **§2's branch**: the `survey`-with-no-reader sentence, and `None` in
   `question_has_data`.
3. **Then relabel**: `reference_data_match` and `nested_column_profile` →
   `analysis`; ownership → `mixed`/`human` per type once the generator can say
   it per type; licence the same way.

If step 3 lands before steps 1 and 2, the catalog is more correct and the screen
is exactly as wrong as it is today.
