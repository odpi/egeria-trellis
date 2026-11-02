"""RE's side of the Analytic Library's reader seam (LS2 step 1): RE implements the reads the library declares.

The library owns what a step may ask to read (`trellis_analytics.readers.OdsRead`); RE's host narrows a step's
`inputs.ods` to `STEP_ODS_METHODS`. The two lists are one list: a read the library declares that RE does not serve
would fail inside a step, and a read RE serves that the library does not declare is one no step written against the
library can know about.
"""
from trellis_analytics.readers import (CATALOGUE_SCOPE, GROUP, INVESTIGATION_PURPOSES, ODS, ODS_READS, QUESTION_IDS,
                                       reader_factory, installed_readers)

from resource_explorer.surveyors.survey_step_host import STEP_ODS_METHODS


def test_re_serves_exactly_the_reads_the_library_declares():
    assert sorted(STEP_ODS_METHODS) == sorted(ODS_READS)


def test_re_registers_every_reader_the_library_asks_for():
    for name in (ODS, CATALOGUE_SCOPE, INVESTIGATION_PURPOSES, QUESTION_IDS):
        assert name in installed_readers(), f"resource-explorer declares no '{name}' entry point in group {GROUP}"


def test_discoverys_readers_are_res_own_functions():
    from resource_explorer.investigation_purposes import known_purpose_names
    from resource_explorer.surveyors.question_catalog_reader import all_question_ids
    assert reader_factory(INVESTIGATION_PURPOSES) is known_purpose_names
    assert reader_factory(QUESTION_IDS) is all_question_ids
