"""hub_tables: an Analytic Library step (slice: the next Analytic Library steps, #2).

Foreign-key degree in and out per table, from the keys Resource Explorer stored, and the hub tables it proposes. The
proposal is an ANNOTATION (a ClassificationAnnotation naming "hub table", content status DRAFT), never a
classification on the table's element: facts come only through Curate (owner, 2026-10-15). The package holds the
code (`step.run`) and its manifest (`manifest.MANIFEST`) and nothing else: no Egeria, no storage, no credentials, no
SQL.
"""
__version__ = "0.2.0"
