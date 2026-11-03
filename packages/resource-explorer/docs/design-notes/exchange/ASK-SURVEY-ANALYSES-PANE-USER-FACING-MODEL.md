# The Survey & Analyses pane keeps confronting the user with RE's internal seams. What should it actually say?

**For:** the Designer session (or whoever owns copy/UX review on this project).
**From:** the coordinating session, 2026-09-25.
**Read against:** `main` at `9c442b2b`, plus `#278` (open, not yet merged —
the run-button routing fix; not relevant to the copy question below, just
named so a reader knows which PR fixed the adjacent bug).
**Replying to:** nothing directly — three pieces of live, in-session
feedback from the project owner while testing `coco_pharma`'s Survey &
Analyses pane tonight, quoted verbatim in the sections below.
**Action needed:** a real design pass on what this pane tells a user, not
another reactive string edit — the project owner's own framing, after I
proposed a fourth quick rewrite: *"the description of a survey could
indicate where it is running or if there is a choice but it isn't
something the user needs to be confronted with before they can do
anything."* That sentence is the actual ask.

## Why this is one ask and not three bug reports

Tonight's live-testing session produced four rounds of feedback on this one
pane, each time about a different string, each time landing back on the same
complaint. In order:

1. First round: *"None of the run or re-run buttons seems to do
   anything?"* — turned out to be a real bug (`runnable_and_reason()`
   checked the wrong catalog; fixed in `#276`).
2. Second round, after that fix made the buttons clickable: *"Also many of
   these questions ... the buttons work but don't actually do anything"* —
   a SECOND real bug (`runAnalysis()` posted to the repo route; fixed in
   `#278`, open).
3. Third round, once both bugs were fixed and the pane was functionally
   correct: the four-part message quoted in full below. **This is not a bug
   report.** Every string it names is now doing exactly what its code says —
   the code is right and the pane still doesn't make sense to the person
   using it.

Two real, separate bugs got found and fixed by testing this pane tonight.
That is the good news. The bad news is that fixing both did not change what
the project owner saw, because the actual problem was never the code path —
it's what the pane is willing to say to a user before they've run anything
at all. Patching individual strings a fourth time (which is what I offered,
before being asked to stop and draft this instead) would produce a fifth
round with the same shape.

## The feedback, verbatim

> 1) I don't know what Scope: all tiers - stage filter unavailable means?
> Is tiers the same as stage? Why not say stage? What do you mean by a
> filter?
>
> 2) Also known to Egeria - the user isn't usually going to care what is
> going to execute in RE vs Egeria - they want to run a survey - the
> description of a survey could indicate where it is running or if there
> is a choice but it isn't something the user needs to be confronted with
> before they can do anything.
>
> 3) No local or RE-authored survey definitions for this resource???
>
> 4) Buttons work but don't actually do anything. [this one was the `#278`
> routing bug, real and separate, and is not part of this ask]

## What each string is actually saying, underneath

Worth being precise here, because none of these three strings is wrong —
they are all accurate — and the ask is not "make them say something else
that's still this literal." It's "should the pane be saying this at all, at
this moment, to this reader."

**(1) "Scope: all tiers — stage filter unavailable · retry"**
(`app.js::loadSurveyPane`, the `data.scoping === 'full-scan'` branch)

The backend (`survey_definitions.py::_do`) tries to narrow the candidate
list to Questions catalogued for the CURRENT stage/phase (`scoping:
"questions"`). When there are no catalogued Questions that resolve to a
real Egeria Survey Definition GUID for this technology — which is exactly
`coco_pharma`'s case, since RE has authored zero local Survey Definitions
for PostgreSQL — it falls back to listing every candidate regardless of
stage (`scoping: "full-scan"`), and says so rather than silently showing a
filtered-looking list that isn't. That fallback is a deliberate, named
design decision (D2), and the banner exists so the UI doesn't lie about
being scoped when it isn't.

The project owner's specific questions are fair on their own terms:
- **"tiers" vs "stage"** — the pane's own tab strip and every other label on
  this page say "stage" (Scouting/Discovery/Assessment/...); this one banner
  alone says "tiers." Same concept, two names, no reason given for the
  switch.
- **"filter unavailable"** — technically accurate (the stage filter
  couldn't narrow anything) but doesn't say WHY, and "filter" as a noun
  isn't otherwise a concept this pane exposes to the reader.
- **The retry button can never succeed here.** `coco_pharma` genuinely has
  zero RE-authored Survey Definitions for PostgreSQL — that's a permanent
  catalog fact, not a transient failure. Clicking retry re-fires the exact
  same query and gets the exact same answer, forever. A retry affordance on
  a permanent state teaches the reader to distrust every retry button in the
  app.

**(2) "Also known to Egeria" / "Not runnable from here yet — listed so you
know they exist."** (`app.js::nativeProcessesSectionHtml`)

This section exists to answer a real, previously-misdiagnosed gap: earlier
in this project, "no survey definitions" was wrongly read as "Egeria has no
surveys for this technology at all," when real Egeria-native survey
processes existed the whole time and RE's UI just wasn't rendering them
(`#269`). The fix was to render them — correctly, as data — but the
presentation frames the ENTIRE section around an implementation seam
(RE-authored vs. Egeria-native) that the project owner is saying, directly,
a user doesn't need to be handed as their first fact. Quoting the ask
again because it's the clearest sentence in this doc: *"the description of
a survey could indicate where it is running or if there is a choice but it
isn't something the user needs to be confronted with before they can do
anything."*

**(3) "No local or RE-authored survey definitions for this resource"**
(`app.js::loadSurveyPane`, the `!all.length` branch — my own rewrite from
earlier tonight, landed in `#277`)

This was a direct, good-faith fix for a real prior complaint (the original
"No survey definitions for this resource" read as "Egeria has nothing,"
which was false). The fix corrected the FACT but kept the same shape as
(2): it leads with RE's internal authorship vocabulary
("local"/"RE-authored") as the first thing a user reads. The project
owner's three question marks are the same complaint as (1) and (2), just
arriving a second time on a string I had *just* rewritten — which is the
signal that the fix pattern itself (find the wrong noun, swap it for a
righter one) is the wrong tool for this problem.

## What the three have in common

All three sentences are built to be correct about RE's own architecture
first, and useful to a user second (if at all). "Tiers" vs "stage" is an
internal-naming inconsistency. "Also known to Egeria" and "RE-authored" are
both naming WHERE something executes or WHO catalogued it, as the pane's
opening statement, before the user has asked either question. None of the
three tells the reader what they actually came to this pane to learn: *can
I run a survey on this database, and if so, how* — the "how" (RE-local vs.
Egeria-native vs. either) being exactly the kind of detail the project
owner is saying belongs in a description a curious reader can open, not a
banner every reader has to get past.

## What we'd want your view on

1. **What should this pane lead with, when the honest answer is "nothing
   RE-authored exists, but Egeria has real processes for this
   technology"?** Candidate shape, NOT a ruling — is the right opening move
   something like naming the runnable thing first ("Survey PostgreSQL
   Database — run it") and folding the RE-vs-Egeria distinction into that
   row's own description/detail, the way (2) above already half-suggests?
   Or does the pane need a real "this is what would run" preview before any
   run affordance, independent of which engine executes it?
2. **Does "Scope"/"tiers"/"stage" need one vocabulary across the whole
   pane, or does the SCOPE CONCEPT ITSELF need to go** — i.e., is the
   full-scan-vs-scoped distinction a fact worth surfacing to a user at all,
   given it can't be acted on when scoping is permanently unavailable (no
   catalogued Questions will ever exist for a technology RE hasn't authored
   Survey Definitions for)? If it should stay, what's the plain-language
   version, and should "retry" only appear when a retry could actually
   change the answer?
3. **Where does "which engine ran this" belong, if not the banner?** A
   result's own record (`last_run_via`, already shown per-row: "·via
   analysis"/"·via survey") already answers this AFTER a run. Does the
   BEFORE-a-run version belong in the same "what it does" popover
   (`openAnalysisPopover`) that already exists per analysis row, rather than
   a standing section above every row regardless of which analysis the
   reader is looking at?
4. **Scope of this ask** — this pane (`Survey & analyses`, database and
   filesystem resources) only, or does the same "implementation seam in the
   opening sentence" pattern need checking against Discovery/Assessment's
   own copy too? Not proposing that expansion here, flagging it as a
   question rather than assuming the answer.

## Reply

A `REPLY-`/`RULING-`-prefixed doc, same convention as prior rounds, or
conversational if that's faster. Concrete rewrites are welcome, but the
main thing we need is the shape decision in Q1 — the rewrites in `#276`/
`#277`/`#278`'s wake all shared the mistake of rewriting one string at a
time inside the existing shape, and the project owner's own framing says
the shape is what's wrong.
