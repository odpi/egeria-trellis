"""resource_type inference in survey_definition_docs.py.

Was hardcoded "repo" at the route level (survey_definitions.py's
list_definitions()) regardless of what a document actually was — harmless
while every authored document happened to be repo-scoped, but would have
silently mislabeled the first database/filesystem Survey Definition ever
authored. Now read from the source filename's
`{resource_type}-survey-definition-*.md` convention.
"""
from __future__ import annotations

from pathlib import Path

from resource_explorer.surveyors import survey_definition_docs as D

_MINIMAL_DOC = """\
## Create Governance Action Process
### Qualified Name
GovActionProcess::X

## Create Governance Action Process Step
### Qualified Name
GovActionProcessStep::X::step_one
"""


def _write(tmp_path: Path, filename: str) -> Path:
    directory = tmp_path / "survey-definitions"
    directory.mkdir(exist_ok=True)
    path = directory / filename
    path.write_text(_MINIMAL_DOC)
    return directory


class TestResourceTypeFromFilename:
    def test_repo_prefix(self):
        assert D._resource_type_from_filename(Path("repo-survey-definition-full.md")) == "repo"

    def test_database_prefix(self):
        assert D._resource_type_from_filename(Path("database-survey-definition-full.md")) == "database"

    def test_filesystem_prefix(self):
        assert D._resource_type_from_filename(Path("filesystem-survey-definition-full.md")) == "filesystem"

    def test_unrecognized_prefix_falls_back_to_repo(self):
        """Matches the dataclass field's own default — a document following
        some other naming scheme entirely is not evidence it's a NEW
        resource type, just an unrecognized one."""
        assert D._resource_type_from_filename(Path("something-else.md")) == "repo"


class TestDocumentedDefinitionsSetsResourceType:
    def test_repo_document_gets_repo(self, tmp_path):
        directory = _write(tmp_path, "repo-survey-definition-x.md")
        docs = D.documented_definitions(directory)
        assert docs["X"].resource_type == "repo"

    def test_database_document_gets_database(self, tmp_path):
        directory = _write(tmp_path, "database-survey-definition-x.md")
        docs = D.documented_definitions(directory)
        assert docs["X"].resource_type == "database"

    def test_filesystem_document_gets_filesystem(self, tmp_path):
        directory = _write(tmp_path, "filesystem-survey-definition-x.md")
        docs = D.documented_definitions(directory)
        assert docs["X"].resource_type == "filesystem"


class TestExtensionDirectoriesAreMergedWhenNoDirectoryIsGiven:
    """Slice 12: database Survey Definitions live in their own
    `survey-definitions-database/` directory, sibling to the repo batch's
    `survey-definitions/` — one bootstrap batch (canary + reconciler) per
    directory, per `bootstrap.py`'s `BATCH_MANIFEST_FILE` discovery. But
    `documented_definitions()`'s real callers (the reader's questions-scoped
    local-match fast path, the cache warmer, `/api/survey-definitions/
    definitions`) all assume one merged list — `_extension_docs_dirs` finds
    every `survey-definitions-*` sibling and folds it in, but ONLY when no
    explicit `directory` was passed (existing fixture-directory tests, and
    every test above in this file, must keep seeing only what they wrote)."""

    def _write_in(self, directory: Path, filename: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_text(_MINIMAL_DOC.replace("::X", "::" + filename.split("-survey-definition")[0].title()))

    def test_primary_and_extension_directories_are_both_read(self, tmp_path, monkeypatch):
        primary = tmp_path / "survey-definitions"
        extension = tmp_path / "survey-definitions-database"
        self._write_in(primary, "repo-survey-definition-x.md")
        self._write_in(extension, "database-survey-definition-x.md")
        monkeypatch.setattr(D, "definition_docs_dir", lambda: primary)

        docs = D.documented_definitions()

        assert docs["Repo"].resource_type == "repo"
        assert docs["Database"].resource_type == "database"

    def test_an_explicit_directory_argument_is_not_merged_with_extensions(self, tmp_path, monkeypatch):
        """A caller passing its own fixture directory (every test above)
        must see exactly that directory, never a real extension directory
        that happens to sit next to the real `definition_docs_dir()`."""
        primary = tmp_path / "survey-definitions"
        extension = tmp_path / "survey-definitions-database"
        self._write_in(primary, "repo-survey-definition-x.md")
        self._write_in(extension, "database-survey-definition-x.md")
        monkeypatch.setattr(D, "definition_docs_dir", lambda: primary)

        docs = D.documented_definitions(primary)

        assert "Repo" in docs
        assert "Database" not in docs

    def test_no_extension_directories_present_is_unaffected(self, tmp_path, monkeypatch):
        """A deployment with no database Survey Definitions authored yet
        (`survey-definitions-database/` doesn't exist) behaves exactly as
        before this generalization — `_extension_docs_dirs` glob simply
        finds nothing."""
        primary = tmp_path / "survey-definitions"
        self._write_in(primary, "repo-survey-definition-x.md")
        monkeypatch.setattr(D, "definition_docs_dir", lambda: primary)

        docs = D.documented_definitions()

        assert list(docs) == ["Repo"]


class TestDocumentsCarryTheirOwnScoping:
    """The `Link Element To Scope` blocks ARE the ScopedBy relationships —
    they are what publish them — so the document is the source and the graph
    the copy. Asserted against the real docs directory, not a fixture, because
    the count is the check that the parser reads every block: 96 across ten
    documents on 2026-09-11, matching `grep -c 'Link Element To Scope'`."""

    def test_every_documented_definition_declares_its_filters_and_scoping(self):
        from resource_explorer.surveyors.survey_definition_docs import documented_definitions
        docs = documented_definitions()
        assert len(docs) >= 10
        total_links = 0
        for name, doc in docs.items():
            assert doc.technology_type, f"{name}: no supported_technology_type read"
            assert doc.survey_kind, f"{name}: no survey_kind read"
            assert doc.display_name, f"{name}: no Display Name read"
            assert set(doc.step_info) == set(doc.steps), f"{name}: step_info does not cover steps"
            for key, info in doc.step_info.items():
                assert info["qualified_name"].endswith(key)
                assert info["executes_at"], f"{name}/{key}: no executes_at"
            total_links += len(doc.scoped_by)
        # Refresh is the one definition scoped by nothing — by design, it
        # answers no question — and everything else is scoped by at least one.
        assert docs["RepoRefreshSurvey"].scoped_by == []
        assert total_links >= 90, total_links

    def test_a_known_link_is_read_verbatim(self):
        from resource_explorer.surveyors.survey_definition_docs import documented_definitions
        doc = documented_definitions()["RepoAnalysisSurvey"]
        assert "What dependencies does this require?" in doc.scoped_by
        assert doc.technology_type == "Git Repository"
        assert doc.survey_kind == "analysis"
