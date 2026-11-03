# Wording: Save / Catalog / Publish (implemented)

Built from `REPLY-DESIGNER-SAVE-AND-PUBLISH-VERBS.md` (its two tables, verbatim) and the owner's decision of 2026-10-06 that
the reserved Egeria verb is **Catalog**, US spelling, on controls and in result lines.

## What changed

| Where | Before | Now |
|---|---|---|
| Curate › Findable › tags | `add` | **Save**; the line after reads `saved · <who> · <when> · tag “x” is on the list`, from the re-read row |
| Curate › Findable › group | line `group is now X` | a choice (the select) stays; the line reads `saved · <who> · <when> · group is now X`, from the route's own answer |
| Curate › Ratings | Submit | **Save rating**; `saved · who · when · it is in the list above` |
| Journal (Curate, Disposition) | Write | **Save entry** + `saved entries are permanent: no edit, no delete`; result `saved · who · when` from the re-read entry |
| Context judgements / observations / human questions (also the Questions tab's answer editor) | `save` | **Save** |
| Enrichment › doc sources | `add + probe` | **Save and probe** (a probe is a network read, not an Egeria send) |
| Scope › new-since link | `declare the scope again` | **Start a new baseline**; result `saved · who · when · new baseline: nothing is new since now` |
| Investigation › Egeria | `Create in Egeria →` / `Sync now` | **Publish to Egeria →** / **Publish again** (result `published · read back <when>` only when the promote answer carries a confirmed classification, otherwise `sent · waiting for Egeria`) |
| Work list | `publish to Egeria` / `re-publish` | **Publish to Egeria** / **Publish again** (result `published · read back <when>` only when the re-read work list holds the Egeria guid, else `sent · waiting for Egeria`) |
| Native-survey rows | `run →` / `re-run →` | **Run in Egeria →** / **Run again in Egeria →** (the local `Run` on analysis rows is unchanged) |
| Repository Curate | `Catalogue →` | **Catalog →**; every UK `catalogue`/`catalogued`/`cataloguing` on controls, rows, manifest, steps, header, preview, blockers and routes' messages now reads `catalog`/`cataloged`/`cataloging` |
| Group route | recorded no author | records the signed-in person (`group_changes`, additive), answers `saved_by`/`saved_at`, 401 signed out |

**An Egeria soft delete reads "deleted from Egeria"** (architect, 2026-10-06): `removed` is reserved for "Remove from Resource Explorer",
which only touches RE's record. Preview `<schema>: nothing hangs off it · will be deleted from Egeria` (the `with its <n> tables` and
` · delete · nothing depends on it` parts stay), button `deletes N from Egeria`, row `deleted from Egeria · <time>`, step
`N of N deleted from Egeria` / `N archived in Egeria` / `2 of 2: 1 deleted from Egeria, 1 archived in Egeria`, `nothing to remove · already
deleted from Egeria` / `already archived in Egeria`, header count `N deleted from Egeria`. The state and proof-kind names `removed` /
`P_REMOVED` stay as identifiers.

## Spelling decisions (every one)

* Rewritten (prose a person reads): all of `static/next/*.js`, `static/index.html` text, and the non-docstring string literals of the Python
  modules that reach a page (manifest, row words, step words, header, preview, blockers, route `detail` messages, activity summaries,
  survey-definition prose, facts answers, scope depth help). The commit-path sentences about WHAT RE DOES say catalog / cataloged.
* **Kept, on purpose** (identifiers, not words): function and import names (`getCatalogueScope`, `renderCatalogueScope`, ...), route paths
  (`/api/catalogue-scope`), file names, data attributes (`data-scope-catalogue-all`, `data-catalogue-depth`), the choice VALUE `'catalogue'`
  sent to the API, state/proof/glyph keys (`catalogued`, `catalogue_failed`, `schemas_catalogue`, `publish_uncatalogued`), the registry
  column default `kind = 'catalogue'`, the `egeria_catalogue_state` key.
* **Kept, Egeria's own term for the connector:** `cataloguer` (the JDBC cataloguer, `JDBCDatabaseCataloguer`, "Egeria's cataloguer creates
  tables and columns", "refresh Egeria's cataloguer now", "refresh the cataloguer"). The pattern deliberately matches only words ending
  `-e/-ed/-es/-ing`, so `cataloguer` never matches.
* **Kept, stored text that is a KEY:** the question catalog's question "Has this resource already been catalogued in Egeria, and when?"
  (`question_catalog.yaml`, `facts.py`'s lookup tables, `docs/dr-egeria/resource_questions.csv`): stored answers are keyed on the text, so
  rewording it orphans them. **Follow-up:** a data migration, then reword. The allow-list in `test_wording_save_catalog_publish.py` names it.
* **Prose in design notes keeps its spelling** until next edited (owner's rule). `docs/dr-egeria/survey-definitions/*.md` were regenerated
  because one description string changed ("Cataloging in layers").

## The test that fails if the UK spelling comes back

`tests/test_wording_save_catalog_publish.py`: `test_no_python_string_a_person_reads_says_catalogue` and `test_no_page_script_or_html_says_catalogue`
scan every non-docstring Python string with whitespace and every page script and the HTML (comments removed) for `catalogue/catalogued/
cataloguing`; the documented allow-lists are `PY_ALLOW` (the two identity strings above) and `JS_ALLOW` (markup identifiers). The render
harness (`wording-save-catalog-publish.test.mjs`) pins the Save / Save rating / Save entry controls, the permanence sentence, the `saved ·
who · when` lines, the group line, the repository `Catalog →`, Publish / Publish again, and a source scan for bare `save`/`Submit`/`Write`.

## Migration (additive, none destructive)

One new table, `group_changes` (entity_type, entity_slug, group_slug, author, changed_at; index on entity), created with `CREATE TABLE IF NOT
EXISTS`. Nothing altered.

## Words in the designer's tables that could not be mapped

None of the table rows was unmappable. Judgement calls and gaps:

* "**A control that does both says both, in order: Save, then catalogue**": no such control exists on main, so nothing was built.
* **Report records › `record`, `save as report`, `save as work list…`**: unchanged, as the table says.
* **Scope tree choices** (catalog / leave out / confirm): unchanged choices; only the spelling moved (`catalog`).
* The designer's table does not name these, so they were NOT changed (listed, not invented): Investigation › `Add` (a member), `Relink
  members`, `Unbind`, `Bind existing project…`, `Reclassify…`; work list `promote N →`, `export CSV`, `add to investigation`; analysis
  rows' local `run →` / `re-run →`; Automate's schedule controls. The doc-source `re-check`/`remove` stay as choices/destructive verbs.
* "result lines: `cataloged · read back when` / `published · read back when` ONLY after the read-back": the commit already derives every
  state word from proof rows; the Investigation and work-list publish lines were changed to this rule above. Context's `answered by …` /
  `set by …` provenance lines (`personRowLineHtml`) already read who and when and were left as they are.

## Dan's check afterwards

1. Open Curate on **amundsen** (a repository) and on **coco_pharma** (a database), signed in.
2. On each page, every send or save control reads one of **Save**, **Save rating**, **Save entry**, **Save and probe** (saves), **Catalog →** /
   **Catalog** (the Egeria commit), or **Publish** (Publish to Egeria / Publish again): no `add`, `Submit`, `Write`, `Create in Egeria`,
   `Sync now`, `Catalogue`.
3. The journal shows `saved entries are permanent: no edit, no delete` beside **Save entry**; saving one adds a line `saved · <you> · <when>`.
4. No page shows the UK spelling anywhere (search the page for "catalogu"; only `cataloguer`, Egeria's connector, may remain).
5. On coco_pharma, a leave-out of a schema with nothing of a steward's on it previews `will be deleted from Egeria`, never `removed`.
