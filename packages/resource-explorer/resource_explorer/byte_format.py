"""The one place a byte count becomes words (TR-81).

Decimal units, 1 KB = 1,000 bytes, as macOS Finder and the file-system card show them. Every size labelled
KB/MB/GB in Resource Explorer comes through here; the JS twin is `fmtBytesDecimal` in
`web/static/next/stages/native-report.js`. Binary units are used only where the words say KiB/MiB/GiB.
"""
from __future__ import annotations


def format_bytes(n: int | float, *, bytes_word: str = "B") -> str:
    """"60.2 MB" for 60,209,097. A value that rounds up to 1000 of its unit moves to the next ("1.0 MB", never
    "1,000.0 KB"). Below 1000 it is whole bytes, labelled `bytes_word`."""
    v = float(n)
    for unit in (bytes_word, "KB", "MB", "GB", "TB"):
        if unit == bytes_word:
            if abs(v) < 1000:
                return f"{v:,.0f} {unit}"
        elif round(v, 1) < 1000 or unit == "TB":
            return f"{v:,.1f} {unit}"
        v /= 1000
    return f"{v:,.1f} TB"  # pragma: no cover - the loop always returns
