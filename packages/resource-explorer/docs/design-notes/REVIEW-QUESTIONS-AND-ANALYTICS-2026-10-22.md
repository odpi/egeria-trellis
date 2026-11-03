# REVIEW — The questions and the analytics that answer them (2026-10-22)

The per-question pass the owner asked for on 2026-10-14 (PLAN-OPEN-FRONTS §2.2):
a verdict for every row of `docs/dr-egeria/resource_questions.csv` — keep,
merge, retire, add — for the owner to rule on, then one CSV edit. Nothing here
changes the CSV, the catalogue or a screen; it is the design pass before the
edit. Inputs: the CSV on main 8564a1ed (97 rows: the plan's 95 plus Q-0096 and
Q-0097 added on 2026-10-21), the generated `question_catalog.yaml`, the analysis
catalogue `analysis_catalog.yaml` (39 repo, 33 database, 1 file-system entries),
the Files brief (F1–F12) and the open-data brainstorm (O1–O13). The
cross-checks were run by script, not read by eye: every analysis id a question
names against the catalogue per kind, every catalogue entry against the
questions that name it, and the stage of each question against the intent of
its analysis.

## 0. The headline

- Every analysis id a question names exists in the catalogue for the kinds the
  question applies to. No question points at a missing analysis.
- The opposite is not true: **23 catalogue entries answer no question** (§1.2).
  Four are stale duplicates; eight are infrastructure that answers nothing by
  design; eleven are real analyses the CSV should name, five of them already
  answering a question that currently says GAP or PARTIAL.
- **Two whole kinds have questions and nothing to answer them**: the generated
  `dataset_questions` (27) and `model_questions` (24) sections carry only
  direct, human and gap answers, because no dataset or model surveyor, adapter
  or analysis exists (§1.1). File systems are better only by one entry.
- Nine questions should merge into others (§2); two are launcher affordances,
  not questions about a resource, and should retire; twelve keep their place but
  name the wrong analysis or a step instead of an analysis; seven file-system
  questions and one Enrichment question are added now, four file-system
  questions later.
- Verdict counts over the 97 rows: keep 84 (of which 12 with an analysis fix and
  5 with a rewording), merge 9, retire 2, add 9 now and 4 later (§4).

## 1. Findings across the catalogue

### 1.1 The 24 "any kind" questions, and the kinds nobody has built

The CSV names six kinds: `repo`, `database`, `filesystem`, `dataset`, `model`
and `*`. 24 rows say `*`. For a repository, 3 of those 24 resolve to a real
analysis (Q-0001-style wording aside: Q-0003 ownership, Q-0030 licence,
Q-0011 overlap); for every other kind all 24 resolve to a direct field, a human
answer or a gap. That is as designed for Q-0005, Q-0008, Q-0038 and the
Enrichment rows: the answer is a registry field or a person. It is not as
designed for Q-0003 (ownership), Q-0007 (existing use), Q-0010 (similar
content), Q-0012 (licence) and Q-0057 (restrictions), where the repo answer
comes from an analysis and the database answer from nowhere. The review marks
each of those rows with what the database and file-system answer should be.

`dataset` and `model` are a different matter. The generator emits a 27-row
dataset section and a 24-row model section, every row direct/human/gap, and
`resource_types.py` admits both names. There is no surveyor, no adapter, no
analysis and no registration screen for either kind; a dataset or model cannot
be registered on /next today. The open-data brainstorm proposes a kind of its
own (`open_data`, O1–O13) that replaces what `dataset` meant in the 2026-09-20
authoring; `model` stays a future kind. **Recommendation:** remove `dataset`
and `model` from `Resource Types` on every row (11 rows carry `dataset`
explicitly, 24 carry `*`), and have the generator refuse a `*` row for a kind
with no adapter rather than emit an unanswerable section. When the open-data
kind lands, its questions are authored for it by id, as O1–O13 already are.

### 1.2 Twenty-three analyses that answer no question

| Group | Entries | Verdict |
|---|---|---|
| Stale duplicates (database) | `change_rates`, `hub_tables`, `statistics_freshness`, `database_staleness` | Retire from the catalogue. Each has a `db_`-prefixed successor that the questions name; no code references the old ids. |
| Infrastructure, not an answer | `repo_profile_refresh`, `rag_ingestion`, `website_ingestion`, `sub_resource_survey`, `code_symbol_extraction`, `manifest_parse`, `egeria_publish`, `egeria_db_survey`, `doc_source_ingestion` | Keep; declare `answers_questions: false` in the manifest field slice 1 of declared extensibility adds, so the "orphan" check stops listing them and the cost-per-question axis divides by nothing. |
| Real analyses the CSV should name | `deployment_evidence` → Q-0015; `security_summary` → Q-0028; `refresh_plan` → Q-0041 (repo); `egeria_interfaces` → Q-0022; `value_profile` → Q-0079; `schema_diff` → Q-0086; `grant_change` → Q-0085; `doc_evidence_check` → Q-0088; `semantic_suggestions` → Q-0087 (proposed, keep the status); `credential_capability` → a new Scouting question (§4) | Fix the `Answering Analysis` cell. Five of these rows say GAP or PARTIAL today for an analysis that exists: the gap is bookkeeping, not a missing capability. |

Two more things the questions name are not catalogue entries at all: the
`sql_analysis` step (Q-0071) and the `resource_reachability` probe (Q-0054,
also F4). Both should get a catalogue entry so the question stops reading
PARTIAL for want of a row.

### 1.3 Stage against intent

The catalogue gives each analysis an intent; the CSV gives each question a
stage. They disagree in five places: `preliminary_fit` is `enrichment` in the
catalogue and Q-0077 is Discovery (the question is right; fix the catalogue);
`quality_checks` is `assessment` and Q-0096/Q-0097 are Analysis (the questions
are right: proposing and running checks is analysis, the verdict on the
database is Q-0090's job); `db_resilience` is `scouting` and serves Q-0065
(Scouting) and Q-0093 (Assessment), which is fine for a two-stage analysis;
`interface_surface` is `discovery` and serves Q-0048 (Analysis) and Q-0053
(Discovery), fine for the same reason; `license_classification` is `discovery`
and Q-0030 is Assessment (fix the catalogue to `assessment` once Q-0012 merges
in, §2). **Rule for the generator:** warn, never refuse, when a question's
stage set does not include its analysis's intent; the warning names both.

### 1.4 Questions about the survey, not about the resource

Five Discovery rows ask about Resource Explorer's own state: Q-0038 (surveyed
before?), Q-0039 (which definition to run?), Q-0040 (worth investigating?),
Q-0041 (changed since last survey?), Q-0042 (is there a definition for this
type?). Q-0038, Q-0040 and Q-0041 are things a person asks about a resource
and have an answer line; keep them. Q-0039 and Q-0042 are the launcher's job:
the candidates list and its empty case already say exactly this on the Run
tab, and a question whose answer is "look at the launcher" has no headline.
**Retire both as questions**; the screens stay.

### 1.5 Enrichment is thin because it is one pattern repeated

Enrichment's seven rows are Q-0016 (cost), Q-0019 (monitoring fit), Q-0020
(security fit), Q-0023 (estate fit), Q-0025 (skills), Q-0029 (governance
fit), Q-0066 (backup, with Q-0093). Four of them are the same question with a
different noun: "does it fit our X?", all human-supplied, each promising an
Egeria pre-fill that none has. **Merge Q-0019, Q-0020 and Q-0023 into one**
estate-fit question with three clauses (headline rule, 2026-09-26): "Does it
fit our estate — the platforms we run, our monitoring, our security
controls?", answered by one Enrichment section with an Egeria pre-fill from
the dependency-support mapping (Q-0014's `dependency_support` already matches
dependencies to catalogued technology) and a person's confirmation. Q-0029
(governance) and Q-0025 (skills) stay separate because their perspectives and
their evidence differ. Enrichment then gains the proposals-from-documents
questions the Files brief and open-data note add (F11, O2), which are
Enrichment in nature: a document says what the data is, a person confirms.

### 1.6 Overlaps worth one question each

- **Overlap and stand-in** (four rows): Q-0009 "replace or extend something we
  have", Q-0010 "similar content", Q-0011 "similar subjects, grain and period;
  could one stand in", Q-0070 "copy, subset or superset by signature". One
  question at resource level for every kind — Q-0011 widened to `*`:
  "Which resources overlap with this one — the same data, a subset, a stand-in
  or a replacement — and how does this one differ?" — with Q-0070 kept at
  container level as the structural-signature evidence for databases and file
  systems. Q-0009 and Q-0010 merge into Q-0011.
- **Licence** (two rows): Q-0012 (Scouting, direct field) and Q-0030
  (Assessment, `license_classification`). One analysis, one question at two
  stages: Q-0030 becomes `Scouting/Assessment`; Q-0012 merges in.
- **Sensitive-data exposure** (two rows): Q-0085 (roles, grants, "are
  sensitive columns readable broadly") and Q-0089 (which sensitive columns
  can which roles read). Same two analyses, same gap (the join). Q-0089 merges
  into Q-0085's last clause.
- **Database documentation** (two rows): Q-0063 (comment coverage, marked
  GAP) and Q-0088 (completeness and consistency, PARTIAL via
  `schema_conventions`). `schema_conventions` already counts tables and
  columns with no comment, so Q-0063 is answered today under the wrong label.
  Q-0063 merges into Q-0088; `doc_evidence_check` supplies the consistency
  half.
- **Semi-structured columns** (two rows): Q-0074 (which columns, how much
  data) and Q-0082 (what is inside them). Same analysis
  (`nested_column_profile`), two clauses of one headline. Q-0074 merges into
  Q-0082 as its first clause; the merged row keeps `Discovery/Analysis`.
- **Interfaces** (three rows): Q-0022 (integrations), Q-0048 (APIs and code
  symbols), Q-0053 (public interfaces). Three different things once the words
  are fixed: Q-0053 is what the resource exposes (`interface_surface`), Q-0048
  narrows to the code symbols behind it (`api_structure`,
  `code_symbol_extraction`), Q-0022 becomes outbound: what it talks to
  (`egeria_interfaces`, dependency names, `deployment_evidence`). Keep all
  three, reword two.

### 1.7 Purposes

Ten purposes are used (Assess, Attest, Certify, Deploy, Explore, Learn,
Maintain, Remediate, Select, Share). The 2026-10-13 ruling made the list an
Egeria valid-value set and named **Find** and **Integrate** as new values;
neither is on any row. Find belongs on the overlap, subject, coverage and fit
rows (Q-0007, Q-0011, Q-0070, Q-0075, Q-0076, Q-0077); Integrate on the
dependency and interface rows (Q-0013, Q-0014, Q-0022, Q-0048, Q-0053,
Q-0084). The list stays suggestive, so this is a tagging pass, not a rule.

### 1.8 Status column

Two rows carry a status (Q-0046 "inadequately answered today", Q-0053 a
precondition on `repo_symbol_extraction`). The column is the right place for
"answered, but badly" and "answered only when X has run"; the table below
keeps both and adds none, because every other known limit is already in the
`Answering Analysis` text as GAP, PARTIAL or MIXED. Rule: GAP, PARTIAL and
MIXED stay in the analysis cell (the generator parses them); prose about
adequacy goes in Status.

## 2. Verdict per question

Verdict words: **keep** (as is), **keep·fix** (keep; the analysis cell names
the wrong thing), **keep·reword** (keep; the question text changes),
**merge→** (fold into the named row), **retire**. "Other kinds" says what a
`*` row answers with for a database or file system when the repo answer is an
analysis.

| Id | Stage | Kinds | Verdict | Note |
|---|---|---|---|---|
| Q-0001 | Scouting | repo | keep | |
| Q-0002 | Scouting | * | keep | Direct field; for repos name `architecture_doc_lens` (README summary) as the second source. |
| Q-0003 | Scouting | * | keep·fix | Repo: analysis. Other kinds: the Enrichment owner field plus `credential_capability`'s owner read; say so instead of "chaoss_metrics". |
| Q-0004 | Scouting | repo | keep | |
| Q-0005 | Discovery | * | keep | Direct field. The "shop for data" search is a separate front, not this row's gap. |
| Q-0006 | Analysis | repo | keep | |
| Q-0007 | Discovery | * | keep·fix | Registry query is buildable today: name a `known_use` analysis (registry groups + Egeria subscriptions). Purpose +Find. |
| Q-0008 | Discovery | * | keep | |
| Q-0009 | Discovery | * | merge→Q-0011 | "Replace or extend" is the stand-in clause of the overlap question. |
| Q-0010 | Analysis | * | merge→Q-0011 | Content similarity is the evidence, not a second question. |
| Q-0011 | Discovery | db;fs;dataset → * | keep·reword | The one overlap question for every kind (§1.6). Purpose +Find. |
| Q-0012 | Scouting | * | merge→Q-0030 | One licence analysis, one question at two stages. |
| Q-0013 | Analysis | repo | keep | Purpose +Integrate. |
| Q-0014 | Analysis/Enrichment | repo | keep | Purpose +Integrate. |
| Q-0015 | Analysis | repo | keep·fix | Name `deployment_evidence` (orphan today) and `repo_conventions`; drop the RAG GAP clause, deployment_evidence reads the docs. |
| Q-0016 | Enrichment | * | keep | |
| Q-0017 | Analysis | repo | keep | |
| Q-0018 | Analysis | repo | keep | Gap stays: RAG read of CHANGELOG/release notes. |
| Q-0019 | Enrichment | * | merge→new E1 | Estate fit (§1.5). |
| Q-0020 | Enrichment | * | merge→new E1 | Estate fit. |
| Q-0021 | Analysis | repo | keep·reword | Two clauses: "What does it promise (SLA, support terms), and what does its history show?" — `sla_content` + the trend chart. |
| Q-0022 | Analysis | repo | keep·reword | Outbound: "What does it talk to — Egeria, databases, services, message buses?" — `egeria_interfaces`, `dependency_analysis`, `deployment_evidence`. Purpose +Integrate. |
| Q-0023 | Enrichment | repo | merge→new E1 | Estate fit. |
| Q-0024 | Assessment | repo | keep | |
| Q-0025 | Enrichment | * | keep | |
| Q-0026 | Analysis | repo | keep | |
| Q-0027 | Analysis | repo | keep | |
| Q-0028 | Analysis | repo | keep·fix | Name `security_summary` (orphan; it is the composite this question wants) in front of the six it lists. |
| Q-0029 | Enrichment | * | keep | |
| Q-0030 | Assessment → Scouting/Assessment | * | keep | Absorbs Q-0012; catalogue intent of `license_classification` becomes assessment. |
| Q-0031 | Analysis | repo | keep | |
| Q-0032 | Analysis | repo | keep | |
| Q-0033 | Analysis | repo | keep | Gap stays; it is the model kind's first question when that kind exists. |
| Q-0034 | Analysis | repo | keep | |
| Q-0035 | Analysis | repo | keep | |
| Q-0036 | Analysis | repo | keep | |
| Q-0037 | Assessment | repo | keep | |
| Q-0038 | Discovery | * | keep | |
| Q-0039 | Discovery | * | retire | Launcher affordance (§1.4). |
| Q-0040 | Discovery | * | keep·reword | "Given what is known, is this worth a full survey?" — the disposition heuristic is the analysis to name once it exists. |
| Q-0041 | Discovery | * | keep·fix | Name `refresh_plan` (repo), `db_change_rates` and `schema_diff` (database), `fs_change_rates` (file system, F5); the subscription is the mechanism, not the analysis. |
| Q-0042 | Discovery | * | retire | Launcher affordance (§1.4). |
| Q-0043 | Discovery | repo | keep | |
| Q-0044 | Discovery | repo | keep | |
| Q-0045 | Discovery | repo | keep | |
| Q-0046 | Scouting | repo | keep | Status "inadequately answered today" stays until the languages pie and the file-type table are re-walked (walk 3 landed 10-21; re-check then clear). |
| Q-0047 | Analysis | repo | keep | |
| Q-0048 | Analysis | repo | keep·reword | Narrow to symbols: "What code symbols — classes, functions, endpoints — does it define, and where?" — `api_structure`, `code_symbol_extraction`. Purpose +Integrate. |
| Q-0049 | Assessment | repo | keep | |
| Q-0050 | Assessment | repo | keep | |
| Q-0051 | Assessment | repo | keep | |
| Q-0052 | Assessment | repo | keep·reword | One question, two clauses: "How much code is there, and how complex is it?" so the headline has two clauses, not two questions. |
| Q-0053 | Discovery | repo | keep | Status stays. Purpose +Integrate. |
| Q-0054 | Discovery | * | keep·fix | Name `resource_reachability` and give it a catalogue entry; this row is F4. The database half is the reach probe the Files work built, applied to database endpoints. |
| Q-0055 | Discovery | * | keep | Gap stays: DataScope from endpoint geolocation plus the Enrichment section. |
| Q-0056 | Analysis | db;fs;dataset → db;fs | keep | |
| Q-0057 | Analysis | * | keep·fix | Half of this is buildable now: zones and classifications are an Egeria read on the published asset; name a `use_restrictions` analysis and leave only the terms-interpretation clause as the gap. |
| Q-0058 | Assessment | * | keep | Gap stays; it is a composite over rows that exist (owner, description, licence, schema, freshness, contact, scope); a good first "declared" composite for slice 1. |
| Q-0059 | Assessment | * | keep·reword | Shorter: "Is there demand for this — subscriptions, requests, feedback or existing use?" |
| Q-0060 | Scouting | database | keep | |
| Q-0061 | Scouting | database | keep | |
| Q-0062 | Scouting | database | keep | Same analysis as Q-0092 at a different stage; both headlines differ, keep both. |
| Q-0063 | Scouting | database | merge→Q-0088 | Already answered by `schema_conventions` under the wrong label. |
| Q-0064 | Scouting | database | keep | Gap stays; `db_server_profile` is cheap (pg_settings, pg_extension, version window) and worth a slice. |
| Q-0065 | Scouting | database | keep | |
| Q-0066 | Enrichment | db;fs | keep | |
| Q-0067 | Discovery | database | keep | |
| Q-0068 | Discovery | database | keep | |
| Q-0069 | Discovery | db;fs | keep | File-system half arrives with F3 (`file_profiles`). |
| Q-0070 | Discovery | db;fs | keep | Container-level evidence row for Q-0011. Purpose +Find. |
| Q-0071 | Discovery | database | keep·fix | Give `sql_analysis` a catalogue entry; the step exists. |
| Q-0072 | Discovery | database | keep | |
| Q-0073 | Analysis | database | keep | |
| Q-0074 | Discovery | database | merge→Q-0082 | First clause of the nested-column headline. |
| Q-0075 | Discovery | database | keep | Purpose +Find. |
| Q-0076 | Discovery | database | keep | Purpose +Find. |
| Q-0077 | Discovery | database | keep | Catalogue intent of `preliminary_fit` becomes discovery. Purpose +Find. |
| Q-0078 | Analysis | database | keep | |
| Q-0079 | Analysis | database | keep·fix | Name `value_profile` (the catalogue id), not the step. |
| Q-0080 | Analysis | db;fs;dataset → db;fs | keep | File-system half is F7. |
| Q-0081 | Analysis | db;fs | keep | |
| Q-0082 | Analysis → Discovery/Analysis | database | keep | Absorbs Q-0074. |
| Q-0083 | Analysis | database | keep | |
| Q-0084 | Analysis | database | keep | Purpose +Integrate. |
| Q-0085 | Analysis | database | keep·fix | Absorbs Q-0089; name `grant_change` for "and what changed". |
| Q-0086 | Analysis | database | keep·fix | Name `schema_diff` for the schema-churn clause. |
| Q-0087 | Analysis | db;fs | keep | Proposed; `semantic_suggestions` is the catalogue stub, say so consistently. |
| Q-0088 | Assessment | database | keep·fix | Absorbs Q-0063; name `doc_evidence_check` for the consistency half. |
| Q-0089 | Assessment | database | merge→Q-0085 | Same analyses, same gap. |
| Q-0090 | Assessment | database | keep | |
| Q-0091 | Assessment | database | keep | |
| Q-0092 | Assessment | database | keep | |
| Q-0093 | Assessment | database | keep | |
| Q-0094 | Scouting | filesystem | keep | This row is F2. |
| Q-0095 | Discovery | filesystem | keep·fix | Name `file_profiles` when F3 lands; `filesystem_inventory` profiles tabular files today. |
| Q-0096 | Analysis | database | keep | Catalogue intent of `quality_checks` becomes analysis. |
| Q-0097 | Analysis | database | keep | |

## 3. Rows that change kind

With `dataset` and `model` withdrawn (§1.1): Q-0011, Q-0056 and Q-0080 drop
`dataset`; the 24 `*` rows apply to repo, database and filesystem only until a
new kind registers an adapter. No row is lost; the generated dataset and model
sections disappear.

## 4. Adds

| New | Stage | Kinds | Analysis | From |
|---|---|---|---|---|
| E1 estate fit (replaces Q-0019/20/23) | Enrichment | * | Enrichment section, pre-filled from `dependency_support` | §1.5 |
| S1 "What can the survey credential see, and what is it blind to — schemas, tables, system views, statistics?" | Scouting | database | `credential_capability` (orphan today; it is why 52 columns read not_permitted on 10-21) | §1.2 |
| F1 shape of the file system | Scouting | filesystem | `filesystem_inventory` (walk extensions) | Files brief, owner pick |
| F3 tabular and structured files with columns and types | Discovery | filesystem | `file_profiles` | owner pick |
| F5 changed since the last walk | Discovery | filesystem | `fs_change_rates` | owner pick |
| F7 personal or sensitive data in files | Analysis | filesystem | `data_class_match` over profiles | owner pick |
| F10 recognisable structure | Discovery | filesystem | `fs_layout_signals` | owner pick |
| F11 what the narrative files say | Discovery/Enrichment | filesystem (and repo, open data) | `folder_narratives` | owner pick |
| Later: F6 duplicates, F8 hot and cold, F9 permissions, F12 cost to survey | | filesystem | | owner: later |

F2 is Q-0094 and F4 is Q-0054; neither is added twice. O1–O13 wait for the
open-data kind decision and are authored for that kind when it exists.

## 5. Rules the generator gains (one small PR with the CSV edit)

1. An `Answering Analysis` cell may name only catalogue ids, `direct`,
   `human`, or GAP/PARTIAL/MIXED prose; a bare step name fails the build
   (Q-0079 and Q-0071 would have failed).
2. A `*` or named kind with no adapter fails the build (§1.1).
3. Stage versus catalogue intent mismatch warns with both names (§1.3).
4. A catalogue entry no question names, and that does not declare
   `answers_questions: false`, is listed by the existing orphan check as a
   warning, so §1.2 cannot regrow silently.

## 6. For the owner to rule

1. Withdraw `dataset` and `model` from the CSV until a surveyor exists (§1.1).
2. The nine merges and two retirements (§2).
3. The estate-fit merge (§1.5) and the credential-capability question (§4).
4. Find and Integrate tagging (§1.7).
5. Whether Q-0057's zones-and-classifications half and Q-0064's server
   profile get a slice now or stay gaps.

After the word: one builder does the CSV edit, regenerates the YAML and the
Dr.Egeria questions document, and the Egeria side follows the usual batch
rules (retired rows keep their ids; the Dr.Egeria terms for retired and merged
questions are updated, never deleted). The catalogue edits (four retirements,
three intent fixes, two new entries, `answers_questions` flags) ride the same
PR because the generator's new rules read both files.

## 7. Addendum 1 (2026-10-22, after the edit was built on re/questions-csv-edit-2026-10-22)

Corrections to this note, found by the builder and by a live case; the
verdicts stand except where stated.

1. **Counts.** The CSV had 97 rows. The true tally from the edit: retire 2,
   merge 9, add 8 (E1, S1, F1, F3, F5, F7, F10, F11), keep·fix 13 (Q-0051 added
   by item 3), keep·reword 7, keep unchanged the rest; 105 rows after the edit,
   94 active. §0 said "keep 84 … add 9", which summed to 95, and §4 listed 8
   adds against 9 in prose. Both were arithmetic errors in this note.
2. **The four "stale duplicates" in §1.2 are not stale.** `change_rates`,
   `hub_tables`, `statistics_freshness` and `database_staleness` are the
   generated catalogue entries of the four Analytic Library steps (the
   user-added steps of the extensibility walking skeleton), declared under
   `docs/dr-egeria/engine-host/library` with `provided_by.questions`, and
   referenced by their tests. "No code references" was a grep for the quoted
   id, which does not reach generated declarations. They stay. Ruling: the
   orphan check treats `provided_by.questions` as a question link, so a
   library step that names the question ids it answers is not an orphan.
3. **Contributors with names** (owner's live case: the chat named
   docling-core's main contributors from per-author commit rows; the question
   page's compile said the names were not listed). Q-0003 and Q-0051 are
   keep·fix: their analyses declare a list extent, "top contributors by
   commit share, with names", that the compile includes and the headline
   cites. The declaration rides declared-extensibility slice 1; the gate is the
   docling-core question showing the names on the question page. The same
   case produced the journal ruling: a journal entry is derived wholly from
   the chat turn (question, answer, cited row ids, as-of and confidence,
   scope), the person adds only the confirmation and an optional note kept
   apart, and the pane's per-turn control derives the same way; recorded on
   the journal-from-chat builder's brief, not here.
4. **Two intent fixes in §1.3 withdrawn.** `preliminary_fit` stays
   `enrichment` (designer ruling 2026-09-29, "declare a lens"); Q-0077 becomes
   `Discovery/Enrichment` instead. `license_classification` stays `discovery`
   (CLAUDE.md rule 17: it derives from the already-fetched licence field, the
   collect-versus-reason axis); the one stage-versus-intent warning on Q-0030
   is accepted and this line is its reason. `quality_checks` → `analysis`
   stands.
5. **`resource_reachability`** is catalogued for file systems only, where the
   probe exists; Q-0054 stays PARTIAL for repositories and databases.
