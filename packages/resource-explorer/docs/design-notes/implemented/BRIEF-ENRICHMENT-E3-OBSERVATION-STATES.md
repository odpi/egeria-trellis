# BRIEF — Enrichment E3: observation states and the evidence fetch (2026-09-30)

From the design session; dispatched by the coordinator on 2026-09-30 in
its own worktree off main (5f152c74). Source rulings:
`REPLY-DESIGNER-ENRICHMENT-STAGE-IA.md` (2026-09-29) and the project
owner's amendment under it. E1 (`ENRICHMENT-E1-CONTEXT-TAB`) and E2
(`ENRICHMENT-E2-DOC-SOURCES-RESEAT`) are merged; this slice builds on
both.

Agent preconditions: `uv sync --all-packages --extra dev` in the worktree,
then print `python -c "import resource_explorer; print(resource_explorer.__file__)"`
into the implemented note before any test run; peer check before any
command that opens the shared registry, dry runs included (a branch's
migrations run on construction).

## What E2's gate left on screen

coco_pharma's Context rail listed repository analyses
(`repository_health`, `interface_surface`, …) as "never run". They do not
apply to a database. "Never run" is a false claim; the fetch used `repo`
as its default entity type, as the old Enrichment form always did.

## The slice

1. **Evidence fetch by kind.** `fetchEnrichmentEvidence` passes the
   resource's real entity type, and the rail lists only analyses
   catalogued for that kind. An analysis that does not apply to the kind is
   absent, not "never run". If no catalogued analysis applies yet, the rail
   says, in one line, "no enrichment evidence is catalogued for databases
   yet"; never an empty panel.

2. **Four observation row states**, computed by one pure function over
   persisted rows, table-tested over every combination:

   | state | proof | drawing |
   |---|---|---|
   | proposed | a survey measured the same fact; no person has acted | muted, no ✓, "proposed by *analysis* · *time*" |
   | confirmed | a person accepted the proposal or typed the value | signed (who · when), feeds line |
   | overridden | a person set a value differing from the measured one | signed; the measured value stays visible beside it |
   | survey-now-disagrees | a later survey measured a value differing from the confirmed one | "⚠ review"; cleared only by a person choosing |

   Proposals exist only for observations, never judgements (sensitivity,
   criticality, use, owner, every "does it fit"). The only proposing pair
   today is licence ← `license_classification`, on repositories.

3. **Licence as an observation row on Context for every kind.** On a
   repository it may be proposed from `license_classification`. On a
   database it is human-supplied only; the row says "no survey measures
   this for databases", which takes over the job of the Scouting ◌ row for
   this fact. Signed, feeds line, the shared row anatomy.

4. **The `datdba` owner line on databases.** The Postgres owner role from
   the survey shows on the owner judgement row as material, "database owner
   role: *name* (measured)", never as a proposal, because owner is a
   judgement.

Honesty rules hold throughout: every state word derives from persisted
rows (`status words derive from proof rows`); absence is drawn as a word,
never as nothing; the accent colour is for controls, never states.

## Owner's gate, on 8813, amundsen then coco_pharma

1. coco_pharma's rail shows only database analyses, or the one-line "no
   enrichment evidence is catalogued for databases yet"; no repository
   analysis name appears.
2. amundsen's licence row shows a proposed value from the licence scan,
   muted, with "proposed by license_classification · *time*"; accepting it
   makes the row confirmed and signed; typing a different value makes it
   overridden with the measured value still visible.
3. After re-running `license_classification` with the same result, the
   confirmed row is unchanged; a test proves the disagree state with a
   differing measurement.
4. coco_pharma's licence row reads "no survey measures this for databases"
   and accepts a typed value, signed.
5. coco_pharma's owner row shows the measured database owner role as
   material, and it is never offered as a proposal.
6. Nothing from E1/E2 regressed: Context sections, signed rows, feeds
   lines, doc sources, Egeria's own surveys under the map.

Merge on green after all six pass. Serve reports carry the three-line
provenance standard (`HANDOFF-PR-CI-MERGE-SESSION-2026-09-28.md`).
