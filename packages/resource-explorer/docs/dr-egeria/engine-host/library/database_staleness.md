<!--
GENERATED from the manifest of 'database_staleness' (trellis-step-database-staleness 0.2.1) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py. Do not hand-edit; regenerate.
Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §4.3 (engine host, step 3).

Declares the Analytic Library step 'database_staleness' as a service of Resource Explorer's engine, so Egeria can
dispatch it: initiating ResourceExplorerSurvey::re-database-staleness creates an engine action with request type re-database-staleness, which RE's engine host
claims, runs through its step host and completes with one of survey-completed, survey-invalid, survey-failed
(survey-completed when there is nothing to derive).

Run it with `resource-explorer dr-egeria run engine-host/library/database_staleness.md`. RE's bootstrap never runs it.
Its pyegeria steps (database_staleness.pyegeria-steps.json) run first, each find-or-create:
  - SurveyActionEngine `ResourceExplorerSurvey` (normally already there, from the engine's own document);
  - SurveyActionService `SurveyActionService::ResourceExplorerSurvey::database_staleness`, linked to the engine by
    SupportedGovernanceService {requestType: "re-database-staleness", serviceRequestType: "re-database-staleness"}.
"Link Action to Action Executor" below is NOT safe to repeat; the runner refuses a second run of the same content
unless forced. Retire the step by setting Content Status to DEPRECATED, never by deleting the element.
-->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — Database Staleness

### Qualified Name
ResourceExplorerSurvey::re-database-staleness

### Description
Rows changed per table since the last survey: compares the latest two row-count snapshots Resource Explorer stored for the database. Reads only what RE already stored; opens no connection to the database. Shown as "not measured", never as zero, when fewer than two row-count snapshots exist, or a table or its count is missing from one.

### Produced Guards
survey-completed, survey-invalid, survey-failed

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | database_staleness |
| engine_host | resource-explorer-worker |
| request_type | re-database-staleness |
| nothing_to_derive_guard | survey-completed |
| kind | step |
| source | ods |
| version | 0.2.1 |
| resource_types | database |
| annotation_type | ResourceMeasureAnnotation |
| questions | Q-0041 |
| purposes | Maintain |
| perspectives | Steward, Data Expert |
| credential_tier | none |
| inputs | ods, source_report |
| measures | database_staleness, writes_since_statistics_reset |
| family | Freshness |
| function | database_staleness.step.run |
| package | trellis-step-database-staleness |
| entry_point | trellis.analytics:database_staleness |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-database-staleness

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-database-staleness

### Description
Egeria dispatches ResourceExplorerSurvey::re-database-staleness to Resource Explorer's engine, which claims it and runs Analytic Library step database_staleness.

___
