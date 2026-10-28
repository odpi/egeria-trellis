<!-- Source of record: the reviewed web document https://claude.ai/code/artifact/2939cea2-bda6-4b98-afcd-ea1b97210013 (rev 16, 2026-10-12), exported here with the owner's corrections folded in. The in/record/out diagram lives in the web document. -->

# Amundsen users and RE/Egeria: a migration assessment

2026-10-12 · the project owner with the design session

## Amundsen is archived; its users hold a graph worth keeping and a front end they will miss

The [Amundsen repository](https://github.com/amundsen-io/amundsen) was archived in September 2026. Its README reads: "Due to inactivity, this project was archived in September 2026. The contents will remain available for historical purposes." The last code change was December 2024; 2025 and 2026 carry only a maintainer move and the archiving notice. 4,784 stars, 959 forks, created May 2019 at Lyft.

[37 companies are listed as users](https://github.com/amundsen-io/amundsen#who-uses-amundsen), among them Lyft, ING, Square, Instacart, Brex, Snap, Workday, Asana, Gusto, REA Group, Delivery Hero, WeTransfer and Remitly. Many have already moved to DataHub or OpenMetadata; the list is historical.

What an Amundsen installation holds today:

- A metadata graph, usually Neo4j (Atlas, Neptune and MySQL are the alternatives), with tables, columns, descriptions carrying their source, owners, tags, badges, dashboards, users, table and column lineage, usage counts, column statistics, watermarks, last-updated timestamps, ML features, queries and the applications that write each table.
- An Elasticsearch index built from that graph.
- A Flask and React front end for analysts.
- Databuilder ETL jobs on a scheduler, usually Airflow, with [40+ extractors](https://github.com/amundsen-io/amundsen/tree/main/databuilder): Snowflake, BigQuery, Redshift, Hive, Postgres, MySQL, Oracle, MSSQL, Glue, Delta Lake, Druid, dbt, Tableau, Mode, Redash, Superset, Databricks SQL, Kafka schema registry and OpenLineage.

## An Amundsen user relies on two different things, and the answer differs for each

1. **The curated record.** Descriptions people typed, owners, tags, badges such as deprecated, PII and certified, bookmarks, and the ETL-fed facts: schema, statistics, lineage, usage. This is the asset worth migrating, and it migrates well.
2. **The consumer experience.** A search box an analyst opens daily, ranked by popularity, with table pages showing columns, statistics, lineage, a data preview, who uses this table, one-click Jira issues, notifications and announcements. This is what they will miss, and it is the part RE does not provide.

## The record fits Egeria's types, with two gaps carried as annotations

| Amundsen | Egeria | Fit |
| --- | --- | --- |
| Database, Cluster, Schema, Table, Column (key `db://cluster.schema/table`) | Database, DeployedDatabaseSchema, RelationalTable, RelationalColumn; qualifiedName derived from the key | Full. "Cluster" maps to the hosting server or an additional property. |
| Description with its source (user, dbt, extractor) | `description`, with the source as RE's signed testimony (author, date) | Full, and better: RE keeps who said it. |
| Owner (user email) | `Ownership` classification to a UserIdentity, or a Person via ActorProfile | Full once the owner-fact ruling lands. |
| Tags | `InformalTag` for user tags, `SearchKeyword`; curated tag sets as valid values | Full. |
| Badges, admin-set: pii, deprecated, certified, custom | Governance classifications: Confidentiality, retirement notice, `Certification`; custom badges as a valid value set | Full for the common badges; custom ones need a mapping table. |
| Users and profiles | UserIdentity, Person, Team | Full. |
| Dashboards (Tableau, Mode, Redash, Superset) with charts, queries, owners, usage | `Report` and `DeployedReport` assets, `InformationView`; owner and tags as above | Mostly. Egeria's report model is thinner than Amundsen's dashboard model (chart list, execution status, last run): additional properties plus a note. |
| Table lineage (OpenLineage, dbt) | `DataFlow` between assets; `Process` for the job | Full. |
| Column lineage | `DataMapping` between schema attributes | Full. |
| Application (the Airflow DAG that writes a table) | `Process` plus DataFlow; `ImplementedBy` | Full. |
| Table and column statistics (pandas-profiling, Elasticsearch stats) | Survey annotations: `ResourceProfileAnnotation`, `DataProfileAnnotation`, `ResourceMeasureAnnotation` on a SurveyReport | Full, and RE already publishes these. |
| Last updated, watermarks (partition low and high) | `ResourceMeasureAnnotation` for now | Adequate; no first-class watermark type. |
| Usage and popularity (reads per user and table from query logs) | None native. RE's registry can hold it and publish a measure annotation. Egeria's `Like` is a different thing. | Gap. Popularity ranking in search is Amundsen's signature feature. |
| Queries (SQL attached to tables), resource generation code | No obvious Egeria home; annotations or RE rows | Gap, minor. |
| ML features (feature group, generation code) | Unclear in Egeria's types; needs a check | Unknown. |
| Bookmarks, frequently used by | `Like` for bookmarks; usage as above | Partial. |
| Notifications, Jira issue creation, announcements, product tours | Front-end features, not metadata | Out of Egeria's scope; a consumer app's concern. |

## The migration is one Cypher reader, about twelve mappings, and writes pyegeria already has

Amundsen's own tooling makes the export easy. Databuilder's [Neo4jExtractor](https://github.com/amundsen-io/amundsen/blob/main/databuilder/databuilder/extractor/neo4j_extractor.py) runs an arbitrary Cypher query against the graph and yields model objects, and the metadata service's [proxy API](https://github.com/amundsen-io/amundsen/blob/main/metadata/metadata_service/proxy/base_proxy.py) lists every resource kind for users on Atlas or Neptune.

1. **Read the graph** by Cypher, one query per node kind, keyed by Amundsen's key; or through the metadata API where Atlas or Neptune sits behind it.
2. **Write to Egeria through pyegeria**, the channel RE already uses, with the mapping above. Descriptions become signed testimony with the Amundsen source as author. Idempotent by qualifiedName derived from the Amundsen key. One proof row per element by GUID, RE's rules.
3. **Re-attach the live sources** through Egeria's connectors (the JDBC cataloguer for any JDBC database, Unity Catalog, Kafka, files) so schema facts stay fresh without databuilder. Where no Egeria connector exists (BigQuery, Hive metastore, Glue, the BI tools) the databuilder extractor keeps running, with its loader pointed at a small pyegeria publisher instead of Neo4j CSV files. That is a one-class change in their ETL, not a rewrite.
4. **Drop the Elasticsearch index.** Egeria's search and RE's pgvector index replace it; nothing in the index is primary data.

For comparison, [OpenMetadata's Amundsen connector](https://docs.open-metadata.org/latest/connectors/metadata/amundsen) reads Neo4j and migrates databases, schemas, tables, tags, descriptions and owners; its documentation names no dashboards, no usage and no lineage. DataHub's path is similar. An importer that also carries dashboards, lineage, statistics and usage would be more complete than either incumbent's.

## What an Amundsen user would miss: the consumer features, feature by feature

Amundsen's front end is [configured feature by feature](https://github.com/amundsen-io/amundsen/blob/main/frontend/docs/application_config.md). Against RE as it stands, what is already planned for it, and the Portal apps, the picture is this. RE serves the steward, the data engineer and the data scientist; the rows below are the analyst's features, and several are closer than the first draft of this table said (owner's corrections, 2026-10-12).

| Amundsen feature | RE today or planned | Portal apps today | Gap for a migrating user |
| --- | --- | --- | --- |
| Search across tables, dashboards, people, ranked by popularity | Scouting finds registered resources; no ranking by use | Search for resources exists | Ranking by popularity; a search that spans people and BI dashboards |
| Table page: columns, types, descriptions, stats, owners, tags, badges in one view | Spread across stages for the person doing the work | A resource view exists | One consumer page per table |
| Editable table and column descriptions, with the source shown | Context facts as signed testimony; column descriptions not yet an RE surface | Unknown | Column-level description editing for a non-steward |
| Column statistics and distinct-value distributions | Coming to RE (owner); survey report and Understanding charts today | Unknown | Casual access outside the stage model |
| Table and column lineage, upstream and downstream | Dependencies for repositories; none for databases yet. Egeria has native OpenLineage support, so an Airflow or dbt estate already emitting run events feeds it; an Airflow integration is worth looking at, in or beside RE | Egeria lineage views exist | Database lineage in RE; a consumer lineage view |
| Data preview (sample rows) | None yet; to be added (owner) | Exists | A preview in RE's resource view |
| Frequently used by, popular tables, my bookmarks | None | Likes exist in Egeria | Usage capture, recorded where a reader app can get it without RE |
| Ratings and feedback on a resource | Rating and category, notes, journal | Ratings on resources | A casual reviewer's path |
| Badges and tags on a browse page with curated tag sections | Tags on the resource; no browse page | Unknown | Browse by tag or badge |
| BI dashboards (Tableau, Mode, Superset) as catalogued resources with charts, queries, owners and last-run status | Not a resource kind RE registers. This is different from RE's own local dashboards and Understanding charts, which are RE's views, not catalogued BI assets | Reports in Egeria | Ingestion of BI dashboards as assets, and a page for them |
| Issue tracking (create a Jira issue from a table) | RFA exists, inside Egeria only | None | Tying RFA to Jira or another tracker: a broader integration |
| Notifications on resource changes; announcements; product tours | Notifications exist in RE and in Egeria; no tours | Notifications in Egeria | Product tours only, if ever |
| Data quality status from an external checker | Survey findings and data-class rules today; external annotators planned under the extensibility work | Unknown | A quality badge a consumer reads at a glance |

The summary after the corrections: RE is built to get information in, with proof, and much of the consumer list is planned or near (column stats, preview, notifications, RFA, external annotators). What remains missing for an Amundsen shop is the reader's surface: the single table page, popularity-ranked search, descriptions anyone can improve, and BI dashboards as catalogued assets.

## Getting information out belongs to a consumer app, not to RE

RE's job is the governed record: register, survey, curate, publish with proof, for the steward, the data engineer and the data scientist. Adding an analyst's browse and search to it would put another audience on one tool, which is the overload the Curate split is undoing. The Portal apps are closer: they search Egeria directly and let a person rate a resource. What neither has is the casual front door Amundsen users live in: a home of popular and bookmarked tables, a search ranked by use, one page per table, a description anyone can improve.

One constraint the owner set: the reader's app should not depend on RE's operational store. Requiring RE to be installed for a browser to run is limiting. So everything a reader needs, usage and popularity included, has to be in Egeria, where the built-in agents (Jaquard, Mendel, Liskov and the rest) already create and manage much of the record. RE contributes through Egeria, never as a second backend.

Egeria holds three flavours of resource, and a reader's first question is which one they are looking at:

- (a) what is known about, curated or not;
- (b) what has been curated and prepared as a data product that can be subscribed to;
- (c) what has been added to a data hub as a reusable asset under active management.

The owner's view, which this note adopts, is that more than one tool is needed, related and possibly independent:

| Tool | For whom | Does | Flavour |
| --- | --- | --- | --- |
| 1. Resource browser, the simple shop for data | Anyone looking for data | Search with popularity and feedback; query by scope and granularity; put resources into a personal or team work-list collection | All three, with the flavour shown on every result and usable as a filter |
| 2. Product manager's tool | Product managers | Create and manage data products with the Egeria agents | Makes (b) out of (a) |
| 3. Data hub management | Hub managers | Add, govern and retire reusable assets with the Egeria agents | Makes and keeps (c) |
| 4. Egeria Advisor extended, or a new trellis app | Anyone with a question | Find, understand and answer questions about resources by chat, dashboards, tables and analyses, popularity and reviews included, technical detail included; let a person register a standing need with its scope, requirements and context, and be notified when data that meets it appears; background agents track and monitor on their behalf | All three |

On the browser's flavour question: it should show all three. A reader rarely knows which flavour they want before they see what exists; what they want is to know, on each result, whether they can use it now (c), subscribe to it (b), or only ask about it (a). The flavour is a facet and a badge, never three browsers. The work-list collection is an Egeria collection with members, which is also how RE's own work lists reach Egeria, so a list made in the browser is visible to a steward in RE and a steward's shortlist is visible in the browser.

### Do the boundaries hold?

The spine is right. The three flavours are Egeria's own lifecycle, not an invented taxonomy, and every tool then has one actor and one verb against it: the browser finds across all three, RE makes (a) trustworthy, the product tool makes (b) from (a), the hub tool makes and keeps (c), the answering app explains any of them. That is the split-by-job principle applied to the whole estate.

**Browser versus answering app** (owner agrees, 2026-10-12). The line is browse versus ask. The browser serves someone who roughly knows what they want. The answering app serves someone who brings a need instead: current sales data from Ireland, new tabular data models for patient analysis. Those needs conform to contextual parameters as well as the common constraints, and the app should let a person register the need and be notified when something that meets it appears, not only answer once. The two stay separate apps and share one resource page component, so the same table never looks different in two doors.

**Product tool versus hub tool.** Both manage a governed collection with the Egeria agents; the difference is the lifecycle, subscribable versus actively managed. One tool with two workflows or two tools is not decided; the owner does not know enough yet, and neither does this note. Plan them together until a real difference in users appears.

**Egeria-only for readers is the pitch, not just a constraint.** For an Amundsen shop the landing footprint is Egeria plus the browser plus the importer: their metadata service, search service and front end replaced, with lineage and provenance they never had. RE, the product and hub tools and the answering app are opt-in from there. Nothing in the landing footprint requires RE to be installed.

**What modern means here** (owner, 2026-10-12). Not a chat bolted onto a 2019 catalogue. It means leveraging the cornucopia of analytics, not only traditional profiling; asking questions and probing the answers; background agents that track and monitor on a person's behalf; and governance, management, observability and auditability built in rather than added. One door that both browses and asks, a resource page that carries the flavour and what you can do next, a work list you can hand to a steward.

**What attracting the user base takes beyond the software.** A migration kit they can run in a day: the importer plus a databuilder publisher so their ETL keeps working. A quickstart deployment as easy as Amundsen's docker-compose. Parity on the daily loop first: search, table page, descriptions, lineage, popularity. And a conversation with the community before building, since the Amundsen Slack and former maintainers are reachable and will say what they actually kept.

Where the candidate surfaces stand today against the browser's job:

| Surface | Audience | Reads | Fit for the Amundsen user |
| --- | --- | --- | --- |
| RE | Steward, data engineer, data scientist | Egeria and RE's registry | No; another audience on a tool already being simplified |
| Portal apps (The Catalog, Egeria Explorer) | Steward and technical consumer | Egeria directly | Partly; search, rating and preview exist, no popularity, no per-user home |
| Resource browser, new, Egeria-only | Analyst, casual browser | Egeria only | Yes; the Amundsen front end's job, built once for all sources, and more modern than Amundsen's |
| Advisor extended or a new app | Anyone with a question | Egeria, RE's analyses where present | Yes for questions; not for browsing |

The recommendation is the browser first, Egeria-only. Four pages: Home (popular, bookmarked, recently changed), Search (ranked by use, filtered by tag, badge, flavour, scope and granularity), one Resource page (columns, descriptions editable by anyone signed in, stats, lineage, owners, ratings, where it is used, the flavour and what it lets you do), and Browse (by tag, badge, owner, source, collection). It writes back only what Egeria models: descriptions, tags, ratings, likes, comments, collection membership. Usage and popularity already have most of their Egeria home: the earlier nanny work captured what can be seen of use and surfaced it, and it needs reinstating after the recent architecture work. The browser ranks by that once it is back.

(diagram: in, record, out · three producers, three readers; see the web document)

Everything on the left writes into Egeria with proof; everything on the right reads it. The shop-for-data app is the one box that does not exist.

## Source integration: Egeria's JDBC cataloguer first, then the non-JDBC databases that matter

RE surveys Postgres today and nothing else. An Amundsen estate is mostly Snowflake, BigQuery, Redshift and Hive, with Postgres and MySQL at the edges. Two routes, and the split should follow who already does the work well.

**JDBC sources.** Egeria's JDBC integration connector catalogues any database with a JDBC driver: schemas, tables, views, columns, primary and foreign keys, with include and exclude lists by name. That covers Snowflake, Redshift, Oracle, SQL Server, MySQL, Postgres, Trino and Databricks SQL by driver. The plan here is not a lister per database in RE. It is: RE registers the server and its credentials, attaches the catalog target, and reads back what Egeria catalogued, exactly as the database commit does for Postgres now. The work is per-driver proof on a throwaway, one test database each, and the credential handling for each driver's connection string. Two facts to carry from this week's reading of the connector: it does not copy table or column comments, so descriptions stay RE's to write; and a foreign key to an uncatalogued schema is skipped silently, so the manifest must say so.

**Non-JDBC databases of significance.** These need their own path, in roughly this order of demand among Amundsen users:

| Source | Why it matters | Egeria today | Proposed route |
| --- | --- | --- | --- |
| BigQuery | Second most common Amundsen source after Snowflake | No native connector; a third-party JDBC driver exists | A pyegeria-based cataloguer reading the BigQuery API (datasets, tables, columns, row counts, last modified), published as the JDBC one does |
| Hive metastore and Glue | The lakehouse estates | Unity Catalog connector exists; not Hive or Glue | A metastore reader (Thrift or Glue API) publishing tables and partitions; watermarks as measure annotations |
| Delta Lake and Iceberg tables | Same estates, table formats on object storage | File and folder cataloguing exists | Table-format readers on top of the file survey, schema from the log or manifest |
| MongoDB, Cassandra, DynamoDB | Document and wide-column stores | None | Schema inference by sampling, published as inferred schema attributes with the sample size stated |
| Elasticsearch and OpenSearch | Indexes people search in | None | Index mappings as schema attributes |
| Kafka topics with schema registry | Streams | Kafka connector exists | Reuse; add the registry's schemas |
| BI dashboards: Tableau, Mode, Superset, Redash, Looker | Amundsen's second resource kind | None | API readers publishing Reports with their queries and owners; the Amundsen importer carries the historical ones |

Each non-JDBC source is a survey step with its own StepManifest in the Analytic Library, read-only on the resource, publishing through the same channel. None of them belongs in RE's core; they are steps a user can add, which is what the extensibility skeleton was for.

Open question for the owner: which two sources first? The answer should come from the Amundsen users we can reach, not from this list.

## Verdict: viable as the governed backend now, as a product only with a new front door

**As a metadata backend: viable, and more complete than the incumbents' importers.** The Amundsen record fits Egeria's types with two annotated gaps, the export is one Cypher reader, and the write path exists.

**As a product an Amundsen shop can switch to this quarter: not yet.** Three things are missing: a reader's browser with popularity ranking, Egeria-only so it runs without RE; cataloguing for Snowflake, BigQuery and Hive on the Egeria side, partly the JDBC cataloguer already; and the importer itself.

**Recommended order.**

1. The resource browser, Egeria-only, four pages, with the flavour (known, product, hub asset) on every result. The owner's call: first, and it makes sense in general.
2. Reinstate the nanny usage and popularity work in Egeria after the architecture changes, so the browser can rank without RE.
3. The Amundsen importer, proven on a public sample graph: your Amundsen graph, governed in Egeria with provenance.
4. The JDBC cataloguer proven on Snowflake and Redshift test databases through RE's register-and-read-back path.
5. Non-JDBC sources as Analytic Library steps, two first, chosen with a real Amundsen user.
6. The product manager's tool and the data hub tool, each with the Egeria agents; the Advisor extension or new app for questions.

The browser, the product tool, the hub tool and the question-answering app are related and possibly independent; each reads Egeria, none requires another to be installed.

**Open questions for the owner.**

- [ ] Is there an Amundsen user we can talk to about what they must keep and what they would give up?
- [ ] Does Egeria have a type for ML features and for stored SQL queries, or does the importer carry them as annotations until one exists?
- [ ] Is the browser a Portal app (same stack, same deployment) or a trellis app beside RE?
- [ ] What does reinstating the nanny usage work need after the architecture changes, and who owns it?
- [ ] Which Egeria agents already produce what the browser shows, and what is left for RE or the importer to add?
- [ ] Which two non-JDBC sources first?
