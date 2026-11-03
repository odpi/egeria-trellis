# Egeria platform stall during the new-scheme republish — 2026-09-02

**I caused this, and the batch that caused it is stopped. The platform is
recovered. 10 of 60 repos are published.**

## What happened

A batch republish of RE's corpus (60 repos, each a full 40-step survey plus a
publish of ~120–160 annotations) ran with `resource-explorer web` deliberately
stopped so the batch was the only outbox drainer. Four repos in, the Egeria
platform stopped responding.

`quickstart-egeria-main` was at **5.48 GiB of a 6 GiB limit, 356% CPU, 857
threads**, with a health-check failing streak of 39 (~26 minutes). The JVM was
alive but could not service requests — its own internal services
(`qs-engine-host`, `qs-integration-daemon`, `qs-nanny-daemon`) were logging
client-side exceptions calling the platform's own APIs. Classic GC thrash.

It did not recover when the load was removed: 90+ seconds after the batch
stopped, memory and CPU were unchanged. A container restart fixed it
immediately — 858 MiB, 12.7% CPU, healthy.

## The container limit is the underlying condition

`quickstart-egeria-main` is the **only** container on this host with a memory
cap: 6 GiB, against 45 GiB available. Every other container is uncapped.

More telling: after the restart, with **no load at all**, memory climbed to
3.77 GiB just starting its own servers. So the platform idles at ~60% of its
ceiling, and roughly 2 GiB of headroom is all that stands between normal
operation and a stall. A sustained publish batch is enough to close that gap.

That reframes the incident. The batch was the trigger; the cap is the cause.
Any comparable workload will do this again.

## Two things the failure was invisible to

**The batch recorded `'ok': True` for polaris.** `publish()` returned an asset
GUID, and the script recorded `rec["ok"] = bool(guid)` — so a repo where
**57 of 125 annotations failed to write** was logged as a success. The asset
was created; that is all the GUID ever meant. This is the same
correct-number-wrong-label defect this codebase keeps paying for, reproduced
in the verification harness written for it.

**Only the three-way check saw it.** `scripts/verify_republish.py` compares
produced vs enqueued vs distinct GUIDs, and polaris came back
`125 / 125 / 68 → COLLIDED 57`. That verdict was itself wrong — the label said
collision, the cause was 56 `TIMEOUT_ERROR_408`s and one 401 — because the
check compares enqueued rows across all statuses against GUIDs from `done`
rows only, so failed rows read as collisions. **The number was right and its
name was wrong**, which is exactly what the check exists to catch in the data
and did not catch in itself. Fixed: the verdict now separates `failed`,
`pending` and genuinely-collided.

## State

- Platform: recovered, healthy, catalog intact (10/60 assets resolve — 7 from
  before the batch plus amundsen, kafka, polaris).
- 57 polaris annotation rows sit `failed` in the outbox with retry backoff.
  They are not lost; the next drain retries them.
- Remaining 50 repos: not attempted.

## What must change before resuming

At the observed pace (avg 1621 s/repo across 4, with polaris at 4813 s once
retries began) the remaining work was ~25 hours, not the 4–8 estimated. Both
the rate and the ceiling need addressing:

1. **Raise the 6 GiB cap**, or
2. **Throttle** — a pause between repos, and a memory check that stops the
   batch before the platform stalls rather than after, and
3. **Stop trusting the asset GUID as a success signal.** A repo is published
   when its annotation rows are `done`, not when `publish()` returns.
