# ASK — Designer: the controls your verb tables did not name (2026-10-06)

From the design session, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-VERBS-UNMAPPED-CONTROLS.md` in this folder. Background:
`REPLY-DESIGNER-SAVE-AND-PUBLISH-VERBS.md` (your two tables and the owner's
decision that the reserved word is **Catalog**, US spelling) and the slice that
built it, `implemented/WORDING-SAVE-CATALOG-PUBLISH-IMPLEMENTED.md` (on its
branch until the owner's gate walk is done).

## What landed

Every control in your two tables now reads as you wrote it: Save on anything
that writes Resource Explorer's record, with "saved · who · when" after;
Catalog or Publish on anything sent to Egeria, with "published · read back
<when>" only once the read-back proves it and "sent · waiting for Egeria"
before; "Save entry" with the permanence sentence on the journal; "Publish to
Egeria →" and "Publish again"; "Run in Egeria →" on native surveys; the group
route records who saved. One stored question keeps its UK spelling
("catalogued") because its text is the key its answers hang on; a follow-up
gives questions a stable id so wording can change without orphaning answers.

## What the builder left alone

The builder mapped nothing that your tables did not name. These controls
exist on main today with their current words; each is a verb that is neither
Save nor Catalog nor Publish, or a control your tables did not list. We want
your word for each, or "keep", so that no page carries a verb nobody ruled.

| Control | Page | Current word | Why it was not mapped |
|---|---|---|---|
| Add member | Investigation scope | "Add" | adds a resource to the scope; a scope act, not a write of a record in your sense |
| Relink members | Investigation, Egeria-bound | "Relink members" | sends links to Egeria, but your table lists only Create and Sync for this panel |
| Unbind | Investigation, Egeria-bound | "Unbind" | destructive local act on the binding |
| Bind existing project | Investigation, not bound | "Bind existing project…" | attaches to an Egeria project that already exists |
| Reclassify | Investigation | "Reclassify…" | changes the Egeria classification |
| Promote | Work list | "promote N →" | a disposition act on list members |
| Export CSV | Work list, Investigation | "export CSV" | a download, nothing written anywhere |
| Add to investigation / Start from list | Work list | "add to investigation", "Start an investigation…" | scope acts in Resource Explorer |
| Run, re-run | Analysis rows, local | "run →", "re-run →" | local runs; your rule only says native surveys must name Egeria |
| Schedule and subscription controls | Automate | existing words | not in your tables |
| Re-check, remove | Doc sources | "re-check", "remove" | your "Not changed" section covers the choice; "remove" is now reserved for "Remove from Resource Explorer" |
| "Save, then catalog" combination | none | none | your rule for a control that does both; no such control exists today |

## The questions for you

1. **Relink members and Reclassify** both send to Egeria. Do they take
   "Publish" with a qualifier ("Publish links", "Publish classification"), keep
   their own verb with "to Egeria" appended, or stay as they are because the
   panel header already says Egeria?
2. **Unbind and Remove (doc sources)** are the two destructive verbs outside
   the commit path. "Remove from Resource Explorer" is now the reserved form
   for taking something out of RE's record. Should a doc source's "remove"
   become that form, and should Unbind say what survives ("Unbind · the Egeria
   project stays")?
3. **Scope acts** (Add, Add to investigation, Start an investigation, Promote):
   keep as acts with their own words, since they move things between lists
   rather than write facts? Our reading of your rule says yes.
4. **Run and re-run on local analyses**: keep, with the native-survey rows
   alone saying "in Egeria"? Or should local rows say "Run here" so the
   contrast is explicit on a page that shows both?
5. **Export CSV**: a download; keep as is, or "Download CSV" so "export" is
   not read as a publish to somewhere?
6. Anything in Automate's schedule and subscription controls that your rule
   should reach.
7. **The per-row choice words on the Curate scope table.** A press on a
   schema row's "catalogue" or "leave out" records a choice in Resource
   Explorer's record and writes nothing to Egeria until the commit button is
   pressed. At the gate today (2026-10-06) the owner read "catalogue" on a row
   as the write itself. The slice adds a result line after each press ("saved
   in Resource Explorer · who · when · not yet cataloged in Egeria") with the
   choice words unchanged, since your reply keeps choices in their own words.
   Should the row verbs change so they cannot read as the write ("Choose to
   catalog" / "Choose to leave out", or a checkbox column headed "Catalog?"),
   or does the result line carry it?

## Constraints

Reserved verbs stay Save, Catalog, Publish; "Remove from Resource Explorer" is
the reserved local removal. No new glyph. Each answer is one word or "keep";
a reason only where a page would otherwise show two verbs for one act.

## What we do with the reply

One small wording slice, gated by the owner on 8813 by reading the pages
named above; the implemented note gains a row per control.
