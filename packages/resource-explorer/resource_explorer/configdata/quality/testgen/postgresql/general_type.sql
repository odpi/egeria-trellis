-- Vendored from flavors/postgresql/data_chars/get_schema_ddf.sql (DataKitchen DataOps TestGen 5.134.0,
-- commit 3606859c; Apache-2.0, see ../NOTICE and ../PROVENANCE.md): the general_type CASE, verbatim.
-- Resource Explorer does not run it; it is ported to Python (surveyors/database/quality/general_type.py),
-- and a test pins the port to the SHA-256 of the CASE text below, as for the selection rules.
       CASE
           WHEN c.data_type ILIKE '%char%' or c.data_type = 'text'
               THEN 'A'
           WHEN c.data_type ILIKE 'boolean'
               OR (c.data_type = 'bit' AND c.character_maximum_length = 1)
               THEN 'B'
           WHEN c.data_type ILIKE 'date'
               OR c.data_type ILIKE 'timestamp%'
               THEN 'D'
           WHEN c.data_type ILIKE 'time with%'
               THEN 'T'
           WHEN LOWER(c.data_type) IN ('bigint', 'integer', 'smallint', 'double precision', 'real', 'numeric', 'money')
               THEN 'N'
           ELSE
               'X'
       END AS general_type,
