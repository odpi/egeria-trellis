"""PDFs yield text, and a PDF that yields none is recorded, not skipped quietly.

The first timed rebuild on laz (2026-11-10) found Docling 2.118 compiling its layout model with torch.compile by
default: on Apple MPS (no float64) every batch failed inside the layout stage, and Docling returned an EMPTY document
without raising. RE's `_ingest_pdfs` then swallowed the rest, so 36 chunks of PDFs became none and only the rebuild's
drop-to-zero check noticed. Compile off was not enough: gate b354 (2026-11-11) ran the real-PDF test below on laz
and transformers' RT-DETRv2 layout model still failed on MPS, since it hard-codes float64. So Docling runs on the CPU
wherever it would pick MPS. These tests convert a real PDF through the same converter the pipeline uses; on laz that
is the MPS check. transformers 5.22.2 fixed the float64, so with it in the lock Docling runs on MPS again and the
CPU fallback only guards an older lock.
"""
from __future__ import annotations

from pathlib import Path

import pytest

TEXT = "Trellis rebuild conversion check"


def _pdf(path: Path, text: str = TEXT) -> Path:
    """A minimal one-page PDF with `text` as real (not rasterised) text, built by hand so the test needs no PDF
    library. Offsets in the xref table are computed, so the file is valid."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(bytes(out))
    return path


def _converter_or_skip():
    pytest.importorskip("docling")
    from resource_explorer.ingestion.doc_parser import build_pdf_converter

    return build_pdf_converter(ocr=False)


def test_building_the_converter_turns_torch_compile_off():
    _converter_or_skip()
    from docling.datamodel.settings import settings

    assert settings.inference.compile_torch_models is False


def test_the_artifact_tree_adapter_turns_it_off_too():
    pytest.importorskip("docling")
    from docling.datamodel.settings import settings
    from trellis_artifact_tree.adapters_pdf import disable_torch_compile

    settings.inference.compile_torch_models = True
    disable_torch_compile()
    assert settings.inference.compile_torch_models is False


def _mps_host(monkeypatch, *, cuda=False, mps_safe=False):
    """A host with MPS; `mps_safe` says whether the installed transformers is taken to handle it."""
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("TRELLIS_DOCLING_ALLOW_MPS", "1" if mps_safe else "0")
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)


def test_the_converter_runs_on_the_cpu_where_it_would_pick_an_unsafe_mps(monkeypatch):
    pytest.importorskip("docling")
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from trellis_artifact_tree.adapters_pdf import keep_off_mps

    _mps_host(monkeypatch)
    for device in (AcceleratorDevice.AUTO, AcceleratorDevice.MPS):
        options = PdfPipelineOptions()
        options.accelerator_options.device = device
        keep_off_mps(options)
        assert options.accelerator_options.device == AcceleratorDevice.CPU


def test_cuda_is_left_alone(monkeypatch):
    pytest.importorskip("docling")
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from trellis_artifact_tree.adapters_pdf import keep_off_mps

    _mps_host(monkeypatch, cuda=True)
    options = PdfPipelineOptions()
    options.accelerator_options.device = AcceleratorDevice.AUTO
    keep_off_mps(options)
    assert options.accelerator_options.device == AcceleratorDevice.AUTO


def test_the_converter_on_an_unsafe_mps_host_uses_the_cpu(monkeypatch):
    pytest.importorskip("docling")
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.datamodel.base_models import InputFormat

    _mps_host(monkeypatch)
    converter = _converter_or_skip()
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options.accelerator_options.device == AcceleratorDevice.CPU


def test_a_safe_mps_is_left_to_docling(monkeypatch):
    pytest.importorskip("docling")
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from trellis_artifact_tree.adapters_pdf import keep_off_mps

    _mps_host(monkeypatch, mps_safe=True)
    options = PdfPipelineOptions()
    options.accelerator_options.device = AcceleratorDevice.AUTO
    keep_off_mps(options)
    assert options.accelerator_options.device == AcceleratorDevice.AUTO


@pytest.mark.parametrize(
    ("installed", "safe"), [("5.10.1", False), ("5.22.1", False), ("5.22.2", True), ("5.23.0", True), ("6.0.0", True)]
)
def test_the_transformers_version_decides_without_the_override(monkeypatch, installed, safe):
    from trellis_artifact_tree import adapters_pdf

    monkeypatch.delenv("TRELLIS_DOCLING_ALLOW_MPS", raising=False)
    monkeypatch.setattr(adapters_pdf.metadata, "version", lambda name: installed)
    assert adapters_pdf.mps_is_safe() is safe


def test_no_transformers_is_taken_as_unsafe(monkeypatch):
    from trellis_artifact_tree import adapters_pdf

    def missing(name):
        raise adapters_pdf.metadata.PackageNotFoundError(name)

    monkeypatch.delenv("TRELLIS_DOCLING_ALLOW_MPS", raising=False)
    monkeypatch.setattr(adapters_pdf.metadata, "version", missing)
    assert adapters_pdf.mps_is_safe() is False


@pytest.mark.parametrize(("value", "safe"), [("1", True), ("true", True), ("YES", True), ("0", False), ("no", False)])
def test_the_override_wins_over_the_version(monkeypatch, value, safe):
    from trellis_artifact_tree import adapters_pdf

    monkeypatch.setenv("TRELLIS_DOCLING_ALLOW_MPS", value)
    monkeypatch.setattr(adapters_pdf.metadata, "version", lambda name: "5.22.2" if not safe else "5.10.1")
    assert adapters_pdf.mps_is_safe() is safe


def test_the_lock_carries_an_mps_safe_transformers(monkeypatch):
    """On laz this is what lets the real-PDF test below run on MPS rather than the CPU."""
    pytest.importorskip("transformers")
    from trellis_artifact_tree import adapters_pdf

    monkeypatch.delenv("TRELLIS_DOCLING_ALLOW_MPS", raising=False)
    assert adapters_pdf.mps_is_safe() is True


def test_a_real_pdf_converts_to_its_text(tmp_path):
    converter = _converter_or_skip()
    try:
        document = converter.convert(str(_pdf(tmp_path / "check.pdf"))).document
    except Exception as exc:  # no models offline is an environment fact, not a result
        if "huggingface" in str(exc).lower() or "connect" in str(exc).lower():
            pytest.skip(f"Docling models are not available here: {exc}")
        raise
    assert TEXT in document.export_to_markdown()


class _Ctype:
    name, chunk_size, chunk_overlap = "pdfs", 384, 48


def test_the_pipeline_records_a_pdf_that_yields_no_text(tmp_path, monkeypatch):
    """Docling's silent failure, reproduced without MPS: a converter that returns a document with no text."""
    from resource_explorer.ingestion import doc_parser
    from resource_explorer.ingestion.pipeline import IngestionPipeline

    class Empty:
        def export_to_markdown(self):
            return ""

        def iterate_items(self, *a, **kw):
            return iter(())

    class Conv:
        def convert(self, path):
            return type("R", (), {"document": Empty()})()

    monkeypatch.setattr(doc_parser, "build_pdf_converter", lambda *a, **kw: Conv())
    monkeypatch.setattr(doc_parser.DocParser, "parse_pdf", lambda self, *a, **kw: [])
    import resource_explorer.ingestion.artifact_tree_sink as sink
    monkeypatch.setattr(sink, "build_pdf_trees_from_documents",
                        lambda converted, slug: type("T", (), {"near_empty": 0, "status": "disabled", "stored": 0,
                                                               "skipped": 0, "reason": ""})())
    _pdf(tmp_path / "a.pdf")
    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    from rich.console import Console
    pipeline.console = Console()
    assert pipeline._ingest_pdfs(tmp_path, "rr", _Ctype()) == []
    assert pipeline.pdf_failures == [("a.pdf", "converted to no text")]


def test_the_pipeline_records_a_pdf_that_fails_to_convert(tmp_path, monkeypatch):
    from resource_explorer.ingestion import doc_parser
    from resource_explorer.ingestion.pipeline import IngestionPipeline

    class Conv:
        def convert(self, path):
            raise RuntimeError("Stage layout failed")

    monkeypatch.setattr(doc_parser, "build_pdf_converter", lambda *a, **kw: Conv())
    import resource_explorer.ingestion.artifact_tree_sink as sink
    monkeypatch.setattr(sink, "build_pdf_trees_from_documents",
                        lambda converted, slug: type("T", (), {"near_empty": 0, "status": "disabled", "stored": 0,
                                                               "skipped": 0, "reason": ""})())
    _pdf(tmp_path / "b.pdf")
    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    from rich.console import Console
    pipeline.console = Console()
    pipeline._ingest_pdfs(tmp_path, "rr", _Ctype())
    assert pipeline.pdf_failures and pipeline.pdf_failures[0][0] == "b.pdf"
    assert "Stage layout failed" in pipeline.pdf_failures[0][1]
