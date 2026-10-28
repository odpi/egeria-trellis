# Runtime architecture plan — topology, process roles, CLI, multi-user, LLM backend

**Status: planned, not yet built. Revision 2, 2026-09-04.** Revision 1 (Opus design pass,
grounded by a Sonnet research pass, same day) settled threading, containerization, multi-user
and the vLLM question against one machine. Revision 2 (Fable, same day, with the project owner)
keeps that reasoning and adds what it did not account for: a CLI path that stays first-class,
four target environments with different hardware, explicit process management, and an
isolation model for multi-user. Measurements in this revision were taken live on Dev 1 and are
labelled as such; numbers for the Linux boxes are extrapolations until measured there.

## Context

Triggered by a real stuck-server incident on 2026-09-03/04 (RE's `resolve_question_guid()` hung
15+ seconds inside a `ThreadPoolExecutor` worker calling pyegeria's `get_guid_for_name()` via
`nest_asyncio.run_until_complete`, while a direct curl to the same Egeria platform's own origin
endpoint returned in 18ms — contained with a per-call timeout, root cause deliberately left open
for this pass). The project owner asked for this to be planned together with containerization,
the multi-user transition, and whether to move to vLLM, rather than solved piecemeal.

Revision 2 adds two requirements and one exhibit:

- **An A2A entry point for other systems** becomes a named role rather than an unauthenticated
  side server (§2).
- **A CLI path stays.** Both apps must remain usable without the web server, including
  interactive ask/chat, with reduced capability accepted where the web tier is the only sensible
  host (see §3).
- **Four target environments**, with different hardware and different purposes (see §1).
- **Process management is itself a goal.** While gathering actuals for this revision, thirteen
  orphaned Prefect ephemeral API servers were found on Dev 1, some four days old, each spawned
  in-process by the Prefect client because `PREFECT_ENABLED` defaults to `True` and the fallback
  path when no API is reachable starts an ephemeral server that nothing shuts down. They are
  gone now; the pattern is the point. "One process, N ad hoc threads, plus whatever libraries
  spawn on their own" is the thing this plan replaces.

## Target environments and what was measured

| | Dev 1 | Dev 2 | Demo 1 "cray" | Demo 2 |
|---|---|---|---|---|
| Machine | MacBook Pro M3 Max, 96 GB | Framework 13, Ryzen AI 9 HX 370, 64 GB | Ryzen 9 3900X, 64 GB | Intel i7-10700K, 16 threads, 62 GiB usable; hostname `trevor`, Pop!_OS, Tailscale SSH (verified 2026-09-04) |
| OS | macOS | Linux, kernel 7.0, 24 threads, 61 GiB usable; hostname `hedwig`, reachable over Tailscale | Linux | Linux |
| GPU | Metal, not passed into Docker | Radeon 890M iGPU; ROCm 7.2 installed, Ollama 0.24 native with `HSA_OVERRIDE_GFX_VERSION=11.0.0` (verified 2026-09-04) | none | **RTX 2070 SUPER, 8 GB VRAM**, currently on the open-source `nouveau` driver so unusable for inference until the proprietary driver and `nvidia-container-toolkit` are installed |
| Storage | — | — | >10 TB free | >10 TB free (system NVMe 778 GB free) |
| Role | dev | dev, or secondary LLM demo box (iGPU: 22 s TTFT) | demo, browser-based portal, no LLM-interactive parts | **primary LLM-interactive demo box** (CUDA: 3 s TTFT); NVIDIA driver installed 2026-09-04; needs native Docker Engine in place of Docker Desktop before the containerized profile can use the GPU |

**Actuals on Dev 1, 2026-09-04.** Docker Desktop is allotted 8 CPUs and 45 GiB. Container
memory in use across six compose projects: 12.9 GiB, of which the Egeria demo core that a demo
box must carry is about 8.2 GiB: `egeria-quickstart` 4.5 GiB (egeria-main 3.4 GiB under a 6 GiB
cap, 4 CPUs, JVM heap 1.6 GiB by the default 25% rule) plus `egeria-shared-infra` 3.7 GiB (Kafka
2.55 GiB, Postgres 0.6 GiB, Kroki 0.23 GiB). Egeria is not a passive store: egeria-main was at
68% of its 4 CPUs with no user activity, because the platform runs its own discovery, watchdog
and governance engines continuously. Any CPU-only inference on the same box competes with that.

Native Ollama, llama3.1:8b, Metal, realistic RAG-shaped prompt of 5.3k tokens: prefill 467
tok/s, generation 41 tok/s, 11 s to first token. **Trellis sets no context window in either
app**, so Ollama loaded the same model at its full 131k context and took 22 GB; reloaded at 8k it
takes 5.7 GB. On a 64 GB box the uncapped default is a third of RAM for one 8B model.

**Extrapolated for the two old demo boxes** (dual-channel DDR4, AVX2, no GPU; generation is
bandwidth-bound and prefill compute-bound): prefill 30 to 50 tok/s, generation 4 to 6 tok/s,
100 to 180 s to first token on a 5k prompt, roughly double that for the 13B code model. Memory is
not the constraint; time-to-first-token is. **This must be measured on a CPU-only box before the
demo profile is finalised** (step 1 of the sequencing; not on cray, which is live). Until then the working assumption, agreed with
the owner, is: the old boxes demo Egeria plus the non-LLM Resource Explorer paths (survey,
publish, curate, reports); Dev 2 is the demo machine when EA chat or RE ask is the point.

**Measured on hedwig (Dev 2), 2026-09-04, native Ollama 0.24, llama3.1:8b, 5.3k-token prompt,
8k context, Egeria quickstart running alongside at load average 1.8.** Cold run each, because a
repeated identical prompt hits Ollama's prompt cache and reports 60k tok/s prefill, which is not a
measurement (the probe script now varies the prompt per run).

| | 890M via ROCm | CPU only (`num_gpu: 0`) |
|---|---|---|
| Prefill | 278 tok/s | 31 tok/s |
| Generation | 12.5 tok/s | 9.8 tok/s |
| Time to first token, 5.3k prompt | 22.5 s | 175 s |
| Loaded footprint at 8k context | 6.3 GB, VRAM 84% allocated | 5.7 GB |

**Measured on trevor (Demo 2), 2026-09-04, Ollama 0.33.3 run natively from a user-space tarball
(Docker Desktop's VM cannot see the GPU), same prompt shape, 8k context, machine otherwise idle:**

| | RTX 2070 SUPER via CUDA | CPU only (`num_gpu: 0`) |
|---|---|---|
| Prefill | 1778 tok/s | 47 tok/s |
| Generation | 67 tok/s | 3.9 tok/s |
| Time to first token, 5.3k prompt | 3.0 s | 114 s |
| Loaded footprint at 8k context | 5.8 GB of 8 GB VRAM, GPU at 99% | 6.2 GB RAM |

Two corrections to what this document said before the number existed: trevor's CPU prefill (47
tok/s, AVX2 at desktop clocks) beats hedwig's CPU (31 tok/s), so the "old x86 boxes are slower
than the Framework on CPU" claim was wrong for prefill and right only for generation (3.9 vs 9.8
tok/s, DDR4 vs LPDDR5X bandwidth). And the discrete GPU makes trevor the best interactive LLM
demo box of the Linux set by a wide margin: 3 s to first token where hedwig's iGPU needs 22 s.
An 8 GB card holds the 8B model at 8k context with 2 GB to spare; codellama:13b (7.4 GB) will not
sit beside it, so on trevor the `demo-gpu` tier should keep every slot on the 8B model or accept
a model swap per slot change (seconds, not minutes).

Three conclusions:

- **The iGPU is a prefill engine, not a generation engine.** Nine times faster prefill, but
  generation is within 30% of the CPU because both share the same LPDDR5X bandwidth. For
  RAG-shaped traffic, where prefill dominates, that is the number that matters, and it makes
  hedwig a usable interactive demo box: 22 s to first token on a large prompt, then a readable
  stream.
- **The revision-1 extrapolation for the old boxes was optimistic.** A Zen 5 HX 370 with
  LPDDR5X-7500 needs 175 s on CPU; a Zen 2 3900X or a 10th-gen i7 on dual-channel DDR4 will be
  slower, plausibly 4 to 6 minutes to first token on the same prompt. **The old boxes are not
  interactive-LLM demo machines** for the current prompt sizes; they remain fine for Egeria and
  the non-LLM Resource Explorer paths. Measure Demo 2 only if that conclusion needs a number.
- **Prompt size is the lever, more than model size.** Time to first token scales linearly with
  prompt tokens. A per-tier RAG context budget (e.g. 2k tokens for the demo tiers instead of
  5k+) cuts hedwig to about 8 s and even the old boxes to about 2 minutes. That belongs in §5's
  tier config next to the model and the context cap: **a context budget per tier**, applied by the
  retrieval layer, not just a `num_ctx` ceiling.

The Mac run with `num_gpu: 0` from earlier the same day (7 tok/s prefill) is discarded: the
machine was under a load average of 23 to 64 at the time and NEON-vs-AVX2 is not comparable
anyway. The same observation stands as a §1 data point: at rest, Egeria's own repository
connector was the largest CPU consumer on Dev 1, and a demo box running CPU inference shares that.

Disk for a demo box: about 20 GB of images for the demo profile without Jupyter (the Jupyter
image alone is 7 GB), plus 5 GB per 8B model and 7.4 GB for codellama:13b, plus the trellis
images. Dev 1 holds 166 GB of images of which 100 GB is reclaimable; demo boxes get a curated
image list, not a copy. With >10 TB free they can also hold the full model library and the RAG
corpora, which makes them the natural place to keep a complete offline demo.

---

## 1. Process topology: profiles, not one decision

Revision 1 recommended one topology, native inference plus containerized apps, justified by the
Metal-passthrough wall on Dev 1. That wall is real (verified live; Docker's own Model Runner
works around it the same way) but it exists on exactly one of the four machines. On Linux,
containerized Ollama with a GPU is routine (NVIDIA via the container toolkit, AMD via `/dev/kfd`
and `/dev/dri`). So the recommendation becomes **two profiles, both overlays on
`egeria-workspaces` `shared-infra`**, with inference always a URL in config, which
`LLMConfig`/`OllamaConfig` already support.

**dev profile** (Dev 1, Dev 2):
- Infra and Egeria in Docker, as today: `egeria-quickstart` + `shared-infra` (Postgres/pgvector,
  Kroki, Kafka), optional Prefect.
- Trellis apps native via `uv run`, with reload. Containerizing the app inside the dev loop is
  friction with no payoff and is dropped from the dev profile.
- Inference native on Dev 1 (Metal). On Dev 2, native or the ROCm container variant, whichever
  the spike (below) shows works.
- One process runs the web role with the worker role embedded (§2), so `make dev` stays one
  command.

**demo profile** (Demo 1, Demo 2, and Dev 2 when acting as a demo box):
- Everything in compose, trellis apps included, as a new
  `optional-associated-runtimes/trellis` runtime in `egeria-workspaces`, the same way Prefect
  and Ollama live there. One command up. Same image as dev, arm64 and amd64.
- Web role at N workers, worker role at one replica, Ollama in a container: CPU variant on the
  old boxes, ROCm variant on Dev 2. The existing `optional-associated-runtimes/ollama` compose is
  CPU-only with no GPU stanza; the GPU variant is new work in `egeria-workspaces`.
- Model tier and context cap pinned per box (§5).

**Future profile, recorded not designed: edge.** A "skinny" Egeria deployed beside trellis on a
demo or edge box, federated with a "mother ship" Egeria elsewhere (owner, 2026-09-04). For this
plan it means two things only: identity comes from whichever view server trellis-auth is pointed
at, so the login policy must not assume the identity provider is the same platform the data lives
in; and everything trellis publishes must carry enough provenance (`Ownership`, zones, the
integration identity) to survive federation, which is Egeria's cohort/federation machinery, not
trellis work. Topology details wait until a skinny profile actually exists.

**Rejected: one fixed topology for all four machines.** Revision 1's hybrid is the dev profile
on Dev 1 and nothing else.

**Rejected: stay 100% bare-metal, and containerize-everything-on-the-Mac.** As in revision 1.

**Docker Desktop for Linux cannot host the demo profile on a GPU box.** Found on trevor
2026-09-04: Docker Desktop for Linux runs the engine inside a qemu VM (`qemu-system-x86` was at
nine cores while idle containers ran), the VM has no access to the host GPU, and `--gpus` is
rejected even with `nvidia-container-toolkit` installed on the host. A CPU probe run inside that
VM crawled for forty minutes without finishing. The demo profile on trevor, and on any Linux box
that should use its GPU, needs **native Docker Engine** (`docker-ce` from Docker's apt repo, or
the distro package) with the NVIDIA runtime configured via `nvidia-ctk runtime configure`. hedwig
already runs the quickstart on a native engine. Docker Desktop is fine on the Mac, where inference
is native anyway.

**ROCm on the 890M: already working natively, container variant still a spike.** Verified on
hedwig 2026-09-04: ROCm 7.2, `/dev/kfd` and `/dev/dri/renderD128` present, and the native Ollama
systemd unit already carries `HSA_OVERRIDE_GFX_VERSION=11.0.0`, `ROCR_VISIBLE_DEVICES=0`,
`HIP_VISIBLE_DEVICES=0` and the ROCm library path. That is the exact override this plan guessed
at, and it is the configuration the containerized `ollama/ollama:rocm` variant must reproduce
(same env, plus `--device /dev/kfd --device /dev/dri` and the `video`/`render` groups). hedwig
also runs the full Egeria quickstart today at a load average under 2, so it is a working
single-box demo host already; the demo profile packages what it does by hand.

### Why this interacts with threading and multi-user

Unchanged from revision 1: moving from one process to N worker processes turns the three daemon
threads into a "run in exactly one place" problem and turns per-request survey threads into
something the OS process boundary handles. The stuck-server incident still needs its direct fix
(one bounded shared `ThreadPoolExecutor` per process for sync-pyegeria bridging, per-call timeout
kept, pyegeria thread-safety verified by a spike). Revision 2 makes "exactly one place" a named
role rather than a lock held by whichever web worker won.

---

## 2. Process roles

**Recommendation: five roles on one core and one image: `web`, `worker`, `cli`, `tui`, `a2a`.**

Today every background loop (scheduler, bootstrap monitor, Egeria resync, outbox drain, orphaned-
run reconciliation, survey-definition cache warm) starts inside the FastAPI lifespan in
`resource_explorer/web/app.py:71-105`. There is no way to run them without the web server, and
with N uvicorn workers they would run N times.

- **`worker`** owns the background loops. `resource-explorer worker` (new command). In the demo
  profile it is one compose service at one replica. It also owns long-running survey and analysis
  runs, pulled from a queue in Postgres (a `runs` table with `claimed_by`, `heartbeat_at`, which
  is what `run_reconciler.py`'s pid-ownership check already approximates), so a web request
  enqueues and returns rather than spawning a thread.
- **`web`** serves requests at N uvicorn workers and never spawns threads for work that outlives
  the request. `--embed-worker` runs the worker role in-process for the dev profile.
- **`cli`** is the existing Typer/Click CLIs, at full core capability (§3).
- **`tui`** is the existing Textual app, unchanged.
- **`a2a`** is the entry point for other systems. **Done for RE, 2026-09-04.** It was
  `agentstack_server.py` running each specialist agent (orchestrator, stats, code, docs, health,
  compare, integration) as its own A2A endpoint on ports 8080 to 8086 via `resource-explorer
  serve`, with **no authentication at all** and a one-port-per-agent layout that was awkward in
  compose. It is now a role: `resource-explorer serve --port 8090` is **one service on one port**,
  all seven agents routed by path (`/agents/<name>`, orchestrator also at `/`), with **a bearer
  token required on every agent call** — either a trellis app JWT or a raw Egeria bearer token
  (§4), the latter validated once against the view server and cached for the token's own lifetime,
  so an external orchestrator acts as a real Egeria identity. An A2A agent card is published per
  agent at the SDK's well-known path, plus a `/.well-known/agents.json` index listing all seven
  with their URLs and the required auth scheme. `A2A_ALLOW_ANONYMOUS=true` keeps the old
  behaviour for local dev and warns at startup.
  (`resource_explorer/a2a_role.py`, `a2a_auth.py`,
  `packages/resource-explorer/docs/a2a.md`.) Two things this did *not* settle and one it added:
  the agentstack SDK hosts exactly one agent per `Server`, so the role mounts each agent's
  `create_app()` FastAPI app under a path prefix on one root rather than using an SDK multi-agent
  mechanism, which does not exist; the caller's token reaches agent code through a ContextVar but
  RE's own pyegeria client construction sites are not yet converted, so Egeria writes are still
  attributed to the service account until RE-wide `trellis-auth` adoption lands.
  **EA's A2A surface is still open** — it has none, and it should get one on the same shape,
  exposing its report and governance-plan agents. MCP stays what it is (EA's tool surface for LLM
  clients); A2A is the agent-to-agent surface. Registering the cards with the Portal is also still
  open; the URL the Portal would fetch is `<app URL>/.well-known/agents.json`, the same shape as
  its existing `EGERIA_ADVISOR_URL` discovery.
- Leader election via `pg_try_advisory_lock` stays as cheap insurance so that two workers, or a
  worker plus an embedded one, cannot both fire schedules.

**Process management rules that come with the roles**, each one motivated by something found
on Dev 1 this week:
- No library may start a server on its own. `PREFECT_ENABLED` defaults to `False`;
  `PREFECT_SERVER_EPHEMERAL_ENABLED=false` is set in every profile; Prefect is enabled only
  where a compose service provides it. (Thirteen orphaned ephemeral servers.)
- Every long-running unit of work has a row: who owns it, when it last heartbeated, how to kill
  it. (The reconciler exists because none of this is recorded today.)
- Every process answers `SIGUSR1` with a thread dump and shuts down within a bound. (Added
  during the incident; now a rule, not a patch.)
- `make ps` lists every trellis-owned process and container with role, pid, age and port, so
  "what is running" is one command. (Took four tool calls to find the orphans.)

---

## 3. CLI path

**Finding: the core is already separable.** RE has a 46-command Typer CLI plus a Textual TUI;
EA has a Click CLI with one-shot query, REPL and agent REPL, plus a `plans` group. None of them
import FastAPI. `cli/main.py:1085` and `web/routes/survey_definitions.py:511` call the same
`run_survey_definition`. Interactive ask and chat already work from the CLI in both apps.

**What was web-only, and has moved into core so the CLI gets it** (all four **done**, RE step 2b,
2026-09-04 — `resource_explorer/workflows/`, five modules, no FastAPI):
- ~~The analysis-run-and-auto-publish workflow, `web/routes/projects.py:632`
  (`_run_single_analysis_sync`) and `:837` (`_run_stage_batch_background`).~~ **Extracted** to
  `workflows/analysis.py`; the CLI now has `analysis run` and `analysis stage-batch`. Its own
  docstring had said it was written FastAPI-free so it could run in a thread — that same property
  is what made it callable from a Typer command and from the run queue with no change.
- ~~GitHub discovery, the whole of `web/routes/discovery.py` (`_build_query`, `_expand_org`,
  `_run_search_query`).~~ **Extracted** to `workflows/discovery.py`; CLI `discovery search` and
  `discovery expand-org`. Two shape changes were unavoidable: `async` became sync (the
  `asyncio.to_thread` hop is a property of being called from an event loop, not of the work, and
  stayed in the route), and `HTTPException` became `DiscoveryError` with a `kind` the route maps
  back onto 400/502.
- ~~Curate materialization on accepted verdicts, `web/routes/curate.py:294` and `:449`, above the
  `ComponentMaterializer` core class.~~ **Extracted** to `workflows/curate.py`; CLI `curate
  materialize`.
- ~~The background loops, via the worker role (§2).~~ **Done in step 2a**, plus the run queue in
  2b: routes enqueue onto a `runs` table and return, and a `worker` claims and executes. The
  scouting scan (`projects.py:366`) and the Survey Definition run were extracted alongside the
  three above, since the queue needed one definition of each.

The CLI also gained `runs list|show|cancel|enqueue` so an operator can drive that queue from a
shell. Every workflow command runs inline by default; `--queue` hands it to a worker instead.

**Postgres is the one hard dependency of every path.** `ProjectRegistry.__init__` connects and
runs DDL on construction (`registry.py:371-415`). The SQLite fallback that still exists there is
**retired**: the owner does not want two dialects with different query semantics. The CLI runs
against the same shared-infra Postgres the web tier uses, natively in the dev profile or via
`docker compose run trellis resource-explorer …` in the demo profile. "Reduced capability" for
the CLI therefore means only: no background loops unless a worker is running, and no browser-
shaped features (the curate arrow UI, dashboards). Everything the core can do, the CLI can do.

---

## 4. Multi-user: identity, attribution, partitioning, isolation

### Identity and scoping

As in revision 1: adopt `trellis-auth` in RE; implement EA's `SESSION_AND_INTERACTION_STATE.md`
two-dimension model (`user_id` persistent, `session_id` ephemeral) in EA first, then port it to
RE. Session store is Postgres-backed from the start because a request can land on any web
worker. `resource_working_set` and `entity_egeria_project_context` gain `user_id` (both already
carry comments anticipating it, `registry.py:1804`, `:4831-4835`). RE's conversation memory,
keyed by `session_id` with no owner, gains `user_id` the same way.

### Authentication: two paths, one token

There are two ways into a trellis app and both must end in the same thing: **a per-request
pyegeria client holding an Egeria bearer token for the actual user.**

- **Direct.** The app's own login form takes an Egeria user id and password, calls
  `create_egeria_bearer_token()` against the configured view server, and issues an app JWT.
- **Via the Portal.** The Portal already logs the user into Egeria and its own JWT carries
  `{sub, role, display_name, egeria_token, exp}` (`demo_auth_handler.py:202`). Its handlers
  apply that token per request with `set_bearer_token()` through a contextvar middleware
  (`egeria_auth.py`). The Portal calling a trellis app is a variation of the direct path: it
  hands over the Egeria token it already holds, and the app validates it the same way it would
  validate one it minted itself, by making a cheap authenticated call to the view server.

**Finding: the current contract between them is mismatched.** `trellis-auth`'s
`exchange_portal_token` expects `{egeria_user, egeria_password}` and its own `create_access_token`
signs the raw password into the app JWT. The Portal never issues a password-shaped token; it
issues a bearer-token-shaped one. Nothing in `egeria-workspaces` calls the password contract.
So the Portal SSO route `/api/auth/portal` in EA is wired to a payload the Portal does not
produce.

**Decision, replacing revision 1's open credential question:** the app JWT carries the Egeria
bearer token, never the password. `trellis-auth` changes to `{user_id, role, egeria_token, exp}`,
matching the Portal's shape so one contract serves both paths, and `get_egeria_credentials`
becomes `get_egeria_token`. The password exists only for the instant the direct login form
exchanges it for a token. Egeria token expiry becomes the app session bound; refresh is a
re-login, as it is in the Portal. This resolves the "should the JWT carry the raw password"
question by removing the password, and it holds on the LAN demo profile.

### Login policy: shared, and required (decided 2026-09-04)

**Both apps require login, and the policy lives in `trellis-auth`, not in each app.** Owner's
decision, superseding `trellis-auth`'s original "whether an app requires login at all is each
app's own answer." One policy object with a default of require-login, a short allowlist of public
paths (health, static assets, the login and Portal-exchange routes, the A2A agent cards and
discovery index, which must be public so a client can learn how to authenticate), and one
deployment override `TRELLIS_ANONYMOUS_READ=true`, off by default, for a dev box only. Each app
keeps a thin adapter that supplies its resolved config and nothing else.

Consequences accepted with the decision:
- **Egeria is a hard dependency** of both apps, because Egeria is the identity provider. The
  QUICKSTART's "answers questions without an Egeria platform" path is retired from the docs;
  the anonymous override is the only remaining way to run without one and is not a supported
  mode. Consistent with RE being integration-daemon-shaped.
- **Sessions are bounded by Egeria's token**: one hour by Egeria's built-in default, and every
  token dies when the platform restarts because the quickstart's `rsa.key-id` is empty (random
  signing key per restart). `application.properties` exposes no lifetime setting today. **Ask the
  Egeria project for a configurable token lifetime in `application.properties`**; until then the
  browser re-logs-in hourly (invisible inside the Portal, which re-issues) and the CLI gets a
  cached login (`resource-explorer login` / `egeria-advisor login`, token in the user config dir,
  a clear "session expired" message) rather than a per-command prompt.
- Portal users never see a second login: the Portal hands over the Egeria token it already holds.
- Background loops (worker role) keep the platform's integration identity; A2A already requires a
  token; the anonymous RAG mode in EA becomes the dev-only override.

**EA moves to multi-user on the same footing as RE**: its session store and per-user namespacing
landed 2026-09-04 (a4ef5ec, d3bf498); login enforcement via the shared policy is next; `Ownership`
on the Egeria elements EA creates when a governance plan executes follows the same rule as RE's
publish. The `EgeriaContext`/MCP report agent singletons stay deferred as before.

### Attribution: Egeria already records who did it

Revision 1 said Egeria writes go out under one service account so "nothing in Egeria records
who did it." That was wrong about Egeria and right about trellis: **Egeria records the
requesting user on every call**; the element header's provenance carries who created and
updated it. The problem is only that trellis makes every call as the service account, so what
Egeria records is `erinoverview` rather than the person. The per-request client above fixes
that with no trellis-side audit table.

Beyond provenance, Egeria models accountability explicitly and trellis should use it rather than
invent parallel columns:
- **`Ownership` classification** on any `Referenceable` (`0445`): `owner`, `ownerTypeName`
  (`ActorProfile`, `UserIdentity` or `PersonRole`), `ownerPropertyName`, and `userIds` for
  security-connector enforcement. Everything trellis publishes (SurveyReports, blueprints,
  components, governance plans, saved queries as Egeria elements) gets `Ownership` set to the
  publishing user's `UserIdentity`, or to a `GovernanceRole` where the artifact belongs to a
  role rather than a person (a curated blueprint is owned by the curator role, not the person
  who happened to click accept).
- **Ownership is curation by default.** The user who discovers, surveys and catalogs a resource
  is its owner, and ownership carries the right to curate it: accept or reject verdicts,
  promote it out of the draft zone, delete it. There is no separate global "curator" role for
  one's own resources. Delegation is re-pointing `Ownership` to another `UserIdentity` or to a
  `GovernanceRole`, which is Egeria's own mechanism, not a trellis table.
- **`GovernanceRole`** (a `PersonRole`, `domainIdentifier` for the domain) with
  `PersonRoleAppointment` is Egeria's model for curation that spans resources one does not own:
  a curator for a zone or a project. Read from Egeria role appointments, not from a trellis
  table, once the RE port lands; the Portal's `role` claim is the bridge until then.

### Partitioning: governance zones

Egeria's `GovernanceZone` (`0424`) with the `ZoneMembership` classification is the partitioning
mechanism trellis should use instead of a trellis-side tenant column on Egeria-backed data.
Zones flow with the element, a view server has `supportedZones`, `defaultZones` and
`publishZones`, and the platform's security connector enforces visibility from them. Trellis-
side data that never reaches Egeria (working sets, conversation memory, drafts) is scoped by
`user_id` as above; anything published carries `ZoneMembership`.

**Decided 2026-09-04 (owner): one zone per app, refine later if needed.** *Superseded for Resource Explorer 2026-10-08 (owner ruling: DRAFT is a content status, not a zone): RE imposes no zone on published assets unless `EXPLORER_DRAFT_ZONE` is configured; private-investigation work is zoned `[resource-explorer-private, <owner>]`; see `packages/resource-explorer/resource_explorer/egeria_identity.py`.* One zone per trellis app for elements in flight
(`resource-explorer-draft`, `egeria-advisor-draft`) that only the publishing user and curators
see, and promotion into the deployment's normal `publishZones` on acceptance, so "curate" has a
zone transition as its Egeria-visible effect. Per-user zones are possible but multiply fast;
per-project zones (one per RE registered project) may be the better grain if visibility needs
to be limited by what is being surveyed rather than by who. Per-project zones stay a later refinement; the
transition lands with the curate workflow extraction (§3). Zone hierarchy and the security
connector's lack of awareness of it (per the type doc) are Egeria facts to design around, not
trellis work.

### Isolation matrix

**"This pass" is DONE for Resource Explorer (2026-09-04)** — every cell below marked ✅ RE. Egeria
Advisor's login enforcement landed the same day (806e702); EA's `Ownership`-on-publish and its own
draft zone follow the same rule and are not yet done.

| Dimension | Today | This pass | Follow-on |
|---|---|---|---|
| Data scoping (trellis-side: working sets, drafts, plans, conversation memory, saved queries) | none; one namespace | **✅ RE.** `user_id` on `resource_working_set`, `entity_egeria_project_context` and `conversation_history`, resolved by `registry.current_user_id()` inside the registry methods rather than by each route; `''` is the shared/legacy bucket and a read falls back to it. The two keyed tables' PRIMARY KEY was widened too — `ADD COLUMN` alone leaves the old two-column key and the second user collides with the first, a schema that looks migrated with no isolation behind it | — |
| Attribution and ownership in Egeria | every call as the service account | **✅ RE.** `egeria_identity.apply_identity` is the one place a pyegeria client is authenticated; `EgeriaPublisher` and both materializers build from the caller's token. `Ownership` (owner + `ownerTypeName: UserIdentity`) on every published element | agent-level singletons as before, plus: **a queued run carries no token** (one-hour credential, longer-lived queue) so the worker publishes as the service account with `Ownership` set to `requested_by` — closing it needs an encrypted-at-rest store or an Egeria delegation token |
| Partitioning of published elements | none; everything in the default zones | **✅ RE (superseded 2026-10-08: no default draft zone).** `resource-explorer-draft` on publish, promoted to `EXPLORER_PUBLISH_ZONES` (default `egeria-runtime`, what the quickstart configures) on curate-accept. The zone element is created once at worker startup, leader-elected — via the *generic* element create, because pyegeria has no `GovernanceZone` API at all (logged as ISSUE-88) | per-project or per-user zones if visibility needs it |
| LLM context | conversation history and RAG retrieval are per session with no owner check | **✅ RE.** `load_turns`/`list_sessions` scoped to the caller plus `''`; retrieval untouched | per-zone corpora if published documents must respect zones in retrieval too |
| Compute fairness | one user's 30-minute survey runs in the web process and starves everyone | **✅ RE.** The queue landed in step 2b; the fairness rule it carried was *inert* until now because every row was attributed to `''`. `run_queue.requested_by()` reads the signed-in caller, so the rule applies to real people and `''` holds only the worker's own service-account runs | priority classes, only if a multi-tenant deployment appears |
| Authorization | `FEEDBACK_ADMIN_TOKEN` and nothing else | **✅ RE.** `workflows/curate.may_curate`: owner allowed, `curator`/`admin` role claim allowed, everyone else 403, nobody-signed-in always denied. Enforced at the workflow layer, so the routes and the CLI share one answer. An element with no recorded owner stays curatable by any signed-in user — the migration story for verdicts recorded before the column existed | reading `PersonRoleAppointment` from Egeria instead of the JWT `role` claim; finer roles, per-project ACLs |

**The worker role is the one legitimate service-account user.** Bootstrap heal, resync and the
outbox drain run as the platform's integration identity, which is the right Egeria attribution
for them. Everything a person initiates, including a queued survey, carries that person's token
into the worker with the job row, so the eventual publish is attributed to them, not to the
worker.

**Rejected: build RE's own multi-user model independently.** As in revision 1.
**Rejected: a trellis-side audit or tenant table for Egeria-backed data.** Egeria already has
both; a parallel copy is the drift pattern this repo's own conventions warn about.

---

## 5. LLM backend, model tiers, and vLLM

**Do not move to vLLM now.** Unchanged from revision 1: `vllm-metal` is a young community
plugin; EA has no backend abstraction to receive a second engine; the Apple Silicon question is
its own spike. Any GPU-bound engine on Dev 1 stays native regardless of which engine wins.

**Added: model tiers and a context cap, per profile.** Neither app sets `num_ctx`; EA hardcodes
a model per task slot (`advisor.yaml:129-145`, `config.py:167-189`) with no tier concept; RE has
one default. The config gains a `tier` with, per task slot, a model, a context cap, and a RAG context budget (the measured lever for time to first token):

| Tier | Where | General / code model | Context cap | Loaded footprint |
|---|---|---|---|---|
| `dev` | Dev 1 | as today; 32B allowed | 32k | measured 5.7 GB at 8k for 8B; scales with cap |
| `demo-gpu` | Dev 2 (hedwig, 890M, 64 GB shared) and Demo 2 (trevor, RTX 2070 SUPER 8 GB) | llama3.1:8b / codellama:13b on hedwig; 8B for every slot on trevor (13B does not fit beside it in 8 GB) | 8k; RAG context budget 2k | hedwig: 22 s TTFT at 5.3k, ~8 s at 2k. trevor: 3 s TTFT at 5.3k |
| `demo-cpu` | Demo 1, Demo 2 | llama3.1:8b for every slot including code | 8k; RAG context budget 2k | ~6 GB; not interactive: 175 s TTFT at 5.3k on hedwig's CPU, worse on the old boxes |

The cap is what makes the demo tiers fit and what stops one loaded model taking 22 GB by default.
This lands in EA alongside the backend abstraction it lacks and RE already has.

**Cheap and worth doing now, still never done:** EA's ONNX embeddings path is built, switched
off, and unbenchmarked. On the CPU-only demo boxes it also decides whether the trellis image
needs PyTorch at all, which is several GB of image and most of the cold-start time.

---

## Sequencing

1. **Measure, cheap, no architecture risk.** Run EA's ONNX benchmark on Dev 1. Run the 5k-prompt
   Ollama probe from this doc CPU-only, 8B at 8k context, and record time-to-first-token. **Not
   on cray**, which is a live demo machine: run it on Demo 2, or on Dev 2 with Ollama forced to
   CPU (`OLLAMA_NUM_GPU=0`), either of which brackets the old boxes. That number decides which
   box demos what; the owner's expectation is that it will not flatter the old boxes. Write RE's threading and process-role model
   into `docs/Architecture.md`. Flip `PREFECT_ENABLED` to default `False` and set the ephemeral-
   start guard now, ahead of everything else, because it leaks today.
2. **Process roles and workflow extraction** (RE first, it has the incident). Worker role and
   run queue; web role at N workers with `--embed-worker` for dev; the three web-only workflows
   moved under `resource_explorer/workflows/` and exposed in the CLI; shared bounded thread pool
   and leader election as part of the same move. pyegeria concurrency spike alongside.
3. **Dev profile packaging.** Dockerfile (multi-stage, arm64 and amd64, ONNX-or-torch decided by
   step 1), `make ps`, CI builds both arches. Dev boxes keep running native; the image is the
   artifact.
4. **Demo profile.** `optional-associated-runtimes/trellis` compose overlay in
   `egeria-workspaces`; Ollama GPU variant and the ROCm spike on Dev 2; model tiers and context
   caps; provisioning list for the demo boxes. The `trellis-auth` token-contract change (§4) lands
   before this ships, because the Portal path depends on it.
5. **Multi-user.** EA's session design with the Postgres session store; port to RE via
   `trellis-auth`; `user_id` migrations; per-request Egeria client on the bearer token;
   `Ownership` on published elements; draft zone and promotion; two authorization roles;
   worker-queue fairness; A2A role with token auth. EA next once the RE pattern is proven, for the role work in step 2 too.
6. **Out of scope, flagged:** agent-level per-user isolation of `EgeriaContext`/MCP report
   agent; `vllm-metal` readiness; the NPU on Dev 2 (no usable Linux LLM backend for it today);
   finer-grained authorization.

## Delegation

Once approved, the work splits by shape rather than by step. Opus-level sessions for step 2 (the
concurrency and role refactor, and the workflow extraction, where the cost of a subtle mistake is
another stuck server) and the §4 token-contract and zone design. Sonnet-level sessions for steps 1, 3 and 4
(benchmarks, Dockerfiles, multi-arch CI, compose overlays, the ROCm spike), each with a written
result to fold back into this doc. Step 5 after 2 has landed, Opus for the RE port and Sonnet for
the EA session-store implementation, which follows EA's own design doc closely.

## Critical files

- `packages/resource-explorer/resource_explorer/web/app.py` (lifespan: the loops that move to the worker role)
- `packages/resource-explorer/resource_explorer/web/routes/projects.py`, `discovery.py`, `curate.py` (web-only workflows to extract)
- `packages/resource-explorer/resource_explorer/cli/main.py`, `tui/app.py`
- `packages/resource-explorer/resource_explorer/config.py` (`PrefectConfig.enabled`, `LLMConfig`, `RegistryConfig`)
- `packages/resource-explorer/resource_explorer/registry.py`, `run_reconciler.py`, `scheduler.py`
- `packages/egeria-advisor/advisor/config.py`, `advisor/configdata/advisor.yaml` (per-slot models, no tier, no context cap)
- `packages/egeria-advisor/advisor/cli/main.py`, `advisor/web/app.py`
- `packages/trellis-auth/README.md`
- `packages/egeria-advisor/docs/design/SESSION_AND_INTERACTION_STATE.md`, `RUNTIME_AND_HARDWARE.md`
- `../egeria-workspaces-fs/compose-configs/shared-infra/shared-infra.yaml`, `optional-associated-runtimes/ollama/docker-compose.yaml` (sibling repo)
