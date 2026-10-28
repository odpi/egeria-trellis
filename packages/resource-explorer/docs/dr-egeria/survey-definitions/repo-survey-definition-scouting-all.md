## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Git Statistics

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_git_statistics

### Description
Refreshes project_stats (stars, forks, contributors, commit activity, releases, security config, deployments) from the GitHub API — the table eight other steps read. Replaces five independent StatsFetcher calls that each refreshed it separately in the same run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_git_statistics |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo File Inventory

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_file_inventory

### Description
Refreshes project_file_inventory from a fresh zipball — the table every file-shape step reads. Closes the gap where the inventory was written only by RAG ingestion/refresh_profile and never by a survey step, so a survey reported whatever an earlier, unrelated run had left behind.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_inventory |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Manifest Parse

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_manifest_parse

### Description
Parses dependency manifests, CI workflow content, supply-chain signals and repo-convention signals from a freshly extracted zipball, refreshing project_dependencies and project_analysis_findings (kind="ci_quality"/"repo_conventions"/"supply_chain") — the three tables previously written only by full ingestion (and, for the latter two, refresh_profile), never by a survey step.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_manifest_parse |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Health

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_health

### Description
Activity, community, release-cadence, and freshness scoring from GitHub stats.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_health |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Language

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_language

### Description
Primary/secondary language and coarse project-type classification.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_language |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo File Classification

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_file_classification

### Description
Classifies every file by type using filename/extension mapping (Egeria-enrichable).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_classification |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo File Structure

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_file_structure

### Description
File counts, per-language breakdown, and top-level directory structure.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_structure |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Data Profiling

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_data_profiling

### Description
Inventories data files and profiles their schema (rows/columns/dtypes/nulls).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_data_profiling |

___

## Create Governance Action Process Step
### Display Name
Scouting Survey — Repo Homepage

### Qualified Name
GovActionProcessStep::RepoScoutingSurvey::repo_homepage

### Description
Finds where the project's homepage and its documentation are, and whether the documentation answers. Reads GitHub's homepage field, the packaging manifests (pyproject.toml, package.json, setup.cfg, Cargo.toml, pom.xml) and the README -- small files only, through the GitHub contents API, never a download -- and keeps the source and the other candidates for each answer. An entry labelled Documentation wins; placeholders such as example.com are rejected. One bounded request records whether the docs URL is reachable. Surfaced in Scouting as links with a short cue; the homepage is published to Egeria as an ExternalReference linked to the repo.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_homepage |

___

## Create Governance Action Process
### Display Name
Scouting Survey

### Qualified Name
GovActionProcess::RepoScoutingSurvey

### Description
Everything Scouting does, in one run: refresh git statistics and the file inventory, then score health, language shape, file classification and structure, data-file profiles and the project's external website. The union of Git Statistics Survey and Coarse Profile Survey, which remain available separately for the narrower/faster cases. Scouting-tier by construction — every step here belongs to Scouting, unlike the automate_full bundle which runs all 20 steps including Assessment- and Analysis-tier ones.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | scouting |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoScoutingSurvey

### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_git_statistics

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_git_statistics

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_inventory

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_inventory

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_manifest_parse

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_manifest_parse

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_health

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_health

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_language

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_language

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_structure

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_file_structure

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_data_profiling

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_data_profiling

### Next Governance Action Process Step
GovActionProcessStep::RepoScoutingSurvey::repo_homepage

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_git_statistics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-git-statistics

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_git_statistics to Resource Explorer's engine, which claims it and runs RE step repo_git_statistics.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_file_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-inventory

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_file_inventory to Resource Explorer's engine, which claims it and runs RE step repo_file_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_manifest_parse

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-manifest-parse

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_manifest_parse to Resource Explorer's engine, which claims it and runs RE step repo_manifest_parse.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_health

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-health

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_health to Resource Explorer's engine, which claims it and runs RE step repo_health.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_language

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-language

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_language to Resource Explorer's engine, which claims it and runs RE step repo_language.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_file_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-classification

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_file_classification to Resource Explorer's engine, which claims it and runs RE step repo_file_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_file_structure

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-structure

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_file_structure to Resource Explorer's engine, which claims it and runs RE step repo_file_structure.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_data_profiling

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-data-profiling

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_data_profiling to Resource Explorer's engine, which claims it and runs RE step repo_data_profiling.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoScoutingSurvey::repo_homepage

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-homepage

### Description
Egeria dispatches GovActionProcessStep::RepoScoutingSurvey::repo_homepage to Resource Explorer's engine, which claims it and runs RE step repo_homepage.

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
Is this repository actively maintained?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
Who owns this resource (accountable owner), and who administers it?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
How widely adopted and active is the community around this repository?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
How is it supported?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
What languages and file types make up this repository?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
How much code is there? How complex?

___

## Link Element To Scope
### Target Element
Scouting Survey

### Scope Reference
What data files does it ship, and what shape are they?

