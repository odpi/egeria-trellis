"""PDFs and other documents via Docling. Requires the `[pdf]` extra.

This is the hardest test of the format-independence claim in
docs/context-compilation-design.md §21, because a PDF has no structure of its
own -- only layout, from which structure is *inferred*. Docling does that
inference; this module maps its result onto containment and records that the
result is inferred rather than stated.

The conversion and the mapping are deliberately separate. `tree_from_items()`
takes an already-converted sequence and needs no Docling at all, so the mapping
is testable without a PDF, a model download, or a conversion run. Only
`_convert()` touches the library, and it imports inside the call so a missing
extra fails readably at the point of use.

**Sections carry IDENTIFIERS; blocks carry FULL.** A heading's node holds only
its own text, and the prose beneath it becomes child block nodes. Giving the
section a FULL rung as well would store every byte twice -- once on the section,
once on its children -- and a packer that wants "the whole section" gets it by
descending, which is what `cut()` is for.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import (
    metadata,  # noqa: F401 -- kept: tests patch adapters_pdf.metadata.version (the shared module)
)
from typing import Iterable

from trellis_artifact_tree.model import ArtifactTree, Node, Provenance, Rung

# Docling labels that carry document structure rather than content.
_HEADING_LABELS = {"title", "section_header"}
# Labels worth keeping as content. Page furniture is dropped: a running header
# repeated on ninety pages is noise a packer would pay for ninety times.
_CONTENT_LABELS = {"text", "list_item", "table", "code", "formula", "caption"}
_DROPPED_LABELS = {"page_header", "page_footer", "picture"}


def disable_torch_compile() -> None:
    """Turn off Docling's torch.compile of its models, before any pipeline options are built.

    Docling (2.118) compiles its layout model by default (`settings.inference.compile_torch_models`). On Apple
    MPS, which has no float64, every batch then fails inside the layout stage ("KeyError: torch.float64"), and
    Docling returns an EMPTY document without raising. Found on laz by the first timed rebuild (2026-11-10): 36
    chunks of PDFs became none. Compilation is only a speed-up, so it is off everywhere; the engines read this
    setting when their options are constructed, so it must be set before `PdfPipelineOptions()`.
    """
    try:
        from docling.datamodel.settings import settings
    except ImportError:  # pragma: no cover - the [pdf] extra is not installed
        return
    settings.inference.compile_torch_models = False


# The device choice is trellis-accel's, shared with RE's and EA's embeddings (docs/hardware-acceleration-proposal.md).
# These names stay here for callers that import them from this module.
from trellis_accel import (  # noqa: F401 -- re-exported
    ALLOW_MPS_ENV,
    MPS_SAFE_TRANSFORMERS,
)


def mps_is_safe() -> bool:
    """Whether Docling may run on Apple's GPU here: `TRELLIS_DOCLING_ALLOW_MPS` when set (1/true/yes allows, anything
    else forbids), else transformers 5.22.2 or later (whose RT-DETR models compute in float32 on MPS). The rule is
    trellis_accel.mps_problem's; this is its yes/no."""
    from trellis_accel import mps_problem

    return mps_problem("docling") is None


def keep_off_mps(options) -> None:
    """Point Docling at the device trellis-accel chooses for it, where that matters: the CPU instead of Apple's GPU
    when the GPU is not safe for Docling, and the CPU whenever `TRELLIS_DEVICE=cpu`. A GPU it chooses (CUDA/ROCm, or
    a safe Apple GPU) is what Docling's own AUTO picks too, so AUTO is left alone; a device set explicitly on the
    options is left alone unless it is an unsafe Apple GPU.

    History: Docling's compiled layout model, then transformers' float64 in RT-DETRv2, failed on Apple's GPU (gate
    b354, laz, 2026-11-11); transformers 5.22.2 fixed the latter. A `TRELLIS_DEVICE` this PyTorch cannot use raises
    trellis_accel.DeviceUnavailable with a sentence.
    """
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice
    except ImportError:  # pragma: no cover - the [pdf] extra is not installed
        return
    from trellis_accel import pick_device

    device = options.accelerator_options.device
    choice = pick_device("auto", purpose="docling")
    auto_or_mps = device in (AcceleratorDevice.AUTO, AcceleratorDevice.AUTO.value,
                             AcceleratorDevice.MPS, AcceleratorDevice.MPS.value)
    if choice.device == "cpu" and auto_or_mps:
        options.accelerator_options.device = AcceleratorDevice.CPU


@dataclass(frozen=True)
class DocItem:
    """The subset of a Docling item this mapping needs.

    A local shape rather than a Docling type on purpose: it keeps
    tree_from_items() importable and testable without the extra installed, and
    it documents exactly which four fields the mapping depends on.
    """

    label: str
    text: str
    level: int = 1


def tree_from_items(
    artifact_id: str, items: Iterable[DocItem], provenance: Provenance,
    fidelity: str = "structural",
) -> ArtifactTree:
    """Map a flat, ordered item sequence onto containment.

    Headings nest by level, exactly as markdown does -- and for the same reason
    they skip levels in real documents, so a stack keyed on level handles jumps
    without inventing intermediate nodes.
    """
    root = Node(
        node_id=f"{artifact_id}:root", artifact_id=artifact_id, kind="document",
        title=provenance.source_id, rungs={Rung.IDENTIFIERS: provenance.source_id},
    )
    nodes = [root]
    stack: dict[int, str] = {}
    ordinals: dict[str, int] = {}
    counter = 0

    def next_ordinal(parent: str) -> int:
        n = ordinals.get(parent, 0)
        ordinals[parent] = n + 1
        return n

    for item in items:
        label = (item.label or "").lower()
        if label in _DROPPED_LABELS or not (item.text or "").strip():
            continue

        if label in _HEADING_LABELS:
            # A title is the outermost heading whatever level it claims.
            level = 0 if label == "title" else max(1, int(item.level or 1))
            parent = root.node_id
            for lvl in sorted((l for l in stack if l < level), reverse=True):
                parent = stack[lvl]
                break
            node_id = f"{artifact_id}:d{counter}"
            counter += 1
            nodes.append(Node(
                node_id=node_id, artifact_id=artifact_id, kind="section",
                title=item.text.strip(), parent_id=parent,
                ordinal=next_ordinal(parent),
                rungs={Rung.IDENTIFIERS: item.text.strip()},
            ))
            stack[level] = node_id
            for deeper in [l for l in stack if l > level]:
                del stack[deeper]
            continue

        if label not in _CONTENT_LABELS:
            continue

        parent = stack[max(stack)] if stack else root.node_id
        node_id = f"{artifact_id}:d{counter}"
        counter += 1
        nodes.append(Node(
            node_id=node_id, artifact_id=artifact_id, kind=label,
            parent_id=parent, ordinal=next_ordinal(parent),
            rungs={Rung.FULL: item.text.strip()},
        ))

    return ArtifactTree(
        artifact_id=artifact_id,
        provenance=replace(provenance, extraction_fidelity=fidelity),
        nodes=tuple(nodes),
    )


def items_from_docling(document) -> list[DocItem]:
    """Project a converted DoclingDocument onto the four fields the mapping
    needs.

    Public because conversion is the expensive step and a caller that has
    already paid it should not pay again. Resource Explorer converts each PDF
    once and feeds the result to both its chunker and this tree builder --
    without this seam, an ingest run converts every PDF twice.

    Duck-typed rather than importing Docling types: this function is then
    importable and testable without the [pdf] extra installed.
    """
    out: list[DocItem] = []
    for item, _level in document.iterate_items():
        label = getattr(getattr(item, "label", None), "value", "") or ""
        text = getattr(item, "text", "") or ""
        out.append(DocItem(label=label, text=text,
                           level=int(getattr(item, "level", 1) or 1)))
    return out


class DoclingDocumentAdapter:
    """For callers that converted already: `source` is a DoclingDocument, not a
    path. Same mapping and same fidelity as PdfAdapter -- only the conversion
    step is skipped, because someone else already ran it."""

    name = "pdf-document"
    fidelity = "inferred"

    def handles(self, kind: str) -> bool:
        return kind.lower() in {"docling-document", "docling"}

    def parse(self, artifact_id: str, source, provenance: Provenance) -> ArtifactTree:
        if isinstance(source, (str, bytes)):
            raise TypeError(
                "DoclingDocumentAdapter needs a converted DoclingDocument; "
                "use PdfAdapter for a path"
            )
        return tree_from_items(
            artifact_id, items_from_docling(source), provenance, self.fidelity
        )


class PdfAdapter:
    """Docling-backed. `source` is a path -- Docling reads the file itself, and
    handing it bytes we just read would only make it write them back out."""

    name = "pdf"
    # "inferred", not "structural": a PDF states layout, not structure, and a
    # reader deserves to know the hierarchy was reconstructed rather than read.
    # This reaches the manifest and the answer's citations.
    fidelity = "inferred"
    _KINDS = {"pdf", ".pdf", "application/pdf", "docx", ".docx", "html", ".html"}

    def handles(self, kind: str) -> bool:
        return kind.lower() in self._KINDS

    # --- OCR ---------------------------------------------------------------
    #
    # OCR is not what extracts tables. `do_table_structure` is a separate
    # Docling stage (TableFormer), on by default, and it works with OCR off --
    # measured: a webinar deck yielded 2 tables and 32 section headers with
    # `ocr=False`. Layout, headings, lists and tables all come from the layout
    # models, not from OCR.
    #
    # OCR is for text that exists only as PIXELS: a scanned page, or words
    # rendered inside a diagram image. Those are real cases here -- an
    # architecture diagram whose labels are the content is invisible without it,
    # and Docling reports such a region as a bare `picture`.
    #
    # Default off because Docling's DEFAULT engine is broken in this workspace,
    # not because OCR is unwanted. rapidocr needs omegaconf, which cannot
    # resolve above 2.0.6 while soda-core pins antlr4 to 4.11, and 2.0.6 rejects
    # the PosixPath rapidocr passes for Global.model_root_dir -- so with the
    # default engine EVERY conversion fails, not just scanned ones.
    #
    # The fix for wanting OCR is therefore NOT the soda migration. It is picking
    # a different engine: neither `ocrmac` (3 dependencies, macOS Vision) nor
    # `easyocr` (12, torch-based and cross-platform, and torch is already
    # present) depends on omegaconf at all.
    #
    #   ocr=False                      digital PDFs; tables and headings still work
    #   ocr=True, engine="easyocr"     scanned pages or diagram text, portable
    #   ocr=True, engine="ocrmac"      same, macOS only, far lighter
    #   ocr=True, engine="rapidocr"    blocked until the omegaconf conflict clears
    ocr: bool = False
    ocr_engine: str = "easyocr"

    _ENGINES = {
        "easyocr": ("EasyOcrOptions", "easyocr"),
        "ocrmac": ("OcrMacOptions", "ocrmac"),
        "tesseract": ("TesseractOcrOptions", "tesserocr"),
        "rapidocr": ("RapidOcrOptions", "rapidocr"),
    }

    def __init__(self, ocr: bool | None = None, ocr_engine: str | None = None) -> None:
        if ocr is not None:
            self.ocr = ocr
        if ocr_engine is not None:
            self.ocr_engine = ocr_engine

    def _ocr_options(self):
        """Build engine options, failing with a usable message rather than an
        ImportError from three libraries down."""
        import importlib.util

        if self.ocr_engine not in self._ENGINES:
            raise ValueError(
                f"unknown OCR engine {self.ocr_engine!r}; "
                f"known: {', '.join(sorted(self._ENGINES))}"
            )
        options_name, package = self._ENGINES[self.ocr_engine]
        if importlib.util.find_spec(package) is None:
            raise ImportError(
                f"OCR engine {self.ocr_engine!r} needs the {package!r} package. "
                f"Note rapidocr is Docling's default and is currently unusable "
                f"here (omegaconf conflict); easyocr and ocrmac are not."
            )
        from docling.datamodel import pipeline_options as po

        return getattr(po, options_name)()

    def _convert(self, path: str) -> list[DocItem]:
        disable_torch_compile()
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ImportError(
                "the pdf adapter needs the [pdf] extra: "
                "pip install 'trellis-artifact-tree[pdf]'"
            ) from exc

        options = PdfPipelineOptions()
        keep_off_mps(options)
        options.do_ocr = self.ocr
        # Left on regardless of OCR: table structure recognition is a separate
        # Docling stage and works without it. Tying them together would mean
        # enabling OCR just to read a table.
        options.do_table_structure = True
        if self.ocr:
            options.ocr_options = self._ocr_options()
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        return items_from_docling(converter.convert(path).document)

    def parse(self, artifact_id: str, source, provenance: Provenance) -> ArtifactTree:
        if not isinstance(source, str):
            raise TypeError(
                f"pdf adapter needs a path (str), got {type(source).__name__}"
            )
        return tree_from_items(
            artifact_id, self._convert(source), provenance, self.fidelity
        )
