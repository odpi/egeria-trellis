# Schema-count headline + level-gate double-message fix — implemented

**Coordinator brief:** Phase 1b, follow-up after the enumeration-floor PR
(#301, `re/enumeration-floor-and-collector-honesty`).
**Replying to:** the owner's live gate screenshot of the 8811 build, two
defects both outside #301's own scope.
**PR:** #TBD (`re/schema-headline-and-level-gate-note`).

## (a) `schema_inventory` had no schema-level field at all

"How big is this database — schemas, tables, views, columns, rows and
bytes?" names schemas first, but `_schema_inventory_results` had no
schema-level field at all — the "8" the design session had in mind was the
credential banner's "6 of 8 schema(s)", a different card, not this one.

**Fix:**

1. `_schema_inventory_results` now reports `schema_count` — distinct
   `schema_name` across the stored `database_tables` rows (the schemas
   that produced at least one table row, "the visible ones"). A schema
   with zero tables at all (e.g. `public` on `coco_pharma`) has no rows to
   be distinct over and so isn't counted here — `schema_total` from the
   credential-capability probe (see below) is the true database-wide
   denominator.
2. `_schema_inventory_headline` (new, registered in
   `DATABASE_ANALYSIS_HEADLINE_MAP`) answers every part of the question
   this analysis owns (schemas/tables/views/columns) in one sentence, e.g.
   `"7 schema(s) (coco_ods, coco_sus, demo, demo_auth, eu_sales,
   target_sales, us_sales) · 61 table(s) (58 base, 3 view) ·
   479 column(s)."` — rows/bytes stay on `row_count_snapshot`'s own
   headline, which already covers them. When the credential-capability
   probe has run, the visibility fraction is appended:
   `", 6 of 8 visible to this credential"`.

**Deliberately NAMES the schemas, not just a count — this is not
cosmetic.** `_check_level`'s sub-resource gate (slice 17b/c) treats ANY
non-empty headline as evidence an analysis answered at its own level. A
bare count (`"8 schema(s)."`) would satisfy that check for "Which schemas
carry the data...?" (`container` level, `schema_inventory` alone)
**without naming a single schema** — reopening the exact "answered, but
nothing names a container" gap slice 17b closed, one level up, as a direct
side effect of fixing (a). Naming the schemas (capped at 15 — see
`_SCHEMA_INVENTORY_HEADLINE_NAME_LIMIT`, generous enough that every real
database seen so far lists in full) makes the headline a genuine answer at
that level instead. A full per-schema classification ("system, empty or
staging") is still slice 22's own dedicated view — this headline names
schemas, it doesn't classify them.

`test_slice17_question_level_gate.py`'s
`TestRealCatalogAgreesWithTheSlice17bLiveBugReport` is updated
accordingly: "Which schemas carry the data" is now correctly answered
(`level_mismatch=False`) using the real headline; the "no headline at all
→ still gates" case is kept as its own regression pin on `_check_level`
itself, independent of whether `schema_inventory` currently has a
headline.

## (b) `_check_level` double-reported a `NOTHING_FOUND` fact

The owner's screenshot showed `db_activity_signals`/`db_resilience` cards
rendering BOTH "This ran; no summary reader exists yet for its results."
AND "This analysis ran and found nothing — a measured zero, not a gap in
coverage." — contradictory, and specifically the "no summary reader" note
was wrong: something DID render.

**Cause:** `readEnvelope` (`app.js`) renders a synthesized sentence for a
`NOTHING_FOUND` fact with no headline/prose — `"<analysis_id> ran and
found nothing."` — ahead of the headline/prose/scalar rungs
`FactLayer._renders_text` checks (slice 17c). `_renders_text` didn't know
about that rung, concluded nothing rendered for a headline-less
`NOTHING_FOUND` fact, and `_check_level` added its own note on top of an
answer that, on screen, already had one.

The two facts on the owner's screenshot were themselves ~26–28h old —
stored from before slice 300's collector-honesty conversion, so no
`_errors` and an all-empty `operations` sub-section resolved to
`NOTHING_FOUND` the ordinary way (`_has_content` on an empty dict). This
bug fires for ANY `NOTHING_FOUND` fact with no headline, not only stale
ones — the age of the specific data was incidental to reproducing it, not
the cause.

**Fix:** `FactLayer._renders_text` now returns `True` immediately when
`fact.state == NOTHING_FOUND`, matching `readEnvelope`'s own ordering. A
`NOTHING_FOUND` fact with a headline was already fine (headline renders
regardless); the gap was specifically a `NOTHING_FOUND` fact with no
headline and no renderable scalars, which read as "answered by the
frontend's special case" to a human but "nothing renders" to
`_renders_text`.

`NO_READER` needed no equivalent change: it is not in `Fact.is_known`'s
state list (`MEASURED`, `NOTHING_FOUND`, `PARTIAL`,
`MEASURED_WITHIN_CREDENTIAL_SCOPE`), so a `NO_READER` fact never enters
`_check_level`'s `known` list at all — if every fact for a question is
`NO_READER`, `env.answerable` is already `False` and `answer()`'s outer
`if not env.answerable` branch handles it before `_check_level` is ever
called.

### Part (ii): does a fresh re-run now show "could not measure" instead of "measured zero"?

Checked, with a nuance to report rather than a clean yes:

- **`db_resilience`**: already converted to the `_errors` pattern in slice
  17c. A privilege gap on ANY of its four sub-queries records `_errors`,
  and `_db_resilience_headline` checks for it before computing the normal
  sentence, returning `"Collection failed (<field>): <reason> — re-run."`
  — this already works as the ruling wants, no change needed here.
- **`db_activity_signals`**: still NOT converted (`get_table_activity()`/
  `get_stats_reset()` return `list[dict]`/`str`, not `dict` — no natural
  place for `_errors` without changing their contract, as logged in the
  enumeration-floor PR's own "Explicitly NOT done here" section). A
  privilege gap here degrades to `NOTHING_FOUND` (now correctly rendered
  as a single honest "ran and found nothing" sentence, thanks to fix (b)
  above) rather than "Collection failed: ...". This is an HONEST
  improvement over the double-message bug, but not literally
  "could not measure: `<reason>`" — that needs the same
  `get_table_activity`/`get_stats_reset` conversion the enumeration-floor
  PR deferred, still open, not attempted here (out of this PR's own
  narrow scope of (a) and (b)).

Live re-run confirmation on `coco_pharma` (this session's own registry,
already refreshed by prior debugging in the enumeration-floor PR):
`db_activity_signals`/`db_resilience` currently resolve `state: measured`
with real headlines (no privilege gap on THIS credential for these
particular queries right now) — so the specific "does a privilege gap
show 'could not measure'" question could not be re-demonstrated live in
this pass; the code-level answer above is what's checkable without one.

## Tests

- `test_schema_inventory_headline.py` (new, 7 tests): a small schema count
  is named in full; a large one collapses to a bare count; relation kinds
  are named separately; column count is named; the credential-visibility
  clause is added when available and omitted when not; `None` when
  nothing was measured.
- `test_nothing_found_not_double_reported.py` (new, 5 tests):
  `_renders_text` recognizes a headline-less `NOTHING_FOUND` fact as
  rendering; a `NOTHING_FOUND` fact with a headline still renders; a
  genuinely unrenderable `MEASURED` fact still does NOT (the fix is scoped
  to `NOTHING_FOUND`, not a blanket suppression); `_check_level` end-to-end
  no longer adds a note for the exact live scenario, and still adds one
  for the original slice 17c bug (contrast case).
- `test_slice17_question_level_gate.py`: `TestRealCatalogAgreesWithTheSlice17bLiveBugReport`
  updated — `schema_inventory` now has a headline in the real map; "Which
  schemas carry the data" is answered; the no-headline-at-all mechanism
  test is kept as its own regression pin.
- Full suite: [pending — recorded once the background run finishes].

## Live signed-in gate

**Not yet run.** Needs a real signed-in session against `coco_pharma`,
after a fresh `Schema Inventory` run, to confirm:

- "How big is this database" now names the actual schemas
  (`coco_ods, coco_sus, demo, demo_auth, eu_sales, target_sales,
  us_sales`), not just a bare count.
- "Which schemas carry the data...?" now shows a real answer (✓, not
  gated) — a genuine change from slice 17b/c's demotion, since the
  question is now actually answered at its own level.
- No card shows two contradictory lines together (the "no summary reader"
  + "ran and found nothing" pairing). A card whose stored data predates
  slice 300 needs a fresh re-run first to pick up the collector-honesty
  fields at all — this fix changes how a `NOTHING_FOUND` fact is
  presented, not whether stale data gets re-measured.

Whoever runs this: append the outcome here, one sentence per screen, per
the coordinator brief's own gate convention.

## Follow-up (2026-09-26): owner's live gate on this build found two more issues

The double-line contradiction from (b) was confirmed gone and schema names
render, but the owner's gate raised two further items, both on this same
branch.

### (c) Leading count is now "how many schemas EXIST", not "how many have tables"

**Owner's ruling:** "we should say 8 schemas if there are, even if one has
no tables." The headline's leading number used to be
`len(schema_names)`/`schema_count` — schemas that produced at least one
stored table row — which understates the real schema count whenever a
schema exists but is empty (`public` on `coco_pharma`, noted as a caveat
in (a) above but not actually fixed there).

**Fix:** when the credential-capability probe's `schema_total` is
available, it is now the leading number, with a separate "N with tables"
clause carrying the named/counted schemas that actually have data:
`"8 schema(s), 7 with tables (coco_ods, coco_sus, demo, demo_auth,
eu_sales, target_sales, us_sales), 6 visible to this credential · 61
table(s) (58 base, 3 view) · 479 column(s)."` (the visibility clause
dropped its old "of 8" since 8 is now stated up front). When no probe has
run at all, `schema_total` is absent and the wording falls back to
`"7 schema(s) with tables (...)"` — explicitly labeled, so the reader
knows it's a floor rather than the real total, per the owner's own
fallback instruction.

### (d) The `schema_total` key mismatch — a real, separate bug, not a display artifact

The owner reported the visibility clause not rendering on the live page at
all, even though the header's own "sees 6 of 8 schema(s)" banner showed
it for the same database — i.e., two pieces of UI reading the same
underlying probe disagreed with each other.

**Root cause:** `_credential_capability_results` (which
`_schema_inventory_headline` and `_credential_scope_status` both read)
called `registry.get_latest_database_survey(slug)` — the SINGLE most
recent stored survey row only. `databases.py`'s `_to_summary` (the
header's own data source) instead loops over `registry.
get_database_surveys(slug)` — every stored survey, newest first — with
its own comment explaining why: "a plain schema/statistics-only run after
the probe ran would otherwise silently hide a still-current capability
reading." `_credential_capability_results` never had that same defense,
so the moment the LATEST run for a database didn't include the
`credential_capability` step (true for `coco_pharma` at gate time), it
saw an empty blob while the header kept showing an older, still-valid
reading.

**Fix:** `_credential_capability_results` now searches every stored
survey the same way `_to_summary` already does, returning the first
(newest) one whose blob actually carries a `credential_capability` key.
`test_credential_totals_from_an_older_survey_still_show_when_the_latest_run_omits_the_probe`
(new, `test_schema_inventory_headline.py`) pins the exact failure shape:
a fake registry with two stored surveys, only the older of which carries
the probe.

Not a display bug and not this document's own `_schema_inventory_headline`
being wrong in isolation — every OTHER reader of
`_credential_capability_results` (`_credential_scope_status`, the third
fact-envelope state) was equally affected and is fixed by the same change.

### Tests (follow-up)

- `test_schema_inventory_headline.py`: existing credential-totals
  assertions updated to the new wording (`"N visible to this credential"`,
  no `"of M"`); new `test_leading_count_is_schema_total_not_schemas_with_tables`
  pins (c); new `test_credential_totals_from_an_older_survey_still_show_when_the_latest_run_omits_the_probe`
  pins (d). `_FakeRegistry` gained `get_database_surveys` alongside its
  existing `get_latest_database_survey`, since the reader now calls the
  former.
- Full suite: 6459 passed, 103 skipped, 1 failed
  (`test_egeria_live_smoke.py::TestTheByNameFallbackWorks::
  test_a_cataloged_database_is_findable_by_name`) — pre-existing, hits the
  live dev platform directly, and is unrelated to this branch:
  `coco_pharma`'s Egeria linkage has been recorded `stale` since
  2026-09-22 (a prior platform reset — `registry.get_egeria_linkage
  ("database", slug)` shows a 404 from Egeria's own repository for the
  stale GUID), so the by-name lookup correctly finds nothing; this
  specific test's own skip condition is supposed to exclude exactly that
  case but apparently doesn't catch it for the deployment's current set of
  cataloged databases. Diagnosed while investigating a Slice 12 report of
  the same underlying staleness (see `is_published` honouring linkage
  status, tracked separately as `re/is-published-honours-linkage`, pulled
  forward from slice 18).
