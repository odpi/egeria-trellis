# Episode V1 — What Resource Explorer is for: narration draft

Written part: [Part 1](../01-what-resource-explorer-is-for.md). About five minutes. Each `[SLIDE]` line names the image
on screen while the lines under it are read. This is a draft for the episode kit, not the final script.

---

[SLIDE: title — "What Resource Explorer is for". Under it: "By the end, you can say what Resource Explorer is for, and
when to use it."]

You are Peter Profile, an information analyst at Coco Pharmaceuticals.
Coco Pharmaceuticals is a made-up company. It lives in the Egeria Quickstart, a demonstration environment.
Nothing in it is real, and nothing in it is permanent. So you can change anything while you learn.

[SLIDE: the story — Peter, Erin, the three hospitals, the three sources]

The Teddy Bear Drop Foot trial is running.
Three hospitals send in measurement files every week.
The files land in a folder called loading bay. From there, they are loaded into a database called coco pharma.
The trial's data is in good order. When a hospital finds an error, it sends that week again, and the original is kept.
What is new is an auditing requirement.
An outsider must be able to see that the data is in good order, without asking the team.
So Erin Overview, the information architect, asks you to make the trial's three sources ones the catalog can vouch for.
The database. The drop folder. And OpenLineage, an open-source project the team is thinking of using to record how
files move from the folder to the database.
And she wants it before the next weekly drop.

[SLIDE: the same, with "catalog everything?" across it]

Your first thought might be to catalog everything the trial touches.
That is the wrong first move.
A catalog is built for a purpose, and then the organization moves on.
Put everything in "just in case", and it fills with noise and stale entries. People stop trusting it.

[SLIDE: S01 — look first, catalog what proves worth keeping]

So Resource Explorer keeps two things apart: looking at a data source, and cataloging it.
To survey a source, Egeria needs to record only where it starts. A database server. A root folder. A repository's
address.
The survey's checks measure the source, cheap ones first.
Each answer shows the measurement it came from, so you can check it rather than trust it.
You add what only you know, such as who owns it, and how sensitive it is.
Then you decide what goes to the catalog, and why.
Nothing reaches Egeria until you press Publish.
Most sources stop early, after a cheap look. Only the ones worth it go further.

[SLIDE: the funnel — tutorial-media/funnel.png]

Your questions decide what runs.
The stages put those questions in order, from cheap and broad to slow and deep.
That order is called the funnel. It lets you set most sources aside early.
It is a guide, not a rule. When a later question is the one you need, go straight to it.

[SLIDE: Assessment question cards, tagged by perspective — blog image, to be checked against the screen]

The same source matters differently to different people.
Each question is tagged with the perspectives that care about it, such as Security, Architecture, or Data Owner.
You can filter by them. A perspective is a filter, not a job title.

[SLIDE: S01 again, the chat box highlighted]

Beside every stage is a chat panel.
Ask in your own words. The answer is built from what the surveys stored, and it names the results it used.
When nothing stored can answer, it tells you which survey would.

[SLIDE: three promises]

Resource Explorer keeps three promises.
Nothing reaches Egeria until you press Publish.
Nothing found, and not looked at yet, are always shown differently.
And every answer can show where it came from.

[SLIDE: closing — "Next: signing in, and a tour of the screen". "What can go wrong: see part 1 of the written
tutorial."]

Resource Explorer is still experimental, and it changes with what its users ask for.
Use the Feedback button on any screen to tell us what you need.
Next, you sign in as Peter, and find your way around the screen.

---

**Pronunciation, beyond the outline's list:** none new in this episode.

**Slides still to make:** the story slide, the "catalog everything?" slide, the three-promises slide, and the title and
closing slides. The perspective slide waits for a capture of the real screen, signed in as Peter.
