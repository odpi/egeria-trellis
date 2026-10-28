# For the discussion with the Egeria leads (2026-10-05)

Prepared by the Resource Explorer design session for the project owner, to
take to the Egeria leads. Everything here was read from the Egeria source at
df82f4fe, observed on the dev platform, or measured in the scratch
cataloguer test of 2026-10-04/05 (`evidence/SCRATCH-CATALOGUER-TEST-2026-10-04.md`,
`evidence/CATALOGUE-LEVER-FINDINGS.md`). The full suggestion list with
file:line evidence is `DESIGN-EGERIA-SUGGESTIONS-AND-RE-OWN-CATALOGUING.md`
(S1–S15). This note is the agenda: six topics, what we found, what we ask.

**Version caveat, resolved the same day.** The Egeria lead fixed bugs on
2026-10-05 and the owner rebuilt the dev platform from that day's upstream
image (6.2-SNAPSHOT, index digest bf2b3799…, built 14:19 UTC, upstream main
up to PR #9357). Every finding below was then re-established against that
build: the JDBC integration connector jar in it is byte-identical to the
2026-10-03 one, so the cataloguer findings (topics 3 and 4, S1–S3, S7, S9,
S12–S14, S16–S20) stand on current code with no re-check needed; the one
item the fixes resolved is the survey's schema scope (S4, landed in
ebafb08fdc and confirmed live); arrays on a database-kind target are now
honoured (S11, partly) though its database-level pass remains; and the
rehearsal findings in topic 7 were made on the rebuilt platform itself.
Source citations are to df82f4fe where the file is unchanged to upstream
main (`JdbcMetadata.java`, `TransferCustomizations.java`, the
`getTables`/`getColumns` call sites) and to the running jar otherwise.
Anything the leads fix after 2026-10-05 needs the same one-comparison
re-check: the scratch scripts are kept for it.

*What the running platform is (established 2026-10-05, read-only):* the
dev platform reports version 6.2-SNAPSHOT; the quickstart image was built
2026-10-03 from a pinned upstream `odpi/egeria-platform:latest` digest, not
from our checkout; its JDBC connector jar contains the 2026-09-22 commit
fcb804149b (new `CatalogTargetKind`, `JDBCDatabaseCatalogTarget`), so the
cataloguer that ran the scratch test is newer than our df82f4fe citations
for it. `JdbcMetadata.java` and `TransferCustomizations.java` are
byte-identical between df82f4fe and upstream main, so topic 3's pattern
and exact-match findings stand on current source. Upstream main is 70
commits ahead at d395c18f; any fix from 2026-10-05 is absent from the
running image, so a re-check needs a new image pin and a platform
restart.

*A lever we had not read, from that newer code:* a catalog target can
now be of kind SCHEMA ("one schema of a database, and nothing else in that
database"), handed a DeployedDatabaseSchema by the
`catalog-<vendor>-schema` processes, as well as kind DATABASE ("every
schema in it"). A per-schema scope without include lists is a candidate
third door for topic 4: RE creates the DeployedDatabaseSchema for each
chosen schema and attaches each as a SCHEMA target, and the cataloguer does
tables and columns. Tested on the scratch state on 2026-10-05: it works
(one refresh created exactly that schema's tables and columns under it,
nothing else), and the owner chose it for RE's commit.

*After the 2026-10-05 rebuild from today's upstream image:* the JDBC
cataloguer jar is byte-identical to the 2026-10-03 one, so topics 3 and 4's
cataloguer findings stand; what landed is scope on the database survey
(`includeSchemaNames` / `excludeSchemaNames`, ebafb08fdc), which closes
topic 2's survey item once a run confirms it, and a platform restart was
observed to clear the cataloguer's cached target list (a detached target
kept being refreshed until then).

*Second scratch run, same day, on today's build:* the survey scope works as
a request parameter (an array on the element's connection is ignored, and
a comma inside a schema name cannot be expressed); arrays on a
database-kind target are now honoured though its database-level pass
remains; schema-kind targets alone produce no database-level pass; a
single-connector restart clears a detached target; archiving a schema
element archives its tables and columns, but an archived schema cannot be
re-included (the template create fails 400 and no restore path was found,
S19), and the `/archive` endpoint itself returns 500 on this build (S20).
Topic 4's door stands on this evidence, with leave-out in two forms.

## 1. The annotation vocabulary and an honesty envelope

**What RE needs.** Every analytic result, Egeria's surveys and RE's own
steps alike, carries a small required envelope: *measured*, *not
established* with a reason, or *not applicable*, plus the run that produced
it. RE's whole user interface derives its state words from that
distinction, and a result that renders "nothing" where it never looked is
the defect we keep paying for.

**Where Egeria is today.** The PostgreSQL database survey emits
ResourceMeasureAnnotations with JSON measurements at database, schema,
table and column level, and says nothing about what it could not measure:
a column absent from `pg_stats` is simply absent from the report (S10).

**To discuss.** The extension of the annotation types that you and Mandy
are already working on: can the envelope be part of it, as a small set of
required attributes on every annotation type, so RE's readers become
generic over annotation type rather than per analysis
(`DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md` A2, R5).

## 2. Scope: what a steward chooses gets catalogued, nothing more

**What RE needs.** A steward's confirmed choice of schemas, tables and
depth, stored and signed in RE, compiled into whatever Egeria needs so that
a republish or a reset keeps it (the designer's Curate scope reply). The
Coco case: coco_pharma grew from 7 to 29 schemas when the scenarios were
extended; without a stored scope, whatever is added is catalogued silently.

**Where Egeria is today.** The JDBC cataloguer's include and exclude lists
are the only lever, and in the test a JSON array in the catalog target's
`configurationProperties` was **not honoured** (read back as a flattened
string; no schema processed) (S11); the `catalog-postgres-database` process
passes one string per list (S6); names match plain, exact, case-sensitive
strings, so "orders in sales but not in archive" cannot be said (S1); there
is no depth option (S2); the database survey has no scope at all, it
measures every non-system schema, table and column (S4). String forms of
the lists are being tried today.

**To discuss.** Schema-qualified names or per-schema lists; a depth option;
arrays or a documented string form; and scope on the database survey, so
that a steward who left out 22 schemas does not see them measured anyway.

## 3. Correctness of the cataloguer on real names

**What we found.** The cataloguer passes real schema and table names back
to `DatabaseMetaData.getTables` / `getColumns` as LIKE patterns: in the
test, table `x_y` received `xZy`'s columns, and `p%t` received about 65
`pg_catalog` columns (S3, S14). A database-level pass catalogued the tables
of five non-public schemas directly under the database when the schema pass
did not run, which the docs' "schema defaults to `public`" rule does not
explain (S12). A soft-deleted schema left its schema type and table active
and orphaned (S13).

**To discuss.** Escaping `_` and `%` or filtering returned rows by exact
name; restricting the public default to `public`; deleting or archiving
dependents with a stale parent. The owner believes guards exist; the
evidence says where to look.

## 4. Mechanisms, and which one RE should drive

**What we found.** Three separate mechanisms put a database into Egeria:
RE's publish (assets, report, annotations, no schema elements); the native
survey (annotations only); the two-stage cataloguer (the only element
creator), reached by attaching a catalog target, with elements arriving on
the daemon's cycle (about 34 minutes in practice, 60 configured; a forced
refresh is synchronous, 16 seconds). No Egeria process combines cataloguing
with surveying; "Catalog and Survey" is RE's own name for its own method.

**To discuss.** Whether RE should attach Egeria's cataloguer (ongoing sync
for free, scope limited by the lists above) or create the schema, table and
column elements itself for the exact scope and attach the cataloguer later
for sync (the second door). The condition for both is that the two agree on
qualified names (`<dbQN>::<schema>`, `<parentQN>::<table>`,
`<tableQN>::<column>`), which the test could not yet establish because the
schema pass never ran. Also: the docs' dotted four-level name versus the
stored `::` identities, recorded side by side.

## 5. Relationships RE will propose: repository informs database

**What RE needs.** When a repository defines, loads, reads or governs a
database (the Coco data lives in egeria-workspaces and loaded coco_pharma),
RE measures the mechanism and proposes the relation; a person confirms; RE
publishes it. The owner's ruling: it is **not lineage**, which implies
information flow; it is closer to a dependency.

**To discuss.** Which relationship type carries it, and whether, once
published, Egeria's own watchers (governance actions, Notification Manager)
can raise a change at one end against the other, so RE is not the only
detector (`DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md` §13).

## 7. Zones, identities and the engines (new, from the live rehearsal)

**What we found.** RE's catalogue commit wrote ZoneMembership
`egeria-runtime` on the database element before anything else, as the
Curate design described. From that moment the platform's security
connector refused every later write on that element and under it
(OPEN-METADATA-SECURITY-0011): by RE's sign-in persona (owner
classification, schema creates, delete, clearing the zone), by the
PostgreSQL survey engine's identity (the native survey threw and left a
report with no annotations), and by the OpenLineage cataloguer's. RE could
not undo the zone it had set. So a zone a steward chooses can silently
disable Egeria's own engines on the element, and nothing warns before the
write.

**To discuss.** Which zones the survey engines, the cataloguers and an
RE persona may write in under the Coco security model, so RE can offer
only those; whether a zone write that would lock out the platform's own
engine identities should be refused or warned about by Egeria rather than
discovered afterwards; and whether a publish zone is best set last, after
the targets and the survey, or by the daemon's default zones with no write
from RE at all. Until this is answered RE writes no zone and shows the
element's zones as a read-back fact.

**Also from the rehearsal, ours not Egeria's:** RE's gateway was parsing
the wrong response shapes for elements, children and relationships, so
every live read came back empty; the live payloads are now the fixtures.
Worth saying only because the fix on our side relies on those shapes
staying stable: a note of which response classes are contract would help.

## 6. Smaller items with evidence

- Per-target status and last-run time on the CatalogTarget relationship,
  which today has neither; only the connector reports a time (S5).
- `includeViewNames` / `excludeViewNames` declared and never read (S7).
- `deleteMethod` default (null = soft delete, drops term assignments) and
  `LOOK_FOR_LINEAGE` as the archive-by-default choice (S8).
- No system schemas excluded by default in the JDBC cataloguer (S9).
- Template-created elements keep `~{…}~` placeholders in description and
  version when a caller leaves them unset (S15).
- The SecretsStoreCataloguer catalogues any `.omsecrets` file it can see,
  including throwaways.
- pyegeria: `AssetMaker.get_catalog_targets` sends a relationship type as
  the element type and always fails (egeria-python ISSUE-122, with the
  working body).
- A detached catalog target keeps being refreshed until its connector
  restarts (S17); a restart of that one connector suffices.
- Re-including an archived schema has no path (S19); the `/archive`
  endpoint returns 500 on the 2026-10-05 build (S20); cascade delete by an
  ordinary user fails over cataloguer-created children (S18).

## Answers from the Egeria leads (2026-10-05, evening review)

Recorded as given, with RE's response.

1. **Annotation types:** yes, they will be extended. RE supplies a
   half-page attribute list for the envelope (state: measured / not
   established with reason / not applicable; the producing run).
2. **Scope:** "the latest fix should have addressed this." The survey half
   is addressed and confirmed live (S4). The cataloguer half is in no build
   we have run (the connector jar is byte-identical across the rebuild);
   if the fix is on the unmerged branch, the scratch scripts re-check it
   the day it ships.
3. **`_` and `%`:** RE's proposal is to list with a null pattern and filter
   by exact equality in Java, one `getTables` per schema and one
   `getColumns` per schema grouped by exact table name, which removes the
   pattern arguments and matches how the lists are already evaluated;
   escaping with the driver's search-string escape is the alternative,
   workable but per call site and driver-dependent.
4. **The cataloguer should create the schema:** agreed in principle; RE will
   call the `catalog-postgres-schema` process per chosen schema instead of
   creating the DeployedDatabaseSchema from the template, provided the
   process takes an existing database element and the schema name and
   creates the schema under it rather than beside a new database. That is
   the first read-back of the next scratch run; RE's own creation stays
   the fallback.
5. **The repository-to-database relation:** there will be several
   relationship types; a list is forthcoming. RE keeps confirmed relations
   local with their mechanism until the list arrives.
6. (no answer needed)
7. **Zones:** start with a default zone, expand to the user's choice later.
   So RE's commit writes no zone and relies on the default; the one fact
   still needed is the default zone's name, and that the survey engine, the
   cataloguer and the RE persona may all write in it, which the next
   scratch run confirms before any real database is catalogued.

### Clarifications from the owner, later the same evening

- **On scope names (topic 2):** schema-qualified names are possible, and a
  *multi-part name*, the database's own `schema.table` identifier, may be
  the better form; examples to be worked. RE's position: whichever form
  Egeria's lists accept, RE compiles the stored scope into it; the
  examples should include a table name present in two schemas, which is the
  case plain names cannot express.
- **On `_` and `%` (topic 3), restated because it was unclear:** this is
  not about what RE passes to Egeria. RE's include and exclude names are
  matched by exact equality and RE can filter them before the call. The
  problem is inside the cataloguer's own listing: once it has chosen a
  schema, it hands that schema's *real name* (`a_b`) to the JDBC driver's
  `getTables` as the pattern argument, and JDBC reads `_` as "any one
  character", so the driver also returns the tables of `aXb`, which the
  cataloguer then creates under `a_b`. The same with `getColumns` and a
  table named `x_y`. Those wrong rows are created inside Egeria's connector
  before RE sees anything, so RE can only warn beforehand; the fix is in
  the connector, by listing with a null pattern and filtering the returned
  rows by exact name, or by escaping the name before the call.
- **On the cataloguer creating the schema (topic 4):** agreed that common
  qualified names cannot be enforced across every user of Egeria. What RE
  needs is narrower: that the identity the cataloguer gives the elements
  it creates is deterministic from the database's multi-part name, so RE
  can read back the elements its commit caused. The cataloguer's
  `<parent>::<name>` scheme already is; the agreement needed is only that
  it stays so.
- **On zones (topic 7):** there is a default zone to use when none is
  requested, and with no zone specified everyone has visibility, which is
  an acceptable default; users may choose special-purpose zones later. So
  RE's commit writes no zone; the one fact to confirm is that the survey
  engine, the cataloguer and the RE persona can all write in that default,
  which tonight's read-back run tests.

## What RE will do regardless

Store the scope in RE and compile it on every commit; derive every state
word from a read-back; keep the plain-name and `_`/`%` checks in its
manifest until the cataloguer no longer needs them; publish one-way and
idempotently; propose relations and never judge them. The discussion
decides how much of that Egeria makes unnecessary.

*Status at the end of 2026-10-05:* topics 2 and 4 are settled on evidence
(survey scope landed; schema-kind targets chosen for RE's commit, owner's
decision); topic 3's findings stand on the current cataloguer, which is
byte-identical across the rebuild; topic 7 is the newest and the one most
in need of the Egeria leads' answer before RE catalogues a real database.
