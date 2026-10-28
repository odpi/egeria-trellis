# Slice 17c: generalize the render-bound gate to every level — implemented

**Coordinator brief:** Phase 1b, slice 17 follow-up (`re/coordinator-brief-phase-1b`).
**Replying to:** the same live signed-in gate that reviewed PR #294 (slice 17b),
one screen further on `coco_pharma`.
**PR:** #TBD (`re/slice17c-renderable-answer-gate`).

## What the gate found

Slice 17b bound the sub-resource-level checkmark to whether a known fact
produced a `headline` — the one rung that can carry member-naming prose
past the frontend's `scalarMeasures()` fallback, which skips list/object
fields by design. That fixed "Which schemas carry the data" (a `container`
-level question).

The same live gate, one screen further: **"Is this database a primary or a
replica, and is it replicating to anything?"** — a plain `resource`-level
question, entirely exempt from slice 17b's check — rendered a ✓ with **no
answer text at all**, only the provenance line "db_resilience · run 20h
ago" underneath.

**Diagnosis:** `db_resilience` has no `headline_reader`
(`DATABASE_ANALYSIS_HEADLINE_MAP`), and its value
(`_survey_operations`'s `resilience` key) is four nested dicts —
`replication`, `wal_archiving`, `backup_tool_signals`, `clustering` — with
**no top-level scalar field at all**. `scalarMeasures()` (`app.js`)
explicitly skips every list/object field. So unlike `schema_inventory`
(which at least has scalar fields like `table_count` alongside its list,
and so rendered a thin-but-nonempty rollup), `db_resilience` was
structurally guaranteed to render nothing, on every run, not only the one
the gate happened to catch live.

## The ruling

The design session's call (2026-09-26): generalize rather than patch
`db_resilience` alone — target_shape/headline was never the right axis;
"does the fact render ANY text, at any level" is. A known fact with no
renderable text is not an answer; the checkmark it sits under must be
withheld regardless of whether the question happens to carry a
sub-resource `levels` entry.

## The fix

1. **`FactLayer._renders_text(fact)`** (`facts.py`) — a small, explicit
   mirror of `app.js`'s `readEnvelope` rungs 1–3 (headline, `value.detail`/
   `summary`/`description` prose, or the `scalarMeasures()` fallback: any
   non-null, non-object, ≤60-char field other than `verdict`). Answers one
   narrow question — would rung 3 find anything to say — not a general
   renderer; the two must be kept in step by hand, called out explicitly
   in the docstring since there is no shared source between a Python
   backend and a browser-side formatter.
2. **`FactLayer._check_level`** now runs an unconditional first check —
   does *any* known fact `_renders_text`? — before consulting `levels` at
   all. This fires for `resource`-level questions too, which the old
   version exempted outright. Only if that passes does the existing
   sub-resource / `headline`-specific check from slice 17b run on top,
   preserving its distinct wording (`target_shape`-based: "nothing to
   show" vs. "rows exist, no reader shows them yet").
3. **Four new headline readers** (`survey_definition_adapter.py`,
   registered in `DATABASE_ANALYSIS_HEADLINE_MAP`), mirroring
   `row_count_snapshot`'s pattern — because the honesty floor
   (`_renders_text`) is not the ceiling; "primary; no replicas; WAL
   archiving off; no backup tool detected" is the answer a Data Owner
   actually came for:
   - `_db_resilience_headline` — replication role + replica count, WAL
     archiving mode (+ failure count when archiving), backup-tool
     detection, Citus clustering when present.
   - `_db_activity_signals_headline` — total writes/reads across all
     tables since the last stats reset. Written alongside the other two
     even though this analysis was never silently empty (it does have two
     real scalar fields, `stats_reset`/`table_count`) — "stats reset
     2026-... · table count 56" answers a different question from "is
     anything reading or writing this database."
   - `_db_external_dependencies_headline` — named counts of extensions,
     foreign servers/tables, publications/subscriptions (every one of its
     fields is a list, the identical structural gap `db_resilience` has).
   - `_db_privilege_audit_headline` — role count and superuser count, how
     many tables PUBLIC has `SELECT` on, how many are PUBLIC-writable
     (`INSERT`/`UPDATE`/`DELETE`). Added on review (design session,
     2026-09-26): `privilege_audit` has the identical all-list shape as
     `db_external_dependencies`, but it directly answers a
     Security-perspective question ("who can read and write what") — a
     known answer with no sentence is a visible gap a Security reviewer
     will see first, not an acceptable floor the way it is for an analysis
     nobody is specifically asking after yet.

## Live gate follow-ups (owner's session on 8811, 2026-09-26)

Two rounds of feedback against the real headlines, both addressed in
follow-up commits on this branch:

1. **`db_activity_signals`'s headline needed WHEN, not just how much.**
   The counter's start time changes what the counts mean — a large write
   count reads differently right after a reset than a year in. Rewritten
   to "N writes and M reads since statistics were reset on `<timestamp>`",
   or "...since statistics collection began (never reset)" when
   `pg_stat_database.stats_reset` is `NULL` — a real, distinct case from
   "we don't know", not glossed over with wording that implies a reset
   happened. **Correction (owner, 2026-09-26):** the first cut said "since
   the server started" — wrong, since `pg_stat_database`'s cumulative
   counters survive a server restart; `NULL` means never reset since
   collection began, not since the server last came up. Also distinct from
   the ANALYZE-driven estimate-freshness stamp (design §5.1a,
   `STATE_CATALOG_ESTIMATE`) — two different clocks on two different kinds
   of number. Also dropped the per-table count from the sentence per the
   review's headline-detail rule (a headline answers what was asked, in
   the fewest words; supporting detail belongs in evidence) — "across N
   tables" answers a different question than "is anything reading or
   writing this database." The other three headlines were reviewed against
   the same rule and needed no change: `db_external_dependencies` and
   `privilege_audit` are already bare counts with no names; `db_resilience`
   was confirmed fine as written (backup-tool names and WAL failure counts
   judged to be the answer itself, not decoration, since "which tool" and
   "is archiving actually succeeding" are exactly what was asked).

2. **`privilege_audit`'s "0 role(s); 0 superuser(s)" was a collection
   failure rendered as a real answer — a second instance of the exact bug
   this slice exists to close, now inside a headline reader instead of the
   gate.** Traced to a genuine, previously-undetected bug in
   `get_privilege_audit()`'s roles query (`connection.py`): its `LIKE
   'pg\_%'` pattern has a literal `%`, but `execute_query` always calls
   `cursor.execute(query, params)` with a params tuple — even the default
   empty `()` — which still switches psycopg2 into printf-style query
   substitution. An unescaped `%` there reads as a malformed format
   placeholder and raises (`IndexError: tuple index out of range`),
   silently caught by the query's own `try/except` and turned into `roles
   = []`. Confirmed live against `coco_pharma`
   (`localhost_docker_coco_pharma`): the stored survey's `table_grants`
   had 8 real rows (a different query, unaffected), but `roles` was empty
   despite `pg_roles` genuinely holding 13 rows there, and
   `has_table_privilege(current_user, 'pg_roles', 'SELECT')` returning
   true — nothing was actually restricting the read. This is a
   universal bug, not specific to this database: every `privilege_audit`
   run since this query was written would have hit the identical
   exception on every engine. Fixed by escaping the literal `%` as `%%`
   (`'pg\_%%'`) — re-verified live: the same connection now returns all 13
   real, non-system roles. Whether the same class of bug hollows out any
   *other* operations sections was checked directly: `grep`-ing
   `connection.py` for every other `LIKE '...'` pattern found none —
   `privilege_audit`'s roles query is the only raw-SQL call in the
   database surveyor with a literal, unescaped `%`.

   The reader-level fix stands independently of the SQL fix, because
   already-stored survey rows (like `coco_pharma`'s from 2026-09-25, before
   the fix) will keep reporting empty roles until re-surveyed:
   `_db_privilege_audit_headline` now treats an empty `roles` list as
   `None` (not measured) rather than a sentence of zeros — the same
   "a count that cannot be zero in a live database is a collection failure
   when it is zero" rule the design session named. `table_grants` gets no
   such floor: a database where every table carries only its owner's
   default privileges, with no explicit `GRANT` rows at all, produces a
   genuinely empty `table_grants` (the query's own `c.relacl IS NOT NULL`
   filter), which is a real zero worth stating, not a failure to mask.

### Why this bug was invisible for weeks, and where else it can hide

The design session's own framing, worth stating explicitly: the bug wasn't
a rare edge case, it was invisible *by construction*. `get_privilege_audit`'s
own `try/except Exception: roles = []` turned "the query is broken" into
"there are no roles" at the collection layer itself — a genuine exception,
not a real empty result, silently became indistinguishable from one. The
headline-level floor added above catches this ONE field (`roles`) because
someone could reason "this can never legitimately be zero." It does not
catch the next collector that swallows an exception into an empty list for
a field where zero *can* be legitimate — there the floor has nothing to
grab onto, and the same failure mode reproduces invisibly again.

**Ruling (design session, 2026-09-26):** a collector that catches an
exception should record it on its section (an `error`/`failed_at` field),
not just return the empty default; a reader should render a recorded error
as "collection failed: `<reason>` — re-run", never silently as an empty
list or a zero. This is a real fix to `connection.py`'s collector
methods themselves, not another reader-side patch — the reader can only
ever be as honest as what the collector handed it.

**Scope, not yet converted here** (this is `connection.py`'s own
`try/except Exception: <field> = <empty>` pattern, grepped exhaustively —
26 occurrences, one per guarded read):

| Method | Falls back to |
|---|---|
| `_get_schema_descriptions` | `{}` |
| `_catalog_only_fallback` | (silently returns, populates nothing) |
| `_catalog_columns_for_table` | `[]` |
| `get_column_stats` | `[]` |
| `get_table_activity` | `[]` |
| `get_stats_reset` | `""` |
| `get_index_stats` | `[]` |
| `get_privilege_audit` (×3: roles, table_grants, default_acl) | `[]` each |
| `get_credential_capability` (×4: connected_as, schemas, tables, stats_role) | `""`/`[]`/`[]`/`False` |
| `get_replication_status` (×2: is_in_recovery, replicas) | `None`/`[]` |
| `get_wal_archiving_status` (×2) | `""`/`(None, None)` |
| `get_backup_tool_signals` | `[]` |
| `get_clustering_info` | `(False, None)` |
| `get_external_dependencies` (×5: extensions, foreign_servers, foreign_tables, publications, subscriptions) | `[]` each |
| `_get_table_row_stats` | `[]` |

Not every one of these is equally dangerous — `get_credential_capability`'s
own fields are largely upstream inputs to *other* honesty checks
(`_credential_scope_status`) that already treat a zero specially, and
several (`_catalog_only_fallback`, `_get_schema_descriptions`) are
best-effort enrichments where an empty result was always a legitimate
degrade path. `privilege_audit`'s `roles` was the one proven live to be
wrong. The other 25 were not individually re-verified against a real
database here — this table is the starting inventory for the follow-up,
not a claim that all 25 are also live bugs. `database_surveyor.py` was
also checked: only 2 bare `except Exception: pass` blocks exist there
(egeria-keyword extraction for PII detection), a different shape (no
collector field falls back to empty because of them) and not part of this
inventory.

**Follow-up (separate PR, after the enumeration-floor PR since it touches
the same file):** convert each of the 26 sites above to record
`{"error": str(exc), "failed_at": <analysis_id or field name>}` on its
section rather than silently defaulting, and update the corresponding
readers (`_schema_inventory_results`, `_row_count_snapshot_results`,
the four `DATABASE_ANALYSIS_HEADLINE_MAP` readers this slice added, and
any classic-UI reader touching the same fields) to render a recorded error
as "collection failed: `<reason>` — re-run" rather than falling through to
a zero-sentence or an honest-absence state that reads as "nothing to
report" rather than "something went wrong."

## Explicitly NOT done here

- **A shared Python/JS implementation of the scalar-fallback rule** —
  `_renders_text` and `scalarMeasures()` are two hand-written
  implementations of the same narrow rule, not one shared source. A
  divergence between them (someone changes one threshold in `app.js`
  without knowing to change `facts.py`, or vice versa) would silently
  reopen exactly this class of bug. Flagged here rather than fixed: unifying
  a Python backend rule with a browser-side JS formatter needs its own
  design pass (a shared JSON schema? A contract test asserting the two stay
  in lockstep?), not a quick patch bolted onto this PR.

## Tests

- `test_slice17c_renderable_answer_gate.py` (36 tests): `_renders_text`
  unit coverage (headline/prose/scalar/all-nested-dict/all-list/empty/
  `verdict`-excluded/overlong-excluded/null-excluded cases, plus the real
  `db_activity_signals` shape rendering via its scalars); the generalized
  gate firing at `resource` level (including a missing-`levels`-key case);
  one renderable fact among several unrenderable ones being enough; the two
  checks composing correctly (renders-something does not by itself satisfy
  a sub-resource question); the four real headline-map entries existing;
  reader-level tests confirming each new headline's actual sentence content
  (not just non-emptiness) — including `privilege_audit`'s PUBLIC
  `SELECT`/world-writable counts and its no-PUBLIC-grants wording;
  `db_activity_signals`'s reset-timestamp wording and its
  never-reset-(`NULL`) case; `privilege_audit`'s collection-failure
  handling (empty `roles` with real `table_grants` present still returns
  `None`, empty `roles` alone returns `None`, a real empty `table_grants`
  with non-empty `roles` still renders); and a stub-cursor regression test
  pinning `get_privilege_audit()`'s roles query against the exact
  psycopg2 percent-substitution behavior that broke it, so a future edit
  reintroducing an unescaped `%` fails a test instead of silently emptying
  the roles list again.
- `test_slice17_question_level_gate.py` (18 existing, unchanged assertions):
  fixture helper `_measured_envelope` updated to default each fact's
  `value` to a non-empty scalar (`{"measured": True}`), since the
  generalized gate's new first check would otherwise fire on every bare
  fixture — this proxies what a real analysis with even one scalar field
  does, and every test's sub-resource-specific assertion is unaffected.
- Confirmed directly against the real shared registry (not a fixture): the
  fixed `get_privilege_audit()`, run live against `coco_pharma`
  (`localhost_docker_coco_pharma`), now returns all 13 real, non-system
  roles where it previously returned zero.
- Full suite: re-run after each round of fixes, passing at the same
  pre-existing-failure baseline noted on slices 16, 17, and 17b
  (`test_egeria_live_smoke.py`, needs a live Egeria environment).

## Live signed-in gate

**First pass (owner, 8811, 2026-09-26): partial.** Resilience row good;
activity row reasonable but missing the reset timestamp (fixed, see above).
External-dependencies and privilege-audit rows weren't seen — both are
Analysis-stage questions and the owner was looking at Scouting. Confirmed
directly against the real registry (not the browser, since re-signing in
mid-session wasn't practical from here) that both render correctly on their
real Analysis-stage questions for `localhost_docker_coco_pharma`: "What
does this database depend on outside itself" → "1 extension(s)."
(`answerable=True`, `level_mismatch=False`); "Who can read and write what"
→ then showed `privilege_audit`'s collection-failure zeros, which are now
fixed to fall back to the honest state until a fresh survey runs (see
above) — needs a **re-survey** of `coco_pharma`'s `privilege_audit` step
before its Analysis-tab row will show a real sentence rather than the
no-summary-reader state, since the fix is in the read path going forward,
not a retroactive repair of the already-stored empty-roles row.

**Still needed**, on the Analysis tab specifically:

- The resilience, activity-signals, external-dependencies, and
  privilege-audit rows each show a real sentence (e.g. "Primary; no
  replicas; WAL archiving off; no backup tool detected." /
  "13 role(s); 1 superuser(s); PUBLIC has SELECT on K table(s); J
  table(s) world-writable.") — none an empty ✓, none the no-summary-reader
  state (privilege-audit needs `coco_pharma` re-surveyed first, see above).
- No ✓ anywhere on the Questions tab has an empty answer line — the
  general claim the ruling asked this slice to close, not just the one
  row that was caught live.
- "Which schemas carry the data" and "How big is this database" still
  behave exactly as slice 17b left them (must not regress).
- Coverage Signals / Subject Signals / Preliminary Fit still show Run
  correctly (must not regress — unrelated to this change, same screen).

Whoever runs this: append the outcome here, one sentence per screen, per
the coordinator brief's own gate convention.
