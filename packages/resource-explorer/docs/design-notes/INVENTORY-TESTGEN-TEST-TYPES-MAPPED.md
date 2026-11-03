# INVENTORY — TestGen's out-of-the-box test types, mapped for RE and Egeria (2026-10-21)

From the design session, on the owner's instruction of 2026-10-21 ("we should inventory testgen's out of the box tests and map those"), read from the owner's TestGen clone `~/localGit/egeria-v6/dataops-testgen` (branch `trellis/evaluation-note`), `testgen/template/dbsetup_test_types/*.yaml`: **89 test types**. Each row maps to one of: ported in RE's slice 1; portable now as data (the D1 route: selection rule and measure over whole-table profile fields RE already stores); needs run history; needs hash or frequent-value measures; needs TestGen's functional-type inference; needs the sampled text-shape measures; cross-table and person-authored; custom SQL; schema drift RE already measures; or manual-only in TestGen. The Egeria column is the ODCS quality dimension Egeria's `DataQualityRule` carries (`DataContractQualityDimension`): TestGen's Validity maps to Conformity, Recency to Timeliness, the rest by name.

## Summary by family

| Family | Count | What it takes |
|---|---|---|
| A. Ported (slice 1) | 7 | ported in slice 1 |
| B0. Portable now as data | 2 | portable now as a table check (duplicate rows over all columns) |
| B1. Needs run history (compare to the prior run) | 25 | history or date-days measures: RE's staleness and change-rate steps cover the table level |
| B2. Needs hash or frequent-value measures plus history | 9 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| C. Needs functional-type inference | 20 | needs TestGen's functional-type inference (port the inference as data first) |
| D. Needs text-shape measures (sampled) | 5 | needs the text-shape measures (sampled, slice 2 of the profile) |
| E. Cross-table, person-authored | 8 | cross-table, person-authored (two tables and a key): later, needs a check editor |
| F. Custom SQL (the custom-rule backlog) | 4 | user-authored SQL: the custom-rule backlog (user-extensible rules), not portable as data |
| G. Schema drift (RE schema_diff) | 4 | metadata drift: RE's schema_diff already measures it; expose as a check reading schema_diff |
| H. Manual-only in TestGen | 5 | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |

**Reading.** Seven are ported. Family B0 is the next slice as data with no new measurement. B1 and B2 are one capability, "compare to the prior run", which RE's run history and change-rate steps already have the shape of: with it, 34 more types arrive at once. C needs TestGen's functional-type inference ported as data (its `functional_data_type` rules), the single largest unlock (20 types, Recency among them). D waits for the sampled text-shape measures (profile slice 2). E, F and H are person-authored or manual in TestGen too, so they belong to the check editor and the custom-rule backlog, not to proposals. G is already measured by RE's schema_diff and only needs exposing as a check.

## The table

| TestGen type (id) | Name | TestGen dimension | Egeria dimension | Scope · run | Family | Mapping |
|---|---|---|---|---|---|---|
| `Future_Date` (1015) | Past Dates | Timeliness | Timeliness | column · CAT | A | ported in slice 1 |
| `Min_Date` (1532) | Minimum Date | Validity | Conformity | column · CAT | A | ported in slice 1 |
| `Missing_Pct` (1022) | Percent Missing | Completeness | Completeness | column · CAT | A | ported in slice 1 |
| `Required` (1030) | Required Entry | Completeness | Completeness | column · CAT | A | ported in slice 1 |
| `Required_No_Placeholder` (1520) | Required, No Placeholder | Completeness | Completeness | column · CAT | A | ported in slice 1 |
| `Row_Ct_Pct` (1032) | Row Range | Completeness | Completeness | table · CAT | A | ported in slice 1 |
| `Unique` (1034) | Unique Values | Uniqueness | Uniqueness | column · CAT | A | ported in slice 1 |
| `Dupe_Rows` (1510) | Duplicate Rows | Uniqueness | Uniqueness | table · QUERY | B0 | portable now as a table check (duplicate rows over all columns) |
| `Future_Date_1Y` (1016) | Future Year | Timeliness | Timeliness | column · CAT | B0 | portable now as data: selection and measure use whole-table profile fields slice 1 already stores |
| `Daily_Record_Ct` (1009) | Daily Records | Completeness | Completeness | column · CAT | B1 | history or date-days measures: RE's staleness and change-rate steps cover the table level |
| `Freshness_Trend` (1515) | Freshness | Recency | Timeliness | table · QUERY | B1 | history or date-days measures: RE's staleness and change-rate steps cover the table level |
| `Match_Distinct_Ct` (1525) | Match Distinct Count | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Distinct_Date_Ct` (1534) | Match Date Count | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Epoch_Sum` (1549) | Match Epoch Sum | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Fractional_Sum` (1531) | Match Fractional Sum | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Lead_Space_Ct` (1538) | Match Leading Spaces | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Max_Date` (1533) | Match Maximum Date | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Max_Length` (1537) | Match Maximum Length | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Max_Val` (1527) | Match Maximum Value | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Min_Date` (1532) | Match Minimum Date | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Min_Length` (1536) | Match Minimum Length | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Min_Val` (1526) | Match Minimum Value | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Non_Printing_Ct` (1541) | Match Non-Printing | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Null_Ct` (1524) | Match Null Count | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Row_Ct` (1521) | Match Row Count | Consistency | Consistency | table · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Stdev` (1530) | Match Standard Deviation | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Sum` (1529) | Match Sum | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Total_Length` (1545) | Match Total Length | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_True_Ct` (1535) | Match True Count | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Upper_Case_Ct` (1540) | Match Upper Case | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Zero_Ct` (1528) | Match Zero Count | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Match_Zero_Length_Ct` (1539) | Match Empty Strings | Consistency | Consistency | column · CAT | B1 | history: compare this run's measure to the prior run's (RE keeps run history; db_change_rates is the precedent) |
| `Table_Freshness` (1511) | Table Freshness | Recency | Timeliness | table · QUERY | B1 | history or date-days measures: RE's staleness and change-rate steps cover the table level |
| `Volume_Trend` (1513) | Volume | Completeness | Completeness | table · CAT | B1 | history or date-days measures: RE's staleness and change-rate steps cover the table level |
| `Match_Distinct_Hash` (1542) | Match Distinct Hash | Consistency | Consistency | column · QUERY | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Frequent_Patterns` (1544) | Match Frequent Patterns | Consistency | Consistency | column · QUERY | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Frequent_Values` (1543) | Match Frequent Values | Consistency | Consistency | column · QUERY | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Hash_Date` (1548) | Match Date Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Hash_Num` (1547) | Match Numeric Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Hash_Text` (1546) | Match Text Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Keyed_Hash_Date` (1552) | Match Keyed Date Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Keyed_Hash_Num` (1551) | Match Keyed Numeric Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Match_Keyed_Hash_Text` (1550) | Match Keyed Text Hash | Consistency | Consistency | column · CAT | B2 | history: needs the hash or frequent-values measures in the profile, then compare to the prior run |
| `Alpha_Trunc` (1004) | Alpha Truncation | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Avg_Shift` (1005) | Average Shift | Consistency | Consistency | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Dec_Trunc` (1011) | Decimal Truncation | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Distinct_Date_Ct` (1534) | Date Count | Timeliness | Timeliness | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Distinct_Value_Ct` (1013) | Value Count | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Incr_Avg_Shift` (1017) | New Shift | Accuracy | Accuracy | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `LOV_Match` (1019) | Value Match | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Min_Val` (1526) | Minimum Value | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Monthly_Rec_Ct` (1023) | Monthly Records | Completeness | Completeness | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Outlier_Pct_Above` (1024) | Outliers Above | Accuracy | Accuracy | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Outlier_Pct_Below` (1025) | Outliers Below | Accuracy | Accuracy | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Pattern_Match` (1026) | Pattern Match | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Recency` (1028) | Recency | Timeliness | Timeliness | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Unique_Pct` (1035) | Percent Unique | Uniqueness | Uniqueness | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Valid_Month` (1042) | Valid Month (inactive) | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Valid_US_Zip` (1044) | Valid US Zip | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Valid_US_Zip3` (1045) | Valid US Zip-3 | Validity | Conformity | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Variability_Decrease` (1041) | Variability Decrease | Accuracy | Accuracy | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Variability_Increase` (1040) | Variability Increase | Accuracy | Accuracy | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Weekly_Rec_Ct` (1037) | Weekly Records | Completeness | Completeness | column · CAT | C | needs TestGen's functional-type inference (port the inference as data first) |
| `Email_Format` (1014) | Email Format | Validity | Conformity | column · CAT | D | needs the text-shape measures (sampled, slice 2 of the profile) |
| `Street_Addr_Pattern` (1033) | Street Address | Validity | Conformity | column · CAT | D | needs the text-shape measures (sampled, slice 2 of the profile) |
| `Text_Length_Range` (1519) | Text Length Range | Validity | Conformity | column · CAT | D | needs the text-shape measures (sampled, slice 2 of the profile) |
| `US_State` (1036) | US State | Validity | Conformity | column · CAT | D | needs the text-shape measures (sampled, slice 2 of the profile) |
| `Valid_Characters` (1043) | Valid Characters (inactive) | Validity | Conformity | column · CAT | D | needs the text-shape measures (sampled, slice 2 of the profile) |
| `Aggregate_Balance` (1500) | Aggregate Balance | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Aggregate_Balance_Percent` (1504) | Aggregate Balance Percent | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Aggregate_Balance_Range` (1505) | Aggregate Balance Range | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Aggregate_Minimum` (1501) | Aggregate Minimum | Accuracy | Accuracy | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Combo_Match` (1509) | Reference Match | Validity | Conformity | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Distribution_Shift` (1503) | Distribution Shift | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Timeframe_Combo_Gain` (1508) | Timeframe No Drops | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `Timeframe_Combo_Match` (1509) | Timeframe Match | Consistency | Consistency | referential · QUERY | E | cross-table, person-authored (two tables and a key): later, needs a check editor |
| `CUSTOM` (1008) | Custom Test | Accuracy | Accuracy | custom · QUERY | F | user-authored SQL: the custom-rule backlog (user-extensible rules), not portable as data |
| `Condition_Flag` (1006) | Custom Condition | Validity | Conformity | custom · CAT | F | user-authored SQL: the custom-rule backlog (user-extensible rules), not portable as data |
| `Metric_Trend` (1514) | Metric Trend | Validity | Conformity | table · CAT | F | user-authored SQL: the custom-rule backlog (user-extensible rules), not portable as data |
| `SQL_Metric` (1516) | SQL Metric | Accuracy | Accuracy | table · QUERY | F | user-authored SQL: the custom-rule backlog (user-extensible rules), not portable as data |
| `Match_Column_Ct` (1522) | Match Column Count | Consistency | Consistency | table · METADATA | G | metadata drift: RE's schema_diff already measures it; expose as a check reading schema_diff |
| `Match_Column_Type` (1523) | Match Column Type | Consistency | Consistency | column · METADATA | G | metadata drift: RE's schema_diff already measures it; expose as a check reading schema_diff |
| `Schema_Conformance` (1700) | Schema Conformance | Validity | Conformity | table · METADATA | G | metadata drift: RE's schema_diff already measures it; expose as a check reading schema_diff |
| `Schema_Drift` (1512) | Schema | null | (none) | tablegroup · METADATA | G | metadata drift: RE's schema_diff already measures it; expose as a check reading schema_diff |
| `Constant` (1007) | Constant Match | Validity | Conformity | column · CAT | H | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |
| `LOV_All` (1018) | Value Match All | Validity | Conformity | column · CAT | H | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |
| `Max_Date` (1518) | Maximum Date | Validity | Conformity | column · CAT | H | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |
| `Max_Val` (1527) | Maximum Value | Validity | Conformity | column · CAT | H | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |
| `Row_Ct` (1521) | Row Count | Completeness | Completeness | table · CAT | H | manual-only in TestGen (no selection rule): offer as a person-added check with its parameters |

## What this decides

1. **Slice 2 of the checks** = family B0 plus Dupe_Rows, as data, with no new measurement in the profile; each gets the same four-line vendoring the seven had (YAML, Postgres measure, selection as pinned Python, provenance).
2. **Slice 3** = the prior-run comparison capability once, then families B1 and B2 (B2 after the profile gains `multiset_hash`, `keyed_hash`, `frequent_values` and `frequent_patterns`).
3. **Slice 4** = the functional-type inference ported as data (TestGen's `functional_data_type` and `functional_table_type` rules), then family C, Recency first.
4. **Profile slice 2** (sampled text-shape measures, `sample_ratio` recorded) unlocks family D.
5. **The check editor and the custom-rule backlog** cover E, F and H: a person writes the check (two tables and a key, or SQL), RE runs it, Egeria holds it as a `DataQualityRule` with `checkType` sql or custom; this is the owner's "ability to define your own rules", the backlog stage.
6. **Schema drift** (G) is exposed from `schema_diff` as checks, no new measurement.

Nothing here re-decides D1 (vendor as data, no TestGen service). It says, type by type, what each needs before RE can propose and run it.

