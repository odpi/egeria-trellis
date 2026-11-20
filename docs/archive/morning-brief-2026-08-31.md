# Morning brief — 2026-08-31

> **Status (2026-11-19, 6.3 Tech Preview):** archived. A dated overnight brief; its "outbox is not built" and repo-state lines are of 2026-08-31 and not maintained. Nothing links here.

Written overnight after a backlog review. Everything below was checked against the code or a
live query, not taken from a tracker's own status line — three tracker entries turned out to be
stale and are corrected in the same commit as this file.

**Repo state:** one branch (`main`), one worktree, clean tree, no open PRs. `odpi/main` and
`dwolfson/main` are content-identical. Every branch merged and deleted; four orphaned worktrees
from closed sessions removed.

---

## Start here

### 1. Resource Explorer runs against GitHub with no token — CORRECTED 31 Aug

**The 30 Aug version of this item was wrong on both counts and is replaced.** It claimed
`skipped_by_design` was defined but unused, and that `security_features` was structurally
impossible. Neither holds:

- `result_status.skipped()` **is** used — once, in `_security_features_results`, added
  2026-08-25. The reader already distinguishes never-run from admin-invisible correctly.
- The field is **not** invisible to us. Measured live against the real API: `admin=True` and
  `security_and_analysis` present on `odpi/egeria`, `odpi/egeria-python`, `odpi/egeria-docs`,
  `odpi/egeria-trellis` and `dwolfson/trellis` — 6 of the 60 registered repos.

The real defect is upstream of all of it. `config.py`'s `_ENV_FILE_CONFIG` sets
`env_file=".env"` — a **relative** path, resolved against the process working directory. The
package's `.env` lives at `packages/resource-explorer/.env`, but `make re-web` runs
`uv run --package resource-explorer resource-explorer web` from the workspace root. Measured:

    from trellis root      token present: False
    from the package dir   token present: True

So every GitHub call made by the web app is **anonymous**. Two consequences, both observed:

1. **60 requests/hour, not 5000.** This is the rate limit that interrupted scouting testing.
2. **Admin-only fields silently vanish.** GitHub omits `security_and_analysis` from an
   anonymous response, so it is stored as `{}`, and the analysis then reports *"GitHub only
   returns these to repository admins, so they are invisible for a repo you do not own"* —
   about repos you do own. A confident wrong answer, and the same failure shape as the six
   found on 30 Aug: a record indistinguishable from a real one.

The evidence is in the data. `egeria_docs` and `egeria_trellis` were fetched on 28 Aug and
stored `{}`; `egeria_git` (27 Aug) and `egeria_workspaces_git` (30 Aug) stored real values —
those two came from CLI runs inside the package directory.

`config.py`'s own comment already records an earlier form of this bug ("`.env` support silently
never worked for ANY of these classes... Found 2026-08-10"). It was fixed for nesting and left
relative for path.

**Fix:** resolve the env file against the package rather than the cwd —
`Path(__file__).resolve().parents[1] / ".env"`. Then re-run stats and `security_features` on the
six admin repos; the other 54 keep reporting `skipped_by_design`, which is correct for them.

**Still open, and now a real design question rather than a bug:** whether `security_features`
stays its own analysis or folds into a broader security survey that runs the admin-only checks
only where admin is actually held.

### 2. Outbox/retry publishing — the one thing blocking the largest piece

`docs/outbox-publishing-design.md` is written and **not built** (no `outbox` module exists). It
is the sole remaining prerequisite for projecting recovered architecture into Egeria, and the
design's own words are *"a half-published blueprint is worse than none."*

Correcting the backlog's framing, which I fixed overnight: "nothing from architecture recovery
reaches Egeria" **stopped being true on 30 Aug**. `arch_recovery/materializer.py` writes a real
`SolutionComponent` when a curator accepts one. That path is deliberately narrow — accepted
verdicts only, bare component, no blueprint or relationships, no retraction if a verdict is
later reversed. So the *blueprint* projection is unbuilt; a single-component one is not.

### 3. The nine questions that answer nothing

Six are `gap`, three `unknown`. The six need a content read nothing does yet — upgrade process,
CLA/DCO provenance, telemetry detection, AI/ML model-card licensing, secret handling,
similar-repo search. These are the concrete shape of the agent-based post-ingestion work, and
they are now named rather than aspirational.

The three `unknown` carry authorial uncertainty in the CSV (`repo_conventions - RAG read?`) and
want a decision, not a surveyor. That is a fifteen-minute job.

---

## Known-broken, logged, unowned

- **Egeria Advisor: 6 failures in `test_report_spec_planner.py`** (`BACKLOG.md` TD-1). They have
  **never passed in this repo** — the file is unchanged since the import commit and the same
  mismatch is present there. Four distinct causes, one of which is a test calling a FastAPI route
  handler with the wrong arity. Anyone running `make test-ea` hits them, which is how red became
  normal.
- **`project_contributor_stats` misleads by shape.** Its one reader was fixed; the writer still
  produces overlapping windows. Either fix the writer to disjoint periods or retire the table.
- **`repo_profile_refresh` is schedulable but invisible to `REPO_ANALYSIS_STEP_MAP`.** It works —
  `scheduler._run_repo_survey` intercepts it earlier on `action == "profile"` — but a reader
  checking the map concludes it cannot be scheduled. The map is not the whole dispatch story
  while reading as though it were.

---

## Decisions waiting on you

- **`granularity-pass.md` §6** — should an analysis stay independently runnable now that surveys
  are schedulable, or become view-only? Two ways to run the same step is how the original
  duplication started.
- **The vocabulary rename's layers two and three** — SQL columns and wire keys. The recorded plan
  is that both land with the wipe-and-reingest, since that makes the column migration free.
  Layer one (526 Python identifiers) is merged.
- **~~`UNVERIFIED` persistence.~~ CORRECTED 2026-08-31 — outcomes ARE stored and ARE queryable.**
  This entry claimed the outcome "is spread into an annotation and never stored, so query time
  cannot reach it". That is wrong. `step_cost_observer.describe_work()` harvests outcome labels
  from the annotations the orchestrator already holds — so no sub-surveyor has to cooperate — and
  `record()` persists them as `observed_outcomes` in **`project_analysis_metrics` under
  `kind='step_cost'`**. Measured: `repo_file_structure` carries `["unverified"]` on 11 runs,
  `repo_manifest_parse` `["no_signal", "recovered", "unverified"]` on 7.

  The error was measuring the wrong table — `project_analysis_findings.detail_json`, where the
  outcome genuinely is nearly absent (3 of 23 kinds) — and reading that as "not stored anywhere".
  A correct count of the wrong population, and the one instance of that shape today which would
  have cost real work: a table was about to be built for a problem that does not exist.

  What IS missing is smaller and named in `repo-context-and-tool-routing.md` §7: coverage (11 of
  ~33 sub-surveyors emit an outcome at all), aggregation (a step reporting
  `["no_signal", "recovered", "unverified"]` has no rule for what the STEP achieved), and
  discoverability (nobody hunting tool-fit would think to query the cost table).

---

## Engine host — still blocked, not on us

ISSUE-79: a template-created `FileFolder` NPEs in `BasicFolderConnector.getFile()`, plus a
server-side registration gap. Confirmed by the `egeria-python` session, who also corrected an
earlier claim of mine: there is no missing `active-engine-actions` wrapper to file, because no
claimable-work endpoint exists anywhere.

---

## What today established, worth not relearning

One failure shape ran through every problem found on 30 Aug: **a record that outlived what it
described, indistinguishable from a current one.** Six instances, and the last two were mine:

- A `gap` note citing OSV.dev as a *candidate tool* beside a `cve_scan` already using it
- A test hard-coding a question as its example of "unanswered", which broke when it was answered
- A comment naming four perspectives retired two days earlier
- A context resolver reading one results table of several, reporting seven live analyses as missing
- `Architecture.md` claiming SQLite when the registry has been Postgres for weeks — **and
  `CLAUDE.md` carrying the same error**, which is what settled the duplication in favour of one copy
- A CI ratchet reporting "these tables are gone" when it meant "I could not see them"

The practice that caught all six was the same: **run it, do not read it.** The corollary, learned
twice the hard way, is that a check needs its own negative control — my first `_has_content`
had the exact bug it existed to prevent, and my first negative control used a fault case that
was not actually a fault.

Two git rules were added to `CLAUDE.md` from live incidents: no whole-tree operations in a shared
checkout (use the pathspec form), and never `git commit` while someone else's merge is open —
`git add <paths>` does not save you there, because the merge state is not addressable by
pathspec.
