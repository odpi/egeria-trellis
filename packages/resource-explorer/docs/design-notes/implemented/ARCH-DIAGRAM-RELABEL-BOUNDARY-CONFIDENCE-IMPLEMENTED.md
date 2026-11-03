# Architecture diagram relabel: boundary-only type, labelled confidence (implemented)

## Environment proof (before any test run)

`uv sync --all-packages --extra dev`, then:

    resource_explorer.__file__ = /Users/dwolfson/localGit/egeria-v6/trellis-re-arch-relabel/packages/resource-explorer/resource_explorer/__init__.py

Signing probe commit verified `G`, then dropped.

## Change

`surveyors/arch_recovery/mermaid.py` `_component_line` is the only place the
diagram's node label is built (classic and /next consume the published Mermaid).

- Untyped node: `unclassified` -> `type not assigned · boundary only`. Coupling
  proposes a boundary and leaves `type=None` by design; it never attempted typing.
- Every node: bare `60%` -> `confidence 60%` (typed nodes too).

Display string only; no classification logic touched.

## Not changed (not the diagram)

Component-list rows still show a bare percentage: `web/static/index.html`
(~4360, `untyped · N%`) and `web/static/next/stages/curate.js` (~483). Same
ambiguity, outside this fix's scope; candidates for a follow-up.

## Proof

`tests/test_arch_mermaid.py`: new typed+boundary test and the updated
type/confidence test fail with the old mermaid.py (2 failed) and pass with the
fix.
