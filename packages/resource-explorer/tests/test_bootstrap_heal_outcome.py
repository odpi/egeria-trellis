"""TR-103: the bootstrap logs "missing — healing (N file(s))" and, until now, nothing about how the heal came out. One line
per batch heal says it: "healed · N files · checks: <the post-heal words>" or "heal failed · <why>", and a post-heal check's own
verdict (its last output line, e.g. "10 declared · 10 ok · drift 0") is in those words, not just "reconciled"."""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from resource_explorer import bootstrap as bs

LOGGER = "resource_explorer.bootstrap"


def _batch(root, name="b", files=("a.md",), extra=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / bs.BATCH_MANIFEST_FILE).write_text(json.dumps(
        {"display_name": name.title(), "files": list(files), "canary": {"qualified_name": f"Q::{name}"}, **(extra or {})}))
    for f in files:
        (d / f).write_text(f"# {f}\n")


@pytest.fixture(autouse=True)
def _clear_state():
    with bs._status_lock:
        bs._status.clear()
    yield
    with bs._status_lock:
        bs._status.clear()


def _lines(caplog, needle):
    return [r for r in caplog.records if r.name == LOGGER and needle in r.getMessage()]


def _canary_sequence(monkeypatch, answers):
    seq = list(answers)
    monkeypatch.setattr(bs, "canary_present", lambda b, client=None: seq.pop(0) if len(seq) > 1 else seq[0])


def test_a_verified_heal_logs_one_line_with_the_file_count_the_words_and_canary_verified(tmp_path, monkeypatch, caplog):
    _batch(tmp_path, files=("a.md", "b.md", "c.md"))
    _canary_sequence(monkeypatch, [False, True])
    words = "post_heal ok; scripts/reconcile_executor_links.py: reconciled — 10 declared · 10 ok · drift 0"
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, words))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        bs.check_and_heal(tmp_path)
    lines = _lines(caplog, "healed ·")
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    assert lines[0].getMessage() == f"bootstrap: b healed · 3 files · {words} · canary verified"
    assert lines[0].levelno == logging.INFO
    assert "checks:" not in lines[0].getMessage()
    assert _lines(caplog, "heal failed") == [] and _lines(caplog, "canary is still missing") == []


def test_a_heal_with_nothing_to_say_does_not_log_ok_as_an_answer(tmp_path, monkeypatch, caplog):
    """A batch with no declared checks and no post-heal words has heal_batch answer "ok": that is not a finding."""
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, True])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "ok"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        bs.check_and_heal(tmp_path)
    assert [r.getMessage() for r in _lines(caplog, "healed ·")] == ["bootstrap: b healed · 1 file · canary verified"]


def test_a_heal_that_ran_but_whose_canary_is_still_missing_is_a_warning_and_never_says_healed(tmp_path, monkeypatch, caplog):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, False])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "post_heal ok"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        res = bs.check_and_heal(tmp_path)
    lines = _lines(caplog, "canary is still missing")
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    assert lines[0].getMessage() == "bootstrap: b heal ran but the canary is still missing · post_heal ok"
    assert _lines(caplog, "healed ·") == [] and _lines(caplog, "heal failed") == []
    # TR-111: the result map agrees with the warning (and with pass_state "failed")
    assert res["batches"]["b"]["action"] == "failed"
    assert res["batches"]["b"]["reason"] == "heal ran but the canary is still missing · post_heal ok"


def test_a_heal_whose_canary_could_not_be_read_says_not_verified(tmp_path, monkeypatch, caplog):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, None])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "post_heal ok"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        bs.check_and_heal(tmp_path)
    lines = _lines(caplog, "not verified")
    assert len(lines) == 1
    assert lines[0].getMessage() == "bootstrap: b healed · 1 file · not verified (could not reach Egeria) · post_heal ok"
    assert "canary verified" not in lines[0].getMessage()


def test_a_heal_whose_canary_could_not_be_read_stays_healed_and_says_not_verified_in_its_reason(tmp_path, monkeypatch):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, None])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "post_heal ok"))
    res = bs.check_and_heal(tmp_path)
    assert res["batches"]["b"]["action"] == "healed"
    assert res["batches"]["b"]["reason"] == "post_heal ok · not verified (could not reach Egeria)"


def test_a_verified_heal_keeps_its_reason_unchanged(tmp_path, monkeypatch):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, True])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "post_heal ok"))
    assert bs.check_and_heal(tmp_path)["batches"]["b"] == {"action": "healed", "reason": "post_heal ok"}


def test_a_failed_heal_logs_heal_failed_with_why_as_a_warning_and_no_healed_line(tmp_path, monkeypatch, caplog):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (False, "a.md: Dr.Egeria exited 2"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        res = bs.check_and_heal(tmp_path)
    assert res["batches"]["b"]["action"] == "failed"
    lines = _lines(caplog, "heal failed")
    assert len(lines) == 1 and lines[0].getMessage() == "bootstrap: b heal failed · a.md: Dr.Egeria exited 2"
    assert lines[0].levelno == logging.WARNING
    assert _lines(caplog, "healed ·") == [] and _lines(caplog, "canary is still missing") == []


def test_the_missing_line_that_says_healing_is_kept(tmp_path, monkeypatch, caplog):
    _batch(tmp_path)
    _canary_sequence(monkeypatch, [False, True])
    monkeypatch.setattr(bs, "heal_batch", lambda b: (True, "ok"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        bs.check_and_heal(tmp_path)
    assert len(_lines(caplog, "missing — healing (1 file(s))")) == 1


def _fake_run(returncode, stdout="", stderr=""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_a_check_that_passes_reports_its_own_verdict_line(tmp_path, monkeypatch):
    _batch(tmp_path, extra={"post_heal_checks": [{"script": "scripts/reconcile_executor_links.py"}]})
    batch = bs.discover_batches(tmp_path)[0]
    monkeypatch.setattr(bs.subprocess, "run", _fake_run(0, "noise\n10 declared · 10 ok · drift 0\n"))
    out = bs._run_post_heal_checks(batch)
    assert out == ["scripts/reconcile_executor_links.py: reconciled — 10 declared · 10 ok · drift 0"]


def test_a_check_with_no_output_still_says_reconciled(tmp_path, monkeypatch):
    _batch(tmp_path, extra={"post_heal_checks": [{"script": "scripts/reconcile_executor_links.py"}]})
    batch = bs.discover_batches(tmp_path)[0]
    monkeypatch.setattr(bs.subprocess, "run", _fake_run(0, ""))
    assert bs._run_post_heal_checks(batch) == ["scripts/reconcile_executor_links.py: reconciled"]


def test_a_check_that_finds_drift_keeps_its_words(tmp_path, monkeypatch):
    _batch(tmp_path, extra={"post_heal_checks": [{"script": "scripts/reconcile_executor_links.py"}]})
    batch = bs.discover_batches(tmp_path)[0]
    monkeypatch.setattr(bs.subprocess, "run", _fake_run(1, "10 declared · 8 ok · drift 2\n"))
    assert bs._run_post_heal_checks(batch) == [
        "scripts/reconcile_executor_links.py: drift found (exit 1) — 10 declared · 8 ok · drift 2"]
