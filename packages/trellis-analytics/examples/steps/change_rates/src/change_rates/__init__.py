"""change_rates: an Analytic Library step (slice: the next Analytic Library steps, #3).

Writes per day per table between the latest two `database_table_activity` snapshots Resource Explorer stored, over
their interval, with the counter-reset caveat. The package holds the code (`step.run`) and its manifest
(`manifest.MANIFEST`) and nothing else: no Egeria, no storage, no credentials, no SQL.
"""
__version__ = "0.1.0"
