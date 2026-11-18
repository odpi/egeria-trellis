# trellis-context

Turns a **ContextSpec** and some resolved sources into a bounded context, plus a
**manifest** describing exactly what it did and why.

**The packer is ordinary code, not an agent.** Determinism, monotonicity,
symmetric packing and the hard budget ceiling are all unavailable if a model
decides what to include — and those guarantees are the reason this exists rather
than a prompt template.

**It never resolves anything.** Callers hand it results. That keeps it free of
stores, clients and credentials, and makes every test a pure function of its
input.

**The manifest is the point, not a debugging extra.** It is what makes a compile
explainable ("why is this here"), auditable ("re-run it"), and negotiable ("drop
that section and try again"). A packer that returns only text has thrown away
the part a person can act on.

See `docs/context-compilation-design.md` §2 (what a compile adds), §11
(explainability), §14 (the invariants), §16 (why the packer is not an agent).

## Using it

```python
from trellis_artifact_tree.model import Rung
from trellis_context import Candidate, ContextSpec, Section, pack

spec = ContextSpec(
    spec_id="adoption-gate:my-repo", version=1,
    sections=(
        Section("instructions", role="instructions", required=True, weight=1.0),
        Section("security_scan", role="evidence", weight=0.8),
    ),
)

candidates = {
    "instructions": Candidate("instructions", {Rung.FULL: "Answer from the evidence below."}),
    "security_scan": Candidate("security_scan", {
        Rung.FULL: "## security_scan\n- ci_config: present\n  workflow runs tests",
        Rung.SUMMARY: "## security_scan\n- ci_config: present",
    }),
}

packed = pack(spec, candidates, budget=4000)
packed.text()            # the context
packed.manifest.packed   # what went in, and at which rung
packed.manifest.dropped  # what did not fit
packed.manifest.gaps     # sections with no candidate at all
```

A section with no candidate becomes a **gap**, not a silent omission — that distinction is the
reason the manifest exists. `budget` is in characters by default; pass `measure=` to count
tokens instead.

## Evidence age (0.2.0)

A `Candidate` may carry `as_of`: when the evidence it packs was produced, never when the context was compiled.
When a section packs rows of different ages, `as_of` is the oldest (what a staleness check should age) and
`as_of_newest` the newest. `as_of_source` names where the date came from, in the caller's own vocabulary. All
three are written to the section's manifest `packed` entry only when `as_of` is set: an unknown age is left
out, not filled in. They are provenance, so they add nothing to a section's size or text.
