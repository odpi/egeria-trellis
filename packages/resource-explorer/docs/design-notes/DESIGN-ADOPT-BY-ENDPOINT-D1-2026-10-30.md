# DESIGN — D1: adopt an existing database element by endpoint, and read Egeria's duplicate links (2026-10-30)

Owner's rulings (coordinator relay, verbatim). On the Coco/RE families: "So these are two distinct database instances
and most likely have different content but the same name … which is why we use qualified names that should providing
uniqueness. Perhaps that isn't the situation here but it sounds like it.." — checked read-only: both families' Endpoints
are jdbc:postgresql://host.docker.internal:5442/coco_pharma, so it is one instance. On the rule: "yes - I agree with
that rule; that said we may still find instances where we have two different qualified names that point to the same
resource - the duplication finder in egeria will most likely find it and will allow them to be linked".

## In one page

When RE publishes a database, it should first ask Egeria whether a database element already exists for the same
endpoint (host, port, database name), and if so use that element and its schema family instead of making a second one.
Two databases with different endpoints stay apart even when their names match. When two elements for one endpoint
already exist, Egeria's duplicate finder links them (PeerDuplicateLink, or a ConsolidatedDuplicate with
ConsolidatedDuplicateLinks), and RE reads those links: anything linked as a duplicate of RE's element counts as RE's for
read-back, the "In Egeria" column and rule publishing, never as absent. For today's coco_pharma pair the choice is the
owner's and Mandy's: leave both, link them by hand, or let the duplicate finder run. No new RE tables are needed.

## 1. Facts this rests on (read 2026-10-30)

- RE's family: `PostgreSQL Relational Database::host.docker.internal:5442::coco_pharma` (RelationalDatabase c990f349),
  Endpoint networkAddress `jdbc:postgresql://host.docker.internal:5442/coco_pharma`, 4 DeployedDatabaseSchema.
- Content-pack family: 22 DeployedDatabaseSchema `PostgreSQL Relational Database Schema::Coco PostgreSQL Server 1::
  coco_pharma.<schema>`, each with an Endpoint `…5442/coco_pharma?currentSchema=<schema>` and a VirtualConnection
  `{databaseName=coco_pharma, databaseSchema=<schema>}`; no RelationalDatabase, SoftwareServer, Connection or Endpoint
  element named "Coco PostgreSQL Server 1" (it is a request parameter of the pack's CreateAsCatalogTarget actions).
- Egeria's duplicate model (OpenMetadataType.java): classification `KnownDuplicate`; relationship `PeerDuplicateLink`
  (two elements that describe one thing, with statusIdentifier/confidence/steward); classification
  `ConsolidatedDuplicate` on a surviving element with `ConsolidatedDuplicateLink` to each contributor. The duplicate
  finder (Egeria's survey/ governance action) proposes PeerDuplicateLinks; a steward confirms.

## 2. (a) Adoption at commit time

Rule: **a database's identity in Egeria is its endpoint — host, port, database name — never its qualified-name prefix
or display name.**

Where: `catalogue_commit.publish_database` (the typed RelationalDatabase read/create) and `catalogue_gateway`.

1. Before creating RE's RelationalDatabase, read candidates by endpoint: `find Endpoint whose networkAddress is
   jdbc:postgresql://<host>:<port>/<db>` (and the `?currentSchema=` variants), then walk ConnectionEndpoint →
   Connection → ConnectionToAsset → the asset, collecting RelationalDatabase and DeployedDatabaseSchema elements.
   Host equivalence is a small, declared table (host.docker.internal ≡ localhost ≡ 127.0.0.1 when RE and Egeria share a
   machine; configured, never guessed), so "laz_local" and "host.docker.internal" compare correctly.
2. Exactly one RelationalDatabase found → **adopt**: record its GUID as the database's `egeria_asset_guid`, and publish
   schemas as members of its family (its QN prefix), attaching to the existing DeployedDatabaseSchema where the schema
   name matches rather than creating a sibling. The proof row says "adopted <guid> · by endpoint".
3. Schemas only, no RelationalDatabase (today's Coco pack shape) → adopt the schema elements by name (aus_inventory →
   b798274a) and create RE's RelationalDatabase if none exists, linking the adopted schemas to it; say so on screen
   ("schema already in Egeria under 'Coco PostgreSQL Server 1' · adopted").
4. More than one RelationalDatabase for one endpoint → do not pick: list them on the Contents page with their QNs and
   the words "two Egeria elements describe this endpoint · Egeria's duplicate finder can link them"; publish to the one
   RE already holds (or the newest if none), and never create a third.
5. Different endpoints, same database name → **separate** by design; the screen may note "another database named
   coco_pharma exists at <endpoint>" with a link to the comparison questions (DESIGN-RESOURCE-COMPARISON-QUESTIONS).

Publish-rules (`not_in_egeria`) and read-back use the same resolver, so a rule's target is "the schema element for this
endpoint and schema name", whichever family holds it.

## 3. (b) Reading Egeria's duplicate links

Wherever RE answers "is this in Egeria?" (read-back after a commit, the IN EGERIA column on Contents, publish-rules'
target check, `reinclude_check`), the set of elements that count as RE's own is:

- RE's own element (by GUID or QN), plus
- every element reachable from it over `PeerDuplicateLink` (either end), plus
- for an element classified `ConsolidatedDuplicate`, every contributor over `ConsolidatedDuplicateLink`, and for a
  contributor, its consolidated survivor.

One relationship read per element (`get_related_metadata_elements(guid, "PeerDuplicateLink")`, then
"ConsolidatedDuplicateLink"), cached per commit-preview. Words: "in Egeria · also described by <other QN> (linked as a
duplicate)". A duplicate link in status proposed (statusIdentifier below confirmed) still counts for reading, with the
word "proposed duplicate"; RE never writes to the other element of a proposed pair. RE never creates PeerDuplicateLinks
itself in this slice; it may offer the pair to Egeria's duplicate finder (a press, a governance action) later.

## 4. (c) Today's coco_pharma second family — for the owner and Mandy

Options, in order of least change:
1. **Leave both** and let adoption (§2) stop any third copy; RE's Contents shows the 22 Coco schemas as "in Egeria
   under 'Coco PostgreSQL Server 1'" once F41/D1 read-back lands.
2. **Run Egeria's duplicate finder** over the two families (Mandy's side; a governance action on the quickstart) and
   confirm the PeerDuplicateLinks as steward; RE then reads them by §3 with no RE change beyond §3.
3. **Consolidate**: pick one survivor (RE's RelationalDatabase, since the pack has none) and mark the pack's schema
   elements ConsolidatedDuplicate contributors; the heaviest change, and it touches the content pack's elements.
Recommendation: 1 now, 2 when the duplicate finder is available on the quickstart; 3 only if the pack is re-loaded.

## 5. Slices, gate, DDL

- **D1-a resolver + read-back** (RE): endpoint resolver with the host-equivalence table; duplicate-link reading in
  read-back, IN EGERIA, publish-rules, reinclude_check. Gate: on coco_pharma, read-back shows the 4 RE schemas and the
  22 Coco schemas as in Egeria (with "under 'Coco PostgreSQL Server 1'"), publish-rules stops saying aus_inventory is
  not in Egeria; on a throwaway with a unique endpoint nothing changes. Unit test with a fake gateway that returns a
  PeerDuplicateLink pair.
- **D1-b adoption at commit** (RE, Egeria write): the five cases of §2 as tests over a fake gateway; the live proof on a
  throwaway database first published under one host alias, then re-registered under another alias of the same endpoint:
  the second publish adopts (one RelationalDatabase, proof row "adopted · by endpoint"). Needs the owner's word
  (shared Egeria write) and a peer round.
- **D1-c** (Egeria, Mandy): run or schedule the duplicate finder; confirm links; RE reads them (D1-a). Not RE work.
- **DDL: none.** `egeria_asset_guid` and the proof rows exist; the host-equivalence table is configuration
  (`EGERIA_HOST_ALIASES`, declared and read — a configured-but-never-read check in the gate). If a builder finds the
  adoption needs a per-schema adopted-GUID column that today's proof rows cannot carry, stop and flag it.

## 6. Open for the owner and Mandy

1. §4: leave / link / consolidate for today's pair.
2. The host-equivalence list for this machine (host.docker.internal, localhost, 127.0.0.1, laz?).
3. Whether a *proposed* (unconfirmed) PeerDuplicateLink should count for the IN EGERIA column, or only confirmed ones.

---

## 7. Owner rulings on §4 and §6 (coordinator relay, 2026-10-30 evening, verbatim) — supersede §4's options and §5's slices

| # | Question (§6) | Owner's words | Effect |
|---|---|---|---|
| D1-1 | §4 leave / link / consolidate for today's pair | "This is a bit of a special case because a coco_pharma is part of a content pack - in this particular case we should merge our annotations and extensions onto the coco_pharma version. We do not, as far as I understand, need to make any changes to the content packs definitions - we are only adding to them - which is perfectly fine." | None of §4's three. The pack's family is the one that stays; RE's annotations and extensions move onto it; the pack's own elements are never edited, only added to (new relationships and new annotation subjects). §8 designs the move. |
| D1-2 | host-equivalence list (localhost, 127.0.0.1, host.docker.internal, laz) | "Agree" | `EGERIA_HOST_ALIASES` ships with that set for this machine; configured and read (gate checks both). |
| D1-3 | proposed vs confirmed duplicate links for IN EGERIA | "Ok" (to: only confirmed count as IN EGERIA; proposed shows as possible duplicate) | §3 amended: a proposed PeerDuplicateLink is read and shown as "possible duplicate · not confirmed" and does NOT make the column say in Egeria; a confirmed one does. |

## 8. Re-anchoring RE's coco_pharma family onto the content pack's (D1-1 design)

**What exists (from §1 and the 2026-10-30 investigation).** RE's family: RelationalDatabase c990f349 with 4
DeployedDatabaseSchema (aus_inventory and three more, 55 tables, built by Egeria's JDBC cataloguer from RE's six
catalog targets), RE's SurveyReports and their annotations anchored on c990f349, the Contents proof rows and
`egeria_asset_guid` in the registry, the two published data-quality rules (on the throwaway, not coco — none on coco
yet). The pack's family: 22 DeployedDatabaseSchema under "Coco PostgreSQL Server 1" (the same 4 names among them), each
with its own Endpoint and VirtualConnection; no RelationalDatabase.

**Principle.** The pack's elements are the subjects. RE adds relationships and annotations that point at them; it
changes no property of theirs. RE's own duplicates are RE's to retire, in RE's time, after the read-back proves the
pack's elements carry everything RE needs.

**The move, in order (each step is an Egeria write: owner's word + peer round each; none is undoable except by hand):**

1. **Resolve** (no write). For each of RE's 4 schemas, find the pack's DeployedDatabaseSchema of the same name by the
   §2 endpoint resolver (host-alias table, `?currentSchema=` variant). Output: a four-row plan on the Contents page
   and in the log — RE GUID → pack GUID, name, what hangs off RE's copy today (annotation subjects, rule links, catalog
   target). This is the D1-a deliverable; the owner reads the plan before any write.
2. **Database anchor.** The pack has no RelationalDatabase, so c990f349 stays as the database-level element (the
   SurveyReports stay on it; nothing moves at database level). RE adds the same database→schema relationship the
   cataloguer uses today between c990f349 and RE's 4 schemas, now to the 4 pack schemas (read the type name from the
   existing relationship at step 1 and reuse it; do not guess it). Adding a relationship to the pack's element is "only
   adding". Result: one database element whose schema members are the pack's.
3. **Schema-level annotations and rules.** Everything RE wrote with a schema or table subject (declared measures
   published per schema, data-quality rules, table-level RequestForAction) is re-published with the pack's element as
   subject. The old annotations on RE's copies are not deleted (they are RE's history; Egeria keeps them under the old
   subject). Publish-rules and the publish table use the resolver, so from this step on new presses land on the pack's
   elements with no further change.
4. **Duplicate link.** RE writes one PeerDuplicateLink per pair (RE copy ↔ pack copy), statusIdentifier proposed,
   steward = the signed-in user. Egeria's duplicate finder would propose the same; writing it now lets the §3 read-back
   explain the pair on screen ("also described by … · possible duplicate · not confirmed"). Confirming is a steward's
   act in Egeria (Mandy's side or the owner in the Portal), not RE's.
5. **Stop feeding RE's copies.** Remove RE's 4 schema catalog targets from the JDBC cataloguer (RE's own relationships,
   not the pack's), so the hourly refresh stops rebuilding RE's copies. The 22 pack targets keep refreshing the pack's.
6. **Retire RE's copies** (later, after one full read-back cycle shows the pack's elements carry the annotations and
   rules): soft-delete RE's 4 DeployedDatabaseSchema and their tables (pyegeria 6.2.10 restore exists if wrong). Blocked
   today by the archive-cascade rule (restored schemas and soft delete stay blocked per the 10-23 proof) — so this step
   waits for that lift, and the plan says so on screen: "RE's copy kept until soft delete is cleared".

**Read-back after each step.** IN EGERIA for a coco schema = the pack's element is found by endpoint and name AND the
annotation or rule RE last published is attached to it (read by GUID). Until step 3 runs for a schema, the column
reads "in Egeria (pack) · RE's measures still on RE's copy" — a true intermediate state, not a pass. The proof rows
record the pack GUID as the schema's anchor (a new value in an existing column if the row shape allows; if a per-schema
anchor column is needed, stop and flag — §5's DDL rule stands).

**Why not move the SurveyReports.** A SurveyReport's subject is the database; the pack has no database element; moving
them would mean inventing one in the pack's family, which is changing the pack. c990f349 stays.

**Generalization.** Any future content-pack database (the pack ships schema elements, RE finds them by endpoint) follows
the same six steps; §2 case 3 ("schemas only, no RelationalDatabase") becomes "adopt the pack's schemas as members of
RE's database element" at first publish, so the duplicates never arise. Today's coco_pharma is the one legacy case.

## 9. Slices after the rulings (replaces §5)

- **D1-a — resolver, alias table, read-back, and the re-anchor plan (no Egeria write).** Endpoint resolver with
  `EGERIA_HOST_ALIASES` (declared AND read; the gate asserts both); IN EGERIA / publish-rules / reinclude_check read
  both families and duplicate links (confirmed counts, proposed shows "possible duplicate · not confirmed"); the
  four-row re-anchor plan on coco_pharma's Contents page and in the log (§8 step 1), with the relationship type name
  read from c990f349's existing schema links. Gate: coco_pharma Contents shows the 4 RE schemas and the 22 pack
  schemas as in Egeria with "under 'Coco PostgreSQL Server 1'"; publish-rules stops saying aus_inventory is not in
  Egeria; the plan lists 4 pairs with GUIDs; a throwaway with a unique endpoint shows no plan and no change; unit tests
  over a fake gateway for the resolver, the alias table, the confirmed/proposed distinction and the plan.
- **D1-b — the re-anchor writes (§8 steps 2–5)**, one step per press, each behind the owner's word and a peer round, with
  the read-back words of §8 after each. Step 6 waits for the soft-delete lift.
- **D1-c — adoption at first publish (§2 cases 1–5, case 3 as amended in §8)**: the live proof on a throwaway stays as
  §5 wrote it.
- **D1-d (Mandy / owner in the Portal):** confirm the PeerDuplicateLinks as steward.
- DDL: none expected; the flag rule stands.

## 10. Correction to §8, and D1-b revised (2026-10-30 late, after the D1-a builder's live read; verified by the architect)

§8 said the pack's 22 schemas held "the same 4 names" as RE's 4. **That was asserted, not read, and it is false.** Read
again 2026-10-30 ~22:40 local (pyegeria, DeployedDatabaseSchema elements whose qualifiedName contains coco_pharma):

| family | count | schemas |
|---|---|---|
| RE (`…host.docker.internal:5442::coco_pharma`, under c990f349 by DataSetContent) | 4 | coco_ods, coco_sus, target_sales, us_sales |
| pack ("Coco PostgreSQL Server 1") | 22 | aus_inventory, austin_haz_mat, ca_payroll, coco_expenses, coco_haz_mat, coco_hrim, coco_inventory, coco_ledgers, coco_products, cocopages, ed_mfg_control, eddepot01, global_crm, kcdepot01, manufacturing_planning, mfctrl9482, nl_payroll, procurement01, sec_admin, uk_payroll, winch_mfg_control, winchdepot01 |
| neither | 4 of the database's 30 | demo, demo_auth, eu_sales, public |

The two families are **disjoint at schema level**. There are no duplicate schema elements, so §8 steps 3 (re-publish
onto pack elements), 4 (PeerDuplicateLinks), 5 (remove RE's catalog targets) and 6 (retire RE's copies) have nothing
to act on for coco_pharma and are **withdrawn**. The only doubling is at the database level: two families describe one
endpoint, with no shared member.

**What the owner's ruling means here.** "Merge our annotations and extensions onto the coco_pharma version … we are only
adding to them": the pack ships 22 schema elements and no database element; RE adds. So:

- **Step 2 stands, and is the whole of D1-b:** c990f349 (RE's RelationalDatabase, the subject of RE's SurveyReports)
  gets the same DataSetContent relationship to each of the pack's 22 schemas that it already has to RE's 4 (the type
  name is read off the existing four links, as the builder did). After it, one database element has 26 schema members:
  22 the pack's, 4 RE's additions. The pack's elements gain a relationship and lose nothing. One press, 22 relationship
  writes, owner's word + peer round; read back by walking DataSetContent from c990f349 and counting 26.
- **RE's 4 schemas are RE's additions, kept** — the plan row says "RE's addition, kept". Nothing moves, nothing retires.
- **The 4 in neither family** are simply not cataloged by anyone; IN EGERIA says so truthfully ("not in Egeria"); if
  the owner wants them in, that is an ordinary Contents choice (RE publishes them under c990f349), not D1.
- **Annotations and rules:** every schema-subject publish already targets whichever family holds the schema (the
  resolver), so nothing is re-published. RE's SurveyReports stay on c990f349.

**D1-a plan page, revised (what the builder is showing):** one database-level row (c990f349 ↔ 22 pack schemas, the
relationship type named, "after the press: 26 members"), four "RE's addition, kept" rows, one "neither" line naming the
4. No pair rows.

**Still true from §8:** the generalization for future packs (adopt the pack's schema elements as members of RE's
database element at first publish) and the principle (add to the pack, never edit it).

**Lesson recorded** (feedback memory): I wrote "the same 4 names among them" from the shape of the problem, not from a
read; §1 had the facts (RE 4, pack 22) and never listed the names side by side. A one-line comparison would have shown
disjoint sets before §8 was designed.
