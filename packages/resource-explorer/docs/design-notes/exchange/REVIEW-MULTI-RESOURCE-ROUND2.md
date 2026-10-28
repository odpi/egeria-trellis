# Round 2: drawings, and three asks that are migrations

**Replying to:** the round-2 request (slices 6 and 7 landed, PRs #191–#193)
**Read against:** `main` at `9d531b3b`
**Drawings:** canvas pages 9 and 10
**Date:** 2026-09-22

---

## 0 · First, a correction to my own round-1 note

I said the heatmap could not be drawn because its role side was slice 8/9 and
did not exist. **It exists.** `#193` shipped `postgres_operations` — the branch
name was in the merge subject I had already read, and I did not follow it. So
page 10 is drawn after all, and the thing I recorded as a deferral was a
lookup I skipped.

Both of my round-1 schema asks landed, with the reasoning quoted in
`registry.py:449-469`. `stats_source` / `stats_computed_at` are there, and
`get_stats_reset()` is there "so a change comparator can tell a real rate from
the negative delta a counter reset produces." Nothing to add on those.

---

## 1 · The profile card (page 9) — one rendering rule, not a design choice

`distinct_count` is `REAL`, which is correct, because **Postgres' `n_distinct`
is negative when it expresses a ratio rather than a count**. `−0.8` means
*distinct values are about 80% of rows*.

A card that prints the field as a count renders **"distinct: −0.8"** — a
negative cardinality, which cannot happen. It is the same shape as the
counter-reset case from round 1.

> One branch on the sign, at the render site.

**Worth a test**, because the sample data in `coco_pharma` may well contain only
one of the two forms, in which case the bug ships invisible.

The four shipped states are drawn as they are: `measured`, `not_collected`
(ANALYZE never ran — carries a *run ANALYZE* action), `not_supported`
(capability undeclared — **no action offered, because none would help**), and
the backfill's `not_measured`. The distinction between the middle two is the
whole reason `EngineCapabilities` declares only three of seven true, and the
comment in that file is right that declaring the rest early "would recreate the
exact bug this field exists to prevent."

---

## 2 · The exposure heatmap (page 10) — and why its failures are not symmetric

For every other representation in the brief, a missing measurement shows as a
gap and the reader is mildly worse off. For an exposure view it is not
symmetric: **a cell that should say *this role can read this column* and instead
says nothing is read as a clean bill of health.**

So: an empty cell must mean *no path found*, never *no path* — and every path
the survey does not look down has to be named on the surface.

Drawing it against the shipped reads found **three such paths**.

### 2.1 · The cells are table grants wearing column labels — and this one is a migration

`get_privilege_audit()` reads `information_schema.role_table_grants`, which is
**table-level**, and `database_grants` (`registry.py:654`) cannot hold anything
finer: its columns are `schema_name, object_name, object_type, grantee,
privilege_type`, and its `UNIQUE` has no column dimension either.

One direction of that inheritance is sound — a role with `SELECT` on a table
really can read every column in it, so a filled cell is true. The other is not.
Postgres also supports column-level grants:

```sql
GRANT SELECT (email) ON patient TO analyst_ro;
```

and **a column-only grant does not appear in `role_table_grants` at all.** So a
role given access to exactly the sensitive column, and nothing else, renders as
a row of dashes.

**The ask:** read `information_schema.column_privileges` instead. It returns
column-level grants *and* table-level grants already expanded per column, so it
is a strict superset of what is read today — one query, not two — and add
`column_name` to `database_grants` and to its `UNIQUE`.

`column_privileges` appears nowhere in the repo today. This is the one item here
that is cheap now and expensive later, which is what §5 said round 2 was for.

### 2.2 · `PUBLIC` is a pseudo-role, so it cannot be a column

The RFA already gets this right — `database_surveyor.py:649` says a grant to
`PUBLIC` "means every current and future role". But in a per-role grid, `PUBLIC`
as one column among five is actively misleading: a reader scanning
`analyst_ro`'s row for red finds none, while `analyst_ro` can in fact read
everything.

So it is a **band above the grid**, not a column in it. Drawn on page 10.

### 2.3 · Role membership is read nowhere

`privilege_audit` reads `pg_roles` — the roles — but `pg_auth_members`,
`pg_has_role` and `rolinherit` are absent from the whole repo. Granting through
a group role is the normal way to administer Postgres: give `SELECT` to
`readers`, put `analyst_ro` in `readers`, and the grid shows `analyst_ro` with
access to nothing.

Two ways out, and the choice is yours:

| approach | what it costs |
|---|---|
| read `pg_auth_members` and resolve the closure ourselves | a graph walk we own and must keep correct |
| ask `has_column_privilege(role, table, column, 'SELECT')` | one call; Postgres already accounts for membership, inheritance **and** `PUBLIC` |

The second answers the question the grid actually asks — *can this role read
this column* — rather than reconstructing it from three catalogs. It also
subsumes 2.1 and 2.2, so if you take it, those two become one change instead of
three.

**Either way, a filled cell then needs to say which path it came by**, because
*granted directly* and *inherited via `readers`* are revoked in different
places, and a reader who cannot tell will revoke the wrong one. The cell's
detail popover carries it; the grid stays a grid.

### 2.4 · The one part that needs no build

`SECTION_GRANTS` (`registry.py:479`) already stores *this survey never looked at
grants* as a fact. So the not-measured row on page 10 is a read of something
that exists, not a new mechanism.

---

## 3 · What I did not draw, and why

**The treemap.** Its inventory rows need checking against what shipped the same
way the card's fields were, and I have not done that. Drawing it from round 1's
description would be drawing against a guess, which is the thing round 2 exists
to stop. It is next, not dropped.

---

## 4 · The `contentStatus: DRAFT` question

Answered separately in `REPLY-DRAFT-BADGE.md`: it is the middle position on the
**measured / declared** axis the card already carries, not a new badge family.
One vocabulary, one filter with three positions, one legend — and the axis lives
on the *match*, not the column, so any column- or table-level summary has to say
how it aggregates.
