"""Document parser — Markdown (native) + Docling (PDF, web pages, DOCX)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath


@dataclass
class DocChunk:
    text: str
    metadata: dict  # source_url, file_path, page_number (PDFs), project_slug


def build_pdf_converter(ocr: bool | None = None, engine: str | None = None):
    """One place that decides how a PDF is converted.

    Both the chunker and the containment-tree builder go through this, so they
    cannot drift into converting the same file two different ways -- which
    would be invisible until their outputs disagreed.

    Table structure recognition is left ON regardless: it is a separate Docling
    stage from OCR and works without it (measured: 2 tables and 32 headings from
    a webinar deck with OCR off). OCR is only for text that exists as pixels --
    a scanned page, or labels rendered inside a diagram image.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from trellis_artifact_tree.adapters_pdf import disable_torch_compile, keep_off_mps

    from resource_explorer.config import get_config

    cfg = get_config().artifact_tree
    use_ocr = cfg.pdf_ocr if ocr is None else ocr

    # Before the options are built: Docling's compiled layout model fails every batch on Apple MPS and returns an
    # empty document without raising (laz, 2026-11-10).
    disable_torch_compile()
    options = PdfPipelineOptions()
    # Off MPS unless the installed transformers handles it: before 5.22.2 the layout model hard-coded float64,
    # which MPS lacks (gate b354, 2026-11-11).
    keep_off_mps(options)
    options.do_ocr = use_ocr
    options.do_table_structure = True
    if use_ocr:
        from trellis_artifact_tree.adapters_pdf import PdfAdapter

        options.ocr_options = PdfAdapter(
            ocr=True, ocr_engine=engine or cfg.pdf_ocr_engine
        )._ocr_options()
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


class DocParser:
    """
    Parses documentation into chunks for embedding.

    - Markdown: split on heading boundaries, then fixed-size windows
    - PDF / DOCX / HTML: delegate to Docling for layout-aware extraction
    - Web pages: Docling WebLoader with configurable crawl depth
    """

    def __init__(self, chunk_size: int = 384, chunk_overlap: int = 48) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def parse_markdown(self, content: str, file_path: str, resource_slug: str) -> list[DocChunk]:
        sections = self._split_on_headings(content)
        chunks = []
        for section in sections:
            for chunk in self._fixed_window(section):
                chunks.append(DocChunk(
                    text=chunk,
                    metadata={"file_path": file_path, "project_slug": resource_slug, "type": "markdown"},
                ))
        return chunks

    def parse_pdf(
        self, file_path: str, resource_slug: str, document=None,
    ) -> list[DocChunk]:
        """Use Docling for layout-aware PDF parsing.

        `document` lets a caller that has ALREADY converted the file pass the
        result in, so conversion happens once. Docling conversion is by far the
        expensive step here, and the containment-tree builder needs the same
        converted document -- without this parameter an ingest run converts
        every PDF twice. Left optional so existing callers are unaffected.
        """
        if document is None:
            document = build_pdf_converter().convert(file_path).document
        text = document.export_to_markdown()
        return [
            DocChunk(
                text=chunk,
                metadata={"file_path": file_path, "project_slug": resource_slug, "type": "pdf"},
            )
            for chunk in self._fixed_window(text)
        ]

    def parse_url(self, url: str, resource_slug: str) -> list[DocChunk]:
        """Use Docling to fetch and parse a web page."""
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(url)
        text = result.document.export_to_markdown()
        return [
            DocChunk(
                text=chunk,
                metadata={"source_url": url, "project_slug": resource_slug, "type": "web"},
            )
            for chunk in self._fixed_window(text)
        ]

    def _split_on_headings(self, markdown: str) -> list[str]:
        import re
        sections = re.split(r"\n(?=#{1,3} )", markdown)
        return [s.strip() for s in sections if s.strip()]

    def _fixed_window(self, text: str) -> list[str]:
        words = text.split()
        step = self.chunk_size - self.chunk_overlap
        chunks = []
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + self.chunk_size])
            if chunk:
                chunks.append(chunk)
        return chunks
