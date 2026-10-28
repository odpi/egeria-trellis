# Reply: copy pass over the credential, fit and native-process strings

**Replying to:** `ASK-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE.md` (#271)
**Read against:** remote `main` at `25532535`. #269 and #270 have merged since
the ask was written, so every string below comes from merged code, not from
the ask's quotes. #268 contains no user-facing copy.
**Date:** 2026-09-25

Proposed rewrites are marked **→**. There's a checklist of the edits at the end.

---

## 0 · Voice (Q1): mostly one voice; the drift is in vocabulary, not tone

The best strings in this batch are some of the best in the product:

- *"the rest are in this database's catalog but out of this credential's reach"*
- *"structure only"* (the schema-scope state)
- *"estimates, not exact"*
- *"not yet runnable from here"*
- *"That is an undeclared hierarchy, NOT a finding that the database has one
  flat namespace."*

Each one says what is missing and whose problem that is, which is the house rule.

The inconsistency isn't tone. It's one habit showing up in all five sources:
**internal vocabulary leaking into the reader's sentence.**

| leak | examples |
|---|---|
| design-doc and file references | *"(rule B)"*; *"in `connection.py` (REPLY-SCHEMA-AS-SUB-RESOURCE.md §5 declares…)"* |
| field names and Python `repr` quotes | *"compute_cost 'medium' is above the 'low' tier"*; *"({requirement!r})"* |
| raw enum values | *"(survey_existing)"*, *"(catalog_and_survey)"* |
| `(s)` pluralisation | *"table(s)"* appears in four strings, and N is always known when they render |

**The rule to carry forward: provenance belongs in the evidence, not in the
sentence.** A doc reference is provenance for whoever maintains the code. The
person reading needs the consequence. This isn't "provenance is decoration";
it's putting provenance in the evidence popover or the hover text, where it
already has a home.

---

## 1 · The combined proposal sentence (item 4), assembled from the code

The ask couldn't quote one, because `Proposal.sentence()` builds it from
parts. All the parts are in `prerequisite_resolver.py`, so here are two real
ones, word for word.

**Capability only** (the common, advisory case):

> `postgres_column_profile` can run, but not completely — `postgres_column_profile`
> needs `stats`; connected as analyst_ro, which is not a member of `pg_monitor`,
> so the per-table activity and row-count views (`pg_stat_user_tables`,
> `pg_stat_user_indexes`) report only what this role owns; run it anyway and
> report what was measured within this credential's scope?

**Cost and capability together:**

> answering this needs `postgres_operations` first — estimated 40s (estimated
> from its declared tier, never yet measured), `postgres_operations` compute_cost
> 'medium' is above the 'low' tier the step you asked for sits in;
> `postgres_column_profile` needs `stats`; connected as analyst_ro, which is not
> a member of `pg_monitor`, so … report only what this role owns; run it anyway
> and report what was measured within this credential's scope?

There are four defects, and they're **structural**: editing strings alone won't
fix them, because the joins produce them.

1. **Semicolons at two levels.** `why = "; ".join(...)` separates reasons with
   `"; "`, and the capability detail has its own `"; "` inside it (*"needs
   `stats`; connected as…"*). A reader can't find where one reason ends and the
   next begins. **→** the detail's inner separator becomes a colon:
   *"needs `stats`: connected as…"*.
2. **The subject is said twice.** The capability-only template opens with
   `demanding_step`, and `_capability_reason` (`:480`) puts the same step key in
   front of the detail. **→** don't prefix the step key when the reason belongs
   to the demanding step.
3. **The decision comes last, after 60–80 words.** The part the reader actually
   has to act on is the final seven words. **→** keep `sentence()` as the single
   source (its docstring's reason for that is right), but end it with a full
   stop, and add a short `question()` alongside it: *"Run it within this
   credential's scope?"* The UI puts that on the buttons; the CLI prints both.
   One source still, so nothing can drift.
4. **Field names, `repr` quotes, and an ambiguous "its".**
   **→** *"needs more compute than the step you asked for (medium; you're at
   low)"* and *"(not yet measured — estimated from declared tiers)"*. And drop
   *"(rule B)"* from the credentials reason.

---

## 2 · The credential sentences (item 1, Q4)

**Audience: write for the DBA.** These messages exist to get a grant made, and
a DBA makes grants. `SELECT` and `pg_monitor` are the exact words they'll type.
A plainer rewrite for a Steward would drop the part that can be acted on.

What helps both readers is **putting the consequence before the mechanism**.
Then a non-DBA gets what it means for them from the first clause, and the DBA
gets what to do from the rest:

- **read** — fine as it is.
- **stats** **→** *"activity and row counts cover only the tables this role
  owns — connected as {who}, which is not a member of `pg_monitor`
  (`pg_stat_user_tables`, `pg_stat_user_indexes`)"*
- **write** — *"(never exercised)"* misreads as *INSERT has never been used on
  this database*. **→** *"connected as {who}, which cannot INSERT (checked
  without writing anything)"*

### Worth one live check before more copy is built on it

As I understand PostgreSQL, `pg_stat_user_tables` and `pg_stat_user_indexes`
**are not filtered by privilege**. Both are views over `pg_class` with no ACL
predicate. `pg_monitor` / `pg_read_all_stats` restrict things like other
sessions' query text in `pg_stat_activity`, not per-table counters.

If that's right, the `stats` sentence tells a DBA to grant `pg_monitor` and
nothing changes. It could also mean the `stats` requirement is over-declared on
the steps that carry it.

I haven't verified this, and `DATABASE-STEP-CAPABILITY-AUDIT.md:26` works from
the opposite premise. So I'm flagging it, not ruling. **The check:** connect as
a role that isn't in `pg_monitor` and owns nothing, then compare
`SELECT count(*) FROM pg_stat_user_tables` with a count of that database's user
tables from `pg_class`. If they're equal, the sentence is wrong, and possibly
the declaration too.

---

## 3 · The catalog-fallback note (item 2)

> 23 table(s) visible via catalog (SELECT access: 3 of 23) as `analyst_ro`;
> row counts for the catalog-only ones are estimates, not exact.

- The headline is inside the parenthesis.
- *"catalog-only ones"* is a term coined in the same sentence. The reader has
  to work out that it means *the 20 it can't read*.

**→** *"23 tables, of which `analyst_ro` can read 3; row counts for the other 20
are planner estimates, not exact."* "Planner" is the one word of provenance
that says whose estimate it is.

---

## 4 · `preliminary_fit` (item 5, Q2): keep all five, but they aren't five verdicts

Don't collapse any of them, in the data or on screen. But the shared prefix
hides that they're **three different kinds of statement**:

| summary | what it's about | whose move is next |
|---|---|---|
| in scope — worth the full pass | the resource | yours: run the full pass |
| out of scope | the resource | nobody's, unless you dispute it |
| could not check | our measurement | ours: named inputs weren't measured |
| nothing measured to compare | our measurement | yours: run a survey |
| no requirement declared | **the lens** | yours: declare one |

The code already knows this. `db_derived.py:3173` calls *"no requirement
declared"* *"a statement about the LENS, not about the resource."* Putting
*"Preliminary fit:"* in front of it makes it read like a verdict on the
resource. This is the *whose absence is it* point from the owner's second
round (canvas page 5), showing up in a new place.

**→**

- *Preliminary fit: in scope — worth the full pass*
- *Preliminary fit: out of scope — {first of failed_inputs}*
- *Preliminary fit not checked — {n} inputs not measured: {unchecked_inputs}*
- *No preliminary fit yet — nothing about this resource has been measured;
  run a survey ›*
- *No preliminary fit — no fit requirement is declared; declare one ›*

Two notes:

- **"Out of scope" with no reason is a dismissal nobody can check.**
  `failed_inputs` is already carried in `resource_properties`, so put the first
  one in the summary. That also removes the asymmetry where "in scope" comes
  with advice and "out of scope" comes with nothing.
- **The fallback** `summaries.get(verdict, "Preliminary fit")` renders a label
  with no answer, which is a blank. **→** *"Preliminary fit: unrecognised
  verdict `{verdict}`"*.

---

## 5 · The native-processes block (item 6)

One copy problem and two visual ones, all inherited from the port:

- **A whole sentence set in small caps.** The house caps style is for short
  labels. The parenthesis, which carries the most important fact (these can't
  run), is the part that reads worst in caps. **→** label: *ALSO KNOWN TO
  EGERIA*. Below it, in normal case: *"Not runnable from here yet — listed so
  you know they exist."*
- **The process name is in the action colour.** `display_name` renders in
  `text-accent-ink`, which is what `/next` uses for things you can act on, and
  it sits right under rows whose *Run →* buttons are the same colour. A name
  you can't click or run, shown in the "click me" colour, contradicts its own
  heading. Classic got away with it because its cyan wasn't reserved for
  actions. **→** `text-ink`, still monospace.
- **Raw enum values.** `(${p.kind})` shows *"(survey_existing)"* and
  *"(catalog_and_survey)"*. **→** *"(surveys an existing catalog entry)"* and
  *"(catalogues, then surveys)"*.

---

## 6 · The schema rollup (item 3, Q3): ship it as text, under one rule

**(a) The one real sentence #266 ships** is `undeclared_envelope`'s
explanation, and it has the doc-reference leak: *"in `connection.py`
(REPLY-SCHEMA-AS-SUB-RESOURCE.md §5 declares containment per engine…)"*.
Its last sentence is excellent and should stay.

**→** *"Resource Explorer doesn't yet know how {engine} groups its tables, so
this result covers the whole database with no per-schema breakdown. That is an
undeclared hierarchy, not a finding that the database has one flat
namespace."* ("NOT" goes to lower case, to match the house voice.)

**(b) Design the breakdown now, or ship text?** Ship text, with **one rule
that costs nothing: a rollup's sentence states its count.** The envelope
already has `is_rollup` and `container_count`, so every rollup explanation
opens *"Across N schemas: …"*. That way the failure #266 was built to prevent,
*"a rollup that reads as a single measured verdict"*, can't happen in text
either.

When the breakdown is built, it doesn't need a new design. It's the house rule
that counts open what they counted: *"across 8 schemas ›"* opens `by_schema`.

And use the schema-scope words exactly as they are in the code: *readable,
structure only, partially readable, not visible, empty*. They're right as they
stand.

---

## 7 · Checklist

| # | where | edit |
|---|---|---|
| 1 | `prerequisite_resolver.py` `sentence()` | end with a full stop; add `question()` |
| 2 | `prerequisite_resolver.py:480` | no step prefix when the reason belongs to the demanding step |
| 3 | `credential_capability.py` details | inner `"; "` → `": "`; consequence first (stats); rewrite the write sentence |
| 4 | `prerequisite_resolver.py` `_exceeds` | no field names or `repr` quotes; fix *"its"* |
| 5 | `prerequisite_resolver.py:437` | drop *"(rule B)"* |
| 6 | `facts.py:913` | the catalog-fallback rewrite; real plurals |
| 7 | `db_derived.py:4908-4920` | five summaries per §4; failed input on *out of scope*; fallback |
| 8 | `app.js` `nativeProcessesSectionHtml` | caps label plus a normal-case caveat; `text-ink`; plain kind labels |
| 9 | `schema_scope.py` `undeclared_envelope` | rewrite per §6(a); every rollup explanation opens *"Across N schemas"* |
| — | a live query | the `pg_monitor` check in §2, before #3's stats rewrite |
