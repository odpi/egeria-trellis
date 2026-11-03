"""Files build step 6: the four zero-fetch steps over a file system's kept walk rows (Files brief F5, F6, F8, F9).

fs_change_rates, fs_duplicates, fs_activity and fs_permissions are installable packages under examples/steps/,
installed here the way pip would (a dist-info carrying their own entry points). Each runs through the real
`SurveyStep` wrapper against a real ProjectRegistry on a scratch SQLite file, read through `OdsReader`.

What is pinned, per step: its nothing-to-derive guard ("one walk only", "no hashes", "access times not recorded on
this walk / on this mount", "no stat fields"), its happy path, that an absence is never a zero, and that no file
name, folder name or owner name reaches a result (a result can be published; those stay in Resource Explorer).
"""
import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "examples" / "steps"
NAMES = ("fs_change_rates", "fs_duplicates", "fs_activity", "fs_permissions")


@pytest.fixture
def fs_steps_installed(tmp_path, monkeypatch):
    for name in NAMES:
        pkg = STEPS / name
        project = tomllib.loads((pkg / "pyproject.toml").read_text())["project"]
        version = re.search(r'__version__ = "([^"]+)"', (pkg / "src" / name / "__init__.py").read_text()).group(1)
        d = tmp_path / "site" / f"{project['name'].replace('-', '_')}-{version}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n")
        (d / "entry_points.txt").write_text("[pyegeria.analytics]\n" + "".join(
            f"{k} = {v}\n" for k, v in project["entry-points"]["pyegeria.analytics"].items()))
        monkeypatch.syspath_prepend(str(pkg / "src"))
    monkeypatch.syspath_prepend(str(tmp_path / "site"))


@pytest.fixture
def fs_registry(tmp_path):
    """(registry, url, slugs registered). Never the shared registry: a scratch SQLite file."""
    from resource_explorer.registry import FileSystemEntity, ProjectRegistry

    db = str(tmp_path / "ods.sqlite")
    reg = ProjectRegistry(db_path=db)
    assert str(tmp_path) in reg.database_url

    def register(slug):
        reg.register_filesystem(FileSystemEntity(slug=slug, display_name=slug, local_mount_point=str(tmp_path)))
    for s in ("tree", "other"):
        register(s)
    return reg, reg.database_url


def row(path, *, size=10, mtime="2026-10-01T00:00:00", atime=None, owner="alice", group="staff", mode="0644",
        digest="", state="over_limit"):
    folder = path.rpartition("/")[0]
    return {"file_path": path, "folder_path": folder, "folder_depth": path.count("/"), "size_bytes": size,
            "modified_at": mtime, "accessed_at": atime, "owner_name": owner, "group_name": group, "mode_octal": mode,
            "world_readable": 1 if mode and int(mode, 8) & 0o004 else 0, "content_hash": digest, "hash_state": state}


def walk(reg, slug, run_id, rows, *, capped=False, blob=True):
    """One stored walk: its per-file rows and (unless blob=False) its filesystem_surveys row, as RE writes both."""
    reg.add_filesystem_walk_files(slug, run_id, rows)
    if blob:
        reg.add_filesystem_survey(slug, run_id, {"surveyed_at": run_id, "total_files": len(rows),
                                                 "walk_limits": {"capped": capped, "max_entries": 100000,
                                                                 "capped_reasons": ["entry cap"] if capped else []}})


def run(url, name, slug="tree"):
    from trellis_analytics.discovery import discover
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    host = HostContext(resource_type="filesystem", slug=slug, producing_run="run-x", ods=OdsReader(url))
    return {(a.measure or name): a for a in SurveyStep(discover()[name]).run(host)}


def state(a):
    return a.envelope.result_state.value


def leaks(results, names):
    """Any of `names` (file, folder, owner) in a result's summary, reason or values."""
    text = json.dumps([{"s": a.summary, "e": a.explanation, "v": a.resource_properties} for a in results.values()])
    return [n for n in names if n in text]


# ── the manifests ──

def test_each_step_declares_in_full_for_file_systems(fs_steps_installed):
    from trellis_analytics.discovery import discover

    found = discover()
    for name in NAMES:
        m = found[name]
        assert m.resource_types == ["filesystem"] and m.inputs == ["ods"] and m.credential_tier == "none"
        assert m.declaration is not None and m.declaration.is_complete(), (name, m.declaration.missing())
        # absence words, never a zero: the card says "not measured"
        assert "not measured" in m.description, name
        assert m.nothing_to_derive_guard == "survey-completed"
        # a measured confidence has no score; every measure has a unit and an as-of
        assert m.declaration.confidence.form == "measured"
        assert all(d.unit and d.as_of for d in m.declaration.measures)
    assert found["fs_permissions"].annotation_type_for("world_readable_sensitive") == "RequestForActionAnnotation"
    assert found["fs_activity"].annotation_types() == ["ResourceProfileAnnotation"]
    assert found["fs_activity"].parameters == {"hot_days": 7, "cold_days": 365}


# ── the reader ──

def test_the_capped_token_is_what_the_registry_writes(fs_registry):
    """`filesystem_walk_runs` reads "capped" with a LIKE on the stored JSON text; pinned to the writer's own form."""
    from trellis_analytics.readers.ods import OdsReader

    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a.txt")], capped=True)
    walk(reg, "tree", "2026-10-02T00:00:00", [row("a.txt")], capped=False)
    walk(reg, "tree", "2026-10-03T00:00:00", [row("a.txt", atime="2026-10-02T00:00:00")], blob=False)
    assert OdsReader.CAPPED_TOKEN.strip("%") in json.dumps({"walk_limits": {"capped": True}})
    runs = OdsReader(url).filesystem_walk_runs("tree", limit=5)
    assert [(r["run_id"][:10], r["capped"], r["with_access_time"]) for r in runs] == [
        ("2026-10-03", None, 1), ("2026-10-02", 0, 0), ("2026-10-01", 1, 0)]


def test_hash_matches_read_only_other_file_systems_newest_walk_and_never_empty_files(fs_registry):
    from trellis_analytics.readers.ods import OdsReader

    reg, url = fs_registry
    walk(reg, "tree", "2026-10-02T00:00:00", [row("a", digest="h1", state="hashed"),
                                              row("e", size=0, digest="h0", state="hashed")])
    walk(reg, "other", "2026-10-01T00:00:00", [row("x", digest="h1", state="hashed")])           # older: not read
    walk(reg, "other", "2026-10-03T00:00:00", [row("y", digest="h1", state="hashed"),
                                               row("z", size=0, digest="h0", state="hashed")])
    out = OdsReader(url).filesystem_hash_matches("tree", "2026-10-02T00:00:00")
    assert out["peers"] == [{"filesystem_slug": "other", "run_id": "2026-10-03T00:00:00", "hashed": 2}]
    assert out["matches"] == [{"filesystem_slug": "other", "content_hash": "h1", "size_bytes": 10, "files": 1}]


# ── fs_change_rates (F5) ──

def test_change_rates_no_walk_and_one_walk_are_not_measured(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    a = run(url, "fs_change_rates")["fs_change_rates"]
    assert state(a) == "not_established" and "a change needs two" in a.summary
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a.txt")])
    a = run(url, "fs_change_rates")["fs_change_rates"]
    assert state(a) == "not_established" and a.summary == "not measured · one walk only; a change needs two"


def test_change_rates_one_walk_reason_says_the_walk_in_the_one_time_form(fs_steps_installed, fs_registry):
    """W36: the reason named the walk by its raw ISO run id ("(2026-10-25T23:47:39.704588)"); it says "(10-25 23:47)"."""
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-25T23:47:39.704588", [row("a.txt")])
    a = run(url, "fs_change_rates")["fs_change_rates"]
    reason = a.envelope.result_state_reason
    assert "only one walk has kept per-file rows (10-25 23:47); a change needs two" in reason
    assert "T23" not in reason and ".704588" not in reason
    assert "Files Changed" not in a.resource_properties          # never a zero where nothing was compared


def test_change_rates_two_walks_added_removed_modified_and_growth(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("keep.txt"), row("gone.txt", size=5), row("edit.txt"),
                                              row("same-mtime.txt", digest="h1", state="hashed")])
    walk(reg, "tree", "2026-10-03T00:00:00", [row("keep.txt"), row("new.txt", size=40),
                                              row("edit.txt", size=12, mtime="2026-10-02T00:00:00"),
                                              row("same-mtime.txt", digest="h2", state="hashed")])
    results = run(url, "fs_change_rates")
    a = results["fs_change_rates"]
    v = a.resource_properties
    assert state(a) == "measured"
    assert (v["Files Added"], v["Files Removed"], v["Files Modified"], v["Files Unchanged"]) == ("1", "1", "2", "1")
    assert v["Bytes Growth"] == str((10 + 40 + 12 + 10) - (10 + 5 + 10 + 10)) and v["Interval Days"] == "2.0"
    assert v["Files Changed Per Day"] == "2.0" and v["toAsOf"] == "2026-10-03T00:00:00"
    assert "over 2.0 days" in a.summary
    assert not leaks(results, ["keep.txt", "gone.txt", "new.txt", "edit.txt"])


def test_change_rates_two_identical_walks_is_measured_nothing(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    for at in ("2026-10-01T00:00:00", "2026-10-01T06:00:00"):
        walk(reg, "tree", at, [row("a.txt"), row("b.txt")])
    a = run(url, "fs_change_rates")["fs_change_rates"]
    assert state(a) == "nothing_found" and a.resource_properties["Files Changed"] == "0"
    assert "over 6.0 hours" in a.summary


@pytest.mark.parametrize("capped,blob,words", [(True, True, "the newer walk stopped at a limit"),
                                               (False, False, "the newer walk has no stored limits to read")])
def test_change_rates_capped_walk_leaves_added_removed_and_growth_out(fs_steps_installed, fs_registry, capped, blob,
                                                                      words):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a.txt"), row("b.txt")])
    walk(reg, "tree", "2026-10-02T00:00:00", [row("a.txt", size=99)], capped=capped, blob=blob)
    a = run(url, "fs_change_rates")["fs_change_rates"]
    v = a.resource_properties
    assert state(a) == "measured" and v["Files Modified"] == "1" and v["Files Unchanged"] == "0"
    for absent in ("Files Added", "Files Removed", "Bytes Growth"):
        assert absent not in v                                  # not measured, so not a number
    assert words in v["notMeasured"] and "not measured" in a.summary


# ── fs_duplicates (F6) ──

def test_duplicates_no_hashes_says_why_from_the_walks_own_states(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a", state="hashing_off"), row("b", state="hashing_off"),
                                              row(".env", state="hashing_off")])
    out = run(url, "fs_duplicates")
    for key in ("fs_duplicates", "fs_duplicates_elsewhere"):
        assert state(out[key]) == "not_established" and out[key].summary == "not measured · no hashes"
        assert "3 hashing off" in out[key].explanation
        assert "Redundant Files" not in out[key].resource_properties


def test_duplicates_sets_copies_and_bytes_empty_files_apart(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [
        row("a/x.csv", size=100, digest="h1", state="hashed"), row("b/x.csv", size=100, digest="h1", state="hashed"),
        row("c/x.csv", size=100, digest="h1", state="hashed"), row("d/y", size=7, digest="h2", state="hashed"),
        row("e1", size=0, digest="h0", state="hashed"), row("e2", size=0, digest="h0", state="hashed"),
        row("big.bin", size=10**9, state="over_limit")])
    out = run(url, "fs_duplicates")
    a = out["fs_duplicates"]
    v = a.resource_properties
    assert state(a) == "measured"
    assert (v["Duplicate Sets"], v["Files In Duplicate Sets"], v["Redundant Files"], v["Redundant Bytes"]) == (
        "1", "3", "2", "200")
    assert v["Empty Files Not Compared"] == "2" and v["Files Not Hashed"] == "1"
    assert a.summary == ("1 set of identical files: 2 copies beyond the first take 200 bytes; 1 file not hashed, so "
                         "not compared")
    assert not leaks(out, ["a/x.csv", "big.bin", "h1"])


def test_duplicates_none_found_is_nothing_found_not_not_established(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a", digest="h1", state="hashed"),
                                              row("b", digest="h2", state="hashed")])
    a = run(url, "fs_duplicates")["fs_duplicates"]
    assert state(a) == "nothing_found" and a.resource_properties["Redundant Files"] == "0"


def test_duplicates_elsewhere_three_ways(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a", digest="h1", state="hashed"),
                                              row("b", digest="h9", state="hashed")])
    e = run(url, "fs_duplicates")["fs_duplicates_elsewhere"]
    assert state(e) == "not_established" and "no other registered file system has a walk" in e.explanation
    walk(reg, "other", "2026-10-01T00:00:00", [row("x", state="hashing_off")])
    e = run(url, "fs_duplicates")["fs_duplicates_elsewhere"]
    assert state(e) == "not_established" and "none of the 1 other registered file systems has hashed" in e.explanation
    walk(reg, "other", "2026-10-02T00:00:00", [row("x", digest="h1", state="hashed"),
                                               row("y", digest="h1", state="hashed")])
    e = run(url, "fs_duplicates")["fs_duplicates_elsewhere"]
    assert state(e) == "measured"
    assert e.resource_properties["Files Also Elsewhere"] == "1"
    assert e.resource_properties["Matches By File System"] == "other: 2 files"
    walk(reg, "other", "2026-10-03T00:00:00", [row("z", digest="h5", state="hashed")])
    e = run(url, "fs_duplicates")["fs_duplicates_elsewhere"]
    assert state(e) == "nothing_found" and e.resource_properties["Files Also Elsewhere"] == "0"


# ── fs_activity (F8) ──

def test_activity_ages_at_the_walks_time_not_now(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-10T00:00:00", [
        row("hot", mtime="2026-10-08T00:00:00", size=1), row("warm", mtime="2026-08-01T00:00:00", size=2),
        row("cold", mtime="2024-01-01T00:00:00", size=4), row("cold2", mtime="2025-01-01T00:00:00", size=8)])
    a = run(url, "fs_activity")["fs_activity"]
    v = a.resource_properties
    assert state(a) == "measured"
    assert (v["Hot Files"], v["Warm Files"], v["Cold Files"], v["Cold Bytes"]) == ("1", "1", "2", "12")
    assert (v["Hot Days"], v["Cold Days"], v["asOf"]) == ("7", "365", "2026-10-10T00:00:00")


def test_activity_access_times_not_recorded_on_this_walk(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-10T00:00:00", [row("a"), row("b")])        # accessed_at NULL: before the column
    a = run(url, "fs_activity")["fs_access_activity"]
    assert state(a) == "not_established" and a.summary == "not measured · access times not recorded on this walk"
    assert "Cold Files" not in a.resource_properties            # can't tell is never "cold"


def test_activity_words_carry_the_walks_time_in_the_pages_form_not_raw_iso(fs_steps_installed, fs_registry):
    """W28: "(2026-10-25T23:47:39.704588)" on the Survey card and the Curate row reads "(10-25 23:47)"; the
    value's asOf stays the walk's own id."""
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-25T23:47:39.704588", [row("a"), row("b")])
    a = run(url, "fs_activity")["fs_access_activity"]
    assert state(a) == "not_established"
    assert "access times not recorded on this walk (10-25 23:47)" in a.explanation, a.explanation
    assert "T23" not in a.explanation and ".704" not in a.explanation


def test_activity_access_times_not_recorded_on_this_mount(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-10T00:00:00", [row("a", mtime="2026-01-01T00:00:00", atime="2026-01-01T00:00:00"),
                                              row("b", mtime="2026-02-01T00:00:00", atime="2026-01-15T00:00:00")])
    a = run(url, "fs_activity")["fs_access_activity"]
    assert state(a) == "not_established" and a.summary == "not measured · access times not recorded on this mount"


def test_activity_reads_measured_and_resource_explorers_own_read_counted_apart(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("ours", digest="h", state="hashed"), row("theirs")])
    walk(reg, "tree", "2026-10-10T00:00:00", [
        # read after RE's previous walk hashed it: possibly RE's own read, never "hot"
        row("ours", mtime="2025-01-01T00:00:00", atime="2026-10-01T00:00:05", digest="h", state="hashed"),
        row("theirs", mtime="2025-01-01T00:00:00", atime="2026-10-09T00:00:00"),
        row("old", mtime="2024-01-01T00:00:00", atime="2024-06-01T00:00:00")])
    a = run(url, "fs_activity")["fs_access_activity"]
    v = a.resource_properties
    assert state(a) == "measured"
    assert (v["Hot Files"], v["Cold Files"], v["Access Possibly Resource Explorer's Own Read"]) == ("1", "1", "1")
    assert "possibly by Resource Explorer's previous walk" in a.summary


def test_activity_parameters_are_read_from_the_manifest(fs_steps_installed, fs_registry, monkeypatch):
    from trellis_analytics.discovery import discover

    reg, url = fs_registry
    walk(reg, "tree", "2026-10-10T00:00:00", [row("a", mtime="2026-10-01T00:00:00")])
    m = discover()["fs_activity"]
    monkeypatch.setattr(m, "parameters", {"hot_days": 30, "cold_days": 60})
    from trellis_analytics.readers.ods import OdsReader
    from trellis_analytics.step import HostContext, SurveyStep

    out = SurveyStep(m).run(HostContext(resource_type="filesystem", slug="tree", producing_run="r", ods=OdsReader(url)))
    assert out[0].resource_properties["Hot Files"] == "1" and out[0].resource_properties["Hot Days"] == "30"


# ── fs_permissions (F9) ──

def test_permissions_no_stat_fields(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a", mode="", owner="", group="")])
    out = run(url, "fs_permissions")
    for key in ("fs_permissions", "world_readable_sensitive"):
        assert state(out[key]) == "not_established" and out[key].summary == "not measured · no stat fields"


def test_permissions_counts_and_a_request_for_action_with_no_names(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [
        row(".env", mode="0644", state="secret_name", owner="alice"),
        row("keys/id_rsa", mode="0600", state="secret_name", owner="alice"),
        row("data.csv", mode="0666", owner="bob", group="data"),
        row("bin/tool", mode="4755", owner="root", group="wheel"),
        row("notes.txt", mode="0640", owner="bob")])
    out = run(url, "fs_permissions")
    p, r = out["fs_permissions"], out["world_readable_sensitive"]
    v = p.resource_properties
    assert state(p) == "measured" and p.annotation_type == "ResourceProfileAnnotation"
    assert (v["Distinct Owners"], v["Distinct Groups"], v["World Readable Files"], v["World Writable Files"],
            v["Group Writable Files"], v["Setuid Or Setgid Files"]) == ("3", "3", "3", "1", "1", "1")
    assert r.annotation_type == "RequestForActionAnnotation" and state(r) == "measured"
    assert r.resource_properties["World Readable Secret Named Files"] == "1"
    assert r.resource_properties["Secret Named Files"] == "2"
    assert not leaks(out, [".env", "id_rsa", "data.csv", "alice", "bob", "root", "wheel"])


def test_permissions_sensitive_none_and_unclassified_names(fs_steps_installed, fs_registry):
    reg, url = fs_registry
    walk(reg, "tree", "2026-10-01T00:00:00", [row("a"), row("b", state="hashing_off")])
    r = run(url, "fs_permissions")["world_readable_sensitive"]
    assert state(r) == "nothing_found" and r.resource_properties["Files Whose Names Were Not Classified"] == "1"
    assert "1 file name not classified, so not checked" in r.summary
    walk(reg, "tree", "2026-10-02T00:00:00", [row("a", state="hashing_off")])
    r = run(url, "fs_permissions")["world_readable_sensitive"]
    assert state(r) == "not_established" and "classified no file name" in r.explanation
