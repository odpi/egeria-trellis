"""CSV batch import/export — inventory in, scorecard out.

The design rule under test is that **import reads intent and never state**.
Every observed column is prefixed `status_` and ignored on the way back in.

Without that rule the format is ambiguous in a way nothing can resolve later: is
`status_last_surveyed_at=2026-08-01` a record of something that happened, or an
instruction to make it so? And an export taken on Tuesday still claims
`status_cataloged=yes` on Friday after Egeria was reset — a file that reads as a
record while being wrong. Letting import act on those columns would write that
fiction into the registry.

So the round-trip test here is not a convenience check. It is the one that keeps
a scorecard from becoming an instruction sheet.
"""
from __future__ import annotations

import csv

import pytest

from resource_explorer.batch_io import (
    ALL_COLUMNS,
    STATUS_COLUMNS,
    export_rows,
    parse_csv,
    plan_import,
    resource_key,
    write_csv,
)
from resource_explorer.registry import Project, ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(db_path=str(tmp_path / "t.db"))
    reg.add(Project(slug="sqlglot", display_name="sqlglot",
                    github_url="https://github.com/tobymao/sqlglot",
                    description="", collections=["sqlglot_docs"]))
    return reg


def _write(tmp_path, text):
    p = tmp_path / "in.csv"
    p.write_text(text, encoding="utf-8")
    return p


class TestIdentity:
    """RE's slug is derived, so the interchange format keys on the address the
    user actually knows — and matches it the way the registry does."""

    @pytest.mark.parametrize("addr", [
        "https://github.com/tobymao/sqlglot",
        "https://github.com/tobymao/sqlglot.git",
        "https://github.com/tobymao/sqlglot/",
        "HTTPS://GitHub.com/tobymao/sqlglot",
    ])
    def test_repo_urls_normalize_to_one_key(self, addr):
        assert resource_key("repo", addr) == resource_key(
            "repo", "https://github.com/tobymao/sqlglot")

    def test_different_repos_do_not_collide(self):
        assert resource_key("repo", "https://github.com/a/x") != resource_key(
            "repo", "https://github.com/b/x")


class TestAlreadyLoadedDetection:

    def test_a_registered_repo_is_recognised_however_it_is_written(self, registry, tmp_path):
        p = _write(tmp_path, "resource_type,address\n"
                             "repo,https://github.com/tobymao/sqlglot.git\n")
        plan = plan_import(registry, parse_csv(p))
        assert len(plan.already_registered) == 1
        assert not plan.to_register

    def test_a_new_repo_is_queued(self, registry, tmp_path):
        p = _write(tmp_path, "resource_type,address\nrepo,https://github.com/apache/airflow\n")
        plan = plan_import(registry, parse_csv(p))
        assert [r.address for r in plan.to_register] == ["https://github.com/apache/airflow"]

    def test_the_same_repo_twice_in_one_file_is_caught(self, registry, tmp_path):
        """Dedup has to happen within the file too, not just against the
        registry — otherwise the second row fails at import time with a
        confusing "already registered" for something registered seconds ago."""
        p = _write(tmp_path, "resource_type,address\n"
                             "repo,https://github.com/apache/airflow\n"
                             "repo,https://github.com/apache/airflow/\n")
        plan = plan_import(registry, parse_csv(p))
        assert len(plan.to_register) == 1
        assert len(plan.duplicate_in_file) == 1


class TestValidation:

    def test_a_row_without_an_address_is_invalid_not_skipped(self, registry, tmp_path):
        """Silently dropping a row from a 500-row file is how you end up with
        497 repos and no idea which three are missing."""
        p = _write(tmp_path, "resource_type,address\nrepo,\n")
        plan = plan_import(registry, parse_csv(p))
        assert len(plan.invalid) == 1 and "no address" in plan.invalid[0].errors[0]

    def test_an_unknown_disposition_is_rejected(self, registry, tmp_path):
        p = _write(tmp_path, "resource_type,address,disposition\n"
                             "repo,https://github.com/a/b,definitely-maybe\n")
        assert len(plan_import(registry, parse_csv(p)).invalid) == 1

    def test_unsupported_types_say_why_rather_than_disappearing(self, registry, tmp_path):
        p = _write(tmp_path, "resource_type,address\nfilesystem,/mnt/data\n")
        plan = plan_import(registry, parse_csv(p))
        assert len(plan.unsupported_type) == 1
        # CSV in/out slice: file systems still have no import path, and the
        # reason is the real one now (databases are importable through `server`
        # / `connection_ref`; their old "needs credentials" reason is gone).
        assert "no file-system import path" in plan.unsupported_type[0].errors[0]

    def test_a_file_with_no_address_column_fails_loudly(self, tmp_path):
        p = _write(tmp_path, "name,url\nfoo,bar\n")
        with pytest.raises(ValueError, match="address"):
            parse_csv(p)

    def test_resource_type_defaults_to_repo(self, tmp_path):
        """The common case is a list of repos; requiring the column on every row
        of a hand-written file earns nothing."""
        p = _write(tmp_path, "address\nhttps://github.com/a/b\n")
        assert parse_csv(p)[0].resource_type == "repo"


class TestImportReadsIntentNeverState:
    """The rule the whole format rests on."""

    def test_status_columns_are_ignored_on_import(self, registry, tmp_path):
        """A row asserting it is registered, cataloged and surveyed must still be
        classified from the registry alone."""
        p = _write(
            tmp_path,
            "resource_type,address," + ",".join(STATUS_COLUMNS) + "\n"
            "repo,https://github.com/apache/airflow,yes,airflow,yes,ok,2026-01-01,2026-01-01,active,yes\n")
        plan = plan_import(registry, parse_csv(p))
        # It claims to be registered. It is not, so it is queued for registration.
        assert len(plan.to_register) == 1
        assert not plan.already_registered

    def test_an_exported_file_reimports_as_a_complete_no_op(self, registry, tmp_path):
        """The round-trip. Everything already exists, so nothing is queued —
        and the status_ columns the export wrote did not confuse the classifier."""
        out = tmp_path / "inv.csv"
        write_csv(export_rows(registry), out)

        plan = plan_import(registry, parse_csv(out))
        assert plan.to_register == []
        assert len(plan.already_registered) == 1
        assert plan.invalid == [] and plan.duplicate_in_file == []

    def test_intent_columns_do_survive_the_round_trip(self, registry, tmp_path):
        """The other half: what a human set must come back, or the file is not
        an inventory, only a report."""
        registry.set_disposition("https://github.com/tobymao/sqlglot", "tracking",
                                 reason="core dependency")
        out = tmp_path / "inv.csv"
        write_csv(export_rows(registry), out)

        row = parse_csv(out)[0]
        assert row.disposition == "tracking"
        assert row.disposition_reason == "core dependency"


class TestExport:

    def test_every_column_is_written_every_time(self, registry, tmp_path):
        """Columns that vary with content cannot be diffed against last week's,
        which is most of what a running scorecard is for."""
        out = tmp_path / "inv.csv"
        write_csv(export_rows(registry), out)
        with out.open(newline="") as fh:
            assert csv.DictReader(fh).fieldnames == list(ALL_COLUMNS)

    def test_observed_state_reflects_the_registry(self, registry, tmp_path):
        rows = {r["status_slug"]: r for r in export_rows(registry)}
        assert rows["sqlglot"]["status_cataloged"] == "no"      # no GUID set
        assert rows["sqlglot"]["status_indexed"] == "yes"       # has collections

        registry.set_egeria_asset_guid("sqlglot", "guid-1")
        rows = {r["status_slug"]: r for r in export_rows(registry)}
        assert rows["sqlglot"]["status_cataloged"] == "yes"

    def test_a_stale_egeria_link_is_visible_in_the_scorecard(self, registry):
        """The condition most worth seeing across a whole corpus at once."""
        registry.mark_egeria_linkage_stale("repo", "sqlglot", "guid-1", "gone")
        rows = {r["status_slug"]: r for r in export_rows(registry)}
        assert rows["sqlglot"]["status_egeria_link"] == "stale"


class TestParseText:
    """Both shapes people actually arrive with: a spreadsheet export, and a list
    pasted out of a wiki. Rejecting the second would push them back to
    registering one at a time, which is the thing this exists to stop."""

    def test_a_plain_url_list_is_accepted(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("https://github.com/a/b\nhttps://github.com/c/d\n")
        assert [r.address for r in rows] == ["https://github.com/a/b", "https://github.com/c/d"]
        assert all(r.resource_type == "repo" for r in rows)

    def test_comments_and_blank_lines_are_skipped(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("# batch 1\n\nhttps://github.com/a/b\n\n# end\n")
        assert [r.address for r in rows] == ["https://github.com/a/b"]

    def test_original_line_numbers_survive_filtering(self):
        """Error messages have to point at the file the user is looking at, not
        at the filtered subset."""
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("# header\n\nhttps://github.com/a/b\n")
        assert rows[0].line == 3

    def test_csv_text_keeps_its_columns(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("resource_type,address,group\nrepo,https://github.com/a/b,asf\n")
        assert rows[0].group == "asf"

    def test_prose_is_flagged_rather_than_treated_as_a_url(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("just some prose here\n")
        assert rows[0].errors


class TestFromListRoute:
    """The uploaded list lands in the same review table as a search — the point
    being that nothing is registered until a human selects rows."""

    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        # The route imports ProjectRegistry inside the function, so it must be
        # patched where it is defined rather than on the route module.
        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **kw: registry)

        async def fake_list_urls(urls, reg):
            return [{"full_name": u.rsplit("/", 2)[-2] + "/" + u.rsplit("/", 1)[-1],
                     "html_url": u, "description": "", "stars": 1, "language": "Python",
                     "license": "", "forks": 0, "archived": False, "fork": False,
                     "updated_at": ""} for u in urls]

        monkeypatch.setattr("resource_explorer.web.routes.discovery._run_list_urls",
                            fake_list_urls)
        return TestClient(app)

    def test_a_registered_repo_comes_back_marked(self, client):
        r = client.post("/api/discovery/from-list",
                        json={"text": "https://github.com/tobymao/sqlglot\n"})
        assert r.status_code == 200
        assert r.json()["repos"][0]["already_registered"] is True

    def test_a_new_repo_is_not_marked(self, client):
        r = client.post("/api/discovery/from-list",
                        json={"text": "https://github.com/apache/airflow\n"})
        assert r.json()["repos"][0]["already_registered"] is False

    def test_the_route_registers_nothing(self, client, registry):
        before = len(registry.list_all())
        client.post("/api/discovery/from-list",
                    json={"text": "https://github.com/apache/airflow\n"})
        assert len(registry.list_all()) == before

    def test_unusable_input_explains_itself(self, client):
        r = client.post("/api/discovery/from-list", json={"text": "not a url at all\n"})
        assert r.status_code == 400
        assert "address" in r.json()["detail"] or "URL" in r.json()["detail"]

    def test_an_exported_scorecard_can_be_fed_straight_back(self, client, registry):
        """The status_ columns must not confuse the parser — same round-trip
        guarantee as the CLI path."""
        from resource_explorer.batch_io import export_rows, write_csv

        import io as _io
        rows = export_rows(registry)
        buf = _io.StringIO()
        import csv as _csv

        from resource_explorer.batch_io import ALL_COLUMNS
        w = _csv.DictWriter(buf, fieldnames=list(ALL_COLUMNS))
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in ALL_COLUMNS})

        resp = client.post("/api/discovery/from-list", json={"text": buf.getvalue()})
        assert resp.status_code == 200
        assert all(x["already_registered"] for x in resp.json()["repos"])


class TestInventoryDownloadRoute:
    """The other half of "Load from file". It was CLI-only at first, which made
    the pane offer an import with no matching export — the asymmetry a user
    noticed before any test did."""

    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **kw: registry)
        return TestClient(app)

    def test_it_downloads_as_a_dated_csv_attachment(self, client):
        r = client.get("/api/discovery/inventory.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        # Dated, because the point of a scorecard is diffing one against another
        # and "inventory.csv (3)" makes that needlessly hard.
        assert "attachment" in r.headers["content-disposition"]
        assert "re-inventory-" in r.headers["content-disposition"]

    def test_it_carries_the_full_column_set(self, client):
        import csv as _csv
        import io as _io

        from resource_explorer.batch_io import ALL_COLUMNS

        r = client.get("/api/discovery/inventory.csv")
        assert _csv.DictReader(_io.StringIO(r.text)).fieldnames == list(ALL_COLUMNS)

    def test_what_it_downloads_can_be_loaded_straight_back(self, client, registry):
        """The pairing is only real if the round-trip holds through the routes,
        not just through the library."""
        csv_text = client.get("/api/discovery/inventory.csv").text

        async def fake_list_urls(urls, reg):
            return [{"full_name": "x/y", "html_url": u, "description": "", "stars": 0,
                     "language": "", "license": "", "forks": 0, "archived": False,
                     "fork": False, "updated_at": ""} for u in urls]

        import resource_explorer.web.routes.discovery as disc
        original = disc._run_list_urls
        disc._run_list_urls = fake_list_urls
        try:
            resp = client.post("/api/discovery/from-list", json={"text": csv_text})
        finally:
            disc._run_list_urls = original

        assert resp.status_code == 200
        assert all(x["already_registered"] for x in resp.json()["repos"])

    def test_it_reflects_the_registry_at_request_time(self, client, registry):
        """No caching: a stale scorecard that looks authoritative is the failure
        mode this format is most likely to produce."""
        before = len(client.get("/api/discovery/inventory.csv").text.strip().splitlines())

        registry.add(Project(slug="newone", display_name="New One",
                             github_url="https://github.com/n/one", description=""))
        after = len(client.get("/api/discovery/inventory.csv").text.strip().splitlines())
        assert after == before + 1


class TestLoadReportsWhatHappenedToTheFile:
    """A file that loses rows on the way in has to say so.

    The route first returned a bare list, so 50 rows yielding 47 results looked
    identical to a file of 47 — the three dropped rows existed only in a server
    log. "I loaded the file and got no confirmation" is what that produces.
    """

    @pytest.fixture
    def client(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **kw: registry)

        async def fake_list_urls(urls, reg):
            # Anything with "missing" in it is treated as unfetchable, standing in
            # for a renamed or private repo — the common case in an edited file.
            return [{"full_name": "x/y", "html_url": u, "description": "", "stars": 0,
                     "language": "", "license": "", "forks": 0, "archived": False,
                     "fork": False, "updated_at": ""}
                    for u in urls if "missing" not in u]

        monkeypatch.setattr("resource_explorer.web.routes.discovery._run_list_urls",
                            fake_list_urls)
        return TestClient(app)

    def test_it_counts_rows_read_against_repos_listed(self, client):
        d = client.post("/api/discovery/from-list", json={"text":
            "https://github.com/a/b\nhttps://github.com/c/d\n"}).json()
        assert d["rows_read"] == 2 and len(d["repos"]) == 2

    def test_skipped_rows_are_named_with_their_line(self, client):
        d = client.post("/api/discovery/from-list", json={"text":
            "https://github.com/a/b\nthis is not a url\n"}).json()
        assert len(d["skipped"]) == 1 and "line 2" in d["skipped"][0]

    def test_unreachable_repos_are_named_not_just_dropped(self, client):
        """The quiet one: GitHub not returning a repo removed it from the results
        with nothing said, so an edited file with a renamed repo came back short."""
        d = client.post("/api/discovery/from-list", json={"text":
            "https://github.com/a/b\nhttps://github.com/org/missing-repo\n"}).json()
        assert d["usable"] == 2
        assert len(d["repos"]) == 1
        assert d["unreachable"] == ["https://github.com/org/missing-repo"]

    def test_an_unreachable_repo_does_not_fail_the_whole_load(self, client):
        """It did: the unreachable-reporting loop referenced an undefined `log`,
        so one unfetchable repo raised NameError and 500'd the request — latent
        until a list actually contained one."""
        r = client.post("/api/discovery/from-list", json={"text":
            "https://github.com/a/b\nhttps://github.com/org/missing-repo\n"})
        assert r.status_code == 200

    def test_already_registered_is_counted(self, client):
        d = client.post("/api/discovery/from-list", json={"text":
            "https://github.com/tobymao/sqlglot\nhttps://github.com/a/b\n"}).json()
        assert d["already_registered"] == 1


class TestOrganisationUrlsAreExpanded:
    """A file may list an account page rather than a repo.

    "https://github.com/apache" is the obvious thing to copy when the point is
    "everything these people publish", and fetching it as a repo simply fails —
    so a file of foundation pages came back empty with nothing explaining why.
    """

    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/apache", "apache"),
        ("github.com/cncf", "cncf"),
        ("https://github.com/apache/", "apache"),
        # GitHub's own canonical org URL, and what the address bar shows.
        ("https://github.com/orgs/apache", "apache"),
    ])
    def test_account_urls_are_recognised(self, url, expected):
        from resource_explorer.batch_io import github_org_from_url

        assert github_org_from_url(url) == expected

    @pytest.mark.parametrize("url", [
        "https://github.com/apache/airflow",     # a repo
        "https://gitlab.com/some-group",         # not GitHub
        "https://github.com/topics/python",      # GitHub's own pages
        "https://github.com/search",
        "",
    ])
    def test_non_accounts_are_left_alone(self, url):
        """A false positive here is expensive: a repo mistaken for an account
        would pull in that account's entire output instead of the one repo."""
        from resource_explorer.batch_io import github_org_from_url

        assert github_org_from_url(url) == ""

    def test_an_account_row_expands_and_says_so(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **kw: registry)

        async def fake_expand(org):
            return [f"https://github.com/{org}/one", f"https://github.com/{org}/two"]

        async def fake_list_urls(urls, reg):
            return [{"full_name": u.split("github.com/")[-1], "html_url": u,
                     "description": "", "stars": 0, "language": "", "license": "",
                     "forks": 0, "archived": False, "fork": False, "updated_at": ""}
                    for u in urls]

        import resource_explorer.web.routes.discovery as disc
        monkeypatch.setattr(disc, "_expand_org", fake_expand)
        monkeypatch.setattr(disc, "_run_list_urls", fake_list_urls)

        d = TestClient(app).post("/api/discovery/from-list",
                                 json={"text": "https://github.com/acme\n"}).json()

        assert len(d["repos"]) == 2
        # One line in, two repos out — the count has to be explained, or it reads
        # as the loader inventing rows.
        assert d["rows_read"] == 1
        assert d["expanded_orgs"] == [{"org": "acme", "count": 2, "truncated": False}]

    def test_a_failed_expansion_is_reported_not_swallowed(self, registry, monkeypatch):
        from fastapi.testclient import TestClient

        from resource_explorer.web.app import app

        monkeypatch.setattr("resource_explorer.registry.ProjectRegistry",
                            lambda *a, **kw: registry)

        async def boom(org):
            raise RuntimeError("rate limited")

        async def fake_list_urls(urls, reg):
            return [{"full_name": "x/y", "html_url": u, "description": "", "stars": 0,
                     "language": "", "license": "", "forks": 0, "archived": False,
                     "fork": False, "updated_at": ""} for u in urls]

        import resource_explorer.web.routes.discovery as disc
        monkeypatch.setattr(disc, "_expand_org", boom)
        monkeypatch.setattr(disc, "_run_list_urls", fake_list_urls)

        d = TestClient(app).post("/api/discovery/from-list", json={
            "text": "https://github.com/acme\nhttps://github.com/a/b\n"}).json()

        assert d["repos"], "one bad org must not lose the rest of the file"
        assert d["expanded_orgs"][0]["error"]


class TestSkippedRowsAreIdentifiable:
    """A skipped row has to say where it was and what was on it.

    The line number alone is enough to find it in an editor; the value is what
    makes the problem obvious without going to look. In a 500-row file that is
    the difference between a hunt and a fix. The CLI already printed the value
    and the web route did not — same data, two formats, and the surface people
    actually use had the less useful one.
    """

    def test_it_names_the_line_and_the_value(self):
        from resource_explorer.batch_io import ImportRow, describe_skipped

        row = ImportRow(resource_type="repo", address="htps://typo/x y", line=312,
                        errors=["not a URL (contains a space)"])
        out = describe_skipped(row)
        assert "line 312" in out
        assert "htps://typo/x y" in out
        assert "not a URL" in out

    def test_an_empty_value_says_so_rather_than_showing_nothing(self):
        from resource_explorer.batch_io import ImportRow, describe_skipped

        row = ImportRow(resource_type="repo", address="", line=7, errors=["no address"])
        assert "(empty)" in describe_skipped(row)

    def test_a_huge_value_is_truncated(self):
        """A malformed CSV row can be an entire record collapsed into one cell;
        one bad row must not flood the report."""
        from resource_explorer.batch_io import ImportRow, describe_skipped

        row = ImportRow(resource_type="repo", address="x" * 500, line=1, errors=["bad"])
        out = describe_skipped(row)
        assert len(out) < 200 and "…" in out

    def test_both_surfaces_use_the_same_formatter(self):
        """They drifted once already. Assert against the source rather than
        trusting that two call sites stay in step."""
        import inspect

        from resource_explorer.cli import main as cli
        from resource_explorer.web.routes import discovery

        assert "describe_skipped" in inspect.getsource(cli.import_resources)
        assert "describe_skipped" in inspect.getsource(discovery.discover_from_list)


class TestMangledCsvIsDiagnosedNotFetched:
    """An exported file whose header was edited or dropped falls back to
    plain-list mode, where each comma-separated row becomes one bogus "URL".

    Those then fail to fetch and are reported as unreachable — pointing at
    GitHub or the network, when the actual problem is the header. That is a
    long way to send someone for a one-line fix.
    """

    def test_a_comma_row_in_plain_mode_is_diagnosed(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("https://github.com/a/b,,,,tracking\n")
        assert rows[0].errors and "no recognised header" in rows[0].errors[0]

    def test_an_empty_csv_row_is_diagnosed_too(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text(",,,,\n")
        assert rows[0].errors

    def test_a_proper_csv_is_unaffected(self):
        """The diagnosis must not fire for a file with a real header — that is
        the normal case and it contains commas on every line."""
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("resource_type,address,group\nrepo,https://github.com/a/b,asf\n")
        assert not rows[0].errors and rows[0].group == "asf"

    def test_a_plain_url_list_is_still_unaffected(self):
        from resource_explorer.batch_io import parse_csv_text

        rows = parse_csv_text("https://github.com/a/b\n")
        assert not rows[0].errors
