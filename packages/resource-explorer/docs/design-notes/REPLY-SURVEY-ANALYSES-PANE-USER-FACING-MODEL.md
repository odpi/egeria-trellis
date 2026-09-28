# Reply: the Survey & Analyses pane leads with what the user can run, and nothing else

**Replying to:** `ASK-SURVEY-ANALYSES-PANE-USER-FACING-MODEL.md` (#279).
**From:** the design session, 2026-09-25. This is an information-architecture
ruling; visual treatment is the designer's round 2.
**Read against:** `main` after #278.
**The project owner's framing, which this reply implements:** *"the
description of a survey could indicate where it is running or if there is a
choice but it isn't something the user needs to be confronted with before
they can do anything."*

---

## 0 · The diagnosis is right: the shape is wrong, not the strings

Three accurate sentences all failed for one reason: each leads with a fact
about RE's construction — how the list was built (scoped / full-scan), who
authored the survey (RE / Egeria), where it will execute — before the fact
the reader came for: *can I run a survey on this database, and how.* A
fourth rewrite inside that shape would fail the same way. The rule that
replaces it:

> **Every pane's opening sentence leads with the user's verb — run, answer,
> declare, compare. Provenance, engine and authorship are secondary details
> on the item they describe. Absence states say what is absent and whether
> anything can change it.**

## 1 · Q1 — what the pane leads with: one list of runnable things

One list, one row shape, ordered *runnable now* first. Authorship and engine
are never section boundaries; a survey is a survey.

| Row element | Content | Source |
|---|---|---|
| Name | in the user's vocabulary: "Survey PostgreSQL database", "Profile columns", "Check data classes" | catalog `display_name` |
| What it answers | one line: the questions this survey resolves, from the catalog's `analysis_ids` inverse | question catalog |
| What it needs | **only when not runnable**: the gate reason — stage / cost tier (design §17.1) or credential capability (credentials reply §3), e.g. "needs read on 23 of 26 tables — pick a broader connection or run partially" | prerequisite resolver, capability probe |
| Engine tag | small, secondary: *runs in Egeria* · *runs here* · *either* | step `executes_at` / rule B |
| Action | **Run** — or, when there is a real choice, Run with the rule-B sentence ("Egeria can reach this; local ≈ 4 s, native ≈ 40 s") | launcher |
| Detail | the existing per-row popover: full description, engine, cost, last run, what it produces | `openAnalysisPopover` |

No preview step before Run; the popover is the preview for the curious. The
"Also known to Egeria" section dissolves into rows like any other, with
the engine tag saying *runs in Egeria*; a native process RE cannot yet
trigger is a row whose gate reason says so ("not runnable from Resource
Explorer yet") — the same shape as any other blocked row, not a separate
class of thing.

### 1.1 · The result summary line (project owner, 2026-09-25, on approving §1)

*"There also needs to be a result summary line to indicate that the survey
was just run and produced these results."* It goes **on the row**, as the
existing second line changing state — not a fourth line and not the
popover. The rule: **the row shows the most recent truth about that
survey.**

| State | Line 2 reads | Action |
|---|---|---|
| never run | *answers:* which columns hold personal data · how big is it | **Run** |
| ran, found something | *Ran 4 d ago:* 6 schemas, 56 tables, 427 columns · answered 3 questions | **Re-run** |
| ran, found nothing | *Ran 4 d ago:* no views or functions found | **Re-run** |
| ran within credential scope | *Ran 4 d ago:* 56 tables — as `egeria_user`, 3 of 8 schemas readable | **Re-run** · *pick connection* |
| ran, failed | *Last run failed 2 h ago:* Egeria could not open the asset (no secret) | **Re-run** · *fix connection* |
| just ran in this session | *Just now:* … (line updates live) | **Re-run** |

The summary text comes from each analysis's existing results reader /
`result_materializer` summary — no new summariser — and the three
absence states are distinct on purpose (`find-absence-as-answer`). The
line links to the full result view; the popover keeps the full result and
the engine sentence. It is on the row rather than in the popover because
the popover is hidden, and "this ran, and here is what it found" is the
one fact a returning user needs before deciding whether to run again —
the same "open the thing a user opens" rule that caught four invisible
extensions in September.

## 2 · Q2 — the scope concept leaves the pane; the stage scoping does not

**Correction, 2026-09-25 (`REVIEW-SURVEY-PANE-285.md`).** The first
implementation of this section dropped the stage filter, and the wording
below gave it cover. To be unambiguous: **the list is scoped to the stage.**
A stage's pane lists the surveys and analyses that answer that stage's
questions — derived from the question catalog's `analysis_ids`
(`survey-model.md` Part II §4, "questions are the composition key") — plus
the stage's own analyses by `intent`. What leaves the pane is the *banner
about how the list was scoped*, not the scoping. When nothing answers the
stage's questions, the empty state says so and names the nearest stage
that has something.

Full-scan versus scoped is a fact about **how the candidate list was
built**, not about the database. It stays in the API response (D2 is
right that the backend must not pretend to be scoped) and leaves the UI.
Its user-facing meaning is already a catalogued Discovery question —
*"Is there a Survey Definition authored for this resource's technology
type at all, or is that a catalog gap?"* — and that is where it belongs: on
the Questions tab, with an honest envelope. Three concrete rules:

- **"Tiers" goes; "stage" everywhere.** One vocabulary, the one the tab
  strip already uses.
- **Retry appears only when a retry could change the answer** — a
  transient error such as Egeria unreachable — never on a permanent
  catalog state. A retry on a permanent state teaches distrust of every
  retry in the app.
- **The empty state says what is true and what to do**, in this order:

  > No surveys are defined for PostgreSQL databases yet.
  > Egeria can survey this database now: **Survey PostgreSQL Database — Run**.
  > Questions already answerable from what is known: 7 → *Questions*.

  If Egeria has nothing either: "No surveys exist for this technology yet.
  Authoring one is developer work (see *Extending Resource Explorer*)." —
  and no button.

## 3 · Q3 — where "which engine" lives: three places, no fourth

1. The row's engine tag, secondary.
2. The popover sentence, before a run.
3. The result record after a run (`last_run_via`, already shown).

The standing section above the rows is the fourth place and it goes.

## 4 · Q4 — scope of the ask: the rule, then a grep

Yes, Discovery and Assessment almost certainly carry the same pattern, but
the fix is the rule in §0 applied once, not another pass of string edits.
The list of sentences to check is mechanical:

```
grep -nE "RE-authored|Egeria-native|Also known to Egeria|scoped|full-scan|tier" next/app.js index.html
```

Each hit is either moved to the item it describes, moved to a question
envelope, or deleted. Nothing is rewritten in place.

## 5 · Who does what

- **Coordinating session:** implement §1–§3 on the Survey & Analyses pane
  for database and filesystem resources; run the §4 grep and file the
  hits as one follow-up, not fix them inline.
- **Project owner:** confirm the shape before the pane is rebuilt a fifth
  time. One yes is enough; the copy above is a starting point, not a
  ruling on wording.
- **Designer, round 2:** the row's visual weight (name and Run heavy; tag,
  reason and popover light) and the empty-state layout, against real rows
  once §1 lands.
