# Part 1 — What Resource Explorer is for

You are Peter Profile, and Erin Overview has asked you to make the Drop Foot trial's three data sources ones the
catalog can vouch for, before the next weekly drop. Your first thought might be to catalog everything the trial
touches. This part explains why that is the wrong first move, and what to do instead.

**Before you start.** Nothing. This part has no steps. It takes about ten minutes to read.

## Where you work: the Egeria Quickstart

This tutorial runs in the [Egeria Quickstart](glossary.md#egeria-quickstart), a demonstration environment. It is set up
around [Coco Pharmaceuticals](https://egeria-project.org/practices/coco-pharmaceuticals/), a fictitious company.

- The Quickstart's users, such as Peter Profile and Erin Overview, are made-up people at Coco Pharmaceuticals. None of
  them is a real person.
- Nothing in it is permanent. You can change, publish and delete freely while you learn. Nothing you do touches real
  data.
- Every screenshot was taken on the Quickstart, so your screens should match closely.

There are two ways to reach it:

- **The hosted demo** at egeria.pdr-associates.com. Register for free; there is nothing to install. Its data is reset
  every day, so work from one day is gone the next. It is sometimes a little behind the latest code, so a screen may
  differ slightly from a screenshot.
- **Your own Egeria Workspaces**, from the egeria-workspaces repository. You install and configure
  [Resource Explorer](glossary.md#resource-explorer) in a separate step. The Getting Started tutorial covers both.

## The story you follow

You are [Peter Profile](https://egeria-project.org/practices/coco-pharmaceuticals/personas/peter-profile/), an
information analyst at Coco Pharmaceuticals. You sign in as Peter, so the screen shows your name.

The Teddy Bear Drop Foot clinical trial is running. Three hospitals, Oak Dene, Old Market and Hampton, send weekly
measurement files. The files land in the `loading_bay` folder and are loaded into the `coco_pharma` database. The
trial's data is in good order. When a hospital finds an error, it sends that week again, and the original is kept.
You will see those resent weeks as files ending in `.restatement.csv`.

What is new is an auditing requirement from the regulator. An outsider must be able to see that the data is in good
order, without asking the team. Each reported figure must trace to the file it came from, and a file that was replaced
must be visibly retired.

[Erin Overview](https://egeria-project.org/practices/coco-pharmaceuticals/personas/erin-overview/), the information
architect, asks you to make the trial's three sources ones the [catalog](glossary.md#catalog) can vouch for, before the
next weekly drop:

- **the database**, `coco_pharma`, where the figures are held;
- **the drop folder**, `loading_bay`, where the hospitals' files arrive;
- **OpenLineage**, an open-source project the team is evaluating to record how files move from the drop folder to the
  database. It is a candidate tool, not a data source, so it gets its own parts later (11 and 12).

Three people can help you. Tessa Tube is the lead researcher. Callie Quartile is a data scientist who reuses the data.
Tanya Tidie is the trial's data steward, accountable for where the data came from. All of them are Egeria's
[Coco Pharmaceuticals characters](https://egeria-project.org/practices/coco-pharmaceuticals/personas/), and all of them
are fictional.

## One situation, among many

Here is a common situation. A department has eighty databases nobody has written down, a few hundred internal
repositories, and file shares that have grown for a decade. None of it is in a catalog. Someone is asked which of it
matters for a new project, such as an AI project, a migration or a report. They have a week.

That is one example. Others are checking a source the catalog already holds, judging an open-source project before
you adopt it, or finding out what is on a file share.

Peter's task has all three of those in it. The `coco_pharma` database may already be in the catalog. The `loading_bay`
folder is not described there yet. OpenLineage is an open-source project to judge before the team adopts it.

## Why a catalog alone does not solve it

A catalog is built for a purpose. Then the organization moves on: new priorities, new platforms, acquisitions. If you
put everything into the catalog "just in case", it fills with noise and stale entries, and people stop trusting it.

So cataloging everything the trial touches would not help Erin. It would bury the three sources that matter among
many that do not.

## Look first, catalog what proves worth keeping

The core idea is that looking at a data source is separate from cataloging it.

To survey a data source, [Egeria](glossary.md#egeria) records only where it starts: the database server, the root
folder of a file system, or the repository's address. Egeria does not have to keep the details or the findings. So the
catalog knows what has been looked at. If that is all it knows about a source, that is useful too: it tells you that
more was not worth keeping at the time.

You catalog the details only when they prove worth keeping.

![A diagram titled "Look first. Catalog what proves worth keeping." Inside a box labelled Resource Explorer, five steps run left to right: Data source (a database, a folder, a code repository); Survey (checks measure it, cheap ones first); Answers (each one shows the measurement it came from); What you know (owner, sensitivity, intended use); Curate (you decide what goes to the catalog, and why). Below them, "Ask in chat, at any point". A Publish arrow crosses into a box labelled Egeria, the shared catalog, holding "What you published". The footer reads "Most data sources stop early, after a cheap look. Only those worth it go further, and only what you choose is published."](images/01-what-for/S01-look-then-catalog.webp)

*Peter's three sources will take this path. Everything before Publish stays in Resource Explorer.*

## What Resource Explorer does

[Resource Explorer](glossary.md#resource-explorer) does three things:

- You can look at a [data source](glossary.md#data-source-resource) without cataloging its details first.
- Every [answer](glossary.md#answer) shows the measurement it came from, so you can check it rather than trust it.
- What you decide, and why, is recorded. It goes into Egeria when you decide something is worth keeping.

The screen says "resource" where this tutorial says "data source". They mean the same thing: a
[database](glossary.md#database), a [file system](glossary.md#file-system) or a code
[repository](glossary.md#repository).

## Your questions decide what runs

You choose which [surveys](glossary.md#survey) and [analyses](glossary.md#analysis) to run by the
[questions](glossary.md#question) you want answered. A survey is a set of analyses run together on one data source.
An analysis is one check that measures something, such as counting tables.

The [stages](glossary.md#stage) put the questions in order, from cheap and broad to slow and deep. This order is called
[the funnel](glossary.md#the-funnel), and part 4 explains it. It lets you set most data sources aside early, after a
cheap look. The funnel is a guide to working efficiently, not the only path. When a later stage's question is the one
you need answered, you can go straight to it.

## Many points of view

The same data source matters differently to different people. On the screen, each question is tagged with the
[perspectives](glossary.md#perspective) that care about it, such as Security, Architecture or Data Owner. You can
filter the questions by perspective. A perspective is a filter on the questions, not a job title, and one person may
use several.

The questions are not fixed. Questions, analyses and surveys can be added or removed. The Advanced tutorial shows how.

## How it fits with Egeria

[Egeria](https://egeria-project.org/) is an open-source metadata catalog: the shared record of what data an
organization has.

Resource Explorer is a tool for exploring what Egeria does not know about yet, and for updating Egeria with the things
worth keeping. It can also explore what Egeria already knows about, as those things change. Either way, your needs and
your curiosity guide it. It records what you learn, and lets you store and share it.

While you explore, Resource Explorer keeps its working results in its own store. That store is not a second catalog.
Running the surveys again rebuilds it. Egeria holds what you [publish](glossary.md#publish). Resource Explorer always
reads from and writes to Egeria as you, under your own user id.

## Who uses it

Each kind of user comes with a different first question:

- **Data scientists, including AI builders:** Which data can I rely on, and what is in it?
- **Data engineers:** What is this source, how big is it, how is it built, and what depends on it?
- **Curators:** What is worth cataloging, and is it described well enough to be found and reused?
- **Security specialists:** Does it hold secrets or known vulnerabilities, and who can read what?
- **DevOps engineers:** Is it maintained, how is it deployed, and how fast is it changing?
- **Architects:** What is it made of, and how does it fit with the rest?
- **Developers:** What interfaces and dependencies does this code have, and is it healthy?

## Ask in plain words

Beside every stage is a chat panel. Ask a question nobody planned for, or a follow-up to an answer, in your own words.
The answer is built from what the surveys have stored, and it names the stored results it used. When nothing stored
can answer, it says which survey would. Part 8 shows how.

## Three promises the screen keeps

- Nothing reaches Egeria until you press **Publish**.
- "Nothing found" and "not looked at yet" are always shown differently. One means somebody measured and there was
  nothing. The other means nobody has looked.
- Every answer can show where it came from.

## Still experimental, and growing

Resource Explorer is experimental. It is changing in response to what its users ask for, so screens and surveys
change from release to release. Some things are marked as not built yet. Your feedback shapes what comes next: use the
**Feedback** button at the bottom right of every screen.

## What you will be able to do at the end

1. Say what Resource Explorer is for and when to use it.
2. Sign in and find your way around the screen.
3. Add a database, a file system and a code repository, and find each one again.
4. Explain the path a data source takes through the stages: cheap, quick questions first, then costlier ones only
   where they are worth it.
5. Run a stage's overview survey, or a narrower one, and read the answers. Tell "nothing was found" from "nobody has
   looked yet".
6. Add what only you know, such as an owner and how sensitive the data is.
7. Ask a question in plain words, and check which stored results the answer came from.
8. Decide what to send to the Egeria catalog, send it, and check that it arrived.
9. Compare several data sources side by side in a work list.
10. Start an investigation, and say how it differs from a work list.

The whole tutorial takes about two and a half hours, in parts of 10 to 25 minutes. Each part ends at a natural place
to stop. A sign-in lasts about an hour, so you will be asked to sign in again at least once.

**Not covered here.** Installing and configuring Resource Explorer is in the Getting Started tutorial. Automate,
adding your own analyses, questions or surveys, and Admin are in the Advanced tutorial. So is the command line. Part 13
points to each.

**Next:** [Part 2 — Sign in and find your way around](README.md#the-core-tutorial). Peter gets in.
