# DESIGN — Curate for repositories: nesting depth, explicit publishing selection, two kinds of dependency, and the component diagram (2026-10-07)

Design session note for the owner's read, from his review of egeria_git on
8813 after the G1 parity merge. No code. UI batch A (accept feedback, select
all/none, checkmark meaning, File types placement, Publish wording,
Understanding order and collapse) is dispatched separately and not repeated.
Constraints: state as a visible cue plus a short word, accent colours for
controls never states; no DDL and no Egeria write without the owner's word;
the ISSUE-117 block stays on, so nothing here archives or deletes.

What the code does today, read on main at 30de07da:

- **"what's in it"** on the Curate pane is the sub-resource survey's
  candidate list (`sub_resource_survey.py`): top-level folders and well-known
  files, scanned to `max_depth = 2`, each labelled *worthy* or not by a
  file-count range and a vendored/generated rule. The pane takes "the
  contained set whole" by one checkbox under the manifest (`curate.js`,
  `CURATE_COLUMNS`). Cataloging a file auto-includes its ancestor folders
  (Egeria's `NestedFile` needs a `FileFolder`), and each item becomes a
  `FileFolder` or `DataFile` asset related to the repository.
- **"dependencies"** is the dependency surveyor over build manifests
  (pyproject, requirements, package.json, pom, gradle): one row per declared
  library, grouped by ecosystem. Nothing reads runtime or deployment
  dependencies as dependencies.
- **"what it's made of"** is architecture recovery: components clustered
  from the code tree, ports and wires read from deployment artifacts
  (Dockerfiles, compose, Helm), shown as a branch tree with verdicts. The
  names the owner saw (`OMAG-Server-Platform::active-metadata-store`,
  `::view-server`) are deployment-derived nodes, not Egeria's logical
  components.

## 1. Nesting depth

**Rule: depth is a request, the tree is lazy, and the words say how far was
looked.** Two levels is the survey's default scan, not a limit of the model.

- The pane shows a tree, not a flat list: a folder row expands to its
  children on demand (one read per expansion, from the repository's tree via
  the existing survey store or a direct listing), to any depth. Rows already
  scanned show their worthiness word; rows below the scanned depth show
  "not scanned · expand to scan" until expanded, never a blank and never a
  guess.
- The scan depth becomes a request parameter on the sub-resource survey
  (the same pattern as `arch_summary`'s depth), default 2, with a control
  on the pane: "scanned to depth 2 · scan deeper" which re-runs the survey
  at depth +1 for the expanded branch only, so a monorepo is not scanned
  whole to reach one folder.
- File systems reuse the same tree, with the walk as the scan; the depth
  words are identical. (File-system work itself waits on G1–G3 being
  complete, per the owner; this only keeps the design shared.)
- Cue: a folder with unscanned children carries a muted "…" after its
  count; a scanned folder shows "N worthy of M"; the expanded branch keeps
  its open state across re-render (the Curate toggle lesson of 2026-10-06).

## 2. Explicit publishing selection

**Rule: selecting a folder never implies publishing what is nested in it.
Publishing is of named items, previewed, and the ancestors needed to hold
them.** The "contained set whole" checkbox goes.

- Each row has the two-part selector of the cue vocabulary, headed "Publish
  to Egeria?": `Include | Leave out`, with × to clear; the selected segment
  filled in ink. A folder's own selector refers to the folder as an asset
  only. Selecting a folder shows, in the same cell, "folder only · N inside
  not selected", with a one-gesture action "include its M worthy children"
  that writes explicit choices on those children, visible as filled
  segments; nothing is ever implied.
- Worthiness is a **proposal**, not a selection: a worthy row reads
  "proposed · worthy · <reason>" in muted ink until a person includes it.
  This replaces the default-checked worthy rows, which read as a decision
  nobody made.
- Ancestors are shown, not hidden: when an included file's folder is not
  itself included, the folder row reads "needed as a container · not an
  asset of its own" with a hollow mark, and the preview counts it
  separately: "3 files · 1 folder you chose · 2 folders needed as
  containers".
- **The preview is the manifest, as a table** (the Curate manifest design of
  2026-10-06): what RE publishes, how many, and what is left out and why.
  Rows: `DataFile` assets · N; `FileFolder` assets you chose · N; container
  folders · N; left out · N (not selected) · M (not worthy, by rule). The
  button "Publish N items" sits at the table's top right; a blocker (nothing
  selected, the repository not yet in Egeria) is listed directly under it.
- Proof rows by GUID per item, written after a read-back, with the states
  "sent · waiting for Egeria", "published · read back <when>", "not
  published · <Egeria's sentence>"; a second press reuses by qualifiedName
  and never makes a second asset.
- Selection persists as RE's record ("saved · you · just now") separately
  from publishing, so a person can select over several visits and publish
  once. The same scope-record pattern as databases (declare, then commit).
**An option beside explicit selection, for discussion (the owner, 2026-10-07):
a selected folder or component becomes a new top-tier asset.** It would be
nested under its ancestor in the left-hand hierarchy and processed on its
own by surveys and curation: the Trellis monorepo as three resources,
Trellis with its common capabilities, Egeria Advisor and Resource Explorer,
each surveyed and curated the way Egeria-Trellis is today. The owner's
words: not the only way, an option for large complex components. My honest
read, no recommendation:

| | Explicit selection (above) | New top-tier asset |
|---|---|---|
| What it fixes | which files and folders become assets, with a preview | depth and scoping: a sub-tree gets its own surveys, questions, Curate scope, Egeria asset tree and investigations, at full depth, because it *is* a resource |
| What it costs | nothing new in the registry; one scope record per repository | registration of the child as a resource (slug, path within the parent, ancestry), per-asset surveys (each child runs the repository surveys on its sub-tree, so a monorepo with three children runs four times), a parent–child relation RE must keep and show, and an Egeria shape for "part of" that is not decided (nested `FileFolder` assets, or a `SoftwareComponent` under the parent's asset) |
| Where selection still applies | everywhere | inside each child, for its own files and folders, the same mechanism |
| Honesty | state words per file | the parent's counts must say "excluding N children surveyed on their own", or they double-count |
| When it pays | small explicit sets, which the owner expects to be the common case | a sub-tree that is a product in its own right, with its own owners, questions and lifecycle |

The two are not rivals: the child-as-asset is a way to scope, and explicit
selection is how anything inside a scope reaches Egeria. If the option is
taken, the registration of a child is an act on the parent's Curate pane
("make this a resource of its own"), previewed like any other write, and
the child appears under the parent in the sidebar with the parent's name
as its ancestry.

**Owner's placement (2026-10-07): backlog.** A child module or component as
a resource of its own is wanted eventually, not now: it must not stand in
the way of completion at this level. His reason, kept as the design's
purpose statement: it is a way to handle the messy reality of systems,
since navigating deeply nested structures becomes confusing and error-prone,
so it compartmentalises information behind clear boundaries. Recorded in
the backlog as "child module or component as a resource (compartmentalise a
large repository behind clear boundaries)", with this section as its
design seed.

- Not in scope: un-publishing. An item once published is left as history
  (roll-forward); the row can be "left out" for future publishes, with the
  word "published earlier · kept in Egeria".

## 3. Two kinds of dependency

**Rule: a dependency is a relationship with a kind, and the kind is stated
on every row. Two kinds today: build-time (what the code declares it
needs) and runtime (what the deployed thing talks to).**

| | Build-time dependency | Runtime dependency |
|---|---|---|
| Source in RE | dependency surveyor over manifests | deployment evidence: compose services, Helm values, Dockerfile `FROM` and env, Kafka/Postgres connection strings, ports and wires already read by architecture recovery |
| Row says | `<library> <version> · <ecosystem> · from <manifest>` | `<service or system> · <protocol or port> · from <artifact>` |
| Direction | this code → library | this deployment → that system (or that system → this, for consumers) |
| Egeria form on publish | as today (annotations per ecosystem; a `SoftwareComponent` dependency only on confirmation) | **not yet available:** Egeria plans new relationship types for "deployed by" and the other relations between a software library (a repository) and the things around it; until they exist and are deployed, a runtime dependency publishes as an annotation and nothing here is designed against a type that does not exist; **never lineage** (owner, 2026-10-05) |
| State words | measured / not measured for this ecosystem | measured from artifacts / "no deployment artifact found" / "runtime not surveyed" |

- **Decided (owner, 2026-10-07): one table with a `kind` column**, because
  it is the more extensible reading: a third kind (a data dependency, a
  service contract) is a new value, not a new section. The table is sorted
  and filterable by kind, each kind has its own count in the header line
  ("12 build-time · 3 runtime · 0 data"), and the heading never says
  "dependencies" alone. The source column names the manifest or artifact
  the row came from.
- For a database or a file system the build-time section does not apply and
  says so ("not applicable · no code"); the runtime section is where a
  Postgres server's clients and a Kafka topic's producers and consumers
  appear, read from Egeria when RE has not surveyed them.
- A runtime dependency found by RE is a **proposal** (proposed → confirmed,
  the E3 states), confirmed in Curate under "how it relates", and published
  as a fact only then, per the Enrichment-and-Curate publishing design.
- The repo→database "informs" relation from the Find/Integrate note §13 is
  a runtime dependency of this kind.

## 4. The component diagram: several blueprints, the kind in the name

The owner did not choose the components shown, and they are not Egeria's
logical components. His answer (2026-10-07): *these are all valid blueprint
diagrams; perhaps a classification for them later, but for now offer
several to a user to select, and write several, with the kind of blueprint
in the name: Egeria Logical Blueprint, Egeria Deployment Blueprint, Egeria
Build Blueprint, and so on.*

**Rule: a blueprint has a kind, the kind is in its name, and a repository
may have several.** The layers never share one box without the kind saying
which.

| Blueprint kind | Nodes | Source in RE | Today |
|---|---|---|---|
| **Deployment Blueprint** | deployed servers and services, their ports and wires | architecture recovery over deployment artifacts (compose, Helm, Dockerfiles) | what the pane draws now, unnamed; becomes "Egeria Deployment Blueprint" |
| **Build Blueprint** | the build's modules (Gradle subprojects, Maven modules, Python packages) and their declared dependencies | manifest parse and the dependency surveyor | partly present as recovered code clusters; named as its own blueprint |
| **Logical Blueprint** | the components the project's own documentation names (for Egeria: the platform, metadata access store, view server, integration daemon, engine host, the service families) with code clusters mapped under them | documentation surveyor plus a mapping a person confirms; recovery proposes the mapping | not built; the first one that needs a person's confirmation to exist |
| others later | a security blueprint, a data-flow blueprint | as declared | not designed |

**Which Egeria type, verified from the code on main (not assumed).** The
owner asked whether "component diagram" means a Solution Component
Blueprint. Yes. What RE writes today, when a curator accepts a candidate
blueprint, is a real Egeria **`SolutionBlueprint`** element
(`surveyors/arch_recovery/blueprint_materializer.py`, through pyegeria's
`SolutionArchitect.create_solution_blueprint`, properties class
`SolutionBlueprintProperties`, `contentStatus` "DRAFT" so it reads as a
proposal in Egeria too), qualifiedName
`SolutionBlueprint::<entity_type>::<slug>::<perspective>::<cluster_name>`.
Accepted components are real **`SolutionComponent`** elements
(`materializer.py`, `SolutionComponentProperties`, with
`solutionComponentType`), and membership is **`CollectionMembership`** from
the blueprint to each component or child blueprint (`egeria_outbox.py`), which
is correct because a `SolutionBlueprint` is a kind of `Collection` in
Egeria's model. **The zone comes from the step after materialisation, not
from the materialiser** (corrected 2026-10-07 after the coordinator read the
live 8813 log: "ZoneMembership['egeria-runtime'] set on 254dbbe6" on the
owner's blueprint-verdicts press). The chain, read from code: the
blueprint-verdicts route (`web/routes/curate.py`, the accepted branch)
materialises the `SolutionBlueprint`, then calls
`workflows/curate.promote_to_publish_zones(guid)`, which calls
`egeria_identity.publish_zones()` and `set_zone_membership`; `publish_zones()`
takes `EXPLORER_PUBLISH_ZONES`, else RE's `egeria.default_catalog_zones`, else
**falls back to `DEFAULT_PUBLISH_ZONES = ("egeria-runtime",)`**. The
component-verdicts route does the same for each accepted `SolutionComponent`.
"egeria-runtime" is therefore RE's own default promotion zone, not a
collection name. **A decision for the owner, not changed here:** the catalog
commit for databases was ruled on 2026-10-05 to write a zone only when one is
configured (`configured_publish_zones()`), because writing the fallback onto
a database element locked out the service identity, Egeria's survey engine
and the cataloguer. The blueprint and component promotion still uses the
falling-back helper, so an accepted blueprint on the dev platform lands in
`egeria-runtime` by default. **Decided (owner, 2026-10-07): promotion adopts the commit's
configured-only rule.** RE writes a zone only when the deployment configured
one (`EXPLORER_PUBLISH_ZONES` or `egeria.default_catalog_zones`); otherwise
the accepted element carries no zone and the verdict row reads "accepted ·
zones left to Egeria · everyone visible" (or "accepted · zone <name>"). One
rule for every zone RE writes, the deployment's intent and never RE's guess.
The builder reads first whether materialisation puts the Draft element into
the private zone (`PRIVATE_ZONE`): if it does, accept must clear that zone
when nothing is configured, or the element stays private; if it does not,
accept is the content-status change alone. Tests: with no zone configured an
accepted blueprint and component carry no ZoneMembership and are readable by
the service identity; with a configured zone they carry exactly that zone.
**Backlog (owner):** zone configuration in the Admin panel, so a deployment
can set different defaults (publish zones, and later per-kind or per-group
defaults) without an environment variable. The "several blueprints" design
below does not depend on any of this. So the owner's
rule maps directly: each blueprint kind is its own
`SolutionBlueprint` with the kind in its `displayName` and in the
`<perspective>` slot of the qualifiedName ("Egeria Deployment Blueprint",
"Egeria Build Blueprint", "Egeria Logical Blueprint"); nothing new in
Egeria is needed to write several, and a blueprint classification can come
later without renaming.

What the pane does:

- "what it's made of" lists the blueprints RE can offer for this
  repository, each with its kind, its source and its state: "Egeria
  Deployment Blueprint · recovered from 3 artifacts · 12 units · proposed",
  "Egeria Build Blueprint · from build.gradle · 41 modules · proposed",
  "Egeria Logical Blueprint · needs your confirmation of 6 components ·
  not yet drawn". A person selects which to view and which to write.
- Each blueprint is drawn on its own; a node carries its source one gesture
  away; accepted nodes in full ink, proposals muted with the word
  "proposed" (the designer's ports round: components are proposals, ports
  are readings, a person's verdict wins). Verdicts stay per blueprint, since
  accepting a deployment unit says nothing about a logical component.
- Writing to Egeria: each blueprint the person selects is written as its
  own element with the kind in its name (the owner's rule), related to the
  repository; the accepted nodes under it as its components, the proposed
  ones not written. Proof rows by GUID as everywhere. A later blueprint
  classification in Egeria is possible and not designed now; the kind lives
  in the name until then.
- Cross-blueprint links (a logical component is realised by these build
  modules and deployed as that unit) are facts a person confirms, one per
  pair, shown as a column on the logical blueprint's rows; they are the
  valuable part and the slowest, so they come after the three kinds exist.
  **Their Egeria form is not yet available:** these are the same planned
  "deployed by" family of relationship types as §3's runtime dependencies;
  until deployed, a confirmed link is RE's record and an annotation, and
  the design here names no type.

Order: name the existing diagram as the Deployment Blueprint and add the
selector (small); the Build Blueprint from the manifests already parsed;
the Logical Blueprint last, since it needs a person's confirmations to be
drawn at all.

## 5. What this changes elsewhere

- The sub-resource survey gains a depth parameter and lazy expansion; the
  database Curate scope tree gets the same lazy pattern for columns.
- The publishing selection becomes a scope record with proof rows,
  the same mechanism as the database catalog scope; one code path for
  "declare, preview, commit" across kinds.
- The dependency kind reaches the context pack (a pack lists runtime
  dependencies as facts once confirmed) and the Find/Integrate fit rows.

## 6. Questions

**Owner:** none open. The decisions of 2026-10-07 are recorded in place:
dependencies as one table with a kind column (§3); several blueprints with
the kind in the name (§4); promotion adopts the configured-only zone rule
(§4); the child-as-resource option and Admin zone configuration go to the
backlog (§2, §4). Only whether file-system trees should appear in the
designer's drawings now, even though their build waits, is his to say.

**Designer:** the tree rows' cues at depth (indent, the "…" for unscanned,
the hollow container mark); the manifest table for a mixed selection; the
dependency table with its kind column and per-kind counts; the blueprint
selector and how several blueprints read side by side.

**Egeria lead:** the planned relationship types ("deployed by" and the rest) and when they land, so §3 and the cross-blueprint links can name them; whether a container folder that holds published files
but is not itself "an asset of its own" should carry a classification
saying so.
