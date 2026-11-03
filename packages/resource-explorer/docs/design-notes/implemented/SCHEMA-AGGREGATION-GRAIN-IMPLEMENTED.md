# Schema is the aggregation grain — shape 1, with the credential probe

**Replying to:** `REPLY-SCHEMA-AS-SUB-RESOURCE.md` shape 1 (§1), its §2
credential integration and its §5 per-engine correction (project owner,
2026-09-24) — which answer `ASK-SCHEMA-AS-SUB-RESOURCE.md` (#263).
**Built against:** `main` at `1f56d2df`.
**Not built:** shape 2 (`sub_resources` rows, D3's literal registration) —
§4 sequences it as the next, separate piece. Nor any engine but PostgreSQL,
nor §3's filesystem discovered-level rule, nor §16's
`coverage_signals`/`coverage_profile`/`preliminary_fit`/`requirement_fit`
(those analyses do not exist yet; they inherit the grain when they are built
against the same declaration).

**The REPLY doc itself is not on `main`.** It is on
`re/merge-reply-schema-as-sub-resource` (`2be0c625`), unmerged at the time this
was built; the spec was read from that commit. The same is true of `#262`'s
`requires_capability` work (`re/requires-capability-combined-gate`,
`450e7b91`) — see §2 below for what that changed about this slice.

## 1 · Containment is declared per engine (§5 point 1)

`surveyors/database/connection.py` gains `ContainmentLevel` and
`EngineContainment` beside `EngineCapabilities`, following the same
"declared per engine, never assumed" pattern:

- each level carries its name **in that engine's vocabulary**, its Egeria
  technology type, the four semantic flags (`namespace`, `owner`,
  `security_boundary`, `physical_unit`), the default container and the system
  containers to exclude (exact names plus prefixes, for `pg_toast*`/`pg_temp*`);
- the **aggregation grain is derived, not declared** (§5 point 2): the
  innermost `namespace` level above table. Postgres → `schema`, because a
  Postgres *database* is deliberately declared `namespace=False` — one
  connection cannot qualify a table with it, which is also why cross-database
  references need a foreign server (§5 point 4);
- `containment_for_engine(db_type)` resolves an engine name (not a connection)
  so `db_derived`, the zero-fetch step, can group correctly for a database
  whose credentials are gone;
- the declaration also carries **`structural_floor`** (architecture session,
  2026-09-24, mid-build): how much structure the engine lets a credential see
  when it cannot read the data. Postgres is `unprivileged` — `pg_class` /
  `pg_namespace` are readable whatever the table grants, which is precisely
  what `#257`'s catalog-only fallback and the `structure_only` credential
  state below both rest on. That property does **not** generalize
  (`role_grant` for Oracle's `SELECT_CATALOG_ROLE` / SQL Server's `VIEW
  DEFINITION`; `none` for MySQL, whose `information_schema` shows only what
  the user already holds a privilege on), so the field exists now for a future
  engine to say so rather than inherit Postgres's. The downstream consequence
  — a completeness state for "M is not established" when there is no floor —
  is deliberately NOT built here; it belongs with MySQL/Oracle support;
- **PostgreSQL is the only instance.** Anything else resolves to
  `NO_CONTAINMENT`, and every analysis then reports whole-database output
  labelled `containment_not_declared` — an undeclared hierarchy, explicitly
  not a finding that the engine has one flat namespace.

## 2 · Schema-scoped output, no new registration (§1's table)

`surveyors/database/schema_scope.py` is the new seam: which containers exist
in a set of stored rows, the `WHERE`-clause equivalent over loaded rows, the
rollup envelope, and the per-container reading of the credential probe. It
holds no domain logic — the checks stay in `db_derived.py`.

`db_derived.py` gains a section §9 that runs the **same pure check functions**
over container-scoped inputs. The whole-database fields on every payload are
untouched; `by_schema` and `aggregation` are added beside them, so the publish
path, the fact layer and the results cards keep working.

| Analysis | Per schema | Rollup (`aggregation.rollup_kind`) |
|---|---|---|
| `db_classification` | that schema's kind, scores, coverage | `set_of_kinds` — the kinds present **with counts**, plus the undecided and insufficient-signal schemas, so every schema is accounted for and nothing lands in an average |
| `db_relationship_graph` | components/FK density **within** the schema | `per_container_summaries_plus_cross_edges` — per-schema summaries plus `cross_container_edges`, counted in **no** schema's own edge count |
| `db_fingerprint` | container-relative signature and digest | `list_of_signatures` plus `cross_container_matches` |
| `schema_conventions` | each check per schema | `totals_plus_spread` — the existing whole-database totals, with the per-check spread and which schemas carry the gaps |
| `grain_determination` | untouched — already per table | marker only, `is_rollup: False`, `grain: "table"` |

`db_documentation_coverage` **does not exist as code** — it appears only in
`multi-resource-questions-design.md`'s question tables. Skipped, as the ask
allowed.

Every rollup carries `is_rollup: True` and `averaged: False`, and each is a
set, a list or a total — never a mean.

**System containers** (`pg_catalog`, `information_schema`, `pg_toast*`) are
excluded through the declaration, at one place, and the excluded names are
reported in the rollup rather than silently dropped. `public` is an ordinary
schema.

## 3 · Credential capability, per schema (§2)

- `schema_scope.container_scope_states()` turns the probe's existing
  `by_schema` map into five states: `readable`, `structure_only` (USAGE, no
  SELECT — the `coco_ods` incident), `partially_readable`, `not_visible`,
  `empty`. No probe yields `{}`, never "everything is readable".
- `_credential_scope_status()` keeps its database-wide `fraction` (the banner
  and fact envelope render it) and gains `by_container`, `shortfall` and
  `schema_fraction` — §2's wording, *"2 of 4 schemas readable; 3 of 26
  tables"*, schema clause first.
- Each per-schema analysis payload gets its own `_status` when that schema is
  short, so a structure-only schema's findings cannot render like a fully-read
  schema's. Joined in the adapter, where the survey blob is reachable —
  `db_derived` opens nothing.
- The credential-capability **RFA now names the short schemas** and leads with
  the schema fraction.

## Judgement calls

1. **Fingerprint threshold.** The REPLY names the finding to surface but no
   threshold, so the existing `_COPY_JACCARD` 0.95 / `_SUBSET_CONTAINMENT`
   0.90 / `_RELATED_JACCARD` 0.50 / `_REPORTABLE_JACCARD` 0.30 are reused
   unchanged. A second, container-only set would be a number nobody could
   compare against anything.
2. **Container-relative signatures.** `_signature` qualifies every entry with
   the schema name, which scores two identical schemas at Jaccard 0.0. Per
   container the signature is relative (`table.column:type`), which is what
   makes `coco_pharma.coco_ods` vs the `coco_ods` database surface at all.
   Comparison is container-to-container, both inside one database and across
   databases.
3. **Cross-schema FKs are withheld from the scoped run**, not re-labelled
   afterwards: left in, the edge inflates the source schema's `edge_count`
   and then reads as a `dangling_reference`. Withholding them makes the
   scoped check's own "not one declares a foreign key" sentence false for
   such a table, so that sentence is restated per schema.
4. **Rollup wording** is this implementation's, since the REPLY specifies the
   shape and not the prose. Each rollup sentence names itself a rollup and
   names the spread.
5. **The rollup sentence is appended to `explanation`.** The screen relays
   `explanation` and skips nested objects, so a breakdown living only in
   `by_schema` would be invisible to the reader who raised this.
6. **`#262`'s launcher message could not be extended**, because it is not on
   `main`. The schema attribution went into the surfaces that do exist (the
   probe's RFA, the fact envelope) and the per-schema reading lives in
   `schema_scope`, so `#262`'s own `credential_capability.py` can adopt it in
   one call when it lands.
7. **The REPLY's "`schema` on every row" claim holds, with a correction:** the
   column is `schema_name`, `NOT NULL` on `database_schemas`,
   `database_tables`, `database_columns`, `database_column_profiles` and
   `database_table_activity`. Scoping is a filter, as promised.
