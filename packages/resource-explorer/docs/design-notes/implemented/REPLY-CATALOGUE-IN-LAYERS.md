# Cataloguing in layers — conceded, and the one thing it needs that isn't scheduled

**Replying to:** `CATALOGUE-IN-LAYERS.md` (the owner's ruling) and `#93` / `#94`
**Date:** 2026-09-14 · **Against:** `origin/main` at `e9f00af`

The layered answer is better than mine, and it is better for a reason I should
have found and did not.

---

## 1 · My S3 answer was half wrong, and the wrong half matters

I wrote: deployment evidence → `SoftwareCapability`; an importable distribution →
`SoftwareLibrary`. The first half holds. **The second half is a type error, not a
granularity call**, and the note has it exactly: `SoftwareLibrary` is a
classification meaning *a server managing distribution of software modules* —
PyPI, Nexus, npm. It names the thing that **manages** libraries, not a library.
So proposing `trellis-context` as one catalogues a package as an artifact server,
and my version would have kept doing that for every importable distribution while
congratulating itself on fixing the applications.

The right shape is the one the owner ruled: layer 1 is what the repository
delivers, as `SoftwareCapability` classified **`Application`**; shared code that
is not deployed on its own **is not a capability at all** and belongs at layer 2
as a component, marked *shared between RE and EA*. That is a cleaner answer than
mine because it stops trying to give every package a layer-1 type.

**And the same misreading one level up is the more serious find:** one
`SourceControlLibrary` per repository, where the classification names the
*service* — GitHub — and the repository is what the service manages. Load-bearing
since August, with every published SurveyReport hanging off those elements.

## 2 · Layer 2 is the accepted verdicts, which changes what the component column says

*Accept/reject is the layer-2 catalogue decision.* That reframes the work I have
open rather than adding to it, and it changes the copy in one place that matters:
the bulk-accept preview. Reviewing and cataloguing are not the same promise, and
the dialog currently makes the smaller one.

But **the preview must not name a type that has not been verified.** The note
says the exact type in the `DeployedSoftwareComponent` family is *to be verified
before the first write* — so until it is pinned, the dialog says what it does in
plain words:

> Accept 14 · `pyegeria/mermaid_utilities/` — 14 components, 3 of them at or
> below 50% confidence. **Catalogues them as software components under
> `resource-explorer`**; the exact Egeria type is not yet pinned. About 21s of
> Egeria writes (measured, 1.5s median). Nothing runs until you confirm.

Naming `DeployedSoftwareComponent` in a confirmation dialog before anyone has
verified it is how a provisional decision becomes a shipped claim — which is the
`SoftwareLibrary` mistake, one step earlier in its life.

## 3 · The layer-2 offer — reuse DepthOffer's rules verbatim, and it has a real price

The catalogue-depth offer is the depth offer's sibling and should be its twin, so
the three properties carry over without restatement: **not a nag** (once per
catalogue record, in the pane, never a modal), **not a gate** (layer 1 is already
recorded when it appears, nothing waits on an answer), **not a scold** (*this
catalogue records 2 applications · the 64 recovered components beneath them are
not catalogued* is a fact about the record, not an instruction). And the outcome
goes on the record — accepted, declined, or chose — for the same reason the depth
decline does: a corpus of declines says *layer 2 is not worth its price here*,
and that cannot be read off anything if declining leaves no trace.

One difference in its favour: **this price has a basis.** Component creation has
no measured rows yet, which is why that dialog says so. Egeria writes *are*
measured — 1.5s median, p90 2.3, post-redeploy — so the offer can say a number
and name its basis:

> catalogue the next layer · 64 components · about 1m 36s of Egeria writes
> (measured, 1.5s median) ›

## 4 · What is not scheduled, and should be: the retraction

Two model corrections are in the Backlog "to schedule, not a quick fix". Agreed
on the sequencing — but a Backlog entry is not where this project puts
corrections, and the doctrine is its own: *a retraction is recorded in place
rather than quietly fixed.*

Everything already published carries the wrong type. Anyone reading Egeria today
sees `trellis-context` as an artifact server and every repository as a source
control service, confidently, with no mark on it. The two sessions that found it
are the only parties who know. So:

- **The resource's records list gains a correction record** — the machinery
  shipped in `#83` and this is exactly its case: a record naming what was
  published under a type since found wrong, appended, not edited.
- **The Curate pane says it until the migration runs:** *published under a type
  since found wrong — `SoftwareLibrary` names a server that manages modules, not
  a module. A correction is scheduled; these elements are not yet re-typed.*
- **And the published elements themselves** should carry it if anything can reach
  them — if not, say that too, because "we cannot mark what we published" is a
  finding about the publish path worth having on the record.

This is the same argument I made about the three repos with a stale `cve_scan`
positive: a contradiction that resolves quietly leaves no trace that the
interface once asserted both, and the people who saw it have no way to know
which half was wrong. The difference here is scale — this one is every published
SurveyReport.

## 5 · Two small things from `#93`

- **The live metric is `code_lines`, not `lines_of_code`.** My worked example
  used the wrong name; `FactInPlace.dc.html` is corrected on the sheet.
- **The `opens` table is the round's best work and it is not mine.** Refusing
  `opens` on `relationship_count` because `_symbol_members` lists symbols rather
  than relationships — traced per cell to the reader's own docstring — is the
  honesty rule applied at a granularity I did not specify and would not have
  thought to ask for. A link that opens the wrong list is worse than no link, and
  that table is the only thing standing between the two.

---

Still mine: the component column and the wire diagram, now with the layer-2
framing, and `detect` / `coupling` — which the layered model makes *more* urgent,
not less: if accepting a component is a catalogue write, then a verdict landing
on a proposal its curator never saw is a catalogue entry nobody authored.
