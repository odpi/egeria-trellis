# DESIGN — An MCP server over RE's engine, and the gaps in the A2A surface (2026-10-14)

From the design session at the owner's request ("we might need this at
some point so other tools can utilize our engine"). Read against main
758839f3: `docs/a2a.md`, `resource_explorer/agentstack_server.py`,
`a2a_auth.py`, `a2a_role.py`, `_mcp_compat.py`, `pyproject.toml`.

## 1. What exists

- **A2A: present.** The `serve` role runs one uvicorn on one port with a
  bearer token on every call (a trellis app JWT, or a raw Egeria token
  validated by use), seven agents routed by path (orchestrator, stats,
  code, docs, health, compare, integration), public cards, and a
  non-standard index at `/.well-known/agents.json` that lists them all.
  Agents that need a project use A2A's `input_required`.
- **MCP: absent.** RE consumes MCP as a client inside the agentstack SDK
  (hence the mcp 2.x compatibility shim); pyegeria ships an MCP server
  for Dr.Egeria. Nothing in RE serves MCP tools.
- **Three ways in, once ISSUE-150 lands:** A2A for agents that converse,
  MCP for tools that call, and Egeria's governance-action path for the
  platform itself, which schedules RE as an engine host. Each serves a
  different caller and none replaces another.

## 2. The gap this note closes

The A2A agents answer questions *about a repository*. None exposes the
engine's operations: register a resource, run or re-run a survey, read a
report or a finding, run an analytic routine, propose or confirm a
curation, read publish state, read or add to a work list. Those exist
only as FastAPI routes behind the web login. An external tool, an agent
in another product, Claude in a terminal, or the Portal cannot call
them. MCP is the protocol those callers speak.

## 3. The MCP server

**Where.** In the same `serve` process as A2A, on the same port, under
`/mcp` (streamable HTTP transport), plus a `resource-explorer mcp`
command for stdio when a local client such as Claude Code wants it.
Same two-token authentication as A2A, same `CallerIdentity` ContextVar,
same 401 shape. One process, one gate, as the A2A redesign of
2026-09-04 intended.

**What a tool is.** A thin wrapper over an existing route or service
function, never a second implementation: the tool calls what the screen
calls and returns what the screen gets, including the honesty fields
(as-of, source, `not measured` versus `measured, nothing found`, proof
state). A tool that cannot say where a number came from does not ship.

**The first tool set** (read tools first; each names the route it wraps):

| Tool | Wraps | Kind |
|---|---|---|
| `list_resources(kind?, group?, query?)` | the Scouting list | read |
| `get_resource(slug)` | the resource header plus Context facts with authors and dates | read |
| `get_survey_report(slug, run?)` | the survey report with each step's status and as-of | read |
| `list_findings(slug, kind?, step?)` | findings and metrics, scoped versus whole-resource stated | read |
| `get_publish_state(slug)` | proof rows by GUID: published, superseded, deleted in Egeria | read |
| `list_work_lists()`, `get_work_list(id)` | RE's WorkingSets (RE's prefix only) | read |
| `run_analytic(function, resource, params)` | the sync in-process path from ISSUE-150; result labelled "computed now · not recorded" | read, computes |
| `register_resource(kind, ...)` | registration | write, RE registry |
| `run_survey(slug, definition?)` | the survey press; returns the run id, never waits | write, RE registry |
| `set_context_fact(slug, fact, value)` | signed testimony under the caller | write, RE registry |
| `add_to_work_list(id, slug, rationale)` | membership with rationale | write, RE registry |
| `request_publish(slug, what)` | the Publish press as a request: returns the commit id and the stepper's first state; the commit runs under RE's own clearance rules | write, Egeria via RE |

**Shared-write rule.** No tool reaches Egeria or a shared registry write
by a path the screen does not already gate. A publish through MCP is
the same commit the Curate page runs, with the same claim, the same
proof rows, the same ISSUE-117 block, and the same refusal sentences.
Archive and delete have no tool. The live-writes clearance variable and
every hold apply unchanged; a tool reports the refusal, never works
around it.

**Identity (owner's decision, 2026-10-14: option 2).** The protocol
already carries Egeria credentials: the serve role accepts a raw Egeria
bearer token or a trellis app JWT with the Egeria token inside, and
puts the caller in a ContextVar every spawned task inherits. The gap is
RE's own construction sites, which build service-account pyegeria
clients, so a call authenticated as the caller still writes as the
service account. The rule from here:

- **Everything synchronous runs as the caller.** Reads, the sync
  analytic run, and any write that completes inside the request use the
  caller's token through `apply_caller_token`. A read then returns
  exactly what the caller may see, zones included, which is the privacy
  property that matters; the read side is converted first.
- **Queued writes stay on the service account, with the requester on
  the row.** The outbox drains after the press, an Egeria token lives
  one hour, so a queued write cannot carry the caller's token; it
  carries the caller's identity, Egeria's `Ownership` and RE's
  provenance name the requester, and every surface says "requested by
  <caller> · written by the service account". Moving queued writes to
  the caller waits for Egeria to offer delegation (option 3, not
  pursued).
- **Construction sites.** RE already has the seam: `egeria_clients.
  egeria_client(identity, ...)` takes `Caller()`, `Daemon(reason,
  requested_by)` or `current_principal()`, and the A2A middleware sets
  the `current_caller` ContextVar from the bearer token. The adoption
  PR is therefore an inventory, not a mechanism: each place RE builds a
  pyegeria client listed with file:line and marked caller or service
  with its reason; every synchronous request path still on the daemon
  converted to the caller; a committed test per converted site that
  the caller's token is the one sent; one test that a queued write
  still carries the requester; a two-user live read of one zoned
  element through a converted route. (The helper `apply_caller_token`
  named in `docs/a2a.md` does not exist; the doc is corrected in the
  same PR.) Owner's go: 2026-10-14.
- **The exchange for MCP.** The same middleware guards `/mcp` for the
  streamable HTTP transport, so a bearer token becomes the caller
  identity exactly as on A2A. For stdio, where a local process has no
  request header, the token is supplied at start (`resource-explorer
  mcp --token` or the environment) and set once as the caller for the
  session. One conversion of the construction sites then serves the web
  routes, A2A and MCP alike.

**Resources and prompts.** MCP resources expose the design notes folder
and a resource's survey report as readable documents; one prompt per
tool family ("curate this database") is optional and comes after the
tools.

**Tests.** One committed test per tool that the tool returns exactly the
route's payload (no second shape); one that an unauthenticated call is
401; one that a write tool under a hold returns the hold's sentence and
writes nothing; one end-to-end faking only the pyegeria boundary for
`request_publish`.

## 4. Gaps in the A2A surface to consider

1. **Repositories only.** Every agent and skill is about a repository
   (stars, commits, code, docs, health, compare, integration). Nothing
   answers for a database or a file system, though RE surveys both.
   Either the agents become kind-aware (stats for a database is row
   counts and schema change) or a database agent is added; the
   orchestrator's routing must then know the kind.
2. **Answers without provenance.** The agents answer from RE's data
   through a model; the A2A reply carries no as-of, no survey run id,
   no source list. Every answer should end with what it drew on and
   when it was measured, the same honesty fields the screens carry.
3. **Caller identity does not reach Egeria.** Stated in a2a.md: a call
   is authenticated as the caller, reads and writes run as the service
   account. Closed by option 2 above: synchronous work as the caller,
   queued writes on the service account with the requester recorded.
   Until that PR lands, the cards say the agents are read-only.
4. **Discovery is half-done.** The index exists; registering the cards
   with the Portal is open, and Egeria Advisor has no A2A surface. A
   Portal that lists RE's agents next to Advisor's is the useful end.
5. **The engine is not an agent.** Operations such as "survey this" or
   "publish that" are not skills; a conversing agent that could start a
   survey would need the MCP tools above behind it. The orchestrator
   gaining tool use over the MCP server is the clean way to get there,
   and it keeps one implementation.
6. **Streaming, cancellation and task state** are not documented; A2A
   defines them. A long answer or a survey started by an agent needs
   `tasks/get` and cancellation, or a caller cannot tell a slow answer
   from a dead one.
7. **Versioning.** The index reports `0.1.0` as the RE version; cards do
   not carry a schema version, so a client cannot tell a changed skill
   from an old one. The index should report RE's real version and each
   card a card version.
8. **Rate and cost.** Every agent call runs a model; nothing limits a
   caller. A per-token budget or a per-minute limit, reported in the
   index, is the minimum before the cards are published anywhere.

## 5. Order of work

1. The caller-identity adoption for synchronous work (option 2, read
   side first), then the MCP server with the read tools and
   `run_analytic` on top of it, in the `serve` process, with tests. This
   is the smallest thing another tool can use, and it already sees
   only what the caller may see.
2. The write tools under the shared-write rule, `request_publish` last.
3. The A2A fixes that cost little: provenance on every answer (2),
   read-only stated on the cards (3), real versions (7).
4. The orchestrator using the MCP tools (5), then database-aware agents
   (1), Portal registration (4), task state and limits (6, 8).

## 6. Open questions for the owner

- [ ] Is the first external caller the Portal, Egeria Advisor, Claude in
      a terminal, or another product? It sets the transport that must
      work first (streamable HTTP versus stdio).
- [x] `request_publish` ships with the write tools under option 2: it
      queues the same commit the screen runs, on the service account,
      with the caller recorded as requester (owner, 2026-10-14).
- [ ] Do the A2A agents stay LLM answerers, or become thin fronts over
      the MCP tools with the model only for phrasing?
