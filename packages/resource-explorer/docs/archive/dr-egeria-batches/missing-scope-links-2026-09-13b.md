# Scope links the reconciler found missing — 2026-09-13 (the first live run)

GENERATED SUBSET, not a source document. Emitted by
`scripts/reconcile_survey_definition_scopes.py --emit-missing` on its first live run, and executed
once (4/4 SUCCESS).

What it found: "How much code is there? How complex?" — one of the four Question terms created on
2026-09-12 — is authored in five definitions' documents but was live-scoped only on RepoFullSurvey.
The 2026-09-12 subset (missing-scope-links-2026-09-12.md) re-ran only the FAILED scope links from
the discovery and full documents; RepoCoarseScout, RepoCoarseProfile, RepoScoutingSurvey and
RepoAnalysisSurvey were never re-run, so their links to the new term never existed. Invisible to
the step-edge reconciler, which is why the scope reconciler now exists. After this ran: all ten
definitions reconciled (exit 0, zero missing, zero extra).

Not in `_batch.json`: a heal runs the full documents, which carry these links already.

---

## Link Element To Scope
### Target Element
Repo Scouting Scan

### Scope Reference
How much code is there? How complex?

___

## Link Element To Scope
### Target Element
Coarse Profile Survey

### Scope Reference
How much code is there? How complex?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
How much code is there? How complex?

___

## Link Element To Scope
### Target Element
Analysis Survey

### Scope Reference
How much code is there? How complex?

