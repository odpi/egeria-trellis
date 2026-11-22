"""TR-132 C1: Egeria 6.4's Branch classification on the default branch's EXISTING root folder, behind gates.

Set only when the surveyed ref is a branch and the default one, the platform carries 6.4 and pyegeria has set_element_as_branch.
Idempotent: the same four values make no call; a renamed default updates them. A gate that fails sets nothing and says which, in
words, on the default's repo_branch_runs row. Nothing here creates an Egeria element. Fakes only: no network, no Egeria.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from resource_explorer.registry import ProjectRegistry
from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher
from tests.qn_lookup_bridge import expert_from_legacy
from tests.test_egeria_publisher_sub_resources import GITHUB_URL, _publisher, _sub_resource_row

FOLDER = "folder-guid-1"
RUN = "2026-11-22T10:00:00"


class FakeDevops:
    def __init__(self):
        self.calls = []

    def set_element_as_branch(self, guid, **kw):
        self.calls.append((guid, kw))


@pytest.fixture
def reg(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "t.db"))


def _row(**over):
    row = {"ref_name": "main", "ref_kind": "branch", "surveyed_commit": "m" * 40, "tier": "surveyed", "is_default": True,
           "protected": True}
    row.update(over)
    return row


def _pub(reg, *, rows=None, carries=(True, "6.4.0"), devops="fake", classification=("absent", None)):
    reg.record_repo_branch_runs("widgets", RUN, rows if rows is not None else [_row()])
    pub = EgeriaPublisher(platform_url="https://fake", registry=reg)
    fake = FakeDevops()
    pub._branch_client = (lambda: fake) if devops == "fake" else (lambda: None)
    pub._egeria_carries_6_4 = lambda: carries
    pub._read_branch_classification = lambda guid: classification
    return pub, fake


def _outcome(reg):
    return next(r for r in reg.list_repo_branch_runs("widgets") if r["tier"] == "surveyed")


def _classify(pub):
    return pub.classify_default_branch("widgets", GITHUB_URL, FOLDER)


# ── set when every gate passes ────────────────────────────────────────────────────────────────────────────────────

def test_branch_is_set_when_all_gates_pass_with_the_four_properties(reg):
    pub, fake = _pub(reg)
    words = _classify(pub)
    assert fake.calls == [(FOLDER, {"display_name": "main", "scope": "default branch",
                                    "purpose": "the branch Resource Explorer surveyed", "url": f"{GITHUB_URL}/tree/main"})]
    assert words == "Branch classification set on the root folder: main"
    row = _outcome(reg)
    assert row["words"] == words and row["egeria_folder_guid"] == FOLDER


def test_a_branch_name_with_a_slash_makes_a_clean_url(reg):
    pub, fake = _pub(reg, rows=[_row(ref_name="release/6.4")])
    _classify(pub)
    assert fake.calls[0][1]["url"] == f"{GITHUB_URL}/tree/release/6.4"


# ── not set, with the exact words, for each failing gate ──────────────────────────────────────────────────────────

def test_not_set_on_egeria_6_3_with_the_words(reg):
    pub, fake = _pub(reg, carries=(False, "6.3"))
    assert _classify(pub) == "Egeria 6.3 has no Branch classification" and fake.calls == []
    assert _outcome(reg)["words"] == "Egeria 6.3 has no Branch classification"


def test_not_set_when_the_platform_version_cannot_be_read_and_never_guessed(reg):
    pub, fake = _pub(reg, carries=(None, ""))
    assert "version could not be read" in _classify(pub) and fake.calls == []


def test_not_set_when_pyegeria_has_no_set_element_as_branch(reg):
    pub, fake = _pub(reg, devops="none")
    assert _classify(pub) == "pyegeria older than 6.3.2 has no set_element_as_branch" and fake.calls == []


def test_not_set_for_a_tag(reg):
    pub, fake = _pub(reg, rows=[_row(ref_name="v1", ref_kind="tag", is_default=False)])
    assert _classify(pub) == "a tag is not a branch" and fake.calls == []
    assert _outcome(reg)["words"] == "a tag is not a branch"


def test_not_set_for_a_commit(reg):
    pub, fake = _pub(reg, rows=[_row(ref_name="abcdef1", ref_kind="commit", is_default=False)])
    assert _classify(pub) == "a commit is not a branch" and fake.calls == []


def test_not_set_for_a_branch_that_is_not_the_default(reg):
    pub, fake = _pub(reg, rows=[_row(ref_name="dev", is_default=False)])
    assert _classify(pub) == "the surveyed branch is not the default branch" and fake.calls == []


def test_no_survey_with_a_ref_means_nothing_is_set_and_it_says_so(reg):
    pub = EgeriaPublisher(platform_url="https://fake", registry=reg)
    pub._branch_client = lambda: FakeDevops()
    assert "no survey has recorded which ref" in pub.classify_default_branch("widgets", GITHUB_URL, FOLDER)


# ── idempotency ───────────────────────────────────────────────────────────────────────────────────────────────────

SAME = {"displayName": "main", "scope": "default branch", "purpose": "the branch Resource Explorer surveyed",
        "url": f"{GITHUB_URL}/tree/main"}


def test_the_same_four_values_make_no_call(reg):
    pub, fake = _pub(reg, classification=("present", dict(SAME)))
    words = _classify(pub)
    assert fake.calls == [] and "already set to main; nothing changed" in words


def test_a_renamed_default_updates_the_classification_once(reg):
    pub, fake = _pub(reg, rows=[_row(ref_name="trunk")], classification=("present", dict(SAME)))
    words = _classify(pub)
    assert len(fake.calls) == 1 and fake.calls[0][1]["display_name"] == "trunk"
    assert words == "Branch classification updated to trunk (was main)"


def test_a_folder_whose_classifications_cannot_be_read_is_not_written_over(reg):
    pub, fake = _pub(reg, classification=("unreadable", None))
    assert "could not be read" in _classify(pub) and fake.calls == []


def test_a_failing_call_is_said_in_words_and_never_raises(reg):
    pub, fake = _pub(reg)
    pub._branch_client = lambda: MagicMock(set_element_as_branch=MagicMock(side_effect=RuntimeError("boom")))
    words = _classify(pub)
    assert "could not be set" in words and "boom" in words


def test_the_classification_reader_parses_a_folder_that_has_one_and_one_that_has_none():
    pub = EgeriaPublisher(platform_url="https://fake", registry=MagicMock())
    pub._metadata_expert = MagicMock()
    pub._metadata_expert.get_metadata_element_by_guid.return_value = {"elementHeader": {"classifications": [
        {"classificationName": "Branch", "classificationProperties": dict(SAME)}]}, "properties": {}}
    assert pub._read_branch_classification(FOLDER) == ("present", SAME)
    pub._metadata_expert.get_metadata_element_by_guid.return_value = {"elementHeader": {"classifications": []}, "properties": {}}
    assert pub._read_branch_classification(FOLDER) == ("absent", None)
    pub._metadata_expert.get_metadata_element_by_guid.return_value = "No elements found"
    assert pub._read_branch_classification(FOLDER)[0] == "unreadable"


# ── the hook: only the ROOT folder, whether it was found or created; no new element is made ───────────────────────

def _hooked(registry_rows):
    registry = MagicMock()
    registry.list_sub_resources.return_value = registry_rows
    pub = _publisher(registry)
    pub.classify_default_branch = MagicMock(return_value="x")
    return pub


def test_the_root_folder_just_created_is_classified():
    pub = _hooked([_sub_resource_row("", "folder")])
    result = pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", [""])
    pub.classify_default_branch.assert_called_once_with("myproj", GITHUB_URL, result[""])


def test_a_root_folder_that_already_exists_is_classified_without_creating_anything():
    pub = _hooked([_sub_resource_row("", "folder")])
    pub._automated_curation.get_guid_for_name.return_value = "11111111-1111-4111-8111-111111111111"
    pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", [""])
    pub._automated_curation.create_elem_from_template.assert_not_called()
    pub.classify_default_branch.assert_called_once()


def test_any_other_folder_is_never_classified():
    pub = _hooked([_sub_resource_row("docs", "folder")])
    pub.publish_sub_resources("myproj", GITHUB_URL, "asset-guid", ["docs"])
    pub.classify_default_branch.assert_not_called()


def test_the_classification_never_creates_an_egeria_element(reg):
    pub, fake = _pub(reg)
    pub._asset_maker = MagicMock()
    pub._automated_curation = MagicMock()
    _classify(pub)
    assert pub._asset_maker.mock_calls == [] and pub._automated_curation.mock_calls == []
