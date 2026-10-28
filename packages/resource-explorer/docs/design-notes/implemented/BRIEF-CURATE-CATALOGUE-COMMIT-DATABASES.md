# BRIEF — Curate slice B: the catalogue commit for databases, by schema-kind targets (2026-10-05)

From the design session. Dispatched by the coordinator once the second
scratch-test evidence is merged, in its own worktree off main. The owner's
decision (2026-10-05): the schema-kind door, revisited as Egeria changes.

Sources, in order of authority on screen then on mechanism:
`REPLY-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md` §3–§6 and
`REPLY-DESIGNER-CURATE-SCOPE-ROUND-2.md` §3 (the reply wins on screen);
`evidence/CATALOGUE-LEVER-FINDINGS.md`,
`evidence/SCRATCH-CATALOGUER-TEST-2026-10-04.md`,
`evidence/SCRATCH-CATALOGUER-VARIANTS-2026-10-05.md`,
`evidence/SCRATCH-CATALOGUER-TEST-2-2026-10-05.md` (the evidence wins on
mechanism); `DESIGN-EGERIA-SUGGESTIONS-AND-RE-OWN-CATALOGUING.md` §2–§3.

Agent preconditions: `uv sync --all-packages --extra dev` in the worktree
and the import-path line in the implemented note before any test; peer
check before any command opens the shared registry; the implemented note
at `implemented/CURATE-CATALOGUE-COMMIT-DATABASES-IMPLEMENTED.md`; no
connector restarts, ever, from RE or from the agent.

## What it is

"Catalogue" on a database's Curate is a **curation record**
(`curate_commit.py`'s pattern): a queued run whose steps write their
outcomes to the record as they land. It is also /next's Publish for
databases; Classic's publish for databases retires into it. Every state
word on the scope tree after commit derives from a proof row, never from
the branch the code took.

## Steps, in the manifest's order

1. **RE publishes** the server and database elements as today, supplying
   `databaseDescription` and `versionIdentifier` so no `~{…}~` placeholder
   remains, and writing the publish ZoneMembership on the database element
   **before** any target is attached (dependents copy it at creation).
   Owner from the Context judgement is written after read-back as an added
   classification; if changing an existing one is refused, the row says
   "owner set by Egeria's source · can't change from RE".
2. **For each schema confirmed "catalogue"**: create the
   DeployedDatabaseSchema from technology type "PostgreSQL Relational
   Database Schema" (GUID `82a5417c-d882-4271-8444-4c6a996a8bfc`; resolve by
   GUID or the exact display name, the short name returns nothing), with
   qualifiedName `PostgreSQL Relational Database Schema::<server>::<db>.<schema>`,
   under the database element; attach it to the JDBC cataloguer
   (`70dcd0b7-9f06-48ad-ad44-ae4d7a7762aa`) as a **SCHEMA-kind** CatalogTarget
   with no configuration lists and `deleteMethod` ARCHIVE; read the targets
   first (body `{"class":"ResultsRequestBody","graphQueryDepth":0}`, since
   pyegeria's default fails, ISSUE-122) and never attach the same schema
   twice. **Never attach the database element itself and never the server**:
   a database-kind target catalogues every schema's tables directly under
   the database regardless of its lists.
3. **Egeria's survey** of the database, with the chosen schemas passed as
   the survey **request parameter** `includeSchemaNames` (comma-joined).
   A schema name containing a comma cannot be scoped: the manifest says so
   for that schema. Never via the element's connection configuration, which
   the service ignores. Manifest line: "Egeria's survey is limited to your
   chosen schemas".
4. **Optional forced refresh** so the first read-back happens at commit:
   `ServerOps.refresh_integration_connectors("JDBCDatabaseCataloguer",
   "qs-integration-daemon", 120)`, about 16 seconds, synchronous; the
   natural cycle (about 34 minutes observed) is the fallback, and the
   connector's `lastRefreshTime` is shown as the connector's, never a
   target's.

## Decision: RE's own survey report is published whole

**Decision (project owner, 2026-10-06):** "publish whole". Two levels of
publishing run from RE to Egeria. The first pass catalogues the top
element, the database, and attaches the survey reports to it, RE's and
Egeria's, covering everything the credential can see; surveying is
measurement, and measurement is published whole. Cataloguing at finer
granularity is the explicit curation step, and nothing else creates
schema, table or column elements; leaving a schema out means no element,
not no measurement. Cutting RE's report to the declared scope would make
the two reports disagree about the same database for no gain. The
manifest sentence: "RE's survey report is published whole; it describes
all *m* schemas; elements are created for the *n* you chose." The owner
expects many security-related nuances later (a schema that must not be
described in Egeria at all); those become a separate per-schema "don't
survey" choice compiling into the survey's exclude list, in the rules
work, not in this slice.

## Leave-out, two forms, chosen per schema and said in the preview

Before the press, each schema being left out gets a relationships read of
its elements ("what hangs off it"), and the preview names the form:

- **Nothing hangs off** → detach the target and **soft-delete** the schema
  element (the tree goes with it); later re-inclusion works by re-creating
  from the template and re-attaching, with new GUIDs.
- **Something hangs off** (term assignments, lineage) → detach the target
  and **archive** through the delete endpoint with `deleteMethod=ARCHIVE`,
  `forLineage` and `forDuplicateProcessing` true (the `/archive` endpoint
  returns 500 on this build); tables and columns are archived with it; the
  row reads "archived in Egeria · <time>" and later "can't be re-included
  until Egeria restores archived elements" (S19).

If the relationships read fails, the row says "couldn't check what hangs
off it", never nothing, and the commit button is disabled for that schema.
The cataloguer keeps refreshing a detached target until its connector
restarts (S17): the row reads "Egeria's cataloguer still lists this schema
until its connector restarts · nothing is recreated", from the read-back.
RE never restarts a connector.

## Re-inclusion

After a soft delete: re-create and re-attach. After an archive: refused
with the S19 sentence. If a schema type `<dbQN>::<schema>_schemaType`
already exists (the orphan case on older builds), adopt it rather than
create.

## Per-node proof (the reply's table)

catalogued (element read back by qualifiedName) · attached, waiting (target
read back, no elements yet, connector time shown as connector-wide) ·
queued (outbox row) · failed (Egeria's word) · left out (scope record) ·
removed or archived (absence or archive read back; the row stays).
The "Saved in Resource Explorer · not yet catalogued in Egeria" header
line becomes state-derived here, never a constant.

## What Egeria cannot honour yet

Table and column choices within a schema: the tree keeps them and says
"Egeria catalogues whole schemas · table choices are kept for when it can".
Depth below tables-and-columns: not offered until Egeria has a depth
option (S2). The pre-attach inventory check flags `_` and `%` collisions
("⚠ Egeria's listing for <schema> would also return <other>") and disables
the commit while any stands.

## Deletes

Every delete RE performs is per element, leaf first, `forLineage` true;
never a cascade (S18).

## Tests

A fake Egeria for the commit steps with every proof-row state; both
leave-out forms; the comma-bearing schema name; the collision check on a
fixture with `a_b`/`aXb` and `x_y`/`xZy`; re-inclusion after soft delete,
refusal after archive, adoption of a pre-existing schema type; a
routing-level harness test for the manifest and the per-node states; red
runs on each.

## Owner's gate, on 8810 after merge, coco_pharma, the 29-schema scope

1. The manifest lists the three mechanisms, the count of schema targets to
   attach and the survey's schema list; Catalogue runs; the tree shows
   queued → attached, waiting → catalogued per schema with read-back times,
   and tables and columns appear under each schema and nowhere under the
   database.
2. Leaving out a schema with nothing hanging off it shows the soft-delete
   preview, then "removed · was catalogued" after the refresh; re-including
   it succeeds.
3. Leaving out a schema with a term assignment shows the archive preview
   naming what hangs off it, then "archived"; re-including it is refused
   with the S19 sentence.
4. Any two colliding names are flagged before commit; otherwise a fixture
   proves it.
5. After an Egeria restart that keeps the store, the scope and the states
   survive; after a reset, the re-commit recreates them from the scope
   record.
6. Classic's database Publish is gone; the header line reads from state.
