# trellis-microflow

> **Vocabulary.** "Microflow" here is Resource Explorer's survey-step sense: one self-contained survey step. It is not
> the report-spec `microflow`, an in-thread sequence of producer, processor and formatter stages; there, a *step* is a
> routine deployed to an engine host. See the
> [report-spec pipeline model](https://github.com/odpi/egeria-workspaces/blob/main/portal-docs/Internals/report-spec-pipeline-model.md)
> (Vocabulary used here). Nothing is renamed.

Shared resource-sharing primitives for microflow-style step execution —
the generic half of `resource-explorer`'s D6 design
(`docs/unified-survey-execution-model-plan.md`).

## Why this exists, and why it's a separate package from the start

Multiple microflows (individual "survey steps," in resource-explorer's
current vocabulary — see `docs/microflow-survey-funnel-model.md`) often
need the *same* expensive external resource — a downloaded zipball, a
database connection, a mounted filesystem — computed once per run and
shared across whichever microflows asked for it, regardless of how many
did. That need isn't specific to repo surveys, or even to
resource-explorer: any Trellis app composing a run out of small,
self-contained steps hits the same problem.

`trellis-vectorstore`'s own history is the reason this package exists
*before* a second app needs it, not after: resource-explorer and
egeria-advisor each independently built structurally similar but
behaviorally distinct `PgVectorStore` implementations, and untangling
that after the fact was real, avoidable work. This package is the
opposite choice, made deliberately: extract the generic mechanism first,
let resource-explorer be its first real consumer, and let any future
Trellis app depend on the same primitive instead of reinventing it.

## What's in it

- `ResourceProvider` — names a resource and how to acquire it.
  `acquire` is a **zero-argument** callable returning a context manager
  (`__enter__` yields the resource, `__exit__` does cleanup) —
  deliberately argument-free so this package never needs to know what a
  "project," "registry," or connection string is; a caller binds its own
  specific context via a closure or `functools.partial` when it builds
  the `ResourceProvider`, not by passing arguments through this package.
- `resolve_resources(stack, providers, needed)` — resolves each named
  resource in `needed` via its provider exactly once, entering each into
  the caller's own `ExitStack` so cleanup happens together when that
  `with` block exits. This is the actual "resolve once, dedupe across
  however many steps asked for it" guarantee — pass the same `ExitStack`
  and the same computed `needed` set across every microflow selected in
  one run, and a resource is acquired exactly once no matter how many
  microflows requested it.

## What's deliberately NOT in here

Anything domain-specific: what a "microflow"/"survey"/"step" actually
is, how steps are registered or composed, what annotations they produce.
Those stay in the consuming app (`resource_explorer.surveyors.
survey_orchestrator`/`repo_survey_definition_adapter` for
resource-explorer's own repo Survey model) — this package is
intentionally just the resource-sharing primitive underneath it.

## Using it

Resources are acquired once and shared across the steps that need them, rather than each step
fetching its own copy.

```python
from contextlib import ExitStack
from trellis_microflow import resolve_resources

with ExitStack() as stack:
    resources = resolve_resources(stack, providers, needed={"zipball_root"})
    root = resources["zipball_root"]
    ...   # every step needing zipball_root reuses this one
```

`providers` maps a resource name to a `ResourceProvider`; `needed` is the union of what the
steps about to run declare. Anything not asked for is never acquired, and the `ExitStack`
releases what was.
