# trellis-analytics-re: Resource Explorer's side of the Analytic Library

The Analytic Library left trellis in LS2 (2026-11). It is now its own distribution,
**`egeria-trellis-analytics`** (import package `trellis_analytics`), from
[odpi/egeria-trellis-analytics](https://github.com/odpi/egeria-trellis-analytics). Its README is the reference for
people who write routines and steps: the envelope, the manifests, the declaration, the reader seam, and how analytics
are found. Its history before the move is in this repository, under `packages/trellis-analytics`.

What stays here, because it needs Resource Explorer:

| Path | What it is |
|---|---|
| `src/trellis_analytics_re/ods.py` | `OdsReader`: the typed reads of RE's registry (the library's `OdsRead`), read-only. Was `trellis_analytics.readers.ods`. |
| `src/trellis_analytics_re/registry_view.py` | `CatalogueScopeReader`: Curate's node set through RE's own `resolve_node_set` (the library's `CatalogueScopeRead`). Was `trellis_analytics.readers.registry_view`. |
| `examples/steps/*` | The ten reference steps, R19 workspace members. They depend on `egeria-trellis-analytics` and stay here until LS2b ships them as one distribution, `egeria-trellis-steps`. |
| `examples/curation_coverage.py` | The routine's script host. Its other two hosts, the notebook and the Portal tile spec, ship in the library itself ([`src/trellis_analytics/examples/`](https://github.com/odpi/egeria-trellis-analytics/tree/main/src/trellis_analytics/examples), the only copy); `tests/test_hosts.py` and `tests/test_spec_document.py` load them from the installed distribution and run all three against RE's readers. |
| `docs/TUTORIAL-ADD-AN-ANALYTIC-STEP-2026-10-30.md` | The step tutorial, which builds on the reference steps. |
| `tests/` | What needs RE or the reference steps: the steps, the hosts, curation_coverage over a real registry, the registry view. The library's own tests run in its repository. |

Resource Explorer registers the readers in the library's `trellis_analytics.readers` entry-point group (its
`pyproject.toml`): `ods` and `catalogue_scope` from this package, and `investigation_purposes`, `question_ids` and
`table_rows` from RE itself. Without RE the library answers in words, but which words depends on how it is called:

- **Called directly** (the library's `portal.curation_coverage_tile` opens its readers itself): a missing reader gives
  `no_reader(...)`, which is `not_established` with words.
- **Run as a report spec** through pyegeria's executor (the Portal's path, pyegeria 6.2.26 and later): the executor
  asks the Portal's role supply for each input the routine declares. Library 0.4.0 declares
  `inputs=["reader:ods", "reader:catalogue_scope", "subject"]` for both `curation_coverage` and
  `curation_coverage_tile` (`manifests.py`), and the Portal supplies no readers (the library is not installed in the Portal today). The run is **refused before it
  starts**, with the supply's words ("The Portal has no reader named 'ods'"). It is not a result with a state.

pyegeria 6.2.26 removed `analytic_binders`, so no binder makes the Portal read RE's database. See
[Inputs by role](../resource-explorer/docs/extending-with-analyses.md#inputs-by-role-pyegeria-6226).
