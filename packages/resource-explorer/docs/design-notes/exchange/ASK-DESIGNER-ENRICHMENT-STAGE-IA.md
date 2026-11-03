# ASK — Designer: what the Enrichment stage's sub-tabs should be (2026-09-29)

From the design session, for the designer session. Reply as
`REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` in this folder; a drawing of one
Enrichment page under `wireframes/` is welcome but a written reply is
enough.

## The prompt, verbatim from the owner

While gating the new Documentation sources block on the Enrichment stage
(`BRIEF-DATABASE-DOCUMENTATION-SOURCES.md`, PR #348), the owner asked:
*"does the 'Survey & analyses' sub-tab make sense in the Enrichment stage?
It may be where human-entered information belongs — rethink what
Enrichment's sub-tabs are."*

He is right that the stage inherited its sub-tabs from Scouting and
Discovery without anyone deciding whether they fit.

## What Enrichment is, and what it holds today

Enrichment is the stage where a person adds what no survey can measure.
Today it holds, on every resource type unless noted:

| what a person supplies | where it lives now | what consumes it |
|---|---|---|
| the Enrichment Context form: cost to run, monitoring fit, security fit, infrastructure fit, skills, governance fit, last backup and restore test (database/filesystem) | `stages/enrichment.js` `renderEnrichmentForm`, one field per catalogue question, each with the owner's note and a "proposed from <analysis>" starting point where a machine half exists | the Assessment stage's fit questions; Curate; the Questions tab shows them as ✓ once a person answers |
| a declared lens (data requirement) for preliminary fit | not yet a control here; `preliminary_fit` reports "no requirement declared" | `preliminary_fit`, the "could this be in scope" question |
| documentation sources (URL, kind, probe state, ingest later) | the new block from PR #348 | chat citations; the three document-read questions; Egeria ExternalReference on publish |
| confirmed glossary terms and data classes for columns | not built (`semantic_suggestions` proposed; Enrichment confirms) | Egeria SemanticAnnotations; "what is this data about" |
| dependency support verdicts (repo) | `dependency_support`'s machine list, confirmed per dependency | "do we already support these dependencies" |

The eight catalogue questions at the Enrichment stage are all of the
"human-supplied, sometimes with a machine starting point" kind. The stage's
own analyses are therefore the ones that *consume* human input:
`preliminary_fit` with a lens, `semantic_suggestions` once built,
`dependency_support`'s reconciliation, `db_resilience`'s human half.

The current sub-tab row on Enrichment is the same four as every stage:
Questions · Survey & analyses · By analysis · Disposition (plus Schema
Inventory for databases). Survey & analyses lists analyses to run;
By analysis shows their results. Neither says "here is what you told us,
and here is what it changed".

## The questions for you

1. **What are Enrichment's sub-tabs?** A first proposal to react to, not
   to accept: *Questions* (unchanged: what is asked, what is answered,
   including the human-supplied ones with their ✓), *What you supplied*
   (the Context form, the lens, documentation sources, confirmed terms,
   each with when and by whom, and what each feeds), *What it changed*
   (the Enrichment-tier analyses re-run with those inputs: fit with a lens,
   semantic suggestions awaiting confirmation, dependency support), and
   *Disposition*. Should "Survey & analyses" survive here at all, and if so
   as what?
2. **The starting-point pattern.** Several human fields have a machine
   proposal ("proposed from db_resilience: no backup tool detected"). How
   should a proposal, a person's confirmation, a person's override and a
   later machine change that disagrees with the person read, in one row?
   Today `movedSince` marks a field whose evidence moved after the person
   answered; the wording is engineering's.
3. **The documentation-sources block** (drawn from PR #348 as shipped): does
   it belong as a section of "What you supplied", and how should its probe
   states (reachable · needs sign-in · not found · blocked, each with status
   and time) and the later ingest state (pages, bytes, as of) sit next to
   the Context form's fields, which are answers rather than sources?
4. **Filesystems and repositories.** The stage is shared; say what differs
   per type only where it must (a repository has dependency support and a
   documentation site it may own; a filesystem has neither).

## Constraints

The honesty rules hold: a human-supplied answer is marked as such and
carries who and when; a machine proposal never reads as a confirmed
answer; a moved evidence mark is never removed silently. Existing tokens
and glyphs (`glyphs.js`) only. The Questions tab's row anatomy is fixed.

## What we do with the reply

The Enrichment sub-tab change becomes one UI slice with a task-based gate
on both databases and one repository; the documentation-sources block is
re-seated inside it rather than redesigned. Until the reply lands, PR #348
ships the block where it is, under the current sub-tabs.
