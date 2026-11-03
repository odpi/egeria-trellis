# DESIGN — Quality slice Q5: accepted checks as Egeria `DataQualityRule`s (2026-10-27)

**Status:** part 1 (2026-10-27) was design and a read-only probe; the probe is
`scripts/probe_data_quality_rule_readonly.py` and §2 records its one run. **Part 2 (2026-10-28) is built**: §12 says
what, and §11 records each question's answer. Nothing has yet been written to Egeria: the first write is the gate
(§9).

**Builds on, does not repeat:**
- `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` §7: the property mapping, the `GovernedBy` target order, the
  rule that accepting is local and Publish writes. That table stands except where §4 below corrects it.
- `SPEC-PYEGERIA-DATA-QUALITY-RULE.md`: the pyegeria convenience API, still a later nicety.
- `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` §2.6 and §3: the two conditions (never fall back; probe before the
  first write) and the Q5 gate.
- `DESIGN-PUBLISHING-COLUMN-VERDICTS.md` §0: how a column element is found (walk the committed schema).
- `DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md` §5: the publish table.
- The Q2 code as built: `run_checks.build_quality_annotations` (one `QualityAnnotation` per evaluated check,
  `ruleSource`/`ruleGuid` already in `additionalProperties`, today always `unpublished`/empty) and
  `database_quality_results.rule_guid`.

## 1. Rulings this note works inside

**Decision (project owner, 2026-10-19):** D3, checks are proposed in RE and accepted into Egeria as
`DataQualityRule`s.

**Decision (project owner, 2026-10-20):** the `GovernedBy` target is the column element of a committed schema;
otherwise the person is asked whether to add the object, shown when the analysis that found it ran; a declined
object gets no rule, and never a fallback onto the database Asset.

**Decision (project owner, 2026-10-20):** an annotation is linked to the rule it evaluated by `GovernedBy`.

**Decision (project owner, 2026-10-21):** the Data Quality Rule that exists in Egeria is used through the API
that exists; no new wrapper first.

## 2. The probe (2026-10-28 00:58 UTC, read-only)

Identity: `resourceexplorernpa` (RE's daemon identity from `packages/resource-explorer/.env`), platform
`https://localhost:9443`, view server `qs-view-server`, client pyegeria **6.2.13** (the locked version, loaded
from the uv cache; see risk R1). Calls made, in order, and nothing else: one bearer-token request per client,
then the reads below. No create, update, delete, link or classify call was made.

| # | Call | Answer |
|---|---|---|
| 1 | `get_typedef_by_name("DataQualityRule")` | ACTIVE, version `6.3-SNAPSHOT`, guid `b0546c6b-…594a`, supertype `GovernanceRule`; own attributes `qualityDimension`, `checkType`, `metric`, `severity`, `businessImpact`, `method`, `units`, `schedule`, `scheduler`, `expression`, `qualityEngine`, `comparisonOperator`, `thresholdValues` |
| 2 | `get_typedef_by_name("GovernanceRule")` | supertype `GovernanceControl`, no own attributes |
| 3 | `get_typedef_by_name("GovernanceControl")` | supertype `GovernanceDefinition`; `implementationDescription` |
| 4 | `get_typedef_by_name("GovernanceDefinition")` | supertype `AuthoredReferenceable`; `domainIdentifier`, `summary`, `scope`, `usage`, `importance`, `implications`, `outcomes`, `results` |
| 5 | `get_sub_types("GovernanceRule")` | `DataQualityRule`, `NamingStandardRule` |
| 6 | `get_valid_relationship_types` for `RelationalColumn`, `RelationalTable` | each includes `GovernedBy`, `AssociatedAnnotation` |
| 7 | … for `Database` | `GovernedBy`, `AssociatedAnnotation`, `GovernanceResults` |
| 8 | … for `QualityAnnotation` | `GovernedBy`, `AssociatedAnnotation`, `ReportedAnnotation` |
| 9 | … for `DataQualityRule` | `GovernedBy`, `GovernanceControlLink`, `GovernanceMechanism`, … |
| 10 | `get_typedef_by_name("GovernedBy")` | supertype `LabeledRelationship`; **end 1 `Referenceable` (the governed element), end 2 `GovernanceDefinition`** |
| 11 | `get_valid_metadata_values(<prop>, "DataQualityRule")` | `qualityDimension`: accuracy, completeness, conformity, consistency, coverage, timeliness, uniqueness · `checkType`: text, library, sql, custom · `metric`: nullValues, missingValues, invalidValues, duplicateValues, rowCount · `severity`: info, warning, error · `comparisonOperator`: mustBe, mustNotBe, mustBeGreaterThan, mustBeGreaterOrEqualTo, mustBeLessThan, mustBeLessOrEqualTo, mustBeBetween, mustNotBeBetween |
| 12 | `find_governance_definitions` body `metadataElementTypeName: "DataQualityRule"`, `"*"` | **No elements found** |
| 13 | the same find with no type filter (control) | 12 elements, all `Risk`: the query works, so 12 is a real empty for the type, not a dead filter |
| 14 | `find_governance_definitions("::Quality::", starts_with=False)` | No elements found: no RE- or Bitol-shaped rule name exists to collide with |

Source read alongside (no calls): Egeria at `8de9572c4d` (2026-10-26), `OpenMetadataTypesArchive6_2.java`
`getDataQualityRuleEntity` (matches call 1), `DataQualityRuleProperties.java`, and the Bitol
`DataContractMapper.linkQualityRules` (§3). pyegeria 6.2.13: `governance_officer.py`
`create_governance_definition` (line 429), `update_…` (957), `delete_…` (1066), `attach_governed_by_definition`
(1603), `detach_…` (1696), `find_…` (2207), `get_…_by_guid` (2718); `validate_new_element_request` still has its
property-class check commented out and `NewElementRequestBody.properties` is a plain dict, so a
`DataQualityRuleProperties` body passes through unvalidated. **"DataQualityRule" appears nowhere in pyegeria
6.2.13**, not even the status-field map entry the 10-19 spec cited (it was in an earlier release).

**What the probe settles.** The type, its attributes and its vocabularies are live and match the 10-19 source
reading. `GovernedBy` may join a rule to a column, a table, the database and a `QualityAnnotation`. No
`DataQualityRule` exists on the platform today.

**What it does not settle.** That a create with this body works. `DESIGN-PUBLISHING-COLUMN-VERDICTS.md` §0
says "the live probe of 2026-10-20 proved that the generic create, read-back and GovernedBy link work", but no
script, test or proof row for that probe is in the repo, and no rule from it remains (call 12). Treat it as
unrecorded: the Q5 gate's first write is the first evidence that counts.

## 3. What Egeria's own producer does (the pattern to copy)

`DataContractMapper.linkQualityRules` is the only code that writes this type. Per check it:
1. builds the qualified name `<elementQN>::Quality::<id>`;
2. finds an existing element by that name; if none, creates the rule **anchored to the data contract**
   (`anchorGUID = agreement`, `isOwnAnchor = false`), then calls `addGovernanceDefinitionToElement(element, rule,
   …, new GovernedByProperties())`;
3. if found, `updateGovernanceDefinition` in place, and does not re-link.

RE's equivalent of the agreement is the **database Asset**: the thing the checks were accepted against.

## 4. The mapping

One accepted check (latest verdict `accepted` for its `check_key`) → one `DataQualityRule`.

| Field | Value | Note |
|---|---|---|
| request `class` | `NewElementRequestBody`; properties class `DataQualityRuleProperties`, `typeName` `DataQualityRule` | never `GovernanceDefinitionProperties` (§6.1) |
| `qualifiedName` | `<databaseAssetQN>::Quality::<check_key>`, i.e. `…::Quality::sales.orders.order_id::Unique` | `check_key` is already `schema.table[.column]::Test_Type` (`catalogue.check_key`), so §7's scheme and RE's key coincide; one name per check, stable across threshold edits |
| anchor | **its own** (`isOwnAnchor = true`, no `anchorGUID`): a reusable rule | **Decision (project owner, 2026-10-28)**, §11 row 1. Superseded the draft's asset anchor. What the rule checks is said by `GovernedBy` (below), not by the anchor |
| `displayName` | `"<test_name_short>: <path>"` | |
| `summary`, `description` | YAML `test_description`; `usage_notes` | |
| `scope` | `schema.table[.column]` | |
| `qualityDimension` | `catalogue.egeria_dimension(...)`, **the same word the Q2 annotation carries** | conflict between the record and the code: owner question 2 |
| `checkType`, `metric`, `method`, `severity`, `units` | as §7's table: `method` = test-type code always (the executor's key); `qualityEngine` = `resource-explorer` | `metric` only for the three ODCS-standard checks |
| `comparisonOperator`, `thresholdValues` | `catalogue.MUST_BE[test_operator]`; the accepted threshold, stringified, one value (two only for a between operator) | all values in the live vocabulary (call 11) |
| `expression`, `schedule`, `scheduler` | not set | RE never stores runnable SQL in Egeria |
| `additionalProperties` | `checkKey`, `catalogueRef`, `origin`, `profileSurveyedAt` (the profile run the baseline came from), `baselineCt`/`baselineValue…` as present, `acceptedBy`, `acceptedAt`, `reProofHash` (§6.3), `originalDimension` when the dimension is re-mapped | |
| `contentStatus` | `ACTIVE`; `DEPRECATED` on retire (§5) | |

**Links.**
- `GovernedBy` element → rule, written with `attach_governed_by_definition(element_guid, definition_guid)`.
  The element is the `RelationalColumn` for a column check, the `RelationalTable` for a table check
  (`Row_Ct`, `Row_Ct_Pct`, `Recency`'s table form), found by walking the committed schema. Probe call 10
  corrects `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` §8, which says end 1 is the rule: on 6.3, end 1 is the
  governed element and end 2 the definition. pyegeria's method takes both GUIDs by role, so the publish is
  unaffected, but a read-back that inspects ends must use end 2 for the rule.
- `GovernedBy` `QualityAnnotation` → rule, per Q2's annotations from the run after the rule exists (§5).
  Annotations published before the rule existed are not back-linked: they say `ruleSource: unpublished`, which
  was true when they were measured.
- `GovernedBy` database Asset → rule, as well as the column or table link: the rule-to-asset relationship the
  owner asked for. Probe call 7 is why it is `GovernedBy`: of the relationships 6.3 allows on a `Database`, it is the
  one whose other end is a `GovernanceDefinition` (`GovernanceResults` is for governance-action results, and
  `GovernanceMechanism`/`GovernanceControlLink` join definitions to definitions). Both links are made once, and
  a retire detaches both.
- A check whose column or table has no element still gets no rule (owner, 2026-10-20): the asset link is in
  addition to the element link, never instead of it.

## 5. When it is written, changed and retired

**Written at Publish, never at Accept.** Accept/Reject stays local (`post_verdict` makes no Egeria call). The
database's one Publish press, which already sends the Q2 annotations, does rules **first**, then the run's
annotations, so an annotation measured after the rule's read-back can name the rule and link to it.

Per accepted, retired or rejected `check_key`, Publish compares the verdict with RE's latest proof row (§6.3):

| Verdict now | Proof row says | Action |
|---|---|---|
| accepted | none, or `retired` | find by qualified name → create (own anchor) or reactivate; link column/table and asset once; read back |
| accepted, parameters as published | `published`, same `reProofHash` | nothing; counted as unchanged |
| accepted, parameters edited (threshold, severity) | `published`, different hash | `update_governance_definition` in place, same GUID; read back |
| rejected or retired | `published` | `contentStatus` → `DEPRECATED`, `detach_governed_by_definition` from the column/table; the rule and its annotation links stay as history; read back |
| any | rule GUID no longer resolves | proof row `missing_in_egeria`; next publish re-creates by name (as after an Egeria reset, `get_egeria_reset_at`) |

**Never deleted by RE.** A deleted rule orphans the `GovernedBy` links of every annotation that evaluated it.

**Egeria wins over RE.** The rule can be edited in Egeria (Dr.Egeria, Portal). Before any update, Publish
reads the rule; if its compared fields (`comparisonOperator`, `thresholdValues`, `severity`, `contentStatus`)
differ from RE's own last proof row, someone else changed it. RE does not overwrite: the row reads "edited in
Egeria · <updatedBy> · <updateTime>", and `run_quality_checks` uses Egeria's values from then on (`rule_source
= egeria`), as §6 of the value-profile note already says. Owner question 3.

**The run step.** Once a check has a proof row with a `rule_guid`, `run_quality_checks` reads the rule by GUID,
uses its operator, threshold and severity, and records `rule_source = egeria` and `rule_guid`; the
`QualityAnnotation` then carries both, and the publish adds the annotation → rule `GovernedBy`. A rule that
cannot be read falls back to the verdict, labelled `rule_source = unpublished · egeria unreadable`, never
silently. A rule with `checkType = sql`, or an `expression` RE did not write, is not executed.

## 6. Idempotence and read-back

### 6.1 Never fall back
The create sends `DataQualityRuleProperties` and nothing else. If the server rejects the class
(`OMAG-COMMON-400-029` or any 4xx), the check's publish ends with the server's sentence and no element is
written. A committed test injects that rejection and asserts no second create call is made. This is the
Dr.Egeria processor's fallback (`md_processing/v2/governance.py`) that RE must not copy.

### 6.2 Find, create, link once
- **Name first.** The exact unique-name read (`egeria_lookup.guid_by_qualified_name`, typed `DataQualityRule`),
  not the governance-definition by-name search, which matches loosely. One hit: adopt it. Two holders, or a holder
  of another type: stop, write nothing to Egeria, record `duplicate` or `name_taken`: someone else's state to
  resolve.
- **Create**, then **read back by GUID** before linking: `typeName == DataQualityRule`, `method`,
  `comparisonOperator`, `thresholdValues` (order preserved) equal what was sent. A mismatch or a base
  `GovernanceDefinition` type ends that check `unverified` with no link. Its proof row is recorded with that
  outcome, as evidence, and is never read as "published": the next Publish adopts the rule by name.
- **Link once**: through `egeria_link_once`, reading the rule's `GovernedBy` links first (the rule is end 2 and
  has few links; a column may have many), so a re-run or a crash between create and link converges on one edge.
- **Crash between steps** is safe by construction: the next Publish finds the rule by name and continues.

### 6.3 Proof rows, the RE pattern
A new append-only table, in the shape of `catalogue_commit_proofs` and the value-profile proof rows:
`database_quality_rule_proofs(database_slug, check_key, rule_guid, qualified_name, governed_guid, governed_kind
(column|table), outcome (created|updated|unchanged|reactivated|retired|unverified|missing_in_egeria|
edited_in_egeria|not_in_egeria), re_proof_hash, detail_json, recorded_by, read_at)`. A row is written **only
from a read-back**; `read_at` is when Egeria was read, not when Publish was pressed. `re_proof_hash` is sha256 of
the canonical compared fields RE sent, so "unchanged" is decided from RE's own row, not a re-read diff. Rows from
before the latest Egeria reset are ignored (`get_egeria_reset_at`), as for profile publish. The DDL is in
`registry.py` beside `database_quality_verdicts` (`CREATE TABLE IF NOT EXISTS`, additive, no foreign keys); it is
applied at the next RE start against :5442, which needs the owner's word. It has not been applied.

## 7. Zones and visibility

The rule is its own anchor (owner, 2026-10-28), so it does **not** inherit the database Asset's zones and it
**survives the asset's deletion**. That is what a reusable rule should do, and two consequences follow:

- **Visibility.** RE writes no `ZoneMembership` on a rule (owner, 2026-10-28, R6): a reusable governance
  definition is visible to everyone who can see governance definitions. `EXPLORER_PUBLISH_ZONES` does not apply
  to rules.
- **Lifecycle.** Deleting or archiving the database Asset leaves its rules, with their `GovernedBy` links to that
  asset gone. A rule left governing nothing is a candidate for retire; RE does not delete it.

Writes run as the signed-in user through `egeria_identity.apply_identity` (the probe used the daemon identity
only to read); unattended scheduled publishes use the service account, the owner's identity rule.

## 8. What a person sees

**In Curate, Quality checks list** (Structure tab), per accepted check, one state line:
- `local · not published` (accepted, no proof row);
- `in Egeria · DataQualityRule · read back <time>`;
- `in Egeria · changed here · publish to update`;
- `edited in Egeria · <who> · <when> · Egeria's threshold is used`;
- `not added to Egeria · <date>` (the person declined to commit the schema);
- `retired in Egeria · <date>`;
- `unverified · <server sentence>`.

**In the Publish band** (Curate's first tab): accepted checks are `proposal`-kind, so per
`DESIGN-DECLARED-EXTENSIBILITY…` §5 they are **not rows of the publish table**; they are a fact path, like
data-class verdicts. The band's "next publish" line names them, e.g. "quality rules: 3 new, 1 changed,
1 retired; 2 not in Egeria (schema not committed)". The page's one press sends them with everything else, and the
acknowledgement and persistent cue cover them. The table's `QualityAnnotation` rows (Q2) show "writes as
QualityAnnotation · governed by <rule display name>" once the rule exists.

**In Egeria** (Portal, Dr.Egeria, Egeria Explorer): on the column, a governance definition of type
DataQualityRule named "Unique Values: sales.orders.order_id", its threshold and severity; from the rule, the
column it governs and the annotations that evaluated it, newest first.

## 9. The gate, by use

On coco_pharma, with `sales` committed in Contents: the owner accepts three proposals (one column check, one
`Row_Ct_Pct` table check, one on a column of an **uncommitted** schema), presses Publish once. Then:
1. two rules read back by GUID as `DataQualityRule` with `method`, operator and threshold as accepted, each its
   own anchor, `GovernedBy` read from the column's (or table's) side and from the database Asset's; the third
   check shows "not added to Egeria" and no rule exists for it;
2. Publish again: zero creates, two `unchanged` proof rows;
3. edit one threshold in RE, Publish: same GUID, updated; reject the other, Publish: `DEPRECATED`, unlinked from
   its table, still present;
4. run the checks: the remaining check's result says `rule_source egeria` with the GUID, and its
   `QualityAnnotation` reads back with `GovernedBy` to the rule;
5. the no-fallback test is in the committed suite and passes.

## 10. Risks

- **R1. The shared venv is not on the locked pyegeria.** `trellis/.venv` reports pyegeria **6.2.3** while
  `uv.lock` and RE's `pyproject.toml` say 6.2.13. The probe ran 6.2.13 from the uv cache. Whoever builds Q5 (and
  whoever runs RE from that checkout) should confirm the venv first; not fixed here (a sync in a shared checkout
  is someone's call, see the repo's `uv sync` rule).
- **R2. The create is unproven.** §2: the 10-20 probe left no evidence. The gate's step 1 is the proof; the
  build should land behind the no-fallback test and run one disposable create/read-back/delete before the gate
  (owner word needed for that write, and a peer round per `coordinate-shared-writes`).
- **R3. `get_governance_definitions_by_name` and type filtering.** A by-name lookup across all governance
  definition types could match a non-rule with the same qualified name; the filter to `DataQualityRule` is
  RE's job, and §6.2's "two hits" rule covers the rest.
- **R4. (Closed 2026-10-28.)** The anchor question no longer arises: rules are their own anchor, the
  Governance Officer create's default.
- **R6. (Closed 2026-10-28.)** Zones on a reusable rule: none.

  **Decision (project owner, 2026-10-28):** no zones on published rules. They are reusable governance
  definitions, visible to everyone who can see governance definitions. This is the behaviour as built; RE writes no
  `ZoneMembership` on a rule, whatever `EXPLORER_PUBLISH_ZONES` says.
- **R5. Annotation → rule `GovernedBy` volume.** One edge per check per run. At the Q3 batch scale this grows
  linearly with runs; the profile-publish re-link pattern may be wanted for annotations of unchanged results.

## 11. Owner questions, answered (2026-10-28)

The owner: "I answered all the questions previously - we should be able to move forward?" Each question's
recorded answer, or the recommendation taken where none was recorded. The four that were assumed were then
answered by the owner on 2026-10-28, in the coordinator session; one of them, the anchor, changed.

**Decision (project owner, 2026-10-28):** "There are different use cases - it might be a reusable rule in which
case it is its own anchor or it may be tied to a data contract in which case it is anchored there...and perhaps
others...as a follow-on, I wonder if we need to pull in data-contracts that egeria knows about (or even create new
ones) and test against those?"

**Decision (project owner, 2026-10-28):** a reject after publish sets DEPRECATED and unlinks, never deletes; no
back-link to annotations published before their rule; one press publishes every pending accepted check.

| # | Question | Answer | Source |
|---|---|---|---|
| 1 | Anchor the rule to the database Asset? | **No: its own anchor.** The rules RE publishes are reusable rules; each is related to the database Asset and to its column or table by `GovernedBy`. A rule tied to a data contract would be anchored to the contract: later (§13) | **owner, 2026-10-28** (quoted above). Changed from the assumed asset anchor |
| 2 | Validity's dimension word | **`accuracy`**, with `originalDimension: Validity` | **recorded**: owner, 2026-10-20, "conformity and validity are not equivalent - accuracy is closer - we could use accuracy and additionalProperties until we add validity" (`DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` §11 item 9; the 10-20 coordinator handoff transcript). The 10-21 inventory and the Q2 code said `conformity` without the ruling; the build corrects the code, so Q2's annotations now say `accuracy` too |
| 3 | An edit in Egeria wins? | **Yes**: RE does not overwrite, the run uses Egeria's values and says "rule edited in Egeria since acceptance" | **recorded in the design**: `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` §6 ("The input is the accepted checks, and Egeria wins"), the 2026-10-19 design the owner answered against; no separate owner quote found |
| 4 | Reject after publish → DEPRECATED and unlinked, never deleted? | **Yes** | **owner, 2026-10-28**: confirmed as built |
| 5 | Back-link annotations published before the rule existed? | **No**: only a run that read its rule from Egeria links its annotation | **owner, 2026-10-28**: confirmed as built |
| 6 | Rules in Publish automatically, or opt-in per press? | **Automatically**: one press publishes every pending accepted check (`POST /api/quality-checks/{slug}/publish-rules`) | **owner, 2026-10-28**: confirmed as built |

Also recorded and applied: `contentStatus` ACTIVE for an accepted check (owner, 2026-10-19, value-profile note §11
item 5); `GovernedBy` from annotation to rule (owner, 2026-10-20, item 6).

## 12. Built (part 2, 2026-10-28)

- `surveyors/database/quality/egeria_rules.py`: the property mapping (`rule_properties`), the plan
  (`plan_rules`, pure), Publish (`publish_rules`, `publish_database_rules`) behind a `RuleGateway` seam, the
  pyegeria gateway (`PyegeriaRuleGateway`: the generic Governance Officer calls, `egeria_lookup` for names,
  `egeria_link_once` for `GovernedBy` read from the rule at end 2; the create is its own anchor and links the
  column or table and the database Asset), the run step's reader (`rules_for_run`,
  `read_rules_for_run`) and the Curate state lines (`rule_states`).
- `registry.py`: `database_quality_rule_proofs` (DDL in code, not applied), `record_quality_rule_proofs`,
  `latest_quality_rule_proofs` (reset cutoff), and `remove_database` deletes its rows.
- `run_checks.py`: `apply_rule`. A published rule's severity and threshold override the verdict's; a rule gone
  from Egeria, deprecated there, changed to an operator the vendored SQL does not test, or changed to SQL or an
  expression is not run, with the sentence; Egeria unreadable runs from the verdict as `local_cache`. Every
  annotation carries `originalDimension` where the word differs, and `ruleNote`.
- `survey_definition_adapter._run_run_quality_checks` reads the rules before the run.
- `egeria_database_surveyor._link_quality_checks_to_rules`: `GovernedBy` from each annotation of a check run
  under a rule read from Egeria, linked once, counted in `element_links.rule_links`.
- Routes: `GET /api/quality-checks/{slug}/rules` (state lines) and `POST …/publish-rules` (the press, as the
  signed-in person, with an activity entry).
- `catalogue.py`: Validity → `accuracy` (§11 row 2), `original_dimension`, `threshold_key`.
- Tests: `tests/test_quality_q5_data_quality_rule.py`, including the no-fallback test.

**Not built here:** the Curate UI (the Structure tab's state line and the band's "next publish" line read the two
routes); folding `publish-rules` into the page's one Publish press, which waits on row 6 and on the publish table
of `DESIGN-DECLARED-EXTENSIBILITY…` §5 landing. No live write has been made: the gate's first write is the first
evidence that the create works (R2).

## 13. Open: rules anchored to data contracts (owner's follow-on, 2026-10-28)

Designed in `DESIGN-QUALITY-Q5-ANCHORING-AND-DATA-CONTRACTS.md` (2026-10-29), which supersedes this section. The owner's follow-on (quoted in §11): rules tied to a data contract are
anchored to the contract, and RE might **pull in the data contracts Egeria already knows about, or create new
ones, and test against those**. That would make a contract's quality rules a source of accepted checks beside RE's
own proposals. What it needs first:
- a read of the data contracts the platform holds (Egeria's Bitol/ODCS mapper creates them; the rules it makes are
  `<elementQN>::Quality::<id>` anchored to the agreement, §3), and how their elements map to RE's columns;
- which of a contract's rules RE can run: only those whose `method` is a vendored test type, never `checkType` sql
  or an `expression` RE did not write (the existing rule);
- whether RE ever creates a contract, or only reads them.

