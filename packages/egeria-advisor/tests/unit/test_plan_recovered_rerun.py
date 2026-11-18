"""A plan that ran, was recovered to inbox, and is executed again is a re-run (2026-11-13).

move_to_inbox (Recover for Editing, and Retry) strips the outcome and the `_executed_` suffix, so the plan
looked like a first run, and its Link commands ran again on a plain yes. Now move_to_inbox leaves a
`<!-- previously_executed: <when> · as <executed id> -->` marker in the plan's header (above the Command
Sequence, so Dr.Egeria never sees it), and prepare_execute treats a marked inbox plan as a re-run: its
non-idempotent commands need "yes, re-run" (test_plan_rerun_ack.py has the rest of that rule).

The marker is sticky. update() (every editor save, every canvas re-compose through sync_document) and
restore_version() carry it forward, so neither an edit nor restoring an older text erases that the plan ran.
"""
from __future__ import annotations

import pytest
from test_dr_egeria_identity import USER, agent  # noqa: F401
from test_plan_execute_confirm_gate import ERIN, api, docs, gpa, ran  # noqa: F401

from advisor.agents.governance_plan_agent import PLAN_ACK_REQUIRED
from advisor.governance_docs import executed_note, mark_previously_executed, previously_executed

PLAN = ("# Linking plan\n**Created by:** erinoverview\n\n## Command Sequence\n\n"
        "## Create Glossary\n\n### Display Name\nTerms\n\n"
        "## Link Term to Term\n\n### Term 1\nA\n")
SAFE = "# Safe plan\n\n## Command Sequence\n\n## Create Glossary\n\n### Display Name\nTerms\n"
OUTCOME = "\n\n---\n\n## Outcome\n\n**Status:** Success\n"


def _ran_and_recovered(docs, content=PLAN):  # noqa: F811
    doc = docs.import_document(content, user_id="erinoverview")
    out_id = docs.move_to_outbox(doc, OUTCOME)
    assert out_id and "_executed_" in out_id
    back = docs.move_to_inbox(out_id)
    assert back == doc
    return doc, out_id


# ── the marker ────────────────────────────────────────────────────────────────

def test_recovery_keeps_a_marker_in_the_header_and_strips_the_outcome(docs):  # noqa: F811
    doc, out_id = _ran_and_recovered(docs)
    content = docs.load(doc)
    assert "## Outcome" not in content
    note = previously_executed(content)
    assert note and note.endswith(f"· as {out_id}") and note[:4].isdigit()
    head, commands = content.split("## Command Sequence", 1)
    assert "previously_executed" in head and "previously_executed" not in commands   # Dr.Egeria never sees it
    assert content.startswith("# Linking plan\n<!-- previously_executed:")


def test_a_first_plan_has_no_marker(docs):  # noqa: F811
    doc = docs.import_document(PLAN, user_id="erinoverview")
    assert previously_executed(docs.load(doc)) is None


def test_recovering_twice_leaves_one_marker_naming_the_latest_run(docs):  # noqa: F811
    doc, first = _ran_and_recovered(docs)
    second = docs.move_to_outbox(doc, OUTCOME)
    docs.move_to_inbox(second)
    content = docs.load(doc)
    assert content.count("previously_executed") == 1
    assert previously_executed(content).endswith(f"· as {second}")


def test_an_edit_that_drops_the_marker_does_not_drop_the_history(docs):  # noqa: F811
    """The editor's narrative textarea or a canvas re-compose can lose the comment; update() puts it back."""
    doc, out_id = _ran_and_recovered(docs)
    edited = PLAN.replace("Terms", "Terms (edited)")          # the original text: no marker in it
    assert docs.update(doc, edited, edited_by="erinoverview")
    content = docs.load(doc)
    assert "Terms (edited)" in content and previously_executed(content).endswith(f"· as {out_id}")


def test_restoring_an_older_version_keeps_that_the_plan_ran(docs):  # noqa: F811
    doc = docs.import_document(PLAN, user_id="erinoverview")
    docs.update(doc, PLAN.replace("Terms", "Terms v2"), edited_by="erinoverview")   # a pre-run version exists
    pre_run = next(v["version_file"] for v in docs.list_versions(doc))
    out_id = docs.move_to_outbox(doc, OUTCOME)
    assert docs.restore_version(out_id, pre_run)
    content = docs.load(doc)
    assert previously_executed(content), content[:200]


def test_the_marker_helpers():
    note = executed_note("plan_x_executed_20261113_031500")
    assert note == "2026-11-13T03:15:00 · as plan_x_executed_20261113_031500"
    marked = mark_previously_executed(SAFE, note)
    assert previously_executed(marked) == note
    assert mark_previously_executed(marked, "later").count("previously_executed") == 1
    assert previously_executed(mark_previously_executed("no title\n", "n")) == "n"


# ── executing a recovered plan is a re-run ────────────────────────────────────

def test_a_recovered_plan_with_link_commands_asks_for_yes_re_run(gpa, ran, docs):  # noqa: F811
    doc, out_id = _ran_and_recovered(docs)
    shown = gpa.prepare_execute(doc, egeria_credentials=USER)
    assert shown["rerun"] is True and shown["ack_required"] is True
    assert shown["previously_executed"].endswith(f"· as {out_id}")
    assert "This plan ran before" in shown["response"] and "**yes, re-run**" in shown["response"]
    assert [u["command"] for u in shown["non_idempotent"]] == ["Link Term to Term"]
    no = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)
    assert no["state"] == "refused" and no["response"].startswith(PLAN_ACK_REQUIRED) and ran == []
    yes = gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER, ack_non_idempotent=True)
    assert yes["response"] == "ran" and len(ran) == 1 and ran[0]["source_folder"] == "inbox"


def test_a_recovered_plan_with_nothing_unsafe_needs_only_yes(gpa, ran, docs):  # noqa: F811
    doc, _ = _ran_and_recovered(docs, SAFE)
    shown = gpa.prepare_execute(doc, egeria_credentials=USER)
    assert shown["rerun"] is True and shown["ack_required"] is False
    assert gpa.confirm(shown["pending_id"], shown["plan_digest"], egeria_credentials=USER)["response"] == "ran"


def test_through_the_routes_recover_then_execute_needs_the_acknowledgement(api, docs, ran):  # noqa: F811
    doc = docs.import_document(PLAN, user_id="erinoverview")
    out_id = docs.move_to_outbox(doc, OUTCOME)
    rec = api.post(f"/api/plans/{out_id}/recover", headers=ERIN)
    assert rec.status_code == 200 and rec.json()["doc_id"] == doc
    shown = api.post(f"/api/plans/{doc}/execute", headers=ERIN, json={}).json()
    assert shown["ack_required"] is True and shown["previously_executed"]
    body = {"pending_id": shown["pending_id"], "digest": shown["plan_digest"]}
    assert api.post(f"/api/plans/{doc}/execute", headers=ERIN, json=body).json()["state"] == "refused"
    assert ran == []
    ok = api.post(f"/api/plans/{doc}/execute", headers=ERIN, json={**body, "ack_non_idempotent": True}).json()
    assert ok["response"] == "ran" and len(ran) == 1


@pytest.mark.parametrize("edit", [True, False])
def test_editing_a_recovered_plan_keeps_it_a_re_run(gpa, ran, docs, edit):  # noqa: F811
    doc, _ = _ran_and_recovered(docs)
    if edit:
        docs.update(doc, PLAN, edited_by="erinoverview")
    assert gpa.prepare_execute(doc, egeria_credentials=USER)["ack_required"] is True
