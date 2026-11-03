<!--
GENERATED from the manifest of 'change_rates' (trellis-step-change-rates 0.1.0) by
packages/resource-explorer/scripts/generate_engine_host_declaration.py. Do not hand-edit; regenerate.
Design: docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md §4.3 (engine host, step 3).

Declares the Analytic Library step 'change_rates' as a service of Resource Explorer's engine, so Egeria can
dispatch it: initiating ResourceExplorerSurvey::re-change-rates creates an engine action with request type re-change-rates, which RE's engine host
claims, runs through its step host and completes with one of survey-completed, survey-invalid, survey-failed
(survey-completed when there is nothing to derive).

Run it with `resource-explorer dr-egeria run engine-host/library/change_rates.md`. RE's bootstrap never runs it.
Its pyegeria steps (change_rates.pyegeria-steps.json) run first, each find-or-create:
  - SurveyActionEngine `ResourceExplorerSurvey` (normally already there, from the engine's own document);
  - SurveyActionService `SurveyActionService::ResourceExplorerSurvey::change_rates`, linked to the engine by
    SupportedGovernanceService {requestType: "re-change-rates", serviceRequestType: "re-change-rates"}.
"Link Action to Action Executor" below is NOT safe to repeat; the runner refuses a second run of the same content
unless forced. Retire the step by setting Content Status to DEPRECATED, never by deleting the element.
-->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — Table Change Rates

### Qualified Name
ResourceExplorerSurvey::re-change-rates

### Description
Rows inserted, updated and deleted per day per table between the latest two table activity snapshots Resource Explorer stored, over their interval; a counter reset between them gives no rate, never a negative one. Opens no connection to the database. With one activity snapshot, or a table missing from one, it is shown as "not measured", never as a rate of zero.

### Produced Guards
survey-completed, survey-invalid, survey-failed

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | change_rates |
| engine_host | resource-explorer-worker |
| request_type | re-change-rates |
| nothing_to_derive_guard | survey-completed |
| kind | step |
| source | ods |
| version | 0.1.0 |
| resource_types | database |
| annotation_type | ResourceMeasureAnnotation |
| questions | Q-0086 |
| purposes | Maintain, Assess |
| perspectives | Steward, Data Expert |
| credential_tier | none |
| inputs | ods |
| measures | change_rates |
| family | Freshness |
| function | change_rates.step.run |
| package | trellis-step-change-rates |
| entry_point | pyegeria.analytics:change_rates |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-change-rates

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-change-rates

### Description
Egeria dispatches ResourceExplorerSurvey::re-change-rates to Resource Explorer's engine, which claims it and runs Analytic Library step change_rates.

___
