"""TR-21 R4 (Architect ruling 2026-11-15): a dispatched step's output is encoded losslessly, or the flow fails.

Found in the hedwig A/B rerun on f292d53c: the worker stored each annotation dataclass as its repr() string (a JSON
dump with a str() fallback), so the host got strings back where it publishes annotations; RB1's norm_hash differed
from RA1's for the same commit. The fixtures are REAL outputs of the A/B step set, captured 2026-11-15 on hedwig
(tests/fixtures/step_outputs; the database role and the temp root replaced by placeholders).
"""
from __future__ import annotations

import dataclasses
import datetime
import decimal
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import resource_explorer.surveyors.prefect_adapter as adapter
from resource_explorer.prefect import flows
from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors import step_output_codec as codec
from resource_explorer.surveyors.survey_report import Annotation
from resource_explorer.surveyors.survey_snapshot import annotation_from_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "step_outputs"
PACKAGE = Path(__file__).resolve().parents[1] / "resource_explorer"


def _real(name: str) -> dict:
    """A real captured output. The repo one is stored in the kept-snapshot form and rebuilt as annotation objects,
    exactly what the step returns."""
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    if name == "repo_repo_symbol_extraction":
        data.pop("_source")
        data["annotations"] = [annotation_from_dict(a) for a in data["annotations"]]
    return data


REAL = ["repo_repo_symbol_extraction", "database_credential_capability", "database_postgres_operations",
        "filesystem_filesystem_inventory"]


@pytest.mark.parametrize("name", REAL)
def test_real_outputs_round_trip_losslessly(name):
    output = _real(name)
    text, norm_hash = codec.encode_step_output(output)
    back = codec.decode_step_output(text)
    assert back == output
    assert codec.encode_step_output(back) == (text, norm_hash), "decode then encode is the identity"


def test_annotations_come_back_as_the_same_dataclasses_not_strings():
    output = _real("repo_repo_symbol_extraction")
    back = codec.decode_step_output(codec.encode_step_output(output)[0])
    assert [type(a) for a in back["annotations"]] == [type(a) for a in output["annotations"]]
    assert all(isinstance(a, Annotation) and dataclasses.is_dataclass(a) for a in back["annotations"])
    assert back["annotations"][0].summary.startswith("Extracted ")


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


@pytest.mark.parametrize("name", REAL)
def test_the_hash_is_the_same_on_the_dispatch_and_in_process_paths(registry, name):
    """R4 point 4: both paths hash through the ONE codec, so RA == RB is like for like."""
    output = _real(name)
    entity_type = name.split("_", 1)[0]

    def run(flow_run_id, keep):
        with patch.object(flows, "ProjectRegistry", lambda *a, **k: registry), \
             patch.object(flows, "run_surveyor_step_task", lambda **kw: output):
            return flows._run_step_as_flow_run(flow_run_id, entity_type, "s", "step_x", {}, keep_result=keep)

    _, dispatched = run("fr-d", True)
    _, in_process = run("fr-i", False)
    assert dispatched["norm_hash"] == in_process["norm_hash"] == codec.encode_step_output(output)[1]
    with patch.object(adapter, "_results_registry", lambda: registry):
        read = adapter._read_flow_run_output("fr-d", json.dumps(dispatched))
    assert read == output, "the host gets back exactly what the step returned"


@pytest.mark.parametrize("stored, why", [
    ({"annotations": [{codec.MARKER: "annotation", "v": 2, "data": {}}]}, "version"),
    ({"annotations": [{codec.MARKER: "pickle", "v": 1, "data": {}}]}, "unknown codec marker"),
    ({"annotations": [{codec.MARKER: "annotation", "v": 1, "data": {"annotation_type": "NoSuchAnnotation"}}]},
     "does not decode"),
])
def test_an_undecodable_output_is_unreadable_and_nothing_is_used(registry, stored, why):
    with pytest.raises(codec.StepOutputDecodeError, match=why):
        codec.decode_step_output(json.dumps(stored))
    registry.write_flow_run_result("fr-x", entity_type="repo", slug="s", step_key="k", step_run_id=1,
                                   output_json=json.dumps(stored), norm_hash="h")
    with patch.object(adapter, "_results_registry", lambda: registry), \
            pytest.raises(adapter.PrefectResultUnreadable) as ei:
        adapter._read_flow_run_output("fr-x", "")
    assert ei.value.status == "result_unreadable"


@pytest.mark.parametrize("value", [
    datetime.datetime(2026, 11, 15, 20, 0, tzinfo=datetime.UTC), decimal.Decimal("1.5"), (1, 2), {1, 2}, object(), float("nan"),
])
def test_a_value_with_no_lossless_encoding_raises_with_its_path(value):
    with pytest.raises(codec.StepOutputEncodeError, match=r"\$\.inner\.v"):
        codec.encode_step_output({"inner": {"v": value}})


def test_a_reserved_or_non_str_key_raises():
    with pytest.raises(codec.StepOutputEncodeError, match="reserved"):
        codec.encode_step_output({codec.MARKER: 1})
    with pytest.raises(codec.StepOutputEncodeError, match="only str keys"):
        codec.encode_step_output({1: "a"})


def test_the_flow_fails_and_keeps_nothing_when_an_output_cannot_be_encoded(registry):
    """R4 point 2: the flow run FAILS loudly (type + key path); nothing is kept, so nothing can be published. Under
    TR-20 the host then records the run as failed and never runs the step locally
    (test_prefect_no_local_fallback::test_failed_or_crashed)."""
    with patch.object(flows, "ProjectRegistry", lambda *a, **k: registry), \
         patch.object(flows, "run_surveyor_step_task",
                      lambda **kw: {"rows": [{"at": datetime.datetime(2026, 11, 15, tzinfo=datetime.UTC)}]}), \
         pytest.raises(codec.StepOutputEncodeError, match=r"\$\.rows\[0\]\.at: a datetime\.datetime"):
        flows._run_step_as_flow_run("fr-bad", "database", "s", "step_x", {}, keep_result=True)
    assert registry.read_flow_run_result("fr-bad") is None


def test_no_str_fallback_anywhere_on_the_result_path():
    """R4 point 1: a JSON dump with default=str is how the annotations became strings. None may come back."""
    paths = [*sorted((PACKAGE / "prefect").glob("*.py")), PACKAGE / "surveyors" / "prefect_adapter.py",
             PACKAGE / "surveyors" / "step_output_codec.py"]
    offenders = [f"{p.relative_to(PACKAGE)}:{i}" for p in paths
                 for i, line in enumerate(p.read_text().splitlines(), 1) if re.search(r"default\s*=\s*str\b", line)]
    assert offenders == []
