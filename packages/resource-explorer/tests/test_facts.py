"""Facts leave the layer already judged.

Every agent that answers a question needs the value AND whether the value means
anything. When each one goes to the tables itself, an empty table becomes "none
found" -- and "none found" and "we never looked" read identically to whoever
asked. An LLM narrating raw rows is the most efficient way yet devised to
produce that error at scale, which is why the state travels with the value.
"""
from __future__ import annotations

import pytest

from resource_explorer.facts import (
    PARTIAL,
    PROVENANCE_RECOVERED,
    UNANSWERABLE_KINDS,
    UNDECLARED_KINDS,
    Envelope,
    Fact,
    FactLayer,
    _has_content,
)
from resource_explorer.surveyors.result_status import (
    MEASURED,
    NEVER_RUN,
    NO_READER,
    NOT_ESTABLISHED,
    NOTHING_FOUND,
)


class TestIsKnown:
    def test_a_measured_zero_is_knowledge(self):
        """nothing_found means we looked and there was nothing. That is an
        answer, and treating it as ignorance would make every clean result
        indistinguishable from an unrun one."""
        assert Fact("x", NOTHING_FOUND).is_known is True

    def test_never_run_and_not_established_are_not(self):
        assert Fact("x", NEVER_RUN).is_known is False
        assert Fact("x", NOT_ESTABLISHED).is_known is False

    def test_no_reader_is_not_knowledge_either(self):
        """A missing results_reader is a gap in the answering machinery, not
        a measured zero -- live-reproduced 2026-09-25 (REVIEW-SURVEY-
        PANE-285.md) as "db_activity_signals ran and found nothing" for an
        analysis with no reader at all. `is_known` must stay False so the
        checkmark and "ran and found nothing" sentence are both withheld."""
        assert Fact("x", NO_READER).is_known is False

    def test_a_partial_run_still_counts(self):
        """A partial result is usable. Discarding it would throw away real work
        because it was incomplete."""
        assert Fact("x", PARTIAL).is_known is True


class TestEnvelope:
    def test_an_envelope_with_nothing_known_is_not_answerable(self):
        """The whole reason this type exists rather than returning values: a
        caller must not be able to turn silence into a negative answer."""
        env = Envelope(subject="s", facts=[Fact("a", NEVER_RUN), Fact("b", NOT_ESTABLISHED)])
        assert env.answerable is False

    def test_one_known_fact_is_enough(self):
        env = Envelope(subject="s", facts=[Fact("a", NEVER_RUN), Fact("b", NOTHING_FOUND)])
        assert env.answerable is True

    def test_can_run_is_deduped_across_facts_in_order(self):
        """Two analyses sharing a step must not offer to run it twice."""
        env = Envelope(subject="s", facts=[
            Fact("a", NEVER_RUN, can_run=["repo_language", "repo_health"]),
            Fact("b", NEVER_RUN, can_run=["repo_health", "repo_security"]),
        ])
        assert env.can_run == ["repo_language", "repo_health", "repo_security"]

    def test_counts_are_computed_once_in_the_envelope(self):
        """So two agents cannot disagree about whether the same envelope had
        an answer."""
        d = Envelope(subject="s", facts=[Fact("a", MEASURED), Fact("b", NEVER_RUN)]).as_dict()
        assert d["known_count"] == 1 and d["unknown_count"] == 1


class TestHasContent:
    """`_status`, `surveyed_at` and `detail` describe the RUN, not the finding."""

    def test_a_results_dict_of_only_envelope_keys_has_measured_nothing(self):
        assert _has_content({"_status": {"state": "measured"},
                             "surveyed_at": "2026-08-26T00:00:00",
                             "detail": {"source": "egeria"}}) is False

    def test_real_content_is_content(self):
        assert _has_content({"surveyed_at": "x", "components": [{"name": "a"}]}) is True

    def test_zero_and_empty_are_not_content(self):
        assert _has_content({"count": 0}) is False
        assert _has_content({"items": []}) is False
        assert _has_content({"name": "   "}) is False

    def test_a_false_flag_is_not_content(self):
        """`partial: False` says a run was complete; it is not a finding."""
        assert _has_content({"partial": False}) is False


class TestReadResultsDistinguishesNoReaderFromEmpty:
    """Live-reproduced 2026-09-25 (REVIEW-SURVEY-PANE-285.md): `_read_results`
    used to return `{}` both when a reader ran and found nothing AND when no
    reader was registered at all, so `_state_for` could not tell "measured a
    real zero" from "there is no way to measure this yet" -- rendered on
    screen as "db_activity_signals ran and found nothing -- a measured zero,
    not a gap in coverage" for an analysis with no results_reader."""

    def _layer(self):
        layer = object.__new__(FactLayer)
        layer._registry = None
        return layer

    def test_no_reader_at_all_returns_none(self):
        assert self._layer()._read_results("slug", "aid", entry=None) is None

    def test_a_bare_callable_entry_with_no_results_is_not_none(self):
        """A real reader that ran and genuinely found nothing must stay `{}`,
        not collapse into the same signal as "no reader exists"."""
        reader = lambda registry, slug: {}
        assert self._layer()._read_results("slug", "aid", entry=reader) == {}

    def test_a_tuple_entry_whose_first_element_is_not_callable_is_none(self):
        assert self._layer()._read_results("slug", "aid", entry=(None, None)) is None

    def test_an_object_entry_with_no_results_reader_attribute_is_none(self):
        class NoReader:
            pass

        assert self._layer()._read_results("slug", "aid", entry=NoReader()) is None


class TestQuestionRouting:
    """The catalog already states how each question is answerable, so routing
    is a lookup rather than an inference -- and for most of them the correct
    behaviour is to produce no answer about the resource at all."""

    def _q(self, kind, ids=None, note=""):
        return {"question": f"a {kind} question",
                "answering": {"kind": kind, "analysis_ids": ids or [], "note": note}}

    def test_a_gap_question_answers_about_the_gap_not_the_resource(self):
        env = FactLayer().answer("any-slug", self._q("gap", note="GAP: CVE scan"))
        assert env.answerable is False
        assert env.facts == []
        assert "No mechanism exists" in env.blocked_reason
        assert "CVE scan" in env.blocked_reason

    def test_a_human_question_is_not_answered_from_surveys(self):
        env = FactLayer().answer("any-slug", self._q("human"))
        assert env.answerable is False
        assert "human-supplied" in env.blocked_reason

    def test_an_undeclared_kind_is_not_reported_as_a_missing_mechanism(self):
        """`direct` questions ARE answerable -- from a field on the resource.
        Calling that "nothing can answer this" would be false, and would hide
        the largest cheap fix in the catalog."""
        env = FactLayer().answer("any-slug", self._q("direct"))
        assert env.answerable is False
        assert "No mechanism exists" not in env.blocked_reason
        assert "catalog records" in env.blocked_reason

    def test_the_two_tables_do_not_overlap(self):
        """A kind in both would resolve by dict order, silently."""
        assert not (set(UNANSWERABLE_KINDS) & set(UNDECLARED_KINDS))

    def test_a_question_declaring_no_analysis_says_so(self):
        env = FactLayer().answer("any-slug", self._q("analysis"))
        assert env.answerable is False
        assert "declares no analysis" in env.blocked_reason


class TestAgainstRealCatalog:
    """Live-ish: the real 41 questions against a real registry."""

    def test_every_catalogued_question_produces_an_envelope(self):
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        fl = FactLayer()
        for q in get_questions():
            env = fl.answer("no-such-repo-at-all", q)
            # Never raises, and never claims an answer about a repo that does
            # not exist.
            assert env.answerable is False
            assert env.blocked_reason, f"no reason given for: {q['question'][:50]}"

    def test_an_unknown_analysis_id_is_not_established_rather_than_empty(self):
        """An id with no results reader cannot be read from, whatever it did --
        egeria_publish is the live example: an action, not an analysis."""
        f = FactLayer().fact("no-such-repo-at-all", "egeria_publish")
        assert f.state == NOT_ESTABLISHED
        assert f.is_known is False
        assert "action rather than findings" in f.note


class TestProvenance:
    def test_recovered_components_are_marked_as_proposals(self):
        """A component recovered from a repo is a proposal, not a validated
        part of an architecture. Reporting a recovered partition as an
        established one is what a blueprint must not be built on."""
        assert FactLayer._provenance_for({"components": []}) == PROVENANCE_RECOVERED

    def test_egeria_sourced_facts_say_so(self):
        from resource_explorer.facts import PROVENANCE_EGERIA

        assert FactLayer._provenance_for({"detail": {"source": "egeria"}}) == PROVENANCE_EGERIA

    def test_a_recovered_diagram_is_marked_as_a_proposal_too(self):
        """architecture_diagram draws its picture from the same detector
        proposal architecture_recovery's `components` describes, but its own
        value shape carries `mermaid`, not `components` -- so the check above
        silently missed it. Caught live 2026-09-08 against
        egeria-workspaces_git: the rendered diagram carried no "(proposed by
        a detector, not yet validated)" caveat (index.html,
        `f.provenance === 'recovered'`) because this function never saw it
        as recovered at all -- it reported PROVENANCE_MEASURED, the same tag
        an established fact gets."""
        assert FactLayer._provenance_for({"mermaid": "graph TD\n a --> b"}) == PROVENANCE_RECOVERED


class TestQuestionsTabWiring:
    """Clicking a catalogued question must not become an LLM prompt.

    The catalog already declares how each of these is answered. Retyping the
    question at a model and letting it re-interpret discards that -- and a
    model reading raw rows reports "no security policy" when the truth is that
    the step never ran, sounding identical either way.
    """

    _INDEX = None

    @classmethod
    def _html(cls) -> str:
        if cls._INDEX is None:
            from pathlib import Path

            cls._INDEX = (Path(__file__).resolve().parents[1] / "resource_explorer"
                          / "web" / "static" / "index.html").read_text()
        return cls._INDEX

    def test_a_question_click_resolves_rather_than_prompting(self):
        html = self._html()
        fn = html.split("async function _answerQuestionInChat(")[1].split("\n}")[0]
        assert "/answer?question=" in fn, "question click no longer resolves through the fact layer"
        assert "sendQuery(" not in fn, "question click routes through the RAG"

    def test_an_unanswerable_envelope_never_renders_as_a_negative_answer(self):
        """"No CVEs" and "nothing can look for CVEs" are opposite claims."""
        html = self._html()
        fn = html.split("function _renderEnvelopeMarkdown(")[1].split("\nfunction ")[0]
        assert "env.answerable" in fn
        assert "I can't answer that yet" in fn
        assert "blocked_reason" in fn

    def test_every_fact_state_has_fixed_wording(self):
        """These five distinctions are exactly what gets lost when a value is
        narrated without its state, so the wording is not left to a model."""
        html = self._html()
        table = html.split("const _FACT_STATE_TEXT = {")[1].split("};")[0]
        for state in ("measured", "nothing_found", "never_run", "not_established",
                      "partial", "no_reader"):
            assert f"{state}:" in table, f"no fixed wording for {state}"
        assert "measured zero" in table, "nothing_found must not read as absence of coverage"
        assert "no summary reader" in table, (
            "no_reader must not borrow nothing_found's 'measured zero' wording -- "
            "a missing reader has not measured anything"
        )

    def test_a_recovered_fact_is_labelled_as_a_proposal(self):
        html = self._html()
        fn = html.split("function _renderEnvelopeMarkdown(")[1].split("\nfunction ")[0]
        assert "not yet validated" in fn

    def test_the_answer_stays_deterministic_but_reads_better(self):
        """Reported 2026-08-31: a raw analysis id and an unlabelled key:value
        dump ("catalog_presence — measured\\n\\nregistered: true · group:
        egeria · ...") read as meaningless to someone who did not already
        know what that analysis measures. Fixed WITHOUT routing this path
        through an LLM -- that would violate
        test_a_question_click_resolves_rather_than_prompting's whole point --
        by using the catalog's own display name.

        **The <details> half of that fix was removed on 2026-09-01**, and
        this test's assertion on it with it. Not because collapsing evidence
        was a bad idea, but because measuring showed it almost never
        happened, and where it did it was hiding the answer:

        The old renderer computed `composedAnswer = proseAnswer ? '' :
        summary` and then showed the toggle only `if (summary !==
        composedAnswer)`. For a fact with no prose the two were equal, so
        the toggle was skipped and the key:value line rendered inline
        anyway. Measured across all 53 questions on egeria_python_git: 20 of
        23 facts were scalar-only, so the inline dump this test guards
        against was already the majority behaviour, and the <details> block
        was reachable for exactly 3 facts -- `foss_scorecard`,
        `project_commits`, `community_support`. Those 3 are precisely the
        ones carrying a verdict, so the one thing the toggle reliably hid
        was the answer ("Is this repository actively maintained?" replied
        "389 commit(s) in the last 90 days", with `verdict: pass` behind the
        toggle).

        So the mechanism was collapsing answers, not evidence. Both halves
        of a fact's value now render on the visible line; see
        tests/test_fact_answer_rendering.py, which executes the renderer's
        own expression rather than grepping for it.

        What this test still pins is the half that was right and is kept:
        the display name.
        """
        html = self._html()
        fn = html.split("function _renderEnvelopeMarkdown(")[1].split("\nfunction ")[0]
        assert "_analysisDisplayName(f.analysis_id)" in fn, (
            "the raw analysis_id should read as a display name, not a slug"
        )
        assert "<details>" not in fn, (
            "the Evidence toggle is deliberately gone -- it was reachable "
            "only for the three verdict-carrying facts, whose verdicts it "
            "hid. If a genuine detail tier is reintroduced, it must not be "
            "the only place a fact's scalars appear."
        )


class TestToolsDoNotAssertAbsence:
    """An empty table is not a finding.

    "No dependencies found for X" is only true if the analysis ran; otherwise
    nobody looked, and the two opposite answers reached the LLM as the same
    sentence — which it then stated as a finding. This is the free-text half of
    what the fact layer fixes; the Questions tab is the other.
    """

    def test_a_measured_zero_is_reported_as_measured(self, monkeypatch):
        from resource_explorer.agents import tools
        from resource_explorer.facts import Fact
        from resource_explorer.surveyors.result_status import NOTHING_FOUND

        monkeypatch.setattr(tools, "_absence", tools._absence)  # keep the real one
        monkeypatch.setattr(
            "resource_explorer.facts.FactLayer.fact",
            lambda self, slug, aid: Fact(aid, NOTHING_FOUND),
        )
        out = tools._absence("s", "dependency_analysis", "dependencies")
        assert "measured result" in out
        assert "never run" not in out

    def test_never_run_says_it_is_not_a_finding(self, monkeypatch):
        """The sentence that has to exist: an unrun analysis must not be
        readable as evidence of absence."""
        from resource_explorer.agents import tools
        from resource_explorer.facts import Fact
        from resource_explorer.surveyors.result_status import NEVER_RUN

        monkeypatch.setattr(
            "resource_explorer.facts.FactLayer.fact",
            lambda self, slug, aid: Fact(aid, NEVER_RUN, can_run=["repo_dependency"]),
        )
        out = tools._absence("s", "dependency_analysis", "dependencies")
        assert "NOT a finding that there are none" in out
        assert "repo_dependency" in out, "must name the step that would settle it"

    def test_a_broken_fact_layer_still_answers_without_asserting(self, monkeypatch):
        """A tool must always return something, but never silently fall back to
        claiming absence."""
        from resource_explorer.agents import tools

        def _boom(self, slug, aid):
            raise RuntimeError("registry down")

        monkeypatch.setattr("resource_explorer.facts.FactLayer.fact", _boom)
        out = tools._absence("s", "dependency_analysis", "dependencies")
        assert "could not be determined" in out

    def test_no_tool_recommends_a_binary_that_does_not_exist(self):
        """The old absence text told users to run `project-explorer refresh` —
        a command gone since the package was renamed to resource_explorer."""
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "resource_explorer"
               / "agents" / "tools.py").read_text()
        # Only what a tool RETURNS. The docstring on _absence names the stale
        # command deliberately, to say why it was removed -- a test that
        # forbade the explanation would invite its deletion.
        offenders = []
        for block in src.split("return ")[1:]:
            head = block.split("\n\n")[0]
            if "project-explorer " in head:
                offenders.append(head.strip()[:70])
        assert offenders == [], f"stale CLI name in tool output: {offenders}"


class TestResourceStateSources:
    """Questions answered from the resource's own recorded state.

    Declared in code rather than as a CSV column because a field NAME is not
    enough for most of them: "catalogued in Egeria AND when" is two fields read
    together, "worth investigating" is a disposition looked up by github_url in
    a different table, and "any known feedback" is a row count.
    """

    def test_every_declared_question_is_in_the_real_catalog(self):
        """A key that matches nothing is a resolver that never runs, and it
        would look exactly like a question we chose not to answer."""
        from resource_explorer.facts import RESOURCE_STATE_SOURCES
        from resource_explorer.surveyors.question_catalog_reader import get_questions

        catalog = {q["question"] for q in get_questions()}
        missing = [q for q in RESOURCE_STATE_SOURCES if q not in catalog]
        assert missing == [], (
            f"declared for questions not in the catalog: {missing}\n"
            "If a question was REWORDED in docs/dr-egeria/resource_questions.csv, "
            "update its key in facts.py's RESOURCE_STATE_SOURCES in the SAME "
            "commit -- these keys match on exact text. Four such rewordings are "
            "expected from the multi-resource question-authoring stream; the "
            "old -> new mapping is in RESOURCE_STATE_SOURCES' own docstring and "
            "in docs/design-notes/QUESTION-CATALOG-MULTI-TYPE-IMPLEMENTED.md."
        )

    def test_an_absent_value_is_a_measured_zero_not_an_unrun_analysis(self):
        """`feedback_count: 0` means nobody left feedback -- a real answer.
        Reporting it as unknown would make a clean resource look unexamined."""
        from resource_explorer.facts import _r_feedback

        class _Reg:
            def list_resource_feedback(self, *_a):
                return []

        class _P:
            slug = "s"

        value, state = _r_feedback(_Reg(), _P())
        assert state == NOTHING_FOUND and value == {"feedback_count": 0}

    def test_undecided_is_the_absence_of_a_judgement_not_a_judgement(self):
        """Every resource starts `undecided`. Reporting that as an answer would
        tell the user someone had considered it when nobody had."""
        from resource_explorer.facts import _r_disposition

        class _Reg:
            def __init__(self, verdict):
                self._v = verdict

            def get_disposition(self, _url):
                return {"disposition": self._v}

        class _P:
            github_url = "u"

        assert _r_disposition(_Reg("undecided"), _P())[1] == NOTHING_FOUND
        assert _r_disposition(_Reg("investigating"), _P())[1] == MEASURED

    def test_a_resource_state_fact_offers_no_survey_to_run(self):
        """No step establishes these. Offering one would be a false promise --
        the same shape of lie the envelope exists to stop."""
        fl = FactLayer()
        f = fl._resource_state_fact("no-such-repo-at-all", lambda r, p: ({}, MEASURED), "x")
        assert f.can_run == []

    def test_an_unregistered_resource_is_not_established_not_empty(self):
        fl = FactLayer()
        f = fl._resource_state_fact("no-such-repo-at-all", lambda r, p: ({}, MEASURED), "x")
        assert f.state == NOT_ESTABLISHED
        assert "is registered" in f.note

    def test_a_declared_source_answers_whatever_the_kind_says(self):
        """`direct` describes WHERE the answer lives. Once that is declared the
        question is answerable, and the kind label must not veto it."""
        from resource_explorer.facts import RESOURCE_STATE_SOURCES

        q_text = next(iter(RESOURCE_STATE_SOURCES))
        env = FactLayer().answer("no-such-repo-at-all", {
            "question": q_text, "answering": {"kind": "direct", "analysis_ids": []},
        })
        # Unregistered, so still not answerable -- but it got as far as the
        # resolver rather than being turned away by the kind.
        assert env.facts, "a declared source was skipped because of its kind"


class TestCrossResourceResolvers:
    """Questions answered by looking across the catalog, not at one analysis."""

    def test_an_unidentified_licence_is_not_reported_as_a_licence(self):
        """GitHub returns "Other"/"NOASSERTION" when it could not identify the
        licence. For a question about RESTRICTIONS ON USE, reporting that as a
        licence is the more dangerous of the two errors."""
        from resource_explorer.facts import _r_license

        class _Reg:
            def __init__(self, name, spdx):
                self._s = {"license": name, "license_spdx_id": spdx}

            def get_latest_project_stats(self, _slug):
                return self._s

        class _P:
            slug = "s"

        assert _r_license(_Reg("Other", "NOASSERTION"), _P())[1] == NOTHING_FOUND
        assert _r_license(_Reg("", ""), _P())[1] == NOTHING_FOUND
        value, state = _r_license(_Reg("Apache License 2.0", "Apache-2.0"), _P())
        assert state == MEASURED and value["identified"] is True

    def test_related_resources_are_evidence_not_a_verdict(self):
        """Whether something REPLACES what you already have is a decision about
        intent. No query settles it, and a shared primary language is nowhere
        near enough -- so the fact is flagged so the client cannot phrase it as
        the answer."""
        from resource_explorer.facts import EVIDENCE_ONLY

        assert "related_resources" in EVIDENCE_ONLY
        fl = FactLayer()
        f = fl._resource_state_fact("no-such-repo", lambda r, p: ({}, MEASURED),
                                    "related_resources")
        # Unregistered here, but the flag is set from the subject, so check the
        # wiring directly on a registered-looking one.
        assert Fact("related_resources", MEASURED, evidence_only=True).as_dict()["evidence_only"]

    def test_being_registered_at_all_is_existing_use(self):
        """A repo with no group still IS known to us. The zero is about
        siblings, not about whether anyone has seen it."""
        from resource_explorer.facts import _r_existing_use

        class _Reg:
            def list_projects_in_group(self, _g):
                return []

        class _P:
            slug = "s"
            group_slug = ""
            last_surveyed_at = ""

        value, state = _r_existing_use(_Reg(), _P())
        assert state == MEASURED
        assert value["registered"] is True and value["siblings_in_group"] == 0

    def test_no_comparable_history_is_not_established_not_unchanged(self):
        """"Nothing changed" and "there was nothing to compare" are opposite
        answers to "is it worth re-running"."""
        from resource_explorer.facts import _r_changed_since_survey

        class _Reg:
            pass

        class _P:
            slug = "s"
            last_surveyed_at = ""

        import resource_explorer.notification_detector as nd

        original = nd.detect_change
        try:
            nd.detect_change = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no history"))
            value, state = _r_changed_since_survey(_Reg(), _P())
        finally:
            nd.detect_change = original
        assert state == NOT_ESTABLISHED
        assert value["changed_count"] == 0 and value["unchanged_count"] == 0


# ── questions that were falling through to raw statistics ───────────────────
def test_maintained_answers_with_a_verdict_not_statistics(pg_registry):
    """The question was not in the fact layer at all, so it fell through to
    generic retrieval, which returned commit counts and left the verdict to the
    reader. foss_scorecard already computes the verdict."""
    from resource_explorer.facts import RESOURCE_STATE_SOURCES
    from resource_explorer.registry import Project
    from resource_explorer.surveyors.result_status import MEASURED, NEVER_RUN

    reg = pg_registry
    reg.add(Project(slug="m", display_name="m", github_url="https://github.com/x/m"))
    fn, _ = RESOURCE_STATE_SOURCES["Is this repository actively maintained?"]

    # Nothing run yet: never_run, not a fabricated "no".
    assert fn(reg, reg.get("m"))[1] == NEVER_RUN

    reg.upsert_finding("m", "foss_scorecard", [
        {"check_name": "maintained", "label": "pass",
         "summary": "332 commit(s) in the last 90 days.", "confidence": 100,
         "detail": {}}], surveyed_at="2026-08-27T00:00:00")
    value, state = fn(reg, reg.get("m"))
    assert state == MEASURED
    assert value["maintained"] is True and value["verdict"] == "pass"
    assert "332" in value["detail"]


def test_maintainers_merge_one_person_committing_under_two_addresses(pg_registry):
    """One human with a GitHub noreply address and a personal one was counted
    as two maintainers, which understates exactly the concentration this
    question exists to surface. Measured on egeria_git: 241 + 132 of 375
    commits reported as two people at 64% and 35%, when it is one at 99.5%."""
    from resource_explorer.facts import RESOURCE_STATE_SOURCES
    from resource_explorer.registry import Project

    reg = pg_registry
    reg.add(Project(slug="w", display_name="w", github_url="https://github.com/x/w"))
    with reg._conn() as conn:
        for email, n in (("a@users.noreply.github.com", 241), ("a@gmail.com", 132)):
            for i in range(n):
                conn.execute(
                    "INSERT INTO project_commits (project_slug, sha, message, "
                    "author_name, author_email, committed_at) VALUES (?,?,?,?,?,?)",
                    ("w", f"{email}-{i}", "x", "Mandy Chessell", email,
                     "2026-08-01T00:00:00"))
        for i in range(2):
            conn.execute(
                "INSERT INTO project_commits (project_slug, sha, message, "
                "author_name, author_email, committed_at) VALUES (?,?,?,?,?,?)",
                ("w", f"k{i}", "x", "Karth", "k@example.com", "2026-08-01T00:00:00"))

    value, _ = RESOURCE_STATE_SOURCES[
        "Who owns this resource (accountable owner), and who administers it?"
    ][0](reg, reg.get("w"))
    assert value["people"] == 2, "two humans, not three commit identities"
    assert value["commit_identities"] == 3, "the split is reported, not hidden"
    top = value["top_authors"][0]
    assert top["name"] == "Mandy Chessell"
    assert top["emails"] == 2, "the merge must be inspectable"
    assert top["share"] > 0.99, f"expected ~99.5%, got {top['share']:.1%}"


class TestResourceStateHeadlines:
    """Owner's gate follow-up (2026-09-27): `_resource_state_fact` never set
    a `headline` at all, so every one of these six questions fell to
    `readEnvelope`'s rung-3 scalar fallback ("no written summary — the
    figures above are the raw measures") — the exact defect this whole area
    exists to close for analysis-backed facts, just never extended to these
    resource-state-sourced ones."""

    def test_every_named_subject_is_registered(self):
        from resource_explorer.facts import _RESOURCE_STATE_HEADLINES
        for subject in (
            "catalog_presence", "related_resources", "survey_history",
            "survey_definitions", "disposition", "change_since_last_survey",
        ):
            assert subject in _RESOURCE_STATE_HEADLINES

    def test_catalog_presence_names_the_group_and_sibling_count(self):
        from resource_explorer.facts import _RESOURCE_STATE_HEADLINES
        from resource_explorer.surveyors.result_status import MEASURED
        fn = _RESOURCE_STATE_HEADLINES["catalog_presence"]
        headline = fn({"registered": True, "group": "data-platform",
                        "siblings_in_group": 3}, MEASURED)
        assert "data-platform" in headline
        assert "3" in headline

    def test_survey_definitions_handles_both_resolver_shapes(self):
        from resource_explorer.facts import _RESOURCE_STATE_HEADLINES
        from resource_explorer.surveyors.result_status import MEASURED, NOTHING_FOUND
        fn = _RESOURCE_STATE_HEADLINES["survey_definitions"]
        # _r_which_survey's shape (a "candidates" list).
        assert "coco_survey" in fn(
            {"count": 1, "candidates": ["coco_survey"], "note": ""}, MEASURED)
        # _r_survey_definition_exists's shape (authored/count, no candidates).
        assert "catalog gap" in fn(
            {"authored": False, "count": 0, "technology_type": "Git Repository"},
            NOTHING_FOUND)

    def test_change_since_last_survey_names_what_changed(self):
        from resource_explorer.facts import _RESOURCE_STATE_HEADLINES
        from resource_explorer.surveyors.result_status import MEASURED
        fn = _RESOURCE_STATE_HEADLINES["change_since_last_survey"]
        headline = fn({"changed_count": 2, "unchanged_count": 5,
                        "changed": [{"analysis_id": "cve_scan", "summary": "x"},
                                    {"analysis_id": "foss_scorecard", "summary": "y"}]},
                       MEASURED)
        assert "2 of 7" in headline
        assert "cve_scan" in headline and "foss_scorecard" in headline

    def test_a_resource_state_fact_carries_the_headline(self, pg_registry):
        from resource_explorer.facts import FactLayer, RESOURCE_STATE_SOURCES
        from resource_explorer.registry import Project

        reg = pg_registry
        reg.add(Project(slug="hl", display_name="hl", github_url="https://github.com/x/hl",
                         group_slug="platform"))
        fl = FactLayer(registry=reg, resource_type="repo")
        resolver, subject = RESOURCE_STATE_SOURCES["Is there any existing use within our organization?"]
        fact = fl._resource_state_fact("hl", resolver, subject)
        assert fact.headline
        assert "platform" in fact.headline
