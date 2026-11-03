"""Guards for configdata/check_registry.yaml and the check-granularity join
it backs (added 2026-08-24; see docs/investigation-framing-design.md).

The registry is hand-maintained but describes runtime behaviour, so the
failure mode to design against is silent drift: a surveyor gains or renames a
check, the registry doesn't, and every question referencing it quietly stops
resolving. Nothing else would notice — an unresolved ref looks exactly like a
question nobody has tagged yet.

Deliberately NOT tested here: agreement with the live Postgres registry.
`project_analysis_findings` only shows checks that have actually run on some
repo, so it can confirm the registry isn't missing anything real but can never
confirm the registry isn't stale. Re-verify that by hand with:

    SELECT kind, check_name, count(*) FROM project_analysis_findings GROUP BY 1,2
"""
from __future__ import annotations

from pathlib import Path

import yaml

CONFIGDATA = Path(__file__).parent.parent / "resource_explorer" / "configdata"
REGISTRY = CONFIGDATA / "check_registry.yaml"
QUESTIONS = CONFIGDATA / "question_catalog.yaml"
ANALYSES = CONFIGDATA / "analysis_catalog.yaml"


def _registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text())


def _known_refs() -> set[str]:
    return {
        f"{aid}:{check}"
        for aid, spec in _registry()["analyses"].items()
        for check in spec["checks"]
    }


def test_every_registry_analysis_id_is_a_real_analysis():
    """A registry entry for an analysis that doesn't exist is dead config, and
    would let a question reference a check that can never run."""
    catalog = yaml.safe_load(ANALYSES.read_text())
    # Database analyses too since 2026-10-20: quality_checks names its checks
    # (the vendored test types) here, for destinations.
    real = {a["id"] for a in catalog["repo_analyses"] + catalog["database_analyses"]}
    for aid in _registry()["analyses"]:
        assert aid in real, f"check_registry declares unknown analysis id: {aid}"


def test_analysis_ids_are_partitioned_without_overlap():
    """Each analysis appears in exactly one of the three sections. An analysis
    in both `analyses` and `whole_analysis_only` is a contradiction about
    whether it decomposes."""
    reg = _registry()
    sections = {
        "analyses": set(reg["analyses"]),
        "whole_analysis_only": set(reg["whole_analysis_only"]),
        "instance_keyed_not_checks": set(reg["instance_keyed_not_checks"]),
    }
    for a, b in [("analyses", "whole_analysis_only"),
                 ("analyses", "instance_keyed_not_checks"),
                 ("whole_analysis_only", "instance_keyed_not_checks")]:
        assert not sections[a] & sections[b], (
            f"{sorted(sections[a] & sections[b])} appears in both {a} and {b}"
        )


def test_every_repo_analysis_is_classified():
    """Silence is ambiguous: an analysis absent from all three sections could
    mean "has no checks" or "nobody has looked". Force the distinction."""
    reg = _registry()
    classified = (
        set(reg["analyses"]) | set(reg["whole_analysis_only"])
        | set(reg["instance_keyed_not_checks"])
    )
    catalog = yaml.safe_load(ANALYSES.read_text())
    for a in catalog["repo_analyses"]:
        assert a["id"] in classified, (
            f"analysis {a['id']!r} is in analysis_catalog.yaml but not classified in "
            f"check_registry.yaml — add it to `analyses` (with its checks), "
            f"`whole_analysis_only`, or `instance_keyed_not_checks`"
        )


def test_check_names_are_unique_within_an_analysis():
    for aid, spec in _registry()["analyses"].items():
        checks = spec["checks"]
        assert len(checks) == len(set(checks)), f"duplicate check names in {aid}"


def test_every_entry_declares_findings_kind_and_source():
    """`findings_kind` is not always the analysis id (security_scan writes as
    `security_hygiene`, documentation_coverage as `documentation`), so it must
    be explicit rather than inferred. `source` is what makes drift diagnosable."""
    for aid, spec in _registry()["analyses"].items():
        assert spec.get("findings_kind"), f"{aid} has no findings_kind"
        assert spec.get("source"), f"{aid} has no source pointer"


def test_question_catalog_check_refs_all_resolve():
    """The join's whole point. An unresolvable ref is indistinguishable from an
    untagged question, so it must fail loudly here."""
    known = _known_refs()
    questions = yaml.safe_load(QUESTIONS.read_text())["repo_questions"]
    for entry in questions:
        for ref in entry["answering"].get("checks") or []:
            assert ref in known, (
                f"question {entry['question']!r} references unknown check {ref!r}; "
                f"fix docs/dr-egeria/resource_questions.csv or check_registry.yaml"
            )


def test_check_refs_imply_their_analysis_id():
    """The generator adds a check ref's analysis to analysis_ids so authors
    don't write both. If that stops happening, analysis-level consumers
    silently lose questions that only carry check refs."""
    questions = yaml.safe_load(QUESTIONS.read_text())["repo_questions"]
    for entry in questions:
        answering = entry["answering"]
        for ref in answering.get("checks") or []:
            aid = ref.split(":", 1)[0]
            assert aid in answering["analysis_ids"], (
                f"question {entry['question']!r} has check {ref!r} but {aid!r} "
                f"is missing from analysis_ids — regenerate question_catalog.yaml"
            )


def test_every_question_carries_at_least_one_purpose():
    """Purpose is the primary dispatch axis (Perspective was measured and
    cannot discriminate), so an untagged question is invisible to dispatch
    rather than merely unlabelled."""
    questions = yaml.safe_load(QUESTIONS.read_text())["repo_questions"]
    untagged = [q["question"] for q in questions if not q.get("purposes")]
    assert not untagged, f"questions with no Purpose: {untagged}"


def test_question_purposes_are_in_the_known_vocabulary():
    """Purpose mirrors Egeria ProjectCharter's `purposes` valid metadata set —
    controlled so dispatch can key on it. A typo'd value would silently create
    a purpose nothing else ever matches."""
    known = {
        "Explore", "Select", "Assess", "Maintain", "Share",
        "Learn", "Certify", "Remediate", "Attest", "Deploy",
        # Added by the 2026-10-13 ruling (investigation_purposes.yaml's seed): Find and Integrate.
        "Find", "Integrate",
    }
    questions = yaml.safe_load(QUESTIONS.read_text())["repo_questions"]
    for entry in questions:
        for purpose in entry.get("purposes") or []:
            assert purpose in known, (
                f"question {entry['question']!r} has unknown purpose {purpose!r}"
            )


def test_purposes_did_not_leak_into_perspectives():
    """Both CSV scripts identify perspective columns BY ELIMINATION, so any
    column missing from their exclusion lists becomes a phantom Perspective on
    every row. 'Purposes' is such a column. If this fails, check
    NON_PERSPECTIVE_COLUMNS and csv_to_dr_egeria_questions.py's
    OPTIONAL_LEAD_COLUMNS."""
    questions = yaml.safe_load(QUESTIONS.read_text())["repo_questions"]
    seen = {p for q in questions for p in q.get("perspectives") or []}
    assert "Purposes" not in seen and "purposes" not in seen
    assert len(seen) == 12, f"expected 12 Perspective terms, got {len(seen)}: {sorted(seen)}"
