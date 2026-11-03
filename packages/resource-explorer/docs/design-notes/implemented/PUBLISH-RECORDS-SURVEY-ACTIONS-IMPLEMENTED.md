# Publish records the surveys it starts (implemented)

Environment proof: `uv run python -c "import resource_explorer; print(resource_explorer.__file__)"` printed
`/Users/dwolfson/localGit/egeria-v6/trellis-re-publish-survey-proof/packages/resource-explorer/resource_explorer/__init__.py`.

## Defect
`EgeriaDatabaseSurveyor._catalog_and_survey` (Publish / "Catalog & Survey in Egeria") initiated a server and a
database survey and kept the engine-action GUIDs in local variables only. No `step_runs` proof row was written, so
the survey never appeared in the native-survey list or the read-back sweep, while the classic UI said a survey
was initiated.

## What changed
- `surveyors/database/egeria_database_surveyor.py`: after each initiation that returns a non-empty GUID,
  `_record_survey_submission` calls the same recorder a native run uses,
  `registry.record_native_survey_submission`. No new table, no second recorder. The result gains
  `survey_submissions` (only surveys whose row was actually written). `submitted_by` is passed in
  (`catalog_and_survey` / `publish_local_survey`), never read from a ContextVar. `_initiate_survey` remembers
  which process it used.
- Recorder arguments: entity_type `database`; slug from the entity; process qualified name = the process
  `_initiate_survey` actually used (a discovered Survey Definition process, else the configured `survey_existing`
  process for the technology type); surveyed_at = submission time (UTC naive ISO); engine_action_guid = the GUID
  returned; submitted_by = the signed-in caller read in the route.
- `web/routes/databases.py`: `PublishResult.survey_submissions`; route reads `requested_by()` in the request.
- `web/static/index.html` (classic UI, publish handler): the "survey initiated" item/detail now derive from
  `survey_submissions`; `report_guid` (a SurveyReport RE created) is shown as "Survey Report", not as a survey
  action. Left alone: the toast "cataloged in Egeria", the survey-definition catalog-retry path, and /next
  (it never calls this route).
- A failed or empty initiation records nothing; a proof row that cannot be written is logged and not claimed.

## Not verified
Nothing ran against live Egeria; every test uses a temp SQLite registry and a stub Egeria. The server-survey
row is recorded and swept, but `native_survey_rows` lists processes for the database technology type only, so it
is not shown in the list. A discovered (non-configured) process name is swept but has no list row either.
