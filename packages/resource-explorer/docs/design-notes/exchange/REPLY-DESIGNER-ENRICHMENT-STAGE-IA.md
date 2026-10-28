# Reply — Designer: the Enrichment stage's sub-tabs

**Replying to:** `ASK-DESIGNER-ENRICHMENT-STAGE-IA.md` (2026-09-29)
**Read against:** `main` at `3d078072`; PR #348 (`pr/348`, not merged);
`next/stages/enrichment.js`; `next/glyphs.js`; the analysis and question
catalogs; classic's Enrichment (`index.html:2147`)
**Drawing:** `wireframes/EnrichmentContext.dc.html` (canvas page 14): the Context
tab for a database, one row in its four states, and "Survey & analyses" on this
stage
**Date:** 2026-09-29

---

## The answers, short

1. **Enrichment opens on a new first tab, *Context*: everything a person has
   supplied, each row signed and dated, and each saying what it feeds.**
   Questions and Disposition stay as they are. *Survey & analyses* and *By
   analysis* stay too, but explained: no analysis runs at this stage, so their
   job here is to say which analyses in other stages read what's supplied.
   There's no separate "What it changed" tab. Each row says what it feeds, on
   the row itself.
2. **A survey may propose a value only for an observation, and only when it
   measured the same fact the field asks for.** A judgement is never proposed.
   A proposal, a confirmation, an override and a later disagreement read as
   drawn on page 14. The later disagreement uses the same "⚠ review" wording
   the judgements already use, and only the person can clear it.
3. **Documentation sources go at the end of Context, under "Where it's
   documented", labeled *sources, not answers*.** The probe result and the
   ingest result are two facts on one line, each with its own glyph from
   `glyphs.js`.
4. **Per type:** a table in §4. Repositories get sources too, with the declared
   homepage offered as a first source.

§0 has corrections to the ask's premises, and they change the answer to Q2.

---

## 0 · What's actually there

### 0.1 · Nothing proposes a value today, and the pane's rule says not to

The ask describes the Context form as having *"a 'proposed from <analysis>'
starting point where a machine half exists"*, with *"proposed from
db_resilience: no backup tool detected"* as the example. **Nothing in `main` or
PR #348 does this.** "Proposed from" appears in neither. `enrichment.js` states
the opposite, as its own settled rule:

> *Evidence sits in the rail as MATERIAL, never as proposals. No "apply
> suggestion". The one exception is a fact a survey already established — the
> licence — which is offered to confirm, with its source, into the observations
> half.*

So Q2 isn't about how to show a pattern that exists. It's about whether to
extend one narrow exception. §2 answers that using the pane's own split between
judgements and observations.

### 0.2 · No analysis in the catalog runs at the Enrichment stage

No entry in any `*_analyses` section has `intent: enrichment`. The analyses the
ask lists as consuming human input are catalogued for other stages:
`preliminary_fit` and `dependency_support` for Discovery, `db_resilience` for
Scouting. `semantic_suggestions` isn't built. **So "Survey & analyses" and "By
analysis" are empty on Enrichment by construction.** That's why they feel wrong
to the owner.

### 0.3 · A person's input lives in three stores with three different rules

| what | store | who | when | evidence-moved flag | how it's entered |
|---|---|---|---|---|---|
| judgements, observations | `state.enrichment` via `saveEnrichmentField` | ✓ | ✓ | judgements only | an inline control |
| answers to the catalog's human questions | `state.contextAnswers` via `saveQuestionAnswer` | **✗** | ✓ | **✗** | **`window.prompt()`** |
| classic's fixed context fields | `ContextData` (`org_owner`, `responsible_steward`, …) | — | — | — | classic's `/` form only |

Documentation sources (PR #348) would be a fourth.

**The second row already breaks this ask's own constraint** that *"a
human-supplied answer … carries who and when."* The Questions tab shows
*"answered 2d ago · change"*, with no author. The answer is typed into a browser
popup: one line, no author recorded, and the popup blocks everything else on
the page.

This reply doesn't require merging the three stores; that's engineering's call.
It does require **one row anatomy for all of them, with who and when on every
row.** That's the prerequisite for Q1.

### 0.4 · Classic already had this tab

Classic's Enrichment strip is **📝 Context · 📊 Survey · 📈 Dashboard · ❓ Questions ·
🧭 Disposition**, and it opens on Context (`_enrichmentSubTab = 'context'`).
`/next` moved the form inside the Questions tab. The owner's instinct returns it
to where classic had it.

---

## 1 · The sub-tabs

**Context · Questions · Survey & analyses · By analysis · Disposition**, plus
Schema Inventory for databases. **The stage opens on Context.**

### Context: everything supplied, and what each feeds

In order, keeping `enrichment.js`'s doctrine (judgements first and given the
room; observations a step down):

1. **What we judge**: sensitivity, criticality, intended use, actual use,
   owner. Perishable.
2. **What you're looking for**: the lens (see below).
3. **What we record**: environment, retention, licence, last backup. Durable.
4. **What only you can answer**: the catalog's human questions, moved here
   from `window.prompt`, with the same row anatomy.
5. **Where it's documented**: sources (§3).

**Every row ends with a "feeds →" line**: *"feeds → Curate (Confidentiality) ·
Assessment: 'is it safe to use'"*. That line is the ask's proposed *What it
changed* tab, placed where it's useful. A separate tab of effects would hold one
or two items today (`preliminary_fit` with a lens, and `dependency_support`).
It would also make the reader match rows across two tabs to connect an input to
its effect. Cause and effect belong on one line.

**The lens belongs to the investigation, not to the resource.** `db_derived.py`
already treats the lens as a stored, versioned `DataLens`, *"a comparison across
resources"* in the working set. Declaring it separately on each resource would
turn one requirement into N copies that drift apart. So Context shows it
**read-only**, as *"no lens declared for A sample db investigation · declare it
on the investigation ›"*. A quiet secondary link offers *"try one on this
resource only"*, since `lens_source: ad_hoc` already exists for that case.

### Survey & analyses, and By analysis: present, explained, and doing a job

My Sub-Resources ruling applies here: **a tab doesn't disappear on one stage.**
That teaches the reader that the strip changes length from stage to stage, and
then the next missing tab reads as normal.

But these two tabs needn't be empty. **On Enrichment, Survey & analyses becomes
the map of which analyses read what's supplied here:** *"No analysis runs at
the Enrichment stage. What you supply here is read by: preliminary_fit ›
(Discovery), db_resilience › (Scouting) …"*, each row showing that analysis's
current state. By analysis says the same in one line and links there. Page 14
draws it.

If the owner would rather these two tabs go, that's a change to the rule for
every stage, and worth deciding once, not as an Enrichment exception. A
stage-specific **leading** tab is already fine: Schema Inventory is a
type-specific extra tab, and Context follows the same pattern.

### Questions and Disposition

Unchanged. The anatomy of a human question's row on Questions is fixed, but two
things inside it change: the answer shows **who** as well as when, and *"Answer
this →"* opens that row in Context instead of `window.prompt`. One editor, one
record.

---

## 2 · The starting-point pattern: when a survey may propose

The pane's own split decides it:

- **A judgement is never proposed.** Sensitivity, criticality, use, owner, and
  every "does it fit" question are opinions. A survey's findings are material
  in the rail, and the existing *"⚠ review — evidence moved: X"* flags the
  judgement when that material changes. No change here.
- **An observation may be proposed, only when the survey measured the same fact
  the field asks for.** Licence qualifies today: `license_classification` reads
  the licence. **Last backup doesn't:** `db_resilience` measures WAL archiving,
  and archiving isn't a tested restore. So the row shows *"material, not an
  answer: WAL archiving last succeeded 2h ago"* and proposes nothing. A proposal
  of a different fact is exactly the "correct number, wrong label" failure this
  project keeps finding.

**The four states, in one row** (page 14, part two):

| state | value | the line under it |
|---|---|---|
| proposed | shown **muted** | *from survey (license_classification, 2d ago) · not confirmed · confirm · enter a different value*. The question stays unanswered: no ✓ on Questions |
| confirmed | ink | *confirmed by dan · 2d ago · source: license_classification* |
| overridden | ink | *dan · 2d ago · the survey said: Apache License 2.0*. The measured value stays visible, muted. An override never erases what was measured |
| survey now disagrees | ink, unchanged | *⚠ review — the survey now says MIT (license_classification, 3h ago) · keep yours · take MIT* |

The last state **uses the judgements' existing ⚠ review wording**, so there's
one vocabulary for "your word, and something has changed since". It clears only
when the person acts. A re-run or the passage of time never clears it, and a
person's value is never replaced automatically.

On databases, **owner** stays a judgement ("who answers for it?"). Its row
gains a measured observation line, *"administered by `postgres` — the
database's own owner role"*, from `pg_database.datdba`. That's the admin half
from the catalog-consistency ruling, which is still dropped at registration
today.

---

## 3 · Documentation sources

**Place:** the last section of Context, **"Where it's documented · sources, not
answers."** Sources aren't answers. They're where answers can be checked, and
later the input to ingestion. So they get their own section and never sit
among the answer rows.

**Two facts, two glyphs, on one line.** Whether the link works *now* and whether
we captured its content *as of* some date are separate facts, and one status
must never stand for both:

| probe | glyph | wording |
|---|---|---|
| reachable | ✓ | *reachable · HTTP 200 · probed 1h ago* |
| needs sign-in | ◐ | *reachable behind a sign-in · HTTP 302 → login*. Same family as a credential scope: reachable within a limit |
| not found | ✕ | *not found · HTTP 404 — the link is broken, not the site* |
| blocked | ? | *couldn't tell — blocked on our side*. The problem is ours, not the source's |
| not probed yet | ○ | *not probed yet* |

The ingest state follows on the same line: *○ not ingested yet · ✓ 42 pages,
1.3 MB, as of 2d ago · ✕ ingest failed*. When access blocks ingestion, it says
*"can't be ingested without access"*.

**Three changes to PR #348 as shipped:**

- **Its `PROBE_GLYPH` is a fifth glyph table**, with `●` for reachable, which
  isn't in the set. G1 retired the other tables. Route these through
  `glyphs.js`.
- **"remove" is colored `text-state-warn`.** It's an action, so it should use
  the action styling like "re-check" does. A state color on a control makes it
  read as a warning.
- **Show who added each source and when.** The registry already stores
  `added_by` and `added_at`, and the row doesn't render them. Every other row
  in Context is signed, so this one should read *"added by dan · 2d ago"* too.

The row also carries a **third** fact: whether the source has been published
to Egeria as an external reference (`egeria_state`). Keep it on its own line,
as #348 does. Link, content and publication are three separate things, each
with its own time.

---

## 4 · Per type, only where it must differ

| | database | filesystem | repository |
|---|---|---|---|
| judgements | as listed | as listed | as listed |
| owner's measured line | *administered by* `datdba` | — (nothing reads POSIX ownership yet) | — |
| licence | observation, no proposal | observation, no proposal | **proposed from `license_classification`** (today's exception) |
| last backup | observation; `db_resilience` as material | observation | — |
| dependency support | — | — | a section of its own: the per-dependency list, one row each, each a judgement |
| sources | yes | yes | **yes**; PR #348 shows the block only for database and filesystem. Offer the declared homepage (already derived for the header) as a candidate, since it's the same fact: a URL the project itself declares |

---

## 5 · One color ruling, answering G1's flag

`G1-GLYPH-CONSOLIDATION-IMPLEMENTED.md` §1.3 left one color question for
review: `partial` is `text-accent-ink` in `app.js`'s `STATE_TONE` but
`text-state-warn` in `glyphs.js`. Behind it is a broader problem.
**`text-accent-ink` is the color that means "you can click this"**, and
`glyphs.js` also gives it to three states you can't click:

| state | now | should be |
|---|---|---|
| `partial` (`STATE_TONE`, app.js) | accent-ink | `text-state-warn`, as in `glyphs.js` |
| `human`, `needs-lens` (⚠) | accent-ink | `text-ink`: an instruction to the reader, at full strength, neither a warning nor a finding |
| `running` (◔) | accent-ink | `text-ink-muted` |

The rule: **the accent colors controls, never states.** I broke it myself on
canvas page 12, where the glyph table draws ⚠ in accent. This supersedes that
drawing.

---

## 6 · Order

1. **One row anatomy with who and when** for the human-question answers, and
   Context's editor in place of `window.prompt` (§0.3). Without this, the new
   tab would put an author-less row beside signed ones.
2. **The strip**: Context first and the default; Survey & analyses as the map;
   By analysis explained.
3. **#348's block, re-seated** as §3, with its glyphs through `glyphs.js`.
4. **The observation states** from §2 (licence first, as today's one case), and
   the lens row once the investigation can hold a lens.
5. **The color fixes** in §5: three small edits.

---

**Addendum 2026-09-29, design session:** analyses whose prerequisite is a
human input do run at Enrichment; the tab lists them as unlocked; see E1.
