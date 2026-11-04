"""Open data from repositories: the dataset descriptor sub-surveyor, the `dataset` role, the sentence, and
import-from-repository (no network, no Egeria; the DataCite answer is the recorded fixture)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from types import SimpleNamespace

import pytest

from resource_explorer.github import repo_role as rr
from resource_explorer.open_data import service
from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.store import OpenDataStore
from resource_explorer.registry import Project, ProjectRegistry, ProjectStatus
from resource_explorer.surveyors.sub_surveyors import dataset_descriptor as dd

FIX = Path(__file__).parent / "fixtures" / "open_data"
DATACITE_DOI = "10.5281/zenodo.3260719"            # the recorded datacite_doi.json record


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _tree(tmp_path: Path, **files) -> Path:
    for rel, text in files.items():
        _write(tmp_path, rel.replace("__", "/"), text)
    return tmp_path


DATAPACKAGE = json.dumps({
    "name": "air-quality", "title": "Air quality", "licenses": [{"name": "CC-BY-4.0"}],
    "resources": [{"path": "data/a.csv", "format": "csv", "bytes": 100}, {"path": "data/b.csv", "bytes": 50},
                  {"path": "data/c.csv"}, {"path": "https://example.org/d.csv"}]})
CROISSANT = json.dumps({
    "@type": "sc:Dataset", "name": "mnist-ish", "license": "https://creativecommons.org/licenses/by/4.0/",
    "citeAs": "Doe 2024, https://doi.org/10.1234/abc.5", "distribution": [
        {"contentUrl": "train.parquet", "encodingFormat": "application/x-parquet", "contentSize": "2 MB"}]})
CFF_DATASET = "cff-version: 1.2.0\ntitle: Census extract\ntype: dataset\ndoi: 10.9999/census.1\nlicense: CC0-1.0\nauthors:\n- family-names: Roe\n  given-names: Ann\n"
CFF_SOFTWARE = "cff-version: 1.2.0\ntitle: A tool\ndoi: 10.9999/tool.1\nlicense: MIT\n"
HF_CARD = "---\nlicense: cc-by-sa-4.0\ndataset_info:\n  features: []\nconfigs:\n- config_name: default\n  data_files:\n  - split: train\n    path: data/train.parquet\n---\n# Card\nSee https://huggingface.co/datasets/acme/cards\n"


# ── A1: the reader, one tmp tree per descriptor kind ───────────────────────────────────

def test_datapackage_is_read_with_licence_flags_and_declared_files(tmp_path):
    rows = dd.read_descriptors(_tree(tmp_path, **{"datapackage.json": DATAPACKAGE}))
    assert [r["kind"] for r in rows] == ["datapackage"]
    r = rows[0]
    assert (r["identifier"], r["identifier_kind"], r["title"]) == ("air-quality", "datapackage_name", "Air quality")
    assert r["licence_id"] == "CC-BY-4.0" and r["licence"] == {"attribution": True, "share_alike": False, "commercial": True}
    assert r["doi"] == NOT_REPORTED and r["publisher"] == NOT_REPORTED
    assert [f["path"] for f in r["data_files"]][:2] == ["data/a.csv", "data/b.csv"]
    assert r["data_files"][0]["format"] == "CSV" and r["data_files"][2]["bytes"] == NOT_REPORTED


def test_croissant_needs_dataset_type_and_metadata_json_without_it_is_ignored(tmp_path):
    root = _tree(tmp_path, **{"croissant.json": CROISSANT, "docs__metadata.json": json.dumps({"@type": "Thing", "name": "x"})})
    rows = dd.read_descriptors(root)
    assert [r["kind"] for r in rows] == ["croissant"]
    r = rows[0]
    assert r["doi"] == "10.1234/abc.5" and r["identifier_kind"] == "doi"       # the DOI in citeAs wins over the name
    assert r["licence_id"] == "cc-by-4.0" and r["data_files"][0]["bytes"] == 2_000_000
    assert r["data_files"][0]["format"] == "X-PARQUET"


def test_citation_cff_names_a_dataset_only_when_it_says_type_dataset(tmp_path):
    ds = dd.read_descriptors(_tree(tmp_path / "a", **{"CITATION.cff": CFF_DATASET}))[0]
    sw = dd.read_descriptors(_tree(tmp_path / "b", **{"CITATION.cff": CFF_SOFTWARE}))[0]
    assert ds["names_a_dataset"] is True and ds["identifier"] == "10.9999/census.1" and ds["authors"] == ["Ann Roe"]
    assert sw["names_a_dataset"] is False and sw["doi"] == "10.9999/tool.1"           # kept, but does not name a dataset


def test_a_readme_doi_badge_is_kept_but_does_not_name_a_dataset(tmp_path):
    root = _tree(tmp_path, **{"README.md": "# x\n[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.1234567.svg)](https://doi.org/10.5281/zenodo.1234567)\n"})
    r = dd.read_descriptors(root)[0]
    assert (r["kind"], r["doi"], r["names_a_dataset"]) == ("readme_doi", "10.5281/zenodo.1234567", False)


def test_a_huggingface_card_needs_license_and_dataset_info(tmp_path):
    root = _tree(tmp_path / "a", **{"README.md": HF_CARD})
    r = [x for x in dd.read_descriptors(root) if x["kind"] == "hf_card"][0]
    assert r["identifier"] == "acme/cards" and r["identifier_kind"] == "hf_repo" and r["licence_id"] == "cc-by-sa-4.0"
    assert r["data_files"][0]["path"] == "data/train.parquet"
    only_license = _tree(tmp_path / "b", **{"README.md": "---\nlicense: mit\n---\n# lib\n"})
    assert dd.read_descriptors(only_license) == []


def test_every_descriptor_found_is_kept_in_the_documented_order(tmp_path):
    root = _tree(tmp_path, **{"README.md": HF_CARD + "doi.org/10.1234/x", "CITATION.cff": CFF_DATASET,
                              "datapackage.json": DATAPACKAGE, "croissant.json": CROISSANT})
    assert [r["kind"] for r in dd.read_descriptors(root)] == ["datapackage", "croissant", "citation_cff", "readme_doi", "hf_card"]


def test_vendored_and_too_deep_descriptors_are_not_read(tmp_path):
    root = _tree(tmp_path, **{"node_modules__pkg__datapackage.json": DATAPACKAGE, "a__b__c__d__datapackage.json": DATAPACKAGE})
    assert dd.read_descriptors(root) == []


def test_a_tree_with_data_files_and_no_descriptor_reads_none(tmp_path):
    assert dd.read_descriptors(_tree(tmp_path, **{"data__x.csv": "a,b\n1,2\n", "README.md": "# just data\n"})) == []


# ── the declared files against the tree ─────────────────────────────────────────────────

def test_three_of_four_declared_files_present_in_the_tree(tmp_path):
    d = dd.read_descriptors(_tree(tmp_path, **{"datapackage.json": json.dumps({"name": "n", "resources": [
        {"path": f"{c}.csv"} for c in "abcd"]})}))[0]
    cc = dd.cross_check(d, ["a.csv", "b.csv", "c.csv", "README.md"])
    assert cc["words"] == "3 of 4 declared files present in the tree" and cc["missing"] == ["d.csv"]


def test_remote_declarations_are_not_counted_as_missing(tmp_path):
    d = dd.read_descriptors(_tree(tmp_path, **{"datapackage.json": DATAPACKAGE}))[0]
    cc = dd.cross_check(d, ["data/a.csv", "data/b.csv"])
    assert cc["declared"] == 3 and cc["present"] == 2 and cc["remote"] == 1
    assert cc["words"] == "2 of 3 declared files present in the tree (+1 remote)"
    none = dd._row("datapackage", "datapackage.json")
    assert dd.cross_check(none, [])["words"] == NOT_REPORTED


# ── A2: the role, ranked, and the sentence ──────────────────────────────────────────────────

def _tree_mock(entries, truncated=False):
    tree = MagicMock()
    tree.truncated = truncated
    items = []
    for path, size in entries:
        e = MagicMock()
        e.path, e.type, e.size = path, "blob", size
        items.append(e)
    tree.tree = items
    return tree


def _gh(entries, readme=None, contents=None):
    from github import GithubException

    repo = MagicMock()
    repo.full_name, repo.name, repo.default_branch, repo.description, repo.homepage = "o/r", "r", "main", "", ""
    repo.owner.login = "o"
    repo.get_topics.return_value = []
    repo.get_git_tree.return_value = _tree_mock(entries)
    if readme is not None:
        rd = MagicMock()
        rd.decoded_content = readme.encode()
        repo.get_readme.return_value = rd
    else:
        repo.get_readme.side_effect = GithubException(404, data={"message": "Not Found"})

    def get_contents(path):
        if path in (contents or {}):
            c = MagicMock()
            c.decoded_content = contents[path].encode()
            return c
        raise GithubException(404, data={"message": "Not Found"})

    repo.get_contents.side_effect = get_contents
    gh = MagicMock()
    gh.get_repo.return_value = repo
    gh.get_organization.side_effect = GithubException(404, data={"message": "Not Found"})
    gh.get_user.return_value.get_repos.return_value = []
    return gh


def test_eight_roles_and_dataset_is_ranked_in_the_precedence():
    assert len(rr.ROLE_VALUES) == 8 and rr.ROLE_DATASET in rr.ROLE_VALUES and rr.ROLE_DATASET in rr._PRIMARY_PRECEDENCE
    assert rr._PRIMARY_PRECEDENCE.index(rr.ROLE_SAMPLES) < rr._PRIMARY_PRECEDENCE.index(rr.ROLE_DATASET) \
        < rr._PRIMARY_PRECEDENCE.index(rr.ROLE_LIBRARY)


def test_a_datapackage_makes_the_repository_a_dataset_with_the_descriptor_as_evidence():
    gh = _gh([("datapackage.json", 400), ("data/a.csv", 10)], contents={"datapackage.json": DATAPACKAGE})
    c = rr.classify_repo_role("o/r", client=gh)
    assert c.primary.role == rr.ROLE_DATASET
    assert ("dataset-descriptor", "datapackage.json (air-quality)") in [(e.signal, e.detail) for e in c.primary.evidence]


def test_data_files_at_half_the_bytes_with_no_build_tooling_is_a_dataset_and_tooling_vetoes_it():
    heavy = [("data/big.parquet", 9_000), ("README.md", 500), ("notes.txt", 500)]
    assert rr.classify_repo_role("o/r", client=_gh(heavy)).primary.role == rr.ROLE_DATASET
    assert rr.classify_repo_role("o/r", client=_gh(heavy + [("pyproject.toml", 10)])).has_role(rr.ROLE_DATASET) is False
    unsized = [("data/big.parquet", None), ("README.md", None)]
    assert rr.classify_repo_role("o/r", client=_gh(unsized)).has_role(rr.ROLE_DATASET) is False      # no size: no proportion


def test_a_huggingface_card_and_a_readme_doi_alone_differ():
    assert rr.classify_repo_role("o/r", client=_gh([("README.md", 10)], readme=HF_CARD)).has_role(rr.ROLE_DATASET)
    badge = "# lib\n[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.1234567.svg)]\n"
    assert not rr.classify_repo_role("o/r", client=_gh([("README.md", 10)], readme=badge)).has_role(rr.ROLE_DATASET)


def test_the_sentence_for_a_dataset_repository(tmp_path):
    d = dd.read_descriptors(_tree(tmp_path, **{"datapackage.json": json.dumps(
        {"name": "n", "id": "https://doi.org/10.1234/abc.9", "licenses": [{"name": "CC-BY-4.0"}]})}))
    assert dd.dataset_sentence(d) == "a dataset repository · identity DOI 10.1234/abc.9 · licence CC-BY-4.0 (from datapackage.json)"
    named = dd.read_descriptors(_tree(tmp_path / "n", **{"datapackage.json": DATAPACKAGE}))
    assert dd.dataset_sentence(named) == ("a dataset repository · identity datapackage name air-quality "
                                          "(from datapackage.json) · licence CC-BY-4.0 (from datapackage.json)")


# ── the stored side: findings, the kind row, the DDL ─────────────────────────────────────────

@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "repo.db"))
    r.add(Project(slug="acme-data", display_name="Acme data", github_url="https://github.com/acme/data",
                  status=ProjectStatus.ACTIVE))
    return r


def _inventory(reg, slug, files: dict[str, int]):
    reg.upsert_file_inventory(slug, list(files.items()), {})


def test_descriptors_are_stored_as_findings_and_a_removed_descriptor_stops_answering(reg, tmp_path):
    root = _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE})
    assert len(dd.store_descriptors(reg, "acme-data", root)) == 1
    assert [d["kind"] for d in dd.descriptors_from(reg.query_findings("acme-data", dd.FINDINGS_KIND))] == ["datapackage"]
    (root / "datapackage.json").unlink()
    dd.store_descriptors(reg, "acme-data", root, surveyed_at="2999-01-01T00:00:00")
    stored = reg.query_findings("acme-data", dd.FINDINGS_KIND)
    assert dd.descriptors_from(stored) == [] and stored[0]["check_name"] == "no_descriptor"      # a measured zero


def _inventory_step(reg, root, at="2031-02-03T04:05:06"):
    from resource_explorer.surveyors.sub_surveyors.file_inventory import FileInventorySurveyor
    return FileInventorySurveyor(reg.get("acme-data"), reg, local_path=str(root), surveyed_at=at).run()


def test_the_coarse_profile_inventory_step_reads_the_descriptors(reg, tmp_path):
    root = _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE, "README.md": "x"})
    out = _inventory_step(reg, root)
    stored = reg.query_findings("acme-data", dd.FINDINGS_KIND)
    assert [d["kind"] for d in dd.descriptors_from(stored)] == ["datapackage"]
    assert dd.stored_descriptors(reg, "acme-data")[1] == "2031-02-03T04:05:06"        # the step's own run time
    assert "dataset descriptors: 1 found" in out[0].summary


def test_the_inventory_step_over_a_tree_without_a_descriptor_stores_the_one_none(reg, tmp_path):
    out = _inventory_step(reg, _tree(tmp_path / "t", **{"README.md": "x"}))
    stored = reg.query_findings("acme-data", dd.FINDINGS_KIND)
    assert [f["check_name"] for f in stored] == ["no_descriptor"] and "no dataset descriptor" in out[0].summary


def test_a_descriptor_reader_that_raises_is_a_note_and_the_inventory_still_stands(reg, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(dd, "store_descriptors", boom)
    out = _inventory_step(reg, _tree(tmp_path / "t", **{"README.md": "x"}))
    assert "dataset descriptors not read (RuntimeError)" in out[0].summary and "disk on fire" not in out[0].summary
    assert out[0].resource_properties["descriptor_error"] == "disk on fire"
    assert "disk on fire" in str(out[0].json_properties)
    assert len(reg.get_file_inventory_with_sizes("acme-data")) == 1


def test_the_served_words_say_not_read_yet_apart_from_no_identifier(reg, tmp_path):
    from resource_explorer.open_data.service import repository_import_state
    store = SimpleNamespace(registry=reg, dataset_by_identifier=lambda _i: None)
    assert dd.NOT_READ_YET_SENTENCE.endswith("run the Coarse Profile survey to read them.")
    assert repository_import_state(store, "acme-data")["words"] == dd.NOT_READ_YET_SENTENCE
    _inventory_step(reg, _tree(tmp_path / "t", **{"README.md": "x"}))
    assert repository_import_state(store, "acme-data")["words"] == dd.NO_IDENTIFIER_SENTENCE
    assert "(the Coarse Profile survey reads them)" in open(dd.__file__).read()


def test_the_kind_row_says_dataset_repository_or_data_files_without_a_descriptor(reg, tmp_path):
    _inventory(reg, "acme-data", {"data/a.csv": 100, "README.md": 5})
    only_data = dd.kind_finding(reg, "acme-data")
    assert only_data["summary"] == ("a repository with data files, not an open dataset by Resource Explorer's rule "
                                    "(no descriptor names it)")
    assert only_data["detail"]["state"] == "data_files_no_descriptor" and only_data["detail"]["can_import"] is False
    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE}))
    f = dd.kind_finding(reg, "acme-data")
    assert f["summary"].startswith("a dataset repository · identity datapackage name air-quality")
    assert f["detail"]["can_import"] is True
    assert f["detail"]["price"] == "no network · reads the descriptor already on disk (+1 DataCite call if a DOI)"
    assert f["detail"]["help"] == "Writes one open-data row to this app's registry and nothing to Egeria."


def test_no_data_and_no_descriptor_says_which_absence(reg, tmp_path):
    """W59: "not checked" (descriptors never read) and "no dataset signal" (read, nothing) both read as nothing before."""
    _inventory(reg, "acme-data", {"src/app.py": 10})
    f = dd.kind_finding(reg, "acme-data")
    assert f["detail"]["state"] == "not_checked" and f["summary"] == dd.NOT_CHECKED_SENTENCE
    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **{"README.md": "# app"}))
    f = dd.kind_finding(reg, "acme-data")
    assert f["detail"]["state"] == "none" and f["summary"] == "no dataset signal (no descriptor, no data files)"
    assert f["detail"]["can_import"] is False


def test_the_ddl_adds_repo_slug_and_the_reserved_portal_once(reg):
    store = OpenDataStore(reg)
    with reg._conn() as conn:
        cols = reg._get_table_columns(conn, "open_data")
    assert "repo_slug" in cols
    reg._migrate_open_data_tables()                                       # idempotent: a second start changes nothing
    assert [p["slug"] for p in store.list_portals(include_reserved=True) if p["slug"] == "repository"] == ["repository"]
    assert store.get_portal("repository")["api_kind"] == "repository"
    assert store.list_portals() == []                                     # never listed with the portals
    with reg._conn() as conn:
        conn.execute("ALTER TABLE open_data RENAME TO open_data_old")
        conn.execute("CREATE TABLE open_data (slug TEXT PRIMARY KEY, identifier TEXT NOT NULL UNIQUE)")
    reg._migrate_open_data_tables()                                       # an old table without the column gets it
    with reg._conn() as conn:
        assert "repo_slug" in reg._get_table_columns(conn, "open_data")


# ── A3: import ───────────────────────────────────────────────────────────────────────────────

class DataCiteReplay:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get_json(self, url, params=None, headers=None):
        self.calls.append(url)
        if self.fail:
            from resource_explorer.open_data.transport import TransportError
            raise TransportError("down")
        return json.loads((FIX / "datacite_doi.json").read_text())

    def get_text(self, url, params=None, headers=None):
        raise AssertionError(url)


def _stored(reg, tmp_path, **files):
    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **files))


def test_import_with_a_doi_uses_the_doi_resolves_it_once_and_keeps_both_records(reg, tmp_path):
    store, t = OpenDataStore(reg), DataCiteReplay()
    _stored(reg, tmp_path, **{"datapackage.json": json.dumps({"name": "n", "id": f"https://doi.org/{DATACITE_DOI}",
                                                             "licenses": [{"name": "cc-by-4.0"}],
                                                             "resources": [{"path": "a.csv", "bytes": 10}]})})
    _inventory(reg, "acme-data", {"a.csv": 10})
    r = service.import_from_repository(store, repo_slug="acme-data", transport=t, imported_by="tester")
    assert r["outcome"] == "imported" and r["identifier"] == DATACITE_DOI and r["datacite"] == "resolved" and len(t.calls) == 1
    row = store.dataset_by_identifier(DATACITE_DOI)
    assert row["portals"] == ["repository"] and row["identifier_kind"] == "doi" and row["repo_slug"] == "acme-data"
    assert row["record"]["descriptor"]["kind"] == "datapackage" and row["datacite"]["record"]["data"]["attributes"]["doi"] == DATACITE_DOI
    assert row["record"]["datacite"] == row["datacite"]
    assert row["formats"] == ["CSV"] and row["size"] == "10" and row["work_list_slug"] == r["work_list"]
    assert row["licence"] == {"attribution": True, "share_alike": False, "commercial": True}
    assert "1 of 1 declared files present in the tree" in r["words"]


def test_import_without_a_doi_qualifies_the_name_by_the_repository_and_makes_no_call(reg, tmp_path):
    store, t = OpenDataStore(reg), DataCiteReplay()
    _stored(reg, tmp_path, **{"datapackage.json": DATAPACKAGE})
    _inventory(reg, "acme-data", {"data/a.csv": 100, "data/b.csv": 50})
    r = service.import_from_repository(store, repo_slug="acme-data", descriptor_path="datapackage.json", transport=t)
    assert r["identifier"] == "acme/data:air-quality" and r["identifier_kind"] == "datapackage_name" and t.calls == []
    assert "2 of 3 declared files present in the tree (+1 remote)" in r["check"]
    row = store.dataset_by_identifier("acme/data:air-quality")
    assert row["datacite"] is None and row["size"] == NOT_REPORTED        # one local file states no size: no partial sum


def test_a_second_import_is_already_imported_not_a_duplicate(reg, tmp_path):
    store = OpenDataStore(reg)
    _stored(reg, tmp_path, **{"datapackage.json": DATAPACKAGE})
    service.import_from_repository(store, repo_slug="acme-data", transport=DataCiteReplay())
    again = service.import_from_repository(store, repo_slug="acme-data", transport=DataCiteReplay())
    assert again["outcome"] == "already_imported" and len(store.list_datasets()) == 1


def test_a_datacite_that_does_not_answer_does_not_stop_the_import_and_says_so(reg, tmp_path):
    store = OpenDataStore(reg)
    _stored(reg, tmp_path, **{"CITATION.cff": CFF_DATASET})
    r = service.import_from_repository(store, repo_slug="acme-data", transport=DataCiteReplay(fail=True))
    assert r["outcome"] == "imported" and r["datacite"] == "could not check" and "DataCite could not be checked" in r["words"]
    assert store.dataset_by_identifier("10.9999/census.1")["datacite"] is None


def test_refused_with_the_sentence_when_no_descriptor_carries_an_identifier(reg, tmp_path):
    store = OpenDataStore(reg)
    _stored(reg, tmp_path, **{"CITATION.cff": "cff-version: 1.2.0\ntitle: nameless\ntype: dataset\n"})
    with pytest.raises(service.OpenDataError) as exc:
        service.import_from_repository(store, repo_slug="acme-data", transport=DataCiteReplay())
    assert str(exc.value) == dd.NO_IDENTIFIER_SENTENCE
    assert store.list_datasets() == []


def test_a_repository_with_nothing_stored_is_refused_with_the_same_sentence(reg):
    with pytest.raises(service.OpenDataError) as exc:
        service.import_from_repository(OpenDataStore(reg), repo_slug="acme-data", transport=DataCiteReplay())
    assert str(exc.value) == dd.NO_IDENTIFIER_SENTENCE


def test_an_unknown_repository_or_descriptor_path_is_not_found(reg, tmp_path):
    store = OpenDataStore(reg)
    with pytest.raises(service.NotFound):
        service.import_from_repository(store, repo_slug="nope", transport=DataCiteReplay())
    _stored(reg, tmp_path, **{"datapackage.json": DATAPACKAGE})
    with pytest.raises(service.NotFound):
        service.import_from_repository(store, repo_slug="acme-data", descriptor_path="elsewhere.json", transport=DataCiteReplay())


# ── the routes and the reserved pseudo-portal ──────────────────────────────────────────────────

@pytest.fixture
def client(reg, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from resource_explorer.web.routes import open_data as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/open-data")
    monkeypatch.setattr(routes, "_transport", lambda: DataCiteReplay())
    monkeypatch.setattr(routes, "_store", lambda: OpenDataStore(reg))
    monkeypatch.setattr(routes, "_signed_in_user", lambda request: "tester")
    return TestClient(app)


def test_the_pseudo_portal_is_not_listed_searched_or_priced(client, reg):
    assert client.get("/api/open-data/portals").json() == []
    r = client.post("/api/open-data/portals/repository/search", json={"terms": "x"})
    assert r.status_code == 422 and "is not a portal" in r.json()["detail"]
    r = client.get("/api/open-data/portals/repository/price")
    assert r.status_code == 422 and "is not a portal" in r.json()["detail"]
    assert all(s["feed_url"] != "repository://" for s in client.get("/api/open-data/portal-seeds").json()["rows"])


def test_import_over_http_and_the_state_read_before_the_press(client, reg, tmp_path):
    _stored(reg, tmp_path, **{"datapackage.json": DATAPACKAGE})
    state = client.get("/api/open-data/repositories/acme-data/dataset").json()
    assert state["identifier"] == "acme/data:air-quality" and state["can_import"] is True
    assert state["price"].startswith("no network") and state["help"].startswith("Writes one open-data row")
    r = client.post("/api/open-data/import-from-repository", json={"repo_slug": "acme-data", "descriptor_path": "datapackage.json"})
    assert r.status_code == 200 and r.json()["outcome"] == "imported"
    assert client.get("/api/open-data/repositories/acme-data/dataset").json()["already_imported"] == r.json()["slug"]
    assert client.get(f"/api/open-data/datasets/{r.json()['slug']}").status_code == 200
    assert client.get("/api/open-data/portals").json() == []               # still not a portal after an import
    bad = client.post("/api/open-data/import-from-repository", json={"repo_slug": "nope"})
    assert bad.status_code == 404


def test_a_refusal_is_a_422_with_the_sentence(client, reg, tmp_path):
    _stored(reg, tmp_path, **{"CITATION.cff": "cff-version: 1.2.0\ntitle: nameless\ntype: dataset\n"})
    r = client.post("/api/open-data/import-from-repository", json={"repo_slug": "acme-data"})
    assert r.status_code == 422 and r.json()["detail"] == dd.NO_IDENTIFIER_SENTENCE
    assert client.get("/api/open-data/repositories/acme-data/dataset").json()["can_import"] is False


# ── served text ─────────────────────────────────────────────────────────────────────────────────

JS_ROOT = Path(__file__).resolve().parents[1] / "resource_explorer/web/static"


def test_the_served_scripts_carry_the_press_the_price_and_the_hover_help():
    views = (JS_ROOT / "next/stages/repo-analysis-views.js").read_text(encoding="utf-8")
    assert "Import as open data" in views and "data-dataset-import" in views and "d.help" in views and "d.price" in views
    admin = (JS_ROOT / "next/admin/open_data.js").read_text(encoding="utf-8")
    assert "From a registered repository" in admin and "Import as open data" in admin
    assert "Writes one open-data row to this app's registry and nothing to Egeria." in admin
    assert "no network · reads the descriptor already on disk (+1 DataCite call if a DOI)" in admin
    assert "importFromRepository" in (JS_ROOT / "re-api.js").read_text(encoding="utf-8")


def test_the_classification_surveyor_adds_the_dataset_kind_finding(reg, tmp_path, monkeypatch):
    from resource_explorer.github.expectations import ExpectationReport
    from resource_explorer.surveyors.sub_surveyors.repo_classification import RepoClassificationSurveyor

    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE}))
    report = ExpectationReport(owner_repo="acme/data", primary_role="dataset", roles=["dataset"],
                               dataset_evidence=["dataset-descriptor: datapackage.json (air-quality)"], gate="skip",
                               gate_reason="dataset role present and no structural evidence")
    monkeypatch.setattr("resource_explorer.github.expectations.build_report", lambda owner_repo: report)
    RepoClassificationSurveyor(reg.get("acme-data"), reg).run()
    by = {f["check_name"]: f for f in reg.query_findings("acme-data", "repo_classification")}
    assert by["dataset_kind"]["summary"].startswith("a dataset repository · identity datapackage name air-quality")
    assert json.loads(by["repo_role"]["detail_json"])["roles"] == ["dataset"]


def test_a_failure_reading_the_descriptors_is_a_visible_finding_not_a_missing_row(reg, tmp_path, monkeypatch):
    from resource_explorer.github.expectations import ExpectationReport
    from resource_explorer.surveyors.sub_surveyors.repo_classification import RepoClassificationSurveyor

    report = ExpectationReport(owner_repo="acme/data", primary_role="library", roles=["library"])
    monkeypatch.setattr("resource_explorer.github.expectations.build_report", lambda owner_repo: report)

    def boom(*a, **k):
        raise RuntimeError("registry went away")

    monkeypatch.setattr(dd, "kind_finding", boom)
    RepoClassificationSurveyor(reg.get("acme-data"), reg).run()
    by = {f["check_name"]: f for f in reg.query_findings("acme-data", "repo_classification")}
    assert by["dataset_kind"]["label"] == "error" and "registry went away" in by["dataset_kind"]["summary"]
    assert "repo_role" in by                                              # the classification itself is intact


def test_vendored_directories_are_not_searched_for_descriptors(tmp_path):
    root = _tree(tmp_path, **{"vendor__lib__datapackage.json": DATAPACKAGE, "datapackage.json": DATAPACKAGE})
    assert [r["path"] for r in dd.read_descriptors(root)] == ["datapackage.json"]


# ── the Architect's conditions on 15d69356 ──────────────────────────────────────────────────────

README_DOI = "# x\n[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.1234567.svg)](https://doi.org/10.5281/zenodo.1234567)\n"


def test_a_doi_alone_is_not_silence_and_not_not_a_dataset(reg, tmp_path):
    _inventory(reg, "acme-data", {"data/a.csv": 100})
    for files in ({"README.md": README_DOI}, {"CITATION.cff": CFF_SOFTWARE}):
        dd.store_descriptors(reg, "acme-data", _tree(tmp_path / ("t" + str(len(files)) + next(iter(files))[:3]), **files))
        f = dd.kind_finding(reg, "acme-data")
        assert f["detail"]["state"] == "doi_only" and "is not a dataset" not in f["summary"]
        assert f["summary"].startswith("a DOI names it (10.")
        assert f["summary"].endswith("whether it is a dataset is not established until the DOI is resolved (Import resolves it)")
        assert f["detail"]["can_import"] is True                              # Import is what resolves it
        assert f["summary"] != dd.NO_DESCRIPTOR_SENTENCE                      # and not the data-files-only sentence


def test_the_kind_row_records_when_the_descriptors_were_read(reg, tmp_path):
    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE}),
                         surveyed_at="2026-11-03T10:00:00")
    assert dd.kind_finding(reg, "acme-data")["detail"]["descriptors_read_at"] == "2026-11-03T10:00:00"
    js = (JS_ROOT / "next/stages/repo-analysis-views.js").read_text(encoding="utf-8")
    assert "data-dataset-read-at" in js and "descriptors read" in js


def test_the_declared_files_reach_data_file_profiling_the_answer_to_q_0047(reg, tmp_path):
    from resource_explorer.surveyors.repo_survey_definition_adapter import _data_profile_results

    _inventory(reg, "acme-data", {"data/a.csv": 100, "data/b.csv": 50})
    dd.store_descriptors(reg, "acme-data", _tree(tmp_path / "t", **{"datapackage.json": DATAPACKAGE}),
                         surveyed_at="2026-11-03T10:00:00")
    got = _data_profile_results(reg, "acme-data")["declared_files"]
    assert got == [{"descriptor": "datapackage.json", "kind": "datapackage", "declared": 3, "present": 2, "remote": 1,
                    "words": "2 of 3 declared files present in the tree (+1 remote)", "missing": ["data/c.csv"],
                    "read_at": "2026-11-03T10:00:00"}]
    reg.add(Project(slug="bare", display_name="bare", github_url="https://github.com/acme/bare", status=ProjectStatus.ACTIVE))
    assert _data_profile_results(reg, "bare")["declared_files"] == []


def test_q_0043_and_q_0047_are_answered_by_the_analyses_that_carry_the_new_facts():
    import yaml

    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "resource_explorer/configdata/question_catalog.yaml").read_text())
    qs = {q["id"]: q for q in doc["repo_questions"]}
    assert qs["Q-0043"]["answering"]["analysis_ids"] == ["repo_classification"]       # dataset_kind is a finding of it
    assert qs["Q-0047"]["answering"]["analysis_ids"] == ["data_file_profiling"]       # declared_files is in its results


def test_the_suggested_portals_fold_never_offers_or_hides_anything_because_of_the_pseudo_portal(client, reg):
    from resource_explorer.open_data import seeds

    OpenDataStore(reg).list_portals(include_reserved=True)
    body = client.get("/api/open-data/portal-seeds").json()
    assert body["count"] == len(seeds.seed_rows())                       # the reserved row removed nothing from the fold
    assert not any("repositor" in (r["display_name"] + r["feed_url"]).lower() for r in body["rows"])
    # even if the reserved row were handed to the matcher, no suggested row matches its address
    assert seeds.unregistered(OpenDataStore(reg).list_portals(include_reserved=True)) == seeds.unregistered([])
    assert client.post("/api/open-data/portal-seeds/add", json={"feed_url": "repository://"}).status_code == 404
