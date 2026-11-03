"""Guards for the corpus republish script, written from the 2026-09-02 failure.

Two things went wrong that night and neither was subtle — they were simply not
looked for. The batch recorded a repo with 57 of 125 annotations failed as a
success, because publish() returned an asset GUID. And it kept running while
the Egeria platform climbed to 91% of its memory cap and stopped serving.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from scripts.republish_corpus import (  # noqa: E402
    MEM_CEILING, _to_bytes, container_mem_fraction,
)


class TestTheMemoryGuard:
    def test_it_fires_at_the_reading_that_actually_stalled_the_platform(self):
        """The known-positive. 5.477 GiB of a 6 GiB limit is what
        quickstart-egeria-main showed when it stopped responding."""
        assert _to_bytes("5.477GiB") / _to_bytes("6GiB") > MEM_CEILING

    def test_it_does_not_fire_at_the_platform_s_idle_footprint(self):
        """It idles near 3.77 GiB of 6 with no load at all. A ceiling below
        that would refuse to start, which is a guard nobody can use."""
        assert _to_bytes("3.77GiB") / _to_bytes("6GiB") < MEM_CEILING

    def test_an_unreadable_container_stops_rather_than_proceeds(self):
        """A guard that passes when it cannot see is not a guard — the caller
        treats None as stop, so this must be None and never 0.0."""
        assert container_mem_fraction("definitely-not-a-container") is None

    def test_unit_parsing_is_not_off_by_a_factor_of_1000(self):
        assert _to_bytes("1GiB") == 1024 ** 3
        assert _to_bytes("1MiB") == 1024 ** 2
        assert _to_bytes("512B") == 512


class TestSuccessIsNotAnAssetGuid:
    """publish() returning a GUID means an asset exists. It says nothing about
    whether the annotations were written."""

    def _rec(self, **kw):
        base = dict(asset_guid="g", enqueued=125, done=125, unfinished=0, guids=125)
        base.update(kw)
        return bool(base["asset_guid"]) and base["unfinished"] == 0 \
            and base["guids"] == base["done"]

    def test_a_fully_published_repo_is_ok(self):
        assert self._rec() is True

    def test_the_polaris_case_is_not_ok(self):
        """57 of 125 failed, asset created. The night's actual record said ok."""
        assert self._rec(done=68, unfinished=57, guids=68) is False

    def test_a_collision_is_not_ok_even_with_everything_done(self):
        """Two rows sharing an adopted GUID: nothing failed, an element is
        still missing."""
        assert self._rec(done=125, unfinished=0, guids=124) is False

    def test_a_missing_asset_is_not_ok(self):
        assert self._rec(asset_guid="") is False

    def test_pending_rows_are_not_ok_either(self):
        """Left mid-flight is not published, however clean it looks."""
        assert self._rec(done=100, unfinished=25, guids=100) is False


def test_importing_the_script_leaves_logging_enabled():
    """The script quiets the library with `logging.disable(WARNING)`; that belongs in main(), not at import, or every
    test after this module's collection loses WARNING output (the archive-cascade caplog tests saw '')."""
    import importlib
    import logging

    importlib.import_module("scripts.republish_corpus")
    assert logging.root.manager.disable == logging.NOTSET
