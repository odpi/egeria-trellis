"""Live smoke tests — the faults no mock could have found.

Auto-skipped when Egeria is unreachable, like the `requires_pgvector` tier.

Everything here exists because it was missed by a green suite. Three faults on
2026-08-20 were visible only against a real platform:

  * Which code paths actually consume a cached Egeria GUID. Two of five guards
    were placed where the element is resolved by name every time, so they could
    never fire — proven by publishing with a GUID Egeria cannot have and getting
    no error at all.
  * A divergence swallowed by a deliberately non-fatal handler, which returned
    success with an empty result and a WARNING nobody reads.
  * The real error codes. This repo's own notes recorded
    `OMRS-REPOSITORY-404-007` from the original incident report; the platform
    actually returns `OMAG-REPOSITORY-HANDLER-404-007` wrapping
    `OMRS-REPOSITORY-404-002`, and labels the response `CLIENT_ERROR_400` while
    `relatedHTTPCode` is 404.

And one that a live check would have caught within seconds but nothing did: the
reconciler correctly removed a stale edge from Full Survey, truncating it from 22
steps to 7, and the definition stayed broken until someone happened to look.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from resource_explorer.egeria_linkage import is_unknown_guid_error
from resource_explorer.surveyors.repo_survey_definition_adapter import STEP_REGISTRY

pytestmark = pytest.mark.requires_egeria

SURVEY_TYPES_CSV = (Path(__file__).resolve().parents[1]
                    / "docs" / "dr-egeria" / "repo_survey_types.csv")
NONEXISTENT_GUID = "00000000-dead-beef-0000-000000000000"


@pytest.fixture(scope="module")
def reader():
    from resource_explorer.surveyors.survey_definition_reader import SurveyDefinitionReader

    r = SurveyDefinitionReader()
    r.connect()
    return r


@pytest.fixture(scope="module")
def asset_maker():
    from pyegeria import AssetMaker

    from resource_explorer.config import get_config

    cfg = get_config().egeria
    am = AssetMaker(cfg.view_server, cfg.platform_url, cfg.user_id, cfg.user_password)
    am.create_egeria_bearer_token()
    return am


def _expected_steps() -> dict[str, list[str]]:
    with SURVEY_TYPES_CSV.open() as fh:
        rows = list(csv.DictReader(fh))
    out: dict[str, list[str]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: int(r["step_order"])):
        out[r["survey_group"]].append(r["step_key"])
    return dict(out)


class TestAuthoredDefinitionsMatchTheirSource:
    """The CSV is the source of truth; Egeria holds a copy authored from it.

    Nothing kept the two in step. Re-running a document duplicates step links and
    takes a definition out of service; the reconciler that repairs that can, if
    the document behind it is stale, remove a link that was load-bearing — which
    is how Full Survey silently became 7 steps of 22. Both failures look
    identical from RE: a definition that loads and runs, just not the one the CSV
    describes.
    """

    @pytest.mark.parametrize("group", sorted(_expected_steps()))
    def test_definition_exists_and_matches_the_csv(self, reader, group):
        expected = _expected_steps()[group]
        guid = reader.find_process_guid_by_name(f"GovActionProcess::{group}")
        assert guid and guid != "No elements found", (
            f"'{group}' is in repo_survey_types.csv but not authored in Egeria. Run its "
            "document in docs/dr-egeria/survey-definitions/, then the reconciler.")
        actual = [s.re_analysis_step for s in reader.fetch(guid).steps]

        if expected == ["*"]:
            # Full Survey must contain *every* STEP_REGISTRY step — no lag
            # allowed, contrary to what this test assumed until 2026-08-21.
            #
            # The reconciler derives the expected chain from STEP_REGISTRY order,
            # so an edge that skips a registry step reads as stale and is removed,
            # truncating the definition. Observed twice: repo_arch_detect and
            # repo_arch_coupling sit between repo_symbol_extraction and
            # repo_sub_resource_survey, and while the authored document omitted
            # them every reconciler run cut the chain there — 22 steps to 20, then
            # to 7 on an earlier occasion.
            #
            # So a step being absent from a *stage* survey is a live option
            # (test_reachability_audit.STEPS_NOT_IN_A_STAGE_SURVEY), but being
            # absent from Full Survey is not: regenerate and re-author instead.
            missing = [s for s in STEP_REGISTRY if s not in actual]
            extra = [s for s in actual if s not in STEP_REGISTRY]
        else:
            missing = [s for s in expected if s not in actual]
            extra = [s for s in actual if s not in expected]

        assert not missing and not extra, (
            f"'{group}' in Egeria does not match the CSV — missing {missing}, extra {extra}.\n"
            "\n"
            "REMEDY, and it is a sequence with a trap in it, not a two-step chore:\n"
            "  1. Re-author the document via Dr.Egeria.\n"
            "  2. Run scripts/reconcile_survey_definition_links.py — ALWAYS, not only if step 1\n"
            "     looks wrong.\n"
            "\n"
            "Why 2 is not optional: the survey-definitions batch is NOT IDEMPOTENT. Dr.Egeria's\n"
            "'Link First/Next Process Step' commands do not dedupe, so re-authoring against an\n"
            "already-linked process creates a SECOND edge per step. SurveyDefinitionReader then\n"
            "sees two outgoing next-steps, correctly refuses to guess which chain is intended,\n"
            "and raises — taking the whole definition out of service as an errored card with\n"
            "zero steps. That has happened twice on this platform: 2026-08-13, and 2026-08-19\n"
            "with 22 duplicate edges at once. Doing step 1 without step 2 IS the 08-19 incident.\n"
            "\n"
            "And check the reconciler can actually delete: it needs pyegeria >= 6.0.18.4. On\n"
            "older versions its deletes silently no-op and it reports removals that did not\n"
            "happen (PYEGERIA_ISSUES.md ISSUE-63) — so a clean-looking run is not evidence.\n"
            "\n"
            "See docs/dr-egeria/survey-definitions/_batch.json for the full record.")

    @pytest.mark.parametrize("group", sorted(_expected_steps()))
    def test_step_order_is_preserved(self, reader, group):
        """Order is correctness, not tidiness: repo_file_inventory must precede
        everything that reads the inventory, or a run reports against the
        previous extraction while looking freshly profiled."""
        expected = _expected_steps()[group]
        guid = reader.find_process_guid_by_name(f"GovActionProcess::{group}")
        actual = [s.re_analysis_step for s in reader.fetch(guid).steps]

        if expected == ["*"]:
            # Subsequence rather than equality: the set is checked by the test
            # above, and what matters here is that order follows STEP_REGISTRY,
            # which is what encodes the prerequisites — and what the reconciler
            # compares against.
            order = {k: i for i, k in enumerate(STEP_REGISTRY)}
            positions = [order[s] for s in actual if s in order]
            assert positions == sorted(positions), (
                f"'{group}' runs steps out of STEP_REGISTRY order: {actual}")
        else:
            assert actual == expected, f"'{group}' step order differs from the CSV"

    def test_no_definition_is_broken_by_duplicate_links(self, reader):
        """`Link First/Next Process Step` is not idempotent: re-running a
        document creates a second edge per step, the reader correctly refuses to
        guess which chain is intended, and the whole definition errors out. This
        has happened twice. fetch() raising here is that state."""
        for group in _expected_steps():
            guid = reader.find_process_guid_by_name(f"GovActionProcess::{group}")
            if not guid or guid == "No elements found":
                continue
            try:
                reader.fetch(guid)
            except Exception as exc:
                pytest.fail(
                    f"'{group}' will not load: {type(exc).__name__}: {exc}. If this is a "
                    "branching/duplicate-edge error, run "
                    "scripts/reconcile_survey_definition_links.py.")


class TestTheDivergenceDetectorMatchesRealErrors:
    """The detector keys on Egeria's own message codes. If Egeria's message
    catalogue changes, every guard silently stops recognising the condition and
    the opaque failure comes back — with nothing failing to say so."""

    def test_a_nonexistent_guid_is_recognised(self, asset_maker):
        with pytest.raises(Exception) as ei:
            asset_maker.get_asset_by_guid(NONEXISTENT_GUID)

        assert is_unknown_guid_error(ei.value), (
            "Egeria's unknown-GUID error is no longer recognised by "
            "egeria_linkage.is_unknown_guid_error. Its codes/phrases need updating, or "
            "every stale-linkage guard has quietly stopped working.\n\n"
            f"Actual error:\n{str(ei.value)[:1500]}")

    def test_the_specific_codes_are_still_the_ones_we_match_on(self, asset_maker):
        """Pin the codes themselves, so a change is reported as a change rather
        than absorbed by a broad phrase fallback that happens to still match."""
        with pytest.raises(Exception) as ei:
            asset_maker.get_asset_by_guid(NONEXISTENT_GUID)

        text = str(ei.value)
        assert "OMRS-REPOSITORY-404-002" in text, (
            "the repository-level code changed; update _UNKNOWN_GUID_CODES")

    def test_an_ordinary_empty_result_is_not_mistaken_for_a_divergence(self, asset_maker):
        """The damaging false positive: marking a healthy entity stale sends
        someone to reconcile a catalog that was fine. Egeria answers a search for
        a name that does not exist with a sentinel string, not an error."""
        result = asset_maker.find_assets("a-name-no-asset-could-possibly-have-xyzzy")
        assert not is_unknown_guid_error(Exception(str(result)))


class TestTheByNameFallbackWorks:
    """Two of the five GUID-consuming paths resolve by name every time, and are
    immune to a stale cached GUID only because this works. It is also the
    mechanism the reverse case (RE reset, Egeria intact) depends on."""

    def test_a_cataloged_database_is_findable_by_name(self):
        from resource_explorer.config import get_config
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.surveyors.database.egeria_database_surveyor import (
            EgeriaDatabaseSurveyor,
        )

        registry = ProjectRegistry()
        # A cached GUID is not enough: after an Egeria reset the GUID is still on
        # the row while the element is gone, and a recorded stale linkage is the
        # expected state until someone resolves it. Asserting the fallback then
        # tests the reset rather than the fallback, so skip with the reason
        # instead of failing — this test is about the by-name mechanism, not
        # about whether the catalog happens to be populated right now.
        #
        # The linkage table only reflects PAST detected divergences
        # (`guard_linkage`/`note_divergence` fire reactively, the next time
        # something actually uses a cached GUID) — a database nothing has
        # surveyed/published recently can carry a genuinely dead GUID with no
        # linkage row at all, which read as "not stale" and let this test pick
        # it and fail for the same reason a stale one would have been skipped
        # for. Found live 2026-09-26: `egeria_optional_prefect_db` had no
        # linkage row (never probed), but its cached GUID 404s exactly like
        # `coco_pharma`'s recorded-stale one does — this test's own skip
        # message already says to run `resource-explorer egeria-recheck`
        # first; it just never did so itself. `recheck_all_linkages`
        # proactively asks Egeria about every cached database GUID and
        # records/clears staleness accordingly, so the filter below is
        # trustworthy.
        #
        # This WRITES to the shared registry (`mark_egeria_linkage_stale`/
        # `clear_egeria_linkage_status`) from inside a test — acceptable only
        # here because (a) it records what is actually true (a proactive
        # confirmation, not a guess), and (b) this whole file is a live-smoke
        # suite that already talks to the real Egeria platform; it is not a
        # pattern to copy into an isolated/unit test. Skip cleanly rather than
        # error if the registry can't be written to or Egeria can't be
        # reached at all — this test is about the by-name mechanism, not
        # about connectivity, and a connection failure here is not evidence
        # that mechanism is broken. Fixed here (not in the Slice 12 branch
        # this was first diagnosed from) since `is_published` honouring
        # linkage staleness (this same branch) is what makes the resulting
        # `stale` records user-visible — the coordinator's own call.
        from resource_explorer.egeria_linkage import recheck_all_linkages
        try:
            recheck_all_linkages(registry, entity_types=["database"])
        except Exception as exc:
            pytest.skip(f"could not recheck Egeria linkages before selecting a "
                        f"database (registry unwritable or Egeria unreachable): {exc}")

        cataloged = [
            d for d in registry.list_databases()
            if d.egeria_asset_guid
            and (registry.get_egeria_linkage("database", d.slug) or {}).get("status") != "stale"
        ]
        if not cataloged:
            pytest.skip("no database in this deployment has a live (non-stale) Egeria link — "
                        "run `resource-explorer egeria-recheck` and resolve from "
                        "Admin > Egeria Links to restore one")

        db = cataloged[0]
        cfg = get_config().egeria
        s = EgeriaDatabaseSurveyor(platform_url=cfg.platform_url, view_server=cfg.view_server,
                                   user_id=cfg.user_id, user_password=cfg.user_password)
        s.connect()
        assert s._find_element_guid(db.database_name) == db.egeria_asset_guid, (
            "by-name lookup no longer returns the same element as the cached GUID — "
            "the fallback those paths rely on has stopped working")
