# trellis-feedback

One feedback store and one triage workflow, shared by Resource Explorer and Egeria Advisor.
It holds ratings of chat answers and page-level product reports. Admins set their status through
an append-only log of triage events.

Design and the project owner's decisions: [`docs/trellis-feedback-extraction.md`](../../docs/trellis-feedback-extraction.md).
RE keeps its page feedback here since 0.2 (`resource_explorer/feedback_store.py`); its chat votes,
Admin triage controls and EA follow (§6 of that doc).

## Why this package exists

Both apps grew feedback separately and both have gaps (§1 of the design doc):

| | Egeria Advisor | Resource Explorer |
|---|---|---|
| Answer ratings | full records, in JSONL files | effectively unrecorded (a hash-only metrics row) |
| Triage | five statuses, keyed by position in a file (which can drift onto the wrong record) | three statuses on page feedback, with no UI to set them |
| Who can read and triage | any signed-in user | admins |

## What it does

- **Items** (`FeedbackItem`) have a `kind`: `answer` (vote, question, full answer text, intent
  label, compile id, resource scope) or `product` (category, page, message, rating, contact
  consent). Each has a stable id assigned by the store, and a submit can carry an **idempotency
  key**, so a retry is not a duplicate.
- **Triage is append-only.** Each status change is a `TriageEvent`, ordered by a monotonic
  sequence id. An item's status is its latest event's, or `new` if it has none. Notes live only
  on events, so nothing is ever overwritten. Statuses: `new`, `triaged`, `known_issue`,
  `actioned`, `deferred`, `not_an_issue`.
- **Diagnosis (0.3).** A triage event on an answer item may also record a `cause`: why the answer
  failed, from the closed-loop design's nine (`CAUSES`, [`docs/feedback-closed-loop-design.md`](../../docs/feedback-closed-loop-design.md) §2).
  The seven in `CAUSE_CHAIN` are in precedence order, and the cause is the earliest stage at
  fault. An optional `secondary_cause` must come later in the chain and is for measurement only.
  An item's cause is its latest diagnosis, so a later status-only event keeps it. Product reports
  and external rows take a status only. `list(cause=...)` filters on it (`UNDIAGNOSED` for answer
  items with none). `cause_counts()` counts answer items by cause. A 0.2 events table gains the two
  columns in place when a 0.3 store opens it.
- **External rows** (RE's Curate `resource_feedback`) are triaged by reference,
  `(app, source, id)`. The source must be on a list the app declares. When the app deletes such
  a row itself, its history stays and is reported as "gone" until an admin clears it.
- **Deleting an item deletes its triage history too**, in one transaction.
- **Storage:** Postgres, with the tables in a schema of the app's choosing, or SQLite. The tables
  are `trellis_feedback_items` and `trellis_feedback_events`.

## Usage

```python
import os
from urllib.parse import quote

from trellis_feedback import FeedbackItem, FeedbackStore

store = FeedbackStore(
    # The password comes from the environment (PGVECTOR_PASSWORD); never written into code (TR-12).
    f"postgresql://egeria_advisor:{quote(os.environ['PGVECTOR_PASSWORD'], safe='')}@localhost:5442/egeria_advisor",
    app="resource-explorer",
    schema="resource_explorer",
    external_sources=("resource_feedback",),
)

item, created = store.submit(
    FeedbackItem(app="resource-explorer", kind="answer", vote=-1,
                 query_text="what changed?", response_text="...", user_id="peterprofile"),
    idempotency_key="client-generated-uuid",
)
store.triage(item.id, "triaged", note="looking at it", actor="erinoverview")
store.list(status="triaged")              # each row carries its current "triage" state
store.triage(item.id, "triaged", cause="wrong_evidence", secondary_cause="reasoning", actor="erinoverview")
store.cause_counts(vote=-1)               # {"catalog_gap": 0, ..., "wrong_evidence": 1, "undiagnosed": 0}
store.triage("42", "known_issue", source="resource_feedback")
store.delete_item(item.id)                # removes its events too

# Moving existing feedback in: keeps the id and created_at; False if this app already has it,
# so a re-run writes nothing. Append any old status with triage() only after a True.
if store.import_item(FeedbackItem(app="resource-explorer", kind="product", message="old",
                                  id=legacy_id, created_at="2026-08-01T10:00:00+00:00")):
    store.triage(legacy_id, "triaged", actor="migration")
```

### Routes (`api` extra)

```python
from trellis_feedback.router import make_router

app.include_router(
    make_router(
        store,
        is_admin=lambda request: ...,      # the app's own admin check
        current_user=lambda request: ...,  # the signed-in user's id, or ""
        external_lookup={"resource_feedback": lambda ids: ids_that_still_exist(ids)},
    ),
    prefix="/api/feedback/v2",
)
```

| Route | Who |
|---|---|
| `GET /meta` | anyone the app lets through |
| `POST /items` | anyone the app's own login middleware lets through (the router does not require a login; an anonymous item has user `""`). The user comes from `current_user`, never from the body. A replay of an idempotency key returns only `{"item": {"id": …}, "created": false}` |
| `GET /items`, `GET /items/{id}` | admin |
| `POST /triage` | admin |
| `DELETE /items/{id}` | admin |
| `GET /external/{source}?ids=…` (states, plus gone rows) | admin |
| `DELETE /external/{source}/{id}` (only once the row is gone) | admin |

Idempotency keys are matched per user, so one user's key never finds another user's item.

Known limitation (0.1): `GET /external/{source}` passes every referenced id of that source to the
app's lookup on each call. Fine at current volumes; batch it if a source grows large.

The library reads no environment and decides nothing about identity: the app passes in its URL,
schema, admin check and current user.

## Tests

```bash
uv run --package trellis-feedback pytest packages/trellis-feedback/tests
# and on Postgres too, in a throwaway schema that is dropped afterwards:
# (PGVECTOR_PASSWORD from the environment, e.g. `source ~/.re-gate/secrets/pgvector.env`; no password in this file)
TRELLIS_FEEDBACK_TEST_PG_URL="postgresql://egeria_advisor:${PGVECTOR_PASSWORD}@localhost:5442/egeria_advisor" \
  uv run --package trellis-feedback pytest packages/trellis-feedback/tests
```
