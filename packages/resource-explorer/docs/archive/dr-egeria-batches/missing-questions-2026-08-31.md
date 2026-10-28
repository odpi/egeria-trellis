# Questions missing from Egeria — 2026-08-31

GENERATED SUBSET, not a source document. Extracted from scouting-questions.md,
which is itself generated from ../resource_questions.csv.

Why a subset: that document holds all 49 questions, of which 41 already exist in
Egeria. Re-running it whole would re-fire 168 `Link Perspective to Question` and
54 `Link Element To Scope` commands against terms that are already linked. That is
the same command class that created 40 duplicate edges on the Survey Definitions
an hour before this file was written — and unlike those, perspective links have no
reconciler to clean up afterwards.

This file touches ONLY the 8 terms that do not exist, so no existing element is
re-linked and no duplicate is possible. Delete it once run.

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What kind of thing is this repository — a library, an application, a tool, or samples?

### Description
repo_classification exists and nothing asked for it. It also decides whether architecture recovery is worth running, so it gates later tiers.

### Summary
The same signal means different things depending on what the repo is; a tutorial with no tests is fine, a library with none is not.

### Usage
Typically asked and answerable during Discovery.

___

## Classify Term as Question

### Term Name
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Perspective to Question

### Perspective Name
Perspective::Consumer

### Question Name
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What kind of thing is this repository — a library, an application, a tool, or samples?

___

## Link Element To Scope

### Target Element
What kind of thing is this repository — a library, an application, a tool, or samples?

### Scope Reference
Discovery

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What is its internal architecture — what components exist and how do they relate?

### Description
architecture_recovery, architecture_summary and architecture_doc_lens all exist; no question referenced any of them. The doc lens specifically compares documented architecture against recovered architecture.

### Summary
I need to know what I would be taking on before committing to it, and whether its own documentation matches what the code actually does.

### Usage
Typically asked and answerable during Discovery.

___

## Classify Term as Question

### Term Name
What is its internal architecture — what components exist and how do they relate?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
What is its internal architecture — what components exist and how do they relate?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What is its internal architecture — what components exist and how do they relate?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What is its internal architecture — what components exist and how do they relate?

___

## Link Element To Scope

### Target Element
What is its internal architecture — what components exist and how do they relate?

### Scope Reference
Discovery

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What languages and file types make up this repository?

### Description
language_file_classification is one of the oldest analyses here and no question referenced it.

### Summary
Language mix is the cheapest signal of whether this is something we can maintain in-house.

### Usage
Typically asked and answerable during Scouting.

___

## Classify Term as Question

### Term Name
What languages and file types make up this repository?

___

## Link Perspective to Question

### Perspective Name
Perspective::Consumer

### Question Name
What languages and file types make up this repository?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What languages and file types make up this repository?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What languages and file types make up this repository?

___

## Link Element To Scope

### Target Element
What languages and file types make up this repository?

### Scope Reference
Scouting

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What data files does it ship, and what shape are they?

### Description
data_file_profiling exists and no question referenced it.

### Summary
Bundled data can carry licensing and privacy obligations that the repo's own licence does not cover.

### Usage
Typically asked and answerable during Analysis.

___

## Classify Term as Question

### Term Name
What data files does it ship, and what shape are they?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Owner

### Question Name
What data files does it ship, and what shape are they?

___

## Link Perspective to Question

### Perspective Name
Perspective::Privacy

### Question Name
What data files does it ship, and what shape are they?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What data files does it ship, and what shape are they?

___

## Link Element To Scope

### Target Element
What data files does it ship, and what shape are they?

### Scope Reference
Analysis

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What APIs and code symbols does it expose to callers?

### Description
api_structure and interface_surface both exist; neither was referenced by a question.

### Summary
The published surface is what we would actually be coupling to, and what breaks on upgrade.

### Usage
Typically asked and answerable during Analysis.

___

## Classify Term as Question

### Term Name
What APIs and code symbols does it expose to callers?

___

## Link Perspective to Question

### Perspective Name
Perspective::Consumer

### Question Name
What APIs and code symbols does it expose to callers?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
What APIs and code symbols does it expose to callers?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What APIs and code symbols does it expose to callers?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What APIs and code symbols does it expose to callers?

___

## Link Element To Scope

### Target Element
What APIs and code symbols does it expose to callers?

### Scope Reference
Analysis

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How does it score against OpenSSF Scorecard-style criteria?

### Description
foss_scorecard computes Scorecard-shaped checks from data already collected — it is NOT the upstream OpenSSF tool's own run, and a check it cannot evaluate is reported as not-established rather than as a failure.

### Summary
A named, comparable rubric is what lets me weigh several candidates against each other rather than judging each on its own terms.

### Usage
Typically asked and answerable during Assessment.

___

## Classify Term as Question

### Term Name
How does it score against OpenSSF Scorecard-style criteria?

___

## Link Perspective to Question

### Perspective Name
Perspective::Governance

### Question Name
How does it score against OpenSSF Scorecard-style criteria?

___

## Link Perspective to Question

### Perspective Name
Perspective::Steward

### Question Name
How does it score against OpenSSF Scorecard-style criteria?

___

## Link Perspective to Question

### Perspective Name
Perspective::Security

### Question Name
How does it score against OpenSSF Scorecard-style criteria?

___

## Link Element To Scope

### Target Element
How does it score against OpenSSF Scorecard-style criteria?

### Scope Reference
Assessment

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

### Description
cii_badge reads the real badge from bestpractices.dev rather than estimating one, and reports level together with the age of the self-assessment. No question referenced it.

### Summary
A badge is a public claim by the project about itself; its age tells me how much the claim is still worth.

### Usage
Typically asked and answerable during Assessment.

___

## Classify Term as Question

### Term Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Perspective to Question

### Perspective Name
Perspective::Governance

### Question Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Perspective to Question

### Perspective Name
Perspective::Steward

### Question Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Perspective to Question

### Perspective Name
Perspective::Community

### Question Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Perspective to Question

### Perspective Name
Perspective::Security

### Question Name
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

___

## Link Element To Scope

### Target Element
Does it hold an OpenSSF Best Practices (CII) badge, and how current is the self-assessment behind it?

### Scope Reference
Assessment

### Scope Category
Asked At

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How concentrated is authorship — would the project survive losing its top contributors?

### Description
chaoss_metrics reports the elephant factor and related CHAOSS metrics on their own terms, never averaged into a score. No question referenced it.

### Summary
A healthy commit rate produced by one person is a different risk from the same rate produced by twenty.

### Usage
Typically asked and answerable during Assessment.

___

## Classify Term as Question

### Term Name
How concentrated is authorship — would the project survive losing its top contributors?

___

## Link Perspective to Question

### Perspective Name
Perspective::Governance

### Question Name
How concentrated is authorship — would the project survive losing its top contributors?

___

## Link Perspective to Question

### Perspective Name
Perspective::Steward

### Question Name
How concentrated is authorship — would the project survive losing its top contributors?

___

## Link Perspective to Question

### Perspective Name
Perspective::Community

### Question Name
How concentrated is authorship — would the project survive losing its top contributors?

___

## Link Element To Scope

### Target Element
How concentrated is authorship — would the project survive losing its top contributors?

### Scope Reference
Assessment

### Scope Category
Asked At
