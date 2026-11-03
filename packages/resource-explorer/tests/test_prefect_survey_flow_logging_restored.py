"""INFO logging is live again once test_prefect_survey_flow.py is done.

That module silences Prefect's run logs for its own tests. It used to call
`logging.disable(logging.INFO)` at import, which leaked into every later test
in a full run. This file sorts directly after it, so in a full-suite run its
second test is the first thing to execute once the module's fixture tears down.
"""
import importlib
import logging


def test_importing_the_survey_flow_module_leaves_logging_enabled():
    importlib.import_module("tests.test_prefect_survey_flow")
    assert logging.root.manager.disable == logging.NOTSET


def test_info_logging_is_enabled_after_the_survey_flow_module_ran():
    assert logging.root.manager.disable == logging.NOTSET

    # The shape that failed: a handler on a module logger, not caplog.at_level
    # (which re-enables logging itself and so hid the leak).
    seen = []
    handler = logging.Handler(logging.INFO)
    handler.emit = seen.append
    log = logging.getLogger("tests.logging_restored_probe")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        log.info("probe")
    finally:
        log.removeHandler(handler)
    assert [r.getMessage() for r in seen] == ["probe"]
