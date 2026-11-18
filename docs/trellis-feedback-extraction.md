# Trellis-level feedback — one store and one triage workflow for both apps

**Status: proposal, 2026-11-09. Not built. All seven open questions in §7 were decided by the project owner on 2026-11-09.** It was written because the project owner asked
for egeria-advisor-style feedback status in Resource Explorer's Admin → Feedback, and then
asked that it be done at the Trellis level, since Egeria Advisor has the same needs. Each
decision is recorded under its question in §7. Read it alongside
`re-ea-consolidation-audit.md` §6 and §8 ("distinct, not duplicates") and the two earlier
extractions it follows, `trellis-auth-extraction.md` and `trellis-vectorstore-extraction.md`.

**Revised 2026-11-09** after the architect's read-back
(`~/.re-gate/READBACK-trellis-feedback-extraction-proposal-2026-11-09.md`, on the
coordinator's machine). The read-back corrected the EA race (§1), added requirements to the
triage design (§4), and added two open questions (§7 Q5, Q6). Each of its claims about EA was
re-checked against the code before going in here. One claim did not hold and is not taken:
that RE's `feedback` store defaults to SQLite. Its configured default is the shared Postgres,
at `bc5322a0` and at `5e610345` alike (`config.py`, `FeedbackConfig.database_url`). Only
`FeedbackStore`'s fallback, used when config cannot be read, is SQLite. The architect has
withdrawn that point and recorded the correction in the read-back file.

## 1. Where the two apps actually stand

Read from the code on 2026-11-09, `main` at `5e610345`.

### Egeria Advisor (EA)

- **Answer feedback (👍 / 😐 / 👎) is mature.** `advisor/feedback_collector.py` (654 lines) is
  a `FeedbackEntry` holding the query, query type, collections searched, rating, star rating,
  category, perspective and routing agent. It feeds `get_feedback_stats`, `get_gap_analysis`
  and `get_routing_improvements` (served at `GET /api/feedback/analysis`), plus MLflow logging.
  It is stored in `<ADVISOR_DATA_PATH>/feedback/user_feedback.jsonl`.
- **The text being reviewed lives in a second file.** `POST /api/feedback`
  (`advisor/web/feedback.py`) also appends to `feedback_extended.jsonl`, which holds the full
  `response_text` plus `triage_status` (default `"new"`) and `analysis_comments`.
  `feedback_text` and `user_comment` are temporarily used to carry the intent override; the
  code says so ("repurpose for intent label until schema expanded").
- **Triage vocabulary:** `new`, `known_issue`, `actioned`, `deferred` and `not_an_issue`
  (`advisor/web/static/admin.html`), plus free-text analysis notes. The admin view filters by
  rating and by status.
- **Triage is keyed by position, and the positions disagree (a live bug).**
  `GET /api/feedback/extended` skips lines it cannot parse. `admin.html` (l.736) numbers each
  record by its position in that filtered list. `PATCH /api/feedback/extended/{idx}` treats the
  number as a *raw* line number. So one blank or corrupt line in the file makes every later
  triage edit change the wrong record. This is the strongest argument here for stable ids.
- **Triage rewrites the whole file in place.** The PATCH reads every line, changes one, and
  calls `write_text`, which truncates first, so a crash part-way through loses the file. A
  submission being lost *between* the read and the write is not possible today, contrary to
  this doc's first version. EA runs one uvicorn process (`cli/main.py`, no workers), and both
  handlers are `async def` with no `await` between the read and the write. That changes as soon
  as a second process or worker writes the same file.
- **A failed submission still reports success.** `POST /api/feedback` catches the failure of
  either write, logs a warning, and returns `{"status": "ok"}` anyway (`web/feedback.py`). It is
  the same "said recorded, wasn't" shape as RE's chat votes below.
- **Feedback admin needs login, not admin, and login itself is a policy switch.** `/admin` and
  its `/api/feedback/*` calls sit behind `trellis_auth.LoginRequiredMiddleware`, and EA has no
  admin role (`advisor/auth.py`, `_EA_PUBLIC_PATHS` and the comment above it). So any signed-in
  user can read every user's queries and responses (`GET /api/feedback/extended`) and change
  any triage status. Login is required by default but can be turned off
  (`policy.require_login`), and with it off, all of that is open to anyone.
- **No page-level product feedback.** EA has no bug / confusing / suggestion / praise widget.

### Resource Explorer (RE)

RE has three stores that look like feedback. They are separate stores, not one.

- **Page-level product feedback:** `resource_explorer/feedback_store.py`, the `feedback` table.
  It is in the shared Postgres (`resource_explorer` schema) by default, or SQLite via
  `FEEDBACK_DATABASE_URL`. It was ported from the Egeria Workspaces Portal
  (`egeria-workspaces/compose-configs/*/PyegeriaWebHandler/demo_feedback_handler.py`). It
  holds category (`bug`, `confusing`, `suggestion`, `praise`), page, message, rating, contact
  consent and build/client details. It **already has** a `triage_status`
  (`new` / `triaged` / `actioned`) and an admin-gated `PATCH /api/feedback/{id}`. Admin →
  Feedback (`web/static/next/admin/feedback.js:163`) displays the status, but there is no
  control to change it and no notes.
- **Per-resource feedback from the Curate tab:** `resource_feedback` in the registry. It is
  about a *resource* (curation input), not about the app, and it has no triage.
- **Chat answer votes: effectively unrecorded.** `POST /api/query/feedback` calls
  `MetricsCollector.record_feedback`. That updates the newest `query_log` row carrying the
  same question hash; `query_log` stores the hash, intent, slug and latency, but no question
  text, answer or user. Only the `rag_system` path writes `query_log` rows, so an update that
  matches no row goes undetected, and the route returns `{"recorded": true}` either way. The
  chat then shows "Marked helpful." When a resource is in scope, `chat.js` *also* posts to
  `/api/feedback/answer`. That writes a `feedback` row and, on "disagree", a gap
  (`gaps.record_disagreement`), so only those votes reach Admin → Feedback.

`GET /api/curate/feedback` combines the page and resource stores for Admin, with a `source`
badge on each row. Merging them was left to "a separate, deliberate task"
(`web/routes/curate.py`, `list_all_feedback`). This proposal is a candidate for that task.

## 2. Why Trellis level rather than one each

The same reasoning as the earlier extractions:

- **Both apps need the same three things:** record a rating of an answer with enough context
  to review it, triage it, and do so with an id that cannot point at the wrong record. EA has
  the first two, and triage that can change the wrong record (§1). RE has the triage column
  without the UI,
  and its chat-vote recording is broken.
- **Building it twice gives two vocabularies and two shapes.** That has already happened:
  EA's five statuses against RE's three. Each later copy would add one more.
- **`trellis-querycache` is the precedent:** two independently grown copies with the same
  purpose and different behaviour, one of which did not do what its name said. Extracting
  before a second divergence is cheaper than after (`trellis-microflow` was extracted "from
  the start" for that reason).

## 3. The argument against, stated fairly

- **EA's answer-feedback analytics are EA-specific** (collections, routing agents, routing
  improvements). Moving them would drag EA concepts into a shared library. This proposal
  moves only the store and triage. The analytics stay in EA and read through the library.
- **Product feedback and answer feedback are different concepts** (audit §8). They share a
  triage need and an admin view, not a meaning. §4 keeps them as separate kinds.
- **One more shared library to version and release.** That is real. It is smaller than
  `trellis-auth` and has no Egeria dependency.

## 4. What is shared and what is not

### Shared, in `packages/trellis-feedback/`

1. **One feedback item with a `kind`.**
   - `answer`: a rating of a chat/RAG answer. Holds the vote (+1 / 0 / −1, the convention
     both apps already use), query text, the full response text (§7 Q4), and optionally the perspective, routing agent, compile id, resource scope
     (entity type + slug) and session.
   - `product`: a page-widget report. Holds category, page, element, message, rating,
     contact email and consent, and build/client details. These are RE's current columns.

   An `answer` item also has a real `intent_label` field, so EA's practice of carrying the
   intent override in `feedback_text` / `user_comment` is not copied into the new model.

   Every item has a stable id assigned by the library, which is never a position in a list.
   Every item records `app` (`resource-explorer`, `egeria-advisor`), `user` (the signed-in
   `sub`, or empty) and `created_at`. Submit takes an **idempotency key** from the client, so
   a retried submission does not create a duplicate.
2. **Triage as an append-only event log.** Each status change records the item id, status,
   note, who made it and when. Events are ordered by a **monotonic sequence id**, not by
   timestamp, since two admins can act in the same second and clocks can disagree. The current
   status is the latest event; an item with no events is `new`. **Notes exist only on events**,
   so there is no edit path that could overwrite one. This removes EA's whole-file rewrite and
   its position mismatch by construction. It also answers "who marked this, and when", which
   neither app can answer today.
3. **Triage of items stored elsewhere.** A triage event can name an external
   `(app, source, id)` instead of a library item. `source` comes from a **closed list each
   app declares** (RE: `resource_feedback`), not a free string. That covers RE's
   `resource_feedback`, which stays where it is (§7 Q2). If the referenced row is later
   deleted, the admin view shows "item gone (`source`, `id`)" and keeps the item's events,
   rather than dropping them or failing the listing.
4. **Storage.** Postgres in the shared instance, one schema per app, with SQLite for tests.
   This is the `trellis-vectorstore` arrangement: the library owns the DDL, and each app
   passes its own URL and schema.
5. **A FastAPI router factory:** submit, list (filter by kind, status, category, app, date),
   get, triage, and delete (§7 Q6). The app passes in `is_admin(request) -> bool`. The library does not
   decide who is an admin. Submit stays open to any signed-in user; list, get, triage
   and delete require admin.
6. **A JSON contract** that both apps' admin UIs call. The UIs stay in each app: RE's
   `static/next` modules and EA's `admin.html` are different frontends, and sharing markup
   is not worth coupling them.

### Not shared

- EA's analytics (stats, gap analysis, routing improvements, sentiment, MLflow) stay in EA.
- RE's gaps loop (`gaps.record_disagreement` on "disagree") stays in RE, and keeps being
  called alongside the library's submit.
- Who is an admin: RE's `admin_auth` (`FEEDBACK_ADMIN_USERS` / `FEEDBACK_ADMIN_TOKEN`), and
  whatever EA adopts (§7 Q3).
- The Portal keeps its own handler. It is in another repository. It is a later candidate for
  the same contract, not part of this change.

## 5. What it does not cost, and what it does

**It does not cost:**
- A frontend rewrite. Each admin view gains a status control, a notes box and a filter.
- Egeria writes. Nothing here touches Egeria.
- Losing EA's history. `feedback_extended.jsonl` imports once, and each record's
  `triage_status` / `analysis_comments` becomes one triage event if it is not `new`. Raw line
  order gives a stable import id. **Only that file is imported.** It holds everything an
  `answer` item needs. `user_feedback.jsonl` shares no id with it, so joining the two would be
  a guess, and it stays where it is as the input to EA's analytics.

**It does cost:**
- **A migration per app**, each with its own count-before / count-after check:
  - RE moves `feedback` rows into the library, and turns its `triage_status` values into
    events.
  - EA imports `feedback_extended.jsonl`.
- **A change to RE's chat vote path.** Every vote is submitted as an `answer` item, whether
  or not a resource is in scope. The chat only says "Marked …" after the library confirms the
  write. `MetricsCollector.record_feedback` stays for chunk scoring, but no longer stands in
  for the record.
- **EA's `POST /api/feedback`** writes through the library rather than to
  `feedback_extended.jsonl`, and it **keeps calling `FeedbackCollector.record_feedback` as
  well**. Otherwise EA's stats, gap analysis and routing improvements stop receiving data.
  `user_feedback.jsonl` keeps feeding them until they are moved to read from the library, in a
  later step of their own. The route also stops returning `ok` when a write failed.

## 6. Phasing

Each step is its own PR, with tests.

1. **This document**, read back by the architect and decided on by the project owner (§7).
2. **`trellis-feedback` 0.1:** the model, storage and router factory, tested on SQLite and on
   Postgres. Tests include:
   - two triage writes and a submit happening concurrently;
   - a retried submit with the same idempotency key;
   - an external reference whose row is gone;
   - deleting an item removes its triage events with it;
   - a guard that **a listed item's triage target can never diverge from the item shown**,
     including when the store holds a corrupt or skipped row (EA's live bug, §1).

   Q5 and Q6 (§7), which fix the schema, are decided: store the question, answer and user,
   keep items until deleted, and delete an item together with its triage history.
3. **RE adoption:**

   Progress, in four PRs:
   - **A (#1013, merged and deployed 2026-11-09):** page feedback is stored in the library, and the
     legacy `feedback` table is copied in once (trellis-feedback 0.2 `import_item`; the old table is
     kept; 27 rows on the production database). Admin's PATCH takes all six statuses and a note, and
     records who made the change.
   - **B (#1014):** chat votes and Questions-checklist verdicts are `answer` items with the question,
     answer and user. Admin lists them.
   - **C:** Admin → Feedback has a status control with a note on each item, a status filter and each
     item's history.
   - **D:** Curate's `resource_feedback` rows are triaged by reference (source `resource_feedback`).
     A triaged row that is deleted is listed as "item gone" with its history until an admin clears it
     (Q7).
   - migrate `feedback`;
   - record chat votes as `answer` items;
   - add a status control, notes and a filter to Admin → Feedback;
   - make Curate's `resource_feedback` triageable through external `(source, id)` events.
4. **EA adoption:**
   - import the JSONL files;
   - write through the library;
   - point the admin view at the library's API;
   - put feedback admin behind EA's admin check (§7 Q3).
5. **Later, separately:** EA's analytics read from the library, and the Portal can adopt the
   same contract.

## 7. Open questions for the project owner — decided 2026-11-09

Each question keeps the recommendation it was asked with, followed by the decision.

1. **The status vocabulary.** Recommendation: EA's five (`new`, `known_issue`, `actioned`,
   `deferred`, `not_an_issue`), the larger and already-used set. The first version of this doc
   mapped RE's `triaged` to `known_issue`. That was wrong: `triaged` means "seen, no decision
   yet", not "a known issue". Either add `triaged` as a sixth status, or map it to `deferred`.
   Count RE's `triaged` rows before choosing. On this development machine (2026-11-09), RE's
   store has one row, `new`, and EA has no `feedback_extended.jsonl`. So nothing here forces the
   choice, but production stores may differ and should be counted at migration.

   **Decision (project owner, 2026-11-09):** EA's five plus `triaged` — six statuses: `new`,
   `triaged`, `known_issue`, `actioned`, `deferred`, `not_an_issue`. RE's `triaged` rows migrate
   unchanged.
2. **Is RE's Curate `resource_feedback` triageable?** Recommendation: yes, through external
   `(source, id)` triage events, without moving the table. It is curation input about a
   resource, so it keeps its own home and meaning.

   **Decision (project owner, 2026-11-09):** yes, by reference. Curate feedback is triageable in
   Admin, and `resource_feedback` stays where it is.
3. **Who may read other users' questions and answers?** Today in EA it is any signed-in user,
   and anyone at all when `policy.require_login` is off. Recommendation: only an admin,
   whatever the login policy is. That needs an EA admin concept, perhaps shared through
   `trellis-auth`. Whether one admin check should serve both apps is a related question; this
   proposal does not depend on it.

   **Decision (project owner, 2026-11-09):** admins only, in both apps, whatever the login
   policy. Each app keeps its own admin check (RE: `FEEDBACK_ADMIN_USERS` /
   `FEEDBACK_ADMIN_TOKEN`; EA: to be added). Submitting stays open to any signed-in user.
4. **How much of an answer to keep.** EA keeps the full response today. Recommendation: the
   full text for 👎 and 😐, which are the ones someone will investigate. For 👍, a capped
   excerpt (for example 4,000 characters). Keep the compile id wherever one exists.

   **Decision (project owner, 2026-11-09):** always store the full answer text, whatever the
   vote, as EA does today. Keep the compile id wherever one exists.
5. **Should RE start storing question text with chat votes?** RE's `query_log` deliberately
   keeps only a hash of the question. Recording every chat vote with its question, answer and
   user is new data collection, not a bug fix. It is the project owner's decision, and the RE
   chat-vote change in §6 step 3 depends on it.

   **Decision (project owner, 2026-11-09):** yes. Every RE chat vote is stored with its question
   text, answer and the signed-in user, readable only by admins (Q3). `query_log` keeps its
   hash for chunk scoring.
6. **Retention and deletion.** Items carry a user together with their question and answer.
   How long are they kept? Can a user's items be deleted on request, and what happens to the
   triage events of a deleted item? Settle this before 0.1 fixes the schema.

   **Decision (project owner, 2026-11-09):** keep items until they are deleted; no automatic
   expiry for now, to be revisited before any public deployment. Deleting an item removes the
   item, its text and all of its triage events. 0.1 provides an admin-only delete.
7. **A referenced row deleted outside the library.** Q6 makes deleting a library item remove its
   triage events. But RE can delete a `resource_feedback` row itself, without going through the
   library, and the library would still hold that row's events. Raised by the architect after
   the six decisions.

   **Decision (project owner, 2026-11-09):** keep them, shown as "item gone (`source`, `id`)",
   until an admin deletes them through the library's delete route. RE's own delete does not
   call the library. 0.1 tests both paths: deleting a library item, and deleting the external
   row.
