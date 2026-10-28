# RULING — File types are a measurement, published as Egeria's own profile annotations; the DataSet-per-type press is retired (2026-10-07)

Design session ruling at the owner's word: *"What Egeria does in the file
and folder survey is create a csv file as an output of the survey (perhaps
an annotation or part of the report spec) that lists file type counts;
treating it as an annotation makes more sense than creating datasets."*
Companion: `BRIEF-CURATE-EXPLICIT-SELECTION-REPOS.md` (brief 2a, how
contents get cataloged), `DESIGN-ENRICHMENT-AND-CURATE-PUBLISHING-TO-EGERIA.md`
(annotations versus facts).

## What RE does today, read on main e092d314

- **The "Catalog file types" press** (brief 1's own section, `repo_publish.py`
  `file_types_commit`, route `catalog-elements` in `routes/egeria.py`)
  creates **one `DataSet` per file type per repository**, with no files
  inside it, linked to the repository asset by a relationship the code
  itself says is expected to fail. A DataSet with no members that stands
  for "there are 212 Java files" is a count wearing an asset's clothes.
- **File types are already measured** by the file inventory step
  (`project_file_inventory`, read by file structure, language,
  classification and the sub-resource survey) and reach the survey report
  only as `ResourceMeasureAnnotation` rows with totals, not as a profile.
- **Egeria's own folder survey** (`FolderSurveyService`,
  `SurveyFolderAnnotationType`, read by the coordinator) writes, as
  annotations on the survey report and never as assets: "Capture File
  Counts" (`ResourceMeasureAnnotation`: file and directory counts, number
  of file types, asset types, deployed implementation types);
  `PROFILE_FILE_EXTENSIONS` and `PROFILE_FILE_TYPES`
  (`ResourceProfileAnnotation`: the count per extension and per file type,
  types being Egeria reference data); `PROFILE_ASSET_TYPES` and
  `PROFILE_DEP_IMPL_TYPES` (the asset and deployed-implementation types that
  cataloguing the files *would* create); `PROFILE_FILE_NAMES`
  (`ResourceProfileLogAnnotation`: per-name counts, large, logged to a CSV
  the annotation points at). Cataloguing is a separate act that creates
  assets.

## The principle (owner, 2026-10-07): follow Egeria's lead

*"Our needs may evolve but I think it is useful to follow Egeria's lead
here."* So: **Egeria's folder-survey annotation types and names are the
default for every file-type and file-count measurement RE publishes, for
repositories and file systems alike.** Any departure is named in this note
with its reason. When RE's needs differ later, RE **extends with a distinct
annotation type** (its own name, declared through the annotation-type
registry) and never changes the meaning of one of Egeria's.

Departures, today: none in meaning. Two in scope: "Profile File Names" is
not published by default (size; Egeria writes it as a CSV log and so would
RE, on request); and RE adds the envelope in `additionalProperties`, which
Egeria's types do not carry, as the leads already agreed. A **third, in a
field value**, found by the builder reading Egeria's source
(`SurveyFolderAnnotationType.getAnalysisStep()`): Egeria stamps the four
profile annotations with `analysisStep = "Profiling Associated Resources"`
(all four, including File Names, whose constant says `PRODUCE_INVENTORY`
but whose getter returns the profiling step). RE used to stamp `analysisStep =
"FileInventory"`, because RE's attribution of annotations to its own
analysis (`egeria_annotation_materializer`, the "By analysis" readers) read
`analysisStep` to decide which step a row belongs to. **Decision and its
implementation: ended by commit b628d7c0 on the file-types branch.**
File-type annotations now carry Egeria's `analysisStep` "Profiling
Associated Resources" verbatim; RE attributes by its own key,
`producingStep` in `additionalProperties` (RE's distinct property, per the
extend-never-change rule): `step_of()` reads `producingStep` first and
falls back to `analysisStep` for annotations written before. So the
departure was named and closed in the same slice.
Two more notes from the same read: Egeria's log annotation
(`ResourceProfileLogAnnotation`) holds `resourceProfileLogGUIDs`, references
to log elements in Egeria, not a file name; RE's `logFile` in
`additionalProperties` is therefore RE's own extension, a distinct
property, never a reinterpretation of Egeria's field, and when RE can
write the CSV as an Egeria log element it fills `resourceProfileLogGUIDs`
and keeps `logFile` beside it. One addition that
is RE's own and therefore its own type when it exists: the *selection*
profile ("what cataloguing the chosen items would create" against "what the
whole tree holds"), which Egeria's survey has no reason to produce.

## The ruling

1. **File types are a measurement.** RE publishes them as Egeria's own
   profile annotations in the survey report, with Egeria's own annotation
   names so a steward reading a repository's report sees the same rows a
   folder survey would write:

   | RE measurement (from `project_file_inventory`) | Published as | Annotation name (Egeria's, verbatim) |
   |---|---|---|
   | file count, directory count, distinct extensions, distinct types | `ResourceMeasureAnnotation` (exists) | "Capture File Counts" |
   | count per extension | `ResourceProfileAnnotation`, `valueCount` map | "Profile File Extensions" |
   | count per file type (RE's `resolve_technology_type` → Egeria's deployed-implementation and file types) | `ResourceProfileAnnotation` | "Profile File Types" |
   | the asset types cataloguing the selected files would create | `ResourceProfileAnnotation` | "Profile Asset Types" |
   | count per file name | **not published by default**; when a person asks, a `ResourceProfileLogAnnotation` whose CSV is the inventory listing (large) | "Profile File Names" |

   The envelope applies: `measured` from this run's inventory, `scope`
   WHOLE or PARTIAL when the inventory was credential- or depth-limited,
   `producingRun` the survey run. `survey_report.py` gains
   `ResourceProfileAnnotation` and `ResourceProfileLogAnnotation` classes
   (the reader and materialiser already know the first by name).

2. **The DataSet-per-type press and the "Catalog file types" section are
   retired.** The route stays only to answer 410 with the sentence "file
   types are published as profile annotations in the survey report; files
   are cataloged by selection on Curate" for any old caller. The manifest
   row "Egeria gets the file types you chose · N DataSet elements" goes.

3. **DataSets already created stay. Roll forward, never undo** (the owner's
   rule): they are not deleted and not archived (the ISSUE-117 block holds
   anyway). RE's record marks each `sub_resources`-style row for them
   "retired mechanism · kept in Egeria · superseded by the Profile File
   Types annotation of <run>", and the Publish band's Egeria-reports
   component lists them under "earlier publishes" with that word. A later
   Egeria-side cleanup, if the owner wants one, is a stewardship act in
   Egeria, not an RE press.

4. **How it fits brief 2a.** Contents get cataloged by **explicit selection
   of files and folders**, which creates `DataFile` and `FileFolder` assets
   under the repository, one per chosen item, previewed and counted; the
   types are a profile of what exists, never a way to catalog it. The
   "Profile Asset Types" annotation is the bridge: it says what cataloguing
   the current selection would create, so the manifest's "Egeria gets · N
   DataFile" and the profile agree by construction (one test).

## The same mapping for file systems (owner: "this will also apply to file systems")

Egeria's folder survey *is* the file-system survey, so a file system is the
natural home of these annotation types; a repository is the same
measurement taken on a checkout. One mapping, two kinds; the table above
applies to both, with "file system" wherever it says "repository".

Where RE's file-system survey stands, read from code only (file-system
builds stay on hold by the owner's order):

- `surveyors/filesystem/local_filesystem_surveyor.py` already measures in
  parity with Egeria's names by its own comments: one walk collecting
  counts, extensions, OS-level facts, with "Capture File Counts" parity
  noted in the code, and data-file profiling through the data profiler;
  `egeria_filesystem_surveyor.py` reads Egeria's native folder survey back
  with "Capture File Counts" and "Profile File Extensions" parity noted.
- **The gap**: what RE *publishes* for a file system is
  `ResourceMeasureAnnotation` (3), `SchemaAnalysisAnnotation` (2, the data
  profiles), `RequestForActionAnnotation` (3) and one
  `ClassificationAnnotation`; **no `ResourceProfileAnnotation`**, so the
  per-extension and per-type counts RE measures reach Egeria as properties
  on a measure annotation rather than as the profile annotations Egeria's
  own survey writes. The file-system slice, when the owner lifts the hold,
  is the same two classes and the same three profile annotations as the
  repository slice, emitted by the local surveyor, plus "Profile File Names"
  on request; the hybrid surveyor then compares RE's profile with Egeria's
  own row for row, which is the parity check the comments promise.
- Cataloguing contents of a file system follows brief 2a's selection
  mechanism unchanged (the `resource_scope_events` table is generic across
  kinds by design).

## Naming of selected sub-resources (the owner's LICENSE worry)

Read from `egeria_publisher.publish_sub_resources`: the qualifiedName is
unique and carries the repository, `GitHubRepository::<github_url>::<path>`,
so no collision in Egeria's record; but the template's `fileName`
placeholder is the **basename**, and the asset's `displayName` derives from
it, so a published `LICENSE` reads as "LICENSE" in every repository and a
search or a list shows thousands of indistinguishable rows. The worry is
real.

Rule: **the display name carries the path and the repository; the file
name stays the basename.** Set `displayName` explicitly in
`replacementProperties` (where RE already sets `qualifiedName`) as
`<relative path> · <repository display name>` ("LICENSE · egeria_git",
"docs/README.md · egeria-workspaces"), `fileName` stays `LICENSE`, and
folders likewise `<relative path>/ · <repository>`. Egeria's own file
cataloguer uses the path for the name for the same reason. Existing
published files are re-named forward on the next publish of the same
locator (the publish reuses by qualifiedName and updates properties),
never by a sweep.

## Slice, small, after brief 2a on the owner's go

- Two annotation classes; the file inventory step emits the three profile
  annotations (names verbatim); the publisher sends them with the report.
- Retire the press: route → 410 with the sentence; the Curate section and
  manifest row removed; the retired-mechanism word on existing rows.
- `displayName` in `replacementProperties` for files and folders.
- Tests: the report for a fixture repository carries "Profile File
  Extensions" and "Profile File Types" with counts equal to the inventory;
  the asset-types profile equals the selection manifest's counts; the
  old route answers 410; a published LICENSE's displayName names the path
  and repository and its fileName is the basename.

Gate (owner, by use, egeria_git): the Publish band's report shows the two
profile annotations with counts; "Catalog file types" is gone; publishing
a selected LICENSE shows "LICENSE · egeria_git" in Egeria's listing.
