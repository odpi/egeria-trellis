"""secret_redaction: the override password stays out of EVERY logger and handler,
including handlers added mid-run and loggers that do not propagate, and its
encoded forms (percent-encoded in a DSN, repr/escaped) are scrubbed too.
Fake values only."""
from __future__ import annotations

import io
import logging
import urllib.parse

from resource_explorer.secret_redaction import redacting_logs, scrub

PW = "fake-p@ss/w:rd+not-real"


def _capture(logger_name: str, *, propagate: bool):
    buf = io.StringIO()
    lg = logging.getLogger(logger_name)
    lg.setLevel(logging.DEBUG)
    lg.propagate = propagate
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(h)
    return lg, h, buf


def test_a_logger_with_its_own_handler_and_no_propagation_is_covered():
    lg, h, buf = _capture("g2.redact.own", propagate=False)
    try:
        with redacting_logs(PW):
            lg.error("connect failed with %s", PW)
        assert PW not in buf.getvalue() and "***" in buf.getvalue()
    finally:
        lg.removeHandler(h)


def test_a_handler_added_mid_run_is_covered():
    lg = logging.getLogger("g2.redact.mid")
    lg.setLevel(logging.DEBUG)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    try:
        with redacting_logs(PW):
            lg.addHandler(h)                 # added AFTER the run started
            lg.error("password is %s", PW)
        assert PW not in buf.getvalue() and "***" in buf.getvalue()
    finally:
        lg.removeHandler(h)


def test_a_percent_encoded_password_in_a_dsn_is_scrubbed_in_logs_and_structures():
    lg, h, buf = _capture("g2.redact.dsn", propagate=False)
    dsn = f"postgresql://u:{urllib.parse.quote(PW, safe='')}@host/db"
    plus = f"pw={urllib.parse.quote_plus(PW)}"
    try:
        with redacting_logs(PW):
            lg.error("could not connect to %s and %s", dsn, plus)
        out = buf.getvalue()
        assert urllib.parse.quote(PW, safe="") not in out
        assert urllib.parse.quote_plus(PW) not in out and PW not in out
    finally:
        lg.removeHandler(h)
    s = scrub({"e": [dsn, plus, repr(PW), PW.encode("unicode_escape").decode()]}, PW)
    flat = str(s)
    assert urllib.parse.quote(PW, safe="") not in flat and urllib.parse.quote_plus(PW) not in flat
    assert PW not in flat


def test_a_traceback_that_carries_the_password_is_scrubbed():
    lg, h, buf = _capture("g2.redact.tb", propagate=False)
    try:
        with redacting_logs(PW):
            try:
                raise RuntimeError(f"bad login {PW}")
            except RuntimeError:
                lg.exception("step failed")
        assert PW not in buf.getvalue() and "RuntimeError" in buf.getvalue()
    finally:
        lg.removeHandler(h)


def test_the_record_factory_is_restored_and_overlapping_runs_do_not_unprotect_each_other():
    before = logging.getLogRecordFactory()
    lg, h, buf = _capture("g2.redact.overlap", propagate=False)
    try:
        a = redacting_logs(PW); b = redacting_logs("other-fake-secret-1")
        a.__enter__(); b.__enter__()
        a.__exit__(None, None, None)            # the first run ends while the second continues
        lg.error("still protected: other-fake-secret-1")
        b.__exit__(None, None, None)
        assert "other-fake-secret-1" not in buf.getvalue()
        assert logging.getLogRecordFactory() is before
        lg.error("after: %s", PW)
        assert PW in buf.getvalue(), "not redacted once no run is active"
    finally:
        lg.removeHandler(h)
