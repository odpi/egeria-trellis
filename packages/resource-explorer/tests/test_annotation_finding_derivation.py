"""The finding row is DERIVED from the annotation, not typed again beside it.

Written for the security_hygiene migration (2026-09-02), the first surveyor
moved onto `check_name`/`label`. The bug these pin is not hypothetical: before
the migration the SECURITY.md check emitted an annotation at confidence=90 and
stored a finding that omitted confidence, so project_analysis_findings
defaulted it to 100. Same check, same run, two stores, two numbers, and
nothing anywhere raised — which is why it survived.

Each test below was run against the pre-migration surveyor and observed to
fail, so a green result here means the derivation holds rather than that the
check did not fire.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from resource_explorer.registry import Project, ProjectRegistry
from resource_explorer.surveyors.sub_surveyors.security_hygiene import (
    SecurityHygieneSurveyor,
)
from resource_explorer.surveyors.survey_report import (
    ClassificationAnnotation,
    RequestForActionAnnotation,
    annotation_qualified_name,
    finding_from_annotation,
    findings_from_annotations,
)


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def project(registry):
    p = Project(slug="myproj", display_name="My Project",
                github_url="https://github.com/test/myproj", collections=[])
    registry.add(p)
    return p


def _seed(registry, slug, *paths):
    with registry._conn() as conn:
        for path in paths:
            conn.execute(
                "INSERT INTO project_file_inventory (project_slug, file_path, indexed_at) "
                "VALUES (?, ?, ?)", (slug, path, "2026-01-01T00:00:00"))


class TestTheDriftIsGone:
    def test_the_security_md_check_reports_one_confidence_not_two(self, registry, project):
        """The exact drift: annotation said 90, the finding row said 100."""
        _seed(registry, "myproj", "src/a.py")          # no SECURITY.md
        annotations = SecurityHygieneSurveyor(project, registry).run()

        rfa = next(a for a in annotations
                   if isinstance(a, RequestForActionAnnotation)
                   and a.check_name == "security_policy")
        assert rfa.confidence == 90, "fixture assumption: the RFA is emitted at 90"

        row = next(f for f in registry.query_findings("myproj", "security_hygiene")
                   if f["check_name"] == "security_policy")
        assert row["confidence"] == rfa.confidence == 90

    def test_no_check_disagrees_with_its_own_annotation(self, registry, project):
        """The general form — every stored row matches the annotation it came
        from on every field both records carry."""
        _seed(registry, "myproj", "src/a.py")
        annotations = SecurityHygieneSurveyor(project, registry).run()
        rows = {f["check_name"]: f
                for f in registry.query_findings("myproj", "security_hygiene")}

        labelled = [a for a in annotations if a.label]
        assert labelled, "fixture assumption: the surveyor emits labelled annotations"
        for a in labelled:
            row = rows[a.check_name]
            assert row["confidence"] == a.confidence, a.check_name
            assert row["summary"] == a.summary, a.check_name
            assert row["label"] == a.label, a.check_name

    def test_the_explanation_survives_into_the_finding(self, registry, project):
        """Previously dropped: the annotation carried an explanation and the
        hand-written finding had nowhere to put it."""
        _seed(registry, "myproj", "src/a.py")
        SecurityHygieneSurveyor(project, registry).run()
        row = next(f for f in registry.query_findings("myproj", "security_hygiene")
                   if f["check_name"] == "security_policy")
        detail = json.loads(row["detail_json"])
        assert "vulnerabilities responsibly" in detail["explanation"]

    def test_the_unverified_early_return_still_leaves_a_row(self, registry, project):
        """An empty inventory is a result, not a silence — and it goes through
        the same derived path as a completed run."""
        annotations = SecurityHygieneSurveyor(project, registry).run()   # no inventory
        rows = registry.query_findings("myproj", "security_hygiene")
        assert [r["check_name"] for r in rows] == ["inventory_available"]
        assert rows[0]["label"] == "unverified"
        assert rows[0]["confidence"] == annotations[0].confidence


class TestDerivationItself:
    def test_an_unlabelled_annotation_is_not_a_finding(self):
        """A measure or a warning has no verdict, so it must not be stored as
        one with an empty label."""
        labelled = ClassificationAnnotation(summary="s", analysis_step="x",
                                            check_name="c", label="pass")
        unlabelled = ClassificationAnnotation(summary="s", analysis_step="x")
        assert len(findings_from_annotations([labelled, unlabelled])) == 1

    def test_the_rfa_action_pair_reaches_the_detail(self):
        row = finding_from_annotation(RequestForActionAnnotation(
            summary="No LICENSE", analysis_step="x", check_name="license",
            label="gap", action_requested="Add one", action_target_name="LICENSE"))
        assert row["detail"]["action_requested"] == "Add one"
        assert row["detail"]["action_target_name"] == "LICENSE"

    def test_a_key_collision_cannot_erase_the_typed_field(self):
        """A real collision, not a co-existence: json_properties carrying the
        same key must not overwrite the annotation's own structured answer."""
        row = finding_from_annotation(RequestForActionAnnotation(
            summary="s", analysis_step="x", check_name="c", label="gap",
            action_target_name="LICENSE",
            json_properties={"action_target_name": "WRONG", "cause": "none"}))
        assert row["detail"]["action_target_name"] == "LICENSE"
        assert row["detail"]["cause"] == "none"


class TestTheQualifiedNameIsNowStable:
    def test_the_same_check_gets_the_same_name_at_a_different_position(self, registry, project):
        """The reason for the whole change: the name used to be the enumeration
        index, and a check's position moves between runs."""
        a = RequestForActionAnnotation(summary="s", analysis_step="x",
                                       check_name="security_policy", label="gap")
        assert (annotation_qualified_name("P", 3, a)
                == annotation_qualified_name("P", 41, a)
                == "P::security_policy")

    def test_two_runs_of_the_surveyor_name_the_same_checks_the_same_way(self, registry, project):
        _seed(registry, "myproj", "src/a.py")
        first = SecurityHygieneSurveyor(project, registry).run()
        second = SecurityHygieneSurveyor(project, registry).run()
        names = lambda anns: sorted(annotation_qualified_name("P", i, a)
                                    for i, a in enumerate(anns))
        assert names(first) == names(second)
        assert len(set(names(first))) == len(first), "names must not collide"


class TestInconclusiveIsNotStoredAsCertain:
    """The same drift as security_hygiene's, in its worst form.

    Four surveyors emit their summary annotation at `100 if conclusive else 0`
    and then stored a finding row that omitted confidence — so the table
    defaulted it to 100. "We could not conclude" was recorded as certainty,
    across 98,364 secret_scan rows among others. The persist step dropped the
    field structurally, in a list comprehension, so setting it on the finding
    dict would not have helped either.
    """

    FILES = ["secret_scan", "telemetry_scan", "contribution_provenance", "sla_content"]

    @pytest.mark.parametrize("name", FILES)
    def test_the_persist_step_carries_confidence(self, name):
        """The comprehension that builds upsert_finding()'s rows must pass
        confidence through — otherwise every per-finding value is discarded on
        the way to the table and no amount of setting it upstream matters."""
        src = (pathlib.Path(__file__).parent.parent / "resource_explorer" /
               "surveyors" / "sub_surveyors" / f"{name}.py").read_text()
        assert '"summary": f["summary"]' in src, "fixture assumption: the comprehension exists"
        assert '"confidence": f.get("confidence"' in src, (
            f"{name}.py's persist comprehension drops confidence, so a finding "
            f"row reads 100 (the column default) whatever the surveyor decided")

    @pytest.mark.parametrize("name", FILES)
    def test_an_inconclusive_summary_is_not_stored_at_full_confidence(self, name):
        """Every `scan_summary` finding must carry a confidence, since its
        paired annotation is 0 whenever the step could not conclude."""
        src = (pathlib.Path(__file__).parent.parent / "resource_explorer" /
               "surveyors" / "sub_surveyors" / f"{name}.py").read_text()
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if '"check_name": "scan_summary"' in line:
                window = "".join(lines[i:i + 4])
                assert '"confidence"' in window, (
                    f"{name}.py:{i+1} stores a scan_summary finding with no "
                    f"confidence, so an inconclusive run reads as certain")


class TestTwoSecretsOnOneLine:
    """The collision a real survey found and static analysis did not.

    egeria_trellis has two `generic-api-key` matches at
    packages/egeria-advisor/cache/doc_sections.json:24201 — one long JSON line,
    two hits of the same rule. The first item_key was path:line:rule_id, which
    assumed that triple identifies a match. It does not, and the two
    annotations would have published the same qualifiedName: Egeria rejects the
    duplicate, apply_element adopts the existing GUID, and the run reports
    success having written one where two were produced.

    The byte offset is what separates them, so it is now a field on SecretMatch
    rather than something reconstructed later.
    """

    def _match(self, offset):
        from resource_explorer.surveyors.sub_surveyors.secret_ruleset import SecretMatch
        return SecretMatch(
            rule_id="generic-api-key", description="Generic API Key",
            path="packages/egeria-advisor/cache/doc_sections.json", line=24201,
            excerpt="****", offset=offset)

    def test_the_offset_distinguishes_two_hits_of_one_rule_on_one_line(self):
        a, b = self._match(982_144), self._match(982_301)
        keys = {f"{m.path}:{m.line}:{m.offset}:{m.rule_id}" for m in (a, b)}
        assert len(keys) == 2, "two distinct matches must not share an item_key"

    def test_without_the_offset_they_would_have_collided(self):
        """Known-positive: the old key really does collapse them, so the test
        above is checking something rather than passing by construction."""
        a, b = self._match(982_144), self._match(982_301)
        old = {f"{m.path}:{m.line}:{m.rule_id}" for m in (a, b)}
        assert len(old) == 1

    def test_the_scanner_records_a_real_offset(self):
        """A defaulted -1 reaching a real scan would make every match on a line
        collide again, silently."""
        src = (pathlib.Path(__file__).parent.parent / "resource_explorer" / "surveyors"
               / "sub_surveyors" / "secret_ruleset.py").read_text()
        # make() in _scan_text_bounded passes base + m.start() as `start`.
        assert "offset=start," in src and "start = base + m.start()" in src

    def test_two_such_annotations_do_not_collide_end_to_end(self):
        from resource_explorer.surveyors.survey_report import (
            RequestForActionAnnotation, assert_unique_qualified_names)
        anns = [RequestForActionAnnotation(
                    summary="Possible secret", analysis_step="repo_secret_scan",
                    check_name="secret_pattern",
                    item_key=f"{m.path}:{m.line}:{m.offset}:{m.rule_id}")
                for m in (self._match(982_144), self._match(982_301))]
        assert_unique_qualified_names("P", anns)     # raises on collision
