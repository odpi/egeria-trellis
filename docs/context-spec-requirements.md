# Context specs and report specs — requirements for one spec model with several flavours (2026-10-02)

**Status: requirements, nothing decided, nothing built.** From the design
session at the project owner's request, 2026-10-02: *we need to start
thinking through the requirements for a context spec (there could be
multiple flavours for different purposes, not just RE) and any
enhancements to report_spec, which is going to continue to evolve anyway.*
Companion to `packages/resource-explorer/docs/design-notes/DESIGN-ANALYTIC-EXTENSIBILITY-WORKING-ASSUMPTIONS.md`
(assumptions A9 to A13: steps versus routines, one runtime, context
compilation as the shared resolver, report specs and context specs as
siblings) and to `context-compilation-design.md`, whose §8 already puts
`ContextSpec`, the resolver registry, the packer, the envelope and the
cache in a shared package.

## 1. The two specs that exist

Both are declarative, both carry the "part of identity?" partition, and
they differ in their consumer: one feeds a packer, the other a renderer.

| | `ContextSpec` (`packages/trellis-context/trellis_context/spec.py`) | Report spec, "FormatSet" (`packages/egeria-advisor/docs/design/REPORT_SPEC_BUILDER_DESIGN.md`) |
|---|---|---|
| says | which sections a context has, their role, weight, required, `mode: rank|gate`, floor rung, symmetric group | what to fetch (`action_function`), what to show (`columns`), how to filter (`content_filters`), how to shape (`shape_defaults`), how to run (`performance_hints`) |
| does not say | what goes in a section: resolvers do, the packer never calls one | how the result is reasoned over: it is a view |
| identity | `spec_id`, `version`, `as_of`, `target_model`; perspective is a weight, never identity | content filters and shape defaults are identity; performance hints are not |
| serialisation | Python dataclass; no user-editable form yet | Dr.Egeria markdown (`## Verb Object` / `### Attribute`), parsed by the UniversalExtractor |
| lifecycle | none | draft → catalog entry → result snapshot; versions, trash, retry, recover; specs never leave the catalog on execute |
| output | a packed context, a **manifest** (packed, compressed, dropped, missing) and a **derivation** | a result snapshot in one of several formats |
| consumer | an LLM, via RE's chat | a renderer: EA's report views, the Portal's dashboards, Dr.Egeria output |

The compiler design borrowed EA's identity partition and spec lifecycle;
EA has the authoring surface and the markdown form; RE has the sections,
the envelope and the manifest. Neither has both halves.

## 2. Flavours: the same spec, different consumers

A flavour is what the consumer does with resolved candidates. The sections,
identity, resolvers and manifest are shared; each flavour adds the fields
its consumer needs and no others.

| flavour | consumer | what it adds | what it must not do |
|---|---|---|---|
| **answer** (RE chat today) | an LLM | budget in tokens, compression ladder, `target_model`, instruction sections | execute a step to fill a gap |
| **derivation** (A12, scorecards) | a derived step | deterministic resolution, no budget, no ladder; the envelope-propagation rule; emits an annotation | use an LLM; drop a section |
| **presentation** (tiles, boards, report views) | a renderer | projection (columns), shape (sort, depth), format; a result set, not prose | reason, summarise, or pack |
| **comparison** (pair questions, Find's ranked rows) | a renderer or an LLM | symmetric groups (already in `Section.group`), one subject per group, the parity precondition | favour whichever subject packed first |
| **agent task** (EA's agents, A2A) | an agent loop | instruction and tool sections with `role`, the gate mode for what an agent may not see | leak a gated section through a tool |
| **export** (CSV out, the designer's discovery reply §4) | a file | intent columns then `status_` columns; state as words | carry state back in as an instruction on import |

A spec declares its flavour. A spec of one flavour may be derived from
another (a presentation spec over an answer spec's sections), which is the
report-spec-over-context-spec case in A13.

## 3. Requirements

Numbered so the inventory and the architecture can cite them. Blank owner
means undecided.

**Identity and caching**
- **C1** One identity partition for every flavour: `spec_id`, `version`,
  `as_of`, the resolved candidates' versions, and the flavour's own
  identity fields (`target_model` for answer; columns and shape for
  presentation; the pair for comparison). Perspective is never identity.
- **C2** A step's version (assumptions A5) enters the candidates' identity,
  so a changed step invalidates every pack, tile and derived annotation
  that read its annotations.
- **C3** `as_of` is mandatory and honoured by every resolver: a spec
  compiled "as of" a date reads the ODS as it was, which is what trending
  and reproducible scorecards need (`context-compilation-design.md` §10).

**Sections and resolution**
- **C4** A section binds to **annotation types plus a predicate**, not to a
  step id. A user-added step that emits a bound annotation type is
  resolvable with no spec change. (Whether sections bind that way today or
  still to analysis ids is inventory question R18.)
- **C5** Resolvers are registered by annotation type and cost tier in the
  shared runtime (A11), and the same registry serves every flavour.
- **C6** The envelope travels: a candidate carries *measured*, *not
  established* with reason, or *not applicable*, plus its run; the manifest
  reports missing sections by that state; the derivation flavour applies
  envelope propagation; the presentation flavour renders the words, never
  a blank or a zero.
- **C7** A compile never executes a step. A spec may *name* what would fill
  a gap ("run column profile on crm_prod ›") so the consumer can offer it.

**Authoring and lifecycle**
- **C8** Every flavour is authorable in Dr.Egeria markdown, the report
  spec's form, with the same `## Verb Object` conventions, so one parser
  reads both and a user edits a context spec the way they edit a report
  spec today.
- **C9** The report spec's lifecycle (draft → preview → save; versions,
  trash, retry, recover; snapshots separate from the spec) applies to
  every flavour. A compiled pack, a tile's result and a derived annotation
  are snapshots; the spec never moves on execute.
- **C10** Specs are catalogued in Egeria when published, as the elements
  the Egeria leads name for them, and cached locally; a spec file in git
  is the authoring copy (assumptions A3, A5).
- **C11** A spec may include another by reference (master-detail in report
  specs; a presentation spec over an answer spec's sections), with the
  identity of the included spec in the including one's key.

**Parameters**
- **C12** Three parameter categories, the report spec's, for every flavour:
  content filters (identity), shape defaults (identity), performance hints
  (not identity). The investigation, purpose and intent of the answer
  flavour are content filters by this test; perspective is a shape
  default that changes weights only.
- **C13** Run-time parameters (a slug, a pair, a date) are declared with
  types and defaults in the spec and supplied at execute; a spec with
  unsupplied required parameters refuses to run and names them.

**Outputs**
- **C14** One manifest format for every flavour: what was resolved, at what
  rung or projection, what was dropped and why, what was missing and in
  which envelope state, and the derivation of each section. The manifest
  is the return path for user input (`context-compilation-design.md` §2)
  and the provenance of every snapshot.
- **C15** A result set contract for the presentation and export flavours:
  typed columns, the envelope state per cell, provenance per row (run,
  source), so a tile and a CSV draw the same words from the same rows.
- **C16** Snapshots live in the ODS, not only in files: a report snapshot
  and a compiled pack are rows with their manifest, so the Portal, EA and
  RE can list, compare and trend them. Files remain as the export form.

**Evaluation and feedback**
- **C17** Feedback on a snapshot is keyed on the axis being tuned
  (perspective, purpose), as EA's feedback collector already does, and
  joins the ODS beside the snapshot it rates.
- **C18** A spec's quality is measured, not asserted: the open claims in
  `context-compilation-design.md` §9 (perspective weights improve answers;
  packing beats the ad hoc chat context) get a measurement before the
  answer flavour is the default.

**Hosts and security**
- **C19** Every flavour compiles from a plain script, the Portal and
  Trellis through the one runtime (A11); the proof is one spec executed
  from all three with identical manifests.
- **C20** A spec declares what it may fetch (annotation types, Egeria
  types, the ODS) and under which credential tier; gated sections stay
  gated through tools and includes; a spec may not name a credential.

## 4. Enhancements to report_spec implied by the above

The report spec keeps evolving on its own; these are the changes this
model asks of it, each small and each additive.

1. **Sources beyond pyegeria methods.** `action_function` today names a
   client method. Add two source kinds: a **routine** (A10) by name with
   parameters, and an **annotation query** (annotation type plus predicate,
   C4). A report over RE's annotations then needs no RE-specific code.
2. **Envelope-aware columns.** A column declares how it renders *not
   established* and *not applicable* (C6), defaulting to the words, so a
   report never prints a blank or a zero for a silence.
3. **A `flavour` field**, defaulting to presentation, so the parser and the
   lifecycle serve context specs without a second format (C8, C9).
4. **Snapshots carry the manifest** (C14) and land in the ODS as well as
   the outbox file (C16).
5. **Typed run-time parameters** with defaults and required marks (C13),
   replacing the run modal's free-form pre-fill.
6. **Includes with identity** (C11): `detail_spec` already references a
   nested spec; the included spec's version joins the key.
7. **A source that is another spec** (C11, A13): a tile over a compiled
   pack's sections, which is how an RE board and a Portal dashboard show
   the same thing.
8. **Publication as an Egeria element** (C10) under the owner's one-way,
   idempotent rule, with the read-back as the proof row.

## 5. Open questions

- Which Egeria element types carry a published spec and a published
  snapshot; the Egeria leads' call alongside the annotation-type extension.
- Whether the answer flavour's budget is tokens only, or rows for the
  presentation flavour under the same field.
- Whether the Portal's local dashboards adopt the report spec as their
  definition form outright, or keep a dashboard file that composes report
  specs (one tile per spec).
- EA's compile unit: the compiler design proposes EA adopt RE's
  investigation (an unbound local row classified PersonalProject); the
  flavour table assumes that and it is still a proposal.
- The first spec of each flavour to build, named by the owner, so the
  requirements are checked against something real; the Coco Find
  investigation offers an answer spec, a comparison spec and a tile at
  once.
