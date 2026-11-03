-- Vendored subset of profiling/functional_datatype.sql
-- (DataKitchen DataOps TestGen 5.134.0, commit 3606859c; Apache-2.0, see ../NOTICE and ../PROVENANCE.md).
--
-- The upstream file is 629 lines of ordered UPDATE statements over TestGen's profile_results. Kept here,
-- verbatim and in upstream order, is every statement that can give a DATE column (general_type 'D') a
-- functional_data_type before the date CASE, the date CASE itself, and every later statement that can
-- overwrite a date column's label without the `functional_data_type IS NULL` guard (so could turn
-- 'Future Date' or 'Schedule Date' into something else). Resource Explorer ports them to Python
-- (surveyors/database/quality/functional_type.py), pinned to this text; it runs none of this SQL.
-- Statements left out, and why, are listed in docs/design-notes/DESIGN-QUALITY-Q6-RECENCY.md.
-- Each block below starts with the upstream line range it came from.

-- upstream lines 18-25
UPDATE profile_results
SET functional_data_type =
        CASE WHEN record_ct = 0 then 'TBD (Not enough data)'
             WHEN record_ct > 0 and ((value_ct < 5 OR zero_length_ct / nullif(value_ct, 0)::FLOAT = 1))
                    THEN 'TBD (Not enough data)'
            ELSE functional_data_type
        END
WHERE profile_run_id = :PROFILE_RUN_ID;

-- upstream lines 28-36
UPDATE profile_results
SET functional_data_type =
        CASE WHEN distinct_value_ct = 1
                AND (((value_ct - coalesce(filled_value_ct, 0) - coalesce(zero_length_ct, 0))::float/record_ct::float) * 100.00 ) > 75
                    -- this tells us how much actual values we have filled in; threshold -> if there is only 1 value and it's 75% of the records -> then it's a constant
            THEN 'Constant'
        ELSE functional_data_type END
WHERE profile_run_id = :PROFILE_RUN_ID
  AND functional_data_type IS NULL;

-- upstream lines 39-43
UPDATE profile_results
SET functional_data_type = 'ID-SK'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND functional_data_type IS NULL
  AND column_name ILIKE :PROFILE_SK_COLUMN_MASK;

-- upstream lines 45-49
UPDATE profile_results
SET functional_data_type = 'ID'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND functional_data_type IS NULL
  AND column_name ILIKE :PROFILE_ID_COLUMN_MASK;

-- upstream lines 72-116
UPDATE profile_results
SET functional_data_type =
      CASE
            WHEN before_20yr_date_ct / NULLIF(value_ct::FLOAT, 0) * 100 >= 75 THEN 'Historical Date'
            WHEN future_date_ct / NULLIF(value_ct::FLOAT, 0) * 100 >= 95 THEN 'Future Date'
            WHEN future_date_ct / NULLIF(value_ct::FLOAT, 0) * 100 >= 50 THEN 'Schedule Date'
            WHEN before_5yr_date_ct / NULLIF(value_ct::FLOAT, 0) * 100 BETWEEN 0 AND 20 -- mostly within 5 yrs
             AND future_date_ct / NULLIF(value_ct::FLOAT, 0) * 100 BETWEEN 0 AND 10  -- up to 10% in future
                THEN
                CASE
                       WHEN value_ct > 100 AND distinct_value_ct > 20 -- at least one biz month
                        -- Date appears at least 2 days per week
                        AND date_days_present >= 2 * (datediff('WEEK', min_date, max_date) + 1)
                        THEN 'Transactional Date'
                    WHEN value_ct > 20 AND distinct_value_ct > 8 -- at least one biz month
                        AND (date_weeks_present =
                                 NULLIF(DATEDIFF('WEEK', min_date, max_date), 0)::FLOAT + 1 -- 1 day a week
                        OR
                         date_weeks_present >= 2 * (DATEDIFF('MONTH', min_date, max_date) + 1) -- 2 weeks a month
                        OR ROUND(distinct_value_ct * (1 - before_5yr_date_ct / NULLIF(value_ct::FLOAT, 0))) >=
                           2 *
                           (DATEDIFF('MONTH', (run_date::DATE - 365)::TIMESTAMP, max_date) + 1) ) -- 2 weeks a month from the last 5 years to current
                        THEN 'Transactional Date (Wk)'
                    WHEN value_ct > 20 AND distinct_value_ct > 3 -- at least 4 biz months
                       AND (date_months_present =
                         NULLIF(DATEDIFF('MONTH', min_date, max_date), 0)::FLOAT + 1 -- every month
                        OR
                         date_months_present >= 5 * (DATEDIFF('YEAR', min_date, max_date) + 1) -- 5 months a year
                        OR ROUND(distinct_value_ct * (1 - before_5yr_date_ct / NULLIF(value_ct::FLOAT, 0))) >=
                           5 * (DATEDIFF('YEAR', (run_date::DATE - 365*5)::TIMESTAMP, max_date) + 1) ) -- 5 months a year from the last 5 years to current
                        THEN 'Transactional Date (Mo)'
                    WHEN value_ct > 10 AND distinct_value_ct > 1 -- at least 2 biz quarters
                       AND distinct_value_ct = DATEDIFF('QUARTER', min_date, max_date) + 1 -- every quarter
                        THEN 'Transactional Date (Qtr)'
                    ELSE 'Date (TBD)'
                END
            WHEN column_type = 'date'
                THEN 'Date Stamp'
            WHEN column_type = 'timestamp'
                THEN 'DateTime Stamp'
            ELSE functional_data_type
      END
WHERE profile_run_id = :PROFILE_RUN_ID
  AND functional_data_type IS NULL
  AND (general_type = 'D' OR (value_ct = date_ct + zero_length_ct AND value_ct > 0));

-- upstream lines 136-142
-- Process Timestamp
UPDATE profile_results
SET functional_data_type = 'Process ' || functional_data_type
WHERE profile_run_id = :PROFILE_RUN_ID
  AND general_type IN ('A', 'D')
  AND ( column_name ~ '^(last_?|system_?|)(add|create|insert|inrt|update|updt|mod|modif|modf|del|delete|refresh)(.{0,3}d?_?(time|tm|date|day|dt|stamp|timestamp|datestamp))$'
   OR   column_name ~ '^(last_?|)(change|chg|update|updt|mod|modify|modf|modified|refresh|refreshed)$' );

-- upstream lines 228-262
-- Period Overrides -- regardless of prior functional_data_type

UPDATE profile_results
SET functional_data_type = 'Period Month'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND (min_date = DATE_TRUNC('month', min_date)::DATE
         AND max_date = DATE_TRUNC('month', max_date)::DATE
        OR
        min_date = (DATE_TRUNC('month', min_date) + INTERVAL '1 month' - INTERVAL '1 day')::DATE
         AND max_date = (DATE_TRUNC('month', max_date) + INTERVAL '1 month' - INTERVAL '1 day')::DATE
       )
   AND date_months_present = date_days_present
   AND date_months_present >= 12;

UPDATE profile_results
SET functional_data_type = 'Period Week'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND ( EXTRACT(DOW FROM min_date) IN (0, 1, 5, 6)
          AND EXTRACT(DOW FROM min_date) = EXTRACT(DOW FROM max_date)
       )
   AND date_months_present < date_days_present
   AND date_weeks_present = date_days_present
   AND date_weeks_present > 25;

UPDATE profile_results
SET functional_data_type =
      CASE
         WHEN LOWER(column_name) = 'month'  THEN 'Period Month'
         WHEN LOWER(column_name) = 'week'   THEN 'Period Week'
         WHEN LOWER(column_name) = 'period'
          AND NOT functional_data_type ILIKE 'Period%' THEN 'Period'
         ELSE functional_data_type
      END
WHERE profile_run_id = :PROFILE_RUN_ID
  AND LOWER(column_name) IN ('period', 'month', 'week');

-- upstream lines 356-366
-- Process User:  tracks data process
UPDATE profile_results
   SET functional_data_type = 'Process User'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND column_name ~ '^(last_?|)(create|update|modif|delete|refresh)(.*?(by|id|name|nm|user|usr))$';

-- System User:  SW system
UPDATE profile_results
   SET functional_data_type = 'System User'
WHERE profile_run_id = :PROFILE_RUN_ID
  AND column_name ~ '(user|usr)_?(name|nm)?$';

