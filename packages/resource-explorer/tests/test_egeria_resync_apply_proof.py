"""Resync apply proof and readiness (2026-10-01).

Apply used to fail per-repo at the publish gate for docling and egeria_docs,
write no activity_log row, show a green toast, and then redraw the panel over
the evidence. Four fixes, one test each. Every test was run RED against the
pre-fix code before the fix went in (see the implementation note).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from resource_explorer.egeria_resync import CATALOG_STEPS, EgeriaResync

from tests.test_egeria_resync_catalog import _registry

STATIC = (Path(__file__).resolve().parents[1]
          / "resource_explorer" / "web" / "static")


def test_a_refused_publish_leaves_an_activity_log_row():
    """Fix 1: the 428 refusal returned before any publisher ran, so nothing
    recorded it. Known-negative: a successful publish is not logged as failed."""
    reg = MagicMock()
    reg.get_project_context.return_value = {"status": "unset"}
    reg.inherited_egeria_project_context.return_value = None
    out = EgeriaResync(registry=reg)._publish_one("docling", CATALOG_STEPS)
    assert out["ok"] is False
    (call,) = reg.write_activity.call_args_list
    entry = call.args[0]
    assert (entry.entity_slug, entry.status) == ("docling", "error")
    assert "428" in entry.summary

    # the check can fail: a not-ok outcome is what triggers it, nothing else
    ok_reg = MagicMock()
    r = EgeriaResync(registry=ok_reg)
    r._publish_one_unlogged = lambda s, st: {"slug": s, "ok": True}
    assert r._publish_one("kafka", CATALOG_STEPS)["ok"] is True
    ok_reg.write_activity.assert_not_called()


def test_both_uis_show_a_persistent_failure_and_no_unconditional_green():
    """Fix 2, pinned at source level (no Node in CI, same as the other /next
    JS tests)."""
    nxt = (STATIC / "next/admin/resync.js").read_text()
    classic = (STATIC / "index.html").read_text()
    # Next: failures render, and the refresh is skipped until dismissed.
    assert "data-resync-failures" in nxt and "data-resync-dismiss" in nxt
    body = nxt[nxt.index("async function showApplyResult"):]
    body = body[:body.index("\n}\n")]
    assert body.index("applyFailures(data).length") < body.index("await load()")
    assert "return;" in body[:body.index("await load()")]
    assert "data.reachable === false" in nxt
    # Classic: no unconditional success toast, and the counts are stated.
    assert "'Resync applied.'" not in classic
    assert "ok · " in classic and "failed" in classic
    assert "resync-dismiss" in classic and "data.reachable === false" in classic


def test_a_repo_with_no_project_context_gets_a_sentence_and_no_tickbox():
    """Fix 3. Known-negative: a repo that inherits a context stays tickable."""
    reg = _registry([("docling", "asset-1", "unset"), ("kafka", "asset-2", "unset")],
                    inherits={"kafka"})
    r = EgeriaResync(registry=reg)
    assert [i["slug"] for i in r._scan_registration_only().items] == ["kafka"]
    blocked = r._scan_registration_blocked()
    assert [i["slug"] for i in blocked.items] == ["docling"]
    assert blocked.repair_step == "" and blocked.needs_decision
    assert "no Egeria Project" in blocked.items[0]["blocked_reason"]
    assert "would not happen" not in Path(
        __import__("resource_explorer.egeria_resync", fromlist=["x"]).__file__
    ).read_text()


def test_published_means_a_survey_row_exists_not_a_published_analyses_claim():
    """Fix 4: docling was published by the scheduled refresh (survey row, no
    claims) and kept being offered. Known-negative: no survey row, still listed."""
    reg = _registry([("docling", "asset-1", "linked"), ("fresh", "asset-2", "linked")],
                    surveyed=("docling",))
    items = EgeriaResync(registry=reg)._scan_registration_only().items
    assert [i["slug"] for i in items] == ["fresh"]
