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

> **Not created here.** All twelve Perspectives are elements Egeria's core
> content pack ships (folder "Product Perspectives"), under exactly the
> qualified names below. Every stage's questions file links to them by
> qualified name; this document no longer creates or updates them.
>
> **Decision (project owner, 2026-11-07):** "So Trellis needs to stop creating
> Data Owner and Admin and use the ones from the content pack." That made all
> twelve content-pack elements. The Trellis-created `Perspective::Data Owner`
> and `Perspective::Admin` are relinked and retired in Egeria by
> `scripts/migrate_trellis_perspectives.py`, run once by the coordinator.
>
> **Why the `Create Perspective` commands were removed (2026-11-09).** Until
> then this section still held twelve `## Create Perspective` commands, on the
> theory that Dr.Egeria's Merge Update would re-write values already there.
> After an Egeria reset the content pack seeds these elements first, with
> Content Pack provenance (`CoreContentPack`), and Egeria refuses ANY update to
> them from RE's account — even a no-op one — with
> `OMAG-REPOSITORY-HANDLER-400-008` ("does not have ownership rights to the
> instance"). Every heal of this batch then reported 12 of 24 commands failed.
> The commands could only ever have succeeded against perspectives Trellis
> had created itself before the content pack owned them. RE's own descriptions
> for these twelve are in git history (`f80c5370` and earlier); the content
> pack's descriptions are the ones Egeria serves.
>
> The list below is read by `scripts/verify_perspectives.py`, which checks
> each one resolves in live Egeria. Keep one `- \`Perspective::<Name>\`` line
> per perspective. Why explicit `Perspective::` qualified names rather than
> display names: several display names (Governance, Steward, Privacy,
> Community, Security) collide with other elements' display names in the Coco
> Pharmaceuticals demo data — see `docs/survey-model.md (§4)`.

- `Perspective::Governance`
- `Perspective::Financial`
- `Perspective::Steward`
- `Perspective::Owner`
- `Perspective::Consumer`
- `Perspective::App/AI Builder`
- `Perspective::Privacy`
- `Perspective::Community`
- `Perspective::Data Expert`
- `Perspective::Security`
- `Perspective::Architecture`
- `Perspective::Administration`
