# Closing the loop on answer feedback

Status: **accepted; the project owner answered §8 on 2026-11-10.**
- The architect read back the first version (f33610dd). This revision folds in that read-back's four
  design changes and its smaller points.
- Step 0, the repair of the existing loop, is built: PR #1015.
- Step 1 is built: the library half is trellis-feedback 0.3 (#1018); RE's half is
  `resource_explorer/feedback_diagnosis.py`, the cause menu and evidence panel in Admin → Feedback, and
  `GET /api/feedback/causes`. Steps 2 to 5 are not built.
- Written 2026-11-09.

Related:
- [`trellis-feedback-extraction.md`](trellis-feedback-extraction.md) is the shared feedback store
  and triage this builds on. Its §6 step 3 put RE's ratings into it.
- [`context-compilation-design.md`](context-compilation-design.md) §13 names the three feedback
  loops and the double-counting guard. §22 covers editable manifests. This document is the "redesign
  AI-response feedback into a real closed loop" workstream that §13 says it is part of.

## Why this doc exists

**Feedback is now collected well.** Since 2026-11-09 (trellis-feedback adoption, parts A to D) every
chat vote and checklist verdict in RE is kept with:
- its question and the full answer;
- the user;
- the compile id, intent and resource;
- an admin-set status and note, appended as an event.

**Almost nothing reads it.** A 👎 changes no later answer, and nobody can tell whether a fix made
answers better. This document proposes how a rating turns into a change, and how we find out whether
the change worked.

## 1. What exists today

### Loops that act automatically

1. **Chunk re-weighting (RE): dead at both ends until step 0.**
   - **The design:**
     - `MetricsCollector.record_feedback` finds the turn's `query_log` row by the question's hash and
       adds the vote to `chunk_feedback` for every chunk that row lists;
     - retrieval adds `(precision − 0.5) × confidence × 0.3` to each chunk's similarity, with
       neutral counting as half.
   - **Findings, 2026-11-09; both silent, both fixed in step 0 (#1015):**
     - **Write:** since `abd936f9` (2026-10-19), chat has answered through `ConversationAgent`, and
       only `RAGSystem` wrote the `query_log` row. A chat vote therefore matched no row. On the
       production database (read by the architect) the last `query_log` row is from 2026-10-20 and
       none of its 45 rows carries a vote, so `chunk_feedback` is empty.
     - **Read:** `vector_store_pg._load_boosts` read `chunk_feedback` with `sqlite3` from the old
       `observability.metrics_db` path, while votes went to `METRICS_DATABASE_URL` (Postgres since
       2026-08-23). The connect created an empty `data/metrics.db`, and a bare `except` returned `{}`.
     - Unit tests of `record_feedback` passed throughout. Step 0's test goes through the routes: ask,
       vote, ask again, and the score has moved.
   - **Even working, it's blunt.** A 👎 marks down every chunk retrieved for that answer, including
     the right ones, when the fault was compression or reasoning (§2, §3.4).
2. **Collection multipliers (EA).**
   - `perspective_routing.get_feedback_adjustments` turns per-collection 👍/👎 counts in EA's own
     `query_metrics` into routing multipliers, once a collection has 3 or more votes.
   - It reads only EA's votes, in EA's schema. §3.4 keeps it that way.
3. **Disagreement → gap (RE).**
   - A `disagree` on a resource-scoped answer becomes a gap marked `ours`, charged to the analysis
     the question catalog names (`gaps.record_disagreement`).
   - That works for the Questions checklist. For free-text chat the catalog names no analysis, so
     the gap is "attributed to none": recorded, but not routable.

### Kept, but read by no automated process

- **Answer items** (trellis-feedback, `kind = answer`): vote, question, answer, user, compile id,
  intent, resource and session.
- **Triage events:** status, note, actor and sequence for every item, including Curate's
  `resource_feedback` rows, which are triaged by reference.
- **Compiles** (`context_compiles`, not pruned): the stored manifest and text. A vote's compile id
  leads to exactly the evidence the model saw.

## 2. The missing piece is a diagnosis

§13's point, made concrete: **a 👎 is a symptom, and the right fix depends on the cause.**

### 2.1 Causes

| Cause | What went wrong | Where the fix lives |
|---|---|---|
| `catalog_gap` | nothing we have answers this question | a new analysis or question; a gap or RFA |
| `missing_evidence` | the analysis that answers it exists, but its result wasn't there (not run, or not in the spec) | run the analysis, or add it to the spec |
| `stale` | the evidence was there but out of date | rescan or reschedule (Automate) |
| `analysis_defect` | the stored result itself is wrong (a surveyor or analysis bug) | the analysis's owner |
| `wrong_evidence` | the compile picked sections or chunks that weren't relevant | retrieval (chunk scores, §3.4) |
| `over_compressed` | the right section was there, cut to a rung too low to answer from | the compile spec's budget and rungs (§22's "go deeper on that one") |
| `reasoning` | the model had what it needed and answered badly | the prompt, model tier or answer template |
| `unclear_question` | the question could reasonably be read another way, or used a name we don't know | catalog phrasing and aliases |
| `not_an_issue` | the answer was right | nothing; it closes the item |

`analysis_defect` and `unclear_question` were added at read-back. Without them, a wrong stored
result gets filed as `stale`, `wrong_evidence` or `reasoning`, and an ambiguous question as
`not_an_issue`. Both have a different fix and a different owner.

### 2.2 Precedence: the earliest stage at fault

Two people reading the same failure must pick the same cause, or no count by cause means anything.
**The rule: the cause is the earliest stage at fault**, in this order:

`catalog_gap` → `missing_evidence` → `stale` → `analysis_defect` → `wrong_evidence` → `over_compressed` → `reasoning`

The test at each stage is **"would fixing this alone have given a right answer?"** The first stage
where the answer is yes is the cause. `unclear_question` and `not_an_issue` sit outside the chain:
they say the failure is not ours to fix in the pipeline.

A **secondary cause** may be recorded, for measurement only. Nothing routes on it.

### 2.3 What the manifest can suggest

A suggestion is shown with **the rule that produced it**, and the admin confirms or changes it. A
suggestion is never recorded as a diagnosis by itself. The manifest (`context_compile.py`,
l.1511–1550) supports more than the first version used:

| Cause | Manifest signal | Rule shown |
|---|---|---|
| `catalog_gap` | an empty `coverage` | "no section of the spec covers this question" |
| `missing_evidence` | a gap judged **absent**; a gap judged zero is a real "none" and doesn't count | "section X had no result" |
| `stale` | `declared[k].as_of` older than the resource's last change or a set age | "section X is as of <date>" |
| `over_compressed` | a section at rung `IDENTIFIERS`, or listed in `dropped` / `deferred` | "section X was packed at identifiers only" |
| `analysis_defect`, `wrong_evidence`, `reasoning`, `unclear_question` | none | human call only; nothing is suggested |

The evidence panel (§3.1) reads the **stored** manifest and text. It never re-compiles: a
re-compile shows today's evidence, not the evidence behind the answer being judged.

## 3. Proposal

### 3.1 Diagnose at triage

- **Data:** `cause` (and an optional `secondary_cause`) on the triage event, checked against the
  vocabulary like the status, and append-only: a re-diagnosis is a new event.
- **UI:** Admin → Feedback gains, for an answer item:
  - the stored compile beside the answer: its sections, rungs, gaps, `coverage`, `dropped` and
    `deferred`;
  - the suggested cause with its rule;
  - a cause menu next to the status menu that part C built.
- **Who may diagnose (§8 Q2): admins only**, the same as triage. Answer feedback stays admin-only,
  as the feedback doc's Q3–Q5 promised.
- **The charge set: which analyses an item is about.** It comes from the answer's stored manifest,
  and is wider than what was packed, because the analysis most often at fault is the one that is
  *not* in the answer:
  - packed sections;
  - gaps judged **absent**;
  - `dropped`;
  - the `deferred` entries `coverage` names.
  The by-cause view groups items by analysis on this set and orders each group by the role the
  section played: absent → dropped → packed at a low rung → packed in full. `catalog_gap`,
  `unclear_question` and answers with no stored compile (RAG and agent turns) are charged to no
  analysis.
- **Product items:** page-widget reports keep status only. Their cause is a UI or product question,
  outside this loop.

### 3.2 Route each cause to one consumer

One consumer per signal (§13's guard), named here so there's no second, silent one.

| Cause | Consumer | What it does |
|---|---|---|
| `catalog_gap` | gaps / RFA | a gap with no analysis, plus an RFA to the catalog owner |
| `missing_evidence` | gaps | a gap `ours` on the analysis named by the manifest's gap, so a chat 👎 becomes routable where today it's "attributed to none" |
| `stale` | Automate | proposes a schedule for the analysis behind the stale section |
| `analysis_defect` | the analysis owner | a gap `ours` on the analysis, marked as a defect, not a missing run |
| `wrong_evidence` | chunk scores (§3.4) | **only** chunk scores. Section weights are not tuned from this signal; they get their own measured input later (context-compilation design §9) |
| `over_compressed` | the compile spec | an entry in the spec-editing loop (§13 loop 2), naming the section and the rung that wasn't enough |
| `reasoning` | a review list | read by a person, not by a weight |
| `unclear_question` | catalog phrasing / aliases | an RFA to the catalog owner, with the question as asked |

### 3.3 Replay: a fixed failure becomes a check

- **The case:** when an admin marks a diagnosed 👎 `actioned`, it becomes a replay case:
  - the question, the resource (or none) and the perspective;
  - the original answer and its compile id;
  - the cause;
  - an optional "must contain" list: short fact statements, or "must not" statements, written by
    the admin. It is not a reference answer.
- **Two separate results, never merged:**
  - **Evidence fixed (precondition):** a deterministic check on the new compile, one per cause:

    | Cause | Check |
    |---|---|
    | `catalog_gap` | `coverage` now includes a section for the question |
    | `missing_evidence` | the section that had no result now has one |
    | `stale` | the section's `as_of` is newer than in the original compile |
    | `analysis_defect` | the analysis's result changed since the original compile. That is only **evidence changed**: a new wrong value passes too, so a case with this cause is verified only by the judge, and needs a "must contain" list |
    | `wrong_evidence` | the chunks the admin marked irrelevant are no longer in the top results |
    | `over_compressed` | the section is packed above the rung that failed |
    | `reasoning`, `unclear_question` | none; these are judged only |

    These test the model's **input**, not its answer, which is why they're reported apart.
  - **Answer verified:** an LLM judge checks the new answer against the "must contain" list, only
    when one exists. The judge is pinned: one model, temperature 0.
- **Why a result changed:** each run records the compile's candidate hash. When a case's state
  changes, the hash says whether the data moved, the code moved, or neither.
- **On current data:** replay compiles against today's data. An "as-of" replay would need result
  history we don't keep, so the Admin view says "on current data".
- **Regressions:** a case that passed and then fails the judge **twice in a row** reopens its item
  (`new`, actor `replay`), so a regression lands in the same queue as a fresh report. A single
  failure is shown but reopens nothing.
- **Scope:** replay runs as the service identity. A case whose original answer depended on the
  asker's zones may not replay faithfully, and such cases are marked so.
- **Placement:** a nightly run, attached to the scheduler RE already has. Cases are few at first,
  and each costs one compile and one answer.

### 3.4 Chunk scores, rebalanced

Counting every 👍 but only diagnosed 👎s would push precision up for any popular chunk, and with the
centre at 0.5, nearly every chunk would get boosted. So, once diagnoses exist:

- **👍 counts only for the chunks the answer used**, not for every chunk retrieval returned. On a
  compiled answer, "used" means packed. On the ConversationAgent path every retrieved chunk goes
  into the model's context, so packing says nothing there. "Used" must mean cited: a chunk the
  answer quotes or names as a source. Until the agent reports its citations, this rule changes
  nothing on that path, and the doc says so rather than implying otherwise.
- **👎 counts only when diagnosed `wrong_evidence`**, and only for the chunks the admin marked
  irrelevant (the panel lets them pick; all of the answer's chunks is the default).
- **Undiagnosed 👎s stay out of the total**, so they neither lower nor dilute a chunk's score.
- **The centre is the observed helpful rate**, not 0.5: the adjustment is
  `(chunk precision − overall helpful rate) × confidence × 0.3`. A chunk is boosted for being
  better than average, not for being in answers people liked.
- **Scope across apps:** each app's retrieval learns only from its own app's votes. EA's collection
  multipliers read EA's `query_metrics` in EA's schema; RE's chunk scores read RE's
  `chunk_feedback` in `resource_explorer`. Step 0 reads on the writer's own URL and search_path, so
  this holds today, and the shared feedback store doesn't change it.

Until diagnoses exist, step 0's raw-vote scoring runs as it was designed in 2026-08.

### 3.5 Measure

Computed from the store, shown in Admin and logged to MLflow (§12):

- **Helpful rate** (👍 / all, 😐 at half credit) by intent, perspective, resource type and analysis,
  weekly.
- **Cause mix per 100 answers**, weekly, not per 100 👎s. That way a cause's share can't rise just
  because another one fell. A falling rate for one cause after its fix is the evidence the fix
  worked.
- **Time to diagnose** and **time to actioned**.
- **Replay:** the share of cases with evidence fixed, the share with the answer verified, and
  regressions found.

On the page, not in a footnote: vote counts will be small for a long time, so most of these are a
direction, not a measurement.

## 4. What changes, by component

| Component | Change |
|---|---|
| RE `vector_store_pg`, `metrics_collector`, chat routes | **step 0 (#1015):** chat turns write `query_log` with their chunk refs; retrieval reads chunk scores from the writer's URL |
| `trellis-feedback` (0.3) | `cause` and `secondary_cause` on triage events, with their vocabulary; `TriageState` carries the latest; list and count filter by cause |
| RE Admin → Feedback | the stored-compile panel, the suggested cause with its rule, the cause menu, and picking irrelevant chunks |
| RE gaps / RFA / Automate | the consumers in §3.2 |
| RE replay | a replay-case table in trellis-feedback (keyed to the item), the nightly run and its Admin view |
| RE `metrics_collector` | §3.4's rebalanced scoring once causes exist |
| EA | none until EA adopts the library (feedback doc §6 step 4); then the same causes and replay |

## 5. What it costs

- **Admin time is the real cost.** Each diagnosis is a person reading an answer and its evidence.
  The suggested cause with its rule, and the stored-compile panel, are there to make that a minute,
  not ten. The project owner expects 20–200 chat votes a week (§8 Q6), so diagnosis is a weekly routine, and the suggestions and the by-cause views are core to step 1, not extras.
- **A judge model for replay**, only where a case has a "must contain" list. It's pinned and
  nightly; it's the one place an LLM grades an LLM, which is why the deterministic precondition
  checks come first and are reported separately.
- **Privacy:** replay cases keep questions and answers, already kept and admin-only (feedback doc
  Q3–Q5). Replay runs as the service identity, so the original user's id must not travel into the
  replay's own logs.

## 6. Phasing

Each step is its own PR, with tests and an architect read-back.

0. **Repair the loop that exists.** Built: #1015. It covers the write side and the read side, with
   an end-to-end test on SQLite and Postgres, per-turn refs owned by the route, and a short cache.
   Known and left for step 4: a vote finds its `query_log` row by question hash, so two people
   asking the same text can cross. Step 4 carries the exchange or row id instead.
1. **Cause on triage:** trellis-feedback 0.3, then RE's cause menu, the suggestion rules, the
   stored-compile panel, and the count by cause and by charged analysis (§3.1, §8 Q6). No consumers yet; it starts collecting diagnoses.
2. **Routing:** the gaps and RFA consumers, then Automate's.
3. **Replay:** the cases, the nightly run, the Admin view, and reopening on regression.
4. **Rebalanced chunk scoring** (§3.4), once there are enough diagnosed votes to matter.
5. **Measurement views.** These can run alongside 1 to 4; their numbers mean little until there are
   diagnoses.

## 7. Not in scope

- **Fine-tuning a model on feedback.** The volume is nowhere near it, and the causes say most fixes
  are in evidence and specs, not weights.
- **Automatic diagnosis without a person.** Suggestions only (§2.3).
- **Tuning section weights from votes.** `wrong_evidence` feeds chunk scores only (§3.2).
- **§22's editable manifest.** It's a better label than a rating, as §22 says, and it would arrive
  already diagnosed. It's a separate, larger piece of work. An accepted manifest edit could later be
  recorded as a diagnosed item.

## 8. Decisions

**Decision (project owner, 2026-11-10):** the seven questions below were answered in a walk-through
with the project owner. Six follow the recommendation; Q6 adds to the design.

1. **The cause vocabulary:** all nine causes, with the earliest-stage precedence (§2.2) and the
   optional secondary cause.
2. **Who diagnoses:** admins only. The project owner first chose admins plus analysis owners. The
   architect's read-back of 9c28a6bc showed that owners would need to read people's questions,
   which can name people and can't be scrubbed reliably. That breaks the Q3–Q5 admin-only promise
   and needs a notice on every vote. **Decision (project owner, 2026-11-10):** drop owner access.
   If it is reconsidered, these terms, also decided 2026-11-10, apply:
   - owners are declared as `owners:` in `analysis_catalog.yaml` and see items on §3.1's charge set;
   - an owner's view is limited by a read variant of the Egeria zone check (`zone_access.py`), open
     when zones aren't in use;
   - a triage event carries an admin-only note owners never see;
   - owners never see the asker's ids, `compile_id`, `page`, `user_agent` or `email`, and see dates
     without times;
   - an owner's cause routes only when it is about their own analysis; any other cause is a
     proposal an admin confirms;
   - the vote and comment controls carry a notice that owners may read the feedback.
3. **"Must contain" for a replay case:** optional. Expected for `analysis_defect`, whose
   deterministic check shows only that the evidence changed.
4. **Where replay cases live:** their own table in trellis-feedback, keyed to the item.
5. **A regression:** reopen the same item (`new`, actor `replay`) after two consecutive judge
   failures.
6. **Volume:** 20–200 chat votes a week expected on production. Diagnosis is a weekly routine, so
   the manifest suggestions (§2.3) and the by-cause views (§3.5) ship with step 1. The by-cause
   view also groups by charged analysis (§3.1).
7. **EA's collection multipliers:** left as they are; decide at EA adoption.
