# D1-b: the database-level press, and how to undo it (2026-10-31)

Design: `DESIGN-ADOPT-BY-ENDPOINT-D1-2026-10-30.md` §10, §12 and §13 (the builder brief). The owner, 2026-10-31
(coordinator relay): "yes, database level is what I meant - go". The press is the owner's, from their own signed-in
pane, after gate, merge, restart and a peer round.

## What the press does

`POST /api/catalogue-scope/{slug}/reanchor` (the button "Link the pack's N schemas" on the plan's database row in
Contents; signed in, with the same publish check the commit uses; `endpoint_identity.apply_reanchor`) adds the
relationship the row names from RE's database element to every schema in the row's `to_add`. For coco_pharma:
`DataSetContent` from c990f349 to the content pack's 22 schemas, 26 members after.

- **The list is the plan's.** The newest `endpoint_resolved` proof row (written by "Read Egeria again") holds
  `plan.database_row` (`link_type`, `re_guid`, `schema_end`, `to_add`). The press uses exactly that list. It is refused,
  writing nothing, when that row is missing, older than the read-back staleness (`READ_BACK_NOT_CHECKED_AFTER_S`,
  120 s), from a failed read, or proposes nothing: "read Egeria again first".
- **The type and the ends are read** off the links RE's database already has to its own schemas (live 2026-10-31:
  `DataSetContent`, the schema at end 1, the database at end 2, as the JDBC cataloguer links them:
  `SCHEMA_PARENT_AT_END1 = False`). The call is pyegeria's `AssetMaker.link_data_set_content(<schema>, <database>)`.
- **Idempotent.** Each schema's relationships are read first; a link to RE's database that already exists is skipped
  and counted `already_linked`. A second press adds nothing and says "22 already linked". One Egeria call per missing
  link; one press per database at a time.
- **Read back by relationship.** pyegeria's wrapper returns no relationship GUID, so each schema is read again after
  its link is made: `added` only when the link is seen, with its GUID; a link sent but not seen is `failed` with
  "sent, but the link is not on the read-back".
- **One proof row**, `reanchor_applied` (node `database`), with every schema's outcome
  `{schema, guid, outcome: added | already_linked | failed, error, relationship_guid}`, the counts, the members after
  and the undo below; then the plan is read again (`record_resolution`), so the row shows "26 members · 22 added ·
  read back <time>" and nothing left to add. The log carries one `reanchor_applied ... added relationship=<guid>` line
  per link and one `reanchor_timing slug= to_add= added= already= failed= total_ms=` line per press.

Nothing of the pack's is changed: its elements keep every property; the press only adds relationships.

## How to undo

Every relationship the press added is listed, by its own GUID, in the press's proof row (`detail_json.outcomes[*]`
with `outcome = "added"`, field `relationship_guid`) and in the log. Remove each one ALONE by that GUID:

```python
from pyegeria import AssetMaker
am = AssetMaker(view_server, platform_url, user_id, user_pwd)   # signed in as a steward of the database
am.create_egeria_bearer_token()
for guid in relationship_guids:          # the proof row's `added` outcomes, never searched for
    am.detach_data_set_content_relationship(guid)
```

`detach_data_set_content_relationship` addresses one relationship by its GUID (DataSetContent is multi-link, so the
two-element `detach_data_set_content(<data set>, <content>)` form could remove a link the cataloguer made). Never
detach a relationship listed as `already_linked` or not in the proof row: RE's own 4 schema links were made by
Egeria's cataloguer.

After an undo, press "Read Egeria again" on Contents: the plan's database row shows the members Egeria holds again.
