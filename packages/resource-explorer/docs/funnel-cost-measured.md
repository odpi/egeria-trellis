# Depth and cost across the tiers — measured

**Status:** live. Measured funnel cost (2026-09-12/13); re-derivable from the committed resolver.

Answers the designer's *Funnel Cost Measurement Spec* (rev 2, 2026-09-09) and applies the
rulings from their reply of 2026-09-13. Numbers re-run read-only against the live registry on
2026-09-12/13 (1,287 `activity_log` rows, 87 `runs` rows). The resolver is committed code —
`resource_explorer/tier_resolution.py`, 18 tests — so every number here can be re-derived and
disagreed with. The scripts that produced the tables are reproducible from it in a few lines.

**Decision (designer, 2026-09-13):** *"the 291 stay in the denominator."* Every table below keeps
the rows it cannot place, as their own row, because "we cannot say" is a finding and "no repo
reached Analysis" would have been a fabrication with the same arithmetic behind it.

**Decision (designer, 2026-09-13):** stop calling it a funnel. A shape that widens as it deepens is
not a funnel, and drawing it as one makes the reader's eye correct the data rather than read it.
This document draws **reach per tier** as independent bars against the honest denominator, and
does not use the word *retention*.

---

## The ladder

`scouting = 1 · discovery = 2 · assessment = 3 · analysis = 3`.

The ladder ranks **commitment** — how much attention a resource has been given — not cost or
quality. Rule 17's axis (*collects* vs *reasons over what was collected*) puts scouting below the
other three and does not order assessment against analysis; they are peers.

`understanding` is **not on the ladder**. It has zero catalog entries, and unlike `enrichment` and
`automate` — whose zero is by design (rule 17: served elsewhere) — its zero is unexplained. That is a
different zero and it is reported as one: *understanding · no analyses in the catalog · not costed.*
`tests/test_tier_resolution.py` fails if an `understanding` analysis ever appears, so this gets
re-opened by the instrument rather than by memory.

## 0.3 — resolving a row to its tier

`activity_log.intent` is a write-time snapshot; the catalog has been retagged twice since most rows
were written. `tier_resolution.py` resolves each row to its **current** tier — an `analysis_run` via
its `analysis_id`, a `survey` via step ownership (`REPO_ANALYSIS_STEP_MAP`) — and says when it
cannot.

| resolution | rows | meaning |
|---|---:|---|
| attributed | 369 | tier(s) known from today's catalog |
| **unattributable** | **291** | a survey predating step recording — which analyses ran is unknowable, not "none" |
| unknown-analysis | 0 | an id no longer in the catalog |
| not-a-run | 627 | catalog / scout / RFA rows — not on the ladder |

Honest denominator: **660** rows, of which 291 (44%) can only be reported as *cannot say*.

Drift, measured: **190 of 369 attributed rows (51%)** carry an `intent` that disagrees with the
catalog. The column shows zero `analysis` rows; resolved, `analysis` is the largest tier (185).

| recorded `intent` → resolves to | rows |
|---|---:|
| discovery → analysis | 57 |
| assessment → analysis | 54 |
| assessment → discovery | 52 |
| assessment → scouting | 17 |
| discovery → scouting | 6 |
| discovery → assessment | 4 |

---

## §2 — Reach per tier

> **On the 26 repos with step recording — the recently surveyed ones, which are the most likely to
> have gone deep — depth does not narrow.** The 63 repos without step recording are the older,
> shallower ones; the selection bias points the same way as the finding.

Repos only (databases and filesystems have no attributable runs). 89 repos have any survey history:
26 attributable, 63 *cannot say*.

```
reach per tier, of 89 repos with survey history
                 reached   cannot say   no run at this tier (of the 26 we can see)
scouting     ██████████████████████ 22   ░░░░░░░ 63    4
discovery    ██████████████████ 18       ░░░░░░░ 63    8
assessment   ██████████████████ 18       ░░░░░░░ 63    8
analysis     █████████████████████████ 25 ░░░░░░░ 63    1
understanding  — no analyses in the catalog · not costed —
```

| tier | repos reached | cannot say | not reached (of 26 visible) |
|---|---:|---:|---:|
| scouting | 22 | 63 | 4 |
| discovery | 18 | 63 | 8 |
| assessment | 18 | 63 | 8 |
| analysis | 25 | 63 | 1 |

**Deepest tier reached:** all 26 visible repos reach analysis/assessment. More repos reach
`analysis` (25) than `scouting` (22): the visible set went deep, and 3 of them went deep without a
recorded scouting run at all. *Everything we can see deeply, we saw deeply* — a statement about the
visible set, not about behaviour.

What would make this untrustworthy — and does: 26 of 89 is the **permanent** visible set for
everything before step recording. Steps cannot be recovered from the rows. Every chart of this
measurement says so for as long as that is true. The human / scheduled / Egeria split the spec asks
for was not possible: `runs.requested_by` covers only queue-era rows.

Cross-check (`docs/coverage-audit-2026-09-11.md`): on the **14 repos a person chose to keep
investigating**, 9–13 of 14 cannot answer the assessment/analysis-tier questions, and only 9
deep-tier runs happened on the 2 stopped repos. Depth respects stops; it is not driven by keeps.

---

## §4 — Where decisions happen

Repo-only. 40 `repo_disposition_history` rows; 16 terminal transitions, 24 non-terminal
(tracking / investigating / undecided — a queue, not decisions). The history table carries a
per-transition timestamp, so this is point-in-time as specified.

| disposition | deepest tier at the moment of the decision | transitions |
|---|---|---:|
| recommended | no attributable run before the decision | 4 |
| using | no attributable run before the decision | 1 |
| using | analysis/assessment | 7 |
| abandoned | no attributable run before the decision | 2 |
| abandoned | analysis/assessment | 1 |
| ignored | no attributable run before the decision | 1 |

**8 of 16 terminal decisions had no attributable run before them.** ("No attributable run" includes
"only unattributable surveys existed" — some were decided after shallow surveys we cannot place.)

**The finding, §2 and §4 together: depth is not driven by the keep/stop decision.** The deep runs
happened on whatever was convenient to survey. Remedy (coverage audit, endorsed by the designer with
one condition): *keep investigating* should **offer** the deeper surveys — the same preview
component as *Run across N*, with what depth would cost on this resource — and never queue them
silently. Instrument the decline: if people mostly decline, the finding becomes "depth is not worth
its price here", which is worth knowing too. → /next session, disposition UI.

**Rationales:** `abandoned` 2 of 3 and `ignored` 1 of 1 carry a reason; `recommended` 0 of 4 and
`using` 0 of 8 carry none. **Decision (designer, 2026-09-13):** leave the asymmetry alone — people
explain why they stopped, not why they continued. Prompt for a reason only when a verdict
**reverses a terminal one**; never on a first verdict. No reversal has happened yet; the prompt
should exist before one does. → /next session.

Databases and filesystems have **no disposition mechanism**. On a non-repo the disposition row should
say *no verdict can be recorded for a database yet*, not render an empty chip set. → /next session.

---

## §1 — Cost per tier: a declared prior, and a first measurement

> **Correction (2026-09-13, overnight).** Every *measured* figure in this section is **Egeria
> publish latency, not analysis cost.** Profiled and then measured on one real run of
> `language_file_classification` through the worker's own path (`scratchpad/lfc-publish/REPORT.md`,
> reproduced in the Backlog): the three survey steps took **0.25 s**; the synchronous auto-publish
> took **558 s** — 53 sequential Egeria writes (46 annotations + 7 evidence links) at a **median of
> 9.4 s each** (p90 15.3 s, max 21.2 s, zero failures, zero concurrency). Every repo that has been
> run has an assigned Egeria asset, so every row in `runs` includes this. The table below therefore
> measures *what a run costs the person who pressed the button*; it says nothing yet about the
> ladder. The `fast` label on `language_file_classification` was **right**. The remedy — per-phase
> timings on the run's activity row (`steps_seconds` / `publish_seconds`) and an enqueue-only
> auto-publish behind a flag — is on branch `re/auto-publish-enqueue-only`, default unchanged
> pending the owner's decision. The freshness skip's price sentence stays true (that *is* what a
> re-run costs) but prices the wait, not the thought.
>
> **Ruling (designer, 2026-09-13):** publish stays out of the ladder; wall clock is shown only
> where someone is deciding, and always split.

The per-phase timings landed (#67): `execute_and_record_analysis` now writes `steps_seconds` /
`publish_seconds` / `publish_mode` onto every run's activity row, and `estimate_run_cost` /
`RunCost` split on them the moment at least one row carries them. Per the ruling above, the
**measured** column below is "run (thought)" wherever a split exists, with a separate
**publish (waited)** figure beside it — labelled as what pressing the button costs *this run*, not
as a property of the tier, since publish scales with annotation count. Everything below §1 was
measured before the split instrumentation existed, so it is still the single, publish-dominated
figure the correction box above describes; **not yet split** marks a cell rather than implying the
old figure was ever "just" analysis cost.

**Decision (designer, 2026-09-13):** the interim prior renders as **declared**, visually distinct
from measured and labelled as such. A declared figure sitting in a cost chart that looks measured is
the failure this spec was written to prevent.

`runs` now holds 87 rows (12 on 2026-09-09), so a first measurement exists beside the prior — thin,
and said to be.

| tier | analyses | **declared** `run_time` fast / minutes / async | measured: analyses with ≥1 succeeded run | **measured** median of per-analysis medians — run (thought) | **publish (waited)** — what the button costs, not a tier property |
|---|---:|---|---|---:|---:|
| scouting | 3 | 2 / 1 / 0 | 2 of 3 | 156 s — not yet split | not yet split |
| discovery | 8 | 8 / 0 / 0 | 4 of 8 | 18 s — not yet split | not yet split |
| assessment | 14 | 11 / 3 / 0 | 11 of 14 | 22 s — not yet split | not yet split |
| analysis | 11 | 8 / 3 / 0 | 7 of 11 | 49 s — not yet split | not yet split |
| curate | 1 | 0 / 1 / 0 | 0 of 1 | — | — |

The one analysis with a split row so far, read live off the registry 2026-09-13 (one instrumented
run): `language_file_classification` — steps (run) **0.06 s**, publish (waited) **92.3 s**; the
sentence `estimate_run_cost` now produces is *"A re-run takes about 0.1s to run and about 1m 32s to
publish (median of 1 run); about 2m 36s in all."* The whole-run median in the table above (156 s)
predates this instrumented row and is a different sample of the same analysis — the two are not
expected to reconcile until there are enough split rows to median over.

All 37 repo analyses are `source: local` except the one `curate` entry. Per-analysis medians, n:

| tier | analysis | median | n | declared |
|---|---|---:|---:|---|
| scouting | language_file_classification | 155.8 s | 5 | fast |
| scouting | repository_health | 13.6 s | 19 | fast |
| discovery | interface_surface | 19.7 s | 4 | fast |
| discovery | repo_conventions | 17.9 s | 3 | fast |
| discovery | maturity | 7.2 s | 1 | fast |
| discovery | license_classification | 2.9 s | 1 | fast |
| assessment | secret_scan | 330.8 s | 1 | minutes |
| assessment | security_features | 39.8 s | 3 | fast |
| assessment | telemetry_scan | 24.1 s | 3 | minutes |
| assessment | security_scan | 23.6 s | 2 | fast |
| assessment | chaoss_metrics | 22.7 s | 5 | fast |
| assessment | documentation_coverage | 21.7 s | 1 | fast |
| assessment | sla_content | 20.2 s | 3 | fast |
| assessment | cii_badge | 15.9 s | 4 | fast |
| assessment | foss_scorecard | 9.7 s | 6 | fast |
| assessment | cve_scan | 7.7 s | 1 | minutes |
| assessment | community_support | 1.0 s | 2 | fast |
| analysis | data_file_profiling | 788.6 s | 1 | minutes |
| analysis | architecture_diagram | 72.1 s | 2 | fast (derived; runs the recovery's steps) |
| analysis | manifest_parse | 69.4 s | 1 | fast |
| analysis | architecture_recovery | 49.1 s | 2 | fast |
| analysis | api_structure | 31.2 s | 3 | fast |
| analysis | sub_resource_survey | 21.9 s | 1 | fast |
| analysis | dependency_analysis | 10.6 s | 3 | fast |

What this says, and how far it can be trusted:

- **The declared ladder is not monotonic and does not claim to be**: 29 of 37 analyses are declared
  `fast` regardless of tier. Declared `run_time` is an availability signal (rule 17: may a compiler
  run this inline), not a cost model, and it was split from `availability` for exactly this reason.
- **The first measurement is inverted at the bottom** — and the correction above explains it:
  scouting's median-of-medians (156 s) is `language_file_classification`, whose steps take 0.25 s
  and whose 46 annotations take ~9 minutes to publish. A scouting analysis produces *more*
  annotations than a deep one and so pays more publish latency. The label is right; the clock was
  timing Egeria.
- **n is tiny**: 13 of the 24 measured analyses have n ≤ 2. Medians over n = 1 are the value, not a
  median. Wall time is still the proxy; the token and acquisition counters (§6) are accumulating
  but no run has enough of them yet to split *waited* from *thought*.
- Cold/warm is not split: `acquisition.py` records it per run but the queue-era rows predate it.

---

## §3 and §5 — not run

§3a was overtaken by the coverage audit (three questions have no analytic at all; seven more are
human by design). §3b and §5 were not run — the designer's ruling is that neither would change a
decision, and the backfill that would move the numbers cannot be done from the rows.

---

## §6 — The instrumentation: built, and the skip names its price

| counter | where | what it records |
|---|---|---|
| tokens in / out | `observability/llm_usage.py` — `complete()` and `stream()`, all three backends | counted, or `record_uncounted()` when a backend returns no usage, so a zero says which zero; persisted to MLflow with an `llm_usage_complete` flag |
| acquisition | `observability/acquisition.py`, hooked in `SourceCache.get()` | **cold / warm / not-consulted** — three states, not "bytes fetched"; a cache hit and a run that never needed the source are different facts |
| Phoenix | `PhoenixConfig.project_name` | traces in their own project |

**Freshness gate** (`workflows/analysis.py`): user-triggered runs are skipped by default when a
result is younger than `freshness_seconds`; `force=true` overrides; the scheduler is exempt
(project owner, 2026-09-10).

**Decision (designer, 2026-09-13):** *the skip says what it saved.* The skip payload now carries
`rerun_cost_seconds` / `rerun_cost_basis` / `rerun_cost_runs` / `rerun_cost_via`, and its sentence
reads e.g. *"'architecture_diagram' already has data from 12 minutes ago (via
'architecture_recovery'); not running it again. A re-run costs about 1m 12s (median of 2 runs)."*
The basis is one of three — **measured** (median of succeeded `runs` rows; a derived analysis costs
what its source costs), **declared** (*"declared 'fast' in the catalog — not yet measured"*), or
**unknown** — so a declared word is never mistaken for a measurement. When §1 has weeks of `runs`
behind it, the measured value replaces the declared one per analysis and the label changes with it;
that is already how `estimate_run_cost` behaves.

---

## What would change these numbers

- Nothing recovers the 291 unattributable rows; 26 of 89 is the permanent visible set for pre-step-
  recording history.
- Time: `runs` at weeks rather than days makes §1 a measurement instead of a first look.
- The keep-investigating offer: §2 + §4 say depth is not gated by keep/stop today; the offer, and
  its decline rate, is what would make depth a decision.
