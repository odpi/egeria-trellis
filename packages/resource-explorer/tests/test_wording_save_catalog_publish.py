"""The wording slice: Save / Catalog / Publish (REPLY-DESIGNER-SAVE-AND-PUBLISH-VERBS.md, owner decision 2026-10-06).

* the reserved Egeria verb is **Catalog** (US spelling): no UK "catalogue" on any rendered control, row, manifest line,
  step word, header, preview, blocker or page, with a documented allow-list;
* the group route records the signed-in person (`saved · who · when`) and answers 401 when signed out;
* an Egeria soft delete reads "deleted in Egeria", never "removed" (reserved for "Remove from Resource Explorer").

The JS wording (controls and result lines) is pinned by the render harness: frontend-build/test-harness/wording-*.test.mjs.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_catalogue_commit import (  # noqa: E402,F401
    ME, _zones, choose, derived, entity, fake, press, registry, step, view, world)

from resource_explorer import catalogue_commit as cc  # noqa: E402
from resource_explorer.auth import create_access_token  # noqa: E402
from resource_explorer.registry import Project, ProjectRegistry  # noqa: E402


@pytest.fixture(autouse=True)
def _zone_access_open(monkeypatch):
    """Brief Z2: this write path asks `curation_access` (the one decision). These tests are about something else,
    so no zone is in use: the access reads are faked open at the client boundary (`tests/zone_fakes.open_access`)."""
    from tests.zone_fakes import open_access

    return open_access(monkeypatch)

# Brief I: a catalog commit is a person's own action; it runs as a signed-in caller.
pytestmark = pytest.mark.usefixtures("signed_in_caller")

PKG = Path(__file__).resolve().parents[1] / "resource_explorer"
# ── no UK "catalogue" on anything a person reads ─────────────────────────────────────────────

#: A UK word is fine where it is NOT prose a person reads: an identifier, a route, a proof or state key, a data attribute,
#: a CSS id. Prose is any string literal with whitespace in it. Egeria's own connector word `cataloguer` (the JDBC
#: cataloguer, `JDBCDatabaseCataloguer`) is never matched: the pattern needs the word to END at e/ed/es/ing.
UK = re.compile(r"(?<![\w/_$.-])[Cc]atalogu(e|ed|es|ing)\b")
UK_PROSE = re.compile(r"(?<![\w/_$.])[Cc]atalogu(e|ed|es|ing)\b")


def _docstring_ids(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
                ids.add(id(first.value))
    return ids


#: Strings that keep the UK word on purpose, each an IDENTITY and not a rendered word:
#:  * the stored `kind` default 'catalogue' (a data value the code compares with; rewriting it orphans every row);
#:  * the question catalog's own text, which is the question's KEY (stored answers are keyed on it: reword it and the
#:    answer is orphaned; renaming it is a data migration, recorded in the implemented note as a follow-up).
PY_ALLOW = (
    "ADD COLUMN kind TEXT NOT NULL DEFAULT 'catalogue'",
    "TEXT NOT NULL DEFAULT 'catalogue'",  # the same stored default, as registry._init_schema's column list spells it
    "Has this resource already been cataloged in Egeria, and when?",
)


def test_no_python_string_a_person_reads_says_catalogue():
    bad = []
    for p in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        docs = _docstring_ids(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs \
                    and re.search(r"\s", n.value) and UK_PROSE.search(n.value) \
                    and not any(a in n.value for a in PY_ALLOW):
                bad.append(f"{p.relative_to(PKG)}:{n.lineno}: {n.value[:90]!r}")
    assert bad == [], "UK spelling in prose a person reads (use Catalog / catalog / cataloged):\n" + "\n".join(bad)


#: Static text that is not a rendered word: markup identifiers and API values. Each is an exact token, documented here.
JS_ALLOW = (
    "data-catalogue-depth", "catalogue-depth-offer", "data-subres-catalogued-row", "data-scope-catalogue-all",
    "/api/catalogue-scope", "catalogue-scope", "catalogue_scope", "catalogue_commit", "catalogue_failed",
    "data-scope-catalogue", "schemas_catalogue", "catalogue-depth",
)


def _code_text(src: str) -> str:
    """JS/HTML with `//` and `/* */` comments removed (comments are prose for developers, not for the page)."""
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)(?<![:\"'`])//.*$", "", src)


def _page_words(path: Path) -> list[str]:
    out = []
    for ln, line in enumerate(_code_text(path.read_text()).splitlines(), 1):
        stripped = line
        for tok in JS_ALLOW:
            stripped = stripped.replace(tok, "")
        for m in UK.finditer(stripped):
            tail = stripped[m.end(): m.end() + 1]
            if tail == "_":                                                    # catalogue_failed, catalogue_scope: identifiers
                continue
            around = stripped[max(0, m.start() - 1): m.end() + 1]
            if re.fullmatch(r"""['"]catalogu(e|ed)['"]""", around):            # 'catalogue' / 'catalogued': a choice VALUE or state KEY
                continue
            if tail == ":" and not stripped[m.end(): m.end() + 2] == "::":     # an object key: `catalogued: ...`
                continue
            out.append(f"{path.name}:{ln}: {line.strip()[:110]}")
            break
    return out


def test_no_page_script_or_html_says_catalogue():
    root = PKG / "web" / "static"
    files = sorted(list((root / "next").rglob("*.js")) + [root / "next" / "index.html"])
    bad = [w for p in files for w in _page_words(p)]
    assert bad == [], "UK spelling on a rendered page (use Catalog / catalog / cataloged):\n" + "\n".join(bad)


def test_the_connector_word_cataloguer_is_the_one_spelling_that_stays():
    """`cataloguer` names Egeria's connector (the JDBC cataloguer, `JDBCDatabaseCataloguer`): it is not matched, and the
    sentences about what RE does say catalog."""
    assert UK.search("Egeria's cataloguer creates tables") is None and UK.search("JDBCDatabaseCataloguer") is None
    assert UK.search("Catalogue →") and UK.search("not catalogued") and UK.search("cataloguing…")


# ── the group route records the person ────────────────────────────────────────────────────────


@pytest.fixture
def group_registry(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    return r


@pytest.fixture
def client(group_registry, monkeypatch):
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None: setattr(self, "__dict__", group_registry.__dict__) or None)
    from resource_explorer import auth
    from resource_explorer.web.app import app
    from resource_explorer.web.routes import projects as projects_routes
    monkeypatch.setattr(projects_routes, "get_current_user", auth.get_current_user)
    return TestClient(app)


def as_user(u):
    return {"Authorization": "Bearer " + create_access_token(user_id=u, egeria_token="t")}


def test_signed_out_the_group_route_is_401_and_changes_nothing(client, group_registry):
    r = client.post("/api/projects/p/group", json={"resource_type": "repo", "group_slug": ""})
    assert r.status_code == 401 and "Sign in" in r.json()["detail"]
    assert group_registry.list_group_changes("repo", "p") == []


def test_the_group_route_records_the_person_and_answers_saved_by_and_when(client, group_registry):
    r = client.post("/api/projects/p/group", json={"resource_type": "repo", "group_slug": ""}, headers=as_user("dwolfson"))
    assert r.status_code == 200
    j = r.json()
    assert j["saved_by"] == "dwolfson" and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", j["saved_at"])
    [row] = group_registry.list_group_changes("repo", "p")
    assert (row["author"], row["group_slug"], row["entity_type"], row["entity_slug"]) == ("dwolfson", "", "repo", "p")
    assert row["changed_at"] == j["saved_at"]


def test_the_author_comes_from_the_session_never_the_body(client, group_registry):
    client.post("/api/projects/p/group", json={"resource_type": "repo", "group_slug": "", "author": "mallory"},
                headers=as_user("dwolfson"))
    assert [r["author"] for r in group_registry.list_group_changes("repo", "p")] == ["dwolfson"]


def test_a_refused_group_change_records_nothing(client, group_registry):
    r = client.post("/api/projects/p/group", json={"resource_type": "repo", "group_slug": "no-such"}, headers=as_user("dwolfson"))
    assert r.status_code == 404 and group_registry.list_group_changes("repo", "p") == []


# ── an Egeria soft delete says "deleted in Egeria", never "removed" ─────────────────────────


def _attached(world, fake, *names):
    for n in names:
        choose(world, n, "catalogue")
    press(world, fake, refresh=False)


def _all_words(world, fake, preview, rec):
    d = derived(world)
    words = [r["text"] for r in preview["leave_out"]] + [preview["button"]]
    words += [s["detail"] for s in rec["steps"]]
    words += [d["schemas"]["sales"]["words"], d["schemas"]["sales"]["second"], d["header"]["text"]]
    return words


def test_the_preview_says_will_be_deleted_from_egeria(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    p = cc.build_preview(world["registry"], "db", view(world), fake)
    row = p["leave_out"][0]
    assert row["text"].startswith("sales: nothing hangs off it · will delete in Egeria")
    assert row["text"].endswith(" · delete · nothing depends on it") and "with its" not in row["text"] or " tables" in row["text"]
    assert "removed" not in row["text"] and "soft-deleted" not in row["text"]
    assert "deletes 1 from Egeria" in p["button"] and "removes" not in p["button"]


def test_the_row_and_the_step_say_deleted_from_egeria_not_removed(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    out, rec = press(world, fake)
    s = derived(world)["schemas"]["sales"]
    assert s["state"] == "deleted"                                    # the identifier stays
    assert re.match(r"^deleted in Egeria · \d\d-\d\d \d\d:\d\d$", s["words"])
    assert step(rec, "leave_outs")["detail"].startswith("1 of 1 deleted in Egeria, each with its proof row")
    for w in _all_words(world, fake, out["preview"], rec):
        assert "removed" not in w.lower().replace("remove from resource explorer", ""), w


def test_a_mixed_leave_out_says_how_many_were_deleted_and_how_many_archived(world, fake):
    _attached(world, fake, "sales", "archive")
    fake._cycle(fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")["guid"])
    choose(world, "sales", "leave_out")
    choose(world, "archive", "leave_out")
    _, rec = press(world, fake)
    assert step(rec, "leave_outs")["detail"].startswith("2 of 2: 1 deleted in Egeria, 1 archived in Egeria")


def test_an_all_archive_step_says_archived_in_egeria(world, fake):
    _attached(world, fake, "archive")
    fake._cycle(fake.by_qn("PostgreSQL Relational Database Schema::host.docker.internal:5442::shop.archive")["guid"])
    choose(world, "archive", "leave_out")
    _, rec = press(world, fake)
    assert step(rec, "leave_outs")["detail"].startswith("1 of 1 archived in Egeria, each with its proof row")


def test_already_deleted_and_already_archived_read_in_those_words(world, fake):
    _attached(world, fake, "sales")
    choose(world, "sales", "leave_out")
    press(world, fake)
    row = cc.build_preview(world["registry"], "db", view(world), fake)["leave_out"][0]
    assert row["text"] == "sales: nothing to remove · already deleted in Egeria"


# ── the one additive table: group_changes ─────────────────────────────────────────────────────────


def test_group_changes_is_additive_and_an_old_shape_database_reopens_safely(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    r = ProjectRegistry(db_path=path)
    r.add(Project(slug="p", display_name="P", github_url="https://github.com/x/p", description=""))
    con = sqlite3.connect(path)
    con.execute("DROP TABLE group_changes")                      # the OLD shape: the table did not exist before this slice
    con.commit()
    assert "group_changes" not in {t[0] for t in con.execute("SELECT name FROM sqlite_master")}
    con.close()
    r2 = ProjectRegistry(db_path=path)                           # opening it creates the table
    assert r2.list_group_changes("repo", "p") == []
    at = r2.record_group_change("repo", "p", "", "dwolfson")
    assert [(x["author"], x["changed_at"]) for x in r2.list_group_changes("repo", "p")] == [("dwolfson", at)]
    assert r2.get("p") is not None                               # nothing existing was touched
    con = sqlite3.connect(path)
    assert [c[1] for c in con.execute("PRAGMA table_info(group_changes)")] == \
        ["id", "entity_type", "entity_slug", "group_slug", "author", "changed_at"]
    con.close()


def test_the_group_changes_sql_survives_the_postgres_translator_untouched_by_its_hazards():
    """The translator rewrites `?` to `%s`, any `:name` token to `%(name)s` and AUTOINCREMENT to SERIAL; DDL text
    must carry no `?` and no `:name` (a colon in a comment or a string would be rewritten)."""
    from resource_explorer.registry import PostgresCursorWrapper
    src = (PKG / "registry.py").read_text()
    ddl = src[src.index("CREATE TABLE IF NOT EXISTS group_changes"): src.index("idx_group_changes_entity")]
    ddl_sql = ddl[: ddl.index('""")')]
    assert "?" not in ddl_sql and not re.search(r"(?<!:):[A-Za-z_]", ddl_sql)
    t = PostgresCursorWrapper(None)._translate_sql
    out = t(ddl_sql)
    assert "SERIAL PRIMARY KEY" in out and "AUTOINCREMENT" not in out and "%" not in out
    ins = ("INSERT INTO group_changes (entity_type, entity_slug, group_slug, author, changed_at) VALUES (?, ?, ?, ?, ?)")
    assert t(ins).count("%s") == 5 and "?" not in t(ins)
    idx = "CREATE INDEX IF NOT EXISTS idx_group_changes_entity ON group_changes(entity_type, entity_slug, id)"
    assert t(idx) == idx


# ── the local guard: a skipped tier's assertion cannot go stale ──────────────────────────────────

#: Prose literals in tests that may keep the UK spelling, each for a stated reason.
TEST_UK_ALLOW = (
    "Has this resource already been cataloged in Egeria, and when?",   # the question catalog's stored KEY (a stored-answer key)
    "- catalogued: true",   # test_fact_answer_rendering: a JS fixture KEY (`{catalogued: true}`) echoed by a formatter, not wording
)

#: Tests that assert on app wording the slice changed and are SKIPPED locally (the pg tier, live tiers): the guard reads their
#: source, so a stale assertion fails here (not skipped in any tier), long before CI's Postgres service container runs them.
SKIPPED_TIER_MARKERS = ("pg_registry", "pg_store", "pg_test_schema", "requires_pgvector", "requires_egeria", "live_")


def _assert_literals(tree):
    """String constants in the TEST EXPRESSION of an `assert` (not its failure message) and inside `.match(...)`/`match=`
    expectations."""
    for node in ast.walk(tree):
        subtree = None
        if isinstance(node, ast.Assert):
            subtree = node.test
        elif isinstance(node, ast.Call) and (getattr(node.func, "attr", "") in ("match", "search", "fullmatch")
                                             or any(k.arg == "match" for k in node.keywords)):
            subtree = node
        if subtree is None:
            continue
        for n in ast.walk(subtree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                yield n


def test_no_test_assertion_expects_the_uk_spelling_in_app_prose():
    bad = []
    for p in sorted((Path(__file__).parent).rglob("*.py")):
        if p.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for n in _assert_literals(tree):
            v = n.value
            if re.search(r"\s", v) and UK_PROSE.search(v) and not any(a in v for a in TEST_UK_ALLOW):
                bad.append(f"{p.name}:{n.lineno}: {v[:90]!r}")
    assert bad == [], ("assertions that expect the UK spelling in app prose (the wording is US: catalog / cataloged); "
                       "this includes tests skipped locally (the pg tier):\n" + "\n".join(bad))


def test_no_test_assertion_expects_a_word_the_slice_retired_on_a_control_or_row():
    """'Sync now', 'Create in Egeria', 'declare the scope again', 'add + probe' and 'will be removed (soft-deleted)'. An
    assertion that the OLD word is GONE (`not in`, `not ...`) is fine; one that EXPECTS it is stale."""
    retired = ("Sync now", "Create in Egeria", "declare the scope again", "add + probe", "will be removed (soft-deleted)")
    bad = []
    for p in sorted((Path(__file__).parent).rglob("*.py")):
        if p.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for a in ast.walk(tree):
            if not isinstance(a, ast.Assert):
                continue
            negated = any(isinstance(n, ast.NotIn) or (isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not))
                          for n in ast.walk(a.test))
            if negated:
                continue
            for n in ast.walk(a.test):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and any(r in n.value for r in retired):
                    bad.append(f"{p.name}:{n.lineno}: {n.value[:90]!r}")
    assert bad == [], "assertions that expect a retired word:\n" + "\n".join(bad)


def test_the_guard_reads_the_pg_tier_files_that_pytest_skips_locally():
    """The point of reading source: these files' tests are skipped without a Postgres, yet the guard still sees their literals."""
    seen = set()
    for p in (Path(__file__).parent).rglob("test_*.py"):
        src = p.read_text()
        if any(m in src for m in SKIPPED_TIER_MARKERS):
            seen.add(p.name)
    assert {"test_egeria_resync_definition_checks.py", "test_facts.py"} <= seen
