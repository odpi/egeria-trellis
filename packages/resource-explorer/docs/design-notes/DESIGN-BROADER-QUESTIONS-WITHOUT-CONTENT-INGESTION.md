# Design — broader questions without ingesting content (2026-09-28)

Owner's question: repositories answer their broadest questions through RAG
ingestion at the Analysis tier. Is that the right approach for repositories,
and what is the equivalent for databases, given that we will not ingest a
database's rows?

Short answers: for repositories, RAG is right for the prose and wrong for
the code, and the catalogue already reflects that split more than it says.
For databases the equivalent is four cheaper layers, none of which copies
the data: the catalog as text, column profiles as the semantic bridge,
Egeria's glossary and data classes as the vocabulary, and bounded queries
against the database itself when a question needs the data. Cross-resource
similarity is then the same mechanism for every type: embed the description
of the resource, never its content.

## 1 · Repositories: search first, embeddings for the narrative

What routes through RAG today, from `resource_questions.csv`: the upgrade
process (changelog and release notes), deployment styles (README and
docs), how the project is supported, and the free-form "ask about this
resource" chat. Every one of those is a prose question whose evidence is a
document a person wrote. Embeddings over those documents are the right
tool: the answer is a paraphrase with a citation.

What does not route through RAG, and should not: components and their
relations (architecture recovery, the AST and dependency engine), public
interfaces (`interface_surface`), dependencies and their support, CI and
tests, licence. Each has a structural reader that returns an exact answer
with provenance. An embedding lookup can only approximate what those
readers state, and it loses the "measured / not established / nothing found"
distinction that the honesty rules depend on.

Rule for the repository catalogue: **a question routes to RAG only when its
evidence is a narrative document**; when the evidence is code, configuration
or a service response, it routes to the structural reader, and the chat
answer is composed from whichever reader produced evidence. The chat's
retrieval should therefore be hybrid — lexical and structural first, vector
second — and its answer must name which retrieval it used.

## 2 · Databases: four layers, no rows copied

The design already refuses full-content ingestion (§5.1, §16.2: counts are
scoped to the credential; a name is a claim; nothing is read that the
survey did not ask for). The broader questions are answered by these layers,
in cost order:

| layer | what it is | cost tier | what it answers |
|---|---|---|---|
| Catalog as text | schema, table and column names; comments; types; keys; DDL | Scouting (catalog read, already stored) | "what is this about" by naming (`subject_signals` today); enough text to embed for cross-resource similarity without touching a row |
| Profiles as the semantic bridge | distinct counts, null fraction, patterns, top values, min/max, from `postgres_column_profile` under the credential's budget | Analysis (bounded sample) | a column whose top values are ISO country codes, dates in a range, or an email pattern says more than its name; this is the evidence a data-class match needs |
| Egeria vocabulary | glossary terms, data classes, reference-data sets, matched against names and profiles | Analysis, then Enrichment (a person confirms) | "which glossary terms do these columns mean" (`semantic_suggestions`, proposed), `data_class_match`, `reference_data_match`; shared across every database rather than learned per table |
| Bounded queries | a generated SQL query run as the surveyor, with a row limit, a time limit and a cost record, its text kept as evidence | Analysis, on request | questions that need the data: "how many orders per region last year", "does any row violate this rule" |

Bounded queries deserve the design attention that RAG got for repositories.
The database is its own best reader: it is always fresher than a copy, it
enforces the credential we surveyed with, and the query text is a better
piece of evidence than a retrieved chunk. The constraints are the same ones
the profile step already carries (design §5.8): sampling configuration,
`LIMIT`, statement timeout, the credential's visibility, and the cost vector
recorded on the `step_runs` row. What must never happen is the text-to-SQL
path silently reading more than the profile step would; it runs under the
same budget or it reports "over budget" as a state.

## 3 · Cross-resource similarity, one mechanism for every type

"Which resources have similar content" is currently a gap that names a
pgvector similarity search over content. Content is the wrong thing to
compare: it is large, it changes, and for databases we refuse to copy it.
Compare the **description** instead:

- repositories: README and doc embeddings (already produced by
  `repo_rag_ingestion`), plus the structural signature (`db_fingerprint`'s
  repository cousin: language, dependency set, interface surface);
- databases and filesystems: the catalog-as-text embedding plus the profile
  summary per column (type, distinct-ratio band, pattern class, top-value
  class), plus the existing schema fingerprint.

Two resources are similar when their descriptions are near, and the
evidence shown is which parts were near (names, profiles, dependencies),
never a retrieved chunk. This also makes similarity honest under a scoped
credential: a description built from 6 of 8 schemas says so.

## 4 · What changes now

Two catalogue rows are reworded on this branch (both remain gaps; the
wording now names the intended mechanism and stops promising content
ingestion):

- **"Which resources have similar content — code, documentation or data
  values — and how does this one differ?"** → similarity over resource
  descriptions (doc embeddings and structural signatures for repositories;
  catalog-as-text and profile summaries for databases and filesystems),
  never over content; an agent describes the difference from the parts that
  were near.
- **"Which glossary terms do these columns probably mean?"** → the
  vocabulary layer: `semantic_suggestions` (proposed) matches column names
  and `postgres_column_profile` evidence against Egeria glossary terms and
  data classes, proposing `SemanticAnnotation`s for a person to confirm in
  Enrichment; no row is read beyond the profile's sample.

Not changed here, queued as design work with its own ask: the bounded-query
analysis (a `db_bounded_query` step), the hybrid retrieval rule for the
repository chat, and the description-embedding job that both similarity
rows depend on. Each is a slice with a gate; the first gate is the
adventureworks question "which tables hold customer contact details",
answered from profiles and glossary matches without a single `SELECT *`.
