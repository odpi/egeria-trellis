# REPLY — Designer: Curate for a database decides what gets catalogued (2026-10-04)

To ASK-DESIGNER-CURATE-CATALOGUE-SCOPE-DATABASES.md, as merged (main at
45f4671b), together with the coco_pharma addendum on
`re/ask-curate-scope-coco-case` (not yet merged). I read it against
`curate_plan.py`, `workflows/curate_commit.py`,
`workflows/catalogue_depth_offer.py`, `web/routes/databases.py` (publish and
`schema-inventory-tree`), `survey_definition_adapter.schema_inventory_tree`,
`egeria_identity.py`, `next/stages/curate-bands.js` and Classic's publish
panel. The Egeria-side facts are the ask's; I didn't re-read the Egeria
source. Drawing: `wireframes/CatalogueScope.dc.html`, canvas page 18.

## The one rule this design rests on

**The scope is a declared, signed, dated choice stored in RE. The commit
compiles it into Egeria's lever every time.** The coco_pharma addendum is
the proof. The 7-schema scope lived only in a connection property, nobody
could see it on screen, and the 2026-10-03 reset erased it. If the choice
lives in RE, a republish after a reset puts it back. If it lives only in
Egeria, the next reset loses it again. Everything below follows from that
rule: the tree edits RE's record, and the commit writes the record to
Egeria.

## 1. Where it sits, and depth before or after

**Band 2 for a database becomes three sections, in this order:**

1. **What gets catalogued**: the depth, the scope tree and the commit.
2. **Glossary terms on tables and columns** (unchanged; waiting on readers).
3. **Logical schema match** (unchanged; waiting on readers).

The scope comes first because the other two act on its output. A glossary
term is assigned to a `RelationalColumn`, and until the cataloguer has
created that element there's nothing to assign it to. With no scope
committed, those two sections' sentences gain: "…and the tables to be
catalogued first (above)."

**Depth is chosen before the tree, at its top.** For a repository the depth
offer comes after the commit because layer 2 is a separate act on a
separate kind of element. For a database, depth changes what the tree
*is* (column rows appear only at column depth) and what the commit compiles
to. So it's part of the first decision, not a follow-up:

> **Depth** · ○ the database only · ○ schemas · ● tables · ○ tables and columns

A depth the lever can't produce stays on the line, disabled, with the
reason in words ("Egeria's cataloguer creates tables with their schemas;
schemas alone can't be asked for"). The implementer confirms which of the
four the JDBC cataloguer's lists can produce before this ships. I haven't
checked whether it accepts a wildcard exclude for "no columns".

**DepthOffer's three rules still apply, one level down, after commit.**
Once tables are catalogued, the pane offers columns once, with its count
and its measured price ("2,140 columns · first run fixes the price"). It's
not a nag, not a gate and not a scold, exactly as `catalogue_depth_offer.py`
words it.

**The tree is the Schema Inventory tree, reused.**
`schema_inventory_tree()` already returns schemas in their classified order
(data by rows, structure-only, views-only, staging, empty, no-access, with
system folded), tables with `row_count`/`row_count_state` and `size_bytes`
kept NULL when not measured, and columns with key roles. The scope tree is
that tree with a choice column on the left and a state column on the
right. **System schemas aren't offered**: they stay folded with "not
catalogued: system schemas are never offered", because nobody catalogues
`pg_catalog` on purpose.

Each row: **choice** · name · tables (schema rows; opens them) · rows
("not established" when it isn't) · data classes found · last write ·
**state**.

## 2. What may be proposed

**A measurement may propose only when the decision follows from it alone.**
That's a stricter reading of the standing rule. It splits the ask's three
examples:

| Measured fact | Proposes | Why |
|---|---|---|
| a schema with 0 tables (measured, not "no access") | leave out | there's nothing to catalogue, so the decision follows mechanically |
| no writes since a date, from `db_change_rates` (the archive finding) | leave out, **with the evidence on the row** | follows for most purposes; overridable in one click |
| matches the investigation's data lens (when the database is in one) | catalogue | the lens is a declared requirement; a measured match against it is not a judgement |
| PII data classes found | **nothing**: a mark on the row, "PII · 3 columns" | whether PII belongs in the catalogue is a governance decision. Often it's exactly what must be catalogued. A measurement can't make that call. |
| schema name suggests staging (`_STAGING_NAME_MARKERS`) | **nothing**: "name suggests staging" as a note | it's a name heuristic, not a measurement |
| no access with this credential | **nothing**: "? not established" | Egeria's cataloguer connects with its own credential and may see it |

**How a proposal is drawn**: the existing ⏵ proposal glyph and word in the
choice cell, with its reason as the cell's second line, and two controls:

> ⏵ proposed: **leave out** · 0 tables, measured 10-02 · confirm · catalogue instead

**The four observation states fit. No fifth is needed:**

- **proposed**: as above.
- **confirmed**: "leave out · confirmed by dwolfson 10-04".
- **overridden**: the person chose the other way. The proposal's reason
  stays visible, struck through, so the next person sees why it was
  proposed.
- **survey now disagrees**: the measurement under a confirmed or overridden
  choice changed. "Confirmed leave out on 10-04, when it had 0 tables; it
  now has 4 (survey of 10-09)." The choice doesn't change on its own. The
  row says so and offers to reconsider, and the commit's manifest counts
  these rows.

**Undecided isn't an observation state, so it needs its own rule.** A table
with no choice inherits its schema's choice. A schema with no choice
**keeps what's in Egeria now**. If it was never catalogued, it isn't now,
and the manifest says so ("12 tables undecided — not catalogued"). If it is
catalogued, it stays. **Only an explicit "leave out" removes anything from
Egeria.** An unconfirmed proposal to leave out is still undecided, so it
removes nothing until a person confirms it. A steward of a 266-table
database mustn't be blocked by undecided rows, and the commit must not
quietly delete for them.

**Inheritance is drawn, not implied.** A table that follows its schema
shows its choice in muted ink, "catalogue (from schema)". A table that
differs shows it in ink with "differs from its schema". Whatever the person
sets on a schema changes the inherited rows and leaves the explicit ones
alone.

## 3. The commit and its proof

**"Catalogue" is a curation record, as it is for a repository**
(`curate_commit.py`): a queued run whose steps write their outcomes to the
record as they land. Per the addendum, it is also /next's Publish for
databases. Classic's publish button retires into it when this slice lands.

**After commit, every node's state comes from a proof row, never from the
branch the code took:**

| State | Glyph | Proof | Words on the row |
|---|---|---|---|
| catalogued | ✓ | the element read back by qualified name | "catalogued · RelationalTable · read back 10-04 09:12" |
| attached, waiting | ◔ | the CatalogTarget relationship read back, with no element yet | "attached · waiting for Egeria's next refresh · last cycle 09:05" ("last cycle not reported" when Egeria doesn't say) |
| in progress | ◔ | an outbox row, not yet applied | "queued · outbox #4182" |
| failed | ✕ | Egeria's error | Egeria's own word, then the step that failed |
| left out | (none) | the scope record | "left out" in muted ink. Not a state of Egeria's, so no glyph. |

**Re-commit with a changed scope shows the difference before the press.**
The commit's manifest above the button lists what changes, not the whole
scope again:

> **This commit:** 3 tables added · 1 schema left out (14 tables) ·
> **14 tables will be removed from Egeria on its next refresh**

**A table that was catalogued and is now left out** is the case to protect.
Before the press, its row reads:

> ⚠ **will be removed from Egeria** on its next refresh · 2 glossary-term
> assignments and 1 lineage mapping hang off it

"What hangs off it" comes from reading the element's relationships at
preview time. If that read fails, the row says "couldn't check what hangs
off it", never nothing. The commit button's label carries the count:
**"Catalogue · removes 14 from Egeria"**. After the refresh the row reads
"removed from Egeria · 10-05 · was catalogued 10-04" (the absence read
back), and the row stays, so the history is visible.

## 4. Three mechanisms, one commit

**The manifest names all three, as three lines, in the order they run.
Each line says what it will do *to this scope*:**

1. **RE publishes**: the server and database assets, a survey report,
   N annotations. (What runs today.)
2. **Egeria's cataloguer creates**: 6 schemas, 121 tables, at table depth.
   "Attaches the database as a catalog target with your scope as its
   lists; the elements arrive on the daemon's next refresh, not now."
3. **Egeria's survey measures**: **every** non-system schema, table and
   column. "Its scope can't be limited, so it measures what you left out
   too." This is the honest sentence the ask's research makes necessary.
   Without it, a steward who left out 22 schemas would see them measured in
   the next survey report and think the scope failed.

**On each node, the state's source is the state's second line**, in muted
ink:

- "element · read back from Egeria" (the cataloguer created it),
- "measured only · in survey report 10-04" (left out of the catalogue but
  surveyed),
- "RE annotation only" (summary-level annotation, no element).

A left-out table that Egeria's survey still measured reads
"left out · measured only". That's exactly true, and it's the fact the
manifest's third line warned about.

**What the lever can't express says so on the tree before commit.** The
JDBC cataloguer's filters match plain names, so "`orders` in `sales` but
not in `archive`" can't be compiled. When the person makes that choice, both
rows get, at once:

> ⚠ needs a person: `orders` is chosen differently in sales and archive.
> Egeria's filter can't tell them apart · catalogue both · leave both out

The commit button is disabled while any such conflict stands, with the
count as its reason ("2 choices Egeria can't express — resolve them
above"). It doesn't fail after the press. The same applies to anything else
the lists can't say (a per-table depth, a row filter).

**Coco_pharma, the first use.** Egeria's survey covers 29 schemas today,
and the old 7-schema scope is gone. The tree's header states both, from
their sources: "Your scope: none declared yet · Egeria's latest survey
covers 29 schemas, 266 tables". The owner's pending decision (7 or 29) is
the first scope record. From then on the header reads "Your scope: 7 of 29
schemas · declared by dwolfson 10-04".

## 5. Ties to Find and the investigation

**Reachable, yes.** Each database row of the investigation's scope grid
(work-lists reply §3) gets a "catalogue scope ›" link to that database's
Curate, opened at the scope tree. Its catalogue state also shows as the
grid's last column, in words ("catalogued · 6 schemas" / "not catalogued").

**A verdict doesn't propose a scope.** "Recommended" is a judgement, and by
the rule in §2 only measurements propose. What a verdict *does* is the
existing population rule: the commit is enabled for `tracking` and `using`,
and the pane says so for any other verdict. That's a gate, not a proposal.
**The data lens does propose**, because a measured match against a declared
requirement isn't a judgement (§2's table). A Find investigation with a
lens therefore arrives at Curate with "⏵ proposed: catalogue — matches the
lens on subject and time range" on the tables that fit.

## 6. Zones and ownership

**Zones aren't a field on the commit.** The database publish route takes no
zones (`PublishRequest` has none). Classic's "Governance zones" input is on
the *repository* publish only. RE's model (`egeria_identity.py`, owner's
decision 2026-09-04) is that a publish joins `resource-explorer-draft`, and
a curate-accept promotes into the deployment's publish zones. The commit
*is* the curate-accept, so it promotes. The manifest says "joins the
deployment's publish zones: <names from config>". A free-text zone field
would bypass that configuration and invite typos, so it doesn't come back.

**What the commit carries from Context**, as classifications on the
database asset, the same way the repository commit copies its judgements:
the owner (a judgement) and the licence (an observation, with its state).
**If either is missing, the commit runs and says so** in the manifest
("not carried: owner — not declared on Context · declare it ›"). It doesn't
block: the publish is idempotent, so a later commit carries what's been
added since. The one thing that blocks is a scope conflict (§4), because
that would publish the wrong thing, not an incomplete one.

**One gap the architect needs to rule on:** the cataloguer's elements are
created by Egeria's integration daemon, not by RE. So they probably carry
neither RE's `Ownership` classification nor the draft zone. I haven't
checked this. If true, a catalogued table has no owner and isn't in the
publish zones, while its database asset has both. The tree would show it
honestly ("element · owner not set"), but whether the commit should
classify each element after read-back (a write per table, 121 here) is
a cost and design question for the architecture session.

## File shares

**Deferred, not answered by analogy.** The shape is similar (directories as
schemas, files as tables), but the lever isn't: the ask's research covers
only the PostgreSQL cataloguers. Designing a file-share scope tree before
knowing what Egeria's file cataloguers can filter would draw controls that
might compile to nothing. One sentence stays in the file share's band 2
until that's read.

## The slice and its gate

| Piece | Kind |
|---|---|
| Scope record: per-node choice, author, date, proposal source; inheritance | storage + route |
| Scope tree on Curate (the Schema Inventory tree + choice + state); depth line | UI |
| Proposals from measured facts (§2's table), the four observation states | UI + route |
| Conflict check against the plain-name filter, before commit | route + UI |
| Commit as a curation record: RE publish, attach CatalogTarget with compiled lists, native survey; manifest of the difference | workflow |
| Per-node proof: element read-back by qualified name; target read-back; outbox; removal read-back | route |
| "Catalogue scope ›" on the investigation's scope grid | UI |

The gate as the ask states it passes on coco_pharma. One addition: after
an Egeria reset and a re-commit, the scope comes back unchanged. That's the
addendum's defect, and it should be in the gate.

## Left open, for the architect

1. Which depths the JDBC cataloguer's lists can produce (schemas only? no
   columns?).
2. Whether the cataloguer adopts RE's template-created server and database
   assets or creates its own by name. If it creates its own, the read-back
   finds two database elements, and the tree's header must say which is
   which.
3. Ownership and zone on cataloguer-created elements (§6).
4. Whether Egeria reports the integration daemon's last cycle time in a
   form RE can read for the "waiting" row.
