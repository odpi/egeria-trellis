# trellis-auth

Shared JWT auth + Portal SSO token exchange for Trellis apps.

## Why this package exists

Egeria Advisor has a real login (`advisor/auth.py`): HS256 JWTs carrying the
signed-in user's Egeria bearer token, plus Portal SSO (`exchange_portal_token`)
via a shared HS256 secret agreed with the Portal. Resource Explorer has none —
its "Connected as: erinoverview" header reads a `.env` value, not a logged-in
identity.

`exchange_portal_token` is a *contract with an external system*: a payload
shape, an algorithm, a secret, an expiry policy. Two independent
implementations of that contract will drift, and this repo has a documented
track record of exactly that happening when the same mechanism is built
twice (query cache, annotation properties, vector store, ...). See
`docs/trellis-auth-extraction.md` for the full reasoning.

## The contract: one token, two ways in

**The app JWT carries the user's Egeria *bearer token*, never their password**
(changed 2026-09-04 — see "The password contract that never matched" below).
Both ways into a Trellis app end in the same thing: a per-request pyegeria
client holding an Egeria bearer token for the actual user.

```
{sub, user_id, role, display_name, egeria_token, iat, exp}
```

That is the Portal's own payload shape, so one contract serves the direct
login and the SSO handoff alike.

## Usage

```python
from trellis_auth import (
    AuthConfig, apply_token, create_access_token, exchange_portal_token,
    get_current_user, get_egeria_token, login_with_password, validate_egeria_token,
)

config = AuthConfig(
    jwt_secret="...",
    jwt_ttl_hours=8,
    portal_secret="...",             # empty string disables Portal SSO
    egeria_view_server="qs-view-server",
    egeria_platform_url="https://localhost:9443",
)

# --- Direct login: the password is used once, here, and never stored. --------
egeria_token = login_with_password("dan.egeria", "hunter2", config)   # None on failure
token = create_access_token("dan", egeria_token, config)

# --- Portal SSO: the Portal already holds an Egeria token; re-sign it. -------
payload = exchange_portal_token(portal_token, config)   # {sub, role, display_name, egeria_token, exp}
validate_egeria_token(payload["egeria_token"], config)  # optional cheap liveness check
token = create_access_token(
    payload["sub"], payload["egeria_token"], config,
    role=payload["role"], display_name=payload["display_name"],
)

# --- Per request ------------------------------------------------------------
user = get_current_user(request, config)          # None if absent/invalid, never raises
egeria_token = get_egeria_token(request, config)  # the user's bearer token, or None

client = EgeriaTech(view_server=..., platform_url=..., user_id=...)
apply_token(client, egeria_token)   # set_bearer_token(), so Egeria records the person
```

`apply_token` mirrors the Portal's own `egeria_auth.apply_token`: with a token
it calls `set_bearer_token()`, without one it falls back to the client's own
configured credentials — which is how the legitimately service-account-backed
paths (bootstrap, outbox drain, background workers) already authenticate.

## Session expiry is bounded by Egeria's

**Egeria bearer tokens last one hour.** Measured 2026-09-04 against the running
quickstart deployment (`qs-view-server` at `https://localhost:9443`) by minting
one with pyegeria's `create_egeria_bearer_token()` and decoding it: an RS256 JWT
with `{"iss": "self", "sub": ..., "iat": ..., "exp": ...}` where `exp - iat` is
3600.

So `create_access_token` sets the app JWT's `exp` to
`min(now + jwt_ttl_hours, the Egeria token's own exp)`. A session that outlives
its Egeria token looks signed in and cannot make a single Egeria call. The cap
is a `min()`, not an assignment — a long-lived Egeria token must not extend the
session past the configured TTL. Refresh is a re-login, as it is in the Portal.

`egeria_token_expiry()` reads that `exp` **without verifying the signature**,
deliberately: the RS256 key belongs to the view server. This is not an
authentication decision, only a bound on how long the session claims to be
usable — Egeria enforces the token's real validity on every call. An opaque
(non-JWT) Egeria token simply falls back to the app TTL.

## The password contract that never matched

Until 2026-09-04 `create_access_token` signed the **raw Egeria password** into
the HS256 app JWT (a JWT is signed, not encrypted — anyone holding the session
token could base64-decode the password out of it), and `exchange_portal_token`
expected a Portal payload of `{egeria_user, egeria_password, iat, exp}`.

The Portal never issued that. `demo_auth_handler.py::_make_jwt` issues
`{sub, role, display_name, egeria_token, exp}`, where `egeria_token` is a real
Egeria bearer token, applied per request with `set_bearer_token()`. Nothing in
`egeria-workspaces` produced the password shape, so EA's
`POST /api/auth/portal` was wired to a payload that does not exist.

Both halves are fixed. A Portal token in the old shape is now rejected with a
400 that names the change, rather than a generic 401 —
`get_egeria_credentials` likewise raises a `RuntimeError` pointing at
`get_egeria_token`. Full write-up: `docs/trellis-auth-extraction.md` §6.

Apps do not use this module directly — each keeps a thin adapter over it
that supplies its own resolved `AuthConfig` and the shared policy below, the
same shape `trellis-querycache`/`trellis-vectorstore` use. See
`advisor/auth.py`.

## One sign-in per box: a session JWT is valid at every trellis app on it (owner decision A, 2026-11-14)

**An intended contract, not an accident.** Every trellis app on a box signs its session JWT with the same
`TRELLIS_JWT_SECRET` and verifies with it, so a session minted by one app (Trellis Studio, RE, EA) is valid at the
others. The owner decided on 2026-11-14 that this is how it should work: a user signed in to Studio uses the EA
panel embedded in Studio with no second sign-in.

What holds it in place:
- **One secret per box.** Set `TRELLIS_JWT_SECRET` once, for every app. An app-specific override
  (`ADVISOR_JWT_SECRET`, `RE_JWT_SECRET`, `STUDIO_JWT_SECRET`) takes that app out of the shared sign-in: a JWT from
  another app no longer verifies there. Studio refuses to start without a secret, and never derives one.
- **The JWT travels only by `postMessage`, to an exact origin.** Studio hands its session to the embedded EA as
  `{type: 'trellis_auth', jwt}`, targeted at EA's origin. EA accepts it only from the parent window and only from an
  origin in `ADVISOR_EMBED_ORIGINS`, and checks it with its own token check (`GET /api/embed/whoami`, which also
  refuses a service account) before keeping it. Never in a URL, a query string, a log line, or a message to `*`.
- **Same payload, same checks.** `{sub, user_id, role, display_name, egeria_token, iat, exp}`; `exp` is capped at
  the Egeria token's own expiry, so a session can't outlive the Egeria sign-in it carries.
- **Not an SSO issuer.** Studio does not mint Portal-shaped tokens with `TRELLIS_PORTAL_SECRET` (the alternative
  the owner turned down): only the Portal issues those.

So a stolen session JWT is worth the same at every app on the box, for at most its own lifetime. Keep it in
`sessionStorage` (each app does) and never put it anywhere else.

## Login policy: shared, and required

**Decided 2026-09-04 by the project owner**, replacing this README's original
"whether an app requires login at all is each app's own answer." Both Trellis
apps require login, and the policy lives here. Two apps independently deciding
"is login required" is the same drift the token contract above already
demonstrated — and the drift that matters most, because the failure mode is a
deployment that is open when someone believed it was closed.

```python
from trellis_auth import LoginRequiredMiddleware, resolve_policy

policy = resolve_policy("ADVISOR")     # TRELLIS_* then ADVISOR_*, app-specific wins
app.add_middleware(LoginRequiredMiddleware, config=config, policy=policy)
```

`AuthPolicy` is a frozen dataclass: `require_login` (default **True**),
`public_paths`, `anonymous_read` (default False), `login_required_message`.
Anything not on the allowlist and without a valid app JWT gets a `401` with
`WWW-Authenticate: Bearer` and a JSON body naming the login route.

**The allowlist is an allowlist, not a denylist**, so a route added tomorrow is
protected by default; the failure mode of getting it wrong is a loud 401 on a
health probe rather than a silently public endpoint. Defaults: `/health`,
`/health/ready`, `/static/`, `/api/auth/login`, `/api/auth/portal`,
`/api/auth/logout`, `/.well-known/`, `/favicon.ico`; `/docs` and
`/openapi.json` only when `TRELLIS_EXPOSE_OPENAPI=true`. An entry ending in
`/` is a prefix, anything else is exact — except `"/"` itself, which is always
exact, because read as a prefix it would silently make everything public.
**A2A agent cards and the discovery index must be public** (a client has to
learn how to authenticate before it holds a token) — add them via
`TRELLIS_PUBLIC_PATHS` or `extra_public_paths`, which are additive, never a
replacement.

### Environment

| Variable | Default | Effect |
|---|---|---|
| `TRELLIS_REQUIRE_LOGIN` / `<APP>_REQUIRE_LOGIN` | `true` | `false` disables the gate entirely — not a supported mode |
| `TRELLIS_ANONYMOUS_READ` / `<APP>_ANONYMOUS_READ` | `false` | **dev boxes only:** unauthenticated `GET`/`HEAD` pass; writes still 401 |
| `TRELLIS_EXPOSE_OPENAPI` / `<APP>_EXPOSE_OPENAPI` | `false` | adds `/docs`, `/openapi.json` to the allowlist |
| `TRELLIS_PUBLIC_PATHS` / `<APP>_PUBLIC_PATHS` | — | comma-separated, **added** to the defaults |

Truthy is exactly `true`/`1`. An unset *or empty* variable is "not set", so an
env file that declares `ADVISOR_ANONYMOUS_READ=` does not mask a deployment-wide
`TRELLIS_ANONYMOUS_READ=true`. The mode is logged once at startup, and
`anonymous_read` additionally logs a WARNING.

**`anonymous_read` is not "auth off".** Reads pass, writes do not, so nothing
is ever created without an identity behind it — the 2026-08-29 decision that
artifact ownership requires an authenticated identity survives the override.

**Consequence: Egeria is a hard dependency of both apps**, because Egeria is
the identity provider. The QUICKSTART's "answers questions without an Egeria
platform" path is retired.

## The CLI's cached login (`trellis_auth.session_file`)

Egeria's tokens last an hour and die on every platform restart, so a CLI that
prompted per command would be unusable and one that stored the password would
undo the token contract. `session_file` caches the **app JWT** at
`$XDG_CONFIG_HOME/trellis/<app>/session.json`, mode `0600`, written atomically
(temp file → `chmod` → `os.replace`, so the file is never briefly
world-readable at its final name).

```python
from trellis_auth import session_file

session_file.save_session("egeria-advisor", user_id, app_jwt)   # exp read from the JWT
record = session_file.load_session("egeria-advisor")            # expired ones ARE returned
if record is None or record.is_expired:
    print(session_file.expired_message(record, "egeria-advisor login"))
session_file.clear_session("egeria-advisor")
```

`load_session` returns an expired record rather than `None` because the caller
needs the expiry time to say *when* it lapsed — "expired at 09:14" tells a
person whether they were idle or whether the platform restarted under them, and
those have different fixes. A token carrying no `exp` is never treated as
expired: "I cannot tell" must not silently mean "you are signed out".

## What is NOT here (deliberately)

* **Config file locations.** EA reads `advisor/configdata/advisor.yaml` and
  `mcp_servers.json`; this package never reads a file or the environment —
  every function takes an `AuthConfig` the caller already resolved.
* **`resolve_egeria_credentials`'s service-account fallback.** Per the
  2026-08-29 decision, artifact ownership requires an authenticated identity
  with *no* config fallback — a shared package must not ship the fallback
  that decision exists to remove.

## Design notes

**No dependencies beyond `pyjwt` and `starlette`.** No pydantic (config is a
frozen dataclass), no loguru (stdlib `logging`). `starlette` rather than
`fastapi`: the only things needed are `Request` and `HTTPException`, both
starlette types that `fastapi`'s own re-exports subclass — a FastAPI app's
default exception handler for the starlette `HTTPException` already catches
what this package raises, so depending on `fastapi` itself would only add
its dependency surface (pydantic, etc.) for nothing this package uses.

**`pyegeria` is an optional extra (`trellis-auth[egeria]`)**, imported
lazily inside `login_with_password`, `validate_egeria_token` and
`validate_egeria_credentials`. A missing install is caught by the same broad
`except Exception` that already handles "Egeria is unreachable", so a consumer
that never calls those doesn't pay for the install. `apply_token` needs no
import at all — it only calls methods on a client the caller built.

**`validate_egeria_credentials`'s service-account fallback is not the
excluded one.** When `AuthConfig.egeria_view_server`/`egeria_platform_url`
aren't set, it compares against `egeria_service_account_user/password`
instead of attempting a live call — that's a sanity check for a
misconfigured deployment's login form, not the per-request credential
*resolution* fallback `resolve_egeria_credentials` uses, which stays out of
this package entirely.
