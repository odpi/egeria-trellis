# Review: the unified Survey & Analyses pane (#285) — revert, then re-land in three gated slices

**For:** the project owner and the coordinating session.
**From:** the design session, 2026-09-25, at the project owner's request after
a live test of `coco_pharma` found the merged pane unusable ("I can't find
anything I would view as correct or usable").
**Read against:** `main` at `3999c4a1` (#285 merged). Code read, not
live-tested; the five screenshots were seen after the first draft and §6 is what
they add.

## Verdict

**Revert #285 now.** It is a clean revert with nothing built on it, and it
removed a working filter (§1 below) while adding nothing the owner could use.
Then re-land the shape in three slices, each gated by a live, signed-in run
against `coco_pharma` recorded in the slice's `*-IMPLEMENTED.md` with what
the implementer saw on screen. Unit tests passed 1327/1327 on a pane that
did not work; they cannot be the gate for a user surface, and the repo's own
rule already says so (`extending-resource-explorer.md`, "open the thing a
user opens").

But the revert fixes **one** of the five points, and none of §6's. Three are older gaps that
#285 made visible by putting everything on one screen, and one is a
design-document defect of mine. Reverting and re-landing the same shape
would reproduce four of the five complaints.

## The five points, sorted by cause

| # | Owner's report | Cause | Introduced by | Fix |
|---|---|---|---|---|
| 1 | Surveys are the same for all stages; they do not correlate to the stage's questions | `renderAnalysesIndexSection` pushes all 21 local analyses with no stage filter; the old `loadSurveyPane` filtered `r.tier === stage` (`git show 41703c55`, lines 616–617 removed). Every database analysis carries an `intent` (stage) in `analysis_catalog.yaml`, so the filter was possible and was dropped | **#285** — and my reply gave it cover: §2 of `REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md` says "the scope concept leaves the pane", meaning the *banner about how the list was built*; an implementer read it as "do not scope the list". That ambiguity is mine, corrected in §3 below | revert; re-land with the rule stated as **the stage's pane lists the surveys that answer the stage's questions**, derived from the question catalog's `analysis_ids` (survey-model Part II §4), never from a hand list |
| 2 | Egeria surveys are not runnable | by design, until RE can trigger them from the pane: the row's gate reason is "not runnable from Resource Explorer yet" | pre-existing; my reply §1 kept them as blocked rows | acceptable *only* when the rest of the pane works; on a pane where nothing else runs it reads as one more broken thing. Re-land order matters (§4) |
| 3 | Chat answer to a credential-capability question was garbled ("6 shown to the model", "No list to open — coverage_signals has no member reader yet", repeated) | not a leak of a trace: it is `chat.js:224–257`'s **evidence footer**, built for repositories (dependency lists with a "shown to the model" count and an open-list link). For a database every analysis has no member reader, so the footer degenerates into one "No list to open" line per analysis, and the answer itself was empty because the capability probe's result is not in the evidence compiler | pre-existing, surfaced by #186 (database chat now compiles evidence) | (a) the footer renders only lists that exist, never absence lines, and never more than the answer; (b) `context_compile` includes the stored capability probe as a fact so "what can this credential see" is answerable; (c) when compiled evidence is empty the answer says so in one sentence |
| 4 | No visualisation; Schema Inventory "lists schemas, tables, columns" and shows counts; Row Count Snapshot shows 3,526 rows, 0 bytes, "7 of 10 catalog estimates" | the `/next` UI has **no database report view at all** — no schema/table/column listing (grep of `next/*.js` for `schema_info` returns nothing), while the classic UI renders a full per-schema accordion (`index.html::renderDatabaseSurveyReport`). "By analysis" shows scalar readers only. `0 bytes` is the size-not-measured case rendered as a number (`database_surveyor.py:1482–1503` already distinguishes it; the reader does not). "7 of 10" is the new estimate labelling on a whole-database rollup that the schema-grain work (#266) should have made per schema | pre-existing: DB entered `/next` (#217) before `/next` had any DB result view; the design's §11 deferred visuals to designer round 2 but a *listing* is not a visual, it is the result | port the classic schema listing into `/next` as the Schema Inventory result view (rows from the structured tables, per schema); render "not measured" for bytes; make Row Count Snapshot per schema and per table with the estimate stamp on each row |
| 5 | Questions do not let me run surveys; "How big is this database" looks at one unnamed schema | (a) the question row has no run affordance for databases because runnability comes from hand lists: `DATABASE_ANALYSIS_STEP_MAP` exists **twice** (`database_surveyor.py:50`, `survey_definition_adapter.py:823`) and covers 10 step-backed ids; the five older `db_derived` analyses are covered by a second list; the three newest (`subject_signals`, `coverage_signals`, `preliminary_fit`) are in neither, hence "has no mapped survey step(s)" on exactly those rows. (b) "one unnamed schema" is the rollup rendered without naming what it rolled up | pre-existing; the "one flat dict" failure `extending-resource-explorer.md` §Scaling warns about, now with two copies | derive runnability from the analysis catalog and the step registry (`PRODUCES`, design §17.1) and delete both hand lists; every database answer names its scope ("across 6 of 8 visible schemas: …") |

## §3 · The correction to my reply (#282)

`REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md` §2 now states, in these
words: **the list is scoped to the stage.** A stage's pane lists the surveys
and analyses that answer that stage's questions, derived from the question
catalog, plus the stage's own analyses by `intent`. What leaves the pane is
the *banner about how the list was scoped* ("Scope: all tiers — stage filter
unavailable"), not the scoping. When nothing answers the stage's questions,
the empty state says so and names the nearest stage that has something.

## §4 · Re-landing order, three slices, each with a live gate

1. **Runnability from the catalog, and the Questions tab runs surveys.**
   Delete both `DATABASE_ANALYSIS_STEP_MAP` copies; derive analysis → steps
   from the catalog and step registry; a question row shows Run for each
   analysis that answers it. Gate: on `coco_pharma`, signed in, every
   question with an answerable analysis shows Run, and "How big is this
   database" runs Schema Inventory and names the schemas it counted.
2. **The Schema Inventory and Row Count result views in `/next`**, per
   schema, from the structured tables, with "not measured" and the estimate
   stamp rendered. Gate: the owner can see schemas, tables and columns for
   `coco_pharma` in `/next`, with the credential banner above them.
3. **The unified pane, stage-scoped**, per the corrected reply. Gate: the
   Scouting and Discovery tabs list different surveys, each row's second
   line shows the last result, Egeria's native survey is a blocked row with
   its reason, and nothing on the screen is a rollup without a name.

The chat footer (#3) is its own small PR and does not wait.

## §6 · What the screenshots add — three defects worse than the five points

Seen 2026-09-25: *By analysis* (Scouting), *Questions* (Scouting), *Survey &
analyses* (23 rows), and the chat answer to "What credential capabilities do
I have?". In order of severity.

**6.1 "Ran and found nothing — a measured zero, not a gap in coverage" is
being rendered where nothing was read.** On *Questions*, "Is this database
alive?" answers `db_activity_signals ran and found nothing — a measured
zero`; "Is this database a primary or a replica, is WAL archiving
configured?" answers `db_resilience ran and found nothing — a measured
zero`. Neither can be a measured zero: a database with 3,526 rows has
activity counters, and `pg_is_in_recovery()` always returns a value. On
*Survey & analyses*, **Credential Capability** reads "Just now: nothing
found" — the probe whose entire output is "connected as X, sees N of M" is
summarised as nothing found. The pattern: an analysis with no results
reader is rendered by a generic fallback that reports absence of a reader
as *measured absence of findings*, in the exact wording this codebase
reserves for a verified zero. That is `find-absence-as-answer` inverted
and made confident. **Rule for the re-land: an analysis with no reader
renders "ran; no summary reader yet", never "nothing found", and the ✓ is
withheld.** This alone justifies the revert regardless of the rest.

**6.2 A generic summariser is fabricating text from result keys.** "5
rankeds · 2 signals useds · 2 signals missings" (Database Classification),
"no grants addeds or grants revokeds found", "53 grains · 3 determineds ·
50 undetermineds", "56 per tables" (Change Rates), and Schema Fingerprint
and Schema Conventions both summarised as "53 tables · 427 columns", which
is not a fingerprint and not a convention check. My reply §1.1 said the
result line comes from each analysis's *existing* results reader and "no
new summariser". A pluralising key-walker was built instead. **Delete it.**
Where no reader exists, 6.1's honest state applies.

**6.3 The cross-type questions carry repository answering logic on a
database.** "What is this resource?" → `N/A — direct field
(Project.description) … summary of the top level README`; "Who owns this
resource?" → `GAP: repository_health + chaoss_metrics … are not a real
analysis for database resources`; "Under what licence?" → `GitHub license
field`. Stream 4 reworded the *question* text to be resource-neutral and
left the *Answering Analysis* column repository-shaped, so every `*` row
answers a database with repository prose. The catalog needs a per-type
answering analysis for cross-type rows (a second CSV column keyed by
resource type, or per-type resolvers in the fact layer), and until it
exists those rows must render *not authored for databases*, which is the
state Stream 2 built for exactly this.

And the smaller ones, each real:

- **Question/answer mismatch shown as answered.** "Which schemas carry the
  data, and which are system, empty or staging?" is ✓ with "table count 56
  · column count 427 · catalog only table count 53" — no schema named. The
  ✓ means the mapped analysis ran, not that the question was answered.
- **Two tabs disagree about the same run.** *By analysis* says Activity
  Signals, Operational Resilience and Credential Capability are "Registered,
  never run" in red; *Survey & analyses* says all three ran "just now". Two
  readers of one state.
- **Developer prose as user text.** Row descriptions cite "design §5.2,
  §5.7", "MIXED analysis (design §5.5)", and "REPLY-DATABASE-CREDENTIAL-
  CAPABILITY-VISIBILITY.md, replying to ASK-…-#251"; the blocked rows say
  "either it's a publish action (not a survey) or an unknown id"; the chat
  header lists JSON keys ("compile_id, resource_type, spec_id, budget, used,
  headroom, packed, lists, coverage, instructions_variant, gaps, recorded").
  The catalog's `description` field was written for developers and is being
  shown to users unedited; it needs a `summary` for people or the rows need
  the question they answer as their line, which the pane already has.
- **The footer contradicts the list.** Twenty runnable rows above; below
  them, "No RE-authored surveys are defined for PostgreSQL Database yet."
- **"Recommended" on every row** carries no information; "chat-only — no
  question asks" on Credential Capability contradicts `security-model.md`
  §7, which makes the probe's result the banner and a question's answer.
- **Credential scope is absent from the numbers.** "10 of 56 tables
  measured", "0 B", and no "as `egeria_user`, 3 of 8 schemas readable" —
  the one fact the probe exists to attach, on the same screen where the
  probe says it found nothing.
- **Relationship Graph: 53 tables · 53 components · 53 isolated tables** is
  the whole-database rollup with no schema named — the aggregation-grain
  defect #263 described, on screen.

## §5 · The process change

Add to `COORDINATOR-BRIEF-MULTI-RESOURCE.md`: **a PR that changes a user
surface is not done until the implementer has used it, signed in, on a real
resource, and the `*-IMPLEMENTED.md` says what they saw** — a sentence per
screen, not a screenshot requirement. Unit tests gate regressions in logic;
they cannot gate whether a person can use the screen. Four invisible
extensions in September and this pane were all green in tests.
