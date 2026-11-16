# Feedback and curation — signals, triage, and what curation still owes

**Status:** consolidated design. Current as of 2026-09-02.
**Scope:** the several kinds of feedback the system collects, why they are not one
thing, how they get triaged, and the curation capabilities that were named but
not built.

> **This document consolidates three.** Part I surveys the feedback signals
> across RE and Egeria Advisor and argues against unifying them. Part II merges
> the triage gap measured against Egeria Workspaces Portal, and the scoped Curate
> follow-ups. Part III is the settled register.

## 1. What Egeria Advisor actually has

### 1.1 The signal: three buckets, not two

EA's chat feedback widget is a three-button control — 👍 / 😐 / 👎 — not RE's two-button one.

- UI: `advisor/web/static/index.html:2001-2037` builds three buttons; `btnNeutral.dataset.vote
  = '0'` (`:2018`).
- Wire format: `FeedbackRequest.vote: int  # 1 = positive, 0 = neutral/partially correct, -1 =
  negative` (`advisor/web/app.py:131-135`).
- Server mapping: `advisor/web/app.py:1815-1827` — `req.vote > 0` → `"positive"`, `== 0` →
  `"neutral"`, else `"negative"`.
- Storage: `FeedbackEntry.rating: str` is one of `"positive" | "negative" | "neutral"`
  (`advisor/feedback_collector.py:29`), with a normalizer (`get_normalized_rating`,
  `:48-59`) that maps `neutral` to `0.5` when a numeric score is needed.

So the third bucket is **"partially helpful / partially correct"** — a real middle value on the
same *whole-response satisfaction* axis as thumbs up/down, not a different kind of signal
(it isn't a flag, a category, or free text; the star-rating and category fields are separate
optional fields on the same entry, `:39-41`).

### 1.2 Storage shape

EA's chat feedback is **not** a Postgres table of its own. It's two files plus a best-effort
Postgres side-write:

- Append-only JSONL, one entry per vote: `data/feedback/user_feedback.jsonl`
  (`advisor/feedback_collector.py:76-78`, path default `:74`).
- A second, richer JSONL written directly by the route handler, `data/feedback/
  feedback_extended.jsonl` (`advisor/web/app.py:1842-1861`), carrying `vote`, `rating`,
  `response_text`, `triage_status: "new"`, `analysis_comments: ""` — this is EA's triage
  record, parallel in *purpose* to RE's `feedback.triage_status`, but it's a flat file with
  index-addressed rows (`PATCH /api/feedback/extended/{idx}` mutates `lines[idx]`,
  `advisor/web/app.py:1890-1911`), not a table with an id-addressed row.
- A best-effort `UPDATE query_metrics ... WHERE id = (SELECT id FROM query_metrics WHERE
  query_text = %s ORDER BY timestamp DESC LIMIT 1)` (`advisor/feedback_collector.py:165-186`)
  folds `rating`/`star_rating`/`suggested_collection`/`feedback_text` back onto the most recent
  matching row of EA's own Postgres table `query_metrics` (schema at
  `advisor/db_consolidated.py:18-21`, feedback columns added `:216-219`). This is a
  correlate-by-text-match, not a foreign key — it can attach feedback to the wrong row if two
  identical queries race, a real (if narrow) gap in EA's own model worth knowing about before
  treating `query_metrics` as an authoritative feedback source.

### 1.3 What the admin panel summarises

`advisor/web/static/admin.html`, fed by `GET /api/feedback/analysis`
(`advisor/web/app.py:1915-1923`, which calls `feedback_collector.py`'s `get_feedback_stats()`,
`get_gap_analysis()`, `get_routing_improvements()`), shows:

- **Overall Satisfaction** card: `sat = round((pos + 0.5*neu) / total * 100)`
  (`admin.html:570`) — this is the one place the neutral bucket's 0.5 weighting is actually
  used to produce a headline number, plus a 👍/😐/👎/total breakdown (`admin.html:579`) and
  positive/negative/neutral counts (`:582-596`).
- **By routing agent** and **by query type** breakdowns (`feedback_collector.py:230,239-240`
  populate these; rendered starting `admin.html:598` onward).
- **Gap analysis** (`feedback_collector.py:385-455`): queries that fell to RAG fallback *and*
  got a negative vote; perspectives/agents/query-types whose satisfaction rate (positive ÷
  total, run only at ≥5 samples) is below 50%.
- **Triage review table** (`admin.html:709-780`): every extended-feedback record, filterable by
  vote (`admin.html:111-115`, `:730`) and by a **triage vocabulary of its own** — `new,
  known_issue, actioned, deferred, not_an_issue` (`admin.html:698-704`). Each
  row has an inline triage-status `<select>` and a free-text "Analysis notes" textarea
  (`admin.html:743-770`), persisted via the `PATCH .../extended/{idx}` endpoint above.

Note EA's triage vocabulary (`new, known_issue, actioned, deferred, not_an_issue`) is a
**superset**, not a rename, of RE's (`new, triaged, actioned` —
`resource_explorer/feedback_store.py:28`): EA distinguishes "we know about it and are not
fixing it" (`deferred`) from "this isn't actually a problem" (`not_an_issue`), which RE's
`triaged` collapses into one bucket. This is a real, useful distinction, not accidental
divergence — see §3.

## 2. What RE has today

Per the task brief's table (verified below), plus the fourth, uncounted store the task didn't
list.

### 2.1 `feedback` — page-level widget

- Schema: `resource_explorer/feedback_store.py:86-108` — `session_id, page, element_guid,
  rating (1-5, nullable), category (bug|confusing|suggestion|praise), message, email,
  wants_response, consent_to_contact, build_version, user_agent, viewport, locale,
  triage_status (new|triaged|actioned), created_at`.
- Written by: `POST /api/feedback` → `submit_feedback`
  (`resource_explorer/web/routes/feedback.py:49-71`), which is the public, unauthenticated
  submission path — a page-level "report a problem with this screen" widget.
- Read by: the **standalone static page** `resource_explorer/web/static/admin-feedback.html`
  (own admin-token gate, `:38-40`; fetches `/api/feedback/stats` and `/api/feedback`,
  `:93,107-108`; renders `avg_rating`, star glyphs `:126`, and per-row triage buttons
  `:138-144`). This page is not reachable from the main app's nav — it's a separate URL.
- Admin API requires `is_admin_request` (`resource_explorer/web/admin_auth.py:21-38`, fail
  closed if no token/user configured — `:11-13`).
- 9 rows, back to 2026-08-11, per the task brief (not independently re-queried here — see
  §7 on migration verification for how that count should be checked before any write touches
  this table).

### 2.2 `resource_feedback` — per-resource, from Curate

- Schema: `resource_explorer/registry.py:1233-1242` — `entity_type, entity_slug, rating (1-5,
  nullable), category, message, created_at`. No `session_id`, no `page`, no `element_guid`, no
  `wants_response`/`consent_to_contact`, no `build_version`/`viewport`/`locale`. This is a
  narrower, resource-scoped shape by design — it's about **the resource's** trustworthiness,
  not **a screen's** usability.
- Written by: `POST /curate/feedback/{entity_type}/{slug}` → `add_feedback`
  (`resource_explorer/web/routes/curate.py:104-109`), from a resource's Curate tab
  (`resource_explorer/web/static/index.html:8579` posts here).
- Read by: the resource's own Curate tab (`index.html:8400`), and — as of this reading — Admin
  → Observe → 💬 Feedback (`loadAdminFeedbackPanel`, `index.html:12640-12712`), which calls
  `GET /api/curate/feedback` (`curate.py:79-96`, `registry.py:2020-2052` for the query,
  `:2053-2061` for counts). **As read right now, `loadAdminFeedbackPanel` queries only
  `/api/curate/feedback` — it does not yet also query `/api/feedback` (the page-level store).**
  The docstring comment at `index.html:628-634` states the intent to badge both stores together
  in this same panel; that part of the concurrent agent's work was not yet present in the code
  at the time of this survey. Do not treat this doc's description of the panel as the finished
  state — re-read `loadAdminFeedbackPanel` before building on it.
- 0 rows currently, per the task brief.

### 2.3 `chunk_feedback` — RAG retrieval-quality signal (this is the "thumbs up/down in chat")

This is the store the task asked to trace and it is **not a triage table at all** — it's a
retrieval-quality accumulator, structurally closer to EA's per-collection satisfaction
breakdown than to either of RE's other two stores.

- Written by: `POST /api/query/feedback` (`resource_explorer/web/routes/query.py:277-281`),
  request shape `FeedbackRequest{query_hash: str, vote: int  # +1 or -1}`
  (`query.py:113-115`) — **strictly binary**, no third option. Called from:
  - the chat UI's 👍/👎 buttons (`index.html:11762-11763`, handler `recordFeedback`
    `:11800-11807`);
  - the TUI (`resource_explorer/tui/app.py:335,338`);
  - a CLI prompt (`resource_explorer/observability/feedback_collector.py:9-19` — itself binary,
    `Prompt.ask(..., choices=["y","n",""])`).
- All three call the same `MetricsCollector.record_feedback(query_hash, feedback: int)`
  (`resource_explorer/observability/metrics_collector.py:163-198`), which does two things in one
  transaction:
  1. `UPDATE query_log SET feedback = ? WHERE query_hash = ? ORDER BY id DESC LIMIT 1`
     (`:169-174`) — attaches the vote to the most recent query with that hash. Same
     most-recent-row-by-correlation-key pattern as EA's `query_metrics` update (§1.2), and the
     same narrow race risk.
  2. For every chunk that was retrieved for that query (`chunk_refs`, looked up from the same
     `query_log` row, `:176-183`), upserts `chunk_feedback (chunk_ref, positive_count,
     total_count, last_updated)` (schema `:85-89`) — `positive_count += is_positive`,
     `total_count += 1`.
- **A real landmine for a third "neutral" option, found while tracing this**:
  `is_positive = 1 if feedback > 0 else 0` (`metrics_collector.py:189`). If a neutral vote were
  ever introduced as `feedback = 0` (mirroring EA's convention), this line would silently count
  it as **negative** for chunk scoring — `0 > 0` is `False`, same branch as `-1 > 0`. That's
  exactly the "field that doesn't apply becomes an empty-looking measured value" failure mode
  this codebase watches for, except inverted: here a *real* neutral measurement would collapse
  into a different real measurement (negative), not into absence. Any adoption of a neutral
  vote must not reuse EA's `0` convention on this exact column without fixing this line first.
- Read by: `resource_explorer/vector_store_pg.py:185` (`SELECT chunk_ref, positive_count,
  total_count FROM chunk_feedback`) — feeds retrieval ranking, not any admin view.
- **No admin surface reads `chunk_feedback` or `query_log.feedback` at all.** Confirmed by
  grep: no route other than `query.py`'s write endpoint references either table, and no panel
  in `index.html` fetches anything summarizing them. EA's exact counterpart signal (its 👍/😐/👎
  vote) has a dedicated admin card; RE's chat thumbs signal has none. This is the most concrete
  gap this survey found — see §6.
- Table name used by tests: `chunk_feedback` (`tests/test_metrics_collector_portability.py:61`).
- Rows: not verified here (out of scope — the task's table only asked about the other three;
  this one one was found in the course of tracing "the thumbs up/down signal in chat").

### 2.4 Storage location: already one physical Postgres

`feedback` (`config.py:293-302`), `resource_feedback`/registry (`config.py:262-271`), and
`chunk_feedback`/`query_log` (`config.py:145-151`) all default to the **same** Postgres
instance and database that EA uses — `postgresql://egeria_advisor:<PGVECTOR_PASSWORD>@localhost:5442/
egeria_advisor` (built from the PGVECTOR_* settings since TR-12; there is no built-in password), with RE's connections pinned to the `resource_explorer` schema via
`options=-csearch_path%3Dresource_explorer` in each URL. EA's own tables (`query_metrics`,
etc., `advisor/db_consolidated.py`) live in the same database's default schema. So "one
physical Postgres, two independently deployed services" — the framing this doc's precedent
document uses for the cost of a shared package (§4) — is not a hypothetical here. It is already
true today, just schema-separated rather than code-shared.

## 3. Is the third signal earned? Judgement call.

**Recommend: adopt a neutral bucket for RE's chat-quality signal (the `chunk_feedback` /
`query_log.feedback` domain only) — do not add it to `feedback` or `resource_feedback`.**

Why it's earned there: RE's chat thumbs is a forced binary choice on a single satisfaction
axis, same as EA's chat feedback was before its third button existed. EA's own admin panel
demonstrates the value: the satisfaction formula treats neutral as half-credit
(`admin.html:566`), and gap analysis uses satisfaction rate to flag underperforming
agents/perspectives/query-types (§1.3) — a "partially helpful" response pushed into either pole
by a forced binary would misclassify a `≥5`-sample bucket's satisfaction rate at the margin,
exactly where the threshold logic is most sensitive. The same argument applies more sharply to
`chunk_feedback`, which trains retrieval ranking directly: labeling a partially-relevant chunk
as fully positive or fully negative is a worse training signal than labeling it, correctly, as
neither.

Why it's *not* earned on the other two stores: `feedback` and `resource_feedback` already carry
a 1-5 star `rating` (both, `feedback_store.py:37`, `registry.py:1236`) plus free-text
`message` and a `category` enum. A forced-binary-with-neutral vote is a coarsening of
information those stores already have at finer grain — adding "😐" there would be adding a
worse instrument next to a better one already in place, not filling a gap.

Implementation note if this is picked up later (not scoped here beyond the landmine already
flagged in §2.3): don't reuse `0` as the neutral value on `query_log.feedback` /
`chunk_feedback.positive_count` without also fixing `metrics_collector.py:189`'s `> 0` check,
or route neutral through a separate counter (e.g. `chunk_feedback.neutral_count`) so `0`-as-
neutral and `0`-as-not-yet-scored never share a column.

## 4. Do the three (four) shapes reconcile? No — and they shouldn't be forced to.

Restating the four shapes now that `chunk_feedback` is in view:

| store | subject | fields with no analogue elsewhere |
|---|---|---|
| `feedback` | a page/element | `session_id, page, element_guid, wants_response, consent_to_contact, build_version, user_agent, viewport, locale` |
| `resource_feedback` | a resource | `entity_type, entity_slug` |
| `chunk_feedback`/`query_log.feedback` | a query → its retrieved chunks | `query_hash, chunk_refs[], intent, cache_hit, latency_ms` |
| EA `feedback_extended.jsonl` | a chat turn | `perspective, routing_agent, response_text, query_type` |

None of these subjects are the same thing wearing different clothes. A UI bug report about a
page is not commentary on a resource's trustworthiness is not a vote on whether a RAG chunk was
relevant is not a rating of a chat agent's routing decision. Flattening them into one row shape
would force every row to carry columns that don't apply to it — `element_guid` on a chat vote,
`chunk_refs` on a page bug report — and per this codebase's standing rule, **a column that
doesn't apply to a row must not silently read as `null`/`""`/`0`, because that renders
"doesn't apply" identical to "applies, and here is the measured value."** Concretely: `feedback.
category` is `""` both when nobody has categorized this UI report yet *and* structurally,
forever, for a merged-in chat vote that has no category concept at all — those need to stay
visibly different states, not the same empty string.

**Proposal: don't merge the tables. Merge the summary.** Keep all three (four) storage shapes
as they are — they're each already reasonably designed for what they capture — and build one
admin-facing **feedback envelope** view-model that every summary/triage screen reads through,
rather than one that reads three raw tables with three sets of column names. Shape:

```
{
  id: str,                        # store-native id (or `f"{store}:{query_hash}:{ts}"` for
                                   #   chunk_feedback, which has no per-vote row id today)
  origin: "page" | "resource" | "chat",   # which physical store this came from
  created_at: iso8601,
  subject: {                      # typed union, not flattened columns
      kind: "page", page, element_guid              |
      kind: "resource", entity_type, entity_slug     |
      kind: "chat", query_hash, chunk_refs, intent
  },
  signal: {                       # typed union — the reconciliation point
      kind: "star_1to5", value: int | None            |   # feedback, resource_feedback
      kind: "binary", value: -1 | 1                    |   # chat, today
      kind: "trinary", value: -1 | 0 | 1               # chat, if §3 is adopted
  },
  message: str | None,            # None, not "", when the store has no free-text concept
  triage_status: str | None,      # None, not "new", for origins that don't triage at all
                                   #   (chat votes today have no triage workflow — see §6)
}
```

The `message: str | None` / `triage_status: str | None` distinction is the direct application
of "absence must not look like a measured empty value": today `resource_feedback.category` is
`""` for "no category given" (a real, chosen absence, since the column always applies to that
store), whereas a chat vote folded into this envelope has *no category field to leave empty* —
that must render as `None`/omitted in the envelope, not as `""`, or a UI reader can't tell "this
store doesn't have categories" from "this row's category is blank."

This is the same design move EA's own admin panel is missing, incidentally: its Overall
Satisfaction breakdown (§1.3) already mixes vote-derived stats with star-rating-derived stats
(`avg_star_rating`, `feedback_collector.py:234-235`) in the same `stats` dict without a typed
union — `star_rating: None` for a pure-vote entry and `star_rating: None` for "nobody asked for
a star rating on this UI" are already indistinguishable there. Not this doc's problem to fix in
EA, but worth naming since the project owner asked about leveraging EA's model, not just its data.

## 5. What's shared vs. per-app

Following the precedent set by `docs/trellis-vectorstore-extraction.md` (a shared package is a
real, admitted cost — "a structural change touching the core data-access layer of two
independently-deployed, currently-live services" — and that doc stopped at a design, not code,
for exactly that reason):

**Share:**
- **The envelope shape** in §4, as a small, storage-agnostic dataclass/TypedDict — not a
  package with I/O, just a shared vocabulary both apps' admin code imports, the way the
  extraction doc's `schema`/`distance_metric` parameters let one class express two apps' real
  differences instead of hiding them. Low cost: it's a contract, not a service.
- **The triage vocabulary**, extended to EA's superset: `new, triaged, known_issue, actioned,
  deferred, not_an_issue`. RE's `triaged` and EA's `known_issue` may be the same status by
  different names, or may be a real difference (RE's `triaged` reads as "seen, not yet
  characterized"; EA's `known_issue` reads as "seen, characterized, not yet actioned") — that's
  a product decision for whoever owns both panels, not something to collapse here on inference.
  Either way, both apps' triage `<select>`s should draw from one shared enum, not two enums that
  drift.
- **Nothing storage-level.** Both apps already sit on the same physical Postgres (§2.4) — that
  argues *against* a shared connection/pooling package here (unlike the vectorstore case, RE's
  `ConnectionWrapper` is already proposed for EA to adopt directly per
  `docs/Backlog.md:2858-2867`, a separate, already-scoped item, not something this doc should
  re-propose) and *against* a shared table, because the four subjects in §4 are genuinely
  different things, not accidental duplication. `trellis-vectorstore`'s lesson applies in
  reverse here: that doc found real code duplication (near-identical `PgVectorStore` classes)
  worth abstracting; this survey found real *data-model* differences that a shared table would
  paper over. The two situations look similar ("two apps, same kind of thing") and aren't.

**Keep per-app:**
- All three physical write paths (`feedback_store.py`, `registry.py`'s `resource_feedback`,
  `metrics_collector.py`'s `chunk_feedback`) and EA's JSONL-plus-`query_metrics` pair stay
  exactly where they are. RE's per-store `_conn()`/`ConnectionWrapper` plumbing is already
  shared *within* RE (feedback_store.py:1-13's own docstring says as much); there's no
  cross-app duplication in this layer to extract, unlike vectorstore's near-identical classes.
- Each app's admin panel UI. EA's satisfaction-card/gap-analysis/triage-table layout
  (`admin.html`) and RE's per-store panels (`admin-feedback.html`, `loadAdminFeedbackPanel`)
  are genuinely different UIs serving genuinely different navigation structures (EA's top-level
  admin app vs. RE's Admin → Observe sub-nav) — sharing the *view-model* (§4) does not require
  sharing the *view*.

## 6. Where does the admin summary live?

Per-app, reading the shared envelope. Concretely for RE: `loadAdminFeedbackPanel` (once the
concurrent agent's badging work lands, §2.2) should read all applicable origins through the
envelope, and should be the place `chunk_feedback`/chat-vote stats finally get a card —
today they have none anywhere (§2.3), which is the most concrete gap this survey found. EA's
`admin.html` keeps its own layout, reading the same envelope shape but with its own three
JSONL/Postgres sources behind it. Neither panel becomes a shared component; only the shape of
data flowing into each panel becomes common — matching the "share the contract, not the
service" cost calculus in §5.

## 7. Migration: the 9 rows in `feedback`

No merge in the sense of "move rows between tables" is being proposed (§4) — `feedback` stays
`feedback`. What "migration" means here is narrower and lower-risk: making sure the envelope
view (§4/§6) can represent those 9 rows, including whatever triage status they're already at,
without altering the underlying table. Concretely:

1. **Before any code changes to the admin surfaces**, snapshot the current `feedback` table:
   `SELECT id, created_at, triage_status FROM feedback ORDER BY created_at` against the live
   Postgres (`FEEDBACK_DATABASE_URL`, §2.4) and save the 9 rows' ids + triage_status alongside
   this doc or in the PR that implements §4-§6. This is the "verified rather than asserted"
   step the task asked for — a literal before-snapshot, not a claim.
2. Implement the envelope reader (§4) as an additive view over `feedback` — a `SELECT`, no
   `ALTER`, no data movement. `feedback.id` (already a UUID string,
   `feedback_store.py:34`) becomes the envelope `id` unchanged, so the 9 rows keep their
   identity across the change.
3. **After** the envelope lands, re-run the same query against the *same* table and diff row-
   for-row against the step-1 snapshot: same 9 ids, same 9 `triage_status` values, same
   `created_at` ordering. Any admin UI built on the envelope must additionally be spot-checked
   by loading it and confirming exactly 9 rows appear with the same triage statuses the
   snapshot recorded — a UI that renders is not proof the underlying query is right (this
   doc's own house rule: a check that always shows something green isn't the same as a check
   that verifies the right thing).
4. Because nothing here touches `INSERT`/`UPDATE`/`DELETE` on `feedback`, there's no rollback
   scenario beyond reverting the reader code — the 9 rows are never at risk of being written to,
   only read differently.

This plan deliberately does not attempt a live verification pass as part of *this* task — per
the ground rules, this doc proposes; it does not touch the running database or other agents'
in-flight files.

## 8. Honest limits

- **`loadAdminFeedbackPanel`'s exact current behavior may have changed since this was read.**
  A separate agent is actively editing it. §2.2's description of what it queries is accurate as
  of the specific read reported there, not guaranteed current by the time this doc is acted on.
- **`chunk_feedback`'s row count** was not queried (out of the task's named scope of three
  tables; it surfaced while tracing the chat thumbs signal). Its emptiness or non-emptiness
  isn't established here.
- **The 9-row count and 2026-08-11 start date for `feedback`** are taken as given from the task
  brief, not independently re-verified against the live database in this pass — see §7 for how
  to verify before any change lands, not a re-assertion that they're currently true.
- **Whether RE's `triaged` and EA's `known_issue` denote the same triage state** is a judgement
  call for whoever owns both panels (§5) — this doc flags the ambiguity rather than resolving
  it, since resolving it requires product intent this survey can't infer from code alone.
- **EA's L2-vs-cosine-style "is this actually a deliberate difference" check** (the standard
  this doc's own precedent document applies) was done for the *subject* shapes in §4 by reading
  each store's schema and call sites, not by interviewing whoever designed `feedback_extended.
  jsonl`'s triage vocabulary — the "genuinely different intent, not accidental divergence" claim
  in §1.3 is inferred from the vocabulary's own words (`deferred` vs `not_an_issue` reads as a
  real distinction), not confirmed with EA's author.
- **No code in either app was changed, and no query was run against the shared Postgres
  instance**, per the ground rules for this task.

---

# Part II — Triage, and what curation still owes

*Merged 2026-09-02 from two notes.*

## 7. The triage gap

*(from `feedback-triage-from-workspaces.md`)*

Measured against what an Egeria Workspaces Portal administrator can actually do,
and the finding is a familiar shape:

> **RE has a triage-status `PATCH` endpoint, admin-gated and working. Nothing in
> the admin feedback page calls it.** A grep for `PATCH` in
> `admin-feedback.html` returns no matches.

So the capability is built and unreachable — the same class as the metric written
to a table no reader consults, and the reason this codebase's standing rule is to
open the surface a person opens rather than verifying next to the code.

Three status vocabularies exist across Workspaces, Egeria Advisor and RE. They
were read **out of the database rather than off the documentation**, which matters
because two of the three had drifted from their own docs.

**Editing safety** is the constraint that shapes any fix: feedback is a record of
what somebody said, so a triage workflow may change *status* and *assignment* but
must not edit the submitted text. An admin surface that can silently rewrite a
report is worse than one that cannot triage at all.

## 8. What curation still owes

*(from `curate-followups.md`)*

Three capabilities were named as belonging to Curate and are **not built**. Each
needs its own design pass rather than being folded into an existing surface:

- **Digital-product evaluation** — judging a resource as something offered to
  others, not merely catalogued.
- **Sample-dataset creation** — producing a representative extract a consumer can
  try without taking the whole thing.
- **Quality-issue remediation** — a workflow, distinct from recording that a
  quality problem exists.

They are recorded here so that "Curate is done" is never read off the presence of
tags, feedback and curator notes, which are the three pieces that *are* built.

---

# Part III — Settled — do not reopen without re-measuring

| Question | Settled | On what basis |
|---|---|---|
| Should the feedback stores be unified into one shape? | **No** | Four different subjects — a page, a resource, a query's retrieved chunks, a chat turn. Flattening forces every row to carry columns that do not apply to it |
| Is a UI bug report the same signal as resource commentary? | **No** | Different subject, different audience, different lifecycle |
| Is RE's admin feedback pane read-only by design? | **No** — unwired | The `PATCH` endpoint exists and is admin-gated; the page simply never calls it |
| Were the status vocabularies taken from the docs? | **No** — from the database | Two of the three had drifted from their own documentation |
| May a triage workflow edit submitted feedback text? | **No** | Feedback is a record of what somebody said; status and assignment are editable, the text is not |
| Is Curate complete once tags, feedback and notes exist? | **No** | Three named capabilities remain unbuilt, each needing its own design pass |
