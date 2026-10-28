# RepoArchitectureDiscovery scope links — re-run alone, 2026-09-13

GENERATED SUBSET, not a source document. The two `Link Element To Scope` blocks from
../survey-definitions/repo-survey-definition-architecture-discovery.md, extracted and run alone
(2/2 SUCCESS) after the superseded Question term was deleted.

Why: the 2026-09-12 re-authoring ran the *discovery* and *full* documents for `repo_dependency_support`
but not this one, so `RepoArchitectureDiscovery` still scoped only to the superseded term "What is
its internal architecture — what components exist and how do they relate?" (split into four in the
CSV on 2026-09-08). When that term's stale `ScopedBy` links were removed and the term deleted
(2026-09-13, project owner's decision), this definition was left scoping to nothing. Running only its
scope blocks — no `Link First/Next Process Step` — cannot duplicate step edges.

Verified afterwards by the graph (`ClassificationExplorer.get_scopes`): 0 → 2 scopes; and by the
app's own `find_candidate_process_guids_by_questions` for the two questions, which returns
`RepoArchitectureDiscovery` and `RepoFullSurvey`. Not in `_batch.json`: a heal runs the full document.

---

## Link Element To Scope
### Target Element
Repo Architecture Discovery

### Scope Reference
What components exist in this repository, and what kind is each?

___

## Link Element To Scope
### Target Element
Repo Architecture Discovery

### Scope Reference
How do its components relate to each other?

___
