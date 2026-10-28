# License spelling sweep — implemented

Design ruling: user-facing text spells it US-style, "license". A user searching
"license" could not find a row spelled "licence". Swept: the `/next` frontend
labels, the question catalog CSV and its generated artifacts
(`question_catalog.yaml`, `scouting-questions.md`, the regenerated survey
definitions and their `.generated.json` provenance), the question-text key in
`facts.py`, surveyor finding text (`foss_scorecard.py`, `git_statistics.py`),
`analysis_catalog.yaml` descriptions, comments and docstrings, and the
render-harness and pytest assertions.

## Intentional remaining "licence" (the stored key)

The enrichment fact key `licence` is stored data, like an analysis id. It is
not user-facing and renaming it would be a migration with no reader benefit, so
it stays permanently. Any `grep -i licence` hit in the swept scope must be one
of these:

- `resource_explorer/web/static/next/stages/curate.js` — `w.licence` (reads the stored key)
- `resource_explorer/web/static/next/stages/enrichment.js` — `key: 'licence'` (the row's label is "License")
- `resource_explorer/web/static/next/stages/context.js` — a comment naming the `` `licence` `` key
- `resource_explorer/curate_plan.py` — `enrichment.get("licence")` and the `"licence"` output key
- `frontend-build/test-harness/context-tab-sections.test.mjs` — the `licence:` fact key in the fixture
- `frontend-build/test-harness/context-parity-routing.test.mjs` — `data-confirm="licence"`
- `tests/test_enrichment_fields.py` — `"key": "licence"` fixture

## Regeneration note

Run generator scripts with `PYTHONPATH=.` from `packages/resource-explorer`.
`scripts/generate_repo_survey_definition.py` otherwise resolves `resource_explorer`
through the editable install (another checkout) and reports "unchanged" against
stale question text.
