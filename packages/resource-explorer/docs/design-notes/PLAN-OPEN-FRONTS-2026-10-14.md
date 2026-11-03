# PLAN — The open fronts after the owner's backlog review (2026-10-14)

From the design session, recording the owner's answers to the backlog
review of 2026-10-14 and the six new fronts he named, each with the
first step and who owns it. Decisions here are his; assessments are
the design session's and say so.

## 1. Decisions taken today

| Item | Decision (owner) | First step |
|---|---|---|
| Inline publish | A synchronous survey does not wait for its publish; show "queued for publish" | PR/CI: flip the default, add the state, proof-backed transition |
| n_distinct rows stored before the sign fix | Repair if it costs the same as labelling, else the simplest; test environment | Closed 2026-10-14 with no change: a read-only count found 0 affected rows (64,806 column-profile rows, none with distinct_count < 0, earliest surveyed_at 2026-09-22, after the fix); a repair is not possible anyway, since the denominator pg_class.reltuples is not stored |
| Stale ConnectorType provider class | A one-time leftover; add a provider-class check to the start-up heal | PR/CI |
| CI duplication | The cap was already raised; the owner suspects the same tests run several times | PR/CI: inventory of jobs and modes, then cuts |
| Coverage threshold, seven db_derived analyses | Design session rules: one shared 80% constant, per-analysis override allowed but unused, through result_status's vocabulary | PR/CI |
| Admin Performance panel | Ready, not high priority; designer round 2 when it rises | later |
| Cataloguing at scale | Designer ask written: `ASK-DESIGNER-CATALOGUING-AT-SCALE.md` | owner pushes it |
| RFA and to-dos | A joint piece of work with the Egeria lead: use cases, requirements, issues across Egeria and trellis | design session drafts the use-case document (§3) |
| Child module as a resource | Review, guided by Egeria's parallels (a database and its schemas; a software capability and its parts) | design session note (§3) |
| Code ingestion | Selective: test code versus core; documentation folders inside a repository (Egeria's "Messages and Codes") ingested as documentation, not code | design session note (§3) |
| Zones | Another discussion with the Egeria lead | owner |

## 2. The six new fronts, in the order the design session recommends

### 2.1 Prove adding analytics, surveys and visualisations (owner's item 3)
The extensibility walking skeleton brief exists
(`docs/extensibility-walking-skeleton-brief.md`) and ISSUE-150 now
fixes how a function is declared to Egeria. Nothing else on this list
can be proven extensible until one new routine, one new step and one
new tile go in end to end through the Analytic Library. First, because
it is designed and unblocks the rest.

### 2.2 The right questions and analytics (owner's item 2)
Today's inventory, from `resource_questions.csv` on main: 95 questions.
By stage: Analysis 32, Discovery 27, Assessment 15, Scouting 13,
Enrichment 7, one shared. By kind: repository 33, database 28, any kind
24, database and file system 5, database/file system/dataset 3, file
system 2. By level: resource 64, container 9, member 7, field 7, mixed 8.
Every question names an answering analysis; only two carry a status
note. Observations the review should settle: Enrichment has seven
questions and the Curate stage none, which is right for Curate (it
decides, it does not ask) but thin for Enrichment; file systems have
two questions of their own; Understanding has no questions because it
looks rather than asks; 24 questions apply to every kind and should be
checked that their answering analysis exists for every kind. The
review is a design session pass producing a per-question verdict
(keep, merge, retire, add) for the owner, then the questions file edit.

### 2.3 Ad hoc questions in chat, with clarifying agents (owner's item 1)
By definition unpredictable. The answering app (tool 4 in the Amundsen
assessment) is where this lives: a question that matches a catalogued
question runs its analysis; one that does not goes to a clarifying
agent that works with the person to turn it into scope, constraints and
context, then either an existing analysis, a composed one, or a
standing need. Depends on 2.1 (composing analyses) and on the MCP tools
(the agent calls the engine). Design note after 2.1 lands.

### 2.4 A simpler app with a handoff to RE (owner's item 4)
Less capable, basic use cases made easy, and an investigation handed to
RE when it outgrows the simple app. The Resource Locator is the reader's
half; this is the doer's half: register, run the default survey, read
the answers, publish with defaults. The handoff is an investigation
whose record lives in Egeria (project, work list, scope) so RE can adopt
it. Design note once the Resource Locator's phases 1 and 2 are walked,
so the shared components are known.

### 2.5 Files (owner's item 5)
File systems are registered and surveyed today with two questions of
their own and no lister; the Publish ruling and the file-types-as-
annotations work already apply. The brief is: a lister (local path, CSV,
later object storage), the scope tree for directories with the same
Include | Leave out and depth, Egeria's file and folder cataloguing as
the publish path, and the questions a file-system owner asks. One brief,
after the cataloguing-at-scale reply, because directories have the same
scale problem as tables.

### 2.6 Open Data and Open Models (owner's item 6)
Open data portals (CKAN, data.gov, Socrata) and open model hubs (Hugging
Face, model cards) as resource kinds: discovery sources that list
candidates, a survey that reads the portal's own metadata (licence,
publisher, update cadence, schema, model card fields), and publishing as
Egeria assets with the licence and provenance carried. Fits the
discovery-sources generalisation already asked of the designer. A
design note after 2.5, since a dataset from a portal is a file set with
provenance.

## 3. Documents the design session writes next, in order

1. `ASK-DESIGNER-CATALOGUING-AT-SCALE.md` (done, branch
   `re/ask-designer-cataloguing-at-scale`).
2. The questions-and-analytics review (2.2), a per-question table.
3. RFA and to-dos: use cases, requirements and issues, for the owner's
   session with the Egeria lead.
4. Code ingestion rules: selective by role (core, test, generated,
   vendored) and by shape (documentation folders, message catalogues),
   where the choice lives (resource, investigation, Admin default).
5. Child module as a resource, with Egeria's parallels.
6. Ad hoc questions and clarifying agents (2.3), after the skeleton.
7. The simpler app and the handoff (2.4), after the Locator walk.
8. Files (2.5), then Open Data and Open Models (2.6).
