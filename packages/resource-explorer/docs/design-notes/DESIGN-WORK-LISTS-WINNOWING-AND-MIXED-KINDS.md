# DESIGN — Work lists as a winnowing bench, and lists of more than one kind (2026-10-23)

Owner's discussion (2026-10-23, to the architect session): work lists have
been a convenient way to run and analyse a group of resources; the same
mechanism could filter and winnow a list of candidates on criteria such as a
data lens or currency of maintenance, and could anchor chat questions about
the differences and relationships between the members. And: the code assumes
a work list holds one kind of resource. Is that required, and would groups of
related resources be convenient to consider together? The owner has created
no mixed-kind list because of that assumption, so the absence of mixed lists
on 8813 is not evidence of no demand.

Discussion note, no builder. Rulings to take from it are listed in §6.

## 1. What is true today

- A work list is one kind by construction: `work_lists.entity_type` is a
  column on the list (default `repo`), and members are resolved against
  that one type (`work_lists.py`, `member_egeria_guid`). A mixed list cannot
  be created through the API, so none exists.
- The bench is "rows × one stage's questions, narrowed by shortlist"
  (REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md, 2026-10-01). A shortlist
  is a child list that names its parent, so the trail of what it was
  shortlisted from survives.
- An investigation's scope is any kind, and carries the data lens: one fit
  cell per declared dimension, states ✓ fits / ∅ does not fit / ? could not
  check (same reply).
- After the 2026-10-22 questions edit, 24 questions apply to every kind that
  has an adapter (repository, database, file system), and the overlap
  question (Q-0011, "which resources overlap with this one — the same data,
  a subset, a stand-in or a replacement — and how does this one differ?")
  applies to every kind at resource level.
- Chat compiles by question id over declared extents under a budget, with
  omissions written in (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md).
- Nothing computes an edge between two members of a list. Dependency edges
  exist for repositories (to the technologies they depend on), and the
  Curate dependency table and blueprints exist per resource; none of it is
  read list-wide.

## 2. Winnowing: every headline is a column, every column a filter

The bench already shows each member's headline per question of the open
stage. Winnowing is the verb that is missing, not the data:

- **Columns**: each question headline is a column, typed by its result kind
  (measure, finding, list, …) so it can be sorted and filtered the way its
  kind allows: a measure by threshold, a finding by state word, a list by
  "contains", a date (currency of maintenance, Q-0001) by age.
- **The lens as columns**: when the list belongs to an investigation, the
  fit row's cells join the columns, one per declared dimension, so "fits the
  lens on period and place" is a filter like any other.
- **Three buckets, never two**: a filter splits the rows into kept, dropped
  and could-not-check. A member whose headline is absent for that question
  (never measured, not permitted, not applicable) goes into the third bucket
  with the absence sentence, visible, never silently dropped and never
  silently kept. Find-absence-as-answer applies to filters exactly as to
  headlines.
- **Shortlist = saved filter + survivors**: the shortlist child list stores
  the filter expression (question id, operator, value, lens dimension) with
  the as-of of the headlines it was applied to, alongside the member rows.
  Reopening a shortlist shows "kept 7 of 23 on <criteria> as of <date>";
  re-applying the filter on fresher headlines is a press, and the diff
  ("2 would now drop, 1 would now join") is shown before the shortlist
  changes. The trail rule stands: a shortlist always says what it was cut
  from and on what.
- **Cost before the cut**: a filter on a question nobody has run for some
  members is a run, not a filter. The bench says "N members have no answer
  to Q-00NN · run <definition> on them first (price)", and the run is the
  existing work-list batch run scoped to those members.

## 3. Chat over a list

List-scoped chat is the existing compile with the list as scope: for each
question the list's stage carries, the members' headlines are the extents,
the budget rule writes in what was omitted ("23 members, 12 shown; the rest
omitted"), and the answer stores the row ids it saw.

- **Difference questions** ("which of these is best maintained?", "which
  cover 2024?") come free from that compile; they are column questions in
  words.
- **Relationship questions** ("do any of these depend on each other?",
  "which two are the same data?") do not, because no list-level analysis
  produces an edge today. They need one: §4.

## 4. A list-level relationship analysis

One analysis over a list, zero fetch, declared with result kind `graph`
(nodes = members, edges typed), run from the bench:

| Edge | Source | Kinds |
|---|---|---|
| overlaps / same / subset / stand-in | Q-0011's analysis (fingerprint, subject and coverage signals) pairwise over members | database, file system, repository |
| depends on | repository dependency rows matched to a member that provides the technology (a database by its engine, a repository by its package name) | repository → repository, repository → database |
| talks to | `egeria_interfaces` and `deployment_evidence` endpoints matched to a member's registered endpoint | repository → database, repository → file system |
| documents | folder_narratives / doc links naming another member | file system → database, repository → database |
| shares subjects | subject_signals intersection above a declared threshold | any |

Each edge carries its basis and a confidence form (measured for a matched
endpoint, inferred for a name match, with the basis stated), and each
absence is a sentence ("no dependency rows for 4 members: dependency
analysis not run"). The graph renders through slice 1's generic graph
renderer, the same one Ports & Wires uses, so this analysis adds no drawing
code. The chat's relationship questions route to this analysis's extents.
Cost: pairwise over N members is N² reads of stored rows; the bench prices it
from N and refuses above a declared cap with "narrow the list first".

## 5. Lists of more than one kind

The one-kind rule exists for the columns, not for the members: a stage's
questions differ by kind, and a bench whose columns mean different things
per row is not a bench. So the rule to relax is "one kind per list", and the
rule to keep is "one column set per view":

- **A mixed list shows the shared columns**: the questions every kind
  present carries (the 24 any-kind rows, plus any row whose `Resource
  Types` covers all kinds in the list). Row cells for a question a member's
  kind does not carry do not exist; they are not "could not check", they are
  "not asked of this kind", and the column set already excludes them.
- **Filter to one kind opens that kind's full columns**: a chip per kind
  present ("12 repositories · 4 databases · 2 file systems"); pressing one
  narrows the rows and widens the columns to that kind's stage questions;
  pressing none shows the shared set. The stage click rule (run stages
  re-scope the list, frame stages close it) is unchanged.
- **Runs stay per kind**: a batch run on a mixed list dispatches one
  definition per kind present (the definition catalog is per kind), and
  the bench shows one run line per kind. "Run on all" is three runs with
  three prices, said so.
- **Storage**: `entity_type` moves from the list to the member
  (`work_list_members.entity_type`, backfilled from the list), and the list
  keeps a derived `kinds` summary for the chips. Member resolution already
  takes an entity type per call. That is DDL on 5442: the owner's separate
  go, deployment-record template in the PR.
- **Publish**: a WorkingSet in Egeria holds assets of any type already;
  nothing changes on the Egeria side.
- **Related groups are two different things**, and the words should keep
  them apart: a mixed list is for *comparison* (candidates winnowed on shared
  criteria); a *system* (a repository with its database and its file system)
  is better held as an investigation scope with relationship edges from §4,
  because what makes it a group is the edges, not the listing. The bench
  offers "Start an investigation from this list…" already; §4's edges give
  that investigation its reason.

## 6. Rulings to take, and the order

1. Relax one-kind-per-list to one-column-set-per-view (§5). Owner's word;
   it bends the 2026-10-01 ruling and the designer should see the chips and
   the column rule as a wireframe before a builder does.
2. Winnowing slice (§2): column filters with the three buckets and the
   saved-filter shortlist, on today's one-kind lists. No DDL beyond the
   shortlist's filter columns; UI-batch plus one Python change for the
   stored filter.
3. Mixed-kind slice (§5): member-level entity_type (DDL), kind chips,
   shared-column rule, per-kind runs.
4. Relationship analysis (§4), after slice 1's graph renderer lands; chat
   relationship questions route to it.
5. Measure after 2 and 3, not before: how many lists get a filter, how many
   are mixed, and which edges people open. The count of mixed lists today is
   zero by construction and says nothing.

## 7. Open

- Whether a shortlist should re-apply automatically when its members'
  headlines refresh (my position: never; a press with the diff shown).
- The cap for pairwise relationship analysis (proposal: 50 members, priced
  by stored-row reads measured on the first run).
- Whether "talks to" edges belong in Curate's dependency table as well as
  the list graph (they are the same fact; one store, two views).

## 8. Rulings as built (slice 1, 2026-10-28)

The owner, 2026-10-28: "I answered all the questions previously - we should be able to move forward?", then
"yes, start Q3 and the work lists too". A search of this note, the `RULING-*` files, `exchange/REPLY-*`, the
coordinator handoffs and the Architect Session found no recorded answer to the §6 or §7 items, so slice 1 was
built on this note's own positions and marked "assumed". The owner then answered each of them on 2026-10-28,
relayed by the coordinator session. One answer, §7.1, differs from this note's position:

**Decision (project owner, 2026-10-28):** §6.1 and §6.3, lists of more than one kind: "keep, as built".

**Decision (project owner, 2026-10-28):** §6.2, winnowing: "keep, as built".

**Decision (project owner, 2026-10-28):** §7.1, re-apply: "automatic". A shortlist re-applies its filter when
its members' answers refresh. Each re-apply is recorded with its as-of and its diff, and the shortlist shows
"re-applied automatically <time>: +N / −M" with the diff one click away. A member that moves to could-not-check
is never silently dropped. "Re-apply now" stays as a manual press.

**Decision (project owner, 2026-10-28):** the DDL (`work_lists.filter_json`, `work_lists.filter_as_of`,
`work_list_members.entity_type` with the wider key) is approved to apply on the next 8813 restart after merge.

| Item | Answer built | Source |
|---|---|---|
| §6.1 relax one kind per list to one column set per view | relaxed | owner, 2026-10-28 (above). Related, recorded 2026-10-24: one shared member resolver, `member_egeria_guid` (#856). |
| §6.2 winnowing slice | typed column filters, three buckets, saved-filter shortlist, re-apply | owner, 2026-10-28 |
| §6.3 mixed-kind slice | `work_list_members.entity_type`, kind chips, shared columns, per-kind runs | owner, 2026-10-28; DDL approved for the restart after merge |
| §6.4 relationship analysis | not built | left for later |
| §6.5 measure after 2 and 3 | not built | nothing to measure until 2 and 3 ship |
| §7.1 shortlist re-applies automatically? | yes, automatic, recorded; manual "re-apply now" kept | owner, 2026-10-28 |
| §7.2 pairwise cap | not applicable (no §4) | — |
| §7.3 "talks to" edges in Curate too | not applicable (no §4) | — |

How §7.1 is built, and where its trigger is:

- **Trigger: on read.** When a filtered shortlist is opened in the bench, its parent's current answers are read
  for the filtered columns and compared by as-of with the cut. If any answer is newer than the filter's
  `applied_at`, the filter is re-applied and the re-apply is recorded as automatic. A run finishing does not
  trigger it on its own. The trigger belongs at the run queue's completion (`run_queue._handle_analysis_run`),
  which needs the evaluator (`next/winnow.js`) in Python as well: a follow-up.
- **The record:** the filter's `reapplies` holds the last 20 re-applies, newest last. Each entry has when it ran,
  whether it was automatic or pressed, the as-of of the answers it read (`answers_as_of`), and who joined, who
  dropped and who was held.
- **Held, never silently dropped:** a current member that moves to could-not-check stays a member and is named
  as held, with its sentence. A member the filter checks and fails is dropped.

Choices the build made that §2 and §5 leave open, still **assumed: owner to confirm**:

- The `kinds` summary is derived on every read from the members, not stored on the list, so it cannot drift from
  them. `work_lists.entity_type` stays as the kind a member is given when none is named.
- A shortlist cut by a filter holds the kept bucket. The could-not-check members of the first cut are not
  carried in, and the stored filter names each of them with its sentence.
- The lens columns of §2 are absent on this build: no data lens is bound to an investigation yet
  (`batch_io.NO_LENS`). The bench says so for a list that belongs to an investigation. The stored filter
  already accepts a `lens_dimension`, so nothing changes shape when a lens arrives.
