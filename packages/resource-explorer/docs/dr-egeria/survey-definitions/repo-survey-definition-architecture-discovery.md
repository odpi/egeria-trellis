## Create Governance Action Process Step
### Display Name
Repo Architecture Discovery — Repo Arch Detect

### Qualified Name
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_detect

### Description
Recovers candidate architecture components from package manifests, deployment units (Dockerfile/compose), and ast-grep code markers — the deterministic half of architecture recovery (design doc §5.1).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_detect |

___

## Create Governance Action Process Step
### Display Name
Repo Architecture Discovery — Repo Arch Coupling

### Qualified Name
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_coupling

### Description
Proposes additional architecture-component boundaries from import coupling and co-change coupling — the conventional, undeclared components (Agents, Core, Surveyors, ...) that manifests and code markers structurally cannot see (design doc §5.5, Phase 1 plan §4.1). Needs real git history, not just a checkout.

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | prefect |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_coupling |

___

## Create Governance Action Process Step
### Display Name
Repo Architecture Discovery — Repo Arch Lens

### Qualified Name
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_lens

### Description
Labels recovered components against the project's own architecture document, wherever it lives — in-repo, a sibling repository, or a documentation site. A LENS: it adds no component, removes none and assigns no type. Milvus goes from 206 candidates to 15 the authors actually name (finding 102).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_lens |

___

## Create Governance Action Process Step
### Display Name
Repo Architecture Discovery — Repo Arch Summary

### Qualified Name
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_summary

### Description
Collapses architecture-recovery findings to the depth the question asked for — the summarising half nothing owned. Milvus recovers 218 candidate components where its own authors describe eight; a suitability answer is 'serves gRPC (297 operations), 24 runnable units, 31 third-party'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_summary |

___

## Create Governance Action Process
### Display Name
Repo Architecture Discovery

### Qualified Name
GovActionProcess::RepoArchitectureDiscovery

### Description
Candidate component partition — package manifests, deployment units, ast-grep code markers, and import/co-change coupling (docs/architecture-recovery-phase1-plan.md) — proposed at every depth it finds and stored as a hierarchy, not one chosen level (repo-analysis-funnel.md (§12) §2a), so 'which subsystem is this?' is answerable from a coarse projection without discarding the finer reading underneath. A SEPARATE survey_group from Repo Discovery Survey, deliberately: unlike every other discovery-tier analysis, both steps here DO fetch (repo_arch_detect needs a zipball, repo_arch_coupling also needs a git clone for co-change history) — bundling them into the zero-new-fetch survey above would make its own description false. Comfortably sufficient for subsystem identification; marginal for component-scoped metrics; not yet a publishable blueprint.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | discovery |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoArchitectureDiscovery

### Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_detect

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_detect

### Next Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_coupling

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_coupling

### Next Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_lens

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_lens

### Next Governance Action Process Step
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_summary

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_detect

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-detect

### Description
Egeria dispatches GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_detect to Resource Explorer's engine, which claims it and runs RE step repo_arch_detect.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_lens

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-lens

### Description
Egeria dispatches GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_lens to Resource Explorer's engine, which claims it and runs RE step repo_arch_lens.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-summary

### Description
Egeria dispatches GovActionProcessStep::RepoArchitectureDiscovery::repo_arch_summary to Resource Explorer's engine, which claims it and runs RE step repo_arch_summary.

___

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

