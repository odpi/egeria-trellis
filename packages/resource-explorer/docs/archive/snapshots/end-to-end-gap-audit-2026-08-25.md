# End-to-end gap audit — 2026-08-25

**Status: DATED SNAPSHOT — 2026-08-25. Do not act on it directly.** Several gaps it records have since been closed. Kept because the *method* is reusable — it was measured against the live 60-repo corpus and the running UI rather than read off the code. Live items are in `Backlog.md`.

**What this is:** a survey of where RE's end-to-end story (launch an analysis →
it runs → it produces something → the UI shows something meaningful) actually
breaks. Measured against the live 60-repo corpus and the running UI, not read
off the code.

**Method.** Every analysis kind's results endpoint was called for all 60 repos
through the real HTTP route; the UI's own catalog and render-mode maps were read
from the running page. Where a number appears below it was counted, not
estimated.

---

## A. Runnable from the UI, shows nothing

Confirmed live in the browser: `GET /api/analyses/repo` returns 23 entries, and
`_REPO_RESULTS_RENDER_MODE` has 19. Of the four without a Results button, two
are actions and correctly excluded (`repo_profile_refresh` → `profile`,
`egeria_publish` → `publish`). The other two are `action: survey`:

| analysis | intent | data it produces | user sees |
|---|---|---|---|
| `architecture_doc_lens` | discovery | **findings on 11 of 60 repos** | Run → toast, no Results button, ever |
| `architecture_summary` | discovery | nothing found under any kind | Run → toast, no Results button |

`architecture_doc_lens` is the sharper case: the work runs, persists, and is
invisible. It is also the step whose whole point is reading documentation the
code-only path cannot see, so its results are the least redundant in the system.

**Fix:** give both an `AnalysisKind.results` reader and a render mode. For the
lens, `evidence_kind` (`emphasised` vs `mentioned`) should be structural in the
rendering, not a style difference — a reader who cannot tell them apart will
over-trust the weaker.

## B. Silently broken

**`_website_ingestion_headline` has the wrong signature.** It is declared
`(results)` and invoked as `(registry, slug)` (`web/routes/projects.py:975`),
so it raises `TypeError` on **every** call — verified 0 of 60. The caller wraps
headline resolution in a bare `except Exception: headline = None`, so the tile
simply never appears and nothing is logged. Every other headline reader in the
file takes `(registry, slug)`; this is the sole outlier.

**Fix:** correct the signature, and add a test asserting every headline reader
is callable as the caller calls it. A bare except around a uniform call is
exactly where an interface mismatch goes to hide.

## C. Coverage — analyses that work but have no input

These are not broken; they are gated behind data that was never collected for
most of the corpus. The end-to-end story still fails for a user: Run → nothing.

| analysis | repos with content | root cause |
|---|---|---|
| `rag_ingestion` | 1/60 (58 `never_run`) | ingestion has only ever run on a handful |
| `security_features` | 2/60 | **structural** — GitHub returns `security_and_analysis` only to repo admins, so this is empty for every third-party repo by design |
| `code_symbol_extraction` | 5/60 | `project_code_symbols` populated for 7 repos |
| `sub_resource_survey` | 5/60 | download-tier, rarely run |
| `api_structure` | 7/60 | same `project_code_symbols` dependency |
| `data_file_profiling` | 7/60 | only repos that contain data files can have profiles — partly legitimate |
| `repository_health` | 24/60 | health metrics only written when the step runs |

**`project_code_symbols` is the single highest-leverage item:** 7 of 60 repos,
and it gates two analyses. `IngestionPipeline.refresh_profile()` already takes
`include_symbols`, and `IncrementalIndexer._run_profile_only()` passes
`include_symbols=False` (`ingestion/incremental.py:178`). The capability exists
and is switched off.

**`security_features` deserves a different fix from the rest.** It cannot work
for third-party repos regardless of how often it runs, so the honest rendering
is `skipped_by_design` with the reason, not an empty findings list — the same
treatment the website-ingestion card now gives `self_published`.

## D. Path divergence — a real data-loss case

For **repos**, the local path and the Survey Definition path are the same engine
underneath (`repo_survey_definition_adapter` delegates to
`SurveyOrchestrator.run(steps=[...])`), so findings, metrics and
`last_surveyed_at` are identical. The only difference is that the Survey
Definition path publishes to Egeria at the end and the local path never does.

For **filesystems** they genuinely diverge, and the divergence loses data:
`filesystem/survey_definition_adapter.py` runs the inventory and returns it, but
**never calls `registry.add_filesystem_survey`**. That write happens only inside
`_publish_step_annotations`, which raises if the filesystem has no Egeria asset
— and the executor catches that into `errors`. A freshly-walked inventory is
then discarded, with `last_surveyed_at` untouched.

`update_filesystem_surveyed_at` and `update_database_surveyed_at` both exist and
have **zero call sites**.

## E. Hybrid execution and Egeria as coordinator

- Every checked-in repo Survey Definition declares `executes_at: resource-explorer`
  for every step. **No authored definition exercises any other executor.**
- The dispatch machinery is real: `other_engine_handlers` is wired for database
  and filesystem (`{"egeria": _trigger_egeria_native_survey}`) but **not for
  repo** — the repo adapter passes no handlers at all.
- An `executes_at: egeria` step in a repo definition today hits a deliberate
  closed stub: it errors with `not_executed_no_egeria_handler` rather than
  silently passing. That is correct behaviour and was fixed on 2026-08-24.
- **Egeria never initiates anything.** RE always drives: `SurveyDefinitionExecutor`
  walks the process chain as a client-side Python loop and never registers an
  Engine Action. The only webhook RE exposes is for GitHub.
- `egeria_delegated_step.py` exists, is live-verified, and is **wired nowhere** —
  no `STEP_REGISTRY` entry uses it.

**For Egeria-coordinated surveys** (`survey-execution.md (§7)`, ON HOLD): five
pyegeria client methods are absent (`claim_engine_action`,
`record_completion_status`, and three others), the server has no wired
registration endpoint, and it is blocked upstream on ISSUE-51.

---

## Plan, in priority order

**1. Make the invisible visible** (small, high value)
Results readers + render modes for `architecture_doc_lens` and
`architecture_summary`; fix the `_website_ingestion_headline` signature; add a
test asserting every headline reader matches its call site. All three are
"work already happens, nobody can see it".

**2. Turn on code symbols** (one flag, two analyses)
Decide whether `_run_profile_only` should pass `include_symbols=True`, or
whether symbol extraction gets its own on-demand action. This moves
`api_structure` and `code_symbol_extraction` from 7/60 toward corpus-wide.
Measure the cost first — it is a download-tier step.

**3. Render "cannot apply here" distinctly from "nothing found"**
`security_features` for third-party repos, and any analysis whose inputs are
structurally unavailable, should say so. The four-state website-ingestion card
is the pattern; a two-state empty list is the anti-pattern.

**4. Fix the filesystem Survey Definition path**
Persist the inventory unconditionally, as the database path already does, rather
than only as a side effect of a successful Egeria publish.

**5. Hybrid execution — experiment, do not build**
The cheapest real experiment is a repo Survey Definition with one
`executes_at: egeria` step, to see the closed stub fire and confirm the
dispatch path. Egeria-as-coordinator is a larger piece blocked upstream and
should not start until ISSUE-51 moves.

## Fixed during the audit (uncommitted, awaiting approval)

- **`_website_ingestion_headline` signature** — 0/60 → 42/60. Plus
  `tests/test_headline_readers.py`, asserting every headline reader matches its
  call site and actually runs. The peer's own test had encoded the broken
  contract, so test and subject agreed while the caller disagreed.
- **`security_features` distinguishes three causes** where it previously showed
  one empty list: 57 repos now report `skipped_by_design` naming GitHub's
  admin-only restriction, 2 have findings, 1 reports `never_run`. No repo in the
  corpus renders a bare empty card.
- **`_results_have_data` counted the `_status` envelope as content** — a latent
  bug my change exposed. Explaining an emptiness made the card claim to hold
  results. Now excludes `_status`/`surveyed_at`/`detail`, matching what the
  frontend metrics renderer already did.

## Found and left to the peer session

- **`ArchSummarySurveyor` persisted nothing** — computed its summary, returned
  it as an annotation, stored no row. Fixed by them (`7cddcff`); both results
  views now exist.
- **`evidence_kind` is computed and never persisted.** 212 lens findings across
  11 repos, none carrying it. The results reader's own docstring states it "is
  carried per component and MUST NOT be flattened by a renderer" — asserting a
  guarantee the storage does not provide. Grouped rendering waits on the data.

## Not investigated

- Database analyses were out of scope; only repo kinds were audited.
- Whether `security_features`' 2/60 is token-scope or genuinely admin-only was
  not tested with a second token.
- `architecture_summary` produces no rows under any kind observed; whether it
  writes somewhere unexpected was not chased.
