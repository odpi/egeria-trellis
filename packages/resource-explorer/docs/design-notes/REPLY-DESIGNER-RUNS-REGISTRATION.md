# REPLY — Designer: survey definition cards, run controls and registration states (2026-10-16)

To ASK-DESIGNER-RUNS-REGISTRATION.md (on `re/ask-designer-runs-registration`),
read against main at df5dbc91: `stages/native-surveys.js` (`nativeSurveyRowHtml`,
`processLineHtml`, `coordinateControlHtml`, `coordinateOffNoteHtml`), `app.js`
(`DEFINITION_RUN_WORD`, the definition run line), `glyphs.js`,
`catalog_and_survey.py` (`check_pointers`, `RegistrationUnresolved`),
`mixed_process.py`, `egeria-host-edit.js` (#717) and the case-3 slice's owner
answers of 2026-10-15. Drawing: `wireframes/RunsAndRegistration.dc.html`,
canvas page 22.

**One rule behind most answers: one thing, one card, one verb per act.** A
survey definition is one thing, so it gets one card. Starting it is one act
with two coordinators, so it gets one verb, "Run", with the coordinator
named after it. Anything Egeria answered is a state on the element, in
Egeria's words, and anything the person can fix says how, on the element.

## 1. One survey definition, two cards

**1. Yes, one card, and the definition card survives.** The top card isn't
one of Egeria's own surveys. It's a *run* of RE's definition that Egeria
happened to coordinate, and its section heading ("Egeria's own surveys ·
run by Egeria's survey action engine") says something untrue about it. The
definition card already carries the description, the qualified name, the
history, the steps and "runs 13 analyses". What it lacks is the run detail,
so that moves onto it:

- **The run line goes under the card's header**, replacing today's short
  "⚠ ran 18h ago · coordinated by Egeria · started by peterprofile". It's
  the latest run, whoever coordinated it, with the same words either way:
  "◐ 2 of 4 ran · why · coordinated by Egeria · started by peterprofile ·
  Oct 15, 7:34 PM · step 4 report ›". The step lines stay one gesture away.
- **Earlier runs** sit behind "runs · 6 ›" on the card, a short list with
  the coordinator on each line ("by RE" or "coordinated by Egeria"). The
  existing definition history stays what it is: changes to the definition.
- **The analyses stay where they are**, below. "runs 13 analyses" on the
  card opens that list filtered to the 13. The other 17 belong to other
  definitions or none.
- **"Egeria's own surveys" keeps only Egeria's own**: native surveys and
  mixed processes. `re_definition` rows leave that list.

**One count to settle before merging.** The card says "8 steps", and the
run says "2 of 4 ran". If both are true, they count different things: steps
in the definition versus steps the process reached or published. On one
card they must say which, or a reader will think one of them is wrong:
"2 of 4 ran · the process stopped after step 4 of 8", or whatever the
builder finds the 4 to be. Counts open what they counted, so "4" should
open the four.

**2. The interim: yes, but with the definition's words, not "Run again in
Egeria".** The owner ruled on 2026-10-15 that this act is never called
"run in Egeria", because Egeria coordinates while RE's engine host runs
each step. "Run in Egeria" is the native rows' verb, where Egeria's own
engine runs the survey. So the interim control on the top card is the
**same control** as on the definition card, with the same label (§2), and
it shares the same pending state. One act, one label, wherever it appears.

## 2. Three run verbs for one definition

**3. "Run" is the verb, and the coordinator follows it.** The owner didn't
recognize "coordinated by Egeria" as a button because it has no verb.
It reads as a state, and the same words are printed as a state on the run
line. On a definition card:

> **Re-run…** · **Re-run · coordinated by Egeria →**

The plain one is RE coordinating, which is the default and needs no
qualifier. The qualified one is Egeria coordinating. Both appear, side by
side, while the owner expects the second to be temporary. Putting it
behind a choice inside the "Re-run…" dialog would be tidier, but it would
hide the thing being tested. When the trial ends, fold it into that dialog
as "Coordinated by: ● Resource Explorer ○ Egeria".

**4. "Run · coordinated by Egeria →": accepted, with one change: "Re-run"
once it has run**, to match its neighbor and the rest of the page (the
"Run…" / "Re-run…" pair already does this). Style it exactly like
"Run again in Egeria →": accent border, accent ink. While a run is live,
the control shows its state on itself, as built ("◔ running"), and ignores
a second press.

**5. "re-run →" and "Re-run…": yes, they must look different, and case
isn't how.** The arrow and the ellipsis are the right convention (→ acts
now, … asks first), but the two shouldn't be twins on one row. The
lowercase/uppercase difference is an accident; use sentence case
everywhere. On each analysis row:

> **Re-run →** · *with options…*

The first is a button: it runs at once with the last options. The second
is a quiet text link (muted, underlined, no border) that opens the dialog.
A button and a link look different without anyone reading the
punctuation. A definition card has only the dialog form, because a
definition run always shows its plan first.

## 3. A definition Egeria can't run

**6. Not hidden: shown off, on the card, with its reason.** The pane-level
note (`coordinateOffNoteHtml`) is right for a reason that applies to every
card (engine host off): say it once. A reason that belongs to *one*
definition belongs on that card. Otherwise the reader sees the control on
one card and not on two others, with the explanation somewhere else.
That's the "did nothing visible" failure again. So on the Scouting Scan
and the Assessment Survey:

> Re-run… · [Re-run · coordinated by Egeria] (off: dashed border, muted ink)
> Egeria can't start this · step 1 has no executor in Egeria · why

"Dispatch" is a word only the builder uses. "Can't start this" is what
the person needs to know, the step is named, and the full reason (which
step, which request type, what's missing) sits behind "why". If every
definition on the page is in this state, the pane-level note covers it
once and the cards stay quiet.

## 4. Partial runs and refused steps

**7. "◐ 2 of 4 ran" with "why" on demand: right. And yes, a refusal the
person can fix says so on the element.** This is the round-1 rule: a
refusal shows its first clause at once, because it changes what the person
does next. Under the run line:

> steps 1–2 not run · the survey's credential reads 3 of 61 tables · use a
> credential that can read all tables ›

The link goes where the credential is set. "why" keeps the per-step
detail.

**One glyph needs correcting.** The step lines print "⊘ refused"
(`mixed_process.py:559, :573`). In `glyphs.js`, ⊘ is "cancelled", ended by
a person's decision, and "refused" isn't in the vocabulary. A step the
capability gate refused for a credential gap is "not run, and can be":
**○ not run**, with "needs a credential" as its reason. ○ is already that
family, and it's the honest state: nothing failed, and the person can
change the outcome. ✕ stays for real failures.

**8. Stopped checking: on the run line, in place of the live timer, with
a control instead of "reload".** The run's state hasn't changed. Only RE
has stopped looking, so the state word stays and the line says when it
was last read:

> ◔ running · as of 14:12 · stopped checking after 10 minutes · **check now**

"Check now" is a quiet button that reads once and resumes following the run
if it's still going. Asking the person to reload the browser makes them
reload the whole page to refresh one line.

## 5. Registering a database with Egeria

**9. Where the Egeria-visible host lives:** where #717 put it, on the
server's row and the database's row. It's a property of how Egeria reaches
that server, so it isn't an Admin setting. Two additions:

- **Inheritance drawn, as on the scope tree.** A database that takes its
  server's value shows it muted, "host.docker.internal:5442 (from its
  server)". One that differs shows it in ink with "differs from its
  server". The server's "use it for them" offer stays as built.
- **"Egeria still holds the old address" is a state in the Egeria lane of
  that row**, not a sentence after the edit:
  > ⚠ will change · Egeria has localhost:5442 · Register again → sends the new address

  ⚠ is "will change in Egeria at the next press", which is exactly this.
  It stays until a read-back shows Egeria holds the new address.

**Should failed surveys point at the address? Yes,** when the failure is a
refused or timed-out connection. The first sentence stays Egeria's, and
then:

> ✕ failed · Connection to localhost:5442 refused · Egeria tried
> **localhost:5442**, and localhost is Egeria's own machine · change the
> Egeria-visible host ›

The middle clause appears only when the host is `localhost` or
`127.0.0.1`. That's a fact about the address, not a guess about Docker.

**The register's own result must stop at what it proved.** "Registered"
reported success, and then both surveys failed to connect. The register
proves the elements exist in Egeria, not that Egeria can reach the
database. So until a survey (or a connection check) proves the reach, the
row reads "registered · Egeria hasn't connected yet". After a failed
connection, it reads "registered · Egeria couldn't connect". Status words
come from proof rows.

**10. A registered element deleted in Egeria: "no longer in Egeria", one
press to register again.** `check_pointers` already separates `gone`
(Egeria answered that there's no such element) from `unreadable` (the read
failed), and `glyphs.js` has the word. The defect is that this read
landed in `unreadable`. If Egeria answers a deleted GUID with an error
rather than an empty result, the builder classifies that error as gone. The
row:

> no longer in Egeria · was registered 10-15 as 4f2a… · **Register again →**

Notes on that row:

- **No glyph**, matching the "deleted" ruling. `gone` currently carries ?,
  the "couldn't establish" family, but this one *was* established: Egeria
  answered. Drop the ? from `gone`.
- **"no longer in Egeria", not "deleted".** RE didn't see the delete and
  can't tell deleted from purged. "Deleted" stays for the elements RE
  deletes itself and reads back (the scope rows).
- **Register again is one press.** Nothing exists to duplicate. The old
  GUID stays in the row's history ("was …"), and RE clears its pointer as
  part of that press, never by hand.
- **"unreadable" keeps its own words**: "couldn't read Egeria · ‹Egeria's
  first sentence› · check again". It offers no register, because the
  element may still be there.

**Start again: two presses are right, but the confirm must say why.** The
second press exists because an earlier registration is unresolved and a
second one could make a duplicate database element
(`RegistrationUnresolved`). The armed button ("Confirm: start again", for
6 seconds) doesn't say that. Make the confirm name the risk:

> An earlier registration (submitted 10-16 13:02) hasn't finished. Starting
> again may create a second database element in Egeria · **Start again** ·
> wait for it

When the earlier element is gone (no longer in Egeria) or the earlier
process failed, there's nothing to duplicate, so it's one press with no
confirm.

**11. A shared server connection: shown, quietly, unless it's the cause.**
It matters because the shared connection's credential decides what
Egeria's surveys of *this* database can read. On the database's
registration row, it's one gesture away:

> Egeria's server element is shared with coco_pharma · its connection uses
> coco_pharma's credentials

It moves onto the row, visible, in two cases. One is when a survey of this
database is refused or limited for a credential reason (§4's line then
names it: "the shared server connection uses coco_pharma's credential").
The other is when this database's own credential differs from the shared
one. Whether two databases *should* share one server connection is an
architecture question, and I've listed it below.

## 6. Sign-in that Egeria then refuses

**12. Refuse the sign-in, in plain words, and never truncate who is signed
in.** On the sign-in form, as the result of the press:

> Egeria accepted **rover**'s password but refuses its requests. Sign in as
> a user this Egeria server knows.

"Issued a token" is the mechanism. What the person needs is that the name
worked and the access didn't, and which name it was, because autofill
chose it.

**The header always shows the full user name.** Who you are acting as
decides what you can see and do, and every signed event carries it. A name
truncated to "rov…" is the one thing on the page that must never be
ambiguous. If space is short, the header shortens something else, the
server's name first, and then wraps to a second line rather than cut the
user. Add the server it's signed in to, one gesture away ("rover · on
egeria-dev:9443"), since a name means something only on one server.

## Slices

| Piece | Kind |
|---|---|
| One card per definition: run line and run list on the definition card; `re_definition` rows leave "Egeria's own surveys"; the 8-versus-4 count reconciled | UI + route |
| Interim: the same coordinated control on the top card until the merge | UI |
| "Re-run · coordinated by Egeria →" as a button; analyses rows "Re-run →" plus "with options…" link | UI copy |
| A definition Egeria can't start: control shown off with the step named | UI |
| Fixable refusal on the run line with the credential link; ○ not run replaces ⊘ refused | UI + words |
| "check now" on a run no longer followed | UI |
| Host inheritance and the ⚠ will-change state; connection failures name the address; "registered · Egeria hasn't connected yet" | UI + words |
| gone classified from Egeria's not-found; "no longer in Egeria" without ?; Register again in one press; Start again's confirm names the risk | route + UI |
| Shared connection line, raised when it's the cause | UI |
| Refused sign-in words; full user name in the header | UI |

## Left open, for the architect

1. Should two databases share one Egeria server element whose connection
   carries the first database's credential? If they must, should a
   database's surveys use a connection with its own credential?
2. What Egeria answers for a GUID it deleted (empty result or an error),
   so `gone` can be classified correctly.
3. What the "4" in "2 of 4 ran" counts, against the definition's 8 steps.
