# Trellis Studio

The deliberate authoring surface for report specs, Reports and dashboard sheets: forms, preview and lifecycle,
over `pyegeria.authoring`. Studio has **no LLM of its own** (D6); AI-assisted authoring is Egeria Advisor's, and
Studio embeds EA's chat as a side panel.

- Port **8840** on laz, **7840** for freshstart (D2). `studio web --port 8840`.
- Every Egeria read and write runs **as the signed-in user**, through a client that never holds a password and is closed after each request. Studio has no service account: with no user token
  there is no Egeria call (`trellis_studio.egeria.user_client` refuses with 401 before building a client).
- Sign-in is trellis-auth's: password, or Portal SSO (`#pt=<token>` fragment, or `postMessage` from an origin in
  `STUDIO_PORTAL_ORIGINS`). The session JWT is signed with the box-wide `TRELLIS_JWT_SECRET`, so it is valid at
  EA too (owner decision A; see `packages/trellis-auth/README.md`).

## Settings

| Variable | Meaning |
|---|---|
| `EGERIA_PLATFORM_URL` (or `EGERIA_VIEW_SERVER_URL`) | the Egeria platform |
| `EGERIA_VIEW_SERVER` | the view server |
| `TRELLIS_JWT_SECRET` | **required**; never derived, and no Studio-only override (one sign-in per box) |
| `TRELLIS_SERVICE_ACCOUNTS`, `RE_DAEMON_USER_ID` | ids no one may sign in as (refused as EA refuses them; list EA's own service account here too) |
| `TRELLIS_PORTAL_SECRET` | the Portal's SSO secret; empty disables Portal SSO |
| `EGERIA_ADVISOR_URL` | where the EA panel loads from (default `http://localhost:8880/`) |
| `STUDIO_PUBLIC_URL` | Studio's own origin (EA's `ADVISOR_EMBED_ORIGINS` must list it) |
| `STUDIO_PORTAL_ORIGINS` | origins allowed to hand a Portal token over by `postMessage` |

The login policy is trellis-auth's shared one (`TRELLIS_*` then `STUDIO_*`).

## Install and run

Studio is part of the Trellis workspace; it needs an Egeria to sign in to (the QuickStart's, by default) and, for
its AI panel, Egeria Advisor.

1. From the repository root, install the workspace once: `make sync` (it creates `.venv` with Studio's `studio`
   command).
2. Create Studio's settings: `cp packages/studio/.env.example packages/studio/.env`, then set what you need. The one
   required value, `TRELLIS_JWT_SECRET`, is written for you by step 3 if no Trellis app has one yet (the same value
   as Resource Explorer's and Egeria Advisor's, so one sign-in works across them).
3. Start it:
   - with the other Trellis apps: `make up` (Resource Explorer, Egeria Advisor and Studio), or
   - on its own: `make up-studio` (or `make studio-web`, without trellis-up's secret and start checks).
   trellis-up reports an app that fails to start, by name with its first error line, and exits non-zero.
4. Open http://localhost:8840 and sign in with your Egeria user id and password (a QuickStart demo user such as
   `erinoverview`, never a service account).

`scripts/start-studio.sh [port]` is what the make targets run: it loads `packages/studio/.env` without overriding
values already in the environment (`--no-override`), and unsets `EGERIA_USER` / `EGERIA_PASSWORD` /
`EGERIA_USER_PASSWORD` (Studio has no service account). `STUDIO_ENV_FILE` points it at another settings file.

## Tests

`pytest packages/studio/tests` — no network, no Egeria.
