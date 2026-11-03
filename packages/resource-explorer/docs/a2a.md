# Resource Explorer — the A2A surface

**Status:** live. The A2A server surface (one service, routed by path).

**The `a2a` process role** (`docs/runtime-architecture-plan.md` §2). One service,
one port, every agent routed by path, a bearer token required on every call.

    resource-explorer serve --port 8090

Before 2026-09-04 this was seven `agentstack_sdk` servers on ports 8080-8086 with
no authentication at all. It is now one uvicorn, one port, and one gate.

---

## Endpoints

Base URL below is `http://<host>:8090`.

| Path | What it is | Token needed |
|---|---|---|
| `/.well-known/agents.json` | **Index of every agent** — names, URLs, cards, skills, auth scheme, RE version, the Egeria platform this service is bound to | no |
| `/.well-known/agent-card.json` | The orchestrator's A2A agent card | no |
| `/` | The orchestrator — the default agent | yes |
| `/whoami` | Who the presented token says you are | yes |
| `/health` | Liveness plus the agent list | no |
| `/agents/<name>/.well-known/agent-card.json` | That agent's card | no |
| `/agents/<name>/v1/message:send` | That agent's A2A **REST** endpoint | yes |
| `/agents/<name>/jsonrpc/` | That agent's A2A **JSON-RPC** endpoint (the card's preferred transport) | yes |

`<name>` is one of `orchestrator`, `stats`, `code`, `docs`, `health`, `compare`,
`integration`. The orchestrator answers at both `/` and `/agents/orchestrator` —
one app mounted twice, so a task started through one URL is visible through the
other.

Prefix a question with `project:<slug>` to scope it to one registered project.
`stats` and `health` will otherwise ask which project you mean, using A2A's
`input_required` pattern.

---

## Authentication

Every agent endpoint requires:

    Authorization: Bearer <token>

Two kinds of token are accepted, per the plan's §4 ("Authentication: two paths,
one token"):

1. **A trellis app JWT** — issued by this deployment's own login, or by the
   Portal SSO exchange. HS256 under `RE_JWT_SECRET` (or the workspace-wide
   `TRELLIS_JWT_SECRET`). The caller's Egeria bearer token rides inside it as the
   `egeria_token` claim. Verification is local and free.
2. **A raw Egeria bearer token** — what the Portal already holds, and what an
   orchestrator that talks to Egeria itself already has. Validated by *using* it:
   one `get_my_profile()` call against the configured view server. The result is
   cached, keyed on the token, for the token's own remaining lifetime (Egeria
   tokens carry an `exp`; one hour in the quickstart). Rejections are never
   cached.

The app JWT is tried first — a token that verifies under our own secret was
issued by us and cannot be mistaken for the other kind.

Anything without an accepted token gets **401** with
`WWW-Authenticate: Bearer realm="resource-explorer-a2a"`.

**Cards and the index are deliberately public.** An agent card describes how to
authenticate to an agent; a client has to read it before it holds a credential.
The cards carry no project data.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `RE_JWT_SECRET` / `TRELLIS_JWT_SECRET` | unset | HS256 secret for verifying trellis app JWTs. Unset means only raw Egeria tokens are accepted, and the role says so at startup. |
| `RE_PORTAL_SECRET` / `TRELLIS_PORTAL_SECRET` | unset | Shared secret with the Egeria Portal, for the SSO exchange. |
| `A2A_ALLOW_ANONYMOUS` | `false` | `true` restores the pre-2026-09-04 no-auth behaviour. **Local development only** — the role logs a warning at startup naming what it gives up. |
| `A2A_PUBLIC_URL` | `http://<host>:<port>` | The URL published *in the cards*. Set it whenever the bind address is not the reachable address — a container binding `0.0.0.0` would otherwise advertise a card nobody can follow. |
| `EGERIA_PLATFORM_URL`, `EGERIA_VIEW_SERVER` | quickstart defaults | Which Egeria validates raw bearer tokens, and what the index reports as the binding. |

### What the caller's identity reaches

The middleware puts a `CallerIdentity` in a ContextVar
(`resource_explorer.a2a_auth.current_caller`) for the life of the request — the
same ContextVar the web login's `_identity_middleware` sets from RE's session
JWT, so both doors reach identical code. Asyncio tasks, `asyncio.to_thread` and
RE's shared pool (`concurrency.run_sync` / `submit`, which copy the submitter's
context) carry it. A bare `threading.Thread` does **not**: copy the context
explicitly (`ctx = contextvars.copy_context(); Thread(target=ctx.run, args=(fn,))`)
or hand the resolved identity in. A read that finds no caller is not safe: the
registry reads `''` as the service identity, which sees every private
investigation.
Agent code gets a pyegeria client for the caller from RE's one factory:

```python
from pyegeria import EgeriaTech

from resource_explorer.egeria_clients import Caller, egeria_client

client = egeria_client(Caller(), purpose="what this read is for").of(EgeriaTech)
# The caller's Egeria token is set on the client. With nobody signed in Caller() raises
# NoCallerIdentity (401); it never falls back to the service account.
```

**Who Egeria sees (owner's decision, 2026-10-14, option 2 of
`docs/design-notes/DESIGN-MCP-SERVER-AND-A2A-GAPS.md`):** everything
synchronous that serves a signed-in request runs as the caller, so a read
returns only what the caller's zones allow. Queued writes (the outbox, the
scheduler, heals, bootstrap) stay on RE's service account with the requester
recorded; the engine host stays a daemon. The read side is inventoried in
`docs/design-notes/evidence/IDENTITY-INVENTORY-2026-10-14.md`; in-request
writes are converted in a following change. The A2A agents themselves make no
Egeria call today.

---

## Discovery

`GET /.well-known/agents.json` is not an A2A standard path — the standard gives
one card per agent and no way to learn that six more exist next to the one you
found. This index closes that in one fetch:

```json
{
  "service": "resource-explorer",
  "role": "a2a",
  "version": "0.1.0",
  "base_url": "http://127.0.0.1:8090",
  "default_agent": "orchestrator",
  "egeria": {"platform_url": "https://localhost:9443", "view_server": "qs-view-server"},
  "authentication": {
    "scheme": "Bearer",
    "required": true,
    "accepts": ["trellis-app-jwt", "egeria-bearer-token"],
    "anonymous_allowed": false
  },
  "agents": [
    {
      "name": "docs",
      "title": "Resource Explorer: Documentation",
      "url": "http://127.0.0.1:8090/agents/docs",
      "card_url": "http://127.0.0.1:8090/agents/docs/.well-known/agent-card.json",
      "endpoints": {
        "rest_message_send": "http://127.0.0.1:8090/agents/docs/v1/message:send",
        "jsonrpc": "http://127.0.0.1:8090/agents/docs/jsonrpc/"
      },
      "skills": ["conceptual_qa", "api_reference"],
      "default": false
    }
  ]
}
```

**The URL a Portal would fetch is `<RESOURCE_EXPLORER_URL>/.well-known/agents.json`**
— the same shape as the Portal's existing `EGERIA_ADVISOR_URL` discovery. Actually
registering the cards with the Portal is out of scope here and still open; so is
an A2A surface for Egeria Advisor.

---

## Example

```bash
# 1. Discover. No token needed.
curl -s http://localhost:8090/.well-known/agents.json | jq '.agents[].name'

# 2. Without a token — 401.
curl -i -s -X POST http://localhost:8090/agents/docs/v1/message:send \
  -H 'Content-Type: application/json' -d '{}' | head -3
# HTTP/1.1 401 Unauthorized
# www-authenticate: Bearer realm="resource-explorer-a2a"

# 3. Get a token. Either an Egeria bearer token …
TOKEN=$(curl -sk -X POST \
  "https://localhost:9443/servers/qs-view-server/api/v1/users/erinoverview/token" \
  -H 'Content-Type: application/json' -d '{"userId":"erinoverview","password":"secret"}')
#    … or a trellis app JWT minted from one (trellis_auth.login_with_password
#      then create_access_token).

# 4. Check what the service thinks you are.
curl -s http://localhost:8090/whoami -H "Authorization: Bearer $TOKEN" | jq
# {"user_id": "erinoverview", "auth_source": "egeria-token", ...}

# 5. Ask the docs agent something. NOTE the body shape — see the warning below.
curl -s -X POST http://localhost:8090/agents/docs/v1/message:send \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": {"role": "ROLE_USER", "message_id": "1",
                   "content": [{"text": "project:docling What is Docling?"}]}}'
# -> {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED"}, ...}}

# 6. The call returns a task, not an answer. Poll it.
curl -s http://localhost:8090/agents/docs/v1/tasks/<task-id> \
  -H "Authorization: Bearer $TOKEN" | jq '.status.state, .history[-1].content'
```

The orchestrator is the same call with no `/agents/<name>` prefix; it classifies
the question and routes to the right specialist itself:

```bash
curl -s -X POST http://localhost:8090/v1/message:send -H "Authorization: Bearer $TOKEN" ...
```

> **The REST body is protobuf-shaped, not the A2A pydantic shape.** The `/v1/…`
> endpoints parse with `google.protobuf.json_format`, so the field names are
> `message_id` and `content` (not `messageId`/`parts`) and `role` is the enum
> name `ROLE_USER` (not `user`). Getting this wrong returns a bare
> `{"message": "unknown exception"}` with the real `ParseError` only in the
> server log — worth knowing before you debug the auth you just set up. The
> `/jsonrpc/` endpoint on each agent takes the A2A JSON-RPC shape instead, and
> is what each card advertises as its preferred transport.

---

## Running it

```bash
resource-explorer serve --port 8090            # the role
resource-explorer serve                        # same; 8090 is the default
A2A_ALLOW_ANONYMOUS=true resource-explorer serve   # local dev, no auth, warns
```

In a container, `docker/entrypoint-resource-explorer.sh a2a` dispatches here and
passes `PORT` through.

`--base-port` is accepted as a **deprecated** alias for `--port` and warns:
there is no base to offset from any more. `--all` is accepted and ignored —
every agent is always served.

Operationally the role behaves like `web` and `worker`:

* `make ps` labels it `re-a2a` with its port.
* `kill -USR1 <pid>` dumps every thread's stack.
* SIGTERM/SIGINT drain within `--shutdown-timeout` (default 10s) and exit.

---

## Why one FastAPI root and not seven SDK servers

`agentstack_sdk.server.Server` hosts exactly one agent —
`Server.agent()` raises `ValueError("Server can have only one agent.")` on a
second registration, and `Server.serve()` builds and owns its own uvicorn. (RE
design rule 8, re-confirmed against agentstack-sdk 0.7.1.)

The supported multi-agent shape is one layer down:
`agentstack_sdk.server.app.create_app(agent, ...)` returns a plain FastAPI app
per agent — the A2A REST app with the A2A JSON-RPC app already mounted at
`/jsonrpc`. The role builds one per agent and mounts them under `/agents/<name>`
on a single FastAPI root, with the orchestrator's mounted again at `/`. The auth
middleware wraps the root, so it applies to every agent identically and cannot
be forgotten for one of them.

See `resource_explorer/a2a_role.py` and `resource_explorer/a2a_auth.py`.
