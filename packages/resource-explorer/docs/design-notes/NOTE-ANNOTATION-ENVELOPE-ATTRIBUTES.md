# NOTE — The annotation envelope RE needs from the annotation-type extension (2026-10-05)

For the Egeria leads, from the Resource Explorer design session, after the
evening review: *"we need to extend annotation types."* This is the
half-page of what RE needs on every annotation so that its readers become
generic over annotation type (`DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`
A2, R5; `context-spec-requirements.md` C6). Everything else in a type stays
domain-specific. Names are proposals; the semantics are the requirement.

## Four attributes, required on every annotation

| attribute | values | meaning | why RE needs it |
|---|---|---|---|
| `resultState` | `MEASURED` · `NOT_ESTABLISHED` · `NOT_APPLICABLE` | whether the step measured the fact, tried and could not, or the fact has no meaning for this resource kind | the only distinction RE's whole interface rests on: "we looked and found nothing" (a measurement) versus "we could not look" versus "there is nothing to look for"; today a column absent from `pg_stats` is simply absent from the report, which reads as all three |
| `resultStateReason` | free text, required when `NOT_ESTABLISHED` | why it could not be measured: `statistics not gathered on the server`, `no SELECT on 3 of 61 relations`, `credential has no CONNECT` | the sentence on the row and the "run column profile ›" offer; without it RE can only say "unknown" |
| `producingRun` | a reference: the SurveyReport GUID, or the engine action GUID, or RE's run id as an external identifier | which run produced this value | provenance per value, perishability ("inputs changed since computed"), and the manifest's "missing" versus "stale" |
| `measuredAt` | timestamp | when the value was read from the resource (not when the annotation was written) | every number on screen carries its as-of; survey timestamps lag writes, and two runs on one day must stay two points |

## Two attributes, optional

| attribute | values | meaning |
|---|---|---|
| `scope` | `WHOLE` · `PARTIAL` with a `scopeReason` | the measurement covered the whole resource or part of it (credential-scoped: "sees 6 of 8 schemas"); RE draws ◐ from it |
| `producerVersion` | the version of the service or step that produced the value | so a changed step cannot silently reinterpret an old measurement; RE records its own commit here for its steps |

## What RE does with them

- A reader per annotation type derives the row's state word from
  `resultState` and `scope` alone; the row's headline comes from the
  type's own attributes. No per-analysis reader code.
- The context compiler's manifest reports a missing section as
  `NOT_ESTABLISHED` with its reason, never as nothing.
- A derived value (a scorecard) inherits the weakest `resultState` of its
  inputs and names the input.
- Publication carries the four required attributes unchanged; RE never
  rewrites them.

## What RE does not ask for

No change to the measurement payloads themselves; no new annotation
types from RE (RE's own measurements fit ResourceMeasure and the existing
family); no requirement on how Egeria stores the attributes, only that
every annotation a survey service writes carries them, and that a service
which cannot measure writes an annotation saying so rather than writing
none.
