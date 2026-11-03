"""The architecture document as a lens — finding 101.

The behaviours worth pinning are the restraints, not the matching: a lens that
quietly became an oracle would be worse than no lens, because its answers would
look like recovered evidence.
"""
from __future__ import annotations

import dataclasses

import pytest

from resource_explorer.github import architecture_doc as ad


@dataclasses.dataclass
class _Art:
    kind: str = "architecture"
    outcome: str = "in-repo"
    evidence: str = "docs/architecture.md"
    date: str | None = "2026-08-21T00:00:00+00:00"
    note: str = ""


@dataclasses.dataclass
class _Comp:
    slug: str
    name: str = ""

    def __post_init__(self):
        self.name = self.name or self.slug


class TestExtraction:
    def test_headings_bold_and_code_are_candidate_names(self):
        terms = ad.extract_terms("# Proxy\n**Root Coord** talks to `datacoord`.\n")
        assert terms == ["proxy", "root-coord", "datacoord"]

    def test_structural_boilerplate_is_dropped(self):
        assert ad.extract_terms("## Overview\n## Table of Contents\n## Design\n") == []

    def test_prose_emphasis_that_names_nothing_simply_fails_to_match_later(self):
        """The stop list is deliberately short. A false candidate costs nothing
        — it matches no component — whereas an over-eager list would silently
        drop a real name, which costs recall and says nothing."""
        terms = ad.extract_terms("## Getting Started\n**Quickly** run `make`\n")
        assert "quickly" in terms
        lens = ad.DocLens(terms=terms)
        assert lens.documented == {}

    def test_a_component_answers_to_its_last_segment(self):
        """`milvus-proxy` is called `proxy` in prose, and that is the match
        that matters — measured on Milvus, where it recovers rootcoord,
        datacoord, querycoord, indexcoord and the three node types."""
        assert ad._component_keys(_Comp("milvus-proxy")) >= {"milvus-proxy", "proxy"}

    def test_very_short_tokens_are_not_keys(self):
        """A two-character slug would match half a document."""
        assert all(len(k) >= 3 for k in ad._component_keys(_Comp("io")))


class TestItIsALensNotAnOracle:
    def test_it_never_adds_a_component(self, monkeypatch):
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [_Art()])
        monkeypatch.setattr(ad, "_read_document",
                            lambda *a, **k: "# Proxy\n# GhostService\n")
        comps = [_Comp("proxy")]
        lens = ad.apply("o/r", comps)
        assert [c.slug for c in comps] == ["proxy"], "the input list was mutated"
        assert set(lens.documented) == {"proxy"}
        assert "ghostservice" in lens.undetected, (
            "a doc-only name must surface as a disagreement, not as a component"
        )

    def test_a_doc_only_name_is_reported_as_undetected_not_adopted(self, monkeypatch):
        """§ finding 101: doc-disagrees-with-code is a FINDING, not a silent
        override. Adopting it would destroy the most useful thing here."""
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [_Art()])
        monkeypatch.setattr(ad, "_read_document", lambda *a, **k: "# Scheduler\n")
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.documented == {}
        assert lens.undetected == ["scheduler"]

    def test_it_never_reports_a_type_or_a_confidence(self):
        """The lens labels which components the document names. Anything that
        looked like a derived type or score would be the oracle creeping in."""
        fields = {f.name for f in dataclasses.fields(ad.DocLens)}
        for banned in ("type", "confidence", "score", "rank", "grade"):
            assert not any(banned in f for f in fields), f"{banned!r} leaked in"


class TestLocationIsPartOfTheAnswer:
    def test_the_outcome_and_date_travel_with_the_result(self, monkeypatch):
        """A sibling-repo answer is the case that justifies this whole path (9
        of the 13 corpus repos), and the date is what findings 65-68's
        version-correlation discipline needs — OpenLineage's doc is from 2023."""
        art = _Art(outcome="sibling-repo", evidence="o/r-docs:architecture.md",
                   date="2023-11-03T00:00:00+00:00")
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [art])
        monkeypatch.setattr(ad, "_read_document", lambda *a, **k: "# Proxy\n")
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.outcome == "sibling-repo"
        assert lens.date.startswith("2023")

    def test_not_found_is_not_an_error_and_reads_nothing(self, monkeypatch):
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [_Art(outcome="not-found", evidence="")])
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.terms == [] and lens.consulted is False
        assert any("nothing to read" in n for n in lens.notes)

    def test_a_doc_site_is_located_but_not_consulted(self, monkeypatch):
        """`doc-site` means a homepage field, which doc_locations itself says is
        not proof of a page. Located and unread are different states and both
        must be visible."""
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [_Art(outcome="doc-site",
                                                 evidence="https://x.example")])
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.outcome == "doc-site"
        assert lens.consulted is False
        assert any("none readable from" in n for n in lens.notes)
        assert lens.sources == [("doc-site", "https://x.example")], (
            "an unreadable site is still a SOURCE — it is what a recommendation "
            "to ingest the site would attach to"
        )

    def test_consulted_distinguishes_read_from_merely_located(self, monkeypatch):
        monkeypatch.setattr(ad.dl, "find_artifacts",
                            lambda *a, **k: [_Art()])
        monkeypatch.setattr(ad, "_read_document", lambda *a, **k: "")
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.outcome == "in-repo", "it WAS located"
        assert lens.consulted is False, "and it was NOT read — different states"
        assert any("none could be fetched" in n for n in lens.notes)


class TestNoSilentTruncation:
    def test_a_lookup_failure_degrades_with_a_note_rather_than_raising(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("github down")
        monkeypatch.setattr(ad.dl, "find_artifacts", _boom)
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert lens.consulted is False
        assert any("lookup failed" in n for n in lens.notes)

    def test_the_file_cap_exists_and_is_smaller_than_a_real_doc_set(self):
        """Milvus's design-docs directory holds 78 markdown files one level
        down; each is an API call. The cap is the reason this stays in the tier
        it claims, and every capped read reports what it dropped."""
        assert 0 < ad.MAX_DOC_FILES < 78


class TestDocumentationIsPlural:
    """`find_artifact` returned one location and that discarded real answers.
    Measured 2026-08-25: OpenLineage has six architecture sources (five sibling
    repos and a homepage), Milvus twelve. Nothing said the first was the one
    holding the architecture."""

    def test_every_readable_source_is_read_not_just_the_first(self, monkeypatch):
        arts = [_Art(evidence="a.md"), _Art(evidence="b.md")]
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k: arts)
        seen = []

        def _read(owner_repo, art, client, notes):
            seen.append(art.evidence)
            return "# Proxy\n" if art.evidence == "b.md" else ""

        monkeypatch.setattr(ad, "_read_document", _read)
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert seen == ["a.md", "b.md"], "stopped at the first source"
        assert lens.documented == {"proxy": "proxy"}, (
            "the term was only in the SECOND source"
        )

    def test_sources_records_what_was_found_and_read_separately(self, monkeypatch):
        arts = [_Art(evidence="a.md"), _Art(outcome="doc-site", evidence="https://x")]
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k: arts)
        monkeypatch.setattr(ad, "_read_document", lambda *a, **k: "# Proxy\n")
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert len(lens.sources) == 2, "both located"
        assert lens.read_sources == [("in-repo", "a.md")], "only one readable"

    # apply() looks each located site up as an ingested pgvector collection, and with no
    # Postgres it reports "could not read ingested collection" instead of the site count.
    # requires_pgvector skips (naming pgvector) off a Postgres machine and is a hard error
    # under RE_REQUIRE_POSTGRES, so GitHub CI still runs it.
    @pytest.mark.requires_pgvector
    def test_a_site_only_project_reports_every_site_it_found(self, monkeypatch):
        arts = [_Art(outcome="doc-site", evidence="https://a"),
                _Art(outcome="doc-site", evidence="https://b")]
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k: arts)
        lens = ad.apply("o/r", [_Comp("proxy")])
        assert len(lens.sources) == 2
        assert lens.consulted is False
        assert "2 documentation site(s) located" in lens.notes[0]


class TestAnIngestedSiteIsReadable:
    """The point of ingesting a documentation site. `sqlglot`'s was 97 chunks
    in `web_docs_sqlglot_com` while this reported "located, not readable" —
    ingestion made the document available to everything except the step that
    most needed it. Now: sqlglot 0 -> 6 documented, openmetadata 0 -> 4."""

    def test_mentions_need_no_markup(self):
        """Ingested content is rendered HTML converted to text, so the markdown
        emphasis `extract_terms` depends on is gone — running it over sqlglot's
        ingested site produced twelve terms, all code fragments. The question
        changes from 'what does this emphasise' to 'does this mention it'."""
        text = "The parser hands tokens to the generator, then the optimizer runs."
        found = ad.find_mentions(text, [_Comp("generator"), _Comp("planner")])
        assert set(found) == {"generator"}

    def test_generic_names_are_not_evidence(self):
        """A component legitimately called `index` is not evidenced by a
        documentation site containing the word "index"."""
        assert ad.find_mentions("the index is rebuilt nightly", [_Comp("index")]) == {}

    def test_matching_is_word_bounded(self):
        assert ad.find_mentions("regenerated nightly", [_Comp("generator")]) == {}
        assert ad.find_mentions("the generator runs", [_Comp("generator")])

    def test_short_names_are_refused(self):
        """Three characters would match most prose."""
        assert ad.find_mentions("the tso and the rest", [_Comp("tso")]) == {}

    def test_a_mention_is_labelled_weaker_than_emphasis(self, monkeypatch):
        """Emphasis in a source document is a stronger claim than a mention in
        rendered HTML. A reader who cannot tell them apart over-trusts the
        weaker one."""
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k:
                            [_Art(outcome="doc-site", evidence="https://sqlglot.com")])
        monkeypatch.setattr(ad, "_ingested_text_for",
                            lambda *a, **k: ("the generator runs", "web_docs_x"))
        lens = ad.apply("o/r", [_Comp("generator")])
        assert lens.documented == {"generator": "generator"}
        assert lens.evidence_kind["generator"] == ad.EVIDENCE_MENTIONED
        assert lens.outcome == "ingested-site"

    def test_emphasis_keeps_its_stronger_label(self, monkeypatch):
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k: [_Art()])
        monkeypatch.setattr(ad, "_read_document", lambda *a, **k: "# Generator\n")
        lens = ad.apply("o/r", [_Comp("generator")])
        assert lens.evidence_kind["generator"] == ad.EVIDENCE_EMPHASISED

    def test_consulted_keys_on_having_read_not_on_extracting(self, monkeypatch):
        """`unitycatalog` read 26 chunks and reported "not consulted", because
        `consulted` keyed on `terms` and HTML text yields none. Extracting
        nothing from a document is not the same as never opening one."""
        monkeypatch.setattr(ad.dl, "find_artifacts", lambda *a, **k:
                            [_Art(outcome="doc-site", evidence="https://x")])
        monkeypatch.setattr(ad, "_ingested_text_for",
                            lambda *a, **k: ("prose with no markup", "web_docs_x"))
        lens = ad.apply("o/r", [_Comp("nothingmatches")])
        assert lens.terms == [], "no markdown emphasis to find"
        assert lens.consulted is True, "but a document WAS read"
