## Create Governance Action Process Step
### Display Name
Filesystem Scouting Survey — Filesystem Inventory

### Qualified Name
GovActionProcessStep::FilesystemScoutingSurvey::filesystem_inventory

### Description
Walks the filesystem (counts, sizes, hidden/symlink/executable/writable flags, timestamps, Egeria-reference-data classification), flags inaccessible/unclassified files, and profiles data file schemas.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | filesystem_inventory |

___

## Create Governance Action Process Step
### Display Name
Filesystem Scouting Survey — Fs Layout Signals

### Qualified Name
GovActionProcessStep::FilesystemScoutingSurvey::fs_layout_signals

### Description
Whether the file system holds a recognisable structure (a project, a dataset layout, an archive, backups) from the names on Resource Explorer's newest walk: project manifests and .git directories, dated and partition folders, dictionary and schema files, archive and backup names. Opens no file and publishes no name. With no walk it is shown as "not measured", never as nothing recognised.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | fs_layout_signals |

___

## Create Governance Action Process Step
### Display Name
Filesystem Scouting Survey — Fs Change Rates

### Qualified Name
GovActionProcessStep::FilesystemScoutingSurvey::fs_change_rates

### Description
Files added, removed and modified, and the growth in bytes, between the latest two walks Resource Explorer kept for a file system, over their interval. Reads the walks' per-file rows; opens no file. With one walk only it is shown as "not measured", never as no change; where a walk was capped, files added, removed and growth are "not measured" and only the files both walks saw are compared.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | fs_change_rates |

___

## Create Governance Action Process
### Display Name
Filesystem Scouting Survey

### Qualified Name
GovActionProcess::FilesystemScoutingSurvey

### Description
Walks the folder and records what is there: how many files and folders, their sizes, hidden files, links and timestamps. No file is opened. It then reads only what that walk stored to say whether the folder has a recognisable structure (a project, a dataset layout, an archive, backups) and how much changed since the previous walk. Cheap, and nothing is written into the folder. This definition is Resource Explorer's own walk and does not run Egeria's Folder Survey. If Egeria can read the folder, run Egeria's Folder Survey from the file system's folder-survey card instead; this definition's walk then stops and says so. A step that has not run shows "Not yet run", never an empty answer.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | File System Directory |
| survey_kind | scouting |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::FilesystemScoutingSurvey

### Governance Action Process Step
GovActionProcessStep::FilesystemScoutingSurvey::filesystem_inventory

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemScoutingSurvey::filesystem_inventory

### Next Governance Action Process Step
GovActionProcessStep::FilesystemScoutingSurvey::fs_layout_signals

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemScoutingSurvey::fs_layout_signals

### Next Governance Action Process Step
GovActionProcessStep::FilesystemScoutingSurvey::fs_change_rates

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemScoutingSurvey::filesystem_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-filesystem-inventory

### Description
Egeria dispatches GovActionProcessStep::FilesystemScoutingSurvey::filesystem_inventory to Resource Explorer's engine, which claims it and runs RE step filesystem_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemScoutingSurvey::fs_layout_signals

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-fs-layout-signals

### Description
Egeria dispatches GovActionProcessStep::FilesystemScoutingSurvey::fs_layout_signals to Resource Explorer's engine, which claims it and runs RE step fs_layout_signals.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemScoutingSurvey::fs_change_rates

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-fs-change-rates

### Description
Egeria dispatches GovActionProcessStep::FilesystemScoutingSurvey::fs_change_rates to Resource Explorer's engine, which claims it and runs RE step fs_change_rates.

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
What kinds of files does this filesystem contain, and how much data is there?

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
Does this filesystem contain recognizable tabular or structured data files, and what do their schemas look like?

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
What is the shape of this file system: how deep, how wide, where does the bulk sit?

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
Does this file system contain a recognisable structure: a project, a dataset layout, an archive, backups?

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
How much has changed since the last time this was surveyed — is it worth re-running now?

___

## Link Element To Scope
### Target Element
Filesystem Scouting Survey

### Scope Reference
What has changed since the last walk: new, modified, removed files, and growth?

