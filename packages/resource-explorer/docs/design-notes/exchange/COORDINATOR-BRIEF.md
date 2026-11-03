# Coordinator brief

**You are the coordinating implementer session.** Read this, then
`PLAN-FINISH-REPOS.md` in this folder. Both are short.

---

## Why you and not the designer session

The designer session runs in Anthropic's cloud; you run on the project owner's
Mac. Peer messaging only reaches sessions on the same machine, so **the designer
cannot send you anything** — every instruction from it has to be copy-pasted by
the owner by hand. That relay is the single most expensive thing about how this
week went. You can reach the other local sessions directly, and you can run the
app and the tests. So dispatch belongs with you.

The designer keeps design and review. **That split is deliberate and worth
holding:** a coordinator who also reviews their own dispatch has no independent
check, which is how three boards in a row ended up claiming shipped work was
open.

---

## What you own

1. **Assignment.** `PLAN-FINISH-REPOS.md` Part 3 is the list — the project
   owner's ten points, each with the files it owns, its dependencies and a done
   test. Order is yours; the dependencies are not.
2. **Worktrees.** Four exist on `origin/main` under
   `/Users/dwolfson/localGit/egeria-v6/trellis/.claude/worktrees/` —
   `wt-honest`, `wt-design`, `wt-classic`, `wt-admin`. One session per worktree.
   New stream: `git worktree add .claude/worktrees/wt-<name> -b re/<name> origin/main`.
   Keep them there — the previous ones lived under `/private/tmp/…/scratchpad/`
   and were all lost to cleanup.
3. **Merge order, and landing it.** A push does not move the running app, which
   serves from the main checkout on port 8810. Finishing is
   `git -C <main checkout> fetch origin && merge --ff-only origin/main`. If
   `--ff-only` refuses, that checkout has commits of its own — stop and ask, do
   not force or stash past it.
4. **The main checkout.** It is currently on
   `re/restore-unbuilt-stages-defect-doc`. Put it back on `main` once every
   active session has moved into a worktree. The designer deliberately left this
   to a local session, having already moved that branch once by mistake.
5. **`app.js` contention.** Worktrees give each session its own HEAD and index;
   they do nothing about two sessions conflicting inside a 6,861-line file.
   Until Part 2 §1 splits it, **exactly one session in `app.js` at a time** — and
   Part 2 §0 and §1 are both in it, so they go first and alone.

---

## The one thing that is non-negotiable

**Write a `*-IMPLEMENTED.md` in this folder when an item lands**, naming what it
replies to. One already exists as the model: `VERDICT-RULING-IMPLEMENTED.md`.

This is the rule most likely to get skipped, because you will be coordinating
*and* building and it will feel like overhead. It is not. Three separate rounds
this week put already-shipped work on the board as ready-to-start, every time
because a reply doc was never written and the board's state was inferred from
the documents instead of the code. The designer can read the repo directly now,
which helps — but a reply doc is where you say what you decided that the code
does not show, and that has been the most valuable thing coming back.

Say what you changed, what you scoped out and why, and what you could not test.
`VERDICT-RULING-IMPLEMENTED.md` does all three, including naming its own known
gap, and that gap turned out to matter more than the feature.

---

## What you can expect from the designer session

- **Review of what lands**, against the code, with citations. Recent ones:
  `REVIEW-VERDICT-RULING.md`.
- **Answers when something is genuinely ambiguous** — but it has committed to no
  further rulings unless something is blocked, and to reading the relevant code
  before specifying anything. Four of its errors this week came from specifying
  against the design record instead of the source.
- **Drawings** for anything visual, on the canvas.

**Push back on it.** Every correction from an implementer this week was right:
the port-scarcity figure, the `SoftwareLibrary` type, `code_lines`, the
retraction the owner had already ruled out, `MetadataExpert` over
`ClassificationExplorer`, and a resolver and a stale-state that both already
existed. It has been wrong more often than you have.

---

## Start here

**`wt-honest`, Part 2 §0** — two small fixes in `app.js`:

- the `unbuilt` flag, read in three places and set in none, so six stages render
  as built (`DEFECT-UNBUILT-STAGES-RENDER-AS-BUILT.md` §3 has the four-line fix)
- `renderWorkListPane`, never called and not among `app.js:26`'s imports

Then Part 2 §1, the `app.js` split, still alone in that file. After it,
`wt-admin`, `wt-classic` and the per-stage streams can all run at once.
