# DESIGN — Where a published `DataQualityRule` is anchored, and how data contracts come into it (2026-10-29)

**Status:** design only. No code, and nothing written to Egeria or :5442. Three read-only lookups against the
platform were made for this note (§2). The owner asked for it on 2026-10-29: "start the Q5 anchoring design note".

**Builds on, does not repeat:**
- `DESIGN-QUALITY-Q5-DATA-QUALITY-RULE.md`: the rule as built (#889 design, #892 build). Its own anchor,
  `GovernedBy` from the column or table and from the database Asset, the property mapping, the proof rows, the
  publish and retire table, "Egeria wins". §13 there is the stub this note replaces.
- `SPEC-PYEGERIA-DATA-QUALITY-RULE.md`: the ODCS vocabularies and Egeria's Bitol mapper as the reference producer.
- `DESIGN-QUALITY-AT-SCALE-AND-TESTGEN.md` §2.6 and §3: never fall back; probe before the first write.
- `INTEGRATION-TESTGEN-RE-EGERIA.md`: TestGen as an optional engine. Its rule still holds here: RE runs only
  vendored test types, never SQL from elsewhere.
- `DESIGN-QUALITY-STEPS-4-5-DUPE-ROWS-AND-DRIFT.md`, on `origin/claude/re-quality-4-5-746d01` and not merged yet,
  item 6: `Schema_Conformance` is vendored but inert, because RE holds no declared schema. §6.3 below is that design.

## 1. The question

**Decision (project owner, 2026-10-28):** "There are different use cases - it might be a reusable rule in which
case it is its own anchor or it may be tied to a data contract in which case it is anchored there...and perhaps
others...as a follow-on, I wonder if we need to pull in data-contracts that egeria knows about (or even create new
ones) and test against those?"

There are two questions here. The first is where a rule is anchored. The second is where the rule comes from:
RE's own proposals, or a contract. This note's main argument is that **the second question settles the first**.
A rule's anchor follows from who authored it, so RE never has to choose an anchor on its own.

## 2. Where things stand (read-only, 2026-10-29 01:37 UTC)

The identity was `resourceexplorernpa` (RE's `.env`), on platform `https://localhost:9443`, view server
`qs-view-server`, with pyegeria 6.2.3 (the shared venv; see R1 in the Q5 note). Only finds and one GET were made.

| Lookup | Answer |
|---|---|
| `find_governance_definitions`, type `DataQualityRule` | **No elements found.** The Q5 gate has not written yet, so no own-anchored rule exists anywhere |
| `find_collections("*", type Agreement)` | 79 elements: **3 `Agreement`** and 76 `DigitalSubscription` (a subtype, anchored to its `DigitalProduct`). A find by type returns subtypes too, so RE must filter on `typeName` |
| the 3 Agreements | `Coco Pharmaceuticals::Agreement::Marketing-Subscription---C360::1.0`; `DataContract::9b13c331-…::1.0` (the Bitol mapper's name shape); `CocoPharma::Agreement::AuthoritativeSourcesDataSharingAgreement` |
| `ProductManager.generate_data_contract(guid)` for each | each returns an ODCS **v3.2.0** `DataContract` document (`id`, `name`, `version`, `status: active`, `description`), with **no `schema` and no `quality` sections** |
| `find_data_structures` / `find_data_fields`, `DataContract::` prefix | none. The one Bitol contract has no schema elements |

**What this settles.** No rule needs migrating, because none has been written (§5). The platform holds
contracts, and Egeria can give each one back as an ODCS document. That is the read path RE needs (§6.1). Today
no contract declares a schema or a quality check, so until someone imports a contract that has them, RE has
nothing to test against. Slices C1 to C3 need a fixture ODCS document in tests, plus one imported contract for
their gate (question 7).

### 2.1 What Egeria's model offers (source at `8de9572c4d`, 2026-10-26)

- **A data contract** is an `Agreement` (a `Collection` subtype) classified `DataSharingAgreement`
  (`OpenMetadataType.java` 6602, 6613). The Bitol `DataContractMapper.catalogueDataContract` names it
  `DataContract::<id>::<version>`, so each version is a separate element. A retired contract is **deleted**
  (`deleteCollection`).
- **Its schema** consists of `DataStructure` elements (one per ODCS schema object), joined to the Agreement by
  `AgreementItem`. Each structure holds `DataField` elements, joined by `MemberDataField` / `NestedDataField`. All
  of them are anchored to the Agreement (`isOwnAnchor = false`). The ODCS physical name is kept as `namespacePath`
  and in `namePatterns`.
- **Its quality checks** are `DataQualityRule` elements named `<structureOrFieldQN>::Quality::<id>` and
  **anchored to the Agreement** (`linkQualityRules`, mapper lines 951–1010). Each has `GovernedBy` from the
  `DataStructure` or `DataField` it checks. There is no `GovernedBy` from the Agreement itself. The SLO and the
  security controls are linked that way, but quality rules are not.
- **Servers** are `Endpoint` elements under the Agreement (`ResourceList`), with `database`, `schema`, `host`,
  `port`, `type` in `additionalProperties`. **Nothing in the contract points at a physical Asset.** A contract
  names its data by server address and physical names, and matching that to RE's `Database` and
  `RelationalColumn` elements is up to the consumer.
- **The bridge exists as a type.** `SchemaAttributeDefinition` (`OpenMetadataTypesArchive5_3`, 0581) joins a
  `DataField` (end 1) to a `SchemaAttribute` such as a `RelationalColumn` (end 2). `AgreementItem` accepts any
  `Referenceable` at end 2, so a contract can also name the database Asset directly.
- **Product Manager OMVS** (`/product-manager`) round-trips ODCS:
  `POST /data-contracts/import-document[-string]` (catalogues a document through `DataContractMapper`) and
  `GET /agreements/{guid}/data-contract-document` (generates the document through `DataContractGenerator`).
  pyegeria has `import_data_contract[_string]` and `generate_data_contract`.
- **Re-anchoring is possible but untested.** Creating a relationship with `makeAnchor = true` adds an `Anchors`
  classification to end 2 pointing at end 1 (`MetadataElementHandler` line 3855). The source does not show
  whether this replaces an existing `Anchors` classification on an own-anchored element.

## 3. The anchor cases

| Case | Anchor | Relationships | Who writes the rule | Deleted with | Zones |
|---|---|---|---|---|---|
| **A. Reusable rule** (built) | itself (`isOwnAnchor = true`) | `GovernedBy` column/table → rule; `GovernedBy` database Asset → rule; `GovernedBy` `QualityAnnotation` → rule | RE's Publish, from an accepted proposal | nothing; retired by `DEPRECATED`, never deleted | none (owner, 2026-10-28) |
| **B. Contract rule** | the `Agreement` (`anchorGUID`, `isOwnAnchor = false`) | `GovernedBy` `DataField`/`DataStructure` → rule (by the mapper); `GovernedBy` `QualityAnnotation` → rule (by RE, once it runs the rule) | **Egeria's Bitol mapper**, from an ODCS document. Never RE directly | the contract version (a retired contract is deleted, and its anchored rules with it) | the Agreement's |
| C. Asset rule | the database Asset | as A, without the asset `GovernedBy` | none | the Asset | the Asset's |
| D. Policy-owned rule | a `GovernancePolicy` | `GovernanceMechanism` policy → rule | none | the policy | the policy's |
| E. Project/spec rule | a `DataSpec` | `DataDescription`/`DataStructure` membership | none | the spec | the spec's |

**Case C, the database Asset, is not reopened.** It was the Q5 draft, and the owner overruled it on 2026-10-28.
A rule anchored to an asset dies with the asset, and RE's assets come and go with surveys and resets.
`GovernedBy` from the asset already records which asset a rule checks, and the anchor adds nothing to that.

**Case D, a governance policy, is a link and not an anchor.** One rule can implement several policies, and an
anchor allows only one owner. A rule that implements a policy gets `GovernanceMechanism` (policy → control) and
stays its own anchor or the contract's. RE has no policy picker today, so this is a later addition to case A or
B, not a separate case (question 5).

**Case E, a `DataSpec`, is how a project states data requirements** (100+ on the platform, mostly anchored to
`Project` or `DigitalProduct`). Its fields could carry rules the way a contract's do, but no Egeria producer
writes rules under a DataSpec today, and the owner named contracts, not specs. It is listed so the decision is
recorded, and set aside (question 5).

**Data products count as case B.** A `DigitalProduct`'s terms are an Agreement (its 76 subscriptions are
`DigitalSubscription`, a subtype), so "a rule tied to a product" means a rule in the product's contract.

## 4. How RE decides, and what Curate shows

### 4.1 The rule: origin decides anchor

| Where the check came from | Anchor | RE writes the rule? |
|---|---|---|
| RE proposed it and a person accepted it (Q2–Q6 proposals, TestGen import) | A, own | yes, as built |
| a contract declares it (ODCS `quality` on a schema object or property) | B, the Agreement | **no**. It already exists in Egeria, written by the mapper. RE reads and runs it, and links the annotation |
| a person wants an RE check to become part of a contract | B, via a new contract version | not directly: RE drafts an ODCS document, and Egeria's mapper writes the rule on import (§6.4) |

So RE writes only own-anchored rules, and there is no anchor chooser in the UI. A rule with a contract anchor
exists because a contract declared it, which is what makes it the contract's. This also keeps one producer for
everything under an Agreement: the mapper's qualified-name scheme, its versioning, and its retire-by-delete stay
consistent, and RE never has to reconcile its own writes with a later re-import of the same contract.

**When a contract check and an RE rule check the same thing.** They are two elements with two names
(`<databaseAssetQN>::Quality::<check_key>` and `<fieldQN>::Quality::<id>`). RE does not merge them, re-anchor
one, or retire its own because a contract appeared. Curate shows the overlap (below), and the owner decides
whether that should ever retire RE's rule (question 4).

*See also* [DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md](DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md) §8 and §10: the one re-anchor RE has made (D1-b) adds a content pack's schemas to RE's database element and moves no rule.

### 4.2 In Curate

**A "Contracts" line on the database** (Structure tab, above Quality checks):
- `no contract applies` (the default);
- `Authoritative Sources Data Sharing Agreement · v1.0 · 4 tables matched · 2 not found in the survey`;
- `contract read <time> · 7 quality checks, 5 runnable here`.

A contract applies to a database only when **a person says so**. RE suggests candidates where a contract's
`servers[]` entry matches the database's host, port and database name, or where its schema objects' physical
names match committed tables. It never binds a contract on a name match alone. This is the same rule as
committing a schema: shown, then confirmed. The binding is local (a registry row), like a verdict.

**The Quality checks list gains a Source column**: `RE proposal` or `contract · <name> v<version>`.
- Contract rows have **no Accept/Reject**, because the contract has already accepted them. A person can
  **exclude** a contract check from RE's runs (local, with a reason), but cannot change its threshold: edits go
  through a new contract version. This is "Egeria wins" in its strongest form.
- Each contract row has one state line: `runnable · as <test type>`, `not run · <sentence>` (§6.2), or
  `not run · contract rule not found in Egeria`.
- Where an RE rule and a contract check match on (column, test type, operator), both rows show
  `also in contract <name>` or `also an RE rule` and the threshold each one uses. Neither row changes.

**The Publish band** says nothing new for contract rules, because RE publishes none. The run's annotations for
contract checks are `QualityAnnotation` rows like any other, and read "governed by <contract rule display name>".

## 5. Rules already published own-anchored

**There are none.** The Q5 gate has not made its first write, and the platform holds no `DataQualityRule` (§2).
The question therefore becomes a policy for after the gate.

**Recommendation: leave own-anchored rules as they are, permanently.** Do not re-anchor them.
- Re-anchoring changes delete behavior without any visible sign. A rule moved under a contract would be deleted
  when the contract version retires, taking its annotations' `GovernedBy` targets with it. RE never deletes a rule
  (Q5 §5) precisely to avoid that.
- The mechanism is untested (§2.1, last point). Using it would mean a probe and an owner-approved write for no
  gain over case A plus `GovernedBy`.
- A contract that adopts an RE check gets its own rule through the mapper (§6.4). Both elements remain, each with
  its own history.
- D1 follows the same rule at database level ([DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md](DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md) §8 and §10): RE adds relationships to a content pack's elements and
  re-anchors nothing of its own.

If the owner wants a contract to replace an RE rule, the existing retire path is the way to do it: `DEPRECATED`
plus unlink, never delete (question 4).

## 6. Data contracts

### 6.1 Reading the contracts Egeria knows about

1. `find_collections("*", metadata_element_type_name="Agreement")`, keeping only `typeName == "Agreement"` (the
   find returns `DigitalSubscription` too). Paged per the paging contract: only an empty page means the end.
2. For each Agreement, `ProductManager.generate_data_contract(guid)`. RE parses **ODCS**, not the element graph.
   The generator already walks `AgreementItem`, `MemberDataField`, `GovernedBy` and the endpoints, and ODCS is
   the format TestGen's `Schema_Conformance` reads (§6.3), so one parser serves both purposes. To get the rule
   GUIDs (the targets for annotation links), use the exact-name lookup on `<fieldQN>::Quality::<id>`, which the
   ODCS `id` and the structure/field names reconstruct. If that is not exact enough, use `GovernedBy` from the
   `DataField`.
3. Store a **snapshot** of the document locally (`database_contract_snapshots`: slug, agreement GUID, qualified
   name, ODCS id, version, status, document JSON, `read_at`), so that runs and Curate do not call Egeria each time.
   This follows the pattern of the stored surveys. It is re-read on demand and whenever a run starts, and rows
   from before the latest Egeria reset are ignored.
4. **Versions.** Each version is its own Agreement. RE uses the newest `active` version that a person has bound,
   and records the version on every annotation (`additionalProperties.contractId`, `contractVersion`). A newer
   version appearing is shown ("v1.1 available") and is not adopted silently (question 3).

**Matching to RE's elements.** A schema object's `physicalName` (or `name`) is a table in the bound database's
committed schema. A property's `physicalName` (or `name`) is a column in that table. A property or object with
no match is reported (`declared, not found in the survey`); `Schema_Conformance` reports the same fact as a
failure (§6.3). Writing `SchemaAttributeDefinition` (`DataField` → `RelationalColumn`) would record the match in
Egeria for everyone. It is a write to a link whose one end belongs to the contract, so it is left for the owner
(question 6), and RE works from its local match until then.

### 6.2 Testing against a contract: ODCS clauses to RE's ported checks

RE's rule stands: a contract check runs only if a vendored test type means exactly what the clause says.
Otherwise it is `not run`, with a sentence, and never approximated. The ported set is `PORTED_TEST_TYPES` in
`catalogue.py`, plus `Dupe_Rows` and the four drift types once steps 4–5 merge.

| ODCS clause (`type` · `metric`/`rule`) | Runs as | Notes |
|---|---|---|
| `library` · `nullValues`, `mustBe 0`, count | **Required** | exact: `catalogue.ODCS_METRIC` already maps Required → `nullValues` |
| `library` · `nullValues`, `unit: percent`, `mustBeLessThan p` | **Missing_Pct** | only if Missing_Pct's measure is the null fraction alone; to confirm against its vendored predicate at build time, otherwise `not run` |
| `library` · `nullValues`, count > 0 | not run | no ported type takes a null-count threshold other than 0 |
| `library` · `duplicateValues`, `mustBe 0` | **Unique** | exact (`ODCS_METRIC`) |
| `library` · `duplicateValues`, other thresholds or percent | not run | Unique's threshold is 0 upstream |
| `library` · `missingValues` with `arguments.missingValues` | **Required_No_Placeholder** only when the list equals the vendored placeholder list | any other list is `not run · the contract's missing-value list differs from the ported check's` |
| `library` · `invalidValues` (`validValues`, `pattern`) | not run | LOV_Match / Pattern_Match are not ported. A future port step, not a reason to approximate |
| `library` · `rowCount`, `mustBeGreaterThan n` (and the others) | not run today; **Row_Ct** if ported | Row_Ct_Pct and Match_Row_Ct are relative to a baseline, not to a fixed count. Row_Ct is a small port (slice C4) |
| `custom` · `engine: resource-explorer` (or `testgen`), `implementation` naming a test type | **that test type**, with the clause's operator and threshold | how an RE check comes back from a contract RE drafted (§6.4). Run only if the type is ported |
| `sql` | never | the standing rule: RE does not run SQL it did not write (Q5 §5) |
| `text` | never | documentation; shown in Curate as text |
| schema: property `name`, `physicalType`, `logicalType`, per object | **Schema_Conformance** | §6.3 |
| schema: property `required: true`, `unique: true`, `primaryKey` | not run as separate checks | they are constraints, not quality clauses. A contract that wants them tested says so in `quality`. Question 8 |

**Results.** A run of a contract check produces a `QualityAnnotation` like any other, with `ruleSource: contract`,
`ruleGuid` the contract rule's GUID, `contractId`, and `contractVersion`. Publish adds `GovernedBy` annotation →
contract rule through the existing link-once path. That is RE's only write involving a contract, and it does not
change the contract. The threshold used is always the contract's, read from the snapshot. RE has no local
threshold for a contract check.

### 6.3 `Schema_Conformance` from a contract's schema

TestGen's vendored statement (`test_types/Schema_Conformance.yaml` on the steps 4–5 branch) reads the declared
schema as ODCS JSON, `prop ->> 'name'`, `'physicalType'`, `'logicalType'`, and reports three kinds of difference
against the live column catalog: `Missing column`, `Unexpected column`, and `Type mismatch` (physical type when
declared, else logical). That is the ODCS document of §6.1, so the inert check becomes runnable as follows:

- **Declared side:** the bound contract's schema object for the table, from the snapshot.
- **Live side:** the newest stored survey's columns and `data_type`, the same source the drift checks use (steps
  4–5). This means no new connection, and the check stays `METADATA`.
- **Measure:** the count of differences. Pass at 0. The result's sentence lists each difference in TestGen's words.
- **Proposal:** one per bound table whose contract declares a schema. Never proposed without a binding (it stays
  `manual_only` until there is one).
- **Logical types:** ODCS `logicalType` (string, integer, number, date, …) compares with RE's vendored
  `general_type` CASE, as Match_Column_Type already does. The exact word map is a build detail and is fixed in a
  test.
- **Annotation links:** a schema clause is not a `DataQualityRule`, so there is no rule for `GovernedBy`. The
  annotation carries `contractId`/`contractVersion` and the structure's qualified name. Linking it to the
  `DataStructure` element needs a relationship whose ends allow that. Probe call 8 shows `AssociatedAnnotation` on
  `QualityAnnotation`, but not its other end, so this is a probe item for C3, not a choice made here.

Egeria has its own notion here: `DataStructureDefinition` joins a `DataStructure` to a `CertificationType`, and
elements "certified" against it conform. A passing `Schema_Conformance` could one day become that certification.
That is out of scope and noted only so a later reader does not invent a parallel mechanism.

### 6.4 Should RE ever create a contract?

**Recommendation: RE drafts, people decide, Egeria catalogues.** RE never writes `Agreement`, `DataStructure`,
`DataField` or contract-anchored rules itself.

- **Draft (slice C5):** from a database with a committed schema and accepted checks, RE produces an ODCS v3
  document. It has a `servers` entry from the database's connection (address only, never credentials), `schema`
  objects and properties from the committed schema (`physicalType` from the survey, `logicalType` from
  `general_type`), and `quality` clauses from the accepted checks: `library` for the three ODCS-standard metrics
  and `custom` with `engine: resource-explorer` and `implementation: <test type>` for the rest (§6.2 reads those
  back). The person downloads it, edits it, and owns it. Status is `draft`.
- **Import (slice C6, optional):** a button that sends the reviewed document to Product Manager's
  `import-document` as the signed-in person. The mapper then writes the Agreement, schema and rules in Egeria's
  own shape. It is an Egeria write and gets its own gate and owner word.

This answers "or even create new ones" without RE taking on a second implementation of the contract model, and
it keeps the draft in the format other tools (Bitol, TestGen's contract import) can read. After import, the
RE-own and contract twins of one check coexist (§4.1); question 4 decides whether that is acceptable.

## 7. Slices

Sizes: **S** is up to a day of building, including tests; **M** is one to three days. Each slice is gated by use,
as before.

| Slice | What | Size | Writes | Depends on |
|---|---|---|---|---|
| **C0** | this note | done | none | — |
| **C1** | read contracts: list Agreements, `generate_data_contract`, snapshot table (DDL in code, applied at next RE start with the owner's word), suggest and confirm a binding per database, Curate's Contracts line and contract rows (read-only, with runnable/not sentences) | M | local only | — |
| **C2** | run contract checks: the §6.2 map, operator and threshold from the snapshot, annotations with `ruleSource: contract`, `GovernedBy` annotation → contract rule at Publish (existing link-once path) | M | one link type, existing path | C1; the Q5 gate (proves the annotation → rule link) |
| **C3** | `Schema_Conformance` from the bound contract's schema over stored surveys; probe the annotation → `DataStructure` link | S | none new | C1; steps 4–5 merged |
| **C4** | port `Row_Ct` (fixed-count `rowCount`) | S | none | — (optional, by request) |
| **C5** | draft an ODCS document from a database (download only) | M | none | C1's parser (the round trip is tested against it) |
| **C6** | import a reviewed draft through Product Manager as the signed-in person | S | **Agreement and its parts, via Egeria's mapper** | C5; owner word and a peer round per `coordinate-shared-writes` |
| **C7** | write `SchemaAttributeDefinition` `DataField` → `RelationalColumn` for confirmed matches | S | one link type on contract elements | C1; question 6 |

The recommended order is C1 → C3 → C2, then C5 and C6 if the owner wants them. C3 is first after C1 because it
is the only one with something to test against without a contract that has quality clauses. A schema can be
imported on its own, and steps 4–5 have already built the comparison base.

## 8. Risks

- **R1. Nothing to test against yet.** All three contracts have no schema and no quality clauses (§2). C1 and C2
  are tested on fixture ODCS documents. Their gates need one contract imported with both sections (question 7).
- **R2. pyegeria version.** `generate_data_contract` and `import_data_contract` were confirmed in the shared venv's
  6.2.3, and are present in most cached builds. Confirm them in the locked 6.2.13 before C1 (the Q5 note's R1).
- **R3. Contract-to-database matching is heuristic.** ODCS has no Asset GUID. A wrong binding would run another
  table's contract against this one, which is why a person always confirms (§4.2).
- **R4. The generator is the parser's input.** If `DataContractGenerator` omits a field the mapper stored (for
  example `arguments.*` in `additionalProperties`), RE sees less than the contract said. C1's tests should
  round-trip a fixture through import and generate once the gate has a contract (§6.1 step 2).
- **R5. Version churn.** A contract version is deleted on retire, and its anchored rules with it. Annotations
  linked to those rules lose their `GovernedBy` targets. That is Egeria's lifecycle, not RE's, but Curate should
  say `contract version retired` rather than `rule not found`.

## 9. Questions for the owner

**Decision (project owner, 2026-10-29):** all nine recommendations below are accepted as written. Question 7's
sample-contract import is approved as the plan; the import itself is an Egeria write made in its slice, after a
peer round, not now.

Each question has a recommendation, and the build can proceed on the recommendations unless the owner says
otherwise.

1. **Is origin enough to decide the anchor?** RE writes only own-anchored rules, a contract's rules are always
   the mapper's, and there is no anchor chooser (§4.1). *Recommend yes.*
2. **Should already-published own-anchored rules ever be re-anchored?** None exist yet (§2). *Recommend never:
   own-anchored rules stay own-anchored. A contract's version of a check is its own element (§5).* D1's database-level
   link (D1-b, [DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md](DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md) §8 and §10) is not a rule re-anchor and does not change this.
3. **Contract versions.** Should RE follow a newer `active` version automatically, or show "v1.1 available" and
   wait for a person? *Recommend wait for a person, as with every other binding.*
4. **Overlap.** When a bound contract and an RE rule check the same column the same way, should RE retire its own
   rule (`DEPRECATED`, unlinked), or keep both? *Recommend keep both and show the overlap. Retiring is a person's
   action, through the existing Reject.*
5. **"Perhaps others".** Are a governance policy (as a `GovernanceMechanism` link, not an anchor) and a `DataSpec`
   (as an anchor) wanted? *Recommend neither now: asset anchoring stays overruled, and policy links come when
   RE has a policy picker.*
6. **`SchemaAttributeDefinition`.** May RE record a confirmed contract-field-to-column match in Egeria (C7)?
   *Recommend yes, after C1 proves the matching, as its own small gate.*
7. **A contract to test against.** Should a sample ODCS contract with `schema` and `quality` sections (for
   coco_pharma `sales`) be imported through Product Manager for the C1–C3 gates, as an owner-approved Egeria
   write? *Recommend yes: one disposable contract, named so that it is obviously a fixture.*
8. **ODCS `required` / `unique` on a property.** Should these be tested as Required / Unique checks, or only
   tested when the contract has a `quality` clause for them? *Recommend only `quality` clauses. `Schema_Conformance`
   covers names and types, as TestGen does.*
9. **Creating contracts.** Is "RE drafts an ODCS document, a person imports it through Egeria" (C5, C6) the
   answer to "or even create new ones"? *Recommend C5 now, if wanted, and C6 only after a draft has been
   reviewed by use.*
