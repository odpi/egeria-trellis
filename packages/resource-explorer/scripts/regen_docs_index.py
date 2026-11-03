#!/usr/bin/env python3
"""Regenerate docs/README.md from each document's own Status line.

A hand-maintained index goes stale the first time someone adds a file, and a
stale index is worse than none: it looks authoritative. This reads the documents
themselves, so the index can only be as wrong as they are.

The header above the generated section is preserved — everything from the first
"## " heading down is replaced.

    python scripts/regen_docs_index.py [--check]

--check exits non-zero if the index is out of date, for use in CI.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

DOCS = pathlib.Path(__file__).resolve().parent.parent / "docs"

BUCKETS = {
    "open": ("Open — planned or on hold",
             "Live plans. The work is not done; the doc is the specification."),
    "design": ("Design and framing",
               "Decisions and models. Cited from source where a comment cannot "
               "restate them."),
    "findings": ("Findings and audits",
                 "Evidence. What was measured, when, and against what."),
    "shipped": ("Shipped — kept because the code cites them",
                "The work is done and these are NOT archive candidates: source "
                "comments point here for the reasoning behind decisions."),
    "unmarked": ("Guides, references and unmarked",
                 "Includes the user-facing guides. Anything here without a "
                 "Status line is a candidate for gaining one."),
}
ORDER = ("open", "design", "findings", "shipped", "unmarked")


def _status(path: pathlib.Path) -> str:
    for line in path.read_text(errors="replace").splitlines()[:40]:
        m = re.match(r"\*\*Status[:*\s]*(.*)", line, re.I)
        if m:
            return re.sub(r"\*\*|\*", "", m.group(1)).strip().rstrip(".")[:96]
    return ""


def _bucket(status: str) -> str:
    low = status.lower()
    if not low:
        return "unmarked"
    # "not yet built" contains "built": say what is not built before testing for what is.
    if ("implemented" in low or not re.search(r"\bnot (yet )?built\b", low)) and any(
            w in low for w in ("implemented", "built", "shipped", "complete and", "done")):
        return "shipped"
    if any(w in low for w in ("not built", "not yet built", "not started",
                              "planned", "on hold", "ready to execute")):
        return "open"
    if any(w in low for w in ("findings", "measurement", "observed", "audit")):
        return "findings"
    return "design"


def _body() -> str:
    rows = []
    for path in sorted(DOCS.glob("*.md")):
        if path.name == "README.md":
            continue
        date = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(path)],
            capture_output=True, text=True, cwd=DOCS,
        ).stdout.strip()
        status = _status(path)
        rows.append((_bucket(status), path.name, date, status))

    out = []
    for key in ORDER:
        group = [r for r in rows if r[0] == key]
        if not group:
            continue
        head, blurb = BUCKETS[key]
        out.append(f"\n## {head}\n\n{blurb}\n")
        for _, name, date, status in sorted(group, key=lambda r: r[1].lower()):
            line = f"- **[{name}]({name})** — {date}"
            if status:
                line += f"  \n  {status}"
            out.append(line)
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the index is stale, instead of writing")
    args = ap.parse_args()

    readme = DOCS / "README.md"
    existing = readme.read_text() if readme.exists() else ""
    header = existing.split("\n## ", 1)[0] if "\n## " in existing else existing
    # The header is carried over verbatim, so a hand-written count in it goes
    # stale silently — which is the exact failure this script exists to prevent,
    # and it had: the header claimed 69 while the directory held 82. Substitute
    # the real number rather than trusting prose.
    n_docs = len([p for p in DOCS.glob("*.md") if p.name != "README.md"])
    header = re.sub(r"^\d+ documents,", f"{n_docs} documents,", header, flags=re.M)
    updated = header.rstrip("\n") + "\n" + _body()

    if args.check:
        if existing != updated:
            print("docs/README.md is out of date — run scripts/regen_docs_index.py",
                  file=sys.stderr)
            return 1
        return 0
    readme.write_text(updated)
    print(f"wrote {readme.relative_to(DOCS.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
