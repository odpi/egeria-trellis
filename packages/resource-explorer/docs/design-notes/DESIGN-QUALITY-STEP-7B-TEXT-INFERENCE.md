# DESIGN — Quality port step 7b: the text branches of the functional-type inference (2026-10-29)

Follows `DESIGN-QUALITY-STEP-7-SAMPLED-TEXT-SHAPE.md` §6 (which named this step) and builds on step 7's tip `90e724aa`.
Code: `quality/functional_infer.py`. No new column, no DDL, no vendored file.

## 1. The owner's answers on step 7 (recorded here so step 7's branch stays as gated)

**Decision (project owner, 2026-10-29):** on step 7 §9, "1 - yes 2 - agree 3 - yes 4 - no". That is: (1) the nineteen columns of
`ddl-value-profile-step7.md` are approved, applied at the next 8813 restart after merge (not by the builder); (2) keep TestGen's
sampling constants; (3) do step 7b next, before step 8 (this step; step 8 is parked on `claude/re-quality-8-hashes`); (4) no
eighth type for step 7, seven it is.

## 2. What is ported

Step 6 ported the whole of `functional_datatype.sql` in upstream order but treated every text-shape measure as unknown, so a text
column was never labelled. The measures are taken now, so each text condition is evaluated:

| Upstream statement (lines) | Reads | Result |
|---|---|---|
| Character Date, Character Timestamp (119-134) | `distinct_pattern_ct`, `min_text`, `max_text`, first of `frequent_patterns` | Date Stamp, DateTime Stamp |
| Period Year, Quarter, Year-Mon, Month, Mon-NN, Week, DOW (146-226) | `min_text`, `max_text`, `avg_length`, `avg_embedded_spaces`, `max_length`, first pattern | the Period labels (their number arms were already ported) |
| Zip, Email, Phone, Address, State (276-298) | `std_pattern_match`, `numeric_ct`, `zero_length_ct`, `max_length` | the five labels, in upstream's CASE order |
| City, by position (301-317) and by shape (520-536) | neighbours' State and Zip; `includes_digit_ct`, `numeric_ct`, `date_ct`, `avg_length`, `avg_embedded_spaces` | City (see departure 2) |
| Person Full / Given / Last Name (320-347) | `avg_length`, `avg_embedded_spaces` | the three labels |
| Boolean (374-395) | `min_text`, `max_text` | Boolean |
| Flag (456-467) | `min_length`, `max_length`, `frequent_patterns` | Flag |

Find-absence-as-answer is unchanged from step 6, and now has three cases:

- **Read, and false:** a text column whose shape was read and whose measure is SQL NULL (no standard pattern; an empty string that
  `NULLIF` removed) makes a positive WHERE not true: the statement does not fire. A column that is not text has these measures NULL
  by construction: the same.
- **Never read:** a text column that has values but no `sample_value_ct` (profiled before step 7, or its read did not finish) has
  these conditions **unknown**, and the reason says "its sampled text shape was not measured". It is never a "no".
- **Cannot be evaluated:** see §3.

A label that is now decided ends a column's set, so selection rules that ask "is this column a Zip" answer a real **no** for a
column decided to be an Email, instead of `not_established`.

## 3. What still reads "type not inferred"

The Code / Category / ID / Attribute / Description / Measurement Text statement (412-448) is guarded by
`LOWER(datatype_suggestion) SIMILAR TO '(%varchar%)'`. `datatype_suggestion` is computed from TestGen's catalog `column_type`
text (`varchar(20)`, `text`, ...) and, for the date, number and timestamp-in-text suggestions, from `frequent_values`
(port step 8b). RE has neither, and guessing which text columns TestGen would call "varchar" would be a guess dressed as a
measurement. So that statement stays unknown for every text column that reaches it, and a plain text column still says:

> type not inferred · a text column; needs datatype_suggestion, which the profile does not take (needs frequent_values (port step
> 8b) and TestGen's catalog column_type text)

Consequence, stated: Pattern_Match, LOV_Match, Distinct_Value_Ct, Alpha_Trunc and Unique_Pct on **plain text** (a free-text,
code or category column) remain `not_established`, as before. What changes is every text column the earlier statements settle:
Zip, Email, Phone, Address, State, names, dates in text, periods in text, Booleans. For those, Valid_US_Zip, Valid_US_Zip3,
Valid_Month and the Monthly/Weekly checks now propose or answer a real no.

## 4. Departures

1. **Collation.** `min_text >= '1900'` is a text comparison; Postgres uses the database collation, Python the code point. For
   digit-led strings they agree. A non-ASCII collation could differ on letters; not seen in the tested names.
2. **City is never concluded, only excluded.** Both City statements look at the neighbouring column's label, decided in the same
   statement for another column. RE evaluates the part it can exactly: a column whose name has no "city" (form 1) or whose shape
   fails (form 2), or whose neighbour cannot be State, is a real no; otherwise City stays in the column's set as possible. A
   neighbour "may be State" only if its `std_pattern_match` is `STATE_USA`.
3. **Phone is now applied to every general type**, as upstream does (a number column named `phone_no` with `max_length` 7 to 11
   is Phone). Step 6 had restricted it to text, which upstream does not.
4. **Positions.** City needs the column's ordinal position; the profile run and `propose` pass the stored inventory's
   `ordinal_position` into the inference (`position` on the inference rows). A row with no position cannot be a City by
   position, and is not excluded either.

## 5. Tests

`tests/test_quality_step_7b_text_inference.py` (33): each label from hand-made rows, the negative case of each, the never-read
column, a column with no values, City by position and by shape, the plain-text reason, the stored label from a profile run, the
selection rules over the new sets. Two step 6 tests updated for expected reasons. sqlite only; nothing reaches :5442 or Egeria.

## 6. Questions for the project owner

1. **Step 8b** (frequent values, `datatype_suggestion`, and with them the plain-text Code / Category / ID / Attribute /
   Description labels) is what finishes the text inference. Recommendation: build it with the rest of step 8 (hashes, the
   frequent-values child table, the three QUERY types), after step 8's parked work is reviewed.
2. **Confirm departure 3** (Phone on every general type, as upstream). Recommendation: yes.
