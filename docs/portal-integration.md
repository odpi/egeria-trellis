# Portal integration — what the quickstart Portal needs to open Egeria Advisor and Resource Explorer

> **Status (2026-11-19, 6.3 Tech Preview):** current. The contract holds for RE and EA, and Trellis Studio uses the same exchange (`POST /api/auth/portal`, `TRELLIS_PORTAL_SECRET` / `STUDIO_PORTAL_SECRET`). `EGERIA_RESOURCE_EXPLORER_URL` is no longer a proposal: the QuickStart carries it. The commit hashes below are from 2026-09.

Answers for the session maintaining `egeria-workspaces` (PyegeriaWebHandler), 2026-09-04. Both apps are
multi-user and require login as of today; the Portal's existing Advisor handoff uses a contract that no
longer exists.

## 1. Egeria Advisor is genuinely multi-user now

- Login required (trellis commit 806e702): shared policy in `trellis-auth`, EA enforces it.
- Per-user state (a4ef5ec, d3bf498): a Postgres session store (`advisor_sessions`), drafts, governance
  plans and report specs written under the signed-in user's namespace, conversation state keyed by
  (user, session), ownership-checked reads (404 for another user's item unless curator/admin role).
- Concurrency is per request; nothing in EA assumes one user at a time. **The exclusive lock in
  `advisor_lock_handler.py` can be removed.** Keep the reachability check and the SSO handoff. If a demo
  wants pacing, make the lock advisory (a banner), never a gate.

## 2. The handoff token contract changed (11b39a5)

`POST {ADVISOR}/api/auth/portal` with body `{"portal_token": "<jwt>"}` now accepts only the Portal's own
session-token shape, HS256 signed with the shared secret (`EGERIA_ADVISOR_SSO_SECRET` on the Portal,
`ADVISOR_PORTAL_SECRET` in EA):

```json
{"sub": "<egeria user id>", "role": "user|admin", "display_name": "<name>",
 "egeria_token": "<the user's Egeria bearer token>", "iat": <now>, "exp": <now + 120>}
```

That is exactly what `demo_auth_handler._make_jwt` already issues for the Portal cookie, so mint the
handoff from the signed-in session's `egeria_token`. The old `{"egeria_user","egeria_password",...}`
payload that `advisor_lock_handler.py` mints is rejected with 400 (the password must never leave the
Portal; the app JWT carries the Egeria bearer token and its session is capped at the token's 1 h expiry).
The `#pt=<token>` fragment convention and the 120 s expiry are unchanged; EA's `auth.js` reads it.

## 3. Resource Explorer is ready to be a tile

Identical mechanism, landed in c3f98e6:

| | Egeria Advisor | Resource Explorer |
|---|---|---|
| URL env for the Portal | `EGERIA_ADVISOR_URL` (default `http://localhost:8880/`) | `EGERIA_RESOURCE_EXPLORER_URL` (default `http://localhost:8810/`) |
| Handoff exchange route | `POST /api/auth/portal` | `POST /api/auth/portal` (same body, same payload) |
| Secret the app verifies with | `ADVISOR_PORTAL_SECRET` | `RE_PORTAL_SECRET` or `TRELLIS_PORTAL_SECRET` |
| Fragment | `#pt=<token>` | `#pt=<token>` |
| Reachability | `GET /health` | `GET /health/ready` |
| Login policy (public) | `GET /api/auth/policy` → `{"login_required":true,...}` | same |
| Lock needed | no | no (long work goes through a Postgres run queue with per-user fairness) |

The trellis compose runtime (`egeria-workspaces/compose-configs/optional-associated-runtimes/trellis`)
passes **one** value to both apps: `ADVISOR_PORTAL_SECRET` to EA and `TRELLIS_PORTAL_SECRET` to RE, so the
Portal can keep a single `EGERIA_ADVISOR_SSO_SECRET` (or rename it to something app-neutral and alias).

## 4. Reachable URLs

`http://localhost:8880/` only works on the operator's own machine. Per deployment the Portal needs the
URL a browser can reach: on trevor today `EGERIA_ADVISOR_URL=http://egeria.pdr-associates.com:8880/`
(from the live quickstart env); on a LAN demo the box's LAN or tailnet name. Both apps publish their ports
on all interfaces in the compose runtime. Same for the new RE URL.

## 5. Ownership of files

`demo-portal.html`, `advisor_lock_handler.py`, `demo_config.py`, `egeria-quickstart.yaml`'s env block and
`.env.example` are yours; the trellis side is complete and needs no change for this. The trellis session is
not editing any of them.
