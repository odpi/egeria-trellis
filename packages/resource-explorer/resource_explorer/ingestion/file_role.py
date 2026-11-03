"""What part a file plays in its repository: the one classifier behind
`file_role` on `project_code_symbols` and `project_code_markers`.

DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md §2-§3: the Artifacts lists filter by
role ("54 routes · under testgen/ · 2,373 in tests/ left out"), the role is
set at extraction so the lists never re-read the tree, and the classifier is
defined once. The ingestion-rules design is to use this same function.

The roles, in the order they are decided (the first that matches wins):

- ``vendored``  somebody else's code checked in (`vendored.VENDORED_DIRS`);
- ``generated`` this repository's own output: build and tool directories
  (`vendored.GENERATED_DIRS`), the Maven/Gradle generator output roots
  (``generated-sources``, ``generated-test-sources``, ``generated-src``), and
  files whose name says a generator wrote them (``*_pb2.py``, ``*.pb.go``,
  ``*.min.js``, ``*.generated.ts``). A directory merely called ``generated``
  is NOT enough: hand-written code lives in such packages too (review,
  2026-10-15), so without a file-name marker it stays ``core``;
- ``test``      `test_paths.is_test_path` decides, unchanged: there is one
  rule for "is this a test" and this module calls it rather than copying it;
- ``docs``      under a documentation directory (``docs/``, ``doc/``,
  ``documentation/``) that is not inside a source root (``src/``), so a Java
  package called ``doc`` stays code;
- ``core``      everything else: the repository's own product code.

Vendored is decided before test because somebody else's tests are still
somebody else's code. Directory segments and file-name shapes only; no
content is read.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from resource_explorer.ingestion.test_paths import is_test_path
from resource_explorer.ingestion.vendored import GENERATED, VENDORED, provenance

CORE = "core"
TEST = "test"
GENERATED_ROLE = "generated"
VENDORED_ROLE = "vendored"
DOCS = "docs"

#: Every value `file_role` can hold. NULL in the column is none of these: it
#: means the row was extracted before the column existed ("not recorded").
FILE_ROLES = (CORE, TEST, GENERATED_ROLE, VENDORED_ROLE, DOCS)

#: Generator OUTPUT ROOTS named by the build tools themselves, beyond the
#: build/tool set `vendored.GENERATED_DIRS` already names. Deliberately not a
#: bare `generated`: that is also a hand-written package name.
GENERATED_SOURCE_DIRS = frozenset({"generated-sources", "generated-test-sources", "generated-src"})

_GENERATED_FILE_RES = (
    re.compile(r"^.+_pb2(_grpc)?\.pyi?$"),     # protoc, Python
    re.compile(r"^.+\.pb(\.gw)?\.go$"),         # protoc, Go
    re.compile(r"^.+_(generated|gen)\.go$"),    # go generate convention
    re.compile(r"^.+\.generated\.[A-Za-z]+$"),  # *.generated.ts, *.generated.cs
    re.compile(r"^.+\.min\.(js|css)$"),         # minified bundles
)

DOC_DIRS = frozenset({"docs", "doc", "documentation"})
_SOURCE_ROOTS = frozenset({"src"})


def _parts(rel: str) -> list[str]:
    return [p for p in str(rel).replace("\\", "/").split("/") if p and p != "."]


def classify_file_role(rel: str) -> str:
    """One of FILE_ROLES for a path relative to the repository root."""
    parts = _parts(rel)
    if not parts:
        return CORE
    kind = provenance(PurePosixPath(*parts))
    if kind == VENDORED:
        return VENDORED_ROLE
    if kind == GENERATED:
        return GENERATED_ROLE
    dirs = [p.lower() for p in parts[:-1]]
    if any(d in GENERATED_SOURCE_DIRS for d in dirs) or any(r.match(parts[-1]) for r in _GENERATED_FILE_RES):
        return GENERATED_ROLE
    if is_test_path("/".join(parts)):
        return TEST
    for d in dirs:
        if d in _SOURCE_ROOTS:
            break
        if d in DOC_DIRS:
            return DOCS
    return CORE
