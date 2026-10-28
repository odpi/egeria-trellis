## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Classification

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_classification

### Description
What the repo represents (7 roles, ranked, multi-valued), where each artifact its role implies actually lives, and whether architecture recovery is worth running at all (design §5.5b).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_classification |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo License Classification

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_license_classification

### Description
Classifies the repo's SPDX license id into a risk tier (permissive/weak copyleft/strong copyleft/source-available/unknown).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_license_classification |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Maturity

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_maturity

### Description
Project age/lifecycle stage (nascent/emerging/established/mature), from repo_created_at — a CHAOSS-informed Discovery-tier signal.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_maturity |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Conventions

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_conventions

### Description
Discovery-tier repo conventions: security policy content, build automation, deployment/Docker evidence, catalog self-description (Backstage-style), documentation breadth.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_conventions |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Community Support

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_community_support

### Description
Community support as separate dimensions — attention, participation, channels — rather than one number. repository_health's community_score is dominated by stars and forks, and scores a four-contributor project 100/100; this reports the weakest dimension instead of averaging it away.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_community_support |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Interface Surface

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_interface_surface

### Description
What can be talked to, and whether the contract is written down — three rungs: 'declared' (a committed contract, or an entry point the packaging declares), 'implemented' (the code runs as one, from stored evidence such as a distribution's own __main__.py), and 'implied' (a dependency name only, e.g. fastapi) — read from the file inventory, declared dependencies, and the distribution/deployment-evidence facts other steps already recorded. None of the first two rungs counts as a published API on its own.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_interface_surface |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Dependency Support

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_dependency_support

### Description
Which curated technologies the dependency list indicates (psycopg2 -> PostgreSQL, kafka-python -> Apache Kafka), and whether Egeria already holds a technology type for each. A starting point for people, not an answer: a match says the repo indicates X, not that X is supported, and an unmatched dependency has simply not been classified yet.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_dependency_support |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Deployment Evidence

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_deployment_evidence

### Description
Per declared distribution (repo_manifest_parse's output): which deployment evidence exists — a console entry point, a __main__.py, an (unambiguous) Dockerfile/compose/Helm chart, a web-framework dependency — and the verdict that evidence supports: application (evidence found), library (importable, none found), or unknown (no manifest read yet). Layer 1 of 'Cataloging in layers' (project owner, 2026-09-14): before a distribution is proposed to Egeria as a SoftwareCapability classified Application, this is the evidence that verdict rests on.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_deployment_evidence |

___

## Create Governance Action Process Step
### Display Name
Repo Discovery Survey — Repo Egeria Interfaces

### Qualified Name
GovActionProcessStep::RepoDiscoverySurvey::repo_egeria_interfaces

### Description
Which Egeria view services this repository consumes — one per pyegeria client class its code symbols reference (signature/return-type/base-class text; imports are not captured anywhere in this codebase, so this is a lower bound, not a count) — mapped via the curated configdata/egeria_view_services.yaml, plus Dr.Egeria doc-path evidence and a best-effort command-family guess from filenames. A repo with no pyegeria dependency reads nothing_found, not never-run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_egeria_interfaces |

___

## Create Governance Action Process
### Display Name
Repo Discovery Survey

### Qualified Name
GovActionProcess::RepoDiscoverySurvey

### Description
Early-headlights signals for deciding whether to pursue this repo further — from data already collected by Scouting/Profile, zero new fetch. Grown by Part 2 (docs/discovery-automate-project-context-plan.md) with maturity/lifecycle-stage and repo-convention signals (security policy, build automation, deployment/Docker evidence, catalog self-description, documentation breadth).

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | discovery |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoDiscoverySurvey

### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_classification

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_license_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_license_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_maturity

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_maturity

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_conventions

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_conventions

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_community_support

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_community_support

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_interface_surface

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_interface_surface

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_dependency_support

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_dependency_support

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_deployment_evidence

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_deployment_evidence

### Next Governance Action Process Step
GovActionProcessStep::RepoDiscoverySurvey::repo_egeria_interfaces

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-classification

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_classification to Resource Explorer's engine, which claims it and runs RE step repo_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_license_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-license-classification

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_license_classification to Resource Explorer's engine, which claims it and runs RE step repo_license_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_maturity

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-maturity

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_maturity to Resource Explorer's engine, which claims it and runs RE step repo_maturity.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_conventions

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-conventions

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_conventions to Resource Explorer's engine, which claims it and runs RE step repo_conventions.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_community_support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-community-support

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_community_support to Resource Explorer's engine, which claims it and runs RE step repo_community_support.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_interface_surface

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-interface-surface

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_interface_surface to Resource Explorer's engine, which claims it and runs RE step repo_interface_surface.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_dependency_support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-dependency-support

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_dependency_support to Resource Explorer's engine, which claims it and runs RE step repo_dependency_support.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_deployment_evidence

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-deployment-evidence

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_deployment_evidence to Resource Explorer's engine, which claims it and runs RE step repo_deployment_evidence.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoDiscoverySurvey::repo_egeria_interfaces

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-egeria-interfaces

### Description
Egeria dispatches GovActionProcessStep::RepoDiscoverySurvey::repo_egeria_interfaces to Resource Explorer's engine, which claims it and runs RE step repo_egeria_interfaces.

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What explicit license does this resource use, and are there non-standard or copyleft terms?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
How mature is it?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What deployment styles does this support?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Is there a validation / deployment test for it?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
How well documented is it?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Is there a current, published, security analysis?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Does the repository publish a clear process for reporting security vulnerabilities?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Does the repository have automated build tooling in place?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Is this repository already self-described for an enterprise catalog (e.g. Backstage catalog-info.yaml)?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
How widely adopted and active is the community around this repository?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What kinds of integrations does it support?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What APIs and code symbols does it expose to callers?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
What are the public interfaces?

___

## Link Element To Scope
### Target Element
Repo Discovery Survey

### Scope Reference
Do we already support these dependencies?

