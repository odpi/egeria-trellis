# BRIEF — Classic→Next parity, groups G1–G3, to the Alpha demo (2026-10-07)

Design session brief for the coordinator and three builders. Source of rows:
`evidence/PARITY-INVENTORY-2026-10-07.md` (read against main 03712d7a). Owner
decisions of 2026-10-06: these three groups reach rough parity and the demo
(Alpha: no path that writes beyond intent, every status word from a proof row,
not polish); the generic Curate controls belong to G2; no file-system work
and no new kind until G1–G3 are complete.

Rules that apply to every slice, not repeated below: the verbs ruling (Save
for RE's record, Catalog and Publish for Egeria; "Publish to Egeria →", "Publish
again", "Run in Egeria →"); the cue vocabulary (two lanes, fill = chosen,
lowered = not included, glyphs only in the Egeria lane with their word, the
sentence one gesture away, a pressed control looks pending and ignores a
second press); proof rows by GUID written after a read of that GUID
(PR #514), Egeria's full sentence stored; the ISSUE-117 block stays on, so no
slice archives or deletes in Egeria; status words derive from proof rows;
US spelling "catalog"; "file system" never "file share"; every builder in its
own worktree with `uv sync` and the import-path line; red runs first; the
registry guard (tests never touch the shared registry or live Egeria). The
owner gates each slice by use on 8813, on egeria_git (repository) and
coco_pharma (database).

Three states in the inventory: DONE rows are not touched; PARTIAL rows name
the missing part only; MISSING rows are built or, where this brief says so,
retired with Classic (recorded in the implemented note's table with the
reason, so the parity table can say "retired" rather than "absent").

## G1 — Egeria on a repository (one slice)

Classic has an "Egeria" tab per repository; Next has a publish-state line in
the resource header and nothing else. The home in Next is the **Publish
stage**, which already exists for databases: one component for every kind.

| Row | Build |
|---|---|
| PI-001 publish survey | "Publish to Egeria →" on the Publish stage of a repository; publishes RE's survey report **whole** (the owner's decision for databases applies to every kind); zones only from `EXPLORER_PUBLISH_ZONES`, never a per-press choice; then "Publish again". Row words: "sent · waiting for Egeria", "published · read back <when>", "not published · <Egeria's sentence>". Reuse an existing report by name on a 409 (the SurveyReport rule from 2026-10-06), never a second one. |
| PI-002 Egeria tab | The Publish stage shows: in Egeria or not, the asset GUID (copyable), and the list below. The header line stays. |
| PI-003 Egeria survey reports | One "Egeria reports" component on the Publish stage, every kind: list of SurveyReports on the asset (not only runs RE launched), refresh, expand annotations by report; this component also closes PI-042 (database) and PI-096's read side (file system) and the Egeria link in PI-046. |
| PI-004 catalog selected file types | Fold into the existing sub-resource catalog control (PI-066, `stages/analysis.js`) if it creates the same Egeria elements through the same route; if the routes differ, a "File types" section on the repository's Curate stage with the same preview-then-commit and the same proof rows as the database commit. The builder reads both routes first and records which. |
| PI-005 reset cached GUIDs | A control on the Publish stage, "Forget Egeria links…", with a confirmation that says what survives: "Egeria is unchanged; Resource Explorer forgets its cached GUIDs and survey history for this resource and re-reads them on the next publish." Roll-forward language; never "reset Egeria". |
| PI-006 project-context gate | The publish control reads the investigation binding (PI-007, built): when the resource is in scope of an investigation bound to an Egeria project, publish proceeds and the row names the project; when not, the control is enabled but the first press shows "no Egeria project context · bind this investigation to a project, or publish without one" with the two choices; the 428 from the route maps to that sentence. No session-wide badge (PI-110 stays retired). |
| PI-008 ask about this | "Ask about this →" on the Publish stage's report rows prefills the chat rail with the report's identity; small, included. |
| PI-009 scoped publish of steps | **Retired with Classic.** Publishing is whole-report; components reach Egeria through the Curate commit. |

Gate (egeria_git, by use): publish once and read "published · read back"; the
GUID shown equals a read-only lookup; the reports list shows the report with
its annotations; publish again reuses, no second report; forget links, then
publish again re-reads the same GUID; a repository with no project binding
shows the two choices.

## G2 — database registration, credentials, generic Curate (one slice, two parts)

**Part A, registration and credentials.** Next adds databases only through a
server's Discover or a CSV. The rule from the discovery reply holds:
preview-then-apply is the only path that creates a resource, so direct
registration is a test-then-register form.

| Row | Build |
|---|---|
| PI-015 register one database | In "+ Find databases", a third way beside Discover and CSV: "Register one database…" (slug, display name, host, port, database, group, credential, optional Egeria fields). "Test connection" runs first and must pass (the capability probe's sentence shown); "Register" then writes the record and the credential through the existing register route. Row after: "saved · you · just now". |
| PI-016 credential override for a run | The run dialog (`planSurveyRun`) gains "Use different credentials for this run" (user, password, "remember for this session"); session memory only, never written to the registry; the run's step rows say "ran as <user> (this run)". The password field is never echoed in any row, log or activity entry. |
| PI-021 change a stored credential | On the database's detail (Admin → servers, and the resource header's Edit): "Change credentials…" with test-before-save (the CLI's credential-safety rules of 2026-10-05: prompt, connect test, activity row, `credential_changed_at`), then the omsecrets re-projection read-back ("in registry · in .omsecrets · in sync" as the drift check prints). |
| PI-018 hybrid "try Egeria first" | An option in the run dialog, shown only when the database is cataloged in Egeria (the publish-state line proves it), with the existing confirm sentence; disabled otherwise with "not cataloged in Egeria · catalog it on Curate first". The run's rows say which source answered each step. |
| PI-019 catalog now, then retry | On a run that failed because the database is not cataloged, the run row offers "Catalog now →" (routes to Curate scope, the existing Catalog button) and, once the publish-state line reads cataloged, "Retry →" on the same row. No automatic retry. |
| PI-020 failing credential | The launch note shows the failing step's sentence at once (cue rule: a failed step shows its first sentence unasked), not only on the reloaded row. |
| PI-014 server detail | Add the Egeria fields and the per-database surveyed state to the saved-server view; read only. |
| PI-017 whole-database survey modal | **Retired with Classic.** A database survey is a Survey Definition run (PI-043, done). |

**Part B, generic Curate controls, every kind.** The inventory shows tags,
rating and category, notes list and delete, and group assignment DONE in
`curate-bands.js`. Two rows remain:

| Row | Build |
|---|---|
| PI-027 notes: add | **Done by design.** New notes are journal entries ("Save entry", permanent, signed; the verbs ruling). The Classic notes list stays read-and-delete for old unsigned notes; its heading says "Notes from the current UI · read only here". Recorded, not built. |
| PI-029 per-row group assign in the sidebar | **Retired with Classic** (the resource-controls ruling: controls live on the resource, not on sidebar rows). |

Gate (coco_pharma and one new scratch database on 5432, by use): register
one database by the form with a failing test first (sentence shown, nothing
saved) then a passing one; run a survey with an override credential and read
"ran as <user> (this run)"; change the stored credential and read the drift
check in sync; a run on an uncataloged database fails with "Catalog now →";
the hybrid option is disabled with its reason on an uncataloged database.

## G3 — database report and Understanding (one slice)

Charts, trend and the changes banner are DONE. What remains is the report's
tabular side and the survey history.

| Row | Build |
|---|---|
| PI-037 survey history with invalid rows | On Understanding, under the trend chart: a "Survey history" table (date, schemas, tables, columns, source), invalid rows lowered with their reason and when marked, a "show invalid" toggle that remembers its state per browser (localStorage is fine for this). Reads `/surveys?include_invalid=true`. |
| PI-030 source badge | The header's "surveyed N ago" gains the source word (local survey, Egeria, hybrid) from the same row the history table reads. |
| PI-031 three-number card | One line above the schema inventory: "N schemas · N tables · N columns · from <source> <when>", counts labelled with what they count. |
| PI-032 column details | Default value and foreign-key target columns in the schema inventory's column rows. |
| PI-036 top tables ranked | Under the Tables chart: the ranked table (rows, size, last analysed, pending changes) from the same data; "pending changes" reads from the diff the banner already uses, or says "no diff yet". |
| PI-040 views and lineage | A "Views" section on Understanding: the view list with complexity, SQL (one gesture away), dependencies; the per-view flowchart through the existing diagram route (`/api/diagrams/mermaid`). The relation to tables is a **dependency**, never "lineage" in the words on screen (owner, 2026-10-05). |
| PI-041 data-class rules | A read-only "Rules Egeria applies" block on the Curate stage's term section, listing active data-class rules from Egeria; "no reader yet" words if the route answers nothing. |
| PI-042 Egeria reports for a database | Closed by G1's "Egeria reports" component. |
| PI-046 after-run Egeria link and annotations | The runs dialog links the report's GUID to the Publish stage's reports component and expands annotations there; no second annotation view. |
| PI-049 repository changes-since-last-run | The same banner component as the database (PI-039, done) on the repository's Understanding, reading `/api/egeria/{slug}/diff`. |
| PI-045 engine choice | **Retired with Classic** (Prefect is the default; the engine shows after the run). |

Gate (coco_pharma and egeria_git, by use): the history table shows the 10-03
and 10-06 surveys with one marked invalid and its reason; the three-number
line matches the inventory; a view's SQL opens and its dependencies name real
tables; the repository banner shows the last diff; the data-class block reads
honestly with no rules configured.

## Order and ownership

G1, G2 and G3 can run in parallel on three builders; they touch different
files except the Publish stage's reports component, which G1 owns and G3
consumes after G1 merges. Merge order G1, G2, G3. Each implemented note
carries the inventory IDs it closes, with DONE, retired or done-by-design,
so the next inventory re-run reads them.

## Not in these slices

File systems (PI-022, 087, 091–097); the "other" group's 30 MISSING rows,
triaged after the demo; the credential prompt driven by the capability probe
(design direction in the credential-gating reply, not specced); the Egeria
Links admin page (PI-130).
