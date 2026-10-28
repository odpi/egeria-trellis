# RULING — Blueprint and component names say what the element represents (2026-10-08)

Design session ruling at the owner's word: *"perhaps when naming blueprints
and components we should include a suffix with the kind of blueprint or
component they are; in other words this blueprint reflects either a code
module or a container definition rather than a live runtime. We might add a
classification later that could replace the naming convention."*
Companions: `DESIGN-BLUEPRINT-NODE-ADMISSION.md` (evidence classes),
`DESIGN-REPO-TO-RUNTIME-COMPONENT-LINKS.md` (artefact versus runtime),
`DESIGN-BLUEPRINT-BENCHMARK-EGERIA-WORKSPACES.md` (the container shape).
No build until the owner's go.

## 1. The vocabulary: one small fixed list, derived from the evidence

A suffix says what the element **represents**, never what it is called or
where it came from. The word is derived from the evidence class RE already
holds, so it is never typed by hand:

| Suffix | The element represents | Derived from |
|---|---|---|
| **(code module)** | a unit of source the build defines: a Gradle or Maven module, a Python or Node package | a package manifest in the repository (`built_here` by manifest) |
| **(container definition)** | how a container is to be built or run, as this repository declares it: a Dockerfile, a compose service with a `build:` context or an `image:` | a Dockerfile or compose service in the repository (`built_here` by Dockerfile, `shipped_here`, `referenced_only`) |
| **(image)** | a built artefact by name, with no claim about where it runs | an image name that a repository publishes (`published_images`) or references, when the row is about the artefact itself (the `builds` / `runs` targets of the two-ends table) |
| **(runtime)** | a live running instance RE has observed or registered: a database server, a platform RE connects to | a registered resource or a live read, never a file in a repository |
| *(none)* | an element Egeria's content pack defines | adopted by its content-pack qualifiedName; its name belongs to the content pack (§3) |

Five words, closed. "Deployment unit" is a container definition;
"manifest package" is a code module; a topic or a database named in a
server configuration document is a container definition's *content* and
takes its container's word until it is observed live, when it becomes a
runtime. A sixth word is added only by a ruling, never by a builder.

## 2. Where it applies: displayName only, never qualifiedName

- The suffix lives in **`displayName`** and nowhere else. The
  `qualifiedName` is the identity (stability, adoption by name, the
  content-pack forms) and never carries it; the proof rows and the
  dependency table key on qualifiedName as today.
- **Components:** `<name> (<suffix>)`: "open-metadata-implementation (code
  module)", "egeria-main (container definition)", "odpi/egeria-platform
  (image)".
- **Blueprints:** `<Repository> <Kind> Blueprint (<represents>)`, the
  represents word in the plural when the members are of one kind: a
  blueprint built from egeria_git's compose and Dockerfiles is **"Egeria
  Deployment Blueprint (container definitions)"**; one built from its
  Gradle modules is "Egeria Build Blueprint (code modules)"; one drawn from
  registered live servers would be "Egeria Deployment Blueprint
  (runtime)". A blueprint whose members mix kinds takes no suffix and its
  members carry theirs.
- **Sub-resources** (files and folders) keep the rule already ruled,
  `<path> · <repository>`, and take **no** suffix: a file represents
  itself, and the kind word would add nothing a person needs.
- **Composition with the repository:** a component's displayName does not
  repeat the repository (the owner's benchmarks use plain names and the
  blueprint names the repository); the repository is in the qualifiedName
  and in the blueprint's name.

## 2a. The identity: kind and repository, never the cluster root (ruled 2026-10-08)

The merged container-shape materialiser (#556) still formed the
qualifiedName as `SolutionBlueprint::<entity_type>::<slug>::<kind>::<cluster_name>`
and keyed its cache on the cluster name, so the identity depended on the
root cluster ("OMAG-Server-Platform"), which a member's name must never
be. Ruling for the follow-up:

- **Form:** `SolutionBlueprint::<entity_type>::<slug>::<kind>`, the kind
  as the last segment (`deployment`, `build`, `logical`, `environment`),
  no cluster segment: `SolutionBlueprint::repo::egeria_git::deployment`.
  The Egeria `identifier` property is `<SLUG>-<KIND>` upper-case, as the
  owner's own blueprints carry it (`EGERIA-GIT-DEPLOYMENT`); displayName
  per §2.
- **Several of one kind in one repository:** the second needs a person's
  identifier at accept time (the owner's benchmarks are exactly this:
  `EGERIA-WORKSPACES-RUNTIMES`, `-SERVERS`); the pane asks for it when a
  blueprint of that kind already exists, and it becomes a fifth segment
  `::<identifier>`, never derived from the cluster root. The first of a
  kind has no identifier segment.
- **The cluster name** stays RE's internal key for the cluster, a column
  on the registry row, never in the Egeria identity or the cache key.
- **Cache key:** `(entity_type, slug, kind, identifier or '')`; rows written
  under the old `(…, perspective, cluster_name)` key are read as legacy by
  a mapping and never written again.
- **Legacy-named blueprints** (written before #556): adopted by the legacy
  form, never duplicated, never created anew; the adoption writes an
  activity row "adopted legacy-named blueprint <guid> · delete it in Egeria
  to recreate under the new name". For 254dbbe6 the owner deletes first.
- **Adoption is by provenance RE wrote on the element, then by a guarded
  fallback** (ruled 2026-10-08 after PR/CI's review found that a lost or
  cleared registry row lets the wrong cluster adopt a plain-kind element,
  which the reset's cache clearing makes likely). RE writes on every
  blueprint it creates `additionalProperties`: `re_entity_type`,
  `re_slug`, `re_kind`, `re_cluster_key` (RE's internal cluster key),
  `re_identifier` (when a person gave one), `re_version`. On finding an
  element by qualifiedName: (a) `re_cluster_key` equals this cluster's →
  adopt, and rewrite the registry row if it was lost (the element is the
  record, the registry a cache); (b) present and different → refuse: "an
  element named <qn> already exists in Egeria for another cluster (<its
  key>) · give this one an identifier"; (c) no RE provenance (not RE-made,
  or made before this slice) → adopt only when the registry has no row for
  this cluster, the displayName equals what this cluster would produce,
  and no other cluster's row records that qualifiedName; else refuse with
  the same sentence. The qualifiedName search is typed and verified; more
  than one hit is ambiguous and refuses. A property RE wrote survives both
  a cleared registry row and a later displayName change, which neither a
  row nor a name does.
- **Tests:** the three adoption cases, plus a cleared registry row with
  provenance present adopts and rewrites the row; the egeria_git fixture
  yields the new qualifiedName and identifier; a second deployment blueprint without an identifier is
  refused with "a Deployment Blueprint already exists for egeria_git · give
  this one an identifier"; a legacy-named blueprint in the fake is adopted
  with the row; no qualifiedName contains a cluster root's name.

## 3. Adopted content-pack elements are never renamed

A content-pack element's name belongs to the content pack: "OMAG Server
Platform", "View Server", "Apache Kafka". When a repository-derived
component maps to one (by `deployedImplementationType` or image family),
RE **adopts** the element, writes no second component and changes no name;
on RE's own pane the row reads "OMAG Server Platform · adopted from the
content pack · matched by image odpi/egeria-platform", so the person sees
both the adopted name and the evidence that matched it. The suffix never
reaches an adopted element. If RE needs to say what the adopted element
represents *in this repository* (a container definition that runs it),
that is a `(container definition)` component of RE's own, composed under
or linked to the adopted one, not a rename.

## 4. How it meets the repo-to-runtime link, and the classification that replaces it

The two kinds the owner wants told apart are exactly `(image)` and
`(runtime)`: a built artefact by name versus the live instance that runs
it, link B of the repo-to-runtime note. The suffix makes the two readable
in any list before the "deployed by" relationship family exists; once it
does, the relationship says the same thing and the suffix becomes
redundant on the elements it joins.

**The classification that would replace the suffix.** None of Egeria's
existing classifications says "what this element represents" in these
terms; `deployedImplementationType` is a property naming the technology,
not the representation. So the replacement is a new classification, to be
proposed to the Egeria leads under the follow-Egeria's-lead rule, named
here provisionally **`Represents`** with one property `kind` whose valid
values are the five words of §1 (a valid value set, evolvable). Until it
exists, the suffix carries the meaning and nothing else does.

**The migration from suffix to classification**, when it lands, is
additive and idempotent: for every element whose displayName ends in one
of the five suffixes, classify it `Represents(kind=<word>)` and
rename-forward the displayName to drop the suffix; the proof row records
both; a second run finds the classification present and does nothing. No
element is created or deleted; qualifiedNames are untouched, which is why
§2 keeps them clean.

## 5. The container-shape materialiser now in gating

`re/blueprint-container-shape` (02f0b8c6) writes displayNames to Egeria for
the first correctly shaped blueprint. **Recommendation: fold the suffix in
before it lands**, as one small addition: a `display_name(name, kind)`
function from the evidence class, used for the blueprint's name and each
RE-made component's name, with two assertions (an adopted content-pack
member keeps its name; the blueprint reads "Egeria Deployment Blueprint
(container definitions)"). It is a few lines, and the alternative is that
the first blueprint the owner gates goes out under names the next slice
renames forward, which teaches the wrong lesson at the gate. If the
builder is past the point where this is small, it becomes the first
follow-up and the implemented note says the names are pre-suffix.

## Gate (owner, by use, on top of the container-shape gate)

On egeria_git the blueprint reads "Egeria Deployment Blueprint (container
definitions)"; its member "OMAG Server Platform" keeps the content-pack
name with the adopted word on RE's pane; a Gradle module in the Build
blueprint reads "<module> (code module)"; the dependency table's image
targets read "(image)"; no qualifiedName contains a parenthesis.
