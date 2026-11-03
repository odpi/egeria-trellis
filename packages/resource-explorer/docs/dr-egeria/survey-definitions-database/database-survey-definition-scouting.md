## Create Governance Action Process Step
### Display Name
Database Scouting Scan — Postgres Schema And Stats

### Qualified Name
GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats

### Description
Schema, table and column inventory plus row-count/size statistics.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_schema_and_stats |

___

## Create Governance Action Process Step
### Display Name
Database Scouting Scan — Postgres Operations

### Qualified Name
GovActionProcessStep::DatabaseScoutingSurvey::postgres_operations

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

## Create Governance Action Process Step
### Display Name
Database Scouting Scan — Credential Capability

### Qualified Name
GovActionProcessStep::DatabaseScoutingSurvey::credential_capability

### Description
Read-only catalog/privilege introspection: what this credential can see and do.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | credential_capability |

___

## Create Governance Action Process
### Display Name
Database Scouting Scan

### Qualified Name
GovActionProcess::DatabaseScoutingSurvey

### Description
The fast, catalog/read-tier pass over a Postgres database — schema inventory (schema_inventory), row counts (row_count_snapshot), credential visibility (credential_capability), and the operations bundle's scouting-tier half (db_activity_signals, db_resilience). postgres_operations also backs an analysis-tier analysis (db_external_dependencies) and an assessment-tier one (privilege_audit) that this step cannot be split apart from — it is chained into all three Survey Definitions (Scouting, Analysis, Assessment) as a result, each under its own qualified name, so each document's ScopedBy links only claim the questions its own analyses answer.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |
| survey_kind | scouting |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::DatabaseScoutingSurvey

### Governance Action Process Step
GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats

### Next Governance Action Process Step
GovActionProcessStep::DatabaseScoutingSurvey::postgres_operations

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseScoutingSurvey::postgres_operations

### Next Governance Action Process Step
GovActionProcessStep::DatabaseScoutingSurvey::credential_capability

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-schema-and-stats

### Description
Egeria dispatches GovActionProcessStep::DatabaseScoutingSurvey::postgres_schema_and_stats to Resource Explorer's engine, which claims it and runs RE step postgres_schema_and_stats.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseScoutingSurvey::postgres_operations

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-operations

### Description
Egeria dispatches GovActionProcessStep::DatabaseScoutingSurvey::postgres_operations to Resource Explorer's engine, which claims it and runs RE step postgres_operations.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseScoutingSurvey::credential_capability

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-credential-capability

### Description
Egeria dispatches GovActionProcessStep::DatabaseScoutingSurvey::credential_capability to Resource Explorer's engine, which claims it and runs RE step credential_capability.

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
How big is this database — schemas, tables, views, columns, rows and bytes?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
Which schemas carry the data, and which are system, empty or staging?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
What is the full schema — tables, columns, types, keys, constraints and indexes?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
Is this database alive — writes since the statistics were reset, last vacuum or analyse, and is anything reading it?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
Is this database a primary or a replica, is it clustered, and is WAL archiving or backup configured?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
When was this resource last backed up successfully, and has a restore ever been tested?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
Is this database maintained and healthy — vacuum recency, dead-tuple ratio and unused indexes?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
Is this database resilient — replica present, archiving on, backup evidence and a tested restore?

___

## Link Element To Scope
### Target Element
Database Scouting Scan

### Scope Reference
What can the survey credential see, and what is it blind to — schemas, tables, system views, statistics?

