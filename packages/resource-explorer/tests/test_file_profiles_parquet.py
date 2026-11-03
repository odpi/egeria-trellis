"""S3 slice Part B (BRIEF-S3-REACH-AND-PARQUET-SLICE-2026-11-02): the Parquet profile reader.

Fixture Parquet files are written with pyarrow in the test (mixed types, nulls, several row groups). Remote reads run
against `moto` only. Pinned: the schema from the footer and the per-column profile; the row-group words; the footer
read FIRST by byte range and nothing past the limit; a `.parquet` that is not Parquet after all, in words; a Delta
layout whose log commits are not profiled as data and whose parts say the log was not read.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from resource_explorer.surveyors.filesystem import file_profiles as fp  # noqa: E402

BUCKET = "datalake"
KEY_ID = "AKIAPARQUETTEST0W7QX"
SECRET = "parquet-s3cr3t-never-shown"


def _table(rows: int = 9) -> "pa.Table":
    return pa.table({
        "id": pa.array(list(range(rows)), type=pa.int64()),
        "amount": pa.array([None if i % 3 == 0 else i * 1.5 for i in range(rows)], type=pa.float64()),
        "name": pa.array([None if i == 4 else f"n{i % 4}" for i in range(rows)], type=pa.string()),
        "seen": pa.array([dt.datetime(2026, 1, 1 + i % 28) for i in range(rows)], type=pa.timestamp("us")),
        "flag": pa.array([i % 2 == 0 for i in range(rows)], type=pa.bool_()),
    })


def _parquet_bytes(table, row_group_size: int = 3) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, row_group_size=row_group_size)
    return buf.getvalue()


def _cols(res: dict) -> dict:
    return {c["column_name"]: c for c in res["columns"]}


# ── local files ──────────────────────────────────────────────────────────────────────────────────────────────────

def test_schema_and_profile_from_a_mixed_parquet_file(tmp_path):
    p = tmp_path / "t.parquet"
    p.write_bytes(_parquet_bytes(_table()))
    res = fp.profile_file(p, "parquet")
    assert res["state"] == fp.MEASURED and res["sample_ratio"] == 1.0
    assert res["note"] == "schema read from the footer; values profiled from 3 of 3 row groups"
    c = _cols(res)
    assert list(c) == ["id", "amount", "name", "seen", "flag"]
    assert c["id"]["declared_type"] == "int64" and c["id"]["inferred_general_type"] == "N"
    assert c["amount"]["null_ct"] == 3 and c["amount"]["value_ct"] == 6
    assert c["amount"]["min_text"] == "1.5" and c["amount"]["max_text"] == "12.0"
    assert c["name"]["declared_type"] == "string" and c["name"]["null_ct"] == 1 and c["name"]["distinct_ct"] == 4
    assert c["seen"]["inferred_general_type"] == "D" and c["seen"]["min_text"].startswith("2026-01-01")
    assert c["flag"]["inferred_general_type"] == "B" and c["flag"]["distinct_ct"] == 2
    assert all(col["record_ct"] == 9 for col in c.values())


def test_over_the_limit_reads_the_row_groups_that_fit_and_says_how_many(tmp_path):
    p = tmp_path / "t.parquet"
    p.write_bytes(_parquet_bytes(_table(), row_group_size=3))
    one_group = pq.ParquetFile(p).metadata.row_group(0).total_byte_size
    res = fp.profile_file(p, "parquet", limit=one_group)
    assert res["state"] == fp.SAMPLED and res["sample_ratio"] == pytest.approx(3 / 9)
    assert res["note"] == "schema read from the footer; values profiled from 1 of 3 row groups"
    assert _cols(res)["id"]["record_ct"] == 3


def test_a_parquet_name_on_a_file_that_is_not_parquet_is_said(tmp_path):
    p = tmp_path / "fake.parquet"
    p.write_bytes(b"id,name\n1,a\n2,b\n")
    res = fp.profile_file(p, "parquet")
    assert res["state"] == fp.UNREADABLE and res["error"] == fp.NOT_PARQUET_WORDS
    assert res["columns"] == []


# ── remote files: the footer first, by byte range ───────────────────────────────────────────────────────────────

@pytest.fixture
def s3(monkeypatch, tmp_path):
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    for name in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    with moto.mock_aws():
        c = boto3.client("s3", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
        c.create_bucket(Bucket=BUCKET)
        yield c


def _mechanism():
    from resource_explorer.reach_mechanisms import s3 as s3m

    settings = s3m.S3Settings(bucket=BUCKET, region="us-east-1", credential_kind="secret_ref",
                              credential_ref="t::S3 reader", access_key_id=KEY_ID)
    mech = s3m.S3Mechanism(settings, key=(KEY_ID, SECRET, ""))
    ranges: list[str] = []
    client = object.__getattribute__(mech._client, "_client")
    client.meta.events.register("provide-client-params.s3.GetObject", lambda params, **_: ranges.append(params["Range"]))
    return mech, ranges


def _big_table(rows: int = 40_000):
    import random

    rnd = random.Random(7)
    return pa.table({"id": pa.array(range(rows), type=pa.int64()),
                     "text": pa.array(["".join(rnd.choice("abcdefghij") for _ in range(12)) for _ in range(rows)])})


def test_a_remote_parquet_is_read_footer_first_and_only_the_groups_that_fit(s3):
    data = _parquet_bytes(_big_table(), row_group_size=10_000)
    s3.put_object(Bucket=BUCKET, Key="t/part-0.parquet", Body=data)
    meta = pq.ParquetFile(io.BytesIO(data)).metadata
    assert meta.num_row_groups == 4 and len(data) > fp.PARQUET_TAIL_BYTES
    limit = meta.row_group(0).total_byte_size + meta.row_group(1).total_byte_size
    mech, ranges = _mechanism()
    res = fp.profile_remote(mech, "t/part-0.parquet", "parquet", len(data), limit=limit)
    assert ranges[0] == f"bytes=-{fp.PARQUET_TAIL_BYTES}"           # the footer, first
    assert res["state"] == fp.SAMPLED and res["note"].endswith("from 2 of 4 row groups")
    assert _cols(res)["id"]["record_ct"] == 20_000
    assert res["bytes_read"] == mech.bytes_read < len(data)           # not the whole file
    whole = fp.profile_remote(_mechanism()[0], "t/part-0.parquet", "parquet", len(data))
    assert whole["state"] == fp.MEASURED and whole["note"].endswith("from 4 of 4 row groups")


def test_a_small_remote_parquet_is_one_tail_read(s3):
    data = _parquet_bytes(_table())
    s3.put_object(Bucket=BUCKET, Key="small.parquet", Body=data)
    mech, ranges = _mechanism()
    res = fp.profile_remote(mech, "small.parquet", "parquet", len(data))
    assert res["state"] == fp.MEASURED and set(_cols(res)) == {"id", "amount", "name", "seen", "flag"}
    assert ranges == [f"bytes=-{len(data)}"]                         # under 64 KiB: one tail read is the whole file


def test_a_remote_file_that_is_not_parquet_is_said(s3):
    s3.put_object(Bucket=BUCKET, Key="fake.parquet", Body=b"not parquet at all, just text bytes")
    res = fp.profile_remote(_mechanism()[0], "fake.parquet", "parquet", 35)
    assert res["state"] == fp.UNREADABLE and res["error"] == fp.NOT_PARQUET_WORDS


def test_a_remote_csv_is_read_once_up_to_the_limit(s3):
    s3.put_object(Bucket=BUCKET, Key="a.csv", Body=b"id,name\n1,a\n2,b\n")
    mech, ranges = _mechanism()
    res = fp.profile_remote(mech, "a.csv", "csv", 16)
    assert res["state"] == fp.MEASURED and [c["column_name"] for c in res["columns"]] == ["id", "name"]
    assert ranges == [f"bytes=0-{fp.PROFILE_LIMIT_BYTES - 1}"]


# ── the run over a Delta layout on S3 ───────────────────────────────────────────────────────────────────────────

def test_a_delta_layout_on_s3_profiles_its_parts_and_says_the_log_was_not_read(s3, tmp_path, monkeypatch):
    from resource_explorer import omsecrets_store
    from resource_explorer.reach_mechanisms import s3 as s3m
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry
    from resource_explorer.surveyors.filesystem.remote_filesystem_surveyor import RemoteFileSystemSurveyor

    store = str(tmp_path / "re.omsecrets")
    monkeypatch.setattr(omsecrets_store, "local_path", lambda: store)
    s3m.store_key("dl", KEY_ID, SECRET)
    s3.put_object(Bucket=BUCKET, Key="demo/sales/_delta_log/00000000000000000000.json",
                  Body=b'{"commitInfo":{}}\n{"add":{"path":"part-0.parquet"}}\n')
    s3.put_object(Bucket=BUCKET, Key="demo/sales/part-0.parquet", Body=_parquet_bytes(_table()))
    s3.put_object(Bucket=BUCKET, Key="demo/sales/part-1.parquet", Body=_parquet_bytes(_table(4)))
    reg = ProjectRegistry(db_path=str(tmp_path / "p.db"))
    fs = FileSystemEntity(slug="dl", display_name="DL", local_mount_point="s3://datalake/demo",
                          canonical_mount_point="s3://datalake/demo", protocol="s3", bucket=BUCKET, region="us-east-1",
                          root_prefix="demo/", credential_kind="secret_ref", credential_ref="dl::S3 reader",
                          access_key_id=KEY_ID)
    reg.register_filesystem(fs)
    fs = reg.get_filesystem("dl")
    from resource_explorer import reach_mechanisms

    sd = RemoteFileSystemSurveyor(fs, reg, reach_mechanisms.mechanism_for(fs)).run(walk_extensions=True)
    reg.add_filesystem_walk_files("dl", sd["surveyed_at"], sd["walk_files"])
    out = fp.run_file_profiles(fs, reg)
    assert out["protocol"] == "s3" and out["delta_tables"] == 1 and out["delta_note"] == fp.DELTA_NOTE
    notes = out["notes"]
    assert notes["sales/_delta_log/00000000000000000000.json"].startswith("a Delta Lake transaction-log commit")
    assert notes["sales/part-0.parquet"] == (
        "schema read from the footer; values profiled from 3 of 3 row groups · " + fp.DELTA_NOTE)
    view = fp.profiles_view(reg, "dl")
    files = {f["file_path"]: f for f in view["run"]["files"]}
    assert files["sales/_delta_log/00000000000000000000.json"]["state"] == fp.NOT_APPLICABLE
    assert files["sales/part-1.parquet"]["state"] == fp.MEASURED
    assert {c["column_name"] for c in files["sales/part-0.parquet"]["columns_list"]} == {"id", "amount", "name",
                                                                                         "seen", "flag"}
    assert view["run"]["delta_note"] == fp.DELTA_NOTE
    assert SECRET not in str(out) and SECRET not in str(view)
