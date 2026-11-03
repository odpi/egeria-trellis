# REPLY — Designer: words and cues for Cancel on the definition card (2026-10-18)

To ASK-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md (on `re/ask-designer-engine-host-cancel-words`),
read against main at b41a82a8: `DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md` §2.4,
`REPLY-DESIGNER-RUNS-REGISTRATION.md`, `glyphs.js`, and the Cancel already on
Egeria's own survey rows (`native-surveys.js`, `cancelControlHtml` and its handlers).
That existing Cancel already has most of the right shape: a quiet control, a
confirm in place, a pending line and a read-back. The definition card should
take the same shape, so a person learns Cancel once. Where the two differ
below, the native row should move to match in its next edit.

The words for the builder's one edit are in the last section.

## 1. The control

**A word, not a glyph: "Cancel run".** It's an act with a consequence, so it
needs a verb anyone can read. A glyph would need learning, and the obvious
candidate, ⊘, is the *result* (cancelled), not the act. "Cancel" rather than
"Stop", because Egeria records the outcome as CANCELLED and the card will
say "cancelled": the act and its result should share a word. Add "run",
because a definition card holds a definition, its history and its runs, and
"Cancel" alone beside them could mean any of them.

**Where: on its own short line, directly under the run line.** Three
reasons:

- The run line ends with "check now". Putting a cancel right beside a
  harmless refresh invites the wrong press.
- The disabled "Re-run · coordinated by Egeria" button is about starting a
  run. Cancel is about *this* run, so it sits with this run's line.
- Everything that follows a press (confirm, pending, refused) replaces that
  one line in place, and the run line above keeps saying what Egeria last
  said. That keeps the two lanes apart: what you asked for, and what Egeria
  has.

> ◔ running · step 2 of 3, table statistics · as of 14:12 · check now
> Cancel run

Style it like the native row's: a text control in the accent color,
underlined, no border.

## 2. The confirm

**Yes, a confirm, in place on that line, not a dialog.** A cancel can't be
undone and costs a whole re-run, so it gets one question. A dialog would
take the eye away from the run it's about. The confirm names what happens
to each part of the run, using the step the run line already knows:

> Cancel this run? Step 2, table statistics, finishes but publishes
> nothing, and step 3 won't start. Step 1 keeps its report.
> **Yes, cancel** · keep running

When the current step isn't known, the general form:

> Cancel this run? The running step finishes but publishes nothing, and no
> later step starts. Finished steps keep their reports.
> **Yes, cancel** · keep running

"keep running" rather than the native row's "keep it", because it says what
the run will do. The confirm disarms on its own after a few seconds, as
the native one does.

## 3. Pending

**Not "cancelling…".** After the press, RE hasn't cancelled anything. It
has asked Egeria and written a proof row of the asking. The word should say
that, and since the proof row exists, it's a recorded state, not just a
look. On the control line, in place of "Cancel run":

> cancel requested by you · 14:13 · waiting for Egeria

**The run line above keeps "as of HH:MM · check now", unchanged.** Egeria
hasn't changed the run's state, so the run line has nothing new to say,
and "check now" is exactly what someone waiting wants. The pending line
takes no glyph: the run's own cue (◔) is the state, and a second cue would
compete with it.

## 4. Mid-step

**Yes, name the step, because it explains the wait.** Without it, a cancel
that takes minutes looks like a cancel that didn't work. Two moments:

- **Requested, Egeria hasn't recorded it yet:** the pending line gains the
  step: "cancel requested by you · 14:13 · waiting for Egeria · step 2,
  table statistics, finishes first".
- **Egeria has recorded CANCELLED, but the step is still finishing:** this
  is now cancelled, by the proof rule, so the run line changes:
  > ⊘ cancelled · step 2, table statistics, is finishing and will publish nothing

  When the step ends, the line settles into the done form below.

## 5. Done

**Yes, it carries who and when.** A cancel is a person's decision, and
decisions are signed. The cancel also replaces the partial pattern: ⊘ is
the run's one cue, because the cancel is what ended it. "◐ N of M ran" is
for a run that ended on its own with steps refused.

> ⊘ cancelled by dwolfson · 14:14 · 1 of 3 steps finished · step 1 report ›

- **"by"** comes from the proof row of the press. **The time** is when
  Egeria recorded CANCELLED (from the read), not the press, because the
  word "cancelled" is Egeria's.
- **"1 of 3 steps finished"** counts steps that ended and kept a report. The
  step that was running doesn't count: it published nothing. Clicking the
  count opens the steps:
  > step 1, catalog check · ✓ report ›
  > step 2, table statistics · ⊘ cancelled · published nothing
  > step 3, change rates · not started
- **A run RE started but someone cancelled elsewhere** (Egeria's own UI, or
  another tool): RE has no proof row of a press, so it doesn't name a
  person. It says "⊘ cancelled in Egeria · 14:14 · not from Resource
  Explorer".
- **The control line goes away** once the run is no longer running. There's
  nothing left to cancel.

## 6. Refused

**In place, on the control line, with Egeria's reason by default.** The
reason is the whole point of the line, since it says what the person can
do next. The full Egeria message, when longer, is on demand. Word it by
case, because the three refusals mean different things to the person:

| Egeria's answer | The line | Then |
|---|---|---|
| the run already finished | "too late to cancel · the run had already finished" | the run line updates on its next read; the control goes |
| not permitted | "Egeria won't let rover cancel this run · ‹Egeria's reason›" | Cancel run stays, in case another person can |
| Egeria unreachable | "cancel not sent · couldn't reach Egeria · try again" | Cancel run comes back |

The third one isn't a refusal, so it shouldn't say "refused". Egeria
never answered. No glyph on any of them. Correct the native row in its next
edit: it shows a refused cancel with ◐, which in `glyphs.js` means
"measured, within a limit", not "your request didn't go through". The run's
own cue is unchanged by a refusal, and the line's words carry it.

## 7. Hidden cases

**Nothing extra. The run line's existing words already explain it, so make
sure they're there:**

- **Not the starter, not an admin:** the run line already says "started by
  peterprofile". That's why there's no Cancel, and anyone who wonders knows
  whom to ask. Adding "you can't cancel this" to every run someone else
  started would be noise on most cards.
- **A run RE didn't start:** the run line's provenance must say so, in place
  of "started by …": "started outside Resource Explorer". On hover: "RE can
  cancel only runs it started; cancel this one in Egeria". That's the one
  place the reason is worth a sentence, because an admin will look for the
  control and not find it.

## The words, for the one edit

| State | Words |
|---|---|
| Control | `Cancel run` |
| Confirm, step known | `Cancel this run? Step N, <step>, finishes but publishes nothing, and <later steps> won't start. <Finished steps> keep their reports.` · `Yes, cancel` · `keep running` |
| Confirm, step unknown | `Cancel this run? The running step finishes but publishes nothing, and no later step starts. Finished steps keep their reports.` · `Yes, cancel` · `keep running` |
| Pending | `cancel requested by you · HH:MM · waiting for Egeria`, plus ` · step N, <step>, finishes first` when known |
| Cancelled, step still finishing | `⊘ cancelled · step N, <step>, is finishing and will publish nothing` |
| Done | `⊘ cancelled by <user> · HH:MM · K of M steps finished` plus each step's report link |
| Done, cancelled elsewhere | `⊘ cancelled in Egeria · HH:MM · not from Resource Explorer` |
| Refused: finished | `too late to cancel · the run had already finished` |
| Refused: not permitted | `Egeria won't let <user> cancel this run · <Egeria's reason>` |
| Not sent: unreachable | `cancel not sent · couldn't reach Egeria · try again` |
| Provenance, run RE didn't start | `started outside Resource Explorer` (hover: `RE can cancel only runs it started; cancel this one in Egeria`) |

For the native survey rows, in their next edit: the same control word,
"keep running", "cancel requested…" for pending, and no ◐ on a refusal.
