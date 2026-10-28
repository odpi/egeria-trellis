## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Postgres Column Profile

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_column_profile

### Description
Bounded value sampling, data_class_match, reference_data_match.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_column_profile |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Postgres Nested Columns

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_nested_columns

### Description
Bounded sampling and nested-schema inference for JSON/JSONB/XML columns.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | postgres_nested_columns |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Db Derived

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::db_derived

### Description
Zero-fetch derivation over already-stored rows.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | db_derived |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Postgres Operations

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_operations

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
Database Analysis Survey — Change Rates

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::change_rates

### Description
Rows inserted, updated and deleted per day per table between the latest two table activity snapshots Resource Explorer stored, over their interval; a counter reset between them gives no rate, never a negative one. Opens no connection to the database. With one activity snapshot, or a table missing from one, it is shown as "not measured", never as a rate of zero.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | change_rates |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Database Staleness

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::database_staleness

### Description
Rows changed per table since the last survey: compares the latest two row-count snapshots Resource Explorer stored for the database. Reads only what RE already stored; opens no connection to the database. Shown as "not measured", never as zero, when fewer than two row-count snapshots exist, or a table or its count is missing from one.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | database_staleness |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Hub Tables

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::hub_tables

### Description
Foreign-key degree in and out per table, from the keys Resource Explorer stored, and the tables it proposes as hubs (referenced by at least hub_min_referencing other tables, a declared parameter; a count from partly captured keys is a lower bound, reported as at least N). The proposal is an annotation, never a classification on the table. Opens no connection to the database. With no foreign keys stored it is shown as "not measured", never as a database with no hubs.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | hub_tables |

___

## Create Governance Action Process Step
### Display Name
Database Analysis Survey — Statistics Freshness

### Qualified Name
GovActionProcessStep::DatabaseAnalysisSurvey::statistics_freshness

### Description
How old each table's planner statistics are: the age of the last ANALYZE and VACUUM, tables never analysed, and tables changed past autovacuum's analyse trigger, as of the newest table activity snapshot Resource Explorer stored. Opens no connection to the database. A table whose statistics were not read, or a database with none stored, is shown as "not measured", never as fresh.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | PostgreSQL Database |
| re_analysis_step | statistics_freshness |

___

## Create Governance Action Process
### Display Name
Database Analysis Survey

### Qualified Name
GovActionProcess::DatabaseAnalysisSurvey

### Description
Every catalog-intent: analysis Postgres database analysis: value sampling and data-class/reference-data matching (postgres_column_profile), nested JSON/JSONB/XML column profiling (postgres_nested_columns), the derived-from-stored-rows analyses (db_derived: schema_conventions, db_change_rates, schema_diff, grant_change), and the operations bundle's analysis-tier member (db_external_dependencies, via postgres_operations — see that step's own note in this CSV for why it is chained into more than one Survey Definition), and the installed library steps change_rates, database_staleness, hub_tables and statistics_freshness.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | PostgreSQL Database |
| survey_kind | analysis |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::DatabaseAnalysisSurvey

### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_column_profile

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_column_profile

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_nested_columns

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_nested_columns

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::db_derived

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::db_derived

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_operations

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_operations

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::change_rates

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::change_rates

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::database_staleness

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::database_staleness

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::hub_tables

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::hub_tables

### Next Governance Action Process Step
GovActionProcessStep::DatabaseAnalysisSurvey::statistics_freshness

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_column_profile

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-column-profile

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::postgres_column_profile to Resource Explorer's engine, which claims it and runs RE step postgres_column_profile.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_nested_columns

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-nested-columns

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::postgres_nested_columns to Resource Explorer's engine, which claims it and runs RE step postgres_nested_columns.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::db_derived

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-db-derived

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::db_derived to Resource Explorer's engine, which claims it and runs RE step db_derived.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::postgres_operations

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-postgres-operations

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::postgres_operations to Resource Explorer's engine, which claims it and runs RE step postgres_operations.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::change_rates

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-change-rates

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::change_rates to Resource Explorer's engine, which claims it and runs RE step change_rates.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::database_staleness

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-database-staleness

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::database_staleness to Resource Explorer's engine, which claims it and runs RE step database_staleness.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::hub_tables

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-hub-tables

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::hub_tables to Resource Explorer's engine, which claims it and runs RE step hub_tables.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::DatabaseAnalysisSurvey::statistics_freshness

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-statistics-freshness

### Description
Egeria dispatches GovActionProcessStep::DatabaseAnalysisSurvey::statistics_freshness to Resource Explorer's engine, which claims it and runs RE step statistics_freshness.

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
What do the columns actually contain — nulls, distinct counts, most-common values, ranges and widths?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
Which columns conform to a known Data Class, with what confidence, and which look like a class we do not have yet?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
Which low-cardinality columns conform to a known reference-data set, and which should become one?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
Who can read and write what — roles, grants, superusers, PUBLIC grants, and are sensitive columns readable broadly?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
What is the sensitive-data exposure — which sensitive columns can which roles read?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
How well-governed is this database's reference data — what share of its low-cardinality columns are bound to a set?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
Which columns hold semi-structured data (JSON, JSONB, XML, arrays or hstore), and how much of the data sits in them?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
What is inside the JSON, JSONB and XML columns — keys, nesting, types, and consistency across rows?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
Which tables have no primary key, no foreign key, no comment, unused indexes, or names that break the convention?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
How is this database changing — rows inserted, updated and deleted per table per period, size drift and schema churn?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
How complete and consistent is this database's documentation?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
How well-modelled is this database — keys, constraints, foreign-key coverage, orphan tables and grain clarity?

___

## Link Element To Scope
### Target Element
Database Analysis Survey

### Scope Reference
What does this database depend on outside itself — foreign data wrappers, dblink, extensions or publications?

