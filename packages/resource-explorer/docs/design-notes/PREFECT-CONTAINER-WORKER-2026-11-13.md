# Resource Explorer's Prefect worker in a container (2026-11-13)

**Decision (project owner, 2026-11-13):** "lets do both host worker and launchd as well as container - its ok
if we sequence them, I just don't want the container work to get deferred forever - it will be used on demo
machine". The host worker under launchd is the interim on laz
(`scripts/launchd/org.odpi.trellis.re-prefect-worker.plist`, branch `re/prefect-worker-host-launchd`). This
note is the container worker: the target for container deployments, first the demo machine (pop-os, Linux,
NVIDIA). The design is the Architect's, DESIGN-PREFECT-ROLE-AND-RE-WORKER-2026-11-13 §1.6.

**Status:** built and tested by unit and stub tests. **Not brought up anywhere yet**, and not switched to:
the A/B proof below comes first, on the demo machine.

## What the image does

- **Every library step is installed.** `docker/Dockerfile.resource-explorer` syncs `resource-explorer` and
  every package under `packages/trellis-analytics/examples/steps/`, read from the tree. A bare
  `uv sync --package resource-explorer` re-resolves the venv down to one package and drops the steps; it did
  so in every image before this change, the web image included.
- **It carries its commit.** The build argument `TRELLIS_RE_SHA` (`make image-re` passes `git rev-parse HEAD`,
  CI passes `github.sha`) becomes `ENV TRELLIS_RE_SHA` and the `org.opencontainers.image.revision` label.
  `make image-re` also tags `trellis/resource-explorer:<sha12>`.
- **Role `prefect-worker`** (`docker/entrypoint-resource-explorer.sh`):
  1. refuses to start without `TRELLIS_RE_SHA`;
  2. refuses to start if `library_steps_line()` reports a step missing;
  3. deploys its own deployment, `RE Survey Flow/re-survey-step-<sha12>`. Its flows load from the image's own
     source, and its runs land on queue `re-<sha12>`;
  4. prunes per-commit deployments beyond the newest 3, with `resource-explorer prefect prune-deployments --keep 3`.
     It never deletes the default deployment, another flow's or pool's, or one with a scheduled, pending,
     running, paused or cancelling run, or one whose commit has an ONLINE worker (an idle older-commit worker keeps
     its deployment; read-back of 6888a58c). It prints each name it deletes (Architect ruling, 2026-11-13, §D).
     A failed prune is reported and doesn't stop the worker. Steps 3 and 4 are Prefect writes, run at every
     start; the host worker under launchd does the same once its tree has this branch;
  5. runs `prefect worker start --pool <PREFECT_WORK_POOL> --work-queue re-<sha12>` as
     `re-worker-<sha12>-<hostname>`. It polls only its own queue.

## Version parity at dispatch

`prefect_adapter._choose_deployment`, read only, runs before any flow run is created. RE knows its own commit
(`build_info.re_sha`: the image's `TRELLIS_RE_SHA`, else `git rev-parse HEAD` of the checkout, else unknown).

| Online workers | Deployment used |
|---|---|
| one advertises RE's own commit | `re-survey-step-<sha12>` (that worker's queue) |
| else one advertises no commit (the host worker, until its wrapper advertises one) | the default deployment, as before, but never silently: a WARNING once per worker ("commit parity not checked: worker <name> advertises none") and a pool banner line while it lasts (Architect ruling, 2026-11-13) |
| only workers on other commits | refused: "no online Prefect worker runs this Resource Explorer's code (<own>); online workers on pool … run <theirs>" |
| none | refused: "no online Prefect worker for pool …" (unchanged) |

A refusal goes through `run_prefect_step`'s existing fallback. The step runs locally, `step_runs.executor`
says `local`, and the run report says "ran locally: Prefect dispatch failed — <the reason>". A run that
reached a worker and failed stays FAILED: there is no local re-run (§1.2).

A commit names committed code only. Uncommitted edits in a host checkout are not in it.

## Compose

The service lives in egeria-workspaces, `compose-configs/optional-associated-runtimes/prefect/docker-compose.yaml`
(branch `prefect/re-worker-container`). It **replaces** the old egeria-pool worker, which the owner deleted
on 2026-11-13. The service has:
- the shared-infra network and service names (`egeria-shared-postgres:5442`, `prefect-server:4200`, Egeria via
  `host.docker.internal`), never `localhost`;
- configuration by environment, with no secret in the image;
- declared mounts: `/app/data` (the same volume the trellis web and worker use) and one read-only survey root;
- an NVIDIA device reservation in a Linux-only overlay.

## A/B proof before switching (on the demo machine)

Run the same definition on the same resource both ways, then compare. Use a resource the worker can reach,
by service name or under the declared survey mount.

1. **Local:** `engine_override="resource-explorer"`. Note the activity id.
2. **Prefect:** with the container worker ONLINE and RE on the same commit (check the worker's name), a step
   with `executes_at: prefect`. Note the activity id.
3. **Compare.** These are read-only queries against the registry:

   ```sql
   SELECT step_key, executor, executor_ref, flow_run_id, dispatch_failed, metrics
     FROM resource_explorer.step_runs
    WHERE slug = '<slug>' AND surveyed_at IN ('<run 1 surveyed_at>', '<run 2 surveyed_at>')
    ORDER BY step_key, surveyed_at;
   ```

   Run 2's dispatched step must say `executor = 'prefect'` with a `flow_run_id`, and its `dispatch_failed` must
   be empty. Run 1 must say `local`. Both runs' stored results for that step (the analysis results reader)
   must be identical apart from timestamps.
4. **Only then** set the demo stack's `PREFECT_ENABLED=true` for the trellis services. They are off today
   (`optional-associated-runtimes/trellis/docker-compose.yaml`).
