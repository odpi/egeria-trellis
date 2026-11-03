# Review — where extensibility stands, against egeria-workspaces#603 (2026-10-30)

> Superseded in part by Q2(b) (#956) and pyegeria 6.2.18 (#959): analytics register through `pyegeria.analytics`, report specs through `pyegeria.report_specs`; `PYEGERIA_REPORT_SPEC_MODULES` is deprecated in 6.2.18 and removed in 6.2.19; dotted function paths are refused in 6.2.19 (pyegeria side: [ISSUE-173](https://github.com/odpi/egeria-python/blob/main/PYEGERIA_ISSUES.md) in egeria-python's PYEGERIA_ISSUES.md, [odpi/egeria-python#523](https://github.com/odpi/egeria-python/pull/523)). This review was read against pyegeria 6.2.16; the registry-only resolver §2.3 calls still open was closed by 6.2.17 (#516).

Owner's words (architect chat, 2026-10-30): "lets review where we are on extensibility - there was an original design
note in egeria-workspaces#603 that was one proposal for extensibility in the portal - please review that as well as
your own extensibility designs and see if we need to update or change anything" … "and perhaps close #603".

Read: odpi/egeria-workspaces PR #603 (open, docs only, 2026-10-04, head dwolfson/egeria-workspaces
docs/analytic-functions-design-note @ 6c3dcc14, `design-docs/analytic-functions-and-metrics.md`, 248 lines, "behind"
main); trellis main 7b179088: DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS (2026-10-02/06),
DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE (2026-10-21), DESIGN-LOCAL-DASHBOARDS-DECLARED-ANALYTICS
(2026-10-28), the extensibility gate result (2026-10-28/30), `trellis_analytics/declaration.py`, `manifest.py`,
`discovery.py`, `portal.py`; pyegeria 6.2.16 `view/analytic_registry.py`, `view/format_set_executor.py`.

## 1. Where RE's extensibility is (the state, not the plan)

- **Built and gated.** One declaration per analysis (`Declaration`: result_kind, measures with unit/as_of/value_field/
  annotation/confidence/scope, four absence sentences, chat extents, confidence form, answers, built, scope). Library
  steps are packages discovered by the `trellis.analytics` entry point; their manifests subclass pyegeria's
  `AnalyticFunctionSpec` and register into pyegeria's registry (`discovery.py`). Four file-system steps and four
  database steps exist outside RE's package. The gate (DESIGN-DECLARED-EXTENSIBILITY §7) walked 2026-10-28/30:
  Survey tab, Chat, Questions row, publish leg and manifest change all PASS; findings F1–F23 are status-words items,
  not architecture.
- **Three consumers of one declaration** are live: Understanding (render, "said not drawn" for `measure`), Chat
  (context compilation from extents), Curate publish table (writes-as from the declared annotation). Confidence is
  declared by form.
- **Dashboards** (2026-10-28 note): every declared analysis becomes a report spec through one generic analytic
  function `re_declared_result`, rendered on a Dashboard Sheet; four owner questions still open; not built.
- **A third channel** (TestGen deployments, 2026-10-30 note) extends the same declaration model with steps that read
  another tool's store; not built.

## 2. What #603 proposed, and what became of each part

#603 was written for the Portal's Overview KPIs and the Governance Metrics panel, before RE's declaration model
existed. It is not a competing design any more; most of it is either done differently in trellis or is a Portal/pyegeria
matter. Item by item:

### 2.1 Its eight lessons → the function contract (§2/§4 of #603) vs RE's Declaration

| # | #603 lesson / contract field | RE today | Verdict |
|---|---|---|---|
| 1 | unknown ≠ 0 → `status: measured/not_measured/partial` | result states measured / not_measured / not_permitted / not_applicable / measured_nothing, each with a declared sentence (`AbsenceSentences`); find-absence-as-answer skill; status-words-from-proof-rows rule | **covered, and stronger** (five states, sentences declared) |
| 2 | existence ≠ outcome | step_runs + result states; ENGINE_REPORT_WORDS for Egeria engine reports; F-list items on "ran" vs "measured" | covered for RE's runs; the SurveyReport completion-status convention (#603 §8.1) is still Egeria/Java-side, open |
| 3 | exclusive categories, `sums_to` | not declared; `result_kind` graph/measure says nothing about whether categories overlap or sum to a stated total | **gap** — add `categories: exclusive|overlapping` + `sums_to` to `Declaration` for graph/measure kinds, so a renderer can refuse to stack bars that double count |
| 4 | capped page = lower bound → `capped` | `ExtentDeclaration.truncation` covers chat extents only; measures have no `capped` | **gap** — add `capped: bool` (and the words "at least") to the measure result; withhold derived figures when set |
| 5 | labelled proxy → `basis {numerator, denominator, label}`, `proxy_for`, caveats | headline readers write "N of M columns" by hand per reader; `unit`, `value_field`, confidence `basis` exist; no declared denominator | **partial** — add `basis` to `MeasureDeclaration` so "456 of 1,236 columns" derives from declared fields, not reader prose; `proxy_for` optional |
| 6 | data-model surprises → `source_types` | pyegeria 6.2.3+ `AnalyticFunctionSpec.source` (KNOWN_ANALYTIC_SOURCES), `ExtentDeclaration.source` | covered at the step level |
| 7 | identity/scope → `context` + cache key parts | action identity = initiating user (ruling, CLOSED 2026-10-24); `scope` on declaration; no declared `context`/cache-key parts | partial — fine for RE (no shared cache by persona); matters for the dashboards tile once Portal personas read RE tiles → put `context` in the dashboards note's generator, not in every step |
| 8 | failure-path fixtures | gate rule: every brief's e2e test covers measured / empty / not-measured (+ not-applicable); manifests carry no `fixtures` | covered as a GATE rule; keep it there, do not add to the manifest |

### 2.2 Its options A / B / C

- **A** (decorator next to the function, spec from the signature): trellis does this with manifest subclasses of
  `AnalyticFunctionSpec` living in the step package — the declaration sits with the code. Done, in trellis form.
- **C** (register through the CONFIG tier, merge into the panel): `discovery.py` registers through pyegeria's registry;
  `PYEGERIA_REPORT_SPEC_MODULES` (removed in 6.2.19) / `portal.py` is the CONFIG-tier module for the Portal tile. Done for registration;
  the *panel merge* half is superseded for RE by the Dashboard Sheet design (RE never targets the Governance Metrics
  panel). For the Portal's own Overview KPIs it remains the Portal's decision.
- **B** (per-column computed attribute): deferred in #603, never needed since; `re_declared_result` as a generic
  action-level function is the answer to #603's question 4 ("action-level is enough"). Keep deferred.

### 2.3 The one item #603 said should not wait — still open

`format_set_executor._resolve_analytic_function` in pyegeria **6.2.16** still imports and calls any dotted path a spec
names; no registry-name-only resolution, no module-prefix allowlist. RE's dashboards slice D1 (`re_declared_result`
as a generic function named from specs) and Dr.Egeria-authored specs both pass through it. Per the pyegeria gaps
rule this is logged, not fixed here: **PYEGERIA_ISSUES.md entry — "analytic_function resolver accepts any importable
dotted path; resolve registry names only (or module-prefix allowlist)"**, and D1's brief must not land before it.

### 2.4 Its ten decisions

| # | Decision asked | Status 2026-10-30 |
|---|---|---|
| 1 | panel backed by Egeria GovernanceMetric vs registry | Portal's call; RE: Dashboard Sheet + report specs, no panel |
| 2 | which prose wins | RE: the declaration is the one source (rendering, chat, publish, future tile all read it) — decided |
| 3 | panel entries for generic functions | moot for RE; `re_declared_result` is generic and the tile shows the declaration's words |
| 4 | action-level `analytic_function` enough? | yes — decided by the dashboards note |
| 5 | where functions live | trellis: in step packages outside RE (`trellis.analytics` entry point); shared ones in trellis-analytics; pyegeria holds the registry only — decided |
| 6 | adopt the result type + metadata; who reviews | adopted in trellis form; the Architect's gate reviews semantics; **gaps 3/4/5 above** |
| 7 | who may author specs naming callables; registry-only resolver | **open in pyegeria** (2.3) |
| 8 | caps/timeouts defaults, heavy functions | RE: priced line before a run; budgets in steps (e.g. frequency 5 min); no declared `cost` field — acceptable, note for the dashboards generator |
| 9 | one survey-outcome standard RE and engine surveys both write | **open, cross-repo** (Mandy, Java side); RE reads engine words via ENGINE_REPORT_WORDS meanwhile |
| 10 | persona-dependent metrics on a shared panel | Portal's call; RE tiles run as the viewer (initiating user) |

## 3. What to change (recommendations)

1. **Declaration additions** (trellis-analytics, small, backwards compatible, with the renderer and headline reader
   consuming them): `capped: bool` on a measure result; `basis {numerator_field, denominator_field, denominator_label}`
   on `MeasureDeclaration`; `categories: exclusive|overlapping` + `sums_to` on graph/measure declarations. These are the
   three #603 lessons RE's model does not yet state. One brief; the e2e test asserts a capped measure says "at least",
   a basis renders "N of M <label>", and an overlapping graph is not stacked.
2. **Gate amendment** (DESIGN-DECLARED-EXTENSIBILITY §7): add a leg for those three fields; keep fixtures a gate rule.
3. **PYEGERIA_ISSUES.md**: the resolver allowlist (2.3), marked as blocking dashboards D1.
4. **Working-assumptions note**: append #603's eight lessons and ten decisions with the statuses above as an
   appendix, so the Portal-era reasoning is kept in trellis after the PR closes.
5. **Close egeria-workspaces#603** with a comment pointing here (superseded; lessons carried; two items re-homed:
   resolver → pyegeria issue, survey-outcome convention → cross-repo with Egeria). The Portal keeps its own copy of
   the question for the Overview KPIs if it wants one; nothing in RE waits on it.
6. **No change** to the three-consumer model, the entry-point discovery, the Dashboard Sheet choice, or the channel
   design: #603 confirms them rather than contradicting them.
