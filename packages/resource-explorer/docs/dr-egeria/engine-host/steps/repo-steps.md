<!--
GENERATED from the repo step registry by packages/resource-explorer/scripts/generate_engine_host_declaration.py.
Do not hand-edit; regenerate. Design: docs/design-notes/SLICE-ENGINE-HOST-CASE3-RE-DEFINITIONS-DISPATCHED-BY-EGERIA.md
(C1, decision 1: one SurveyActionService per step key, never per definition).

Declares 43 repo step(s) as services of Resource Explorer's engine, so the GovernanceActionExecutor
each Survey Definition step carries (C2, dr_egeria_survey_publisher) names a request type the engine advertises. Each:
one SurveyActionService, one SupportedGovernanceService {requestType: re-<step key>} (both in
repo-steps.pyegeria-steps.json, run first by `resource-explorer dr-egeria run`, find-or-create), then
the GovernanceActionType ResourceExplorerSurvey::re-<step key> with producedGuards from step_outcome.py's mapping, and
its GovernanceActionExecutor below.

Run it with `resource-explorer dr-egeria run engine-host/steps/repo-steps.md` AFTER the
engine's own document. RE's bootstrap never runs it. "Link Action to Action Executor" is NOT safe to repeat; the runner
refuses a second run of the same content unless forced. Retire a step by Content Status DEPRECATED, never by deleting.
-->

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_refresh_plan

### Qualified Name
ResourceExplorerSurvey::re-repo-refresh-plan

### Description
What a refresh would actually need to do: which targets have never run, which are stale against the current head commit, and which are current. One GitHub call, no archive download. ADVISORY — the executor runs every step regardless, so this records the decision rather than enforcing it.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_refresh_plan |
| engine_host | resource-explorer-worker |
| request_type | re-repo-refresh-plan |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_refresh_plan is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-refresh-plan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-refresh-plan

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-refresh-plan to Resource Explorer's engine, which claims it and runs RE step repo_refresh_plan.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_git_statistics

### Qualified Name
ResourceExplorerSurvey::re-repo-git-statistics

### Description
Refreshes project_stats (stars, forks, contributors, commit activity, releases, security config, deployments) from the GitHub API — the table eight other steps read. Replaces five independent StatsFetcher calls that each refreshed it separately in the same run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_git_statistics |
| engine_host | resource-explorer-worker |
| request_type | re-repo-git-statistics |
| entity_type | repo |
| fetch_cost | api_heavy |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_git_statistics fetches sources or calls models (fetch cost api_heavy, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-git-statistics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-git-statistics

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-git-statistics to Resource Explorer's engine, which claims it and runs RE step repo_git_statistics.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_file_inventory

### Qualified Name
ResourceExplorerSurvey::re-repo-file-inventory

### Description
Refreshes project_file_inventory from a fresh zipball — the table every file-shape step reads. Closes the gap where the inventory was written only by RAG ingestion/refresh_profile and never by a survey step, so a survey reported whatever an earlier, unrelated run had left behind.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_file_inventory |
| engine_host | resource-explorer-worker |
| request_type | re-repo-file-inventory |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_file_inventory fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-file-inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-inventory

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-file-inventory to Resource Explorer's engine, which claims it and runs RE step repo_file_inventory.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_manifest_parse

### Qualified Name
ResourceExplorerSurvey::re-repo-manifest-parse

### Description
Parses dependency manifests, CI workflow content, supply-chain signals and repo-convention signals from a freshly extracted zipball, refreshing project_dependencies and project_analysis_findings (kind="ci_quality"/"repo_conventions"/"supply_chain") — the three tables previously written only by full ingestion (and, for the latter two, refresh_profile), never by a survey step.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_manifest_parse |
| engine_host | resource-explorer-worker |
| request_type | re-repo-manifest-parse |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_manifest_parse fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-manifest-parse

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-manifest-parse

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-manifest-parse to Resource Explorer's engine, which claims it and runs RE step repo_manifest_parse.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_file_structure

### Qualified Name
ResourceExplorerSurvey::re-repo-file-structure

### Description
File counts, per-language breakdown, and top-level directory structure.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_file_structure |
| engine_host | resource-explorer-worker |
| request_type | re-repo-file-structure |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_file_structure is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-file-structure

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-structure

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-file-structure to Resource Explorer's engine, which claims it and runs RE step repo_file_structure.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_file_size

### Qualified Name
ResourceExplorerSurvey::re-repo-file-size

### Description
Per-file sizes, total footprint, size-by-type, top-10 largest files.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_file_size |
| engine_host | resource-explorer-worker |
| request_type | re-repo-file-size |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_file_size is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-file-size

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-size

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-file-size to Resource Explorer's engine, which claims it and runs RE step repo_file_size.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_language

### Qualified Name
ResourceExplorerSurvey::re-repo-language

### Description
Primary/secondary language and coarse project-type classification.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_language |
| engine_host | resource-explorer-worker |
| request_type | re-repo-language |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_language is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-language

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-language

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-language to Resource Explorer's engine, which claims it and runs RE step repo_language.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_health

### Qualified Name
ResourceExplorerSurvey::re-repo-health

### Description
Activity, community, release-cadence, and freshness scoring from GitHub stats.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_health |
| engine_host | resource-explorer-worker |
| request_type | re-repo-health |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_health is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-health

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-health

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-health to Resource Explorer's engine, which claims it and runs RE step repo_health.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_homepage

### Qualified Name
ResourceExplorerSurvey::re-repo-homepage

### Description
Finds where the project's homepage and its documentation are, and whether the documentation answers. Reads GitHub's homepage field, the packaging manifests (pyproject.toml, package.json, setup.cfg, Cargo.toml, pom.xml) and the README -- small files only, through the GitHub contents API, never a download -- and keeps the source and the other candidates for each answer. An entry labelled Documentation wins; placeholders such as example.com are rejected. One bounded request records whether the docs URL is reachable. Surfaced in Scouting as links with a short cue; the homepage is published to Egeria as an ExternalReference linked to the repo.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_homepage |
| engine_host | resource-explorer-worker |
| request_type | re-repo-homepage |
| entity_type | repo |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_homepage is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-homepage

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-homepage

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-homepage to Resource Explorer's engine, which claims it and runs RE step repo_homepage.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_website_ingestion

### Qualified Name
ResourceExplorerSurvey::re-repo-website-ingestion

### Description
Ingests the project's documentation site into pgvector as web_docs_{host}, so Chat and Understanding can answer from the project's own documentation rather than only its source tree. Keyed on the site's host, not the repo slug — several repos in one project share one site and therefore one collection. Uses the site repo_homepage derived, collapsing versioned docs to the current release; skips entirely when the repo builds that site itself, since the source is already ingested in a better form.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_website_ingestion |
| engine_host | resource-explorer-worker |
| request_type | re-repo-website-ingestion |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | medium |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_website_ingestion fetches sources or calls models (fetch cost download, compute cost medium) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-website-ingestion

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-website-ingestion

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-website-ingestion to Resource Explorer's engine, which claims it and runs RE step repo_website_ingestion.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_dependency

### Qualified Name
ResourceExplorerSurvey::re-repo-dependency

### Description
Package dependencies per ecosystem (PyPI/npm/Maven).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_dependency |
| engine_host | resource-explorer-worker |
| request_type | re-repo-dependency |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_dependency is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-dependency

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-dependency

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-dependency to Resource Explorer's engine, which claims it and runs RE step repo_dependency.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_documentation

### Qualified Name
ResourceExplorerSurvey::re-repo-documentation

### Description
Presence of README/CHANGELOG/CONTRIBUTING/SECURITY and overall doc-quality label.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_documentation |
| engine_host | resource-explorer-worker |
| request_type | re-repo-documentation |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_documentation is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-documentation

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-documentation

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-documentation to Resource Explorer's engine, which claims it and runs RE step repo_documentation.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_security

### Qualified Name
ResourceExplorerSurvey::re-repo-security

### Description
Presence of SECURITY.md, CI config, LICENSE — flags gaps as RFAs.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_security |
| engine_host | resource-explorer-worker |
| request_type | re-repo-security |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_security is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-security

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-security to Resource Explorer's engine, which claims it and runs RE step repo_security.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_classification

### Qualified Name
ResourceExplorerSurvey::re-repo-classification

### Description
What the repo represents (7 roles, ranked, multi-valued), where each artifact its role implies actually lives, and whether architecture recovery is worth running at all (design §5.5b).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_classification |
| engine_host | resource-explorer-worker |
| request_type | re-repo-classification |
| entity_type | repo |
| fetch_cost | api_heavy |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_classification fetches sources or calls models (fetch cost api_heavy, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-classification

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-classification to Resource Explorer's engine, which claims it and runs RE step repo_classification.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_license_classification

### Qualified Name
ResourceExplorerSurvey::re-repo-license-classification

### Description
Classifies the repo's SPDX license id into a risk tier (permissive/weak copyleft/strong copyleft/source-available/unknown).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_license_classification |
| engine_host | resource-explorer-worker |
| request_type | re-repo-license-classification |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_license_classification is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-license-classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-license-classification

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-license-classification to Resource Explorer's engine, which claims it and runs RE step repo_license_classification.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_security_features

### Qualified Name
ResourceExplorerSurvey::re-repo-security-features

### Description
GitHub's native security feature toggles (Dependabot, secret scanning, etc.) — configuration state, not artifact presence.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_security_features |
| engine_host | resource-explorer-worker |
| request_type | re-repo-security-features |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_security_features is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-security-features

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-features

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-security-features to Resource Explorer's engine, which claims it and runs RE step repo_security_features.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_ci_quality

### Qualified Name
ResourceExplorerSurvey::re-repo-ci-quality

### Description
Whether CI workflows actually run tests/lint/build, via a keyword scan of workflow content — not just whether a CI config exists.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_ci_quality |
| engine_host | resource-explorer-worker |
| request_type | re-repo-ci-quality |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_ci_quality is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-ci-quality

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-ci-quality

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-ci-quality to Resource Explorer's engine, which claims it and runs RE step repo_ci_quality.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_interface_surface

### Qualified Name
ResourceExplorerSurvey::re-repo-interface-surface

### Description
What can be talked to, and whether the contract is written down — three rungs: 'declared' (a committed contract, or an entry point the packaging declares), 'implemented' (the code runs as one, from stored evidence such as a distribution's own __main__.py), and 'implied' (a dependency name only, e.g. fastapi) — read from the file inventory, declared dependencies, and the distribution/deployment-evidence facts other steps already recorded. None of the first two rungs counts as a published API on its own.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_interface_surface |
| engine_host | resource-explorer-worker |
| request_type | re-repo-interface-surface |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_interface_surface is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-interface-surface

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-interface-surface

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-interface-surface to Resource Explorer's engine, which claims it and runs RE step repo_interface_surface.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_chaoss_metrics

### Qualified Name
ResourceExplorerSurvey::re-repo-chaoss-metrics

### Description
CHAOSS community-health metrics over recorded commits — chiefly the elephant factor, the fewest contributors accounting for half the commits. Contributor COUNT calls deep_causality a five-person project; the distribution says one person wrote 98% of it.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_chaoss_metrics |
| engine_host | resource-explorer-worker |
| request_type | re-repo-chaoss-metrics |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_chaoss_metrics is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-chaoss-metrics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-chaoss-metrics

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-chaoss-metrics to Resource Explorer's engine, which claims it and runs RE step repo_chaoss_metrics.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_cii_badge

### Qualified Name
ResourceExplorerSurvey::re-repo-cii-badge

### Description
The real OpenSSF Best Practices (CII) badge, read from bestpractices.dev rather than estimated. Reports the level with the age of the self-assessment behind it, and keeps 'no badge' apart from 'could not ask'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_cii_badge |
| engine_host | resource-explorer-worker |
| request_type | re-repo-cii-badge |
| entity_type | repo |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_cii_badge is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-cii-badge

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cii-badge

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-cii-badge to Resource Explorer's engine, which claims it and runs RE step repo_cii_badge.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_community_support

### Qualified Name
ResourceExplorerSurvey::re-repo-community-support

### Description
Community support as separate dimensions — attention, participation, channels — rather than one number. repository_health's community_score is dominated by stars and forks, and scores a four-contributor project 100/100; this reports the weakest dimension instead of averaging it away.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_community_support |
| engine_host | resource-explorer-worker |
| request_type | re-repo-community-support |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_community_support is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-community-support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-community-support

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-community-support to Resource Explorer's engine, which claims it and runs RE step repo_community_support.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_cve_scan

### Qualified Name
ResourceExplorerSurvey::re-repo-cve-scan

### Description
Dependency advisories from OSV.dev, over dependencies the manifest parser already recorded. Reports coverage with the count: declared dependencies only, and only those with a pinned, parseable version.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_cve_scan |
| engine_host | resource-explorer-worker |
| request_type | re-repo-cve-scan |
| entity_type | repo |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_cve_scan is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-cve-scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cve-scan

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-cve-scan to Resource Explorer's engine, which claims it and runs RE step repo_cve_scan.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_secret_scan

### Qualified Name
ResourceExplorerSurvey::re-repo-secret-scan

### Description
Committed-credential scan over HEAD content, using a VENDORED gitleaks ruleset (222 rules, MIT, provenance recorded). Reports what it matched AND which ruleset version it matched with — never 'no secrets', only 'no matches against this ruleset in HEAD'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_secret_scan |
| engine_host | resource-explorer-worker |
| request_type | re-repo-secret-scan |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | high |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_secret_scan fetches sources or calls models (fetch cost download, compute cost high) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-secret-scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-secret-scan

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-secret-scan to Resource Explorer's engine, which claims it and runs RE step repo_secret_scan.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_telemetry_scan

### Qualified Name
ResourceExplorerSurvey::re-repo-telemetry-scan

### Description
Telemetry / phone-home indicators: known SDK imports and literal outbound endpoints, paired with whether the project discloses them. Never labels an ordinary API client as telemetry.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_telemetry_scan |
| engine_host | resource-explorer-worker |
| request_type | re-repo-telemetry-scan |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | medium |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_telemetry_scan fetches sources or calls models (fetch cost download, compute cost medium) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-telemetry-scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-telemetry-scan

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-telemetry-scan to Resource Explorer's engine, which claims it and runs RE step repo_telemetry_scan.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_contribution_provenance

### Qualified Name
ResourceExplorerSurvey::re-repo-contribution-provenance

### Description
CLA/DCO provenance, kept as two separate questions: whether sign-off is STATED, and whether it is ENFORCED. Config presence alone is reported `partial`, never `pass`.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_contribution_provenance |
| engine_host | resource-explorer-worker |
| request_type | re-repo-contribution-provenance |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_contribution_provenance fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-contribution-provenance

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-contribution-provenance

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-contribution-provenance to Resource Explorer's engine, which claims it and runs RE step repo_contribution_provenance.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_sla_content

### Qualified Name
ResourceExplorerSurvey::re-repo-sla-content

### Description
Whether the project publishes support or service-level commitments. Deliberately NEUTRAL (present/absent, not pass/gap): most repositories legitimately publish none, and absence alone never raises an action.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_sla_content |
| engine_host | resource-explorer-worker |
| request_type | re-repo-sla-content |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_sla_content fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-sla-content

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-sla-content

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-sla-content to Resource Explorer's engine, which claims it and runs RE step repo_sla_content.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_foss_scorecard

### Qualified Name
ResourceExplorerSurvey::re-repo-foss-scorecard

### Description
OpenSSF-Scorecard-shaped checks computed from data already held — an unevaluable check reports unknown and is excluded from the score, rather than scored zero as OpenSSF's own tool does.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_foss_scorecard |
| engine_host | resource-explorer-worker |
| request_type | re-repo-foss-scorecard |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_foss_scorecard is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-foss-scorecard

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-foss-scorecard

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-foss-scorecard to Resource Explorer's engine, which claims it and runs RE step repo_foss_scorecard.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_maturity

### Qualified Name
ResourceExplorerSurvey::re-repo-maturity

### Description
Project age/lifecycle stage (nascent/emerging/established/mature), from repo_created_at — a CHAOSS-informed Discovery-tier signal.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_maturity |
| engine_host | resource-explorer-worker |
| request_type | re-repo-maturity |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_maturity is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-maturity

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-maturity

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-maturity to Resource Explorer's engine, which claims it and runs RE step repo_maturity.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_dependency_support

### Qualified Name
ResourceExplorerSurvey::re-repo-dependency-support

### Description
Which curated technologies the dependency list indicates (psycopg2 -> PostgreSQL, kafka-python -> Apache Kafka), and whether Egeria already holds a technology type for each. A starting point for people, not an answer: a match says the repo indicates X, not that X is supported, and an unmatched dependency has simply not been classified yet.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_dependency_support |
| engine_host | resource-explorer-worker |
| request_type | re-repo-dependency-support |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_dependency_support is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-dependency-support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-dependency-support

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-dependency-support to Resource Explorer's engine, which claims it and runs RE step repo_dependency_support.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_deployment_evidence

### Qualified Name
ResourceExplorerSurvey::re-repo-deployment-evidence

### Description
Per declared distribution (repo_manifest_parse's output): which deployment evidence exists — a console entry point, a __main__.py, an (unambiguous) Dockerfile/compose/Helm chart, a web-framework dependency — and the verdict that evidence supports: application (evidence found), library (importable, none found), or unknown (no manifest read yet). Layer 1 of 'Cataloging in layers' (project owner, 2026-09-14): before a distribution is proposed to Egeria as a SoftwareCapability classified Application, this is the evidence that verdict rests on.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_deployment_evidence |
| engine_host | resource-explorer-worker |
| request_type | re-repo-deployment-evidence |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_deployment_evidence is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-deployment-evidence

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-deployment-evidence

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-deployment-evidence to Resource Explorer's engine, which claims it and runs RE step repo_deployment_evidence.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_egeria_interfaces

### Qualified Name
ResourceExplorerSurvey::re-repo-egeria-interfaces

### Description
Which Egeria view services this repository consumes — one per pyegeria client class its code symbols reference (signature/return-type/base-class text; imports are not captured anywhere in this codebase, so this is a lower bound, not a count) — mapped via the curated configdata/egeria_view_services.yaml, plus Dr.Egeria doc-path evidence and a best-effort command-family guess from filenames. A repo with no pyegeria dependency reads nothing_found, not never-run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_egeria_interfaces |
| engine_host | resource-explorer-worker |
| request_type | re-repo-egeria-interfaces |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_egeria_interfaces is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-egeria-interfaces

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-egeria-interfaces

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-egeria-interfaces to Resource Explorer's engine, which claims it and runs RE step repo_egeria_interfaces.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_conventions

### Qualified Name
ResourceExplorerSurvey::re-repo-conventions

### Description
Discovery-tier repo conventions: security policy content, build automation, deployment/Docker evidence, catalog self-description (Backstage-style), documentation breadth.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_conventions |
| engine_host | resource-explorer-worker |
| request_type | re-repo-conventions |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_conventions is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-conventions

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-conventions

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-conventions to Resource Explorer's engine, which claims it and runs RE step repo_conventions.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_data_profiling

### Qualified Name
ResourceExplorerSurvey::re-repo-data-profiling

### Description
Inventories data files and profiles their schema (rows/columns/dtypes/nulls).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_data_profiling |
| engine_host | resource-explorer-worker |
| request_type | re-repo-data-profiling |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_data_profiling fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-data-profiling

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-data-profiling

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-data-profiling to Resource Explorer's engine, which claims it and runs RE step repo_data_profiling.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_file_classification

### Qualified Name
ResourceExplorerSurvey::re-repo-file-classification

### Description
Classifies every file by type using filename/extension mapping (Egeria-enrichable).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_file_classification |
| engine_host | resource-explorer-worker |
| request_type | re-repo-file-classification |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_file_classification is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-file-classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-classification

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-file-classification to Resource Explorer's engine, which claims it and runs RE step repo_file_classification.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_symbol_extraction

### Qualified Name
ResourceExplorerSurvey::re-repo-symbol-extraction

### Description
Extracts class/function/method symbols (tree-sitter/ast) for every supported language, refreshing project_code_symbols/project_code_relationships — D5's self-contained microflow closing the bug where those tables were only ever populated by RAG ingestion, never by a survey step. Also extracts decorator/annotation registrations (route/rpc/resolver/message-handler) for Python and Java into project_code_markers, and parses any committed OpenAPI/Swagger file from the same checkout into the API reference.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_symbol_extraction |
| engine_host | resource-explorer-worker |
| request_type | re-repo-symbol-extraction |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | medium |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_symbol_extraction fetches sources or calls models (fetch cost download, compute cost medium) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-symbol-extraction

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-symbol-extraction

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-symbol-extraction to Resource Explorer's engine, which claims it and runs RE step repo_symbol_extraction.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_api_structure

### Qualified Name
ResourceExplorerSurvey::re-repo-api-structure

### Description
Public API surface (functions/classes/methods) per language.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_api_structure |
| engine_host | resource-explorer-worker |
| request_type | re-repo-api-structure |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_api_structure is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-api-structure

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-api-structure

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-api-structure to Resource Explorer's engine, which claims it and runs RE step repo_api_structure.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_arch_detect

### Qualified Name
ResourceExplorerSurvey::re-repo-arch-detect

### Description
Recovers candidate architecture components from package manifests, deployment units (Dockerfile/compose), and ast-grep code markers — the deterministic half of architecture recovery (design doc §5.1).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_arch_detect |
| engine_host | resource-explorer-worker |
| request_type | re-repo-arch-detect |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_arch_detect fetches sources or calls models (fetch cost download, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-arch-detect

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-detect

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-arch-detect to Resource Explorer's engine, which claims it and runs RE step repo_arch_detect.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_arch_coupling

### Qualified Name
ResourceExplorerSurvey::re-repo-arch-coupling

### Description
Proposes additional architecture-component boundaries from import coupling and co-change coupling — the conventional, undeclared components (Agents, Core, Surveyors, ...) that manifests and code markers structurally cannot see (design doc §5.5, Phase 1 plan §4.1). Needs real git history, not just a checkout.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_arch_coupling |
| engine_host | resource-explorer-worker |
| request_type | re-repo-arch-coupling |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | high |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_arch_coupling fetches sources or calls models (fetch cost download, compute cost high) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-arch-coupling

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-coupling

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-arch-coupling to Resource Explorer's engine, which claims it and runs RE step repo_arch_coupling.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_arch_lens

### Qualified Name
ResourceExplorerSurvey::re-repo-arch-lens

### Description
Labels recovered components against the project's own architecture document, wherever it lives — in-repo, a sibling repository, or a documentation site. A LENS: it adds no component, removes none and assigns no type. Milvus goes from 206 candidates to 15 the authors actually name (finding 102).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_arch_lens |
| engine_host | resource-explorer-worker |
| request_type | re-repo-arch-lens |
| entity_type | repo |
| fetch_cost | api |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_arch_lens is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-arch-lens

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-lens

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-arch-lens to Resource Explorer's engine, which claims it and runs RE step repo_arch_lens.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_arch_summary

### Qualified Name
ResourceExplorerSurvey::re-repo-arch-summary

### Description
Collapses architecture-recovery findings to the depth the question asked for — the summarising half nothing owned. Milvus recovers 218 candidate components where its own authors describe eight; a suitability answer is 'serves gRPC (297 operations), 24 runnable units, 31 third-party'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_arch_summary |
| engine_host | resource-explorer-worker |
| request_type | re-repo-arch-summary |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_arch_summary is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-arch-summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-summary

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-arch-summary to Resource Explorer's engine, which claims it and runs RE step repo_arch_summary.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_sub_resource_survey

### Qualified Name
ResourceExplorerSurvey::re-repo-sub-resource-survey

### Description
Surveys file/folder characteristics to recommend which sub-resources are worthy of cataloging as their own Egeria assets (Assessment sub-resource cataloging plan) — survey only, does not catalog.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_sub_resource_survey |
| engine_host | resource-explorer-worker |
| request_type | re-repo-sub-resource-survey |
| entity_type | repo |
| fetch_cost | api_heavy |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_sub_resource_survey fetches sources or calls models (fetch cost api_heavy, compute cost low) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-sub-resource-survey

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-sub-resource-survey

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-sub-resource-survey to Resource Explorer's engine, which claims it and runs RE step repo_sub_resource_survey.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_security_summary

### Qualified Name
ResourceExplorerSurvey::re-repo-security-summary

### Description
Reduces the security family's stored findings to one topic summary. Measures nothing itself — it reads what the other security steps wrote, so it belongs LAST in any survey that runs them. Reports coverage and the age of its oldest input alongside the verdict, and refuses a verdict at all below four inputs.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_security_summary |
| engine_host | resource-explorer-worker |
| request_type | re-repo-security-summary |
| entity_type | repo |
| fetch_cost | none |
| compute_cost | low |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_security_summary is not run by Resource Explorer's engine host yet: it runs database steps only so far. Run its definition from Resource Explorer's own Run |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-security-summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-summary

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-security-summary to Resource Explorer's engine, which claims it and runs RE step repo_security_summary.

___

## Create Governance Action Type
### Display Name
Resource Explorer Survey — repo_rag_ingestion

### Qualified Name
ResourceExplorerSurvey::re-repo-rag-ingestion

### Description
Refreshes the project's pgvector collections via IncrementalIndexer — the queryable representation Chat, the query router and every RAG-backed answer read, previously built only at registration, on webhook or from a bespoke route branch and never by a survey step. A no-op when the repository's last indexed commit is unchanged.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Content Status
ACTIVE

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| re_analysis_step | repo_rag_ingestion |
| engine_host | resource-explorer-worker |
| request_type | re-repo-rag-ingestion |
| entity_type | repo |
| fetch_cost | download |
| compute_cost | high |
| requires_capability | undeclared |
| engine_host_dispatch | survey-invalid: repository step repo_rag_ingestion fetches sources or calls models (fetch cost download, compute cost high) |

___

## Link Action to Action Executor
### Governance Action Type
ResourceExplorerSurvey::re-repo-rag-ingestion

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-rag-ingestion

### Description
Egeria dispatches ResourceExplorerSurvey::re-repo-rag-ingestion to Resource Explorer's engine, which claims it and runs RE step repo_rag_ingestion.

___
