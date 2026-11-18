"""web_docs skips site-generator scaffolding (owner, 2026-11-11, option A).

egeria_docs' web_docs collection was 7 chunks of noise: mkdocs-material templates under
site/overrides and a meta-refresh redirect stub (tests/fixtures/web_docs_egeria_docs, the real
files). They are now skipped and counted; real HTML docs, including a complete page that happens to
sit in a folder called `partials`, are kept.
"""
from __future__ import annotations

import io
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from resource_explorer.configdata.collection_config import COLLECTION_TYPES
from resource_explorer.ingestion import site_templates as st

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "web_docs_egeria_docs"
WEB = COLLECTION_TYPES["web_docs"]

REAL_PAGE = """<!DOCTYPE html>
<html lang="en"><head><title>Glossaries</title></head>
<body><h1>Glossaries</h1><p>A glossary is a collection of terms that describe the concepts of an
organization. Each term has a summary, a description and examples, and terms are linked by
relationships such as synonym and antonym. """ + "More real documentation text. " * 20 + """</p></body></html>
"""


@pytest.fixture
def pipeline(monkeypatch):
    from resource_explorer.ingestion import artifact_tree_sink
    from resource_explorer.ingestion.pipeline import IngestionPipeline
    p = IngestionPipeline.__new__(IngestionPipeline)
    p.console = Console(file=io.StringIO(), width=200)
    p.trees_for = []
    monkeypatch.setattr(artifact_tree_sink, "build_trees",
                        lambda files, slug, kind: p.trees_for.extend(path for path, _ in files) or None)
    monkeypatch.setattr(IngestionPipeline, "_report_tree_result", lambda self, result, slug: None, raising=False)
    return p


def _tree(tmp_path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return tmp_path


def test_egeria_docs_html_is_all_scaffolding(pipeline, tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(FIXTURE / "site", root / "site")
    chunks = pipeline._ingest_web_docs(root, "egeria_docs", WEB)
    assert chunks == [] and pipeline.trees_for == []
    out = pipeline.console.file.getvalue()
    assert "skipped 6 site template file(s)" in out and "skipped 1 redirect stub file(s)" in out


def test_each_real_file_is_classified(tmp_path):
    reasons = {}
    for f in sorted((FIXTURE / "site").rglob("*.html")):
        rel = f.relative_to(FIXTURE).as_posix()
        reasons[rel] = st.skip_reason(rel, f.read_text(encoding="utf-8"))
    assert reasons == {
        "site/docs/egeria-docs/index.html": "redirect stub",
        "site/overrides/404.html": "site template",
        "site/overrides/home.html": "site template",
        "site/overrides/main.html": "site template",
        "site/overrides/partials/comments.html": "site template",  # no template syntax, not a full page
        "site/overrides/partials/header.html": "site template",
        "site/overrides/partials/logo.html": "site template",
    }


def test_real_docs_are_kept(pipeline, tmp_path):
    root = _tree(tmp_path, {
        "site/docs/glossaries/index.html": REAL_PAGE,
        "public/partials/index.html": REAL_PAGE,     # a rendered page in a folder named partials
        "docs/overrides.html": REAL_PAGE,            # a FILE named like a template folder
        "site/overrides/main.html": "{% extends 'base.html' %}{% block content %}x{% endblock %}",
    })
    chunks = pipeline._ingest_web_docs(root, "proj", WEB)
    paths = {c.metadata["file_path"] for c in chunks}
    assert paths == {"site/docs/glossaries/index.html", "public/partials/index.html", "docs/overrides.html"}
    assert set(pipeline.trees_for) == paths


def test_extra_docs_paths_are_filtered_too(pipeline, tmp_path):
    extra = _tree(tmp_path / "extra", {"_templates/layout.html": "{% block body %}{% endblock %}",
                                       "guide/index.html": REAL_PAGE})
    root = _tree(tmp_path / "repo", {})
    chunks = pipeline._ingest_web_docs(root, "proj", WEB, extra_paths=[("extra", extra)])
    assert {c.metadata["file_path"] for c in chunks} == {"extra/guide/index.html"}


@pytest.mark.parametrize("folder", sorted(st.SITE_TEMPLATE_DIRS))
def test_every_template_folder(folder):
    assert st.skip_reason(f"docs/{folder}/x.html", "<div>{{ page.title }}</div>") == "site template"
    assert st.skip_reason(f"docs/{folder}/x.html", "<footer>partial, no syntax</footer>") == "site template"
    assert st.skip_reason(f"docs/{folder}/x.html", REAL_PAGE) is None


def test_a_redirect_with_real_content_is_kept():
    page = REAL_PAGE.replace("<head>", '<head><meta http-equiv="refresh" content="30">')
    assert st.skip_reason("docs/live.html", page) is None


# ── A rebuild of egeria_docs: the all-scaffolding web_docs is a planned drop, not a refusal ─────────────────────
# Read-back of b6747238: the analyzer still proposes web_docs (mkdocs marker), every HTML file is now skipped, so the
# rebuild saw 7 -> 0 and hit the zero-refusal. It is now dropped with a note, and leaves projects.collections on accept.

from resource_explorer import reindex  # noqa: E402
from tests.test_reindex_rebuild import FakePipeline, FakeStore, SLUG, ct, execute, target  # noqa: E402


class _RealWebDocsPipeline(FakePipeline):
    """The rebuild's fake pipeline, except web_docs runs the REAL _ingest_web_docs over the downloaded tree."""

    def _ingest_collection(self, repo, slug, name, ctype, root, *, id_collection=None, extra_paths=None):
        if ctype.name != "web_docs":
            return super()._ingest_collection(repo, slug, name, ctype, root, id_collection=id_collection,
                                              extra_paths=extra_paths)
        from resource_explorer.ingestion import artifact_tree_sink
        from resource_explorer.ingestion.pipeline import IngestionPipeline
        real = IngestionPipeline.__new__(IngestionPipeline)
        real.console = Console(file=io.StringIO(), width=200)
        real._report_tree_result = lambda result, s: None
        saved, artifact_tree_sink.build_trees = artifact_tree_sink.build_trees, lambda files, s, kind: None
        try:
            chunks = real._ingest_web_docs(root, slug, WEB, extra_paths)
        finally:
            artifact_tree_sink.build_trees = saved
        self.last_web_docs_scaffolding = real.last_web_docs_scaffolding
        if not chunks:
            return 0
        return self.store.insert(name, [c.text for c in chunks], [c.metadata for c in chunks],
                                 id_collection=id_collection)


@pytest.fixture
def egeria_docs(tmp_path):
    from resource_explorer.registry import Project, ProjectRegistry
    reg = ProjectRegistry(db_path=str(tmp_path / "reg.db"))
    reg.add(Project(slug=SLUG, display_name="egeria-docs", github_url="https://github.com/odpi/egeria-docs",
                    collections=[f"{SLUG}_markdown_docs", f"{SLUG}_web_docs", "web_docs_egeria_project_org"]))
    store = FakeStore()
    store.insert(f"{SLUG}_markdown_docs", ["intro"], [{"file_path": "site/docs/index.md"}])
    # The live egeria_docs_web_docs: its 7 chunks were these files (shared store, read-only, 2026-11-11).
    store.insert(f"{SLUG}_web_docs", [f"chunk {i}" for i in range(7)],
                 [{"file_path": f} for f in ["site/docs/egeria-docs/index.html", "site/overrides/404.html",
                                             "site/overrides/home.html", "site/overrides/home.html",
                                             "site/overrides/home.html", "site/overrides/main.html",
                                             "site/overrides/partials/header.html"]])
    store.insert("web_docs_egeria_project_org", ["site page"], [{"file_path": "https://egeria-project.org"}])

    def download(repo, d, sub=None, ref=None):
        shutil.copytree(FIXTURE / "site", Path(d) / "site")
        return Path(d)

    client = SimpleNamespace(get_repo=lambda url: SimpleNamespace(), get_latest_commit_sha=lambda repo: "sha1",
                             download_zipball=download)
    d = reindex.Deps(registry=reg, store=store, analyzer=SimpleNamespace(
        analyze=lambda url, subpath=None: SimpleNamespace(proposed_collections=[ct("markdown_docs"), ct("web_docs")])),
        pipeline=_RealWebDocsPipeline(store, {"markdown_docs": [("site/docs/index.md", "intro")]}),
        client=client, query_cache=SimpleNamespace(invalidate_project=lambda slug: None))
    return reg, store, d


def test_a_rebuild_drops_an_all_scaffolding_web_docs_instead_of_refusing(egeria_docs):
    reg, store, d = egeria_docs
    run_id = reindex.queue(reg, SLUG, requested_by="erinoverview", store=FakeStore())["run_id"]
    assert execute(reg, run_id, d) == ("succeeded", "")            # held for accept: the first rebuild ever
    t = target(reg, run_id)
    assert t["phase"] == "awaiting_accept" and not t.get("refused")
    assert t["dropped"] == [f"{SLUG}_web_docs"]
    assert t["counts"][f"{SLUG}_web_docs"]["scaffolding_only"] == 7
    built = next(o["words"] for o in t["outcomes"] if o["phase"] == "built")
    assert f"{SLUG}_web_docs: 7 → dropped" in built
    assert "every HTML file (7) is site scaffolding" in built and "collection dropped" in built
    assert not store.collection_exists(reindex.aside(f"{SLUG}_web_docs"))

    cont = reindex.accept(reg, run_id, requested_by="erinoverview")["run_id"]
    assert execute(reg, cont, d) == ("succeeded", "")
    assert sorted(reg.get(SLUG).collections) == sorted([f"{SLUG}_markdown_docs", "web_docs_egeria_project_org"])
    assert not store.collection_exists(f"{SLUG}_web_docs")
    assert store.count(f"{SLUG}_web_docs__previous") == 7          # kept 7 days; undo restores it
    assert store.count("web_docs_egeria_project_org") == 1          # the shared site collection is untouched


def test_an_empty_web_docs_that_is_not_all_scaffolding_is_still_refused(egeria_docs, tmp_path):
    """Only the all-scaffolding case is a planned drop: a web_docs that lost real pages still refuses."""
    reg, store, d = egeria_docs
    d.pipeline.last_web_docs_scaffolding = None
    real_ingest = d.pipeline._ingest_collection

    def lost_pages(repo, slug, name, ctype, root, **kw):
        out = real_ingest(repo, slug, name, ctype, root, **kw)
        if ctype.name == "web_docs":   # pretend one real page existed and yielded nothing
            sc = d.pipeline.last_web_docs_scaffolding
            d.pipeline.last_web_docs_scaffolding = {**sc, "candidates": sc["candidates"] + 1}
        return out

    d.pipeline._ingest_collection = lost_pages
    run_id = reindex.queue(reg, SLUG, requested_by="erinoverview", store=FakeStore())["run_id"]
    state, error = execute(reg, run_id, d)
    assert state == "failed" and f"{SLUG}_web_docs had 7 chunk(s) and now has none" in error
    assert store.count(f"{SLUG}_web_docs") == 7
