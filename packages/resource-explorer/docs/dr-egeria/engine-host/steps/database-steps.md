<!--
GENERATED from the database step registry by packages/resource-explorer/scripts/generate_engine_host_declaration.py.
Do not hand-edit; regenerate. Design: docs/design-notes/SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md
(C1, decision 1: one SurveyActionService per step key, never per definition).

Declares 6 database step(s) as services of Resource Explorer's engine, so the GovernanceActionExecutor
each Survey Definition step carries (C2, dr_egeria_survey_publisher) names a request type the engine advertises. Each:
one SurveyActionService, one SupportedGovernanceService {requestType: re-<step key>} (both in
database-steps.pyegeria-steps.json, run first by `resource-explorer dr-egeria run`, find-or-create), then
the GovernanceActionType ResourceExplorerSurvey::re-<step key> with producedGuards from step_outcome.py's mapping, and
its GovernanceActionExecutor below.
Not here, declared by the engine's own document: db_derived.

Run it with `resource-explorer dr-egeria run engine-host/steps/database-steps.md` AFTER the
engine's own document. RE's bootstrap never runs it. "Link Action to Action Executor" is NOT safe to repeat; the runner
refuses a second run of the same content unless forced. Retire a step by Content Status DEPRECATED, never by deleting.
-->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — postgres_schema_and_stats

### Qualified Name
ResourceExplorerSurvey::re-postgres-schema-and-stats

### Description
Schema, table and column inventory plus row-count/size statistics.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | postgres_schema_and_stats |
| engine_host | resource-explorer-worker |
| request_type | re-postgres-schema-and-stats |
| entity_type | database |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | read |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-postgres-schema-and-stats

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-schema-and-stats

### Description
Egeria dispatches ResourceExplorerSurvey::re-postgres-schema-and-stats to Resource Explorer's engine, which claims it and runs RE step postgres_schema_and_stats.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — postgres_operations

### Qualified Name
ResourceExplorerSurvey::re-postgres-operations

### Description
privilege_audit, db_activity_signals, db_resilience, db_external_dependencies.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | postgres_operations |
| engine_host | resource-explorer-worker |
| request_type | re-postgres-operations |
| entity_type | database |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | stats |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-postgres-operations

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-operations

### Description
Egeria dispatches ResourceExplorerSurvey::re-postgres-operations to Resource Explorer's engine, which claims it and runs RE step postgres_operations.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — postgres_column_profile

### Qualified Name
ResourceExplorerSurvey::re-postgres-column-profile

### Description
Bounded value sampling, data_class_match, reference_data_match.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | postgres_column_profile |
| engine_host | resource-explorer-worker |
| request_type | re-postgres-column-profile |
| entity_type | database |
| fetch_cost | api_heavy |
| compute_cost | medium |
| requires_capability | read |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-postgres-column-profile

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-column-profile

### Description
Egeria dispatches ResourceExplorerSurvey::re-postgres-column-profile to Resource Explorer's engine, which claims it and runs RE step postgres_column_profile.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — postgres_nested_columns

### Qualified Name
ResourceExplorerSurvey::re-postgres-nested-columns

### Description
Bounded sampling and nested-schema inference for JSON/JSONB/XML columns.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | postgres_nested_columns |
| engine_host | resource-explorer-worker |
| request_type | re-postgres-nested-columns |
| entity_type | database |
| fetch_cost | api_heavy |
| compute_cost | medium |
| requires_capability | read |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-postgres-nested-columns

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-nested-columns

### Description
Egeria dispatches ResourceExplorerSurvey::re-postgres-nested-columns to Resource Explorer's engine, which claims it and runs RE step postgres_nested_columns.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — sql_analysis

### Qualified Name
ResourceExplorerSurvey::re-sql-analysis

### Description
SQL view dependencies, column-level lineage and complexity scores.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | sql_analysis |
| engine_host | resource-explorer-worker |
| request_type | re-sql-analysis |
| entity_type | database |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | read |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-sql-analysis

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-sql-analysis

### Description
Egeria dispatches ResourceExplorerSurvey::re-sql-analysis to Resource Explorer's engine, which claims it and runs RE step sql_analysis.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — credential_capability

### Qualified Name
ResourceExplorerSurvey::re-credential-capability

### Description
Read-only catalog/privilege introspection: what this credential can see and do.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | credential_capability |
| engine_host | resource-explorer-worker |
| request_type | re-credential-capability |
| entity_type | database |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | catalog |
| engine_host_dispatch | runs |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-credential-capability

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-credential-capability

### Description
Egeria dispatches ResourceExplorerSurvey::re-credential-capability to Resource Explorer's engine, which claims it and runs RE step credential_capability.

___
