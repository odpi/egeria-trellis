"""Tests for parsers, DataPrep, and IngestionPipeline dispatch."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from resource_explorer.configdata.collection_config import COLLECTION_TYPES
from resource_explorer.ingestion.code_parser import CodeChunk, CodeParser
from resource_explorer.ingestion.doc_parser import DocChunk, DocParser
from resource_explorer.ingestion.api_parser import APIChunk, APIParser
from resource_explorer.ingestion.data_prep import DataPrep


# ── CodeParser ────────────────────────────────────────────────────────────────

class TestCodeParser:
    def test_parses_python_file(self):
        parser = CodeParser(chunk_size=20, chunk_overlap=2)
        content = "def hello():\n    return 'world'\n\ndef foo():\n    pass\n"
        chunks = parser.parse("src/hello.py", content, "myproj")
        assert len(chunks) >= 1
        assert all(isinstance(c, CodeChunk) for c in chunks)
        assert all(c.metadata["language"] == "python" for c in chunks)
        assert all(c.metadata["project_slug"] == "myproj" for c in chunks)

    def test_detects_language_from_extension(self):
        parser = CodeParser()
        for ext, lang in [(".js", "javascript"), (".ts", "typescript"),
                          (".java", "java"), (".go", "go")]:
            chunks = parser.parse(f"file{ext}", "x = 1", "proj")
            assert chunks[0].metadata["language"] == lang

    def test_unknown_extension_uses_text(self):
        parser = CodeParser()
        chunks = parser.parse("file.xyz", "hello world", "proj")
        assert chunks[0].metadata["language"] == "text"

    def test_empty_content_returns_no_chunks(self):
        parser = CodeParser()
        assert parser.parse("file.py", "", "proj") == []

    def test_chunk_overlap_produces_sliding_window(self):
        parser = CodeParser(chunk_size=4, chunk_overlap=2)
        words = " ".join([f"w{i}" for i in range(10)])
        chunks = parser._fixed_window(words)
        # With step=2, 10 words → multiple overlapping chunks
        assert len(chunks) > 1
        # First chunk of second window overlaps with end of first
        first_words = chunks[0].split()
        second_words = chunks[1].split()
        assert second_words[0] in first_words  # overlap

    def test_includes_chunk_index_in_metadata(self):
        parser = CodeParser(chunk_size=5, chunk_overlap=0)
        content = " ".join([f"word{i}" for i in range(15)])
        chunks = parser.parse("file.py", content, "proj")
        indices = [c.metadata["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))


# ── DocParser ─────────────────────────────────────────────────────────────────

class TestDocParser:
    def test_parse_markdown_splits_on_headings(self):
        parser = DocParser(chunk_size=100, chunk_overlap=0)
        md = "# Introduction\nSome intro text.\n\n## Usage\nHow to use it.\n\n## API\nAPI docs."
        chunks = parser.parse_markdown(md, "README.md", "myproj")
        assert len(chunks) >= 3  # one per heading section
        texts = " ".join(c.text for c in chunks)
        assert "Introduction" in texts
        assert "Usage" in texts

    def test_parse_markdown_metadata(self):
        parser = DocParser()
        chunks = parser.parse_markdown("# Hello\nworld", "docs/guide.md", "proj")
        assert all(c.metadata["file_path"] == "docs/guide.md" for c in chunks)
        assert all(c.metadata["project_slug"] == "proj" for c in chunks)
        assert all(c.metadata["type"] == "markdown" for c in chunks)

    def test_empty_markdown_returns_no_chunks(self):
        parser = DocParser()
        assert parser.parse_markdown("", "file.md", "proj") == []


# ── APIParser ─────────────────────────────────────────────────────────────────

class TestAPIParser:
    def test_parses_openapi_spec(self):
        parser = APIParser()
        spec = {
            "paths": {
                "/users": {
                    "get": {
                        "summary": "List all users",
                        "description": "Returns a list of users",
                        "parameters": [{"name": "limit"}, {"name": "offset"}],
                    },
                    "post": {"summary": "Create a user", "description": ""},
                }
            }
        }
        import yaml
        content = yaml.dump(spec)
        chunks = parser.parse("openapi.yaml", content, "myapi")
        assert len(chunks) == 2
        get_chunk = next(c for c in chunks if "GET" in c.text)
        assert "List all users" in get_chunk.text
        assert "limit" in get_chunk.text
        assert get_chunk.metadata["endpoint"] == "/users"
        assert get_chunk.metadata["method"] == "get"

    def test_invalid_yaml_returns_empty(self):
        parser = APIParser()
        chunks = parser.parse("openapi.yaml", "not: valid: yaml: [[[", "proj")
        assert chunks == []

    def test_non_openapi_yaml_returns_empty(self):
        parser = APIParser()
        chunks = parser.parse("config.yaml", "key: value\nother: thing", "proj")
        assert chunks == []  # no "paths" key


# ── DataPrep ──────────────────────────────────────────────────────────────────

class TestDataPrep:
    def test_filters_short_chunks(self):
        prep = DataPrep(min_chars=50)
        chunks = [
            CodeChunk(text="x", metadata={}),
            CodeChunk(text="a" * 60, metadata={}),
        ]
        result = prep.filter(chunks)
        assert len(result) == 1
        assert result[0].text == "a" * 60

    def test_deduplicates_identical_chunks(self):
        prep = DataPrep(min_chars=0)
        text = "this is a chunk of content"
        chunks = [
            CodeChunk(text=text, metadata={"i": 1}),
            CodeChunk(text=text, metadata={"i": 2}),
        ]
        result = prep.filter(chunks)
        assert len(result) == 1

    def test_reset_dedup_clears_hash_set(self):
        prep = DataPrep(min_chars=0)
        text = "a" * 60
        chunks = [CodeChunk(text=text, metadata={})]
        prep.filter(chunks)
        assert len(prep.filter(chunks)) == 0  # duplicate
        prep.reset_dedup()
        assert len(prep.filter(chunks)) == 1  # fresh state

    def test_filters_boilerplate(self):
        prep = DataPrep(min_chars=0)
        boilerplate = CodeChunk(
            text="# auto-generated\nThis file was auto-generated. Do not edit.",
            metadata={},
        )
        normal = CodeChunk(text="def main():\n    print('hello')", metadata={})
        result = prep.filter([boilerplate, normal])
        assert len(result) == 1
        assert result[0] == normal


# ── IngestionPipeline dispatch ────────────────────────────────────────────────

class TestIngestionPipelineDispatch:
    """Verify that _ingest_collection routes to the right parser without hitting GitHub."""

    @pytest.fixture
    def pipeline(self, tmp_path):
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        from resource_explorer.registry import ProjectRegistry
        from resource_explorer.vector_store_pg import MultiCollectionStore

        from resource_explorer.registry import Project

        pipeline = IngestionPipeline.__new__(IngestionPipeline)
        pipeline.registry = ProjectRegistry(db_path=str(tmp_path / "test.db"))
        # 'proj' has to actually exist: _ingest_code writes project_code_symbols,
        # which has a real FK to projects.slug. The test passed only because
        # SQLite ignored that FK by default — on Postgres this same call has
        # always been an IntegrityError. Registering it here makes the fixture
        # match what production guarantees, rather than what SQLite tolerated.
        pipeline.registry.add(Project(
            slug="proj", display_name="Proj",
            github_url="https://github.com/test/proj", collections=[]))
        pipeline.store = MagicMock()
        pipeline.store.insert.return_value = 5
        pipeline.console = MagicMock()
        return pipeline

    def _make_local_root(self, files: dict[str, str], tmp_path) -> "Path":
        """Write files to a temp directory and return the root path."""
        from pathlib import Path
        for rel_path, content in files.items():
            p = tmp_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return tmp_path

    def test_python_code_uses_code_parser(self, pipeline, tmp_path):
        local_root = self._make_local_root({"src/main.py": "def hello(): pass\n" * 20}, tmp_path)
        ctype = COLLECTION_TYPES["python_code"]
        chunks = pipeline._ingest_code(local_root, "proj", ctype)
        assert len(chunks) >= 1
        assert all(c.metadata["language"] == "python" for c in chunks)

    def test_markdown_uses_doc_parser(self, pipeline, tmp_path):
        local_root = self._make_local_root({"README.md": "# Title\nContent here.\n" * 5}, tmp_path)
        ctype = COLLECTION_TYPES["markdown_docs"]
        chunks = pipeline._ingest_markdown(local_root, "proj", ctype)
        assert len(chunks) >= 1
        assert all(c.metadata["type"] == "markdown" for c in chunks)

    def test_api_reference_uses_api_parser(self, pipeline, tmp_path):
        import yaml
        spec = {"paths": {"/items": {"get": {"summary": "List items", "description": ""}}}}
        local_root = self._make_local_root({"openapi.yaml": yaml.dump(spec)}, tmp_path)
        ctype = COLLECTION_TYPES["api_reference"]
        chunks = pipeline._ingest_api_specs(local_root, "proj", ctype)
        assert len(chunks) >= 1
        assert "GET" in chunks[0].text

    def test_release_notes_uses_github_releases(self, pipeline):
        repo = MagicMock()
        release = MagicMock()
        release.tag_name = "v1.0.0"
        release.body = "Initial release. Fixed bugs. Added features.\n" * 10
        release.published_at = MagicMock()
        release.published_at.isoformat.return_value = "2024-01-01T00:00:00"
        release.published_at.strftime.return_value = "2024-01-01"
        repo.get_releases.return_value = [release]
        ctype = COLLECTION_TYPES["release_notes"]
        chunks = pipeline._ingest_releases(repo, "proj", ctype)
        assert len(chunks) >= 1
        assert any("v1.0.0" in c.text for c in chunks)

    def test_unknown_collection_type_returns_zero(self, pipeline):
        from resource_explorer.configdata.collection_config import CollectionType
        fake_ctype = CollectionType(
            name="unknown_type", description="", file_extensions=[".xyz"],
            chunk_size=100, chunk_overlap=10, min_file_count=1,
        )
        # Early-return guard fires before any GitHub API call
        result = pipeline._ingest_collection(
            "https://github.com/a/b", "proj", "proj_unknown_type", fake_ctype
        )
        assert result == 0
