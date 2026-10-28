"""List-of-names settings must never crash startup (FEEDBACK_ADMIN_USERS=peterprofile did, 2026-10-14)."""
from __future__ import annotations

import logging

import pytest

from resource_explorer.config import EgeriaConfig, FeedbackConfig

ACCEPTED = [
    ('["peterprofile"]', ["peterprofile"]),
    ('["a", "b"]', ["a", "b"]),
    ("peterprofile,erinoverview", ["peterprofile", "erinoverview"]),
    (" peterprofile , erinoverview ,, ", ["peterprofile", "erinoverview"]),
    ("peterprofile", ["peterprofile"]),
    ("", []),
    ("   ", []),
    ("[]", []),
]
UNREADABLE = ['["peterprofile"', "[peterprofile]", '{"a": 1}', '"peterprofile"', "'x'"]


def _admins(monkeypatch, value):
    monkeypatch.setenv("FEEDBACK_ADMIN_USERS", value)
    return FeedbackConfig(_env_file=None).admin_users


@pytest.mark.parametrize("raw,expected", ACCEPTED)
def test_accepted_forms(monkeypatch, raw, expected):
    assert _admins(monkeypatch, raw) == expected


def test_unset_is_empty(monkeypatch):
    monkeypatch.delenv("FEEDBACK_ADMIN_USERS", raising=False)
    assert FeedbackConfig(_env_file=None).admin_users == []


@pytest.mark.parametrize("raw", UNREADABLE)
def test_unreadable_gives_empty_and_one_warning(monkeypatch, caplog, raw):
    with caplog.at_level(logging.WARNING, logger="resource_explorer.config"):
        assert _admins(monkeypatch, raw) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "admin_users" in warnings[0].getMessage()


def test_startup_config_load_does_not_raise(monkeypatch):
    from resource_explorer.config import ExplorerConfig
    monkeypatch.setenv("FEEDBACK_ADMIN_USERS", '["peterprofile"')
    cfg = ExplorerConfig()
    assert cfg.feedback.admin_users == []


def test_zone_lists_share_the_rule(monkeypatch):
    monkeypatch.setenv("DEFAULT_CATALOG_ZONES", "z1, z2")
    assert EgeriaConfig(_env_file=None).default_catalog_zones == ["z1", "z2"]
