## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — Filesystem Inventory

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::filesystem_inventory

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
Filesystem Analysis Survey — File Profiles

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::file_profiles

### Description
Profiles the columns of the data files the newest walk found (a .csv on an Egeria-led file system goes to Egeria's CSV Survey Service; every other readable file is profiled here). Writes nothing into the path.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | file_profiles |

___

## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — Narrative Docs

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::narrative_docs

### Description
Reads the narrative files the newest walk found, whole and size-capped, chunks them by section and replaces this file system's collection in Resource Explorer's search index. Publishes nothing to Egeria.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | narrative_docs |

___

## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — File Data Class Match

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::file_data_class_match

### Description
Matches every column of the newest profile run against the data classes Egeria holds, and the file names against the personal-data words. Every match is a proposal; nothing is published or created in Egeria.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | file_data_class_match |

___

## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — Fs Activity

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::fs_activity

### Description
How long since each file was modified, and since it was read where the mount records access times: hot, warm and cold files and bytes, aged at the newest walk's own time, from the per-file rows Resource Explorer kept. Opens no file. Where access times were not recorded on the walk or the mount does not update them, reads are shown as "not measured" (can't tell), never as cold.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | fs_activity |

___

## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — Fs Duplicates

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::fs_duplicates

### Description
Files with the same content (size and SHA-256) within a file system, and files whose content another registered file system also holds, from the hashes Resource Explorer's newest walk kept. Opens no file. Where the walk hashed nothing (hashing off, every file over the limit) it is shown as "not measured", never as no duplicates; files the walk did not hash are counted as not compared.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | fs_duplicates |

___

## Create Governance Action Process Step
### Display Name
Filesystem Analysis Survey — Fs Permissions

### Qualified Name
GovActionProcessStep::FilesystemAnalysisSurvey::fs_permissions

### Description
Owners, groups and modes as counts (world-readable, world-writable, group-writable, setuid), and world-readable files whose names look like secrets as a request for action, from the owner, group and mode Resource Explorer's newest walk kept. Opens no file and publishes no name. With no stat fields, or no file name classified, it is shown as "not measured", never as none found.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | File System Directory |
| re_analysis_step | fs_permissions |

___

## Create Governance Action Process
### Display Name
Filesystem Analysis Survey

### Qualified Name
GovActionProcess::FilesystemAnalysisSurvey

### Description
Walks the folder, then goes deeper. It profiles the columns of the tabular data files, reads README-style narrative files in full and indexes them for search inside Resource Explorer, and proposes matches between columns or file names and Egeria's data classes (proposals only; nothing is created in Egeria). It also reports which files nobody has touched for a long time, which files are duplicates, and who can read what (counts of owners, groups and modes, and secret-looking names that anyone can read). Costs more than scouting: it opens data and narrative files and hashes files to find duplicates. Nothing is written into the folder. Resource Explorer leads this definition: when Egeria can read the folder, Egeria's Folder Survey leads and is run from the file system's folder-survey card, and this definition's walk stops and says so. A step that has not run shows "Not yet run", never an empty answer.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | File System Directory |
| survey_kind | analysis |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::FilesystemAnalysisSurvey

### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::filesystem_inventory

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::filesystem_inventory

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::file_profiles

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::file_profiles

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::narrative_docs

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::narrative_docs

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::file_data_class_match

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::file_data_class_match

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::fs_activity

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::fs_activity

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::fs_duplicates

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::fs_duplicates

### Next Governance Action Process Step
GovActionProcessStep::FilesystemAnalysisSurvey::fs_permissions

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::filesystem_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-filesystem-inventory

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::filesystem_inventory to Resource Explorer's engine, which claims it and runs RE step filesystem_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::file_profiles

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-file-profiles

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::file_profiles to Resource Explorer's engine, which claims it and runs RE step file_profiles.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::narrative_docs

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-narrative-docs

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::narrative_docs to Resource Explorer's engine, which claims it and runs RE step narrative_docs.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::file_data_class_match

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-file-data-class-match

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::file_data_class_match to Resource Explorer's engine, which claims it and runs RE step file_data_class_match.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::fs_activity

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-fs-activity

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::fs_activity to Resource Explorer's engine, which claims it and runs RE step fs_activity.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::fs_duplicates

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-fs-duplicates

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::fs_duplicates to Resource Explorer's engine, which claims it and runs RE step fs_duplicates.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::FilesystemAnalysisSurvey::fs_permissions

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-fs-permissions

### Description
Egeria dispatches GovActionProcessStep::FilesystemAnalysisSurvey::fs_permissions to Resource Explorer's engine, which claims it and runs RE step fs_permissions.

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Does this filesystem contain recognizable tabular or structured data files, and what do their schemas look like?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Which tabular or structured files are here, with what columns and types?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
What do the narrative files inside it (README, INDEX, ABOUT, NOTES, a data dictionary, a codebook, LICENSE, CHANGELOG) say the contents are, folder by folder?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Which columns conform to a known Data Class, with what confidence, and which look like a class we do not have yet?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Which files or folders contain personal or sensitive data?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Which files has nobody touched for a long time, and which are hot?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Which files are duplicates of each other, or of files in another registered file system?

___

## Link Element To Scope
### Target Element
Filesystem Analysis Survey

### Scope Reference
Who owns or can read what, and are there world-readable sensitive files?

