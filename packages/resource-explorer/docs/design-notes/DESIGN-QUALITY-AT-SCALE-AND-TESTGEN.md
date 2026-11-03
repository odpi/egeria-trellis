# DESIGN — Quality checks beyond one database: RE's quality slice, TestGen's runtime, batches and Egeria (2026-10-21)

From the design session, at the owner's request through the RE
coordinator ("yes, send testgen to design"). The owner's question: "Do
we need to be able to kick off work to effectively use it for more than
a single project/database?" Short answer: yes, and RE already has the
three mechanisms; what is missing is one definition that uses them, a
price, and a clear line between what RE runs today and what of TestGen's
wider catalogue comes later. Facts on TestGen's interfaces below
come from the coordinator's summary of the 10-21 live run and are marked
where the evaluation session should confirm them.

## 1. State today

| Piece | State |
|---|---|
| RE quality slice 1 (#809, main 885053a5) | per-card Value Profile and Quality Checks on one Postgres database; four registry tables (`database_value_profiles`, `database_quality_proposals`, `database_quality_results`, `database_quality_verdicts`), created at the next 8813 restart after a peer round; three linear steps `postgres_value_profile` → `propose_quality_checks` → `run_quality_checks`; seven vendored TestGen test types; verdicts in Curate |
| #791 | superseded verdicts retired; works with pyegeria 6.2.7 (#806) |
| Engine-host declarations for propose and run | held (`HELD_FROM_DECLARATION`) because they would complete with empty survey reports until results publish |
| TestGen runtime (egeria-workspaces #663) | ran live 10-21: `seed_examples.py` creates one TestGen project and connection per target database, reading as `surveyor`, then profile, generate, test; five example projects took about three hours; SUBSCRIPTION_STAGING failed on TestGen's 50-character schema-name column; `export_testgen.py` and `compare/compare_re_testgen.py` diff TestGen's profile and tests against RE's slice-1 tables |
| Egeria | `DataQualityRule` (0450) exists on the platform; no pyegeria wrapper (`SPEC-PYEGERIA-DATA-QUALITY-RULE.md`, #755 spec only); results to Egeria are a mapping document |

The owner's decisions of 2026-10-19 stand and bound this note: D1, TestGen's
catalogue is vendored as data and RE executes it, TestGen's engine,
metadata database, scheduler and UI are not run as a service; D2, RE is
not the coordinator, Egeria coordinates through a Survey Definition or
RE's executor runs the steps; D3, checks are proposed in RE and accepted
into Egeria as `DataQualityRule`s.

## 2. Rulings

### 2.1 Engine of record now; TestGen's wider catalogue later

RE's vendored checks propose and run (D1). TestGen's live runtime is used
today for calibration: its generated tests and profile are compared with
RE's for the same database, and every disagreement is a finding about
RE's port. Nothing publishes from it and it does not run on a schedule.

That is not the end state. The owner (2026-10-21): "testgen has
additional quality checks and the ability to define your own rules, so
at a later stage we may want to deal with that (backlog). We should
inventory testgen's out of the box tests and map those." So:

- **Backlog entry:** TestGen's checks beyond the seven RE vendored, and
  TestGen's user-defined rules, as a later stage.
- **Next concrete step (slice Q4 below):** an inventory of every TestGen
  out-of-the-box test type, one row each, mapped to one of: an RE slice-1
  type (ported), an Egeria quality dimension and annotation (where RE
  could port it as data the way D1 did), or "not covered" with the reason
  (needs TestGen's inference, needs a sample, needs a custom rule
  engine). The inventory decides what slice 2 and later port; it is a
  design task, not a build.
- **No adapter that drives TestGen from RE** for now. The seed script
  stays a hand-run harness; whether TestGen's engine ever runs as a
  service for the checks RE will not port is the backlog question above,
  decided after the inventory.

### 2.2 Batch: one Survey Definition, run across a work list, scheduled by Automate

The three mechanisms are one path, not three options:

1. **The definition.** `DatabaseQualitySurvey`, a Survey Definition
   authored in Dr.Egeria with the three steps in order (value profile,
   propose, run) and the guards they already declare. It runs on one
   database the way every definition does, from the Survey press or the
   "coordinated by Egeria" control once the engine-host declarations are
   unheld.
2. **The batch.** A work list of databases plus "run this definition
   across the list" (the work-list batch run RE has, `enqueueBatch`),
   one run per database, each its own row with its own price, through
   the same queue and the same credential gate. The batch is a list of
   runs, never a run of its own, so every result, verdict and proof row
   stays per database.
3. **The schedule.** Automate schedules the batch (the work list and the
   definition) with the existing change-detection rule: a database whose
   inventory did not change since its last quality run is skipped and
   says so.

Nothing new is invented; the slice is the definition plus the two
existing controls pointed at it, with the per-run price shown before
the batch starts.

### 2.3 The price, and sampling on large tables

TestGen's three hours for five projects is TestGen's cost (its own
profiling, test generation and metadata database), not RE's. RE's steps
price themselves from what the launcher already knows, per database,
before the batch starts, and the measured wall time of each run becomes
the next estimate.

The owner asked why large tables would be refused rather than sampled, as
TestGen samples. The answer, from TestGen's own profiling SQL and RE's
design of 2026-10-19: TestGen samples only the text-shape measures
(patterns, lengths, placeholder counts, through a `TABLESAMPLE BERNOULLI`
block) and reads counts, nulls, distinct counts and date bounds over the
whole table, because a sample cannot honestly give a row count or a
minimum date. The seven slice-1 checks use only whole-table measures,
and two of them refuse a sampled profile outright (their selection rules
require `sample_ratio IS NULL`). So on those measures RE does exactly
what TestGen does: whole table, no sample, and the `sample_ratio` column
already in the profile table stays empty.

Revised rule, replacing the cap: **no size cap and no refusal by size.**
Value profile reads whole-table counts for every table; what bounds it
is a per-table time budget (one read-only transaction per table, as
built) and the price line, which shows the bytes per table from the
catalog and says "size unknown · never analysed" where it is. When
slice 2 adds the text-shape measures, they are sampled as TestGen
samples them, with `sample_ratio` recorded on the profile row and shown
on every measure derived from it, and the checks that need whole-table
counts keep reading the whole table. A table whose whole-table read
exceeds its time budget is reported "profile incomplete · exceeded N min
· counts not measured", never a sampled count passed off as a count.

### 2.4 Results flowing back from TestGen

Only through the compare bridge, and only as proposals. A TestGen test
that RE did not propose for the same column becomes a proposal row with
`origin=testgen`, the TestGen test id and the run it came from, never a
verdict; a person accepts it in Curate like any other. A TestGen test RE
also proposed is a match, recorded on the compare report. A proposal RE
made that TestGen did not is a note for the calibration log, not a
demotion. TestGen's test results (pass or fail) are not imported;
RE runs accepted checks itself, so one engine produces every result.

### 2.5 Credentials, identity and TestGen's limits

For RE's own steps: the database's stored credential as today, the
capability gate as today (`requires_capability`), and the run under the
initiating user with the service account only for scheduled, unattended
batches, the owner's identity rule. For the calibration harness:
TestGen keeps its `surveyor` read credential in its own runtime, outside
RE, and the harness is run by a person, so no RE identity question
arises. TestGen's 50-character schema-name limit is TestGen's; the
compare report lists the schemas it could not take, and RE's steps are
unaffected.

### 2.6 Egeria: what publishes, when, using the Data Quality Rule that exists

The owner asked whether Egeria's existing Data Quality Rule governance
definition can be used as it is. Yes, as far as the source shows
(`SPEC-PYEGERIA-DATA-QUALITY-RULE.md`, read 2026-10-19): the type
`DataQualityRule` exists on the running platform with its quality
attributes; the Governance Officer view service creates, updates, finds
and links governance definitions; and pyegeria's generic
`create_governance_definition(body)`, `update_…`, `find_…` and
`attach_governed_by_definition` already call those endpoints. A body
whose properties class is `DataQualityRuleProperties` passes through the
client unchanged, because its validation list check is commented out,
and the server registers that class. So **no new wrapper is required to
start**; RE builds the body itself, as Egeria's own Bitol contract mapper
does (one rule per check, qualified name `<elementQN>::Quality::<id>`,
find by name before create, link with an empty `GovernedBy`).

Two conditions, from RE's own rule that generic governance-definition
bodies are untested for most types:

1. **Never fall back.** Dr.Egeria's processor retries with the base
   `GovernanceDefinitionProperties` when the server rejects a subtype,
   which would create a plain definition and lose every quality
   attribute. RE's publish must refuse that: a committed test that a
   rejection of the subtype class ends the publish with the server's
   sentence and writes no base definition.
2. **A probe before the first write.** Read-only: `find_governance_definitions`
   restricted to type `DataQualityRule` on the running platform, showing
   the type is queryable and what, if anything, the content packs already
   hold of it. Then the first write is the gate itself: one rule created
   from one accepted check, read back by GUID with its type name
   `DataQualityRule` and its quality attributes present, and the
   `GovernedBy` link read from the column's side.

The dedicated wrapper in the spec stays as a pyegeria convenience to add
later, for the owner's word; it is no longer on the critical path.

Order of publishing:

- **Now (slice 1 as built):** nothing, which is why the engine-host
  declarations are held.
- **Next:** results as `QualityAnnotation`s on the column or table
  elements, one per evaluated check, with the dimension, measure,
  threshold and outcome, and `rule_source` naming the rule's GUID once it
  exists; the engine-host declarations are unheld when a run publishes at
  least its results, so `survey-completed` carries a report with
  annotations.
- **With it:** accepted proposals as `DataQualityRule`s linked by
  `GovernedBy` through the generic API under the two conditions above.

## 3. Slices and gates

| # | Slice | Gate, by use |
|---|---|---|
| Q2 | `DatabaseQualitySurvey` definition (three steps, authored, reconciled); the 5-minute per-table budget on value profile with the "profile incomplete" sentence; results published as `QualityAnnotation`s; the two engine-host declarations unheld with a committed test that a run with zero accepted checks completes `survey-invalid` ("no accepted checks"), never an empty completed report | the owner runs it on coco_pharma from the Survey press, accepts three proposals, re-runs, reads three annotations back in Egeria by GUID |
| Q3 | Batch and schedule: the work-list batch run and Automate pointed at the definition, with the per-database price line and the skip-unchanged rule | a work list of coco_pharma and adventureworks run as one batch with two priced rows; a second scheduled run skips the unchanged one and says so |
| Q4 | Inventory and map TestGen's out-of-the-box test types (design task): one row per type, mapped to an RE slice-1 type, an Egeria dimension and annotation RE could port as data, or "not covered" with the reason; plus the compare harness documented (`seed_examples.py`, `compare_re_testgen.py`) with its calibration report and the import of TestGen-only tests as `origin=testgen` proposals | the inventory reviewed by the owner; one calibration report on coco_pharma; one TestGen-only proposal accepted in Curate and run by RE |
| Q5 | `DataQualityRule` publish through the generic governance-definition API (2.6), with the no-fallback test and the read-only probe first | one accepted check read back by GUID as a `DataQualityRule` with its attributes, linked `GovernedBy` to its column |
| Q6 | Slice 2 of the checks: Recency with the inference ported as data | the eighth check proposes on a dated column and nowhere else |

## 4. The owner's answers (2026-10-21) and what is still open

Answered:

- TestGen is not only a calibration harness in the long run: its extra
  checks and custom rules are a backlog item, and the next step is the
  inventory and mapping of its out-of-the-box tests (2.1, Q4).
- Large tables are not refused: whole-table counts as TestGen does,
  sampling only for the measures TestGen samples, with the sample ratio
  recorded (2.3).
- The Data Quality Rule definition that exists in Egeria is used through
  the API that exists; no new wrapper first (2.6).
- The target is the demo machine when ready; the Alpha framing is gone.

Still open, in plain terms:

- [x] The TestGen inventory: done by the design session from the owner's
      clone, `INVENTORY-TESTGEN-TEST-TYPES-MAPPED.md` (89 types).
- [x] The per-table time budget for value profile: **5 minutes per
      table** (owner, 2026-10-21). A table whose whole-table read exceeds
      it is reported "profile incomplete · exceeded 5 min · counts not
      measured" and its checks are not proposed, never a sampled count
      passed off as a count.
