"""S3 reach, Part A (BRIEF-S3-REACH-AND-PARQUET-SLICE-2026-11-02): the mechanism, its discovery, the three credential
sources, the walk over list/stat/read, and that no write is ever made. Against `moto` only: no network, no real
bucket, no real key.
"""
from __future__ import annotations

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from resource_explorer import reach_mechanisms as rm  # noqa: E402
from resource_explorer.reach_mechanisms import s3 as s3m  # noqa: E402

BUCKET = "datalake"
KEY_ID = "AKIATESTKEY0000W7QX"
SECRET = "s3cr3t-Value-Never-Shown-0123456789"


@pytest.fixture
def aws(monkeypatch, tmp_path):
    """A moto S3 and a clean credential environment (no host profile leaks in)."""
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y").create_bucket(
            Bucket=BUCKET)
        yield


def _put(key: str, body: bytes = b"x"):
    boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y").put_object(
        Bucket=BUCKET, Key=key, Body=body)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """A host-visible secrets store holding one S3 key under "<slug>::S3 reader"."""
    from resource_explorer import omsecrets_store

    path = str(tmp_path / "re.omsecrets")
    monkeypatch.setattr(omsecrets_store, "local_path", lambda: path)
    return path


def _settings(**kw) -> s3m.S3Settings:
    base = dict(bucket=BUCKET, region="us-east-1", credential_kind="none")
    base.update(kw)
    return s3m.S3Settings(**base)


def _keyed(**kw) -> s3m.S3Mechanism:
    """A mechanism with a key in memory (moto refuses an unsigned read of a private bucket, as S3 does)."""
    return s3m.S3Mechanism(_settings(credential_kind="secret_ref", credential_ref="t::S3 reader",
                                     access_key_id=KEY_ID, **kw), key=(KEY_ID, SECRET, ""))


def _operations(mech) -> list[str]:
    """Every S3 operation the mechanism's real client sends, recorded by botocore's own event hook."""
    seen: list[str] = []
    client = object.__getattribute__(mech._client, "_client")
    client.meta.events.register("before-call.s3", lambda model, **_: seen.append(model.name))
    return seen


# ── discovery ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_builtin_s3_mechanism_is_discovered_with_its_manifest():
    found = rm.discover_mechanisms()
    assert "s3" in found
    m = found["s3"].manifest
    assert m.protocol == "s3" and m.credential_tier == rm.READ_ONLY and not m.executes_commands
    assert set(m.credential_kinds) == {"none", "secret_ref", "profile"}
    assert "1,000 keys" in m.cost_shape
    assert rm.mechanism_for_protocol("S3") is found["s3"]


def test_a_broken_entry_point_is_listed_by_name_with_its_error(monkeypatch):
    class _Bad:
        manifest = rm.MechanismManifest(name="sftp", protocol="sftp", credential_kinds=("none",), cost_shape="")

    class _EP:
        def __init__(self, name, target):
            self.name, self._t = name, target

        def load(self):
            if isinstance(self._t, Exception):
                raise self._t
            return self._t

    monkeypatch.setattr(rm, "_entry_points",
                        lambda: [_EP("sftp", _Bad), _EP("gone", ImportError("No module named 'gone'"))])
    found = rm.discover_mechanisms()
    assert "s3" in found and "sftp" not in found          # the others stay
    failures = rm.mechanism_load_failures()
    assert "cost shape" in failures["sftp"]
    assert "No module named" in failures["gone"]


@pytest.mark.parametrize("kw, words", [
    (dict(credential_tier="read-write"), "credential tier"),
    (dict(executes_commands=True), "executes commands"),
    (dict(credential_kinds=("password",)), "credential kinds"),
    (dict(cost_shape=""), "cost shape"),
])
def test_a_manifest_that_cannot_be_priced_or_trusted_is_refused(kw, words):
    base = dict(name="x", protocol="x", credential_kinds=("none",), cost_shape="one call per thing")
    base.update(kw)
    with pytest.raises(rm.ManifestError, match=words):
        rm.MechanismManifest(**base).validate()


# ── the three credential sources ─────────────────────────────────────────────────────────────────────────────────

def test_none_is_an_unsigned_client_and_reads_public_bucket(aws):
    from botocore import UNSIGNED

    mech = s3m.S3Mechanism(_settings())
    client = object.__getattribute__(mech._client, "_client")
    assert client.meta.config.signature_version is UNSIGNED
    assert _settings().credential_name() == "public bucket"


def test_secret_ref_reads_the_key_from_the_store_by_reference(aws, store, monkeypatch):
    s3m.store_key("dl_minio", KEY_ID, SECRET)
    seen = {}
    real = boto3.session.Session

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(boto3.session, "Session", spy)
    settings = _settings(credential_kind="secret_ref", credential_ref="dl_minio::S3 reader", access_key_id=KEY_ID)
    s3m.S3Mechanism(settings).list("")
    assert seen["aws_access_key_id"] == KEY_ID and seen["aws_secret_access_key"] == SECRET
    assert settings.credential_name() == "key …W7QX (S3 reader)"
    assert KEY_ID not in settings.credential_name() and SECRET not in settings.credential_name()


def test_secret_ref_with_no_store_names_the_ref_and_no_value(aws, monkeypatch):
    from resource_explorer import omsecrets_store

    monkeypatch.setattr(omsecrets_store, "local_path", lambda: "")
    with pytest.raises(rm.ReachError, match="No secrets store") as exc:
        s3m.S3Mechanism(_settings(credential_kind="secret_ref", credential_ref="dl::S3 reader"))
    assert "dl::S3 reader" in str(exc.value)
    with pytest.raises(rm.ReachError, match="No secrets store"):
        s3m.store_key("dl", KEY_ID, SECRET)


def test_profile_uses_the_host_profile_by_name(aws, tmp_path, monkeypatch):
    (tmp_path / "aws-credentials").write_text(
        "[analytics-ro]\naws_access_key_id = AKIAPROFILE00000ABCD\naws_secret_access_key = profilesecret\n")
    seen = {}
    real = boto3.session.Session

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(boto3.session, "Session", spy)
    settings = _settings(credential_kind="profile", credential_ref="analytics-ro")
    mech = s3m.S3Mechanism(settings)
    assert seen == {"profile_name": "analytics-ro"}
    client = object.__getattribute__(mech._client, "_client")
    assert client._request_signer._credentials.access_key == "AKIAPROFILE00000ABCD"
    assert settings.credential_name() == "host profile analytics-ro"


def test_an_unknown_profile_is_a_redacted_reach_error(aws):
    with pytest.raises(rm.ReachError, match="ProfileNotFound"):
        s3m.S3Mechanism(_settings(credential_kind="profile", credential_ref="no-such-profile"))


# ── list, stat, read ─────────────────────────────────────────────────────────────────────────────────────────────

def test_list_paginates_one_call_per_thousand_keys(aws):
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    for i in range(2500):
        client.put_object(Bucket=BUCKET, Key=f"big/part-{i:05d}.parquet", Body=b"")
    listing = s3m.S3Mechanism(_settings()).list("big")
    assert len(listing.entries) == 2500 and listing.calls == 3 and not listing.truncated
    capped = s3m.S3Mechanism(_settings()).list("big", limit=1000)
    assert capped.truncated and capped.calls == 1


def test_a_folder_is_a_prefix_and_the_root_prefix_is_added(aws):
    for k in ("demo/t/_delta_log/00000.json", "demo/t/part-0.parquet", "demo/readme.md", "demo/t/"):
        _put(k)
    mech = _keyed(root_prefix="/demo")
    top = mech.list("")
    assert [e.key for e in top.entries] == ["readme.md"]
    assert top.folders == ["t/"]
    inner = mech.list("t/")
    assert [e.key for e in inner.entries] == ["t/part-0.parquet"]          # the "t/" marker object is not a file
    assert inner.folders == ["t/_delta_log/"]
    st = mech.stat("t/part-0.parquet")
    assert st.size == 1 and st.etag and not st.multipart and st.modified_at


def test_range_reads_are_capped_head_and_tail(aws):
    _put("f.bin", bytes(range(200)))
    mech = _keyed()
    assert mech.read("f.bin", 10) == bytes(range(10))
    assert mech.read("f.bin", 10, tail=True) == bytes(range(190, 200))
    assert mech.read_range("f.bin", 50, 5) == bytes(range(50, 55))
    assert mech.read("f.bin", 0) == b""
    assert mech.bytes_read == 25


def test_no_write_operation_is_ever_sent(aws):
    for k in ("a/README.md", "a/b/data.csv", "c.txt"):
        _put(k, b"# readme\nLicense: MIT\n")
    mech = _keyed()
    ops = _operations(mech)
    from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import RemoteFileSystemSurveyor

    sd = RemoteFileSystemSurveyor(_fs(), None, mech).run(walk_extensions=True)
    mech.stat("c.txt")
    mech.read("c.txt", 4)
    mech.check_reach()
    assert sd["total_files"] == 3
    assert set(ops) <= {"ListObjectsV2", "HeadObject", "GetObject"}
    # and the wrapper refuses a write by name, before any request
    for write in ("put_object", "delete_object", "copy_object", "delete_objects", "upload_file"):
        with pytest.raises(PermissionError, match="reads only"):
            getattr(mech._client, write)
    keys = sorted(o["Key"] for o in boto3.client("s3", region_name="us-east-1", aws_access_key_id="x",
                                                 aws_secret_access_key="y").list_objects_v2(Bucket=BUCKET)["Contents"])
    assert keys == ["a/README.md", "a/b/data.csv", "c.txt"]      # the bucket is as it was


# ── the walk ─────────────────────────────────────────────────────────────────────────────────────────────────────

def _fs(**kw):
    from resource_explorer.registry import FileSystemEntity

    base = dict(slug="dl", display_name="DL", local_mount_point="s3://datalake", canonical_mount_point="s3://datalake",
                protocol="s3", bucket=BUCKET, region="us-east-1", credential_kind="none")
    base.update(kw)
    return FileSystemEntity(**base)


def test_the_walk_keeps_rows_with_absence_words_and_etag_hashes(aws):
    from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import RemoteFileSystemSurveyor

    _put("t/_delta_log/00000000000000000000.json", b"{}")
    _put("t/part-0.parquet", b"PAR1....PAR1")
    _put("t/.env", b"SECRET=1")
    sd = RemoteFileSystemSurveyor(_fs(), None, s3m.S3Mechanism(_settings())).run(walk_extensions=True)
    rows = {r["file_path"]: r for r in sd["walk_files"]}
    assert set(rows) == {"t/_delta_log/00000000000000000000.json", "t/part-0.parquet", "t/.env"}
    part = rows["t/part-0.parquet"]
    assert part["owner_name"] == part["group_name"] == part["mode_octal"] == rm.NOT_REPORTED
    assert part["hash_state"] == "hashed" and part["content_hash"].startswith("etag:")
    assert rows["t/.env"]["hash_state"] == "secret_name" and rows["t/.env"]["content_hash"] == ""
    # what an object store does not report is left out, never zero
    assert sd["symlink_count"] is None and sd["writable_count"] is None and sd["last_access_time"] == ""
    assert sd["total_directories"] == 2 and sd["walk_limits"]["list_calls"] == 3
    from resource_explorer.surveyors.filesystem import walk_extensions as we

    summary = we.summaries(sd["walk_files"], hash_limit=we.hash_limit_bytes(), algorithm=we.ETAG_ALGORITHM)
    assert summary["owners"] is None and summary["modes"] is None
    assert summary["not_reported"]["words"] == rm.NOT_REPORTED
    assert summary["hashing"]["algorithm"].startswith("S3 ETag")


def test_a_multipart_etag_is_not_a_hash():
    from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import remote_row

    row = remote_row(rm.Entry(key="x/big.parquet", size=9, etag="9b2cf535f27731c974343645a3985328-3"),
                     modified_at="2026-11-02T00:00:00")
    assert row["hash_state"] == "multipart_etag" and row["content_hash"] == ""


def test_the_walk_obeys_the_depth_and_entry_caps(aws):
    from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import RemoteFileSystemSurveyor

    for k in ("a/1.txt", "a/b/2.txt", "a/b/c/3.txt", "z.txt", ".git/HEAD"):
        _put(k)
    deep = RemoteFileSystemSurveyor(_fs(), None, s3m.S3Mechanism(_settings()), max_depth=1).run()
    assert sorted(f["file_path"] for f in deep["inventory"]) == ["a/1.txt", "z.txt"]
    assert deep["walk_limits"]["capped_reasons"] == ["depth cap"]
    assert deep["walk_limits"]["not_walked_by_name"] == {".git": 1}
    few = RemoteFileSystemSurveyor(_fs(), None, s3m.S3Mechanism(_settings()), max_entries=2).run()
    assert few["walk_limits"]["capped"] and "entry cap" in few["walk_limits"]["capped_reasons"]
    assert few["total_files"] <= 2


def test_fs_permissions_says_objects_carry_no_posix_permissions():
    from fs_permissions import step

    class _Ods:
        def filesystem_walk_runs(self, slug, limit=1):
            return [{"run_id": "2026-11-02T00:00:00"}]

        def filesystem_walk_files(self, slug, run_id):
            return [{"file_path": "t/a.parquet", "owner_name": rm.NOT_REPORTED, "group_name": rm.NOT_REPORTED,
                     "mode_octal": rm.NOT_REPORTED, "hash_state": "hashed"}]

    class _In:
        slug = "dl"
        ods = _Ods()

    out = step.run(_In())
    assert [r["state"] for r in out] == ["not_established", "not_established"]
    assert out[0]["summary"] == "not established · S3 objects carry no POSIX permissions"


# ── the reach check and redaction ────────────────────────────────────────────────────────────────────────────────

def test_check_reach_gives_the_outcome_3_sentence(aws):
    out = s3m.S3Mechanism(_settings()).check_reach()
    assert out["reachable"] is True
    assert out["sentence"].startswith(f"reachable by Resource Explorer over S3 from {s3m.re_host()} as public bucket")
    assert "Egeria has no reach to this bucket" in out["sentence"] and "· checked " in out["sentence"]
    assert out["write_permission"] == s3m.WRITE_NOT_CHECKED
    missing = s3m.S3Mechanism(_settings(bucket="no-such-bucket")).check_reach()
    assert missing["reachable"] is False and "no such bucket" in missing["sentence"]


def test_no_secret_reaches_an_error_string():
    class _Echo:
        def list_objects_v2(self, **_):
            raise RuntimeError(f"auth failed: password={SECRET} key {KEY_ID} secret {SECRET}")

    settings = _settings(credential_kind="secret_ref", credential_ref="dl::S3 reader", access_key_id=KEY_ID)
    mech = s3m.S3Mechanism(settings, client=_Echo(), key=(KEY_ID, SECRET, ""))
    out = mech.check_reach()
    assert out["reachable"] is False
    for text in (out["error"], out["sentence"]):
        assert SECRET not in text and KEY_ID not in text
    with pytest.raises(rm.ReachError) as exc:
        mech.list("")
    assert SECRET not in str(exc.value)


def test_settings_refuse_a_path_for_a_bucket_and_a_key_with_no_ref():
    with pytest.raises(rm.ReachError, match="bucket is a name"):
        _settings(bucket="datalake/demo").validate()
    with pytest.raises(rm.ReachError, match="secrets-store reference"):
        _settings(credential_kind="secret_ref").validate()
    assert _settings(root_prefix="/demo").location == "s3://datalake/demo"
