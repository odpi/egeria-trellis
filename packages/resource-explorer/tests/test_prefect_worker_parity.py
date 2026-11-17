"""A step is dispatched only to a Prefect worker on Resource Explorer's own code (2026-11-13).

DESIGN-PREFECT-ROLE-AND-RE-WORKER-2026-11-13 §1.6: a container worker advertises the commit its image was
built from (`re-worker-<sha12>-<host>`) and serves its own deployment and queue; RE sends a step there only
when that is its own commit. A worker on other code is refused in words and the step runs locally (the
existing fallback); a worker that advertises no commit (the host worker) keeps serving the default
deployment, as before.
"""
from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

import resource_explorer.surveyors.prefect_adapter as adapter
from resource_explorer import build_info

OWN = "0123456789ab"
OTHER = "fedcba987654"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _own_sha(monkeypatch):
    monkeypatch.setenv("TRELLIS_RE_SHA", OWN + "cdef0123456789abcdef01234567")
    build_info.re_sha.cache_clear()
    adapter._PARITY_UNCHECKED_WARNED.clear()
    yield
    build_info.re_sha.cache_clear()
    adapter._PARITY_UNCHECKED_WARNED.clear()


def _worker(name, status="ONLINE"):
    return types.SimpleNamespace(name=name, status=types.SimpleNamespace(value=status))


def _client(workers, deployments=("RE Survey Flow/re-survey-step-deployment",), pools=None):
    """`pools` maps a deployment name to its pool; every other deployment is on resource-explorer-pool."""
    c = MagicMock()
    known = {n: types.SimpleNamespace(id=n, name=n, work_pool_name=(pools or {}).get(n, "resource-explorer-pool"))
             for n in deployments}

    async def read_deployment(name):
        if name not in known:
            raise LookupError(f"{name} not found")
        return known[name]

    c.read_deployment_by_name = AsyncMock(side_effect=read_deployment)
    c.read_workers_for_work_pool = AsyncMock(return_value=workers)
    return c


def _choose(client):
    return asyncio.run(adapter._choose_deployment(client, "resource-explorer-pool"))


SHA_DEP = f"RE Survey Flow/re-survey-step-{OWN}"


class TestChooseDeployment:
    def test_a_worker_on_this_commit_gets_its_own_deployment(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os"), _worker("re-host-worker-laz")],
                    deployments=("RE Survey Flow/re-survey-step-deployment", SHA_DEP))
        assert _choose(c).name == SHA_DEP

    def test_a_worker_with_no_commit_serves_the_default_deployment_as_before(self):
        c = _client([_worker("re-host-worker-laz")])
        assert _choose(c).name == "RE Survey Flow/re-survey-step-deployment"
        c = _client([_worker("re-host-worker-laz"), _worker(f"re-worker-{OTHER}-pop-os")])
        assert _choose(c).name == "RE Survey Flow/re-survey-step-deployment", "the other-code worker is not used"

    def test_a_worker_with_no_commit_is_warned_about_once_per_worker(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        for _ in range(3):
            _choose(_client([_worker("re-host-worker-laz"), _worker("re-host-worker-b")]))
        warns = [r.getMessage() for r in caplog.records if "commit parity not checked" in r.getMessage()]
        assert len(warns) == 2, warns
        assert warns[0].startswith("commit parity not checked: worker re-host-worker-b advertises none")
        assert f"this Resource Explorer runs {OWN}" in warns[0]

    def test_only_workers_on_other_code_is_refused_naming_both_commits(self):
        c = _client([_worker(f"re-worker-{OTHER}-pop-os")])
        with pytest.raises(RuntimeError) as ei:
            _choose(c)
        assert str(ei.value) == (f"no online Prefect worker runs this Resource Explorer's code ({OWN}); "
                                 f"online workers on pool resource-explorer-pool run {OTHER} "
                                 "(Resource Explorer is configured for pool resource-explorer-pool; the default "
                                 "deployment 'RE Survey Flow/re-survey-step-deployment' is on pool "
                                 f"resource-explorer-pool; online commits: {OTHER})")

    def test_an_offline_worker_on_this_commit_does_not_count(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os", "OFFLINE")], deployments=(
            "RE Survey Flow/re-survey-step-deployment", SHA_DEP))
        with pytest.raises(RuntimeError, match="no online Prefect worker for pool resource-explorer-pool"):
            _choose(c)

    def test_an_unknown_own_commit_never_claims_parity(self, monkeypatch):
        monkeypatch.setenv("TRELLIS_RE_SHA", "")
        build_info.re_sha.cache_clear()
        with patch.object(build_info.subprocess, "run", side_effect=OSError("no git")):
            c = _client([_worker(f"re-worker-{OWN}-pop-os")])
            with pytest.raises(RuntimeError, match=r"\(commit unknown\)"):
                _choose(c)

    def test_a_matching_worker_whose_deployment_is_missing_is_said(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")])
        with pytest.raises(RuntimeError, match=f"deployment '{SHA_DEP}' is missing"):
            _choose(c)

    def test_names(self):
        assert adapter.worker_sha(_worker(f"re-worker-{OWN}-pop-os")) == OWN
        assert adapter.worker_sha(_worker(f"re-worker-{OWN}")) == OWN
        for name in ("re-host-worker-laz", "ProcessWorker 856bbae1", f"re-worker-{OWN}x", None):
            assert adapter.worker_sha(_worker(name)) == ""
        assert adapter.sha_deployment(OWN) == f"re-survey-step-{OWN}"
        assert adapter.sha_work_queue(OWN) == f"re-{OWN}"


DEFAULT = "RE Survey Flow/re-survey-step-deployment"


class TestTheConfiguredPoolIsAuthoritative:
    """Architect ruling 2026-11-14 (§RE-RULING of RULING-RE-PREFECT-AB-ENGINE-2026-11-13). Found on hedwig: the
    default deployment was left on default-agent-pool, the pool was taken from it, and the live container worker
    on resource-explorer-pool was never seen, so every step ran locally while the banner said "1 live worker"."""

    def test_1_a_worker_on_this_commit_with_its_deployment_on_the_configured_pool(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")], deployments=(DEFAULT, SHA_DEP),
                    pools={DEFAULT: "default-agent-pool"})
        assert _choose(c).name == SHA_DEP, "a default deployment on another pool does not get in the way"
        c.read_workers_for_work_pool.assert_awaited_once_with("resource-explorer-pool")

    def test_2_its_deployment_on_another_pool_is_refused_naming_both(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")], deployments=(DEFAULT, SHA_DEP),
                    pools={SHA_DEP: "default-agent-pool"})
        with pytest.raises(RuntimeError) as ei:
            _choose(c)
        assert str(ei.value).startswith(f"deployment '{SHA_DEP}' is on pool default-agent-pool but Resource "
                                        "Explorer is configured for pool resource-explorer-pool")
        assert f"online commits: {OWN}" in str(ei.value)

    def test_3_a_worker_with_no_commit_and_the_default_deployment_on_the_configured_pool(self):
        """laz's case: the host worker, the default deployment on resource-explorer-pool. Unchanged."""
        c = _client([_worker("re-host-worker-laz")])
        assert _choose(c).name == DEFAULT
        c.read_workers_for_work_pool.assert_awaited_once_with("resource-explorer-pool")

    def test_4_the_default_deployment_on_another_pool_is_refused_never_dispatched(self):
        """hedwig's case, with a worker that advertises no commit: never sent to another pool silently."""
        c = _client([_worker("re-host-worker-hedwig")], pools={DEFAULT: "default-agent-pool"})
        with pytest.raises(RuntimeError) as ei:
            _choose(c)
        msg = str(ei.value)
        assert msg.startswith(f"default deployment '{DEFAULT}' is on pool default-agent-pool but Resource "
                              "Explorer is configured for pool resource-explorer-pool")
        assert "online commits: none advertised by re-host-worker-hedwig" in msg
        c.read_workers_for_work_pool.assert_awaited_once_with("resource-explorer-pool")

    def test_5_no_online_worker_on_the_configured_pool(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os", "OFFLINE")], deployments=(DEFAULT, SHA_DEP),
                    pools={DEFAULT: "default-agent-pool"})
        with pytest.raises(RuntimeError) as ei:
            _choose(c)
        assert str(ei.value) == ("no online Prefect worker for pool resource-explorer-pool; run `make prefect-up` "
                                 "(Resource Explorer is configured for pool resource-explorer-pool; the default "
                                 f"deployment '{DEFAULT}' is on pool default-agent-pool; online commits: none)")
        c.read_workers_for_work_pool.assert_awaited_once_with("resource-explorer-pool")

    def test_a_missing_default_deployment_does_not_stop_a_worker_on_this_commit(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")], deployments=(SHA_DEP,))
        assert _choose(c).name == SHA_DEP

    def test_a_missing_default_deployment_is_said_for_a_worker_with_no_commit(self):
        c = _client([_worker("re-host-worker-laz")], deployments=())
        with pytest.raises(RuntimeError, match=f"Prefect deployment '{DEFAULT}' not found") as ei:
            _choose(c)
        assert f"the default deployment '{DEFAULT}' is missing" in str(ei.value)

    def test_dispatch_itself_does_not_read_the_other_pools(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")], deployments=(DEFAULT, SHA_DEP))
        _choose(c)
        c.read_work_pools.assert_not_called()

    def test_look_elsewhere_names_re_workers_only(self):
        pools = {"resource-explorer-pool": [_worker(f"re-worker-{OWN}-pop-os")],
                 "default-agent-pool": [_worker("re-host-worker-hedwig"), _worker(f"re-worker-{OTHER}-x", "OFFLINE")],
                 "egeria-pool": [_worker("ProcessWorker 4b23da06")]}
        c = _client([], deployments=(DEFAULT, SHA_DEP))
        c.read_work_pools = AsyncMock(return_value=[types.SimpleNamespace(name=n) for n in pools])
        c.read_workers_for_work_pool = AsyncMock(side_effect=lambda name: pools[name])
        choice = asyncio.run(adapter.resolve_dispatch(c, "resource-explorer-pool", look_elsewhere=True))
        assert choice.elsewhere == ["re-host-worker-hedwig on pool default-agent-pool"]
        assert choice.deployment.name == SHA_DEP, "it never changes the decision"

    def test_the_lookup_is_read_only(self):
        c = _client([_worker(f"re-worker-{OWN}-pop-os")], deployments=(DEFAULT, SHA_DEP))
        _choose(c)
        assert {m[0] for m in c.method_calls} <= {"read_deployment_by_name", "read_workers_for_work_pool"}


def test_a_refused_dispatch_runs_locally_and_says_why(monkeypatch):
    """The parity refusal goes through run_prefect_step's existing fallback: the step runs locally and
    dispatch_info carries the reason, which the run report shows ("ran locally: Prefect dispatch failed — …")."""
    client = _client([_worker(f"re-worker-{OTHER}-pop-os")])
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    cfg = types.SimpleNamespace(prefect=types.SimpleNamespace(
        enabled=True, api_url="http://x/api", work_pool="resource-explorer-pool"))
    local = MagicMock(return_value={"ran": "locally"})
    info: dict = {}
    with patch.object(adapter, "get_config", lambda: cfg), \
         patch.object(adapter, "re_prefect_client", return_value=client), \
         patch.object(adapter.run_surveyor_step_task, "fn", local):
        out = adapter.run_prefect_step("database", "db1", "soda_data_quality", {}, dispatch_info=info)
    assert out == {"ran": "locally"}
    assert info["engine"] == "local"
    assert "runs this Resource Explorer's code" in info["dispatch_failed"]
    client.create_flow_run_from_deployment.assert_not_called()


class TestBuildInfo:
    def test_the_image_build_arg_wins(self):
        assert build_info.re_sha().startswith(OWN) and build_info.re_sha_short() == OWN

    def test_a_checkout_answers_with_git(self, monkeypatch):
        monkeypatch.delenv("TRELLIS_RE_SHA")
        build_info.re_sha.cache_clear()
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        assert build_info.re_sha() == head

    def test_garbage_is_not_a_commit(self, monkeypatch):
        monkeypatch.setenv("TRELLIS_RE_SHA", "not-a-sha")
        build_info.re_sha.cache_clear()
        with patch.object(build_info.subprocess, "run", side_effect=OSError("no git")):
            assert build_info.re_sha() == ""


def test_deploy_takes_a_name_and_a_queue():
    from resource_explorer.cli.main import app
    from resource_explorer.prefect import flows

    sourced = MagicMock()
    with patch.object(flows.re_survey_flow, "from_source", return_value=sourced) as from_source:
        out = CliRunner().invoke(app, ["prefect", "deploy", "--name", f"re-survey-step-{OWN}",
                                       "--work-queue", f"re-{OWN}"])
    assert out.exit_code == 0, out.output
    assert from_source.call_args.kwargs["source"].endswith("packages/resource-explorer")
    kw = sourced.deploy.call_args.kwargs
    assert kw["name"] == f"re-survey-step-{OWN}" and kw["work_queue_name"] == f"re-{OWN}"
    with patch.object(flows.re_survey_flow, "from_source", return_value=sourced):
        CliRunner().invoke(app, ["prefect", "deploy"])
    kw = sourced.deploy.call_args.kwargs
    assert kw["name"] == "re-survey-step-deployment" and kw["work_queue_name"] is None, "the host default is unchanged"


# ── the image's prefect-worker role, run against stub commands ────────────────

ENTRYPOINT = REPO / "docker" / "entrypoint-resource-explorer.sh"


def _stub(bin_dir: Path, name: str, body: str):
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _run_role(tmp_path, *, sha, steps_line):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    _stub(bin_dir, "python", f'echo "{steps_line}"')
    _stub(bin_dir, "resource-explorer", f'echo "resource-explorer $*" >> {calls}')
    _stub(bin_dir, "prefect", f'echo "prefect $*" >> {calls}')
    _stub(bin_dir, "hostname", 'echo pop-os')
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "TRELLIS_RE_SHA": sha}
    r = subprocess.run(["bash", str(ENTRYPOINT), "prefect-worker"], env=env, capture_output=True, text=True,
                       check=False)
    return r, (calls.read_text() if calls.exists() else "")


NONE_MISSING = ("library steps · Egeria host: change_rates, hub_tables (none missing) · "
                "Files host: fs_activity (none missing)")


def test_the_role_deploys_its_own_deployment_then_serves_only_its_queue(tmp_path):
    r, calls = _run_role(tmp_path, sha=OWN + "cdef0123456789abcdef01234567", steps_line=NONE_MISSING)
    assert r.returncode == 0, r.stderr
    assert calls.splitlines() == [
        f"resource-explorer prefect deploy --name re-survey-step-{OWN} --work-queue re-{OWN}",
        "resource-explorer prefect prune-deployments --keep 3",
        (f"prefect worker start --pool resource-explorer-pool --type process --work-queue re-{OWN} "
         f"--name re-worker-{OWN}-pop-os"),
    ]
    assert NONE_MISSING in r.stderr


def test_the_role_refuses_without_a_commit(tmp_path):
    r, calls = _run_role(tmp_path, sha="", steps_line=NONE_MISSING)
    assert r.returncode == 1 and "TRELLIS_RE_SHA is not set" in r.stderr and calls == ""


def test_the_role_refuses_when_a_step_is_missing(tmp_path):
    r, calls = _run_role(tmp_path, sha=OWN, steps_line=(
        "library steps · Egeria host: change_rates (1 missing: hub_tables) · Files host: none installed "
        "(5 missing: fs_activity, fs_change_rates, fs_duplicates, fs_layout_signals, fs_permissions)"))
    assert r.returncode == 1 and "library steps are missing" in r.stderr and calls == ""


def test_the_image_carries_its_commit():
    """(Installing every step is test_dockerfile_installs_steps.py's.)"""
    text = (REPO / "docker" / "Dockerfile.resource-explorer").read_text()
    runtime = text.split("AS runtime", 1)[1]
    assert 'ENV TRELLIS_RE_SHA="${TRELLIS_RE_SHA}"' in runtime
    assert "TRELLIS_RE_SHA=${{ github.sha }}" in (REPO / ".github" / "workflows" / "images.yml").read_text()
    assert "--build-arg TRELLIS_RE_SHA=" in (REPO / "Makefile").read_text()
    assert os.access(ENTRYPOINT, os.R_OK)


# ── pruning per-commit deployments (Architect ruling 2026-11-13, §D) ──────────

def _id(name):
    import uuid
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def _dep(name, created, pool="resource-explorer-pool"):
    return types.SimpleNamespace(id=_id(name), name=name, created=created, work_pool_name=pool)


def _prune(deployments, active=(), keep=3, workers=()):
    client = MagicMock()
    client.read_workers_for_work_pool = AsyncMock(return_value=list(workers))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.read_deployments = AsyncMock(return_value=deployments)

    async def runs(*, deployment_filter, flow_run_filter, limit):
        return ["a run"] if deployment_filter.id.any_[0] in {_id(n) for n in active} else []

    client.read_flow_runs = runs
    client.delete_deployment = AsyncMock()
    with patch.object(adapter, "re_prefect_client", return_value=client):
        out = asyncio.run(adapter.prune_commit_deployments(keep, "resource-explorer-pool"))
    return out, [c.args[0] for c in client.delete_deployment.call_args_list]


def test_prune_keeps_the_newest_three_and_deletes_older_ones():
    deps = [_dep(f"re-survey-step-{i:012x}", f"2026-11-{10 + i:02d}T00:00:00Z") for i in range(6)]
    out, deleted = _prune(deps)
    assert out["kept"] == [f"re-survey-step-{i:012x}" for i in (5, 4, 3)]
    assert out["deleted"] == [f"re-survey-step-{i:012x}" for i in (2, 1, 0)]
    assert deleted == [_id(f"re-survey-step-{i:012x}") for i in (2, 1, 0)]


def test_prune_never_touches_a_deployment_in_use_the_default_or_another_pool():
    old = "re-survey-step-000000000000"
    deps = [_dep(f"re-survey-step-{i:012x}", f"2026-11-{10 + i:02d}T00:00:00Z") for i in range(4)]
    deps += [_dep("re-survey-step-deployment", "2026-01-01T00:00:00Z"),
             _dep("re-survey-step-aaaaaaaaaaaa", "2026-01-01T00:00:00Z", pool="other-pool"),
             _dep("something-else", "2026-01-01T00:00:00Z")]
    out, deleted = _prune(deps, active={old})
    assert out["skipped_active"] == [old] and deleted == []
    assert "re-survey-step-deployment" not in out["kept"] + out["deleted"]


def test_prune_refuses_to_keep_none():
    with pytest.raises(ValueError):
        asyncio.run(adapter.prune_commit_deployments(0, "resource-explorer-pool"))


def test_the_prune_command_prints_every_name_it_deletes():
    from resource_explorer.cli.main import app

    result = {"kept": ["re-survey-step-000000000005"], "deleted": ["re-survey-step-000000000001"],
              "skipped_active": ["re-survey-step-000000000002"]}
    with patch.object(adapter, "prune_commit_deployments", AsyncMock(return_value=result)) as prune:
        out = CliRunner().invoke(app, ["prefect", "prune-deployments", "--keep", "3"])
    assert out.exit_code == 0, out.output
    assert prune.call_args.args[0] == 3
    assert "deleted deployment re-survey-step-000000000001" in out.output
    assert "kept re-survey-step-000000000002: it has an active flow run" in out.output


def test_prune_keeps_the_deployment_of_an_online_worker_on_an_older_commit():
    """Read-back of 6888a58c: an idle worker on an older commit, outside the newest three, keeps the
    deployment it serves; an OFFLINE one does not."""
    deps = [_dep(f"re-survey-step-{i:012x}", f"2026-11-{10 + i:02d}T00:00:00Z") for i in range(6)]
    online_old = f"re-survey-step-{1:012x}"
    out, deleted = _prune(deps, workers=[_worker(f"re-worker-{1:012x}-pop-os"),
                                         _worker(f"re-worker-{0:012x}-pop-os", "OFFLINE")])
    assert out["skipped_online_worker"] == [online_old]
    assert out["deleted"] == [f"re-survey-step-{i:012x}" for i in (2, 0)]
    assert _id(online_old) not in deleted
