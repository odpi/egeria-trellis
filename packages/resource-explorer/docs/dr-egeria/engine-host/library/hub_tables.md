<!--
GENERATED from the manifest of 'hub_tables' (trellis-step-hub-tables 0.2.0) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py. Do not hand-edit; regenerate.
Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §4.3 (engine host, step 3).

Declares the Analytic Library step 'hub_tables' as a service of Resource Explorer's engine, so Egeria can
dispatch it: initiating ResourceExplorerSurvey::re-hub-tables creates an engine action with request type re-hub-tables, which RE's engine host
claims, runs through its step host and completes with one of survey-completed, survey-invalid, survey-failed
(survey-completed when there is nothing to derive).

Run it with `resource-explorer dr-egeria run engine-host/library/hub_tables.md`. RE's bootstrap never runs it.
Its pyegeria steps (hub_tables.pyegeria-steps.json) run first, each find-or-create:
  - SurveyActionEngine `ResourceExplorerSurvey` (normally already there, from the engine's own document);
  - SurveyActionService `SurveyActionService::ResourceExplorerSurvey::hub_tables`, linked to the engine by
    SupportedGovernanceService {requestType: "re-hub-tables", serviceRequestType: "re-hub-tables"}.
"Link Action to Action Executor" below is NOT safe to repeat; the runner refuses a second run of the same content
unless forced. Retire the step by setting Content Status to DEPRECATED, never by deleting the element.
-->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — Hub Tables

### Qualified Name
ResourceExplorerSurvey::re-hub-tables

### Description
Foreign-key degree in and out per table, from the keys Resource Explorer stored, and the tables it proposes as hubs (referenced by at least hub_min_referencing other tables, a declared parameter; a count from partly captured keys is a lower bound, reported as at least N). The proposal is an annotation, never a classification on the table. Opens no connection to the database. With no foreign keys stored it is shown as "not measured", never as a database with no hubs.

### Produced Guards
survey-completed, survey-invalid, survey-failed

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | hub_tables |
| engine_host | resource-explorer-worker |
| request_type | re-hub-tables |
| nothing_to_derive_guard | survey-completed |
| kind | step |
| source | ods |
| version | 0.2.0 |
| resource_types | database |
| annotation_type | ResourceMeasureAnnotation |
| questions | Q-0072, Q-0068 |
| purposes | Explore, Integrate |
| perspectives | Data Expert, Steward |
| credential_tier | none |
| inputs | ods |
| measures | hub_tables, hub_table_proposal |
| family | Structure |
| function | hub_tables.step.run |
| package | trellis-step-hub-tables |
| entry_point | trellis.analytics:hub_tables |
| parameters | hub_min_referencing=3 |
| measure_annotation_types | hub_table_proposal: ClassificationAnnotation |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-hub-tables

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-hub-tables

### Description
Egeria dispatches ResourceExplorerSurvey::re-hub-tables to Resource Explorer's engine, which claims it and runs Analytic Library step hub_tables.

___
