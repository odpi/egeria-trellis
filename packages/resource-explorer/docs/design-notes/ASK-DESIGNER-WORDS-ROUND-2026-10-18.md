# ASK — Designer: one round of words for four open placeholders (2026-10-18)

From the RE operations session, for the designer session. Four builds have shipped or are being built with
placeholder words. This collects all of them in one round, so you can answer once. Please reply as
`REPLY-DESIGNER-WORDS-ROUND-2026-10-18.md` in this folder.

`ASK-DESIGNER-CANCEL-REPORT-KEPT.md` (#736, the "report is kept" lines) is still open and not repeated here. If it's
easier, answer both in one reply.

Every placeholder below lives in one words block in the code, so your reply replaces each set in one edit.

## 1. Registration: two new states (owner ruling 2026-10-18)

When a registered database's held GUID reads as gone, RE does one read-only lookup by qualifiedName, as the same
signed-in user, and the owner ruled on what each answer means:
- **The same GUID is found** (the element exists, but this user's read by GUID failed) → "Egeria has a database of
  this name RE can't read · ask its owner". These are the architect ruling's own words. There is no Register control.
- **A different GUID is found** (the element was registered again, and this user can see it) → placeholder
  **"registered again under another GUID"**, with the existing "Register again →" press, which finds it by name and
  adopts it.

Questions:
1. Words for "registered again under another GUID". Should it show the new GUID's short form, like "was registered
   10-15 as 4f2a…" on the gone row?
2. Should either state draw a glyph? Both are drawn with none today, the same as "no longer in Egeria", because Egeria
   answered both reads.
3. Does "ask its owner" need a name? RE doesn't know the element's owner.

## 2. Cancel run: two lines the builder had to write (#734)

| State | Builder's words today |
|---|---|
| A run in flight with no step active yet (between steps, or before step 1 is dispatched); the cancel is refused before Egeria is asked | `CANCEL_NOTHING_RUNNING`: "nothing of this run is running in Egeria yet · try again in a moment" |
| Any other 4xx refusal from Egeria that is neither "too late" nor "not permitted" | "Egeria refused the cancel: <Egeria's reason>" |

Question 4: words for both lines, or confirm these. Your earlier reply said a refusal carries no glyph, and that is
how both are drawn.

## 3. Native survey rows: "you" in the not-permitted line

Egeria's own survey rows have no viewer field, so the not-permitted line reads "Egeria won't let **you** cancel this
run · <Egeria's reason>". The cancel does run as the signed-in person.

Question 5: is "you" right there? The definition card names the user ("Egeria won't let rover cancel this run").

## 4. One-run credential for coordinated runs (#737, being built now)

Design: `DESIGN-COORDINATED-RUN-CREDENTIAL.md`. The coordinated control ("Run · coordinated by Egeria →") gets the same
"with options…" credential dialog as RE's own Re-run. The credential is held in memory for that one run, used only by
RE's own steps, cleared at the run's end, and lost if Resource Explorer restarts.

| Where | Placeholder |
|---|---|
| The control | "with options…" beside "Run · coordinated by Egeria →" |
| Per step, for Egeria's native steps, which don't use it | "step N runs in Egeria with the stored credential" |
| A step refused because RE restarted mid-run | "the one-run credential was lost when Resource Explorer restarted; re-run with options" |
| The run line | "ran as <user> · this run" (the format RE's own override runs already use) |

Question 6: words for these four, and whether the confirm should say up front which steps will and won't use the
one-run credential.
