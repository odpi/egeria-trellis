# Handoff — the PR / CI / merge session (2026-09-28, 08:05)

You are taking over the mechanical half of the Resource Explorer coordination
thread: verifying agent tips, opening PRs, polling CI, merging in order,
fast-forwarding the serving checkout, and relaying between the coordinator
session and the design session. The design session (Fable) keeps the
judgement half: briefs, gates, screenshot verdicts. Do not merge those two
roles into one session; the value of the last two days came from a second
pair of eyes on every agent's claim.

Read first: `BRIEF-KEYS-AND-ACTIVITY-CLOBBER.md` (sections A–E, all landed
except E), `BRIEF-BY-ANALYSIS-PANEL-USABILITY.md`,
`REPLY-DESIGNER-ROUND2-DATABASE-SCREENS.md` (sets the order for the G
slices), and the memory files under
`~/.claude/projects/-Users-dwolfson/memory/` whose index lines mention
git hygiene, unattended auth, batch-to-branches, and "verify the surface
the user reads".

## Who is who

| role | where | how to reach |
|---|---|---|
| project owner (Dan) | this desktop app | plain chat; approves 1Password prompts, runs gates on a second port |
| coordinator session | "Resource Explorer experimental UI", id `local_4a12e0c4-df9c-422e-8ede-9c7849a04dcc` | `mcp__ccd_session_mgmt__send_message`; it relays to its subagents, so start every message with "For the <section> agent (<id>):" |
| design session (Fable) | the thread this note came from | the owner relays, or a new design session reads the docs |
| designer session | writes `REPLY-*.md` and `wireframes/*.dc.html` on disk | the owner hands it `ASK-*.md` files |

## Rules that bite

- **The serving checkout** `~/localGit/egeria-v6/trellis` is only ever
  fast-forwarded to `origin/main`. Never `git checkout` a branch there,
  never edit there, not even docs. Port 8810 serves from it. Check
  `git -C ~/localGit/egeria-v6/trellis rev-parse --abbrev-ref HEAD` is
  `main` before and after each agent round; it was found on a branch once
  last night.
- **Every commit is signed and DCO'd** (`git commit -s`). Signing and `gh`
  writes go through 1Password and hang or fail when Dan is away
  ("authorization timeout", "failed to fill whole buffer"). Never bypass
  with `--no-gpg-sign`; never tell an agent it may. Read CI via
  `gh api …/commits/<sha>/check-runs` when Dan is present, and via
  unauthenticated REST (`curl https://api.github.com/repos/dwolfson/trellis/…`)
  when he is not — that one is capped at 60 requests/hour, so poll every
  90 s at most and one branch per loop.
- **Merge only** a PR whose head equals the branch tip, whose `test`
  check is `success` on that head, and whose `git merge-tree --write-tree
  origin/main <head>` prints no CONFLICT, in the order the design session
  set. `gh pr merge N --repo dwolfson/trellis --merge` (never bare `gh`
  without `--repo`; the checkout's upstream is odpi/egeria-trellis).
  Then `git fetch https://github.com/dwolfson/trellis.git
  main:refs/remotes/origin/main` and `git merge --ff-only origin/main` in
  the serving checkout, and report `origin/main` SHA plus parents.
- **Agents never open PRs**; they push and report a tip. You verify the
  tip against origin before doing anything with it. Agents never touch
  another agent's files without asking; you answer overlap questions
  from `git merge-tree` against every open branch, not from memory.
- **Gates** are the design session's to set and Dan's to run. When a
  branch needs a screen check, put it on port 8813 from a clean worktree
  (`git worktree add .claude/worktrees/wt-<name> origin/<branch>`, add a
  `~/.claude/launch.json` entry `re-wt-<name>-8813` with
  `bash -c "cd <worktree> && uv run --package resource-explorer
  resource-explorer web --port 8813"`, `preview_start` it), confirm
  "Application startup complete" in `preview_logs`, then hand Dan the
  gate text the design session wrote. Tear it down after.
  *Superseded in part, 2026-09-30; see the next three rules.*
- **A gate server runs under the same configuration as 8810.** `config.py`
  loads `.env` from the directory of the package it imports, and a worktree
  has none. Until 2026-09-29, every gate on 8813 therefore ran without main's
  `PREFECT_*`, `GITHUB_TOKEN`, `TRELLIS_JWT_SECRET` and `TRELLIS_PORTAL_SECRET`:
  Prefect was off, and the owner had to sign in separately. The launch command
  now loads main's file explicitly:
  `cd <worktree> && uv run --package resource-explorer dotenv -f
  /Users/dwolfson/localGit/egeria-v6/trellis/packages/resource-explorer/.env
  run -- resource-explorer web --port 8813`. One reused gate worktree,
  `.claude/worktrees/wt-relgraph`, is checked out detached at each verified
  tip. Its name is historical and says nothing about what is checked out.
- **Every serve report carries three literal lines** read from the live
  process, and a gate does not start until they match the intended tip:
  - the worktree HEAD, from `git -C <cwd> rev-parse HEAD`;
  - the process cwd, from `lsof -a -p $(lsof -ti tcp:8813 -sTCP:LISTEN) -d cwd -Fn`;
  - the env key NAMES present in the process, never values.

  This rule came after an agent read the worktree's name as proof that the
  wrong branch was being served, which briefly voided a day of gate results.
  RE has no version endpoint yet; it is logged in Backlog.md.
- **The desktop app stops preview servers on its own.** On 2026-09-29, 8813
  was stopped by the app about an hour after launch, and it was reported as
  serving for the next 11 hours. Check the process is live
  (`lsof -ti tcp:8813 -sTCP:LISTEN`) before stating serve state.
- **Never restart a gate server while an Egeria survey run is in flight**
  (about 14 minutes). The gate item "the row moves to complete on its own"
  means nothing if the server changes mid-run.
- **Unsigned commits are held, never merged.** Check each commit for a
  `gpgsig` header (`git cat-file commit <sha>`), not only a DCO trailer. An
  agent's commits once carried `Signed-off-by` but no signature. The branch
  was re-signed with `rebase --exec 'git commit --amend --no-edit -S -s'`
  and force-pushed; verify that the tree is unchanged with
  `git diff <old> <new>`. A one-line check for a whole branch is
  `git log --format='%G? %h' base..head`, which must show `G` on every
  commit. Three commits reached a PR on 2026-09-30 with the DCO trailer and no
  signature, and the PR was held until they were re-signed.
- **Agents test in their own worktree, after printing the import path.** Run
  `uv sync` there (or set PYTHONPATH to the worktree's packages), then
  `python -c "import resource_explorer; print(resource_explorer.__file__)"`
  and put that line in the implemented note. The shared `trellis/.venv`
  resolves the package to main's checkout, so a suite run without that line
  tested main's code with the branch's tests.
- **Anything that opens the shared registry migrates it, dry runs included.**
  Do a peer check first, or point the command at a scratch database.
- **The owner sometimes merges on green before the gate.** That is the
  owner's call. Report it as "merged without its gate", never as "passed",
  and keep the unwalked gate items listed until they are reported.
- macOS has no `timeout` binary; a wrapped command silently never runs.

## State at handoff

origin/main = `bf1c2f33` (G1 merged). Serving checkout matches. 8810 is up
on it (restart it after the next merges so the code follows). 8813 is up
on G2's tip 3979bbd7 from `.claude/worktrees/wt-g1` (detached), waiting for
Dan's one-row re-check once G2's fix lands.

Merged this morning, in order: #323 cache fix → #319 B → #320 D → #321 C →
#324 F → #325 G3 → #326 doc branch → #329 G1.

### Open PRs

| # | branch | head | CI | what to do |
|---|---|---|---|---|
| 327 | re/prefect-prerequisite-resolution | 3f746393 | **red again** (run 36420724638, 08:19) although this head contains the cache fix, so the flaky trio no longer explains it. The E agent (ad8f5b0a) is running `-rf` on this tip; suspect a semantic interaction with B/F/C merged this morning, or a test that needs the live Prefect server. Do not merge until green on a head that contains main. | Section E. No gate (in-process live run recorded in its IMPLEMENTED doc). |
| 328 | re/table-count-rename | 36a5964f | **green** (08:21) | Rename 157 → relation_count with old keys read as fallback. **Ready to merge now**: head 36a5964f, no conflicts vs bf1c2f33 as of 08:05 (re-check with merge-tree), no gate. Independent of E. The design session's Bash was blocked by a transient classifier error at 08:25, so it did not merge this; it is the new session's first merge. |
| 330 | re/g2-questions-headline-slot | 3979bbd7 | in progress | G2. **Do not merge yet**: Dan's gate found the answer line shows the lead analysis's whole paragraph. The G2 agent (a5c353d5) is adding a first-sentence rule in envelope.js with cross-check cases; it will push a new tip. Then: verify, re-poll, restart 8813 on it, ask Dan for a one-row re-check ("the fit row on adventureworks reads exactly one sentence starting NO REQUIREMENT DECLARED, marked ⚠; 'How big is this database' still reads its full single sentence"), merge on pass + green. |

### Branches without a PR yet

| branch | tip | what |
|---|---|---|
| re/coordinator-backlog-patch | adbf6085 | one more Backlog entry (Curate stage slow to load on repos; timing capture requested). CI in progress. Open a doc-only PR, merge on green. The coordinator keeps adding Backlog entries here; one doc PR per batch. |

### Dispatched, not yet reported

- **notify-me fix** (any free agent): the dialog posts `entity_type: "repo"`
  for every resource; automate.py:86 rejects databases. Client sends the
  real type; route validates per type; dialog gains an inline "schedule
  this analysis" action and states both delivery paths (local RFA always;
  Egeria RFA only when published, via rfa_egeria_sync). Independent
  branch off main; tip to you.

### Queued (design session has written the briefs; not started)

1. The By-analysis panel slice: `BRIEF-BY-ANALYSIS-PANEL-USABILITY.md`
   plus designer reply §2 (band not box; contents-board rows truncate at a
   sentence boundary; server returns `{lead, parts}`; `preliminary_fit`'s
   0 is not a confidence; "table count" is two populations) plus the
   progressive-render requirement (contents board renders immediately,
   cards fill in). Starts after G2 merges; uses glyphs.js and envelope.js.
   Gate: timed tasks on both databases, text in the brief.
2. The tree slice: reply §3.1 (level heading from declared containment,
   kind word only on table rows), §3.2 (in-row bars: columns always,
   rows when readable; four bar states), §3.4, §3.5. Depends on G3 (merged).
3. The header slice: reply §4.2–4.4 (two states, glyph carries state,
   remedy is the only accent; publish remedy links to classic
   `POST /api/databases/{slug}/publish`; broader-account remedy links to
   the capability RFA; "no full survey yet · N analyses run individually").
4. Small: `/next` database publish action; the scouting-overview 404 for
   database slugs; the cross-cutting dispatch-level `status: "ok"` (Section
   F's doc names it).
5. Larger, from the coordinator brief: slice 19 scope model (must render
   whatever containment levels the engine declares), then 20, 26, 23, 24,
   25. Phase 2 stays held.

Gates for 1–3 need the design session or Dan; ask before merging any of
them.

## Registered resources and ports

- `localhost_docker_coco_pharma` (egeria-shared-postgres, 5442; credential
  `surveyor`, sees 6 of 8 schemas, SELECT on 3 of 61 tables).
- `laz_local_adventureworks` (native Postgres.app 16 on localhost:5432,
  database `adventureworks`, user `dwolfson`, full visibility; psql at
  `/Applications/Postgres.app/Contents/Versions/16/bin/psql -p 5432 -U
  dwolfson -d adventureworks`, trust auth on the socket). Every database
  gate runs on both.
- RE registry: `egeria_advisor` on 5442, schema `resource_explorer`
  (`docker exec egeria-shared-postgres psql -U postgres -p 5442 -d
  egeria_advisor`).
- Egeria dev platform: writes are ordinary shared writes; announce them in
  the IMPLEMENTED doc with a timestamp.

## Worktrees to clean when their branch merges

`.claude/worktrees/wt-clobber` (B, merged), `wt-gap-guard` (C, merged),
`wt-renders-text-cross-check` (D, merged), `wt-g1` (serving 8813; keep until
G2 is done), `~/localGit/egeria-v6/trellis-g1-glyphs`,
`trellis-re-empty-split` (merged), `trellis-re-g2-headline`,
`trellis-re-table-count-rename`, `~/localGit/egeria-v6/worktrees/
column-profile-fix` (merged), `worktrees/prefect-prereq`. `git worktree
remove <path>` only after `git status --porcelain` is empty there and the
branch is on main. The older `agent-*` and `wt-*` entries predate this
thread; leave them.

## What the design session still owes

Nothing outstanding. New briefs come from it when Dan asks; screenshot
verdicts too. If a gate result is ambiguous, send Dan's words and the
screenshot path to the design session rather than deciding alone.
