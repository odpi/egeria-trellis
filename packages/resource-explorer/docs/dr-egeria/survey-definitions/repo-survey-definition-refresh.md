## Create Governance Action Process Step
### Display Name
Refresh Survey — Repo Refresh Plan

### Qualified Name
GovActionProcessStep::RepoRefreshSurvey::repo_refresh_plan

### Description
What a refresh would actually need to do: which targets have never run, which are stale against the current head commit, and which are current. One GitHub call, no archive download. ADVISORY — the executor runs every step regardless, so this records the decision rather than enforcing it.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_refresh_plan |

___

## Create Governance Action Process Step
### Display Name
Refresh Survey — Repo File Inventory

### Qualified Name
GovActionProcessStep::RepoRefreshSurvey::repo_file_inventory

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
Refresh Survey — Repo Manifest Parse

### Qualified Name
GovActionProcessStep::RepoRefreshSurvey::repo_manifest_parse

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
Refresh Survey — Repo Rag Ingestion

### Qualified Name
GovActionProcessStep::RepoRefreshSurvey::repo_rag_ingestion

### Description
Refreshes the project's pgvector collections via IncrementalIndexer — the queryable representation Chat, the query router and every RAG-backed answer read, previously built only at registration, on webhook or from a bespoke route branch and never by a survey step. A no-op when the repository's last indexed commit is unchanged.

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | prefect |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_rag_ingestion |

___

## Create Governance Action Process Step
### Display Name
Refresh Survey — Repo Website Ingestion

### Qualified Name
GovActionProcessStep::RepoRefreshSurvey::repo_website_ingestion

### Description
Ingests the project's documentation site into pgvector as web_docs_{host}, so Chat and Understanding can answer from the project's own documentation rather than only its source tree. Keyed on the site's host, not the repo slug — several repos in one project share one site and therefore one collection. Uses the site repo_homepage derived, collapsing versioned docs to the current release; skips entirely when the repo builds that site itself, since the source is already ingested in a better form.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_website_ingestion |

___

## Create Governance Action Process
### Display Name
Refresh Survey

### Qualified Name
GovActionProcess::RepoRefreshSurvey

### Description
Bring a repo's derived data back up to date: file inventory and data profiles, dependency/CI/convention parsing, pgvector re-embedding, and the documentation site. Begins with Refresh Plan, which reads stored state and the current head commit to record which targets are stale, which never ran, and which are already current — judged per target, because "unchanged" and "complete" are different questions and a target that never ran needs work whatever the commit says. That plan is ADVISORY in this version: the executor runs every step in the graph regardless, so the plan records the decision rather than enforcing it. Lives in Automate because a refresh is sustained machine attention — the thing you schedule, not the thing you launch to answer a question.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | refresh |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoRefreshSurvey

### Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_refresh_plan

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_refresh_plan

### Next Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_file_inventory

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_file_inventory

### Next Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_manifest_parse

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_manifest_parse

### Next Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_rag_ingestion

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_rag_ingestion

### Next Governance Action Process Step
GovActionProcessStep::RepoRefreshSurvey::repo_website_ingestion

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoRefreshSurvey::repo_refresh_plan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-refresh-plan

### Description
Egeria dispatches GovActionProcessStep::RepoRefreshSurvey::repo_refresh_plan to Resource Explorer's engine, which claims it and runs RE step repo_refresh_plan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoRefreshSurvey::repo_file_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-inventory

### Description
Egeria dispatches GovActionProcessStep::RepoRefreshSurvey::repo_file_inventory to Resource Explorer's engine, which claims it and runs RE step repo_file_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoRefreshSurvey::repo_manifest_parse

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-manifest-parse

### Description
Egeria dispatches GovActionProcessStep::RepoRefreshSurvey::repo_manifest_parse to Resource Explorer's engine, which claims it and runs RE step repo_manifest_parse.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoRefreshSurvey::repo_website_ingestion

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-website-ingestion

### Description
Egeria dispatches GovActionProcessStep::RepoRefreshSurvey::repo_website_ingestion to Resource Explorer's engine, which claims it and runs RE step repo_website_ingestion.

___

## Link Element To Scope
### Target Element
Refresh Survey

### Scope Reference
How much has changed since the last time this was surveyed — is it worth re-running now?

