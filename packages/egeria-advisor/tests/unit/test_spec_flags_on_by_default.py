"""EA_SPEC_AUTHORING_V2 and EA_SPEC_ELICITOR_FIXES are ON by default (6.3 Tech Preview, owner 2026-11-17:
"Turn them on by default"), and OFF only when set to 0/false/no/off. EA_SPEC_STATUS_MERGE stays OFF by
default (the Architect's ruling 2026-11-14). The fixes still need the re-base. The harness's baseline mode
turns both off explicitly."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from advisor import spec_authoring_v2 as v2

FLAGS = (v2.FLAG, v2.FIXES_FLAG, v2.STATUS_MERGE_FLAG)


@pytest.fixture
def clean(monkeypatch):
    for f in FLAGS:
        monkeypatch.delenv(f, raising=False)
    return monkeypatch


def test_unset_means_both_on_and_the_merge_off(clean):
    assert v2.enabled() and v2.fixes_enabled() and not v2.status_merge_enabled()


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_is_the_default(clean, blank):
    clean.setenv(v2.FLAG, blank)
    clean.setenv(v2.FIXES_FLAG, blank)
    assert v2.enabled() and v2.fixes_enabled()


@pytest.mark.parametrize("word", ["0", "false", "FALSE", "no", "off", " Off "])
def test_an_off_word_turns_each_off(clean, word):
    clean.setenv(v2.FIXES_FLAG, word)
    assert v2.enabled() and not v2.fixes_enabled()
    clean.setenv(v2.FLAG, word)
    assert not v2.enabled() and not v2.fixes_enabled()


@pytest.mark.parametrize("word", ["1", "true", "yes", "on", "ON"])
def test_an_on_word_is_on(clean, word):
    clean.setenv(v2.FLAG, word)
    clean.setenv(v2.FIXES_FLAG, word)
    assert v2.enabled() and v2.fixes_enabled()


def test_fixes_on_with_the_rebase_off_is_off(clean):
    clean.setenv(v2.FLAG, "0")
    clean.setenv(v2.FIXES_FLAG, "1")
    assert not v2.fixes_enabled()


def test_a_word_that_is_neither_keeps_the_default(clean):
    clean.setenv(v2.FLAG, "maybe")
    clean.setenv(v2.STATUS_MERGE_FLAG, "maybe")
    assert v2.enabled() and not v2.status_merge_enabled()


def test_the_status_merge_is_off_unless_set(clean):
    assert not v2.status_merge_enabled()
    clean.setenv(v2.STATUS_MERGE_FLAG, "1")
    assert v2.status_merge_enabled()
    clean.setenv(v2.FIXES_FLAG, "0")
    assert not v2.status_merge_enabled()       # still only with the fixes on


def test_the_startup_line_names_the_effective_values(clean):
    assert v2.flags_line() == ("report-spec authoring: EA_SPEC_AUTHORING_V2=on, EA_SPEC_ELICITOR_FIXES=on, "
                               "EA_SPEC_STATUS_MERGE=off")
    clean.setenv(v2.FLAG, "0")
    assert "EA_SPEC_AUTHORING_V2=off, EA_SPEC_ELICITOR_FIXES=off" in v2.flags_line()


def test_the_web_app_logs_the_flags_at_startup():
    src = (Path(__file__).resolve().parents[2] / "advisor" / "web" / "app.py").read_text()
    assert "spec_authoring_v2.flags_line()" in src


def _harness():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ea_rebase" / "harness.py"
    spec = importlib.util.spec_from_file_location("ea_rebase_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("mode, rebase, fixes", [("baseline", False, False), ("rebased", True, False),
                                                  ("rebased_fixes", True, True)])
def test_each_harness_mode_runs_with_its_own_flags(clean, mode, rebase, fixes):
    """baseline really means off now that unset is on; a stray merge flag is cleared in every mode."""
    clean.setenv(v2.STATUS_MERGE_FLAG, "1")
    env = {}
    import os
    h = _harness()
    h.set_mode_env(mode)       # os.environ; the clean fixture restores it
    assert (v2.enabled(), v2.fixes_enabled(), v2.status_merge_enabled()) == (rebase, fixes, False)
    assert os.environ[v2.FLAG] == ("1" if rebase else "0") and os.environ[v2.FIXES_FLAG] == ("1" if fixes else "0")
    h.set_mode_env(mode, env)
    assert env == h.MODE_ENV[mode]
