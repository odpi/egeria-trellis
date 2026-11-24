"""TR-117: a Funnel Stages scope reference reaches Dr.Egeria as a guid from the ONE Funnel Stages glossary.

On laz three ACTIVE elements are called "Assessment" (our Funnel Stages term, a DataFieldNaming glossary term and Egeria's
ValidMetadataValue typeName-(Assessment); read-only, 2026-11-23T20:55Z), so the 17 `Link Element To Scope` commands
naming it failed on every heal: 630/647. The runner's child now rewrites each such value to `<name> (guid:<guid>)`,
read from the glossary's members (Funnel Stages -> CollectionMembership -> its 10 terms on laz). Retire when PY-73 lands.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from resource_explorer import dr_egeria_scope_refs as sr

PKG = Path(__file__).resolve().parents[1]
QUESTIONS = PKG / "docs" / "dr-egeria" / "questions" / "scouting-questions.md"
FOUNDATIONS = PKG / "docs" / "dr-egeria" / "foundations" / "foundations.md"

DOC = """# Questions

## Create Glossary Term

### Display Name
Assessment

___

## Link Element To Scope

### Target Element
How mature is it?

### Scope Reference
Assessment

### Scope Category
Asked At

___

## Link Element To Scope

### Target Element
Who owns it?

### Scope Reference
Somewhere Else

### Scope Category
Answered At (guid:already-a-guid)
"""
TERMS = {"Assessment": "g-assess", "Asked At": "g-asked", "Answered At": "g-answered"}


def _doc(tmp_path, text=DOC):
    p = tmp_path / "docs" / "q.md"
    p.parent.mkdir()
    p.write_text(text)
    return p


def test_only_scope_values_inside_link_element_to_scope_are_found():
    assert [v for _n, v in sr.scope_values(DOC)] == ["Assessment", "Asked At", "Somewhere Else",
                                                     "Answered At (guid:already-a-guid)"]


def test_a_funnel_term_is_rewritten_to_its_guid_and_nothing_else_changes(tmp_path):
    p = _doc(tmp_path)
    out = sr.rewrite_scope_refs(p, object(), terms=lambda _c: TERMS, tmpdir=str(tmp_path / "copy"))
    assert out.count == 2 and out.rewritten == {"Assessment": "g-assess", "Asked At": "g-asked"}
    new = out.path.read_text()
    assert "### Scope Reference\nAssessment (guid:g-assess)\n" in new
    assert "### Scope Category\nAsked At (guid:g-asked)\n" in new
    assert "### Display Name\nAssessment\n" in new                        # outside the command: untouched
    assert "Somewhere Else\n" in new and "Answered At (guid:already-a-guid)" in new
    assert p.read_text() == DOC and out.path != p                          # the original file is never written
    assert "2 scope reference(s)" in out.words and "PY-73" in out.words


def test_a_lookup_that_cannot_name_one_glossary_sends_the_document_as_written(tmp_path):
    p = _doc(tmp_path)

    def two(_c):
        raise LookupError("2 glossaries are named 'Funnel Stages' (exactly one is needed)")
    out = sr.rewrite_scope_refs(p, object(), terms=two)
    assert out.path == p and out.count == 0 and "exactly one is needed" in out.words


def test_a_document_without_scope_links_makes_no_egeria_read(tmp_path):
    p = _doc(tmp_path, "## Create Glossary Term\n\n### Display Name\nX\n")

    def must_not(_c):
        raise AssertionError("read Egeria for a document with no scope link")
    out = sr.rewrite_scope_refs(p, object(), terms=must_not)
    assert out.path == p and out.words == ""


class _Client:
    def __init__(self, glossaries, members):
        self.glossaries, self.members, self.pages = glossaries, members, []

    def get_glossaries_by_name(self, name, output_format="JSON"):
        return self.glossaries

    def get_collection_members(self, collection_guid=None, start_from=0, page_size=0, output_format="JSON"):
        self.pages.append(start_from)
        page = self.members.get(collection_guid, [])[start_from:start_from + page_size]
        return page or "No elements found"


def _el(guid, name):
    return {"elementHeader": {"guid": guid}, "properties": {"displayName": name}}


def test_funnel_terms_reads_the_exact_glossary_and_pages_its_members():
    members = [_el(f"t{i}", f"Term {i}") for i in range(150)] + [_el("dup1", "Twice"), _el("dup2", "Twice")]
    c = _Client([_el("g-other", "Funnel Stages Archive"), _el("g-fs", "Funnel Stages")], {"g-fs": members})
    got = sr.funnel_terms(c)
    assert got["Term 0"] == "t0" and got["Term 149"] == "t149" and "Twice" not in got   # a name twice is not unique
    assert c.pages == [0, 100, 152]


def test_funnel_terms_refuses_when_the_glossary_is_missing():
    with pytest.raises(LookupError, match="0 glossaries"):
        sr.funnel_terms(_Client("No elements found", {}))


def test_the_real_questions_batch_rewrites_every_scope_reference_including_the_17_assessments(tmp_path):
    stages = [line.strip() for i, line in enumerate(FOUNDATIONS.read_text().splitlines())
              if i and FOUNDATIONS.read_text().splitlines()[i - 1].strip() == "### Display Name"]
    terms = {name: f"g-{n}" for n, name in enumerate(stages)}
    values = [v for _n, v in sr.scope_values(QUESTIONS.read_text())]
    out = sr.rewrite_scope_refs(QUESTIONS, object(), terms=lambda _c: terms, tmpdir=str(tmp_path))
    assert values and out.count == len(values)                             # nothing left to resolve by name
    assert values.count("Assessment") == 17


def test_the_report_line_before_the_summary_does_not_hide_it():
    from resource_explorer import dr_egeria_runner as runner

    sample = (PKG / "tests" / "fixtures" / "dr_egeria_summary_repo_analysis_process.txt").read_text()
    words = "TR-117: 2 scope reference(s) sent as guids from the Funnel Stages glossary (Assessment); retire ...\n"
    assert runner._summary_rows(words + sample) == runner._summary_rows(sample) is not None


def test_the_child_processes_the_rewritten_copy_reports_it_and_removes_it(tmp_path, monkeypatch, capsys):
    import md_processing.dr_egeria as dre

    from resource_explorer import dr_egeria_child as child
    from resource_explorer import egeria_clients

    p = _doc(tmp_path)
    fake = _Client([_el("g-fs", "Funnel Stages")], {"g-fs": [_el("g-assess", "Assessment"), _el("g-asked", "Asked At")]})
    monkeypatch.setattr(egeria_clients, "egeria_client", lambda *a, **k: type("C", (), {"of": lambda self, _t: fake})())
    seen = {}

    async def _process(input_file, **_kw):
        seen["path"] = Path(input_file)
        seen["text"] = Path(input_file).read_text()
    monkeypatch.setattr(dre, "process_md_file_v2", _process)
    monkeypatch.setenv(child.PASSWORD_ENV, "x-not-real")
    monkeypatch.delenv(child.TOKEN_ENV, raising=False)
    assert child.main(["--userid", "u", "--server", "s", "--url", "https://e", str(p)]) == 0
    assert seen["path"] != p and "Assessment (guid:g-assess)" in seen["text"]
    assert not seen["path"].exists()                                       # the copy's temp directory is gone
    assert "2 scope reference(s) sent as guids" in capsys.readouterr().out
    assert p.read_text() == DOC


# ── W-117-1 (Architect's read-back): the line also names the scope values left as names ──────────────────────────

def test_the_report_names_what_was_left_as_a_name(tmp_path):
    p = _doc(tmp_path)
    out = sr.rewrite_scope_refs(p, object(), terms=lambda _c: TERMS, tmpdir=str(tmp_path / "copy"))
    assert out.left == ["Somewhere Else"] and out.already == 1                 # W-117-2: a guid is not "left"
    assert "1 already guids; 1 left as names: Somewhere Else" in out.words
    assert "(Asked At, Assessment)" in out.words


def test_none_rewritten_still_names_every_value_left(tmp_path):
    p = _doc(tmp_path)
    out = sr.rewrite_scope_refs(p, object(), terms=lambda _c: {})
    assert out.count == 0 and "1 already guids; 3 left as names: Asked At, Assessment, Somewhere Else" in out.words


def test_all_rewritten_says_none_left(tmp_path):
    p = _doc(tmp_path, DOC.replace("Somewhere Else", "Assessment").replace(" (guid:already-a-guid)", ""))
    out = sr.rewrite_scope_refs(p, object(), terms=lambda _c: TERMS, tmpdir=str(tmp_path / "copy"))
    assert out.count == 4 and "0 already guids; 0 left as names" in out.words


def test_w117_2_values_already_guids_are_counted_apart_and_a_repeated_name_is_counted_once(tmp_path):
    text = DOC.replace("Somewhere Else", "Elsewhere") + DOC.split("# Questions", 1)[1].replace("Somewhere Else", "Elsewhere")
    p = _doc(tmp_path, text)
    out = sr.rewrite_scope_refs(p, object(), terms=lambda _c: TERMS, tmpdir=str(tmp_path / "copy"))
    assert out.count == 4 and out.already == 2                                  # two commands of each, twice over
    assert out.left == ["Elsewhere"] and out.left_count == 1                     # distinct: named twice, counted once
    assert "4 scope reference(s) sent as guids" in out.words and "2 already guids; 1 left as names: Elsewhere" in out.words


def test_the_real_questions_batch_leaves_nothing_as_a_name(tmp_path):
    lines = FOUNDATIONS.read_text().splitlines()
    terms = {lines[i].strip(): f"g-{i}" for i in range(1, len(lines)) if lines[i - 1].strip() == "### Display Name"}
    out = sr.rewrite_scope_refs(QUESTIONS, object(), terms=lambda _c: terms, tmpdir=str(tmp_path))
    assert out.left_count == 0 and out.already == 0 and "0 already guids; 0 left as names" in out.words
