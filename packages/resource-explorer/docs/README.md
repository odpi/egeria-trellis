# Resource Explorer documentation

61 documents, grouped by what they *are* rather than what they are about — the
distinction that matters when deciding whether one is still live.

**Generated from each document's own `**Status:**` line**, not from a
hand-maintained list, because a hand-maintained index goes stale the first time
someone adds a file. Regenerate after adding or reclassifying:

```bash
python scripts/regen_docs_index.py
```

This index covers the documents in `docs/` itself. The design notes are indexed separately, by
where their work stands on main: [design-notes/README.md](design-notes/README.md).

**Start here if you use Resource Explorer:** [user-guide.md](user-guide.md) and
[admin-guide.md](admin-guide.md).

**Start here if you add your own analysis:** the [tutorial](../../trellis-analytics/docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md), then the reference, [extending-with-analyses.md](extending-with-analyses.md).

## Open — planned or on hold

Live plans. The work is not done; the doc is the specification.

- **[blueprint-materialization-plan.md](blueprint-materialization-plan.md)** — 2026-09-03  
  planned, not yet built. Written 2026-09-03 (Opus-model plan pass), scoping
- **[curate-evidence-based-decisions-plan.md](curate-evidence-based-decisions-plan.md)** — 2026-09-03  
  design pass, not yet built. Companion to `docs/blueprint-materialization-plan.md` (the

## Design and framing

Decisions and models. Cited from source where a comment cannot restate them.

- **[a2a.md](a2a.md)** — 2026-10-15  
  live. The A2A server surface (one service, routed by path)
- **[admin-guide.md](admin-guide.md)** — 2026-10-26  
  live. Operator guide; the best-maintained user-facing document
- **[architecture-recovery-extraction-design.md](architecture-recovery-extraction-design.md)** — 2026-09-03  
  design reference. Part of the architecture-recovery design — split out 2026-09-03
- **[architecture-recovery-in-practice.md](architecture-recovery-in-practice.md)** — 2026-09-02  
  reference, current as of 2026-09-02
- **[Architecture.md](Architecture.md)** — 2026-10-15  
  live. Module map, flows and key decisions; the zone passage was corrected 2026-10-14
- **[Backlog.md](Backlog.md)** — 2026-10-30  
  not scheduled
- **[BUILDER-BRIEF-CHECKLIST.md](BUILDER-BRIEF-CHECKLIST.md)** — 2026-10-14  
  live. Short checklist for every builder brief
- **[cross-resource-test-protocols.md](cross-resource-test-protocols.md)** — 2026-10-17  
  Ready for manual and automated verification
- **[ddl-analysis-table-rows.md](ddl-analysis-table-rows.md)** — 2026-11-02  
  branch `claude/re-b1-table-result`, not deployed. This is the deployment record for one new tabl
- **[ddl-artifact-columns.md](ddl-artifact-columns.md)** — 2026-10-28  
  merged to main (PR #700). The columns are added by RE's schema init the first time a process run
- **[ddl-investigation-visibility.md](ddl-investigation-visibility.md)** — 2026-11-04  
  branch `claude/re-f56-visibility-axis`, not deployed. This is the deployment record for one colu
- **[ddl-open-data-data-request.md](ddl-open-data-data-request.md)** — 2026-10-29  
  APPROVED (owner, 2026-10-30). Not yet applied. Decision (project owner, 2026-10-30): "approve th
- **[ddl-open-data-od1.md](ddl-open-data-od1.md)** — 2026-10-28  
  branch pushed, not merged. This is the deployment record for three NEW tables in RE's shared reg
- **[ddl-step-annotations.md](ddl-step-annotations.md)** — 2026-10-14  
  live. Deployment record for the step_annotations table (2026-10-13)
- **[ddl-value-profile-1b.md](ddl-value-profile-1b.md)** — 2026-10-28  
  branch, not deployed. This is the deployment record for fourteen additive columns in RE's shared
- **[ddl-value-profile-q6.md](ddl-value-profile-q6.md)** — 2026-10-28  
  merged to main (PR #880, 2026-10-27). The column is added by RE's schema init the first time a p
- **[ddl-value-profile-step6.md](ddl-value-profile-step6.md)** — 2026-10-29  
  branch, not deployed. This is the deployment record for five additive nullable columns in RE's s
- **[ddl-value-profile-step7.md](ddl-value-profile-step7.md)** — 2026-10-29  
  branch, not deployed, proposed. This is the deployment record for nineteen additive nullable col
- **[ddl-value-profile-step8.md](ddl-value-profile-step8.md)** — 2026-10-29  
  branch, not deployed, not approved. Decision needed (project owner): approve the seven columns a
- **[ddl-walk-files-accessed-at.md](ddl-walk-files-accessed-at.md)** — 2026-10-28  
  branch `claude/re-files-steps-6-8`, not deployed. This is the deployment record for one additive
- **[dr-egeria-engine-host-gaps.md](dr-egeria-engine-host-gaps.md)** — 2026-10-14  
  live. Request to the egeria-python maintainers (2026-10-14); open until they respond
- **[egeria-integration.md](egeria-integration.md)** — 2026-09-03  
  consolidated design. Current as of 2026-09-02
- **[egeria-operations.md](egeria-operations.md)** — 2026-10-03  
  consolidated runbook. Current as of 2026-09-02
- **[egeria-support-for-multi-resource.md](egeria-support-for-multi-resource.md)** — 2026-10-14  
  validation and planning input, 2026-09-20. Expands §12 of
- **[extending-resource-explorer.md](extending-resource-explorer.md)** — 2026-10-30  
  live. How to add a question, analysis step, annotation type or survey type
- **[extending-with-analyses.md](extending-with-analyses.md)** — 2026-10-30  
  live reference. Written 2026-10-28, brought up to date 2026-10-30 against `origin/main` at `aacd
- **[feedback-and-curation.md](feedback-and-curation.md)** — 2026-09-03  
  consolidated design. Current as of 2026-09-02
- **[filesystem-and-database-surveying.md](filesystem-and-database-surveying.md)** — 2026-09-25  
  consolidated design and reference. Current as of 2026-09-02
- **[funnel-cost-measured.md](funnel-cost-measured.md)** — 2026-10-14  
  live. Measured funnel cost (2026-09-12/13); re-derivable from the committed resolver
- **[gap-analyses-design.md](gap-analyses-design.md)** — 2026-10-14  
  live. Design for four gap analyses; code and tests cite it
- **[kroki-diagram-rendering.md](kroki-diagram-rendering.md)** — 2026-10-14  
  live. How server-side mermaid rendering works
- **[multi-resource-questions-design.md](multi-resource-questions-design.md)** — 2026-10-15  
  design and planning input. First draft 2026-09-20; revised the same
- **[open-stack-checklist.md](open-stack-checklist.md)** — 2026-10-15  
  live, partly dated. Outstanding-work checklist, last updated 2026-08-31; entries are deleted whe
- **[process-model.md](process-model.md)** — 2026-10-14  
  live. Process roles, leader locks and loop inventory
- **[question-answering-and-context.md](question-answering-and-context.md)** — 2026-09-03  
  consolidated design. Current as of 2026-09-02
- **[repo-analysis-funnel.md](repo-analysis-funnel.md)** — 2026-09-02  
  consolidated design. Current as of 2026-09-02
- **[security-model.md](security-model.md)** — 2026-10-15  
  consolidated model, current as of 2026-09-25. This is the single
- **[survey-execution.md](survey-execution.md)** — 2026-09-04  
  consolidated design. Current as of 2026-09-02
- **[survey-model.md](survey-model.md)** — 2026-10-01  
  consolidated design and reference. Current as of 2026-09-02
- **[surveyor-reference.md](surveyor-reference.md)** — 2026-10-19  
  stale. Oldest unrevised reference (2026-08-07); predates the database surveyors and Prefect. Che
- **[testgen-integration-guide.md](testgen-integration-guide.md)** — 2026-10-30  
  live guide. Written 2026-10-28, brought up to date 2026-10-30 against `origin/main` at `aacdfaff
- **[user-guide.md](user-guide.md)** — 2026-10-30  
  live. Investigations, work lists, private work, the questions, Curate, Publish, Files and live s

## Findings and audits

Evidence. What was measured, when, and against what.

- **[architecture-recovery.md](architecture-recovery.md)** — 2026-09-03  
  consolidated design and reference. Phase 0 and Phase 1 measurement complete; the
- **[coverage-audit-2026-09-11.md](coverage-audit-2026-09-11.md)** — 2026-10-14  
  historical findings. Dated audit (2026-09-11); numbers are as of that date

## Shipped — kept because the code cites them

The work is done and these are NOT archive candidates: source comments point here for the reasoning behind decisions.

- **[admin-surface-options.md](admin-surface-options.md)** — 2026-10-14  
  implemented. Options survey from 2026-09-03; the admin surface it proposed has been built
- **[annotation-identity-check.md](annotation-identity-check.md)** — 2026-10-14  
  implemented. Answered 2026-09-11 from the schema and live registry; the answer is applied in cod
- **[automate-notification-manager-pyegeria-spec.md](automate-notification-manager-pyegeria-spec.md)** — 2026-08-13  
  specification only, not implemented. Written per explicit direction
- **[cross-resource-chat-extensions-plan.md](cross-resource-chat-extensions-plan.md)** — 2026-10-20  
  In progress (Groups 1–3 implemented and verified; Group 4 chips and A2A `input_required` impleme
- **[curated-architecture-answers-design.md](curated-architecture-answers-design.md)** — 2026-10-14  
  implemented in part (code cites §6 items 1-4). Originally a design pass, not yet built. Opened 2
- **[curation-lenses-design.md](curation-lenses-design.md)** — 2026-10-14  
  design, unmeasured (2026-09-08). Nothing here is built. §8 defines the spike that
- **[discovery-automate-project-context-plan.md](discovery-automate-project-context-plan.md)** — 2026-10-28  
  all 5 parts built and live-verified (2026-08-13). Part 1 (Discovery
- **[investigation-classification-and-zoning-design.md](investigation-classification-and-zoning-design.md)** — 2026-10-14  
  BUILT. All six phases are implemented and live-verified (2026-09-07/08). Extends
- **[investigation-framing-design.md](investigation-framing-design.md)** — 2026-10-13  
  design; two pieces have since been built (2026-08-24). All 41 questions are tagged with Purpose 
- **[next-ui-design-rules.md](next-ui-design-rules.md)** — 2026-10-15  
  live. Design rules the Next UI is built to
- **[repair-operations-design.md](repair-operations-design.md)** — 2026-10-14  
  implemented. Four repo-repair operations (2026-08-28), built
- **[rfa-dismissals.md](rfa-dismissals.md)** — 2026-10-14  
  implemented. Not applicable and won't do dismissals for RFAs, built
- **[survey-definitions.md](survey-definitions.md)** — 2026-10-19  
  Implemented and validated end-to-end against a live Egeria server (2026-07-07/08) — both a singl
- **[vendored-guard.md](vendored-guard.md)** — 2026-10-14  
  implemented. The vendored-walk guard and the record of it failing (2026-09-12)

## Guides, references and unmarked

Includes the user-facing guides. Anything here without a Status line is a candidate for gaining one.

- **[open-data-adapters.md](open-data-adapters.md)** — 2026-10-28
