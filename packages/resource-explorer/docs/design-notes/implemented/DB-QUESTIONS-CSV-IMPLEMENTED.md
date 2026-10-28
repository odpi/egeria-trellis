# Implemented: the database question rows and the cross-type rewording

**Replies to:** `multi-resource-questions-design.md` §4 (the cross-type set) and
§5.2–§5.6 (the database rows), plus the §2.1 decision that Automate is not a
funnel stage.

**Shipped in:** `re/db-questions-csv`, one file —
`docs/dr-egeria/resource_questions.csv`. Branched from `main` at `0acb2565`.

This is Stream 4 of the multi-resource plan. It changes **row content only**.
The generators, the reader and the generated YAML are Stream 2's and are
untouched here; three tests fail because of that, all three deliberately, and
each is named below with the Stream 2 change that turns it green.

---

## What landed

The CSV went from **21 columns × 52 rows** to **22 columns × 88 rows**.

### The new column

`Resource Types` was added between `Answering Mechanism` and `Purposes`, per
the project owner's decision in design §1.1 — `;`-separated, `*` for all. Every
pre-existing row was set to `repo`, which is what they are in effect today; the
rows §4 makes cross-type were then moved to `*`.

Distribution: `repo` 33, `database` 25, `*` 24, `database;filesystem;file` 3,
`database;filesystem;file;dataset` 2, `database;filesystem` 1. No row is blank.

**Stream 2 owns this column's final shape.** It is authored here so the rows are
complete and correct the moment Stream 2's generalisation merges — see
"Pending Stream 2" below for what it does not yet do.

### §4 — the cross-type rows

Nineteen existing rows are now `*`. The brief said eighteen; the extra one is
the licence row, which design §4 lists twice (once at Scouting as "under what
licence or agreement", once at Analysis as "restrictions beyond the licence").
Nineteen is the count of existing rows marked, not a disagreement with §1.2's
"roughly 18".

**Only eight of the nineteen had their text or stage changed.** That restraint
is deliberate and is the main judgement call in this branch — see "The
`facts.py` collision" below.

Seven reworded:

| Was | Now |
|---|---|
| What does this repository do? | What is this resource, and what is it for? |
| Who maintains this repository? | Who owns this resource (accountable owner), and who administers it? |
| Has this repository already been catalogued in Egeria and when? | Has this resource already been catalogued in Egeria, and when? |
| Are there any restrictions for use? | Under what licence or agreement may this resource be used? (also moved Analysis → Scouting) |
| What are similar repos/projects? How does this differ? | What are similar resources, and how does this differ? |
| What explicit license does the repository use…? | What explicit licence does this resource use…? |
| Do we know what the cost to run it is? | What does this resource cost to run, host or license? |

One stage change, no rewording: "How much has changed since the last time this
was surveyed — is it worth re-running now?" moved **Automate → Discovery**, per
the §2.1 decision that Automate is not a funnel stage. It is asked before
deciding to spend on a re-run, so Discovery is where it belongs.

The other eleven were left verbatim and only gained `*`. Design §4's wording for
them differs from the CSV's only cosmetically — a contraction, a British
spelling, a shortened tail — and every one of those texts is a live key in
`facts.py`. Changing them would have bought nothing and broken an answer each.

Six rows were **added** for the parts of §4 that have no existing row:
engine reachability vs. local survey (rule B), sovereignty and jurisdiction,
scope of the data in time, restrictions beyond the licence, data-product
readiness (supply), and defined need or market (demand).

### §5.2–§5.6 — the database rows

Thirty rows added: 6 Scouting (§5.2), 1 Analysis/Enrichment (§5.5's
non-observable half), 7 Discovery (§5.3), 10 Analysis (§5.4), 6 Assessment
(§5.6).

Four §5 rows were **not** added as database rows because §4 already covers them
cross-type, and duplicating them would have produced two questions with the same
meaning and different texts: "what is this database and what is it for", the
sovereignty first pass, "how much has changed since the last survey", and the
two data-product composites. §5.6's "does it meet a named standard?" was also
skipped — the design marks it Certify path, no new analysis.

`Answering Analysis` was written to the guide's conventions, against the real
`analysis_catalog.yaml`, not against the design's proposed names:

- **`GAP:` — 26 of the 30.** Everything design §5 marks "(new)" has no analysis
  behind it, and the guide is explicit that `GAP:` is the right answer more
  often than it feels. Each note names what the proposed analysis would read
  (`pg_stats`, `pg_stat_user_tables`, `pg_foreign_server`, …) so the gap is
  actionable rather than just recorded.
- **Bare existing ids — 2.** `schema_inventory` answers "which schemas carry the
  data" and "what is the full schema".
- **`MIXED:` — 1.** Size, which needs `schema_inventory` and
  `row_count_snapshot` together.
- **`PARTIAL:` — 2.** Both are `privilege_audit`: it genuinely answers the
  access half of "who can read what" and of the sensitive-data exposure
  assessment, and genuinely cannot answer the sensitivity half without the
  proposed data-class matching.
- **`N/A — human-supplied` — 1.** §5.5's backup and restore-test questions.

No id was invented. `egeria_db_survey`, `privilege_audit`, `row_count_snapshot`,
`schema_inventory` and `filesystem_inventory` are the only non-repo analyses
that exist, and only the four named above are cited as answering anything.

One correction to the design worth recording: **`sql_analysis` does not exist as
an analysis.** Design §5.3 marks it "(exists)", and a step by that name really
does run in `surveyors/database/survey_definition_adapter.py` and parse view DDL
with SQLGlot. But `analysis_catalog.yaml` has no `sql_analysis` entry, so nothing
in the question layer can resolve it. That row is `PARTIAL:` and says so.

---

## Verification

**Column-count integrity** (the done-test's first item), by loading the CSV with
Python's `csv` module: every one of the 89 lines parses to exactly 22 fields.
No duplicate question text across all 88 rows; no blank `Question` or
`Resource Types` cell.

**Only the intended rows changed.** Re-parsed the `HEAD` version and this one and
compared row by row ignoring the new column: 44 of the 52 original rows are
byte-identical apart from the column insert, and the 8 that differ are exactly
the 7 rewordings and the 1 stage move listed above. The whole-file diff
(89 insertions / 53 deletions) is the column insert touching every line, not a
reformat — the file is still LF, and quoting is unchanged.

**Kinds.** Under today's generator: gap 32, analysis 23, direct 11, mixed 8,
human 7, partial 4, chart 1, **unknown 2**. Under a local simulation of Stream
2's two changes (`Resource Types` added to `NON_PERSPECTIVE_COLUMNS`,
`_load_known_analysis_ids` reading `database_analyses` and
`filesystem_analyses` as well as `repo_analyses`): analysis 25, **unknown 0**,
and the phantom perspective gone. The simulation patched a copy in a temp file;
the generator on disk was not modified.

**Tests:** full suite, `uv run pytest tests/ -q` — **5195 passed, 3 failed,
103 skipped**.

---

## Pending Stream 2

All three failures are the same root cause and none is a content defect.

1. `test_catalog_invariants.py::TestNoStaleGaps::test_every_row_parses_into_a_known_kind`
   — the two bare `schema_inventory` rows parse to `unknown`, because
   `_load_known_analysis_ids()` reads only `repo_analyses`
   (`csv_to_question_catalog_yaml.py:74`, design §1.1 item 1). Writing anything
   else would have misstated a row to work around a bug that is already scoped
   and assigned. Verified green in the simulation above.
2. `test_question_catalog_generator_guard.py::test_the_committed_catalog_is_what_its_csv_generates`
3. `test_question_catalog_generator_guard.py::test_the_committed_questions_document_is_what_its_csv_generates`

   — the committed `question_catalog.yaml` and `questions/scouting-questions.md`
   are now stale against the CSV. Regenerating them is Stream 2's, both because
   it owns the generators and because regenerating with today's generator would
   bake the phantom `Resource Types` perspective into the committed YAML.

Also pending, not a test failure: **`Resource Types` is currently read as a
perspective column** on every row, exactly as the comment above
`NON_PERSPECTIVE_COLUMNS` warns. Harmless while unmerged, wrong the moment
anything consumes the YAML.

---

## Two things the coordinator should decide, not me

**Both resolved 2026-09-21**, when this branch was merged with `main`
(Stream 2, `re/question-catalog-multi-type`, had landed by then) — recorded
here rather than rewritten out, since the decisions and their reasoning are
still the record worth keeping.

### The `facts.py` collision

`RESOURCE_STATE_SOURCES` (`facts.py`) keys **fourteen resolvers on exact question
text**, and `facts.py` is explicitly outside this stream's scope. Four of the
seven rewordings above change a text that is one of those keys:

| Reworded question | Resolver that stops matching |
|---|---|
| What is this resource, and what is it for? *(was "What does this repository do?")* | `_r_description` |
| Who owns this resource… *(was "Who maintains this repository?")* | `_r_maintainers` |
| Has this resource already been catalogued in Egeria, and when? | `_r_catalogued` |
| Under what licence or agreement may this resource be used? *(was "Are there any restrictions for use?")* | `_r_license` |

Until those four keys are updated, each renders as unanswered rather than
wrong — the Path B lookup misses, it does not mis-resolve. Design §1.1 already
lists `facts.py:589-618` as one of the five places that must be generalised, so
this is on the plan; it is not on *this* branch, and it should land in the same
merge window as the rewording rather than after it.

This is also why eleven rows were left verbatim. Every additional cosmetic
reword would have cost another resolver for no gain in type-neutrality.

**Resolution:** all four keys updated to the new wording in the same commit
that merged this branch with Stream 2's. See
`QUESTION-CATALOG-MULTI-TYPE-IMPLEMENTED.md`'s "HANDOVER"
section for the full before/after table and its own "Resolved" note.

### The Egeria join key

The guide is explicit that `Question` text is the case- and
punctuation-sensitive join key back to the Egeria `Question` GlossaryTerm, and
that editing it after publishing creates a new term rather than updating the
existing one. Seven rows are reworded here. If those terms are already published
in a live Egeria, someone has to decide between re-linking them and accepting
seven orphans — the design's §1.1 decision authorises the rewording but does not
say which.

**Resolution:** each reworded row (and the one stage-moved row) got a dated
`Catalog History` entry recording the change, per the guide's own audit-trail
convention. **Retiring the seven old terms' `ScopedBy` links on a live Egeria
platform is explicitly NOT done here** — that only matters once this CSV is
next published, and is a separate, deliberate step for whoever does that
(`scripts/reconcile_survey_definition_scopes.py`, report-only by default;
the exact tool its own module docstring names for this exact incident shape —
a Question term changing text, old `ScopedBy` links left dangling). Nobody
should read this merge as having already done that.

---

## One trap worth carrying forward

`TestNoStaleGaps::test_no_gap_row_names_a_readable_analysis` matches analysis ids
as **substrings** of the note. Design §5.2 proposes an analysis called
`db_documentation_coverage` — which contains `documentation_coverage`, a real and
readable *repo* analysis. Writing the proposed id into a `GAP:` note therefore
tripped the guard: the row was read as claiming no mechanism exists while
pointing at one that does.

It took two attempts to fix, because the first correction explained the collision
in prose that itself spelled the colliding id out. Both database documentation
rows now describe the proposed analysis without naming it, and say why.

The general shape: a *proposed* id that contains an *existing* id as a substring
is invisible to review and caught only by this guard. Worth a look when the rest
of design §5's proposed names become real — `column_profile`,
`schema_conventions` and the `db_*` family are clear today, but the check is
substring-based and will stay that way.
