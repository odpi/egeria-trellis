# Ask for the designer: previewing a table's rows (2026-10-19)

From the RE coordinator session, for the designer session. Please reply as `REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md` in this folder. The
other ask in this PR covers group scheduling and first-index consent; answer them separately or together.

**Owner decisions (2026-10-19, in the coordinator session):**
- **Credentials:** the person previewing supplies their own database credential. The surveyor is usually more
  restricted than a user.
- **Who may preview:** "any signed-in user - if they know the database credentials then they have access already".
- **Masking:** "Masking of sensitive data should be on by default".
- **Egeria:** "we should tell egeria so it can be audited centrally".
- **Placement:** "we might have a tab for table previews just like the schemas tab".
- **Agreed:** RE runs a typed query of its own, never SQL text; rows are shown and never stored (not in the registry,
  Egeria, traces or chat); each preview leaves an audit row without the values; the first version shows the first n
  rows, labelled as a preview, not a sample.

This is the first RE surface that shows **data** rather than metadata. Everything RE shows today describes tables.

## Precedent: DataKitchen TestGen (measured in its source, 2026-10-19)

- **Where:** a Data Catalog table/column preview shows **100 distinct rows** (`fetch_table_sample`). The source-data
  lookup behind a failed test or hygiene issue shows up to **500 rows**, sampled client-side.
- **Masking:** both paths mask PII **unless the user holds a `view_pii` permission**. Masked cells read
  `[PII Redacted]`, and the result carries `pii_redacted`, so the screen can say that something was hidden.
- **Where its PII knowledge comes from:** profiling sets a `pii_flag` per column, with a risk letter (A high, B
  moderate, C low) and a type (`A/ID/SSN`, `B/NAME/Individual`, `B/DEMO/Birthdate`, `A/ID/Security`…). It's a mix of
  column-name patterns and value patterns (SSN shape, credit-card match). It is a heuristic and is labelled "Potential
  PII". An unprofiled column has no flag, which means unknown, not clean.
- **Engines:** one query builder per SQL flavor (`row_limit_clauses(limit)`), across 12 flavors (Postgres, Redshift,
  Snowflake, BigQuery, Databricks, SQL Server, Oracle, SAP HANA, DuckDB…).

## What RE knows about sensitive columns today

- **Value-based:** the column-profile step's `data_class_match` checks columns against six seeded PII data classes.
  It already refuses to say "no PII" for a column it never read.
- **Name-based:** the survey's PII keywords, with their source recorded per run.
- **Asserted:** a Confidentiality classification set in Curate and published to Egeria.

So the honest answer is "partly". RE knows which columns were matched or classified, and it knows which columns were
never profiled. The rule that follows (owner: masking on by default): **mask what is matched or classified, and also
mask what was never profiled**, because "unknown" must not render as "safe". A column shows unmasked only if it was
profiled and matched nothing, and no classification says otherwise.

## Questions for the designer

1. **The tab:** a "Rows" (or "Preview") tab beside Schemas on the database page. What does a person pick first:
   schema → table, or a table from the Schemas tab that opens Rows? How many rows (20? 100?), and does it page?
2. **Masked cells:** how a masked cell looks (a cue on the cell, not a sentence). How the three reasons read
   differently: matched a PII class, classified confidential, never profiled. Whether the person can reveal one column
   at a time, and what that costs (a confirmation, a reason, an extra audit row).
3. **No credential:** what the person sees when they have no stored credential for this database. RE's existing
   pattern is the in-place "set the database credential ›" form from the Survey run line.
4. **Refusals:** a table the credential can't read, a timeout, or an empty table. Each needs its own state; "no rows"
   is not "could not read".
5. **Views:** should views be previewable? Running one can be expensive, and RE already counts them separately.
6. **The Egeria audit:** whether the person sees that the preview was recorded ("logged to Egeria"), and in what words.

## For the architect, not the designer

- **The Egeria record:** what element/relationship carries "who previewed which table, when, how many rows, which
  columns revealed". Candidates: an audit-log entry via the engine host, or a lightweight Note/Annotation on the table
  element. This is a question for Mandy; follow Egeria's lead on the type.
- **Non-Postgres engines (general, not only previews):** `connection.py` already models engine differences
  (`EngineCapabilities`, `EngineContainment`: the schema level vs MySQL's database, Oracle's owner, DuckDB's catalog),
  but **`PostgreSQLConnection` is the only implementation**. The owner's 2026-10-14 direction applies: reuse connector
  libraries rather than rebuild, and keep a capability seam (inventory, statistics, sampling, permissions,
  credentials) that a native adapter or Egeria can fill. SQLAlchemy and sqlglot are already dependencies; TestGen's
  per-flavor builders are a second model. A preview needs only "quote an identifier" and "limit n rows" per engine, so
  it's a good first capability to put behind that seam.
