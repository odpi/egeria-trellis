# Unbuilt stages render as built — the honest path is wired to a property nobody sets

**Found by:** the project owner asking whether Automate was missing from the
inventory
**Corrects:** `INVENTORY-CLASSIC-TO-NEXT.md` — both its method and one of its
claims
**Read against:** `main` at `1b370cbe`
**Date:** 2026-09-17

---

## 1 · The defect

`/next` declares which stages it has built. `STAGES` (`next/app.js:138-149`)
carries `built: true` on `scouting`, `frame: true` on `investigation`, and
**nothing at all** on `discovery`, `assessment`, `analysis`, `enrichment`,
`curate` and `automate`.

The code that renders an unbuilt stage honestly tests a different property:

- `next/app.js:581` — `if (s.unbuilt) {` — the dashed, deferred treatment in the
  intent nav.
- `next/app.js:5053` — `if (stageDef?.frame || stageDef?.unbuilt) {` — the pane
  message, with this text written out beneath it:

  > *"This stage has no rows in the analysis catalog or the activity log, so
  > there is nothing for a questions pane to show. It is marked here rather than
  > hidden, which is the point."*

**`unbuilt` is read in those places and set in none.** Grep for it: three reads,
zero writes. Every `STAGES` entry uses `built`, so `stageDef?.unbuilt` is
`undefined` for all nine stages, both branches are dead, and **six unbuilt stages
render as live** — normal nav entries leading to panes that behave as though the
stage works.

That message at `:5056` has never been shown to anyone.

**And the sub-tab strip fails the same way independently.** `:3001` reads
`if (t.id === 'questions' || t.built)` and never consults the *stage's*
built-ness, so all four `SUB_TABS` — every one marked `built: true` at module
level — render as live links on all six unbuilt stages.

So Automate is missing twice over and says so nowhere: the stage is undeclared to
the renderer, and its sub-tabs advertise four working panes.

## 2 · Why this is the worst shape of defect this project has

Every other honesty defect found here was a missing rule. **This one is a rule
that was written, argued for in a comment, and silently disconnected by a
property name.** `:12-13` states the doctrine —

> *"Every other sub-tab renders an honest 'not built in /next' rather than a
> half-working version: a half-built version of everything…"*

— and the code one screen down cannot act on it. The design is right; the wiring
is wrong; and nothing fails loudly, so it reads as done.

It is also the exact inverse of the `Find repos` defect. That one greyed a
corpus-level action across nine stages — nine wrong promises of **absence**. This
one offers four built sub-tabs across six unbuilt stages — twenty-four wrong
promises of **presence**. Presence is the worse direction: a deferral
under-promises and costs a user nothing, while this invites someone to work in a
stage that cannot do the work.

## 3 · The fix, and why it should not be `unbuilt: true`

The tempting fix is to add `unbuilt: true` to the six entries. **Do not.** It
leaves two sources of truth for one fact and will drift the moment a stage is
added.

**Invert the tests to read the flag that exists:**

- `:581` → `if (!s.built && !s.frame)`
- `:5053` → `if (stageDef?.frame || !stageDef?.built)`
- `:3001` → the sub-tab is live only when the tab **and** the current stage are
  built: `if (stageDef?.built && (t.id === 'questions' || t.built))`, with the
  deferred rendering otherwise.

Then a stage added without `built` is honest by default, and honesty is not
something anyone has to remember to declare. **Same lesson as the board's
default:** make the safe state the one you get for free, not the one you opt
into.

Worth checking while in there whether `understanding` should now carry
`built: true` — the comment at `:145` says it renders charts, so it may be the
one stage whose flag is genuinely out of date rather than mis-read.

## 4 · What this does to the inventory, which was wrong in two ways

**(a) My method was wrong.** I screened by comparing subsystem vocabulary counts
between the two files. Automate appears in `/next` — as a stage label — so it
never showed as a gap. The method can only find capability that is missing
*along with its words*, which is why it found Admin (absent vocabulary) and
missed both Automate and group collapse.

**`/next` maintains its own authoritative list of what it has not built**, in the
`built` flags, and I should have read that first instead of inventing a
screen. I printed the `STAGES` list into this very conversation earlier and did
not notice that one entry in nine carried `built: true`.

**(b) One claim was wrong in the other direction.** The inventory credits `/next`
with honestly declaring its deferrals — and I praised that doctrine. The
declaration exists in the source and **does not reach the screen**, so `/next`
is not currently honest about six of its nine stages. A deferral nobody can see
is not a deferral.

**The corrected structure has three tiers, not two:**

| tier | what it is | why it matters |
|---|---|---|
| **declared deferred** | `/next` states it, and the user sees it | honest; needs no inventory entry, only a decision to build or not |
| **silently absent** | no declaration anywhere — Admin and its ten views, scout source mode, sidebar width | conspicuous at least: a whole subsystem is obviously missing |
| **silently partial** | the surface claims to work and a behaviour inside it is gone — group collapse, per-answer feedback, perspective persistence, **and now all six unbuilt stages** | **the dangerous tier.** It actively misleads, and a vocabulary screen cannot find it |

The third tier is the one worth auditing and the one my method was structurally
incapable of finding. **Sixteen was a floor; the six stages move it to
twenty-two**, and the true figure still needs a read rather than a screen.

## 5 · Priority

**This goes above everything else on the board.** It is roughly a four-line
change, it restores behaviour that was designed and paid for and never
delivered, and until it lands every statement anyone makes about `/next`'s
completeness — including the retirement question — is being made against a UI
that misrepresents what it can do.
