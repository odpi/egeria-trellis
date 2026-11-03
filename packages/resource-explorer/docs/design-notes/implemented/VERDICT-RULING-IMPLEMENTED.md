# Implemented: what a component verdict is about

**Replies to:** `RULING-WHAT-A-VERDICT-IS-ABOUT.md` §2a–§2d and §3.
**Shipped in:** PR #107 (`re/verdict-ruling-implementation`), merged as `1b370cbe`
alongside #106. Read against `342e0ca3` when written; `main` is at `1b370cbe`
now.

All four consequences, no schema migration:

- **§2a — both proposals shown.** `_architecture_recovery_results`
  (`repo_survey_definition_adapter.py`) now groups `comp_rows` by `run_label`
  instead of `max(comp_rows, key=surveyed_at)` picking one. Each currently-
  active extractor gets its own entry in a new `proposals` list (type,
  confidence, reading, `run_label`); the existing top-level fields stay as a
  single "primary" pick for callers not yet reading `proposals`.
- **§2b — agreement.** `agreement` is true when two or more extractors
  currently propose the same path. Rolled up to the branch level in
  `component_tree.py` (`agreement_count`) and rendered on the `/next`
  branch-tree leaf row as "two extractors agree this is a component". It
  outranks a single high confidence in the "sort by confidence" toggle,
  per §2b's own ordering rule.
- **§2c — withdrawal, flagged not invalidated.** `_withdraw_vacated`'s
  existing per-`run_label` withdrawal rows are now cross-referenced against
  each scope's still-accepted verdict. An accepted verdict whose path one
  extractor no longer proposes renders "⚠ review — no longer proposed by X"
  on the leaf row; the verdict itself, and `resolve_verdict`'s resolution,
  are untouched.
- **§2d — two populations named.** The coverage sentence now reads
  "N of M component paths reviewed" (path-keyed, spans every reading)
  separately from "N of M clusters in the {reading} reading reviewed" — one
  clause per reading that actually has clusters, instead of one combined
  "blueprints" figure. `_architecture_verdict_coverage` returns
  `blueprints_by_perspective` (keyed by reading) in place of the old single
  `blueprints` bucket.
- **§3 — diagram names its reading.** The diagram caption ("found by
  coupling · detect also on file") now renders in both the classic Curate
  panel (already had the data, wasn't naming it right) and the `/next`
  diagram view, which showed nothing here before.

**§0 vocabulary**, rendered text only — no field, schema, or JSON-key
renames: `run_label` → "found by", `Component.perspective` → "reading". The
chrome's 12-chip role-filter keeps "Perspective" untouched.

**Test coverage:** `test_component_tree.py` and
`test_architecture_verdict_coverage.py` both extended (agreement rollup,
withdrawal flag, per-reading coverage buckets, updated sentence wording).
740+ tests pass; the shared-Postgres-backed suite couldn't be fully
exercised this session (Docker wasn't running on the host) — unrelated to
this change, all failures were `connection refused` on port 5442.

**Known gap, not built:** the classic Curate panel's `_archRow` (`index.html`)
doesn't render `proposals`/`agreement`/`withdrawn_by` — only the `/next`
branch-tree UI got the full §2a/§2b/§2c treatment. The backend returns the
data to both surfaces; the classic panel's own row markup was left as-is,
scoped out given this session's `/next` mandate. Flagging in case the
component-tree spec (canvas page seven) assumes parity with the classic view.

**On the retraction:** saw `REPLY-RETRACTION-WITHDRAWN.md` already covers
this — no new information to add; independently reached the same read on the
09-14 wipe-and-redeploy decision when I was asked to check.
