# REPLY — Designer: the design-notes move (2026-10-01)

To ASK-DESIGNER-DESIGN-NOTES-REORG.md, read against main at a9c9657d.
**Go ahead with Option A.** The answers below don't block the move, but
one of them changes how the dangling-pointer test has to work.

## 1. INBOX.md's "answered" column: neither option is needed

The ask's premise is that the column is *derived* from the
`*-IMPLEMENTED.md` names. Nothing derives it. INBOX.md's header says
"derived", but I wrote every row by hand, reading the implemented notes.
There's no script to re-point, and I'll correct that sentence in the
reconcile pass (§2).

INBOX.md also contains no links to implemented notes. It names 10 of
them, 18 times in all, as **bare filenames in backticks** with no
directory, like every other designer note. A bare name can't dangle when
the file moves, because filenames don't change and are unique across
`design-notes/`.

So the choice the ask offers doesn't come up. INBOX.md needs no edit
for the move, and the single-writer exception isn't needed. **The
dangling-pointer test should resolve a bare note name anywhere under
`design-notes/`**, not only at the path it would have had before the
move. Bare names then survive this move and any later one. If the test is
written to need paths instead, I'll add the prefixes myself within a day
of the merge, and the exception stays unused.

### The pointers that will actually dangle are in code

The move PR should know about these before it's sized:

- **84 full-path references** like `docs/design-notes/X-IMPLEMENTED.md`
  in **59 files outside `docs/`**: module docstrings, comments, test
  docstrings, a CI workflow comment, and the Tailwind config. All of
  them break when the note moves.
- **Three are runtime strings**, not comments:
  `surveyors/prefect_adapter.py:122` (a message that names
  `PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`),
  `surveyors/database/egeria_database_surveyor.py:451`
  (`CATALOG-AND-SURVEY-REFRESH-FIX.md`, one of the two unsuffixed fix
  notes moving to `implemented/`), and `bootstrap.py:255` (bare
  `docs/design-notes/`). These are text a person reads, so they have to
  be right, not just resolvable.
- **About 40 references are split across a line break**: 31 mid-name
  (`docs/design-notes/PER-REQUEST-SERVER-LATENCY-` ⏎ `ROUND-2-IMPLEMENTED.md`),
  and the rest right after `docs/design-notes/`. A one-line regex won't
  see them, so a permanent test written that way would pass while they
  dangle. The test either joins comment continuation lines before
  matching, or the move PR rejoins them onto one line first.
  `git grep -n "design-notes/\?[A-Z0-9-]*$"` lists them.

My recommendation is the same rule as for INBOX: rewrite the code
references to the **note name alone** (`see PREFECT-DISPATCH-HONESTY-IMPLEMENTED.md`)
and have the test resolve names across the tree. Paths in code comments
are what made this move expensive, so they shouldn't be put back.

Counts to check at move time: on main today I see 188 notes outside
`wireframes/` and `screenshots/` and 102 `*-IMPLEMENTED.md`, not the
ask's 219 and 103. The move script should count from the tree, not from
the ask.

## 2. Is INBOX.md still authoritative? Reconcile, and I'll do it

**Reconcile, after the move, as my own pass.** INBOX.md answers "what did
design say, and was it built"; TIMELINE.md answers "what happened to this
note". Those are different questions, and the second can't stand in for
the first.

It's further behind than the ask says. **17** designer notes on main
aren't in the ledger, not 16:

RULING-NAV-GROUPING, RULING-SUBRESOURCES-PLACEMENT,
RULING-DB-QUESTION-CATALOG-CONSISTENCY, REVIEW-MULTI-RESOURCE-ROUND2,
REVIEW-MULTI-RESOURCE-ROUND3, REVIEW-MULTI-RESOURCE-REPRESENTATIONS,
REVIEW-SURVEY-PANE-285, REVIEW-CURATE-PUBLISH-FRESHNESS,
REPLY-COPY-REVIEW-CREDENTIAL-AND-FIT-LANGUAGE,
REPLY-DATABASE-CREDENTIAL-CAPABILITY-VISIBILITY, REPLY-DRAFT-BADGE,
REPLY-RECONCILE-FLAGS, REPLY-SCHEMA-AS-SUB-RESOURCE,
REPLY-SURVEY-ANALYSES-PANE-USER-FACING-MODEL,
REPLY-DESIGNER-ROUND2-DATABASE-SCREENS, REPLY-DESIGNER-ENRICHMENT-STAGE-IA,
REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.

The pass will also change the file's shape:

- **The ledger becomes the file.** There's one row per designer note,
  with its state citing the implemented note or PR by name, or "not
  built", or "needs no build". "Unanswered until proven shipped" stays
  the rule.
- **The narrative boards go** ("Start here", "Just landed", the dated
  rounds). They were the 09-17 state of play and are now history, which
  is TIMELINE's job. "Start here" shrinks to the open items: notes with
  no implemented twin.
- The header stops claiming the column is derived.

I'll use the coordinator's TIMELINE entries as the cross-check, as the
ask suggests.

## 3. TIMELINE.md lines for my replies: the coordinator adds them

**The implementer or coordinator adds the line when a reply merges.**
There are two reasons:

- TIMELINE.md is the coordinator's file. If I write to it too, it has two
  writers, and INBOX.md's header records what that cost once (the 02:32
  crossing).
- "At the time it lands" means merge time, and I don't know when that
  is. My replies sit on branches until you push them, sometimes for
  days: 46afda4d waited two days. Every reply branch appending to the
  end of the same file would also conflict with every other one.

Each reply's header already gives its date, what it answers, and the
commit it was read against, so the line can be written from the reply
alone.

## Timing

`re/design-worklists-scope` (46afda4d, 74f8299d) is still unmerged. It
only adds files at the root and under `wireframes/`, none of which move,
so it doesn't conflict with the move in either order.
