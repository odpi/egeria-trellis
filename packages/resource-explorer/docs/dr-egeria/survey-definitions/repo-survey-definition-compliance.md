## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Git Statistics

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_git_statistics

### Description
Refreshes project_stats (stars, forks, contributors, commit activity, releases, security config, deployments) from the GitHub API — the table eight other steps read. Replaces five independent StatsFetcher calls that each refreshed it separately in the same run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_git_statistics |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo File Inventory

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_file_inventory

### Description
Refreshes project_file_inventory from a fresh zipball — the table every file-shape step reads. Closes the gap where the inventory was written only by RAG ingestion/refresh_profile and never by a survey step, so a survey reported whatever an earlier, unrelated run had left behind.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_inventory |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Manifest Parse

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_manifest_parse

### Description
Parses dependency manifests, CI workflow content, supply-chain signals and repo-convention signals from a freshly extracted zipball, refreshing project_dependencies and project_analysis_findings (kind="ci_quality"/"repo_conventions"/"supply_chain") — the three tables previously written only by full ingestion (and, for the latter two, refresh_profile), never by a survey step.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_manifest_parse |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Security

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_security

### Description
Presence of SECURITY.md, CI config, LICENSE — flags gaps as RFAs.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_security |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo License Classification

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_license_classification

### Description
Classifies the repo's SPDX license id into a risk tier (permissive/weak copyleft/strong copyleft/source-available/unknown).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_license_classification |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Security Features

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_security_features

### Description
GitHub's native security feature toggles (Dependabot, secret scanning, etc.) — configuration state, not artifact presence.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_security_features |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Cii Badge

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_cii_badge

### Description
The real OpenSSF Best Practices (CII) badge, read from bestpractices.dev rather than estimated. Reports the level with the age of the self-assessment behind it, and keeps 'no badge' apart from 'could not ask'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_cii_badge |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Cve Scan

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_cve_scan

### Description
Dependency advisories from OSV.dev, over dependencies the manifest parser already recorded. Reports coverage with the count: declared dependencies only, and only those with a pinned, parseable version.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_cve_scan |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Secret Scan

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_secret_scan

### Description
Committed-credential scan over HEAD content, using a VENDORED gitleaks ruleset (222 rules, MIT, provenance recorded). Reports what it matched AND which ruleset version it matched with — never 'no secrets', only 'no matches against this ruleset in HEAD'.

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | prefect |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_secret_scan |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Telemetry Scan

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_telemetry_scan

### Description
Telemetry / phone-home indicators: known SDK imports and literal outbound endpoints, paired with whether the project discloses them. Never labels an ordinary API client as telemetry.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_telemetry_scan |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Contribution Provenance

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_contribution_provenance

### Description
CLA/DCO provenance, kept as two separate questions: whether sign-off is STATED, and whether it is ENFORCED. Config presence alone is reported `partial`, never `pass`.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_contribution_provenance |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Sla Content

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_sla_content

### Description
Whether the project publishes support or service-level commitments. Deliberately NEUTRAL (present/absent, not pass/gap): most repositories legitimately publish none, and absence alone never raises an action.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_sla_content |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Foss Scorecard

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_foss_scorecard

### Description
OpenSSF-Scorecard-shaped checks computed from data already held — an unevaluable check reports unknown and is excluded from the score, rather than scored zero as OpenSSF's own tool does.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_foss_scorecard |

___

## Create Governance Action Process Step
### Display Name
Compliance Survey — Repo Security Summary

### Qualified Name
GovActionProcessStep::RepoComplianceSurvey::repo_security_summary

### Description
Reduces the security family's stored findings to one topic summary. Measures nothing itself — it reads what the other security steps wrote, so it belongs LAST in any survey that runs them. Reports coverage and the age of its oldest input alongside the verdict, and refuses a verdict at all below four inputs.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_security_summary |

___

## Create Governance Action Process
### Display Name
Compliance Survey

### Qualified Name
GovActionProcess::RepoComplianceSurvey

### Description
Everything that assesses a repository's compliance and disclosure posture, in one runnable survey: the existing security-family steps plus the four gap analyses added 2026-09-01 (secret handling, telemetry/phone-home, CLA/DCO provenance, published SLA content). Created as a NEW GROUP because RepoAssessmentSurvey was then ten steps and deliberately all-low-compute. On 2026-10-02 the project owner decided Assessment should include these four steps as well, so RepoAssessmentSurvey now carries them too (test_assessment_carries_the_compliance_steps_by_owner_decision pins that membership); this survey remains as the narrower compliance-only run, with manifest parsing and license classification added. Steps are shared, not moved: every one of these also remains in whatever group it already belonged to, which the step->group mapping has always supported (11 of 36 steps were already multi-group before this). Prefixed by the two prerequisite refresh steps because every gap analysis declares requires_context={'has_file_inventory': ...} and would otherwise be correctly SKIPPED_BY_DESIGN on a repo nothing had inventoried.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | assessment |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoComplianceSurvey

### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_git_statistics

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_git_statistics

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_file_inventory

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_file_inventory

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_manifest_parse

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_manifest_parse

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_security

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_security

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_license_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_license_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_security_features

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_security_features

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_cii_badge

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_cii_badge

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_cve_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_cve_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_secret_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_secret_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_telemetry_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_telemetry_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_contribution_provenance

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_contribution_provenance

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_sla_content

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_sla_content

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_foss_scorecard

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_foss_scorecard

### Next Governance Action Process Step
GovActionProcessStep::RepoComplianceSurvey::repo_security_summary

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_git_statistics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-git-statistics

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_git_statistics to Resource Explorer's engine, which claims it and runs RE step repo_git_statistics.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_file_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-inventory

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_file_inventory to Resource Explorer's engine, which claims it and runs RE step repo_file_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_manifest_parse

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-manifest-parse

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_manifest_parse to Resource Explorer's engine, which claims it and runs RE step repo_manifest_parse.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_security

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_security to Resource Explorer's engine, which claims it and runs RE step repo_security.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_license_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-license-classification

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_license_classification to Resource Explorer's engine, which claims it and runs RE step repo_license_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_security_features

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-features

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_security_features to Resource Explorer's engine, which claims it and runs RE step repo_security_features.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_cii_badge

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cii-badge

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_cii_badge to Resource Explorer's engine, which claims it and runs RE step repo_cii_badge.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_cve_scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cve-scan

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_cve_scan to Resource Explorer's engine, which claims it and runs RE step repo_cve_scan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_telemetry_scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-telemetry-scan

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_telemetry_scan to Resource Explorer's engine, which claims it and runs RE step repo_telemetry_scan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_contribution_provenance

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-contribution-provenance

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_contribution_provenance to Resource Explorer's engine, which claims it and runs RE step repo_contribution_provenance.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_sla_content

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-sla-content

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_sla_content to Resource Explorer's engine, which claims it and runs RE step repo_sla_content.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_foss_scorecard

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-foss-scorecard

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_foss_scorecard to Resource Explorer's engine, which claims it and runs RE step repo_foss_scorecard.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoComplianceSurvey::repo_security_summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-summary

### Description
Egeria dispatches GovActionProcessStep::RepoComplianceSurvey::repo_security_summary to Resource Explorer's engine, which claims it and runs RE step repo_security_summary.

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Does it fit into our security infrastructure?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Is there a current, published, security analysis?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Does the repository publish a clear process for reporting security vulnerabilities?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
What explicit license does this resource use, and are there non-standard or copyleft terms?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Are there outstanding CVEs?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
How does the repository handle secrets, credentials, and sensitive configurations?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Does the software contain telemetry, phone-home mechanisms, or external metrics tracking?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Is intellectual property (IP) provenance managed via CLA or DCO?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
How is it supported?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
Is this repository actively maintained?

___

## Link Element To Scope
### Target Element
Compliance Survey

### Scope Reference
How does it score against OpenSSF Scorecard-style criteria?

