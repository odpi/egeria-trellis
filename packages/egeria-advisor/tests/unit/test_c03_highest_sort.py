"""C03, "Which risks in the risk register have the highest importance?" (quiet live re-run 2026-11-17, 21f906da).

The LLM's recorded reply set NO sort. It asked for a column `importanceLevel`, which isn't an attribute of
Risk and was dropped, and its "unmet" was "The request does not specify a particular importance level or
status, so all risks are listed." So the run showed 12 risks in Egeria's order with no importance column.
The TR-6 ranking was never reached, and the answer carried "Saved spec is narrower than your question",
though nothing had been narrowed.

Now:
(1) "highest/lowest <attribute>" in the question is a property sort on that attribute, whatever the LLM
    returns, and the attribute is shown as a column.
(2) An LLM "unmet" that only says the question didn't ask for something doesn't raise the banner.

Offline: the recorded LLM reply, temp stores, and pyegeria's real FormatSet models.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pyegeria.authoring")

from advisor import spec_authoring_v2 as v2  # noqa: E402

QUESTION = "Which risks in the risk register have the highest importance?"
#: The LLM's decomposition as the 2026-11-17 run recorded it (turn 1: the dropped column, the unmet text).
RECORDED = {"title": "highest_importance_risks", "heading": "Risks with Highest Importance in Risk Register",
            "description": "This report lists risks that have been identified as having the highest importance.",
            "target_type": "Risk", "conditions": {},
            "columns": [{"name": "Risk Name", "key": "display_name"},
                        {"name": "Importance Level", "key": "importanceLevel"}],
            "unmet": "The request does not specify a particular importance level or status, so all risks are listed."}
#: The 12 risks on laz with their importance (31e359c5 quiet run), in the order Egeria returned them.
C03_ROWS = [("Inconsistent Data Definitions Undermining Cross-Department Reporting", "Medium-High"),
            ("Terrorism-Related Incident at the Annual Conference", "Medium-High"),
            ("Failure to Discharge Martyn's Law Responsible Person Duties", "Medium-High"),
            ("Onboarding of a Fraudulent or Compromised Supplier", "High"),
            ("Loss of Undocumented Governance Knowledge on Staff Departure", "High"),
            ("Accidental Disclosure of Patient Data through Misconfigured Access", "High"),
            ("Insider Misuse of Sensitive Patient Data", "High"),
            ("Manufacturing Batch Records Failing GMP Traceability Requirements", "High"),
            ("Counterfeit or Substandard Materials Entering Manufacturing", "Critical"),
            ("Ransomware Disruption to Manufacturing Operations", "Critical"),
            ("Theft of Personalised Treatment Intellectual Property", "Critical"),
            ("Loss of Clinical Trial Data Integrity", "Critical")]


@pytest.fixture
def stores(tmp_path, monkeypatch):
    import advisor.report_draft as rd
    import advisor.report_spec_docs as rsd

    def _drafts():
        p = tmp_path / "drafts"
        p.mkdir(parents=True, exist_ok=True)
        return p
    monkeypatch.setattr(rd, "_drafts_path", _drafts)
    monkeypatch.setattr(rsd, "_load_paths", lambda: {k: tmp_path / k for k in ("inbox", "outbox", "trash", "versions")})
    return tmp_path


@pytest.fixture
def fixes_on(monkeypatch):
    monkeypatch.setenv(v2.FLAG, "1")
    monkeypatch.setenv(v2.FIXES_FLAG, "1")
    monkeypatch.delenv(v2.STATUS_MERGE_FLAG, raising=False)


class FakeLLM:
    def __init__(self, *replies):
        self.replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.prompts = []

    def generate(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _start(monkeypatch, question=QUESTION, reply=None):
    from advisor.agents.report_spec_elicitor import ReportSpecElicitor
    from advisor.report_draft import get_report_draft_manager
    fake = FakeLLM(reply or RECORDED)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor_v2.get_planning_llm", lambda: fake)
    monkeypatch.setattr("advisor.agents.report_spec_elicitor.get_planning_llm", lambda: fake)
    el = ReportSpecElicitor()
    r = el.start(question)
    return el, r, get_report_draft_manager().load(r["draft_id"])


# ── (1) "highest importance" is a sort on importance, shown as a column ──────

def test_c03_recorded_reply_becomes_a_descending_sort_on_importance(fixes_on, stores, monkeypatch):
    _, r, spec = _start(monkeypatch)
    assert spec["conditions"]["output_sort_order"] == "PROPERTY_DESCENDING"
    assert spec["conditions"]["order_property_name"] == "importance"
    assert [c["key"] for c in spec["columns"]] == ["display_name", "importance"]
    assert "'highest importance' read as a sort by importance, highest first" in r["response"]
    sent = v2.build_format_set(spec).action.spec_params
    # pyegeria's executor maps these to the finder's sequencing_order / sequencing_property
    assert sent["output_sort_order"] == "PROPERTY_DESCENDING" and sent["order_property_name"] == "importance"


def test_c03_rows_then_rank_critical_first(fixes_on, stores, monkeypatch):
    _, _, spec = _start(monkeypatch)
    prev = {"result": {"kind": "table", "columns": [{"key": c["key"], "label": c["name"]} for c in spec["columns"]],
                       "rows": [{"display_name": n, "importance": v} for n, v in C03_ROWS]},
            "capped": False, "shown": 12, "notes": []}
    out = v2.order_rows(prev, spec["conditions"])
    assert [r["importance"] for r in out["result"]["rows"]] == ["Critical"] * 4 + ["High"] * 5 + ["Medium-High"] * 3
    assert out["reordered"] == "rank"


@pytest.mark.parametrize("question, order", [
    ("Which risks have the lowest importance?", "PROPERTY_ASCENDING"),
    ("What are the most important risks?", "PROPERTY_DESCENDING"),
    ("Which risks are least important?", "PROPERTY_ASCENDING"),
])
def test_other_wordings_sort_too(fixes_on, stores, monkeypatch, question, order):
    _, _, spec = _start(monkeypatch, question)
    assert spec["conditions"].get("output_sort_order") == order
    assert spec["conditions"].get("order_property_name") == "importance"


@pytest.mark.parametrize("question", [
    "Show risks with at least medium importance",          # read-back R1
    "List risks with an importance of at least high",
    "Which risks have at most low urgency?",
    "Which risks have the least medium importance?",       # a scale value after the sort word: a threshold
    "Show the lowest 3 importance risks",                  # a count, not an attribute
])
def test_a_threshold_is_not_a_sort(fixes_on, stores, monkeypatch, question):
    _, _, spec = _start(monkeypatch, question)
    assert "output_sort_order" not in spec["conditions"]


def test_an_llm_sort_attribute_that_fails_is_not_unmet_when_the_words_sort(fixes_on, stores, monkeypatch):
    """R3: the LLM's order_property_name didn't resolve, the question's words did: no stale unmet, no banner."""
    reply = {**RECORDED, "unmet": None,
             "conditions": {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "riskScore"}}
    el, r, spec = _start(monkeypatch, reply=reply)
    assert spec["conditions"] == {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "importance"}
    assert spec["unmet"] is None
    assert "sorting by 'riskScore': it isn't an attribute of Risk; sorted from the question's words instead" \
        in r["response"]
    assert "narrower" not in el.process(r["draft_id"], "generate now")["response"]


def test_an_llm_sort_attribute_that_fails_stays_unmet_without_a_sort_from_the_words(fixes_on, stores, monkeypatch):
    reply = {**RECORDED, "unmet": None,
             "conditions": {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "riskScore"}}
    _, _, spec = _start(monkeypatch, "Which risks are in the register?", reply)
    assert "output_sort_order" not in spec["conditions"]
    assert spec["unmet"] == "sorting by 'riskScore': it isn't an attribute of Risk"


def test_a_word_that_is_no_attribute_adds_no_sort(fixes_on, stores, monkeypatch):
    _, _, spec = _start(monkeypatch, "Which risks have the highest exposure?")
    assert "output_sort_order" not in spec["conditions"]


def test_a_sort_the_llm_set_is_kept(fixes_on, stores, monkeypatch):
    reply = {**RECORDED, "conditions": {"output_sort_order": "CREATION_DATE_RECENT"}}
    _, _, spec = _start(monkeypatch, reply=reply)
    assert spec["conditions"] == {"output_sort_order": "CREATION_DATE_RECENT"}


def test_an_llm_sort_attribute_is_grounded_to_the_types_own():
    cond, notes, unmet = v2.ground_conditions({"output_sort_order": "PROPERTY_DESCENDING",
                                               "order_property_name": "importanceLevel"}, "Risk")
    assert cond == {"output_sort_order": "PROPERTY_DESCENDING", "order_property_name": "importance"}
    assert notes == ["sort attribute 'importanceLevel' read as importance, the attribute of Risk"] and not unmet


def test_an_llm_sort_attribute_the_type_lacks_is_unmet_and_drops_the_sort():
    cond, _, unmet = v2.ground_conditions({"output_sort_order": "PROPERTY_DESCENDING",
                                           "order_property_name": "exposure"}, "Risk")
    assert cond == {} and unmet == ["sorting by 'exposure': it isn't an attribute of Risk"]


# ── (2) the banner only when something was narrowed ──────────────────────────

def test_c03_has_no_narrower_banner(fixes_on, stores, monkeypatch):
    el, r, spec = _start(monkeypatch)
    assert spec["unmet"] is None and "Not applied" not in r["response"]
    r = el.process(r["draft_id"], "generate now")
    assert "narrower" not in r["response"]
    assert "narrower" not in el.save_and_exit(r["draft_id"])["response"]


@pytest.mark.parametrize("text, narrows", [
    ("The request does not specify a particular importance level or status, so all risks are listed.", False),
    ("The question doesn't ask for a status.", False),
    ("User did not mention a time range", False),
    ("null", False), ("", False), (None, False), ("None.", False), ("n/a", False),
    ("no leader", True),
    ("risks with no owner: an absence can't be filtered", True),
    ("searched for APPROVED only; ACTIVE was not searched (one contentStatus value per search here)", True),
    # read-back R2: real unmets that the first pattern swallowed
    ("Filtering by owner isn't supported, so all owners' risks are listed.", True),
    ("There is no specific attribute for region, so the region filter was not applied.", True),
    ("The spec does not include the date range you asked for.", True),
    # read-back R4: a two-part unmet keeps its real half
    ("The request does not specify a status. Filtering by owner isn't supported.", True),
    ("Request does not specify a region; owners filter not supported", True),
])
def test_narrows(text, narrows):
    assert v2.narrows(text) is narrows


def test_a_real_unmet_still_raises_the_banner(fixes_on, stores, monkeypatch):
    reply = {**RECORDED, "target_type": "Team", "unmet": "no leader",
             "columns": [{"name": "Name", "key": "display_name"}]}
    el, r, spec = _start(monkeypatch, "Which teams have no leader?", reply)
    assert spec["unmet"] == "no leader"
    r = el.process(r["draft_id"], "generate now")
    assert "⚠️ **Saved spec is narrower than your question. Not applied:** no leader" in r["response"]


@pytest.mark.parametrize("text, kept", [
    ("The request does not specify a status. Filtering by owner isn't supported.", "Filtering by owner isn't supported"),
    ("Request does not specify a region; owners filter not supported", "owners filter not supported"),
    ("The question doesn't ask for a date, but the region filter isn't available", "the region filter isn't available"),
    ("The request does not specify a particular importance level or status, so all risks are listed.", None),
    ("no leader", "no leader"),
])
def test_kept_unmet_keeps_only_the_clauses_that_narrow(text, kept):
    assert v2.kept_unmet(text) == kept


def test_a_two_part_unmet_bans_only_its_real_half(fixes_on, stores, monkeypatch):
    """R4: the banner names the owner part only."""
    reply = {**RECORDED, "unmet": "The request does not specify a status. Filtering by owner isn't supported."}
    el, r, spec = _start(monkeypatch, reply=reply)
    assert spec["unmet"] == "Filtering by owner isn't supported"
    r = el.process(r["draft_id"], "generate now")
    line = "⚠️ **Saved spec is narrower than your question. Not applied:** Filtering by owner isn't supported"
    assert line in r["response"] and "does not specify" not in r["response"]
    saved = el.save_and_exit(r["draft_id"])["response"]
    assert line in saved and "does not specify" not in saved
