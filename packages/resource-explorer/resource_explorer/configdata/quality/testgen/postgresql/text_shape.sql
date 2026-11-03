-- Vendored subset of flavors/postgresql/profiling/project_profiling_query.sql
-- (DataKitchen DataOps TestGen 5.134.0, commit 3606859c; Apache-2.0, see ../NOTICE and ../PROVENANCE.md).
--
-- The sampled text-shape measures of a TEXT column (port step 7): upstream's `sampleable_rows` / `pattern_rows`
-- CTEs (with and without sampling), `sampleable_measures`, and the pattern CTEs, each block verbatim from the
-- upstream line range named in its `-- upstream lines` header (the header comment is RE's). Resource Explorer
-- (quality/text_shape.py) assembles ONE statement per text column from these blocks:
--   WITH <sampling or no-sampling CTEs>, <sampleable_measures>, <pattern CTEs>
--   SELECT * FROM sampleable_measures CROSS JOIN pattern_measures
-- Upstream's final SELECT also joins `whole_table_measures`; RE reads those measures in its batched whole-table scan
-- instead (profile_measures.sql, flag is_A_text), so that CTE is not part of this statement. Placeholders:
-- {COL_NAME}, {DATA_SCHEMA}, {DATA_TABLE}, {SAMPLE_PERCENT_CALC} (a number RE computes, never user input),
-- {MAX_PATTERN_LENGTH} (25, upstream's `ProfilingSQL.max_pattern_length`). `<%IS_NUM;..%>` and `<%IS_DATE;..%>` are
-- upstream's templated functions (templated_functions.yaml). Each block is pinned by its entry in
-- text_shape.TEXT_SHAPE_SHA256 and checked against the clone by scripts/sync_quality_catalog.py.

-- upstream lines 26-33 (sampling_rows)
, sampleable_rows AS (
  SELECT "{COL_NAME}"
    FROM "{DATA_SCHEMA}"."{DATA_TABLE}" TABLESAMPLE BERNOULLI ({SAMPLE_PERCENT_CALC}) REPEATABLE (64)
)
, pattern_rows AS (
  SELECT "{COL_NAME}" AS v
    FROM "{DATA_SCHEMA}"."{DATA_TABLE}" TABLESAMPLE BERNOULLI ({SAMPLE_PERCENT_CALC}) REPEATABLE (64)
)

-- upstream lines 36-43 (no_sampling_rows)
, sampleable_rows AS (
  SELECT "{COL_NAME}"
    FROM "{DATA_SCHEMA}"."{DATA_TABLE}"
)
, pattern_rows AS (
  SELECT "{COL_NAME}" AS v
    FROM "{DATA_SCHEMA}"."{DATA_TABLE}"
)

-- upstream lines 46-100 (sampleable_measures)
, sampleable_measures AS (
  SELECT
    COUNT("{COL_NAME}") AS sample_value_ct,
    SUM(CASE
          WHEN "{COL_NAME}" = '' THEN 1
                               ELSE 0
        END) AS zero_length_ct,
    SUM( CASE
           WHEN ("{COL_NAME}" LIKE ' %' AND LTRIM("{COL_NAME}") <> '') THEN 1
                                                ELSE 0
         END ) AS lead_space_ct,
    SUM( CASE WHEN "{COL_NAME}" ILIKE '"%"' OR "{COL_NAME}" ILIKE '''%''' THEN 1 ELSE 0 END ) AS quoted_value_ct,
    SUM( CASE WHEN "{COL_NAME}" ~ '[0-9]' THEN 1 ELSE 0 END ) AS includes_digit_ct,
    SUM( CASE
           WHEN LOWER("{COL_NAME}") SIMILAR TO '(.{1,}|-{1,}|\?{1,}|\s{1,}|0{2,}|9{2,}|x{2,}|z{2,})' THEN 1
           WHEN LOWER("{COL_NAME}") IN ('blank','error','missing','tbd',
                                      'n/a','#na','none','null','unknown')           THEN 1
           WHEN LOWER("{COL_NAME}") IN ('(blank)','(error)','(missing)','(tbd)',
                                      '(n/a)','(#na)','(none)','(null)','(unknown)') THEN 1
           WHEN LOWER("{COL_NAME}") IN ('[blank]','[error]','[missing]','[tbd]',
                                      '[n/a]','[#na]','[none]','[null]','[unknown]') THEN 1
                                                                                     ELSE 0
         END ) AS filled_value_ct,
    SUM(CASE WHEN "{COL_NAME}" COLLATE "C" = UPPER("{COL_NAME}") COLLATE "C" AND "{COL_NAME}" COLLATE "C" <> LOWER("{COL_NAME}") COLLATE "C" THEN 1 ELSE 0 END) AS upper_case_ct,
    SUM(CASE WHEN "{COL_NAME}" COLLATE "C" = LOWER("{COL_NAME}") COLLATE "C" AND "{COL_NAME}" COLLATE "C" <> UPPER("{COL_NAME}") COLLATE "C" THEN 1 ELSE 0 END) AS lower_case_ct,
    SUM(CASE WHEN "{COL_NAME}" COLLATE "C" = UPPER("{COL_NAME}") COLLATE "C" AND "{COL_NAME}" COLLATE "C" = LOWER("{COL_NAME}") COLLATE "C" THEN 1 ELSE 0 END) AS non_alpha_ct,
    COUNT( CASE WHEN TRANSLATE("{COL_NAME}", E'\u00a0\u2009\u200b\u200c\u200d\u200e\u200f\u202f\u3000\ufeff', 'XXXXXXXXXX') <> "{COL_NAME}" THEN 1 END) AS non_printing_ct,
    SUM(<%IS_NUM;LEFT("{COL_NAME}", 31)%>) AS numeric_ct,
    SUM(<%IS_DATE;LEFT("{COL_NAME}", 26)%>) AS date_ct,
    CASE
      WHEN SUM( CASE WHEN "{COL_NAME}" ~ '^[0-9]{1,5}[a-zA-Z]?\s\w{1,5}\.?\s?\w*\s?\w*\s[a-zA-Z]{1,6}\.?\s?[0-9]{0,5}[A-Z]{0,1}$'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.8 THEN 'STREET_ADDR'
      WHEN SUM(CASE WHEN "{COL_NAME}" IN ('AL','AK','AS','AZ','AR','CA','CO','CT','DE','DC','FM','FL','GA','GU','HI','ID','IL','IN','IA','KS','KY','LA','ME','MH','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','MP','OH','OK','OR','PW','PA','PR','RI','SC','SD','TN','TX','UT','VT','VI','VA','WA','WV','WI','WY','AE','AP','AA')
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.9 THEN 'STATE_USA'
      WHEN SUM( CASE WHEN "{COL_NAME}" ~ E'^(\\+1|1)?[ .-]?(\\([2-9][0-9]{2}\\)|[2-9][0-9]{2})[ .-]?[2-9][0-9]{2}[ .-]?[0-9]{4}$'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.8 THEN 'PHONE_USA'
      WHEN SUM( CASE WHEN "{COL_NAME}" ~ '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.9 THEN 'EMAIL'
      WHEN SUM( CASE WHEN TRANSLATE("{COL_NAME}",'012345678','999999999') IN ('99999', '999999999', '99999-9999')
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.9 THEN 'ZIP_USA'
      WHEN SUM( CASE WHEN "{COL_NAME}" ~ '^[\w\s\-]+(?<!\s)\.(txt|csv|tsv|dat|doc|pdf|xlsx)$'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.9 THEN 'FILE_NAME'
      WHEN SUM( CASE WHEN "{COL_NAME}" SIMILAR TO '^([0-9]{4}[- ]?){3}[0-9]{4}$'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.8 THEN 'CREDIT_CARD'
      WHEN SUM( CASE WHEN "{COL_NAME}" ~ '^([^,|\t]{1,20}[,|\t]){2,}[^,|\t]{0,20}([,|\t]{0,1}[^,|\t]{0,20})*$'
                      AND "{COL_NAME}" !~ '\s(and|but|or|yet)\s'
           THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.8 THEN 'DELIMITED_DATA'
      WHEN SUM ( CASE WHEN "{COL_NAME}" SIMILAR TO '^[0-8][0-9]{2}-[0-9]{2}-[0-9]{4}$'
                       AND LEFT("{COL_NAME}", 3) NOT BETWEEN '734' AND '749'
                       AND LEFT("{COL_NAME}", 3) <> '666' THEN 1 END)::FLOAT/COUNT("{COL_NAME}")::FLOAT > 0.9 THEN 'SSN'
    END AS std_pattern_match,
    SUM(SIGN(LENGTH(TRIM("{COL_NAME}")) - LENGTH(REGEXP_REPLACE(TRIM("{COL_NAME}"), ' ', '', 'g')))::BIGINT) AS embedded_space_ct,
    AVG(LENGTH(TRIM("{COL_NAME}")) - LENGTH(REGEXP_REPLACE(TRIM("{COL_NAME}"), ' ', '', 'g'))::FLOAT) AS avg_embedded_spaces
    FROM sampleable_rows
)

-- upstream lines 101-142 (pattern_measures)
, pattern_counts AS (
  SELECT pattern,
         COUNT(*) AS pattern_ct,
         COUNT(value_length) AS value_ct,
         MAX(value_length) AS max_length
    FROM ( SELECT CASE
                    WHEN v > ' '
                      THEN REGEXP_REPLACE(REGEXP_REPLACE( REGEXP_REPLACE(
                             v, '[a-z]', 'a', 'g'),
                                         '[A-Z]', 'A', 'g'),
                                         '[0-9]', 'N', 'g')
                  END AS pattern,
                  LENGTH(v) AS value_length
             FROM pattern_rows ) patterned
   GROUP BY pattern
)
, ranked_patterns AS (
  SELECT pattern,
         pattern_ct,
         value_ct,
         CASE
           WHEN pattern IS NOT NULL AND MAX(max_length) OVER () BETWEEN 3 AND {MAX_PATTERN_LENGTH}
             THEN ROW_NUMBER() OVER (ORDER BY CASE WHEN pattern IS NULL THEN 1 ELSE 0 END, pattern_ct DESC, pattern COLLATE "C")
         END AS pattern_rank
    FROM pattern_counts
)
, pattern_measures AS (
  SELECT
    MAX(CASE WHEN pattern_rank = 1 THEN pattern END) AS pattern_0,
    MAX(CASE WHEN pattern_rank = 1 THEN pattern_ct END) AS pattern_ct_0,
    MAX(CASE WHEN pattern_rank = 2 THEN pattern END) AS pattern_1,
    MAX(CASE WHEN pattern_rank = 2 THEN pattern_ct END) AS pattern_ct_1,
    MAX(CASE WHEN pattern_rank = 3 THEN pattern END) AS pattern_2,
    MAX(CASE WHEN pattern_rank = 3 THEN pattern_ct END) AS pattern_ct_2,
    MAX(CASE WHEN pattern_rank = 4 THEN pattern END) AS pattern_3,
    MAX(CASE WHEN pattern_rank = 4 THEN pattern_ct END) AS pattern_ct_3,
    MAX(CASE WHEN pattern_rank = 5 THEN pattern END) AS pattern_4,
    MAX(CASE WHEN pattern_rank = 5 THEN pattern_ct END) AS pattern_ct_4,
    COUNT(pattern) AS distinct_pattern_ct,
    SUM(value_ct) AS sample_pattern_value_ct
    FROM ranked_patterns
)
