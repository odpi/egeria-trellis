# Ground-truth partition — egeria-workspaces

> **PRE-REGISTERED**, with provenance marked per component (see `README.md`).
> Scored **per perspective** (plan §5a, design §4.1).

**Target:** egeria-workspaces
**Checkout:** `/Users/dwolfson/localGit/egeria-v6/egeria-workspaces-fs`
**Perspective:** deployment
**Vocabulary:** SoftwareCapability subtypes (Area 0) — *not* SolutionComponentType (design §4.2)
**Written by:** dwolfson
**Written at:** 2026-08-20

> Transcribed by the assistant from the maintainer's hand-written workspaces outline
> (since folded into `docs/Architecture.md#runtime-neighbours`).
> **Component names, groupings and descriptions are the maintainer's**; the assistant added
> structure, types, and the perspective/vocabulary fields. Marked per component.
>
> This is the **deployment** perspective — components are named by `container_name`, which is
> what appears in `docker ps` and what the maintainer called them. Types come from the
> `SoftwareCapability` family, not the 13 `SolutionComponentType` values.
>
> The 11 optional runtime add-ons are **components, not blueprints** (§8.2b). The 3 runtime
> modes (`demo-quickstart` / `local-quickstart` / `freshstart`) are **not components at all**.

---

## Blueprints

### QuickStart

- quickstart-egeria-main
- quickstart-egeria-watchdog
- quickstart-jupyter-work-full
- quickstart-web-server
- quickstart-pyegeria-web
- quickstart-my-profile
- obsidian-quickstart
- egeria-shared-kafka
- egeria-shared-postgres
- egeria-shared-kroki
- egeria-shared-kroki-mermaid
- egeria-shared-openlineage-proxy-backend

### FreshStart

- freshstart-egeria-main
- freshstart-jupyter-work-full
- freshstart-web-server
- freshstart-pyegeria-web
- egeria-shared-kafka
- egeria-shared-postgres
- egeria-shared-kroki
- egeria-shared-kroki-mermaid
- egeria-shared-openlineage-proxy-backend

---

## Components

### quickstart-egeria-main

- **Type:** Application
- **Perspective:** deployment
- **Notes:** egeria-platform — a single Java runtime running several Egeria servers, not
  standalone processes.
- **Provenance:** maintainer

### quickstart-egeria-watchdog

- **Type:** Application
- **Perspective:** deployment
- **Provenance:** maintainer

### quickstart-jupyter-work-full

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Jupyter server.
- **Provenance:** maintainer

### quickstart-web-server

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Apache web server.
- **Provenance:** maintainer

### quickstart-pyegeria-web

- **Type:** Application
- **Perspective:** deployment
- **Files:**
  - `compose-configs/egeria-quickstart/PyegeriaWebHandler/**`
- **Notes:** PyegeriaWebHandler — a complex Python module using FastAPI, serving both MCP
  commands and the Egeria Portal; configurable for local or demo mode. The Portal provides many
  single-page applications over Egeria data and services, JavaScript front ends with Python
  backends. MCP serves both report execution and Dr.Egeria commands. Near-superset of the
  FreshStart copy (§8.2a): 90 shared paths, 60 byte-identical, 30 divergent, 48 unique here.
- **Provenance:** maintainer (files added by assistant)

### quickstart-my-profile

- **Type:** Application
- **Perspective:** deployment
- **Notes:** A Textual application letting users manage their environment.
- **Provenance:** maintainer

### obsidian-quickstart

- **Type:** Application
- **Perspective:** deployment
- **Notes:** An Obsidian client configured with a vault over the Coco Workbooks; can author and
  issue Dr.Egeria MCP calls.
- **Provenance:** maintainer

### freshstart-egeria-main

- **Type:** Application
- **Perspective:** deployment
- **Notes:** egeria-platform — single Java runtime running several Egeria servers.
- **Provenance:** maintainer

### freshstart-jupyter-work-full

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Jupyter server.
- **Provenance:** maintainer

### freshstart-web-server

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Apache web server.
- **Provenance:** maintainer

### freshstart-pyegeria-web

- **Type:** Application
- **Perspective:** deployment
- **Files:**
  - `compose-configs/egeria-freshstart/PyegeriaWebHandler/**`
- **Notes:** PyegeriaWebHandler with nothing pre-configured. **Two components, not one** — split
  by deployment unit, the pre-registered answer that reordered §8.2's identity precedence.
- **Provenance:** maintainer (files added by assistant)

### egeria-shared-kafka

- **Type:** EventBroker
- **Perspective:** deployment
- **Provenance:** maintainer (type assigned by assistant)

### egeria-shared-postgres

- **Type:** DatabaseManager
- **Perspective:** deployment
- **Notes:** A pgvector-enhanced Postgres supporting all egeria-workspaces runtimes.
- **Provenance:** maintainer (type assigned by assistant)

### egeria-shared-kroki

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Helper for mermaid graphs.
- **Provenance:** maintainer

### egeria-shared-kroki-mermaid

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Helper for mermaid graphs.
- **Provenance:** maintainer

### egeria-shared-openlineage-proxy-backend

- **Type:** NetworkGateway
- **Perspective:** deployment
- **Notes:** Common proxy supporting OpenLineage integration.
- **Provenance:** maintainer (type assigned by assistant)

---

## Optional runtime add-ons

> Components, **not** blueprints (§8.2b). Each attaches optionally to either solution. Names
> here are the add-on directories under `compose-configs/optional-associated-runtimes/`; their
> individual container names are not yet enumerated.

### airflow-marquez

- **Type:** WorkflowEngine
- **Perspective:** deployment
- **Notes:** Demonstrating OpenLineage and other metadata integrations.
- **Provenance:** maintainer (type assigned by assistant)

### dagster

- **Type:** WorkflowEngine
- **Perspective:** deployment
- **Notes:** Orchestrating pipelines and metadata harvesting with Egeria and OpenLineage.
- **Provenance:** maintainer (type assigned by assistant)

### prefect

- **Type:** WorkflowEngine
- **Perspective:** deployment
- **Notes:** Workflow automation and Egeria catalog integrations.
- **Provenance:** maintainer (type assigned by assistant)

### apache-atlas

- **Type:** InventoryCatalog
- **Perspective:** deployment
- **Notes:** An Atlas/Hadoop/Hive stack demonstrating Egeria's Apache Atlas integration.
- **Provenance:** maintainer (type assigned by assistant)

### mlflow

- **Type:** Application
- **Perspective:** deployment
- **Notes:** ML lifecycle tool with a MinIO artifact store.
- **Provenance:** maintainer

### milvus

- **Type:** DatabaseManager
- **Perspective:** deployment
- **Notes:** Vector database used in AI workloads.
- **Provenance:** maintainer (type assigned by assistant)

### duckdb

- **Type:** DatabaseManager
- **Perspective:** deployment
- **Notes:** Federation server for querying across Postgres, S3, Iceberg.
- **Provenance:** maintainer (type assigned by assistant)

### superset-compose

- **Type:** ReportingEngine
- **Perspective:** deployment
- **Notes:** Analytics and reporting dashboard used to present Egeria survey results.
- **Provenance:** maintainer (type assigned by assistant)

### unity-catalog

- **Type:** InventoryCatalog
- **Perspective:** deployment
- **Notes:** Open-source Unity Catalog, demonstrating Egeria's integration with other catalogs.
  **Two deployments** — one using Postgres, one native. A second variant case (§8.2a).
- **Provenance:** maintainer (type assigned by assistant)

### ollama

- **Type:** Application
- **Perspective:** deployment
- **Notes:** Local LLM runtime for experimenting with local inference alongside Egeria.
- **Provenance:** maintainer

### deltalake-spark

- **Type:** DataProcessingEngine
- **Perspective:** deployment
- **Notes:** Small sandbox: Spark, Delta Lake, MinIO (S3-compatible object store), Hive metastore.
- **Provenance:** maintainer (type assigned by assistant)

---

## Unassigned OK

> Content and working areas, not runtime components. Several belong to the **Dev/DevOps**
> perspective (design §4.4) and move there once it is written up.

- `coco-workbooks/**` — samples and tutorials in Jupyter and Dr.Egeria
- `coco-data/**`
- `exchange-freshstart/**` — shared folders across the freshstart ecosystem
- `exchange-quickstart/**` — shared folders across the quickstart ecosystem
- `portal-docs/**` — environment and Portal documentation, launched from the Portal
- `runtime-volumes/**` — local runtime state kept outside the containers
- `templates/**` — Dr.Egeria template files used by many components
- `work/**` — unmanaged area for local user work
- `workbooks/**` — to be deprecated shortly
- `workspaces/**` — to be deprecated shortly
- `docs/**`
- `tests/**`

---

## Excluded — not first-party

> `node_modules` **is tracked** in this repo — 1697 of 1703 tracked `.js` files (plan §3a).
> `obsidian-plugins/` itself is first-party (shared source for the Dr.Egeria Obsidian plugins);
> only its vendored dependencies are excluded.

- `obsidian-plugins/**/node_modules/**`
