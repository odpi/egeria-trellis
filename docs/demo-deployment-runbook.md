# Demo deployment runbook — trellis on a Linux box with a GPU

> The general guide for all three configurations (demo, Mac developer, Linux developer) is
> [deploying-trellis-with-quickstart.md](deploying-trellis-with-quickstart.md); this file is the
> verbatim trevor sequence it refers to.

What worked on trevor (Pop!_OS 24.04, i7-10700K, RTX 2070 SUPER) on 2026-09-04, written so it can be
repeated. It is the plan's **demo profile** (`docs/runtime-architecture-plan.md` §1) with one variation
for a box whose GPU is used by a **host-native Ollama**: the apps run as containers, inference runs on the
host. The dev profile on a laptop is at the end.

## 0. Identities and secrets used

Nothing here comes from another machine. All identities are the quickstart's own demo users, defined in
`compose-configs/egeria-quickstart/secrets/coco-user-directory.omsecrets` (password `secret` for all):

| Purpose | User |
|---|---|
| RE and EA service account (`EGERIA_USER` / `EGERIA_USER_PASSWORD` in the trellis `.env`), the worker role's own Egeria identity | `erinoverview` |
| Signed-in surveys and CLI login in the verification below | `erinoverview` (any demo user works) |
| EA login in the verification below | `peterprofile` |
| Platform-services reads (origin, server status) | `garygeeke` |

Secrets the trellis `.env` needs, and where each comes from:

| Key | Source |
|---|---|
| `ADVISOR_PORTAL_SECRET` | the same value as the quickstart's `EGERIA_ADVISOR_SSO_SECRET` (its `.env`), so the Portal can hand a signed-in user to EA |
| `PGVECTOR_PASSWORD` | the `egeria_advisor` role's password from `shared-infra/docker-entrypoint-initdb.d/init_egeria.sql` (default `advisor`) |
| `TRELLIS_JWT_SECRET` | generate once per box: `openssl rand -hex 32`; every trellis container must share it |
| `GITHUB_TOKEN` | a GitHub token for Resource Explorer's surveys; without it GitHub's 60 calls/hour are gone in one survey |
| `TRELLIS_MODEL_TIER` | `demo-gpu` on a GPU box, `demo-cpu` otherwise |

## 1. Host prerequisites (sudo, once)

- Native Docker Engine, not Docker Desktop for Linux: Desktop runs the engine in a VM that cannot see the
  GPU, and starting it flips the active `docker context` so every command silently talks to the VM.
  `sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`,
  `sudo systemctl enable --now docker`, `sudo usermod -aG docker $USER`, then re-login. If Desktop is
  installed, `systemctl --user mask docker-desktop` and always `docker context use default`.
  **mask, not disable** — `disable` only prevents systemd auto-start and does not stop an
  interactive launch. On trevor 2026-09-07 Desktop was `disabled`, `AutoStart: false`, with no XDG
  autostart entry, and was running regardless; it had flipped the context and misdirected a build.
  `mask` makes a start attempt fail with `Unit docker-desktop.service is masked`.
- NVIDIA: the proprietary driver (Pop!_OS: `system76-driver-nvidia`) plus `nvidia-container-toolkit`
  and `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker`.
  `docker run --rm --gpus all ubuntu:22.04 nvidia-smi -L` must list the card.
- Ollama on the host, listening on all interfaces so containers can reach it. Releases are `.tar.zst`
  now: `curl -fL -o o.tar.zst https://github.com/ollama/ollama/releases/download/<ver>/ollama-linux-amd64.tar.zst`,
  extract under `~/ollama`, and run it as a user service with `OLLAMA_HOST=0.0.0.0:11434`
  (see `~/.config/systemd/user/ollama.service` on trevor). Pull `llama3.1:8b`.
- `git`, and Tailscale if the box is driven remotely.

## 2. Check out the two repos

```bash
mkdir -p ~/localGit/trellis-demo && cd ~/localGit/trellis-demo
git clone --branch main https://github.com/dwolfson/egeria-workspaces.git
git clone --branch re/docs-consolidation-part-2 https://github.com/dwolfson/trellis.git   # until merged
```

Keep them separate from any existing live checkout on the box; that one stays untouched.

## 3. Build the trellis images on the box

```bash
cd ~/localGit/trellis-demo/trellis && make images     # trellis/resource-explorer:local, trellis/egeria-advisor:local
```

About six minutes with a warm cache. CI publishes multi-arch images to ghcr on merge to main; until then
build locally.

## 4. Egeria: shared infrastructure and the quickstart

```bash
cd ~/localGit/trellis-demo/egeria-workspaces
docker network create egeria_network                      # declared external everywhere, created nowhere
cp <your quickstart .env> compose-configs/egeria-quickstart/.env
cp <your shared-infra .env> compose-configs/shared-infra/.env
```

In `compose-configs/shared-infra/.env`: `HARDENED_KAFKA_DATA_DIR` is a **host** path, must exist and be
writable by uid 65532 (`chmod 0777` on a demo box); `HARDENED_KAFKA_LOG_DIR` is the **in-container**
path `/var/lib/kafka-data/kraft-logs`. Then:

```bash
./quick-start-local --skip-unity-catalog < /dev/null
```

It builds the platform and portal images, brings up shared-infra under project `egeria-shared-infra` and
the quickstart under `egeria-quickstart`, copies server configs and secrets into `runtime-volumes/`.
Verify each of `qs-metadata-store qs-engine-host qs-integration-daemon qs-view-server qs-nanny-daemon`
has `runtime-volumes/quickstart-platform-data/data/servers/<name>/config/<name>.config`; on trevor the
copy step did not run and the platform restart-looped with `OMAG-ADMIN-400-011` until the tracked files
under `compose-configs/egeria-quickstart/servers/qs-*/config/` were copied by hand. Wait for
`OMAG Server 'qs-view-server' successful start` in `docker logs quickstart-egeria-main`.

## 5. trellis runtime

```bash
cd compose-configs/optional-associated-runtimes/trellis
cp .env.example .env && chmod 600 .env        # fill the keys from section 0
docker exec -e PGPASSWORD=<postgres pw> egeria-shared-postgres psql -h localhost -p 5442 -U postgres -d egeria_advisor \
  -c 'CREATE SCHEMA IF NOT EXISTS resource_explorer AUTHORIZATION egeria_advisor'   # not needed from commit 8671242 on
docker compose -p trellis -f docker-compose.yaml -f docker-compose.ollama-host.yaml --env-file .env up -d
```

Never include `shared-infra.yaml` in this command and always pass `--env-file .env`: compose reads the
`.env` next to the *first* file it is given. For a box that should run Ollama in a container instead,
use `../ollama/docker-compose.yaml -f docker-compose.ollama-container.yaml [-f docker-compose.ollama-nvidia.yaml | -f docker-compose.ollama-rocm.yaml]`
in place of the host overlay.

## 6. Verify

```bash
curl -s localhost:8810/health/ready; curl -s localhost:8880/health          # both ok
curl -s -o /dev/null -w '%{http_code}\n' localhost:8810/api/projects/         # 401: login is required
docker logs trellis-re-worker | grep 'worker loop started'                     # three loops, leader=true
docker logs trellis-ea-web | grep 'Initialized Ollama client'                  # host.docker.internal:11434
C='docker compose -p trellis -f docker-compose.yaml -f docker-compose.ollama-host.yaml --env-file .env'
$C run --rm --no-deps trellis-re-web cli add https://github.com/odpi/egeria-python.git --yes   # slug egeria_python_git
echo secret | $C run --rm --no-deps -T --entrypoint sh trellis-re-web -c \
  'resource-explorer login --user erinoverview; resource-explorer survey egeria_python_git --publish'
$C run --rm --no-deps -T trellis-re-web cli ask "What does pyegeria provide?" --project egeria_python_git
docker exec -w /app/packages/egeria-advisor trellis-ea-web python scripts/clone_repos.py --phase 1
docker exec -w /app/packages/egeria-advisor trellis-ea-web python scripts/ingest_collections.py --phase 1
```

Verified on trevor 2026-09-04 21:19 (the zone shown was the then-default draft zone, retired 2026-10-08; RE now imposes none unless `EXPLORER_DRAFT_ZONE` is set): the asset (`SourceControlLibrary`) and the `SurveyReport` both read back
with `createdBy=erinoverview`, `Ownership.owner=erinoverview`, `ZoneMembership=['resource-explorer-draft']`
via `MetadataExpert.get_metadata_element_by_guid` as the same user. The survey CLI ends with an interactive
"Trigger governance action process?" prompt; under `-T` it aborts harmlessly after the publish. Do **not** send
`SIGUSR1` to a `survey` run for a thread dump: only `web`, `worker` and `serve` install the handler,
a plain CLI command dies.

## 7. Known issues on a fresh box

- Egeria: `qs-engine-host` retries `startMissedEngineActions` forever on a fresh repository, refused as
  `generalnpa` on one `DigitalProductFamily` element (egeria-python `PYEGERIA_ISSUES.md` ISSUE-90).
  Costs several cores; Egeria-side.
- `jdbcMaximumPoolSize` in the `.http` config builders only reaches the platform when those builders
  are run against it.
- ~~EA phase-1 ingestion: `pyegeria` and `pyegeria_cli` collections ingest zero files~~ — fixed.
  Measured on pop-os 2026-09-06 with pyegeria 6.1.10: phase 1 ingests 431 files / 8,779 chunks
  (`pyegeria` 281 files / 7,737 chunks, `pyegeria_cli` 100 / 554, `pyegeria_drE` 50 / 488).
- Platform addressing: the trellis containers reach Egeria as `https://host.docker.internal:9443`,
  not by its `egeria_network` name, because the platform image's baked-in certificate names only
  `localhost` and `host.docker.internal`. Switching to the container name would only work with TLS
  verification off. Decision 2026-09-05: deferred; a SAN request is filed with Egeria alongside the
  token-lifetime request. The intra-platform round trip that caused the `HttpConnectTimeoutException`
  bursts was fixed separately in egeria-workspaces (#482, `egeriaLocalEndpoint`), so nothing on the
  trellis side is waiting on this.
- pyegeria's stdio MCP server needs `mcp>=2.0` but declares `mcp>=0.1` (ISSUE-91); trellis pins
  `mcp>=2.0.0` since 8441efb. Symptom if it regresses: "No response from MCP server" at EA start.
  The flip side: agentstack-sdk (RE's A2A role) still imports mcp 1.x's `streamablehttp_client`, so
  `resource_explorer/_mcp_compat.py` installs that name over mcp 2's `streamable_http_client` and must
  be imported before any `agentstack_sdk` import. Drop the shim once agentstack-sdk supports mcp 2.
- pyegeria floor is 6.1.10 in all three packages (ISSUE-86 token support; report runs are attributed to
  the signed-in user). Keep the quickstart's pyegeria-web/jupyter images on the same version.

## Docker Desktop variant (Linux or Mac), for visibility

The same compose files run under Docker Desktop; what changes is where inference runs and which engine
the `docker` command talks to.

- **Two engines, one CLI.** Starting Docker Desktop makes `desktop-linux` the active context, so every
  `docker`/`docker compose` command silently addresses the Desktop VM until you switch back. Decide which
  engine hosts the stack and pin it: `docker context use desktop-linux` for Desktop, `docker context use
  default` for the native engine, and check `docker context show` before anything else when both are
  installed. On 2026-09-04 a briefly started Desktop made the native stack look as if it had been replaced.
  On 2026-09-07 the same thing happened again on trevor *without anyone starting Desktop by hand* — it is
  installed but not systemd-enabled, and the desktop session launched it, flipping the context mid-task.
  Note the failure mode: the wrong engine reports **emptiness, not an error**. `docker images` lists
  nothing and `docker inspect <container>` answers `no such object` for a container that is up and
  healthy on the other engine, so any check reading that output passes or fails for the wrong reason.
  `docker context use default` does not protect a script against a flip that happens after it runs —
  force it per-process with `export DOCKER_CONTEXT=default`. The worst form, seen on trevor earlier the
  same day, is both engines running shared-infra at once with the demo's data split across them and only
  one holding the published ports; `docker ps` looks normal in either context.
- **No GPU inside the Desktop VM** (Linux and Mac alike), so inference stays on the host: native Ollama
  listening on `0.0.0.0:11434` and the `docker-compose.ollama-host.yaml` overlay. `host.docker.internal`
  resolves inside Desktop containers without `extra_hosts`; the overlay sets it anyway.
- **VM sizing.** Desktop's VM gets a fraction of the host by default (trevor's showed 12 CPUs / 32 GB);
  the Egeria demo core needs about 8 GB and the two trellis apps another 5 GB, so give the VM at least
  24 GB and most of the cores in Desktop's settings. CPU-only work inside the VM is slower than native;
  a CPU inference probe that took 2 minutes natively did not finish in 40 inside the VM.
- **Volumes and data.** Named volumes and the Postgres data live inside the VM, not in the host's
  `/var/lib/docker`; a stack built on the native engine and one built under Desktop do not share images,
  volumes or networks. Bind mounts (`runtime-volumes/`, the Kafka data dir) are host paths and work the
  same, with the same uid 65532 write requirement for Kafka.
- **Ports** are published through Desktop's proxy on the host; the native engine binds them directly.
  Running both engines with the same published ports at once fails on the second one to start.
- Everything else, sections 2 to 7, is identical: same repos, same `make images`, same network creation,
  same `./quick-start-local`, same trellis overlay command with `--env-file .env`, same verification.

## Dev profile on a laptop

Infra and Egeria in Docker as today (`shared-infra` + quickstart), Ollama native, apps native:

```bash
make dev                       # RE web on 8810 with the worker embedded, EA web on 8880
```

Login is now required by both apps: sign in with a quickstart user in the browser, or
`resource-explorer login` / `egeria-advisor login` for the CLI (token cached under
`~/.config/trellis/<app>/session.json`, one hour). Set `RE_JWT_SECRET`/`ADVISOR_JWT_SECRET` (or one
`TRELLIS_JWT_SECRET`) in the app `.env` files so sessions survive a restart; `TRELLIS_ANONYMOUS_READ=true`
is the dev-only override that lets GETs through without a login. Model tier: `EXPLORER_MODEL_TIER` /
`ADVISOR_MODEL_TIER=dev` (the default). A web server started before today's commits does not have any
of this; restart it.
