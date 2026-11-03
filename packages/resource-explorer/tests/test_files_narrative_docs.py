"""Files build step 7: the narrative files the walk found, read in full and chunked by section (F11's long form).

`narrative_docs` reads exactly the files the walk's `folder_narratives` recorded, whole and size-capped, through the walk's
own safe read, and replaces this file system's collection in Resource Explorer's search index. Here the index is a fake
store (no pgvector, no :5442), the registry is a temp SQLite file, and nothing contacts Egeria.

Pinned: the price before the press and its absence words, the section split, a file read in full past the walk's 64 KB
excerpt, every passage a proposal stated by its file, a secret-named file never opened, a file swapped for a link or grown
past the limit not read, the collection replaced whole, the run row and its words, staleness after a newer walk, the
claim, an unreachable mount, nothing written into the path, and the routes.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from resource_explorer.registry import FileSystemEntity, ProjectRegistry
from resource_explorer.surveyors.filesystem import narrative_docs as nd


class FakeStore:
    """The four calls the step makes, in memory."""

    def __init__(self):
        self.collections: dict[str, list[tuple[str, dict]]] = {}
        self.dropped: list[str] = []

    def collection_name(self, slug, ctype):
        return f"{slug}_{ctype}"

    def collection_exists(self, c):
        return c in self.collections

    def drop_collection(self, c):
        self.dropped.append(c)
        self.collections.pop(c, None)

    def insert(self, c, texts, metas):
        self.collections.setdefault(c, []).extend(zip(texts, metas))
        return len(texts)


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(db_path=str(tmp_path / "files7.db"))


@pytest.fixture
def store():
    return FakeStore()


def _register(registry, root, slug="tree"):
    registry.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=str(root)))
    return registry.get_filesystem(slug)


def _tree(tmp_path) -> Path:
    root = tmp_path / "tree"
    (root / "survey").mkdir(parents=True)
    (root / "survey" / "README.md").write_text(
        "# Household survey\n\nAnswers from 2024.\n\n## Variables\n\nage: years\nincome: dollars\n")
    (root / "survey" / "answers.csv").write_text("age,income\n1,2\n")
    (root / "codebook.txt").write_text("Codebook\n========\n\nThe codes.\n\nRegions\n-------\n\nN, S, E, W\n")
    return root


def _walk(registry, fs):
    """RE's own walk with its extensions (which finds the narrative files), stored as the folder survey stores it."""
    from resource_explorer.surveyors.filesystem import walk_extensions as we
    from resource_explorer.surveyors.filesystem.local_filesystem_surveyor import LocalFileSystemSurveyor

    sd = LocalFileSystemSurveyor(fs, registry).run(profile_contents=False, walk_extensions=True)
    we.attach_extensions(fs, registry, sd, led_by="re")
    sd.pop("walk_files", None)
    registry.add_filesystem_survey(fs.slug, sd["surveyed_at"], sd)
    return sd


def _stored(registry, run, entries, rows=None, slug="tree"):
    """A stored walk whose narratives are `entries` (paths relative to the root), as RE writes it."""
    registry.add_filesystem_walk_files(slug, run, rows or [
        {"file_path": e["file"], "folder_path": e["file"].rpartition("/")[0], "folder_depth": e["file"].count("/"),
         "size_bytes": e.get("size", 10), "hash_state": "over_limit"} for e in entries])
    registry.add_filesystem_survey(slug, run, {"surveyed_at": run, "walk_extensions": {"run_id": run},
                                               "folder_narratives": [{"modified_at": run, **e} for e in entries]})


# ── the section split ──

def test_markdown_headings_split_into_sections_with_the_opening_first():
    out = nd.split_sections("Intro line.\n\n# Title\n\nBody one.\n\n## Variables\n\nage\n")
    assert out == [("", "Intro line."), ("Title", "Body one."), ("Variables", "age")]


def test_underlined_titles_split_markdown_setext_and_rst_with_an_overline():
    out = nd.split_sections("=====\nGuide\n=====\n\nAbout it.\n\nUsage\n-----\n\nRun it.\n")
    assert out == [("Guide", "About it."), ("Usage", "Run it.")]


def test_text_with_no_heading_is_one_section():
    assert nd.split_sections("just words\nand more") == [("", "just words\nand more")]


# ── the price, before the press ──

def test_with_no_walk_it_is_not_priced(registry, tmp_path):
    _register(registry, tmp_path)
    p = nd.price(registry, "tree")
    assert p["priced"] is False and p["files"] is None and "no walk has looked" in p["words"]
    v = nd.view(registry, "tree")
    assert v["run"] is None and v["words"] == "not read yet"


def test_a_walk_that_found_no_narrative_says_nothing_to_read_and_the_press_refuses(registry, tmp_path):
    fs = _register(registry, tmp_path)
    _stored(registry, "2026-10-28T09:00:00", [])
    assert "found no narrative file" in nd.price(registry, "tree")["words"]
    with pytest.raises(nd.NothingToRead):
        nd.run_narrative_docs(fs, registry, store_factory=FakeStore)


def test_the_price_names_the_files_and_their_bytes_from_the_walk(registry, tmp_path):
    fs = _register(registry, _tree(tmp_path))
    _walk(registry, fs)
    p = nd.price(registry, "tree")
    assert p["priced"] and p["files"] == 2
    assert p["bytes"] == sum((Path(fs.local_mount_point) / f).stat().st_size for f in ("survey/README.md", "codebook.txt"))
    assert "reads 2 narrative files in full" in p["words"] and "opens no other file" in p["words"]


# ── the run ──

def test_the_run_reads_the_walks_narratives_and_stores_proposals_by_section(registry, tmp_path, store):
    fs = _register(registry, _tree(tmp_path))
    _walk(registry, fs)
    out = nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert out["outcomes"]["read"] == 2 and out["chunks"] == out["stored"] > 0
    rows = store.collections["fs_tree_narratives"]
    assert {m["file_path"] for _, m in rows} == {"survey/README.md", "codebook.txt"}
    assert {m["section"] for _, m in rows if m["file_path"] == "survey/README.md"} == {"Household survey", "Variables"}
    assert {m["section"] for _, m in rows if m["file_path"] == "codebook.txt"} == {"Codebook", "Regions"}
    assert all(m["status"] == "proposal" and m["stated_by"] == f'{m["file_path"]} § {m["section"]}' for _, m in rows)
    assert all(m["resource_type"] == "filesystem" and m["walk_run"] == out["walk_run"] for _, m in rows)
    # the heading leads each passage, so a search for it finds the section
    assert any(t.startswith("Variables\n") and "income" in t for t, _ in rows)
    # the CSV beside the README is not a narrative file: never read here
    assert all(m["file_path"] != "survey/answers.csv" for _, m in rows)


def test_a_file_is_read_in_full_past_the_walks_excerpt(registry, tmp_path, store):
    root = tmp_path / "big"
    root.mkdir()
    tail = "THE-LAST-SECTION-WORD"
    (root / "README.md").write_text("# Start\n\n" + ("word " * 30000) + f"\n\n# End\n\n{tail}\n")
    assert (root / "README.md").stat().st_size > 64 * 1024
    fs = _register(registry, root)
    _stored(registry, "2026-10-28T09:00:00", [{"file": "README.md"}])
    nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert any(tail in t for t, _ in store.collections["fs_tree_narratives"])


def test_a_secret_named_entry_is_never_opened(registry, tmp_path, store, monkeypatch):
    root = tmp_path / "s"
    root.mkdir()
    (root / ".env").write_text("TOKEN=x")
    fs = _register(registry, root)
    _stored(registry, "2026-10-28T09:00:00", [{"file": ".env"}])
    opened = []
    from resource_explorer.surveyors.filesystem import walk_extensions as we

    real = we.read_plain_file
    monkeypatch.setattr(we, "read_plain_file", lambda p, *a, **k: opened.append(p) or real(p, *a, **k))
    out = nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert out["outcomes"]["secret_name"] == 1 and not opened and out["stored"] == 0


def test_a_file_swapped_for_a_link_or_grown_past_the_limit_is_not_read(registry, tmp_path, store, monkeypatch):
    root = tmp_path / "w"
    root.mkdir()
    (root / "target.md").write_text("# secret target\n\nnot to be read")
    os.symlink(root / "target.md", root / "README.md")
    (root / "NOTES.md").write_text("# notes\n\n" + "x " * 400)
    fs = _register(registry, root)
    _stored(registry, "2026-10-28T09:00:00", [{"file": "README.md"}, {"file": "NOTES.md"}])
    monkeypatch.setattr(nd, "_max_bytes", lambda: 100)
    out = nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert out["outcomes"] == {"read": 0, "too_large": 1, "not_a_plain_file": 1, "secret_name": 0, "empty": 0}
    assert out["stored"] == 0
    words = nd.run_words(out)
    assert "0 files read in full" in words and "grown past the size limit since the walk" in words
    assert "no longer a plain file" in words


def test_the_collection_is_replaced_whole_on_each_run(registry, tmp_path, store):
    fs = _register(registry, _tree(tmp_path))
    _walk(registry, fs)
    nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    first = len(store.collections["fs_tree_narratives"])
    (Path(fs.local_mount_point) / "codebook.txt").unlink()
    _walk(registry, fs)
    nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert store.dropped == ["fs_tree_narratives"]
    rows = store.collections["fs_tree_narratives"]
    assert len(rows) < first and {m["file_path"] for _, m in rows} == {"survey/README.md"}


def test_the_run_row_reads_back_in_words_and_goes_stale_after_a_newer_walk(registry, tmp_path, store):
    fs = _register(registry, _tree(tmp_path))
    _walk(registry, fs)
    nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    v = nd.view(registry, "tree")
    assert v["run"]["words"].startswith("2 files read in full") and "search index" in v["run"]["words"]
    assert v["run"]["stale"] is False and "proposal" in v["run"]["stated"]
    assert "not published to Egeria" in v["kept"]
    _stored(registry, "2099-01-01T00:00:00", [{"file": "survey/README.md"}])
    assert nd.view(registry, "tree")["run"]["stale"] is True


def test_nothing_is_written_into_the_path(registry, tmp_path, store):
    fs = _register(registry, _tree(tmp_path))
    _walk(registry, fs)
    root = Path(fs.local_mount_point)
    before = sorted((str(p), p.stat().st_mtime_ns) for p in root.rglob("*"))
    nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert sorted((str(p), p.stat().st_mtime_ns) for p in root.rglob("*")) == before


def test_an_unreachable_mount_and_a_held_claim_each_refuse_and_store_nothing(registry, tmp_path, store):
    fs = _register(registry, tmp_path / "gone")
    _stored(registry, "2026-10-28T09:00:00", [{"file": "README.md"}])
    with pytest.raises(nd.NotReachable):
        nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    (tmp_path / "gone").mkdir()
    assert registry.take_claim(f"{nd.STEP_KEY}:tree", "someone-else")
    with pytest.raises(nd.AlreadyRunning):
        nd.run_narrative_docs(fs, registry, store_factory=lambda: store)
    assert not store.collections and not registry.query_step_runs(slug="tree", step_key=nd.STEP_KEY)


# ── the routes and the card's read ──

@pytest.fixture
def api(registry, monkeypatch):
    from fastapi.testclient import TestClient

    from resource_explorer.auth import create_access_token
    from resource_explorer.web.app import app
    from tests.zone_fakes import open_access

    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", registry.__dict__) or None)
    open_access(monkeypatch, caller_if_none="files7-tester")
    headers = {"Authorization": "Bearer " + create_access_token(user_id="files7-tester", egeria_token="tok")}
    return TestClient(app), headers


@pytest.mark.usefixtures("signed_in_caller")
def test_the_routes_price_refuse_and_run(api, registry, tmp_path, monkeypatch, store):
    client, headers = api
    assert client.get("/api/filesystems/ghost/narrative-docs", headers=headers).status_code == 404
    fs = _register(registry, _tree(tmp_path))
    r = client.post("/api/filesystems/tree/narrative-docs", headers=headers)
    assert r.status_code == 409 and "No walk has looked" in r.json()["detail"]
    _walk(registry, fs)
    assert client.get("/api/filesystems/tree/narrative-docs", headers=headers).json()["price"]["files"] == 2
    monkeypatch.setattr(nd, "default_store", lambda: store)
    r = client.post("/api/filesystems/tree/narrative-docs", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["outcomes"]["read"] == 2 and r.json()["view"]["run"]["stored"] == r.json()["stored"]
    from resource_explorer.folder_survey_view import folder_survey_view

    assert folder_survey_view(registry, "tree")["narrative_docs"]["run"]["collection"] == "fs_tree_narratives"


def test_stated_by_is_the_path_under_the_root_and_the_section_never_the_bare_name():
    assert nd.stated_by_of("docs/ingest/README.md", "Inputs") == "docs/ingest/README.md § Inputs"
    assert nd.stated_by_of("bitol/README.md") == "bitol/README.md"
    assert nd.stated_by_of("bitol/README.md") != nd.stated_by_of("secrets/README.md")
    assert nd.passage_id("run1", "a/README.md", "Inputs") == "run1|a/README.md|Inputs"
    meta = [m for _, m in nd.chunks_for("# T\n\nbody words here\n", rel_path="bitol/README.md", modified_at="", slug="s", run="r1")]
    assert meta and all(m["stated_by"].startswith("bitol/README.md") for m in meta)


def test_a_chat_journal_entry_stores_the_passage_id_of_a_cited_passage():
    from resource_explorer import journal

    class Reg:
        def get_compile(self, cid):
            return {"manifest": {"packed": [{"key": "narratives", "walk_run": "r1", "file_path": "a/README.md",
                                             "section": "Inputs"}], "coverage": {"kind": "some"}}}

    e = journal.derive_turn_entry(Reg(), question="q", answer="a", compile_id="c" * 16, investigation="i", resource_slug="s")
    assert e["turn"]["passages"] == ["r1|a/README.md|Inputs"]
    assert "Passages: r1|a/README.md|Inputs" in e["body"]
    none = journal.derive_turn_entry(Reg(), question="q", answer="a", compile_id=None, investigation="i", resource_slug=None)
    assert none["turn"]["passages"] == [] and "Passages" not in none["body"]
