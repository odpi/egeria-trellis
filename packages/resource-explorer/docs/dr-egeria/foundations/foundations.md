# Foundations — Glossaries & Perspectives

> Shared across every funnel-stage question document (one file per stage,
> e.g. `scouting-questions.md`). Run this file once, before any stage's
> questions file, then run each stage file independently — they reference
> these glossaries/perspectives/stage-terms by name, they don't recreate
> them. Run with VALIDATE first, then PROCESS.
>
> Modular by design (per discussion): glossaries and perspectives are
> foundational, reused identically across all seven funnel stages, and
> across tools (RE/EA/Portal/Overview Dashboard) where applicable — keeping
> them in one file means updating a perspective's description once, not
> once per stage file.
>
> Perspective list — reviewed against Coco Pharmaceuticals' real personas,
> EA's `developer`/`data_engineer`/`data_steward`/`governance_officer` set,
> Portal's persona-switcher, and Overview Dashboard's already-shipped
> perspective ribbon (the strongest precedent — it's the one that already
> implements this exact Perspective -> ScopedBy -> Question -> answered-by-
> report-spec model). Converged, not just RE's own list. Not every
> perspective is relevant to every tool/resource type — that's expected and
> sorts itself out per-question, per-stage, not enforced here.
>
> Perspectives are lenses, not personas/roles — an individual can hold
> several concurrently and switches based on what they're working on at a
> given moment, not a fixed identity. Named Coco personas above are
> evidence a lens is real and recurring, not a definition of the lens.
> "Lead"/"Officer"-style names were dropped where they crept in (Governance
> Lead -> Governance, Community Lead -> Community) for the same reason —
> a title shifts the reading toward role, not lens.

---

# Glossaries

## Create Glossary

### Display Name
User Questions

### Description
Questions users of Resource Explorer / Egeria Advisor / Trellis tools might
ask about a resource, grouped and scoped by the funnel stage (intent) and
perspective(s) each question applies to.

### Usage
Reference from Link Perspective to Question and Link Element To Scope
commands. One glossary shared across all funnel-stage question documents,
not re-created per stage.

___

## Create Glossary

### Display Name
Funnel Stages

### Description
One term per Trellis funnel-stage intent (Scouting, Discovery, Assessment,
Analysis, Enrichment, Understanding, Curate) — used as the Scope target for
Question elements via ScopedBy, so a question's intent-relevance is a
plain, editable relationship rather than a hardcoded property. Also holds
the two Scope Category terms (Asked At / Answered At) used as the second
leg of each ScopedBy relationship.

___

# Funnel Stage Terms

> Link Element To Scope resolves both Scope Reference and Scope Category
> as references to existing elements, not free text — confirmed live via
> validate (2026-08-12: "Referenced element 'Scouting' ... not found").
> These terms must exist before any stage's questions file is run.

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Scouting

### Description
Funnel-stage intent: broad inventory across many resources.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Discovery

### Description
Funnel-stage intent: find resources by what surveys revealed, launch
Egeria Survey Definitions.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Assessment

### Description
Funnel-stage intent: scored evaluation of a specific resource against
criteria.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Analysis

### Description
Funnel-stage intent: structural/quantitative analysis — dependencies,
profiling, API extraction.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Enrichment

### Description
Funnel-stage intent: provide human context — facts about the resource.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Understanding

### Description
Funnel-stage intent: visualize trends over time.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Curate

### Description
Funnel-stage intent: make a resource easier to find and more trustworthy
to reuse.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Automate

### Description
Funnel-stage intent: subscribe to an analysis; get notified (via RFA) when
it changes on a future scheduled run. Added 2026-08-15 — this term was
missed when the original 7-intent set was authored and only caught when
the Questions restoration (see docs/survey-model.md (§4)) tried
to ScopedBy a Question against it and got "Referenced element 'Automate'
... not found." Automate itself became the 8th canonical intent on
2026-08-13 (CLAUDE.md rule 17); this term should have existed since then.

___

# Scope Category Terms

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Asked At

### Description
ScopedBy Scope Category: the funnel stage where a Question is naturally
raised.

___

## Create Glossary Term

### Glossary Name
Funnel Stages

### Display Name
Answered At

### Description
ScopedBy Scope Category: the funnel stage whose data can actually answer
a Question (may differ from Asked At).

___

# Perspectives

> Created here, once, shared by every stage's questions file. Merge Update
> (Dr.Egeria's default) makes re-running this file safe if a perspective
> already exists from another app's rollout (e.g. EA/Overview Dashboard).
>
> Every Perspective gets an explicit `### Qualified Name` (`Perspective::
> <Name>`) rather than letting Dr.Egeria auto-generate one — added
> 2026-08-15 after several Perspective *display* names (Governance,
> Steward, Privacy, Community, Security) turned out to collide with
> pre-existing elements' display names elsewhere in this Egeria instance
> (the stock Coco Pharmaceuticals demo data), which made every by-display-
> name `Link Perspective to Question` command against those 5 fail
> "Multiple elements found for supplied name!" A qualified name is
> guaranteed unique, so referencing Perspectives by qualified name in
> `Link` commands sidesteps the collision entirely — confirmed live. The
> `Perspective::` prefix (not the auto-generated `Coco Pharmaceuticals::
> Perspective::...`) keeps this portable across Egeria instances rather
> than baking in this instance's default local qualifier/org name. See
> `docs/survey-model.md (§4)` for the full incident writeup.
>
> **Decision (project owner, 2026-11-07):** "So Trellis needs to stop creating
> Data Owner and Admin and use the ones from the content pack." All twelve
> Perspectives below are now elements Egeria's core content pack ships
> (folder "Product Perspectives"). `Owner` and `Administration` replace RE's
> own `Data Owner` and `Admin`; their descriptions are the content pack's,
> verbatim (its "the the" included), so a Merge Update re-run writes the
> values that are already there. The Trellis-created `Perspective::Data Owner`
> and `Perspective::Admin` are relinked and retired in Egeria by
> `scripts/migrate_trellis_perspectives.py`, run once by the coordinator.

## Create Perspective

### Display Name
Governance

### Qualified Name
Perspective::Governance

### Description
Executive/policy-level oversight of data-driven strategy and compliance —
Overview Dashboard's default perspective. Cares about program-level
posture (is this resource governed at all), not per-check detail. Renamed
from "Governance Lead" — "Lead" reads as a role/title, not a lens (2026-08-12).

___

## Create Perspective

### Display Name
Financial

### Qualified Name
Perspective::Financial

### Description
Cares about cost — cost to acquire/run/support a resource, cost of
duplication vs. reuse, cost of the skills/infrastructure gap to adopt it.
Added 2026-08-12 during Scouting question review — several real Scouting
questions (deployment cost, support cost, duplication cost) had no home in
the original 11-perspective set.

___

## Create Perspective

### Display Name
Steward

### Qualified Name
Perspective::Steward

### Description
Owns day-to-day responsibility for a specific domain's data quality and
correctness — recurring across many different domains (clinical trial
data, patient records, financial data, a team's own development
artifacts, a project's own metadata), not tied to one job title.

___

## Create Perspective

### Display Name
Owner

### Qualified Name
Perspective::Owner

### Description
A focus on the the existence, protection and investment, of specific resources of the organization.

___

## Create Perspective

### Display Name
Consumer

### Qualified Name
Perspective::Consumer

### Description
Evaluating whether a resource is suitable to use for their own purpose —
the "shopping" lens: is this worth adding to my basket, trustworthy enough
to build on.

___

## Create Perspective

### Display Name
App/AI Builder

### Qualified Name
Perspective::App/AI Builder

### Description
Building an application or AI capability (including fine-tuning/prompting)
against a resource — distinct from general engineering: cares about
content suitability for training/context, not just structural quality.

___

## Create Perspective

### Display Name
Privacy

### Qualified Name
Perspective::Privacy

### Description
Monitors regulatory compliance and sensitive-data exposure across
resources — distinct from general Security (breach/vulnerability risk).
Renamed from "Privacy Officer" — "Officer" reads as a role/title, not a
lens, matching the Governance/Community rename rationale (2026-08-13).

___

## Create Perspective

### Display Name
Community

### Qualified Name
Perspective::Community

### Description
Cares about the health of the community around a resource — contributor
activity, responsiveness to issues, adoption — relevant mainly to
community-driven resources like open-source repos. Renamed from
"Community Lead" — "Lead" reads as a role/title, not a lens (2026-08-12).

___

## Create Perspective

### Display Name
Data Expert

### Qualified Name
Perspective::Data Expert

### Description
Works with, moves, and shapes data — consolidates what would otherwise be
three overlapping lenses (data science/analysis, data engineering/pipeline
work, data integration) into one, since in practice they overlap heavily
and asking three near-identical questions per stage added noise without
adding signal.

___

## Create Perspective

### Display Name
Security

### Qualified Name
Perspective::Security

### Description
Cares about risk signals — contextual per resource type; for a repo this
means CVEs, dependency vulnerabilities, and community responsiveness to
security issues, not a fixed checklist.

___

## Create Perspective

### Display Name
Architecture

### Qualified Name
Perspective::Architecture

### Description
Broadest view of the metadata landscape — how a resource fits into the
overall information architecture and supply chain. Natural starting point
for understanding how information flows between systems.

___

## Create Perspective

### Display Name
Administration

### Qualified Name
Perspective::Administration

### Description
A focus on the operational and administrative aspects of the organization's resources. This involves the day-to-day upkeep, access, and reliability of these resources.

___
