## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Refresh Plan

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_refresh_plan

### Description
What a refresh would actually need to do: which targets have never run, which are stale against the current head commit, and which are current. One GitHub call, no archive download. ADVISORY — the executor runs every step regardless, so this records the decision rather than enforcing it.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_refresh_plan |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Git Statistics

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_git_statistics

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
Full Survey (all steps) — Repo File Inventory

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_file_inventory

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
Full Survey (all steps) — Repo Manifest Parse

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_manifest_parse

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
Full Survey (all steps) — Repo File Structure

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_file_structure

### Description
File counts, per-language breakdown, and top-level directory structure.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_structure |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo File Size

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_file_size

### Description
Per-file sizes, total footprint, size-by-type, top-10 largest files.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_size |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Language

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_language

### Description
Primary/secondary language and coarse project-type classification.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_language |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Health

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_health

### Description
Activity, community, release-cadence, and freshness scoring from GitHub stats.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_health |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Homepage

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_homepage

### Description
Finds where the project's homepage and its documentation are, and whether the documentation answers. Reads GitHub's homepage field, the packaging manifests (pyproject.toml, package.json, setup.cfg, Cargo.toml, pom.xml) and the README -- small files only, through the GitHub contents API, never a download -- and keeps the source and the other candidates for each answer. An entry labelled Documentation wins; placeholders such as example.com are rejected. One bounded request records whether the docs URL is reachable. Surfaced in Scouting as links with a short cue; the homepage is published to Egeria as an ExternalReference linked to the repo.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_homepage |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Website Ingestion

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_website_ingestion

### Description
Ingests the project's documentation site into pgvector as web_docs_{host}, so Chat and Understanding can answer from the project's own documentation rather than only its source tree. Keyed on the site's host, not the repo slug — several repos in one project share one site and therefore one collection. Uses the site repo_homepage derived, collapsing versioned docs to the current release; skips entirely when the repo builds that site itself, since the source is already ingested in a better form.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_website_ingestion |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Dependency

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_dependency

### Description
Package dependencies per ecosystem (PyPI/npm/Maven).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_dependency |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Documentation

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_documentation

### Description
Presence of README/CHANGELOG/CONTRIBUTING/SECURITY and overall doc-quality label.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_documentation |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Security

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_security

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
Full Survey (all steps) — Repo Classification

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_classification

### Description
What the repo represents (7 roles, ranked, multi-valued), where each artifact its role implies actually lives, and whether architecture recovery is worth running at all (design §5.5b).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_classification |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo License Classification

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_license_classification

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
Full Survey (all steps) — Repo Security Features

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_security_features

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
Full Survey (all steps) — Repo Ci Quality

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_ci_quality

### Description
Whether CI workflows actually run tests/lint/build, via a keyword scan of workflow content — not just whether a CI config exists.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_ci_quality |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Interface Surface

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_interface_surface

### Description
What can be talked to, and whether the contract is written down — three rungs: 'declared' (a committed contract, or an entry point the packaging declares), 'implemented' (the code runs as one, from stored evidence such as a distribution's own __main__.py), and 'implied' (a dependency name only, e.g. fastapi) — read from the file inventory, declared dependencies, and the distribution/deployment-evidence facts other steps already recorded. None of the first two rungs counts as a published API on its own.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_interface_surface |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Chaoss Metrics

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_chaoss_metrics

### Description
CHAOSS community-health metrics over recorded commits — chiefly the elephant factor, the fewest contributors accounting for half the commits. Contributor COUNT calls deep_causality a five-person project; the distribution says one person wrote 98% of it.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_chaoss_metrics |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Cii Badge

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_cii_badge

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
Full Survey (all steps) — Repo Community Support

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_community_support

### Description
Community support as separate dimensions — attention, participation, channels — rather than one number. repository_health's community_score is dominated by stars and forks, and scores a four-contributor project 100/100; this reports the weakest dimension instead of averaging it away.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_community_support |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Cve Scan

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_cve_scan

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
Full Survey (all steps) — Repo Secret Scan

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_secret_scan

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
Full Survey (all steps) — Repo Telemetry Scan

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_telemetry_scan

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
Full Survey (all steps) — Repo Contribution Provenance

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_contribution_provenance

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
Full Survey (all steps) — Repo Sla Content

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_sla_content

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
Full Survey (all steps) — Repo Foss Scorecard

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_foss_scorecard

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
Full Survey (all steps) — Repo Maturity

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_maturity

### Description
Project age/lifecycle stage (nascent/emerging/established/mature), from repo_created_at — a CHAOSS-informed Discovery-tier signal.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_maturity |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Dependency Support

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_dependency_support

### Description
Which curated technologies the dependency list indicates (psycopg2 -> PostgreSQL, kafka-python -> Apache Kafka), and whether Egeria already holds a technology type for each. A starting point for people, not an answer: a match says the repo indicates X, not that X is supported, and an unmatched dependency has simply not been classified yet.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_dependency_support |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Deployment Evidence

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_deployment_evidence

### Description
Per declared distribution (repo_manifest_parse's output): which deployment evidence exists — a console entry point, a __main__.py, an (unambiguous) Dockerfile/compose/Helm chart, a web-framework dependency — and the verdict that evidence supports: application (evidence found), library (importable, none found), or unknown (no manifest read yet). Layer 1 of 'Cataloging in layers' (project owner, 2026-09-14): before a distribution is proposed to Egeria as a SoftwareCapability classified Application, this is the evidence that verdict rests on.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_deployment_evidence |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Egeria Interfaces

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_egeria_interfaces

### Description
Which Egeria view services this repository consumes — one per pyegeria client class its code symbols reference (signature/return-type/base-class text; imports are not captured anywhere in this codebase, so this is a lower bound, not a count) — mapped via the curated configdata/egeria_view_services.yaml, plus Dr.Egeria doc-path evidence and a best-effort command-family guess from filenames. A repo with no pyegeria dependency reads nothing_found, not never-run.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_egeria_interfaces |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Conventions

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_conventions

### Description
Discovery-tier repo conventions: security policy content, build automation, deployment/Docker evidence, catalog self-description (Backstage-style), documentation breadth.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_conventions |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Data Profiling

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_data_profiling

### Description
Inventories data files and profiles their schema (rows/columns/dtypes/nulls).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_data_profiling |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo File Classification

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_file_classification

### Description
Classifies every file by type using filename/extension mapping (Egeria-enrichable).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_file_classification |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Symbol Extraction

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_symbol_extraction

### Description
Extracts class/function/method symbols (tree-sitter/ast) for every supported language, refreshing project_code_symbols/project_code_relationships — D5's self-contained microflow closing the bug where those tables were only ever populated by RAG ingestion, never by a survey step. Also extracts decorator/annotation registrations (route/rpc/resolver/message-handler) for Python and Java into project_code_markers, and parses any committed OpenAPI/Swagger file from the same checkout into the API reference.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_symbol_extraction |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Api Structure

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_api_structure

### Description
Public API surface (functions/classes/methods) per language.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_api_structure |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Arch Detect

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_arch_detect

### Description
Recovers candidate architecture components from package manifests, deployment units (Dockerfile/compose), and ast-grep code markers — the deterministic half of architecture recovery (design doc §5.1).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_detect |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Arch Coupling

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_arch_coupling

### Description
Proposes additional architecture-component boundaries from import coupling and co-change coupling — the conventional, undeclared components (Agents, Core, Surveyors, ...) that manifests and code markers structurally cannot see (design doc §5.5, Phase 1 plan §4.1). Needs real git history, not just a checkout.

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | prefect |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_coupling |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Arch Lens

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_arch_lens

### Description
Labels recovered components against the project's own architecture document, wherever it lives — in-repo, a sibling repository, or a documentation site. A LENS: it adds no component, removes none and assigns no type. Milvus goes from 206 candidates to 15 the authors actually name (finding 102).

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_lens |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Arch Summary

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_arch_summary

### Description
Collapses architecture-recovery findings to the depth the question asked for — the summarising half nothing owned. Milvus recovers 218 candidate components where its own authors describe eight; a suitability answer is 'serves gRPC (297 operations), 24 runnable units, 31 third-party'.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_arch_summary |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Sub Resource Survey

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_sub_resource_survey

### Description
Surveys file/folder characteristics to recommend which sub-resources are worthy of cataloging as their own Egeria assets (Assessment sub-resource cataloging plan) — survey only, does not catalog.

### Produced Guards
survey-completed, survey-invalid, survey-failed, Any

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | resource-explorer |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_sub_resource_survey |

___

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Security Summary

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_security_summary

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

## Create Governance Action Process Step
### Display Name
Full Survey (all steps) — Repo Rag Ingestion

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_rag_ingestion

### Description
Refreshes the project's pgvector collections via IncrementalIndexer — the queryable representation Chat, the query router and every RAG-backed answer read, previously built only at registration, on webhook or from a bespoke route branch and never by a survey step. A no-op when the repository's last indexed commit is unchanged.

### Ignore Multiple Triggers
true

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| executes_at | prefect |
| supported_technology_type | Git Repository |
| re_analysis_step | repo_rag_ingestion |

___

## Create Governance Action Process
### Display Name
Full Survey (all steps)

### Qualified Name
GovActionProcess::RepoFullSurvey

### Description
Every current Resource Explorer repo analysis step, chained into one Survey Definition — the comprehensive bundle for an already-tracked repo, not Discovery's launch target. Regenerated by scripts/generate_repo_survey_definition.py from repo_survey_definition_adapter.py's STEP_REGISTRY, the single source of truth for what RE can actually run against a Git repository. Named 'Full Survey (all steps)' — deliberately NOT 'Scouting - ...', which it was briefly called: this is an automate_full bundle, and the Scouting prefix made it read as a Scouting-tier survey, which is how running it from Scouting came to deposit Assessment-tier results that rendered as Scouting Results cards. Scouting's own run-everything option is 'Scouting Survey'. qualified_name has never changed across either rename, so both were in-place updates of the same element. Scales poorly by construction: as repo survey steps grow into the dozens, some of them expensive, 'run every step' stops being a sensible default and this becomes a deliberate, scheduled choice rather than a convenient one.

### Additional Properties
| Parameter Name | Parameter Value |
|---|---|
| supported_technology_type | Git Repository |
| survey_kind | automate_full |

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoFullSurvey

### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_refresh_plan

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_refresh_plan

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_git_statistics

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_git_statistics

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_inventory

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_inventory

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_manifest_parse

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_manifest_parse

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_structure

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_structure

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_size

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_size

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_language

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_language

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_health

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_health

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_homepage

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_homepage

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_website_ingestion

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_website_ingestion

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_dependency

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_dependency

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_documentation

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_documentation

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_license_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_license_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security_features

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security_features

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_ci_quality

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_ci_quality

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_interface_surface

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_interface_surface

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_chaoss_metrics

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_chaoss_metrics

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_cii_badge

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_cii_badge

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_community_support

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_community_support

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_cve_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_cve_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_secret_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_secret_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_telemetry_scan

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_telemetry_scan

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_contribution_provenance

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_contribution_provenance

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_sla_content

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_sla_content

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_foss_scorecard

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_foss_scorecard

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_maturity

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_maturity

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_dependency_support

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_dependency_support

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_deployment_evidence

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_deployment_evidence

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_egeria_interfaces

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_egeria_interfaces

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_conventions

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_conventions

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_data_profiling

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_data_profiling

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_classification

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_file_classification

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_symbol_extraction

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_symbol_extraction

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_api_structure

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_api_structure

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_detect

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_detect

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_coupling

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_coupling

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_lens

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_lens

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_summary

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_arch_summary

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_sub_resource_survey

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_sub_resource_survey

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security_summary

### Guard
Any

___

## Link Next Process Step
### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_security_summary

### Next Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_rag_ingestion

### Guard
Any

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_refresh_plan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-refresh-plan

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_refresh_plan to Resource Explorer's engine, which claims it and runs RE step repo_refresh_plan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_git_statistics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-git-statistics

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_git_statistics to Resource Explorer's engine, which claims it and runs RE step repo_git_statistics.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_file_inventory

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-inventory

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_file_inventory to Resource Explorer's engine, which claims it and runs RE step repo_file_inventory.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_manifest_parse

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-manifest-parse

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_manifest_parse to Resource Explorer's engine, which claims it and runs RE step repo_manifest_parse.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_file_structure

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-structure

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_file_structure to Resource Explorer's engine, which claims it and runs RE step repo_file_structure.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_file_size

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-size

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_file_size to Resource Explorer's engine, which claims it and runs RE step repo_file_size.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_language

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-language

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_language to Resource Explorer's engine, which claims it and runs RE step repo_language.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_health

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-health

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_health to Resource Explorer's engine, which claims it and runs RE step repo_health.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_homepage

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-homepage

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_homepage to Resource Explorer's engine, which claims it and runs RE step repo_homepage.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_website_ingestion

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-website-ingestion

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_website_ingestion to Resource Explorer's engine, which claims it and runs RE step repo_website_ingestion.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_dependency

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-dependency

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_dependency to Resource Explorer's engine, which claims it and runs RE step repo_dependency.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_documentation

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-documentation

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_documentation to Resource Explorer's engine, which claims it and runs RE step repo_documentation.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_security

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_security to Resource Explorer's engine, which claims it and runs RE step repo_security.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-classification

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_classification to Resource Explorer's engine, which claims it and runs RE step repo_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_license_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-license-classification

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_license_classification to Resource Explorer's engine, which claims it and runs RE step repo_license_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_security_features

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-features

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_security_features to Resource Explorer's engine, which claims it and runs RE step repo_security_features.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_ci_quality

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-ci-quality

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_ci_quality to Resource Explorer's engine, which claims it and runs RE step repo_ci_quality.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_interface_surface

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-interface-surface

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_interface_surface to Resource Explorer's engine, which claims it and runs RE step repo_interface_surface.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_chaoss_metrics

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-chaoss-metrics

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_chaoss_metrics to Resource Explorer's engine, which claims it and runs RE step repo_chaoss_metrics.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_cii_badge

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cii-badge

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_cii_badge to Resource Explorer's engine, which claims it and runs RE step repo_cii_badge.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_community_support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-community-support

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_community_support to Resource Explorer's engine, which claims it and runs RE step repo_community_support.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_cve_scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-cve-scan

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_cve_scan to Resource Explorer's engine, which claims it and runs RE step repo_cve_scan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_telemetry_scan

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-telemetry-scan

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_telemetry_scan to Resource Explorer's engine, which claims it and runs RE step repo_telemetry_scan.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_contribution_provenance

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-contribution-provenance

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_contribution_provenance to Resource Explorer's engine, which claims it and runs RE step repo_contribution_provenance.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_sla_content

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-sla-content

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_sla_content to Resource Explorer's engine, which claims it and runs RE step repo_sla_content.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_foss_scorecard

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-foss-scorecard

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_foss_scorecard to Resource Explorer's engine, which claims it and runs RE step repo_foss_scorecard.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_maturity

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-maturity

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_maturity to Resource Explorer's engine, which claims it and runs RE step repo_maturity.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_dependency_support

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-dependency-support

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_dependency_support to Resource Explorer's engine, which claims it and runs RE step repo_dependency_support.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_deployment_evidence

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-deployment-evidence

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_deployment_evidence to Resource Explorer's engine, which claims it and runs RE step repo_deployment_evidence.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_egeria_interfaces

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-egeria-interfaces

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_egeria_interfaces to Resource Explorer's engine, which claims it and runs RE step repo_egeria_interfaces.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_conventions

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-conventions

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_conventions to Resource Explorer's engine, which claims it and runs RE step repo_conventions.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_data_profiling

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-data-profiling

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_data_profiling to Resource Explorer's engine, which claims it and runs RE step repo_data_profiling.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_file_classification

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-file-classification

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_file_classification to Resource Explorer's engine, which claims it and runs RE step repo_file_classification.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_symbol_extraction

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-symbol-extraction

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_symbol_extraction to Resource Explorer's engine, which claims it and runs RE step repo_symbol_extraction.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_api_structure

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-api-structure

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_api_structure to Resource Explorer's engine, which claims it and runs RE step repo_api_structure.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_arch_detect

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-detect

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_arch_detect to Resource Explorer's engine, which claims it and runs RE step repo_arch_detect.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_arch_lens

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-lens

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_arch_lens to Resource Explorer's engine, which claims it and runs RE step repo_arch_lens.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_arch_summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-arch-summary

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_arch_summary to Resource Explorer's engine, which claims it and runs RE step repo_arch_summary.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_sub_resource_survey

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-sub-resource-survey

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_sub_resource_survey to Resource Explorer's engine, which claims it and runs RE step repo_sub_resource_survey.

___

## Link Action to Action Executor
### Governance Action Type
GovActionProcessStep::RepoFullSurvey::repo_security_summary

### Governance Engine
ResourceExplorerSurvey

### Request Type
re-repo-security-summary

### Description
Egeria dispatches GovActionProcessStep::RepoFullSurvey::repo_security_summary to Resource Explorer's engine, which claims it and runs RE step repo_security_summary.

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What languages and file types make up this repository?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How much code is there? How complex?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Is this repository actively maintained?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Who owns this resource (accountable owner), and who administers it?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How widely adopted and active is the community around this repository?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How is it supported?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What dependencies does this require?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How well documented is it?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Does it fit into our security infrastructure?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Is there a current, published, security analysis?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Does the repository publish a clear process for reporting security vulnerabilities?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What explicit license does this resource use, and are there non-standard or copyleft terms?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Is there a validation / deployment test for it?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Does the repository have automated build tooling in place?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What kinds of integrations does it support?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What APIs and code symbols does it expose to callers?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What are the public interfaces?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How concentrated is authorship — would the project survive losing its top contributors?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Are there outstanding CVEs?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How does the repository handle secrets, credentials, and sensitive configurations?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Does the software contain telemetry, phone-home mechanisms, or external metrics tracking?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Is intellectual property (IP) provenance managed via CLA or DCO?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How does it score against OpenSSF Scorecard-style criteria?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How mature is it?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Do we already support these dependencies?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What deployment styles does this support?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
Is this repository already self-described for an enterprise catalog (e.g. Backstage catalog-info.yaml)?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What data files does it ship, and what shape are they?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
What components exist in this repository, and what kind is each?

___

## Link Element To Scope
### Target Element
Full Survey (all steps)

### Scope Reference
How do its components relate to each other?

