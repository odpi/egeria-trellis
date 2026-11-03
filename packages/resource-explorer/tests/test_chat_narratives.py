"""Chat searches a file system's narrative passages (Files follow-up 1, the reader half of F11).

The search index is a fake collection (PGVECTOR_PORT=1 keeps the real one unreachable); the registry is a null mock with
the one call the section makes. Pinned: a scoped question returns a cited passage, the proposal label, the journal's
passage id, "not read yet" for a file system never pressed, the no-hit words, an old press's bare file name cited as it
is with the metadata's file_path added, and the compile's budget respected.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from trellis_vectorstore.base import SearchResult

from resource_explorer import journal, narrative_context
from resource_explorer.context_compile import compile_context
from resource_explorer.surveyors.filesystem import narrative_docs as nd


class _Reg(MagicMock):
    def _get_child_mock(self, **kw):
        child = _Reg(**kw)
        child.return_value = None
        return child


def _registry(pressed: bool):
    r = _Reg()
    r.query_findings.side_effect = lambda *a, **k: []
    r.query_step_runs.side_effect = lambda **k: [{"metrics": {"walk_run": "w1"}}] if pressed else []
    return r


class FakeIndex:
    def __init__(self, hits=None, exists=True):
        self.hits, self.exists, self.searched = hits or [], exists, []

    def collection_name(self, slug, ctype):
        return f"{slug}_{ctype}"

    def collection_exists(self, c):
        return self.exists

    def search(self, query, collections, top_k=None, min_score=None):
        self.searched.append((query, list(collections), top_k))
        self.floor = min_score
        return [h for h in self.hits if min_score is None or h.score >= min_score][:top_k]


def _hit(path="survey/README.md", section="Variables", text="age: years", **extra):
    meta = {"file_path": path, "section": section, "walk_run": "w1", "status": "proposal",
            "stated_by": f"{path} § {section}", **extra}
    return SearchResult(id="1", score=0.9, text=text, metadata=meta)


def _compile(registry, index, budget=4000, q="What does the codebook say about age?"):
    return compile_context(registry, "tree", q, resource_type="filesystem", budget=budget,
                           narrative_store_factory=lambda: index)


def test_a_scoped_question_returns_a_cited_passage():
    index = FakeIndex([_hit()])
    c = _compile(_registry(True), index)
    assert "stated by survey/README.md § Variables" in c.text
    assert "age: years" in c.text
    assert index.searched[0][1] == ["fs_tree_narratives"] and index.searched[0][2] == narrative_context.TOP_K


def test_the_passage_is_labelled_a_proposal():
    c = _compile(_registry(True), FakeIndex([_hit()]))
    assert "stated by survey/README.md § Variables — proposal" in c.text
    assert "not a measured fact" in c.text
    [p] = c.manifest["narratives"]["passages"]
    assert p["status"] == "proposal" and p["walk_run"] == "w1"


def test_the_journal_stores_the_passage_id():
    reg = _registry(True)
    c = _compile(reg, FakeIndex([_hit()]))
    reg.get_compile.side_effect = lambda cid: {"manifest": c.manifest}
    entry = journal.derive_turn_entry(reg, question="q", answer="a", compile_id=c.compile_id,
                                      investigation="inv", resource_slug="tree")
    assert entry["turn"]["passages"] == [nd.passage_id("w1", "survey/README.md", "Variables")]
    assert "- Passages: w1|survey/README.md|Variables" in entry["body"]


def test_never_pressed_says_not_read_yet_and_does_not_search():
    index = FakeIndex([_hit()])
    c = _compile(_registry(False), index)
    assert "not read yet" in c.text
    assert not index.searched
    assert c.manifest["narratives"]["state"] == "not_read" and c.manifest["narratives"]["passages"] == []


def test_no_hit_says_so_without_claiming_there_are_no_narratives():
    c = _compile(_registry(True), FakeIndex([]))
    assert c.manifest["narratives"]["state"] == "no_match"
    assert "no passage matched this question" in c.text
    assert "do not tell the person there are no narratives" in c.text
    assert "not read yet" not in c.text


def test_an_unreachable_index_is_not_reported_as_an_absence():
    def boom():
        raise ConnectionError("down")
    c = compile_context(_registry(True), "tree", "q", resource_type="filesystem", budget=4000,
                        narrative_store_factory=boom)
    assert c.manifest["narratives"]["state"] == "unavailable"
    assert "could not be reached" in c.text and "no passage matched" not in c.text


def test_a_row_from_an_old_press_is_cited_as_it_holds_with_its_file_path():
    old = SearchResult(id="2", score=0.8, text="codes", metadata={
        "stated_by": "README.md", "file_path": "survey/README.md", "section": "Variables", "walk_run": "w0"})
    c = _compile(_registry(True), FakeIndex([old]))
    assert "stated by README.md (survey/README.md)" in c.text
    assert c.manifest["narratives"]["passages"][0]["passage_id"] == "w0|survey/README.md|Variables"


def test_the_budget_is_respected():
    big = [_hit(path=f"d{i}/README.md", text="word " * 600) for i in range(5)]
    for budget in (1500, 3000, 6000):
        c = _compile(_registry(True), FakeIndex(big), budget=budget)
        assert c.manifest["used"] <= budget and len(c.text) <= budget


def test_a_repo_compile_has_no_narrative_section():
    c = compile_context(_registry(True), "egeria", "q", resource_type="repo", budget=4000)
    assert "narratives" not in c.manifest


def test_a_weak_nearest_neighbour_is_no_match_in_chat():
    index = FakeIndex([SearchResult(id="9", score=0.01, text="unrelated", metadata={"file_path": "x.md"})])
    c = _compile(_registry(True), index)
    assert index.floor == narrative_context.min_score()
    assert c.manifest["narratives"]["state"] == "no_match" and "unrelated" not in c.text
