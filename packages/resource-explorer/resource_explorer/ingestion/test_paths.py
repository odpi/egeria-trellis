"""One rule for "is this file a test", shared by anything that must not read a
test's mock registrations as the product's own surface.

Why it exists: dataops_testgen held 2,438 route markers and only 54 were real
routes; 2,373 sat under ``tests/`` and were mostly ``patch("...")`` calls read
as FastAPI ``@app.patch`` registrations. Route and message markers are facts
about what the product EXPOSES, so a test file contributes none.

The rule (directory segments and file-name shapes only; no content is read):

* Distinctive directory names count anywhere, case-insensitively: ``tests``,
  ``__tests__``, ``__test__``, ``testdata``, ``e2e``, ``cypress``,
  ``integrationTest``, ``testFixtures``.
* Ambiguous names count only in conventional positions: ``test`` at the repo
  root, directly under ``src`` (Maven/Gradle), or directly beneath a top-level
  module (``module/test``, a package's tests sibling); ``it`` only under
  ``src`` (``src/it``). ``app/lib/test/`` is product code.
* File names, case-insensitive where the convention allows: ``test_*.py``,
  ``*_test.py``/``*_test.go``, ``conftest.py``, ``tests.py`` (Django),
  ``*.test|spec.js|ts``. Java/Kotlin/Scala: ``*Test``, ``*Tests``,
  ``*TestCase``, ``*TestIT`` with a capital T, case-sensitive, so
  ``TestimonialController.java``, ``Contest.java`` and ``BusinessUnIT.java``
  are product code. A ``Test*.java`` helper is a test only through its directory.

(The Spring detector's ``_TEST_PATH_SEGMENTS`` and admission's ``FIXTURE_DIRS``
answer narrower questions about compose files and resources and are left alone.)
"""
from __future__ import annotations

import re

_ANYWHERE = frozenset({"tests", "__tests__", "__test__", "testdata", "e2e", "cypress",
                       "integrationtest", "testfixtures"})

_TEST_FILE_RES = (
    re.compile(r"^test_.+\.py$", re.I),
    re.compile(r"^.+_tests?\.(py|go)$", re.I),
    re.compile(r"^(conftest|tests)\.py$", re.I),
    re.compile(r"^.+\.(test|spec)\.[cm]?[jt]sx?$", re.I),
)
_JVM_TEST_FILE = re.compile(r"^.+(Test|Tests|TestCase|TestIT)\.(java|kt|scala)$")


def is_test_path(rel: str) -> bool:
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if not parts:
        return False
    dirs = [d.lower() for d in parts[:-1]]
    for i, d in enumerate(dirs):
        if d in _ANYWHERE:
            return True
        if d == "test" and (i <= 1 or dirs[i - 1] == "src"):
            return True
        if d == "it" and i > 0 and dirs[i - 1] == "src":
            return True
    name = parts[-1]
    return bool(any(r.match(name) for r in _TEST_FILE_RES) or _JVM_TEST_FILE.match(name))
