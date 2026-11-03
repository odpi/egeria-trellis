# HANDOFF — the Resource Explorer design and gate session (2026-10-16)

For the next architecture session. Written by the outgoing design session
on the owner's question "is there anything to hand off?". Everything here
is on main unless a branch is named; the owner (Dan) decides, merges and
runs infrastructure; the "Resource-explorer PR/CI merge" session builds,
tests, opens PRs and serves the gate build on 8813; the designer is a
separate session whose replies the owner pushes.

## 1. The role and its rules

- Read `WORKING-RULES-STANDING-CLEARANCES.md` first: what needs no word
  (scratch-schema rounds, gate restarts with the running-work, DDL-grep
  and uvloop boot checks, docs PRs, reads) and what stays a hard gate for
  the owner's direct word (DDL on 5442, live Egeria writes, archive or
  delete, the clean-up `--apply`, platform restarts, credentials and
  zones). A relay of the owner's word is not his word; PR/CI takes
  decisions from him in its own session and will say so.
- Honesty rules, unchanged: status words derive from proof rows; a proof
  row proves one element by GUID after a read-back; "never measured" and
  "measured, nothing found" are different sentences; follow Egeria's
  lead on annotation types (extend by a distinct type, never a changed
  meaning); two publish channels (survey and Enrichment publish
  annotations; Curate publishes facts; proposals are annotations).
- Work in your own worktree (`~/localGit/egeria-v6/wt-design-asks`,
  branches from `origin/main`), commits signed and DCO'd, never the shared
  checkout, never a bare `git stash`. Docs land as one-file PRs that
  PR/CI opens; the owner merges.
- Report by delta; no status lists unless asked. The owner has corrected
  the design session for: naming a home without reading the list, calling
  a planned thing new, imposing a hold on a platform finding without
  re-reading the mechanism (2026-10-13, lifted the same hour), and
  letting a slice contradict a later ruling (step 4's negative 2).

## 2. Where the record is

- `PLAN-OPEN-FRONTS-2026-10-14.md`: the owner's decisions of 2026-10-14,
  the six new fronts and the design session's writing order.
- The engine-host line: `DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md` (with
  the owner's and Mandy's answers), `SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md`,
  `SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md`,
  `SLICE-ANALYTIC-LIBRARY-STEPS-NEXT-FIVE.md`; egeria-python ISSUE-150
  for the declaration shape.
- The reader's side: `DESIGN-AMUNDSEN-MIGRATION-ASSESSMENT.md` (source of
  record is the owner's reviewed web document, linked in its first line)
  and `SPEC-RESOURCE-BROWSER-PORTAL-APP.md` (the Resource Locator, built
  by the workspaces session; phase 3 work lists done).
- Interfaces: `DESIGN-MCP-SERVER-AND-A2A-GAPS.md` (option 2 on caller
  identity: synchronous work as the caller, queued writes on the service
  account with the requester recorded).
- Curate: the designer's split-by-job reply and `RULING-PUBLISH-NOT-CATALOG.md`
  (both on `re/design-curate-split-by-job`, unsigned, not merged), the
  cataloguing-at-scale ask (merged) and its reply (on
  `re/design-cataloguing-at-scale`, unsigned, stacked on the split branch).
- `DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md`; `RULING-OWNER-FACT-VS-OWNERSHIP-CLASSIFICATION.md`.
- The Mandy note (PR/CI keeps it; the owner's Downloads copy is refreshed)
  holds every Egeria-side question, numbered.

## 3. Open with the owner (only he can close these)

- Adopt the Curate split by job: still a proposal; the rules slice and the
  Resource Locator both assume its Contents tab.
- Rules over data classes (the cataloguing reply's open point).
- Prompt the designer to sign and rebase the two unsigned branches; no PR
  can open until then, and the docs clean-up's two held replies wait on
  them.
- The Artifacts note's three questions (tab placement, publishing a
  confirmed doc link to Egeria, private symbols).
- The journal leftover on Erin's profile (a Portal test's residue) and the
  unzoned personal project `a15e3ad4` (zone it or delete it).
- The optional one private write for a true 6.2 zone-denial proof.
- The CI duplication inventory, when PR/CI delivers it.
- Zones discussion and the RFA/to-do work with Mandy; the design session
  owes a use-case document for the latter (§4).

## 4. Documents the design session owes, in order (from the plan)

1. The questions-and-analytics review: 95 questions on main, per-question
   verdict (keep, merge, retire, add); inventory numbers are in the plan.
2. RFA and to-dos: use cases, requirements and issues across Egeria and
   trellis, for the owner's session with Mandy.
3. Code ingestion rules: selective by role (core, test, generated,
   vendored) and by shape (documentation folders, message catalogues);
   where the choice lives. Shares the file-role classifier with the
   Artifacts slice 1.
4. Child module as a resource, against Egeria's database/schema and
   software-capability parallels.
5. Ad hoc questions with clarifying agents (the answering app), after the
   skeleton; the simpler app with an investigation handoff, after the
   Locator walk; files; Open Data and Open Models.
6. The rules slice brief (schema-level rules, arrivals, the Egeria S1/S11
   seam) once the split is adopted and the designer branches land.

## 5. In flight with PR/CI (no design action unless asked)

Superseded on the same day: by the evening of 2026-10-16 the gate build
served main 14dade2d with #712–#721 merged and no open PRs (coordinator
handoff review). The live state of play is the top of
`~/.re-gate/HANDOFF-RE-OPERATIONS.md`; the list below is what was in
flight when this note was written that morning.

Caller-identity reads PR (built, in review; the two-user live probe is the
owner's to run); 6.2 header readers and S19 re-include-on-confirmation;
membership `confidence` rename with the relationship-property audit;
zone-control start-up check; typed valid-value finds with the data-class
canary heal; pyegeria 6.2.2 bump with its two audits; artifact markers and
order fixes, then the `survey_commit_sha` and `file_role` columns (DDL,
owner's word given); step 4 fix branch (standalone staleness from
snapshots; empty sentences) and its PR; then case 3 and the first three
library steps (owner's answers folded in); inline publish as enqueue-only;
the connector-type start-up check; the docs clean-up steps 7 and 8.

## 6. Facts a new session will otherwise rediscover

- Egeria 6.2's `ElementHeader` has dedicated fields (anchor,
  zoneMembership, ownership, memento, dataScope, …) and no generic
  classifications list; MetadataExpert routes still return a list,
  AssetMaker does not. DataScope's usage properties
  (`readsPerDay`, `readsInWindow`, `readersInWindow`, `lastReadInWindow`,
  the window and the analysis time) are the usage measure, live on
  quickstart.
- Egeria renames an archived element `<qn>_archivedOn_<date>`; archiving
  any part of a database tree archives the tree (ISSUE-117, block ON).
- A userId-named zone only grants; after Egeria e9fd817f4c every other
  named zone without an access control locks everyone out; RE's private
  pair is safe; elements with no zone stay open to all.
- `CollectionMembership` carries `confidence`, SemanticAssignment carries
  `termAssignmentStatus`; the old names are silently dropped.
- The JDBC cataloguer copies no table or column comments, has no key
  switch, skips a foreign key to an uncatalogued schema silently, and
  works at whole-schema targets (S1, S11; fix on Egeria's list).
- `WorkingSet` is the official type; RE reads its collections only by
  stored GUID; the Portal's lists use `WorkingSet::portal::<owner>::…`.
- The memory directory for this role is at
  `~/.claude/projects/-Users-dwolfson/memory/`; its index lists every
  note above by date.
