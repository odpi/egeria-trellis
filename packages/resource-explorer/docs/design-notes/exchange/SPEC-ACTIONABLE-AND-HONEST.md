# Actionable, and honest about which population

**Replying to:** the owner's six points and three annotated screens, 2026-09-15
**Read against:** `origin/main` at `93be502` (after `#96`)
**Drawn at:** `design_handoff_list_answers/` page five — `Actionable`, `WhatWeFound`

Two of the six are questions with short answers. The other four are one habit
and one missing half, and both are worth more than a layout change.

---

## 1 · Did the dashboard split? **Yes — it shipped.**

`SUB_TABS` now reads `Find repos · Questions · Survey & analyses · By analysis ·
Disposition`, and screen 2 is `By analysis` live with the Health & Maturity and
Code Structure sections under it. Screen 3 is `Survey & analyses` with the fetch
counts on the rows — *7 steps · 4 fetch*, *3 steps · none fetch*, *9 steps · 4
fetch*. Point 2 of the last round is answered in the shipped UI.

## 2 · Why does *Find repos* appear at every stage? **Because I got a rule wrong.**

`SUB_TABS` is one module-level list and `search` is the only entry with no
`built: true`, so it renders greyed with its dashed underline on all nine stages.
That is my own ruling working exactly as written — *identical sub-tab order on
every stage; grey out what a stage lacks, never remove* — and the ruling is wrong
here.

**Uniformity is for things that are per-stage.** Finding and importing candidate
repositories is not: it is corpus-level work that happens before any stage
applies, and it is the same action on Scouting as on Curate. A corpus-level
action greyed across nine stages is nine wrong promises, and the rule was written
to stop *stage-scoped* affordances vanishing, not to mint a tenth copy of a thing
that has no stage.

**So `Find repos` leaves the sub-tab strip** and goes where adding a repository
actually belongs — at the corpus level, beside the sidebar's `Repos · DBs · FS`
switcher, which is the control that already scopes the whole left column. The
strip drops to four, all of them genuinely per-stage.

## 3 · Making it actionable — the missing half is *whose absence it is*

`Actionable.dc.html`. This is the important one.

The honesty rules produced a surface that is scrupulous about what it cannot say
and silent about what to do. *"responsiveness — not established. Not measurable
from what is collected — issue and pull-request response times are not
gathered"* is a **true sentence about the analysis**, put in front of someone who
wanted to know about the repository. There is nothing they can do with it,
because the work it implies is ours.

The rule stays — absence and failure are states, never blanks. What was missing
is the other half: **whose absence.** Two audiences, split:

- **A finding about the repository** belongs on the page, and says where it goes.
- **A finding about the analysis** belongs in a gaps list the project owns. On the
  resource it collapses to one line:

  > 3 of 11 community measures cannot be computed from what is collected —
  > *what is missing, and why ›*. Not a finding about this repository.

  The last clause is what makes it readable: it tells the person this is not
  their problem. Behind the link, the same three sentences plus the thing the
  wall never had — **an RFA against the analysis**, so the gap lands on a work
  list belonging to whoever maintains it.

And every finding about the repository **names one of three destinations**:

| finding | destination |
|---|---|
| *elephant factor — sole.* One contributor wrote 93% of 413 commits. | **a judgement** · *is that a risk here? ›* |
| *published spec — no.* No specification file found. | **a task** · *raise an RFA ›* |
| *project maturity — established.* 3.9 years old. | **context** · nothing to do |
| *symbol count* — 8,756 against 8,362, two analyses disagree. | **ours** · in the gaps list |

The third is as important as the others. A finding that implies no action is not
a failure, and pretending otherwise is how every dashboard becomes a chore list
nobody reads.

**A judgement is not a task.** *One contributor wrote 93%* has no correct action
— whether it matters depends on what the repository is for — so it is an
**Enrichment field**, and the link goes to the judgement it informs. The
judgement then carries the measurement it was made against, so when the number
moves it says *review — evidence moved*. That machinery shipped in `#34`; this is
the road to it, which nothing currently builds.

**And two analyses disagreeing is never the reader's problem.** It stays visible
— a number nobody can reconcile must not be quietly picked between — but marked
**ours**, with both analyses named. Which also puts a number on the owner's
instinct that the analytics need work: **the gaps list *is* that number**,
accumulated by the interface rather than asserted in a meeting.

## 4, 5, 6 · One habit: a row written as though it came from one analysis

`WhatWeFound.dc.html`.

### 5 · The two sentences that cannot both be true

> 24 components recovered — 4 of 87 components reviewed (4 accepted); 0 of 12
> blueprints reviewed · `architecture_doc_lens` ran and found nothing. · 87
> candidate components, none documented
> *This analysis ran and found nothing — a measured zero, not a gap in coverage.*
> `architecture_recovery, architecture_doc_lens, architecture_summary` · run 2d ago

**Both are true, and the row makes them contradict.** The provenance names
**three** analyses. The 24 is `architecture_recovery`'s. The nothing is
`architecture_doc_lens`'s — and the answer line already says so, attributed, in
the middle of itself. Then the caveat says it again as *"**This** analysis"*,
unattributed, directly under a number from a different one.

The rule, and it is the one `#88` just applied to rationale-as-caveat: **a caveat
on a multi-analysis row names its analysis, or it does not render.** *This* has no
referent where there are three.

### 4 · 24 recovered from what?

Three populations, unnamed and mixed: **24** is the projection at depth 1, **87**
is every recovered component, **12** is the candidate blueprints. All legitimate,
none interchangeable. So **every count carries its population** — *at depth 1*,
*recovered*, *candidate* — and no sentence holds two populations without the word
*of* between them:

> 24 components at depth 1, of 87 recovered · 4 reviewed, 4 accepted · 0 of 12
> blueprints reviewed

**Why we think each is what it is:** a component's type came from a detector with
a confidence and a `proposed_by` list the row never shows. It should — *a console
entry point*, *a compose service*, *two detectors agree*. Those are the words
that let someone accept or reject without opening the evidence table, and the
branch tree is where they belong.

**On completeness:** the honest fix is not always to show all of them, it is to
say what is shown and what is not — *24 of 87 shown at this depth · 63 nested
below · all 87 ›*. The tree carries this at the branch; the answer row does not.

### 6 · What a public interface is — and the rung that is missing

`interface_surface` already has a ladder; its own comment says *"openapi.yaml is
'specified'; a fastapi dependency is only 'implied'"*. Two rungs, and the truth
is almost always on the one between them:

| rung | what it means | evidence |
|---|---|---|
| **declared** | a contract committed in the repo, or an entry point the packaging declares | `openapi.yaml`, `.proto`, `[project.scripts]` |
| **implemented** — *missing* | the interface exists in the code, whether or not anything documents it | FastAPI route decorators, click commands, the CLI's entry function |
| **implied** | something in the dependency list suggests an interface and nothing confirms it | depends on `click`, depends on `fastapi` |

**And the current sentence is wrong on this very repository, with the fact that
refutes it already in the codebase four times over.** The screen says *"cli —
implied. Depends on click — suggests a cli, but nothing in the repo specifies
one."* Meanwhile `[project.scripts]` is read by `repo_role.py`, by
`distribution_parser.py` — whose docstring calls it *"the CLI entry point as a
fact"* — by an architecture-recovery rule, and by `deployment_evidence.py`, which
shipped yesterday in `#94` and calls a console entry point the **strongest
signal**. `interface_surface` consults none of them.

That is the vendored-rule defect again, one analytic over: **a fact known by one
walk and not read by another, producing a confident sentence the repository
itself contradicts.** The walk guard exists because of the last one.

What it should say:

> **cli — declared.** `pyegeria = "pyegeria.cli:main"` in `[project.scripts]`.
> **http api — implemented.** 31 FastAPI routes across 4 modules; no committed
> specification. *the 31 routes ›*
> 2 interfaces declared or implemented · 0 with a published contract — a task,
> not a judgement.

And the visualisation follows from the ladder rather than needing its own design:
interfaces group by rung, the count of each is the headline, and *implied* stops
being a claim about the repository and becomes what it is — a list of things
worth looking for properly.

---

## Whose work this is

Four of the six need analytic changes, and the owner is right that this is not
the user's job. My part is specifying what the interface must be able to say,
which then says what the analytics must produce:

- **the measuring session:** `interface_surface` reads the declared entry points
  and gains the *implemented* rung; every finding carries a `destination`
  (judgement / task / context / ours); the gaps list as a real collection, with
  the disagreements and the not-measurables in it; `proposed_by` and the type
  evidence on the component rows.
- **the `/next` session:** the caveat attribution rule, the population clauses,
  the destination column, the one-line collapse with its *not a finding about
  this repository* clause, the interface ladder's grouping, and `Find repos`
  leaving the strip.
- **mine, still:** the component branch tree with the type evidence on it, the
  wire diagram, and `detect`/`coupling` — which point 4 has now touched from a
  second direction, since "why do we think it is this kind" is unanswerable while
  two perspectives propose different sets and one verdict lands on both.
