# REPLY — Designer: one verb for "saved in RE", one for "sent to Egeria" (2026-10-06)

To ASK-DESIGNER-SAVE-AND-PUBLISH-VERBS.md (on `re/ask-designer-save-verbs`,
b79b6cd5), read against main at 204ac675: `stages/curate-bands.js`,
`curate-scope.js`, `context.js`, `investigation.js`, `enrichment.js`, `native-surveys.js`,
`worklist.js` and `app.js`'s journal, report and record controls. No
drawing: no control changes shape.

## The rule, confirmed with one sharpening

**Two families of verb, never mixed, and the result line says which
family acted:**

| Family | On the control | After the click, the row says |
|---|---|---|
| **Saved in RE's record** | **Save**, when the control ends a form. A choice keeps its own word ("catalogue", "leave out", "confirm", "mark as…"). | "saved · ‹who› · ‹when›" |
| **Sent to Egeria** | **Catalogue** (Curate's commit, for every kind) or **Publish** (any other send) | "catalogued · read back ‹when›" / "published · read back ‹when›", only once the read-back proves it. Until then: "sent · waiting for Egeria" |

The sharpening is in the right-hand column. A verb on a button is a
promise, and the line after the click is what keeps it. The Egeria family's
words appear only once the read-back proves them. That's the proof rule
every publish here already follows, and it applies to the vocabulary too.

**A control that does both says both, in order**: "Save, then catalogue".
I expect this to be rare. If one shows up, check whether it should be two
controls.

## The journal: Save, not Write

**No exception.** The owner's complaint was five verbs with no rule behind
them. If one stays for a good reason, the next person finds another good
reason, and the vocabulary drifts back to where it started. What makes a
journal entry different isn't the verb; it's that it's **permanent**. So
the permanence is stated in words beside the button:

> [ Save entry ] · saved entries are permanent: no edit, no delete

That's more honest than "Write", which suggests a draft. It tells the
person what they're agreeing to before they press, which no verb alone
can. The same sentence fits signed curator notes. The appended result line
is "saved · dwolfson · 10-06 09:12", the same as everywhere else.

## Every control that changes, on main today

**Saved in RE (becomes Save, or keeps its choice word):**

| Where | Today | Becomes |
|---|---|---|
| Curate › Findable › tags | `add` (a bare link) | **Save** beside the input, after "＋ tag" opens it |
| Curate › Findable › group | `change` → select | stays a choice (the select); the line after reads "saved · ‹when›" (see the gap below) |
| Curate › Ratings | **Submit** | **Save rating** |
| Journal (Curate and Disposition) | **Write** | **Save entry**, with the permanence sentence |
| Context › human questions, judgements, observations | `save` | **Save** (capitalized like every other control; the word was already right) |
| Enrichment › doc sources | `add + probe` | **Save and probe**. A probe is a network read of the URL, not an Egeria send, so it isn't reserved, but it's a second act and the control says so. |
| Scope tree | catalogue / leave out / confirm | unchanged: choices. The header's new line already carries where they live ("Saved in Resource Explorer · not yet catalogued in Egeria"). |
| Scope › `declare the scope again` | (a link) | **Start a new baseline**. It saves; it doesn't redeclare the choices, and the old words suggested it did |
| Report records › `record`, `save as report`, `save as work list…` | as is | unchanged: they already say save, or name the thing saved |

**Sent to Egeria (becomes Catalogue or Publish):**

| Where | Today | Becomes |
|---|---|---|
| Investigation › Egeria | **Create in Egeria →** | **Publish to Egeria →** |
| Investigation › Egeria | **Sync now** | **Publish again**. RE's publish is one-way (owner, 2026-10-01); "sync" promises a two-way reconciliation it doesn't do. |
| Work list | `publish to Egeria` / `re-publish` | **Publish to Egeria** / **Publish again** (capitalized; same words) |
| Repository Curate | **Catalogue →** | unchanged; it's the reserved word |
| Database Curate (slice B) | not built | **Catalogue**, as ruled |

**Starting work inside Egeria isn't a send, but it says where it runs.**
The native-survey rows' `run →` / `re-run →` (`stages/native-surveys.js`)
become **Run in Egeria →** / **Run again in Egeria →**, so a person can
tell them apart from the local `Run` on the analysis rows beside them. "Run" isn't
reserved, but the destination is named whenever it's Egeria.

## Two things this surfaces

1. **Group has no author to show.** `POST /api/projects/{slug}/group`
   records no author (`CURATE-UI-DATABASES-IMPLEMENTED.md` deviation 2), so
   its result line can only say "saved · ‹when›". Either the route records
   the person, like the other Curate writes since `CURATE-AUTHORS`, or the
   line is honest about it: "saved · ‹when› · who isn't recorded". I'd
   record it.
2. **Catalogue or Catalog.** The reserved verb is becoming a word people
   learn as *the* Egeria act, so its spelling should be settled now. The
   house writes "catalogue". Egeria writes "catalog" (`CatalogTarget`,
   "Catalog and Survey", the cataloguer's own name), and the owner's
   writing preference is US English. I recommend **Catalog** on controls
   and in result lines, from the slice that builds the commit. That's one
   word across RE and the Egeria screens a steward moves between. It's the
   owner's call; whichever is chosen, there shouldn't be two spellings on
   one page.

**Decision (project owner, 2026-10-06):** **Catalog**, the US spelling, is
the reserved verb on controls and in result lines ("Catalog", "cataloged ·
read back ‹when›"), matching Egeria's own word. Every "Catalogue" in the
tables above reads "Catalog". The repository Curate's `Catalogue →`
becomes `Catalog →` in the same slice, so no page shows both spellings.
Design notes keep their existing filenames; their prose moves to the US
spelling as they're next edited.

## Not changed

- Choice words on the scope tree, verdict pickers ("mark as…") and
  include/exclude controls: a choice names what it chooses.
- Destructive verbs ("Remove from Resource Explorer", per the controls
  reply): removal is neither a save nor a send.
- The accent: Save, Catalogue and Publish are all controls, so all three
  may carry it. The *result lines* are states and stay in ink.
