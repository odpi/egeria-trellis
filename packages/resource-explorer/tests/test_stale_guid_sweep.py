"""The sweep must agree with the publisher about whether an asset exists.

Measured 2026-08-26 against a live, healthy catalog: get_asset_by_guid raised
PyegeriaNotFoundException/400 for GUIDs that plainly existed, so the sweep
declared ALL 23 cached GUIDs stale while find_asset_guid returned those exact
GUIDs for the same repos. `--apply` on that verdict would have cleared every
valid registration in the catalog, along with its survey records and -- once
publish claims were added to the clearing -- its publish history too.

A destructive script whose "is it there?" test disagrees with the one the
publisher uses is worse than having no script.
"""
from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sweep_stale_egeria_guids.py"
PUBLISHER = (Path(__file__).resolve().parents[1] / "resource_explorer" / "surveyors"
             / "egeria_publisher.py")


def test_the_sweep_verifies_a_repository_by_guid_type_agnostically():
    """`projects.egeria_asset_guid` is a repository's own SourceControlLibrary GUID (TR-89), which is not an Asset:
    `find_assets` / `get_asset_by_guid` cannot see it, and a sweep built on either would call every repository
    stale and `--apply` would delete its survey records. The sweep reads each GUID with the type-agnostic
    `ClassificationExplorer.get_element_by_guid`, the same read egeria_resync's scan uses."""
    src = SCRIPT.read_text()
    calls = [ln for ln in src.splitlines() if ".find_assets(" in ln and not ln.lstrip().startswith("#")]
    assert calls == [], f"the sweep still decides by an Asset-only search: {calls}"
    assert "classifier.get_element_by_guid(" in src
    assert "classifier.get_element_by_guid(" not in PUBLISHER.read_text()   # the publisher has its own verification


def test_the_sweep_does_not_decide_staleness_by_asset_guid_lookup():
    """The specific call that produced 23 false positives."""
    # The CALL, not the word -- the comment explaining why it is wrong should
    # survive, and a test that forbids the explanation invites its deletion.
    src = SCRIPT.read_text()
    calls = [ln for ln in src.splitlines()
             if ".get_asset_by_guid(" in ln and not ln.lstrip().startswith("#")]
    assert calls == [], f"staleness still decided by asset-GUID lookup: {calls}"


def test_an_error_is_never_treated_as_stale():
    """"Unreachable" and "not there" must stay distinct, or a network blip
    clears the whole catalog."""
    src = SCRIPT.read_text()
    assert "unknown.append" in src
    # The unknown bucket is reported, and never cleared.
    assert "Projects that could not be determined are never cleared." in src


def test_all_three_kinds_of_cached_guid_are_swept():
    """Asset GUIDs came first; an investigation's Egeria Project and each
    disposition WorkingSet's Collection were added later and were left dangling
    by every sweep before this one."""
    src = SCRIPT.read_text()
    assert "egeria_project_guid" in src
    assert "egeria_collection_guid" in src
    # And they are checked even when no asset GUID is stale -- an early return
    # is why they went unchecked.
    assert src.count("_sweep_investigations(registry, maker") >= 3


def test_clearing_a_registration_also_drops_its_publish_claims():
    """project_published_annotation_types is what the Analyses cards' Published
    badge is derived from. Leaving it would put a Published badge on a repo
    whose catalog entry is gone -- the exact thing the sweep exists to end."""
    registry = (Path(__file__).resolve().parents[1] / "resource_explorer" / "registry.py").read_text()
    fn = registry.split("def clear_egeria_registration(")[1].split("\n    def ")[0]
    assert "project_published_annotation_types" in fn
    assert "published_types_deleted" in fn


class TestResolverHonesty:
    """A sweep that reports confidence it does not have is worse than the stale
    GUIDs it was written to find.

    The investigation/working-set resolver called AssetMaker.get_element_by_guid,
    which does not exist. Every lookup raised AttributeError, every result became
    "could not determine", and the caller collected only definite-False -- so it
    printed "all resolve. Nothing to clear." against a catalog where all 7 were
    in fact dangling (measured 2026-08-26, right after a redeploy).
    """

    def test_projects_and_collections_use_their_own_clients(self):
        src = SCRIPT.read_text()
        assert "get_project_by_guid" in src
        assert "get_collection_by_guid" in src
        # The call that does not exist on AssetMaker.
        # (The repository check, `_check_cached`, uses a ClassificationExplorer, which has it: TR-89.)
        investigations = src.split("def _sweep_investigations(")[1]
        calls = [ln for ln in investigations.splitlines()
                 if ".get_element_by_guid(" in ln and not ln.lstrip().startswith("#")]
        assert calls == [], f"resolver still calls a method AssetMaker lacks: {calls}"

    def test_undetermined_results_are_reported_not_dropped(self):
        """Silently swallowing them is what let a wholly broken lookup pass as
        a clean bill of health."""
        src = SCRIPT.read_text()
        fn = src.split("def _sweep_investigations(")[1]
        assert "unknown.append" in fn
        assert "could not determine — not cleared" in fn
        # And a clean report must not be claimed while any are undetermined.
        assert "Nothing stale (" in fn

    def test_an_attribute_error_is_undetermined_not_stale(self):
        """The specific mapping that hid the bug: an exception type that is not
        NotFound/404 means we could not tell, never that it is gone."""
        import sys
        sys.path.insert(0, str(SCRIPT.parent))
        from sweep_stale_egeria_guids import _resolves

        def boom(_guid):
            raise AttributeError("no such method")

        def missing(_guid):
            raise type("PyegeriaNotFoundException", (Exception,), {})("gone")

        assert _resolves(boom, "g") is None
        assert _resolves(missing, "g") is False
        assert _resolves(lambda g: {"guid": g}, "g") is True
        assert _resolves(lambda g: "No elements found", "g") is False


class TestNothingBecomesUnreachable:
    """Clearing was keyed on the project HAVING a stale asset GUID.

    So anything a pass missed became invisible the moment that GUID was
    cleared: no later sweep could see it, because the sweep's own entry
    condition was the GUID it had just removed. Measured after the 2026-08-26
    redeploy, when a run from an older checkout left 97 publish claims and 7
    investigation/working-set GUIDs stranded -- and a re-run of the FIXED
    script said "No cached Egeria asset GUIDs. Nothing to sweep."
    """

    def test_orphaned_publish_claims_are_findable_without_a_guid(self):
        src = SCRIPT.read_text()
        assert "_report_orphan_publish_claims" in src
        fn = src.split("def _report_orphan_publish_claims(")[1].split("\ndef ")[0]
        # Found by the ABSENCE of a GUID, which is the whole point.
        assert "coalesce(p.egeria_asset_guid, '') = ''" in fn
        assert "project_published_annotation_types" in fn

    def test_no_early_return_skips_the_other_sweeps(self):
        """Three early returns in main() each skipped the investigation sweep at
        some point. Every path that returns must first run both."""
        src = SCRIPT.read_text()
        main = src.split("def main(")[1].split("\ndef ")[0]
        returns = [i for i, ln in enumerate(main.splitlines())
                   if ln.strip() == "return 0"]
        lines = main.splitlines()
        for idx in returns:
            window = "\n".join(lines[max(0, idx - 12):idx])
            assert "_sweep_investigations" in window, \
                f"a `return 0` at main() line {idx} skips the investigation sweep"
            assert "_report_orphan_publish_claims" in window, \
                f"a `return 0` at main() line {idx} skips orphaned publish claims"

    def test_unreachable_egeria_still_refuses_to_clear(self):
        """The connect helper must keep failing closed on every path."""
        src = SCRIPT.read_text()
        assert "Refusing to sweep: unreachable is not the same as stale." in src
        fn = src.split("def _connect(")[1].split("\ndef ")[0]
        assert "return None" in fn


class TestEveryKindOfCachedGuid:
    """Four tables cache an Egeria GUID, and each new one was added behind a
    check for the previous ones -- so it went unswept until something broke.

    After the 2026-08-26 redeploy the sweep cleared investigations and working
    sets while 21 entity_egeria_project_context rows still pointed at those
    same dead Projects. That table decides whether a resource may publish and
    what it attaches to, so publishing would have attached to a Project that
    does not exist.
    """

    def test_resource_project_context_is_swept(self):
        src = SCRIPT.read_text()
        assert "entity_egeria_project_context" in src
        fn = src.split("def _sweep_investigations(")[1]
        assert "stale_ctx" in fn

    def test_the_guard_counts_every_kind(self):
        """Returning on a subset is the mistake this script made four times."""
        src = SCRIPT.read_text()
        fn = src.split("def _sweep_investigations(")[1]
        guard = [ln for ln in fn.splitlines() if ln.strip().startswith("if not invs")]
        assert guard, "the early-return guard moved or was renamed"
        assert "ctxs" in guard[0], f"guard ignores a kind of cached GUID: {guard[0]}"
        assert "sets" in guard[0]

    def test_a_cleared_context_goes_back_to_unset(self):
        """Not 'personal' or 'declined'. The decision that was made pointed at a
        Project that no longer exists, so it must be asked again rather than
        silently reinterpreted as some other answer the user never gave."""
        src = SCRIPT.read_text()
        fn = src.split("def _sweep_investigations(")[1]
        assert "status = 'unset'" in fn

    def test_each_distinct_project_is_only_looked_up_once(self):
        """21 rows shared 3 Project GUIDs; asking Egeria 21 times would be 18
        pointless round trips."""
        src = SCRIPT.read_text()
        assert "seen_projects" in src
