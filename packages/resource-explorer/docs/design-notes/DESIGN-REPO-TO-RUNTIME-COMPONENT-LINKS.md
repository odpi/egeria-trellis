# DISCUSSION — Linking a repository's built components to the runtime components that deploy them (2026-10-08)

Design session note for the owner's discussion ("one other thing this
brings up: linking components from a repo to deployed runtime
components"). Read from pyegeria's clients and the merged node-admission
code; nothing built. Companions: `DESIGN-BLUEPRINT-NODE-ADMISSION.md`
(evidence classes), `DESIGN-DEPENDENCY-ROW-TWO-ENDS.md` (`deployed_by`),
`DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md` (the two blueprints).

## 1. What the link is

Two different things that both get called "deployed by", and the design
should keep them apart:

| Link | From | To | Meaning |
|---|---|---|---|
| **A. realisation** | a *design* element: a `SolutionComponent` in a blueprint ("OMAG Server Platform" in the Runtimes blueprint) | the *thing that implements it*: a built artefact or code component in a repository (egeria_git's `open-metadata-implementation` module, the `odpi/egeria-platform` image) | "this design component is implemented by that code" |
| **B. deployment** | a *built artefact* (an image, a package) | the *runtime instance* that runs it (the compose service `egeria-main` in egeria_workspaces_git; a registered server) | "this artefact is deployed as that running thing" |

The owner's question is the chain A→B seen from the repository: a built
component in egeria_git → the image it publishes → the runtime component in
the workspace that runs it → the design component in the Runtimes
blueprint that stands for it.

## 2. What Egeria has for it (checked in pyegeria, model 0737 Solution Implementation)

- **`ImplementedBy`** (design element → implementation element):
  `SolutionArchitect.get_solution_component_implementations` reads it;
  pyegeria's own overview metric "solution components implemented" is
  fixed to it, filtered to Asset-subtype ends. This is link **A**: a
  `SolutionComponent` is implemented by an Asset, which for RE is the
  repository asset, or a `SoftwareComponent`/`DeployedSoftwareComponent`
  RE's catalog commit creates for an accepted component, or the image as
  a `DeployedImplementationType`-typed asset.
- **`ImplementationResource`** (design element → a resource used to
  implement it): `GovernanceOfficer.link_implementation_resource`, model
  0737. Looser than `ImplementedBy`: "this design uses that resource";
  the right one for "this blueprint component is realised with this
  repository" when the repository is the whole, not a component.
- **`DeployedOn`** (a software server capability / deployed component →
  the host it runs on): the hosting side of link **B** for a runtime
  instance on a platform; appears once in pyegeria.
- **`deployedImplementationType`** is a *property*, not a relationship:
  the owner's components carry it ("OMAG Server Platform", "View
  Server"), and the content pack's components are keyed on it. It is how
  RE matches an image or a server name to the content-pack component
  without a relationship at all: the *name* is the join.
- The **"deployed by" family of new relationship types** the Egeria leads
  are adding: not yet available; when it lands it is link B's proper form
  between an artefact and its runtime instance, and the `deployed_by`
  dependency rows become relationships by migration.

So link A exists in Egeria today (`ImplementedBy`), link B half-exists
(`DeployedOn` for hosting; the artefact→instance relation is the pending
family), and the join RE can make now without any relationship is by
`deployedImplementationType` and image name.

## 3. Where RE gets the evidence

| Evidence | Where it already is | Gives |
|---|---|---|
| which images a repository builds | node admission's `published_images(root, census, package_names)` (`arch_recovery/admission.py`): image names from Dockerfile `LABEL`s, CI publish steps and manifests, per repository | artefact ← builder (within one repository) |
| which images a workspace runs | the referenced-only services of egeria_workspaces_git with their `image:` | runtime instance → artefact name |
| the join across repositories | image name equality between one repository's `published_images` and another's referenced services, **only when both repositories are registered in RE** | built component in repo X → runtime service in repo Y: the cross-repository fact the benchmark note's §1 names |
| the design component | the content-pack `SolutionComponent` whose `deployedImplementationType` matches the image family or the server type; or RE's own accepted component | runtime instance → design component, by name |
| the code component | the recovered component in egeria_git whose Gradle module or Dockerfile produced the image (admission `built_here` with the Dockerfile's path) | design component → code component (link A's far end) |

Nothing here needs a person except the confirmation: every row is a
proposal from name equality, and name equality across repositories is
exactly the kind of inference the honesty rules want confirmed before it
is published as a fact.

## 4. How it is recorded and shown

- **In the dependency table**, as two-ended rows: `<egeria_git module>
  builds <image>` (kind build, relation `builds`, new verb), and
  `<workspace service> runs <image>` (already there); the join row
  `<egeria_git module> deployed_by <workspace service>` is derived from
  the two and marked `proposed · by image name across repositories`.
- **On the blueprint**, once the Runtimes blueprint exists: the design
  component's "implemented by" column (link A) naming the repository
  component, with the ImplementedBy relationship written on confirmation;
  the runtime instance's hosting as composition (the container shape),
  not a wire.
- **In Egeria on confirmation**: `ImplementedBy` from the content-pack or
  RE-made `SolutionComponent` to the repository's `SoftwareComponent` (or
  the repository asset when no component is accepted), idempotent by the
  pair; the artefact→instance link as an annotation naming both ends until
  the new types land (the two-ends publication rule), then migrated.

## 5. How it meets the two follow-ups

- **Wires with an idempotency key**: unchanged; link A is not a wire (it
  leaves the blueprint for the repository), and link B inside a workspace
  blueprint is composition, not a wire. No new wire kind.
- **Annotation re-homing**: link B's interim annotation is written on the
  *artefact's* side (the repository's component, or the repository asset
  until components have assets), which is the same home the re-homing
  follow-up moves confirmed dependency annotations to; one mechanism.

## 6. Questions for the discussion

1. Is `ImplementedBy` the relationship the owner wants from a blueprint
   component to a repository component, or `ImplementationResource` to the
   repository asset as a whole? (My read: `ImplementedBy` to the accepted
   `SoftwareComponent` when one exists, `ImplementationResource` to the
   repository asset otherwise, and the row says which.)
2. Should RE write link A on confirmation now, since the type exists, or
   wait for the "deployed by" family so A and B land together?
3. Does the join by image name across repositories count as enough
   evidence to *propose*, with a person confirming, or must the image be
   catalogued in Egeria first (a `DeployedImplementationType` asset) so the
   join is by GUID?
4. For egeria-python: pyegeria is both a built artefact (the wheel) and a
   design component in the Servers blueprint; the same chain applies with
   PyPI as the "image registry". Is that in scope for the first link?
