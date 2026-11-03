# Execution modes — resolving the "hybrid" naming collision

*(Status refreshed 2026-10-28: built on main 2026-09-20 (commit `4da212aa`, which folds the hybrid surveyors into `executes_at: egeria-adaptive`). The original status line is kept below.)*

**Status:** proposal, 2026-09-20. Corrects `PLAN-EXECUTION-MODES-VERIFICATION.md` §3, which the
project owner flagged as using "hybrid" for two different things. Nothing here is built.

## The collision

`PLAN-EXECUTION-MODES-VERIFICATION.md` §3 recommends a new `executes_at: egeria-hybrid` value to
absorb the strategy-selector logic in `HybridDatabaseSurveyor`/`run_hybrid_filesystem_survey`
(reuse-cached-Egeria-survey → local-scan-then-publish → native-trigger → degrade-to-local, for
**one step**).

The project owner's own definition of "hybrid" is different: **a single Survey Definition whose
steps execute on a mixture of Egeria and RE**, with the same result regardless of which engine ran
a given step. That is not a fourth engine choice for one step — it is what a survey with several
steps declaring different `executes_at` values already does.

**Resolution: these are two different things and only one of them needs a name change.**

## 1. "Mixed-engine survey" — already built, needs no new value

`survey_definition_executor.py`'s dispatch loop already reads `executes_at` per step and routes
each one independently — `"resource-explorer"` runs in-process (optionally via Prefect if
`route_local_steps` is set), `"prefect"` always routes to Prefect, `"egeria"` hands the step to
Egeria's own engine host. A single Survey Definition with, say, `repo_secret_scan` on `prefect`,
`repo_arch_coupling` on `prefect`, and a database step on `egeria` is already exactly the mixed
execution the project owner means by "hybrid" — it runs today, per-step, with no additional code.

**What was missing is not the mechanism but its visibility to an end user** — the project owner's
second question this session ("I don't understand how executes_at manifests to an end user"). Two
gaps, both closeable without new engine logic:

- The per-step `executes_at` value isn't shown anywhere in the UI's survey-definition or run views
  today — a user can't currently see, before or after a run, which steps went where.
- `steps_report`'s existing per-step engine record (see Part A / Part B work, PR #158/#160) is a
  developer-facing structure, not surfaced as a result column or badge.

**Recommendation:** show `executes_at` per step in the Survey Definition detail view (already has a
step list — add an engine badge) and in a completed run's report (already has `steps_report` — add
the same badge, plus provenance from Part B's async result work for `egeria` steps). This is a small
UI task, not an architecture change, and doesn't need a new `executes_at` value.

## 2. The strategy selector — needs folding in, needs a name that isn't "hybrid"

The capabilities in `HybridDatabaseSurveyor.survey()` / `run_hybrid_filesystem_survey()` are real
and still worth keeping (§3 of the original plan's reasoning stands: it's the default web/CLI
survey path today, not dead code, and retiring it without replacing its capabilities is a straight
capability loss):

- **Cache-or-run**: reuse the latest existing Egeria survey instead of re-running.
- **Catalog-on-demand**: create the Egeria asset if it doesn't exist yet (the plain `"egeria"`
  handler refuses to run against an uncatalogued asset).
- **Provenance on the result**: which engine actually produced these numbers (`egeria` /
  `egeria-custom` / `custom` / `error`).

None of that is "mixing engines across steps" — it is "pick the best available engine for *this one
step*, with fallback, and say which one you picked." Calling it `egeria-hybrid` borrows a word the
project owner already uses for something else in the same codebase — the exact case CLAUDE.md's
doc-writing rule warns about (check for a collision with existing domain vocabulary before a
rename).

**Recommendation: call the new `executes_at` value `egeria-adaptive`** (alternatives considered:
`egeria-auto`, `egeria-or-local` — `egeria-adaptive` reads best next to the existing three values
and doesn't imply a specific fallback order in the name itself). Everything else in the original
plan's §3 "Concretely, folding in means" list carries over unchanged, with `egeria-hybrid` replaced
by `egeria-adaptive` throughout:

- Registered in `other_engine_handlers` on the database and filesystem adapters — the dispatch
  loop already routes any key present there, no executor change needed.
- `source` (`egeria` / `egeria-custom` / `custom` / `error`) becomes a first-class field in
  `steps_report`, not just the hybrid function's return dict — this also answers §1's UI-visibility
  gap for these steps specifically.
- Catalog-on-demand becomes an explicit, named property of the new handler rather than an implicit
  side effect of falling into `catalog_and_survey`.
- Web/CLI call sites move to the executor with a one-step synthetic definition; old modules become
  thin deprecation shims for one release, then are deleted.
- CLAUDE.md rule 15 (run the local scan immediately after triggering Egeria's native survey) moves
  with the code to the new handler's docstring.

## 3. What doesn't change from the original plan

Phases 0–6 of `PLAN-EXECUTION-MODES-VERIFICATION.md` §4 are unaffected by this correction — most are
now done anyway (Phase 4's live-harness → PR #155; Phase 5's trigger/poll/attribute → PR #160; the
engine-visibility groundwork → PR #158). What changes is only Phase 7's name (`egeria-hybrid` →
`egeria-adaptive`) and the addition of the small UI task from §1 above, which the original plan
didn't call out because it hadn't yet separated "mixed-engine survey" from "per-step strategy
selection."

Phase 8 (repo `executes_at: egeria` handler — B3) is untouched by this note.

## Decision needed (project owner)

1. Is `egeria-adaptive` an acceptable name for the folded-in strategy selector? (Or a different one
   — the point is only that it must not be "hybrid".)
2. Proceed with both: (a) the `egeria-adaptive` fold-in (~3-4 days per the original estimate), and
   (b) the small `executes_at` UI-visibility task from §1 (~0.5-1 day, no dependency on (a))?
