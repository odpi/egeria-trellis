"""Tests for survey_definition_reconciler.py — pure diff logic, no live
Egeria needed. Regression-guards the exact live incident (2026-08-13):
duplicate NextProcessStep links from re-running non-idempotent Dr.Egeria
Link commands, plus one genuinely stale edge on Repo Full Survey."""
from __future__ import annotations

from resource_explorer.surveyors.survey_definition_reconciler import (
    compute_expected_edges,
    diff_links,
    diff_scopes,
    expected_scopes_from_document,
)


def _link(prev_qn, next_qn, link_guid="link-guid"):
    return {
        "previousProcessStep": {"uniqueName": prev_qn},
        "nextProcessStep": {"uniqueName": next_qn},
        "nextProcessStepLinkGUID": link_guid,
    }


class TestComputeExpectedEdges:
    def test_linear_chain(self):
        edges = compute_expected_edges("RepoCoarseScout", ["repo_health", "repo_language"])
        # Triples since 2026-08-26: the guard is part of an edge's identity, or
        # two branches out of one step read as one edge duplicated.
        assert edges == {
            ("GovActionProcessStep::RepoCoarseScout::repo_health",
             "GovActionProcessStep::RepoCoarseScout::repo_language", "Any"),
        }

    def test_three_step_chain_has_two_edges(self):
        edges = compute_expected_edges("X", ["a", "b", "c"])
        assert edges == {
            ("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "Any"),
            ("GovActionProcessStep::X::b", "GovActionProcessStep::X::c", "Any"),
        }

    def test_single_step_has_no_edges(self):
        assert compute_expected_edges("X", ["only_step"]) == set()

    def test_empty_steps_has_no_edges(self):
        assert compute_expected_edges("X", []) == set()


class TestDiffLinks:
    def test_clean_chain_removes_nothing(self):
        expected = compute_expected_edges("X", ["a", "b", "c"])
        links = [
            _link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1"),
            _link("GovActionProcessStep::X::b", "GovActionProcessStep::X::c", "l2"),
        ]
        result = diff_links(links, expected, "GovActionProcess::X")
        assert result.kept == 2
        assert result.to_remove == []
        assert result.error == ""

    def test_duplicate_edge_second_occurrence_removed(self):
        # Reproduces the live incident's simplest case: repo_health ->
        # repo_language linked twice by re-running Link Next Process Step.
        expected = compute_expected_edges("RepoCoarseScout", ["repo_health", "repo_language"])
        links = [
            _link("GovActionProcessStep::RepoCoarseScout::repo_health", "GovActionProcessStep::RepoCoarseScout::repo_language", "l1"),
            _link("GovActionProcessStep::RepoCoarseScout::repo_health", "GovActionProcessStep::RepoCoarseScout::repo_language", "l2"),
        ]
        result = diff_links(links, expected, "GovActionProcess::RepoCoarseScout")
        assert result.kept == 1
        assert result.removed_duplicate == 1
        assert result.removed_stale == 0
        assert [e.link_guid for e in result.to_remove] == ["l2"]
        assert result.to_remove[0].reason == "duplicate"

    def test_stale_edge_from_prior_chain_ordering_removed(self):
        # Reproduces Repo Full Survey's actual live bug: repo_ci_quality
        # used to link directly to repo_api_structure before
        # repo_maturity/repo_conventions were inserted between them.
        expected = compute_expected_edges("X", ["repo_ci_quality", "repo_maturity", "repo_conventions", "repo_api_structure"])
        links = [
            _link("GovActionProcessStep::X::repo_ci_quality", "GovActionProcessStep::X::repo_maturity", "l1"),
            _link("GovActionProcessStep::X::repo_maturity", "GovActionProcessStep::X::repo_conventions", "l2"),
            _link("GovActionProcessStep::X::repo_conventions", "GovActionProcessStep::X::repo_api_structure", "l3"),
            _link("GovActionProcessStep::X::repo_ci_quality", "GovActionProcessStep::X::repo_api_structure", "stale-l4"),
        ]
        result = diff_links(links, expected, "GovActionProcess::X")
        assert result.kept == 3
        assert result.removed_stale == 1
        assert result.removed_duplicate == 0
        assert result.to_remove[0].link_guid == "stale-l4"
        assert result.to_remove[0].reason == "stale"

    def test_duplicates_and_stale_together_full_survey_shape(self):
        # A step with 3 live copies of its real edge plus a stale edge to
        # a different step — exactly RepoFullSurvey's repo_ci_quality shape
        # before the manual fix.
        expected = compute_expected_edges("X", ["repo_ci_quality", "repo_maturity"])
        links = [
            _link("GovActionProcessStep::X::repo_ci_quality", "GovActionProcessStep::X::repo_maturity", "l1"),
            _link("GovActionProcessStep::X::repo_ci_quality", "GovActionProcessStep::X::repo_maturity", "l2-dup"),
            _link("GovActionProcessStep::X::repo_ci_quality", "GovActionProcessStep::X::repo_api_structure", "l3-stale"),
        ]
        result = diff_links(links, expected, "GovActionProcess::X")
        assert result.kept == 1
        assert result.removed_duplicate == 1
        assert result.removed_stale == 1
        assert result.removed_total == 2
        reasons = {(e.link_guid, e.reason) for e in result.to_remove}
        assert reasons == {("l2-dup", "duplicate"), ("l3-stale", "stale")}

    def test_no_links_and_no_expected_edges_is_clean(self):
        result = diff_links([], set(), "GovActionProcess::SingleStep")
        assert result.kept == 0
        assert result.to_remove == []

    def test_idempotent_on_already_reconciled_graph(self):
        # Running the reconciler twice on an already-clean graph must be a
        # true no-op both times.
        expected = compute_expected_edges("X", ["a", "b"])
        links = [_link("GovActionProcessStep::X::a", "GovActionProcessStep::X::b", "l1")]
        first = diff_links(links, expected, "GovActionProcess::X")
        second = diff_links(links, expected, "GovActionProcess::X")
        assert first.to_remove == second.to_remove == []
        assert first.kept == second.kept == 1


FIXTURE_SCOPE_DOC = """## Create Governance Action Process Step
### Display Name
Repo Full Survey — Repo Health

### Qualified Name
GovActionProcessStep::RepoFullSurvey::repo_health

### Description
Runs the repo_health analysis.

___

## Create Governance Action Process
### Display Name
Repo Full Survey

### Qualified Name
GovActionProcess::RepoFullSurvey

### Description
Runs every current STEP_REGISTRY step.

___

## Link First Process Step
### Governance Action Process
GovActionProcess::RepoFullSurvey

### Governance Action Process Step
GovActionProcessStep::RepoFullSurvey::repo_health

___

## Link Element To Scope
### Target Element
Repo Full Survey

### Scope Reference
What does this repository do?

___

## Link Element To Scope
### Target Element
Repo Full Survey

### Scope Reference
Is this repository actively maintained?

___
"""


class TestExpectedScopesFromDocument:
    def test_two_scope_blocks_and_unrelated_commands(self):
        expected = expected_scopes_from_document(FIXTURE_SCOPE_DOC)
        assert expected == {
            "What does this repository do?",
            "Is this repository actively maintained?",
        }

    def test_no_scope_blocks_is_empty(self):
        doc = "## Create Governance Action Process Step\n### Display Name\nX\n"
        assert expected_scopes_from_document(doc) == set()

    def test_empty_document_is_empty(self):
        assert expected_scopes_from_document("") == set()

    def test_stops_at_next_heading_without_scope_reference(self):
        """A 'Link Element To Scope' block missing its Scope Reference section
        (malformed authoring) contributes nothing rather than borrowing a
        LATER command's Scope Reference — the bound must be the next '## '
        heading, not "keep scanning until one is found somewhere.\""""
        doc = (
            "## Link Element To Scope\n"
            "### Target Element\n"
            "Repo Full Survey\n"
            "\n"
            "## Some Other Command\n"
            "### Scope Reference\n"
            "Bogus value that must not leak into the malformed block above\n"
            "\n"
            "___\n"
        )
        assert expected_scopes_from_document(doc) == set()

    def test_two_blocks_separated_by_an_unrelated_command_each_keep_only_their_own(self):
        doc = (
            "## Link Element To Scope\n"
            "### Target Element\n"
            "Repo Full Survey\n"
            "\n"
            "### Scope Reference\n"
            "What does this repository do?\n"
            "\n"
            "___\n"
            "\n"
            "## Link Element To Scope\n"
            "### Target Element\n"
            "Repo Full Survey\n"
            "\n"
            "### Scope Reference\n"
            "Is this repository actively maintained?\n"
        )
        assert expected_scopes_from_document(doc) == {
            "What does this repository do?",
            "Is this repository actively maintained?",
        }


def _scope(display_name, guid="guid-1", qualified_name=None, type_name="GlossaryTerm"):
    return {
        "elementHeader": {"guid": guid, "type": {"typeName": type_name}},
        "properties": {"qualifiedName": qualified_name, "displayName": display_name},
    }


class TestDiffScopes:
    def test_kept_missing_extra_unresolvable_all_present(self):
        expected = {"Q1", "Q2"}
        live = [
            _scope("Q1", guid="g1", qualified_name="Org::Term::Q1::1.0"),
            _scope("Q3", guid="g3", qualified_name="Org::Term::Q3::1.0"),  # extra
            {  # unresolvable: not a GlossaryTerm
                "elementHeader": {"guid": "g4", "type": {"typeName": "Perspective"}},
                "properties": {"qualifiedName": "Perspective::X", "displayName": "X"},
            },
            {  # unresolvable: GlossaryTerm with no displayName
                "elementHeader": {"guid": "g5", "type": {"typeName": "GlossaryTerm"}},
                "properties": {"qualifiedName": "Org::Term::Weird::1.0"},
            },
        ]
        result = diff_scopes(live, expected)
        assert result.kept == ["Q1"]
        assert result.missing == ["Q2"]
        assert [e.display_name for e in result.extra] == ["Q3"]
        assert result.extra[0].guid == "g3"
        assert result.extra[0].qualified_name == "Org::Term::Q3::1.0"
        assert len(result.unresolvable) == 2
        reasons = {u.guid for u in result.unresolvable}
        assert reasons == {"g4", "g5"}

    def test_clean_match_has_nothing_missing_or_extra(self):
        expected = {"Q1", "Q2"}
        live = [_scope("Q1"), _scope("Q2", guid="g2")]
        result = diff_scopes(live, expected)
        assert sorted(result.kept) == ["Q1", "Q2"]
        assert result.missing == []
        assert result.extra == []
        assert result.unresolvable == []

    def test_no_live_scopes_reports_everything_missing(self):
        # 2026-08-19 shape: the questions batch was absent, so every Link
        # Element To Scope command created nothing.
        result = diff_scopes([], {"Q1", "Q2"})
        assert result.kept == []
        assert sorted(result.missing) == ["Q1", "Q2"]
        assert result.extra == []

    def test_stale_term_left_linked_is_extra_not_missing(self):
        # 2026-09-13 shape: a superseded term stayed ScopedBy after being
        # replaced in the document by new questions.
        expected = {"New question A", "New question B"}
        live = [_scope("Superseded question", guid="term-guid", qualified_name="Org::Term::Superseded::1.0")]
        result = diff_scopes(live, expected)
        assert result.kept == []
        assert sorted(result.missing) == ["New question A", "New question B"]
        assert [e.display_name for e in result.extra] == ["Superseded question"]
        assert result.extra[0].guid == "term-guid"

    def test_non_dict_live_entry_is_unresolvable_not_extra(self):
        result = diff_scopes(["not-a-dict"], {"Q1"})
        assert result.extra == []
        assert len(result.unresolvable) == 1
        assert result.missing == ["Q1"]

    def test_empty_everything_is_clean(self):
        result = diff_scopes([], set())
        assert result.kept == result.missing == [] and result.extra == result.unresolvable == []

    # ── a DEPRECATED term: its own bucket (architect ruling (b), 2026-10-23) ──────────────────────────────
    # The term batch marks retired and merged questions DEPRECATED (contentStatus) and keeps their links.

    @staticmethod
    def _deprecated(display_name, guid, description="", status_in="contentStatus"):
        item = _scope(display_name, guid=guid, qualified_name=f"Org::Term::{guid}::1.0")
        if status_in == "contentStatus":
            item["properties"]["contentStatus"] = "DEPRECATED"
        else:
            item["elementHeader"]["status"] = "DEPRECATED"
        if description:
            item["properties"]["description"] = description
        return item

    def test_deprecated_term_is_its_own_bucket_never_kept_missing_or_extra(self):
        """Fail-on-purpose: before the bucket, the named one read "kept" and the unnamed one "extra"."""
        expected = {"Retired question", "Live question"}
        live = [
            self._deprecated("Retired question", "g-ret", "retired 2026-10-22: answered by Q-0044 instead"),
            self._deprecated("Merged question", "g-mrg", "merged into Q-0016 2026-10-22: same ask"),
            self._deprecated("Bare question", "g-bare", status_in="header"),
            _scope("Live question", guid="g-live"),
        ]
        result = diff_scopes(live, expected)
        assert result.kept == ["Live question"]
        assert result.missing == []
        assert result.extra == [] and result.unresolvable == []
        assert [(d.display_name, d.guid, d.label) for d in result.deprecated] == [
            ("Retired question", "g-ret", "deprecated: retired 2026-10-22"),
            ("Merged question", "g-mrg", "deprecated: merged into Q-0016 2026-10-22"),
            ("Bare question", "g-bare", "deprecated: deprecated"),
        ]

    def test_reconcile_scripts_render_the_deprecated_bucket(self):
        """Both consumers of diff_scopes print the bucket, and neither counts it as drift or removes it."""
        from pathlib import Path

        scripts = Path(__file__).resolve().parents[1] / "scripts"
        for name in ("reconcile_survey_definition_scopes.py", "reconcile_database_survey_definition_scopes.py"):
            text = (scripts / name).read_text()
            assert "for d in result.deprecated:" in text and "d.label" in text, name
            assert "deprecated {len(result.deprecated)}" in text, name
            assert "if result.missing or result.extra:" in text, name     # drift stays missing/extra only
