"""The step's manifest: the wrapper's declaration. Its version is the package's."""
from trellis_analytics.declaration import (AbsenceSentences, BasisDeclaration, CategoriesDeclaration,
                                           ConfidenceDeclaration, Declaration, ExtentDeclaration, MeasureDeclaration)
from trellis_analytics.manifest import StepManifest

from schema_comparison import __version__

MANIFEST = StepManifest(
    name="schema_comparison",
    display_name="Schema Comparison",
    function="schema_comparison.step.run",
    description=("Is the schema of database 1 the same as the schema of database 2? Compares the schema inventories "
                 "Resource Explorer stored for the two databases of a work list (member 1 is database 1) and answers "
                 "with a relation and its degree, never a yes or a no: identical, superset, subset, overlapping or "
                 "disjoint, with how many tables match by name of the tables on either side, how many columns of the "
                 "tables both hold match by name and type, and every table and column that differs, named. Reads "
                 "stored facts only and opens no connection to either database. A database with no schema "
                 "inventory in RE is shown as \"not established\", never as a different schema."),
    returns=("one result per member (the comparison, as seen from that database) and one roll-up for the work list, "
             "each carrying schema_relation and schema_similarity"),
    source="ods",
    version=__version__,
    resource_types=["database"],
    subject="set",
    credential_tier="none",
    inputs=["ods"],
    # Owner ruling 1 (2026-10-30): the schema question is an Assessment one.
    stage="assessment",
    perspectives=["Data Owner", "Steward", "Architecture", "Consumer"],
    # Q-0110: "Is the schema of database 1 the same as the schema of database 2?"; Q-0112: "Is database 1 a subset or
    # superset of database 2?" (the schema half; data_comparison, CMP2, answers the data half).
    questions=["Q-0110", "Q-0112"],
    purposes=["Assess", "Select", "Integrate"],
    family="Structure",
    tags=["comparison", "schema", "set"],
    request_type="re-schema-comparison",
    nothing_to_derive_guard="survey-completed",
    declaration=Declaration(
        # The roll-up is ONE result (a set step returns exactly one), so the per-table detail travels as named fields
        # and the result is the distribution of the tables over the categories below, plus the degree.
        result_kind="distribution",
        measures=[MeasureDeclaration(
            name="schema_comparison", unit="tables matching by name", as_of="asOf", value_field="schema_similarity",
            basis=BasisDeclaration(denominator_field="tables_on_union", denominator_label="tables on either side"))],
        categories=CategoriesDeclaration(exclusive=True, sums_to="tables_on_union"),
        absence=AbsenceSentences(
            not_measured="Not established: a database of the pair has no schema inventory in RE yet — survey it, "
                         "then compare.",
            not_permitted="Not established: RE could not read a database's stored schema inventory, so nothing "
                          "is claimed about either side.",
            not_applicable="Not applicable: the two members are not both databases.",
            measured_nothing="Measured: neither database holds a table, so there is no schema to compare."),
        extents=[ExtentDeclaration(
            name="comparison", source=["."],
            fields=["database_1", "database_2", "schema_relation", "schema_similarity", "tables_on_union",
                    "column_similarity", "columns_compared", "identical_shape", "same_name_different_shape",
                    "only_in_database_1", "only_in_database_2", "tables_only_in_1_names", "tables_only_in_2_names",
                    "columns_differing_names", "other_differences", "as_of_1", "as_of_2"],
            rows=1, order="one row: the comparison of the pair",
            truncation="{shown} of {total} comparisons shown", token_cost=420,
            citation="{analysis} · {scope} · as of {as_of}")],
        # Counted from the stored inventories: exact as of each side's survey, which the result names.
        confidence=ConfidenceDeclaration(form="measured"),
        scope="resource",
    ),
)
