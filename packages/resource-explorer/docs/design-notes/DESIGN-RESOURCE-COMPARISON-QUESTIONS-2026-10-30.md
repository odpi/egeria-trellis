# DESIGN — "Is database 1 the same as database 2?" Comparison questions for RE (2026-10-30)

Owner's ask (coordinator relay, verbatim): "This is actually an excellent example of questions a user would want
answered - is database 1 the same as database 2? Is the schema of database 1 the same as the schema of database 2?
Is database 1 a subset or superset of database 2. We should be able to ask these kinds of questions in a chat at
least." Design only; a builder follows after the owner reads it. Builds on DESIGN-ANALYTIC-LIFECYCLE §11–§13
(comparison by subject roles, owner ruling 12: "wait for the library split") and Part C (#941, the set as subject).

## In one page

Two databases can look alike for four different reasons, and a person needs to know which one is true. RE already
holds, for every surveyed database, the facts that settle it: the list of schemas, tables and columns with their
types and keys (Schema Inventory), row counts per table (snapshots), value profiles per column, and a fingerprint of
each table's shape. A comparison is a step that takes two databases as its subject, lines those facts up side by
side, and says, with counts and a basis, what is identical, what is only similar, and what is missing on one side.
The answer is never yes or no. Every verdict is a **relation with a degree**: identical, A is a subset of B, A is a superset of B, overlapping, or disjoint, each with a similarity count and its basis ("41 of 45 tables match by name and columns, 91%"). "Same database?" is answered as the strongest relation the facts support. "Same" is never one word: the step answers four separate questions (same schema? same tables with different rows?
same data? which is the subset?) and says "not established" for any side it has not surveyed. In chat, a question
that names two databases routes to this step; if a result exists it is cited, if not the answer says what a run would
cost and offers it. The worked case is coco_pharma on the owner's older server at 5432 against the newer one RE has at
5442: today RE knows only 5442, so the first answer is honest — "5432 is not registered; register and survey it (one
survey, about N minutes) and ask again." No new tables are needed: the result is a Part C set result over a set of two.

## 1. The questions (new catalogue rows; ids provisional, after Q-0108)

| Id | Question, in the owner's phrasing | Stage | Lead analysis | Also answers |
|---|---|---|---|---|
| Q-0109 | Is the schema of database 1 the same as the schema of database 2? | Assessment | `schema_comparison` (library step) | which schemas/tables/columns differ, and how (type, nullability, key) |
| Q-0110 | Is database 1 the same as database 2? (the same data, not just the same shape) | Analysis | `data_comparison` (library step) | row counts per matching table, value-profile agreement, fingerprint match |
| Q-0111 | Is database 1 a subset or superset of database 2? | Analysis | both, read together | by schema (tables/columns contained) and by data (rows contained, where keys allow) |

Each has the four-part header RE's rows carry: the question, who asks it (Data Owner, Steward, Architecture, Consumer:
"which copy do I use?"), the stage, and the lead analysis. The questions apply to a **set of two or more** databases,
never to one, so the row appears on the set (work list) page and in Chat, not on a single database's Questions tab
(the single-database tab may carry a pointer: "compared with X on <date> · see the set").

## 2. The comparison step

**Subject.** Part C's set subject, `subject="set"`, with the set being a work list of exactly two (first slice) or N
(later) databases. §12's roles become the set's order: member 1 is "database 1", member 2 "database 2"; a role field is
not needed until a non-resource reference (a glossary, an expected-values file) joins, which §12 keeps for the library
split. Reading every member through `inputs.ods` is already the Part C rule (no `connection` input on a set step).

**What it reads (RE's existing facts, per member, nothing live):**

| Fact | Reader (ods) | Used for |
|---|---|---|
| schema inventory: schemas, tables, columns, types, nullability, PK/FK | the Schema Inventory tables behind Q-0074 | Q-0109 and subset-by-schema |
| row count snapshots (latest per table) | `row_count_snapshots` (the tutorial's reader) | "same tables, different rows", subset-by-data (counts) |
| value profile per column (distinct counts, min/max, null share, top values) | the value-profile container reader (Q-0079) | data agreement per column |
| table fingerprint (shape hash RE already publishes as FingerprintAnnotation) | survey report / facts | a quick "identical shape" test before the column walk |

**Declared result** (Part A/B words throughout):

- result kind `table` (one row per matched pair of tables, plus unmatched rows on either side) with a resource-scope
  roll-up record per member pair.
- **relation** (owner, 2026-10-30: "the answer may be a degree of similarity or a is a subset etc. Not just a yes/no on are
  they the same"): the roll-up record carries `schema_relation` and `data_relation`, each one of `identical` ·
  `subset` (A ⊂ B) · `superset` (A ⊃ B) · `overlapping` · `disjoint` · `not_established`, chosen as the strongest relation
  the counts support (see §3a), never a boolean; and a **degree** per relation as a measure with a basis.
- measures, each with a **basis** so every number says "N of M": `tables_in_common` (basis: tables on the smaller
  side, label "tables"), `tables_only_in_1`, `tables_only_in_2`, `columns_differing` (basis: columns in common),
  `rows_equal_count` (basis: tables in common), `columns_profile_agree` (basis: columns compared);
  plus the degrees: `schema_similarity` = tables matching by name and columns, basis = tables on the union ("41 of 45,
  91%"); `column_similarity` = columns matching by name and type, basis = columns on the union of shared tables
  ("312 of 330"); `data_similarity` (estimated) = shared tables whose row counts are within a declared tolerance
  (default 2%), basis = shared tables ("38 of 41"), capped when profiles were sampled.
- **capped**: a value-profile comparison that read a sample, not the whole table, sets `capped_field` so the row says
  "at least 412 of 480 columns agree".
- **categories** (exclusive, `sums_to` = tables on the union): identical · same shape, different rows · same name,
  different shape · only in database 1 · only in database 2. This is also the first list-kind result that needs the
  categories legend on screen (READ-B1531EE0 F35).
- **absence sentences** (the owner's rule: one side not surveyed is "not established", never "different"):
  - not_measured: "Not established: <database> has no schema inventory in RE yet — survey it, then compare."
  - not_permitted: "Not established: RE could read <database>'s schema but not its row counts/profiles; the data
    comparison needs them."
  - not_applicable: "Not applicable: the two members are not both databases."
  - a run-level state when one member is unregistered: "Not established: <name> is not registered in RE."
- confidence: `measured` for schema and counts; `estimated` with the method "row-count estimates and column profiles,
  not a row-by-row diff" for data agreement — RE does not read rows across two databases in this slice.

**Price line before the press:** the step reads stored facts only, so the price is the sum of two fact reads (seconds).
If one side lacks a survey, the price shown is that survey's (the tutorial's priced line already exists for surveys).

## 3. What "same" can honestly mean — kept as separate verdicts

| Verdict | Test | Words on screen |
|---|---|---|
| identical schema | every schema/table/column present on both sides with equal type, nullability and keys | "schema identical: 263 of 263 tables, 1,916 of 1,916 columns" |
| same tables, different rows | schema identical (or same-shape fingerprints) and row counts differ on k tables | "same shape · rows differ on 47 of 263 tables" |
| data overlap / agreement | per column: distinct count, null share, min/max, top values agree within the profile's own precision | "profiles agree on 1,790 of 1,846 columns compared (estimated)" |
| shared fingerprint | RE's table fingerprint equal | a fast pre-check; shown as "fingerprint match on N of M tables" |
| subset / superset by schema | one side's tables ⊆ the other's (and columns within them) | "database 1's schema is contained in database 2's: 30 of 30 schemas, 266 of 288 tables" |
| subset / superset by data | within matched tables, counts ≤ and profiles consistent (containment is a claim about rows RE did not read: say estimated) | "by counts, database 1 is a subset of 2 on 240 of 263 tables (estimated)" |

### 3a. How a relation and its degree are computed

Let T1, T2 be the table sets (schema.table names) of the two sides, M the tables matching by name whose column sets
also match by name and type, and U = T1 ∪ T2.
- **degree** = |M| / |U|, said as "N of M tables" with the basis; the percentage is derived only when uncapped.
- **identical**: M = T1 = T2 (degree 100%, no table only on either side).
- **subset / superset**: every table of one side matches a table of the other and the other has more ("A ⊂ B: 41 of 41
  of A's tables match; B adds 4"); the degree is still |M|/|U| so a small subset of a huge database reads honestly low.
- **overlapping**: tables on both sides match (|M| > 0) and each side has tables the other lacks; the degree says how
  much ("17 of 60, 28%").
- **disjoint**: |M| = 0 with both sides surveyed (same name, nothing in common).
- **not established**: one side has no schema inventory in RE; said for that side ("5432: not surveyed"), and no
  relation is claimed. A side whose inventory is older than its newest survey is marked "as of <date>".
"Similar" versus "identical": identical is the relation when the degree is 100% and nothing is one-sided; any lower
degree is a similarity with its number, never rounded up to "the same". The same computation runs at column level
inside the matched tables (column_similarity) and, estimated, at data level (row counts within tolerance; profile
agreement per column), each with its own relation word and degree. The roll-up names the strongest relation that
holds at schema level and the first that fails below it.

Never collapsed into one "same/different" word. The roll-up sentence names the strongest verdict that holds and the
first that fails: "same schema, not the same data: rows differ on 47 of 263 tables."

## 4. Chat: recognising and routing "is X the same as Y?"

- **Recognise:** a question that names two registered resources (slug, display name or alias; the resolver RE's chat
  already uses for one resource is applied twice) with a comparison verb (same, identical, differ, subset, superset,
  contains, copy of). Two names → the set route; one name plus "the other one"/"the old one" → ask which.
- **Route:** look for an existing set whose members are exactly those two and a `schema_comparison`/`data_comparison`
  result on it; cite its rows (persist-what-the-model-saw: the journal keeps the row ids). None → the answer says so
  and offers the run with its price ("Compare now · reads stored facts · seconds"); the press creates the two-member
  work list (named "compare: X ⇄ Y", kind database) and queues the set step. The creation is a write, so it is the
  person's press, never the model's.
- **One side unregistered or unsurveyed:** the declared sentence, plus the door: "Register <name> (Admin › Find
  databases) and survey it (≈ N min for a database this size), then ask again." Never "they differ".
- **Answer shape:** one sentence with the relation and the degree first ("5442 is a superset of 5432's schema: 91% of tables match (41 of 45); it adds 4"), never "yes" or "no"; then the breakdown: columns ("312 of 330 match by name and type"), data (estimated: "row counts within 2% on 38 of 41 shared tables"), then the category counts, then the first ten differing tables, each a
  citation to the comparison row. The Chat compile uses the same declared words (basis, capped) as the screens.

## 5. Worked example: coco_pharma at 5432 (older) vs 5442 (newer, RE's)

Today: RE has `localhost_docker_coco_pharma` = host.docker.internal:5442 (30 schemas, 266 tables, surveyed 10-24).
The 5432 database is not registered. First chat answer: "Not established: coco_pharma on 5432 is not registered in
RE. Register it and survey it, then compare." Cost of getting to an answer:

| Step | Cost |
|---|---|
| register 5432 (Admin › Find databases, the owner's credential, native Postgres.app) | a minute of the owner's time; the slug will differ (e.g. laz_local_coco_pharma) |
| local survey of 5432 (schema inventory + row counts + value profile) | by size; the 5442 survey of 266 tables ran in the minutes range on 10-24 — the price line will say |
| the comparison step | seconds (stored facts only) |

Expected verdicts, if the owner's memory is right ("probably different schema as well as rows"): "same name, different
schema: N of M tables differ in shape; K tables only in 5442", with the category counts drawn. This also answers the
Egeria question from today's D1 check in the other direction: two Egeria families on **one** endpoint (5442) are one
database; two endpoints with one name are two databases until this step says otherwise.

## 6. Slices, gate, DDL

- **CMP1 — schema comparison step** (library, `schema_comparison`): set subject of two; inventory diff; categories +
  basis measures; absence sentences; Q-0109 and the schema half of Q-0111. Gate: on two throwaways (one a copy of
  the other with one table dropped and one column retyped): "1 is a superset of 2: N-1 of N tables match (x%); 1 adds 1 table", column degree "C-1 of C columns match", the retyped column named in the breakdown;
  with one side unsurveyed: the not_measured sentence, no zero.
- **CMP2 — data comparison step** (`data_comparison`): counts + profile agreement + fingerprints; Q-0110 and the data
  half of Q-0111; `estimated` confidence words; capped when sampled. Gate on the same pair plus rows inserted on one side.
- **CMP3 — chat route**: two-name recognition, cite-or-offer, the unregistered answer. Gate: the three answers of §4.
- **CMP4 — screen**: the set page's Questions rows for Q-0109–0111, the categories legend for list kinds (F35), the
  "compared with X" pointer on each member. Gate: a screenshot-ready set page.
- **DDL: none.** Results are Part C set results (per-member + roll-up records in the existing step-result tables); the
  work list of two is an ordinary work list. If a builder finds the pair needs its own table (e.g. for the matched-table
  rows at scale), STOP and flag it — that is the point where the §12 `subjects` manifest field, which belongs to the
  library split, should be decided instead.
- Dependencies: F33/F34 (declared words on the publish row) should land first so the comparison's "N of M" words show
  the same everywhere; the categories legend (F35) is CMP4's first item.

## 7. Owner rulings (coordinator relay, 2026-10-30, verbatim) — the note is FINAL

"1 agree 2. It can create; that work list may be useful for other comparisons as well - perhaps just table based
comparisons of # rows or quality scores. 3. yes"

| # | Question | Ruling | Effect on the design |
|---|---|---|---|
| 1 | ids Q-0109–Q-0111; schema at Assessment, data at Analysis | agreed | catalogue rows as §1 |
| 2 | chat creates the two-member work list on the person's press | yes, and the list is reusable | §4 stands; the list "compare: X ⇄ Y" is an ordinary database work list, so every set step offered on a work-list page (#944) runs on it too — see §8 |
| 3 | row-by-row diff deferred to a later, priced slice | yes | CMP2 uses counts, profiles and fingerprints (estimated); a keyed row diff is CMP5, backlog, with its own price line |
| 4 | (clarification) "to be clear, on comparison - the answer may be a degree of similarity or a is a subset etc. Not just a yes/no on are they the same" | ruling | every verdict is a relation with a degree (§2 relation + degree measures, §3a computation, §4 answer shape); CMP1's declared result carries `schema_relation` and `schema_similarity` |

## 8. The comparison list as a reusable set (owner ruling 2)

The two-member work list the chat press creates is not special to the comparison steps. It is a set subject (#941),
so any set step runs on it and stores a result per member plus a roll-up: the row-count snapshot step gives "rows per
table, side by side"; the quality-checks roll-up gives "quality score 0.91 vs 0.87, 37 vs 41 failing checks"; the value
profile container gives per-column agreement. Design rules this adds:
- the list is named for its purpose ("compare: coco_pharma@5432 ⇄ coco_pharma@5442"), kind database, created by the
  person's press, deletable like any list; it appears under Work lists with the set steps section #944 built;
- a set step's roll-up on a two-member list may declare a `comparison` presentation hint (two columns, one per member,
  difference column last) — a rendering choice in the roll-up record, not a new result kind;
- the comparison steps of §2 remain the only ones that *judge* sameness; the other steps lay numbers side by side and
  say nothing about "same".

## 9. Backlog

- CMP5: keyed row-by-row diff on matched tables, priced before the press (reads rows across two databases).
- §12 of the lifecycle note (`subjects` with roles, non-resource references) waits for the library split.
