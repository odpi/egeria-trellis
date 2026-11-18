"""A rebuild can tell a lost PDF from one that never had text, and a file left with no chunks says why.

Two facts it needs (Architect read-back of #1055, 2026-11-11): each PDF chunk's `file_path` is the repository-relative
path, not the run's temporary download folder (which differs every run), and data preparation records which earlier
file a dropped duplicate chunk copied (the three egeria_docs PDFs with no chunks on laz were content duplicates).
"""
from __future__ import annotations

from types import SimpleNamespace

from resource_explorer.ingestion.data_prep import DataPrep
from resource_explorer.ingestion.doc_parser import DocChunk

TEXT = "Scope 3 emissions are the indirect emissions in a reporting company's value chain, upstream and down."


def _chunk(path, text=TEXT):
    return DocChunk(text=text, metadata={"file_path": path, "type": "pdf"})


class TestDuplicateOf:
    def test_a_chunk_dropped_as_a_copy_of_another_files_names_that_file(self):
        prep = DataPrep()
        kept = prep.filter([_chunk("docs/a.pdf"), _chunk("docs/b.pdf"), _chunk("docs/b.pdf", TEXT.upper())])
        assert [c.metadata["file_path"] for c in kept] == ["docs/a.pdf"]
        # The second b chunk is a near-duplicate (case only) of a's, so it is a's too.
        assert prep.duplicate_of == {"docs/b.pdf": {"docs/a.pdf": 2}}

    def test_a_chunk_repeated_inside_one_file_explains_nothing_about_that_file(self):
        prep = DataPrep()
        prep.filter([_chunk("docs/a.pdf"), _chunk("docs/a.pdf")])
        assert prep.duplicate_of == {}

    def test_reset_forgets_what_was_seen_and_what_was_dropped(self):
        prep = DataPrep()
        prep.filter([_chunk("docs/a.pdf"), _chunk("docs/b.pdf")])
        prep.reset_dedup()
        assert prep.duplicate_of == {}
        assert [c.metadata["file_path"] for c in prep.filter([_chunk("docs/b.pdf")])] == ["docs/b.pdf"]


def test_pdf_chunks_carry_the_repository_relative_path(tmp_path, monkeypatch):
    from rich.console import Console

    import resource_explorer.ingestion.artifact_tree_sink as sink
    from resource_explorer.ingestion import doc_parser
    from resource_explorer.ingestion.pipeline import IngestionPipeline

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(doc_parser, "build_pdf_converter",
                        lambda *a, **kw: SimpleNamespace(convert=lambda p: SimpleNamespace(document=object())))
    # parse_pdf is handed the absolute path, and stamps it, as the real one does.
    monkeypatch.setattr(doc_parser.DocParser, "parse_pdf", lambda self, path, slug, document=None: [_chunk(path)])
    monkeypatch.setattr(sink, "build_pdf_trees_from_documents",
                        lambda converted, slug: SimpleNamespace(near_empty=0, status="disabled", stored=0, skipped=0,
                                                                reason=""))
    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    pipeline.console = Console()
    ctype = SimpleNamespace(name="pdfs", chunk_size=384, chunk_overlap=48)
    chunks = pipeline._ingest_pdfs(tmp_path, "rr", ctype)
    assert [c.metadata["file_path"] for c in chunks] == ["docs/a.pdf"]
    assert pipeline.pdf_failures == []


def test_the_collection_ingest_reports_what_survived_and_what_was_dropped_per_file(monkeypatch):
    from resource_explorer.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    inserted = {}
    pipeline.store = SimpleNamespace(insert=lambda n, texts, metas, **kw: inserted.setdefault(n, len(texts)))
    monkeypatch.setattr(pipeline, "_ingest_pdfs", lambda root, slug, ct, *a: [
        _chunk("docs/a.pdf"), _chunk("docs/copy.pdf"),
        _chunk("docs/b.pdf", "A different paragraph altogether, long enough to pass the minimum length filter.")],
        raising=False)
    ctype = SimpleNamespace(name="pdfs", chunk_size=384, chunk_overlap=48)
    n = pipeline._ingest_collection(None, "rr", "rr_pdfs__rebuild", ctype, "/root", id_collection="rr_pdfs")
    assert n == 2
    assert pipeline.last_kept_per_file == {"docs/a.pdf": 1, "docs/b.pdf": 1}
    assert pipeline.last_duplicate_of == {"docs/copy.pdf": {"docs/a.pdf": 1}}
