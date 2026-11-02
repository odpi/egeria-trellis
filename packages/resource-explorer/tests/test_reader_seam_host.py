"""RE's side of the Analytic Library's reader seam (LS2 step 1): RE implements the reads the library declares.

The library owns what a step may ask to read (`trellis_analytics.readers.OdsRead`); RE's host narrows a step's
`inputs.ods` to `STEP_ODS_METHODS`. The two lists are one list: a read the library declares that RE does not serve
would fail inside a step, and a read RE serves that the library does not declare is one no step written against the
library can know about.
"""
from trellis_analytics.readers import GROUP, ODS, ODS_READS, installed_readers

from resource_explorer.surveyors.survey_step_host import STEP_ODS_METHODS


def test_re_serves_exactly_the_reads_the_library_declares():
    assert sorted(STEP_ODS_METHODS) == sorted(ODS_READS)


def test_re_registers_the_ods_reader():
    assert ODS in installed_readers(), f"resource-explorer declares no '{ODS}' entry point in group {GROUP}"
