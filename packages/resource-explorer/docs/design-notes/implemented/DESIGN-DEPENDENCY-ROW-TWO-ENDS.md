# DESIGN — A dependency has two ends: the row, its publication, and its wire (2026-10-07)

Design session note at the owner's word on brief 1's dependency table:
*"How are you going to use marked dependencies? You show a source but not
a target, so they are not a wire."* Companion to
`DESIGN-BLUEPRINT-NODE-ADMISSION.md` (evidence classes) and
`DESIGN-CURATE-NESTING-SELECTION-AND-DEPENDENCY-KINDS.md` §3 (kinds).

## The rule

**A dependency is a relationship with two ends and a kind. A row that names
only where it was read is evidence, not a dependency.** Every row names
the dependent, the target, the relation and the kind; the file it came from
is the evidence column, not the content.

## The row

| Column | Content | Example (egeria_workspaces_git) | Example (egeria_git) |
|---|---|---|---|
| **dependent** | the thing that depends: a component of this repository (by its locator, and GUID once accepted) or the repository itself when no component owns the evidence | the `egeria-main` compose service (referenced only, so: the *deployment* this repository describes) | the `open-metadata-implementation` Gradle module |
| **relation** | the verb, from a short list that grows: `requires` (build-time library), `runs` (a deployment runs an image), `connects_to` (a runtime endpoint), `reads` / `writes` (a data store or topic), `deployed_by` (the planned Egeria relation, for a target that is another repository's build) | `runs` | `requires` |
| **target** | the other end, typed: `package` (name, version, ecosystem), `image` (name, tag), `service` (name, port), `endpoint` (host:port or URL), `resource` (an RE-registered resource or an Egeria element, with slug or GUID when known) | `image odpi/egeria-platform:latest` → resolved to **resource egeria_git** when RE knows that repository builds it | `package org.apache.kafka:kafka-clients 3.7.0 (maven)` |
| **kind** | `build-time` / `runtime` / `data` (a store or topic read or written); a new kind is a new value | runtime | build-time |
| **evidence** | file and line, the line's text one gesture away | `compose-configs/egeria-quickstart/docker-compose.yaml:41` | `build.gradle:112` |
| **state** | the envelope's words: `measured` (read from a manifest or artifact), `proposed` (a runtime or data relation inferred from config, awaiting a person), `confirmed` (a person's act in Curate), `not established` (the target could not be resolved: "image name not matched to any repository RE knows") | proposed → confirmed | measured |

Direction is always dependent → target, read "<dependent> <relation> <target>".
A target that is itself a registered resource or an Egeria element gets its
GUID in the row, which is what makes the row publishable as a relationship
the day the type exists.

Header line per kind with counts, as brief 1 built; the table sorts and
filters by any column, and "by target" groups the many dependents of one
Kafka or Postgres together, which is the question a person usually has.

## Where the rows come from

- **Build-time**: the dependency surveyor's manifest parse (dependent = the
  module or package the manifest belongs to; target = the declared
  package). Already measured; gains the dependent column (today it is
  implicit: the repository).
- **Runtime and data**: the architecture recovery IR already holds ports
  and wires read from compose, Helm and Dockerfiles (`interfaces.py`,
  `port_materializer.py`); those wires **are** runtime dependencies with
  two ends, and the table reads them from the IR rather than from a second
  parse. A compose service's `image:` is a `runs` row; a `depends_on`,
  an environment variable holding a host:port, or a connection string is a
  `connects_to` or `reads`/`writes` row with the evidence line.
- **Resolution** of a target to a resource: by image name against the
  images RE knows repositories publish (node-admission's "shipped here"
  evidence on other repositories), by host:port against registered database
  servers, by topic name against Kafka resources when that kind exists.
  Unresolved stays `not established` with the reason; never guessed.

## How a confirmed row publishes today

Egeria's "deployed by" family of relationship types is not yet available,
so a confirmed runtime or data row publishes as an **annotation on the
dependent's asset** (the repository asset, or the accepted
`SolutionComponent` when the dependent is one), `ResourceMeasureAnnotation`
with the envelope in `additionalProperties` and these fields:
`relation`, `target_type`, `target_name`, `target_guid` (when resolved),
`kind`, `evidence`, `confirmed_by`, `confirmed_at`. One annotation per row,
qualifiedName from the two ends and the relation so a second publish
reuses it. Build-time rows stay as today (per-ecosystem annotations) and
gain the same fields. When the relationship types land, a migration reads
these annotations and creates the relationships from `target_guid`; the
rows were written so that no re-measurement is needed.

## When a row becomes a wire

A row is drawn as a **wire** in a blueprint when both ends are admitted
nodes of that blueprint (`SolutionLinkingWire` is the Egeria element the
materialiser already knows for this, written only for accepted ends):

| Dependent | Target | Drawn as |
|---|---|---|
| admitted node | admitted node of the same blueprint | a wire between them, the relation as its label |
| admitted node | referenced-only service (not a component, per node admission) | a wire from the node to the blueprint's **edge**: the target is drawn as a port on the boundary labelled with the target's name, not as a box inside |
| admitted node | a resource RE knows (another repository, a database) | a cross-blueprint link: drawn as an edge port here and recorded as the confirmed cross-repository fact (the `deployed_by` relation for an image another repository builds) |
| the repository itself | anything | not drawn in the component blueprint; listed in the table and in the Environment Deployment Blueprint when that kind is offered |

So for egeria_workspaces_git the Deployment Blueprint has no wires (no
components) and the Environment Deployment Blueprint draws the services
with their `connects_to` and `reads`/`writes` rows as wires, which is the
diagram a person wants of a workspace. For egeria_git the Gradle modules'
`requires` rows between modules draw as wires in the Build Blueprint, and
the Deployment Blueprint's wires come from the platform's own compose.

## What changes in brief 1's table

Add the `dependent`, `relation` and `target` columns and the target's type
and GUID; read runtime rows from the IR's wires; resolve targets to
resources where RE can and say `not established` where it cannot; group by
target. The publish path adds the fields above to the annotation. The
blueprint draws wires from rows, not from a separate wire list, so the
table and the diagram can never disagree.

Tests: a compose fixture with `image:` and `depends_on` yields `runs` and
`connects_to` rows with both ends; an image matching another repository's
shipped-here evidence resolves to that resource; an unmatched image reads
`not established` with the reason; a confirmed row publishes one annotation
with `target_guid`; a row whose ends are both accepted nodes produces one
wire and the diagram's wire count equals the table's.

Gate (owner, by use): on egeria_workspaces_git, the table reads "<service>
runs <image>" and "<service> connects_to <service>" with evidence lines;
on egeria_git, a module's `requires` row names both ends; confirm one
runtime row and read back its annotation by GUID with the target named.
