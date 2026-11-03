# REPLY — Designer: cataloguing at scale: rules, arrivals and earlier decisions (2026-10-15)

To ASK-DESIGNER-CATALOGUING-AT-SCALE.md, read against main at 08390160
(`catalogue_scope.py`, `catalogue_commit.py`, `resource_scope.py`,
`worklist-actions.js`, `db-server-discovery.js`), the four backlog entries
of 2026-10-05, `BRIEF-CURATE-CATALOGUE-COMMIT-DATABASES.md` and
`DESIGN-EGERIA-SUGGESTIONS-AND-RE-OWN-CATALOGUING.md` (S1, S2, S11). It
builds on the scope round 2 reply, which already answered most of
questions 1 to 3, and on the split-by-job reply and
`RULING-PUBLISH-NOT-CATALOG.md` (both on `re/design-curate-split-by-job`,
not yet merged). Drawing: `wireframes/CatalogueAtScale.dc.html`, canvas
page 21. It shows the Contents tab on a return visit to coco_pharma.

## First: what Egeria can honor today, and the owner's decision

The ask's code reading says a rule "must be compiled to plain names at
commit time". The commit as built doesn't compile any names. Since the
owner's decision of 2026-10-05, the door is **one schema-kind catalog
target per chosen schema, attached with no lists**
(`catalogue_commit.py`), because a target's list holds one name only
(S11) and plain names can't say which schema a table is in (S1). The
screen already says so: "Egeria catalogs whole schemas · table choices are
kept for when it can". Two consequences the ask didn't draw:

1. **A table rule can't be honored.** Only a schema rule changes what
   Egeria catalogs.
2. **A new table in a published schema is published without a press.**
   Egeria's cataloguer refreshes each target on its own cycle (about 34
   minutes in the 2026-10-05 test) and creates whatever the schema now
   holds.

**Decision (project owner, 2026-10-15):** conform to Egeria's current
default, as the publish ruling does. **Rules act at schema level now.**
Table and column rules may be written and are kept, shown as "kept for
when Egeria can", and the manifest counts them separately. An arrival in
a published schema says plainly that Egeria adds it on its next refresh.
The gate moves from "a new matching table" to "a new matching schema"
(the end of this reply).

The design position, "a rule proposes, the commit publishes", holds for
everything RE does. Egeria's cataloguer adding a table to a schema you
published is Egeria's behavior, not RE's, and the screen says whose it
is (§3).

**A correction to the publish ruling's depth line, from the same fact.**
The ruling says all four depths are expressible. Under schema-kind
targets they aren't: the cataloguer creates a schema's tables and columns
together, and there's no depth option (S2). So the line reads:

> Depth · ○ the database only · ○ schemas (off) · ○ tables (off) · ● tables and columns

"Schemas" and "tables" stay on the line, disabled, with the reason
"Egeria's cataloguer creates a schema's tables and columns together"
(round 1's rule for a depth the lever can't produce). I've made that edit
to the ruling in this branch.

## 1. A rule as a signed object

**Yes, as round 2 ruled. Here's what's new.**

**Where it's stored.** It goes in `resource_scope_events`, which today has
one row kind per node decision. Three event kinds are added:
`rule_declared`, `rule_edited` and `rule_retired`. Each carries the rule's
name, its level (schema, table, column), its verb (publish or leave out),
its criterion, the sentence the person confirmed, the author and the
time. An edit is a new event, never an update, so the history shows what
the rule said on each date.

**Where it's shown: a strip, not a row above the tables it matches.** A
rule can match rows in many schemas ("every schema whose name ends in
_sales" matches `us_sales`, `eu_sales`, `target_sales`), so there's no
one place in the tree to put it. The rules strip sits at the top of
Contents, under the summary line, one line per rule:

> `Sales schemas` · **publish** every schema whose name ends in "_sales" ·
> dwolfson 10-14 · **matches 3 · 1 new since 10-14 · 1 overridden by
> hand** · edit · retire

Each count opens the rows it counted. A table or column rule's line ends
with the tag **"kept for when Egeria can"**, and its counts read "matches
14 · not sent to Egeria".

**Hand versus rule, on the row.** This is unchanged from round 2. A hand
choice shows the person and date. A rule's match shows the rule's name in
a bordered tag that links to the rule. Both are in ink, because both are
decisions. A row inherited from its schema is muted.

**Overriding one row without breaking the rule.** A hand choice wins
(precedence 1), and the row says **"differs from rule ‹name›"**. The rule
is untouched and keeps deciding every other row. Its strip line counts
the overrides. "All except these three" is exactly this: one rule
("publish every schema") and three hand choices, each reading "differs
from rule All schemas". There's no "except" clause inside the rule, so
an exception is always signed by the person who made it.

## 2. Filter, sort, decide

**Filters** (one bar above the list; any combination):

| Filter | Can define a rule? | Why |
|---|---|---|
| name: contains / starts with / ends with (literal text, any case) | yes | the owner's "Sales in its name" |
| schema | yes | |
| rows ≥ / ≤ | yes, with its source named | see the staleness note below |
| size ≥ / ≤ | yes | |
| activity: active / dormant / can't tell | yes | round 2's word and window |
| has keys | yes | |
| data class: any / a named class | yes, on the owner's say (left open below) | |
| published in Egeria / not | **filter only** | a rule that publishes what's published decides nothing |
| choice: undecided / proposed / differs from rule / new since… | **filter only** | it describes the record, so a rule on it would decide from itself |

**Sorts:** click any column header: name, rows, size, activity (by window
length, then writes), data classes (count), first seen (newest first).
Sorting never changes a rule's definition.

**"Decide for everything shown" and "Make this a rule" stay two acts, as
round 2 drew them.** The difference that matters: a bulk choice records
**the rows shown now**, one signed event each. A rule records **the
criteria in the bar**, not the list of rows. So a row that matches later
is picked up, and the sort and "needs you" view don't leak into it. The
bar offers both, with the count:

> **3 schemas match** · Publish these 3 · Leave out these 3 · **Make this a rule…**

With a table or column filter, "Make this a rule…" reads "Make this a
rule… (kept for when Egeria can)". That way nobody writes one believing
it will act.

**No separate rule editor.** "Edit" on a strip line reopens the filter bar
loaded with the rule's criteria, under a heading ("Editing Sales
schemas"). Before saving, it shows what the edit changes: "matches 3 → 4 ·
1 row changes · `apac_sales` would be published". The filter bar is the
editor a person already knows.

**Numbers that go stale.** A rule on rows or size reads stored numbers,
and Postgres refreshes its catalog statistics only during maintenance
(the owner's ongoing discussion). The rule's sentence names its source,
"every table with at least 10,000 rows (RE's measured count, else
Postgres's estimate)". A row whose match changes after an ANALYZE is
reported like any other match change (§3), never applied silently.

**At scale, the flat list is the working view.** With a filter on, the
tree flattens to the matching rows with a schema column, sorted. With no
filter on, schemas show collapsed with their counts, and a schema's tables
load when it opens. That gives 29 rows on coco_pharma, not 266. A table's
columns appear only when the table is opened.

## 3. Arrivals: what a later survey brings

**One summary line at the top of Contents.** It separates what waits for
you from what Egeria does on its own:

> **Since your last publish (10-11):** 4 new · **2 schemas wait for you**
> (1 matched by `Sales schemas`, 1 undecided) · **2 tables Egeria adds on
> its own** (in published schemas) · 1 gone from the source · 1 match
> changed

Every count opens its rows. The tree opens filtered to "needs you", as
round 2 drew it.

**The states, each on one row.** The word goes where the eye already is,
and nothing new is added to the cue vocabulary:

| Case | Name cell, second line | Choice cell | In Egeria |
|---|---|---|---|
| **new schema, matches a rule** | "new · first seen in survey 10-14" | "publish" + the rule's tag | "not published" · **waits for Publish** |
| **new schema, matches nothing** | "new · first seen in survey 10-14" | "undecided" (keeps what's in Egeria: nothing) | "not published" |
| **new table in a published schema** | "new · first seen in survey 10-14" | "publish (from schema)", muted. A table rule that matches adds its tag, marked "kept" | ◔ "Egeria adds it on its next refresh · its schema is published whole", then ✓ "published · by Egeria's cataloguer · read back 10-14" |
| **gone from the source** | "gone from the source · last seen in survey 10-11" | the last choice, muted, with "no longer applies" | if it was published: "in Egeria · not checked since the source dropped it", until a read-back gives ✓ (still there) or "deleted in Egeria · 10-15" (no glyph) |
| **match changed** | "no longer matches ‹rule› · ‹why› · survey 10-14" or "now matches ‹rule›" | the next decider in the precedence, saying which | unchanged until a Publish |

**The row lowers** for "gone" (nothing of it is still in the source), as a
left-out or deleted row does. A gone row stays in the needs-you view until
the next publish, then lives in the history. It's never silently dropped.

**What Egeria does with a vanished table isn't assumed.** The lever
findings say excluded elements go on the next refresh, but nobody has
observed what the cataloguer does when a table disappears from the
source. The row says "not checked" until a read-back says otherwise, and
that's a small probe for the builder.

**Does an arrival ever change what's published without a press?**
**RE: never.** A rule proposes; Publish compiles. A new schema that
matches a rule waits, with "waits for Publish". **Egeria: yes, inside a
schema you published,** by its own default. The summary line and the
row say so in Egeria's name, so the steward knows whose act it was. If
someone wants a schema's new tables held back, the honest answer today is
to leave the schema out, and the Egeria cell says that on hover: "to hold
new tables back, leave the schema out; Egeria can't take table lists yet
(S1, S11)".

## 4. Earlier decisions

**Round 2 §5 stands: no "worth publishing" flag, and one record with
several doors.** What the earlier stages can contribute, and how Curate
shows it:

- **A person's choice made early** goes through the Schema Inventory tab,
  which is on every stage for a database. It writes the same scope event
  as Contents. Curate shows it as a decision, not a proposal, so there's
  nothing to re-decide: "leave out · Dan 10-05 · **set in Assessment**".
  Rules can also be written from Schema Inventory's filter bar (the same
  component), and their strip line says where: "· written in Discovery".
- **A measurement proposes only when it decides alone**, as ruled: an
  empty schema, or a dormant one with its window. The proposal says which
  survey measured it and on which stage that survey ran: "⏵ proposed:
  leave out · dormant, 0 writes in 338 days · survey 10-06 (Assessment)".
  Active, large, or "has PII" propose nothing, because none of them
  decides by itself whether a schema belongs in the catalog.
- **A data-class match proposes only through the lens** (the glossary
  path), and not before it exists. Until then a lens term in names is a
  suggested rule, as round 2 corrected.

**Finalizing in Curate, without re-deciding.** Contents opens on "needs
you", where early choices don't appear because they're decided.
Proposals do appear, and the bar offers **"Confirm these 4 proposals"**
(one signed event each, the confirmer named). The history reads the
whole story: proposed in Assessment by the survey of 10-06, confirmed in
Curate by Dan on 10-14.

**Which stage proposes first** (the ask's second slice): it isn't really
a choice of stage. Proposals come from whichever survey ran. Build the
door first (the choice and rule controls on Schema Inventory, writing the
same events), because it works from every stage at once.

## 5. Columns

**Today, nothing at column level reaches Egeria.** The depth line covers
"tables but not columns" in principle, but that depth is disabled (the
correction above). So a column choice is, like a table choice, "kept for
when Egeria can".

**When Egeria can, the only per-column choice needed is "leave these
out",** and its natural form is a rule, because Egeria's column lists are
plain names. "Leave out every column named `ssn`" is what the lever says
natively. A hand "leave out `orders.notes`" compiles to `notes` in every
table, so the row must warn first ("⚠ Egeria would also leave out `notes`
in 6 other tables"), the same name-collision check as round 1's.
"Include only these columns" isn't offered: depth includes all of them,
and an include list of columns is a maintenance trap.

**Where it lives:** column rows appear under an opened table with the same
choice cell, and column rules sit in the same strip, at column level.

## 6. "Add these to an investigation"

**On a finding row, the count is the control.** "Counts open what they
counted" already makes it a link. The fix is to make the link look like
one ("14 databases ›") and put the act where the rows open: the Members
rail's action bar, at the top, "Add these 14 to ‹investigation›". Design's
framing in the 2026-10-04 backlog row is right: any count on any stage
that counts members opens the same rail with the same acts. That includes
Scouting's counts, which open nothing today.

**It's not the same control as "include by rule", but it's the same
place.** The two acts change different records about different things.
An investigation's scope holds **resources** (databases, repositories). A
publish rule decides **parts of one resource** (schemas, tables). So the
rail's action bar offers whichever acts fit what was counted:

- a count of resources (databases on a server): "Add these N to
  ‹investigation›" · "Start an investigation from these…";
- a count of a database's parts (dormant schemas, tables with PII):
  "Open in Contents with this filter ›", which lands on Contents with the
  filter bar loaded, where "Make this a rule…" is one press away.

A finding that counts parts never offers "Add to investigation". Tables
aren't investigation members, and offering it would add the database
silently under another name.

## The slice and its gate, restated

| Piece | Slice |
|---|---|
| Rule events in `resource_scope_events`; rules strip; precedence and tags; overrides counted; table and column rules marked "kept" | rules |
| Filter bar with the criteria table above; sorts; flat filtered list; edit-by-filter with its before/after count | rules |
| Summary line with "waits for you" vs "Egeria adds on its own"; the five arrival rows; "gone" read-back probe | rules |
| Depth line correction (two depths disabled with the reason) | rules (copy) |
| Choice and rule controls on Schema Inventory, writing the same events, "set in ‹stage›" | earlier-decisions slice |
| Count link styling and the rail's action bar by what was counted; Scouting counts open members | its own small slice |

**The gate, by use on coco_pharma, restated for schema rules:** the owner
writes one schema rule ("publish every schema whose name ends in _sales")
and sees the three matched rows carry its tag. A new matching schema
arrives (one loaded on purpose) and shows "waits for Publish". It's
published only after he presses Publish. In the same visit, a new table
in an already-published schema shows "Egeria adds it on its next
refresh", and later reads back as published by Egeria's cataloguer.

## Left open

1. **Rules on data classes** (round 2's open point 2, still open):
   "leave out every schema with PII" turns a governance policy into a
   filter. I'd allow it with the sentence saying exactly what it does.
   It's the owner's call.
2. **What Egeria's cataloguer does with a table that vanished from the
   source** (the probe in §3).
3. **S1 and S11 for the Egeria leads**, now with a concrete use behind
   them: table rules, and holding back a published schema's new tables,
   both wait on them.
