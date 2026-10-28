# Outbox drain identity - implemented (2026-10-07)

## Phase 1 finding

The report: seven `collection_membership` rows (#69837-#69843, from the blueprint-verdicts
press on repo/egeria_git) fail on every drain with
`AUTHORIZATION_ERROR_401 ... received for user - ``` and the call was taken to have gone
out for the empty user ''. **That reading does not hold.**

* The empty user in that sentence is a pyegeria rendering gap. `PyegeriaUnauthorizedException`
  fills `{0}` from `additional_info["userid"]`. The bare-HTTP-401 branch sets it; the
  Egeria-wrapped branch (`relatedHTTPCode` 401/403, `_base_server_client.py`) passes
  `additional_info=json_response`, which has no `userid`. Every wrapped 401 therefore reads
  "for user - ``", whoever was sent.
* The drain never had '' to send. `scheduler._drain_egeria_outbox` -> `drain_outbox(registry)`
  -> `_default_clients()` -> `EgeriaPublisher()` -> `resolve_identity()` ->
  `caller_credentials()`: with an empty ContextVar that is `service_credentials()`, the
  configured service account (default `erinoverview`); the client is built with
  `EGERIA_USER`/`EGERIA_USER_PASSWORD` and `create_egeria_bearer_token()`.
* Outbox rows record no requesting user (`registry.enqueue_outbox_element`), so a per-row
  identity is not an established pattern and was not added.

What IS true, and what was fixed: (1) the drain took whatever the ContextVar held, so a drain
started from a request context (`asyncio.to_thread` copies it) would run as that person on an
hour-lived token; (2) a blank service user id was not stopped before reaching Egeria;
(3) `service_credentials()` reads `EGERIA_USER_ID` while the publisher's own constructor read
`EGERIA_USER`, two names for one account; (4) a refusal row never said who was refused.

**Not established:** why Egeria refuses the service account for these seven. The likely class
is authorisation on a blueprint created under the owner's session (the service account may
lack update rights on it), but that was not read from Egeria and nothing was run against it.
This change makes the next failure say which identity was refused; it does not claim to fix
that refusal. Same for the ~10 dead 09-29 egeria-understanding rows: unverified.

## What changed (`resource_explorer/egeria_outbox.py`)

* `drain_identity()`: always `service_credentials()`; blank user id raises `OutboxIdentityError`.
* `_default_clients()` builds `EgeriaPublisher(user_id, user_password, identity=...)` from it,
  and records `OutboxClients.acting_as`.
* `drain_outbox`: `OutboxIdentityError` is its own branch. No Egeria call; rows return to
  `pending` with attempts untouched and "Not attempted - configuration error: ..." in
  `last_error` (visible in Admin -> Publish Queue); `log.error`; `summary["config_error"]`.
  The first drain after the setting is fixed takes them. Applies to destructive kinds too (none
  attempted, none burnt).
* A `PyegeriaUnauthorizedException` failure appends who was refused and why the user shows empty.
* `DESTRUCTIVE_OUTBOX_KINDS` unchanged; rows already `failed` retry normally. No DDL, no
  migration, no row touched.

Tests: `tests/test_outbox_drain_identity.py`. `test_default_clients_carries_a_collection_manager`
needed its fake constructor to accept keywords.
