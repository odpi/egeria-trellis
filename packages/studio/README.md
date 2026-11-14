# Trellis Studio

The deliberate authoring surface for report specs, Reports and dashboard sheets: forms, preview and lifecycle,
over `pyegeria.authoring`. Studio has **no LLM of its own** (D6); AI-assisted authoring is Egeria Advisor's, and
Studio embeds EA's chat as a side panel.

- Port **8840** on laz, **7840** for freshstart (D2). `studio web --port 8840`.
- Every Egeria read and write runs **as the signed-in user**. Studio has no service account: with no user token
  there is no Egeria call (`trellis_studio.egeria.user_client` refuses with 401 before building a client).
- Sign-in is trellis-auth's: password, or Portal SSO (`#pt=<token>` fragment, or `postMessage` from an origin in
  `STUDIO_PORTAL_ORIGINS`). The session JWT is signed with the box-wide `TRELLIS_JWT_SECRET`, so it is valid at
  EA too (owner decision A; see `packages/trellis-auth/README.md`).

## Settings

| Variable | Meaning |
|---|---|
| `EGERIA_PLATFORM_URL` (or `EGERIA_VIEW_SERVER_URL`) | the Egeria platform |
| `EGERIA_VIEW_SERVER` | the view server |
| `TRELLIS_JWT_SECRET` | **required**; never derived |
| `TRELLIS_PORTAL_SECRET` | the Portal's SSO secret; empty disables Portal SSO |
| `EGERIA_ADVISOR_URL` | where the EA panel loads from (default `http://localhost:8880/`) |
| `STUDIO_PUBLIC_URL` | Studio's own origin (EA's `ADVISOR_EMBED_ORIGINS` must list it) |
| `STUDIO_PORTAL_ORIGINS` | origins allowed to hand a Portal token over by `postMessage` |

The login policy is trellis-auth's shared one (`TRELLIS_*` then `STUDIO_*`).

## Starting it

`scripts/start-studio.sh [port]` (dotenv `--no-override`, `EGERIA_USER`/`EGERIA_PASSWORD` unset). On laz, only
when the Coordinator says so.

## Tests

`pytest packages/studio/tests` — no network, no Egeria.
