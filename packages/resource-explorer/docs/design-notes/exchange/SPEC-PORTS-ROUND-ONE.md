# The ports work, round one — and a claim that needs correcting first

**Date:** 2026-09-14 · **Read against:** `origin/main` at `91d6d93`
**Drawn at:** `design_handoff_list_answers/` page three — `ComponentReview.dc.html`
and `PortsAndWires.dc.html`

I mapped the model before drawing, and two findings changed what this round
should be. Neither is about a screen.

---

## 1 · The Curate column asserts a derivation the code does not perform

`curate_plan.py` ships this, and `/next` renders it:

> *ports and wires are derived from the accepted components' interfaces and
> relationships*

They are not. `arch_recovery/interfaces.py` → `propose(root, first_party,
components, …)` reads **deployment artifacts only** — Dockerfile `EXPOSE`,
compose `ports` / `expose` / `depends_on`, OpenAPI, `.proto`, GraphQL, Thrift,
FastAPI route markers — at **survey time**, and attaches a port to a component
by longest path-prefix match (`_owner_of`). No verdict is consulted anywhere in
that path. **Rejecting a component leaves its port and wire rows untouched.**

What acceptance actually changes is which ports get *drawn*: the diagram is
rendered fresh on every read, drops rejected components, and drops any port
whose owner is not a shown node — reporting the count in its caption rather
than attaching it to a guess. That is display, not derivation, and the sentence
claims the stronger thing.

This is the same defect class as the inventory counting vendored files as the
repository's own, and it sits in the sentence a curator reads to decide whether
to trust the column. **Fix the sentence now:**

> ports and wires are read from the repository's deployment artifacts; the
> diagram shows those belonging to accepted components

One line, true today, and it costs nothing.

**The destination, named and still blocked.** *Adopt on accept* — accepting a
component adopts its declared ports as curated facts with the acceptance's
author and date, written to `architecture_materialized_ports`, a table that
exists and that nothing writes, by a `PortMaterializer` that exists and has no
caller. It stays deferred, on grounds you measured on 2026-09-03 rather than on
taste: `SolutionPort` creation is a generic-metadata workaround for pyegeria
ISSUE-85, the outbox has no creator for wires at all, and
`SolutionLinkingWire` relationships carry no `qualifiedName`, so the outbox's
idempotency cannot cover a retry.

**And ports get no verdict.** A port is not a proposal about the world; it is a
line in a Dockerfile. The classic UI already says the right thing —
*read-only, no verdict to give*. If a declared port is wrong, the repository is
wrong and the fix is a pull request. Keep the asymmetry: **components are
proposals, ports are readings.**

## 2 · On most repositories there are almost no ports at all

Ports exist "essentially not at all" for logical components; Prometheus, one of
the better cases, has 1 port and 0 wires. A dedicated ports-and-wires screen
would be an empty room on nearly every resource — and *an absence that holds
for every resource is a bug report about the reader, not a finding about the
corpus.*

So **ports are a column on the component, not a screen**: two words on the row
where they exist, nothing where they do not, and one sentence at the foot of the
tree when a repository declares none —

> No declared topology — `egeria-python` declares no ports and no wires. Ports
> are read from Dockerfiles, compose files and API documents; this repository
> has none of those with an exposed interface.

— which says what it looked in, so a reader can tell *nothing declared* from
*nobody looked*.

---

## 3 · Review happens at the branch, not in a queue

Your own spike settled this before I drew anything: at recovery scale a flat
per-component queue fails, and **no human-accepted component is cohesive under
any bar** — curators accepted top-level named packages, a signal nothing
measures. So the surface is not ordered by confidence. It is **the repository's
own naming**, and the decision is made at the branch.

The arithmetic is the argument. `kafka` recovers **609** components; the median
node size is **1** everywhere; `kafka` has **0** verdicts and `egeria_python`
has **4 of 85**. Six hundred decisions is not a review, it is an abandonment.
Roughly twenty named branches is a morning.

`ComponentReview.dc.html` draws it:

- **Rows are branches** of the path the components were already keyed by —
  `pyegeria/ · 23 components`, with type, the declared-port count where there is
  one, and the verdict. Leaves expand under them.
- **A branch verdict inherits, and a component's own verdict wins** — which your
  curation-lenses design already proposed. An inherited verdict says so
  (*accepted · with `pyegeria/`*) rather than posing as a decision someone made
  about that file.
- **It costs no schema change.** Verdicts are already per `scope_locator` and
  already append-only, so a branch verdict is one row at the branch's scope and
  the reader resolves the longest prefix. A bulk endpoint would make *accept all
  14* one call instead of fourteen, but correctness does not need it.
- **Confidence routes; it never hides.** The `⚠ ≤50%` count rides on the branch
  so a weak cluster is visible before it is opened, and *by confidence* is a
  sort, not a filter.
- **Grouping nodes stay marked**, with the classic UI's own words: *grouping
  only — directories that hold components, not components themselves.*
- **Bulk accept uses the shared preview dialog**, because rule 4 makes that
  mandatory rather than optional: *14 components, 3 of them at or below 50%
  confidence. 14 will be created as Egeria SolutionComponents; none exist yet.
  About 50s to publish — queued, so the pane returns at once. Nothing runs until
  you confirm.*
- **No undo, and the word is not offered.** A verdict is a new row, so
  unaccepting is a `rejected` row and the trail keeps both. *change* is the
  right word and the classic UI already uses it.

## 4 · The diagram reads; the tree acts

The diagram is **already verdict-aware** — fresh on every read, rejected
dropped, accepted solid, undecided dashed, a rejected blueprint ungrouping its
members. Accept a branch and the next read redraws; nothing needs building for
that.

What it is not is the acting surface. It comes back from Kroki as a finished
SVG, so nodes are not controls without a mapping layer nobody has asked for.
Side by side, on paper, in the diagram font. And it keeps saying its two
ceilings out loud: **50,000 characters**, reported and never truncated, and
**20 classed nodes**, past which some *pending* nodes lose their dashes on the
stated grounds that losing the styling beats losing the diagram. On a
609-component repository both bite — which is why the tree is the surface that
scales and the diagram is the one that explains.

---

## What this round does not settle

**The two perspectives.** `detect` and `coupling` propose different component
sets and are never merged, while a verdict is keyed by scope and therefore
lands on both. That is a real ambiguity, it is not addressed here, and it needs
its own round — naming it so it is not mistaken for settled.

**What `/next` has today**, so the build is scoped honestly: no verdict write
path at all. The four curate verdict routes are wired only into the legacy
shell; `_component_members` is flat and reads materialized components only, so
candidates are not in the member reader; and `children_for` knows only
`file:` keys, so a nested component tree has no server side yet. The tree above
is new behaviour, not a re-skin — which is worth knowing before anyone
estimates it.
