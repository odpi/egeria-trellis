# Investigation framing — declaring the body of work before Scouting

**Status: design; two pieces have since been built (2026-08-24).** All 41 questions are tagged with Purpose (`341d2f5`) and the check-granularity join exists and is guarded (`852955f` — `configdata/check_registry.yaml`, `tests/test_check_registry.py`). Everything else here — the Investigation record, the tab, the create-Egeria-Project path — remains unbuilt. This line read "nothing built" after both had already shipped. Supersedes nothing; sits above
`docs/discovery-automate-project-context-plan.md` Part 5 (shipped 2026-08-13), which stays
as-is — see §1.

**§3's dispatch chain has been measured against the real catalog.** Perspective cannot drive
dispatch; Purpose can, but only as a *ranking* axis, not a filter — and the exclusivity bar an
earlier draft set was itself unachievable. Read §3 and the measurement section at the end
before building anything that depends on either axis.

## Context

RE today has no concept of *the piece of work you are currently doing*. You land on a
resource and start surveying it. The eight intents (CLAUDE.md rule 17) describe **what kind
of work is happening to one resource**; Perspectives describe **whose concerns filter what
is shown**. Nothing captures **why this set of work exists at all** — and because nothing
does, RE cannot decide what to show first, what to run by default, or whether a finding is
merely evidence or actually somebody's problem.

This design adds a framing step ahead of Scouting: declare the body of work, its purposes,
and its membership. Everything downstream — which resources are visible, which analyses are
proposed, whether a failed check raises an RFA — derives from that declaration.

The word "project" currently means three different things in this codebase, which is the
second half of the problem. See §7.

## Standards & prior art grounding this design

Checked before designing, per the standing rule that has now paid off four times
(`SolutionComponentType`, `ResourceUse`, `SolutionPortDirection`, and now this):

- **`ProjectCharter` (0442)** — *"describes the reasons why a particular project exists…
  often established **before the project itself is set up**."* Carries `mission` (free text
  outcome), `projectType` (org-chosen, valid-value-set encoded — the doc's own examples
  include `"security-assessment"` and `"incident-investigation"`), and **`purposes`**, a
  *list* of formal purposes, also a valid metadata set. `ProjectCharterLink` allows one
  charter across several Projects.
- **`Project` (0130)** — *"the project acts as an anchor for collections of resources that
  the project is using."* Classifications `Campaign` / `Task` / `PersonalProject` /
  `StudyProject`; `ProjectHierarchy` for sub-projects, `ProjectDependency` for
  needs-the-results-of. Note `ProjectScope` is **deprecated** in favour of `AssignmentScope`
  (0120), and `Project.status` in favour of `projectStatus`.
- **`ResourceList` (0019)** — links a `Referenceable` to a supporting resource, with
  `resourceUse`, `resourceUseDescription`, `resourceUseProperties`, and **`watchResource`**
  (*"whether the parent entity should receive notification about changes"*).
- **`CollectionMembership` (0021)** — membership with **rationale and confidence**
  attributes. `Collection` classifications include `WorkItemList` and `ResultsSet`.
- **`CertificationType` / `Certification` (0482)** — see §4.
- **`GovernanceMetric` (0450)** + `GovernanceResults` — see §5.

The recurring finding: almost everything this design needs already exists in Egeria. The
genuinely new work is RE-side wiring, not new concepts.

## 1. The Investigation

An **Investigation** is one body of work. It is the thing the new tab creates and the
context everything else runs inside.

Three ways to start one, as specified:

1. **Bind to an existing Egeria Project** — already supported for search;
   `surveyors/egeria_project_finder.py` wraps `ProjectManager.find_projects`.
2. **Create a new Egeria Project** — net-new. Optionally under an existing Project via
   `ProjectHierarchy`, with a classification (`PersonalProject` / `Task` / `StudyProject` /
   `Campaign`). Explicitly listed as "not designed here" in the Part 5 plan.
3. **Local investigation** — a name, no Egeria write. Promotable later (§6a, and Backlog).

**One local row in all three cases**, with a nullable `egeria_project_guid`. This is the
single most important structural decision here: it makes promotion a fill-in rather than a
migration, and it matches the outbox/publisher model RE already uses elsewhere.

### Membership is many-to-many — and Part 5's table is not where it goes

**A resource belongs to many investigations, not one** (confirmed 2026-08-24). This is the
normal case, not an edge case: the same repo is simultaneously something you are evaluating,
something a compliance sweep is checking, and something someone else's Campaign owns. Any
model that resolves a resource to *one* project context is wrong.

So `entity_egeria_project_context`'s `(entity_type, entity_slug)` key — one row per resource —
cannot express membership at all. Rather than re-graining it:

- **Membership is a join**, N:M between investigations and resources, carrying its own state
  (§6). When the investigation is Egeria-bound this is `ResourceList` / `CollectionMembership`
  relationships, which are natively multi-link — Egeria already models this correctly.
- **`entity_egeria_project_context` keeps its current grain and its current job**, which is
  *not* membership: it records the resource's own default Egeria Project for publish-time
  attribution, set by the Part 5 prompt. Leave it alone.

There is no override chain and no resolution order — those were an artifact of assuming a
resource had one context. The investigation supplies the working context; the per-resource
row supplies a publish default when no investigation is active.

The Part 5 prompt currently fires on **first Egeria publish**. Framing moves that decision
to the front, but **keep the late prompt** as the fallback for resources registered outside
any investigation. It is not dead code once this lands.

## 2. Purpose — why this work exists

**Purpose is `ProjectCharter.purposes`.** Not a new RE vocabulary — a valid metadata set,
which is controlled (so dispatch logic can key on it) and org-extensible without a rebuild
(so an enterprise can add its own). This is the mechanism we were about to reinvent as
"controlled kind + free-text other".

Seed set, consolidated from this session and from what the corpus already implied:

| Purpose | Notes |
|---|---|
| Explore / Research | the default; no commitment implied |
| Select | choose among resources already found, and decide whether to adopt them; subsumes "compare alternatives" (Find comes first: you find, then may or may not select) |
| Assess | review resources already in use |
| Maintain | track change and risk over time; version-change impact |
| Share | produce blueprints/docs so others can find and reuse |
| Learn | bring a person up to speed on a resource |
| Certify | validate against a named external standard (§4) |
| Remediate | fix what a prior Certify or Assess found (§4) |
| Attest | publish a scorecard/certification for others |
| Deploy | investigate (re)deployment approaches |

**Naming note:** the train-a-person purpose is **`Learn`** (decided 2026-08-24). Not
"Understanding" — that is already one of the eight intents, and the collision would be
permanent. The two are genuinely different: `Understanding` (intent) is *did I grasp this
resource*; `Learn` (purpose) is *the point of this work is to bring someone up to speed*.

Some purposes take a **target**: `Certify(standard=…)`. The standard is frequently an
arbitrary internal checklist, so the target cannot be a fixed vocabulary — it is a
reference to a `CertificationType` (§4).

**Discretionary vs imposed.** Most purposes are chosen; compliance ones are imposed by
someone else, with an external deadline, a bar you did not set, and no option to walk away.
The UI should not treat "explore this repo" and "pass this audit by Friday" as the same kind
of thing. Carried on the charter, not inferred.

## 3. Purpose × Perspective → Questions → analyses

These are **different axes and must not be merged** (an earlier draft of this design merged
them and was wrong):

- **Perspective** is a property of the *person* — durable, carried across all their work.
  It answers *whose concerns are in play*. Twelve terms exist
  (`docs/dr-egeria/foundations/foundations.md:230+`).
- **Purpose** is a property of the *engagement* — bounded, starts and ends with the
  investigation. It answers *why this work exists*.

The separating test: *would this change if a different person did the same work?* →
Perspective. *Would this change if the same person did different work?* → Purpose.

They are orthogonal — a Security person casually exploring asks different questions than the
same Security person running a pre-adoption gate, and a Financial person running that same
gate asks different ones again. They will be **correlated in practice**, which is fine for
defaulting the UI and not a reason to conflate them in the model.

Both select over the same Question corpus:

```
Purpose (why) + Perspective (whose concerns) → Questions → analysis_ids → Surveys & Analyses
```

**No third vocabulary.** An earlier draft proposed a "Lens" axis; it dissolved on inspection
— its members turned out to be a mix of Perspectives, Purposes, and one Question set.

### Measured against the real catalog (2026-08-24) — Perspective cannot drive dispatch

An earlier draft of this section asserted the chain and never tested it. Tested now against
`configdata/question_catalog.yaml` (41 repo questions) and `configdata/analysis_catalog.yaml`
(21 repo analyses). It resolves mechanically — every `analysis_ids` reference is valid, no
dangling ids — but it does not discriminate:

| Perspective | analyses reachable | unique to it |
|---|---|---|
| Admin | 9 | **none** |
| Security | 8 | **none** |
| Architecture | 7 | **none** |
| Consumer / Data Expert | 5 | **none** |
| Community / Financial / Governance | 4 | **none** |
| App/AI Builder / Data Owner | 3 | **none** |
| Steward | 2 | **none** |
| Privacy | **0** | — |

**Not one of the twelve perspectives reaches a single analysis that another perspective does
not also reach.** The sets are strictly nested — Perspective varies the *size* of the result,
never its *content*. `Privacy` reaches nothing at all (3 questions, none analysis-answerable).
17 of 41 questions carry 4+ perspectives, so nearly half barely filter anything.

This is not a tagging error — it is what the tags were *for*. Perspectives were assigned to
support **display filtering** ("show me the questions I care about"), where subset semantics
are exactly right. Dispatch needs discrimination, which is a different property. Reusing the
tags for both was my assumption and it does not survive contact with the data.

**Revised model — Purpose is primary, Perspective is secondary:**

- **Purpose orders** the question set, and therefore what runs by default. It is the
  discriminating axis — measurably so (overlap 0.22 vs Perspective's 0.37; see the measurement
  section at the end of this doc). It **ranks, it does not exclude**: nothing is hidden.
  **Scoped to questions and analyses only.** Purpose *does* exclude on one other axis —
  §4 gates RFA emission on it, and `Select` emits none at all. Both rules are correct and
  they are not in tension, but read in isolation this sentence says Purpose never excludes
  anything. Implementing §4's rule as a ranking produces precisely the flooded RFA drawer
  §4 exists to prevent, and the failure is silent — it surfaces as drawer noise nobody
  traces back to here.
- **Perspective ranks within that** — presentation, ordering, emphasis. It keeps doing exactly
  the job it already does well.

The §2/§3 distinction between the two axes stands unchanged; what changes is that they are
**not co-equal inputs to dispatch**. Treating them as co-equal would produce a system where
changing perspective changes how much you see but never what gets run.

### Other things the catalog says that this design assumed wrongly

- **`Asked At` / `Answered At` no longer exist as separate fields.** They were collapsed into
  a single `stage` on 2026-08-14 (`docs/survey-model.md (§4)`), which may carry a
  slash-combined value (`Analysis/Enrichment`) for the Analysis-first/Enrichment-fallback
  pattern in `docs/repo-analysis-funnel.md (§14)`, treated as a literal string until
  that plan is built. Any dispatch keyed on the two-scope-category model is keyed on
  something that was removed.
- **Two incompatible perspective vocabularies.** Questions use the 12 Title-Case Glossary
  names (`Security`, `Data Expert`, `App/AI Builder`…); analyses use 5 snake_case UI values
  (`all`, `security`, `steward`, `data_scientist`, `dba`). They cannot be joined directly —
  `data_scientist` and `dba` do not exist in the question vocabulary, and `Architecture` and
  `Admin` (25 questions each) have no analysis counterpart. The chain only works because it
  routes through `answering.analysis_ids`, never through matching perspective values. This is
  worse than the "4 vs 12" framing in the backlog: it is two vocabularies that cannot meet.
- **Coverage is thin.** Only 5 of 41 questions have `answering.kind == analysis`; the rest are
  `direct` (11), `gap` (8), `human` (7), `mixed` (6), `unknown` (3), `chart` (1). Only 10 of
  21 repo analyses are reachable from any question. Purpose-driven dispatch will hit `gap` and
  `human` frequently, so the UI must treat "no analysis answers this" as a normal outcome that
  routes to Enrichment or an RFA — not an error state.
- **Stage distribution is skewed** toward Analysis (20) and Discovery (8); Scouting has 4 and
  Assessment 3. A framing step that promises to order the funnel is ordering a funnel whose
  early stages are barely populated with questions.
- **Only `repo_questions` exists.** No database or filesystem question sets, though the
  analysis catalog has `database_analyses` and `filesystem_analyses`. Purpose dispatch works
  for repos only until those are written.

### What tagging Purpose actually costs

`question_catalog.yaml` is **generated** — the source of truth is
`docs/dr-egeria/resource_questions.csv`, via `scripts/csv_to_question_catalog_yaml.py`, and
the header says not to hand-edit the YAML. Adding Purpose means a new CSV column, a
regeneration, and 41 questions tagged by hand. The `question` field is also the join key back
to Egeria's own Question elements (exact display-name match, case- and punctuation-sensitive),
so the CSV is not free-form.

That is a bounded, honest cost — but it is real work that must happen before any of §3
functions, and it should be sequenced before the UI work, not alongside it.

### `ResearchQuestion` — the investigation's own open questions

Examined 2026-08-24 (was an open question; confirmed useful). It is **complementary to the
existing Question corpus, not a replacement**, and the distinction is worth keeping sharp:

| | Standing question catalog | `ResearchQuestion` (0430) |
|---|---|---|
| Egeria type | `GlossaryTerm` + **`Question`** classification (0340 Dictionary) | `GovernanceDefinition` → `GovernanceControl` subtype |
| Egeria's own words | *"a question that a particular actor might ask"* | *"an issue or question that needs to be investigated"* |
| Grain | vocabulary — stable, reusable across all work | instance — specific to one body of work |
| RE today | the 41 in `question_catalog.yaml`; already resolved by display name in `survey_definition_reader.py:351-375` | unused |

So the catalog stays exactly as it is — **no migration.** What `ResearchQuestion` adds is a
place to record *the questions this particular investigation exists to answer*, which is the
intent-capture the framing step was asked for in the first place and which nothing in RE can
express today.

It inherits usefully from `GovernanceDefinition`: `summary`, `scope`, `usage`, `importance`
(priority), `implications`, **`outcomes`** and **`results`** (what answering it produced), plus
`implementationDescription` from `GovernanceControl` — which lines up with the catalog's own
`answering_mechanism` field. It has no attributes of its own.

Two relationships make it fit without new types:

- **`GovernanceDefinitionScope`** links a governance definition to the **Project** that scopes
  its applicability — so a `ResearchQuestion` attaches to the investigation directly.
- **`GovernedBy`** links it to the resources it concerns.

A `ResearchQuestion` may reference a catalog `Question` term where one fits, and stand alone
where the investigation is asking something the catalog has never covered. That second case is
the interesting one: **unmatched ResearchQuestions are the growth path for the catalog** —
what people actually needed to ask, observed rather than guessed, and reviewable for promotion
into the standing vocabulary. It is the same escape-hatch-then-promote pattern as free-text
Purposes, applied one level down.

Not required for a first cut of §3, but it is where the "capture the intent of the project"
requirement actually lands, so it should not be lost.

**Partial home already exists:** `configdata/analysis_catalog.yaml:26-40` already carries
`intent` and `perspectives` per analysis. This needs a `purposes` field alongside — an
addition to an existing mechanism, not a new one.

**Storage split, which follows from the distinction rather than being arbitrary:** Purpose
on the Investigation/charter; Perspective on the actor. An investigation with two
participants can legitimately have one purpose and two perspectives.

## 4. Certify, Remediate, Attest

### Certification is an award, not a verdict

`Certification` is a **relationship**, not an entity
(`OpenMetadataTypesArchive1_2.java:7837-7899`). Fourteen properties, all administrative
(`certificateId`, `coverageStart`/`coverageEnd`, `conditions`, `certifiedBy*`, `custodian*`,
`recipient*`, `notes`). **No verdict, status or score field**, and `0482-Certifications.md`
makes clear this is deliberate: *"The certifications **awarded** can be captured…"*. An
award has no failed state.

So: **do not add a status enum to the Certification relationship.** It fights the type's
meaning and would rightly be rejected upstream. (An earlier draft proposed exactly that,
reusing `OpenMetadataConformanceStatus`; withdrawn.)

### The shipping design — zero upstream change

- **The standard** → `CertificationType`, creatable today via Dr.Egeria
  `Create Certification Type`. If externally mandated, link to a `Regulation` via
  `RegulationCertificationType` (`Regulation` carries `regulationSource`, `regulators`).
- **Per-check results** → **`QualityAnnotation`** (0640): `qualityDimension` (check name),
  `qualityScore`, `qualityDescription`. RE already emits annotations into `SurveyReport`s,
  so this needs no new plumbing.
- **Award** the `Certification` relationship **only on pass.** Failures live as dated
  annotations inside the survey report. This mirrors how certification works in reality —
  you do not receive a certificate saying "failed" — and the audit trail is preserved in the
  survey layer, which is where evidence belongs.
- **Scope** a `CertificationType` to the investigation via `GovernanceDefinitionScope`,
  which links governance definitions to Projects.

### Failed check → RFA, conditionally

`QualityAnnotation` is emitted **always** — it is evidence. An RFA asserts *someone must do
something*, which is only true when you own the resource:

| Purpose | A failed check is |
|---|---|
| Select (evaluating a candidate) | evidence for a decision — **no RFA** |
| Assess / Maintain / Certify / Remediate | work to be done — **RFA** |

Filing RFAs against every candidate repo browsed while shopping would flood the RFA drawer
and make it useless. This is the first place Purpose does real work rather than labelling.

**Note the asymmetry against §3**, which states that Purpose ranks and never excludes. That holds for questions and analyses — nothing is hidden from the menu. RFA emission is the one axis Purpose genuinely gates, and it is binary, not an ordering.

**Required change:** RFA emission is currently hardcoded inside each surveyor's branch —
`security_hygiene.py:151-160` builds a `RequestForActionAnnotation` inline whenever
SECURITY.md is missing, regardless of why anyone is looking. Move it out of the surveyors
and into a gating rule at the orchestrator, keyed on the investigation's Purpose. This also
kills the copy-paste problem in `security_hygiene.py:131-250` (three near-identical
blocks): surveyors emit findings, the orchestrator decides what becomes work.

**Remediate's membership is exactly the open RFAs from a prior Certify run.** It is
outcome-driven — it starts from findings and derives its resources, rather than starting
from resources and producing findings. This is why membership needs candidate/in-scope/
excluded states (§7) and why investigations must be able to spawn investigations
(`ProjectHierarchy` / `ProjectDependency`).

The full loop — validate → fail → remediate → re-validate → attest → *again next quarter* —
is the strongest case yet for the Ongoing Monitoring intent that has been sitting proposed
and unbuilt. Compliance recurs in a way exploration does not.

## 5. Scores: the GovernanceMetric gate

RE has a stated design principle against scoring, in five places —
`docs/architecture-recovery.md:1085-1088` and echoes in `github/repo_role.py:28-29`,
`github/expectations.py:33-38`, `github/doc_locations.py:18`,
`surveyors/sub_surveyors/repo_classification.py:23-28`:

> "an expectation checklist turns into a maturity score, and a maturity score punishes
> deliberate choices… Report the findings and their locations as dated evidence; do not rank
> projects on the count."

That principle holds, but it needs to permit being opinionated when opinion is warranted.
The rule, per the 2026-08-24 decision:

> **RE may emit a number only if a declared `GovernanceMetric` exists for it, with
> `measurement` and `target` filled in.** No metric, no number.

If you cannot write down what you are measuring and what good looks like, you do not get to
publish the score. This follows the Portal's existing pattern exactly (see the Governance
Metrics SPA, `egeria-workspaces-fs/portal-docs/tools/governance-metrics.md`): a real
`GovernanceMetric` element, a `GovernanceResults` relationship to whatever computes it, and
a documented data flow. RE's opinionated scores then appear in the same SPA as every other
metric, inspectable by anyone.

Summary rule set:

- **Relaying an external standard** → `CertificationType` + `QualityAnnotation`. No metric;
  the standard is the authority and every number is attributable to it.
- **RE's own opinion** → declared `GovernanceMetric` first, then the number.
- **Neither** → an undeclared score computed inline. `documentation.py:151-167` and
  `health.py:118-159` do this today; both are existing violations (backlog).

### Authoring path and the idempotency constraint

Both confirmed against current code 2026-08-24 by the egeria-workspaces session, not read off
the docs:

**Follow the Portal's authoring path, do not invent a parallel one.**
`gen_governance_metrics.py` → one Dr.Egeria document → registered as a Data Initialization
batch with a canary is still the *only* path (verified via `bootstrap_batches.py`'s
`_EXTRA_BATCH_ROOTS`). If RE declares its own `GovernanceMetric`s it should take the same
shape, so they reseed after an Egeria wipe like everything else.

**The duplicate-`GovernanceResults` bug is fixed — but the fix does not automatically cover
RE.** egeria-python `43758a0` added a pre-existence check before linking (shipped in pyegeria
6.1.0/6.1.1, live in quickstart); `governance-metrics.md` has been corrected accordingly
(`1320fa1f`), so the older "manual re-runs are unsafe" warning no longer applies to the Portal.

The catch that matters here: **that guard fires only for the literal
`object_type == "Governance Results"` branch in `md_processing/v2/governance.py`.** If RE links
its metrics through a different Dr.Egeria command or a direct pyegeria call, it inherits
nothing and will happily duplicate relationships.

So the requirement stands, and is sharper than when this doc first stated it: **RE must carry
its own existence check** unless it links specifically via `Link Governance Results`. "Upstream
fixed it" is true and irrelevant to any path RE might actually take.

## 6. Membership lives in Egeria

Per `0130-Projects.md`, the Project *is* the anchor for the resources it uses. So when an
investigation is bound to an Egeria Project, **membership is Egeria relationships, not an RE
table**:

- `Project` → **`ResourceList`** → each Repo / SurveyReport / Report, with `resourceUse`
  recording *why* it is in scope.
- **`watchResource`** on that relationship is the monitoring subscription flag. RE's
  `scheduler.py` subscriptions are currently a local table; they belong here.
- Where richer membership state is needed, **`CollectionMembership`** already carries
  rationale and confidence.

Three membership states, because Scouting is precisely the act of finding things not yet in
the investigation — a strict in/out model dead-ends a fresh investigation on an empty
screen:

- **candidate** — surfaced by Scouting, not yet committed
- **in scope** — admitted to the investigation
- **excluded** — looked at and deliberately rejected (*this is valuable intent; do not
  discard it*)

Column scoping gets a selector — *This investigation / Candidates / Everything known* —
defaulting to the investigation once non-empty and to everything-known while empty.

**This is cheaper than it looks.** `group_slug` does not filter the sidebar today —
`loadProjects()` (`web/static/index.html:2987-2991`) fetches unfiltered and
`renderProjectList()` (`:3059-3090`) buckets into collapsible sections. Investigation
scoping lands in the same place as the Owner filter (§7). Build them together.

**Related, worth fixing here:** `docs/repo-analysis-funnel.md (§9)` flags that
`OnboardingWizard.run()` triggers full RAG ingestion at `add` time, before Scouting or
Discovery run — contrary to the funnel's own cheap-first premise. An investigation with a
declared Purpose knows whether deep ingestion is warranted at all. Natural to fix here
rather than separately.

## 6a. Closing out an investigation

Raised 2026-08-24; sketched here rather than deferred, because a framing step that can only
ever *open* work accumulates dead investigations and the resource-scoping in §6 degrades as
they pile up.

Egeria supplies the state without extension:

- **`projectStatus`** (valid-value-set backed; note the older `status` attribute is
  deprecated) and **`projectPhase`** carry where the work has got to.
- **`actualCompletionDate`** alongside `plannedCompletionDate` records that it actually ended
  and whether it ended when intended.
- **`projectHealth`** is orthogonal to status — a project can be in-flight and unhealthy.
- **`Success Criteria`** (a list, on the Project) is what closeout should be evaluated
  *against*. Capturing it at framing time and reviewing it at closeout is what makes the
  charter more than paperwork.

**Closed investigations must stay queryable, not disappear.** Their findings are the dated
evidence that a later Certify or Maintain investigation compares against — the change
detection in `notification_detector.py` is worthless if the prior baseline is unreachable.
Closeout should drop an investigation out of the default scope selector, not out of the data.

**Reuse as the basis for a new investigation** — the mechanism already exists and is the more
interesting half. `ProjectCharterLink` explicitly *"allows the same project charter to be
used by multiple related projects"*, so "run last quarter's compliance sweep again" is a new
`Project` under the **existing charter**, inheriting purposes and mission unchanged, with its
own dates and its own membership. That is exactly the recurring-compliance loop from §4, and
it means a charter is effectively a reusable template without anything being called a
template.

Where the new work genuinely derives from the old — a remediation pass, or a re-validation —
link them with **`ProjectDependency`** (*"a project that needs the results of another project
to complete its work"*) rather than duplicating findings across both.

**Carry-forward rules on reuse** (decided 2026-08-24):

- **Membership carries forward for `Certify` and `Maintain`**, where re-checking the same set
  *is* the point — a compliance sweep whose membership reset each quarter would measure a
  different population every time and its trend line would be meaningless. It does **not**
  carry forward for `Explore` or `Select`, where the whole activity is deciding what belongs.
- **`excluded` decisions carry forward too, with their timestamps.** Re-litigating every
  rejection each quarter is exactly the tedium this is meant to remove. But an exclusion is a
  judgement made against a resource *as it was on a date*, and resources change — so an
  exclusion is carried as a **dated, reviewable default, never a permanent verdict**. The UI
  should surface age ("excluded 14 months ago") and prompt re-review once an exclusion is
  older than the successor's own cadence. Silently inheriting a two-year-old rejection is how
  a tool starts hiding things its user would now want to see.

The `notification_detector.py` change detection already diffs successive runs, so a carried-
forward exclusion whose underlying resource has materially changed is detectable rather than
merely aged — worth wiring once both exist.

## 7. The two renames

"Project" currently means three things in this repo, and a fourth in EA's tables:

| Sense | Today | Becomes |
|---|---|---|
| One registered repo | `Project` dataclass, `projects` table (`registry.py:35-58`, `:441-464`) | **`Repo`** / `Resource` |
| A grouping of repos | `ProjectGroup`, `group_slug`, `project_groups` (`registry.py:26-31`, `:482-488`) | **`Owner`** |
| An intra-repo subdivision | `Project.subproject_path` / `parent_slug` (`registry.py:52-53`) | unchanged — *different concept, do not touch* |
| Per-repo code symbols | `resource_explorer.project_code_symbols` etc. | see tripwire |

**`Owner`, not `Org`** — GitHub's own model is an owner of type `User | Organization`, and
plenty of interesting repos live under a person. Real GitHub sync is
agreed, and **`Owner` is keyed on `node_id` from the start** (decided 2026-08-24) — logins get
renamed upstream, `node_id` does not. `login` is stored alongside as the display value and
refreshed on sync; it is never the key. Doing this later would mean rewriting every stored
reference after someone's rename has already broken them.

Renaming `Project → Repo` is the higher-value half. Building an investigation concept whose
Egeria binding is a `Project`, in a codebase where `Project` means a repo, is a permanent
tax. The existing workaround — *"always say 'Egeria Project', never bare 'Project'"*
(`discovery-automate-project-context-plan.md:215-220`) — exists because of this collision,
and the rename retires it.

### ⚠️ Tripwire: EA reads RE's tables cross-schema, by hardcoded string

The registry is **shared PostgreSQL** (`config.py:242-248`,
`postgresql://…/egeria_advisor`, `search_path=resource_explorer`) — not the SQLite that
`data/registry.db` (0 bytes, stale artifact) suggests. EA reads RE's tables directly:

```
advisor/re_code_symbol_reader.py:22        "resource_explorer.project_code_symbols"
advisor/agents/code_intel_agent.py:34-35   symbols + relationships
advisor/rag_retrieval.py:272-273           both tables
advisor/analytics.py:237, advisor/re_code_scope.py, advisor/agents/tools.py:90
```

These are `project_*`-prefixed tables in the *fifth* sense of the word. A regex sweep
turning `project_` into `repo_` will rename them, **EA will still compile, and then fail at
runtime** on a table that no longer exists. There is no import-time error to catch it.

Either leave `project_code_symbols` / `project_code_relationships` alone, or rename them and
fix all six EA call sites **in the same commit** — which the monorepo makes atomic, and is
exactly the leverage of having RE and EA in one repo.

### Scope of the rename

- **Cheap:** modal labels (`index.html:404,587,839`), group badges (`:1116,1358,1419`),
  `loadGroups()`, `registry.py` docstrings, `--group` in `cli/main.py:44`.
- **Real but tractable:** `Group*` Pydantic models (`web/routes/projects.py:44-150`);
  Postgres `ALTER TABLE` for `project_groups` and the `group_slug` columns on `projects`,
  `databases`, `file_systems`, `db_servers`.
- **API paths** — today's are genuinely broken and should be fixed, not merely renamed:

```
GET/POST  /api/projects/groups        ← owner endpoints namespaced under the repo path
DELETE    /api/projects/groups/{slug}
POST      /api/projects/{slug}/group  ← ambiguous with /api/projects/{slug}
```

Move to `/api/owners`, keep old paths as deprecated aliases for one release.

- **Do not touch:** `config.py:68` `experiment_name="project-explorer"` (MLflow label);
  README/CLAUDE.md prose using "project" to mean this codebase.

**Already half-built, which ratifies the rename:** `github/org_importer.py` bulk-imports a
GitHub org, `GitHubClient.search_repos()` (`client.py:26-51`) replaced the old org-only
call, and `GET /api/projects/groups/suggestions` (`web/routes/projects.py:128`)
auto-suggests a group when several registered repos share a GitHub org. The code is already
drifting toward "owner". Only `cli/wizard.py:55`'s single-repo path still says "not an org".

## 8. What is genuinely new vs. already there

| Piece | Status |
|---|---|
| Investigation record + tab | **net-new** |
| Purpose vocabulary | **net-new** as a valid value set; mechanism exists |
| Purpose tags on questions | **net-new** — 41 questions to tag in `docs/dr-egeria/resource_questions.csv`, then regenerate. Blocks all of §3. |
| Create a new Egeria Project | **net-new** (Part 5 built search/bind only) |
| RFA gating on Purpose | **net-new** rule; RFA plumbing exists |
| Standard-definition + verdict layer | **net-new** (§4) |
| Perspective | exists — but **two incompatible vocabularies** (12 Title-Case on questions, 5 snake_case on analyses) and zero dispatch discrimination; demoted to a secondary ranking axis (§3) |
| Funnel stages | exist — become the *output* of Purpose × Perspective |
| Per-check storage | exists — `project_analysis_findings` (`registry.py:908-921`) is already
  one row per check with caller-supplied `kind`/`check_name`/`label`. **No schema change.** |
| Change detection | exists — `notification_detector.detect_change()`
  (`notification_detector.py:99-113`) is generic across any `kind`. Trigger is "changed at
  all"; extending to "went from pass to fail" is small. |
| Membership | Egeria relationships (§6) — local table only for unbound investigations |

## Decisions taken 2026-08-24

Recorded so they are not silently reopened. All seven of this doc's original open questions
were resolved in the design session:

| Question | Decision | Where |
|---|---|---|
| Name for the train-a-person purpose | **`Learn`** — "Understanding" is an intent and the collision would be permanent | §2 |
| Renames in this doc or their own? | **This doc.** Framing does not depend on the rename landing first, but they share the same UI surfaces (§6) and splitting them splits the tripwire from its context | §7 |
| GitHub `node_id` keying now or later? | **Now.** `login` is a display value, never the key | §7 |
| Is `ResearchQuestion` useful? | **Yes** — complementary to the `Question` GlossaryTerm catalog, not a replacement; no migration | §3 |
| Does membership carry forward on charter reuse? | **Yes for `Certify`/`Maintain`, no for `Explore`/`Select`** | §6a |
| Do `excluded` decisions pre-seed the successor? | **Yes, with timestamps** — a dated reviewable default, never a permanent verdict | §6a |
| Does Purpose actually discriminate? | **Unproven — tag a subset and measure first** | below |

## Purpose measured (2026-08-24) — it works, but the bar I set was wrong

Tagged the 16 of 41 repo questions that have non-empty `analysis_ids` (the only ones that can
move the metric — a question reaching no analysis cannot make a Purpose reach one). Method
fixed before looking at any outcome: tag every Purpose that genuinely applies to each question,
no revision after seeing results. Working file kept out of the repo; the tags are reproduced in
the backlog item when this is picked up.

### The §3 bar fails — and is unachievable by construction

Every one of the 8 purposes reaches **zero** analyses that no other purpose reaches. Identical
to Perspective's result. But before concluding the axis is wrong, the ceiling:

- `repo_conventions` is reached by **7 of 16** questions; `repository_health` by 4. Two
  analyses absorb 11 of 16.
- Only **6 of 10** analyses are reachable from exactly one question. So **at most 6 of 8
  purposes could ever hold a unique analysis, under any tagging whatsoever.**

The bar — *"each value must reach at least one analysis no other value reaches"* — was invented
in an earlier draft of this doc and is not satisfiable by this catalog. Worse, it encodes a
false premise: purposes genuinely overlap. A published security analysis is honestly relevant
to both `Assess` and `Certify`, and demanding exclusivity demands something untrue of the
domain. **The bar was wrong, not the axis.**

### On the fair test, Purpose clearly beats Perspective

Both measured over the same 16 questions, same 10 analyses:

| | mean pairwise Jaccard overlap | strict-subset (nested) pairs | median set size |
|---|---|---|---|
| **Purpose** (8 values) | **0.22** | **6** | 3.5 of 10 |
| **Perspective** (11 values) | 0.37 | 18 | 4.0 of 10 |

Purpose sets overlap **40% less** and are nested **three times less often**. That is the
property dispatch actually needs: not exclusivity, but that changing the axis value changes
the working set in a way a user would notice.

### What this changes in the design

**Purpose ranks and prioritises; it does not exclude.** Dispatch orders the analysis list and
picks what runs by default — it never hides an analysis. This is the honest reading of a 0.22
overlap: the sets differ usefully but share a common core, so the top of the list should change
with Purpose while the tail stays reachable. A filtering-out model would be over-claiming what
the data supports, and would hide the shared core that every purpose legitimately needs.

Perspective keeps its existing job (display filtering) and is the secondary ordering key, per §3.

### The check-granularity join — BUILT 2026-08-24

`repo_conventions` was never one check — `ingestion/repo_conventions_parser.py:97-179` emits
five. The discrimination existed in the question corpus and was destroyed by the mapping.
The CSV had in fact *already* recorded the checks in prose (`repo_conventions (deployment_docker
presence signal)`); the pipeline just dropped them on the floor.

What landed:

- **`configdata/check_registry.yaml`** — new. Declares the 28 checks across 8 analyses that
  were previously undeclared anywhere. Every analysis in `analysis_catalog.yaml` must appear in
  exactly one of three sections (`analyses` with its checks / `whole_analysis_only` /
  `instance_keyed_not_checks`), so "absent" is never ambiguous between *has no checks* and
  *nobody looked*. Validated against the live registry: 255 `(kind, check_name)` pairs over 60
  surveyed repos, zero live checks missing.
- **`answering.checks`** on every question entry — `analysis_id:check_name` refs, alongside
  `analysis_ids` rather than replacing it. A check ref implies its analysis, added automatically.
- **Generator validation** — an unknown check ref now raises rather than being silently dropped,
  because a typo'd ref is otherwise indistinguishable from an untagged question, which is the
  exact failure this join exists to remove.
- **9 CSV rows tagged**, only where the CSV already asserted the check in prose. Nothing invented.
- **`tests/test_check_registry.py`** — 7 guards against silent drift.

**Two traps found while building it**, both of which would have produced a plausible-looking
broken join:

1. **`findings_kind` is not always the analysis id.** `security_scan` writes findings under
   `security_hygiene`, `documentation_coverage` under `documentation`, `sub_resource_survey`
   under `repo_sub_resource_survey`. A join built on the natural assumption breaks on exactly
   the analyses with the most checks. The registry makes it explicit per entry.
2. **`check_name` is overloaded.** For `architecture_recovery` (70 distinct values) and
   `repo_sub_resource_survey` (86) the column holds an *instance key* — a component identity, a
   coupling shape, a directory path — not a check identifier. Treating those as checks would
   pollute the vocabulary with directory names. They are excluded by name and by test.

**Result of the re-measurement** (same 16 questions, same tags):

| | distinct targets | mean pairwise overlap | nested pairs |
|---|---|---|---|
| analysis granularity | 10 | 0.22 | 6 |
| **check granularity** | **15** | **0.14** | **5** |

Overlap drops by a further **36%** — Purpose sets are now less than half as overlapping as
Perspective's 0.37. The exclusivity bar still fails 7 of 8, which remains the right outcome:
that bar was unachievable and encoded a false premise (§ above). What matters is that Purpose's
sets are now sharply distinct, which is what ranking needs.

**Only 9 of 41 questions carry check refs so far** — the remaining tagging is mechanical and
should follow the same rule: add a ref only where the CSV already asserts the check, never
invent one to improve the metric.

### All 41 tagged (2026-08-24) — the subset held, with one caveat

Tagged every question via a new `Purposes` column in
`docs/dr-egeria/resource_questions.csv`. Measured at check granularity over the
whole corpus:

| | mean pairwise overlap | nested pairs | passes the exclusivity bar |
|---|---|---|---|
| **Purpose** (8 in use) | **0.14** | **5** | 1/8 |
| Perspective (12) | 0.22 | 9 | 0/12 |

Purpose is ~36% less overlapping and half as nested. The 16-question subset
result held at full scale, which is the thing that needed checking — the
sample was representative, not lucky.

**The caveat, stated plainly so 0.14-on-41 is not read as stronger than it is:**
only 16 of 41 questions have any analysis or check target at all. The other 25
are `direct` (11), `gap` (8), `human` (5), `chart` (1) and contribute nothing to
reach. So the numbers are still driven by the same 16 questions. Full tagging
**confirmed** representativeness and readied the tags for when coverage
improves; it did **not** add independent evidence.

**Two findings from tagging that are about the corpus, not the axis:**

- **`Remediate` and `Attest` have zero questions.** Nothing asks about fixing
  what was found, or about publishing an attestation. The question set predates
  both purposes — they came out of the enterprise-scorecard discussion. Real
  purposes, currently unserved by any question.
- **`Select` is tagged on 25 of 41 (60%).** The corpus was authored around
  "should we use this repo?", so `Select` dominates and therefore discriminates
  least. The UI should not lean on it as a distinguishing choice.

**Implementation note worth not rediscovering:** both CSV scripts identify
Perspective columns *by elimination* — anything absent from
`NON_PERSPECTIVE_COLUMNS` (`csv_to_question_catalog_yaml.py`) or
`OPTIONAL_LEAD_COLUMNS` (`csv_to_dr_egeria_questions.py`) silently becomes a
phantom Perspective on every row. That is why Purpose is one semicolon-separated
column rather than ten X-marked ones in the Perspective style: ten columns would
be ten chances for a typo'd header to corrupt the Perspective vocabulary.
`tests/test_check_registry.py` asserts exactly 12 Perspective terms as the
tripwire. The Dr.Egeria markdown output was diffed before and after and is
byte-identical.

### The real limiter, for whoever picks this up

The analysis catalog is coarse relative to the question corpus — 16 questions routing to 10
analyses, two of which absorb two-thirds of them. Any dispatch axis will be blunt until either
the join moves to check granularity or the bundled surveyors are split. That is a property of
the catalog, not of Purpose or Perspective, and no amount of re-tagging will fix it.

