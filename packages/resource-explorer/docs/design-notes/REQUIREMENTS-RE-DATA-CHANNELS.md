# Requirements: how RE reaches data (data channels)

Status: draft for the project owner and Mandy to react to. 2026-10-14.
Origin: the owner's discussion after engine-host step 1. This note is the short-term answer and a start at the roadmap; it decides nothing beyond what the owner said.

## 1. The question

Every database in RE today is Postgres. Egeria talks to many server types, with a connector per type, because what can be derived differs by server.

The question is whether RE must support the same set of server types, or a broader one.

The owner's position (2026-10-14):

- RE may run only some steps of a survey, with Egeria's connectors as the data channel.
- That path alone has two costs:
  1. RE could not run independently of Egeria unless all you have is Postgres.
  2. RE could not be deployed to reach where Egeria can't.
- So the Egeria-channel path may be part of the answer, but it cannot be the only path in the long term.
- Reuse existing connector libraries rather than rebuilding them: Prefect, Airflow, and DataKitchen TestGen. The owner is investigating TestGen.

## 2. Deployment situations RE must serve

| | Situation | What it needs |
|---|---|---|
| A | **Standalone.** No Egeria | Native adapters for every server type RE claims |
| B | **Alongside Egeria.** Egeria can reach the server | Either channel, chosen per step |
| C | **Long arm.** RE runs near the data, where Egeria can't reach (another network, a cloud account, a locked-down zone), and publishes back to Egeria | Native adapters, plus publishing |

A and C both need native adapters. Native adapters are therefore a long-term requirement, not a fallback.

## 3. One capability contract, several channels

RE's steps consume capabilities, never a specific channel. Each capability can report "not supported", "not collected" or "not sampled", and none of these may ever show as zero or as "nothing found".

| Capability | What it gives |
|---|---|
| Inventory | Schemas, tables, columns, keys |
| Statistics | Row counts, sizes, last change, catalog column statistics |
| Value sampling / value profile | Measures read from the data, within budgets |
| Permissions | Who can see and do what |
| Credentials | How the connection is authorised |
| Server-specific extras | Replication, external dependencies, and so on |

A server type is supported capability by capability, not all or nothing. This mirrors the per-connector variability in Egeria.

Every result records the channel that produced it: native, Egeria, or a named library or service.

**Already in place.** `surveyors/database/connection.py` declares `EngineCapabilities` per capability. Postgres is the one implementation, and "not supported" is carried through to annotations (`column_profile_step.py`). This is the seam to extend, not a new one.

Capability names and their absence states should follow Egeria's connector vocabulary where it exists, so that findings map back upstream.

## 4. Build vs reuse

| Capability | Native (RE) | Library reused inside RE | External service as a channel |
|---|---|---|---|
| Credentials | RE's store | Prefect Blocks | Egeria's integration connector (see §6); Airflow connections |
| Inventory | Postgres | SQLAlchemy inspector; DataHub / OpenMetadata ingestion sources | Egeria's connectors; an Airflow DAG |
| Statistics | Postgres catalog | Thin per-engine layer | Egeria; Airflow |
| Value profile and quality | RE's sampling and matching | DataKitchen TestGen; Great Expectations / Soda | TestGen run as a service; Airflow |
| Analysis (coverage, staleness, investigations, questions) | RE, server-neutral | — | — |

Notes:

- **Prefect.** RE already runs on Prefect, so its integrations and Blocks fit with no new runtime.
- **Airflow.** Egeria Workspaces already has compose scripts for Airflow. RE would trigger DAGs and read the results, not import Airflow core, so Airflow's providers become reachable without RE carrying the dependency.
- **DataHub and OpenMetadata ingestion.** These are the closest analogue to Egeria's connectors: per-source metadata extraction in Python, under Apache 2.0. Both can run headless.
- **What to check for every candidate:**
  - the licence;
  - the weight of its dependencies;
  - whether it is usable as a library or only as an application;
  - whether it reports absence honestly.

## 5. Profiling: catalog statistics vs values

**Today.** `postgres_column_profile` works in two ways:

- **Measures** come from `pg_stats`: null fraction, distinct count, most-common values. This is the default `catalog_stats_only`, and it is only as fresh as the last `ANALYZE`.
- **Values are sampled only for matching** against data classes and reference data.

The sampling machinery is sound and engine-independent in design:

- strategies: head, random (`TABLESAMPLE … REPEATABLE`), systematic, stratified, and full (explicit only);
- row, byte and time budgets per column;
- "not sampled" is never shown as "no match".

**Gap, where TestGen is ahead.** RE does not yet have:

- value-based measures: min and max, lengths, formats and patterns, distributions, and null and blank counts measured rather than estimated;
- quality tests generated from the profile and re-run over time;
- measures from a sample on very large tables.

**Direction.** Add a "value profile" capability behind the same seam. It reuses RE's sampling settings and absence rules, backed by TestGen or an adapter modelled on it. RE's analysis stays on top.

## 6. Open questions

**For the owner and Mandy:**

- **Credentials.** The owner believes Egeria's integration connector is what obtains credentials. Should RE have a similar interface, so that one consumer-side interface carries both the credentials and the server specifics?
- **Reading Egeria's catalogued structure.** For a non-Postgres server that Egeria catalogued, does RE read the schema, table and column elements? Today nobody creates them unless the cataloguer is configured.

**For the owner:**

- **TestGen findings.** Is it usable as a library, or only as an application with its own results store? How much do its profile measures overlap RE's?
- **Ranking server types.** Which ones do people actually have? Which does Egeria already cover? Which can Egeria not reach?

## 7. Roadmap shape (post-Alpha)

0. **Short term, unchanged.** Engine-host steps 1 and 2, then the Alpha demo, Postgres only. Engine-host step 2 completes an action whose target is not registered in RE with a clear reason. "Adopt from Egeria" (register from the asset's connection and endpoint) comes later and needs credentials (§6).
1. **Name the seam.** Steps call `EngineCapabilities`-gated adapters only, with no new Postgres assumptions. This is mostly refactoring.
2. **Credentials as a pluggable source.** RE's store first, then Prefect Blocks, and Egeria's interface if one exists.
3. **Value profile capability.** On Postgres first, informed by TestGen.
4. **The Egeria channel** for server types Egeria catalogues.
5. **Native adapters by demand.** One server type at a time, preferring reused libraries, with the long-arm deployment as a driver.
