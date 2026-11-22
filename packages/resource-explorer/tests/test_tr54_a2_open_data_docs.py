"""TR-54 A2: the open-data notes say what is true. A pointer to a guide section must land on that section's words, and a
status line must not say "not merged" about code that is on the base."""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
GUIDE = (DOCS / "user-guide.md").read_text(encoding="utf-8")
WALK = (DOCS / "walkthrough-find-a-new-data-source.md").read_text(encoding="utf-8")
DDL = (DOCS / "ddl-open-data-od1.md").read_text(encoding="utf-8")


def _section(md: str, number: int) -> str:
    m = re.search(rf"^## {number}\. .*?(?=^## \d+\. |\Z)", md, re.S | re.M)
    assert m, f"no section {number}"
    return m.group(0)


def test_the_guide_section_the_walkthrough_points_to_covers_registering_a_portal():
    assert "Section 8 of the guide covers it" in WALK                     # the pointer stays; the section must earn it
    admin = _section(GUIDE, 8)
    assert "Open data" in admin and "Data portals" in admin and "Register" in admin
    assert "Suggested portals" in admin and "never applied" in admin       # the shipped list: offered, one Add each


def test_the_admin_section_counts_its_pickers_right():
    admin = _section(GUIDE, 8)
    assert "It has four pickers" in admin and "It has three pickers" not in admin
    assert admin.count("- **") >= 4


def test_the_walkthrough_does_not_say_profile_and_publish_are_still_to_ship():
    assert "ship in the OD3 and OD4 changes" not in WALK
    assert "Profile" in WALK and "Publish to Egeria" in WALK                # the steps themselves are still described


def test_the_guide_does_not_say_od3_and_od4_are_on_branches():
    assert "on branches" not in GUIDE


def test_the_ddl_record_says_it_was_merged():
    assert "branch pushed, not merged" not in DDL
    assert "merged" in DDL.split("\n", 3)[2]


def test_the_guide_says_any_signed_in_user_can_add_a_suggested_portal_from_find():
    admin = _section(GUIDE, 8)
    assert "Any signed-in user can add a suggested portal from Find, not only an administrator." in admin
