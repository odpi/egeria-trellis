# Four gap analyses: secret handling, telemetry, CLA/DCO, SLA content

**Status:** live. Design for four gap analyses; code and tests cite it.

Design only — no code in this note. Scopes four `STEP_REGISTRY` / `ANALYSIS_KINDS` entries that
do not exist today, tracked as open items in `docs/open-stack-checklist.md` ("From the design
note — nothing built": *"Four GAP questions with no analysis at all: secret handling,
telemetry/phone-home detection, CLA/DCO provenance, SLA/availability content"*) and named in
`docs/survey-model.md (§1):65,98` as the steps `Security_Assessment`
wants once they exist. They also correspond to four `GAP` rows in
`docs/dr-egeria/resource_questions.csv:31-34` and `resource_explorer/configdata/question_catalog.yaml:581,597,613,628`.

## Why this matters more than a normal backlog item

`resource_explorer/surveyors/sub_surveyors/security_hygiene.py` is named `SecurityHygieneSurveyor`
today, but the class comment (lines 44-52) records that it used to be called `SecuritySurveyor`,
and the `analysis_catalog` id it still answers to is `security_scan`
(`repo_survey_definition_adapter.py:2638`). Reading the surveyor itself: it checks for exactly
three things — a `SECURITY.md`/`SECURITY.rst` file, a CI config file, and a `LICENSE` file
(`security_hygiene.py:20-35`, the `_SECURITY_FILES`/`_CI_INDICATORS`/`_LICENSE_FILES` constants).
It does not read file *content* at all beyond matching filenames and paths already in
`project_file_inventory`. There is no secret scanning anywhere in this surveyor, and no other
surveyor in `STEP_REGISTRY` does it either — confirmed by grepping the whole package for
`gitleaks`/`trufflehog`/`detect-secrets`/`secret.?scan`: every hit is either GitHub's
*secret-scanning **toggle** state* (`security_features.py`, `stats_fetcher.py` — whether the
repo owner turned the GitHub feature on, not a scan RE performs) or a `GAP` note.

So a user who runs "Security Scan" and sees `security_scan` in the security family, next to
`cve_scan` and `cii_badge`, has no way to tell from the UI that it never looked at file content for
secrets. This is exactly the shape `find-absence-as-answer` and this codebase's own `step_outcome.py`
exist to prevent: **a step whose name promises more than it checks is a confident wrong answer
wearing a correct one's UI.** The rename already fixed the class name; it did not, and could not by
itself, fix the missing capability. This design is that fix.

## What I found that changes the premise

**The precondition-skip mechanism this design leans on is not merely proposed — it is already
built and wired.** `docs/Backlog.md`'s "Survey execution" entry (§"No conditional execution of
survey steps", dated 2026-09-01) read as though `requires_context` is undispatched. It is not, as
of the current checkout: `resource_explorer/surveyors/survey_orchestrator.py:220-240` calls
`step_preconditions.evaluate()` for every step whose `StepInfo.requires_context` is non-empty, and
on an unmet precondition emits a `ClassificationAnnotation` carrying
`candidate_classifications=[result_status.SKIPPED_BY_DESIGN]` and
`json_properties=step_preconditions.skip_status(...)` — never silence, never a plain skip. `cve_scan`
already declares `requires_context={"has_versioned_dependencies": ...}`
(`repo_survey_definition_adapter.py:605-610`) and exercises this path in production.
**Independently verified by the coordinating session, and the Backlog entry — written by that same
session — has now been corrected in place**: it was stale relative to this build, not describing a
richer unbuilt mechanism. The four `PRECONDITIONS` entries currently registered
(`step_preconditions.py:75-84`: `has_dependencies`, `has_versioned_dependencies`,
`has_file_inventory`, `has_code_symbols`) are the complete list as of this checkout; a
`first_party_code`-style context fact (`docs/question-answering-and-context.md (§9)` §4) is a real gap in
that list, not a gap in the dispatch mechanism itself. **The plumbing this design needs already
exists for the "reads a table that might be empty" shape**, and the only new work on that axis is
adding `has_contributing_doc`-style entries to `PRECONDITIONS` if a given analysis needs one not
already there.

This is good news for scope: none of the four analyses below need new orchestrator machinery, only
new `PRECONDITIONS` checks (where they need one beyond the existing four) and new `STEP_REGISTRY`
/ `ANALYSIS_KINDS` entries.

**None of the four is partially implemented as a surveyor.** I searched for scanner libraries,
regex patterns matching secret shapes, `requests`/`socket`/`urllib` call-site scanning, `CLA`,
`DCO`, `bot: cla`, and `SLA`/`service level`/`uptime commitment` across `resource_explorer/` and
found nothing beyond the GAP notes already cited. `repository_health`'s trend chart
(`question_catalog.yaml:394`) is offered as a partial, non-content answer to the SLA/availability
question — a trend of stars/commits is not an SLA — and the note there already says so ("GAP if
SLA-doc content read is needed instead").

## Shared design rules these four must follow

Restating what the constraints section asked for, because every analysis below is built to it:

1. **`NEVER_RUN` / `SKIPPED_BY_DESIGN` / `NOTHING_FOUND` are three different renders, not three
   names for zero.** (`result_status.py:33-40,68-72`, `step_outcome.py` docstring.) A precondition
   miss emits `SKIPPED_BY_DESIGN` via `step_preconditions.skip_status()`. A conclusive scan with no
   hits emits `StepOutcome(NO_SIGNAL, known_positive=True, ...)` via `step_outcome.no_signal()`,
   which requires proof the method could have found something. Anything else — the step never
   dispatched, or dispatched but the registry has no row for this slug at all — must not be
   rendered by any of these four as "clean".
2. **`known_positive` is the whole game for these four.** `step_outcome.py`'s constructor-enforced
   rule ("no_signal requires known_positive=True... without a known-positive check a zero is
   indistinguishable from a broken method") is exactly the CI Security-Scan failure mode restated
   as an invariant. Every analysis below states, explicitly, what its known-positive check is —
   the thing that proves the scan actually ran over real content rather than an empty or absent
   input.
3. **Cost tiers are declared, then measured, not measured then declared.** Following
   `step_cost_observer.py`'s own lesson (three real mis-declarations in one week, none catchable by
   a test because each was a declaration disagreeing with behaviour) — the tiers below are informed
   guesses from what data each analysis reads, and each is flagged **VERIFY** with what to check
   after the first live run.
4. **A confident wrong answer is worse than no answer.** Each analysis has an explicit "must not
   claim" list. These are not hedges — OpenSSF Scorecard's own failure mode, which
   `foss_scorecard.py`'s docstring calls out by name ("Scorecard awards a low score to a check it
   could not evaluate... That is the failure this codebase keeps removing"), is the shape to avoid:
   never fold "we didn't check" and "we checked and it's clean" into the same score or the same
   green checkmark.
5. **The analysis id and the finding kind are two different strings, and conflating them is a
   live, twice-realised bug in this codebase — not a hypothetical risk.** See the dedicated
   subsection immediately below; every analysis in this design states both explicitly and neither
   is a near-homograph of an existing one.

---

## 0. A live bug this design must not repeat: analysis id vs finding kind

`security_scan` is the `analysis_catalog` **id** `SecurityHygieneSurveyor` answers to
(`repo_survey_definition_adapter.py:2638`). The **finding kind** that surveyor actually writes to
`project_analysis_findings` is `"security_hygiene"` (`security_hygiene.py:270`, the literal
argument to `registry.upsert_finding`). These are two different strings for the same underlying
work, and they diverged the moment the class was renamed from `SecuritySurveyor` without a matching
rename of the *id* — a decision documented at the time in `security_hygiene.py:44-58` as
deliberate ("Identifiers ... deliberately did NOT change — only the Python class/file name, to
avoid breaking existing schedules/data").

That divergence has already produced two independent, real bugs in reducers that read findings by
kind rather than by id:

- **`security_summary.py`** originally queried by the id string and got zero rows for every repo
  on its first run — documented in its own file: *"the *analysis id* is `security_scan`; the
  **findings kind** it writes is `security_hygiene` — they diverged when SecuritySurveyor [was
  renamed]"* (`security_summary.py:49-53`). Fixed there; `INPUT_KINDS` now correctly lists
  `"security_hygiene"` (`security_summary.py:59-60`).
- **`foss_scorecard.py`** had the *same* mistake, independently, and it did not inherit
  `security_summary`'s fix. Measured 2026-09-01: querying by kind `"security_scan"` matched 0 rows
  across 0 repos, while `"security_hygiene"` — the kind `SecurityHygieneSurveyor` actually writes —
  had 252 `security_policy` rows recorded. Every one of `foss_scorecard`'s 155 `security_policy`
  check verdicts on record was `"unknown"`, and its explanation text read *"Neither security
  analysis has run for this resource"* about repos where it plainly had — the confident-wrong-
  answer failure mode this whole design responds to, produced by a naming collision rather than a
  missing capability. Now fixed in this checkout (`foss_scorecard.py:340-353` documents the
  correction and the measurement), but the fix did not travel from `security_summary` to
  `foss_scorecard` on its own — each reducer independently rediscovered the same bug.

**The lesson for this design, stated as a rule rather than a warning:** every one of the four new
analyses above declares *both* its `analysis_catalog` id and the finding `kind` string its surveyor
passes to `upsert_finding`, in this note and in the surveyor's own module docstring, and the two
are chosen to be visibly different rather than merely technically distinct — `secret_scan` (id) /
a finding kind that says what was written (e.g. `secret_scan_findings` or similar, not a bare
repeat of the id), and likewise for the other three. A reducer written against any of these four
in the future should not be able to make the `foss_scorecard` mistake by pattern-matching the
"obvious" name. Naming them apart once, here, is cheaper than a third independent rediscovery.

---

## 0b. One provider concept, not a secrets-only one — and IN SCOPE for three existing analyses

The project owner's review reframed §1's vendoring decision, and the reframing is the more important design
input than the decision itself: *"vendored analyses have provenance and reputation — if we rolled
our own, who would believe us?"* For this whole class of analysis, **the standing of the answer is
part of the deliverable, not a footnote to its accuracy.** A user does not just want "clean" or
"not clean" — they want to know *whose judgement* that is, so they can weigh it the way they
already weigh a vendor's SOC2 report differently from an internal audit.

Standards bodies and organisations disagree about what the right ruleset/scheme even is, so the
mechanism has to be **transparent and swappable** — an organisation with its own compliance
requirements must be able to point this codebase at a different ruleset without touching RE code —
and **checking for ruleset updates is its own recurring activity**, separate from running the scan,
because a stale standard is this codebase's signature failure shape in a new guise (§0c and every
"must not claim" section in this doc restate the same point from a different angle).

**The resolution: the ruleset/scheme is DATA, not code.** Identity, version, and source recorded in
every finding; the provider is swapped by configuration, not by editing a surveyor.

**Confirmed in scope: `cve_scan`, `license_classification`, and `foss_scorecard` adopt this
concept, not merely could.** Building a fourth secrets-only version of this idea would be exactly
the kind of repeated mistake this codebase keeps finding and removing — after the analysis-id/kind
duplication in §0 and the independently-invented outcome vocabularies `step_outcome.py`'s own
docstring lists. What follows is the adoption design for each, not a hypothetical.

**The one shared shape, stated once so all four adoptions use the same fields:**

    provider_name        — e.g. "gitleaks", "osv.dev", "re_foss_scorecard", "spdx_license_list"
    provider_kind        — "vendored_ruleset" | "live_queried_api" | "re_authored_scheme"
    version_or_as_of      — a pinned version/commit for a vendored ruleset; a query timestamp for
                            a live API; a scheme-version tag for an RE-authored table
    source_url            — where a reader can go to see the standard itself
    staleness_policy      — differs BY KIND: a vendored ruleset needs a periodic update-check
                            (§1's `ruleset_freshness`, modelled on `security_summary`'s
                            `summary_freshness`); a live API is "fresh" by construction per query
                            but its *coverage* can lag reality (OSV can simply not know about a new
                            advisory yet — a different failure shape than staleness, not solved
                            here); an RE-authored scheme needs periodic human review against
                            whatever it is shaped after, since nothing pulls updates for it
                            automatically.

**The one guardrail every adoption must honour, non-negotiably: no finding KIND string or
`check_name` changes.** These four fields are added to each row's `detail` dict — a pure addition
— never a rename of `kind`/`check_name`/the analysis id. This is not caution for its own sake: it
is the specific, twice-realised failure mode §0 documents. `foss_scorecard` reads five other
analyses' findings **by kind** — `ci_quality`, `security_hygiene`, `security_features`, `cve_scan`,
`supply_chain` (`foss_scorecard.py:353-354`) — and `security_summary` reduces over **eight**
`INPUT_KINDS` (`security_summary.py:59-67`), withholding a verdict below `MIN_INPUTS_FOR_VERDICT =
4` (`security_summary.py:73`). Either reducer is a live downstream consumer of the exact kind
strings these three analyses write today, and a provider-adoption change that touched a kind name
would not fail loudly — it would silently narrow `foss_scorecard`'s inputs or drop
`security_summary`'s coverage, exactly the "matched 0 rows and reported *neither analysis has run*"
shape §0 already found once and fixed once. Provider adoption is additive-fields-only for exactly
this reason.

### `license_classification` — adopt first; safest

**Today.** *"A static SPDX-id -> risk-tier lookup table, not an external tool integration"*
(`license_classifier.py`, module docstring). Two different things are bundled and only one is a
real external standard: the **vocabulary** (SPDX license identifiers) is versioned by the SPDX
License List; the **risk-tier mapping** (`_PERMISSIVE`/`_WEAK_COPYLEFT`/`_STRONG_COPYLEFT`/
`_SOURCE_AVAILABLE`, `license_classifier.py:31-58`) is RE's own editorial judgement, not any
external body's risk taxonomy. Neither is recorded with a version anywhere today.

**What changes.** Two provider records, not one, because two different standards are bundled here
and conflating them would misattribute RE's own judgement to SPDX's authority (the "borrowed
credibility" risk §0b's shared-shape intro names). Add to each persisted row's `detail`:
`spdx_list_provider={"provider_name": "spdx_license_list", "provider_kind": "re_authored_scheme"
[vocabulary only — SPDX itself is external but this table's *use* of it is not queried live],
"version_or_as_of": <the SPDX List version this table's identifiers were last checked against>,
"source_url": "https://spdx.org/licenses/"}` and `risk_scheme_provider={"provider_name":
"re_license_risk", "provider_kind": "re_authored_scheme", "version_or_as_of": "v1", "source_url":
""}` — the empty `source_url` on the second is itself informative: there is no external page to
point to for RE's own risk judgement, and saying so plainly is more honest than a placeholder link.

**What a reader gains.** Today "License: GPL-3.0" and a risk tier of "strong copyleft" read as one
fused fact. After adoption, a reader can tell that the *identifier* GPL-3.0 is SPDX-standard (high
confidence) while the *label* "strong copyleft" is RE's own call (open to disagreement, and now
visibly attributable rather than silently presented with SPDX's borrowed authority). If RE's risk
table is ever revised — a real, plausible event, since it is hand-maintained — `risk_scheme_
provider.version_or_as_of` changing from `"v1"` to `"v2"` is now a fact a reader can see, where
today a revised table would change a repo's reported risk tier with nothing in the record
explaining why.

**Migration risk: low.** No live network call, no downstream reducer reads this surveyor's
*internal* detail schema (only its `kind`/`check_name`/`label`, per the guardrail above, which this
change does not touch), and the surveyor itself is ~60 lines of static tables
(`license_classifier.py`). This is why it goes first: the blast radius of a mistake here is the
smallest of the three, and doing it first lets any friction in the general shape (field names,
where `detail` gets merged, how `upsert_finding` handles it) surface against the lowest-stakes
analysis before touching the two that matter more.

### `foss_scorecard` — adopt second

**Today.** `CHECKS` (`Check` dataclass instances) is pure Python — no external file, no version
identifier (`foss_scorecard.py`). Its own docstring already states it deliberately diverges from
OpenSSF Scorecard's real scoring (*"the score is not comparable with a published OpenSSF score...
it is trying to be true about what was measured"*), so this is squarely an `re_authored_scheme`,
the same `provider_kind` as half of `license_classification`'s adoption above.

**What changes.** Add one provider record to the `foss_scorecard` finding kind's persisted rows:
`{"provider_name": "re_foss_scorecard", "provider_kind": "re_authored_scheme", "version_or_as_of":
"v1", "source_url": ""}` — naming the current `CHECKS` shape `v1` costs nothing today and is what
lets a future `v2` (different weights, a real `scorecard`-CLI-backed check added alongside the
hand-computed ones) be told apart in stored history rather than silently reinterpreting old rows
under new rules.

**What a reader gains.** A `foss_scorecard` score is currently presented with no more provenance
than "computed"; a reader cannot tell a score computed under today's `CHECKS` from one computed
after a future rebalancing of the weights without reading commit history. Naming and versioning the
scheme makes that visible in the data itself, the same win `license_classification` gets for its
risk table — and this surveyor is the more consequential one to fix, since it is the analysis
`docs/survey-model.md (§1)` names as the aggregate security-family score.

**Migration risk: low-to-moderate, and this is the right moment to also lock in what §0 found
today.** `foss_scorecard` is the reducer that had the live `security_scan`/`security_hygiene`
kind-matching bug until today (`foss_scorecard.py:340-353`) — fixed, but discovered by measurement,
not by a test. Bundling the provider-field addition with a regression test that pins its five
input-kind reads (`ci_quality`, `security_hygiene`, `security_features`, `cve_scan`,
`supply_chain`) against known-non-zero row counts is cheap to add at the same time and closes the
exact gap that let the id/kind bug go unnoticed for as long as it did — not part of this design's
scope to write, but flagged as the natural companion change for whoever implements this adoption.
No live network call is involved in this adoption (the provider fields are metadata about a local
scheme, not a fetch), so the risk here is entirely about downstream reads — which the guardrail
above (no kind-string changes) is built to prevent, and which the bundled test would catch if the
guardrail were violated by mistake.

### `cve_scan` — adopt third, and pin before touching it

**Today.** Genuinely queries a live, external, real standard — OSV.dev, via `_OSV_BATCH_URL =
"https://api.osv.dev/v1/querybatch"` (`cve_scan.py:47`) — but records neither a provider name nor
an as-of instant in any persisted finding (grepped for `provider`/`ruleset_version`/`as_of` in
`cve_scan.py`: none). Its `provider_kind` is `live_queried_api`, the one kind among the four where
"version" cannot mean a pinned release — OSV's advisory database changes continuously, so the
honest unit of provenance here is **the as-of instant of the query**, not a release tag.

**What changes.** Add to each persisted row: `{"provider_name": "osv.dev", "provider_kind":
"live_queried_api", "version_or_as_of": <surveyed_at — the run's own shared timestamp, already
threaded through this surveyor via `accepts_surveyed_at`>, "source_url":
"https://osv.dev"}`. Every field this needs already exists in scope at the point the finding is
built; this is a detail-dict addition, not new plumbing.

**What a reader gains.** "No CVEs found" and "no CVEs found, queried against OSV.dev as of
2026-09-01T14:00Z" are different claims in exactly the way this whole design exists to insist on —
the second says what was checked and when, so a reader six months later can judge for themselves
whether the check is stale, rather than trusting an unscoped "no CVEs" that reads the same on day
one and a year later.

**Migration risk: the highest of the three, named honestly rather than assumed away.** `cve_scan`
is not a stable, low-traffic analysis — it is a component that **today, in this measurement round,
went from producing nothing to producing 14 findings on `egeria_git` and took `security_summary`
from 7 of 8 `INPUT_KINDS` covered to 8 of 8.** A refactor that breaks it costs more than the
provenance abstraction buys: `security_summary` would silently drop back to 7/8 coverage (still
above `MIN_INPUTS_FOR_VERDICT=4`, so it would keep issuing a verdict — the kind of coverage
regression that does not fail loudly, per the guardrail's whole point). **Before touching
`cve_scan.py` for this adoption:**

1. Pin a regression fixture asserting today's exact finding shape on a known repo (`egeria_git`, 14
   findings, per this measurement) — so the additive change can be verified against a concrete
   baseline, not merely asserted safe by code inspection.
2. Make the change additive-only: append `provider_name`/`provider_kind`/`version_or_as_of`/
   `source_url` to the `detail` dict already being built at each finding's construction site; touch
   no matching, batching, or version-normalisation logic (`normalise_version`, `query_osv`, the
   ecosystem map) at all.
3. Re-run the pinned fixture after, and confirm `security_summary`'s coverage for the same repo is
   still 8 of 8, not silently 7 of 8 — the one number that would prove the guardrail held.

Doing this analysis last, after the same field-addition pattern has already gone smoothly through
`license_classification` and `foss_scorecard`, means any friction with the general shape is found
and resolved against lower-stakes code before it is applied to the analysis that just, today, made
`security_summary` complete for the first time.


## 0c. Evidence shape: summary annotations linked to evidence via `AnnotationExtension`

The project owner pointed at Egeria's Annotations model (https://egeria-project.org/types/6/0610-Annotations/).
The coordinating session verified against the Java rather than the page: `OpenMetadataType.
java:6010` defines the **`AnnotationExtension`** relationship, GUID
`605aaa6d-682e-405c-964b-ca6aaa94be1b`, model 0610, *"Additional information to augment an
annotation."* Adjacent at `:6020` is **`AnnotationReview`** (model 0612), *"the results of a
stewardship review of an annotation."* Checked independently here, against RE's own source:
`AnnotationExtension` appears nowhere in `resource_explorer/` — genuinely zero references.
`AnnotationReview` appears exactly once, and it is telling — named in
`resource_explorer/surveyors/surveyor_design.md`'s original "What We Learned from Egeria Area 6"
table (*"Steward assessment: approve, invalidate, or convert an annotation"*) as a known Egeria
type, and never implemented since: no code reference anywhere. So this is not an unknown corner of
the model — it was read and recorded at design time and then not built, which is a slightly
different, and slightly more pointed, fact than "nobody knew about it." Either way: real, unused
platform capability, not a convention this design is inventing.

**What it buys.** Every analysis in this design produces one or many matches (a secret finding per
hit, a telemetry call-site per hit, a CLA/DCO check per document). Today's only shape for that is
`SurveyResult.annotations` — a flat list, each independently persisted as `ReportedAnnotation`
under one `SurveyReport` (`egeria_publisher.py:564`, `build_annotation_body`'s hard-coded
`"parentRelationshipTypeName": "ReportedAnnotation"`, `annotation_props.py:187-198`). A reader
today either gets one finding with a detail blob crammed full of every match, or fifty independent,
unranked annotations with no stated relationship to each other. `AnnotationExtension` gives a
third shape: **one summary annotation, with each evidence annotation linked to it as an
extension** — the summary is the headline claim, the evidence is inspectable independently, and
the link between them is a real, modelled relationship rather than an implicit "these were all
written in the same run" convention.

**The shape, applied to each of the four analyses below (detailed per-analysis in §1-§4's own
"Evidence shape" subsections):**

    ONE summary annotation   — the outcome (`RECOVERED`/`NO_SIGNAL`/`UNVERIFIED`/
                               `SKIPPED_BY_DESIGN`), the provider/ruleset identity (§0b), the count,
                               and (for §1) the fixture self-test result. This is what a reader sees
                               first and it is a claim about THE RUN, not about any one match.
    MANY evidence annotations — one per match (a secret hit, a telemetry call site, …), linked to
                               the summary via AnnotationExtension. Each carries only what is
                               specific to that one match — path, line, rule id, excerpt.

**Constraint 1 — the summary must not become the only readable thing.** Linking evidence does not
relieve the summary of the absence-distinction this entire design is built around. A reader who
never follows a single `AnnotationExtension` link must still be able to tell "we looked and found
nothing" from "we could not look" from the summary alone — `outcome`/`outcome_cause`/
`known_positive` (`step_outcome.py`'s `.as_row()`) belong on the summary annotation itself, not
only inferable by counting how many evidence annotations exist (zero evidence annotations is
ambiguous between "scanned, found nothing" and "never scanned" unless the summary's own outcome
field disambiguates it — which is exactly `step_outcome.py`'s constructor-enforced rule, restated
at the relationship-modelling layer rather than the Python layer).

**Constraint 2 — this is new plumbing in `EgeriaPublisher`, not free, and not designed here.**
`EgeriaPublisher.publish()`'s existing annotation path
(`egeria_publisher.py:564-586`, `enqueue_annotations`/`publish_annotations`) creates each
annotation independently and parents it to the report; nothing in the current outbox/direct-publish
path creates a second relationship between two annotations. Adding `AnnotationExtension` support
needs, at minimum — named as a prerequisite with an honest cost, not designed here:

- **A two-pass creation order.** Both ends of a relationship need a GUID before the relationship
  can be created, so evidence annotations must be created (and their GUIDs known) before the
  summary annotation's extension links can be written — the reverse of assuming a flat list can be
  published in one pass, which is what every existing surveyor does today.
- **A uni-link vs multi-link decision, made explicitly.** This project's own recorded lesson
  (`reference_egeria_dedup_and_link_patterns` — `attach()` upserts for a uni-link but silently
  duplicates for a multi-link) is directly on point: an `AnnotationExtension` from one summary to
  many evidence annotations is inherently multi-link on the summary's side, so a naive retry on
  outbox replay could duplicate links the same way a naive multi-link `attach()` already does
  elsewhere in this codebase. The outbox mechanism (`egeria_outbox.py`) already has replay-safety
  patterns for exactly this shape (`egeria_outbox.py:139-143` discusses relationship replay safety
  for a different relationship); whoever builds this should study that pattern rather than
  reinventing idempotency for `AnnotationExtension` specifically.
- **Failure isolation matching `publish()`'s existing contract.** A publish problem must not turn
  an otherwise-successful survey into a reported error (`egeria_publisher.py:555-559`'s existing
  rule for the annotation path generally) — an `AnnotationExtension` link that fails to write must
  degrade to "evidence exists but is not linked from the summary", not to losing the evidence
  annotation entirely or failing the whole publish.

This is real, non-trivial new work, and it is a prerequisite for the evidence-linking shape any of
the four analyses below use in production. Nothing in §1-§4 below requires it to be built before
those analyses can run and persist findings locally (`project_analysis_findings` already holds
per-match rows regardless of Egeria publication) — it is specifically the Egeria-side presentation
of "one summary, many linked evidence annotations" that waits on this plumbing. Flagged as a
sequencing dependency in the Sequencing section below.

**Why this also protects the deferred third reporting level, without building it.** the project owner named three
reporting levels — overall finding/score, evidence, and improvement suggestions keyed off whether
the reader maintains or consumes the artifact — and confirmed level 3 is out of scope here,
already backlogged (`docs/Backlog.md` "Reporting levels", written up in detail there; not repeated
in full here). The one requirement from that discussion that belongs in this design: **finding,
evidence, and any future recommendation must be separately addressable.** Evidence flattened into a
summary string cannot be re-read by a recommender built later without re-running every survey —
`AnnotationExtension` is exactly what keeps that door open, because it makes the evidence
independently addressable (its own GUID, its own queryable annotation) rather than baked into the
summary's prose. Each analysis below is designed to that requirement already, by virtue of using
the summary-plus-linked-evidence shape rather than a single flattened finding.


## 1. Secret handling (`repo_secret_scan`)

**RESOLVED (2026-09-01, the project owner): the secret-scanning ruleset is vendored, not hand-rolled.** *"use a
standard approach - so perhaps vendored?"* — the honest-limits section of the previous draft
flagged the choice between a vendored ruleset (e.g. gitleaks-shaped) and a small hand-maintained
pattern list as undetermined and materially affecting both compute cost and false-positive rate.
It is now decided: **vendored**, and that choice reshapes the design below rather than just
renaming the pattern source — the four points that follow are the actual consequences, not a
cosmetic swap. This is the concrete, `vendored_ruleset`-flavoured instance of the general provider
concept §0b generalises to `cve_scan`/`license_classification`/`foss_scorecard` as well; this
section's `ruleset`/`ruleset_version` fields are this provider kind's names for §0b's shared
`provider_name`/`version_or_as_of`, kept as the more natural names for a pinned ruleset rather than
forcing the generic field names into every analysis's prose.

**What it measures.** Whether the repository's tracked file content, at the current HEAD snapshot,
matches any rule in the vendored ruleset — API keys, private-key blocks, cloud-provider secret
shapes, populated `.env` values, and whatever else that ruleset's authors maintain. Deliberately
*not* git history: a secret removed in a later commit but still reachable via `git log -p` is a
real, distinct, and much more expensive finding (needs `VIEW_HISTORY`, i.e. `git_clone_root`, and
a full log walk), and conflating "not in HEAD" with "never committed" would be exactly the
false-clean-scan bug this design exists to prevent. Scope this analysis to HEAD-only; a
`repo_secret_scan_history` step is a legitimate future sibling, not a merged responsibility.

**What "vendored" means concretely, and what it costs.** A vendored ruleset is either invoked as
an external tool (shell out to a `gitleaks`-shaped binary, if present on the host, over the
extracted zipball) or pulled in as a rules-only data file (a ruleset repo's `.toml`/`.yml` rules
committed or vendored into this package, read and matched by this codebase's own regex engine
without adopting the tool's runtime). Both are legitimate; both have a real cost this design must
name rather than wave past:

- **Licence compatibility.** The ruleset's licence (and the scanning tool's, if the binary path is
  chosen) must be checked against this project's own before either is vendored — this is exactly
  the kind of fact `license_classification`'s risk-tier work already exists to reason about for
  *dependencies*; a vendored ruleset is a dependency in every sense that matters here, even if it
  never appears in a package manifest.
- **Update cadence.** Vendoring freezes a ruleset at the version pulled in; it does not track
  upstream automatically. This is the direct lead-in to point 2 below (ruleset age as a first-class
  reported fact) — vendoring solves "which patterns are used" and immediately creates "how stale are
  they," which a hand-rolled list would not have hidden as well but also would not have solved
  either.
- **Where the rules live in-repo.** If pulled in as data (not a binary dependency), the ruleset
  file(s) belong under a clearly-provenanced path (e.g. `resource_explorer/configdata/vendored/
  <ruleset-name>/`, mirroring how `configdata/` already holds `check_registry.yaml` and
  `analysis_catalog.yaml`) with the upstream source URL and pulled commit/tag recorded next to it —
  not copied in silently, for the same reason `foss_scorecard.py` insists a check's `needs` field
  says what would make it evaluable: a reader auditing this codebase's own supply chain must be
  able to find where this rule set came from without git-archaeology.
- **If no scanner binary is present and the data-file path was not taken either** (e.g. a
  deployment that deliberately omits the vendored asset to control image size), the step must
  decline with `SKIPPED_BY_DESIGN` and a reason naming the missing dependency — **never** a silent
  `NO_SIGNAL`. This is the same rule every other precondition miss in this design already follows;
  restated here because "the tool wasn't installed" is a new, ruleset-specific way to reach that
  same absence.

**Artifacts it reads.** `zipball_root` (`VIEW_SOURCE`) — a fresh extraction, walked file-by-file
(or handed whole to the external binary, if that path is chosen), matched against the vendored
ruleset's rules. Binary/generated/vendored paths need exclusion (the same "first-party" question
`docs/question-answering-and-context.md (§9)` §4 names as `holds first-party code` / `exclusion.scan`'s
census) — scanning a vendored `node_modules` or a fixture blob is both slow and a likely
false-positive generator, and most standard rulesets (gitleaks included) ship their own default
ignore-paths that should be honoured rather than re-invented.

**`requires_resources`:** `{"zipball_root": "local_path"}`, `{"zipball_root": VIEW_SOURCE}` for
views — the standard D3/D4 zipball step shape (`fetch_cost` cannot be `"none"` once this is
declared; enforced by `test_step_cost_tiers.py`).

**`requires_context`:** `{"has_file_inventory": "secret_scan walks project_file_inventory paths to
avoid a second independent directory walk and to reuse the same first-party/vendor exclusion the
inventory already classifies"}` — reuses the existing `has_file_inventory` precondition
(`step_preconditions.py:81-82`), no new precondition needed for the file-inventory half. The
ruleset-availability check (scanner binary/data file present and loadable) is a *different* kind
of precondition — not "another step's output is absent" but "this step's own dependency is absent"
— and does not fit `PRECONDITIONS`' existing shape (which checks stored-data row counts via
`_needs_rows`). Handle it as a plain runtime guard inside the surveyor's `run()`, emitting
`result_status.skipped(...)` directly the way `step_preconditions.skip_status()` does, rather than
forcing it through the `requires_context`/`PRECONDITIONS` machinery built for a different shape of
absence.

**Proposed cost tiers:** `fetch_cost="download"` (one zipball, shared with any other zipball step
in the same run via `resolve_resources`), `compute_cost="medium"` **VERIFY**. Vendoring changes
*which* engine does the matching but not this estimate's shape — a full-repo content scan, whether
run by this codebase's own regex loop or shelled out to a vendored binary, is still a full read
pass over every non-excluded file, closer to `repo_data_profiling`'s per-file work than to
`repo_file_inventory`'s path-only walk. If the binary path is chosen, wall-clock includes process
spawn/IPC overhead the in-process alternative would not have — measure both shapes if both are
seriously considered, since they may land in different tiers. `repo_manifest_parse`'s own comment
(`repo_survey_definition_adapter.py:381-395`) is the cautionary tale for guessing "medium" without
measuring — confirm against `step_cost_observer` p90 elapsed after the first ~10 real runs before
trusting the label.

**Findings shape.** One `project_analysis_findings` row per match: `check_name="secret_pattern"`,
`label` = the rule id the vendored ruleset itself assigns (not a locally-invented category —
using the upstream rule id keeps the finding traceable to the exact rule that fired),
`detail={"path": ..., "line": ..., "rule_id": ..., "ruleset": <name>, "ruleset_version": <version
or pulled commit/tag>, "excerpt": <redacted/truncated>}`. The excerpt must be truncated/masked in
storage — a survey step that persists the plaintext secret it just found would be creating the
exact incident it exists to flag. Emit `RequestForActionAnnotation` per hit (rotate/remove the
credential, add the path to `.gitignore`), not a single rolled-up count — a security RFA needs the
path to be actionable, the same reasoning `SecurityHygieneSurveyor` already applies per-gap rather
than per-repo.

**Ruleset provenance is part of every finding, not an implementation detail.** This is the direct
consequence of vendoring, stated in the constraint that motivated it: *"'No secrets found' over
gitleaks v8.18 rules is a different claim from the same sentence over a set someone wrote by
hand, and a reader cannot tell them apart unless the output says."* So `ruleset` and
`ruleset_version` (or pulled-commit hash, if the ruleset ships without its own version tags)
belong in **every** persisted row this step writes — both the per-match findings above and the
summary/outcome row below — not just the ones with hits. A clean scan and a scan that never
checked look identical unless the clean one names what it checked against.

**Ruleset staleness is a first-class, surfaced fact — following `security_summary`'s
`summary_freshness` pattern.** `security_summary.py` already solves an analogous problem: it
carries `oldest_input_at`/`oldest_input_age_days` (computed by `_age_days`,
`security_summary.py:93,170`) and emits a dedicated `check_name="summary_freshness"` finding whose
`label` is `"current"` when the oldest contributing result is ≤30 days old and `"stale"` otherwise
(`security_summary.py:235-248`), with `confidence=0` and an explicit "could not be read" text when
the age itself is unknown rather than guessing. `repo_secret_scan` should do the direct analogue
for the ruleset's own age, not the finding's age: a `check_name="ruleset_freshness"` finding,
`label` in `{"current", "stale", ""}`, computed from the ruleset's recorded pull/release date
(recorded once, next to the vendored asset, per the "where the rules live" point above) rather than
from when the scan ran — a scan run yesterday against a ruleset vendored eighteen months ago is
stale in exactly the sense this whole design exists to catch: **a confident clean result produced
by rules that predate key formats introduced since.** Threshold TBD (security_summary's 30 days is
tuned for a different kind of input and should not be copied blindly — a secret-pattern ruleset
plausibly wants a longer window, closer to "has a new major version shipped upstream" than to a
calendar cutoff) — flagged as a decision for whoever builds this, not resolved here.

**Known-positive check: prefer the vendored ruleset's own fixtures over a scanned-file count.**
The previous draft's known-positive was "the inventory is non-empty and N files were scanned" —
real, but weak: it proves files were read, not that the *matching logic* actually works on this
run (a regex-engine version mismatch, a rules-file that failed to load, a binary that silently
no-opped could all still produce a non-zero scanned-file count and zero matches). A standard
vendored ruleset ships known-positive test fixtures — content specifically constructed to match
specific rules. Running the scanner over that fixture as part of (or immediately before) every
real scan, and asserting it matches what it is supposed to match, is a direct proof the method
fired correctly on *this* run, not an inference from an adjacent count. Use it as the primary
known-positive; keep the scanned-file count as a secondary corroborating detail, not the proof
itself.

**Known-positive check / outcome mapping (revised):**
- Ruleset unavailable (no scanner binary and no data-file fallback) → `SKIPPED_BY_DESIGN`
  (runtime guard inside `run()`, see above), reason naming the missing dependency. Distinct from
  the file-inventory precondition miss below — both are real skips, with different reasons.
- File inventory empty or unreadable → `SKIPPED_BY_DESIGN` (`has_file_inventory` precondition
  miss), never dispatched as a scan.
- Ruleset available, fixture self-test run, **fixture does not match as expected** → `UNVERIFIED`,
  cause `"ruleset_self_test_failed"` — the method could not prove itself working this run, so
  whatever it reports (including zero matches) cannot be trusted as `NO_SIGNAL`. This is a new
  outcome path the fixture check specifically enables and the file-count-only version could not:
  a broken scanner that finds nothing is no longer indistinguishable from a working scanner that
  finds nothing.
- Ruleset available, fixture self-test passes, real scan runs, zero matches → `StepOutcome(
  NO_SIGNAL, known_positive=True, detail={"ruleset": ..., "ruleset_version": ..., "fixture_self_test":
  "passed", "files_scanned": N})` — the fixture pass is the known-positive; the scanned-file count
  travels as corroborating detail, not as the proof.
- Matches found → `StepOutcome(RECOVERED, ...)`, with the count, `ruleset`/`ruleset_version` in
  every row per the provenance point above.
- **Every persisted row must carry `outcome`/`outcome_cause` via `.as_row()`** so
  `result_status.status_from_detail()` can render it — this is what makes "0 secrets found (over a
  named, current, self-tested ruleset)" and "never scanned" different strings in the UI
  (`result_status.py`'s whole reason for existing).

**Evidence shape (§0c's summary-plus-linked-evidence pattern, applied here).** ONE summary
annotation per run: outcome, `ruleset`/`ruleset_version`, `fixture_self_test` result, and the match
count — this is what a reader sees first, and per §0c's constraint 1 it must carry
`outcome`/`outcome_cause`/`known_positive` itself, not rely on evidence-annotation counting. MANY
evidence annotations, one per match, linked to the summary via `AnnotationExtension` once that
plumbing exists (§0c) — each carrying only `path`/`line`/`rule_id`/`ruleset`/`excerpt` for that one
hit. Until `AnnotationExtension` support lands in `EgeriaPublisher`, this degrades gracefully to
today's flat-list shape (one `RequestForActionAnnotation` per match, as described above) — the
evidence-linking upgrade is additive to the findings shape already specified, not a prerequisite
for building this analysis at all.

**What it must NOT claim.**
- Must not claim "no secrets" as a repo-level property — only "no matches against `<ruleset>
  <version>`'s rules, in the current HEAD snapshot of tracked files, self-test passing." Naming the
  ruleset and version in the claim itself, not just in stored detail, is the point of resolving
  this as vendored rather than hand-rolled — see the provenance requirement above.
- Must not claim the result is current without checking ruleset age — see `ruleset_freshness`
  above; a stale-ruleset clean scan should render visibly differently from a current-ruleset clean
  scan, not as the same green state.
- Must not claim git-history cleanliness — see the HEAD-only scoping above.
- Must not silently skip vendored/generated paths without saying it did — if an exclusion list is
  used (the ruleset's own defaults or a local one), the finding detail or a companion metric should
  record `files_excluded` and why, or a reader cannot tell "clean" from "we didn't look there."
- Must not report `NO_SIGNAL` on a failed or skipped fixture self-test, however clean the real scan
  looked — see the outcome mapping above.
- Must not be badged as the same `security_scan` id that `SecurityHygieneSurveyor` already owns,
  **and must not write its findings under a `kind` string that collides with or is a near-homograph
  of any existing analysis id or finding kind.** New analysis id (`secret_scan`), new step key
  (`repo_secret_scan`), new finding kind — see the dedicated subsection below on why this is not a
  style preference.

---

## 2. Telemetry / phone-home (`repo_telemetry_scan`)

**What it measures.** Whether the software, as shipped in this repository, makes outbound network
calls at runtime that were not clearly the point of the software (an HTTP client library is not
telemetry; a call to a fixed analytics/metrics endpoint embedded in application code is) — and
whether such calls are disclosed (a privacy policy, a `DISCLOSURE.md`, a documented opt-out env
var, a README section). This is `docs/dr-egeria/resource_questions.csv:32`'s question verbatim:
*"Does the software contain telemetry, phone-home mechanisms, or external metrics tracking?"*

**Artifacts it reads.** `zipball_root` (`VIEW_SOURCE`) — source-level static scan for known
telemetry SDK imports/calls (Segment, Mixpanel, Amplitude, Sentry DSN strings, Google Analytics,
custom `analytics.track`/`metrics.send`-shaped call sites) and for literal outbound-URL constants
in non-test, non-vendored code. Also `project_file_inventory` (already covered by
`has_file_inventory`) for the disclosure-document check (README/PRIVACY/DISCLOSURE content
presence — presence only at this tier; a RAG-tier follow-on could grade the disclosure's
*quality*, out of scope here per the same Discovery/Assessment boundary
`docs/survey-model.md (§1)` draws for `cve_scan`).

**`requires_resources`:** `{"zipball_root": "local_path"}` / `{"zipball_root": VIEW_SOURCE}`.

**`requires_context`:** `{"has_file_inventory": "phone_home checks for a disclosure document
(README/PRIVACY/DISCLOSURE) via the file inventory before scanning source for the calls it would
be disclosing"}`.

**Proposed cost tiers:** `fetch_cost="download"`, `compute_cost="medium"` **VERIFY** — same
reasoning as secret scanning: a source-content scan over every non-vendored file, not a metadata
read. If restricted to source-file extensions only (skip data/binary/doc files using the same
language/extension classification `repo_file_classification` already computes), it likely measures
closer to `repo_manifest_parse`'s "low" (0.0-0.6s across three repos of very different size,
`repo_survey_definition_adapter.py`'s own measured comment) than to a full-tree walk — but this is
a guess pending measurement, same as #1.

**Findings shape.** `check_name="telemetry_call_site"` findings, one per detected call site or
import, `detail={"path", "line", "sdk_or_pattern", "target_host_literal_if_any"}`; a separate
`check_name="disclosure_document"` finding (`pass`/`gap`) for whether *some* document plausibly
discloses network behaviour. `RequestForActionAnnotation` only when calls are found **and** no
disclosure document is present — the RFA is "document what this already does," not "remove
network calls," since outbound calls that are the software's actual job (an API client, a
package-manager install step) are expected and this analysis cannot distinguish "telemetry" from
"legitimate networking" with certainty; see must-not-claim below.

**Known-positive check / outcome mapping:**
- Empty/unreadable inventory → `SKIPPED_BY_DESIGN`.
- Inventory populated, source files scanned, zero telemetry-shaped matches → `NO_SIGNAL`,
  `known_positive=True`, `detail={"files_scanned": N}` — same shape as #1: the count of files
  actually scanned is the known-positive.
- A **repo with zero source files matching any scannable extension** (e.g. a pure-data or
  pure-docs repo) is a distinct case: `has_file_inventory` alone does not prove there was anything
  scannable, only that the inventory exists. This analysis needs its own known-positive stronger
  than `from_upstream_table`'s: track `source_files_considered` separately from
  `inventory_rows_available`, and only claim `NO_SIGNAL` when `source_files_considered > 0`.
  Zero source files considered → `UNVERIFIED`, cause `"no_source_files_in_inventory"` — this repo
  might be a config/data-only repo, not a telemetry-free one, and those are different claims.
- Matches found → `RECOVERED`.

**Evidence shape.** ONE summary annotation per run (outcome, `source_files_considered`, match
count, disclosure-document verdict) carrying its own `outcome`/`outcome_cause`/`known_positive`
per §0c constraint 1. MANY evidence annotations, one per call site or import match, linked via
`AnnotationExtension` once §0c's `EgeriaPublisher` prerequisite exists — each carrying
`path`/`line`/`sdk_or_pattern`/`target_host_literal_if_any` for that one match. Same graceful
degrade to a flat `RequestForActionAnnotation`-per-match list as §1 until that plumbing lands.

**What it must NOT claim.**
- Must not claim "no phone-home" — must claim "no telemetry-SDK-shaped call sites or literal
  outbound-URL constants found in scanned source, over the pattern set checked." A static scan
  cannot see dynamically constructed URLs, calls routed through config/env vars resolved only at
  runtime, or calls in compiled/vendored dependencies (which is itself a reason this analysis is
  about *this repository's own code*, not its dependency tree — a dependency's own phone-home
  behaviour is a different, harder question this analysis does not answer).
- Must not label every outbound call "telemetry" — a false positive here (flagging an API client
  as spyware) is reputationally costly in a different direction than a false negative, so pattern
  matches against a curated telemetry-SDK/analytics-vendor list, not a bare "any URL literal
  found," should drive the RFA; a broader "any network constant" finding can exist as a lower-
  confidence classification, not an RFA.
- Must not treat "no disclosure document found" as proof no disclosure exists — a disclosure could
  live in a separate docs site (the same `has_a_documentation_site` fact
  `docs/question-answering-and-context.md (§9)` §4 already names as observable via `doc_locations`'
  outward hop) that this Discovery-tier check does not follow.

---

## 3. CLA / DCO provenance (`repo_contribution_provenance`)

**What it measures.** Whether the project requires contributors to sign off on IP provenance
before a contribution is accepted, and whether that requirement is *enforced* rather than merely
stated — `docs/dr-egeria/resource_questions.csv:31`'s question. Two-part, deliberately: a
`CONTRIBUTING.md` that *says* "we use DCO" is a claim; a `.github/workflows/*` or bot config that
actually gates PRs on it is enforcement, and this project's own DCO practice
(`feedback_dco_signoff_required` — every commit in this checkout needs `git commit -s`) is a case
where the enforcement half is the half that matters.

**Artifacts it reads.** `project_file_inventory` for presence: `CONTRIBUTING.md` (and variants:
`CONTRIBUTING.rst`, `.github/CONTRIBUTING.md`), `.github/PULL_REQUEST_TEMPLATE.md`. `zipball_root`
content for two things a filename check cannot answer: (a) CONTRIBUTING content actually
*mentioning* "DCO"/"Developer Certificate of Origin"/"CLA"/"Contributor License Agreement" rather
than being an empty stub, and (b) enforcement config — a DCO GitHub Action
(`.github/workflows/*dco*`), a CLA-bot config file (`.github/cla.yml` / `.clabot` /
`CLAassistant` config), or GitHub's own branch-protection requiring a named DCO/CLA status check
(which would need `project_stats` / branch-protection data this codebase already fetches for
`security_features` — reuse that channel rather than a new API call if the field is already
present in `security_and_analysis_json`-adjacent stats; **VERIFY** whether branch-protection
required-status-checks are already captured anywhere in `stats_fetcher.py` before assuming a new
GitHub API call is needed).

**`requires_resources`:** `{"zipball_root": "local_path"}` / `{"zipball_root": VIEW_SOURCE}` —
needed for content, not just filename presence; a filename-only check would repeat exactly
`SecurityHygieneSurveyor`'s current shallowness (file present ⇒ pass), which is the thing being
fixed elsewhere in this doc.

**`requires_context`:** `{"has_file_inventory": "checks for CONTRIBUTING.md and enforcement
config paths via the file inventory before reading their content"}`.

**Proposed cost tiers:** `fetch_cost="download"` (needs content, not just paths — cannot be
`"none"`), `compute_cost="low"` **VERIFY** — this reads at most a handful of specific files
(CONTRIBUTING.md, a few workflow/bot-config candidates) rather than walking the whole tree the way
#1 and #2 must; likely genuinely `low` like `repo_manifest_parse`, but confirm rather than assume,
per rule 3 above.

**Findings shape.** `check_name="cla_dco_stated"` (`pass`/`gap`) — content mentions DCO or CLA.
`check_name="cla_dco_enforced"` (`pass`/`gap`/`unknown`) — a bot config or workflow found, or
branch protection requires the check. `ClassificationAnnotation` when both are checkable;
`RequestForActionAnnotation` only for the "stated but not enforced" gap specifically — that is the
actionable, asymmetric-risk finding (a stated-but-unenforced policy is worse than either extreme,
because it creates a false sense of coverage — the same "confident wrong answer" failure mode as
the false secret-scan claim this whole design responds to).

**Known-positive check / outcome mapping:**
- Empty/unreadable inventory → `SKIPPED_BY_DESIGN`.
- Inventory populated, `CONTRIBUTING.md` (or variant) absent entirely → `NO_SIGNAL` for the
  *stated* check only (`known_positive=True` — the inventory proves there is no such file to have
  missed), but `cla_dco_enforced` in that case should NOT independently claim `NO_SIGNAL` — no
  contribution doc plus no enforcement config found is still a real, conclusive "this repo has no
  CLA/DCO story", but say so as one combined finding rather than two that could be read as
  contradicting each other if only one is skimmed.
- `CONTRIBUTING.md` present, content read, no DCO/CLA keyword found → `NO_SIGNAL`,
  `known_positive=True` (the file was read; absence of the keyword in read content is a real
  answer).
- `CONTRIBUTING.md` present but unreadable/unparseable (encoding error, binary masquerading as
  markdown) → `UNVERIFIED`, cause `"contributing_doc_unreadable"` — do not fall through to "no
  DCO mentioned" silently.
- Enforcement check: config file/workflow found → `RECOVERED`. None found and branch-protection
  data was itself never fetched for this repo → `UNVERIFIED`, cause naming the missing upstream
  data, not `NO_SIGNAL` — "we didn't check GitHub's branch protection" and "GitHub's branch
  protection doesn't require it" are different claims and this is exactly the trap the whole
  design responds to.

**Evidence shape.** ONE summary annotation carrying both check verdicts (`cla_dco_stated`,
`cla_dco_enforced`) and the outcome/cause pair for each, since (per the combined-finding note
above) these two must be read together rather than independently. Evidence here is thinner than
§1/§2 — usually one or two supporting documents/config files rather than many matches — but the
same shape still applies for consistency and for level-3 addressability (§0c): the CONTRIBUTING.md
excerpt that triggered `cla_dco_stated` and the config file that triggered (or failed to trigger)
`cla_dco_enforced` are each a distinct evidence annotation, linked to the one summary, rather than
folded into the summary's own prose.

**What it must NOT claim.**
- Must not claim "no CLA/DCO" from filename absence alone — must have actually read content (see
  artifacts above); this is the specific defect `SecurityHygieneSurveyor` has today for its three
  checks (path/filename match only) and this analysis exists partly to *not repeat*.
- Must not claim "enforced" from a bot config file's mere presence without checking it is active
  (a `.clabot` config committed but the bot uninstalled from the org is a real, observed OSS
  pattern) — if the check cannot distinguish "config present" from "config active," it must say
  "config present" and not "enforced," and the `cla_dco_enforced` label vocabulary should reflect
  that (`pass` reserved for a verifiably-active enforcement path such as branch-protection required
  checks; a mere config file present without corroboration is `partial`, not `pass`).
- Must not conflate CLA and DCO as interchangeable in the finding text — they are different legal
  mechanisms with different contributor burden, and this project's own
  `feedback_dco_signoff_required` memory note is itself evidence the distinction is operationally
  load-bearing, not academic.

---

## 4. SLA content (`repo_sla_content`)

**What it measures.** Whether the project publishes any support or service-level commitment —
response-time commitments, uptime/availability commitments, a support-tier document, an SLA page
or section — as *stated content*, not as an operational fact this codebase could ever verify
(RE has no way to check whether an actually-published 99.9% uptime figure is true; it can only
check whether one is published). `docs/dr-egeria/resource_questions.csv:21`'s "Is there any
reliability or availability aspects?" is presently answered, per `question_catalog.yaml:394`, by
`repository_health`'s trend chart — a proxy (commit/star trend), not content. This analysis is the
real answer the note there already flags as a gap.

**Artifacts it reads.** `project_file_inventory` for candidate paths (`SLA.md`, `SUPPORT.md`,
`.github/SUPPORT.md`, a `docs/` subtree matching `sla|support|availability`). `zipball_root`
content for keyword/structure matching within those candidates ("uptime", "response time",
"business hours", "P1/P2/P3", "99.9%", explicit support-tier language) — this is the one analysis
of the four for which the *absence* of any SLA content at all is the overwhelmingly common,
entirely unremarkable case (most OSS repos have no SLA — that is not a defect, unlike a missing
LICENSE). Framing matters here: this must render as a neutral fact, not a gap/RFA, for any repo
that is not itself claiming to be a supported product.

**`requires_resources`:** `{"zipball_root": "local_path"}` / `{"zipball_root": VIEW_SOURCE}`.

**`requires_context`:** `{"has_file_inventory": "checks candidate SLA/support document paths via
the file inventory before reading content"}`.

**Proposed cost tiers:** `fetch_cost="download"`, `compute_cost="low"` **VERIFY** — narrowest
scope of the four (a handful of candidate filenames, not a full-tree source scan), so `low` is the
strongest prior of the four proposed tiers, but still unmeasured.

**Findings shape.** `check_name="sla_content"`, `label` in `{"present", "absent"}` — deliberately
*not* `pass`/`gap`, breaking from `SecurityHygieneSurveyor`'s vocabulary on purpose: `pass`/`gap`
frames absence as a defect, and for the large majority of repositories that never intended to
publish an SLA, absence is not a defect. `ClassificationAnnotation` only — **no
`RequestForActionAnnotation` for absence**, ever, from this analysis alone. An RFA would only be
defensible when *paired* with independent evidence the project positions itself as a supported
product (e.g. `repo_classification`'s role output naming it a "service"/"product" rather than a
"library"/"tool") — out of scope for this first version; flag as a possible v2 refinement rather
than building the cross-analysis join now.

**Known-positive check / outcome mapping:**
- Empty/unreadable inventory → `SKIPPED_BY_DESIGN`.
- Inventory populated, no candidate paths present → `NO_SIGNAL`, `known_positive=True`,
  `detail={"candidate_paths_checked": [...]}` — the searched-and-absent list *is* the
  known-positive; without listing what was checked, "no SLA content" and "we didn't know where to
  look" are indistinguishable to a reader, which is the same trap `_CAUSE_HINTS` in
  `result_status.py` is built to avoid for every other analysis.
- Candidate path present, content read, no SLA-shaped keywords found → `NO_SIGNAL`,
  `known_positive=True` — a document exists but is not an SLA (e.g. a generic `SUPPORT.md` saying
  "file an issue").
- Candidate path present but unreadable → `UNVERIFIED`, not `NO_SIGNAL`.
- SLA-shaped content found → `RECOVERED`.

**Evidence shape.** ONE summary annotation (outcome, `candidate_paths_checked`, whether SLA-shaped
content was found) carrying its own outcome fields per §0c constraint 1 — critical here
specifically because, per the findings-shape note above, `NO_SIGNAL` must read as neutral rather
than as a gap, and a reader who never opens the linked evidence must see that neutrality in the
summary text itself, not infer it from an absence of evidence annotations. MANY evidence
annotations only when content was actually found (a candidate path with SLA-shaped keywords) — for
the common "absent" case there is deliberately little evidence to link, which is fine: the summary
alone already carries the full, honest claim.

**What it must NOT claim.**
- Must never claim the SLA is *true* or *honoured* — only that it is *published*. This is the
  starkest "must not claim" of the four: RE has zero ability to verify an uptime commitment against
  reality, and any UI surfacing this finding must not phrase it as "This project meets its SLA."
- Must not render "absent" as a negative signal by default (see findings-shape note above) — this
  is the one analysis of the four where the neutral framing is itself the main design risk, more
  than the cost-tier guess.
- Must not search only `SLA.md`-shaped filenames and call that exhaustive — a support/availability
  commitment can live inside a general `README.md` or `SUPPORT.md` section with no dedicated file;
  scope the candidate list generously and say explicitly, in the finding detail, which paths were
  actually inspected.

---

## Survey group membership: a new `RepoComplianceSurvey`, not a bigger `RepoAssessmentSurvey`

**Steps and surveys are already many-to-many — this is answered by the existing model, not a new
mechanism.** `docs/dr-egeria/repo_survey_types.csv` maps `step_key -> survey_group` as a plain
per-row membership, and 11 of 36 distinct step keys already belong to more than one group measured
directly from that file: `repo_file_inventory` appears in five groups (`RepoAnalysisSurvey`,
`RepoAssessmentSurvey`, `RepoCoarseProfile`, `RepoRefreshSurvey`, `RepoScoutingSurvey`),
`repo_git_statistics` in four, five more steps in three groups each, and four more in two. So the
review's framing question — "is each GAP a separate survey, or a step in a larger security survey?"
— is a false choice under this model: **each of the four is one step, individually runnable and
schedulable on its own** (as every other `STEP_REGISTRY` entry already is), **and** it can belong to
as many survey groups as make sense, each membership a separate CSV row. Adding a step to a new
group does not remove it from any group it is already in.

**Proposed new group: `RepoComplianceSurvey`.** Not `RepoSecurityAssessment` — three of the four new
steps are not security findings in the sense `RepoAssessmentSurvey`'s existing members are (a
missing `SECURITY.md`, an unpatched CVE). CLA/DCO provenance is a legal/IP question, SLA content is
a support-commitment question, and telemetry disclosure is a privacy question; only secret handling
is squarely "security" in the same sense as the rest of `RepoAssessmentSurvey`. "Compliance" is
also the framing `docs/dr-egeria/resource_questions.csv` already uses for three of these four (its
own notes tag them "OSPO/DevSecOps tool category" and "AI Governance tooling category" — compliance
framing, not pure security framing), so the new group name matches vocabulary this codebase already
uses rather than introducing a new one.

**Composition — which existing steps join, and why.** `RepoAssessmentSurvey`'s current ten steps
(`repo_survey_types.csv`, measured directly) are `repo_git_statistics`, `repo_file_inventory`
(prerequisite refreshes), `repo_documentation`, `repo_security`, `repo_security_features`,
`repo_ci_quality`, `repo_cve_scan`, `repo_foss_scorecard`, `repo_cii_badge`, `repo_security_summary`
— already substantially the security/compliance picture per `docs/open-stack-checklist.md`'s own
finding that a separately-proposed `Security_Assessment` survey would have been "a fourth copy of
steps that already run." `RepoComplianceSurvey` reuses that judgement rather than re-deciding it:

    prerequisites   repo_git_statistics, repo_file_inventory        (already required by several
                                                                      of the joining steps)
    existing        repo_security, repo_security_features,          the existing security-family
                    repo_cve_scan, repo_foss_scorecard,              members of RepoAssessmentSurvey
                    repo_cii_badge, repo_security_summary            that answer adjacent questions
                    repo_license_classification                     Discovery-tier today
                                                                      (repo_survey_types.csv), joins
                                                                      here too — IP/licensing risk is
                                                                      the same family of question as
                                                                      CLA/DCO provenance, and it is
                                                                      already cheap (reads
                                                                      project_stats only)
    new (this design) repo_secret_scan, repo_telemetry_scan,
                       repo_contribution_provenance, repo_sla_content

`repo_documentation` and `repo_ci_quality` are deliberately **not** pulled into this new group even
though they are `RepoAssessmentSurvey` members — they answer "does the repo document/test itself
well", a different question from "does the repo meet an external compliance bar", and including
every `RepoAssessmentSurvey` member here would just be renaming that survey rather than composing a
genuinely different one. `repo_license_classification` and the four new steps are the only members
this group adds beyond what already exists; everything else it draws in is a real join, named with
the reason above.

**`repo_security_summary` stays the reducer, and does not (yet) count the new four.** Its
`INPUT_KINDS` is a fixed eight (`security_summary.py:59-67`), pinned by
`test_security_summary.py` per its own docstring. Adding the four new finding kinds to that tuple
is real, in-scope-to-describe-but-out-of-scope-to-build work: it would raise `MIN_INPUTS_FOR_VERDICT`
coverage math from "of 8" to "of 12", change what "below the floor" means, and needs its own
`PRECEDENCE` consideration if any new kind overlaps an existing one's question (none obviously do,
on inspection — CLA/DCO, SLA, and telemetry are new questions, not stronger/weaker restatements of
an existing `INPUT_KINDS` member). Flagged for whoever wires this survey group up; not resolved
here.


## Sequencing

**The constraint.** All four register in `repo_survey_definition_adapter.py`'s `STEP_REGISTRY`
(currently ~35 entries in one dict literal, `repo_survey_definition_adapter.py:284` onward) and
`ANALYSIS_KINDS` (`repo_survey_definition_adapter.py:2630` onward) — both live in the *same file*.
`git add`/commit is per-file. Four sessions each appending an entry to the same dict literal will
produce four diffs touching overlapping line ranges (dict insertion order matters for
`STEP_REGISTRY` — see the file's own repeated comments that its order *is* "Repo Full Survey"
run order, so position is load-bearing, not cosmetic) — genuinely not parallelizable without
merge conflicts or, worse, a silent last-write-wins that drops one analysis's registration.

**What genuinely is parallel.** The four surveyor modules themselves — new files under
`resource_explorer/surveyors/sub_surveyors/` (e.g. `secret_scan.py`, `telemetry_scan.py`,
`contribution_provenance.py`, `sla_content.py`), each self-contained, each importable and unit-
testable in isolation against a fake `Project`/`ProjectRegistry`/local directory, with zero shared
state between them. Four agents can write and test these four files fully in parallel with no
coordination — the same reasoning `docs/survey-model.md (§1)` gives for
why sub-surveyors are "already composed of independent units" for repo surveys specifically
(this module's own docstring, `repo_survey_definition_adapter.py:30-33`: "repo surveys are already
composed of independent sub-surveyor units").

**Recommended order.**

1. **One session first extends `resource_explorer/surveyors/sub_surveyors/__init__.py`'s export
   list and reserves four step keys / analysis ids** (`repo_secret_scan`, `repo_telemetry_scan`,
   `repo_contribution_provenance`, `repo_sla_content`; analysis ids `secret_scan`,
   `telemetry_scan`, `contribution_provenance`, `sla_content`) in a short note or stub — a single,
   fast, low-conflict-risk commit that exists purely so the four parallel sessions are not also
   guessing at each other's naming. Given this design note itself already fixes the names, this
   step may be unnecessary — but call it out explicitly to whoever picks this up, since a naming
   collision discovered after two modules are already written is expensive to unwind.
2. **Four sessions build the four sub-surveyor modules in parallel**, each against this design
   section, each with its own test file (`tests/test_secret_scan.py`, etc., following the existing
   one-test-file-per-surveyor convention visible in the package). None of these four touches
   `repo_survey_definition_adapter.py`.
3. **One session, after all four modules exist and pass their own tests, does the
   `STEP_REGISTRY`/`ANALYSIS_KINDS` registration for all four in one pass.** This is the
   serialised step — one file, one diff, one commit, done once rather than four times. It is also
   the natural point to add any new `PRECONDITIONS` entries these analyses turn out to need beyond
   `has_file_inventory` (none identified above beyond the existing four, but confirm while writing
   this pass — e.g. if branch-protection data for CLA/DCO enforcement is not already fetched
   anywhere, that surfaces here).
4. **The same session (step 3 already has the file open) adds the `RepoComplianceSurvey` rows to
   `docs/dr-egeria/repo_survey_types.csv`** — the new group's composition is a set of CSV rows
   (Survey group membership section, above), not a code change, and does not touch
   `STEP_REGISTRY`/`ANALYSIS_KINDS` at all, so it can ride along with step 3 without adding a
   second serialised pass over the shared Python file.
5. **Same or a following session updates `security_overview` / `Security_Assessment`-shaped
   dashboard bundles** (`docs/survey-model.md (§1)`'s proposed
   `Security_Assessment` survey names these four explicitly as its missing steps) and the
   `question_catalog.yaml` GAP notes at the four cited line numbers, replacing `GAP: ...` with the
   real analysis id now that one exists — closing the loop the design docs already point at.

This order minimizes both conflict risk (only step 3 touches the shared file, and only once) and
wasted work (a naming collision or a `PRECONDITIONS` gap is caught in step 1/3 rather than after
four modules are already built against inconsistent assumptions).

**Two more workstreams this design names, genuinely separate from the four-analysis build above,
and each independently sequenced:**

- **Provider adoption in the three existing analyses (§0b)** — `license_classification` then
  `foss_scorecard` then `cve_scan`, in that order, for the reasons given there (ascending blast
  radius, `cve_scan` pinned behind a regression fixture before it is touched at all). This can run
  fully in parallel with steps 1-5 above — it touches three existing surveyor files, none of which
  the four new analyses depend on or modify — but should NOT be done by the same session doing step
  3's `STEP_REGISTRY` pass, to keep that one serialised edit small and reviewable on its own.
- **`AnnotationExtension` support in `EgeriaPublisher` (§0c)** — a genuine prerequisite for the
  summary-plus-linked-evidence *Egeria-published* shape, but not for building or running any of the
  four analyses locally (§0c's own note: `project_analysis_findings` already holds per-match rows
  regardless of Egeria publication, and the flat-annotation-list fallback works today). This can
  start any time, independently, and does not block steps 1-5 — it should land before any of the
  four analyses' Egeria-side evidence-linking is turned on, not before they are built.

## Honest limits

**What I could not determine from reading alone:**

- Whether GitHub's branch-protection *required status checks* (needed for a solid CLA/DCO
  enforcement signal) are already captured anywhere in `project_stats` /
  `security_and_analysis_json` from `stats_fetcher.py`'s existing fetch, or would need a new API
  call. I read `security_features.py`'s toggle list (Dependabot/secret-scanning/advanced-security)
  and it does not obviously include required-status-checks, but I did not trace every field
  `stats_fetcher.py` populates. **Measure before building #3**: grep `stats_fetcher.py` for
  `branch_protection`/`required_status_checks`, and if absent, `repo_contribution_provenance`'s
  enforcement half needs its own `fetch_cost` line — likely `api`, one call — separate from the
  `download` tier its content-reading half needs, and `StepInfo` only has one `fetch_cost` field
  per step, so this may argue for splitting enforcement into its own step key rather than bundling
  it with the stated-policy check. Flagged, not resolved.
- The real compute cost of a full-content regex scan (secret scan, telemetry scan) against this
  codebase's actual repo-size distribution. I have `repo_manifest_parse`'s measured numbers (0.0s/
  0.2s/0.6s across three very differently sized repos, `repo_survey_definition_adapter.py`'s own
  comment) as the nearest analogue, but that step parses three specific manifest shapes, not every
  file's raw content — a full-file-content grep pass over every source file in a large repo
  (`odpi/egeria`-sized) could plausibly land in `medium`, not `low`, and I have not run it. This is
  why both #1 and #2 above are flagged `compute_cost="medium" **VERIFY**` rather than asserted.
- Whether the `docs/question-answering-and-context.md (§9)` §4 "context as declared facts" table's
  unbuilt facts (e.g. `first_party_code`, only "computed in `arch_recovery_detect`" per that
  table, not yet a `PRECONDITIONS` entry) should gate #1/#2's file-scanning scope (skip
  third-party/vendored code) via a real precondition, or via an in-surveyor exclusion list the way
  proposed above. Both are legitimate; the precondition route is more consistent with this
  codebase's stated direction but is more plumbing than this design commits to building.

**What should be measured before building, in priority order:**

1. Whether required-status-check / branch-protection data already exists in `project_stats` (cheap
   grep, resolves the #3 fetch-cost/step-split question above).
2. A throwaway timing script — full-content regex scan over 3-5 real zipballs of varying size,
   using the exact exclusion approach chosen (vendored/generated path skip or none) — to settle
   `compute_cost` for #1 and #2 before the `StepInfo` entries are written, not after, so
   `step_cost_observer`'s first-observation "loud on purpose" warning (its own docstring) fires
   against a plausible declared tier rather than an admitted guess.
3. Licence compatibility of the specific vendored ruleset chosen against this project's own licence,
   and — if the binary-tool path is taken rather than the rules-data-only path — of the scanning
   tool itself, before either is committed into the tree (§1's "what vendored costs" list).
