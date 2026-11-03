"""Value profile and quality checks for databases (DESIGN-VALUE-PROFILE-AND-QUALITY-CHECKS.md).

Three steps, linear: `postgres_value_profile` measures, `propose_quality_checks` applies the vendored
catalogue's selection rules to that profile, and `run_quality_checks` executes the accepted checks with the
vendored Postgres SQL. The catalogue is data (`configdata/quality/testgen/`, see its PROVENANCE.md); every
piece of code that reads or runs it lives here.
"""
