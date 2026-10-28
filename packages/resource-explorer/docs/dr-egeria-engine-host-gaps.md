# Dr.Egeria gaps found declaring RE's engine host (for egeria-python)

**Status:** live. Request to the egeria-python maintainers (2026-10-14); open until they respond.

Written 2026-10-14 for the egeria-python maintainers. Resource Explorer (RE) declares itself to Egeria as the host
of one survey engine (`docs/design-notes/DESIGN-RE-ENGINE-HOST-BY-EQUIVALENCE.md` §5.1). The declaration is
generated into `docs/dr-egeria/engine-host/`. pyegeria 6.2.3's Dr.Egeria can express two of its five parts:
`Create Governance Action Type` and `Link Action to Action Executor`. It has no command for the other three, so RE
runs them as pyegeria API calls (`resource_explorer/dr_egeria_runner.py`, "pyegeria steps"). This note asks for the
three commands. It is a request, not a change: egeria-python is not edited from this repo.

## The three missing commands

### 1. Create Survey Action Engine

- Element: `SurveyActionEngine` (a `GovernanceEngine`, a `SoftwareCapability`).
- Egeria bean: `SurveyActionEngineProperties` (`frameworks/openmetadata/properties/softwarecapabilities`).
- Endpoint: Asset Maker `POST .../asset-maker/software-capabilities`.
- pyegeria call RE uses today: `AssetMaker.create_software_capability(body)` with `properties.class =
  "SurveyActionEngineProperties"` and `typeName = "SurveyActionEngine"`.
- Attributes RE needs: Display Name, Qualified Name, Description. The sibling engine types
  (`GovernanceActionEngine`, `WatchdogActionEngine`, `ExplorerActionEngine`, `RepositoryGovernanceEngine`) would
  want the same command shape, so one `Create Governance Engine` command with a `Type Name` attribute may be
  the better fit.

### 2. Create Survey Action Service

- Element: `SurveyActionService` (a `GovernanceService`, a `DeployedConnector` asset).
- Egeria bean: `SurveyActionServiceProperties` (`frameworks/openmetadata/properties/assets/processes/connectors`).
- Endpoint: Asset Maker `POST .../asset-maker/assets`.
- pyegeria call RE uses today: `AssetMaker.create_asset(body=...)` with `properties.class =
  "SurveyActionServiceProperties"` and `typeName = "SurveyActionService"`.
- Attributes RE needs: Display Name, Qualified Name, Description. A JVM-hosted service would also need its
  connection (connector provider class), which RE's service does not have because RE hosts it itself.
- As with engines, one `Create Governance Service` command with a `Type Name` attribute would cover the sibling
  service types.

### 3. Link Supported Governance Service

- Relationship: `SupportedGovernanceService`, end 1 the governance engine, end 2 the governance service.
  **Multi-link** (`OpenMetadataTypesArchive2_6`: `setMultiLink(true)`): one engine may call the same service under
  several request types.
- Properties: `requestType`, `serviceRequestType`, `requestParameters`, `generateConnectorActivityReports`,
  `deleteMethod` (`SupportedGovernanceServiceProperties`).
- Endpoint: Asset Maker `POST .../governance-engines/{engineGUID}/supported-governance-services/{serviceGUID}/attach`.
- pyegeria call RE uses today: `AssetMaker.link_supported_governance_service(governance_engine_guid,
  governance_service_guid, body)`.
- **Idempotency.** Because the relationship is multi-link, a command that attaches without reading first adds
  a second registration on every re-run. RE's pyegeria step reads the engine's existing links first
  (`MetadataExpert.get_related_metadata_elements(engine_guid, "SupportedGovernanceService", body,
  starting_at_end=1)`) and creates only when no link to that service with that `requestType` exists. A Dr.Egeria
  command should do the same, keyed by (engine, service, requestType), and say "found" on a repeat. Dr.Egeria's
  existing link commands do not do this. RE's `scripts/reconcile_survey_definition_links.py` exists because
  `Link First/Next Process Step` duplicated every edge on a re-run.

## Smaller findings while reading pyegeria 6.2.3

- `AssetMaker` defines `link_supported_governance_service` (and its async, update and detach siblings) twice in
  `pyegeria/omvs/asset_maker.py`. The second definition wins. The two bodies are the same today, so nothing is
  broken, but one should go.
- The `dr_egeria` CLI exits 0 when a command fails and when the client raises (the exception is printed and
  swallowed), so a caller cannot use the exit code as an answer. RE's runner reads the summary table instead,
  and treats a run with no readable table as `unknown`, never as success. A machine-readable result (JSON, or a
  non-zero exit when any command fails) would help every caller. RE's bootstrap heal still uses the exit code,
  so it reports such runs as `ok`; its canary re-check is what catches them.

## What RE does until the commands exist

`docs/dr-egeria/engine-host/resource-explorer-survey-engine.pyegeria-steps.json` (generated beside the document)
names the three operations. `resource-explorer dr-egeria run engine-host/resource-explorer-survey-engine.md` runs
them first, in the order engine, service, link, then the document. When Dr.Egeria gains the commands, the steps
move into the document and the steps file is deleted.
