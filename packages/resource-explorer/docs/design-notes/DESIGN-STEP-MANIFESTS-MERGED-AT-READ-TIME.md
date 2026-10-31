# Design: installed step manifests merge into the analysis catalog at read time

Status: built on branch `claude/re-ext-followups-928989`, 2026-10-29. Follows finding F1 of the Architect's
extensibility gate walk (`GATE-RESULT-EXTENSIBILITY-FS-2026-10-28.md`): "adding a step still needs a regeneration
commit in RE." It changes how `analysis_catalog.yaml` is read, which the Architect flagged as a design change, so the
decision is written here.

## 1. What changed

`analysis_catalog_reader._load` still reads `configdata/analysis_catalog.yaml`. It now also reads the installed
`pyegeria.analytics` step manifests (`trellis.analytics` is only read as an alias) (`survey_step_host.discover_steps`, per resource type) and merges them in, using the
same function that writes the generated block (`survey_step_host.catalog_entry`), so a merged entry and a generated one
cannot differ in shape:

- a manifest whose step has **no committed entry** is appended to its resource type's list;
- a manifest whose step **has a committed generated entry** replaces it in place (same position, same id);
- a manifest that takes the id of a **built-in** (non-`provided_by`) analysis is refused and logged, as `attach` already
  refuses it.

Adding a step is now: install the package, restart RE (or call `survey_step_host.refresh`). No commit in RE. The card,
the run, the declaration, Chat's compile and Curate's publish table all read through `_load()`, so they see it.

## 2. Decision: the committed generated entries stay, as the fallback

The entries already on main between the GENERATED markers are **kept**, and the generator stays. Reasons:

1. **A step whose package is absent still needs an entry.** The catalog is the only record of a step that is not
   installed in this process (the web process may lack a package the Prefect worker has, or the reverse). The "step not
   installed here: 'x' comes from package P v" card, the Run button's disabled reason, and the "N missing: ..." part of
   the start-up line all come from a catalogued entry with no manifest to match. Removing the entries would turn "not
   installed here" into "does not exist", which is absence read as nothing.
2. **Installed wins.** When the package is installed its manifest is the source, so the committed copy can no longer
   drift from it in what RE shows; the staleness test keeps the committed copy honest as a fallback only.
3. **Nothing is lost by keeping them.** A step added later and never regenerated simply has no fallback: it shows as
   absent only if its package is not installed, which is the true state of the world.

So regeneration is now **optional**: run it when you want a package-less environment to know the step exists and say why
it cannot run. Nothing breaks if you do not.

## 3. Decision (project owner, 2026-10-29): a step's new questions carry a namespaced id

**Decision (project owner, 2026-10-29):** "Namespaced id in manifest." A step's manifest may carry new questions, each
with its text and an id under the step's own prefix (for example `fs_permissions:Q1`). Resource Explorer merges them at
read time, as it merges the analysis entry, with no central allocation.

This closes the open question this note first recorded (who allocates ids; what a manifest carries; where the merged row
lives; retirement). As built:

- **Who allocates.** The step does, inside its own prefix `<step name>:`. A manifest whose question id lacks that prefix,
  or repeats one, does not load, with a sentence saying so. The central `Q-0001` sequence and `reserved_question_ids` are
  never touched, so two packages cannot collide with each other or with the catalogue.
- **What a manifest carries.** `question_defs`: a list of `{id, question, stage, perspectives, purposes, levels,
  rationale}`; only `id` and `question` are required. Empty `stage` is the step's stage; empty perspectives and purposes
  are the manifest's. The ids are added to the manifest's `questions` when left out of it, so `questions` stays the one
  list of what the step answers (and so the declaration's `answers`).
- **Where the row lives.** `question_catalog_reader._load` merges them into the `<resource_type>_questions` sections of
  every kind the step measures (`_merge_step_questions`), `answering.kind: analysis` with the step as its analysis. No
  configdata row. A refresh (`survey_step_host.refresh`) re-reads them.
- **Retirement.** The row exists while the package is installed. Persisting it past an uninstall would be a registry table
  (DDL) and is **not** built: an old stored answer still names the question by its id and text, which it stores itself.
  If the owner wants questions to outlive their package, that is a separate decision with DDL.
- **Unknown-id warnings.** A step's own `question_defs` ids are not warned about as "not an id in the question catalog".
  The discovery check is skipped when the question catalogue itself discovers the manifests (`discover(check=False)`),
  which would otherwise ask it to load itself.

## 4. One type declaration (F10, F11; 2026-10-29)

A measure's Egeria type was declared twice: in the manifest (`annotation_type`, or `measure_annotation_types` for a
measure other than the step's own) and in the measure's `declaration.measures[].annotation`. A difference raised a
`ValueError` at load, so changing one field made the step vanish from the entry point (F10). And the publish table read
only the second, so a step that wrote only the first showed `generic measure · no native type declared` (F11).

Now one derives from the other (`StepManifest._one_type_declaration`):

- the manifest names a type for the measure and the measure declares none: the measure's `annotation` is filled from the
  manifest. A library step's measure then reads as that native type, including a default `ResourceMeasureAnnotation` the
  author wrote out;
- the measure declares a type and the manifest names none: the manifest follows the measure (the step's other measures
  keep what they inherited);
- both are written and differ: the error names both fields, since no default settles a contradiction.

The committed fallback entries in `analysis_catalog.yaml` were regenerated: the DB library steps' measures now carry
`annotation: ResourceMeasureAnnotation`.
