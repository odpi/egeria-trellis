# Blueprint node admission: implemented (2026-10-08)

Built from `DESIGN-BLUEPRINT-NODE-ADMISSION.md`, branch `re/blueprint-node-admission`.

- Each component carries an evidence class: built here, shipped here, or referenced only
  (`surveyors/arch_recovery/admission.py`). The class and a short "from <file>" / "image <name>" sentence
  are persisted on the row and shown on the node.
- Referenced-only compose services are not components. They are runtime rows in the dependency table
  (`dependency_table.py`) and are recorded as a whole-resource `architecture_admission` finding with the
  lines of what was found and not admitted.
- A repository with only referenced-only services gets zero components and the sentence pointing at
  Dependencies · runtime. The "Environment Deployment Blueprint" is offered in the selector only then.
- A person may reclassify a node with a reason (`node_admission.py`): one never-updated setting per entry,
  bound to the node's directory, honoured by the next survey.
- Compose services have no files, so their slug is their scope. A name shared by several directories is
  qualified by the whole directory path for every one of them, decided from the full census.
- A compose service merges into a Dockerfile/manifest node only if it builds that directory.

## Published-image rule (what makes a service "shipped here")

An image counts as published by this repository only on a PUBLISH SIGNAL, and image names are compared for
equality (registry host, tag and digest removed), never by last segment across namespaces:

- a CI workflow step that builds, tags or pushes a namespaced image (`odpi/egeria-ui`);
- a CI step that pushes a bare image name, only when it equals one of the repository's own manifest
  package names (the manifest says what the bare name is; the CI step is the signal);
- a compose service that builds (`build:`) and names its image.

A manifest package name alone is not a signal: a package called `redis` does not make `bitnami/redis`
shipped. Whatever cannot be decided from this evidence stays **referenced only**, the honest default:
a repository's image published by a pipeline outside the repository, an image published under a bare name
with no matching package, and an image whose namespace differs from every signal. A person can reclassify.

## Same-named compose services, scoped runs

Slug, merge and class of a compose service are decided from the whole census, so a scoped run agrees with
the full run. A service merges into a Dockerfile/manifest node only if it builds that node's directory: the
directory of its `dockerfile:` (resolved against the context, itself against the compose file), else its
build context. Derived slugs (`.svc`, hash) are made unique against every slug in use.

## Consequences of the tightened rule (fail-safe, a person can reclassify)

- A service whose image is built in the repository by a Dockerfile but published only through CI tags
  written as variables (`${{ ... }}/app`, or build-push-action variable tags) is DEMOTED to referenced only:
  the variable cannot be resolved to an image name, so there is no publish signal.
- Cross-repository `built by` links are stricter: a bare `egeria-platform` no longer links to
  `odpi/egeria-platform`. `docker.io/library/nginx` normalises to `library/nginx`.
- A manifest twin of a compose service merges when the service's build context OR its Dockerfile's directory
  is the manifest's directory; a Dockerfile twin merges when the Dockerfile's directory is its directory.
  A compose file in the repository root unit "." is not matched to a manifest by name (its plain slug is
  `.::<name>`), as before.
- The published-image list is computed once from the census (build_components fills `census_info`) and is the
  list used both to classify and to record, so full and scoped runs agree. Scoped runs never write the
  not-admitted record.
