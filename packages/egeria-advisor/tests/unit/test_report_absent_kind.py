"""pyegeria 6.2.17 (#518): a not_established / not_applicable analytic result comes back from exec_report_spec as
kind "absent" -- the sentence in `content`, no `data`, any partial result under `detail`. Both advisor readers
show the sentence and never render `detail`'s numbers as if they were measured."""

from advisor.agents.report_spec_agent import ReportSpecAgent
from advisor.report_pipeline import ReportPipeline

SENTENCE = "Not measured: could not open the registry."
ABSENT = {"kind": "absent", "content": SENTENCE,
          "absence": {"state": "not_measured", "sentence": SENTENCE, "reason": "could not open the registry",
                      "declared": False},
          "detail": {"Published": 0, "Not yet published": 0, "kpis": []},
          "envelope": {"state": "not_established", "reason": "could not open the registry",
                       "subtitle": "not established"}}
CONN = {"view_server": "vs", "platform_url": "https://localhost:9443", "user_id": "u", "user_pwd": "p"}

SPEC_MD = """
# Coverage

## Create Report Spec
### Heading
Coverage
### Output Types
DICT, JSON, BAR
### Analytic Function
curation_coverage_tile
"""


def test_run_report_returns_the_sentence(monkeypatch):
    monkeypatch.setattr("pyegeria.exec_report_spec", lambda *a, **k: ABSENT)
    pipeline = ReportPipeline()
    monkeypatch.setattr(pipeline, "_read_pyegeria_connection", lambda egeria_credentials=None: CONN)
    out = pipeline.run_report("Trellis - Curation Coverage", output_type="DICT")
    assert out == SENTENCE
    assert "Published" not in out and "0" not in out


def test_report_spec_agent_shows_the_sentence_not_a_table(monkeypatch):
    class Docs:
        def load(self, doc_id):
            return SPEC_MD

    class Pipeline:
        def _read_pyegeria_connection(self, egeria_credentials=None):
            return CONN

        def _format_output(self, *a, **k):
            raise AssertionError("an absent result was handed to the table formatter")

    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_spec_doc_manager", lambda: Docs())
    monkeypatch.setattr("advisor.agents.report_spec_agent.get_report_pipeline", lambda: Pipeline())
    monkeypatch.setattr("pyegeria.exec_report_spec", lambda *a, **k: ABSENT)
    res = ReportSpecAgent().execute("coverage_tile", output_format="TABLE")
    assert res["status"] == "Completed"
    assert res["execution_output"] == SENTENCE
