"""One rule for "is this the repository's own code?" — ingestion/vendored.py —
and the places it must be read.

Before 2026-09-11 the rule was private to the line census, and on
egeria-workspaces 68% of the inventory, 77% of the symbols and 99% of the
JavaScript chunks were node_modules/typescript. These pin: the rule itself;
that the inventory RECORDS provenance rather than dropping files; that
readers exclude vendored rows by default; and that the symbol/chunk walk
skips them.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from resource_explorer.ingestion.vendored import (GENERATED, GENERATED_DIRS, OWN, SKIPPED_DIRS, VENDORED,
                                                  VENDORED_DIRS, is_vendored, is_vendored_abs, provenance)
from resource_explorer.registry import Project, ProjectRegistry


class TestTheRule:
    def test_any_directory_segment_counts_and_the_filename_never_does(self):
        assert is_vendored("node_modules/typescript/lib/typescript.js")
        assert is_vendored("plugins/x/node_modules/a.js")
        assert is_vendored("site-packages/requests/api.py")
        assert not is_vendored("src/vendor.py")          # a FILE named vendor
        assert not is_vendored("src/app/main.py")
        assert not is_vendored("README.md")

    def test_the_line_census_reads_the_same_set(self):
        from resource_explorer.ingestion import line_census
        assert line_census._EXCLUDED_DIRS is SKIPPED_DIRS

    def test_abs_form_is_relative_to_the_walk_root(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        f = tmp_path / "node_modules" / "x.js"; f.write_text("")
        assert is_vendored_abs(f, tmp_path)
        assert not is_vendored_abs(tmp_path / "x.js", tmp_path)

    def test_a_path_outside_the_root_is_a_caller_bug_not_a_no(self, tmp_path):
        """It failed open: the extra-docs PDF walk passed the clone root for
        a directory outside it and every file came back "not vendored"."""
        with pytest.raises(ValueError):
            is_vendored_abs(tmp_path / "elsewhere" / "node_modules" / "x.js", tmp_path / "clone")


class TestVendoredIsNotGenerated:
    """Two claims, two sets (review, 2026-09-12): vendored is provenance --
    somebody else's code checked in -- and generated is the repository's own
    build output. Every walk skips both; the inventory records which."""

    def test_the_sets_are_disjoint_and_together_are_what_walks_skip(self):
        assert not (VENDORED_DIRS & GENERATED_DIRS)
        assert SKIPPED_DIRS == VENDORED_DIRS | GENERATED_DIRS
        assert "node_modules" in VENDORED_DIRS and "dist" in GENERATED_DIRS

    def test_provenance_names_the_kind(self):
        assert provenance("node_modules/x/y.js") == VENDORED
        assert provenance("dist/bundle.js") == GENERATED
        assert provenance("src/app.py") == OWN
        assert provenance("vendor/lib/build/out.js") == VENDORED   # somebody else's build output is still theirs
        assert provenance("build/vendor.js") == GENERATED           # a FILE named vendor under our build dir
        assert is_vendored("dist/x") and is_vendored("vendor/x") and not is_vendored("src/x")

    def test_the_summary_and_the_rail_say_which(self, db):
        from resource_explorer.members import inventory_sentence
        db.upsert_file_inventory("p", [("src/a.py", 1), ("node_modules/t/b.js", 1), ("node_modules/t/c.js", 1), ("dist/d.js", 1)])
        inv = db.file_inventory_summary("p")
        assert (inv["total"], inv["own"], inv["vendored"], inv["generated"]) == (4, 1, 2, 1)
        assert inv["short"] == "4 files · 2 vendored · 1 generated"
        assert inventory_sentence(inv) == "1 of 4 files are this repository's own; 2 vendored and 1 generated, not counted."
        # the other branch: an inventory that predates the flag
        assert inventory_sentence({"total": 9, "own": 9, "vendored": 0, "generated": 0, "indexed_at": "2026-09-01T00:00:00"}) == (
            "9 files. Indexed 2026-09-01; an index before 2026-09-11 counts vendored code as the repository's own until re-indexed.")
        # and the readers still see only own code
        assert db.get_file_inventory("p") == ["src/a.py"]


class TestFoldersAreJudgedByTheirOwnName:
    """One `src/x/node_modules/y.js` marked all of `src` not worthy ·
    vendored. A folder that contains vendored code is still ours."""

    def test_a_folder_containing_vendored_code_is_not_itself_vendored(self):
        from resource_explorer.surveyors.sub_surveyors import sub_resource_survey as srs
        src = (srs.__file__ and open(srs.__file__, encoding="utf-8").read())
        assert "vendored_tops" not in src
        assert "if folder in VENDORED_DIRS" in src and "elif folder in GENERATED_DIRS" in src



@pytest.fixture
def db(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


class TestInventoryRecordsProvenance:
    def test_vendored_files_are_kept_marked_and_excluded_from_readers(self, db):
        db.upsert_file_inventory("p", [("src/a.py", 10), ("node_modules/t.js", 99), ("docs/x.md", 5)])
        # recorded, not dropped
        inv = db.file_inventory_summary("p")
        assert (inv["total"], inv["own"], inv["vendored"], inv["generated"]) == (3, 2, 1, 0)
        # readers see the repository's own files by default
        assert sorted(db.get_file_inventory("p")) == ["docs/x.md", "src/a.py"]

    def test_a_pre_migration_row_reads_as_own_until_reindexed(self, db):
        """DEFAULT 0 means an old inventory is unchanged by the migration —
        and the summary's indexed_at is how a reader tells old from new."""
        db.upsert_file_inventory("p", [("src/a.py", 10)])
        with db._conn() as conn:
            conn.execute("UPDATE project_file_inventory SET vendored = NULL WHERE project_slug = 'p'")
        assert db.get_file_inventory("p") == ["src/a.py"]
        assert db.file_inventory_summary("p")["vendored"] == 0


class TestTheWalks:
    def test_local_files_skips_vendored_paths(self, tmp_path):
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        (tmp_path / "src").mkdir(); (tmp_path / "node_modules" / "lib").mkdir(parents=True)
        (tmp_path / "src" / "own.py").write_text("x = 1\n")
        (tmp_path / "node_modules" / "lib" / "theirs.py").write_text("y = 2\n")
        pipe = IngestionPipeline.__new__(IngestionPipeline)   # no store, no registry: the walk is pure
        got = dict(pipe._local_files(tmp_path, [".py"]))
        assert list(got) == ["src/own.py"]


class TestSubResourceSurveyStillListsVendored:
    def test_a_vendored_folder_is_listed_not_worthy_with_the_reason(self, db):
        """The one reader that must see vendored rows: a sub-resource survey
        that omitted node_modules would make "34 sub-resources" silently
        mean "34 of 41". Listed, and the reason is the honest one."""
        from resource_explorer.surveyors.sub_surveyors.sub_resource_survey import SubResourceSurveyor
        db.upsert_file_inventory("p", [(f"node_modules/lib/f{i}.js", 1) for i in range(12)]
                                 + [(f"src/m{i}.py", 1) for i in range(12)])
        rows = db.get_file_inventory_with_sizes("p", include_vendored=True)
        assert {r["file_path"].split("/")[0]: r["vendored"] for r in rows} == {"node_modules": True, "src": False}
        # and the default reader still hides it from everyone else
        assert all(not r["file_path"].startswith("node_modules") for r in db.get_file_inventory_with_sizes("p"))


class TestEveryWalkReadsTheRule:
    """PR #36 fixed the pipeline's walks and missed the survey step's
    duplicate of one of them, which put 20,654 vendored symbols back one
    survey later. A walk that measures the repository's content and does
    not consult the rule is the shape of that regression.

    The first version of this guard (#39) was two regex literals and a
    file-level substring test: a file with any walk had to mention
    `is_vendored` somewhere. Reviewed 2026-09-12, it missed exactly the
    shape it was for -- a sixth bare walk appended to pipeline.py, which
    already mentions the rule five times, was invisible -- and it never saw
    `rglob("*.pdf")`, `glob("**/*")`, `Path.walk()`, or anything outside
    surveyors/ and ingestion/ (cli/wizard.py walked a docs directory with
    no rule). This version walks the AST of every module in the package:
    each FUNCTION that contains a tree walk must reach the rule through its
    own call graph, resolved transitively within the module. The allowlist
    is per function and every entry must still name a walk that exists, so
    a rename cannot retire an exemption silently. The recorded failures
    that proved the detector fires are in docs/vendored-guard.md.
    """
    #: (module, function) -> why this walk may skip the rule.
    ALLOWED_WITHOUT = {
        ("ingestion/line_census.py", "census_tree"): "reads VENDORED_DIRS by its old name _EXCLUDED_DIRS (pinned by test_line_census_reads_the_same_rule)",
        ("ingestion/dependency_parser.py", "parse"): "manifest walk; excludes vendor/node_modules ad hoc per manifest kind",
        ("ingestion/pipeline.py", "_store_file_inventory"): "records EVERY file on purpose -- provenance, not exclusion; registry.upsert_file_inventory stamps the flag",
        ("surveyors/arch_recovery/exclusion.py", "_walk"): "IS the arch-recovery exclusion rule",
        ("surveyors/arch_recovery/spring_app.py", "_spring_entry_points"): "arch recovery has its own exclusion module",
        ("surveyors/arch_recovery/spring_app.py", "_properties_files"): "arch recovery has its own exclusion module",
        ("surveyors/arch_recovery/spring_app.py", "_style_references"): "arch recovery has its own exclusion module",
        ("github/source_cache.py", "_entries"): "sums the on-disk size of cache entries; not a measurement of repository content",
        ("github/source_cache.py", "_tree_bytes"): "same reason as _entries: sizes ONE freshly acquired artifact for the bytes_fetched cost axis (design 17.2). Excluding vendored directories would understate what was actually downloaded, which is the opposite of what this number is for",
    }
    RULE_NAMES = {"is_vendored", "is_vendored_abs", "VENDORED_DIRS"}

    @staticmethod
    def _is_tree_walk(call: "ast.Call") -> bool:
        import ast
        f = call.func
        if not isinstance(f, ast.Attribute):
            return False
        if f.attr == "rglob":
            return True
        if f.attr == "walk":
            # os.walk(root) and Path.walk(). tree-sitter's node.walk() /
            # tree.walk() is a cursor over a syntax tree, not a filesystem:
            # a no-argument .walk() on a receiver not named like a path is
            # left alone. (`root.walk()` and `path.walk()` still count.)
            if isinstance(f.value, ast.Name) and f.value.id == "os":
                return True
            if isinstance(f.value, ast.Name) and f.value.id == "ast":
                return False        # Python's own ast.walk(node): a syntax tree
            if call.args:
                return True
            recv = f.value.id if isinstance(f.value, ast.Name) else getattr(f.value, "attr", "")
            return any(k in recv.lower() for k in ("path", "root", "dir"))
        if f.attr == "glob":
            return any(isinstance(a, ast.Constant) and isinstance(a.value, str) and "**" in a.value
                       for a in call.args)
        return False

    @classmethod
    def _functions_with_walks(cls, tree: "ast.Module"):
        """Yield (name, walks, callees, reads_rule) per function. Nested
        functions are folded into their enclosing function -- a walk inside
        a local helper is the enclosing function's walk."""
        import ast
        for node in tree.body:
            fns = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fns.append(node)
            elif isinstance(node, ast.ClassDef):
                fns.extend(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            for fn in fns:
                walks, callees, reads_rule = [], set(), False
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.Call):
                        if cls._is_tree_walk(sub):
                            walks.append(sub.lineno)
                        f = sub.func
                        if isinstance(f, ast.Name):
                            callees.add(f.id)
                        elif isinstance(f, ast.Attribute):
                            callees.add(f.attr)     # self._helper(...) -> _helper
                    if isinstance(sub, ast.Name) and sub.id in cls.RULE_NAMES:
                        reads_rule = True
                    if isinstance(sub, ast.Attribute) and sub.attr in cls.RULE_NAMES:
                        reads_rule = True
                yield fn.name, walks, callees, reads_rule

    @classmethod
    def _scan(cls, root):
        """Return (offenders, seen): offenders are (module, function,
        first_walk_line) that contain a walk and never reach the rule; seen
        is every (module, function) that contains a walk."""
        import ast
        offenders, seen = [], set()
        for f in sorted(root.rglob("*.py")):
            rel = str(f.relative_to(root))
            tree = ast.parse(f.read_text(encoding="utf-8"))
            fns = {name: (walks, callees, reads) for name, walks, callees, reads in cls._functions_with_walks(tree)}

            def reaches(name, stack=()):
                # transitive closure within the module: a function reaches
                # the rule if it reads it or calls a same-module function that does
                walks, callees, reads = fns[name]
                if reads:
                    return True
                return any(c in fns and c not in stack and reaches(c, stack + (name,)) for c in callees)

            for name, (walks, callees, reads) in fns.items():
                if not walks:
                    continue
                seen.add((rel, name))
                if (rel, name) in cls.ALLOWED_WITHOUT:
                    continue
                if not reaches(name):
                    offenders.append((rel, name, walks[0]))
        return offenders, seen

    def test_every_walk_reaches_the_rule(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "resource_explorer"
        offenders, seen = self._scan(root)
        assert offenders == [], (
            "functions that walk a tree and never reach the vendored rule "
            "(module, function, line of first walk): " + ", ".join(f"{m}:{fn}@{ln}" for m, fn, ln in offenders))
        stale = sorted(k for k in self.ALLOWED_WITHOUT if k not in seen)
        assert stale == [], f"allowlist entries that no longer name a walk (renamed or removed?): {stale}"

    def test_the_detector_fires_on_a_bare_walk_in_a_file_that_already_reads_the_rule(self, tmp_path):
        """The shape the first guard missed. One module, one function that
        reads the rule, one that walks without it."""
        (tmp_path / "m.py").write_text(
            "from resource_explorer.ingestion.vendored import is_vendored_abs\n"
            "def good(root):\n"
            "    return [p for p in root.rglob('*') if not is_vendored_abs(p, root)]\n"
            "def helper(root):\n"
            "    return [p for p in root.rglob('*.pdf') if not is_vendored_abs(p, root)]\n"
            "def via_helper(root):\n"
            "    return helper(root)\n"
            "class C:\n"
            "    def bad(self, root):\n"
            "        return list(root.rglob('*.py'))\n"
            "def bad_glob(root):\n"
            "    return list(root.glob('**/*.md'))\n"
            "def fine_glob(root):\n"
            "    return list(root.glob('*.md'))\n"
        )
        offenders, seen = self._scan(tmp_path)
        assert [(m, fn) for m, fn, _ in offenders] == [("m.py", "bad"), ("m.py", "bad_glob")]
        # `seen` is "contains a walk": via_helper reaches the rule but has no
        # walk of its own, and a single-level glob is not a tree walk
        assert seen == {("m.py", "good"), ("m.py", "helper"), ("m.py", "bad"), ("m.py", "bad_glob")}

    def test_the_survey_symbol_walk_skips_vendored(self, tmp_path):
        from resource_explorer.surveyors.sub_surveyors.symbol_extraction import _local_files
        (tmp_path / "src").mkdir(); (tmp_path / "node_modules" / "t").mkdir(parents=True)
        (tmp_path / "src" / "own.py").write_text("x = 1\n")
        (tmp_path / "node_modules" / "t" / "theirs.py").write_text("y = 2\n")
        assert [p for p, _ in _local_files(tmp_path, [".py"])] == ["src/own.py"]


class TestExtraDocsPaths:
    """`_local_files_for_paths` is the reader for extra_docs_paths. Its
    directory branch referenced `local_root`, unbound in that method -- a
    NameError on any extra docs DIRECTORY, shipped by #36 with no test on
    the branch (found in the designer's 2026-09-12 review). An extra path
    lives outside the clone, so vendored-ness is relative to the extra
    directory itself, not to local_root; against local_root the check
    failed open and excluded nothing."""

    def test_a_directory_entry_is_read_and_its_vendored_files_skipped(self, tmp_path):
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        docs = tmp_path / "elsewhere" / "docs"
        (docs / "guide").mkdir(parents=True)
        (docs / "node_modules" / "x").mkdir(parents=True)
        (docs / "guide" / "a.md").write_text("# own\n")
        (docs / "node_modules" / "x" / "b.md").write_text("# theirs\n")
        (docs / "skip.txt").write_text("not a doc\n")
        got = IngestionPipeline._local_files_for_paths(
            object(), [("docs", docs)], [".md"])
        assert got == [("docs/guide/a.md", "# own\n")]

    def test_a_file_entry_still_works(self, tmp_path):
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        f = tmp_path / "README.md"; f.write_text("hi\n")
        assert IngestionPipeline._local_files_for_paths(object(), [("readme", f)], [".md"]) == [("readme", "hi\n")]


class TestDocumentationCoverageSaysWhatItCounted:
    """"40.4% of the public API documented" over a mostly-TypeScript repo
    reads as a claim about the repository; only Python and Java are
    measured for docstrings. The headline now says so (review, 2026-09-12)."""

    def test_the_headline_names_the_languages_and_the_count(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _coverage_clause
        c = {"label": "40.4%", "summary": "392 of 971 public symbols carry a docstring (40.4%), measured over java, python. Not counted for javascript — ..."}
        assert _coverage_clause(c) == "40.4% of public Java and Python symbols carry a docstring (971 measured; other languages not measured)"

    def test_an_older_summary_shape_falls_back_rather_than_inventing_a_scope(self):
        from resource_explorer.surveyors.repo_survey_definition_adapter import _coverage_clause
        assert _coverage_clause({"label": "12%", "summary": "something else"}) == "12% of the public API documented"
