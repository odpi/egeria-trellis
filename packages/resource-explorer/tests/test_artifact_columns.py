"""Artifacts slice 1 (DESIGN-ARTIFACTS-VIEW-AND-DOC-LINKS.md §3, §7.1; owner's go 2026-10-15):
`file_role` on every symbol and marker row, `survey_commit_sha` on every repo step run.

Both columns are additive and nullable. NULL means "extracted / run before the column existed" and a reader
says "not recorded", never a guess. Registries here are a tmp SQLite file or the throwaway Postgres test
schema (`pg_registry`); never the shared one.
"""
from __future__ import annotations

import sqlite3

import pytest

from resource_explorer.registry import Project, ProjectRegistry

NEW_COLUMNS = {
    "project_code_symbols": "file_role",
    "project_code_markers": "file_role",
    "step_runs": "survey_commit_sha",
}


def _cols(reg, table) -> set[str]:
    with reg._conn() as conn:
        return reg._get_table_columns(conn, table)


def _reg(tmp_path, name="t.db") -> ProjectRegistry:
    r = ProjectRegistry(db_path=str(tmp_path / name))
    assert str(tmp_path) in r.database_url                                   # never the shared registry
    return r


def _project(reg, slug="p", sha=""):
    reg.add(Project(slug=slug, display_name=slug, github_url=f"https://github.com/x/{slug}", description=""))
    if sha:
        reg.update_commit_sha(slug, sha)


# ── the migration ───────────────────────────────────────────────────────────────────────────────────────

def test_fresh_sqlite_has_the_columns_and_a_second_init_is_a_no_op(tmp_path):
    path = str(tmp_path / "twice.db")
    first = {t: _cols(ProjectRegistry(db_path=path), t) for t in NEW_COLUMNS}
    second = {t: _cols(ProjectRegistry(db_path=path), t) for t in NEW_COLUMNS}
    for table, col in NEW_COLUMNS.items():
        assert col in first[table], table
    assert first == second


def test_an_existing_sqlite_registry_gains_the_columns_and_its_rows_stay_null(tmp_path):
    """Simulate a registry from before the change: drop the columns, keep a row in each table, re-init."""
    from resource_explorer.ingestion.code_symbol_extractor import CodeMarker, CodeSymbol

    path = tmp_path / "old.db"
    reg = ProjectRegistry(db_path=str(path))
    _project(reg)
    reg.upsert_code_symbols("p", [CodeSymbol("p", "a.py", "python", "function", "f", "f", "()", "", 1, 2)])
    reg.upsert_code_markers("p", [CodeMarker("p", "a.py", 1, "python", "route", "fastapi", "http_api", "GET /", "f")])
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-01T00:00:00")
    con = sqlite3.connect(path)
    for table, col in NEW_COLUMNS.items():
        con.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    con.commit()
    con.close()

    again = ProjectRegistry(db_path=str(path))                                  # the migration runs
    for table, col in NEW_COLUMNS.items():
        assert col in _cols(again, table), table
        with again._conn() as conn:
            rows = [dict(r) for r in conn.execute(f"SELECT {col} FROM {table}").fetchall()]
        assert rows and all(r[col] is None for r in rows), (table, rows)   # old rows: NULL, not a guess


def test_the_migration_is_idempotent_on_postgres(pg_registry, monkeypatch):
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url             # the throwaway schema only
    before = {t: _cols(pg_registry, t) for t in NEW_COLUMNS}
    for table, col in NEW_COLUMNS.items():
        assert col in before[table], table
    monkeypatch.setattr(PR, "_pg_schema_ready", set())                      # forget the per-process init
    again = PR(database_url=pg_registry.database_url)
    assert {t: _cols(again, t) for t in NEW_COLUMNS} == before


def _drop_new_columns(reg):
    with reg._conn() as conn:
        for table, col in NEW_COLUMNS.items():
            conn.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")


def test_two_registries_adding_the_columns_at_the_same_moment_both_succeed(pg_registry, monkeypatch):
    """Truly concurrent: two threads, two connections, both read each column as missing (a barrier holds
    them until both have looked), then both ALTER. The second waits on the first's lock and then is a
    no-op under ADD COLUMN IF NOT EXISTS; neither raises."""
    import threading

    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url
    _drop_new_columns(pg_registry)
    real = PR._get_table_columns
    barrier = threading.Barrier(2, timeout=30)

    def looked_then_wait(self, conn, table):
        cols = real(self, conn, table)
        if table in NEW_COLUMNS and threading.current_thread().name.startswith("ddl-"):
            barrier.wait()                                                  # both have seen it missing
        return cols
    monkeypatch.setattr(PR, "_get_table_columns", looked_then_wait)
    errors: list = []

    def start():
        try:
            pg_registry._migrate_artifact_columns()
        except Exception as exc:                                            # pragma: no cover - the failure
            errors.append(exc)
    threads = [threading.Thread(target=start, name=f"ddl-{i}") for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    monkeypatch.setattr(PR, "_get_table_columns", real)
    assert errors == []
    for table, col in NEW_COLUMNS.items():
        assert col in _cols(pg_registry, table), table


def test_a_held_table_lock_fails_the_start_cleanly_and_a_later_start_adds_the_column(pg_registry, monkeypatch):
    """Another process holds step_runs: the ADD COLUMN gives up after lock_timeout with the named
    RuntimeError, its transaction rolls back (column still absent, the holder unaffected), and a start
    once the table is quiet adds it."""
    import psycopg2

    from resource_explorer.config import get_config
    from resource_explorer.registry import ProjectRegistry as PR

    assert "resource_explorer_test" in pg_registry.database_url
    _drop_new_columns(pg_registry)
    monkeypatch.setattr(PR, "_DDL_LOCK_TIMEOUT", "300ms")
    schema = pg_registry.database_url.split("search_path%3D")[-1]
    cfg = get_config().pgvector
    holder = psycopg2.connect(host=cfg.host, port=cfg.port, dbname=cfg.dbname, user=cfg.db_user,
                              password=cfg.password)
    try:
        cur = holder.cursor()
        cur.execute(f'LOCK TABLE "{schema}".step_runs IN ACCESS EXCLUSIVE MODE')     # held, not committed
        with pytest.raises(RuntimeError, match=r"Could not add step_runs\.survey_commit_sha: another process"):
            pg_registry._migrate_artifact_columns()
        cur.execute(f'SELECT count(*) FROM "{schema}".step_runs')                    # holder still fine
        assert cur.fetchone()[0] >= 0
    finally:
        holder.rollback()
        holder.close()
    assert "survey_commit_sha" not in _cols(pg_registry, "step_runs")            # rolled back, not half-done
    assert "file_role" in _cols(pg_registry, "project_code_symbols")              # own short transaction
    pg_registry._migrate_artifact_columns()                                        # the table is quiet now
    assert "survey_commit_sha" in _cols(pg_registry, "step_runs")


# ── the classifier ──────────────────────────────────────────────────────────────────────────────────────

FIXTURE_ROLES = {
    # Python
    "testgen/api/app.py": "core",
    "testgen/__init__.py": "core",
    "tests/unit/test_api.py": "test",
    "testgen/conftest.py": "test",
    "proto/service_pb2.py": "generated",
    "proto/service_pb2_grpc.py": "generated",
    "build/lib/testgen/app.py": "generated",
    ".venv/lib/python3.12/site-packages/fastapi/app.py": "vendored",
    "vendor/requests/api.py": "vendored",
    "vendor/requests/tests/test_api.py": "vendored",                      # somebody else's tests
    "docs/conf.py": "docs",
    "docs/examples/app.py": "docs",
    # Java
    "src/main/java/org/x/Api.java": "core",
    "src/main/java/org/x/doc/DocBuilder.java": "core",                     # a package called doc is code
    "src/test/java/org/x/ApiTest.java": "test",
    "module/src/it/java/org/x/ApiIT.java": "test",                         # src/it (new is_test_path rule)
    "src/main/java/org/x/TestimonialController.java": "core",
    "target/generated-sources/annotations/org/x/Api_.java": "generated",
    "module/generated/org/x/Model.java": "core",                           # a dir called generated is not enough
    "module/generated/api_pb2.py": "generated",                            # ... a generator's file name is
    "module/generated-sources/org/x/Gen.java": "generated",
    "documentation/samples/Sample.java": "docs",
    # JavaScript
    "web/static/app.js": "core",
    "web/static/app.test.js": "test",
    "web/src/__tests__/app.js": "test",
    "web/src/util.spec.ts": "test",
    "web/static/vendor.min.js": "generated",
    "web/dist/bundle.js": "generated",
    "node_modules/react/index.js": "vendored",
    "web/node_modules/lodash/lodash.js": "vendored",
    "web/api.generated.ts": "generated",
    # Go
    "pkg/api/server.pb.go": "generated",
    "pkg/api/server.go": "core",
    "pkg/api/server_test.go": "test",
}


@pytest.mark.parametrize("path,role", sorted(FIXTURE_ROLES.items()))
def test_the_classifier_labels_each_fixture_file(path, role):
    from resource_explorer.ingestion.file_role import FILE_ROLES, classify_file_role

    assert role in FILE_ROLES
    assert classify_file_role(path) == role


def test_the_classifier_agrees_with_is_test_path_for_own_code():
    """One rule for tests: outside vendored/generated, role == test exactly when is_test_path says so."""
    from resource_explorer.ingestion.file_role import classify_file_role
    from resource_explorer.ingestion.test_paths import is_test_path

    for path, role in FIXTURE_ROLES.items():
        if role in ("vendored", "generated"):
            continue
        assert (classify_file_role(path) == "test") == is_test_path(path), path


# ── extraction writes file_role ─────────────────────────────────────────────────────────────────────────

APP_PY = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/token")
def get_token():
    return 1
'''
TEST_PY = '''
def test_token():
    assert True
'''
JAVA = '''
@RestController
public class Api {
    @GetMapping("/x")
    public String x() { return ""; }
}
'''
JS = "function render() { return 1; }\n"


def _fixture_repo(root):
    files = {
        "testgen/api/app.py": APP_PY,
        "tests/test_api.py": TEST_PY,
        "docs/examples/app.py": APP_PY,
        "proto/svc_pb2.py": "class Svc:\n    pass\n",
        "src/main/java/org/x/Api.java": JAVA,
        "src/test/java/org/x/ApiTest.java": JAVA,
        "web/static/app.js": JS,
        "web/static/app.test.js": JS,
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_the_extractor_stamps_file_role_on_symbols_and_markers():
    from resource_explorer.ingestion.code_symbol_extractor import CodeSymbolExtractor

    ex = CodeSymbolExtractor()
    syms = ex.extract("tests/test_api.py", TEST_PY, "p", "python")
    assert syms and {s.file_role for s in syms} == {"test"}
    marks = ex.extract_markers("docs/examples/app.py", APP_PY, "p", "python")
    assert marks and {m.file_role for m in marks} == {"docs"}


def test_survey_extraction_writes_file_role_on_every_symbol_and_marker_row(tmp_path):
    from resource_explorer.surveyors.sub_surveyors.symbol_extraction import SymbolExtractionSurveyor

    reg = _reg(tmp_path)
    _project(reg)
    root = tmp_path / "repo"
    _fixture_repo(root)
    SymbolExtractionSurveyor(reg.get("p"), reg, str(root)).run()

    with reg._conn() as conn:
        syms = [dict(r) for r in conn.execute(
            "SELECT file_path, file_role FROM project_code_symbols WHERE project_slug = 'p'").fetchall()]
        marks = [dict(r) for r in conn.execute(
            "SELECT file_path, file_role FROM project_code_markers WHERE project_slug = 'p'").fetchall()]
    by_file = {r["file_path"]: r["file_role"] for r in syms}
    assert syms and all(r["file_role"] is not None for r in syms)
    assert by_file["testgen/api/app.py"] == "core"
    assert by_file["tests/test_api.py"] == "test"
    assert by_file["docs/examples/app.py"] == "docs"
    assert by_file["proto/svc_pb2.py"] == "generated"
    assert by_file["src/main/java/org/x/Api.java"] == "core"
    assert by_file["src/test/java/org/x/ApiTest.java"] == "test"
    assert by_file["web/static/app.js"] == "core"
    assert by_file["web/static/app.test.js"] == "test"
    roles = {r["file_path"]: r["file_role"] for r in marks}
    assert marks and all(r["file_role"] is not None for r in marks)
    assert roles == {"testgen/api/app.py": "core", "docs/examples/app.py": "docs",
                     "src/main/java/org/x/Api.java": "core"}


def test_a_re_extraction_overwrites_the_role(tmp_path):
    from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol

    reg = _reg(tmp_path)
    _project(reg)
    s = CodeSymbol("p", "a.py", "python", "function", "f", "f", "()", "", 1, 2)
    reg.upsert_code_symbols("p", [s])
    s.file_role = "core"
    reg.upsert_code_symbols("p", [s])
    with reg._conn() as conn:
        assert [dict(r)["file_role"] for r in conn.execute("SELECT file_role FROM project_code_symbols").fetchall()] == ["core"]


# ── the survey run records its commit ───────────────────────────────────────────────────────────────────

def _step_shas(reg):
    with reg._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT slug, entity_type, step_key, survey_commit_sha FROM step_runs ORDER BY id").fetchall()]


class _FakeClient:
    """GitHubClient at its boundary: the zipball it serves is commit ZIP_SHA."""
    served_root = None

    def __init__(self, *a, **k):
        self.last_served_sha = None

    def get_repo(self, url):
        return object()

    def zipball_root(self, repo, subproject_path=None):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self.last_served_sha = ZIP_SHA
            yield _FakeClient.served_root
        return _cm()


ZIP_SHA = "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6a7b8c9d0"
INDEXED_SHA = "0000000000000000000000000000000000000old"


@pytest.fixture
def zipball_run(tmp_path, monkeypatch):
    reg = _reg(tmp_path)
    _project(reg, "p", sha=INDEXED_SHA)                                    # projects.last_commit_sha = Y
    root = tmp_path / "repo"
    _fixture_repo(root)
    _FakeClient.served_root = root
    monkeypatch.setattr("resource_explorer.github.client.GitHubClient", _FakeClient)
    return reg


def test_a_run_records_the_sha_of_the_zipball_it_read_not_last_commit_sha(zipball_run):
    """The zipball has SHA X while projects.last_commit_sha is Y: every step row of the run records X."""
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    result = SurveyOrchestrator(zipball_run).run("p", steps=["repo_symbol_extraction", "repo_api_structure"])
    rows = _step_shas(zipball_run)
    assert {r["step_key"] for r in rows} >= {"repo_symbol_extraction", "repo_api_structure"}
    assert {r["survey_commit_sha"] for r in rows} == {ZIP_SHA}               # the API-reading step too
    assert result.survey_commit_sha == ZIP_SHA


def test_the_runner_output_carries_the_sha_to_the_executor(zipball_run):
    from resource_explorer.surveyors.repo_survey_definition_adapter import _run_batch, _run_step

    assert _run_step("repo_symbol_extraction")(zipball_run.get("p"), zipball_run)["survey_commit_sha"] == ZIP_SHA
    out = _run_batch(zipball_run.get("p"), zipball_run, ["repo_symbol_extraction", "repo_api_structure"])
    assert out["survey_commit_sha"] == ZIP_SHA


def test_the_executor_records_the_sha_from_the_runner_output(tmp_path):
    from resource_explorer.surveyors import step_cost_observer
    from resource_explorer.surveyors.survey_definition_executor import SurveyDefinitionExecutor

    reg = _reg(tmp_path)
    _project(reg, "p", sha=INDEXED_SHA)
    obs = step_cost_observer.Observation(step_key="repo_symbol_extraction", elapsed=0.1, connects=0,
                                         declared_fetch="download", declared_compute="medium")
    ex = SurveyDefinitionExecutor.__new__(SurveyDefinitionExecutor)
    ex.registry = reg
    ex._record_cost([obs], "repo", "p", {"annotations": [], "survey_commit_sha": ZIP_SHA}, "2026-10-15T10:00:00")
    ex._record_cost([obs], "repo", "p", {"annotations": []}, "2026-10-15T11:00:00")
    assert [r["survey_commit_sha"] for r in _step_shas(reg)] == [ZIP_SHA, None]


def test_a_run_that_read_no_code_stores_null_even_with_a_last_commit_sha(zipball_run):
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    with zipball_run._conn() as conn:        # api_structure declares has_code_symbols: seed the symbols it reads
        conn.execute(
            "INSERT INTO project_code_symbols (project_slug, file_path, language, kind, name, "
            "qualified_name) VALUES ('p', 'a.py', 'python', 'function', 'f', 'a.f')")
    SurveyOrchestrator(zipball_run).run("p", steps=["repo_api_structure"])    # reads stored symbols only
    rows = _step_shas(zipball_run)
    assert rows and {r["survey_commit_sha"] for r in rows} == {None}


def test_record_step_run_never_falls_back_to_last_commit_sha(tmp_path):
    reg = _reg(tmp_path)
    _project(reg, "p", sha=INDEXED_SHA)
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T10:00:00")
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T11:00:00", survey_commit_sha=ZIP_SHA)
    reg.record_step_run("db1", "db_columns", "2026-10-15T10:00:00", entity_type="database")
    assert [r["survey_commit_sha"] for r in _step_shas(reg)] == [None, ZIP_SHA, None]


def test_a_failed_step_records_no_commit(zipball_run, monkeypatch):
    from resource_explorer.surveyors.sub_surveyors.api_structure import ApiStructureSurveyor
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    def boom(self):
        raise RuntimeError("boom")
    monkeypatch.setattr(ApiStructureSurveyor, "run", boom)
    result = SurveyOrchestrator(zipball_run).run("p", steps=["repo_symbol_extraction", "repo_api_structure"])
    shas = {r["step_key"]: r["survey_commit_sha"] for r in _step_shas(zipball_run)}
    assert shas["repo_symbol_extraction"] == ZIP_SHA
    assert shas["repo_api_structure"] is None                              # raised: read nothing to cite
    assert "repo_api_structure" not in result.commit_by_step


def test_a_warned_extraction_records_no_commit_and_the_reader_keeps_the_good_one(zipball_run, monkeypatch):
    """A good extraction at X, then one that fails (step_error, the way surveyors report a failure): the
    failed row has no commit and the drill-down still shows X."""
    from resource_explorer.artifact_provenance import extraction_commit
    from resource_explorer.surveyors.sub_surveyors import symbol_extraction
    from resource_explorer.surveyors.survey_orchestrator import SurveyOrchestrator

    SurveyOrchestrator(zipball_run).run("p", steps=["repo_symbol_extraction"])
    monkeypatch.setattr(symbol_extraction, "COLLECTION_TYPES", None)       # extraction fails inside run()
    SurveyOrchestrator(zipball_run).run("p", steps=["repo_symbol_extraction"])
    rows = [r for r in _step_shas(zipball_run) if r["step_key"] == "repo_symbol_extraction"]
    assert [r["survey_commit_sha"] for r in rows] == [ZIP_SHA, None]
    assert extraction_commit(zipball_run, "p") == ZIP_SHA


def test_the_reader_skips_a_failed_latest_row_even_with_a_commit(tmp_path):
    from resource_explorer.artifact_provenance import extraction_commit

    reg = _reg(tmp_path)
    _project(reg)
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T10:00:00",
                        metrics={"outcomes": ["recovered"]}, survey_commit_sha="good")
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T11:00:00",
                        metrics={"outcomes": []}, survey_commit_sha="bad")
    assert extraction_commit(reg, "p") == "good"


def test_prefect_rows_carry_the_runner_commit(tmp_path, monkeypatch):
    from resource_explorer.prefect import flows
    from resource_explorer.surveyors import step_cost_observer

    reg = _reg(tmp_path)
    _project(reg, "p", sha=INDEXED_SHA)
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry", lambda *a, **k: reg)
    obs = step_cost_observer.Observation(step_key="repo_symbol_extraction", elapsed=0.1, connects=0,
                                         declared_fetch="download", declared_compute="medium")
    flows._record_step_cost("repo", "p", obs, {"annotations": [], "survey_commit_sha": ZIP_SHA},
                            "2026-10-15T10:00:00")
    assert [r["survey_commit_sha"] for r in _step_shas(reg)] == [ZIP_SHA]


def test_the_zipball_is_downloaded_by_the_sha_it_is_recorded_under(tmp_path, monkeypatch):
    """client.py's real code, faking only HTTP: the SHA lookup answers X, the branch then moves; the
    download URL must name X, and last_served_sha is X."""
    import io
    import zipfile

    import resource_explorer.github.source_cache as cache_mod
    from resource_explorer.github.client import GitHubClient

    real_cls = cache_mod.SourceCache
    monkeypatch.setattr(cache_mod, "SourceCache", lambda: real_cls(cache_dir=tmp_path / "cache"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("owner-repo-a1b2c3d/app.py", "x = 1\n")
    urls: list[str] = []

    class _Resp:
        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield buf.getvalue()

    def fake_get(url, **kw):
        urls.append(url)
        return _Resp()
    monkeypatch.setattr("requests.get", fake_get)

    class _Commit:
        sha = ZIP_SHA

    class _Repo:
        full_name = "owner/repo"
        default_branch = "main"

        def get_commits(self):
            return [_Commit()]

    client = GitHubClient.__new__(GitHubClient)
    client._base_url = "https://api.github.test"
    with client.zipball_root(_Repo()) as root:
        assert (root / "app.py").exists()
    assert urls == [f"https://api.github.test/repos/owner/repo/zipball/{ZIP_SHA}"]
    assert client.last_served_sha == ZIP_SHA


def test_a_clone_records_its_own_head(tmp_path, monkeypatch):
    """The clone's HEAD, read from the clone, not the cache key."""
    import subprocess

    import resource_explorer.github.source_cache as cache_mod
    from resource_explorer.github.client import GitHubClient

    origin = tmp_path / "origin"
    origin.mkdir()
    git = ["git", "-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    (origin / "a.py").write_text("x = 1\n")
    subprocess.run(git + ["add", "a.py"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "one"], check=True)
    head = subprocess.run(["git", "-C", str(origin), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()

    real_cls = cache_mod.SourceCache
    monkeypatch.setattr(cache_mod, "SourceCache", lambda: real_cls(cache_dir=tmp_path / "cache"))
    client = GitHubClient.__new__(GitHubClient)
    client._default_branch_sha = lambda repo: "0" * 40                       # a stale key
    client.clone_git_root = lambda repo, dest, shallow_since=None: (
        subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True) and None) or dest

    class _Repo:
        full_name = "owner/repo"

    with client.git_clone_root(_Repo()):
        pass
    assert client.last_served_sha == head


# ── readers say "not recorded" for NULL ─────────────────────────────────────────────────────────────────

def test_the_labels_say_not_recorded_for_null():
    from resource_explorer.artifact_provenance import NOT_RECORDED, commit_label, role_label

    assert NOT_RECORDED == "not recorded"
    assert role_label(None) == "role not recorded"
    assert role_label("test") == "test"
    assert commit_label(None) == "commit not recorded"
    assert commit_label("") == "commit not recorded"
    assert commit_label("3606859c0ffee1234567") == "commit 3606859c0ffe"


def test_the_symbol_drill_down_shows_role_and_commit_or_not_recorded(tmp_path):
    from resource_explorer import members
    from resource_explorer.artifact_provenance import extraction_commit
    from resource_explorer.ingestion.code_symbol_extractor import CodeSymbol

    reg = _reg(tmp_path)
    _project(reg)
    old = CodeSymbol("p", "a.py", "python", "function", "old_f", "old_f", "()", "", 1, 2)   # role NULL
    new = CodeSymbol("p", "a.py", "python", "function", "new_f", "new_f", "()", "", 3, 4)
    new.file_role = "core"
    reg.upsert_code_symbols("p", [old, new])

    rows = {r["name"]: r["detail"] for r in members._symbols_in_file(reg, "p", "a.py", "all", 50)}
    assert rows["old_f"].startswith("function · role not recorded")   # then line and signature
    assert rows["new_f"].startswith("function · core") and "line 3" in rows["new_f"]

    assert extraction_commit(reg, "p") is None                             # no extraction run recorded
    assert "commit not recorded" in members._symbol_members(reg, "p", "all", 50).note
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T10:00:00",   # completed, read no zipball
                        metrics={"outcomes": ["recovered"]})
    assert "commit not recorded" in members._symbol_members(reg, "p", "all", 50).note
    reg.record_step_run("p", "repo_symbol_extraction", "2026-10-15T11:00:00",
                        metrics={"outcomes": ["recovered"]}, survey_commit_sha="3606859c0ffee1234567")
    assert extraction_commit(reg, "p") == "3606859c0ffee1234567"
    assert "extracted at commit 3606859c0ffe" in members._symbol_members(reg, "p", "all", 50).note
