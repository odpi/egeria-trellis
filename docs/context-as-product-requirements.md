# Context as a product — requirements for the Context Intelligence flavour (2026-10-06)

Design session note, from the owner's direction of 2026-10-05 and 2026-10-06.
Status: requirements for discussion; nothing built. Companion:
`docs/context-spec-requirements.md` (context for a computation we run),
`packages/resource-explorer/docs/design-notes/DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`
(A1–A13, R1–R20, §6 the owner's review),
`packages/resource-explorer/docs/design-notes/DESIGN-ENRICHMENT-AND-CURATE-PUBLISHING-TO-EGERIA.md`.

## 1. Why a separate note

The context-spec requirements cover **context for a computation we run**: the
shared resolver compiles facts and annotations into the inputs of a step, a
tile, a report, a comparison, an agent task or an export. The consumer is our
own code, the bundle lives for one run, provenance rides in the envelope.

The owner's Context Intelligence view asks a different question: **how the
information Egeria manages can be applied to other kinds of processing, and
specifically to AI applications and training.** The product is the context
itself, packaged for a consumer outside Trellis. That flavour has its own unit,
shape, guarantees, curation and versioning, none of which the spec model asks
about. The owner's framing of 2026-10-06: *Resource Explorer gathers the
evidence from which new context is built.* Egeria is the contextual backplane;
the product is built from Egeria's governed record, never from RE's evidence
directly.

Four forms of context are now distinguished (diagrams of 2026-10-06): about a
question (the investigation), on a screen (the Context tab), for a computation
(the spec model), and **as a product** (this note).

## 2. Decisions already taken

- **First consumer: the data scientist and data engineer** (owner,
  2026-10-05). RE has evolved into an instrument for technical users.
- **The non-technical view is another Trellis application**, chat-first, on
  the same underlying data as RE; not a Portal projection, not a simplified
  RE mode (owner, 2026-10-05). It is the product's **second consumer**.
- **RE stays the instrument.** The chat application explains, answers and
  recommends over RE's proof rows and Egeria's record with the same honesty
  words; it never establishes anything RE did not.
- **A pack is built from the governed record only** (what RE has cataloged,
  published or confirmed in Egeria), so zones and licences are enforceable
  at packaging time and nothing RE knows but has not published can reach a
  consumer.
- **Egeria rolls forward; nothing is undone.** A pack version is a forward
  event; a recall is a newer version that says what changed.
- **Four hosts, not three** (A11 amended): RE, Egeria Advisor, the Portal and
  the chat application all import the one shared library.

## 3. The pack, defined

A **context pack** is one deliverable: a dated, versioned, licensed slice of
Egeria's governed record about a declared subject, shaped for one consumer
kind, carrying provenance per item.

| Part | Content | Source |
|---|---|---|
| Subject | the resource(s) or the investigation the pack is about; the question (purpose, scope, lens) when there is one | investigation binding (Egeria project) or an explicit resource list |
| Record slice | elements, terms (semantic assignments), lineage and dependencies, ownership, classifications, quality and other annotations that are **confirmed or published**, external references | Egeria, read through the access layer with the consumer's identity, so zones apply |
| Evidence slice (optional, declared) | survey annotations with their envelope, marked as measurements not facts | Egeria's survey reports (never RE's local rows) |
| Guarantees | freshness per item (Egeria's version and time), provenance per item (who, when, from which run), licence and zone per item, the pack's own version and the rule that made it | computed at packaging |
| Manifest | what was asked for, what was included, **what was left out and why** (zone, licence, not established, nothing found), counts by state | computed at packaging; the honesty rules apply to the manifest as to a screen |

**Shapes**, one source, several renderings, declared per consumer kind:

- **Documents for retrieval** (RAG): one document per element with its facts
  as prose and its provenance as metadata; the shared Egeria-family corpus is
  the existing instance.
- **A tabular feature set** (training, analysis): one row per element, one
  column per fact, state words as values, never blanks.
- **A graph slice** (JSON-LD or Egeria's own JSON): elements and
  relationships with types, for tools that reason over structure.
- **MCP resources and tools** (agents): the pack exposed live rather than
  copied, same manifest, same gates.

## 4. Requirements

- **P1 Unit.** A pack has one subject and one consumer kind; a pack for an
  investigation is the union of packs for its scope, with the question
  carried. No pack spans subjects a person did not declare.
- **P2 Source.** Only the governed record and Egeria's survey reports.
  Never RE's local rows, never unconfirmed proposals, never a judgement a
  person has not signed. The manifest names anything excluded for that
  reason as "not published", not as absent.
- **P3 Identity read.** Packaging reads Egeria as the requesting person or
  service, so zone and licence enforcement is Egeria's, not a filter of ours.
  A pack states the identity it was built for.
- **P4 Provenance per item.** Every fact in a pack carries its element GUID,
  version, time, and the producer (survey run, person, connector), so a
  consumer can trace any item back and a later pack can say what changed.
- **P5 Freshness.** A pack carries its as-of time and each item's own time;
  a consumer can ask "is pack v3 stale" and get an answer from the record,
  not a guess.
- **P6 Honesty in the manifest.** Counts say what they count; "nothing
  found", "not established", "not published" and "not applicable" are
  distinct words in the manifest, as on screen; no field is blank.
- **P7 Versioning and recall.** Packs are versioned; a new version lists
  additions, removals and changed facts against the previous one; a licence
  or zone change produces a new version that names the items it withdrew,
  and the registry records who received which version.
- **P8 Curation.** Fit for a consumer kind is a Curate act on the resource
  (a confirmed mark, published as a fact), so "AI-ready" on the Portal
  derives from it; the fit-by-dimension model (✓ fits / ∅ doesn't / ? couldn't
  check / ◌ no reader) applies with the consumer kind as the dimension set.
- **P9 Shape declaration.** A consumer kind declares its shape(s) and the
  fields it needs; the shared library renders every shape from the one
  record slice; adding a shape never changes the slice.
- **P10 Shared library.** Packaging is a routine in the shared library (A11,
  R19), callable from a script, a notebook, the Portal and the chat
  application; no executor needed.
- **P11 Declared in Egeria.** A pack definition (subject rule, consumer kind,
  shape, inclusion rules) is an Egeria element so it can be found, governed
  and reused; each built version is recorded against it. Which element types
  is the Egeria leads' call, alongside the spec question in the spec note §5.
- **P12 Measured value.** The first pack is measured: what it improves for
  its consumer against the same task without it, recorded as evidence, so
  the product has a number before it has a second shape.
- **P13 Chat application consumer.** The chat application consumes packs
  through the same library and the same gates, and renders the honesty
  words in plain language without dropping them.

## 5. Order of work

1. The data scientist and engineer pack for one database (coco_pharma) in the
   tabular and document shapes, built by a routine in the shared library,
   with manifest and provenance; measured on one task (P12).
2. The pack definition as an Egeria element (P11), after the Egeria leads'
   answer on types.
3. The chat application's first read of a pack (P13), which is also the
   first proof of the fourth host.
4. The graph and MCP shapes, when an agent task needs them.

## 6. Questions

**Owner.** Whether a pack's inclusion rule is per consumer kind only, or also
per team (a profile, as R9's publication profiles). Which task measures the
first pack.

**Egeria leads.** The element types for a pack definition and a built
version; whether a pack's recipients belong in the record.

**Designer.** How a pack's manifest and its "left out and why" read on the
Portal's AI-ready tile and in the chat application; the same cue vocabulary
or a document form.
