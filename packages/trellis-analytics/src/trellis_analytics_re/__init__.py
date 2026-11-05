"""Resource Explorer's implementations of the Analytic Library's reader seam (LS2 step 3).

The library (`egeria-trellis-analytics`, import package `trellis_analytics`) owns what a routine may read
(`trellis_analytics.readers`); these modules answer it from RE's registry and stay in trellis because they read it:

    ods            OdsReader: the typed reads of the registry (OdsRead)
    registry_view  CatalogueScopeReader: Curate's node set through RE's resolve_node_set (CatalogueScopeRead)

resource-explorer registers both, with its own investigation_purposes, question_ids and table_rows, in the
`trellis_analytics.readers` entry-point group. Until LS2 they lived at `trellis_analytics.readers.ods` and
`trellis_analytics.readers.registry_view`.
"""
