# How TestGen, Resource Explorer and Egeria fit together

*(Status refreshed 2026-10-28: the diagrams below draw the value profile, propose and run steps and Curate's review as
"designed, not built". Main has since built them (#809, #816, #822, #880, #887). Publishing DataQualityRules is still
only on a branch. The user-facing account is `docs/testgen-integration-guide.md`. The original status line is kept below.)*

**Status:** reference, 2026-10-20. Two views of the same integration (owner's option C: TestGen's test
catalogue is vendored as data; RE runs it; Egeria is the catalog of record):

- **Conceptual:** who does what, with no internals.
- **Technical:** the modules, tables and calls.

In the technical view, **solid** lines and boxes are built and merged. **Dashed** ones are designed and
not built: see `DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md` and `DESIGN-PUBLISHING-COLUMN-VERDICTS.md`.

## Conceptual

```mermaid
flowchart LR
  TG["DataKitchen TestGen<br/>upstream project"]
  P(["Steward"])
  RE["Resource Explorer"]
  DB[("Surveyed database")]
  EG["Egeria<br/>catalog of record"]

  TG -- "test catalogue,<br/>vendored as data" --> RE
  P -- "reviews, accepts,<br/>publishes" --> RE
  RE -- "profiles values,<br/>runs checks" --> DB
  EG -- "coordinates surveys" --> RE
  RE -- "reports, verdicts,<br/>quality results" --> EG
  RE -- "accepted rules,<br/>confirmed data classes" --> EG
  EG -- "catalogues tables<br/>and columns" --> DB
```

Three points the picture carries:

- **TestGen does not run.** Its test-type definitions and per-dialect SQL are copied into RE as data and
  kept in step by a sync script.
- **A person decides what becomes a fact.** Measurements and proposals reach Egeria as survey
  annotations. Accepted rules and confirmed data classes reach it as governance elements, only by a
  person's Publish.
- **Either side can coordinate a survey.** Egeria can start a survey through RE's engine host, or RE can
  run it itself. The results land in the same place either way.

## Technical

```mermaid
flowchart TB
  subgraph TGR["TestGen fork (git)"]
    YAML["test_types/*.yaml<br/>per-dialect SQL"]
  end

  subgraph RE["Resource Explorer"]
    VEND["configdata/quality/testgen<br/>vendored catalogue"]
    SYNC["sync_quality_catalog.py --check"]
    subgraph RUN["Worker: engine host and executor"]
      EH["engine_host<br/>claims engine actions"]
      EX["SurveyDefinitionExecutor<br/>step dispatch"]
      CP["postgres_column_profile<br/>data-class / ref-data match"]
      VP["postgres_value_profile"]
      PQ["propose_quality_checks"]
      RQ["run_quality_checks"]
    end
    subgraph WEB["Web and Curate"]
      CUR["Curate: accept, reject"]
      PUB["Publish"]
    end
    subgraph REG["Registry, Postgres 5442"]
      CM[("database_column_matches")]
      SS[("resource_sampling_settings")]
      QT[("value profiles, proposals,<br/>verdicts, results")]
    end
  end

  DB[("Surveyed Postgres")]

  subgraph EGR["Egeria, qs-view-server"]
    GAP["GovernanceActionProcess<br/>engine actions"]
    SR["SurveyReport and annotations"]
    DQR["DataQualityRule<br/>GovernedBy"]
    DCA["DataClassAssignment"]
    CAT["JDBC cataloguer<br/>RelationalTable / Column"]
  end

  YAML -.-> SYNC -.-> VEND
  VEND -.-> PQ
  VEND -.-> RQ
  GAP -- "engine action,<br/>requestParameters" --> EH
  EH --> EX
  EX --> CP
  EX -.-> VP
  EX -.-> PQ
  EX -.-> RQ
  SS -- "sampling scopes" --> CP
  CP -- "read tier SQL" --> DB
  VP -.-> DB
  RQ -.-> DB
  CP --> CM
  VP -.-> QT
  PQ -.-> QT
  RQ -.-> QT
  EX -- "publish, inline" --> SR
  CUR -.-> QT
  CUR -.-> PUB
  CM -.-> PUB
  PUB -- "kept survey" --> SR
  PUB -.-> DQR
  PUB -.-> DCA
  CAT -- "column elements" --> SR
  CAT -.-> DQR
  classDef designed stroke-dasharray: 4 3;
  class VEND,SYNC,VP,PQ,RQ,CUR,QT,DQR,DCA designed
```

Reading the technical view:

- **Built and merged:**
  - the engine host forwarding the request parameters a step declares (#766);
  - column-profile verdicts published by the executor (#750, live-verified 2026-10-20);
  - the verdict store `database_column_matches` (#759);
  - sampling settings at every scope (#767);
  - Curate's Publish of the kept survey.
- **Designed, not built:**
  - the vendored TestGen catalogue and its sync script;
  - the value-profile, propose and run steps, and their tables;
  - Curate's Publish sending the stored verdicts (`DESIGN-PUBLISHING-COLUMN-VERDICTS.md` §1);
  - accepted checks becoming `DataQualityRule`s, and confirmed verdicts becoming `DataClassAssignment`s.
- **Who each call runs as:**
  - Publish runs as the signed-in person.
  - The engine host runs as `Daemon(ENGINE_HOST, requested_by)`.
  - A queued run publishes as the service account, with `Ownership` set to the requester.
- **What Egeria creates itself:** column elements, made by Egeria's JDBC cataloguer for schemas a Curate
  catalogue commit chose. RE finds them by walking the schema; it never creates them.
