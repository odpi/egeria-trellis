"""Data preparation — quality filtering, deduplication, and boilerplate removal."""
from __future__ import annotations

import hashlib
import re
import unicodedata


class DataPrep:
    """
    Pre-ingestion quality filtering applied to all chunks before embedding.

    Filters applied (in order):
    1. Minimum length — skip near-empty chunks
    2. Maximum repetition ratio — skip chunks that are >60% repeated lines
    3. Boilerplate patterns — auto-generated files, license blocks, lock files
    4. Low information density — skip chunks with too many non-alphanumeric chars
    5. Exact deduplication — SHA-256 content hash
    6. Near-duplicate detection — normalised hash (strips whitespace/case)

    Integration point: swap internals with IBM's Data Prep Toolkit
    (data-prep-kit) for production-grade PII detection, quality scoring,
    and language detection at scale.
    """

    BOILERPLATE_PATTERNS = [
        r"^#\s*auto.?generated",
        r"^//\s*generated\s+by",
        r"^/\*\s*\*\s*\*.+licen[sc]e",
        r"^this file is generated",
        r"^do not edit",
        r"^\s*<!-{2,}\s*generated",
        r"^package-lock\.json",
        r"^yarn\.lock",
        r"^Gemfile\.lock",
        r"^Pipfile\.lock",
    ]

    # Patterns that are mostly noise regardless of context
    _NOISE_PATTERNS = [
        re.compile(r"^[\s\-_=*#~`]{3,}$", re.MULTILINE),  # horizontal rules / dividers
    ]

    def __init__(self, min_chars: int = 50, max_repetition_ratio: float = 0.6) -> None:
        self.min_chars = min_chars
        self.max_repetition_ratio = max_repetition_ratio
        # hash -> the file_path of the chunk that first had it, so a dropped duplicate can say what it duplicated.
        self._seen_hashes: dict[str, str] = {}
        self._seen_norm_hashes: dict[str, str] = {}
        # file -> {the earlier file whose chunk it duplicated: chunks dropped}. Only across files: a chunk repeated
        # inside one file explains nothing about that file. Read by a rebuild, so a file left with no chunks says why.
        self.duplicate_of: dict[str, dict[str, int]] = {}
        self._boilerplate_re = [
            re.compile(p, re.IGNORECASE) for p in self.BOILERPLATE_PATTERNS
        ]

    def filter(self, chunks: list) -> list:
        return [c for c in chunks if self._keep(c)]

    def reset_dedup(self) -> None:
        self._seen_hashes.clear()
        self._seen_norm_hashes.clear()
        self.duplicate_of.clear()

    def score(self, chunk) -> float:
        """
        Return a quality score 0.0–1.0.  Used for ranking when chunks are
        near the minimum quality threshold rather than hard-filtering.
        """
        text = self._text(chunk)
        length_score = min(len(text) / 500, 1.0)
        alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
        rep_ratio = self._repetition_ratio(text)
        return round(length_score * 0.3 + alpha_ratio * 0.5 + (1 - rep_ratio) * 0.2, 3)

    # ── private ───────────────────────────────────────────────────────────────

    def _text(self, chunk) -> str:
        return chunk.text if hasattr(chunk, "text") else str(chunk)

    @staticmethod
    def _file(chunk) -> str:
        meta = getattr(chunk, "metadata", None)
        return str(meta.get("file_path") or "") if isinstance(meta, dict) else ""

    def _seen(self, seen: dict[str, str], digest: str, chunk) -> bool:
        if digest not in seen:
            seen[digest] = self._file(chunk)
            return False
        here, first = self._file(chunk), seen[digest]
        if here and first and here != first:
            per = self.duplicate_of.setdefault(here, {})
            per[first] = per.get(first, 0) + 1
        return True

    def _keep(self, chunk) -> bool:
        text = self._text(chunk)

        if len(text) < self.min_chars:
            return False

        if self._is_boilerplate(text):
            return False

        if self._repetition_ratio(text) > self.max_repetition_ratio:
            return False

        if self._low_information(text):
            return False

        # Exact dedup
        exact_hash = hashlib.sha256(text.encode()).hexdigest()
        if self._seen(self._seen_hashes, exact_hash, chunk):
            return False

        # Near-duplicate dedup (normalise whitespace + case)
        norm = re.sub(r"\s+", " ", text.lower().strip())
        norm = unicodedata.normalize("NFKD", norm)
        norm_hash = hashlib.sha256(norm.encode()).hexdigest()
        return not self._seen(self._seen_norm_hashes, norm_hash, chunk)

    def _is_boilerplate(self, text: str) -> bool:
        first_line = text.strip().splitlines()[0].lower() if text.strip() else ""
        return any(r.search(first_line) for r in self._boilerplate_re)

    def _repetition_ratio(self, text: str) -> float:
        """Fraction of lines that are duplicates of other lines in the chunk."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 4:
            return 0.0
        unique = len(set(lines))
        return 1.0 - (unique / len(lines))

    def _low_information(self, text: str) -> bool:
        """True if the chunk is mostly non-alphanumeric (e.g. minified JS, binary dump)."""
        if len(text) < 200:
            return False
        alnum = sum(c.isalnum() or c.isspace() for c in text)
        return (alnum / len(text)) < 0.40
