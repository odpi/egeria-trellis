# Parity slice G1: Egeria on a repository (implemented)

Built from `BRIEF-PARITY-G1-G3-TO-ALPHA.md` §G1 and `evidence/PARITY-INVENTORY-2026-10-07.md`. Branch `re/parity-g1-egeria-repository`.
The owner confirmed on 2026-10-07 ("yes, dispatch the three parity builders"); merge order G1, G2, G3.

## Where it lives (a correction to the brief)

The brief says the Publish stage "already exists for databases". It does not: `STAGES` in `next/app.js` has no publish stage, and the
database Catalog commit lives in Curate's scope tree. I did not add a stage (that changes the nav, the URL and a dozen pinned tests). The
home is a **fourth band of the Curate pane**, `[data-curate-band="publish"]`, after the kind's own work and before "What people say",
the same band on every kind: `next/stages/publish.js` (`renderPublishBand`). If the owner wants a separate stage, the band moves whole.

## The table (every id)

| ID | Result | What and where |
|---|---|---|
| PI-001 publish survey | **DONE** (repository) | `Publish to Egeria →`, then `Publish again`. `POST /api/egeria/{slug}/publish-report` (`routes/egeria.py publish_report` → `repo_publish.publish_report`): whole survey (`steps=None`), no zone field, zones only by the publisher's own rule (`EXPLORER_PUBLISH_ZONES` on promotion). Row words from proof rows: `published · read back <when>`, `sent · waiting for Egeria`, `not published · <Egeria's first sentence>` (full sentence under "details"). A 409 on the report name reuses the report in Egeria (`EgeriaPublisher._create_survey_report` sets `report_reused`), never a second one; a duplicate whose report cannot be read fails loud. A second press while one runs is ignored in the UI and answered 409 by the server. Needs a signed-in person (401 otherwise). |
| PI-002 Egeria tab | **DONE** | The band's status line: `in Egeria` / `not in Egeria`, the asset GUID (copyable), the last report row, the project. `GET /api/egeria/{slug}/publish-state` reads RE's own records only (no Egeria contact). For a database or file system the same line reads from the resource's summary row (`is_published`, `egeria_asset_guid`), with the sentence that a database reaches Egeria through Catalog. The resource header's publish line is unchanged. |
| PI-003 Egeria survey reports | **DONE** | One component, `mountEgeriaReports(el, { entityType, slug, inEgeria, onAsk })` in `publish.js`: lists the SurveyReports on the asset whoever ran them, Refresh, expand annotations by report (read on expand), copyable GUID, `Ask about this →`. Reads `/api/egeria/{slug}/egeria-surveys` (repo), `/api/databases/{slug}/egeria-surveys`, `/api/filesystems/{slug}/egeria-surveys` and the matching `.../{guid}/annotations`. Not in Egeria says so and never asks; a failed read says "Egeria could not be read: …", never "no reports". |
| PI-004 catalog file types | **DONE, as a "File types" section (routes differ)** | Route comparison (read first): Classic's `catalogSelected` posts `/api/egeria/{slug}/catalog-elements`, which creates one `DataSet` per file-type label (`DataSet::{slug}::{label}`) and links it with `CapabilityAssetUse`. The sub-resource catalog control (PI-066, `analysis.js`) posts `/api/projects/{slug}/sub-resources/catalog`, which publishes `FileFolder`/`DataFile` assets by locator. Different Egeria elements, so it is NOT folded. Built: a `File types` section in the band, preview first (`GET .../file-types`, nothing sent), then `Catalog →` (`POST .../file-types/commit`). Each type is looked up by qualified name (adopted, never a second element), created when missing, **read back by GUID**, and only then does `file_type_read_back` get written; the link to the asset is its own step with its own row (`cataloged, not linked to the repository · <sentence>` when it fails, which Classic's own docstring says it is expected to). Old route untouched. |
| PI-005 reset cached GUIDs | **DONE** | `Forget Egeria links…` opens a confirmation that says exactly: "Egeria is unchanged; Resource Explorer forgets its cached GUIDs and survey history for this resource and re-reads them on the next publish." `POST .../forget-links` calls `registry.clear_egeria_registration` (RE's registry only), then appends a `links_forgotten` proof row with the person. It constructs no Egeria client and has no archive, delete or remove call (a test walks the module's AST for those names). |
| PI-006 project-context gate | **DONE** | The control is enabled; the first press with no answer gets 428 and shows "no Egeria project context · bind this investigation to a project, or publish without one" with two choices: `Bind this investigation to a project` (opens the Investigation stage) and `Publish without one` (presses again with `without_project`, which records the explicit "declined" answer). A resource in scope of an investigation bound to an Egeria project proceeds, the inherited answer is written (existing rule), and the row names the project. The Classic route now calls the same `repo_publish.resolve_project_context`, so the two cannot drift. No session-wide badge (PI-110 stays retired). |
| PI-007 bind investigation | **DONE earlier, untouched** | `stages/investigation.js` inv-bind. Read by the gate through `registry.inherited_egeria_project_context`. |
| PI-008 ask about this | **DONE** | `Ask about this →` on each report row prefills `#ask-input` in the chat rail with the report's name, GUID and survey time and sends nothing (`askAboutText`). |
| PI-009 scoped publish of steps | **retired with Classic** | Publishing is whole-report; components reach Egeria through the Curate commit. The new route accepts no `steps`. Classic's route is unchanged. |
| PI-042 database Egeria reports | **DONE (read side)** | The same component, mounted on a database's Curate band against the database route. |
| PI-046 after-run Egeria link | **component ready; link is G3's** | `mountEgeriaReports(...).expand(guid)` opens one report's annotations. The runs dialog's link to it is G3's wiring, per the brief. |
| PI-096 file-system reports (read side) | **DONE (read side)** | The same component, mounted on a file system's Curate band against the file-system route. |

## How a status word is derived

All rows go in `catalogue_commit_proofs` (no DDL), `node_kind = repo_report` or `file_type`, keyed by the resource slug. The success row is
written only by GUID after a read of that GUID: `report_published` after `get_survey_reports_by_guid(asset)` lists the report's GUID;
`file_type_read_back` after `get_asset_by_guid(dataset)`. A read that does not show the GUID writes `report_sent` (never published), a refusal
writes `read_failed` with Egeria's full sentence in `detail.error`. `publish-state` reads the newest of those rows; nothing is cached in the
browser, and every action re-reads the state before drawing. Forgetting is a local act and a row of its own.
Caveat: the table is keyed on the slug the registry normalises (`-` to `_`), so a repository and a database sharing a slug would share rows;
`node_kind` keeps the reads apart and the database delete path (`DELETE ... WHERE database_slug`) would also drop a same-slug repository's rows.
No such pair exists today.

## Using the Egeria reports component (for G3)

```js
import { mountEgeriaReports } from '/static/next/stages/publish.js';
const reports = mountEgeriaReports(hostEl, { entityType: 'database', slug, inEgeria: true });
await reports.expand(reportGuid);   // open one report's annotations (PI-046's link)
reports.refresh();
```
`entityType` is the API kind (`repo`, `database`, `filesystem`) and is never defaulted. `onAsk(text) => boolean` replaces the chat prefill.

## Tests

* `tests/test_repo_publish_g1.py` (31): publish whole, read-back words, refusal sentence stored in full, 409 reuse (unit, on the publisher), no
  zone field, 401, 409 on a second press, the gate (428, without-one, inherited project), forget (RE only, rows kept, no Egeria client), file types.
* `frontend-build/test-harness/publish-egeria-g1.test.mjs` (19): the band on a repository, a database and a file system; known-negatives (a 200
  with nothing recorded never says published; sent is not published); pending and second-press; gate; forget; reports; ask; file types.
* `curate-bands.test.mjs`: the band order assertion now includes `publish` (the one existing test touched).

## Open questions

1. **A Publish stage?** The brief assumes one exists; it does not. Built as a Curate band (above). Owner to confirm or ask for a stage.
2. **The Classic file-type route's link** (`CapabilityAssetUse` from a plain Asset) is documented as expected to fail against a live Egeria;
   the new section reports that honestly per item instead of hiding it, but nobody has run it live. The gate on egeria_git will show it.
3. **A database/file system's `Forget links`** is not offered: the route clears a repository's registration only (`clear_egeria_registration`
   is project-scoped). Not in G1's rows.
4. **Report rows for a repository published by the old Classic route** have no proof row here, so the row says "no publish recorded here"
   while the status line (from the cached asset GUID) says "in Egeria". Honest, but it will read oddly on egeria_git until the first publish.

## Not run

The whole pytest suite, the live page on 8813, headless Chrome, anything against a real Egeria. Egeria behaviour is proved on fakes only.
