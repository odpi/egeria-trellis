"""A Survey Definition reference without `::` resolves by its qualified name, through the shared unique-name resolver, and a
lookup that raised is not reported as "not found".

Found live 2026-10-30: the Q3 batch carried the bare name `DatabaseQualitySurvey`; Egeria's by-name and find calls answer
"No elements found" for it and resolve `GovActionProcess::DatabaseQualitySurvey`, so a batch run failed at once. The
Architect's line: resolve by the definition's qualified name through `egeria_lookup.guid_by_qualified_name` (as the Files
and database hosts do), never by display name, and never through the exact-name read that hydrates the candidate set.
"""
from __future__ import annotations

import pytest

from resource_explorer import quality_batch
from resource_explorer.surveyors import survey_definition_reader as sdr
from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor, SurveyDefinitionExecutorError

QN = "GovActionProcess::DatabaseQualitySurvey"
GUID = "616c51d4-1111-2222-3333-444444444444"


def _answer(type_name="GovernanceActionProcess"):
    return {"elementGUID": GUID, "type": {"typeName": type_name, "superTypeNames": ["GovernanceDefinition"]}}


class _Expert:
    """Egeria's unique-name read as probed: it answers for the qualified name only; anything else is its explicit miss."""

    def __init__(self, raises=None, type_name="GovernanceActionProcess"):
        self.raises, self.type_name, self.reads = raises, type_name, []

    def get_metadata_element_by_unique_name(self, name, property_name="qualifiedName"):
        self.reads.append(name)
        if self.raises:
            raise self.raises
        return _answer(self.type_name) if name == QN else "No elements found"


class _Officer:
    """The calls that time out on a grown repository: any use of them fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"the exact-name / search read {name} must not be used to resolve a definition")


def _reader(expert):
    reader = sdr.SurveyDefinitionReader.__new__(sdr.SurveyDefinitionReader)
    reader._governance_officer = _Officer()
    reader.connect = lambda: None
    reader._thread_local_question_lookup_client = lambda: expert
    return reader


def test_the_bare_name_resolves_through_the_qualified_name():
    expert = _Expert()
    assert _reader(expert).find_process_guid_by_name("DatabaseQualitySurvey") == GUID
    assert expert.reads == [QN]


def test_the_qualified_name_resolves_directly():
    expert = _Expert()
    assert _reader(expert).find_process_guid_by_name(QN) == GUID
    assert expert.reads == [QN]


def test_no_display_name_and_no_name_search_is_ever_tried():
    expert = _Expert()
    reader = _reader(expert)
    assert reader.find_process_guid_by_name("NoSuchSurvey") is None
    assert expert.reads == ["GovActionProcess::NoSuchSurvey"]       # one read, by qualified name; _Officer would raise


def test_an_unknown_name_is_none_with_no_errors():
    found = _reader(_Expert()).lookup_process("NoSuchSurvey")
    assert found.guid is None and found.errors == [] and found.tried == ["GovActionProcess::NoSuchSurvey"]


def test_a_read_that_raised_is_reported_apart_from_not_found():
    reader = _reader(_Expert(raises=RuntimeError("view server unreachable")))
    assert reader.find_process_guid_by_name("DatabaseQualitySurvey") is None
    assert reader.last_lookup_errors and "view server unreachable" in reader.last_lookup_errors[0]


def test_an_element_of_another_type_holding_the_name_is_an_error_not_an_absence():
    reader = _reader(_Expert(type_name="GovernanceRule"))
    assert reader.find_process_guid_by_name("DatabaseQualitySurvey") is None
    assert reader.last_lookup_errors and "nothing is adopted or created" in reader.last_lookup_errors[0]


class _Registry:
    def set_survey_definition_guid(self, *a, **k):
        pass


def _executor(reader):
    executor = SurveyDefinitionExecutor.__new__(SurveyDefinitionExecutor)
    executor.reader, executor.registry = reader, _Registry()
    return executor


def test_the_executor_says_could_not_look_up_when_the_read_raised():
    reader = _reader(_Expert(raises=RuntimeError("view server unreachable")))
    with pytest.raises(SurveyDefinitionExecutorError) as raised:
        _executor(reader)._resolve_process_guid("database", "x", "PostgreSQL Database", "DatabaseQualitySurvey", False)
    assert "Could not look up" in str(raised.value) and "not a finding that it does not exist" in str(raised.value)


def test_the_executor_refuses_in_the_architects_words_when_nothing_raised():
    reader = _reader(_Expert())
    with pytest.raises(SurveyDefinitionExecutorError) as raised:
        _executor(reader)._resolve_process_guid("database", "x", "PostgreSQL Database", "NoSuchSurvey", False)
    assert str(raised.value) == "no survey definition named GovActionProcess::NoSuchSurvey in Egeria"


def test_the_executor_resolves_the_bare_quality_definition():
    guid, ref = _executor(_reader(_Expert()))._resolve_process_guid(
        "database", "x", "PostgreSQL Database", "DatabaseQualitySurvey", False)
    assert (guid, ref) == (GUID, "DatabaseQualitySurvey")


def test_the_batch_queues_its_runs_under_the_qualified_name_and_prices_from_either_spelling(tmp_path, monkeypatch):
    assert sdr.qualified_process_name(quality_batch.QUALITY_DEFINITION) == QN
    queued = {}

    class _Reg:
        def list_runs(self, **k):
            import json
            return [{"target": json.dumps({"slug": "db", "params": {"survey_definition_ref": ref}}),
                     "started_at": "2026-10-30T01:00:00+00:00", "finished_at": "2026-10-30T01:00:30+00:00"}
                    for ref in ("DatabaseQualitySurvey", QN, "SomethingElse")]

    assert quality_batch.measured_seconds(_Reg(), "db") == [30.0, 30.0]
