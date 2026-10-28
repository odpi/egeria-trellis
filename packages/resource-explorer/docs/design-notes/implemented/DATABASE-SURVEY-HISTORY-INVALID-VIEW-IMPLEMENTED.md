# Database survey history: "show invalid" view — implemented

Branch `re/database-survey-history-invalid-view`, cut from origin/main 04e76e35.

## Environment evidence (before any test run)

`uv sync --all-packages --extra dev` was run in the worktree, then:

```
$ .venv/bin/python -c "import resource_explorer; print(resource_explorer.__file__)"
/Users/dwolfson/localGit/egeria-v6/trellis-re-history-invalid-view/packages/resource-explorer/resource_explorer/__init__.py
```

That path is INSIDE this worktree (`trellis-re-history-invalid-view`), not main's
checkout (`trellis`). Test numbers below are only evidence because of this line.

## What changed

- Route: `GET /api/databases/{slug}/surveys` (`web/routes/databases.py`) takes
  `include_invalid: bool = False` and passes it to
  `ProjectRegistry.get_database_surveys`. Default response is unchanged.
- Classic UI (`web/static/index.html`, `renderDbSurveyReport`): the Survey
  History section always renders a "show invalid" checkbox. Unchecked = today's
  table from the default fetch (still only when more than one valid survey).
  Checked = re-fetch with `?include_invalid=true`; invalid rows render muted
  (`opacity-50 italic`, the same muted-row pattern used elsewhere in the file), with an
  extra "Invalid" column showing the `invalid_reason` and `marked invalid <invalid_at>`;
  the Date column still shows the original `surveyed_at`. The report header,
  latest-survey schema view and charts keep using the valid-only list.
- Why this doc exists at all: see the correction appended to
  `FALSE-ZERO-PUBLISH-HOTFIX-IMPLEMENTED.md` (its plan said this would land; it
  had not).

## Tests

`tests/test_database_survey_history_invalid_view.py`: route default excludes /
`include_invalid=true` returns the row with reason and mark time; the render
function is extracted from `index.html` and executed under node for the four
states (off: absent; on: present and muted; reason text; marked-at time distinct
from surveyed-at) plus a missing-reason case. The muted-class guard was made to
fail on purpose (class removed) and the test failed. Known limits: node-executed
render only; the checkbox `onchange` fetch wiring is asserted at source level,
not by a browser click. With `include_invalid=true` the registry also returns
each row's `survey_data` blob (large for big databases); only fetched on toggle.

Result (import path confirmed above, inside this worktree):
`uv run pytest packages/resource-explorer/tests -k "database or index or classic or survey"`:
1226 passed, 3 skipped, 0 failed; the new file alone: 8 passed. Not the full
suite (the hotfix's full run was 7158 passed).

