# Review: multi-resource representations, round 1

**Replying to:** `SPEC-MULTI-RESOURCE-REPRESENTATIONS.md`
**Read against:** `main`, and classic's two reports read directly
**Date:** 2026-09-21 · No drawings, per §5.

**One citation correction first**, since §5.2 asks me to cite classic and one of
yours does not resolve: `renderDatabaseSurveyReport` does not exist anywhere in
the repo. The function is **`loadDbSurveyReport`, `index.html:13122`** — your
line number was right, the name was not. `renderFilesystemSurveyReport:14744` is
exact.

---

## 1 · §3's two questions, which are the ones with a deadline

### 1.1 · One card, one shared column set — and the proposed set is missing the field that makes it honest

**One card.** The question is the same, the measurements are the same, and two
cards would duplicate every honesty rule in §4. Share the column set.

**But add two fields, because the thing that differs between DB and FS is not
what is measured — it is whose numbers they are and when they were computed.**

- A Postgres column profile can come from **the database's own statistics**
  (`pg_stats`, maintained by `ANALYZE`). Those cover the whole table, are not
  ours, **can be older than the survey that reported them**, and can be absent
  entirely.
- A data-file column profile is **computed by us at survey time**, possibly over
  a sample.

`sample_strategy`, which you already propose, answers *how much was looked at*.
It does not answer *whose numbers* or *as of when*. So:

| add | holds |
|---|---|
| `stats_source` | the database's own statistics / read by us |
| `stats_computed_at` | `last_analyze` for `pg_stats`; the survey time for a file |

**Why this is not tidiness.** Without it a card can show a null fraction computed
three months ago beside a row count measured two minutes ago, with nothing
saying so — a fast path lying, and the same defect as the publish-state round: a
stored number whose freshness is never checked.

It also makes §4's required state exact rather than special-cased:
*"no statistics collected, run `ANALYZE`"* is simply
`stats_source = database, stats_computed_at = null`. A state, not a blank.

### 1.2 · Per-table, as proposed — and the counters force a third state

**Per-table.** *"How is it changing"* is answered by **which** tables are
changing. Schema-level with drill-down still needs per-table rows to drill into,
so coarsening the table delays the need rather than removing it.

**But `pg_stat_user_tables`' counters are cumulative since the last stats reset**,
and your rates are differences between snapshots. A stats reset — or a failover,
or a restore — makes that difference **negative**, and a small multiple would
render "−40,000 inserts", which is not a thing that can happen.

**So record `stats_reset` alongside the counters.** When it moves between two
snapshots, that interval is not a rate and must not be drawn as one:

> counters were reset in this interval — change cannot be measured here

A third state beside measured and unmeasured, and the only one of the three that
a reader would otherwise see as a wrong number rather than a missing one.

Separately, and not blocking: per-table × per-snapshot is the one series here
with unbounded growth. It wants a retention decision eventually; it does not want
one now.

---

## 2 · The twelve rows

**Nine keep as written.** Three have a change, and one of those is a real catch.

| # | Family | Verdict |
|---|---|---|
| 1 | Inventory / treemap | **keep, with one constraint** — see below |
| 2 | Data model / ER graph | **keep, with a correction** — see below |
| 3 | Column contents / profile card | keep, plus §1.1's two fields |
| 4 | Sensitive exposure / heatmap | keep |
| 5 | Change over time / small multiples | keep, plus §1.2's third state |
| 6 | Lineage / graph | keep — and a view SQLGlot cannot parse is *unparsed*, a state, not an absence of lineage |
| 7 | Sovereignty / map + timeline | **keep, conditionally** — see below |
| 8 | Resilience / status strip with source | keep — the model row in this table |
| 9 | Conformance proposals / review queue | keep, and **reuse rather than build** — see §3 |
| 10 | Readiness / two-sided checklist | keep — and the caveat applies within a side too, not just between them: a composite of ten checks where three are not-established is not a score |
| 11 | Cards / rendered with badges | keep — *"do not re-summarise it, annotate it"* is exactly right |
| 12 | Reachability and cost / a sentence | keep, and build it **from** the existing cost ladder (`#70`, `#75`) rather than anew — a price with a measured basis and a choice is already a settled pattern |

**Row 1, the constraint.** A treemap encodes one quantity as area and another as
colour, and you offer area = rows *or* bytes, colour = kind *or* age band — four
combinations. A treemap whose reader does not know which encoding is live is
worse than the flat list it replaces. So the encoding is a **visible control
stating itself in words** (*area: bytes · colour: age*), not configuration. That
is the classic FS report's real failing, by the way: `renderFilesystemSurveyReport`
(`:14744`) leads with format in mono and never says what the numbers are of.

**Row 2, the correction, and this is the one I would hold up.** FK edges exist
only where foreign keys are **declared**. Plenty of real databases have genuine
relationships and no FK constraints at all — ORMs that manage integrity in
application code, warehouses, anything loaded by bulk ETL. An ER graph drawn from
FKs alone will render such a database as *"a bag of tables"*, which is the exact
question the row is meant to answer, answered wrongly with confidence.

> **No foreign keys are declared** is not **the tables are unrelated.**

The graph must distinguish them, and it should say which it is doing:
*"12 tables, no declared foreign keys — relationships may exist and are not
recorded in the schema."* Inferred relationships (name-matching, SQLGlot join
observation from the views you are already parsing for row 6) can be offered as
a separate, clearly-marked edge kind — but never silently mixed with declared
ones. This is the measured-zero-versus-nothing-found rule in the one place where
getting it wrong looks most authoritative.

**Row 7, the condition.** A map is the most expensive representation in this
table and the least often load-bearing: most databases' honest answer to *where
is the data* is a jurisdiction string, not a bounding box. **Render the map only
when a measured bounding box exists**; otherwise the text carries it and no empty
map is drawn. The measured-versus-declared pair still shows in the timeline
strip, which is the half that earns its space.

---

## 3 · Build once, share with repositories now

Three, and one is not a build at all:

- **The profile card.** `data_file_profiling` already produces its inputs
  (`index.html:3191`, `:3277`, `:4792`), so repositories get it immediately for
  data files inside a repo. Build it once, against the shared column set in §1.1.
- **The treemap.** It serves repositories directly, and there is a live case for
  it: Curate's component tree has 64 of 69 components under a single `packages/`
  branch, where indentation stops reading as hierarchy. That is a treemap
  problem.
- **The review queue — do not build it, reuse it.** Row 9 is structurally the
  component verdict queue one domain over: proposals with evidence, accept /
  dismiss / refine. `RULING-WHAT-A-VERDICT-IS-ABOUT.md` already settled the hard
  parts — show every current proposal rather than the latest, agreement between
  independent detectors outranks a single high confidence, and a withdrawn
  proposal flags an accepted verdict rather than invalidating it. **A conformance
  proposal is the same object**, and all three rules apply unchanged.

---

## 4 · §1, where you invited an objection

Two, both small, neither a disagreement with the substance.

**(a) "Automate is not a stage" needs reconciling with the nav ruling.**
`RULING-NAV-GROUPING.md` (2026-09-18, on the owner's ruling) places Automate in
the **cross-cutting** group — still in the nav, as a place. Your §1 says it is
not a stage and that schedule/subscribe/history are affordances on every
analysis, with the `automate` tab surviving as where they are *managed*. Those
are reconcilable, and they are currently two sentences rather than one. Worth
settling in one place before either surface is built, or `STAGES` and this brief
will disagree — which is the two-sources-of-truth shape that has already cost
this project three defects.

**(b) "'vs. last time' is always available" is not true of the first snapshot.**
A minor wording point with a real consequence: as written it would put a
comparison affordance on a resource that has been surveyed once, where there is
nothing to compare to. Your own §4 requires the absent state to be drawn, so the
sentence wants to be *available whenever a previous snapshot exists, and says so
when none does.*

---

## 5 · Nothing else blocks

Everything in §2 other than the two schema answers can be redrawn without a
migration, as you say. Round 2 is the right place for the drawings, and against
real annotations rather than guesses is the right trigger.
