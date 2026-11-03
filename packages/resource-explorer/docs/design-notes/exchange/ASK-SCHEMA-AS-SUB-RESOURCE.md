# Database analyses answer for the whole database — schemas inside it can be structurally unrelated. What's the right grain?

**For:** the architecture session.
**From:** the coordinating session, 2026-09-24.
**Read against:** `main` at `772ccf3f`.
**Replying to:** nothing directly — raised by the project owner while reviewing
`coco_pharma`'s Discovery pane live.
**Action needed:** a ruling on which of two distinct shapes (or both, and in
what order) closes this, before any database-analysis code changes.

## The observation, with evidence

`coco_pharma` is one Postgres database with (at least) these schemas, per
the live `pg_namespace` read done for the credential-capability work:
`coco_ods` (23 tables, a real transactional schema), `coco_sus`, `eu_sales`
(1 table), `target_sales` (1 table), `us_sales` (1 table), `public`, plus
`demo`/`demo_auth` invisible to the current credential.

Every whole-database structural analysis — `db_classification`,
`db_relationship_graph`, `grain_determination`, `db_fingerprint` — collapses
all of this into one verdict. Live screenshot evidence, at a point where
only `eu_sales`/`target_sales`/`us_sales` were visible to the connected
credential:

> **Is there a data model here?** — table count 3 · edge count 0 · component
> count 3 · largest component 1 · connected tables 0

That number is *correct* for what's visible (three single-table schemas,
each an independent regional sales mart, genuinely have no foreign keys
between them) and *useless* as a database-level verdict — it says nothing
about whether the database "has a data model," because the database isn't
one thing. Once `coco_ods` becomes visible (a real schema with real FK
structure, 23 tables), the same rollup gets worse, not better: a real graph
mixed with three isolated singletons and — per `#257`'s catalog-only
fallback — a chunk of tables recovered from `pg_class` with no constraint
data at all, since PK/FK lookups are `information_schema`-filtered and the
fallback deliberately doesn't guess them. `db_classification` similarly asks
"transactional, analytical, reference, staging or a copy" of a database that
is legitimately several of those at once, one per schema.

## This is already a decided principle, not yet built for this layer

**D3** (`multi-resource-questions-design.md` §14, project owner, 2026-09-20):
*"Files, and equally server / database / schema / table, are sub-resources
by default and first-class on direct registration."* Schema is already
named at the same tier as file/table. What's missing is that none of the
current database structural analyses honor it — they all read across every
visible schema as one flat bag of tables.

## Two distinct shapes, possibly both needed, in an order that matters

Checked the one existing precedent in this codebase — repo's
`repo_sub_resource_survey` (`repo_survey_definition_adapter.py`) — before
assuming schema should work the same way. It's an *advisory* layer: it
surveys file/folder characteristics to **recommend which sub-resources are
worth promoting to their own Egeria assets** ("survey only, does not
catalog"). It does not re-run every repo analysis once per candidate
sub-resource; it produces a ranked candidate list for a human to act on.

That may not be the right model here, for a structural reason: a file/folder
promotion candidate is optional — most folders in a repo are not worth their
own asset. A Postgres schema is not optional in the same way: **every table
in the database belongs to exactly one schema**, always, by construction.
So the question isn't really "which schemas deserve promotion" (repo's
shape) — it's closer to "these existing analyses are computing the wrong
aggregate, and need a `GROUP BY` schema they don't have today" (a different
shape entirely, and arguably not a sub-resource question at all, just a
grain fix inside the analyses that already exist).

Two candidate shapes, not mutually exclusive:

1. **Schema-scoped analysis output, no new registration.**
   `db_classification`/`db_relationship_graph`/`grain_determination`/
   `db_fingerprint` (and `db_activity_signals`/`db_resilience`, per the
   `#254` capability audit's mixed-tier findings) each report *per schema*
   instead of one rolled-up number, with a top-level summary that names the
   spread rather than averaging it ("3 schemas look like independent
   single-table marts, 1 schema (`coco_ods`) looks transactional with real
   FK structure — no single verdict fits"). No new resource, no new slug,
   no lifecycle/disposition per schema. Closest existing precedent in this
   codebase for "report a breakdown instead of a rollup" is probably
   `interface_surface`'s per-`interface_kind` findings or
   `schema_conventions`'s per-check granularity — worth confirming which
   pattern actually fits best.
2. **Schema as a genuinely first-class registered sub-resource** — its own
   slug, its own survey history, its own disposition, reachable
   independently in `/next`'s sidebar the way a database or filesystem is —
   matching D3's literal wording ("first-class on direct registration") and
   mirroring how `credential_capability` *already* reports per-schema
   visibility/access internally (schema is already the natural unit for
   that probe, not a stretch).

## What we'd want your view on

1. **Which shape actually closes the observed problem** — is (1) alone
   sufficient (fix the aggregation grain inside existing analyses), or does
   the database-first-class-resource model (`DatabaseEntity`, its own
   sidebar entry, its own disposition) need a schema-level sibling type for
   this to be coherent, given D3 already commits to that literally?
2. **If (2), how does it interact with the credential-capability model
   (`#253`/`#257`/`#262`, still landing)?** A schema RE can see via `USAGE`
   but has zero `SELECT` on (this session's whole `coco_ods` incident) would
   register as a sub-resource with nothing measurable in it yet — is that a
   legitimate "registered, not yet surveyable" state, or does registration
   itself need to wait for at least `read`-tier access?
3. **Does this affect filesystem the same way** — a filesystem resource
   with genuinely disjoint top-level directories (e.g. one directory of
   Parquet data lake files, another of unrelated application logs) has the
   identical "aggregate makes no sense" problem, or is Postgres schema a
   qualitatively different case (schema is a hard structural partition every
   table belongs to; a directory tree is not partitioned the same way)?
4. **Sequencing against what's already in flight** — `requires_capability`
   (`#262`) currently declares one tier per *step*, database-wide; if schema
   becomes a real unit, does that declaration eventually move to
   per-(step, schema), or does the coarser database-wide declaration stay
   right and only the *reported result* gets the schema breakdown?

## Reply

A `REPLY-`/`RULING-`-prefixed doc, same convention as prior rounds, or
conversational if that's faster — this ask is trying to establish scope and
shape before any implementation, not to propose one.
