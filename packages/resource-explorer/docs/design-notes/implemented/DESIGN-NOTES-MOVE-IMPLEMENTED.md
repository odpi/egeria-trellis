# Design-notes reorganisation — implemented (2026-10-01)

Replies to: the reorganisation proposal and executable plan (coordinator, 2026-10-01) and
`REPLY-DESIGNER-DESIGN-NOTES-REORG.md` ("Go ahead with Option A"). Owner decisions applied:
Option A; the two suffix-less fix notes move unrenamed; `DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md`
stays in the root (it is design-only); `INBOX.md`, the handoff note, the triage doc, `TIMELINE.md`,
`wireframes/` and `screenshots/` do not move.

Environment proof (run in the new worktree before any test):

```
$ uv sync --all-packages --all-groups
$ python -c "import resource_explorer; print(resource_explorer.__file__)"
/Users/dwolfson/localGit/egeria-v6/trellis-re-design-notes-move/packages/resource-explorer/resource_explorer/__init__.py
```

## The layout

`docs/design-notes/` (written DN below) now has two new subdirectories next to `wireframes/` and
`screenshots/`:

| location | what | files |
|---|---|---:|
| `DN/implemented/` | 102 `*-IMPLEMENTED.md`; `OUTBOX-DRAIN-RACE-FIXED.md` and `CATALOG-AND-SURVEY-REFRESH-FIX.md` (unrenamed); 17 shipped design notes that a `*-IMPLEMENTED` note cites | 121 |
| `DN/evidence/` | 7 audit / probe / measurement notes and the 3 `step_runs-*.csv` files | 10 |
| `DN/` root | `INBOX.md`, handoff, triage, `TIMELINE.md`, `DESIGN-INTERFACE-SURFACE-IMPLEMENTED-RUNG.md`, every other design-only note, every designer reply | 59 |
| `DN/wireframes/`, `DN/screenshots/` | unchanged | 30, 5 |

Counts from `git ls-tree -r` on `origin/main` before and on the branch after: 225 files before,
225 after (190 in the root before, 59 after; 131 moved = 190 - 59; no basename occurs twice before
or after). This note itself is the 122nd file in `implemented/` (the counts above are for the
rename commit).

The 17 design notes that moved to `implemented/` (each has a citing `*-IMPLEMENTED` twin): SPEC-ADMIN-THE-FOUR-GAPS,
SPEC-CURATE-SELECTION-AND-BLUEPRINTS, SPEC-PARITY-INVENTORY-AND-GROUPS, SPEC-PUBLISH-STATE-AFTER-REDEPLOY,
BRIEF-BY-ANALYSIS-PANEL-USABILITY, BRIEF-ENRICHMENT-E3-OBSERVATION-STATES, BRIEF-KEYS-AND-ACTIVITY-CLOBBER,
BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH, ASSESSMENT-CHAT, PLAN-EXECUTION-MODES-VERIFICATION,
PUBLISH-STATE-AFTER-REDEPLOY-CORRECTIONS, REPLY-PUBLISH-STATE-GO-AHEAD, REPLY-CATALOGUE-IN-LAYERS,
REVIEW-CURATE-PUBLISH-FRESHNESS, REVIEW-VERDICT-RULING, RULING-CLASSIC-AND-NEXT,
RULING-WHAT-A-VERDICT-IS-ABOUT. The other 16 SHIPPED rows of the mapping (commit-only or ledger-only
evidence) did not move in this change.

`DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md` is on both the evidence list and the mapping's SHIPPED
list; it went to `evidence/` (the move list's rule (b)) and is not one of the 17.

## How a note is cited now

The convention is the BARE note name: `see PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`. No directory
prefix, so a later move between subdirectories cannot dangle a citation. This was done in two
commits so history follows the files:

1. Pure `git mv` renames, no content edits (131 renames).
2. Pointer repairs: path-qualified or wrapped citations of moved notes in code, tests, scripts, CI,
   config, `docs/` and the notes themselves rewritten to the bare name, including references wrapped
   across a line break (mid-name and after `docs/design-notes/`) which are now on one line. The
   user-facing strings (`bootstrap.py` pool-worker warning, `prefect_adapter.py` startup log,
   `egeria_database_surveyor.py` missing-connection error) read "see the design note NAME.md".
   `CLASSIC-VS-NEXT-PARITY`'s search scope is now recursive; `scripts/csv_to_question_catalog_yaml.py`'s
   glob points at `implemented/`; `Backlog.md`'s done-ness convention notes the new directory.

Not edited on purpose: `INBOX.md`, `TIMELINE.md`, the designer's reorg ask and reply (they describe the
old paths as history), `docs/survey-model.md:220`, `egeria_async_survey_result.py:242`, `.dockerignore`.

## The permanent test

`tests/test_design_note_references_resolve.py`. Every design note cited in a comment, docstring or
message string under `resource_explorer/`, `tests/`, `scripts/`, `frontend-build/`, `egeria-outbox/`,
`.github/` and the package config files must resolve. A bare name resolves to a file of that name
ANYWHERE under `DN/`. A citation written as a path (`docs/design-notes/<...>/NAME`) is held to that
exact path, which is what makes a move that strands path-qualified pointers fail while bare names
survive any later move. Wrapped citations are joined across comment, docstring and adjacent-string-literal
line breaks before matching; unit tests pin the joiner so the guard cannot degrade into a one-line scan.

Two citations were dangling on `origin/main` before the move and are recorded in `KNOWN_DANGLING`
(the test fails if an entry goes stale, so the list only shrinks): `STAGE-PAGE-ROUND.md` (4 files) and
`SORT-DIRECTION-FIX-IMPLEMENTED.md` (1 file). Neither file has ever existed in git history.

## Verification (in the order run)

1. **Guard is red on the rename alone.** Commit 1 (renames, no repairs) checked out in a throwaway
   detached worktree with the new test copied in: `1 failed, 8 passed`, 135 dangling citations,
   including the runtime string and wrapped names. Excerpt:
   ```
   resource_explorer/bootstrap.py:183: design-notes/PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md
   resource_explorer/bootstrap.py:255: design-notes/PREFECT-DEFAULT-WHOLE-DEFINITION-IMPLEMENTED.md   (split across two string literals)
   resource_explorer/registry.py:8372: design-notes/PER-REQUEST-SERVER-LATENCY-ROUND-2-IMPLEMENTED.md  (name wrapped mid-name)
   resource_explorer/surveyors/database/survey_definition_adapter.py:878: design-notes/SLICE-17-RUNNABILITY-FROM-CATALOG-IMPLEMENTED.md  (wrapped)
   ```
   An earlier draft of the test resolved every citation by basename and passed on that same tree;
   it was changed (path citations are held to their path) because a guard that never failed is not trusted.
2. **Planted bad pointer, final tree.** A wrapped `docs/design-notes/GONE-AWAY-NOTE-` / `IMPLEMENTED.md`
   in `reachability.py`'s docstring: `1 failed, 8 passed`, reported as `reachability.py:1`. Removed.
3. **Pre-move main.** The same test file copied onto `origin/main` (995781ba): `9 passed`.
4. **Final tree.** New test `9 passed`. Python: 77 test files (those edited by the repair, plus every
   `test_next*`, `test_tailwind*`, `test_frontend*`, `test_*static*`, `test_*workflow*`,
   `test_ci_workflow_parser`): `1369 passed, 104 skipped, 0 failed` (Postgres and Egeria
   integration tests skipped: `PGVECTOR_PORT` pointed at a dead port and `REGISTRY_DATABASE_URL` at
   SQLite, so nothing touched the shared Postgres or Egeria). The full suite is 7,400 tests and about
   90 minutes at the measured rate, so it was not run. Node harness (`npm run test:harness`,
   Node 20.11): `168 pass, 0 fail`. `node --check` on every edited JS file and `py_compile` on every
   edited Python file: clean.
5. **Counts.** 225 files under `DN/` before and after (190 root files before, 59 after; 121 in
   `implemented/`, 10 in `evidence/`, 30 `wireframes/`, 5 `screenshots/`); no basename duplicated.
6. **Renames.** `git diff -M --stat origin/main` shows the 131 moves as renames; the working tree is clean.
