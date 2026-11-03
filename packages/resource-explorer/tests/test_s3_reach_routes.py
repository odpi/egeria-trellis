"""S3 reach, Part A: the register form's routes and words. "Check reach" before Register, a key held in the secrets
store by reference only (never a registry column, never answered), the recorded outcome-3 sentence, the walk run
through the folder-survey route, and the served form. Against `moto` only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

pytestmark = pytest.mark.usefixtures("signed_in_caller")

BUCKET = "datalake"
KEY_ID = "AKIAROUTETEST000W7QX"
SECRET = "route-s3cr3t-never-shown-9876543210"


@pytest.fixture
def aws(monkeypatch, tmp_path):
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    with moto.mock_aws():
        c = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
        c.create_bucket(Bucket=BUCKET)
        for k in ("demo/t/_delta_log/00000000000000000000.json", "demo/t/part-0.parquet", "demo/README.md"):
            c.put_object(Bucket=BUCKET, Key=k, Body=b"# demo\nOwner: data team\n")
        yield


@pytest.fixture
def store(monkeypatch, tmp_path):
    from resource_explorer import omsecrets_store

    path = str(tmp_path / "re.omsecrets")
    monkeypatch.setattr(omsecrets_store, "local_path", lambda: path)
    return path


@pytest.fixture
def no_store(monkeypatch):
    from resource_explorer import omsecrets_store

    monkeypatch.setattr(omsecrets_store, "local_path", lambda: "")


@pytest.fixture
def reg(tmp_path, monkeypatch):
    from resource_explorer.registry import ProjectRegistry

    r = ProjectRegistry(db_path=str(tmp_path / "s3.db"))
    monkeypatch.setattr("resource_explorer.registry.ProjectRegistry.__init__",
                        lambda self, db_path=None, **k: setattr(self, "__dict__", r.__dict__) or None)
    return r


@pytest.fixture()
def _zone_access_open(monkeypatch):
    from tests.zone_fakes import open_access

    return open_access(monkeypatch, caller_if_none="s3-tester")


def _headers():
    from resource_explorer.auth import create_access_token

    return {"Authorization": "Bearer " + create_access_token(user_id="s3-tester", egeria_token="tok")}


def _client():
    from resource_explorer.web.app import app

    return TestClient(app)


def _keyed_form(**kw) -> dict:
    form = {"slug": "dl-minio", "display_name": "DL MinIO", "protocol": "s3", "bucket": BUCKET, "region": "us-east-1",
            "root_prefix": "demo", "path_style": True, "credential_kind": "secret_ref", "access_key_id": KEY_ID,
            "secret_access_key": SECRET}
    form.update(kw)
    return form


def _row_text(reg, slug: str) -> str:
    """Every column of the stored row, as text: where a secret would show if it had been stored."""
    with reg._conn() as conn:
        row = conn.execute("SELECT * FROM file_systems WHERE slug = ?", (slug,)).fetchone()
    return " ".join(str(v) for v in dict(row).values())


def test_check_reach_answers_the_sentence_and_stores_nothing(aws, reg, store):
    c = _client()
    r = c.post("/api/filesystems/check-reach", json=_keyed_form(), headers=_headers())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["reachable"] is True
    assert " over S3 from " in out["sentence"] and "as key …W7QX (S3 reader)" in out["sentence"]
    assert SECRET not in r.text and KEY_ID not in r.text
    from resource_explorer import omsecrets_store

    assert omsecrets_store.collection_names(path=store) == set()       # the typed key was used once, not held
    assert reg.list_filesystems() == []


def test_check_reach_says_the_refusal_in_s3_words(aws, reg):
    r = _client().post("/api/filesystems/check-reach",
                       json={"protocol": "s3", "bucket": "nope", "region": "us-east-1", "credential_kind": "none"},
                       headers=_headers())
    assert r.status_code == 200 and r.json()["reachable"] is False
    assert "no such bucket" in r.json()["sentence"]


def test_register_holds_the_key_by_reference_and_never_in_the_registry(aws, reg, store):
    c = _client()
    r = c.post("/api/filesystems/", json=_keyed_form(), headers=_headers())
    assert r.status_code == 200, r.text
    assert SECRET not in r.text and KEY_ID not in r.text
    body = r.json()
    assert body["slug"] == "dl_minio" and body["protocol"] == "s3"
    assert body["location"] == "s3://datalake/demo"
    assert body["credential_name"] == "key …W7QX (S3 reader)"
    fs = reg.get_filesystem("dl_minio")
    assert fs.credential_ref == "dl_minio::S3 reader" and fs.access_key_id == KEY_ID and fs.path_style is True
    assert SECRET not in _row_text(reg, "dl_minio")
    from resource_explorer import omsecrets_store

    held = omsecrets_store._load(store)["secretsCollections"]["dl_minio::S3 reader"]["secrets"]
    assert held == {"userId": KEY_ID, "clearPassword": SECRET}
    # the reach check ran at register and was recorded as outcome 3, read back from the row
    reach = c.get("/api/filesystems/dl_minio/reach", headers=_headers()).json()
    assert reach["state"] == "remote" and reach["surveyor"] == "Resource Explorer"
    assert reach["sentence"].startswith("reachable by Resource Explorer over S3 from ")
    assert reach["report_sentence"].endswith("Egeria has no reach to this bucket")
    listed = c.get("/api/filesystems/", headers=_headers()).json()
    assert listed[0]["credential_name"] == "key …W7QX (S3 reader)" and SECRET not in str(listed)


def test_register_refuses_a_key_when_no_store_is_configured_before_writing_a_row(aws, reg, no_store):
    r = _client().post("/api/filesystems/", json=_keyed_form(), headers=_headers())
    assert r.status_code == 400 and "No secrets store is configured" in r.json()["detail"]
    assert SECRET not in r.text
    assert reg.list_filesystems() == []
    mech = _client().get("/api/filesystems/reach-mechanisms", headers=_headers()).json()
    assert mech["secrets_store_configured"] is False
    assert [m["name"] for m in mech["mechanisms"]] == ["s3"]


def test_a_public_bucket_registers_with_no_credential(aws, reg, no_store):
    r = _client().post("/api/filesystems/", json={"slug": "pub", "display_name": "Public", "protocol": "s3",
                                                 "bucket": BUCKET, "region": "us-east-1", "credential_kind": "none"},
                       headers=_headers())
    assert r.status_code == 200, r.text
    assert r.json()["credential_name"] == "public bucket"
    fs = reg.get_filesystem("pub")
    assert fs.credential_kind == "none" and fs.credential_ref == "" and fs.access_key_id == ""


def test_a_validation_refusal_never_echoes_the_secret(reg):
    r = _client().post("/api/filesystems/", json={"protocol": "s3", "secret_access_key": SECRET}, headers=_headers())
    assert r.status_code == 422
    assert SECRET not in r.text


def test_a_local_file_system_still_needs_its_mount(reg):
    r = _client().post("/api/filesystems/", json={"slug": "loc", "display_name": "L"}, headers=_headers())
    assert r.status_code == 400 and "mount point" in r.json()["detail"]
    assert _client().post("/api/filesystems/", json={"slug": "loc", "display_name": "L", "protocol": "sftp"},
                          headers=_headers()).status_code == 400


def test_existing_local_rows_read_local_after_the_migration(reg):
    from resource_explorer.registry import FileSystemEntity

    reg.register_filesystem(FileSystemEntity(slug="old", display_name="Old", local_mount_point="/mnt/old"))
    fs = reg.get_filesystem("old")
    assert fs.protocol == "local" and fs.tls_verify is True and fs.path_style is False
    with reg._conn() as conn:
        assert conn.execute("SELECT protocol FROM file_systems WHERE slug='old'").fetchone()[0] == "local"
    from resource_explorer.registry import FILE_SYSTEMS_REMOTE_REACH_COLUMNS

    with reg._conn() as conn:
        cols = reg._get_table_columns(conn, "file_systems")
    assert {c for c, _ in FILE_SYSTEMS_REMOTE_REACH_COLUMNS} <= set(cols)
    assert not any("secret" in c for c in cols)


@pytest.mark.usefixtures("_zone_access_open")
def test_the_folder_survey_walks_the_bucket_re_led(aws, reg, store):
    c = _client()
    assert c.post("/api/filesystems/", json=_keyed_form(), headers=_headers()).status_code == 200
    r = c.post("/api/filesystems/dl_minio/folder-survey", json={}, headers=_headers())
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["led_by"] == "resource_explorer" and out["total_files"] == 3
    assert out["reach"]["state"] == "remote"
    runs = reg.list_filesystem_walk_runs("dl_minio")
    rows = reg.list_filesystem_walk_files("dl_minio", runs[0]["run_id"])
    assert {r["owner_name"] for r in rows} == {"not reported by this source"}
    assert SECRET not in r.text


def test_the_form_is_served_with_its_s3_fields_and_words():
    js = _client().get("/static/next/fs-register.js").text
    for words in ("An S3 bucket (or MinIO)", "Check reach", "Endpoint URL (blank = AWS)", "Bucket *", "Root prefix",
                  "Path style (MinIO)", "Verify TLS", "None · a public bucket", "A key held in the secrets store",
                  "This server's host profile", "no secrets store is configured on this server",
                  "held in the secrets store by reference"):
        assert words in js, words
    import re

    from resource_explorer.web.routes.filesystems import FileSystemRegistration

    payload = re.search(r"export function s3Payload\(f\) \{.*?return \{(.*?)\};", js, re.S).group(1)
    sent = set(re.findall(r"(\w+):", payload))
    assert {"protocol", "bucket", "credential_kind", "secret_access_key"} <= sent
    assert sent <= set(FileSystemRegistration.model_fields)
    assert not [k for k in sent if k.startswith("egeria")]
    api = _client().get("/static/re-api.js").text
    assert "/api/filesystems/check-reach" in api and "/api/filesystems/reach-mechanisms" in api
