<!--
GENERATED from MIXED_PROCESSES["postgres_survey_then_derived"] (resource_explorer/mixed_process.py) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py --mixed-process. Do not hand-edit; regenerate.
Design: docs/design-notes/SLICE-ENGINE-HOST-STEP4-MIXED-PROCESS.md (M1).

Declares GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived: 2 steps, each run by the engine its executor names, chained by
NextGovernanceActionProcessStep links whose guards Egeria evaluates. A plain GovernanceActionProcess: Dr.Egeria cannot
create the SurveyingActionProcess subtype yet (egeria-python ISSUE-150); one line changes when it can.

Needs, already declared: RE's engine ResourceExplorerSurvey serving re-database-staleness (step 2)
(engine-host/library/), and the content pack's engines. Every element here has a Dr.Egeria command, so there is no
pyegeria steps file. Run it with `resource-explorer dr-egeria run engine-host/processes/postgres-survey-then-derived.md`;
RE's bootstrap never runs it. The Link commands are NOT safe to repeat; the runner refuses a second run of the same
content unless forced. Start a run with Dr.Egeria's Initiate Governance Action Process, or RE's Run on the database.
-->

## Create Governance Action Process
### Display Name
Postgres survey then derived

### Qualified Name
GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived

### Description
Egeria's own PostgreSQL survey of the database, then Resource Explorer's database staleness derived from the report that survey just wrote. Step 2 runs only when step 1 completes survey-completed.

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| declared_by | resource-explorer |
| mixed_process | postgres_survey_then_derived |
| entity_type | database |

___

## Create Governance Action Process Step
### Display Name
Postgres survey then derived — 1 PostgreSQL survey

### Qualified Name
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::1-PostgreSQLSurvey

### Description
Egeria's native PostgreSQL database survey (Java, the content pack's PostgreSQLSurvey engine).

### Produced Guards
survey-completed, survey-invalid, survey-failed

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| governance_action_type | PostgreSQLSurvey::survey-postgres-database |
| runs_on | egeria |
| request_type | survey-postgres-database |
| process | GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived |
| step_number | 1 |

___

## Create Governance Action Process Step
### Display Name
Postgres survey then derived — 2 database staleness

### Qualified Name
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::2-re-database-staleness

### Description
Resource Explorer's database staleness, derived from step 1's survey report (the surveyReport action target). Run by Resource Explorer's engine host.

### Produced Guards
survey-completed, survey-invalid, survey-failed

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| governance_action_type | ResourceExplorerSurvey::re-database-staleness |
| runs_on | resource-explorer |
| request_type | re-database-staleness |
| process | GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived |
| step_number | 2 |

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::1-PostgreSQLSurvey

### Governance Engine
PostgreSQLSurvey

### Request Type
survey-postgres-database

### Description
Step 1 of GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived runs as PostgreSQLSurvey::survey-postgres-database does: engine
PostgreSQLSurvey, request type survey-postgres-database.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::2-re-database-staleness

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-database-staleness

### Description
Step 2 of GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived runs as ResourceExplorerSurvey::re-database-staleness does: engine
ResourceExplorerSurvey, request type re-database-staleness.

___

## Link First Process Step
### Governance Action Process
GovActionProcess::ResourceExplorer::PostgresSurveyThenDerived

### Governance Action Process Step
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::1-PostgreSQLSurvey

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::1-PostgreSQLSurvey

### Next Governance Action Process Step
GovActionProcessStep::ResourceExplorer::PostgresSurveyThenDerived::2-re-database-staleness

### Guard
survey-completed

### Mandatory Guard
true

___
