# Kroki — server-side diagram rendering

**Status:** live. How server-side mermaid rendering works.

## What it's for

Resource Explorer's database ER-diagram view (mermaid flowcharts of table/
view relationships) renders diagrams server-side via [Kroki](https://kroki.io)
instead of running mermaid.js in the browser. `POST /api/diagrams/mermaid`
(`resource_explorer/web/routes/diagrams.py`) is a thin backend proxy: it
takes mermaid source text, posts it to Kroki's `/mermaid/svg` endpoint, and
returns the SVG Kroki generates. The frontend (`renderAllDbViewsMermaid()`
in `index.html`) injects that SVG directly into the page and attaches
`svg-pan-zoom` to it for interactivity — the same as it did with mermaid.js's
own `render()` return value before this change, so nothing downstream of
"get an SVG string" changed.

**Nothing else in this app depends on Kroki.** It's scoped to exactly one
feature (the database ER-diagram view). Every other page, tab, and action
works normally if Kroki is unreachable — see "What happens when Kroki isn't
reachable" below.

## Why server-side, not client-side mermaid.js

Two independent reasons converged on this design:

1. **mermaid.js is a genuinely large dependency** (~3.4MB minified) for one
   diagram-rendering feature used on one view. Moving rendering server-side
   means the browser never downloads it at all.
2. **Consistency with the rest of the egeria-v6 checkout.** `pyegeria`
   already dropped its own hardcoded dependency on the public `kroki.io`
   service (unreliable under that service's load) in favor of the same
   shared, self-hosted `egeria-shared-kroki` container this app now uses —
   see the comment on the `kroki`/`kroki-mermaid` services in
   `egeria-workspaces-fs/compose-configs/shared-infra/shared-infra.yaml`.
   Reusing that container instead of standing up a second rendering path
   keeps one Kroki instance, one thing to keep healthy, one thing to
   document.

## Why a backend proxy, not the browser calling Kroki directly

`resource-explorer` runs as a bare `uv run resource-explorer web` host
process — it is **not** a container on the `egeria_network` docker network
the way `egeria-freshstart`/`egeria-quickstart` are. Those services reach
Kroki via its container name (`http://egeria-shared-kroki:8000`), which only
resolves *inside* that docker network. resource-explorer can't use that URL
at all; it has to reach Kroki via a published host port instead
(`http://localhost:6002` locally — see "Making Kroki reachable" below).

Given that, the browser calling Kroki directly would mean either:
- baking a specific host:port into client-side JS (breaks the moment the
  deployment topology changes, and differs per environment — dev vs.
  staging vs. prod), or
- exposing `KROKI_URL` as a value the *frontend* reads, which means every
  browser hitting this app needs direct network access to Kroki, not just
  the resource-explorer server.

Routing through a backend proxy avoids both: the frontend always calls its
own origin (`/api/diagrams/mermaid`), and only the resource-explorer server
process needs network access to wherever Kroki actually runs. This matches
every other internal-infra call in this app (Postgres, Egeria) — the
browser never talks to internal infrastructure directly, only through a
backend route.

## Configuration

One setting, `KrokiConfig` in `config.py`:

| Env var | Default | Meaning |
|---|---|---|
| `KROKI_URL` | `http://localhost:6002` | Base URL of the Kroki instance to render against |
| `KROKI_TIMEOUT_SECONDS` | `30.0` | How long to wait for a render before giving up |

Both are read once at request time (`get_config().kroki`), not cached at
import time, so a config change + process restart is all that's needed —
no code changes.

## Making Kroki reachable

### Local dev (Kroki on the same machine as resource-explorer)

This is the common case in a Trellis checkout — `egeria-shared-kroki` is
part of the same `shared-infra.yaml` compose stack as the shared Postgres
instance. It publishes a host port (mirroring the same fix
`egeria-shared-postgres` already needed for exactly this reason, publishing
`5442`) so resource-explorer can reach it without being on `egeria_network`:

```yaml
  kroki:
    ports:
      - "6002:8000"
    # ...
```

That's `6002` on the host, not Kroki's own default `8000` — `8000` collides
with common local dev servers (`mkdocs serve`, etc.) that a Trellis checkout
is likely to also have running. Container-internal port stays `8000`
(unaffected — that's what the container-name URL above uses). If this
mapping is ever missing (e.g. an older shared-infra checkout), add it and
apply with (from `egeria-workspaces-fs/compose-configs/shared-infra/`):

```bash
docker compose -p egeria-shared-infra -f shared-infra.yaml up -d kroki
```

Then the default `KROKI_URL=http://localhost:6002` just works. Verify with:

```bash
curl http://localhost:6002/health
```

### Remote (Kroki runs where Egeria runs, on a different host)

Not every deployment runs resource-explorer on the same box as Egeria's
shared infra — a common shape is Egeria (and its `egeria-shared-kroki`
sidecar) running on a server, with resource-explorer running elsewhere
(a different host, a laptop against a shared dev environment, etc.). In
that case:

1. Make sure the remote host's Kroki container has a **published** port
   (same `ports:` mapping as above, `6002:8000`) reachable from wherever
   resource-explorer runs — not just from that host's own docker network.
2. Set `KROKI_URL` to that host's address, e.g.
   `KROKI_URL=http://egeria-host.internal:6002`.
3. **Kroki has no built-in authentication.** Exposing it to a wider network
   than "the same machine" means it's reachable by anyone who can reach that
   host:port. Put it behind a firewall, VPN, or reverse proxy that adds
   auth — don't publish it directly to the open internet. This is the same
   caution that already applies to Egeria's own platform URL in a remote
   deployment; Kroki doesn't change that risk profile, it's just one more
   service sharing it.
4. Consider raising `KROKI_TIMEOUT_SECONDS` if the remote host is on a
   slower/higher-latency network than local dev — the default (30s) already
   assumes some real network latency, not just render time, but a
   significantly slower link may need more headroom.

No code changes are needed for either case — `diagrams.py` never assumes
`localhost`, it always reads `KROKI_URL` from config.

## What happens when Kroki isn't reachable

`POST /api/diagrams/mermaid` returns **HTTP 502** with a message naming the
URL it tried to reach (`Could not reach Kroki at {url}: {error}`) — not a
500, and not a silent failure. The frontend catches this per-diagram: the
one ER-diagram panel that failed shows a red error message inline
(`Could not render lineage diagram: ...`); every other diagram, tab, and
feature on the page is unaffected.

**There is deliberately no client-side fallback.** `pyegeria`'s own
`render_mermaid()` falls back to rendering in the browser if its configured
Kroki container is absent — a reasonable choice there, since it already
had a client-side mermaid.js path to fall back to. resource-explorer
doesn't: mermaid.js was removed from the client bundle entirely as part of
this change (see `frontend-build/README.md`), specifically to avoid
shipping ~3.4MB to every page load for a single-view feature. Rebuilding a
fallback would mean re-vendoring mermaid.js just to cover Kroki being
temporarily down — undoing the size win for a failure mode that surfaces
clearly and doesn't block anything else. If Kroki's reliability in your
deployment turns out to be worse than this tradeoff assumes, that's worth
revisiting explicitly (re-add a vendored mermaid.js fallback), not silently
working around.

## Troubleshooting

- **502 "Could not reach Kroki at ..."** — Kroki isn't running, isn't
  reachable from resource-explorer's host, or `KROKI_URL` is wrong. Check
  `docker ps --filter name=kroki` (is the container up and healthy?) and
  `curl {KROKI_URL}/health` from the same machine resource-explorer runs on
  — not from your own laptop if resource-explorer runs somewhere else.
- **502 "Kroki returned 4xx/5xx"** — Kroki is reachable but rejected the
  diagram source itself (a real mermaid syntax problem, not a connectivity
  one). The error body is included (truncated to 500 chars) in the detail
  message.
- **Diagram renders but looks unstyled/wrong theme** — the dark-theme
  styling is applied via a `%%{init: {"theme":"dark", ...}}%%` directive
  prepended to the mermaid source before it's sent to Kroki (see
  `renderAllDbViewsMermaid()`), not via any Kroki-side config. If a new
  Kroki/mermaid version changes how `%%{init}%%` is parsed, that's the
  place to look.
