# Archived documents

Kept, not deleted. Each of these described work that finished, and the finishing
is what made it historic — the code is now the truth and these record how it was
reasoned about at the time.

## What is here

**`PHASE1`–`PHASE4_*` (2026-08-06, eight files).** The completion states and
frontend plans from Resource Explorer's original four-phase build. A closed
cluster: before archiving they referenced only each other, plus one link from
`database-surveyor-quickstart.md`, which now points here.

**`arch-recovery-handoff-2026-08-24.md`.** A session handoff, time-bound by
nature. Its durable content lives in `../architecture-recovery.md` (the
earlier `architecture-recovery-design.md` and `-phase1-findings.md` were consolidated into it on
2026-09-03 and no longer exist as separate files). `Backlog.md`
links here.

## Snapshots, one-off Dr.Egeria batches and incident notes (2026-10-15)

- `snapshots/` holds dated, self-labelled snapshots (consolidation, gap audit, project
  review, work timeline), the `next-ui-*` experiment and round records, and the
  historical `tutorial.md`. `../next-ui-design-rules.md` stays live.
- `dr-egeria-batches/` holds Dr.Egeria documents that were executed once (the dated
  `questions/` repair and update batches), `re-inventory-2026-08-21.csv`, and two
  stale hand-authored files. None is read at runtime; the live inputs stay in
  `../dr-egeria/`.
- `incidents/` keeps only the two written findings of the September 2026 incidents.
  The raw logs, `docker inspect` output and registry snapshots (about 5 MB) were
  dropped from the package; they remain in git history before this change.

## What is deliberately NOT here

Most plans in `docs/` describe work that has shipped, and moving them would have
been wrong. **The code cites them.** `survey-tab-unification-plan.md` has 26
references from source, `discovery-automate-project-context-plan.md` 24,
`survey-question-context-plan.md` 23, `repo-scope-narrowing-funnel.md` 19. A
comment reading "(repo scope-narrowing funnel plan, D5)" is not nostalgia — it is
the explanation for a decision the code cannot restate in place.

So "the work is done" is not the archiving test. **"Nothing points at it" is.**
A plan whose decisions are still cited is load-bearing documentation whatever its
status line says, and the reference count is the measurement that tells them
apart. Anything moved here was checked that way first.
