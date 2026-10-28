# Questions needing an update — 2026-09-13

GENERATED SUBSET, not a source document. Extracted by diffing all 52 live
`User Questions` glossary terms (read via GlossaryManager.get_term_by_guid,
GUIDs resolved via ClassificationExplorer.get_guid_for_name) against the
Description/Summary/Usage text `scripts/csv_to_dr_egeria_questions.py` would
generate today from ../resource_questions.csv. 48 of the 52 terms predate
tonight's CSV/generator sync (2026-09-12, PR #58) and were authored from an
older CSV; 33 of those 48 already match and are NOT included here. This file
contains ONLY the 19 terms whose Description and/or Usage text (never
Display Name, never Summary in this batch) has drifted from the CSV.

Why "Create Glossary Term" and not an "Update ..." command: no
"Update Term"/"Update Glossary Term" Dr.Egeria command exists in this
instance's compact command registry (checked both the venv's
md_processing/data/compact_commands/commands_glossary_compact.json and the
egeria MCP server's `egeria_list_commands` — neither lists one). The
installed "Create Glossary Term" compact spec instead declares
`"upsert": true` ("Creates or updates a glossary term"), and this was
verified live before touching any real term: a throwaway
"ZZZ Upsert Test Term DELETE ME" term was created via Create Glossary Term,
then a second Create Glossary Term block with the same Display Name but
changed Description/Summary/Usage was validated (Dr.Egeria itself reported
the canonical command as "Update Glossary Term", found=Yes) and processed —
the SAME GUID was reused and all three fields updated in place, confirmed by
an independent read-back, no duplicate term was created. The test term was
then deleted. So re-running "Create Glossary Term" for an existing Display
Name IS this system's update mechanism.

This document MUST NOT contain any `Classify Term as Question`, `Link
Perspective to Question`, or `Link Element To Scope` command — those terms
are already classified and linked; `Classify`/`Link` commands are not
idempotent here (perspective links in particular have no reconciler) and
re-issuing them against already-linked terms would create permanent
duplicate links. Only `Create Glossary Term` blocks appear below, and each
supplies the full target Description/Summary/Usage (not just the changed
field) since Create Glossary Term's upsert replaces the whole set of
supplied attributes.

To be executed ONCE with `--process --summary-only`, output captured to a
file, then verified by an independent read-back — never by re-running.


---

# Updates

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Are there outstanding CVEs?

### Description
Covers advisories against DECLARED dependencies only, queried from OSV.dev, with coverage reported as prominently as the count — so a zero is 'none found in what we can see', not 'none exist'.

### Summary
Understand the risk.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What dependencies does this require?

### Description
The declared dependency list, per ecosystem, from the manifests present.

### Summary
Influences deployment cost and exposes additional transitive risks.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Do we already support these dependencies?

### Description
Needs the org's own tech inventory — human-supplied cross-reference, not resource-derivable.

### Summary
Reduces risk.

### Usage
Typically relevant during Analysis and Enrichment.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What deployment styles does this support?

### Description
Covers whether a Dockerfile, compose file or Helm chart is present. What the deployment instructions actually say is not read.

### Summary
Need to understand if we can deploy it?

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Do we know what the cost to run it is?

### Description
Human-supplied/derived — not resource-derivable.

### Summary
Cost is part of the decision process.

### Usage
Typically relevant during Analysis and Enrichment.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Is there a validation / deployment test for it?

### Description
Covers whether CI runs tests and whether deployment evidence exists; a real deployment validation test is not detected.

### Summary
How do we know if it is working properly once deployed?

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Does it fit into our security infrastructure?

### Description
An organisation-internal cross-reference: the security findings inform it, a person answers it.

### Summary
Will it be allowed in? What are the risks?

### Usage
Typically relevant during Analysis and Enrichment.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What kinds of integrations does it support?

### Description
Covers which interfaces exist and whether they are documented. Which named third-party systems it integrates with is not read.

### Summary
Can we connect it to our ecosystem?

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How well documented is it?

### Description
Covers root README, CHANGELOG and CONTRIBUTING presence with a quality label, plus whole-tree density — README count, a docs folder, .md/.txt count.

### Summary
Ease of adoption and maintenance.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How is it supported?

### Description
Combines community responsiveness, documentation quality and any formal support documents.

### Summary
Enterprise fallback and support options.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Is there a current, published, security analysis?

### Description
Not answered — a published third-party audit is a different artifact from these checks, and nothing detects one.

### Summary
Third-party audit and security posture.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Is intellectual property (IP) provenance managed via CLA or DCO?

### Description
Reports which mechanism is in use — DCO sign-off in commit trailers, a CLA bot configuration, or a CONTRIBUTING.md statement — rather than a yes/no.

### Summary
Protects the organization from copyright infringement claims from unauthorized third-party contributions.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Does the software contain telemetry, phone-home mechanisms, or external metrics tracking?

### Description
Matches known telemetry and analytics SDK imports and endpoints in source, and separately checks whether each match is disclosed in the repo's own docs. An undisclosed match is the finding, not the presence of telemetry.

### Summary
Prevents unauthorized leakage of internal network metadata, system details, or usage patterns.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How does the repository handle secrets, credentials, and sensitive configurations?

### Description
Runs a vendored gitleaks ruleset over the HEAD snapshot of tracked files, naming the ruleset and commit. Never claims 'no secrets' — only 'no matches against this ruleset, in this snapshot' — and says nothing about git history, where a removed secret still lives.

### Summary
Prevents hardcoded secrets or unencrypted credential passing in containerized and automated deployment environments.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Does the repository publish a clear process for reporting security vulnerabilities?

### Description
Reads SECURITY.md's content for an actual disclosure process, not just the file's presence — which is reported separately.

### Summary
Knowing how to responsibly disclose a finding — and whether maintainers take security seriously — matters before we rely on this.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Does the repository have automated build tooling in place?

### Description
Covers whether build tooling configuration is present and whether CI actually runs, tests and gates.

### Summary
Independent of CI wiring — a Makefile/build.gradle/pyproject build-system table means it can be built locally even if CI is absent or broken.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Is this repository already self-described for an enterprise catalog (e.g. Backstage catalog-info.yaml)?

### Description
repo_conventions detects catalog-info.yaml and equivalent self-description files. Same parser problem — the row was wired correctly and phrased in a way the generator read as unknown.

### Summary
A strong, cheap signal that this repo is already integrated into some enterprise inventory, independent of which catalog it names.

### Usage
Typically asked and answerable during Assessment.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What data files does it ship, and what shape are they?

### Description
An inventory and shape profile of the data files shipped in the repository.

### Summary
Bundled data can carry licensing and privacy obligations that the repo's own licence does not cover.

### Usage
Typically asked and answerable during Analysis.

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What APIs and code symbols does it expose to callers?

### Description
Covers the extracted code symbols and the interfaces implied by dependencies; a published contract is detected only where a specification file exists.

### Summary
The published surface is what we would actually be coupling to, and what breaks on upgrade.

### Usage
Typically asked and answerable during Analysis.

___

