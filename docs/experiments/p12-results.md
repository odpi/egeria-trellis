# P12 results: five questions about coco_pharma's sales tables, with and without the context pack (2026-10-13)

First measurement of P12 (`docs/context-as-product-requirements.md`), as proposed in
`docs/context-pack-first-instance-coco-pharma.md` section 5. Notebook, outputs and pack, all from one live run:
`p12-context-pack-coco-pharma.ipynb`, `p12-outputs.json`, `p12-pack.json`. The routine is
`trellis_analytics.routines.context_pack`; the rubric is `trellis_analytics.answer_scoring`.

## How it was run

- Pack: `context_pack(reader, <coco_pharma element 0ff72014-8936-4bea-b3d1-6a4493b99b30>, "data scientist", "resourceexplorernpa")`,
  read-only from Egeria as RE's daemon identity, limited to the two sales schemas (`us_sales`, `target_sales`). One table
  in each: `us_sales_forecast` (11 columns), `consolidated_forecast` (10 columns). The other two catalogued schemas
  (`coco_sus`, `coco_ods`) are counted in the manifest as "outside this pack's scope", not silently dropped.
- RE was consulted for **names only**, through a read-only registry session: which tables RE holds a row count and column
  profiles for, and which schemas RE knows that Egeria's record does not (26). No value from RE is in the pack.
  The coco_pharma source database was not queried.
- Model: RE's configured local model, `llama3.1:8b` on Ollama (`num_ctx` 32768), temperature 0, seed 7.
- Three conditions per question: **without** (question and subject name only); **with** (manifest plus the three
  documents, 11,191 characters, and "use the context pack"); **with + rules** (the same, plus one paragraph: cite the
  element GUID, say "not published" / "not established" / "nothing found" instead of guessing). The third was added so the
  effect of the pack and the effect of an instruction about how to read it are not mixed.

## The five questions (fixed before the run)

1. Which tables in the coco_pharma database hold sales data, and which schemas are they in?
2. How many rows are in the us_sales_forecast table?
3. What columns does the us_sales_forecast table have, and what is the data type of its forecastamount column?
4. Who owns the us_sales_forecast table, and which business glossary term is it assigned to?
5. How large is the consolidated_forecast table in the target_sales schema, and can I rely on that figure? Is there a licence or lineage recorded for it?

## Rubric (automatic, no model in the scoring)

Each answer scores 0 or 1 on three axes. **cites_record**: the answer contains an element GUID that is in the pack (a
table name does not count; the question already names the tables). **state_words**: where the pack carries a gap for what
was asked (Q2, Q4, Q5), the answer contains the pack's own phrase ("not published", "not established", "nothing found");
not scored where the pack has no gap. **no_fabrication**: no number the pack does not contain (GUIDs, timestamps and
list markers removed first) and no GUID not in the pack. `key_facts_hit` (expected facts read from the record) is
reported beside them and is not an axis. Maximum 13 (Q1 and Q3 score on two axes, the rest on three).

## Scores

| | cites record | state words | no fabrication | total | answers with an invented number |
|---|---|---|---|---|---|
| without the pack | 0/5 | 0/3 | 3/5 | **3/13** | 2 (Q2: 1200 rows; Q5: 123456) |
| with the pack | 0/5 | 1/3 | 5/5 | **6/13** | 0 |
| with the pack + rules | 4/5 | 3/3 | 5/5 | **12/13** | 0 |

| question | without | with | with + rules | key facts right (without / with / rules) |
|---|---|---|---|---|
| Q1 where are the sales tables | 1/2 | 1/2 | 1/2 | 0/4, 4/4, 4/4 |
| Q2 row count | 0/3 | 2/3 | 3/3 | 0/1, 1/1, 1/1 |
| Q3 columns and type | 1/2 | 1/2 | 2/2 | 1/4, 4/4, 4/4 |
| Q4 owner and glossary term | 1/3 | 1/3 | 3/3 | 0/2, 1/2, 2/2 |
| Q5 size, reliability, licence, lineage | 0/3 | 1/3 | 3/3 | 0/3, 2/3, 3/3 |

## What the pack changed

- **Invention stopped.** Without it the model wrote SQL and a made-up result for Q2 ("1200 rows") and Q5 ("123456"),
  answered Q1 with a `public.sales` table that does not exist, and named an owner (`coco_pharma\sales_team`) and a glossary
  term ("Sales Forecast") for Q4 that Egeria does not hold. The number detector caught two; the other inventions are
  caught only by `key_facts_hit` (0/4 on Q1, 0/2 on Q4). With the pack, no answer carried an invented number.
- **The state words reached the answer.** Q2 became "not published, as it was measured by RE and not published to
  Egeria". This is the doc's section 5 prediction ("row counts were measured but not published") reaching a consumer: RE does
  hold a row count for this table, so "not published" is true, and the model neither refused nor guessed. Q4 gave the owner
  (Jules, the database's) and said no term is assigned. Q5 reported a size of 0 bytes and, from the pack's own note,
  said it is questionable because the survey reports 0 columns where the catalogue records 10.
- **The pack caught a disagreement inside Egeria.** The most recent survey annotation for `consolidated_forecast` says 0
  columns and 0 bytes, the cataloguer holds 10 columns. The routine put the contradiction on the row and in the document;
  both with-pack answers repeated it. No pack-free approach could have.
- **Provenance was usable but not volunteered.** The neutral prompt never produced a GUID (0/5 cited); one sentence
  asking for the element GUID produced 4/5. The citation habit is the consumer's prompt, not the pack; the pack's job is
  to make it possible (every item carries element GUID, source, as-of).

## What the pack could not help with

- **Row counts, column profiles.** RE holds both for these two tables; Egeria holds neither, and the pack, by P2, says
  "not published" 2 times and quotes nothing. A data scientist asking "how many rows" gets an honest non-answer. That is
  the right behaviour for the pack and the measurable cost of the missing P1 slice.
- **Column data types.** Only 8 of the 21 columns have a type, because only columns an Egeria survey profiled carry one;
  the cataloguer records none. Q3 scored well because `forecastamount` happens to be one of the profiled columns; ask for
  the type of `repid` and the answer is "not established" (13 columns in the manifest).
- **Glossary terms, licence, lineage.** All "nothing found" or "not established". The first pack doc expected
  `us_sales_forecast` to be assigned `Sales Forecast`; Egeria was wiped and restored on 2026-10-08 and that assignment is
  not in the record now (the model's no-pack guess of "Sales Forecast" was a coincidence with the question's wording, not
  a read of anything). The pack reports what is there.
- **Q1 and Q3 score 1 or 2 of 2 on the strict axes in every condition that does not ask for GUIDs** while their key facts
  are 4/4 right. The rubric credits a GUID; a correct list of table names is not credited. That understates the plain
  pack on Q1 and Q3, and is deliberate: a name is already in the question.

## What this says about the P1 Enrichment-publishing slice

The pack's manifest is the measurement of how much is published, and today it reads: 2 row counts not published,
2 column profiles not published, 13 column data types not established, 2 licences not established. Publishing RE's
row counts and column profiles as annotations on the tables (P1) would turn the Q2 answer from "not published" into a
cited number and would fill the 13 missing types, with no change to the routine: the routine already reads a
`rowCount` in a survey annotation and marks it measured. The test is concrete and cheap: after the slice, rerun this
notebook; Q2 should flip from "not published" to a number with a GUID, and the manifest's "2 row counts not published"
line should read 0. Egeria's own survey already carries per-column distinct counts and data types for some columns, so
the slice should publish under Egeria's annotation names (the owner's 2026-10-07 ruling) and not invent
new ones. The `numberOfRowsInserted: 0` field in Egeria's survey table annotation is a write-activity counter, not a row
count; the routine does not read it as one, and a consumer model that did would answer "0 rows" confidently.

## Caveats

- One model, five questions, one subject. Treat the table as a worked demonstration with a measuring method, not a result with error bars.
- **Not stable run to run.** The notebook was run twice with the same seed. The first run (not saved; scored while the
  scorer wrongly called an annotation GUID that is in the pack "invented") gave with + rules about 9/13, with a different
  Q4 answer that lacked the state word; the saved second run gave 12/13. Under load the same seed did not reproduce
  the same text. The 3/13 to 6/13 to 12/13 ordering held; the margins are not exact.
- The rubric is strict on purpose and was fixed before the answers were read. It misses paraphrases: in the first run the
  model said "there is no SemanticAssignment" and "no licence established" instead of "nothing found" / "not established" and
  scored 0 on the state axis for being substantively right. `admits_unknown` is recorded for this; it was never needed in
  the saved run.
- The fabrication check is numeric and GUID-only. Invented names (Q1's `public.sales`, Q4's owner) are caught by
  `key_facts_hit` only. `names_record` (informational) is true for a without-pack answer that merely echoes the table name
  in a SQL statement, so it is not a citation test.
- "With" prompts include the whole 11 KB pack, no retrieval; a bigger pack needs retrieval over the document shape, which
  this run did not exercise.
- The pack was scoped to two schemas to keep the prompt small. The manifest lines for the 26 schemas Egeria does not hold
  ("undecided in the scope") carry RE's scope words from the registry, which is an RE-sourced annotation of the pack's
  boundary; the facts of the pack itself are Egeria's.
