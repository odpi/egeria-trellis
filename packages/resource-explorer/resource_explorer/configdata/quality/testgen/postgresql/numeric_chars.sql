-- Vendored subset of flavors/postgresql/data_chars/get_schema_ddf.sql
-- (DataKitchen DataOps TestGen 5.134.0, commit 3606859c; Apache-2.0, see ../NOTICE and ../PROVENANCE.md).
--
-- Two CASE expressions, each verbatim, that decide which numeric measures the value profile takes for a column
-- (upstream's ProfilingSQL.run_column_profiling): `logical_type` (only its 'integer' arm is read, for
-- is_N_exact_sum) and `is_decimal` (for is_N_decimal and is_N_exact_sum). Both read the target catalog's
-- `information_schema.columns.numeric_scale`, which Resource Explorer's stored inventory does not keep, so the value
-- profile reads it from the target at profile time. Ported to Python in quality/general_type.py, pinned to the
-- SHA-256 of each CASE.
-- upstream lines 28-49
       CASE
           WHEN LOWER(c.data_type) IN ('bigint', 'integer', 'smallint')
               OR (c.data_type = 'numeric' AND c.numeric_scale = 0)
               THEN 'integer'
           WHEN LOWER(c.data_type) IN ('double precision', 'real', 'numeric', 'money')
               THEN 'number'
           WHEN c.data_type ILIKE '%char%' OR c.data_type IN ('text', 'uuid', 'bytea', 'xml')
               THEN 'string'
           WHEN c.data_type ILIKE 'boolean'
               OR (c.data_type = 'bit' AND c.character_maximum_length = 1)
               THEN 'boolean'
           WHEN c.data_type ILIKE 'date'
               THEN 'date'
           WHEN c.data_type ILIKE 'timestamp%'
               THEN 'timestamp'
           WHEN c.data_type ILIKE 'time with%'
               THEN 'time'
           WHEN c.data_type IN ('json', 'jsonb')
               THEN 'object'
           WHEN c.data_type = 'ARRAY'
               THEN 'array'
       END AS logical_type,
-- upstream lines 67-71
       CASE
         WHEN c.data_type = 'numeric' THEN COALESCE(numeric_scale, 1) > 0
         WHEN c.data_type = 'money' THEN TRUE
         ELSE numeric_scale > 0
       END as is_decimal,
