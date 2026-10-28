# Questions tab cold-load measurement — 2026-09-29

**Status:** Measurement only. No code changes — this branch carries only this doc.

**Question asked by design:** before deciding whether to dispatch a "Questions-envelope"
latency slice, what does the Questions tab's cold-load path (`getQuestions()`, calling
`build_question_checklist` server-side) actually cost NOW, against current merged `main`
(which already carries three rounds of latency fixes: schema-verification caching,
connection-pool sizing, event-loop-block fixes, `list_projects`/`list_databases` N+1 fixes,
the `board_summary` persisted-read mechanism, the `survey_data` jsonb fix, and
`db_classification`'s N+1 fix — see `PER-REQUEST-SERVER-LATENCY-IMPLEMENTED.md`,
`-ROUND-2-IMPLEMENTED.md`, `-ROUND-3-IMPLEMENTED.md`)? Before this round of work,
`getQuestions()` on `laz_local_adventureworks` measured **48-52 seconds**, under load. Nothing
in rounds 1-3 directly targeted this path.

## Setup

- Fresh worktree off `origin/main` (HEAD `8c6f1845`, "Merge pull request #356 from
  dwolfson/re/glyph-state-tone-color-fix"), at
  `/Users/dwolfson/localGit/egeria-v6/trellis-re-questions-timing`.
- `uv sync --all-packages --extra dev` in `packages/resource-explorer`.
- Scratch `resource-explorer web` process on **port 8815** (never 8810/8813 — those are the
  owner's dev server and PR/CI's gate server respectively), with `TRELLIS_ANONYMOUS_READ=true`
  set for this process only, so the endpoint could be measured via a real unauthenticated
  `GET` through `curl` rather than a direct in-process function call. This is a documented
  dev-box-only override (`packages/trellis-auth/README.md`) that passes reads and still
  blocks writes; it was set only on this throwaway process, not on any shared server, and this
  measurement performed no writes.

## Coordinated quiet window

Confirmed by the PR/CI coordinator: PR/CI running nothing against the shared Postgres registry
(localhost:5442) at the time, owner not actively using port 8813. Both 8813 and 8810 carry
light 2s-poll embedded workers, the same acceptable baseline named in prior rounds — not
stopped for this measurement.

- **Window:** 2026-09-29T19:09:56Z (setup/pre-check) – 2026-09-29T19:13:04Z (post-check),
  ~3 minutes of active measurement inside that span.
- **`pg_stat_activity` before (19:09:56Z):** 1 active, 33 idle, 37 with no state
  (background/idle connections in the shared pool).
- **`pg_stat_activity` after (19:13:04Z):** 1 active, 1 idle in transaction, 49 idle, 40 with
  no state.
- **CPU (`uptime`):** load averages 18.37 / 22.27 / 23.05 at window start, 14.99 / 19.47 /
  21.73 at window end. Consistent with prior rounds' finding that this box is never fully
  idle (other long-running processes, including the owner's own 8810 dev server, contribute
  background load) — noted, not treated as a confound serious enough to block measurement,
  same posture as round 2.

## Measurement

Endpoint measured: `GET /api/databases/laz_local_adventureworks/questions?phase=scouting`
— the exact route the Questions tab's `getQuestions()` calls (`re-api.js`'s
`_questionsPath('database', slug)` → `databases.py`'s `GET /{slug}/questions`, which calls
`build_question_checklist(registry, "database", slug, phase, ...)` in
`workflows/scouting.py`), confirmed by reading the actual cold-load call site in
`web/static/next/app.js` (the Questions tab's per-resource screen, ~line 6629) — same call,
same default `phase='scouting'` (`state.stage`'s initial value).

"Cold" here means: freshly-started server process, first request of any kind to this route
handled by that process — no query cache, no connection-pool warmup, no registry cache from a
prior call. Two independent cold measurements were taken, each against its own freshly
restarted scratch server process (not just a fresh client request against an already-warm
server), so neither run could benefit from the other's warmup:

| Run | Condition | Result |
|---|---|---|
| 1 | Fresh server start, first request | `time_total=1.264s`, HTTP 200, 9 questions returned (scouting phase) |
| 2 | Server restarted again, first request | `time_total=0.777s`, HTTP 200, 9 questions returned (scouting phase) |

Both responses inspected and confirmed to be real checklist payloads (not empty/error bodies
masquerading as fast 200s): `{"phase": "scouting", "perspectives": [], "purposes": [],
"questions": [...]}`, 9 question entries each with `has_data` populated via
`question_has_data()`.

For context only (not part of the cold-load gate, since it reuses the already-warm process
from run 1 rather than a fresh restart): a same-process repeat call on `scouting` measured
0.409s, and a same-process first call on `phase=assessment` (a different, larger question set)
measured 4.086s — still nowhere near the pre-round 48-52s baseline, and a useful data point
that even the coldest same-process phase transition stays well under the old number, though it
is not a controlled "fresh restart" cold measurement the way runs 1 and 2 are.

## Verdict

**Now under 2s.** Both independently-measured cold-restart runs (1.264s, 0.777s) are well
under the 2-second bar, a roughly **25-65x improvement** over the pre-round 48-52s baseline —
even though no round 1-3 fix directly targeted `getQuestions()`/`build_question_checklist`.
The likely explanation, not independently re-verified here since it is out of this
measurement's scope: `question_has_data()` is called once per catalogued question and reads
through the same registry/analysis-result paths that `list_databases`'s N+1 fix, the
`board_summary` persisted-read mechanism, and the connection-pool sizing fix all touch, so this
path appears to have been an incidental beneficiary of those three rounds' work rather than
needing a dedicated fix. **Recommendation to design: a dedicated "Questions-envelope" latency
slice does not look warranted on the strength of this number** — the path that was 48-52s is
now consistently sub-2s on the same database.

## Teardown

Scratch server process on port 8815 killed at end of measurement; no residual process
confirmed via `ps aux | grep 8815`. No writes were made to the shared registry beyond the
server's own idempotent schema-init check (`registry_init ... ran_schema_init=True`, same as
every `resource-explorer web` startup) and this measurement's read-only `GET` requests.
