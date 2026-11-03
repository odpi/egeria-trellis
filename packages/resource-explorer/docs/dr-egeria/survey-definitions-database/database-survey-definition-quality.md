## Create Governance Action Process Step
### Display Name
Database Quality Survey — Postgres Value Profile

### Qualified Name
GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile

### Description
Measured (not estimated) per-column counts and date bounds, whole table.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_value_profile |

___

## Create Governance Action Process Step
### Display Name
Database Quality Survey — Propose Quality Checks

### Qualified Name
GovActionProcessStep::DatabaseQualitySurvey::propose_quality_checks

### Description
Zero-fetch: the vendored selection rules over the latest value profile, as proposed checks.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | propose_quality_checks |

___

## Create Governance Action Process Step
### Display Name
Database Quality Survey — Run Quality Checks

### Qualified Name
GovActionProcessStep::DatabaseQualitySurvey::run_quality_checks

### Description
The accepted quality checks, run with the vendored Postgres SQL against the database.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | run_quality_checks |

___

## Create Governance Action Process
### Display Name
Database Quality Survey

### Qualified Name
GovActionProcess::DatabaseQualitySurvey

### Description
Data-quality checks on one Postgres database, in three steps: a measured value profile of every table (postgres_value_profile, 5 minutes at most per table), the proposed checks (propose_quality_checks, zero-fetch, for a person to accept or reject in Curate), and the accepted checks run against the database (run_quality_checks), each evaluated check published as one Egeria QualityAnnotation. With no accepted check the last step ends survey-invalid ("no accepted checks"), never as an empty report.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |
| survey_kind | assessment |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::DatabaseQualitySurvey

### Governance Action Process Step
GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile

### Next Governance Action Process Step
GovActionProcessStep::DatabaseQualitySurvey::propose_quality_checks

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseQualitySurvey::propose_quality_checks

### Next Governance Action Process Step
GovActionProcessStep::DatabaseQualitySurvey::run_quality_checks

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-value-profile

### Description
Egeria dispatches GovActionProcessStep::DatabaseQualitySurvey::postgres_value_profile to Resource Explorer's engine, which claims it and runs RE step postgres_value_profile.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseQualitySurvey::propose_quality_checks

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-propose-quality-checks

### Description
Egeria dispatches GovActionProcessStep::DatabaseQualitySurvey::propose_quality_checks to Resource Explorer's engine, which claims it and runs RE step propose_quality_checks.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseQualitySurvey::run_quality_checks

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-run-quality-checks

### Description
Egeria dispatches GovActionProcessStep::DatabaseQualitySurvey::run_quality_checks to Resource Explorer's engine, which claims it and runs RE step run_quality_checks.

___

## Link Element To Scope
### Target Element
Database Quality Survey

### Scope Reference
What do the columns actually contain — nulls, distinct counts, most-common values, ranges and widths?

___

## Link Element To Scope
### Target Element
Database Quality Survey

### Scope Reference
Which data-quality checks should this database have?

___

## Link Element To Scope
### Target Element
Database Quality Survey

### Scope Reference
Which data-quality checks hold on this database's tables, and which fail?

