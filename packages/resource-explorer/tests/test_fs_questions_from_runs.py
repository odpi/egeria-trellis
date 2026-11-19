"""C1 (2026-11-19, Files proof walk D2): a file system's questions answer from the runs that answer them.

The walk found Q-0101 (file profiles), Q-0105 (narratives), Q-0080 / Q-0103 (class match), Q-0054 (reach) and Q-0109
(price) saying "no reader yet" or "not run" minutes after the steps ran. Here each step's stored run (or probe row) is
written the way the step writes it, and the question is asked through the fact layer. Fakes only: a temp SQLite registry,
a fake vector store, a fake data-class catalogue; nothing contacts Egeria and nothing touches a shared database.

Pinned: each question answers with its step's own words; a step that never ran says "Not yet run" (the absence test, per
question); Q-0103 stays partial; Q-0054 answers from a stored probe and says "Not yet run" with none; Q-0109 names the
priced steps and the unpriced ones; a completed Egeria Folder Survey credits filesystem_inventory and a submitted,
failed or report-less one does not; a narrative run on an older walk reads as stale.
"""
from __future__ import annotations

import pytest

from resource_explorer.facts import FactLayer
from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors import question_catalog_reader
from resource_explorer.surveyors.database.column_matching import KnownDataClass
from resource_explorer.surveyors.database.egeria_reference_catalog import ReferenceCatalog
from resource_explorer.surveyors.filesystem import file_class_match as fcm
from resource_explorer.surveyors.filesystem import narrative_docs as nd

SLUG = "tree"
PROFILE_RUN = "2026-11-19T01:37:15"
WALK_RUN = "2026-11-19T01:32:57"
EMAIL = KnownDataClass(guid="dc-email", qualified_name="DataClass::Email", display_name="Email Address",
                       specification=r"[^@\s]+@[^@\s]+\.[a-z]{2,}", match_property_names=("email",), data_type="string")

Q_PROFILES, Q_NARRATIVES, Q_CLASS, Q_SENSITIVE, Q_REACH, Q_PRICE, Q_INVENTORY = (
    "Q-0101", "Q-0105", "Q-0080", "Q-0103", "Q-0054", "Q-0109", "Q-0100")


class FakeStore:
    def __init__(self):
        self.collections: dict = {}

    def collection_name(self, slug, ctype):
        return f"{slug}_{ctype}"

    def collection_exists(self, c):
        return c in self.collections

    def drop_collection(self, c):
        self.collections.pop(c, None)

    def insert(self, c, texts, metas):
        self.collections.setdefault(c, []).extend(zip(texts, metas))
        return len(texts)


@pytest.fixture
def registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "c1.db"))
    root = tmp_path / "tree"
    (root / "hr").mkdir(parents=True)
    (root / "README.md").write_text("# Tree\n\nWhat this is.\n\n## Variables\n\nage: years\n")
    (root / "hr" / "payroll.csv").write_text("email\n" + "\n".join(f"p{i}@example.com" for i in range(24)) + "\n")
    r.register_filesystem(FileSystemEntity(slug=SLUG, display_name=SLUG, local_mount_point=str(root)))
    return r


def _fs(registry):
    return registry.get_filesystem(SLUG)


def _question(qid):
    return next(q for q in question_catalog_reader.get_questions("filesystem") if q.get("id") == qid)


def _env(registry, qid):
    return FactLayer(registry, "filesystem").answer(SLUG, _question(qid))


def _says(env) -> str:
    """Every sentence the answer carries: the facts' headlines and the one blocked reason."""
    return " | ".join([env.blocked_reason or ""] + [f.headline or "" for f in env.facts])


def _col(path, name, i, state="measured", source="resource-explorer"):
    return {"file_path": path, "column_name": name, "ordinal": i, "declared_type": "", "inferred_general_type": "object",
            "record_ct": 24, "value_ct": 24, "null_ct": 0, "distinct_ct": 24, "sample_ratio": 1.0,
            "stats_source": source, "state": state}


def _profile(registry, run=PROFILE_RUN):
    registry.add_filesystem_file_columns(SLUG, run, [_col("hr/payroll.csv", "email", 0)])
    registry.record_step_run(SLUG, "file_profiles", run, entity_type="filesystem", source="file-profiles", executor="local",
                             metrics={"walk_run_id": WALK_RUN, "egeria_half": "", "errors": {}, "notes": {}})


def _walk(registry, run=WALK_RUN, entries=(("README.md", 40),)):
    narrative = [{"file": f, "size": n, "modified_at": run} for f, n in entries]
    registry.add_filesystem_walk_files(SLUG, run, [
        {"file_path": f, "folder_path": "", "folder_depth": 0, "size_bytes": n, "hash_state": "over_limit"}
        for f, n in entries])
    registry.add_filesystem_survey(SLUG, run, {"surveyed_at": run, "walk_extensions": {"run_id": run},
                                               "folder_narratives": narrative})


def _narratives(registry):
    nd.run_narrative_docs(_fs(registry), registry, store_factory=FakeStore)


def _match(registry):
    fcm.run_file_class_match(_fs(registry), registry, catalog_loader=lambda: ReferenceCatalog(data_classes=[EMAIL]))


def _probe(registry, outcome="egeria_verified", at="2026-11-19T01:00:00"):
    registry.record_reachability_check(SLUG, probed_at=at, outcome=outcome, probed_from="test")


def _native(registry, guid, *, status="COMPLETED", report=True, at="2026-11-19T01:20:49", slug=SLUG):
    qn = "FileSurvey::survey-folder"
    registry.record_native_survey_submission("filesystem", slug, qn, at, engine_action_guid=guid, submitted_by="p")
    if status:
        registry.record_native_survey_readback(guid, read_at=at, status=status)
    if report:
        registry.record_native_survey_report(
            guid, entity_type="filesystem", slug=slug, process_qualified_name=qn, report_guid=f"r-{guid}",
            report_at=at, read_at=at, annotations=[{"guid": "a1", "annotation_type": "ResourceMeasureAnnotation"}])


def _egeria_leads(registry):
    _probe(registry)    # a verified probe on the file system's own canonical path makes Egeria the leader


# ── the absence test: with no run, every question says so, and none claims an answer ──

@pytest.mark.parametrize("qid", [Q_PROFILES, Q_NARRATIVES, Q_CLASS, Q_SENSITIVE, Q_REACH])
def test_with_no_run_each_question_says_not_yet_run(registry, qid):
    env = _env(registry, qid)
    assert not env.answerable, qid
    assert env.blocked_reason == "Not yet run."


def test_with_no_run_the_price_question_is_not_yet_run(registry):
    env = _env(registry, Q_PRICE)
    assert not env.answerable and env.blocked_reason == "Not yet run."


def test_a_run_row_with_nothing_readable_behind_it_is_not_established_never_found_nothing(registry):
    registry.record_step_run(SLUG, "file_profiles", PROFILE_RUN, entity_type="filesystem", metrics={})
    fact = FactLayer(registry, "filesystem").fact(SLUG, "file_profiles")
    assert fact.state == "not_established"


# ── each question answers from its step's run, in the step's own words ──

def test_q0101_answers_with_the_profile_runs_own_words(registry):
    _profile(registry)
    env = _env(registry, Q_PROFILES)
    assert env.answerable
    assert "1 files profiled (0 by Egeria, 1 by RE)" in _says(env)
    fact = env.facts[0]
    assert fact.last_run_at.startswith(PROFILE_RUN[:10]) and fact.analysis_id == "file_profiles"


def test_q0105_answers_with_the_narrative_runs_own_words_and_its_proposal_line(registry):
    _walk(registry)
    _narratives(registry)
    env = _env(registry, Q_NARRATIVES)
    says = _says(env)
    assert env.answerable
    assert "1 file read in full" in says and "section" in says and "passage" in says
    assert "each passage is stated by its file · a proposal, never a fact, until a person confirms it" in says
    assert nd.STALE_WORDS not in says


def test_q0080_answers_with_the_class_match_words_and_carries_the_weak_evidence_through(registry):
    _profile(registry)
    _match(registry)
    env = _env(registry, Q_CLASS)
    says = _says(env)
    assert env.answerable
    assert "column of 1 profiled file examined" in says or "columns of 1 profiled file examined" in says
    assert "against 1 data class Egeria holds" in says       # the catalogue it was matched against stays on the line
    assert "not sampled" in says or "match a data class" in says


def test_q0103_stays_partial_and_names_the_class_match_run(registry):
    q = _question(Q_SENSITIVE)
    assert q["answering"]["kind"] == "partial" and q["answering"]["analysis_ids"] == ["file_data_class_match"]
    assert "a class match is not a sensitivity review" in q["answering"]["note"]
    _profile(registry)
    _match(registry)
    env = _env(registry, Q_SENSITIVE)
    assert env.answerable and env.kind == "partial" and "examined" in _says(env)


def test_q0054_answers_from_a_stored_probe_and_never_probes(registry, monkeypatch):
    from resource_explorer import file_reach

    def refuse(*a, **k):
        raise AssertionError("answering a question must never probe")

    monkeypatch.setattr(file_reach, "probe_filesystem", refuse)
    _probe(registry)
    env = _env(registry, Q_REACH)
    assert env.answerable
    assert "verified 2026-11-19" in _says(env)


def test_q0109_names_the_steps_that_are_priced_and_the_ones_that_are_not(registry):
    _walk(registry)
    _profile(registry)
    env = _env(registry, Q_PRICE)
    says = _says(env)
    assert env.answerable
    assert "data-file profiles:" in says and "narratives:" in says and "not priced:" in says
    value = env.facts[0].value
    assert "narratives" in value["priced_steps"] and "data-file profiles" in value["priced_steps"]


# ── freshness: a step that ran on an older walk says so ──

def test_a_narrative_run_on_an_older_walk_reads_as_stale(registry):
    _walk(registry)
    _narratives(registry)
    _walk(registry, run="2099-01-01T00:00:00")
    env = _env(registry, Q_NARRATIVES)
    assert nd.STALE_WORDS in _says(env)
    assert env.facts[0].value["stale"] is True


def test_a_class_match_on_older_profiles_reads_as_stale(registry):
    _profile(registry)
    _match(registry)
    _profile(registry, run="2099-01-01T00:00:00")
    assert fcm.STALE_WORDS in _says(_env(registry, Q_CLASS))


# ── an Egeria Folder Survey credits filesystem_inventory only when it completed with its report read back ──

def _inventory_run_at(registry):
    return registry.get_analysis_last_run("filesystem", SLUG).get("filesystem_inventory", {}).get("last_run_at")


def test_a_completed_native_survey_with_its_report_read_back_credits_filesystem_inventory(registry):
    _egeria_leads(registry)
    _native(registry, "g-done")
    assert _inventory_run_at(registry) == "2026-11-19T01:20:49"


@pytest.mark.parametrize("label,kwargs", [
    ("submitted, no status read", dict(status="", report=False)),
    ("running", dict(status="IN_PROGRESS", report=False)),
    ("failed", dict(status="FAILED", report=False)),
    ("completed, report not read back", dict(status="COMPLETED", report=False)),
])
def test_a_submitted_running_failed_or_report_less_native_survey_credits_nothing(registry, label, kwargs):
    _egeria_leads(registry)
    _native(registry, "g-x", **kwargs)
    assert _inventory_run_at(registry) is None, label


def test_a_completed_native_survey_credits_nothing_where_resource_explorer_leads(registry):
    _probe(registry, outcome="re_only")
    _native(registry, "g-re")
    assert _inventory_run_at(registry) is None


def test_the_three_steps_each_credit_their_own_analysis(registry):
    _profile(registry)
    _walk(registry)
    _narratives(registry)
    _match(registry)
    last = registry.get_analysis_last_run("filesystem", SLUG)
    for aid in ("file_profiles", "narrative_docs", "file_data_class_match"):
        assert last[aid]["last_run_at"], aid
    assert "survey_price" not in last and "resource_reachability" not in last
