"""schema_comparison: an Analytic Library step (DESIGN-RESOURCE-COMPARISON-QUESTIONS-2026-10-30, slice CMP1).

"Is the schema of database 1 the same as the schema of database 2?", answered as a relation with a degree (identical,
subset, superset, overlapping, disjoint, or not established), never a yes or a no, from the schema inventories
Resource Explorer already stored. Its subject is a set: a work list of two databases, member 1 being "database 1".
The package holds the code (`step.run`) and its manifest (`manifest.MANIFEST`) and nothing else: no Egeria, no
storage, no credentials, no SQL, and no read of either database itself.
"""
__version__ = "0.1.0"
