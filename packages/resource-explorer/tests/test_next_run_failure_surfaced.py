"""Parity inventory D-43 (CLASSIC-VS-NEXT-PARITY-2026-09-30.md): a per-analysis
run that finished in error had its finished activity entry discarded --
`pollActivity` resolves with the row whatever it ended as, and both callers
(the Survey & analyses row's run button and the Questions `rerun`) ignored the
return value. Only a failure to START produced text.

Static-source pins, the pattern test_next_survey_pane_refresh.py uses (CI has
no Node). No browser check with a signed-in session is asserted here.
"""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"


def _app() -> str:
    return (STATIC / "next" / "app.js").read_text(encoding="utf-8")


def _slice(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


def _rerun() -> str:
    return _slice(_app(), "async function rerun(", "\n}\n")


def _run_button_handler() -> str:
    # The click handler's body is `startRun` (brief section 8 shares it with the credential dialog).
    return _slice(_app(), "const startRun = async (", "\n  };\n")


def _helper() -> str:
    return _slice((STATIC / "re-api.js").read_text(encoding="utf-8"),
                  "export function activityFailure(", "\n}\n")


class TestFinishedEntryIsInspected:
    def test_rerun_keeps_the_finished_entry(self):
        assert "finishedRun = await pollActivity(" in _rerun()

    def test_rerun_checks_it_for_failure_before_re_reading_the_old_answer(self):
        fn = _rerun()
        assert "activityFailure(finishedRun)" in fn
        assert fn.index("activityFailure(finishedRun)") < fn.index("await loadAnswer(")

    def test_run_button_keeps_the_finished_entry(self):
        fn = _run_button_handler()
        assert "const finished = await pollActivity(" in fn
        assert "activityFailure(finished)" in fn

    def test_failure_text_is_stored_and_rendered_in_the_row(self):
        src = _app()
        assert "state.analysisRunFailures.set(failKey, failureText(failure))" in src
        row = _slice(src, "function analysisIndexRowHtml(", "\n}\n")
        assert "data-analysis-run-failed" in row
        assert "state.analysisRunFailures.get(" in row

    def test_failure_is_not_auto_dismissed(self):
        # Cleared only when the same analysis is run again.
        src = _app()
        assert src.count("analysisRunFailures.delete(") == 1
        assert "setTimeout" not in _run_button_handler()


class TestReasonComesFromThePersistedRow:
    def test_reason_is_detail_error_then_summary(self):
        h = _helper()
        assert "detail.error" in h and "entry.summary" in h

    def test_status_decides_not_the_caller(self):
        h = _helper()
        assert "'error'" in h and "'failed'" in h

    def test_no_reason_is_stated_not_invented(self):
        src = (STATIC / "re-api.js").read_text(encoding="utf-8")
        assert "no reason recorded" in src


class TestKnownNegative:
    def test_a_successful_entry_is_not_a_failure(self):
        """The helper returns null for any non-error status, and both callers
        only act when it is non-null -- a successful run shows nothing new."""
        h = _helper()
        assert "if (status !== 'error' && status !== 'failed') return null;" in h
        assert "if (failure)" in _app()
