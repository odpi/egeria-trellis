-- Vendored subset of flavors/postgresql/profiling/project_profiling_query.sql
-- (DataKitchen DataOps TestGen 5.134.0, commit 3606859c; Apache-2.0, see ../NOTICE and ../PROVENANCE.md).
--
-- The three exact percentiles upstream takes for a number column (its `is_N_exact_pctile` block, upstream lines
-- 241-246), verbatim apart from this header. Upstream reads them in a sub-select joined to the main aggregate;
-- Resource Explorer runs them as a statement of their own per table (quality/percentiles.py), inside their own
-- time budget, so a table too big to sort does not lose its other measures. Pinned to the SHA-256 of the three
-- PERCENTILE_CONT lines.
             PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY "{COL_NAME}"::NUMERIC) AS pct_25,
             PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY "{COL_NAME}"::NUMERIC) AS pct_50,
             PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY "{COL_NAME}"::NUMERIC) AS pct_75
