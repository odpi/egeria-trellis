"""statistics_freshness: an Analytic Library step (slice: the next Analytic Library steps, #1).

How old each table's planner statistics are, from the newest `database_table_activity` snapshot Resource Explorer
stored: the age of the last ANALYZE and VACUUM, tables never analysed, and tables whose changes since the last
analyse are past autovacuum's own trigger. The package holds the code (`step.run`) and its manifest
(`manifest.MANIFEST`) and nothing else: no Egeria, no storage, no credentials, no SQL.
"""
__version__ = "0.1.0"
