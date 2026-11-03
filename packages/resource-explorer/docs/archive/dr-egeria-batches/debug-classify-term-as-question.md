# Debug — Classify Term as Question validation failure

> **Resolved 2026-08-12** — fixed upstream by upgrading pyegeria to
> 6.0.17.21 (see egeria-workspaces-fs's `PyegeriaWebHandler/Dockerfile-
> fast-api` + new `bin/update-pyegeria.sh`). Kept below for reference:
> the repro pattern and the by-name-lookup gotcha it surfaced are still
> useful precedent.
>
> **Diagnostic tip for next time a by-name lookup goes sideways**:
> pyegeria has `ClassificationExplorer.get_element_by_guid()` — resolve
> the element by GUID directly instead of re-guessing name variants
> (apostrophes, punctuation, etc.) against a broken search. This would
> have shortcut the "is the term still even there?" uncertainty during
> the cleanup below (see `Delete Term` duplicate-cleanup note) — none of
> the `egeria` MCP tools here expose it (`egeria_execute_command`/
> `dr_egeria_run_block` only run Dr.Egeria markdown commands), so it
> needs a direct pyegeria call, not this MCP path.
>
> **`Delete Term` works** (confirmed 2026-08-12 cleaning up two duplicate
> "How widely adopted..." terms created while diagnosing this bug) —
> requires both `Display Name` and `Qualified Name` (Qualified Name alone
> fails: "Missing required attribute: 'Display Name'"). Not currently
> exercised anywhere else in this doc set; dwolfson is adding a
> delete-command family to the dr_egeria backlog since `Delete Glossary
> Term` (a different, guessed name) is a recognized-but-unimplemented
> command ("No processor registered").
>
> Minimal repro for a `Request body failed validation` error hit while
> processing the Scouting questions batch (2026-08-12). Targets an
> already-existing real GlossaryTerm ("Who maintains this repository?",
> GUID 73e54706-1cb9-4361-9ad8-1414b28531bd, created via `Create Glossary
> Term` against qs-view-server) so there's no dependency on anything else
> in this file — just the one command.
>
> Reproduced via the egeria MCP server's `dr_egeria_run_block` /
> `egeria_execute_command` tools with directive=process against
> qs-view-server (Coco Pharmaceuticals sandbox). Fails identically even
> with only `Term Name` supplied — varying `Status` (tried `ACTIVE`,
> `DISCOVERED`) made no difference. Works fine in directive=display /
> validate (which doesn't call the classification endpoint) — only fails
> when actually processed.
>
> By contrast, the standalone `Create Question` command (creates a
> separate Question-typed element, not a classification on a
> GlossaryTerm) succeeds cleanly in process mode with the same kind of
> attributes. That's the main clue: this looks like the classification
> command sending Create-style default fields (Is Own Anchor, Merge
> Update, Parent at End1) into what should be a pure classification
> request body, and the backend rejecting them.

## Classify Term as Question

### Term Name
Who maintains this repository?

___
