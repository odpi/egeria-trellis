"""W39: the refusal for a resource with no Egeria asset speaks in its own kind's words; a file system is told to attach
its root folder, not to "publish". The card's matching behaviour is tests: frontend-build/test-harness/fs-survey-refusal.test.mjs."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from resource_explorer import native_survey_run as nsr

NEXT = Path(nsr.__file__).parent / "web" / "static" / "next"


def test_file_system_gets_attach_root_words():
    fs = SimpleNamespace(local_mount_point="/x", egeria_asset_guid="")
    words = nsr._no_asset_words(fs)
    assert words == nsr.FS_NEEDS_ROOT_ASSET
    assert "Attach the root folder to Egeria first" in words and "Publish it to Egeria first" not in words


def test_database_and_other_kinds_keep_their_words():
    db = SimpleNamespace(db_type="postgres")
    assert nsr._no_asset_words(db) != nsr.FS_NEEDS_ROOT_ASSET
    assert nsr._no_asset_words(SimpleNamespace()) == nsr._NEEDS_CATALOGUING


def test_served_text_carries_the_card_and_row_words():
    assert "Attach the root folder to Egeria first" in (NEXT / "fs-survey.js").read_text(encoding="utf-8")
    assert "Run from the Folder survey card above (Survey)" in (NEXT / "stages" / "native-surveys.js").read_text(encoding="utf-8")
