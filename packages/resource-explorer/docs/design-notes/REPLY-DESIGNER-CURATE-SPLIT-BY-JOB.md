# REPLY — Designer: splitting Curate (and Understanding) by the job a person is doing (2026-10-08)

*Wording follows the owner's decision of the same day: Publish is the one Egeria verb, and "Catalog" is retired from controls (`RULING-PUBLISH-NOT-CATALOG.md`).*

To ASK-DESIGNER-CURATE-SPLIT-BY-JOB.md, a proposal for reaction, read
against main at 62bcd4ad (`next/app.js` `SUB_TABS` and `subTabsHtml`,
`stages/curate.js` `CURATE_SECTIONS`, `stages/publish.js`,
`stages/curate-scope.js`, `stages/understanding.js`) and the coordinator's
control count. Drawing: `wireframes/SplitByJob.dc.html`, canvas page 20.
It shows the tab rail and tab 1 on a repository, the rail on a database,
and Understanding's rail.

## The answer to the owner

He's right, and the count shows it: 102 controls on a repository's Curate
and 48 on a database's, on one page. **Split by job, as the design session
proposes, into three. No mode.** The job split also turns out to be the
non-expert surface he asked about. Tab 1 is under 20 controls on either
kind, and a person can finish the most common job without opening the
other two.

## First: where the tabs go

/next already has two levels: the **stage strip** (Investigation …
Curate, Automate) and the **sub-tab rail**, which is identical on every
stage (Questions, Survey & analyses, By analysis, Disposition, plus
Schema Inventory on a database). Curate's pane is drawn into the
Questions slot today ("review and commit · Curate"). Adding tabs *inside*
that pane would make a third level of tabs, which is one level too many to
learn.

**The three jobs become Curate's stage-specific leading tabs on the
existing rail, following the precedent already set:** Enrichment's
**Context** tab leads the rail on that stage only, and Schema Inventory
appears only for a database. So on the Curate stage the rail reads:

> **Describe and publish** · **Contents · 3 chosen · 26 undecided** ·
> **Structure · 12 awaiting you** · Questions · Survey & analyses · By
> analysis · Disposition · (Schema Inventory)

On every other stage the rail is unchanged. "Tabs never disappear" holds
on two counts. The shared tabs keep their places. The three leading tabs
name things that exist only on Curate, so they're filtered elsewhere, as
Context is. The page-level jump line (`CURATE_SECTIONS`, six links) goes:
each tab holds two or three sections, which don't need a table of
contents.

## 1. Is three right?

**Three, cut as the design session proposes, with one move and shorter
names:**

| Tab | Holds (repository) | Holds (database) |
|---|---|---|
| **Describe and publish** | confirmed Context facts, read-only with "edit on Context ›"; Findable (tags, group); ratings; notes (the journal); **Publish to Egeria →** of the survey report; the Egeria reports list; Re-survey now | the same describe bands; the Egeria reports list; the **publish state, read-only** ("2 schemas published · last commit 10-07 · Contents ›") |
| **Contents** | what's in it (the selection tree, proposals, containers), what gets written, **Publish N items**, the steps list; **Forget Egeria links** and the project bind (moved, see §4) | the scope tree, the manifest, **Publish N schemas · ‹depth› · ‹facets›**, the steps list; Read Egeria again |
| **Structure** | what it is (the lines to confirm), what it's made of (components, verdicts), blueprints, how it relates (dependencies, confirmations), term and data-class confirmations, data-class rules | term and logical-schema proposals, data-class rules; dependencies, present and explained ("none detected for databases yet") |

**The one move:** "what it is" (the capability lines a person confirms)
goes to **Structure**, not Contents. It's a judgement about what the
resource *is*, confirmed against evidence, the same kind of act as a
component verdict. Contents is about *which parts* become assets.

**Why not four** (relations on their own)? On a database there are no
dependencies yet, and on a repository the dependency table is 5 controls.
A tab for 5 controls is a tab that's usually empty. If dependencies grow,
the split can come later. **Why not two?** "Everything that publishes parts or
confirms" is still 70 controls on a repository, which is the problem as
the owner stated it.

**Names, briefly:** "Contents" rather than "Publish the contents", because
the Egeria verb (Publish) belongs on buttons, not on a tab. A tab
name that's a verb reads as a press. That's the same lesson as the scope
row's "catalogue" at the gate.

## 2. Modes

**No mode. A saved landing, and nothing hidden.** A basic/advanced mode
either hides controls, and then a count somewhere disagrees with what the
person can see, or it hides nothing, and then it's just a landing tab.
The split already gives what a mode would honestly give: tab 1 is the
whole of the basic job, and the other two tabs are one click away, *with
their counts on the tab name*. "Structure · 12 awaiting you" is how a
non-expert learns something is waiting without having to understand it.

The one preference worth saving is **which tab Curate opens on** (tab 1
by default, remembered per person). A steward who publishes parts every day lands
on Contents.

## 3. A running commit on every tab?

**One line on every tab. The stepper only where it was pressed.**
Directly under the rail, on every Curate tab:

> ◔ **Publish · commit a3f2 · step 6 of 9 · running** · Contents ›

When it finishes, the line reads "✓ published · read back 10-08 09:14", and
it stays until the tab where it ran is next opened. This is the cue rule
again: the stepper sits where the decision was made. But a commit in
progress is a consequence that's still happening, and switching tabs must
not make it invisible. That's why it gets one line, never the steps.

## 4. Tab 1 as the seed of the non-expert tool

**Yes, with three controls moved out of it.** Tab 1 is exactly "what do we
say about this, and is it in Egeria". That's the whole job of the separate
tool. Three controls in today's Publish band don't belong to a
non-expert, and they move to **Contents**, beside the publishing plumbing
they belong to:

- **Forget Egeria links** (with its confirm). It's destructive, and it
  rebinds identity.
- **Bind or decline a project after a 428**, and the project-context
  choices. They decide where RE's elements live in Egeria.
- **copy GUID**. It's for someone debugging, and its value is shown in the
  detail anyway.

**Re-survey now** stays in tab 1: it refreshes what tab 1 describes.

**What a non-expert must not see**, rather than what's moved: nothing in
tab 1 should need tabs 2 or 3 to be understood. The confirmed Context
facts are shown **read-only** in tab 1, with their provenance ("owner ·
declared by dwolfson 10-02 · edit on Context ›"). Editing stays on Context,
where the testimony rules live. So tab 1 never becomes a second place to
enter facts.

## 5. Per kind

**The split doesn't assume a repository, but it shows one real asymmetry.**
For a repository, *publish the report* and *publish its parts*
(publish N items) are two presses. For a database they're **one press**,
the commit (Classic's Publish retired into it). So a database's tab 1
shows the publish **state**, with a link to Contents, and has no press of
its own. There's one press, in one place, and tab 1 carries its result.
A file system will look like a database (a scope of directories, one
commit), so it takes the database's shape.

## 6. What to measure first

**Yes, read it, but don't wait for it to draw.** A count of what the owner
pressed during two days of gate walks measures the gate script more than
use: he pressed what the gate asked him to. It's still useful in two ways.
A control **never pressed** in the walks is a candidate for the third tab
or a disclosure. And a control he **needed help to find** (the commit, the
Find dialog, remove) shows what the drawing must make obvious. Bring both
lists, pressed and needed help, and the drawing will move to match them.

## Understanding: the same shape, its own names (questions 7 and 8)

**7. One shape, learned once, with names that say each stage's job.**
Positions match across the two stages: **tab 1 is about the whole thing,
tab 2 is its parts, tab 3 is the detail behind it.** The names differ
where the jobs differ, and **share a word where the job is the same**:

| Position | Curate (decide) | Understanding (look) |
|---|---|---|
| 1 · the whole | Describe and publish | **At a glance** |
| 2 · its parts | **Contents** | **Contents** |
| 3 · the detail | Structure | **Over time** |

Both stages call position 2 "Contents". On Understanding it's what's
inside and how it's distributed; on Curate it's which parts become
assets. It's the same object seen for a different purpose. A person who
learns "Contents" once finds it in the same place on both stages.

What moves where in Understanding follows the ask's table, with one
change: **the changes-since-last-run sentence leads At a glance**, above
the tiles. It's the one thing that's different on this visit (the fifth-
visit rule from the scope reply).

**8. Yes, the two tab 1s are the chat-first application's surface**,
"about this resource". That changes one thing on each:

- **Understanding's At a glance** must read from the same records the
  chat answer cites, so the screen and the answer can't disagree.
- **Curate's Describe and publish** must show nothing that's RE-internal
  without an explanation: no outbox row ids or step names. Those stay on
  Contents.

## If the owner adopts it

One slice per kind, as the ask says, moving existing sections under the
tabs **without changing them**. The gate is by use: the owner describes and
publishes a resource from tab 1 alone, without opening the others. I'd add
one check: on a database, he finds the publish press from tab 1's state
line in one click.

## Left open

1. **The rail gets long on Curate:** three leading tabs plus four or five
   shared ones. In the drawing it wraps at the pane's width. If it wraps
   in the app at an ordinary window width, the leading tabs' counts move
   into a tooltip before any tab is shortened or hidden. Measure it in the
   slice.
2. **Questions on Curate.** Curate has drawn its pane in the Questions
   slot until now. Once the three tabs lead, Questions shows Curate's
   catalogued questions, if any are tagged to the stage. If none are, it's
   the shared tab with its existing empty sentence.
