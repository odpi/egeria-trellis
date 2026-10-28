# App entry points: group by kind (implemented)

## Environment confirmation

- `uv sync --all-packages --extra dev` run in this worktree.
- `resource_explorer.__file__` =
  `/Users/dwolfson/localGit/egeria-v6/trellis-re-app-entry-points/packages/resource-explorer/resource_explorer/__init__.py`
- Signed test commit verified `%G? = G`, then dropped.

## Root cause

`_renderDeploymentEvidenceResults` (classic `index.html`) built its evidence
line from `d.evidence.map(e => e.kind)` -- the KIND of every item, never its
name (`EvidenceItem.detail`) and never grouped. One `console_script` item per
`[project.scripts]` entry therefore printed N bare "console_script" strings.
The count was right; the label was the kind. Backend data was already correct.

## Fix

Group by kind. A kind with one item prints bare (unchanged: my-egeria); a kind
with several prints `kind · N: name, name, ...` (names omitted when items carry
no `detail`, e.g. `dunder_main · 2`). Separator is `, ` as before unless a
group has names, then `; `.

## Tests

`tests/test_deployment_evidence_group_by_kind.py` (real node execution; 50
console_scripts + 2 dunder_main + dockerfile). Reverted index.html: grouping
test FAILS; restored: PASSES (23 passed with test_deployment_evidence.py).
