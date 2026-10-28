# ASK — Designer: survey definition cards, run controls and registration states (2026-10-16)

From the RE operations session, for the designer session, at the owner's request
("lets collect up questions for the designer"). Reply as
`REPLY-DESIGNER-RUNS-REGISTRATION.md` in this folder; drawings under `wireframes/` welcome.
Background: `REPLY-DESIGNER-STATE-CUES-*` (two lanes of cues, a cue and a short word on the
element, the sentence on demand), the owner's 2026-10-06 principle that state shows on the element
rather than in a sentence, and the 2026-10-15 owner decision that Run starts an Egeria-coordinated run
("add a Run button labelled Egeria-coordinated, likely temporary").

Everything below came from the owner using coco_pharma and a new database on 2026-10-16.

## 1. One survey definition, two cards

**The owner's words:** "There are two cards with Database Analysis Survey - the top one has no run button."

**What the screen shows (Analysis stage, Survey tab, coco_pharma):**
- **Top card**, in the list of Egeria survey rows ("Survey PostgreSQL Server", library steps):
  "◐ 2 of 4 ran why · step 1 … ⊘ refused · … · step 4 … ✓ report · coordinated by Egeria ·
  started by peterprofile · Oct 15, 7:34 PM", plus report links. Every other row in that list has
  **Run again in Egeria →** on the right. This one has none, by code (`native-surveys.js:283`
  skips `re_definition` rows).
- **Lower card**, "ANALYSIS · 1": the definition itself (description, qualifiedName, definition
  history, "produces …"), the header "8 steps · none fetch · runs 13 analyses · ⚠ ran 18h ago ·
  coordinated by Egeria · started by peterprofile", and two buttons: **Re-run…** (RE runs it) and
  **coordinated by Egeria** (Egeria runs it as a process).
- Below that, "ANALYSES · 30" lists each analysis with its own **re-run →** / **Re-run…**.

**Questions:**
1. Should a survey definition be one card? If so, which one survives, and where do its run history
   (the top card's line and report links) and its 30 analyses sit?
2. Until that is decided, we plan to put **Run again in Egeria →** on the top card as well, the
   same control as its neighbours. Is that acceptable as an interim?

## 2. Three run verbs for one definition

The same definition can be started by **Run again in Egeria →** (the native rows), **Re-run…**
(RE runs it), **coordinated by Egeria** (Egeria runs it), and per analysis by **re-run →** and
**Re-run…** (with options). The owner did not recognise "coordinated by Egeria" as a run button.

**Questions:**
3. What are the words for "RE runs this" and "Egeria runs this"? Should both appear on a
   definition, or one with the other behind a choice?
4. Our interim label is **Run · coordinated by Egeria →**, styled as a button like
   "Run again in Egeria →". Acceptable?
5. **re-run →** and **Re-run…** differ only by case and an ellipsis (the second opens options).
   Should they look different?

## 3. A definition Egeria cannot run

The Database Scouting Scan (3 steps) and the Database Assessment Survey (1 step) have no executor
in Egeria for any step, so Egeria cannot dispatch them. Today their cards still offer
"coordinated by Egeria", and pressing it would start a process that can never progress.

**Question:**
6. We plan to hide the control on such a definition and say once, short, "Egeria can't dispatch
   step 1", with the full reason on demand. Right cue and placement?

## 4. Partial runs and refused steps

Yesterday's run reads "◐ 2 of 4 ran why". Steps 1 and 2 were refused because the surveyor's
credential can read only 3 of the database's 61 tables, which is a credential gap, not a fault.

**Questions:**
7. Is "◐ 2 of 4 ran" with "why" on demand right? Should a refusal for a credential gap say so on
   the element ("needs a credential that can read all tables"), since it is something the person
   can fix?
8. When the page stops polling a run that still shows as running (after 10 minutes), we say once:
   "Stopped checking · still shown as running · reload to check again". Placement and words?

## 5. Registering a database with Egeria

The owner registered a new database (`localhost_docker_egeria`). Four states came up that the screen
handles poorly:

- **Wrong address.** The server record said Egeria reaches it at `localhost`. Egeria runs in Docker,
  so both its surveys failed ("Connection to localhost:5442 refused"), while the register itself
  reported success. A new edit control (#717) lets the person change the **Egeria-visible host** on
  a server or a database. A server edit offers to carry the new value to its databases, and says
  that elements Egeria already holds keep the old address until the database is registered again.
- **Registered element deleted in Egeria.** RE read the deleted element as "unreadable", not gone,
  and offered no way forward until its stored pointer was cleared by hand.
- **Start again.** A second registration needs two presses ("Start again →", then confirm).
- **Shared server connection.** The row says "the server's connection uses
  localhost_docker_coco_pharma's credentials": two databases share one server element in Egeria.

**Questions:**
9. Where does the Egeria-visible host edit live (server card, database header, Admin), and how is the
   "Egeria still holds the old address" note shown? Should a registration whose surveys then fail on
   connection point at the address?
10. What does a row say when its registered Egeria element has been deleted, and what does it offer
    (register again)?
11. How should a shared server connection, with another database's credentials, be shown, if at all?

## 6. Sign-in that Egeria then refuses

The owner was signed in as `rover` without knowing it (most likely browser autofill). Egeria issued
a token, then refused every call with 401. A builder card proposes refusing such a sign-in:
"Egeria issued a token for rover but refuses its requests · sign in as a user this Egeria server
knows". The signed-in user is also truncated in the header ("rov…").

**Question:**
12. Words for the refused sign-in, and should the header always show the full signed-in user?
