# ASK — Designer: discovery sources for every resource kind, and CSV in and out (2026-10-01)

From the design session, for the designer session, at the owner's request.
Reply as `REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md` in this folder; a
drawing of the pane and of one candidate row per kind under `wireframes/`
is welcome. Background: the Backlog entry "Admin → Discovery Sources is
repo-only by name and by behaviour" (2026-10-01) and
`DESIGN-FIND-AND-INTEGRATE-PURPOSES-AND-THE-DATA-LENS.md` §2.1 and §7.

## The owner's direction, in one line

*Generalize it. Similar discovery is wanted for every resource kind, and a
CSV should be able to seed an investigation without searching.*

## What the code says

- Admin → Discovery Sources (`next/admin/discovery_sources.js`,
  `web/routes/discovery.py`) manages saved GitHub configs of two
  `source_type`s, `search` and `list`. "Run" is read-only and returns
  candidate `DiscoveredRepo` rows; a second step, `POST /discovery/import`,
  imports repositories after a confirm that names the count and the
  destination group. The pane made preview-then-apply the only path for
  anything that adds or removes repositories, and that rule is worth
  keeping for every kind.
- The `discovery_sources` table has `slug, display_name, source_type,
  config_json, created_at` and no link to `projects`; deleting a source
  removes only the saved config.
- Nothing in it can describe where databases or file systems come from. For
  databases, the nearest thing is server discovery: `connection.py`'s
  `server_connection()` lists the databases on a registered Postgres server,
  which is a "list" source in all but name, and registration is the import.
  For filesystems there is no discovery path at all.
- The Coco scenario (design note §7) is the driver: an acquiring company
  inherits a spreadsheet of systems from each acquisition and wants those
  systems as candidates in a Find investigation, not typed in one by one.
  The regional sales-forecasting databases will be created with synthetic
  data for the same demo, and a CSV is how that corpus gets registered.

## The questions for you

1. **One pane or one per kind.** Options to react to: (a) one pane with a
   kind selector, each source declaring its kind, one candidate table whose
   columns change with the kind; (b) one sub-pane per kind, each with its
   own source types and its own candidate row; (c) sources stay per kind
   but "Run" results land in one candidate list for the investigation,
   mixed. Which reads best to a steward setting up where to look, and to
   the person triaging what came back?
2. **What a candidate row carries, per kind.** A repository candidate shows
   name, owner, stars, language, last push. What does a database candidate
   show before it is registered (server, name, size, owner role, last
   activity, reachable or not), and a file system? The honesty rules hold: a
   fact the source didn't return is "not reported by this source", never
   blank. Which facts does a person need to decide "import this one"?
3. **CSV in.** A CSV of resources (and optionally of sources) imported to
   seed a work list or an investigation's scope. Is an imported row a
   candidate that goes through the same preview-then-import confirm as a
   "Run" result, or a direct import? What does the person see when the
   file's columns don't match the kind (unknown column, missing required
   one, a row of another kind)? What does re-importing the same file do:
   the design session's position is idempotent by key, the same resource
   never becomes a second row, and the screen says "12 already registered ·
   3 new".
4. **CSV out.** Export a source's candidates, a work list, or an
   investigation's scope to CSV, for sharing or for round-tripping into
   another tool. Which columns, and does the export carry the state marks
   (in scope, verdict, fit headline) as words, or only the resource facts?
5. **Where it lives.** Discovery Sources is under Admin today, which reads
   as system configuration. "Run a source and triage candidates" is a
   person's Scouting work, and "seed this investigation from a file" is an
   investigation-page act. Does the pane split, with sources configured in
   Admin and run from Scouting, or stay together?
6. **The name.** If the generalization lands, "Discovery Sources" is
   accurate again. Until then, should the label say "Repository discovery
   sources", so it stops promising databases it can't deliver?

## Constraints

Preview-then-import stays the only path that creates a resource, for every
kind. A candidate row uses the same state marks as everywhere else
(`glyphs.js`); no new glyph. The `discovery_sources` table can gain a
`resource_type` column; nothing else about storage is decided. The
work-lists reply (§2, "Add these N to <investigation>") is the model for
how a candidate set reaches a scope.

## What we do with the reply

Two slices: sources and candidates for databases first (the demo needs
them), then CSV in and out. Each gated on 8813 by the owner, task-based: a
person registers the regional databases from one CSV, sees them as
candidates with their facts, confirms, and finds them in scope of the
investigation, without typing a connection string.
