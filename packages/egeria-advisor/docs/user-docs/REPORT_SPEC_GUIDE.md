# Report Spec Builder Guide

> **Status (2026-11-09).** pyegeria 6.2.26 removed `analytic_binders` and declares inputs by role. The report-spec
> design ([pipeline model](https://github.com/odpi/egeria-workspaces/blob/main/portal-docs/Internals/report-spec-pipeline-model.md))
> replaces the Advisor-era `Create Report Spec` format (§9.13). This page is revisited with the Advisor work.

Egeria Advisor lets you build, save, and re-run **report specs** — reusable view
definitions that fetch and format Egeria metadata on demand.  Unlike LGCI plans (which run
once), a report spec is persistent: save it once, run it whenever you need fresh data.

---

## Quick start

### 1 — Build a spec by talking to the advisor

Type a description of what you want to report on:

```
Show me all active glossaries with their descriptions and GUID
```

The advisor proposes:
- **Action function** — the pyegeria method that fetches the data (e.g. `GlossaryManager.find_glossaries`)
- **Target type** — the Egeria entity type (e.g. `Glossary`)
- **Column set** — an initial list of columns to display

Confirm with **"yes"**, adjust with natural language (**"add a status column"**, **"change to
find all collections"**), then say **"generate now"** to save it to your catalog.

### 2 — Run a saved spec

From the **Reports** sidebar (left panel), click any spec.  A run modal opens:

| Field | What it does |
|---|---|
| **Search string** | Filters the fetch (leave blank for all, or enter e.g. `finance`) |
| **Output format** | REPORT (default), LIST, DICT — see format guide below |

Click **Execute**.  Results appear in the **Outbox** tab; the spec stays in your catalog
ready to run again.

### 3 — Edit a spec

- **Via chat** (while in a draft session): describe the change and the advisor updates it
- **Via canvas**: open the spec from the Inbox tab → click the canvas icon → drag/reorder
  columns, edit names and keys, add or remove columns.  Changes sync back to the spec file
  automatically.
- **Via the markdown file directly**: the spec is a plain markdown file in
  `~/egeria-reports/inbox/`.  You can edit it in any text editor.

---

## Lifecycle at a glance

```
Draft              → in-progress session in ~/egeria-reports/drafts/
                     (can be paused and resumed)
        ↓ "generate now"
Catalog entry      → ~/egeria-reports/inbox/<spec_id>.md
                     stays here permanently; run it as many times as you like
        ↓ Execute
Result snapshot    → ~/egeria-reports/outbox/<spec_id>_executed_<timestamp>.md
                     each run creates a new timestamped copy; the spec is NOT moved
        ↓ Delete (optional)
Trash              → ~/egeria-reports/trash/  (soft-delete; restorable)
```

**Key point:** running a spec does not consume or move it.  You always have the spec in your
catalog after execution.

---

## Spec parameters

Every spec has three layers of parameters that you can tune:

### Content filters — *what the report is about*

These are part of the spec's identity.  Changing them changes what data is returned.

| Parameter | Default | Description |
|---|---|---|
| `search_string` | `*` | Glob filter on names / qualified names |
| `status_filter` | *(none)* | `ACTIVE`, `DRAFT`, `DEPRECATED`, etc. |

Other filters depend on the action function (e.g. `classification_name` for asset searches).

### Shape defaults — *how data is organized*

These control presentation and traversal depth.  Shown as a collapsible "Report
Configuration" section in future UI releases.

| Parameter | Default | Description |
|---|---|---|
| `sort_field` | *(function default)* | Attribute key to sort results by |
| `sort_order` | `ASC` | `ASC` or `DESC` |
| `graph_query_depth` | `0` | How many relationship hops to follow (0 = shallow) |
| `include_anchors` | *(unset)* | Include anchor elements in traversal |
| `include_lineage` | *(unset)* | Include lineage relationships |

### Performance hints — *operational tuning*

Most users never need to change these.

| Parameter | Default | Description |
|---|---|---|
| `page_size` | `100` | Number of results per page |
| `start_from` | `0` | Offset for pagination |

### Overriding parameters at run time

The run modal accepts a JSON body with a `params` field that overrides any parameter from
any category:

```json
{
  "params": {
    "search_string": "finance",
    "page_size": 50,
    "graph_query_depth": 2
  },
  "output_format": "LIST"
}
```

Merge order: `spec.content_filters → spec.shape_defaults → spec.performance_hints → runtime overrides`

---

## Output formats

The most commonly used formats:

| Format | Best for |
|---|---|
| `REPORT` | Deep readable markdown with all attributes |
| `LIST` | Compact markdown table; great for dashboards |
| `DICT` | Python dict/list — use in notebooks or APIs |
| `JSON` | Raw Egeria response — for advanced users |
| `FORM` | Dr.Egeria-editable markdown form |

The run modal's format dropdown is spec-specific — it's rebuilt from each spec's actually
supported formats (which can also include `TABLE`, `REPORT-GRAPH`, `MD`, `MERMAID`, `HTML`,
and `GRAPH` for specs that support them), defaulting to the most browser-friendly option
available. Non-runnable specs are filtered out of the catalog automatically.

---

## Catalog management

All catalog operations are available from the **Inbox / Outbox / Trash** tabs:

| Action | How |
|---|---|
| View spec markdown | Click the doc name in Inbox tab |
| Edit spec content | PUT `/api/reports/docs/{doc_id}` with updated markdown body |
| Re-run a spec | POST `/api/reports/docs/{doc_id}/execute` |
| Re-run from a result | POST `/api/reports/docs/{result_id}/retry` (strips `_executed_<ts>` suffix) |
| Soft-delete | DELETE `/api/reports/docs/{doc_id}` |
| Restore from trash | POST `/api/reports/docs/{doc_id}/restore-trash` |
| View version history | GET `/api/reports/docs/{doc_id}/versions` |
| Restore a version | POST `/api/reports/docs/{doc_id}/versions/{version_file}/restore` |

---

## Column definition reference

Each column in a spec has these fields:

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Display label in the report header |
| `key` | Yes | pyegeria attribute key (e.g. `display_name`, `guid`, `description`) |
| `format` | No | `false` (default), `true` (format as summary), or `"bulleted-list"` |
| `detail_spec` | No | Name of a FormatSet to use for nested detail (master-detail) |
| `formats` | No | Output format filter: `ALL` (default) or CSV list e.g. `LIST,REPORT` |

Common keys: `display_name`, `qualified_name`, `guid`, `description`, `status`,
`create_time`, `update_time`, `classification_names`, `asset_type`.

## Perspectives & Example Questions (Question Specs)

Report Specs can be annotated with target **Perspectives** (user roles) and **Questions** (example natural language queries). Egeria Advisor uses this information to intelligently guide users to relevant reports.

### 1 — How Guidance Works
When a user asks a question in the chat (e.g. *"what active glossaries do we have?"*), Egeria Advisor performs:
1. **Semantic Search over Question Specs:** It embeds the user prompt and calculates semantic similarity against all example questions defined across report specs.
2. **Perspective Boosting:** If the user has a selected role active (e.g., Developer or Data Steward), reports tagged with that perspective receive a score boost, prioritizing them in the recommendations.

If a match score is high enough, the Advisor suggests or automatically triggers the relevant report.

### 2 — Configuring Perspectives and Questions
You can attach this information to your report specs in three ways:

* **Via the Canvas Editor:** Expand the **Spec Info** section at the top of the Report Canvas. Enter your roles and questions into the **Perspectives** and **Questions** input fields as comma-separated lists:
  * *Perspectives:* `Developer, Data Steward`
  * *Questions:* `what glossaries exist?, show all active glossaries`
* **Via the Markdown Spec File:** In your report spec's `.md` file, add `### Perspectives` and `### Questions` under the `## Create Report Spec` header (see example below).
* **Via Chat:** Ask the advisor to add perspectives or questions during the spec drafting/clarification phase (e.g., *"add Developer to perspectives"* or *"add the question: show my glossaries"*).

---

## Markdown spec format (for manual editing)

```markdown
# My Report Heading

## Create Report Spec
### Target Type
Glossary

### Heading
My Report Heading

### Description
All active glossaries with descriptions.

### Perspectives
Developer, Data Steward

### Questions
what glossaries exist?, show all active glossaries

### Action Function
GlossaryManager.find_glossaries

### Required Params
search_string

### Content Filters
search_string=*

### Performance Hints
page_size=100
start_from=0

## Create Column
### Name
Display Name
### Key
display_name

## Create Column
### Name
Description
### Key
description

## Create Column
### Name
GUID
### Key
guid
### Format
True
```

After editing the file directly, the spec is immediately available to run — no rebuild step is needed.

---

## Tips

- **Start broad, filter later.** Leave `search_string=*` in the spec and override it at run time when you want a focused result.
- **Use `LIST` for browsing, `REPORT` for sharing.** `LIST` gives you a compact table; `REPORT` gives a rich per-element breakdown readable by non-technical stakeholders.
- **Name specs by purpose, not by filter.** `active_glossaries` is a bad name; `glossary_catalog` is better — it can run with any `search_string` and `status_filter`.
- **Version history is automatic.** Every save (including canvas edits and chat-driven changes) writes a version snapshot in `~/egeria-reports/versions/`.
- **Drafts auto-save.** Close the window during Q&A and resume from **Drafts** in the sidebar.
