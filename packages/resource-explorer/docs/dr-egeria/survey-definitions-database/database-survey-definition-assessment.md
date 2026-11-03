## Create Governance Action Process Step
### Display Name
Database Assessment Survey — Postgres Operations

### Qualified Name
GovActionProcessStep::DatabaseAssessmentSurvey::postgres_operations

### Description
privilege_audit, db_activity_signals, db_resilience, db_external_dependencies.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_operations |

___

## Create Governance Action Process
### Display Name
Database Assessment Survey

### Qualified Name
GovActionProcess::DatabaseAssessmentSurvey

### Description
The catalog-intent: assessment Postgres database analyses. Currently a single-analysis document — privilege_audit (via postgres_operations) is the only database analysis tagged intent: assessment in analysis_catalog.yaml today; more will be added here as new assessment-tier database analyses land, matching the coordinator brief's own instruction to author this document honestly with what exists rather than inventing steps.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |
| survey_kind | assessment |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::DatabaseAssessmentSurvey

### Governance Action Process Step
GovActionProcessStep::DatabaseAssessmentSurvey::postgres_operations

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAssessmentSurvey::postgres_operations

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-operations

### Description
Egeria dispatches GovActionProcessStep::DatabaseAssessmentSurvey::postgres_operations to Resource Explorer's engine, which claims it and runs RE step postgres_operations.

___

## Link Element To Scope
### Target Element
Database Assessment Survey

### Scope Reference
Who can read and write what — roles, grants, superusers, PUBLIC grants — which sensitive columns can which roles read, and what has changed?

