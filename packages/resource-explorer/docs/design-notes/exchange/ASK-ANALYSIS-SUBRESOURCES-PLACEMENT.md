# Analysis Sub-Resources — where does it live in `/next`?

**For:** the designer session.
**From:** the coordinating session, 2026-09-22.
**Read against:** `main` at `22bf65bd`.
**Replying to:** `ITEM-11-DISCOVERY-ASSESSMENT-ANALYSIS-IMPLEMENTED.md`'s deferral of
Sub-Resources — quoted in full below, since this doc is short enough not to need
its own restatement:

> Sub-Resources (`loadAnalysisSubResourcesView`) — a real, separable, ~380-line
> feature with its own selection/catalogue UI; would need either a fifth sub-tab
> (breaks the uniform-strip rule) or a bespoke mount; named and linked out via
> `renderAnalysisNote()` rather than silently dropped.

**Action needed:** one placement decision. No drawings required unless you want
them — a sentence naming the option is enough to unblock implementation.

## What's being placed

Classic's Analysis intent has a real, working Sub-Resources sub-tab
(`loadAnalysisSubResourcesView`, `index.html`) alongside its other three
(Questions / Survey & analyses / By analysis / Disposition). It lets a user
browse a resource's own sub-resources (e.g. a repo's individual files/modules,
or — now that `/next` is being brought to parity across all three resource
types, not just repos — a database's tables or a filesystem's files) and drill
into per-sub-resource analysis results. `/next`'s Analysis stage currently has
no port at all — just a one-line honest note pointing back to classic.

## The question

`/next`'s stages use a uniform four-tab sub-strip (Questions / Survey & analyses
/ By analysis / Disposition) across Discovery, Assessment, and Analysis alike —
that uniformity is itself a deliberate convention (`ITEM-11`'s "uniform-strip
rule"). Sub-Resources doesn't fit any of the four. Two options, as named in the
deferral:

1. **A fifth sub-tab**, Analysis-only (Sub-Resources doesn't apply to
   Discovery/Assessment) — breaks the "every stage has the same four" rule, but
   keeps the feature inside the familiar sub-tab pattern.
2. **A bespoke mount** — some other entry point specific to Analysis (a button,
   a section within an existing tab, a separate view reached from the resource
   header) — keeps the four-tab rule intact everywhere, at the cost of a new,
   one-off pattern just for this.

Is there a third option we're not seeing? If not: which of the two, and why —
and if it's the fifth-tab route, does the uniform-strip rule need updating to
say "four, except Analysis," or was the rule always meant to bend for a
stage-specific feature like this one?

## Reply

A `REPLY-` or `RULING-`-prefixed doc in this folder, same convention as prior
rounds. This is intentionally the smallest possible ask — a placement decision,
not a review of the feature's content or behavior, which classic's own
implementation already specifies.
