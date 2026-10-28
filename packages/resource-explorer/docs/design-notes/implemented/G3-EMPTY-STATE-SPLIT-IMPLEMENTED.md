# G3 — split "empty" into its real distinct states — implemented

**Dispatch:** item G3 of the "Resource Explorer expansion architecture"
session's dispatch plan, following `REPLY-DESIGNER-ROUND2-DATABASE-
SCREENS.md` §3.3 (merged to `main` at `9bda025d`).
**Branch:** `re/empty-state-split`, off `origin/main` `9bda025d`, built in a
separate worktree (`/Users/dwolfson/localGit/egeria-v6/trellis-re-empty-
split`) per this repo's git-hygiene convention — the shared checkout at
`~/localGit/egeria-v6/trellis` was never touched or switched off `main`.
**Coordination:** confirmed with the "Resource Explorer expansion
architecture" session before writing (no file overlap with G1's
`glyphs.js` or G2's `app.js` in the original plan). That session then
flagged one real overlap after all — `app.js`'s own `_SCHEMA_SHORTFALL_
LABELS` map renders `classification` client-side and would show nothing
for the two new classifications — and asked for option (a): a two-line
addition to that map, reserved for G3 so G1/G2 stay out of those specific
lines. Done that way; see below.

## The bug

`_schema_inventory_container_rows` (`resource_explorer/surveyors/
database/survey_definition_adapter.py`) classified a schema as `"empty"`
whenever ANY of four different conditions held:

```python
table_count == 0 or scope_state == SCOPE_EMPTY or row_total == 0 or row_total is None
```

and then stored `"row_total": row_total or 0` — silently turning a
genuine "never measured" into a stored, displayed zero.

The docstring justified this with the owner's ruling, quoted as "a schema
whose every readable table has zero rows **[or, as here, no row data at
all]** is class `empty`" — with the bracketed clause added *inside* the
quote marks. The owner ruled on schemas with zero rows; the bracket is
what smuggled "never measured" into the same bucket. That bracketed
insertion has been removed along with the collapsed branch it justified.

## The fix

`_schema_inventory_container_rows` now classifies these as three distinct
outcomes:

| condition | classification | rendering |
|---|---|---|
| `table_count == 0` (or the credential probe's own `SCOPE_EMPTY`, which means the same thing: USAGE granted, catalog shows zero tables) | `"no_tables"` | `?`-free: **"no tables"** — a structural fact, not a measurement |
| `row_total is None` (every table's `row_count IS NULL` — never measured, no catalog-estimate fallback either) | `"not_measured"` | **`?` "rows not measured"** — genuinely unknown |
| `row_total == 0` (every table measured, and zero) | `"empty"` (unchanged name — this is the one case the owner's original ruling actually covers) | **`∅` "0"** in the design vocabulary; today's text rendering is `"N table(s) · 0 row(s) — empty"` (unchanged from before, since this is the one case that was already correct) |

`row_total` itself is now `None` (not `0`) for both `no_tables` and
`not_measured` — the stored-fake-zero this was all about is gone at the
data layer, not just in the rendered text.

Per the architecture session's correction, the glyphs (`?`/`∅`) themselves
are **not** wired in yet — that's G1's `glyphs.js` module, deliberately
out of scope here so as not to duplicate or preempt it. This slice only
fixes the classification and the plain-text renderings that already exist
in this module (the `_schema_inventory_container_headline` sentence and
the `_schema_inventory_container_measurements` evidence-table `note`
column), plus the one client-side label map the architecture session
identified as unexpectedly overlapping.

### Files changed

- `resource_explorer/surveyors/database/survey_definition_adapter.py`
  - `_schema_inventory_container_rows`: three-way split (above), row_total
    semantics fixed, docstrings updated, the misquoted bracket removed.
  - `_schema_inventory_container_headline`: renders `"no_tables"` as
    `"{name} — no tables"` and `"not_measured"` as `"{name} {count}
    table(s) — rows not measured"`, each its own `elif` branch rather than
    folded into the old single `"empty"` branch.
  - `_schema_inventory_container_measurements`'s `_NOTES` map: added
    `"no_tables": "no tables"` and `"not_measured": "rows not measured"`.
- `resource_explorer/web/static/next/app.js` — `_SCHEMA_SHORTFALL_LABELS`
  (the Schema Inventory tree's own client-side classification→label map,
  ~line 3417): added the same two entries, so the tree view's schema rows
  don't fall back to the raw `no_tables`/`not_measured` string. This is
  the one addition to app.js authorized by the architecture session for
  G3 specifically; no other line in that file was touched.
- `tests/test_schema_inventory_tree.py` — new `TestEmptyIsThreeDistinctStates`
  class (one test per state, one test asserting the three are pairwise
  distinguishable by classification AND `row_total`, one for the headline
  sentence, one for the measurements-table note) and
  `TestCocoPharmaSalesSchemasLiveRegression` (below).
- `tests/test_schema_inventory_container_headline.py` — two pre-existing
  tests asserted the OLD, buggy behavior and have been corrected rather
  than left to rot as a second implementation of the bug:
  - `test_a_zero_table_schema_the_probe_knows_about_still_appears` asserted
    `"public — empty (no tables)"`; now asserts `"public — no tables"`.
  - Renamed `test_a_schema_whose_only_table_has_no_row_data_is_empty_not_
    data` → `..._is_not_measured_not_data`; asserted `"eu_sales 1 table(s)
    · 0 row(s) — empty"`, now asserts `"eu_sales 1 table(s) — rows not
    measured"`. This was the exact bug, pinned as a passing test — the
    live giveaway that a green test suite doesn't mean the behavior is
    right if the test itself encodes the mistake.
  - Everything else in that file (ordering tests, structure-only,
    views-only, staging, system-fold) was checked and needed no change —
    the row_count=0 "measured zero" cases keep their existing
    `"empty"`/"0 row(s) — empty" behavior unchanged, and the two ordering
    tests that reference the split states (`eu_sales`/`public` with
    `row_count=None` / zero tables) only assert relative order, which is
    unaffected by the text change.

## Live check: coco_pharma's eu_sales / target_sales / us_sales

Queried the shared registry directly (`localhost_docker_coco_pharma` —
the real slug; `coco_pharma` alone is not what it's stored under) rather
than assuming from the code path:

```
eu_sales     table_count=1  eu_sales_forecast        row_count=None  state=measured
target_sales table_count=1  consolidated_forecast    row_count=None  state=measured
us_sales     table_count=1  us_sales_forecast        row_count=None  state=measured
```

Credential capability for the survey's own user, same three schemas:

```
eu_sales     {usage_granted: True, table_total: 1, table_select: 1}
target_sales {usage_granted: True, table_total: 1, table_select: 1}
us_sales     {usage_granted: True, table_total: 1, table_select: 1}
```

**Verdict: never measured, not genuine zeros.** All three schemas are
fully readable by this credential (`SCOPE_READABLE` — `SELECT` on all of
each schema's one table), so this isn't a visibility gap. The single table
in each schema has `row_count IS NULL` in `database_tables`, with
`state == "measured"` — meaning it went through the real (non-catalog-
fallback) code path but `pg_stat_user_tables` had no row for it, i.e. it
has never been `ANALYZE`d or `VACUUM`ed. That's exactly the
`reltuples == -1` "never analyzed" condition
`database_surveyor.py`'s `NEVER_ANALYZED` docstring names as the usual way
to get a `NULL` row count on a fresh Docker Postgres. **Before this fix,
`_schema_inventory_container_rows` reported all three as `"empty"` — "1
table(s) · 0 row(s) — empty" — a confirmed measurement that never
happened. After this fix they classify as `"not_measured"` — "1 table(s)
— rows not measured."**

This is captured as a live-pinned regression test,
`TestCocoPharmaSalesSchemasLiveRegression.test_pins_never_measured_not_
confirmed_zero` (`tests/test_schema_inventory_tree.py`), using the exact
table/schema shape found live rather than a synthetic stand-in.

## Testing

```bash
uv run pytest tests/test_schema_inventory_tree.py tests/test_schema_inventory_container_headline.py tests/test_schema_inventory_headline.py tests/test_schema_inventory_results_evidence_fields.py tests/test_schema_inventory_tree_route.py -q
# 51 passed

uv run pytest tests/ -k "schema_inventory or container_headline or database_adapter or db_derived" -q
# 175 passed, 6540 deselected

uv run pytest tests/ -q   # full suite, run in background; see report for result
```

## Status

Pushed to `origin/re/empty-state-split`. **No PR opened** — per the
dispatch, the project owner is away for the evening and `gh` PR creation
hangs on an unattended 1Password prompt; the architecture session is
batching all PR-opening for the morning.

Reported to the "Resource Explorer expansion architecture" session:
branch `re/empty-state-split`, tip SHA, and "G3" on push.
