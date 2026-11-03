# Find a new data source

This walkthrough takes you from a question ("I need data about X") to a dataset that is published in
Egeria. It uses open data: datasets that a public portal lists, such as `demo.ckan.org`.

You do not need to know how portals work. You need an investigation, a portal that someone has registered,
and about twenty minutes.

## Before you start

- **Marks used below.**
  - **[portal]** means the step calls an outside data portal over the internet. It reads only.
  - **[Egeria]** means the step writes to Egeria as you.
  - A step with neither mark stays inside Resource Explorer.
- **What you need.**
  - You are signed in.
  - An investigation exists. If not, make one first (guide section 3).
  - A portal is registered. An administrator does this in **Admin › Open data**. Section 8 of the guide covers it.
- **What is not in place yet.** Steps 6 and 7 (profile, publish) ship in the OD3 and OD4 changes. If your
  screen has no **Profile** or **Publish to Egeria** section, those changes are not on your server yet.
  Steps 1 to 5 work without them.

Every number you see has a "retrieved" time and says where it came from. Where a portal gave no value, the
screen says **not reported by this source**. That is not zero and not empty. It means the portal said nothing.

## 1. State what you need

Open your investigation. Scroll to **Data request**. It lists six things: subjects, period, places, grain,
license needs and formats. Each one says **not stated** until you fill it in.

1. Click **State it**.
2. Fill in only what you know. "Subjects: parking, Texas" is a good start. Period "2019-2023" is fine.
3. Click **Save**.

You now see your request in short form. Each dimension you left empty still says **not stated**.

**What can go wrong**
- **Save is greyed out.** The sentence beside it says the request's table is applied at the next restart.
  The server has not been restarted since that change. Ask the person who runs the server. Until then you can still search
  from the sidebar (step 2), typing the terms yourself.

## 2. Search a portal  [portal]

1. Click **Search portals for this request**. Or use the sidebar: choose the **Open data** chip, then **＋ Find open data**.
2. Pick a portal. Only portals Resource Explorer can read are listed. A portal marked **registered for later**
   has no reader yet.
3. The search form opens with **Terms** filled from your request. Change them if you like.
4. Look at the price line above the **Run** button. It reads like "2 pages × 1 call = 2 calls". It is what the search will cost
   the portal. Nothing has been sent yet.
5. Click **Run**.

You see a table of datasets. Each row shows the title, publisher, license, formats, size and last-modified date.
A field the portal left out says **not reported by this source**. A row whose license is unknown stays in the
table and says **could not check**. Resource Explorer never hides a row because information is missing.

**What can go wrong**
- **"Could not reach the portal."** The portal is down or blocks us. Try again later, or pick another portal.
  An empty table with this sentence is not the same as "the portal has nothing".
- **Zero rows, and no error.** The portal answered and has nothing for those terms. Loosen the terms.

## 3. Choose rows and import them

1. Tick the rows that look useful. Pick up to 25.
2. Name the work list. The box is filled with your request, so you can usually leave it.
3. Click **Import chosen**.

Each chosen row becomes a dataset in the **Open data** list in the sidebar. They are gathered into one work list.
A row shows **already in the catalog** when Egeria already holds that dataset (matched by its identifier). It is
not imported twice. If Egeria could not be asked, the row says **could not check Egeria** and is still imported.

**What can go wrong**
- **"One import takes at most 25."** Choose fewer. An administrator can change the cap.
- **A row says "refused".** The portal stopped answering for that one dataset. The sentence says why. Try it again alone.

## 4. Read a dataset page

Click a dataset in the sidebar. The page shows what the publisher stated and who stated it:

- a table of fields: publisher, license, the license flags (attribution, share-alike, commercial use), modified date,
  formats and size. Each row says who stated it and when it was retrieved, or that the portal gave no value.
- the identifier, and whether it is a DOI or the portal's own id
- who imported it, when, and into which work list
- under **The publisher's record, verbatim**: the portal's own record, unedited

A section that has not been run says **Nothing has been measured for this yet · never run**. That is a statement
about us, not about the data.

## 5. Scout the shortlist, then winnow it  [portal]

Below the dataset page is the **Shortlist** panel for the work list.

1. Read the **Scout** price line. It is "N datasets × 1 call = N calls".
2. Click **Scout**. Each dataset is read again from its portal. The line beside the button counts what was read and
   what could not be.
3. Fill in the form: formats, period, places, largest size, license needs. Leave blank what you do not care about.
4. Click **Winnow**.

You see one row per dataset with a cell for license, format, size, period and place. Each cell is one of
✓ fits, ✗ does not fit, ? could not check, – not stated. The last column is the verdict:

- **passes**: every dimension you stated fits.
- **dropped**: at least one stated need is not met. The row stays visible so you can see why.
- **open: nothing stated drops it**: nothing you stated excludes it, but some dimensions could not be checked.

Only a stated mismatch drops a row. A portal that says nothing about the period never drops a dataset.

The **Overlap** column says "may duplicate ..." when a dataset shares at least half its title and tag words with one you already hold. It compares only with
other open data held here. It does not yet compare with databases or file systems.

**What can go wrong**
- **Every row says "could not check" for period and place.** Most portals do not state coverage. That is real. Open
  the dataset page and read its description yourself.
- **Scout says some could not be read.** The dataset keeps the values it had. Run Scout again later.

## 6. Profile what is left  [portal]

*Available after the OD3 change is on your server.*

Profiling downloads a sample of each file and measures it. Use it on your shortlist, not on everything.

1. Open the **Profile** section of the Shortlist panel.
2. Read the price line. It says "up to N calls" and "each data file capped at 50 MB". A file larger than the cap is not read.
3. Click **Profile the shortlist**. It can take a while.

For each dataset you see its first three tabular files. Each shows:

- the number of rows read and the **sample ratio**. A ratio of 1 means the whole file was read.
- the column names
- whether a column looks like personal data (matched against Egeria's data classes)

A file over the cap says **not profiled · over the cap**. It is never shown as zero rows. A file in a format that is not
read says so. A file that could not be downloaded says why.

Documentation files (a data dictionary, a codebook, a citation, a license) are read as notes. Each note is marked
**stated by** its file. Treat a note as a lead to check. It is not a fact until a person confirms it.

The last lines say where the dataset came from and whether another profiled dataset has the same columns.

**What can go wrong**
- **"Egeria's data classes could not be read".** The column match could not run. The rest of the profile is still valid.
  The match rows say why.
- **"A profile of this list is already running".** Someone else pressed it. Wait, then reload.

## 7. Publish one dataset to Egeria  [Egeria]

*Available after the OD4 change is on your server.*

Publish only datasets you want kept in the catalog.

1. Open the dataset page.
2. In **Publish to Egeria**, read the price line, then click **Publish this dataset**.
3. Read the result. Each step is a line:
   - **asset**: created, or reused if it was already there
   - **origin**: the publisher is recorded
   - **scope**: the coverage is recorded, only if the portal stated it
   - **licence**: linked to Egeria's license entry, or kept as a property if Egeria has none for that id

**Published · read back by GUID** means Egeria answered with the same element when asked for it by its GUID. If it
says **Published in part**, one or more steps failed. The step lines say which. Press **Publish this dataset**
again: it picks up where it stopped and does not create a second copy.

**What can go wrong**
- **"Not permitted".** Egeria's zones do not let you create elements. Ask an administrator.
- **The dataset is in a private investigation.** Publishing is refused before anything is written. Private
  investigations cannot publish this kind yet.
- **Origin or scope says "not written".** The portal gave no publisher or no coverage. Nothing was invented.

## 8. Look again later  [portal]

*Available after the OD4 change is on your server.*

Portals change. Open the **Look again** section of the Shortlist panel.

1. Read the price line. It is two calls per dataset.
2. Click **Look again at the shortlist**.

Each dataset says one of:

- **no change on their side**
- **changed on their side**, with a list: "modified: before → after", "distribution added: name", and so on
- **first look**: nothing earlier to compare, so it will not claim "no change" about the files
- **could not be read**: the portal did not answer. The earlier values are kept.

Look again after a day or a week. A dataset that changed after you profiled it needs profiling again.

## Where each step runs

| Step | Needs a portal | Needs Egeria | Writes anything |
|---|---|---|---|
| 1 State the request | no | no | your request, in Resource Explorer |
| 2 Search | yes (read) | no | no |
| 3 Import | yes (read) | read only | datasets and a work list, in Resource Explorer |
| 4 Read a page | no | no | no |
| 5 Scout, winnow | scout: yes (read) | no | refreshed values, in Resource Explorer |
| 6 Profile | yes (downloads) | read only | the profile, in Resource Explorer |
| 7 Publish | no | yes, as you | the Egeria asset and its proof |
| 8 Look again | yes (read) | no | the new look, in Resource Explorer |

## If something looks wrong

- **A number looks too good.** Open the dataset page and check "retrieved" and the portal that stated it.
- **A row you expected is missing.** Missing information never removes a row. Look in the **dropped** rows, and in step 3's list of refusals.
- **You want to stop.** Nothing is published until step 7. Remove the work list and the datasets stay out of the catalog.
