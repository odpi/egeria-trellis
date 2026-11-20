# Trellis docs — index

Status as of **2026-11-19, the 6.3 Tech Preview**. Each file also carries its own status line at the top. Package-specific
docs live under `packages/*/docs/`; the install path is [QUICKSTART.md](../QUICKSTART.md) at the repository root.

## Reference

Checked against the code for 6.3.

| File | What it is | Status |
|---|---|---|
| [deploying-trellis-with-quickstart.md](deploying-trellis-with-quickstart.md) | Install, configure and run RE and EA: demo, Mac and Linux developer; ports, secrets, config reference, Prefect dispatch | Current; covers Studio, `trellis-up`, step timeout, JWT settings |
| [demo-deployment-runbook.md](demo-deployment-runbook.md) | The verbatim 2026-09-04 Linux/GPU bring-up | Current for RE and EA; dated record, corrected in place |
| [packaging.md](packaging.md) | Container images, entrypoint roles, health checks | Current; Studio has no image |
| [portal-integration.md](portal-integration.md) | The Portal handoff contract | Current |
| [trellis-architecture.md](trellis-architecture.md) | Three apps, ten libraries, shared Postgres | Current |
| [context-compilation-architecture.md](context-compilation-architecture.md) | What shipped for context compilation | Current for its packages; EA not covered |
| [context-compilation-usage.md](context-compilation-usage.md) | The context-compilation flags and commands | Current for shipped flags |

## Design & history

| File | What it is | Status |
|---|---|---|
| [runtime-architecture-plan.md](runtime-architecture-plan.md) | Process roles, CLI, multi-user, tiers | Largely implemented; Prefect default reversed (on) |
| [trellis-vectorstore-extraction.md](trellis-vectorstore-extraction.md) | Design for `trellis-vectorstore` | Implemented |
| [trellis-auth-extraction.md](trellis-auth-extraction.md) | Design for `trellis-auth` | Implemented |
| [trellis-feedback-extraction.md](trellis-feedback-extraction.md) | Design for `trellis-feedback` | Implemented |
| [feedback-closed-loop-design.md](feedback-closed-loop-design.md) | Closing the loop on answer feedback | Steps 0–2 built; 3–5 not found |
| [hardware-acceleration-proposal.md](hardware-acceleration-proposal.md) | One device choice; install the right PyTorch | Proposal A built (`trellis-accel`); B not built |
| [context-compilation-design.md](context-compilation-design.md) | Original context-compilation design | Partly implemented |
| [context-compilation-brief.md](context-compilation-brief.md) | Sept 2026 presentation source | Historical |
| [ea-context-compilation-design.md](ea-context-compilation-design.md) | Context compilation for EA | Not built |
| [corpus-profiler-design.md](corpus-profiler-design.md) | A measured chunk-size profiler | Partly built; not wired to ingest |
| [context-spec-requirements.md](context-spec-requirements.md) | One spec model, several flavours | Requirements; partly met |
| [context-as-product-requirements.md](context-as-product-requirements.md) | Context Intelligence flavour | Not built |
| [context-pack-first-instance-coco-pharma.md](context-pack-first-instance-coco-pharma.md) | First worked context pack | Not built (example) |
| [experiments/](experiments/p12-results.md) | The p12 context-pack experiment: notebook, outputs, pack, results | Experiment record |
| [extensibility-walking-skeleton-brief.md](extensibility-walking-skeleton-brief.md) | Analytic extensibility skeleton | Implemented as the Analytic Library |
| [analytic-extension-points-inventory-2026-10-02.md](analytic-extension-points-inventory-2026-10-02.md) | Inventory of extension points | Snapshot at `afe9bd28` |
| [analytic-standards-comparison.md](analytic-standards-comparison.md) | Adopt/study/decline standards (R20) | Recommendations; OpenLineage built |
| [ingestion-pipeline-audit.md](ingestion-pipeline-audit.md) | RE vs EA ingestion audit | Completed audit |
| [re-ea-consolidation-audit.md](re-ea-consolidation-audit.md) | RE vs EA beyond ingestion | Completed audit |
| [resource-vocabulary-rename-plan.md](resource-vocabulary-rename-plan.md) | `project` → `resource`, `group` → `org` | Not started |
| [trellis-journey.md](trellis-journey.md) | Narrative timeline | Through 2026-08-28; not extended |

## Archive

Kept, not deleted ([archive/](archive/)).

| File | What it is | Status |
|---|---|---|
| [archive/morning-brief-2026-08-31.md](archive/morning-brief-2026-08-31.md) | Overnight brief, 2026-08-31 | Archived 2026-11-19 |
| [archive/task-list-2026-08-28.md](archive/task-list-2026-08-28.md) | Scheduling view of the backlogs, 2026-08-28 | Archived 2026-11-19; use the backlogs |
