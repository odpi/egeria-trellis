# Egeria and pyegeria support needed for multi-resource surveying — what exists, what is missing, what to ask for

**Status:** validation and planning input, 2026-09-20. Expands §12 of
`docs/multi-resource-questions-design.md`. Nothing here is built or filed.

**Method.** Each item below was checked on 2026-09-20 against three sources,
read-only: the Egeria Java checkout (`/Users/dwolfson/localGit/egeria-v6/egeria`,
types in `OpenMetadataType.java`, property classes, view-service REST
resources, content packs and connectors), the pyegeria checkout
(`/Users/dwolfson/localGit/egeria-python`, `pyegeria/omvs/*`, the Dr.Egeria
compact command specs under `md_processing/data/compact_commands/`, the
functional tests) and `PYEGERIA_ISSUES.md`. Every claim carries a path so it
can be re-checked; nothing was executed against a live platform. §9 is the
list of live probes to run before anything is filed upstream.

**Headline.** The first draft of §12 overstated the gaps. Five of the seven
concerns have server types, REST endpoints, pyegeria methods *and* Dr.Egeria
commands already. Two things are genuinely missing (a model-card
representation; folder-survey → DuckDB chaining), two are partial (proposals
of *new* governance elements from a survey; a reachability record), and one
is a probable server bug that blocks the Purpose spec (`ReferenceValueAssignment`
is dispatched as `ValidValuesAssignment`).

| # | Concern | Egeria server | pyegeria | Dr.Egeria | Verdict |
|---|---|---|---|---|---|
| 1 | Notification type, monitored resource, subscriber | types, endpoint, handler, runtime (Baudot) | dedicated for both links; generic for create | all three commands | **exists** — convenience gap only |
| 2 | Model card / analytics model | three entity types with no properties; no card concept | nothing dedicated; `ExternalModelSource` via external references | `Create External Model Source` | **missing** — the real ask |
| 3 | Survey proposals of *new* data classes / reference sets / grains / scopes | RFA carries free-form `actionProperties`; annotations can only name *existing* candidates by GUID | `create_annotation` untyped; `create_data_class`, `create_data_grain`, valid value creation | data class, data grain, no valid-value-set command | **partial** — convention now, type later |
| 4 | `ReferenceValueAssignment` (for Purpose) | type + endpoint | dedicated method | command | **exists, probably broken server-side** |
| 5 | Reachability record | nothing on Connection/Endpoint/Asset; `ConnectorActivityReport` has no outcome field | — | — | **missing** — keep in RE for now |
| 6 | DuckDB via folder survey | DuckDB survey service exists; no file type, no chaining | nothing DuckDB-specific | — | **partial** — content-pack ask |
| 7 | `DataScope` measured vs declared | classification only; no annotation | dedicated add/update/clear | classify / update / declassify | **exists for declared**; measured needs a convention |

Plus two pyegeria-side findings that are not extension asks but affect the
plan: survey request parameters cannot be passed through the `initiate_*_survey`
wrappers (§8.1), and the Purpose spec depends on the bug in item 4.

---

## 1. Notifications

### What we mean

A subscriber (a person, a team, or an automated process — project owner,
2026-09-20) is told when a comparator (`schema_diff`, `class_change`,
`survey_absence`, …) fires for a scope (a resource, a group, a product), at a
cadence (immediate or digest). Design in `multi-resource-questions-design.md`
§9.

### What exists

**Server.** `NotificationType` is a governance definition (model 0451,
`OpenMetadataType.java:3818`) with the fields the design needs:
`plannedStartDate`, `multipleNotificationsPermitted`,
`minimumNotificationInterval`, `notificationInterval`, `lastNotification`,
`nextScheduledNotification`, `notificationCount`, `plannedCompletionDate`
(`NotificationTypeProperties.java:23-37`), plus the inherited
`summary/scope/usage/domainIdentifier/importance/implications/outcomes/results`
and `implementationDescription`. `MonitoredResource` carries only
`label`/`description` (`MonitoredResourceProperties.java:20`);
`NotificationSubscriber` carries `activityStatus`, `zoneMembership`,
`iscQualifiedName`, `lastNotification` (`NotificationSubscriberProperties.java:26-31`).
Creation is the generic governance-officer endpoint
`POST /governance-definitions` (`GovernanceOfficerResource.java:59`), which
accepts any `GovernanceDefinitionProperties` subclass.

**A runtime exists.** `NotificationHandler.notifySubscribers(...)`
(`open-governance-framework/.../handlers/NotificationHandler.java:118`) does
the send, and the **Baudot** integration connector
(`nanny-connectors/.../baudot/BaudotSubscriptionManagementProvider.java:17`,
`BaudotNotificationTypeProcessor.java`) runs one processor per
`NotificationType` catalog target, caches its monitored resources and
notifies on change events. This is the piece the earlier spec did not know
about. There is an `open-watchdog-framework`, but the only implementing
service is Lovelace's karma-points awarder; the generic watchdog governance
action connectors do not reference notifications.

**pyegeria.** `link_monitored_resource` (`notification_manager.py:122`) and
`link_notification_subscriber` (`:263`) are dedicated, with detach
counterparts. Creation is `GovernanceOfficer.create_governance_definition(body)`
(`governance_officer.py:430`) with `properties.class =
"NotificationTypeProperties"`, which *is* in the whitelist
(`governance_officer.py:42`). No tests exercise `notification_manager.py`.

**Dr.Egeria.** `Create Notification Type` (`commands_governance_officer_compact.json:4255`),
`Link Monitored Resource` (`:4683`), `Link Notification Subscriber` (`:4705`).
Report FormatSets `Notification-Type-DrE-Basic/-Advanced`
(`dr_egeria_reports.py:108`).

### Gap and ask

- **No extension needed.** The earlier "no dedicated method" gap in
  `docs/automate-notification-manager-pyegeria-spec.md` is a convenience gap:
  the properties class is whitelisted and the Dr.Egeria command exists.
  RE can author notification types through Dr.Egeria today.
- **pyegeria convenience (low):** a `create_notification_type(...)` wrapper
  with typed arguments, so RE does not hand-build the body. Log in
  `PYEGERIA_ISSUES.md` as an enhancement; not blocking.
- **Operational, not a type ask:** Baudot has to be *running* as an
  integration connector on the platform for Egeria-side delivery to happen.
  **Project owner, 2026-09-20: Baudot is deployed in the egeria-workspaces
  quickstart, which is the dev environment.** Probe 1 in §9 reduces to
  confirming it is running and has the notification types as catalog targets.
- **Design consequence:** because Baudot watches *change events on monitored
  resources*, the Egeria-side path fires on catalog changes (a new annotation,
  a changed classification), not on RE's comparators. So RE's comparator
  result must be *written to Egeria* — as an annotation on a fresh survey
  report or an RFA — for Baudot to notice it. That is consistent with rule D
  (every result is published) and means §9.2 step 3 in the design doc
  ("let Egeria detect") is closer than it looked: RE publishes the change
  finding, Egeria delivers it.

**Decision (project owner, 2026-09-21):** confirmed — nothing needed. No
extension request for notifications.

---

## 2. AI models and the model card

### What we mean by "model card"

A model card (Mitchell et al., 2019; the Hugging Face card template is the
de-facto instance) is the structured self-description that ships with a
model. The sections that matter to the questions in design §8, in the order a
consumer reads them:

| Section | Content | Which question it answers |
|---|---|---|
| Model details | developer, licence(s), task, architecture, modality, parameter count, base model, language(s), version, formats and quantisations available | what is it; who owns it; weights vs code licence; how big to host |
| Uses | direct use, downstream use, **out-of-scope use** | intended use; restrictions |
| Bias, risks and limitations | known failure modes, populations affected, recommendations | regulatory obligations; suitability |
| Training details | training data (named datasets), preprocessing, procedure, hyperparameters, compute | provenance; are the datasets known to us |
| Evaluation | test data, metrics, results per benchmark (HF `model-index` front matter) | claimed performance; reproducibility |
| Environmental impact | hardware, hours, emissions | cost; obligations |
| Technical specifications | architecture objective, infrastructure | can we run it |
| Citation, contact, card authors | | ownership, contact |

Two properties of a card that shape the representation: it is **versioned
with the model** (a card describes a specific revision), and it is
**asserted by the publisher**, so every field is a claim, not a measurement
— RE's checks (`model_card_completeness`, `model_artifact_scan`,
`descriptor_conformance`) are what turn claims into evidence.

### What exists

**Server.** `DeployedAnalyticsModel` (`OpenMetadataType.java:2881`),
`AnalyticsModelRun` (`:2891`), `AnalyticsEngine` (`:1108`),
`ExternalModelSource` (`:389`) — model 0265. **All three property classes
have zero own fields**: `DeployedAnalyticsModelProperties` extends
`DeployedSoftwareComponentProperties` (only `implementationLanguage`);
`AnalyticsModelRunProperties` extends `TransientEmbeddedProcessProperties`;
`ExternalModelSourceProperties` extends `ExternalReferenceProperties`
(`referenceTitle`, `referenceAbstract`, `authors`, `organization`, `sources`,
`license`, `copyright`, `attribution`). No relationship in
`OpenMetadataType.java` mentions analytics models. `ModelCard`, "intended
use", "training data", "evaluation", "benchmark": not found.

Licence twice is supported: `LicenseProperties` has `licenseId`,
`coverageStart/End`, `conditions`, `licensedBy`, `custodian`, `licensee`,
`entitlements`, `restrictions`, `obligations`, `notes` (`LicenseProperties.java:26-42`),
and there is no uniqueness constraint, so a model can carry a weights licence
and a code licence distinguished by `licenseId`.

**pyegeria.** Nothing for `DeployedAnalyticsModel` or `AnalyticsModelRun`;
the generic `AssetMaker.create_asset(body)` with a `typeName` is the route
(the same pattern the functional tests use for `SurveyReport`).
`ExternalModelSource` is handled by the external-references family
(`external_links.py:28-50`). `add_license_to_element` (`classification_explorer.py:7973`)
returns the relationship GUID, so two licences coexist. `add_ownership_to_element`
(`:8288`) exists (note closed ISSUE-87: the docstring names the wrong
properties class; the code wants `OwnershipProperties`).

**Dr.Egeria.** `Create External Model Source`
(`commands_external_references_compact.json:3055`), `Link License`,
`Classify Ownership`. No analytics-model command.

### Gap and ask

This is the one real type ask. Proposed in two tiers so the small one can
land without waiting for the large one.

**Tier 1 — properties and relationships on what exists (small, model 0265):**

| Addition | Type | Carries |
|---|---|---|
| Properties on `DeployedAnalyticsModel` | strings / ints / arrays | `task`, `architecture`, `modality`, `parameterCount`, `modelVersion`, `formats` (array), `languages` (array), `intendedUse`, `outOfScopeUse`, `limitations`, `cardURL` |
| `DerivedFromModel` relationship | model → model | `derivationType` (fine-tune, merge, quantisation, distillation), `notes` — the lineage question |
| `TrainedOn` relationship | model → `DataSet` (or any Asset) | `role` (training, validation, test), `notes` — links §7 datasets to §8 models |
| Evaluation results | reuse `AnalyticsModelRun` as "one evaluation run" with properties `benchmark`, `metric`, `value`, `evaluatedOn` (date), `datasetGUID` | the `model-index` front matter, one run per benchmark result |

**Tier 2 — a `ModelCard` element (larger):** a `Referenceable` subtype
anchored to the model, versioned, with the section list above as typed
properties or as nested `ModelCardSection` elements (`sectionName`, `content`,
`claimedBy`, `claimedOn`), and a `ModelCardVerification` annotation type so a
survey can attach "present / thin / missing / contradicted by measurement"
per section. This is what makes `model_card_completeness` a first-class
catalog fact rather than a profile annotation.

**Until either lands:** RE publishes a `DeployedAnalyticsModel` with the
card's Model-details fields in `additionalProperties`, the card itself as an
`ExternalModelSource` (its `license`, `authors`, `organization` fields fit
directly), each card section as a `ResourceProfileAnnotation`, two `License`
relationships, `Ownership`, and datasets/base models as `ExternalReference`s
until the two relationships exist. Nothing in that interim conflicts with
Tier 1 or 2.

**Recommendation:** ask for Tier 1 with the field list above once Phase 4 in
the design doc has produced three real cards' worth of data to show; Tier 2
is a conversation to open, not a request to file. Filesystems and databases
come first, so this is not on the critical path (project owner, 2026-09-20:
review the model-card proposal separately; there is time).

**Decision (project owner, 2026-09-21):** in design — matches this
section's own recommendation (Tier 1 ask waits for three real cards from
Phase 4; Tier 2 stays a conversation, not a request).

---

## 3. Proposals from surveys — new data classes, reference sets, grains, scopes

### What we mean

A survey observes something that *should* become a governance element that
does not yet exist: a column pattern that matches no `DataClass`; a
low-cardinality column whose values form no known `ValidValueSet`; a table
whose grain is evident but has no `DataGrain`; a date range with no declared
`DataScope`. The survey must *propose*, with evidence and confidence, and a
curator must *declare* (design doc §14, "measured is not declared").

### What exists

**Server.** The annotation types can only point at *existing* elements:
`DataClassAnnotationProperties` has `specification` and
`candidateDataClassGUIDs` (`:22-25`); `DataGrainAnnotationProperties` has
`granularityBasis`, `grainStatement`, `interval`, `candidateDataGrainGUIDs`
(`:22-27`); `interval` is a Java `long` (a count in the grain's units), so
RE's period word ("per-date", "monthly") goes in
`additionalProperties.intervalName` instead (a word there was a 400, live
2026-10-14). `RequestForActionProperties` (note: not
`RequestForActionAnnotationProperties`) has `actionSourceName`,
`actionRequested`, `actionProperties: Map<String,String>` (`:24-28`) plus
inherited `dataType`, `matchingValues`, `nonMatchingValues`; its target
relationship `RequestForActionTarget` carries `actionTargetName`
(`RequestForActionTargetProperties.java:23-25`). `ValidValueDefinitionProperties`
has **no status field** (`:31-37`); no `PROPOSED`/`DRAFT` enum applies to
`DataClass` or `ValidValueDefinition`, though the generic instance
`ElementStatus` (`search/ElementStatus.java`) exists and create bodies
generally accept an `initialStatus`. `DataClassAssignment` as a relationship
type is **not in the current type set** (only legacy traces in
`OpenMetadataTypesArchive1_2.java:9129`); the current path is the generic
`DataValueAssignment` (data class or data grain → any Referenceable).

**pyegeria.** `create_data_class` (`data_designer.py:528`), `create_data_grain`
(`:482`), `create_valid_value_definition` (`reference_data.py:558`; a set is
the same call with `typeName: "ValidValueSet"`), `link_data_value_assignment`
(`data_designer.py:4862`), `link_valid_values_assignment` (`reference_data.py:1154`).
`DataDiscovery.create_annotation(body)` (`data_discovery.py:142`) is
untyped: subtype by `typeName`/`annotationType`; the functional test
`test_data_discovery.py` is an accept/reject matrix exercised with
`ResourceMeasureAnnotation`, `RequestForAction`, `ClassificationAnnotation`.
`DataClassAnnotation`, `DataGrainAnnotation`, `ResourceProfileAnnotation`,
`SemanticAnnotation`, `FingerprintAnnotation` have never been sent from
Python (zero hits). `SurveyReport` creation over REST **works**
(`test_data_discovery.py:195-204`, `create_asset` with
`SurveyReportProperties` and `parentRelationshipTypeName="ReportSubject"`).

**Dr.Egeria.** `Create Data Class` (`commands_data_designer.json:3637`),
`Create Data Grain` (`:3675`), `Assign Data Value Specification`,
`Link Element to Valid Values`. **No command creates a ValidValueSet or
ValidValueDefinition** (the reference-data family has only the
Valid-*Metadata*-Value commands and the assignment links). No annotation or
survey-report command.

### Gap and ask

- **Now, no extension:** a convention on `RequestForAction`.
  `actionRequested` ∈ {`propose-data-class`, `propose-valid-value-set`,
  `propose-data-grain`, `propose-data-scope`}; `actionProperties` carries the
  proposal (`specification` regex or pattern, `matchingValues` sample,
  `confidence`, `columnCount`, `grainStatement`, `dataCoverageStart/End`,
  `scopeElements`); `RequestForActionTarget` points at the column, table or
  asset. RE's review queue (design §11) reads these and, on accept, calls the
  dedicated create + assignment methods. This is enough for Phase 1.
- **Then, a small type ask:** `candidateDataClassSpecification` (string) on
  `DataClassAnnotation` and `candidateValidValues` (array) on a new
  `ReferenceDataAnnotation`, so a proposal is a typed annotation instead of
  an RFA convention. Same shape as the existing `candidate…GUIDs` fields, so
  it is a natural extension rather than a new idea. A `DataScopeAnnotation`
  mirroring `DataScopeProperties` fields would complete the set (see §7).
- **Verify (probe 4):** whether `create_data_class` accepts an
  `initialStatus: DRAFT`. If it does, "propose" can create the element in
  DRAFT and the curator's accept is a status change — a better UX than an
  RFA carrying a spec in a string map.
- **pyegeria:** typed helpers for the five annotation types never sent from
  Python, and a `create_valid_value_set` convenience. Enhancement, not
  blocking — but each one needs a probe (§9) because `create_annotation`'s
  accept/reject behaviour is only known for three types.
- **Dr.Egeria:** a `Create Valid Value Set` / `Create Valid Value Definition`
  command pair, so the Purpose set (§4) and proposed reference sets can be
  authored the same way as everything else.


**Decision (project owner, 2026-09-21):** not clear anything is missing —
under consideration by the Egeria team. Two existing mechanisms this
section's "type ask" tier did not account for:

- **`Annotation.contentStatus` can itself be `DRAFT`**
  (`ContentStatus.java:37`, `"The content is incomplete"` —
  `AnnotationProperties extends AuthoredReferenceableProperties`, which
  carries `contentStatus`/`userDefinedContentStatus`,
  `AuthoredReferenceableProperties.java:58-59`). A proposal annotation can
  be published as incomplete/unconfirmed without inventing a
  `candidateDataClassSpecification`/`ReferenceDataAnnotation` field to carry
  that meaning.
- **`AssociatedAnnotation`** (`OpenMetadataType.java:6039-6044`,
  `AssociatedAnnotationProperties.java`) — a relationship distinct from
  `ReportedAnnotation` (report → its annotations): "link between an element
  and an Annotation that describes a characteristic of its associated
  real-world counterpart." This is the direct element↔annotation link the
  `candidate…GUIDs` fields were working around with a plain property list.

**Probes 4 and 5 run 2026-09-21 — see `PROBES-2026-09-21.md`
for the full write-up.** Answer: **yes** for probe 4 (corrected — see
below), **yes** for probe 5.

**Probe 4's first pass tested the wrong field.** `create_data_class` with
`initialStatus: DRAFT` — Egeria's generic entity persistence/lifecycle
status (`ElementStatus`) — does return success with the element reading back
as `ACTIVE`, confirmed at both the pyegeria layer (`NewElementRequestBody`
has no `initial_status` field, silently dropped by `extra='ignore'` — logged
as ISSUE-113 in `PYEGERIA_ISSUES.md`, PR against upstream
`odpi/egeria-python`) and the server layer (a raw REST call bypassing
pyegeria entirely also came back `ACTIVE`). But `ElementStatus` was never
the mechanism this section's decision meant — re-reading it (and the Java
source it cites) against the actual result caught this: the decision is
about **`contentStatus`**, a domain property on
`AuthoredReferenceableProperties` — a different field entirely from
`ElementStatus`, describing whether the *content* is complete rather than
whether the *entity* persists.

**Corrected probe 4 — `contentStatus: DRAFT` inside `properties`: works
cleanly**, tested on both proposed carriers: a `DataClass`
(`DataDesigner.create_data_class`) and a `DataClassAnnotation`
(`DataDiscovery.create_annotation`). Both round-trip `contentStatus` as
`DRAFT` on read-back; both keep their separate `ElementStatus` at `ACTIVE`
throughout — the two statuses are independent, exactly as the class
hierarchy implies (`AnnotationProperties` and
`DataValueSpecificationProperties`/`ValidValueDefinitionProperties` all
extend `AuthoredReferenceableProperties`, so both proposed carriers get it
for free).

Probe 5 (`create_annotation` for all five proposal-shaped types, attached via
`ReportedAnnotation`) worked cleanly for all five, verified by an independent
re-read (5 distinct GUIDs, correct types) rather than by trusting the create
calls.

**So the §3 "type ask" tier DOES drop, confirmed via the corrected probe**:
a proposal — a new governance element, or a proposal-carrying annotation —
can be published as genuinely incomplete/unconfirmed today, via
`contentStatus: DRAFT`, no pyegeria fix or Egeria change needed. No new
`candidateDataClassSpecification`/`ReferenceDataAnnotation`/
`DataScopeAnnotation` fields are needed; a proposal is a normal annotation
(or element) with `contentStatus: DRAFT` set, linked to its originating
evidence via `AssociatedAnnotation`.

**Open question the RFA path didn't have to answer, sharpened by the
correction rather than resolved: does an ordinary read path surface
`contentStatus` at all?** The original visibility concern here was about
`ElementStatus` (checked via `QueryOptions.limitResultsByStatus`, which
defaults to `null`/"all statuses") — now confirmed *not* the live risk,
since a `contentStatus: DRAFT` element's `ElementStatus` stays `ACTIVE` and
was never going to be excluded by that filter regardless. The real question
is different: a consumer reading via `find_metadata_elements` or similar
sees the same `ACTIVE` element either way — does anything in that path
distinguish a `contentStatus: DRAFT` annotation/DataClass from a confirmed
one, or does it render identically unless the reader specifically checks
`contentStatus`? Not checked here. Needs its own probe (does RE's own
annotation rendering, or a naive consumer, surface `contentStatus`
anywhere?) before relying on it as a safe default for unconfirmed
proposals — worth doing before Phase 1 slice #10 leans on this as the
primary UX, though it doesn't block starting the slice.

---

## 4. `ReferenceValueAssignment` and the Purpose spec

### What we mean

Design §10: Purposes as a `ValidValueSet` with one `ValidValueDefinition`
each, and each Question term tagged with its purposes by
`ReferenceValueAssignment` — the relationship Egeria defines for "this
Referenceable is tagged with this valid value", carrying `confidence`,
`steward`, `notes`.

### What exists

**Server.** Type at `OpenMetadataType.java:5626`; properties `attributeName`,
`confidence`, `steward`, `stewardTypeName`, `stewardPropertyName`, `notes`
(`ReferenceValueAssignmentProperties.java:27-32`). Endpoints on the
reference-data view service: `/elements/{elementGUID}/reference-value-assignment/{validValueDefinitionGUID}/attach`
(`ReferenceDataResource.java:305`) and detach (`:336`).

**Probable bug.** `ReferenceDataRESTServices.linkReferenceValueAssignment`
(`:537`) sets `methodName = "linkValidValuesAssignment"` (`:542`),
type-checks the body for `ValidValuesAssignmentProperties` (`:560`) and calls
`handler.linkValidValuesAssignment(...)` (`:562-583`). A body carrying
`ReferenceValueAssignmentProperties` falls to the `else` at `:578` and is
rejected; a body carrying `ValidValuesAssignmentProperties` would be accepted
and would create the *wrong relationship type*. `ReferenceValueAssignmentProperties`
is referenced nowhere in the view services.

**pyegeria.** `link_reference_value_assignment` (`reference_data.py:1339`),
detach (`:1433`) — dedicated, and it sends `ReferenceValueAssignmentProperties`
(`:1322`), which is exactly the body the server rejects if the reading above
is right. **Dr.Egeria.** `Link Reference Value Assignment` / `Detach`
(`commands_reference_data_compact.json:610`). Also present and worth knowing:
`Link Question to Valid Values` in the glossary family, which is
`ValidValuesAssignment` (the values *of* the question are drawn from a set)
— not the same semantics, but it shows the Question-term-to-valid-value path
has been thought about.



### Gap and ask

- **Probe 2 first** (§9): call `link_reference_value_assignment` against the
  dev platform. If it fails as predicted, file an Egeria server issue with
  the four line references above; the fix is a copy-paste correction. Until
  fixed, the Purpose spec cannot be published as designed.
- **Fallback if the fix is slow:** tag Question terms with purposes via
  `ValidValuesAssignment` and note the semantic mismatch, *or* keep Purposes
  RE-side (today's state). The first is a small lie in the catalog; the
  second is honest. Prefer waiting for the fix.
- **Dr.Egeria:** the missing `Create Valid Value Set` command (§3) is what
  would let `foundations.md` create the Purpose set the way it creates
  Perspectives.

**Decision (project owner, 2026-09-21):** verified as a real bug — being
fixed on the Egeria side. Until it lands, follow this section's own
fallback (tag via `ValidValuesAssignment` with the semantic mismatch noted,
or keep Purposes RE-side) rather than waiting.

---

## 5. Reachability record

### What we mean

Rule B in the design doc decides per resource whether Egeria's engine host
can open it. That probe is cheap (`finalAnalysisStep=CHECK_ASSET`) but every
client repeating it is waste, and "last known reachable from engine host X at
time T" is a fact other consumers (Egeria Advisor, an operator) would use.

### What exists

Nothing on `Connection`, `Endpoint` or `Asset`: no `lastConnected`,
`connectorStatus` or resource-status classification in `OpenMetadataType.java`
or `OpenMetadataProperty.java`. `ResourcePhysicalStatusAnnotation` describes
the resource (`resourceCreateTime`, `resourceUpdateTime`,
`resourceLastAccessedTime`, `size`, `encodingType`), not an attempt to reach
it. `ConnectorActivityReport` (model 0457, `:4232`) records a connector's
start, refresh and disconnect times and element counts, but **no outcome or
error field**. `IntegrationReport` no longer exists.

**Learned from probe 9 (2026-09-21, `PROBES-2026-09-21.md`):**
a connection's existence says nothing about whether its secret resolves on
the engine host. `coco_pharma`'s asset had a real `VirtualConnection` whose
embedded `SecretsStoreConnection` still carried the literal
`~{secretsCollectionName}~` placeholder, and the native survey failed on
SCRAM authentication. The quickstart's own surveys resolve secrets from
`/deployments/secrets/*.omsecrets` inside the engine host container; none of
the three collections there is named for a Postgres survey. **Decision
(project owner, 2026-09-21):** RE keeps its own client-side secret store
(Egeria's client-side-secret structure, own YAML file under
`/deployments/secrets/`, one collection per resource, written via
`save_client_side_secret` at registration) rather than writing into Egeria's
bundled files — dwolfson/trellis#185 (branch `re/own-secrets-store`). So the reachability record
below has two outcome kinds to distinguish, `no_connection` and
`unresolvable_secret`, plus the network failures.

### Gap and ask

- **Keep it in RE for now.** A `resource_reachability` table `(slug,
  probed_from, probed_at, outcome, error_code, latency_ms)` is a day's work
  and serves rule B and the launcher sentence in design §11. `outcome`
  values: `reachable`, `no_connection`, `unresolvable_secret`,
  `network_unreachable`, `auth_rejected`, `unknown`.
- **Ask later, small:** an `outcome` + `errorMessage` pair on
  `ConnectorActivityReport`, or a `ReachabilityAnnotation` produced by
  `CHECK_ASSET`. The second is more natural given that `CHECK_ASSET` is
  already the analysis step that answers the question. Not worth filing
  until rule B has run for a while and the value is demonstrated.


**Decision (project owner, 2026-09-21):** defer the `resource_reachability`
table until further tests — do not build it yet.

**Correction (project owner, 2026-09-22):** the 2026-09-21 deferral above is
reversed — the project owner asked for this slice (Phase 1 slice #13) to
proceed now. Probe 7/8 (below) are the "further tests" the deferral was
waiting for; both ran live 2026-09-22. Built: `resource_reachability`
(filesystem-scoped, not the fully polymorphic shape sketched above — see
`RESOURCE-REACHABILITY-IMPLEMENTED.md` for why), the
check (`resource_explorer/reachability.py`), and the classic-UI launcher
sentence. The live run also surfaced a real, previously-unconfirmed gap:
RE's own folder-cataloging path attaches no Connection to the Asset it
creates, so this check reports `no_connection` for essentially every
filesystem RE has cataloged today — see the design notes doc and
`PROBES-2026-09-21.md`'s "Probes 7 and 8, run live"
section for the live evidence. Database reachability remains out of scope
(not probed as part of this slice).

---

## 6. DuckDB via folder survey

### What we mean

`survey-folder` finds a `.duckdb` file; the same governance action process
should catalogue it as a DuckDB database asset and survey it as a database,
so a file-that-is-a-database is not left as an unclassified file. Project
owner, 2026-09-20: DuckDB is the second engine and a compose script exists.

### What exists

**Server.** A full DuckDB survey: `DuckDBDatabaseSurveyActionProvider`
(`duckdb-connectors/.../survey/DuckDBDatabaseSurveyActionProvider.java:35`),
service, annotation types, stats and federation extractors; content-pack
service `duckdb-database-survey-service`, request type
`survey-duckdb-database` (`RequestTypeDefinition.java:915-931`), technology
types `DUCKDB_DATABASE` and `DUCKDB_DATABASE_SCHEMA`
(`DuckDBDeployedImplementationType.java:22,37`), and a create-then-survey
process (`DuckDBPackArchiveWriter.java:104-107`) keyed on a database, not on
a discovered file. **Missing:** no `duckdb` entry in `FileType.java` /
`FileExtension.java` reference data, and no reference in the files content
pack's folder processes.

**pyegeria / Dr.Egeria.** Nothing DuckDB-specific; the generic technology-type
and `initiate_gov_action_type` routes apply.

### Gap and ask

- **Content-pack ask (feasible, self-contained):** add `.duckdb` to the file
  reference data as a `DuckDB Database` file type with deployed
  implementation type `DUCKDB_DATABASE`, and a governance action process
  "on file classified DuckDB Database → create DuckDB database asset from
  template → `survey-duckdb-database`" that the folder survey's
  `PRODUCE_ACTIONS` step can trigger. Both halves exist; the link is what is
  missing.
- **RE meanwhile:** the file classifier already keys on extension and Egeria
  reference data; RE can classify `.duckdb` locally, register it as a
  database sub-resource of the filesystem (design D3), and run its own DuckDB
  introspection through the engine capability declaration (design §5.1).
- **Note for the plan:** the DuckDB survey's `DuckDBFederationExtractor`
  suggests it reads attached databases, which is a finding RE's
  `db_external_dependencies` should match in shape (rule A).


**Decision (project owner, 2026-09-21):** planned for Egeria — the
`.duckdb` file type + folder-survey chaining ask is already on Egeria's own
roadmap. No RE-side filing needed; use the interim local-classification
path in the meantime.

---

## 7. `DataScope` — declared exists, measured needs a convention

### What we mean

Design §14: *measured* scope (a survey saw dates x..y, a bounding box) is
evidence on the report; *declared* scope (a curator says x..current, this
jurisdiction, this controller) is the classification on the asset.

### What exists

**Server.** `DataScope` classification only (`OpenMetadataType.java:2167`),
with `min/maxLongitude`, `min/maxLatitude`, `min/maxHeight`,
`dataCollectionStart/EndTime`, `dataValidityStart/EndTime`,
`dataCoverageStart/EndTime`, `scopeElements`, `additionalProperties`
(`DataScopeProperties.java:27-40`). Endpoints on classification-explorer
`/elements/{elementGUID}/data-scope` add / update (`ClassificationExplorerResource.java:587,618`),
handler `StewardshipManagementHandler.addDataScopeClassification` (`:1632`).
Existing producers are Lovelace's OpenLineage service and two cataloguers —
all *declaring* on the asset. **No `DataScopeAnnotation`** exists.

**pyegeria.** `add_data_scope` (`classification_explorer.py:9948`),
`update_data_scope` (`:10089`), `clear_data_scope` (`:10229`). **Dr.Egeria.**
`Classify Data Scope`, `Update Data Scope`, `Declassify Data Scope`
(`commands_curation_compact.json:3778`).

### Gap and ask

- **Declared: nothing to ask.** The Curate surface calls `add_data_scope`
  with the curator's values; jurisdiction and controller go in
  `scopeElements` (`jurisdiction`, `region`, `controller`, `residencyRule`)
  — agree the key names once and record them in `foundations.md`.
- **Measured: a convention now**, a `ResourceMeasureAnnotation` whose
  `resourceProperties` use the *same key names* as `DataScopeProperties`
  (`dataCoverageStartTime`, `dataCoverageEndTime`, `minLatitude`, …) plus
  `confidence` and `basis` (which column or file the range came from). Same
  names, so the Curate prefill is a key-for-key copy.
- **Then a small type ask:** `DataScopeAnnotation` with those fields and a
  `candidateScope` flag, parallel to `DataClassAnnotation`. Bundle with the
  §3 proposal annotations as one request: "annotations that propose
  governance elements".


**Decision (project owner, 2026-09-21):** under consideration — again, the
`ContentStatus.DRAFT` state on an `Annotation` (see §3's decision above)
may be sufficient on its own, without a dedicated `DataScopeAnnotation`
type: a measured-scope proposal could be a plain `ResourceMeasureAnnotation`
(using the `DataScopeProperties` key names, as already planned) published
with `contentStatus: DRAFT`, rather than needing its own annotation
subtype's `candidateScope` flag. Worth re-checking against probe 5's actual
`create_annotation` results before deciding whether the "small type ask" in
this section is still needed.

---

## 8. Two pyegeria findings that are not asks but change the plan

### 8.1 Survey request parameters do not reach the survey wrappers

`initiate_gov_action_type(..., request_parameters=...)` (`automated_curation.py:3375-3381`)
and `initiate_engine_action(...)` (`:3861-3873`) both accept and forward
`requestParameters`. But `_async_initiate_survey(survey_name, resource_guid)`
(`:3427`) hard-codes a body with only the action type name and one
`serverToSurvey` target (`:3456-3466`), and every `initiate_*_survey`
wrapper (`:3470-3597`) goes through it. So `finalAnalysisStep`,
`ignoreAnalysisSteps` and `analysisLevel` — the three parameters rule B's
reachability probe and the folder-depth choice depend on — cannot be passed
through the convenience wrappers. RE already calls `_async_initiate_survey`
directly for another reason (`egeria_database_surveyor.py:528`); it should
call `initiate_gov_action_type` instead and pass parameters. **Log in
`PYEGERIA_ISSUES.md`** (per the standing rule: do not fix pyegeria in place).

### 8.2 Relationship visibility lag

ISSUE-108 (`PYEGERIA_ISSUES.md:833`): new relationships can be invisible to
related-element queries for ~20 minutes. Every verification in §9 that
reads back a link it just created must allow for this, or it will report a
false failure. The open pyegeria list is otherwise empty as of 2026-09-20.

---

## 9. Live probes to run before filing anything

All against the dev platform; writes there are ordinary shared writes.
Each is one script under `scripts/probes/` that prints exists/works/fails
with the response, so the result is evidence and not a recollection.

| # | Probe | Expected if the reading above is right | Decides |
|---|---|---|---|
| 1 | List integration connectors on the dev platform; is Baudot deployed and running? | unknown | whether Egeria-side delivery is operational or a deployment task |
| 2 | `link_reference_value_assignment` between a scratch term and a scratch valid value | rejected with a properties-class error | file the §4 server bug; unblock or fall back for Purpose |
| 3 | `create_governance_definition` with `NotificationTypeProperties`, then both links, then read back after the ISSUE-108 lag | succeeds | §1 needs no extension |
| 4 | `create_data_class` with `initialStatus: DRAFT` | unknown | whether proposals can be DRAFT elements (§3) |
| 5 | `create_annotation` with each of `DataClassAnnotation`, `DataGrainAnnotation`, `ResourceProfileAnnotation`, `SemanticAnnotation`, `FingerprintAnnotation`, attached by `ReportedAnnotation` to a scratch `SurveyReport` | unknown per type | which typed helpers to ask pyegeria for; what RE can publish today |
| 6 | `add_data_scope` with `scopeElements` {jurisdiction, region, controller}, read back | succeeds | key-name convention (§7) |
| 7 | `initiate_gov_action_type("FileSurvey::survey-folder", request_parameters={"finalAnalysisStep": "CHECK_ASSET"})` against a folder asset | completes fast with only CHECK_ASSET | the rule-B reachability probe is real |
| 8 | Same with `analysisLevel=ALL_FOLDERS_AND_FILES` | full recursion | folder-depth control works |
| 9 | Native `survey-postgres-database` and `survey-folder` against correctly catalogued assets; dump annotation types and metric keys | matches §1.4 of the design doc | ground truth for rule A (design Phase 1 step 1 / Phase 2) |
| 10 | Two `add_license_to_element` calls on one scratch asset with different `licenseId` | both relationships exist | §2 interim publishing shape |

---

## 10. What to file, and where

| Target | Item | Priority | Blocked on |
|---|---|---|---|
| Egeria server | `linkReferenceValueAssignment` dispatches as ValidValuesAssignment (§4) | **being fixed on Egeria's side (2026-09-21)** — no filing needed | probe 2, to unblock/confirm before relying on it |
| Egeria types | model Tier 1: properties on `DeployedAnalyticsModel`, `DerivedFromModel`, `TrainedOn`, evaluation-run properties (§2) | medium — **in design (2026-09-21)** | three real cards from Phase 4 |
| Egeria types | "annotations that propose": `candidateDataClassSpecification`, `ReferenceDataAnnotation`, `DataScopeAnnotation` (§3, §7) | **unneeded (2026-09-21)** — probes 4/5 confirmed `contentStatus: DRAFT` (a domain property on `AuthoredReferenceableProperties`, distinct from the `ElementStatus`/`initialStatus` field the first probe pass mistakenly tested) works cleanly on both a proposal annotation and the candidate element itself; a proposal is a normal element/annotation with `contentStatus: DRAFT` | none — still open: does a normal read path surface `contentStatus` to a consumer (unchecked) |
| Egeria content pack | `.duckdb` file type + folder → DuckDB survey process (§6) | **already planned for Egeria (2026-09-21)** — no filing needed | nothing; self-contained |
| Egeria types | model Tier 2 `ModelCard` (§2) | discuss | Tier 1 |
| Egeria types | reachability outcome on `ConnectorActivityReport` or a `CHECK_ASSET` annotation (§5) | **deferred (2026-09-21), reversed (2026-09-22)** — `resource_reachability` (filesystem-scoped) is now built; the `ConnectorActivityReport`/`ReachabilityAnnotation` upstream ask itself is still not worth filing until the check has run for a while | rule B in use; probes 7/8 run live 2026-09-22 |
| pyegeria | `_async_initiate_survey` drops request parameters (§8.1) | high for rule B | none — log now |
| pyegeria | `create_notification_type`, `create_valid_value_set` | low | probe 3 |
| pyegeria | typed annotation helpers for the five proposal-shaped annotation types | **unneeded (2026-09-21)** — probe 5 confirmed the generic `create_annotation` already handles all five cleanly, no missing helper | none |
| pyegeria | `NewElementRequestBody` has no `initial_status` field, so `initialStatus` is silently dropped client-side on every create routing through it (§3, ISSUE-113) | high — affects any create call that needs non-ACTIVE initial status, not just `create_data_class` | none — log now |
| Dr.Egeria | `Create Valid Value Set` / `Create Valid Value Definition` commands | medium | none |
| RE | `resource_reachability` table; RFA proposal convention; measured-scope key convention; call `initiate_gov_action_type` directly | — | none |
