"""The architecture diagram labels an untyped node "type not assigned ·
boundary only" and a score "confidence N%" (arch_recovery/mermaid.py). Two other
surfaces carried the same bare-percentage mislabel; they now use the same words.
Source-text checks, matching the precedent in test_next_component_review.py."""
from __future__ import annotations

from pathlib import Path

from resource_explorer.surveyors.arch_recovery.mermaid import UNTYPED_LABEL

STATIC = Path(__file__).resolve().parents[1] / "resource_explorer" / "web" / "static"


def test_classic_component_list_uses_boundary_and_confidence_wording():
    src = (STATIC / "index.html").read_text(encoding="utf-8")
    assert (
        "${c.type ? _esc(c.type) : '" + UNTYPED_LABEL + "'} · confidence ${c.confidence}%"
    ) in src
    assert "'untyped'} · ${c.confidence}%" not in src


def test_next_curate_leaf_uses_boundary_and_confidence_wording():
    src = (STATIC / "next" / "stages" / "curate.js").read_text(encoding="utf-8")
    assert f"l.type ? esc(l.type) : '{UNTYPED_LABEL}'" in src
    assert 'confidence <span class="tnum">${l.confidence}</span>%' in src
    assert 'confidence <span class="tnum">${l.confidence ?? 0}</span>%' in src
    assert '· <span class="tnum">${l.confidence}</span>%' not in src
    assert '⚠ <span class="tnum">${l.confidence ?? 0}</span>%' not in src


# Exhaustive sweep (2026-10-01): every remaining user-facing bare confidence
# percentage. Each hit gets one line; revert the fix and the line fails.
def test_next_curate_proposal_row_labels_confidence():
    src = (STATIC / "next" / "stages" / "curate.js").read_text(encoding="utf-8")
    assert "reading · confidence <span class=\"tnum\">${p.confidence ?? 0}</span>%" in src
    assert "reading · <span class=\"tnum\">${p.confidence ?? 0}</span>%" not in src


def test_classic_evidence_line_labels_confidence():
    src = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '(confidence ${e.confidence}%)' in src
    assert '(${e.confidence}%)' not in src
