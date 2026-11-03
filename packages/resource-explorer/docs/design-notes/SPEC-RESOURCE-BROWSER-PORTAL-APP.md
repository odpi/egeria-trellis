# SPEC — Resource browser: a shop-for-data app on Egeria alone (2026-10-12, revised after the Portal session's review)

For the Claude session working on egeria-workspaces (the Egeria Portal),
from the design session, at the project owner's request. Companion to
`DESIGN-AMUNDSEN-MIGRATION-ASSESSMENT.md`, which carries the reasoning;
this note carries what to build. Revision 2 folds in the Portal
session's review of the same day and the owner's decisions it relayed
(marked "owner, 2026-10-12"); the owner confirms them by merging.

## 1. What it is, in one paragraph

A single-page app, beside the Portal's existing apps and in the same
no-build React stack served by `PyegeriaWebHandler`, for anyone looking
for data: an analyst, a casual browser, a person who does not curate.
It reads Egeria only; it never reads Resource Explorer's registry, so it
runs wherever Egeria runs. It writes back only what Egeria already
models. It replaces what an Amundsen user opens daily: home, search
ranked by use, one page per resource, browse.

## 2. Hard constraints

- **Egeria is the only master copy.** Every read and write goes through
  pyegeria's clients or the Portal's existing FastAPI endpoints that
  wrap them. A search index (§6, layer L2) is allowed only as a
  rebuildable cache fed from Egeria that the app falls back from when it
  is down; nothing lives only there. No hard dependency on anything but
  Egeria (owner's rule).
- **Egeria's own types and feedback.** `Rating`, `Like`, `Comment`,
  `NoteLog`, `InformalTag`, `SearchKeyword`, `Collection` and its
  subtypes with `CollectionMembership`, `Ownership`, `DataScope`, the
  governance classifications, `SurveyReport` annotations, lineage
  relationships. No parallel notion of a tag, a note or a list.
- **Honesty.** A number is labelled with what it counts and when it was
  measured. "Never measured" and "measured, found nothing" are shown
  differently, always. A count from a sample says so. An element hidden
  by zones is never reported as "nothing exists": the page says the
  result may be incomplete for this user.
- **No steward controls.** No publish, no survey, no curation verdicts,
  no archive or delete. A person who needs those is sent to RE or the
  Portal's steward apps by a link that names them.
- **Sign-in is the Portal's** (X-Egeria-Token header). The signed-in
  user is the author of every write. Zone security already blocks
  unauthorised updates, which is why in-place editing is allowed (owner).
- **The three flavours are a facet and a badge, never three apps.**
- **Portal mechanics** (from the Portal session): both PyegeriaWebHandler
  copies (freshstart and quickstart) get the change; caches key on
  `egeria_auth.lineage_key()`; async routes use the `*_async` factories.

## 3. The three flavours, and how the app tells them apart

| Flavour | Meaning | How the app recognises it | Badge and next action |
|---|---|---|---|
| (a) Known | An asset Egeria knows about, curated or not | Any asset without (b) or (c) below | "Known" · next: ask about it (the owner, comments, a Data Sharing Request to the party that holds it) |
| (b) Data product | Curated and prepared to be subscribed to | Being, or membership of, a `DigitalProduct` (model 0710) with a subscription available | "Product" · next: subscribe |
| (c) Hub asset | Authoritative, actively maintained data available for sharing | `CollectionMembership` in a `DataSharingHub` (0705, a Collection subtype; [concept page](https://egeria-project.org/concepts/data-sharing-hub)) | "Hub" · next: use now under the hub's access conditions, or raise a Data Sharing Request |

Precedence: (c), then (b), then (a); the badge shows the highest and the
page lists the others. One function, one committed test per flavour and
one for precedence.

## 4. Pages

### Resource (phase 1; one standalone component, GUID as its only input)
The Catalog's views in `tech-catalog.html` are the nearest existing
thing; there is no dedicated asset page in the Portal today, so this
component is new and The Catalog may adopt it later.
- Header: name, type, flavour badge, one primary next action by flavour,
  owner, rating summary, favourite, add to list; **data scope** (the
  `DataScope` classification: time and place) shown as chips.
- Attribution line: **"last updated by <user> · <date>"** from the
  element header (owner: `updatedBy` covers any property, so never
  "described by").
- Description: editable in place by anyone zone security allows. Beside
  it, **notes**: a `NoteLog` on the element for annotations that are not
  the official description. "Request expert review of something I think
  is wrong" is a backlog item, to be a request for action (owner).
- Columns (tables and schemas): name, type, description editable in
  place, data class if present, key marks, statistics from the latest
  survey annotation with its date, "no survey yet" when none.
- Statistics: row count and size with as-of date and source (catalog
  estimate versus measured); distinct values and profiles where present.
- Lineage: reuse `lineage_handler` and the lineage explorer; one level,
  expandable; "no lineage recorded" when none.
- Where it is used: processes and reports that read it; usage by user
  where the usage measure records it.
- People: owner, stewards, people who rated or commented.
- Feedback: reuse `/api/egeria-feedback/*` with `EgeriaFeedbackWidget`
  and `EgeriaCommentsSection` (likes, ratings, comments).
- Tags: `informal_tags_handler` reads today; attaching and removing an
  `InformalTag` under the signed-in user is new backend work.
- Preview: the Portal's only preview is `/api/digital-products/{guid}/tabular`
  for `TabularDataSet`. Show it where it applies; for a relational
  table say "no preview available for this resource" until a table
  preview exists. Never a blank pane.

### Home (phase 2, popularity in phase 4)
- Recently changed: `LAST_UPDATE_RECENT` ordering, already in the Portal.
- Favourites: the Portal's existing favourites
  (`favorites_handler.py`; master copy
  `Person.additionalProperties["portal_favorites"]`), not Likes (owner).
  Likes and ratings are ranking inputs, not bookmarks.
- My lists: the collections the user owns or can see (§5).
- Popular: by the usage measure Egeria already holds (owner, 2026-10-14):
  the `DataScope` classification's additionalProperties written by the
  open-lineage data-scope governance service (the "nanny" work). Live
  on quickstart 2026-10-14 (analysis runs about 12:00 and 23:00 UTC; 12
  of 110 DataScope elements carried usage). Properties present on every
  measured element: `readsPerDay`, `readsInWindow`, `readersInWindow`,
  `lastReadInWindow`, `analysisWindowStart`, `analysisWindowEnd`,
  `analysisTime`, `analysisService`; present on only some: `readCount`,
  `lastReadAt`, `lastReadByRunId`, so the tile relies on the first set
  only. Location trap: in Egeria 6.2 and pyegeria 6.2 the classification
  is returned at `elementHeader.dataScope`, not in the
  `elementHeader.classifications` list; a reader that looks in the list
  reports zero. The tile ranks by `readsPerDay` within the window and
  shows `analysisWindowEnd` as the as-of. A resource whose DataScope
  carries no usage properties, or has no DataScope, says "no usage
  recorded". Never a fake ranking.

### Search (phase 2 at L0; ranking and facet counts in phase 4)
- One box over names, qualified names, descriptions, tags, keywords,
  column names, owners; reuse `/api/catalog/search` where it fits.
- Facets: flavour; resource type; tag; badge (governance classification);
  owner; source system; collection; **data scope** (time and place);
  scope and granularity (the level results are shown at, children
  rolled up).
- Each result row shows the find-result fields first (name, type,
  description, owner, last updated). The per-row lookups (flavour badge,
  rating summary, "in N of your lists", usage) fill in **lazily for
  visible rows only**, so a search costs one find plus a handful of
  calls, not hundreds against a ten-connection pool.
- Paging follows Egeria's rule: only an empty page means the end.
- The page always says how results are ordered and from which layer.

### Browse (phase 2)
- By tag, badge, owner, source system, collection, flavour, data scope.
  Each a list page with the same result row as Search.

## 5. Work lists (phase 3, after the two decisions below)

What exists: RE's work lists are `WorkingSet` collections with
qualifiedName `WorkingSet::resource-explorer::<slug>` and a
`membershipRationale` on each `CollectionMembership`; investigation
disposition sets are also WorkingSets, so a reader filters on the
prefix. RE's registry is the master copy, publishing is explicit, and RE
never reads collections back from Egeria. So the first draft's "same
collection, nothing special" holds only one way, RE to browser, for
published lists.

Proposal for v1, for the owner to decide:
- **(a) Two prefixes, one direction each.** The browser reads RE's
  published lists read-only and shows them under "from Resource
  Explorer". It creates its own as `WorkingSet::portal::<owner>::<slug>`
  with the same rationale property. RE may later pull Portal lists
  (option (b), its own design note) with a stated rule for which copy
  wins; until then neither app edits the other's.
- **Team sharing** follows the pattern RE's investigations already use
  (owner, 2026-10-12): a list is shared with a **Community or a Team**,
  both Egeria actor profiles, and "my lists" shows the lists of every
  community and team the user belongs to. The relationship is
  `ResourceList` from the Community's or Team's actor profile to the
  WorkingSet (owner, 2026-10-12), the same one RE's investigation
  publish writes, so the two apps agree. As built by the Portal session
  (2026-10-13): a user's communities and teams are found by the chain
  UserIdentity → ProfileIdentity → Person → PersonRoleAppointment → role
  ← AssignmentScope ← Team or Community; a shared list is read-only to
  members, who copy it to change it, and only its creator edits it. RE's
  own "my lists" should read the same chain so the two apps agree on who
  sees what.
- **Deleting a list:** Egeria refuses to delete a collection that still
  has members without cascade (OMAG-GENERIC-HANDLERS-403-005, which
  pyegeria reports as 401), and cascade is never used; remove the
  memberships first, then delete by GUID.
- **The type is right.** RE's work lists are already Egeria's
  `WorkingSet` (the official type; RE's module says so in its first
  lines). Only the qualifiedName prefix is RE's own. Nothing to correct.
- **Hand to a steward** is unresolved: request for action versus to-do,
  with several mechanisms; the owner is working it out with the Egeria
  lead. Until then the control is absent, not stubbed.
- Backend: `collections_handler` already creates collections and adds or
  removes members in bulk; the new work is the prefix, the rationale and
  the read of RE's prefix.

## 6. Search layers and backend

- **L0 (build first):** Egeria find with property and classification
  conditions, relevance order, source labelled on every response. The
  "source on every response" field is the seam that lets a later layer
  replace L0 without changing the page.
- **L1, buckets as search keywords (owner's direction, 2026-10-12):**
  rather than ordering by discrete counts, which Egeria's find cannot
  do, the popularity measure (DataScope's `readsPerDay` and
  `readersInWindow`, above) is turned into a small set of **buckets**
  ("Very popular", "Popular", "Rarely used", "No usage recorded") and
  each written to the asset as a `SearchKeyword` through
  `search_keywords_handler.py`, never as an `InformalTag`, which is
  user-facing. A bucket is a filter and a coarse sort tier that find can
  apply; the exact figure stays on the measure annotation and shows on
  the row with its as-of date. Rules: the bucket names are a valid value
  set; the boundaries are quantiles recomputed on a schedule and stored
  with the set, so "Very popular" means the same thing on every row of
  one run; the keyword carries the run's as-of; a resource with no usage
  measure gets "No usage recorded", never the lowest bucket. The same
  pattern serves ratings ("Highly rated") and recency. It is cheap to
  maintain, re-keyed only when a bucket changes, and gives the Home and
  Search pages a ranking without an index. Fine ordering inside a bucket
  is relevance at L0, or an index at L2.
- **L2:** an external search index fed by Egeria events, rebuildable,
  Egeria the master copy, with the app falling back to L0 whenever the
  index is unavailable. Allowed under §2 only with that fallback.
- Ranking by use, likes or ratings, facet counts and scope filters all
  need either the full result set or precomputed signals, which Egeria's
  find cannot sort by or count; they wait for L1 or L2 (phase 4).
- Endpoints: FastAPI in the web handler, one per page need, thin over
  pyegeria, following the `pyegeria-spa-add-tab` pattern; every response
  carries its as-of time and its source call.

## 7. What is explicitly not in scope

- Anything that needs RE's registry.
- Ingestion of any kind.
- Popularity when Egeria has no usage measure: show the honest absence.
- Standing needs and notifications: the answering app's job.
- Product and hub management: their own tools.

## 8. Phasing and gate, by use

Phases: (1) Resource page with feedback, notes and tag widgets; (2) L0
search, Home with recently changed, favourites and my lists, Browse;
(3) work lists once §5 is decided; (4) popularity, ranking and facet
counts after the usage measure is reinstated and the search layer is
chosen. Phases 1 and 2 are enough for gates 2 and 5.

1. (phase 4) From Home, find coco_pharma's most-used table without
   typing; before phase 4, see the honest absence and recently changed.
2. (phase 1–2) Search "sales", filter to tables and the (a) flavour,
   open a result, read its columns and statistics with dates, improve
   one column's description, see "last updated by <you> · <time>".
3. (phase 3) Favourite it, rate it, add it to a new personal list under
   the Portal prefix, and see an RE-published list appear read-only
   under "from Resource Explorer".
4. Open a data product and a hub asset (once one exists), see the right
   badge and the right next action.
5. (phase 1) Open a resource with no survey and no lineage, and see "no
   survey yet" and "no lineage recorded", never empty tables; open one
   hidden by zones under another user and see the incompleteness notice.

## 9. Open items

- [x] Hub flavour type: `DataSharingHub` (owner, 2026-10-12).
- [ ] Work lists v1: option (a) two prefixes, one direction each (owner).
- [x] Team sharing: Community or Team by `ResourceList`, as
      investigations do (owner, 2026-10-12).
- [ ] Hand to a steward: RFA versus to-do (owner with the Egeria lead).
- [ ] Search beyond L0: L1 buckets as search keywords is the owner's
      direction; bucket names and quantile schedule to be set; L2 later
      if fine ordering is wanted.
- [x] Where the usage measure lives: the DataScope classification's
      additionalProperties from the open-lineage data-scope governance
      service (owner, 2026-10-14); its reinstatement after the
      architecture changes is the Egeria side's item.
- [ ] Portal app or trellis app: this spec assumes the Portal.
