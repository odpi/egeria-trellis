"""Found live, `adventureworks`, 2026-09-27: a Survey Definition run that
had genuinely completed (three survey rows written) still left the Survey
pane reading "never run" — `launchSurvey()` set a one-time note and never
re-fetched. No browser verification with a signed-in session is asserted
here — see the same static-source-assertion pattern
test_next_db_server_discovery.py established.
"""
from __future__ import annotations

from pathlib import Path

NEXT = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static" / "next"


def _app():
    return (NEXT / "app.js").read_text(encoding="utf-8")


def _launch_survey_fn():
    src = _app()
    start = src.index("async function launchSurvey(")
    end = src.index("\n}\n", start)
    return src[start:end]


class TestLaunchSurveyRefreshesThePane:
    def test_watches_the_run_via_poll_activity(self):
        fn = _launch_survey_fn()
        assert "pollActivity(res.activity_id" in fn

    def test_a_poll_timeout_is_not_treated_as_a_failure(self):
        """The same distinction the per-analysis run button already draws:
        the browser giving up watching is not the run failing."""
        fn = _launch_survey_fn()
        assert "err.name !== 'PollTimeout'" in fn

    def test_reloads_the_survey_pane_when_still_on_it(self):
        fn = _launch_survey_fn()
        assert "state.subTab === 'survey'" in fn
        assert "await loadSurveyPane();" in fn
