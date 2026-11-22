"""TR-124: Curate's folder-description proposals were raw README licence headers ("<!-- SPDX-License-Identifier:
CC-BY-4.0 -- …"): extract_narrative chose the first non-blank line as the title and the first paragraph as the description
without skipping the comment and licence header an Egeria-style README opens with. HTML comments and SPDX/copyright
header lines are now stripped before the title and description are chosen; if nothing is left, nothing is proposed
(absence, not junk). The licence is still READ from the text; only what is proposed as a description changes. No Egeria.
"""
from __future__ import annotations

import pytest

from resource_explorer import fs_review
from resource_explorer.surveyors.filesystem import walk_extensions as we

EGERIA_README = """<!-- SPDX-License-Identifier: CC-BY-4.0 -->
<!-- Copyright Contributors to the ODPi Egeria project. -->

# Open Metadata Types

Open metadata types define the structure of the metadata that Egeria stores and exchanges between
repositories.

## More

Later text.
"""


def _x(text, name="README.md"):
    return we.extract_narrative(text, name, ["a.csv"])


def test_an_egeria_style_readme_proposes_its_title_and_first_paragraph_not_its_licence_comment():
    f = _x(EGERIA_README)
    assert f["title"] == "Open Metadata Types"
    assert f["description"].startswith("Open metadata types define the structure of the metadata")
    assert "SPDX" not in f["title"] + f["description"] and "Copyright" not in f["title"] + f["description"]
    assert "<!--" not in f["title"] + f["description"] and "-->" not in f["title"] + f["description"]


def test_a_multi_line_comment_header_is_stripped():
    f = _x("<!--\n  SPDX-License-Identifier: CC-BY-4.0\n  Copyright Contributors to the Egeria project.\n-->\n\n"
           "# Data Dictionary\n\nWhat each column means.\n")
    assert f["title"] == "Data Dictionary" and f["description"] == "What each column means."


def test_plain_text_spdx_and_copyright_lines_are_stripped():
    f = _x("SPDX-License-Identifier: Apache-2.0\nSPDX-FileCopyrightText: 2024 Foo Inc.\nCopyright 2024 Foo Inc.\n"
           "(c) 2024 Foo\n© 2024 Foo\n\nA dataset of monthly rainfall by station.\n", name="README.txt")
    assert f["title"] == "A dataset of monthly rainfall by station."
    assert "Copyright" not in f["title"] and "SPDX" not in f["title"]


def test_comment_leader_characters_before_spdx_do_not_hide_it():
    f = _x("# SPDX-License-Identifier: MIT\n// Copyright (c) 2024 Foo\n\n# Rainfall\n\nMonthly rainfall.\n")
    assert f["title"] == "Rainfall" and f["description"] == "Monthly rainfall."


def test_a_comment_inside_the_paragraph_is_removed():
    f = _x("# Stations\n\nWeather <!-- internal note --> stations across the region.\n")
    assert f["description"] == "Weather stations across the region."


def test_a_readme_that_is_only_a_licence_header_proposes_nothing():
    f = _x("<!-- SPDX-License-Identifier: CC-BY-4.0 -->\n<!-- Copyright Contributors to the ODPi Egeria project. -->\n")
    assert f["title"] == "" and f["description"] == ""


def test_a_heading_that_merely_starts_with_the_word_copyright_is_kept():
    f = _x("# Copyright and licence terms\n\nHow the data may be reused.\n")
    assert f["title"] == "Copyright and licence terms" and f["description"] == "How the data may be reused."


def test_a_readme_with_no_header_is_unchanged():
    f = _x("# Rainfall\n\nMonthly rainfall by station.\n")
    assert (f["title"], f["description"]) == ("Rainfall", "Monthly rainfall by station.")


def test_the_licence_is_still_read_from_the_full_text_including_the_stripped_header():
    f = _x("<!-- SPDX-License-Identifier: CC-BY-4.0 -->\n\n# Rainfall\n\nMonthly rainfall.\n\nLicense: CC-BY-4.0\n")
    assert f["licence"] == "CC-BY-4.0" and f["title"] == "Rainfall"


def test_the_review_lists_nothing_for_a_header_only_readme_and_the_real_text_for_the_others(tmp_path):
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.register_filesystem(FileSystemEntity(slug="walkme", display_name="walkme", local_mount_point=str(tmp_path)))
    entries = []
    for folder, text in (("egeria", EGERIA_README), ("junk", "<!-- SPDX-License-Identifier: CC-BY-4.0 -->\n")):
        found = _x(text)
        entries.append({"folder": folder, "file": f"{folder}/README.md", "status": "proposal", "stated_by": "README.md", **found})
    reg.add_filesystem_survey("walkme", "2026-11-22T01:00:00", {"surveyed_at": "2026-11-22T01:00:00", "total_files": 2,
                                                              "folder_narratives": entries,
                                                              "walk_extensions": {"run_id": "2026-11-22T01:00:00"}})
    _run, proposals = fs_review.folder_proposals(reg, "walkme")
    assert [p["file"] for p in proposals] == ["egeria/README.md"]
    assert proposals[0]["title"] == "Open Metadata Types" and "SPDX" not in proposals[0]["description"]
