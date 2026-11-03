# ASK — Designer: words and cues for Cancel on the definition card (2026-10-17)

From the RE operations session, for the designer session, at the owner's request ("ask the designer for Q4 in
parallel"). Please reply as `REPLY-DESIGNER-ENGINE-HOST-CANCEL-WORDS.md` in this folder.
Background: `DESIGN-ENGINE-HOST-GOVERNANCE-ENGINE-API.md` §2.4 (merged in #731), your
`REPLY-DESIGNER-RUNS-REGISTRATION.md` (one card per definition, "Run/Re-run · coordinated by Egeria →", ⊘ =
cancelled, "running · as of HH:MM · check now"), and the owner's principle that state shows on the element with
a cue and a short word, with the sentence on demand.

A builder is working now with placeholder words held in one place. Your words replace them in one edit.

## What is being built

While an Egeria-coordinated run of a survey definition is running, its definition card gets a **Cancel** control
beside the run line. Pressing it asks Egeria to cancel that process instance.

Owner rulings (2026-10-17):
- Cancel shows only to the person who started the run and to admins. Everyone else sees no control.
- No Cancel for a coordinated run RE did not start (started from another tool or Egeria's own UI), even for admins.

How it behaves:
- A step that is already running cannot be stopped mid-step. It finishes its work, publishes nothing, and Egeria
  dispatches no further steps. Steps that finished earlier keep their reports.
- The press writes a proof row right away. The card can then show a pending state until Egeria confirms.
- "Cancelled" is shown only when a read of Egeria shows it recorded CANCELLED. That can take seconds, or until the
  running step ends.
- Egeria can refuse the cancel: the run already finished, the user isn't permitted, or Egeria can't be reached.
  Its reason comes back as a short Egeria message.

## Placeholders in the build today

| State | Placeholder |
|---|---|
| Control | `Cancel` |
| Confirm on press | `Stop this run? Steps already finished keep their reports.` |
| Pending, after the press and before Egeria confirms | `cancelling…` |
| Refused | `Egeria refused the cancel: <Egeria's reason>` |
| Done | existing ⊘ glyph + `cancelled`, from Egeria's read-back only |

## Questions

1. **The control.** Is it a word ("Cancel" or "Stop"), a glyph, or both? Where does it sit relative to
   "running · as of HH:MM · check now" and the "Run/Re-run · coordinated by Egeria →" button, which is disabled
   while the run is in flight?
2. **The confirm.** Is a confirm needed, and in what words? It should say that the running step finishes and
   that finished steps keep their reports. Does it sit in place on the card, or in a dialog?
3. **Pending.** The cue and the word between the press and Egeria's confirmation. Does the line keep
   "as of HH:MM · check now" while pending?
4. **Mid-step.** Should the line say that the current step is finishing, for example by naming it?
5. **Done.** Does ⊘ cancelled carry who cancelled it and when ("cancelled by X · HH:MM")? How does it show which
   steps finished before the cancel, given the existing "◐ N of M ran" pattern?
6. **Refused.** Words for a refused cancel, shown in place on the line. Does Egeria's own reason show by default
   or on demand?
7. **Hidden cases.** When Cancel is hidden from someone who isn't the starter or an admin, or because RE didn't
   start the run, should anything say why (on demand), or show nothing at all?
