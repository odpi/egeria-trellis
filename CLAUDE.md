# Trellis — repo-wide conventions

Package-specific guidance lives in `packages/resource-explorer/CLAUDE.md` and
`packages/egeria-advisor/CLAUDE.md`. This file covers what is true across the whole repo.

## Git: multiple sessions run in this repo at once

Several Claude Code sessions routinely work in this repo simultaneously, sometimes in the same
worktree on the same branch. Three rules follow. The first two matter most, because they hold
even when you do not know what other sessions are doing — and the second is the case where the
first one's advice does not apply at all.

**1. No whole-tree git operations in a shared checkout. Name the paths — every command that
takes a pathspec accepts one.**

This covers `git add -A` / `git add .` / `git commit -a`, and equally `git stash`, `git checkout`,
`git reset` and `git clean`. They all operate on *the tree*, which in this repo means everyone's
work, not yours.

Both halves have now happened. A blanket stage: `97ad95a` ("Checklist: record the 12-repo backfill
slice and its verification") also committed `docs/context-compilation-design.md`, an unrelated
design doc authored by a concurrent session and untracked at that moment — see `6756408` for the
record. And a blanket stash on 2026-08-28: a session ran `git stash` to answer "is this failing
test mine?", which reverted three files another session had in flight. Both were recoverable and
neither lost work, but only because the session that did it said so immediately.

The prohibition alone is not enough, because the need is real — you *will* legitimately want to
set your changes aside to check whether a failure is yours. Use the pathspec form:

```
git add path/one path/two
git stash push -- path/one path/two
git checkout -- path/one
```

`git stash push -- <paths>` is the safe door into the same room as `git stash`. Reach for it
rather than working around the rule.

**But the pathspec form is safer, not safe — `git checkout -- <path>` still reverts whatever is in
that path, including someone else's uncommitted work.** On 2026-08-29 an agent ran `uv sync`, saw it
had modified `pyproject.toml` and `uv.lock`, and reverted them with exactly the pathspec form above.
It was following this rule correctly and still destroyed another session's workspace wiring, because
those files carried that session's edits too. Reverting to HEAD is destructive at *any* scope.

So before any `checkout`/`restore` of a path, **read `git status` for that path first**. If a change
you did not make is in it, do not revert — ask whoever is active. Files that several sessions edit
for unrelated reasons (`pyproject.toml`, `uv.lock`, `CLAUDE.md`, the backlogs) are the ones this
catches, because nothing about your own task suggests anyone else would be in them.

Before committing, read `git status` and confirm every staged path is yours. An unexpected file is
a signal that another session is mid-write, not something to sweep in. If naming paths is tedious,
it is because the change is large, not because the rule is wrong.

**But a named path is per-FILE, not per-hunk — and that gap is invisible.** `git add <path>` stages
*everything* in that file, including hunks another session wrote and has not committed. The file is
legitimately yours to edit; the rule is satisfied; `git status` shows nothing unexpected, because the
path IS one of yours.

Happened 2026-08-31, to a session that had spent the day telling other sessions to name their paths.
`3ce1254` added a 15-line cross-reference to `docs/egeria-reset-recovery.md` and carried 19 further
lines — another session's `## 3a. The outbox does NOT restore anything`, written and not yet
committed — into the same commit under the wrong author's message and sign-off. Nothing was lost and
the content was correct, so it was only noticed because its author went to commit, found nothing
staged, and used `git log -S` instead of assuming they had lost it.

For a file several sessions write to — `CLAUDE.md`, the backlogs, the checklists, the runbooks —
naming the path is not enough. Look at what you are about to stage:

```
git diff --stat -- <path>     # more changed than you changed?
git add -p -- <path>          # stage your hunks, not the file
```

`git diff --stat` is the cheap version and catches the common case: if the line count is bigger than
your edit, someone else is in there with you.

**2. Never `git commit` while a merge is in progress unless the merge is yours.**

This is the case where rule 1's advice does not merely weaken — it does not apply. `git commit`
during a merge commits **the merge**: every staged path, including another session's conflict
resolution, under your message and your sign-off. `git add <my paths>` does not save you,
because the merge state is not addressed by a pathspec. There is no safe pathspec form of this
one.

That is what makes it worth its own rule. Everyone here has learned "name the paths", and here
that habit offers no protection while feeling like it does.

Check before committing, always — it is one command:

```
git rev-parse -q --verify MERGE_HEAD && echo "MERGE IN PROGRESS — do not commit"
```

Use that form, not `test -f .git/MERGE_HEAD`. Inside a linked worktree `.git` is a *file*, not
a directory, so the `test -f` version finds nothing and reports all-clear during a real merge —
a check that fails silently in exactly the setup rule 3 describes.

`git status` does say so — "You have unmerged paths" while conflicts remain, "All conflicts
fixed but you are still merging" once they are resolved — but it says it below the file list,
which is exactly where a long status stops being read. The second wording is the dangerous
one: it reads like an all-clear.

Encountered 2026-08-30: a session finished a documentation change and found `MERGE_HEAD` set
to `162200d` with three files `UU` — conflicts already resolved on disk by someone else, not
yet staged. Committing would have completed a stranger's merge. The work was held for ten
minutes until the merge landed, then committed normally.

**If you find yourself here:** wait, and copy any uncommitted work of your own outside the
checkout first. `git merge --abort` is a legitimate thing for the merge's owner to run and it
takes unstaged changes with it — yours included, with no warning that they were there.

The same shape appears in any whole-repository operation whose blast radius is invisible in its
name. `uv sync` run from a package directory is the other one people hit.

**3. Worktrees are not currently a usable isolation mechanism here — know why before relying on
one.**

Every Claude session on this machine runs with `cwd: /Users/dwolfson`, which is not a git
repository. Sessions reach into this repo by absolute path, so they have no per-session repo
context and cannot take a worktree of it. That is why concurrent sessions all land in the same
checkout: not carelessness, but the absence of any mechanism that would separate them.

`git worktree list` does show worktrees (`.claude/worktrees/`, plus sibling checkouts). Those come
from subagents given an explicit repo path, or from manual setup — not from session isolation. A
stale one is normal and harmless; check it is clean and behind before removing it.

If you genuinely need isolation for a long parallel change, the fix is to start a session **inside
the repo** rather than in the home directory. That is a deliberate trade: sessions run from home
precisely so they can work across trellis, egeria-python, egeria-workspaces and pyegeria in one
place, and rooting a session in one repo gives that up.

Absent that, **rules 1 and 2 are the only controls that actually operate.** Treat them accordingly.

**If you do work in a worktree, fast-forward this checkout when you are done.**

A worktree is real isolation, and that is exactly the problem: **the running app serves from THIS
checkout** (`/Users/dwolfson/localGit/egeria-v6/trellis` — `resource-explorer web --reload`, port
8810), not from yours. Pushing to `origin/main` does not move it. Nothing pulls it.

Found the hard way on 2026-08-31: a session built six user-visible changes in a worktree, pushed
each to `origin/main`, and reported each as shipped. The project owner restarted the server, hard-refreshed, and
saw none of them — this checkout was three commits behind, so they were looking at an earlier version
of the same feature and reasonably concluded the fix had not worked. The work was fine; the
delivery was not.

So the last step of a change is not the push:

```
git -C /Users/dwolfson/localGit/egeria-v6/trellis fetch origin
git -C /Users/dwolfson/localGit/egeria-v6/trellis status --porcelain   # someone else mid-write?
git -C /Users/dwolfson/localGit/egeria-v6/trellis merge --ff-only origin/main
```

`--ff-only` is doing more work than it looks. **It refuses rather than clobbers**, on both counts:

- If a locally-modified file would be overwritten it stops with *"Your local changes to the
  following files would be overwritten by merge"* and changes nothing. Verified directly on
  2026-08-31 rather than assumed. So a fast-forward **cannot** destroy another session's
  uncommitted work — a dirty checkout is not by itself a reason to hold off.
- If it will not fast-forward at all, this checkout has commits of its own and someone needs to
  look at them, not be merged past.

**Stop when it refuses. Do not force it, do not stash to get past it, do not `checkout --` the
path.** Those are the operations rules 1 and 2 are about, and the refusal is the signal to ask
whoever is active rather than to work around it. Check for a merge in progress too
(`git rev-parse -q --verify MERGE_HEAD`) — rule 2 applies here whoever is driving.

*This paragraph originally said "stop if it is dirty". That was over-strict, and the session that
wrote it then broke it twice within the hour on a checkout that had three of someone else's files
modified — safely both times, because `--ff-only` was doing the protecting rather than the rule. A
rule people route around teaches them to route around rules; the accurate one is narrower and
actually load-bearing.*

**Before committing, read `git status` and confirm every staged path is yours.** An unexpected file
is a signal that another session is mid-write, not something to sweep in.

## uv: run `uv sync` from the workspace root, never from a package directory

Same failure mode as the git rules above, in a different tool — and harder to suspect, because the
command's name suggests "make consistent" and its effect can be "remove".

**From the workspace root, a bare `uv sync` installs the whole workspace** (since R19, 2026-10-31).
The root is a *virtual* project — `pyproject.toml` declares `dependencies = []` and has no
`[build-system]`, deliberately, so that it exists only to hold workspace membership. Until R19 that
made a bare `uv sync` resolve to nothing and uninstall the entire venv (observed 2026-08-29). The
root now has a `[dependency-groups] dev` group listing every member — the packages with their `dev`
extras and the ten reference steps under `packages/trellis-analytics/examples/steps/` — and uv
installs the `dev` group by default, so a bare sync gives the same venv as
`uv sync --all-packages --extra dev`. Either form is fine from the root.

**Adding a reference step:** put its package under `examples/steps/` (the members glob picks it up)
**and** add its distribution name to the root `dev` group. A step that is a member but not in the
group is uninstalled by the next sync. Third-party steps stay outside the lock: install them with
`uv pip install -e <path>` and expect any sync to remove them.

**From a package directory, it re-resolves the shared venv down to that one package.** All sessions
here share one `.venv`, so `uv sync` run inside `packages/resource-explorer/` uninstalled
`trellis_context` — leaving another session's `tests/test_context_compile.py` unable to collect at
all, in a file that session had not touched. Adding `--extra dev` to a package-scoped sync does not
save you; the scope is the problem. This is unchanged by R19.

`--all-extras` does **not** work as a substitute on macOS: it pulls `onnxruntime-gpu`, which ships no
arm64 wheel, and the sync fails outright.

If you only need one package's dependencies installed, scope it explicitly with `--package <name>`
rather than by changing directory — but in a shared checkout, prefer a root sync and leave the
venv in a state the next session can use.

## DCO sign-off

All commits require sign-off: `git commit -s`, or an explicit `Signed-off-by:` trailer. This repo
feeds work toward odpi/Egeria, which enforces DCO.

## Writing project docs

A 2026-09 sweep of RE and EA's docs found 77 first-name references ("Dan says...", "Raised by
Dan") across 15 files, plus one more in EA's own source. That volume is a sign the habit was
default, not exceptional — the following rules exist so it doesn't recur.

**Attribute decisions by role, not by first name, from the moment they're written.** "The project
owner decided/ruled X, `<date>`" — not "Dan says X." A doc read months later, or by someone
outside the session that wrote it, should read as a project record, not as private
correspondence. This applies in prose, in code comments, and in commit messages alike — the EA
instance found in the sweep was a `# Dan's call` comment, not a doc.

**Before a doc-wide rename or terminology substitution, check for a collision with existing
domain vocabulary.** `grep -ci '<candidate term>'` across the target files before and after the
pass — if the count grows by more than the substitutions you intended, the term already meant
something else in this codebase and your pass just introduced ambiguity. Found live in this
sweep: a first attempt at the rule above used "the maintainer," which collided with RE's own
established sense of "maintainer" (the maintainer of a *surveyed* repository, used 21 times in
`architecture-recovery.md` alone) — caught only by diffing occurrence counts, not by reading.

**Give project-owner decisions their own greppable, consistently-formatted callout** —
`**Decision (project owner, <date>):** "..."` — rather than folding a ruling into flowing
narrative prose. `Backlog.md` already drifts toward this in places; make it the convention
everywhere a decision is recorded, so a later reader (or session) can find every ruling with one
`grep -n "^\*\*Decision"` instead of a close read.

**Watch document size as a doc absorbs more content, independent of file-count metrics.**
Consolidating many small docs into fewer broad ones can still leave individual documents too
large to onboard from — `architecture-recovery.md` reached 2,720 lines in the same sweep that
took RE's doc count from 92 files to 35. Fewer files is not the same success criterion as
readable files; if a single document crosses roughly 1,500–2,000 lines, split out a
clearly-scoped sub-document rather than continuing to append.
