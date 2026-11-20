"""The GitHub platform is a singleton, not one per repository.

History: until TR-89 (2026-11-19) GitHub was ONE `SourceControlLibrary` (`_find_or_create_github_scl`) and each
repository an Asset it owned. TR-89 reversed it: each repository is a SourceControlLibrary, and GitHub is ONE
`SoftwareServerPlatform` classified `CloudPlatform` (`_find_or_create_github_platform`), cached as a global
`app_settings` row under a NEW key (`egeria.github_platform_guid`), verified by a type-agnostic read.

The wider TR-89 behaviour (what the platform carries, the link, the zones) is in `test_tr89_repo_as_scl.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fake_egeria_catalogue import FakeRepoEgeria  # noqa: E402

from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher  # noqa: E402

KEY = "egeria.github_platform_guid"
QN = EgeriaPublisher._GITHUB_PLATFORM_QUALIFIED_NAME


class _Registry:
    """Just enough of ProjectRegistry's setting store to exercise the cache."""

    def __init__(self, initial: str | None = None):
        self._settings: dict[str, str] = {}
        if initial:
            self._settings[KEY] = initial

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value


def _publisher(egeria, registry=None):
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._asset_maker = egeria
    pub._metadata_expert = egeria
    pub._registry = registry
    pub.zone_names = None
    return pub


def _platforms(egeria):
    return [g for g, e in egeria.elements.items() if e["type"] == "SoftwareServerPlatform"]


def test_a_verified_cached_guid_is_reused_with_no_create():
    egeria = FakeRepoEgeria()
    cached = egeria.seed("SoftwareServerPlatform", QN)
    pub = _publisher(egeria, _Registry(initial=cached))
    assert pub._find_or_create_github_platform() == cached
    assert egeria.ops("create_asset") == []


def test_a_stale_cached_guid_falls_through_to_search_then_create():
    reg = _Registry(initial="dead-guid")
    egeria = FakeRepoEgeria()                    # the cached GUID no longer resolves
    guid = _publisher(egeria, reg)._find_or_create_github_platform()
    assert guid in _platforms(egeria) and len(egeria.ops("create_asset")) == 1
    assert reg.get_setting(KEY) == guid


def test_no_cache_but_egeria_already_has_one_is_found_not_duplicated():
    reg = _Registry()
    egeria = FakeRepoEgeria()
    found = egeria.seed("SoftwareServerPlatform", QN)
    assert _publisher(egeria, reg)._find_or_create_github_platform() == found
    assert egeria.ops("create_asset") == []
    assert reg.get_setting(KEY) == found         # backfilled for next time


def test_nothing_cached_nothing_found_creates_exactly_one():
    egeria = FakeRepoEgeria()
    _publisher(egeria, _Registry())._find_or_create_github_platform()
    assert len(_platforms(egeria)) == 1


def test_it_works_with_no_registry_at_all():
    """publish() without a registry is a supported path elsewhere in this file: the lookup must degrade the same
    way, not crash on a None registry."""
    egeria = FakeRepoEgeria()
    guid = _publisher(egeria, registry=None)._find_or_create_github_platform()
    assert guid in _platforms(egeria)
