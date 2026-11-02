# Episode V3 — Adding a database, a file system and a repository: narration draft

Written part: [Part 3](../03-add-data-sources.md). About six minutes. Each `[SLIDE]` line names the image on screen
while the lines under it are read. This is a draft for the episode kit, not the final script.

---

[SLIDE: title — "Adding a database, a file system and a repository". Under it: "By the end, you can register each kind
of data source, and find it again."]

Erin named three sources. The trial's database, the folder where the hospitals' files arrive, and OpenLineage.
Before Resource Explorer can look at any of them, Peter registers each one.
Registering tells Resource Explorer where a source is. It sends nothing to Egeria.

[SLIDE: S06 — the Register one database form]

Start with the database. In the sidebar, click DBs, then Find databases, then Register one database.
Give it a slug: a short name with no spaces, used in addresses. Here, coco pharma.
Then the host, the port, the database's name, and a read-only user and password.
Why read-only? The first stages read only the database's own catalog: its tables, columns and keys.
Resource Explorer asks for more only when a later step needs to read rows, and it says why.
The password is stored encrypted, and never shown again.
Click Test connection. Once the test has passed, click Register.

[SLIDE: S07 — registered]

The screen says it is in the database list now, and that nothing was sent to Egeria.

[SLIDE: S08 — never surveyed]

Open the database. It reads never surveyed, and not published to Egeria.
Most of its questions show the mark for not run. Nobody has looked yet.
The next episodes change that.

[SLIDE: the Add a file system form — to be captured after the server path is changed; see gap G25]

Next, the drop folder. Click FS, then Add a file system.
Give it a slug, loading bay, and the folder's path on this server. Resource Explorer checks that it can read it.
Then the canonical mount: where Egeria sees the same folder, inside its container.
That way, Egeria's own survey can read it later.
Click Register.

[SLIDE: S10 — OpenLineage, from a list]

Then the repository. Click Repos, then Find repos, then From a list.
Paste OpenLineage's GitHub address, and load the list.
Tick the row, and click Import Selected.
On this Quickstart, OpenLineage was already registered, so Peter uses the one that is there.

[SLIDE: S11 — the filter]

To find any of them again, type in the filter box under Repos, DBs or FS.

[SLIDE: S11b — Find by passages]

File systems can also be found by what their own files say.
Right now the new folder reads not read yet, not searched. That is not the same as no match.
Its files are read in a later episode.

[SLIDE: closing — "Next: the stages, a funnel from cheap to costly". "What can go wrong: see part 3 of the written
tutorial."]

Three sources, registered. Nothing sent to Egeria yet.
Next, Peter decides where to spend the time before the next drop.

---

**Pronunciation, beyond the outline's list:** none new. "coco_pharma_trial" and "loading_bay_trial" in the
screenshots are not read aloud; say "coco pharma" and "loading bay".

**Slides still to make:** the title and closing slides, and the Add a file system slide, which waits on a capture
whose server path names no real account (gap G25).
