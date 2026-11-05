"""W69: the Q-0043 answer line ("What kind of thing is this repository") says the ROLE, not the word "Role".

Live (deep_causality, Discovery > Questions, 2026-11-05): the row read "Answered by: repo_classification Role" and the
fact's headline "Role · architecture recovery: run". The stored `repo_role` finding carries `label: "Role"` (the fixed
word for what the finding IS — see repo_classification.py) with the role words in `summary` ("samples (also library) —
architecture recovery: run") and `detail.roles`; the headline builder took the label. The headline now leads with the
role words, keeps the architecture-recovery tail, and a classification with no role verdict says so in words."""
from __future__ import annotations

import pytest

from resource_explorer.surveyors import repo_survey_definition_adapter as rsda

#: deep_causality's stored finding shape, 2026-11-05 (the live row).
LIVE_ROLE = {"check_name": "repo_role", "label": "Role",
             "summary": "samples (also library) — architecture recovery: run", "confidence": 60,
             "detail": {"roles": ["samples", "library"], "notes": []}}
GATE = {"check_name": "architecture_recovery_gate", "label": "run", "summary": "enough structure", "confidence": 60,
        "detail": {"primary_role": "samples", "roles": ["samples", "library"]}}
DATASET_KIND = {"check_name": "dataset_kind", "label": "DOI only", "summary": "a DOI names it (10.5281/zenodo.13750369)",
                "confidence": 60, "detail": {"state": "doi_only"}}


def _headline(monkeypatch, findings):
    monkeypatch.setattr(rsda, "_repo_classification_results", lambda registry, slug: {"findings": findings})
    return rsda._repo_classification_headline(object(), "deep_causality")


def test_the_headline_leads_with_the_role_words_and_keeps_the_gate_tail(monkeypatch):
    out = _headline(monkeypatch, [LIVE_ROLE, GATE, DATASET_KIND])
    assert out == {"label": "samples (also library) · architecture recovery: run", "status": "info"}
    assert "Role" not in out["label"]


def test_a_single_role_reads_as_the_role_alone(monkeypatch):
    role = {**LIVE_ROLE, "summary": "library — architecture recovery: run", "detail": {"roles": ["library"], "notes": []}}
    assert _headline(monkeypatch, [role, GATE])["label"] == "library · architecture recovery: run"


def test_without_ranked_roles_the_summary_words_are_read_never_the_label(monkeypatch):
    role = {**LIVE_ROLE, "detail": {"notes": []}}
    assert _headline(monkeypatch, [role, GATE])["label"] == "samples (also library) · architecture recovery: run"
    role_no_detail = {**LIVE_ROLE, "detail": {}}
    assert _headline(monkeypatch, [role_no_detail])["label"] == "samples (also library)"


@pytest.mark.parametrize("role", [
    None,                                                                       # no repo_role finding at all
    {**LIVE_ROLE, "summary": "", "detail": {"roles": [], "notes": []}},         # a finding with no roles
])
def test_a_classification_without_a_role_verdict_says_so_in_words(monkeypatch, role):
    findings = [f for f in (role, GATE, DATASET_KIND) if f]
    out = _headline(monkeypatch, findings)
    assert out["label"] == f"{rsda.ROLE_NOT_DETERMINED_WORDS} · architecture recovery: run"
    assert out["label"].startswith("role: not determined (classification ran without a role verdict)")


def test_no_findings_at_all_is_still_no_headline(monkeypatch):
    """Nothing ran: the fact layer's own never_run words, not a role sentence invented here."""
    assert _headline(monkeypatch, []) is None


def test_a_classification_error_still_wins(monkeypatch):
    err = {"check_name": "classification_error", "label": "error", "summary": "GitHub refused", "confidence": 0,
           "detail": {}}
    assert _headline(monkeypatch, [err, LIVE_ROLE]) == {"label": "GitHub refused", "status": "error"}


def test_the_role_words_helper_matches_the_surveyors_own_summary_shape():
    """`repo_role_words` joins `detail.roles` exactly as repo_classification.py writes the summary's head."""
    assert rsda.repo_role_words(LIVE_ROLE) == "samples (also library)"
    assert rsda.repo_role_words({"detail": {"roles": ["tool"]}}) == "tool"
    assert rsda.repo_role_words({"label": "Role", "summary": "", "detail": {}}) == rsda.ROLE_NOT_DETERMINED_WORDS
    assert rsda.repo_role_words(None) == rsda.ROLE_NOT_DETERMINED_WORDS
