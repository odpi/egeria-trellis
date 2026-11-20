"""A repo must not adopt a sibling's Egeria element.

Measured live 2026-08-25: `docling` and `docling_eval` shared one asset GUID, so one repo's survey reports were
attaching to the other's catalog entry, when `_find_or_create_asset` took `existing[0]` of a starts_with search.
Where one repo's URL is a prefix of another's (`docling-project/docling` against `.../docling-eval`) the search
matches both.

Since TR-89 the repository is a SourceControlLibrary found by an EXACT qualifiedName read
(`egeria_lookup.guid_by_qualified_name`), so a prefix cannot match at all; these tests keep the live case pinned
on the fake that knows a capability is not an Asset.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fake_egeria_catalogue import FakeRepoEgeria  # noqa: E402

from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher  # noqa: E402


class _Result:
    resource_slug = "docling"
    project_display_name = "docling"
    github_url = "https://github.com/docling-project/docling"


QN = "GitHubRepository::https://github.com/docling-project/docling"


def _publisher(egeria):
    pub = EgeriaPublisher.__new__(EgeriaPublisher)
    pub._asset_maker = egeria
    pub._metadata_expert = egeria
    pub._registry = None
    # Only what the create path reads; this test is about WHICH element is chosen, not how one is built.
    pub.zone_names = None
    pub._zone_names = None
    return pub


def test_a_sibling_prefix_match_is_not_adopted():
    """docling must not take docling-eval's element just because its name starts the same."""
    egeria = FakeRepoEgeria()
    eval_guid = egeria.seed("SourceControlLibrary", QN + "-eval")
    core_guid = egeria.seed("SourceControlLibrary", QN + "-core")
    guid = _publisher(egeria)._find_or_create_asset(_Result())
    assert guid not in (eval_guid, core_guid), "adopted a sibling repo's element: the live docling bug"
    assert egeria.elements[guid]["props"]["qualifiedName"] == QN
    platform = egeria.by_qn("SoftwareServerPlatform::github.com")
    assert [(r["end1"], r["end2"]) for r in egeria.relationships if r["type"] == "SupportedSoftwareCapability"] \
        == [(platform, guid)], "the new repository must be linked from the GitHub platform"


def test_the_exact_match_is_still_adopted():
    """The reuse path must keep working: this is not 'always create'."""
    egeria = FakeRepoEgeria()
    egeria.seed("SourceControlLibrary", QN + "-eval")
    mine = egeria.seed("SourceControlLibrary", QN)
    assert _publisher(egeria)._find_or_create_asset(_Result()) == mine
    assert egeria.ops("create_software_capability") == [], "created a duplicate instead of reusing the exact match"
    assert [r for r in egeria.relationships if r["type"] == "SupportedSoftwareCapability"] == [], \
        "an already-existing repository must not be re-linked"
