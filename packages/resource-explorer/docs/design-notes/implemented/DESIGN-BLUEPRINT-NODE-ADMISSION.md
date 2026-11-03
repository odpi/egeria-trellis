# DESIGN — What admits a node to a blueprint: built here, shipped here, or only referenced (2026-10-07)

Design session note at the owner's word after running a full survey and
Curate on egeria_workspaces_git on 8813 (brief 1 live): "we need to tailor
it rather than just build out something irrelevant, which is what it does
now." This is the admission rule for the Deployment Blueprint and the
design seed for the Build and Logical blueprint slices
(`DESIGN-CURATE-NESTING-SELECTION-AND-DEPENDENCY-KINDS.md` §4).

## What chooses a node today, read on main e092d314

`surveyors/sub_surveyors/arch_recovery_detect.py` runs the detectors in
`surveyors/arch_recovery/detectors.py` over the checkout after
`exclusion.py` drops vendored and cache directories (nothing else):

- **compose services**: every service in every file that *looks like* a
  compose file by content (`_is_compose`), one component per service;
- **deployment units**: every directory holding a Dockerfile or a compose
  file, with the Dockerfile's `CMD`/`ENTRYPOINT` as evidence;
- **package manifests**: Python, Node and Gradle modules;
- code markers (Spring apps, interfaces) on top.

`clustering.py` then groups components per architectural perspective
(physical, deployment, logical, dev), and the Curate pane draws the
deployment clusters. Every signal "reads a boundary something already
declared", which is right, but **nothing asks whose boundary it is**. A
compose service whose `image:` is `odpi/egeria-platform` and one whose
`build:` points at a Dockerfile in this repository are admitted the same
way. For egeria_workspaces_git, whose whole content is compose files that
run other projects' images (Egeria's servers, Kafka, Postgres, Jupyter, the
optional runtimes), that yields a "component diagram" of things the
repository deploys and builds none of. That is the irrelevance the owner
saw, and it is a classification gap, not a detector bug.

## The rule

**A node enters a repository's blueprint by the evidence class of its
boundary, and the class is shown on the node. Three classes:**

| Class | Evidence | Admitted as | Word on the node |
|---|---|---|---|
| **built here** | a compose `build:` context or Dockerfile inside the repository; a package manifest or Gradle module in the repository; a code marker in first-party source | a **component** of this repository (proposed) | "built here · from <file>" |
| **shipped here** | a compose service or deployment unit that runs an image **this repository publishes** (the image name matches a Dockerfile's declared image, a CI publish step, or the manifest's package name) | a **component** of this repository (proposed) | "shipped here · image <name>" |
| **referenced only** | a compose service running an image the repository neither builds nor publishes; a Helm chart from elsewhere; an external endpoint in config | **not a component**: a **runtime dependency** of the deployment this repository describes | listed in the dependency table (§3 of the Curate design note) as kind `runtime`, "from <compose file> · image <name>", never drawn as a component |

A repository whose evidence is all "referenced only" gets an honest
Deployment Blueprint: **zero components, N runtime dependencies**, with the
sentence "this repository deploys other software and builds none of its
own: see Dependencies · runtime". That is the correct diagram for
egeria_workspaces_git, and it is useful: it is the deployment topology of
an Egeria environment, which is a *different* blueprint, below.

## What a person confirms, and what is left out

- **Proposals, never decisions.** Every built-here and shipped-here node is
  a proposal with its evidence one gesture away; a person accepts or
  rejects it (the existing verdicts); only accepted nodes are written to
  Egeria as `SolutionComponent`s under the blueprint.
- **Reclassify, one gesture.** A node's class is itself a proposal: a
  person may say "this is built here after all" (a Dockerfile the detector
  missed) or "this is only referenced" (a vendored build the repository
  does not own), and the verdict carries the reclassification with its
  reason; the node moves between the blueprint and the dependency table
  accordingly.
- **Left out, said.** The blueprint's manifest states what the detectors
  found and did not admit: "12 compose services referenced only · listed as
  runtime dependencies", "3 directories with Dockerfiles but no first-party
  source · proposed as built here, unconfirmed", "node_modules and 2 cache
  directories excluded". Nothing silently dropped.
- **Not a component ever:** a test fixture's compose file (path under a
  test or example directory, the existing worthiness rules), an image
  pinned only in documentation, a CI service container.

## The second blueprint this exposes: the deployment topology

What egeria_workspaces_git *is* describes is worth a blueprint of its own,
and it is not the repository's components. **An "Environment Deployment
Blueprint"** (kind in the name, per the owner's rule) whose nodes are the
services the compose files run, with their images, ports and wires, as
**deployment units of other projects**, each linked to the repository that
builds it when RE knows that repository (egeria_git builds
`odpi/egeria-platform`; that link is a confirmed cross-repository fact, the
"deployed by" relation the Egeria leads are adding types for, carried as an
annotation until then). For a workspaces repository this is the blueprint a
person wants; for egeria_git it is empty and not offered. The selector
lists it only when the detectors found referenced-only services.

## How this feeds the Build and Logical blueprints

- **Build Blueprint**: nodes are "built here" by manifest evidence only
  (Gradle modules, Python packages, Node packages), with their declared
  dependencies on each other; compose and Dockerfiles do not contribute.
  Admission is therefore already decided by the class rule.
- **Logical Blueprint**: nodes come from the project's own documentation
  (the documentation surveyor: architecture pages, component lists), each
  a proposal until a person confirms it; built-here code clusters are
  proposed as *realisations* of a logical node, never as logical nodes
  themselves. The class rule keeps the layers from sharing a box.

## Slice, small

1. Classify each detected component by the three classes in
   `arch_recovery_detect` (the evidence is already collected; the class is a
   derived field on `Component`), persist it in the IR.
2. The Deployment Blueprint draws built-here and shipped-here only; the
   referenced-only services flow into the dependency table as `runtime`
   rows with their compose source (brief 1's table already exists).
3. The blueprint manifest's "found and not admitted" lines; the
   reclassify gesture on a node with its reason recorded in the verdict.
4. The Environment Deployment Blueprint as a selector entry when
   referenced-only services exist, drawn from the same IR.

Tests: egeria_workspaces_git's fixture yields 0 components and N runtime
rows with the sentence; egeria_git's fixture yields built-here Gradle
modules and shipped-here images and no Kafka or Postgres node; a
reclassification moves a node between the two tables and is recorded.

Gate (owner, by use): egeria_workspaces_git's Curate shows the sentence and
the dependency table full; egeria_git's Deployment Blueprint shows only
things Egeria builds or ships, with the class word on each node and the
left-out lines in the manifest.
