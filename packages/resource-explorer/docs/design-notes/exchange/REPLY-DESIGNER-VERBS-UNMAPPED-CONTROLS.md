# REPLY — Designer: the controls the verb tables did not name (2026-10-06)

To ASK-DESIGNER-VERBS-UNMAPPED-CONTROLS.md, read against main at f1f679eb.
One word or "keep" per control, with a reason only where a page would
otherwise show two verbs for one act. Question 7 overlaps with
ASK-DESIGNER-STATE-AS-VISUAL-CUE.md; the drawing for it is in that reply
(`wireframes/StateCues.dc.html`).

**The rule behind every answer: the control that sends to Egeria says
Publish (or Catalog). A control that only opens a dialog keeps its own
verb, and the dialog's sending button says Publish.**

| Control | Page | Today | Becomes |
|---|---|---|---|
| Add member | Investigation scope | Add | **keep** |
| Relink members | Investigation, Egeria-bound | Relink members | **Publish member links** (it sends) |
| Unbind | Investigation, Egeria-bound | Unbind | **Unbind…**, with the confirmation "Unbind · the Egeria project stays; RE stops publishing to it" |
| Bind existing project | Investigation, not bound | Bind existing project… | **keep** (it opens a picker; the bind itself is RE's record) |
| Reclassify | Investigation | Reclassify… | **keep** on the opener; the dialog's sending button reads **Publish classification** |
| Promote | Work list | promote N → | **Shortlist N →**. It makes a narrower list in RE. "Promote" is also the investigation route's word for sending to Egeria (`/promote`), so two different acts would otherwise share one verb. |
| Export CSV | Work list, Investigation | export CSV | **Download CSV** |
| Add to investigation / Start an investigation… | Work list | as is | **keep** (scope acts, W1 wording) |
| Run, re-run | local analysis rows | run →, re-run → | **keep** |
| Schedules and subscriptions | Automate | Run now, Remove, toggles | **keep** (local). If a schedule ever starts an Egeria engine action, its button becomes "Run now in Egeria". |
| re-check | Doc sources | re-check | **keep** (a probe, not a send) |
| remove | Doc sources | remove | **Remove source…**, with a confirmation that names what survives: "the Egeria external reference stays" when one was published |
| Save, then catalog | none | none | **keep the rule**; no control needs it |

## The seven questions, briefly

1. **Relink and Reclassify:** Publish with its object, on the control that
   sends. The panel header saying "Egeria" isn't enough, because a header
   scrolls away and a button doesn't.
2. **Unbind and a doc source's remove:** neither takes "Remove from
   Resource Explorer". That form is reserved for taking a *resource* out of
   RE, and a doc source isn't one. Both confirmations say what survives,
   which is the part a person needs before pressing.
3. **Scope acts:** keep, as you read it. The one exception is *promote*,
   for the collision above.
4. **Local run:** keep "Run". "Run here" would put a word on every local
   row to explain the few that say "in Egeria". The marked case is the
   unusual one, so it's the one that carries the word.
5. **Export:** "Download CSV".
6. **Automate:** nothing today; the condition is noted in the table.
7. **The scope tree's row words: change them; the result line alone won't
   carry it.** The owner read "catalogue" as the write because it's a verb
   in the same accent-underlined style as every action link on the page.
   No sentence placed after the press can undo a reading made before it.
   The row choice becomes a **two-part selector**, not two links:

   > Include in catalog? **[ Include | Leave out ]**  ×

   - The column header asks the question. The segments are answers, not
     verbs.
   - The selected segment is filled in ink, the other outlined. It's a
     setting that shows its current value, which no link can do.
   - **Catalog** appears on one control only, the commit button, so the
     word means one act.
   - "×" clears the choice back to undecided.

   The result line stays ("saved in RE · you · just now · not yet
   cataloged"). The visual-cue reply puts it in the choice cell, where the
   eye already is.
