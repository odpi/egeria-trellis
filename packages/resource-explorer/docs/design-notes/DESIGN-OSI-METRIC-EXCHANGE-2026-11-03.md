# Design note — OSI (Apache Ossie) metric exchange with the Analytic Library and Egeria — 2026-11-03

Status: queued for discussion and design. Not a brief; nothing here starts without the owner's word.

## 1. What OSI is
Apache Ossie, formerly the Open Semantic Interchange (spec v0.1 January 2026, Apache Incubator since July 2026, 0.2 in
draft), is a YAML or JSON description of a semantic model: `datasets` with a `source`, `fields`, `relationships`,
`dimensions` (with `is_time`), `metrics` with an `expression` per `dialect`, and `ai_context.instructions`. It is not a
catalog, not lineage, not governance and not a query engine; it carries definitions between tools and leaves storage and
ownership to the catalog around it. Backers include Snowflake, dbt Labs, Salesforce, Databricks, Oracle, Dremio and
Collibra; converters exist to the dbt Semantic Layer and Apache Polaris, and MetricFlow consumes OSI models.

Sources: https://ossie.apache.org/ , https://github.com/apache/ossie (core-spec),
https://cwiki.apache.org/confluence/spaces/INCUBATOR/pages/430408796/OssieProposal .

## 2. Where it touches this repository
- **Analytic Library** (`trellis-analytics`): a `MeasureDeclaration` (name, unit, value field, as-of, scope, basis,
  confidence, annotation) is an OSI `metric` without an `expression`; a `TableColumn` is an OSI `field`; a manifest is a
  `semantic_model`. The library's steps compute in Python, so no declaration carries SQL today.
- **Egeria**: every OSI noun exists except a business-metric type. The closest carrier is `DataValueSpecification`, the
  Data Designer supertype of `DataClass` and `ValidValueDefinition`: `specification`, `specificationDetails`, `dataType`,
  `units`, absolute and relative uncertainty, `namespacePath`, and the matching rules `matchPropertyNames`,
  `matchThreshold`, `namePatterns`; `DataValueHierarchy` for specialization, `DataValueAssignment` to any element. It can
  hold an expression, dialect, type and unit; it has no aggregation grain and no time-dimension binding. (Attributes
  read from pyegeria 6.2.19's Data Designer specs; the builder confirms them against the Egeria type archive first.)
- **Portal / Dr.Egeria**: Data Structures, Data Fields and schema relationships map onto `datasets`, `fields` and
  `relationships`; a report spec could emit an OSI file.

## 3. Proposed order (for discussion)
1. **Export** library declarations as OSI: one converter beside the publish table; basis and absence words go into
   `ai_context.instructions`; a measure computed in Python exports with no `expression` rather than a fabricated one.
2. **Import** OSI `metrics` as declared-not-implemented `MeasureDeclaration`s: one reader module; the imported measure
   sits in the library's existing `no reader yet` state until a step computes it.
3. **Portal**: a Dr.Egeria report spec that emits Egeria's data design as OSI `datasets`, `fields`, `relationships`.
4. **Store** OSI metrics in Egeria: first a convention on `DataValueSpecification` (a subtype or classification naming
   it a metric; expression in `specification`, dialect in `specificationDetails`; grain and time dimension as additional
   properties until a type carries them; `DataValueAssignment` to the dataset measured). Propose a `MetricDefinition`
   subtype upstream only if the convention proves too loose, with step 1's export as the worked example.

None of these changes a declaration's meaning, so the convert-as-touched rule holds.

## 4. Questions to settle before any brief
- Does step 1 ride LS2 step 3 (the `trellis-analytics-re` split) or follow it as its own step?
- What `dialect` does a Python-computed measure claim? Proposal: none; the absence is stated, not filled.
- Does using `DataValueSpecification` as a metric carrier need a type ask to the Egeria leads first, or is a convention
  acceptable for the first cut? Check the current type archive for anything newer before deciding.
- Which consumer first: dbt MetricFlow, Snowflake, or only our own round trip? That sets how strictly 0.1 vs 0.2 is followed.

## 5. Watch
Ossie spec 0.2 and the Metric Language working group (fixes the `expression` shape the export emits); the Catalog working
group and the Polaris converter (the storage pattern Egeria should match or offer); Incubator graduation (the signal for a
type proposal upstream).

The data-space standards looked at alongside this one (IDSA's Dataspace Protocol, Gaia-X) are "watch" only: a
data-space connector beside the quickstart pays only when a counterparty runs one.
