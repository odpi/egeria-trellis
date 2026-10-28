# Gap guard hardening — implemented (BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md §C)

Branch `re/gap-guard-hardening`, off `origin/main` @ `019c2796`.

## What was wrong

`tests/test_question_catalog_gap_guard.py` only ever checked a `kind: gap`
question's `analysis_ids` against `analysis_catalog.yaml`. Two blind spots:

1. A prose `GAP:` note that names no id at all has nothing to check.
2. Several real, running capabilities are **steps**, not analyses —
   `postgres_column_profile` and `postgres_nested_columns` exist and produce
   rows today but have no `analysis_catalog.yaml` entry of their own. A `GAP:`
   naming one read as a permanent gap because the old guard never consulted
   the step registries at all.

Three CSV rows were hand-corrected on 2026-09-27
(`re/questions-false-gaps-and-hygiene`, commit `b0d678bc`) precisely because
nothing caught them being false gaps.

## What changed

`packages/resource-explorer/scripts/csv_to_question_catalog_yaml.py`:

- `_load_known_step_ids()` — reads `re_analysis_step` keys live from all
  three resource-type adapters: `repo_survey_definition_adapter.STEP_REGISTRY`,
  `database/survey_definition_adapter.DATABASE_STEP_REGISTRY`, and
  filesystem's `re_analysis_step_info` (via `get_adapter("filesystem")`).
  Same "read live, don't hand-sync" pattern `_load_known_analysis_ids()`
  already uses for `analysis_catalog.yaml`.
- `KNOWN_REGISTERED_IDS = set(KNOWN_ANALYSIS_IDS) | KNOWN_STEP_IDS` — the
  union a `GAP:`/`PARTIAL:` note is now checked against. Kept separate from
  `KNOWN_ANALYSIS_IDS`, which still drives `kind: analysis` classification
  via `_is_pure_analysis_list` — naming a step is not the same claim as
  naming an analysis that fully answers a question on its own, but it is
  just as real a thing to falsely claim doesn't exist.
- `_referenced_registered_ids(note)` — extracts snake_case, underscore-joined
  tokens from the note (`_ID_TOKEN_RE`) and intersects them with
  `KNOWN_REGISTERED_IDS`.
- `_parse_answering()` now validates at generation time (raises `ValueError`,
  same pattern as the existing unknown-check-ref guard just above it):
  - `kind == "gap"` and the note names a registered id → **fails**: "this gap
    names something that exists ... re-word as PARTIAL: or name the missing
    piece instead of something already built."
  - `kind == "partial"` and the note names **no** registered id → **fails**:
    "PARTIAL: note names no registered analysis or step id ... nothing to
    check its claim against."

This means a bad row now fails **CSV regeneration itself**
(`python scripts/csv_to_question_catalog_yaml.py ...`), not only a separate
test — the same class of guard as the existing unknown-check-ref and
unknown-Purpose/-Level validations already in that function.

`tests/test_question_catalog_gap_guard.py` gained the test classes below,
all calling the real `_parse_answering()` (imported by path, the same
pattern `test_question_catalog_multi_type.py` uses) rather than
re-implementing extraction logic — per the coordinating session's explicit
instruction to keep one parser, not two.

`tests/test_question_catalog_multi_type.py` — one pre-existing synthetic
fixture (`test_a_multi_type_row_lands_in_exactly_those_types`) used
`"GAP: grain_determination not built"` as throwaway test data.
`grain_determination` is a real, registered analysis id, so this fixture now
correctly fails generation under the new rule — fixed by rewording it to an
unregistered placeholder (`grain_ranking_hypothetical`); the test's actual
subject (cross-type row placement) is unaffected.

## Decision: free-text matching, not backticks

The brief's own follow-up flagged a real risk: matching *any* underscored
token in free prose could fire on an incidental word that coincidentally
collides with a registered id, and asked me to either require a marking
(e.g. backticks) before a token counts, or explicitly accept and document
the free-text tradeoff.

**Decision: free-text matching, no marking required.** Reasons, made
concrete rather than asserted:

1. **Retrofitting cost.** The CSV has 21 `GAP:`/`PARTIAL:` rows today (12
   gap, 9 partial). The two rows corrected on 2026-09-27 that now carry
   `kind: partial` already name their real id in **plain prose** —
   `"...the postgres_column_profile step reads pg_stats..."` and
   `"PARTIAL: nested_column_profile (design §5.4...) classifies..."` — with
   no backticks. Requiring a marking would mean rewording rows that are
   already correct, on top of building the guard itself, and the
   coordinating session confirmed these two rows "must pass your new guard
   unchanged" — i.e. without being rewritten. Free-text matching is what
   makes that possible.
2. **The vocabulary is small and specific.** `KNOWN_REGISTERED_IDS` is ~70
   ids (62 analyses + step ids), all engineered compound identifiers
   (`postgres_column_profile`, `db_fingerprint`, `grain_determination`). A
   collision needs an author to write an underscore-joined token — already
   an unusual thing to do in prose — that *also* happens to exactly equal a
   currently-registered id. `TestFalsePositiveRegression` checks the brief's
   own two named candidates (`not_collected`, `primary_key`) against the
   live registry and confirms neither is registered, and confirms a GAP
   using `not_collected` descriptively does not raise.
3. **Cheap failure mode.** A false positive fails CSV regeneration at
   authoring time, with the exact offending token named in the error — a
   noisy, immediately-actionable build failure, not a silent, user-facing
   wrong answer. That's a different risk class from the "confident wrong
   answer" bugs `find-absence-as-answer` is about, where the harness never
   fires at all.

Full reasoning is also inline as a module-docstring addendum in
`csv_to_question_catalog_yaml.py`.

## A second "verify truth a second way" finding (Section C's own version)

The brief's Tests section asks: *"the three previously-corrected rows,
restored to their old wording in a fixture, must fail the new guard."*

Checked against the actual pre-fix CSV (`git show
b0d678bc^:packages/resource-explorer/docs/dr-egeria/resource_questions.csv`)
rather than assumed — and it's false for all three rows. Their **old**
`Answering Analysis` text, verbatim:

| Row | Old text | Names a registered id? |
|---|---|---|
| reachability | "GAP: no reachability probe exists — this needs a CHECK_ASSET-only engine action plus the observed-cost comparison `step_cost_observer` already collects." | No — `resource_reachability` is never mentioned; `step_cost_observer` isn't registered. |
| semi-structured columns | "GAP: no analysis classifies a column as semi-structured ... no id in the database analysis catalog performs it either." | No — no id-shaped token appears anywhere. |
| columns-actually-contain | "GAP: `column_profile` (proposed) — pg_stats is never read, and no sampling fallback exists." | No — `column_profile` was never registered under that exact spelling, proposed or built; the real step is `postgres_column_profile`. |

All three are **blind spot #1** (a GAP naming no real id has nothing to
check) — not blind spot #2 (a GAP naming a real *step* id, which the old
analysis-catalog-only guard specifically couldn't see) — even though the
brief's evidence section files all three under one "three CSV rows" claim.
A mechanical, token-based guard has no way to know that `column_profile
(proposed)` and `postgres_column_profile` name the same thing, or that "no
reachability probe exists" went stale once `resource_reachability` shipped
under a name this note never used. Closing that would need matching intent
or synonyms against a moving target, not token lookup — a materially larger
problem, out of this section's scope.

`tests/test_question_catalog_gap_guard.py::TestTheThreeCorrectedRowsRestoredToOldWording`
pins this finding directly: it restores the three rows' literal old text and
asserts they **pass** (not fail) unchanged, with an inline comment saying
what would have to become true (one of those exact strings getting
registered) for that assertion to need revisiting.

`TestAGapNamingARegisteredIdFailsTheBuild` demonstrates the guard actually
failing a build, using wording that names the real id the way a
name-correct restatement of the original bug would have to (e.g. `"GAP:
postgres_column_profile has not been built"`) — that is the guard this
section was asked to build, and it works; it just isn't reachable by
replaying the three rows' *literal* historical prose.

Flagging this explicitly to the coordinating session rather than silently
declaring the literal fixture "done" when it verifiably isn't.

## Per-resource-type breakdown (task 3)

Computed from the current, unchanged `question_catalog.yaml` (201 entries):

| Resource type | GAP | PARTIAL | Answered (analysis/direct/human/mixed/chart) | Total |
|---|---|---|---|---|
| repo | 8 | 1 | 48 | 57 |
| database | 12 | 8 | 39 | 59 |
| filesystem | 12 | 1 | 21 | 34 |
| dataset | 9 | 0 | 18 | 27 |
| model | 8 | 0 | 16 | 24 |
| **Total** | **49** | **10** | **142** | **201** |

("Answered" = `kind` in {analysis, direct, human, mixed, chart}; no row is
`kind: unknown` today.)

## Tests

`uv run --package resource-explorer python3 -m pytest tests/ -q` — **full
suite**, not a `-k` subset — from the worktree at
`.claude/worktrees/wt-gap-guard`:

```
6620 passed, 104 skipped, 4864 warnings in 814.97s (0:13:34)
```

(0 failed. The warnings are pre-existing `datetime.utcnow()` deprecation
notices and JWT key-length warnings across many unrelated test files, plus
one benign "I/O operation on closed file" logging error from Prefect's
ephemeral-server shutdown path at the very end of the run — none introduced
by this branch, which touches only the two files listed below.)

New/changed tests specifically:

```
$ uv run --package resource-explorer python3 -m pytest \
    tests/test_question_catalog_gap_guard.py \
    tests/test_question_catalog_generator_guard.py \
    tests/test_question_catalog_multi_type.py \
    tests/test_catalog_invariants.py -q
69 passed, 2 warnings in 9.23s
```

`tests/test_question_catalog_gap_guard.py` (17 cases) covers, per the
brief's Tests section:

- **Registered-id vocabulary is real** (`TestTheRegisteredIdVocabularyIsRealAndNonEmpty`)
  — pins that the step loader actually finds `postgres_column_profile`/
  `postgres_nested_columns`, so a later regression here can't silently make
  every "fails" test below pass for the wrong reason (nothing to match).
- **Step-named gap fails** (`TestAGapNamingARegisteredIdFailsTheBuild`) —
  a `GAP:` naming `postgres_column_profile` (a step, no analysis entry) and
  one naming `schema_inventory` (an analysis id) both fail, with the
  offending id named in the message.
- **Prose-only gap passes** (`TestAProseOnlyGapCannotBeCheckedAndIsAllowedToStand`)
  — a `GAP:` with no id-shaped token at all is allowed to stand; this is
  documented as the accepted, unavoidable limit (blind spot #1), not silently
  treated as covered.
- **PARTIAL must name something** (`TestAPartialMustNameSomethingRegistered`)
  — a `PARTIAL:` naming nothing fails; naming a real analysis id or a real
  step id both pass.
- **False-positive regression** (`TestFalsePositiveRegression`) — the
  brief's own named candidates (`not_collected`, `primary_key`) are
  confirmed unregistered and confirmed not to fire a false failure when used
  descriptively in a GAP; a PARTIAL using the same unregistered word still
  correctly fails (it names nothing real).
- **The three corrected rows, restored** (`TestTheThreeCorrectedRowsRestoredToOldWording`)
  — see the finding above: verified, documented, and pinned as passing (not
  failing) with the reasoning inline.

## Coordination

Peer session "Resource Explorer expansion architecture" confirmed no
overlap before this work started, flagged `wt-fix-guards`
(`re/questions-regenerate-doc-and-count`, already merged via PR #299) and
`wt-question-level` (`re/questions-level-column`, already merged) as stale
worktrees to ignore, and set these rules, followed here:

- No hand-edits to `question_catalog.yaml` — none were made; the file is
  unchanged by this branch.
- Extend the known-id set in `_parse_answering()` itself, not a second
  parser in the test — done (`_load_known_step_ids`, `KNOWN_REGISTERED_IDS`,
  `_referenced_registered_ids`, called directly inside `_parse_answering`).
- Backticks-or-documented-tradeoff — documented above and in the script's
  module docstring.
- That session opens the PR, polls CI, and merges. This branch is pushed and
  reported to it with the tip SHA; no PR opened from here.
