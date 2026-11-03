# Round 3: the DRAFT badge where it lives, and the blank my own ask created

**Replying to:** the round-3 request (slices 9, 10, 12 merged)
**Read against:** `main` at `9bc1c153`
**Drawing:** canvas page 11
**Date:** 2026-09-22

---

## 0 · Both round-2 items landed, and one went further than I asked

`_resolve_n_distinct` (`database_surveyor.py:82`) is three corrections deep, and
**two of the three were not in my review**: that the row count must be the same
`ANALYZE`'s own `reltuples` rather than a live count read separately, and that
`reltuples == 0` is ambiguous on PG13 and earlier because the `-1` sentinel only
exists from PG14. The second in particular would have produced a confident,
wrong `0` on every column of an un-analyzed table — worse than the bug I
reported, and found by taking the report seriously rather than applying it.

And slice 10 found a visibility gap on its own: `contentStatus` was read
nowhere, so a draft proposal rendered exactly like a confirmed finding.

---

## 1 · The badge in classic is right, including a part nobody asked for

`index.html:6660` draws a badge only for a non-empty `contentStatus`, and says
why: an empty value is *not stated*, which is not the same as *confirmed*, so it
must not earn a badge of its own. That is the measured-zero-versus-nothing-found
rule applied unprompted.

It also holds up for a reason worth naming, because it is load-bearing and
undocumented: the other verdicts survive having no badge only because
`ColumnMatch.describe()` already puts the verdict in words — *"not established —
no values were sampled. This is not a statement that no match exists."* **The
badge is a second channel on a distinction the sentence already carries.** If
`describe()` were ever shortened, the badge's absence would start doing work it
cannot do.

One structural note, in the same spirit as the `SCHEDULED_STEPS` catch:
`EgeriaAnnotationItem` now exists in three copies, and
`filesystems.py:81-86` explains that `content_status` was declared on the
filesystem copy — which proposes nothing — precisely so the three cannot drift
under one renderer. Right call. It is still three copies of one model, and the
fourth consumer will not have that comment in front of it.

### The older path really is divergent, in both directions

`AnnotationItem` on `/{slug}/annotations` (`egeria.py:63`) has no
`content_status`. It *does* have `subtype_data`, which `EgeriaAnnotationItem`
lacks. So the two models differ in both directions, and neither is a superset —
worth knowing before anyone tries to collapse them.

---

## 2 · `/next` — permitted today, a silent omission the day the pane is built

`/next` has **no live-Egeria annotation surface at all**: no `content_status`,
no `egeria-surveys`, nowhere. So the badge is not missing from a screen that
shows annotations; the screen does not exist.

By `RULING-CLASSIC-AND-NEXT.md` that is permitted — capability may diverge. It
is not the silent-omission case, because nothing on `/next` currently renders a
proposal as though it were a finding. But it becomes that case on the day that
pane is built, so:

> **`/next`'s annotation pane ships with the status mark on day one, or it does
> not ship.** A pane that lists proposals and findings in one list with nothing
> separating them is the defect slice 10 removed, rebuilt one surface over.

### And amber cannot port — but nothing new is needed

Classic's `bg-amber-500/20 text-amber-400` suits classic's slate chrome. `/next`
has three audited state roles — `ok`, `warn`, `gap` — and the audit already
records that `state-warn` and gold sit within 1.02:1. There is no room for a
fourth, and **there does not need to be one:**

> **DRAFT is not a state.** `ok`/`warn`/`gap` say what we found out. DRAFT says
> *who has agreed to it so far* — the same axis as measured / declared, and
> orthogonal to all three. A near-unique national-id column can be a `warn`
> **and** a draft at the same time.

So it renders as a mark in ink — a small outlined chip — not a colour. The state
colour stays available for the finding; the chip carries the agreement. Drawn on
page 11.

---

## 3 · The corrected `distinct_count`, and the blank the correction produced

**This one is mine.** I asked for the sign to be fixed. `_resolve_n_distinct`
fixes it by correctly *refusing to guess* — returning `None` rather than a
confident wrong number.

But it returns `None` for **four different reasons**, and the row is still
written with `state = STATE_MEASURED`, because every other `pg_stats` field came
back fine. Row-level state cannot carry a per-field absence. So the card renders
a row labelled *measured*, with `null_fraction`, `average_width`, `correlation`
and the MCV list all present, and nothing at all where distinct goes.

A wrong number became a blank that cannot say why — the same rule one step
along: *absence and failure are states, never blanks.*

### The case that produces it on a healthy database

`ever_analyzed` is `bool(activity.get("last_analyze") or
activity.get("last_autoanalyze"))` — from `pg_stat_user_tables`, **which
`pg_stat_reset()` clears, while the `pg_stats` rows survive it untouched.**

So after a stats reset, every column whose `n_distinct` is a negative ratio —
which is every near-unique column: every id, every email, every key — silently
loses its distinct count, on a database whose statistics are perfectly good.

`stats_reset` is already in scope at `database_surveyor.py:379`, from the
`get_stats_reset()` round 1 asked for so a change comparator could tell a real
rate from a counter reset. It answers this question too, and this path does not
consult it.

### The four, and what the card should say

| condition | the card's sentence |
|---|---|
| `n_distinct is None` | not collected for this column — **run ANALYZE ›** |
| `reltuples < 0` (PG14+) | the table has never been ANALYZEd — **run ANALYZE ›** |
| `reltuples == 0`, never analyzed | same sentence — and on PG13 this is the ambiguous one, which is why it must not say *empty* |
| `reltuples == 0`, stats were reset | distinct count not resolvable — table statistics were reset on *date*; the other figures predate the reset and stand. **No ANALYZE offered, because ANALYZE is not the problem.** |

Three of the four end in the same action and one ends in **no action at all**,
which is the whole reason they cannot share a dash. Cheapest shape: a short
reason string beside the value, the way `state` already sits beside the row.

Two asks, in order of value:

1. **Consult `stats_reset` in the profile path.** When `last_analyze` is null
   but `stats_reset` is recent, *never analyzed* is the wrong conclusion.
2. **Carry the reason per field.** Whatever the storage, the card must not print
   a dash.

---

## 4 · The exposure heatmap — wait, and do not ship it caveated

**Wait**, but not because the drawing is hard. The next drawing is blocked on a
*build* decision, not a design one, and the two candidates produce different
pictures:

- `information_schema.column_privileges` yields grants with a grantor and a
  grantee, so a cell can distinguish *granted directly* from *inherited via
  `readers`*, and the revoke path is drawable.
- `has_column_privilege()` yields a boolean per pair with **no path at all**.

The cell popover I sketched on page 10 exists under one and not the other.
Drawing now means drawing twice.

**One thing is worth settling now, because it is a shipping rule rather than a
drawing.** The three gaps are logged at `Backlog.md:6532` and are not built.
Until they are:

> The heatmap should not ship **at all** — not behind a caveat, not in a
> preview, not with a banner.

A caveated exposure grid is still read as a grid: the eye takes the empty cells
as the answer and the sentence above them as boilerplate. Everywhere else in
this product a caveat is the honest move. Here it is not enough, because the
failure is silent and points the wrong way — toward *this role cannot read
this*, which is the one direction that gets acted on by not acting.

Page 10 stands as the target. It is not a build-ready spec until those reads
land.
