# Episode V4 — The stages: a funnel from cheap to costly: narration draft

Written part: [Part 4](../04-the-stages.md). About five minutes. Each `[SLIDE]` line names the image on screen while
the lines under it are read. This is a draft for the episode kit, not the final script.

---

[SLIDE: title — "The stages: a funnel from cheap to costly". Under it: "By the end, you can explain the path a data
source takes through the stages, and read an answer's mark."]

The next weekly drop sets Peter's deadline. Three sources need attention before then.
So the time has to go where it counts.

[SLIDE: S12 — the funnel]

Surveying a data source costs time, and it puts load on the source.
Counting a database's tables takes seconds. Profiling every column can take minutes.
So Resource Explorer puts its questions in six stages, from least costly to most.
Scouting is a fast first look. What is it, how big is it, is it alive?
Discovery works out what that first look already tells you, mostly without touching the source again.
Assessment judges the source against criteria, such as security and documentation.
Analysis looks at structure and numbers in detail.
Enrichment is what only a person knows, such as the owner.
And Curate is where you choose what to publish.
You go down the funnel only as far as you need. Most sources stop early.
The saving multiplies, because the costly stages run only on the sources that survive the cheap ones.
It is a guide, not a rule. When a later question is the one you need, go straight to it.

[SLIDE: S13 — a stage on coco pharma, plain]

Every stage has the same tabs.
Questions lists what the stage asks, and the answers so far.
Survey and analyses lists the surveys that answer them.
By analysis shows the results one analysis at a time.
And Disposition records your verdict on the source.

[SLIDE: S14 labelled — the overview and a narrower survey]

Each of the first four stages has an overview survey. It runs everything the stage can do, so it is the place to
start.
Narrower surveys run part of it, when you want only some answers, or want to keep the cost down.
Here, on the OpenLineage repository, Scouting Survey is the overview. Repo Scouting Scan is a narrower one.
The screen does not mark the overview yet, so the written tutorial has a table of them.

[SLIDE: S13 labelled — reveal callouts 2 and 3]

Back on the database, the Questions tab counts the answers, and the key sorts them by their mark.

[SLIDE: S15 — the marks]

Every answer carries a mark and a short word.
A tick means it was answered.
The mark for nothing found means it was measured, and there was nothing. That is an answer.
The open circle means not run. Nobody has looked yet.
Those two are never the same.

[SLIDE: S13 labelled — reveal callout 4]

Here, the question about what this database is for shows nothing found.
The screen says so: nothing is recorded, and this is a real absence, not an analysis nobody ran.

[SLIDE: closing — "Next: running the overview survey on coco pharma". "What can go wrong: see part 4 of the written
tutorial."]

Next, Peter runs the first survey on the trial's database, and reads what comes back.

---

**Pronunciation, beyond the outline's list:** none new. "coco_pharma @ local-docker" is not read aloud; say "the coco
pharma database".

**Slides still to make:** the title and closing slides. S13 and S14 each have a plain and a labelled version.
