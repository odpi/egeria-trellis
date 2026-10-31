# DESIGN — Local dashboards in Resource Explorer from declared analytics and report specs (2026-10-28)

> Superseded in part by Q2(b) (#956) and pyegeria 6.2.18 (#959): analytics register through `pyegeria.analytics`, report specs through `pyegeria.report_specs`; `PYEGERIA_REPORT_SPEC_MODULES` is deprecated in 6.2.18 and removed in 6.2.19; dotted function paths are refused in 6.2.19 (pyegeria side: [ISSUE-173](https://github.com/odpi/egeria-python/blob/main/PYEGERIA_ISSUES.md) in egeria-python's PYEGERIA_ISSUES.md, [odpi/egeria-python#523](https://github.com/odpi/egeria-python/pull/523)).

Owner's ask (2026-10-28, to the architect session): "design the ability for
users to add their own local dashboards with new or existing analytics and
report specs to RE". This is the dashboard-definition design the
declared-extensibility note held for its own note (§6, owner's answer 4,
2026-10-21: "dashboards: displayed results are local, measures published
separately; the dashboard DEFINITION should follow the Portal approach,
Dr.Egeria defines structure incl. analysis and method; worth investing").

## 0. The headline

The Portal already has the whole mechanism, shipped 2026-07-29/30: a
**Report** (Dr.Egeria `Create Report`, a real Egeria asset naming a Report
Spec plus default parameters), a **Dashboard Sheet** (Dr.Egeria `Create
Dashboard Sheet` / `Link Report to Dashboard Sheet` / `Add Text on Dashboard
Sheet`, a pyegeria-local JSON store rendered by `local-dashboards.html`), an
**analytic function registry** behind report specs, and chart output formats
(SERIES, BAR, PIE as Vega-Lite). RE should add nothing parallel. What RE
adds is one thing: **every declared analysis is a report spec**, generated
from its declaration, so a person authors a dashboard in Dr.Egeria that
mixes RE's analyses (built-in or their own library steps) with the Egeria
report specs that already exist, and RE renders that sheet on a Dashboards
tab with RE's words for provenance. The declaration the owner approved on
2026-10-21 is already most of a report spec; this note is the adapter and
the rules, not a new model.

## 1. What exists, read from source (2026-10-28)

- **Report spec** (`pyegeria/view/_output_format_models.py`): `FormatSet`
  (target_type, heading, description, family, aliases, `formats`, `action`
  or `analytic_function`, `question_spec`) with `Format` (types, columns)
  and `Column` (name, key, format, detail_spec). 150 generated specs plus
  10 analytic demo specs; a CONFIG tier (`PYEGERIA_REPORT_SPEC_MODULES`)
  loads extra spec modules with no registration call.
- **Analytic functions** (`pyegeria/view/analytic_registry.py`):
  `AnalyticFunctionSpec` with params, `generic` (what it counts is a
  parameter) or fixed, `binding_note`; `format_set_executor.exec_report_spec`
  runs a spec through either the client-method path or the analytic path,
  and SERIES/BAR/PIE wrap the result as Vega-Lite. trellis-analytics'
  StepManifest already subclasses this spec class (the registry's own
  comment says so).
- **Report** (Dr.Egeria `Create Report`, qn prefix `DeployedReport`): an
  Egeria asset naming a Report Spec, an output format and fixed parameter
  values. This is what made a placement parameterisable.
- **Dashboard Sheet** (`pyegeria/view/_output_dashboard_sheet_models.py`):
  `DashboardSheet` (name, heading, description, aliases, family,
  placements) with `Placement` (ref, span, emphasis kpi|panel, content for
  text, perspectives, detail_spec). Stored in `~/.pyegeria/dashboard_sheets.json`
  (bind-mounted in the quickstart), rendered by the Portal's
  `local-dashboards.html` / `local_dashboards_handler.py`, authored per
  `LOCAL_DASHBOARDS_TUTORIAL.md` (pick a spec, Create Report, Create
  Dashboard Sheet, Link Report to Dashboard Sheet).
- **The Overview dashboard** is a Perspective's Questions each answered by a
  report spec rendered as a drill-able tile, with a container/placement
  model (`overview_containers.py`) that the Sheet track superseded for
  user-authored dashboards. Its own re-assessment (OVERVIEW_REPORTING_MODEL
  §13) lists what Sheets still lack: per-viewer role filtering (NEXT-19), a
  detail drawer per tile (NEXT-21), caching (NEXT-22).
- **RE's side**: the declaration (`trellis_analytics/declaration.py`: eight
  result kinds, measures with unit and as-of, absence sentences, extents,
  five confidence forms, optional Egeria type); the fact layer and
  `/api/analyses/facts/{slug}` and `/facts/{slug}/answer` (already judged:
  each fact carries a state, never a bare value); the Curate publish table
  reading the same declaration; the Understanding graph renderer keyed by
  result kind (the other seven kinds are "said, never drawn", slice 1's
  remaining half); library steps discovered through the `trellis.analytics`
  entry point. RE has no dashboard surface today; "local dashboards" in
  RE's notes means the Portal's.
- **Rulings that bind this design**: results a dashboard shows are local
  and say so ("local · not published" / "published · read back <time>",
  DESIGN-OPTIONAL-ANALYSES §e); absence is never rendered as zero
  (find-absence-as-answer); every count names what it counts; state as a
  visual cue, not a sentence; US spelling on screen; a declaration drives
  rendering, chat and publish alike, and now the tile too.

## 2. The design in one sentence

A dashboard is a Dashboard Sheet whose Reports name report specs, and RE
contributes a report spec per declared analysis, generated from the
declaration and bound to a scope parameter, so the same declaration that
draws Understanding, answers Chat and fills the publish table also fills a
tile, in RE or in the Portal, with RE's provenance words.

## 3. The adapter: declaration → report spec

One generator, in trellis-analytics (not RE), run at load like the
catalog generator:

| Declaration | Report spec |
|---|---|
| analysis id, name, description | `FormatSet.target_type = "RE:<kind>:<analysis_id>"`, heading, description, family "Resource Explorer · <resource kind>" |
| `questions` | `question_spec` (the spec answers these catalog questions, by id) |
| result kind | default output format: measure → KPI; series → SERIES; distribution → BAR; list, table, proposal, finding → TABLE; graph → the graph renderer's payload (no Vega-Lite; the Portal shows it as a table until it has a graph renderer) |
| measures (name, unit, as-of, confidence) | `Column` per measure with `key` = the fact-layer path; unit and confidence form carried as column metadata the tile reads |
| absence sentences | carried on the spec; the tile shows the sentence, never an empty chart |
| extents | the drill-down: `detail_spec` per extent, a generated detail spec over the extent's rows |
| confidence | tile badge: form and basis |
| Egeria annotation type | the "published as" words when a read-back exists |

The spec's `analytic_function` is one RE function, `re_declared_result(
analysis_id, scope_kind, scope_ref, as_of=None)`, registered in the
analytic registry as `generic=True` (what it reads is a parameter), which
returns the fact-layer envelope: value or rows, state, as-of, confidence,
absence sentence, run id, published state. Nothing in the function is
per-analysis; the declaration says how to read the rows. A library step a
person adds gets its spec the moment its manifest is discovered, with no
RE code change, which is the extensibility gate's claim extended to tiles.

## 4. Scope: what a tile is about

A Report carries fixed parameters. For RE specs the parameters are:
`scope_kind` ∈ {resource, investigation, work_list, all}, `scope_ref`
(slug, investigation id, list slug), `as_of` (a time, or "latest"). So:

- a **resource tile** shows one analysis on one resource (the Understanding
  headline, as a tile);
- an **investigation tile** shows one analysis across the scope's members,
  one row per member, the list-scoped compile's extents (the work-lists
  note §2, winnowing columns, is the same read);
- a **work-list tile** likewise over the list's members;
- an **all tile** is the fleet view: one analysis over every registered
  resource of a kind, with the count of resources that have no answer
  stated ("12 of 40 databases measured; 28 never run"), never a number over
  the measured only.

The same analysis can appear on a sheet three times with three scopes.
Parameters are fixed in the Report (Dr.Egeria), not chosen on the tile;
a person who wants a different scope creates another Report. That is the
Portal's rule and it keeps a dashboard a definition rather than a query.

## 5. Where the sheet is authored, stored and shown

- **Authored** in Dr.Egeria, as the Portal's are: `Create Report` naming
  an RE spec and its scope parameters; `Create Dashboard Sheet`; `Link
  Report to Dashboard Sheet` with span and emphasis; `Add Text on Dashboard
  Sheet` for narrative. A sheet mixes RE Reports with any existing Egeria
  report spec (asset counts, certifications, the Overview's KPIs), which is
  the "existing analytics and report specs" half of the ask with no new
  mechanism.
- **Stored** where the Portal stores it: the pyegeria-local sheet store,
  one file, shared between the Portal and RE by the same bind mount.
  "Local" is the owner's word and the Portal's choice; moving the store
  into Egeria (OVERVIEW_REPORTING_MODEL §8's endgame) is a pyegeria
  decision this note does not make. RE reads the store through pyegeria's
  sheet models, never by parsing the file itself. Git holds the Dr.Egeria
  document (working assumption A5): the sheet is reproducible from the
  document, and the store is a cache of it.
- **Shown** in two places from one definition: the Portal's
  `local-dashboards.html` (RE tiles render through the generic spec
  executor, as any analytic spec does) and a **Dashboards** tab in RE's
  /next, which lists the sheets whose Reports name at least one RE spec,
  renders each tile through RE's own renderers by result kind (the graph
  renderer today; the seven others as slice 1's remaining half), and
  carries RE's provenance words on every tile: the state cue (measured /
  never run / not permitted / not applicable / planned), "as of <time>",
  the confidence badge, and "local · not published" or "published · read
  back <time>". A tile's drill-down opens the resource's Understanding at
  that analysis, or the list bench for a scoped tile.
- **Perspectives** on a placement (the Sheet model has them) filter which
  tiles a viewer sees by their perspective, the Overview's per-role view
  (NEXT-19) done on the sheet rather than in code.

## 6. Rules the tile obeys

1. **A tile is the declaration, rendered.** No per-analysis tile code. A
   kind with no renderer yet shows the headline sentence and the measures as
   text with their units, labelled "no chart for this kind yet"; it never
   shows an empty frame.
2. **Absence is a sentence.** A never-run analysis shows its declared
   absence sentence on the tile with the ◌ cue, and a scoped tile counts
   the unmeasured members in words. The KPI format may not render zero for
   an absent value.
3. **Every number carries its as-of and its basis.** The tile's hover is
   the fact's envelope; the number alone is never the whole tile.
4. **Local by default, published by choice.** The tile never triggers a
   publish. The publish word on a tile is the publish table's proof row for
   that measure, nothing else.
5. **Cost before fetch.** An RE spec is zero-fetch by construction: it
   reads stored results. A tile may offer "Run <analysis>" for a never-run
   resource, which is the existing priced press, not a tile action of its
   own. A fleet tile over N resources reads N fact rows and says so in its
   cost line.
6. **Identity.** Reads run as the viewer; a sheet is readable by anyone
   signed in, as the Portal's are; the Report asset's creator is the author.

## 7. What is built where

| Slice | Where | What |
|---|---|---|
| D1 | trellis-analytics | the declaration → report spec generator and `re_declared_result` registered as a generic analytic function; the CONFIG-tier module so pyegeria, the Portal and RE all see RE's specs with no registration call; tests: one spec per declared analysis, the four library steps included, a hand-added step appears without an RE change |
| D2 | RE /next | the Dashboards tab reading the sheet store through pyegeria's models; tile rendering by result kind through RE's renderers with the provenance words and cues of §6; drill-down to Understanding or the bench |
| D3 | Portal (workspaces) | RE tiles rendering through the generic executor in `local-dashboards.html`, with RE's provenance fields shown (a small change in the Portal's tile template to read state, as-of and confidence from the envelope) |
| D4 | docs | the tutorial extended with an RE example: one sheet with a resource tile, a work-list tile and an Overview KPI side by side |

D1 before D2 and D3; D2 and D3 independent. The gate (§8) needs D1 and D2.
The seven missing renderers are not in this design's scope but every tile
of those kinds degrades to rule 1's text form until they land.

## 8. The gate, by use

A person adds a library step (one of the four file-system steps serves)
and, with no RE code change: writes a Dr.Egeria document with `Create
Report` naming the step's generated spec with scope = one registered file
system, `Create Dashboard Sheet`, `Link Report to Dashboard Sheet`, plus
one existing Egeria report spec (asset count by type) as a second Report;
applies it; opens RE's Dashboards tab and sees both tiles, the RE tile
with its value, unit, as-of, confidence badge and "local · not published";
opens the Portal's local dashboards and sees the same sheet; changes the
Report's scope to a work list and sees one row per member with the
unmeasured count in words; and on a never-walked file system sees the
declared absence sentence, not a zero.

## 9. Open, for the owner

1. The sheet store stays pyegeria-local (this note's assumption) or RE
   asks pyegeria to move it into Egeria first.
2. Whether a fleet ("all") scope is wanted in the first slice or only
   resource and list scopes.
3. Whether a tile may offer the priced "Run" press (rule 5) or stays
   strictly read-only.
4. The Dashboards tab's place on the rail: its own tab, or under
   Understanding as "Boards".
