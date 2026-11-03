# Ask for the designer: scheduling many repositories, and asking before a first index (2026-10-19)

From the RE coordinator session, for the designer session. Please reply as `REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19.md` in this folder. The
other ask in this PR covers table previews; answer them separately or together.

**Decision (project owner, 2026-10-19):** "leave bulk scheduling for a designer ask." Earlier ruling (2026-10-18) on
first indexing: "the analysis step should create it but ask first with a cost estimate ... UI should say Not Yet
Indexed — and give them a link to where we would execute it ... a bulk add queue indexing could work."

## What a person sees today (measured 2026-10-19)

- **Where scheduling lives:** Automate → ⏱ Schedules lists every schedule (resource, analysis, cadence, state, last run,
  next run), with "run now" and "remove". The "Add a schedule" form appears only after a resource is picked in the
  sidebar. Otherwise the tab shows one muted line, and the project owner looked for it and missed it. The other entry
  points are a ⏱ action on a question row and one in chat.
- **One resource at a time:** 73 repositories are registered, and 6 are on a daily refresh. Scheduling the rest means
  repeating the form for each one. Groups exist (Admin), but nothing schedules by group.
- **Silent failures:** a schedule that failed (`coco_ods index_health`, error on its last run) shows as a row state
  only, and nothing reaches the person.
- **Never indexed:** 42 of 73 repositories have never been indexed for chat. A refresh cannot create the first index.
  A builder is now adding a "not yet indexed" cue, with placeholder words, and a hook where the first-index action will
  go. The action itself does not exist yet.

## Questions

1. **Finding it.** Where should "put this on a schedule" live so it's found without first picking a resource? Options:
   - the form always visible, with a resource picker inside it;
   - an action on each sidebar row;
   - a "schedule…" action on a Group.
2. **Many at once.** What does scheduling many repositories look like? By Group, by a selection in the sidebar, or
   both? Should the Schedules table then show one row per repository, or one per (group, survey), expandable?
3. **Mixed cadences.** A Group schedule meets a repository that already has its own schedule for the same survey. Which
   wins, and how does the table show it?
4. **First index, asked first.** The owner wants a cost estimate before indexing.
   - What does the ask show: chunk estimate, time, disk, and whether it fetches?
   - Is it one confirm per repository, or one confirm for a queued batch, with progress shown where?
   - Where does the "not yet indexed" cue's link take the person?
5. **Failure reaching a person.** Should a failed scheduled run raise something (an RFA in the drawer, a cue on the
   resource, or both), or is the Schedules row enough?
6. **Words.** Please confirm or replace the builder's placeholders: "not yet indexed" and "chat can't search this
   repository's own content yet".

The answers shape one builder round for group scheduling and one for first indexing with consent. Nothing is built
against these questions until you reply.
