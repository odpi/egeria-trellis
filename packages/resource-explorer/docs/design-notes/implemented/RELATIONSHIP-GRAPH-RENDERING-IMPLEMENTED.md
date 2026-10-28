# Relationship graph rendering — implemented

## Update: long table-name labels overflowing their node boxes (separate branch, `re/relationship-graph-label-overflow`)

Reported live by the project owner, with screenshots, the same night this feature merged:
table-name labels ("salesorderheadersalesreason", "specialofferproduct", "countryregioncurrency")
visibly extending past their rounded node boxes in the rendered SVG.

**Root cause, confirmed empirically, not assumed from source.** The investigation started from a
specific hypothesis — that the `node [...]` default declarations in `relationship_dot.py` never set
an explicit `fontname`, so Graphviz would fall back to a different font than the `fontname="DejaVu
Serif"` the edge declarations pin, and lay boxes out for one font while a viewer sees another.
**That hypothesis was wrong**: reading the file (all three `node [...]` defaults, ~line 172/301/379)
showed every one of them already sets `fontname="DejaVu Serif"`, matching the edges exactly. No
missing/inconsistent `fontname` anywhere.

The real mechanism, found by rendering this module's own output through the real
`egeria-shared-kroki` container at `localhost:6002` (not just reading the DOT source) and inspecting
both the returned SVG and a browser's own text metrics:

1. Rendering `schema_detail_dot`'s output for `sales` with `salesorderheadersalesreason` (28 chars),
   `specialofferproduct` (20 chars), and `countryregioncurrency` (22 chars) through real Kroki
   reproduces the reported overflow exactly — the 28-char label's box has almost no left/right
   margin left, unlike the other two, which still have a visible (if tight) margin at 20–22 chars.
2. `"DejaVu Serif"` is correctly and consistently named in every `graph`/`node`/`edge` default in
   this module — but Graphviz does not embed the font or convert the label to outlined paths in the
   SVG it returns; it emits plain `<text font-family="DejaVu Serif">` elements. Graphviz's own
   server-side box-width *calculation* (done inside the Kroki container, against whatever "DejaVu
   Serif" metrics its fontconfig resolves there) and the box's *actual displayed* width (done by
   whatever renders the SVG afterward — the browser the project owner was looking at it in) are two
   independent font-metric lookups that only agree if that exact font is genuinely installed in both
   places.
3. Confirmed directly that it is not, in the environment used to view this app's evidence rail:
   `canvas.measureText()` with `font: '10px "DejaVu Serif"'` returns the width `112.72` for
   `"salesorderheadersalesreason"` — and asking for a font name that **does not exist at all**
   (`"ThisFontDoesNotExist12345"`) returns the *identical* `112.72`. Both silently fall back to the
   same generic serif. So the box was sized against real DejaVu Serif metrics inside Kroki, tightly
   (its per-node `margin` — `"0.06,0.02"`/`"0.07,0.03"` — leaves very little slack), and then
   displayed using a different, apparently wider, substituted font — which is exactly the overflow
   pattern reported, worst on the longest unbroken identifier.

**This is not fixable by choosing a "better" font name.** Any font-family string written into the
SVG is only as good as whatever the *viewer's* environment happens to have installed under that
exact name — there is no font this module could name that is guaranteed present in every browser
that will ever open this app. A fixed margin has the same problem one level removed: it can be
tuned to survive *today's* measured substitution gap, but nothing bounds how much wider a different
viewer's fallback font could be for an arbitrarily long, unbroken identifier.

**Fix: wrap, don't shrink or cut.** Real-world table names that trigger this
(`salesorderheadersalesreason`, `specialofferproduct`, `countryregioncurrency`) are the
AdventureWorks style — long, single "words" with no natural break — so no margin tuning fixes this
in general. `relationship_dot.py` now wraps any identifier over `WRAP_LINE_CHARS` (14) onto multiple
lines inside its node, instead of truncating it:

- `_wrap_identifier_lines(name)` slices `name` into ≤14-character lines, preferring to break right
  after the last `_` inside each window (readable for snake_case names) and falling back to a hard
  character-count wrap when there's no separator at all (the AdventureWorks case). Built so
  `"".join(_wrap_identifier_lines(name)) == name` always — the wrap only slices contiguous
  substrings, so **no character can ever be dropped**, unlike an earlier draft of this same helper
  which discarded the `_` itself at a line break (`special_offer_product` → `special_offerproduct`)
  until a test (`test_wrap_helper_prefers_underscore_breaks_when_present`) caught it.
- `_wrapped_html_label_text()` / `_wrapped_plain_label_text()` join those lines with `<br/>` (inside
  an HTML-like `label=<...>`) or Graphviz's own `\n` line-break escape (inside a quoted
  `label="..."`) respectively — both existing label forms this module already used, so no new label
  shape was introduced. The node's box and height grow to fit the extra lines the same way Graphviz
  already grows a box for the existing two-line title+caption pattern.
- Applied everywhere a bare table/schema name enters a node label: `_node_style` (used by
  `full_database_dot`'s hub and plain nodes), `full_database_dot`'s not-captured (dashed) nodes,
  all four branches of `schema_detail_dot`'s per-table labelling, and the "ghost" nodes
  `schema_detail_dot` draws for an outgoing key's external target.
- **The full identifier is never shortened anywhere in this fix.** Node IDs (used for edge
  references) were always the untruncated qualified name and stay that way; only the *display label*
  wraps. A shortened label with the full name on a `tooltip` attribute (an SVG `<title>`, visible on
  hover) was considered as a fallback but wasn't needed by anything this module draws — no node this
  module produces is constrained to a single line that can't grow (a `schema_map_dot` box, the one
  place that IS a single fixed-ish line, never carries a bare table name to begin with — only schema
  names, short in every real database surveyed so far).

**Clearing up the "schema-map" screenshot.** The project owner's report described the overflow as
visible "on both the schema-map (small sales schema box) and whole-database-schema-cluster views."
`schema_map_dot` (zoom level 1) draws one box per *schema* with table/key counts — it never draws a
bare table name, so it structurally cannot exhibit this specific overflow (confirmed by rendering it
through real Kroki with the same schema names — no issue, screenshot below). The "small sales schema
box" is almost certainly `schema_detail_dot`'s own per-schema cluster (zoom level 3) — a box titled
"sales" containing the schema's tables — which is exactly where the overflow reproduces. Recorded
here rather than corrected silently, since the discrepancy is a fact about the report, not a reason
to distrust it.

## Live verification (this fix)

Rendered `schema_detail_dot`'s `sales` view (the reported case), `full_database_dot`'s `sales`
cluster with the same three long names, and `schema_map_dot`, all through the real
`egeria-shared-kroki` container at `localhost:6002` (not a mock), and inspected the resulting SVG in
a real browser (not just the DOT source):

- **Before the fix** (`schema_detail_dot`, unwrapped): `salesorderheadersalesreason`'s label and its
  "no keys inside sales — joined across" caption both crowd the box edges with almost no margin,
  visibly worse than `specialofferproduct`/`countryregioncurrency` in the same rendering —
  reproduces the reported bug.
- **After the fix** (`schema_detail_dot`): all three long names wrap onto two lines
  (`salesorderhead`/`ersalesreason`, `specialofferpr`/`oduct`, `countryregionc`/`urrency`), each line
  sitting with a clear, comfortable margin inside its box — no overflow, at real 1x scale and at 3x
  zoom.
- **After the fix** (`full_database_dot`, `sales` cluster with the same three names plus `customer`):
  same wrapping behaviour, same clear margins, cross-cluster edge still renders correctly to the
  wrapped node.
- **`schema_map_dot`**: unaffected either way, as expected — it never carries a table name.

## Tests

`tests/test_relationship_dot.py` gained a new `TestLabelWrapping` class (8 cases): the wrap helper
never drops a character (short names pass through unwrapped; `salesorderheadersalesreason`, a
60-character synthetic name, and an underscore-separated name all reassemble byte-for-byte via
`"".join`); the longest real AdventureWorks name and the 60-char synthetic name are never cut (no
`…`/`...`) in `schema_detail_dot`, `full_database_dot`, or a `schema_detail_dot` ghost node; and
determinism holds with a long wrapped name in the mix. All 39 cases in the file pass (31 pre-existing
+ 8 new — see file for the exact count by class), unchanged behaviour for every existing case
(escaping, hub sizing, isolated/not-captured captions, credential-scope titles, the wired
integration tests) since every name in those fixtures is short enough to pass through the wrap
helper as a single unwrapped line.

## Full test suite (this fix)

```
uv run pytest tests/ -q -rf
6746 passed, 103 skipped, 0 failed, 4930 warnings in 1032.93s (0:17:12)
```

No `-k`, no deselects, full run. Zero failures. (Total collected count is higher than the
`6718 passed` recorded after the previous escaping/credential-scope pass above — other sessions'
work landed on `main` between that pass and this one; this run is against a fresh `origin/main` tip
fetched for this fix, not a stale count.)

---

## Update: escaping fix + credential-scope title (same branch, before merge)

Two items raised in review before this branch merged, both addressed in the same push:

**1. DOT/HTML escaping bug (PR/CI session review).** Node IDs were emitted unescaped, and
HTML-like labels (`label=<...>`) used `_dot_escape` (backslash/quote escaping — correct for a
*quoted DOT string*, wrong for HTML content) instead of HTML-entity escaping. A quoted Postgres
identifier containing `&`, `<`, `>` or `"` would have made Graphviz reject the WHOLE graph —
"renderer unavailable" instead of a diagram. Fixed by adding `_html_escape` (entity escaping, used
for every HTML-label's visible text) alongside `_dot_escape` (kept for quoted-string content) and a
`_id` helper (quote-escaping for every node/edge/cluster ID). `TestEscaping` (6 new cases) in
`tests/test_relationship_dot.py` pins this with a table named `a&b<c>"d` across all three zoom
levels, the schema-map box, the credential-scope title, and both label forms (`label="..."` and
`label=<...>`). AdventureWorks itself never exercised this path (no table name that hostile in
practice), which is exactly why it needed its own test rather than relying on the live fixture.

**2. Credential-scope title + per-schema "why" captions**, wired rather than left as a follow-up.
`db_derived.py` gained `_read_credential_capability` (a self-contained stored-blob read — cannot
import `survey_definition_adapter.py`'s existing `_credential_capability_results` without a
circular import, since that module already imports FROM `db_derived`) and
`_credential_scope_for_graph`, which reads the SAME stored `credential_capability` probe result the
header banner and the 21b coverage headline (`_credential_scope_status`) already read, and produces:

- `credential_scope` — `{connected_as, measured, total}` from the probe's own
  `relation_select`/`relation_total`, `None` when the credential has full visibility (same "stay
  silent" contract `_credential_scope_status` follows). Threaded into `full_database_dot`'s
  `credential_scope` parameter (already built and unit-tested in the first pass), producing a graph
  title reading "within `<connected_as>`'s access, N of M tables" — the same numbers the header
  banner shows, never a second count that could disagree with it.
- `not_captured_reason_by_schema` — for a schema whose credential grant is uniformly
  `SCOPE_STRUCTURE_ONLY` ("structure only — no SELECT") or `SCOPE_NOT_VISIBLE` ("not visible — no
  USAGE"), its not-captured tables get that specific caption instead of the generic "keys not
  captured". **Scoped honestly to what is actually stored**: `credential_capability`'s `by_schema`
  entries carry only a per-schema `table_select` COUNT (`connection.py`'s
  `get_credential_capability`), never a per-table boolean, so a `SCOPE_PARTIALLY_READABLE` schema
  (some tables selectable, others not, with no record of *which*) cannot be captioned more
  precisely than the existing generic caption — and that generic caption is still exact per table
  (it already comes straight from whether THAT table's own columns carry key information via
  `DerivedInputs.keys_captured_for_table`), it just cannot say *why* for a partially-granted schema.
  This is stated as a real, checked limit, not silently approximated as more precise than it is.

`TestWiredIntoDbRelationshipGraph` gained two integration tests: one reproducing the coordination
session's own coco_pharma shape (a `coco_ods`-equivalent schema with USAGE but SELECT on none of
its 23 tables, `relation_select: 3` of `relation_total: 26`) and asserting the title carries
`connected_as`/"3 of 26 tables" and `coco_ods`'s tables read "structure only — no SELECT" (never the
generic caption, never bleeding into the fully-readable `eu_sales`-equivalent schema); the other
confirming a fully-visible credential adds no title at all. Not live-verified against the real
`coco_pharma` database in this pass (the fixture reproduces its exact shape from
`tests/test_schema_containment_grain.py`'s own `_CAP_COCO_PHARMA`, so the numbers are the project's
own recorded ones, not invented) — see "Follow-ups" below.

Full suite after both fixes: see the updated "Full test suite" section below.

---

Wires the wireframe-only design note "Design: the relationship graph, drawn from the real
AdventureWorks edges" (`1051e4d4`, merged into `main` 2026-09-28 as part of
`re/design-batch-0928`) into the running `/next` app. Before this change,
`docs/design-notes/wireframes/RelationshipGraph.dc.html` and the three example `.dot` files under
`docs/design-notes/wireframes/relationship-graph/` were a design canvas only — the "Is there a data
model here?" question in the Questions tab rendered text/stats from `db_relationship_graph` and no
graph image anywhere. It now does.

## What was built

- **`resource_explorer/surveyors/database/relationship_dot.py`** (new) — pure Graphviz DOT
  generation, no I/O. Three zoom levels:
  - `schema_map_dot` — one box per schema, always renders regardless of database size.
  - `full_database_dot` — the whole database: schema clusters, hub nodes sized/emphasised by
    in-degree (`HUB_MIN_IN_DEGREE = 5`, matching the wireframe's own examples), cross-schema edges
    drawn darker than intra-schema ones, `dot` layout (not `fdp`, per the design note's own
    finding). Returns `(None, reason)` above `FULL_GRAPH_TABLE_LIMIT = 100` tables — the design
    note's own threshold — with the reason worded as a named state
    (`"graph not drawn: N tables; per-schema views available"`), never a blank panel.
  - `schema_detail_dot` — one schema opened, with its OUTGOING keys also drawn (as edges to
    plaintext "ghost" nodes for the external target). This is what fixes the design note's own
    finding: `sales.salestaxrate` and `sales.shoppingcartitem` have keys LEAVING the schema and
    must never be drawn/captioned as "isolated" — they are captioned "no keys inside sales — joined
    across", and only a table with NO key in either direction is captioned "isolated".
  - Honesty extension beyond the wireframe: a table whose own keys were never captured
    (`DerivedInputs.keys_captured_for_table` is False — a native-survey read-back, or a
    catalog-only-fallback table) is drawn dashed and labelled "keys not captured" in both the
    whole-database and per-schema views — never as "no relationships", which would assert a
    measurement that was never taken. `full_database_dot`/`schema_detail_dot` both take an optional
    `not_captured_by_schema`/`not_captured` parameter for this. A credential-scoped survey can also
    carry a `credential_scope` (`connected_as`/`measured`/`total`) into the whole-database drawing's
    title, in the same vocabulary the existing 21b coverage headline uses.
  - `build_relationship_diagrams` — the single entry point, assembling all three levels from data
    `db_derived.py` already computed (no second query path).

- **`resource_explorer/surveyors/database/db_derived.py`** — `relationship_graph_by_container`
  (the function that already computes the correct per-schema counting, withholding crossing
  columns from each schema's internal graph and reporting them separately as
  `cross_container_references`) now also builds `tables_by_schema`/`not_captured_by_schema` from
  the same `inputs`/`containment` it already has, calls `build_relationship_diagrams`, and attaches
  the result as a new `"graphviz"` key on its return dict. Via `apply_container_grain`'s existing
  `.update()`, this lands at `derived["db_relationship_graph"]["graphviz"]` — the exact dict every
  other reader of this analysis already reads (`_db_derived_field_reader`), so it reaches the
  frontend through the existing fact-reading path with no new plumbing.

- **`resource_explorer/web/routes/diagrams.py`** — new `POST /api/diagrams/graphviz`, mirroring the
  existing `/api/diagrams/mermaid` route exactly (same shared Kroki container, same raw-SVG
  response, same "no fallback, a clear 502" failure behaviour), but posting to Kroki's
  `/graphviz/svg` endpoint. Graphviz is emitted rather than Mermaid because `dot`'s cluster layout
  has no Mermaid equivalent — the design note's own instruction.

- **Frontend** (`envelope.js`, `app.js`):
  - `envelope.js`'s new `factGraphviz(env)` mirrors the existing `factMermaid(env)` (used by
    `architecture_diagram`) — reads `f.value.graphviz` off the envelope's facts.
  - The Evidence rail (`showEvidence`) now renders a "Relationship graph" block when a question's
    envelope carries one: a "Schema map" button (always present), a "Whole database" button (or,
    above the 100-table threshold, the named fallback state as text instead of a broken/blank
    button), and a schema-name `<select>` for "Open a schema…" covering every schema the question's
    containment scope names.
  - `promoteToPane` (the pane that actually renders a diagram, reused unchanged from the Mermaid
    path for pan/zoom/theming) now branches on `turn.graphviz` vs `turn.mermaid` — DOT skips the
    Mermaid-specific `mermaidForKroki` escaping and hits `/api/diagrams/graphviz` instead.
  - **Kroki-down handling**: both a network-level fetch failure and a 502 from the backend (Kroki
    itself unreachable or erroring) render a "renderer unavailable" panel with the DOT/Mermaid
    SOURCE shown as copyable text (`renderRendererUnavailable`/`bindCopyTargets`) — never a blank
    or broken image. The source is real evidence independent of whether Kroki is up.

## Coverage of the design note's three zoom levels + the coordination session's five requirements

1. Schema map — always renders, any database size. ✅ (live-verified, see below)
2. Whole database — schema clusters, hub weighting, darker cross-schema edges, `dot` layout,
   >100-table fallback as a named state. ✅ (live-verified for the render path; fallback path is
   unit-tested — `laz_local_adventureworks` has 68 key-captured tables, under the threshold, so the
   live database could not exercise the fallback itself)
3. Per-schema — outgoing keys drawn, "no keys inside X" never "isolated" for a table with only
   outgoing keys. ✅ (live-verified — see below)
4. Absence honesty (not-captured tables drawn dashed, credential-scope title) — ✅, now fully wired
   (see "Update" section above). `db_derived.py`'s `_credential_scope_for_graph` reads the stored
   `credential_capability` probe (same source as the header banner and `_credential_scope_status`)
   and threads both a whole-database title and per-schema "structure only"/"not visible" captions
   through to `build_relationship_diagrams`. Integration-tested against the project's own recorded
   coco_pharma shape (`TestWiredIntoDbRelationshipGraph::test_credential_scoped_survey_titles_the_
   graph_and_captions_structure_only`); not live-verified in a browser against the real
   `coco_pharma` database in this pass — see "Follow-ups".
5. Named fallback state, not a blank panel. ✅ (`full_fallback_reason` string, unit-tested with the
   exact wording; rail renders it as text when `full` is absent).
6. Kroki-down reporting with "copy as evidence" text fallback. ✅ (built; not live-exercised against
   a real down Kroki in this pass — covered by `TestRenderGraphvizRoute` at the route level, which
   is what actually determines the 502 path the frontend branches on).
7. Fold the whole-database headline before the per-schema list. Already true before this change —
   `apply_container_grain` appends the per-schema rollup sentence AFTER the whole-database
   `explanation` (`"{payload['explanation']} {rollup_sentence}"`), so the numbers already lead. No
   change needed; left as-is.
8. Tests: DOT generation determinism, not-captured/scoped cases, fallback threshold, Kroki-down
   route test. ✅ — see below.

## Live verification — `laz_local_adventureworks`

Ran the app from this worktree on a scratch port (8891, separate from the shared checkout's 8810 —
per this repo's CLAUDE.md, the shared checkout at `~/localGit/egeria-v6/trellis` must stay on `main`
and nobody else's server runs from it) and drove it through the actual `/next` UI, signed in as
`erinoverview` (logged out again afterward).

**Counts, as measured by the ALREADY-EXISTING `db_relationship_graph` analysis** (this change reads
that analysis's own output; it does not recompute anything):

- Evidence rail for "Is there a data model here...": **edge count 91 · component count 2 · largest
  component 67 · connected tables 67 · isolated tables 1 (`production.transactionhistoryarchive`) ·
  dangling references 0**.
- Schema map view: **person 13 tables/13 keys · humanresources 6 tables/5 keys · production 25
  tables/27 keys · purchasing 5 tables/4 keys · sales 19 tables/22 keys**, with cross-schema edge
  counts drawn between them.
- 68 of 68 key-captured tables total (5 real schemas + 5 empty abbreviation-named duplicate schemas
  — `hr`/`pe`/`pr`/`pu`/`sa` — that this survey run reports `not_established (no_schema_rows)` for,
  which is why the database's raw sidebar count reads "11 schemas · 157 tables" while the
  relationship-graph analysis's own key-captured population is 68, exactly matching the design
  note).

**These numbers match the design note's own recorded counts exactly** (68 tables, 91 keys, 2
components, 1 isolated table — the design note's "20 cross-schema" isn't a field this analysis
reports as one number, but the schema-map view's edge labels sum to the same cross-schema edges the
note counted). Nothing in `docs/Backlog.md` or recent commits shows `db_relationship_graph`'s
counting changed since the note was written 2026-09-28 — Section A/PR #316's PK/FK undercount fix
predates the note (the note's own commit message says its counts were fetched *after* that fix), so
no drift to explain here.

**Zoom level 1 — schema map**: rendered via Kroki, showing all 5 real schemas as boxes with their
table/key counts and cross-schema edges labelled with counts — visually matches
`schema_map.dot`'s shape.

**Zoom level 2 — whole database**: rendered via Kroki, showing schema clusters (person, sales,
purchasing, humanresources, production) with hub nodes in bold (`person`, `businessentity`,
`employee`, `product`, `salesterritory`) — matches `full_dot.dot`'s shape. (68 tables is under the
100-table threshold, so this is the render path, not the fallback path — the fallback is covered
by unit tests instead, per item 2 above.)

**Zoom level 3 — schema detail (`sales`)**: rendered via Kroki. Verified by reading the actual
rendered SVG's text nodes (not just eyeballing a screenshot):

```
"salestaxrate", "no keys inside sales — joined across",
"shoppingcartitem", "no keys inside sales — joined across"
```

This is the design note's own finding, confirmed fixed end-to-end against real data: both tables
have a key leaving `sales` and are captioned accordingly — never "isolated".

Screenshots of all three views were reviewed live in the session (schema map, whole-database, and
the `sales` detail view showing the salestaxrate/shoppingcartitem captions) but are not attached to
this doc as files; the SVG-text-content evidence above is the more precise record of the same
verification.

## Tests

`tests/test_relationship_dot.py` (new, 23 cases):
- `TestSchemaMapDot` — deterministic output, cluster-free per-schema boxes with counts, cross-schema
  edge labelling, width scales with table count.
- `TestFullDatabaseDot` — schema clusters present, cross-schema edges styled differently from
  internal ones (colour check), hub nodes sized/labelled by in-degree (built via a fixture crossing
  `HUB_MIN_IN_DEGREE`, not hardcoded to the wireframe's own numbers), not-captured tables drawn
  dashed and never claim "no keys", credential-scope title uses the 21b vocabulary, determinism,
  the >100-table fallback returns `None` with a named reason, and the boundary case (exactly at the
  threshold still draws).
- `TestSchemaDetailDot` — the core fix: a table with only outgoing keys says "no keys inside X",
  never "isolated"; the outgoing edge is actually drawn; a table with no keys at all IS labelled
  isolated (the one case allowed to say so); not-captured takes priority over the isolated label;
  determinism.
- `TestBuildRelationshipDiagrams` — assembles all three levels; fallback reason present with schema
  map still rendering at 100+ tables; not-captured threads through to both `full` and `by_schema`.
- `TestWiredIntoDbRelationshipGraph` (integration, same fixture pattern as
  `test_schema_containment_grain.py`) — `run_db_derived` over a real registry produces a
  `db_relationship_graph` result carrying `graphviz` with all three levels, and a table whose keys
  were never captured is dashed in the real wired-up output, not just in the pure function.

`tests/test_diagrams_route.py` — added `TestRenderGraphvizRoute` (5 cases), mirroring
`TestRenderMermaidRoute` exactly: success returns raw SVG, Kroki-unreachable and Kroki-error-response
both surface as 502 (not 500 — this is what the frontend's "renderer unavailable" branch depends
on), missing `source` is a 422, and the route posts to Kroki's `/graphviz/svg` with the right
content-type.

## Full test suite

First pass (schema map / whole database / per-schema zoom levels, Kroki route, absence honesty):

```
uv run pytest tests/ -q -rf
6710 passed, 103 skipped, 0 failed, 4898 warnings in 697.95s (0:11:37)
```

Second pass, after the escaping fix and the credential-scope title/caption wiring (8 new tests —
6 escaping cases, 2 credential-scope integration cases):

```
uv run pytest tests/ -q -rf
6718 passed, 103 skipped, 0 failed, 4902 warnings in 662.28s (0:11:02)
```

No `-k`, no deselects, full run both times. Zero failures.

## Follow-ups (not done in this pass)

- **Credential-scope title/captions are wired (see "Update" section) but not live-verified against
  the real `coco_pharma` database** — verified only through an integration test built from the
  project's own recorded `_CAP_COCO_PHARMA` shape (`tests/test_schema_containment_grain.py`), not
  by opening the app against the live database. If a live check later shows a discrepancy, check
  first whether `coco_pharma`'s actual `credential_capability` blob differs from that fixture shape
  (e.g. a different schema being the structure-only one, or a `SCOPE_PARTIALLY_READABLE` schema
  where this change's own documented limit applies — see the "Update" section's note on why
  per-table precision isn't available there).
- **Per-table (not per-schema) structure-only/not-visible captions are not possible without new
  storage.** `credential_capability`'s stored blob (`connection.py`'s `get_credential_capability`)
  aggregates SELECT capability to one `table_select` COUNT per schema; it never persists which
  SPECIFIC tables were/weren't selectable. A `SCOPE_PARTIALLY_READABLE` schema's not-captured
  tables therefore keep the generic "keys not captured" caption (still accurate per table — it
  reflects a real, measured fact about each one — just silent on *why* for that schema). Making
  this precise would mean persisting per-table `can_select` from `_enumerate_relations()` through
  `get_credential_capability`'s stored result, a change to the credential-capability probe itself
  and its stored shape, not to this rendering layer — out of scope for a graph-rendering change and
  flagged here rather than attempted.
- No PR opened, per instructions — this is a pushed branch only; the "Resource-explorer PR/CI
  merge" session batches PRs and was sent the tip.
