# Questions missing from Egeria — 2026-09-12

GENERATED SUBSET, not a source document. Extracted from scouting-questions.md,
which is itself generated from ../resource_questions.csv. Same shape and same
reason as missing-questions-2026-08-31.md.

Why a subset: the CSV split "What is its internal architecture — what components
exist and how do they relate?" into four questions (49 → 52) and the generated
document was never regenerated or re-run, so the four terms did not exist and
the two Survey Definitions re-authored the same day could not scope to them
(5 `Link Element To Scope` failures). Re-running the whole document would
re-fire ~170 `Link Perspective to Question` commands against terms that are
already linked, and perspective links have no reconciler.

Executed once, 2026-09-12 20:3x, 23/23 SUCCESS. NOT listed in _batch.json on
purpose: a heal must not re-run it.

The superseded term "What is its internal architecture — what components exist
and how do they relate?" is still on the platform. Left in place: nothing scopes
to it any more, and deleting a glossary term is a separate, deliberate write.

---

# Questions

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What components exist in this repository, and what kind is each?

### Description
architecture_recovery, architecture_summary and architecture_doc_lens all exist; no question referenced any of them. The doc lens specifically compares documented architecture against recovered architecture.

### Summary
I need to know what I would be taking on before committing to it, and whether its own documentation matches what the code actually does.

### Usage
Typically asked and answerable during Discovery.

___

## Classify Term as Question

### Term Name
What components exist in this repository, and what kind is each?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
What components exist in this repository, and what kind is each?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
What components exist in this repository, and what kind is each?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What components exist in this repository, and what kind is each?

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How do its components relate to each other?

### Description
Split from the combined 'internal architecture' question 2026-09-08 (Dan: is a separate question worth splitting out; chat-facts rendering cannot flatten a component graph into bullet text at any real scale, so this half needs its own answer, not a share of the other one's). architecture_diagram is the Mermaid diagram + caption rendered from architecture_recovery/architecture_summary at survey time -- previously computed and persisted, never read back by anything.

### Summary
I need to know what I would be taking on before committing to it, and whether its own documentation matches what the code actually does.

### Usage
Typically asked and answerable during Discovery.

___

## Classify Term as Question

### Term Name
How do its components relate to each other?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
How do its components relate to each other?

___

## Link Perspective to Question

### Perspective Name
Perspective::Data Expert

### Question Name
How do its components relate to each other?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
How do its components relate to each other?

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
How much code is there? How complex?

### Description
Both halves are answerable as of 2026-09-02. SIZE: language_file_classification carries a per-language line census — code, comment, docstring and blank — written at ingestion and at Coarse Profile refresh (pipeline.py::_record_line_census). Markdown, JSON, HTML, CSS, SQL and XML are counted as text and excluded from the code total by construction; project_stats.ingestion_lines_of_code, which counted every newline in every text file and reported 1,118,195 against ~156k real code lines on egeria-python, is retired and must not be cited. COMPLEXITY: api_structure carries the per-language max/mean it always computed, python and java only — go and javascript store 0 for every symbol because their extractors never compute it, so they are excluded and named rather than averaged in (0.32 vs 2.69 on milvus). A repo with no census reports no line counts rather than zero: not counted is not the same as no code.

### Summary
Gives an estimate on cost to maintain.

### Usage
Typically asked and answerable during Assessment.

___

## Classify Term as Question

### Term Name
How much code is there? How complex?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
How much code is there? How complex?

___

## Link Perspective to Question

### Perspective Name
Perspective::Security

### Question Name
How much code is there? How complex?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
How much code is there? How complex?

___

## Link Perspective to Question

### Perspective Name
Perspective::Admin

### Question Name
How much code is there? How complex?

___

## Create Glossary Term

### Glossary Name
User Questions

### Display Name
What are the public interfaces?

### Description
interface_surface detects which interface styles a repo exposes (http_api, grpc, graphql, messaging, soap, cli) and whether a contract is actually published, versus merely implied by a dependency. Discovery altitude: 'is there a published contract' rather than 'what symbols exist', which is the Analysis-stage question 'What APIs and code symbols does it expose to callers?' (api_structure + interface_surface).

### Summary
How do we use this - does it look like it does what we want?

### Usage
Typically asked and answerable during Discovery.

___

## Classify Term as Question

### Term Name
What are the public interfaces?

___

## Link Perspective to Question

### Perspective Name
Perspective::App/AI Builder

### Question Name
What are the public interfaces?

___

## Link Perspective to Question

### Perspective Name
Perspective::Privacy

### Question Name
What are the public interfaces?

___

## Link Perspective to Question

### Perspective Name
Perspective::Security

### Question Name
What are the public interfaces?

___

## Link Perspective to Question

### Perspective Name
Perspective::Architecture

### Question Name
What are the public interfaces?

___

## Link Perspective to Question

### Perspective Name
Perspective::Admin

### Question Name
What are the public interfaces?

___
