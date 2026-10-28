# DESIGN — Curate's "By analysis": analyses of the curation record itself (2026-10-07)

Design session note at the owner's word: *"there could be some interesting
things to put into By analysis, but it will require some design: how often,
how much has been published, what kinds of things are not yet published,
analyses about data classes and dependencies validated. What is there now
is not even a good placeholder."* Today, on a database, the tab shows the
generic by-analysis pane with one item, "Egeria Native DB Survey · not run
yet". No build until the owner's go.

## The rule

**Curate's By analysis is about the curation record, not the resource.**
Every other stage's By analysis shows what surveys measured about the
resource; Curate's shows what people and RE have done with those
measurements: chosen, published, confirmed, left out, blocked. Each row
derives from proof rows, scope events, outbox and activity rows, never from
an estimate, and carries the envelope's state words (`measured` when the
rows exist, `nothing found` when they exist and are empty, `not
established` when a source could not be read). The tab is per resource,
with an "across my resources" view where the investigation or work list
gives the set.

## The analyses, ranked

| # | Analysis | Question it answers | Source rows | Row words |
|---|---|---|---|---|
| **1** | **Published and not yet** | how much of what RE knows about this resource is in Egeria, and why the rest is not | `catalogue_commit_proofs` (by GUID, read-back only), `resource_scope_events` / `catalogue_scope_events` (choices), the survey report's annotation count, the Enrichment fields' publish rows (P1 when built) | "published · 54 tables · 1 term · 1 report (76 annotations) · read back <when>"; "not yet published · 26 schemas undecided · 1 left out by a person · 2 observations confirmed but not published · 3 proposals awaiting a person"; "blocked · 1 (ISSUE-117 block, kept not sent)" |
| **2** | **Publish history and cadence** | how often and when this resource was published, how long a commit took, what failed and why | `catalogue_commit_proofs` by commit, `egeria_outbox` rows for the resource, activity rows | a small table per commit: when, by whom, items, outcome words, duration; a line "last published <when> · N commits · M failed · last failure: <Egeria's sentence>"; a cadence sentence "published 3 times in 2 days · survey older than the last publish: no" |
| **3** | **Proposals: made, confirmed, overridden, stale** | what RE proposed (terms, data classes, dependencies, worthiness) and what people did with it | E3 observation rows (`measured_value`, `measured_at`, state), dependency rows' state, scope events with `source='proposal'`, verdict rows | "12 proposals · 5 confirmed · 1 overridden · 6 awaiting · 2 disagree with a later survey"; per kind: terms, data classes, dependencies, components, folders |
| 4 | Data-class rule coverage | which data-class rules Egeria applies, how many columns each matched, how many columns no rule reached | the Rules Egeria applies block's source (`/rules/dataclasses`), column data-class rows | "7 rules · 212 columns matched · 1,018 columns no rule reached"; `not established` when the rules route answers only RE's built-in list |
| 5 | Dependencies validated | of the two-ended dependency rows, how many are measured, proposed, confirmed, unresolved | the dependency table's rows (two-ends design) | "41 build-time measured · 9 runtime proposed · 2 confirmed · 3 targets not established" |
| 6 | Blueprints and verdicts | per blueprint: proposed, accepted, rejected nodes; written to Egeria or not | verdict rows, `architecture_materialized_blueprints`, outbox membership rows | "Deployment Blueprint · 12 proposed · 4 accepted · 1 rejected · written · 4 members attached"; the old "7 memberships superseded" reads from the outbox's `superseded` status |
| 7 | Left out and why, over time | the history of choices: what was left out, by whom, with what reason, and what was later included | scope events | a timeline, lowered rows for leave-outs |

**First three: 1, 2, 3.** They answer the owner's three questions in his
own order (how much, how often, what is not yet), and their sources all
exist on main today; 4 and 5 wait on the rules block and the two-ends
dependency table being real; 6 on the blueprint selector; 7 is a view of
1's history.

## Routine or plain read

These are the natural first routines for the Analytic Library: each is a
pure computation over the ODS, needs no credential, no executor and no
Egeria call, and is wanted in three places (Curate's tab, a Portal tile for
"publication coverage across my resources", and a notebook). So:

- **1 and 2 are routines** in the library (`curation_coverage(ods, slug)`,
  `publish_history(ods, slug, limit)`), with an envelope on the whole and a
  manifest with `family="Curation"`, `source=ods`; RE's tab calls them;
  the Portal's tile lists them through the same registry; the notebook
  proves the third host. They are better first routines than the survey
  trend of the skeleton brief because their value is obvious to the owner
  on the page he is looking at; the skeleton keeps the trend as its
  example only because its data exists on every database. Decided (owner,
  2026-10-07: "good idea for the routine"): `curation_coverage` is the
  extensibility skeleton's first routine; the survey trend joins later
  (`docs/extensibility-walking-skeleton-brief.md` §2b as amended).
- **3 is a plain RE read** for now (it joins E3 rows, scope events and
  verdicts, all RE-shaped), lifted into a routine once the proposal record
  has one shape across kinds (the Enrichment-publishing slice gives it
  one).
- 4 to 7 follow the same split: plain reads until their sources stabilise.

Every row is one sentence with the state word first and the number
labelled by what it counts; counts link to the rows they count (the
commit, the choice, the proof), so "26 undecided" opens the scope tree
filtered to them, which is the cue rule applied to a count.

## The tab until then

**Hide it on Curate** rather than show the generic pane: a tab that lists
"Egeria Native DB Survey · not run yet" on a stage about curation says
nothing a person came for and teaches them to ignore the tab. The stage
strip keeps the tab's place (tabs never disappear per the Enrichment
ruling applies to stages a person navigates, not to a sub-tab with no
content yet), so: the tab stays visible but reads "By analysis · nothing to
show yet · analyses of what you have published are coming" and is not
clickable, which is honest and cheap, and the first slice replaces that
sentence with analysis 1.

## Slice (owner's go)

1. Hide-with-sentence on Curate's tab (one line, with the UI batch).
2. Analyses 1 and 2 as library routines with the envelope, called by the
   tab, listed in the registry, run from a notebook in the test; each row
   links to its rows.
3. Analysis 3 as a plain read.

Tests: a fixture with proof rows, scope events and outbox rows yields the
exact sentences; a resource with no commits reads "nothing published yet ·
<n> known to RE" with `measured`; an unreadable registry reads `not
established`. Gate (owner, by use): coco_pharma's Curate By analysis reads
the published count equal to the gate's read-back, names the 26 undecided
and the one left out, shows three commits with the refresh-timeout failure
sentence on the second, and the proposals line counts the Sales Forecast
assignment as confirmed.
