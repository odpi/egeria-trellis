"""
EA Phase 1, Q4 (ops hygiene): the defaults an operator gets without asking.

  - ADVISOR_LOG_LEVEL is honoured (default INFO) by the web command, for
    loguru and uvicorn; the query CLI keeps errors on stderr.
  - The data root is refused inside a git checkout and defaults to
    ~/egeria-advisor-data (inside, outside, unset, relative, linked worktree).
  - The embedding model loads offline when it is already cached, and says so;
    when it is not, one warning names the download.
  - `egeria-advisor-web` runs without --reload unless asked.
  - scripts/run_web.sh starts one process unless --both is asked for.
"""
import ast
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner
from loguru import logger

from advisor import config as cfg
from advisor.config import (
    AdvisorDataRootError,
    AdvisorSettings,
    DEFAULT_ADVISOR_DATA_ROOT,
    configure_logging,
    enclosing_git_checkout,
    resolve_advisor_data_root,
    resolve_log_level,
    uvicorn_log_level,
)

PKG = Path(__file__).resolve().parents[2]
REFUSAL = ("is inside a git checkout; set it to a directory outside any repository, "
           "e.g. ~/egeria-advisor-data")


@pytest.fixture
def loguru_lines():
    """Capture loguru messages (level, text) for the test, then restore stderr at DEBUG."""
    lines = []
    logger.remove()
    hid = logger.add(lambda m: lines.append((m.record["level"].name, m.record["message"])), level="TRACE")
    yield lines
    logger.remove(hid)
    logger.add(sys.stderr)


# ---------------------------------------------------------------------------
# Data root outside git
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    return root


def test_data_root_inside_a_checkout_is_refused_with_the_sentence(monkeypatch, repo):
    target = repo / "packages" / "egeria-advisor" / "data"
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(target))
    with pytest.raises(AdvisorDataRootError) as exc:
        resolve_advisor_data_root()
    msg = str(exc.value)
    assert msg.startswith(f"ADVISOR_DATA_PATH ({target}) {REFUSAL}")
    assert str(repo.resolve()) in msg


def test_data_root_at_the_checkout_root_is_refused(monkeypatch, repo):
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(repo))
    with pytest.raises(AdvisorDataRootError):
        resolve_advisor_data_root()


def test_data_root_inside_a_linked_worktree_is_refused(monkeypatch, tmp_path):
    # A linked worktree (and a submodule) has a .git FILE, not a directory.
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /somewhere/.git/worktrees/wt\n")
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(wt / "data"))
    with pytest.raises(AdvisorDataRootError):
        resolve_advisor_data_root()


def test_relative_data_root_from_inside_a_checkout_is_refused(monkeypatch, repo):
    (repo / "pkg").mkdir()
    monkeypatch.chdir(repo / "pkg")
    monkeypatch.setenv("ADVISOR_DATA_PATH", "./data")
    with pytest.raises(AdvisorDataRootError):
        resolve_advisor_data_root()


def test_data_root_outside_any_checkout_is_accepted(monkeypatch, tmp_path):
    target = tmp_path / "advisor-data"
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(target))
    assert enclosing_git_checkout(target) is None
    assert resolve_advisor_data_root() == target
    assert not target.exists()  # resolving does not create it


def test_unset_data_root_defaults_to_home_egeria_advisor_data(monkeypatch):
    monkeypatch.delenv("ADVISOR_DATA_PATH", raising=False)
    assert DEFAULT_ADVISOR_DATA_ROOT == Path.home() / "egeria-advisor-data"
    if enclosing_git_checkout(DEFAULT_ADVISOR_DATA_ROOT) is not None:
        pytest.skip("this machine's home directory is itself a git checkout")
    assert resolve_advisor_data_root() == DEFAULT_ADVISOR_DATA_ROOT


def test_unset_default_is_refused_too_if_home_is_a_checkout(monkeypatch, repo):
    monkeypatch.delenv("ADVISOR_DATA_PATH", raising=False)
    monkeypatch.setattr(cfg, "DEFAULT_ADVISOR_DATA_ROOT", repo / "egeria-advisor-data")
    with pytest.raises(AdvisorDataRootError):
        resolve_advisor_data_root()


def test_settings_construction_stops_on_a_refused_root(monkeypatch, repo):
    # advisor_cache_dir's default follows the data root, so building the
    # settings is where a running Advisor meets the refusal.
    monkeypatch.delenv("ADVISOR_CACHE_DIR", raising=False)
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(repo / "data"))
    with pytest.raises(AdvisorDataRootError):
        AdvisorSettings()


def test_explicit_cache_dir_inside_a_checkout_is_refused(monkeypatch, repo, tmp_path):
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path / "advisor-data"))
    monkeypatch.setenv("ADVISOR_CACHE_DIR", str(repo / "data" / "cache"))
    with pytest.raises(AdvisorDataRootError) as exc:
        AdvisorSettings()
    assert str(exc.value).startswith(
        f"ADVISOR_CACHE_DIR ({repo / 'data' / 'cache'}) is inside a git checkout; set it to a "
        "directory outside any repository, e.g. ~/egeria-advisor-data/cache")


def test_relative_cache_dir_from_inside_a_checkout_is_refused(monkeypatch, repo, tmp_path):
    # laz's .env shape: ADVISOR_CACHE_DIR=./data/cache, run from the package dir.
    (repo / "pkg").mkdir()
    monkeypatch.chdir(repo / "pkg")
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path / "advisor-data"))
    monkeypatch.setenv("ADVISOR_CACHE_DIR", "./data/cache")
    with pytest.raises(AdvisorDataRootError):
        AdvisorSettings()


def test_explicit_cache_dir_outside_any_checkout_is_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path / "advisor-data"))
    monkeypatch.setenv("ADVISOR_CACHE_DIR", str(tmp_path / "cache"))
    assert AdvisorSettings().advisor_cache_dir == tmp_path / "cache"


def test_cache_dir_tilde_is_expanded(monkeypatch, tmp_path):
    monkeypatch.setenv("ADVISOR_DATA_PATH", str(tmp_path / "advisor-data"))
    monkeypatch.setenv("ADVISOR_CACHE_DIR", "~/egeria-advisor-data/cache")
    if enclosing_git_checkout(Path.home()) is not None:
        pytest.skip("this machine's home directory is itself a git checkout")
    assert AdvisorSettings().advisor_cache_dir == Path.home() / "egeria-advisor-data" / "cache"


def test_module_settings_reraises_the_refusal_instead_of_defaulting():
    # The module-level `settings = AdvisorSettings()` falls back to defaults on
    # other errors; the refusal must not be swallowed that way.
    tree = ast.parse((PKG / "advisor" / "config.py").read_text())
    handlers = [h for node in ast.walk(tree) if isinstance(node, ast.Try)
                for h in node.handlers
                if isinstance(h.type, ast.Name) and h.type.id == "AdvisorDataRootError"]
    assert handlers and all(isinstance(h.body[-1], ast.Raise) for h in handlers)


# ---------------------------------------------------------------------------
# Log level
# ---------------------------------------------------------------------------

def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("ADVISOR_LOG_LEVEL", raising=False)
    monkeypatch.setattr(cfg.settings, "advisor_log_level", "INFO")
    assert resolve_log_level() == "INFO"


def test_log_level_reads_env(monkeypatch):
    monkeypatch.setenv("ADVISOR_LOG_LEVEL", "debug")
    assert resolve_log_level() == "DEBUG"


def test_log_level_explicit_value_wins(monkeypatch):
    monkeypatch.setenv("ADVISOR_LOG_LEVEL", "DEBUG")
    assert resolve_log_level("warn") == "WARNING"


def test_unknown_log_level_falls_back_to_info_and_says_so(monkeypatch, loguru_lines):
    assert resolve_log_level("LOUD") == "INFO"
    assert any(lvl == "WARNING" and "'LOUD'" in msg for lvl, msg in loguru_lines)


@pytest.mark.parametrize("level,expected", [("TRACE", "trace"), ("DEBUG", "debug"), ("INFO", "info"),
                                            ("SUCCESS", "info"), ("WARNING", "warning"),
                                            ("ERROR", "error"), ("CRITICAL", "critical")])
def test_uvicorn_level_mapping(level, expected):
    assert uvicorn_log_level(level) == expected


def test_configure_logging_filters_below_the_level():
    seen = []
    configure_logging("WARNING", sink=lambda m: seen.append(m.record["level"].name))
    try:
        logger.info("quiet")
        logger.warning("loud")
        logger.error("louder")
    finally:
        logger.remove()
        logger.add(sys.stderr)
    assert seen == ["WARNING", "ERROR"]


# ---------------------------------------------------------------------------
# The web command: --reload off by default, log level passed to uvicorn
# ---------------------------------------------------------------------------

@pytest.fixture
def web_run(monkeypatch):
    import uvicorn
    from advisor.cli import main as cli_main

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    configured = []
    monkeypatch.setattr(cfg, "configure_logging", lambda level, sink=None: configured.append(level))

    def run(*args, env=None):
        result = CliRunner().invoke(cli_main.web_command, list(args), env=env or {})
        assert result.exit_code == 0, result.output
        return calls[-1], configured[-1]
    return run


def test_web_defaults_no_reload_and_info(web_run, monkeypatch):
    monkeypatch.delenv("ADVISOR_LOG_LEVEL", raising=False)
    monkeypatch.setattr(cfg.settings, "advisor_log_level", "INFO")
    (app, kw), level = web_run()
    assert app == "advisor.web.app:app"
    assert kw["reload"] is False and kw["reload_dirs"] is None
    assert kw["log_level"] == "info" and level == "INFO"
    assert "ssl_certfile" not in kw


def test_web_reload_is_opt_in(web_run):
    (_, kw), _ = web_run("--reload")
    assert kw["reload"] is True and kw["reload_dirs"] == ["advisor"]


def test_web_honours_advisor_log_level(web_run):
    (_, kw), level = web_run(env={"ADVISOR_LOG_LEVEL": "DEBUG"})
    assert level == "DEBUG" and kw["log_level"] == "debug"


def test_web_log_level_flag_overrides_env(web_run):
    (_, kw), level = web_run("--log-level", "error", env={"ADVISOR_LOG_LEVEL": "DEBUG"})
    assert level == "ERROR" and kw["log_level"] == "error"


def test_web_passes_tls_files(web_run):
    (_, kw), _ = web_run("--ssl-certfile", "/c.pem", "--ssl-keyfile", "/k.pem")
    assert kw["ssl_certfile"] == "/c.pem" and kw["ssl_keyfile"] == "/k.pem"


def test_query_cli_no_longer_points_stderr_at_devnull():
    # The non-debug path used os.dup2(devnull, 2), hiding every error and
    # traceback. It now keeps an ERROR sink on stderr.
    tree = ast.parse((PKG / "advisor" / "cli" / "main.py").read_text())
    ask = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "ask")
    src = ast.unparse(ask)
    assert "dup2" not in src and "devnull" not in src
    assert "configure_logging('ERROR')" in src


# ---------------------------------------------------------------------------
# Offline embedding model
# ---------------------------------------------------------------------------

_HF_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


@pytest.fixture
def emb(monkeypatch):
    """advisor.embeddings, with the HF offline variables restored afterwards.

    Importing the module applies the offline default to this process (that is
    the point of it); the snapshot keeps that from leaking into later tests.
    """
    saved = {k: os.environ.get(k) for k in _HF_VARS}
    import advisor.embeddings as module
    for k, v in saved.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return module


def _cache_with(tmp_path, repo_id, populated=True):
    cache = tmp_path / "hub"
    snap = cache / ("models--" + repo_id.replace("/", "--")) / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    if populated:
        (snap / "config.json").write_text("{}")
    return cache


def test_cached_model_is_detected(emb, tmp_path):
    cache = _cache_with(tmp_path, "sentence-transformers/all-MiniLM-L6-v2")
    assert emb.hf_model_cached("sentence-transformers/all-MiniLM-L6-v2", cache)
    assert emb.hf_model_cached("all-MiniLM-L6-v2", cache)  # bare sentence-transformers name
    assert not emb.hf_model_cached("org/other-model", cache)


def test_empty_snapshot_is_not_cached(emb, tmp_path):
    cache = _cache_with(tmp_path, "org/m", populated=False)
    assert not emb.hf_model_cached("org/m", cache)


def test_local_model_directory_counts_as_cached(emb, tmp_path):
    assert emb.hf_model_cached(str(tmp_path))


def test_hub_cache_dir_precedence(emb, monkeypatch, tmp_path):
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    assert emb.hf_hub_cache_dir() == Path.home() / ".cache" / "huggingface" / "hub"
    monkeypatch.setenv("HF_HOME", str(tmp_path / "home"))
    assert emb.hf_hub_cache_dir() == tmp_path / "home" / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "direct"))
    assert emb.hf_hub_cache_dir() == tmp_path / "direct"


def test_cached_model_sets_offline_flags(emb, monkeypatch, tmp_path):
    for k in _HF_VARS:
        monkeypatch.delenv(k, raising=False)
    cache = _cache_with(tmp_path, "org/m")
    assert emb.prefer_offline_if_cached("org/m", cache) == "offline"
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_cached_model_updates_an_already_imported_hub(emb, monkeypatch, tmp_path):
    for k in _HF_VARS:
        monkeypatch.delenv(k, raising=False)
    fake = types.SimpleNamespace(HF_HUB_OFFLINE=False)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", fake)
    assert emb.prefer_offline_if_cached("org/m", _cache_with(tmp_path, "org/m")) == "offline"
    assert fake.HF_HUB_OFFLINE is True


def test_operator_choice_to_stay_online_is_left_alone(emb, monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    assert emb.prefer_offline_if_cached("org/m", _cache_with(tmp_path, "org/m")) == "explicit-online"
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_uncached_model_leaves_flags_unset(emb, monkeypatch, tmp_path):
    for k in _HF_VARS:
        monkeypatch.delenv(k, raising=False)
    assert emb.prefer_offline_if_cached("org/missing", tmp_path / "hub") == "download"
    assert all(k not in os.environ for k in _HF_VARS)


def _generator(emb, model_name):
    gen = object.__new__(emb.EmbeddingGenerator)
    gen.model_name = model_name
    return gen


def test_uncached_model_logs_one_warning_naming_the_download(emb, monkeypatch, loguru_lines):
    monkeypatch.setattr(emb, "prefer_offline_if_cached", lambda name, cache_dir=None: "download")
    gen = _generator(emb, "org/missing-model")
    gen.load_mode = gen._announce_load_mode()
    gen._log_loaded()
    warnings = [m for lvl, m in loguru_lines if lvl == "WARNING"]
    assert len(warnings) == 1
    assert "org/missing-model" in warnings[0] and "downloading" in warnings[0]
    assert not any("loaded from cache" in m for _, m in loguru_lines)


def test_cached_model_logs_one_info_line_after_loading(emb, monkeypatch, loguru_lines):
    monkeypatch.setattr(emb, "prefer_offline_if_cached", lambda name, cache_dir=None: "offline")
    gen = _generator(emb, "org/cached-model")
    gen.load_mode = gen._announce_load_mode()
    assert loguru_lines == []  # nothing claimed before the load succeeds
    gen._log_loaded()
    assert loguru_lines == [("INFO", "embedding model loaded from cache (offline): org/cached-model")]


def test_offline_decision_runs_before_sentence_transformers_import():
    # huggingface_hub reads the flags once, at import: the decision must come first.
    src = (PKG / "advisor" / "embeddings.py").read_text()
    assert src.index("_MODEL_LOAD_MODE[\"mode\"] = prefer_offline_if_cached(") < \
        src.index("from sentence_transformers import SentenceTransformer")


# ---------------------------------------------------------------------------
# scripts/run_web.sh: one process by default
# ---------------------------------------------------------------------------

@pytest.fixture
def run_web(tmp_path):
    """Run a copy of run_web.sh against a fake egeria-advisor-web that records its args.

    The copy sits in a scratch package dir with no .venv and no .env, so the
    script can neither activate the real venv nor read a real .env.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    pkg = tmp_path / "pkg"
    (pkg / "scripts").mkdir(parents=True)
    shutil.copy(PKG / "scripts" / "run_web.sh", pkg / "scripts" / "run_web.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    fake = bindir / "egeria-advisor-web"
    fake.write_text(f'#!/bin/bash\necho "$*" >> "{log}"\n')
    fake.chmod(0o755)
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("c")
    key.write_text("k")

    def run(*args, tls=False):
        env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
        if tls:
            env.update(ADVISOR_SSL_CERTFILE=str(cert), ADVISOR_SSL_KEYFILE=str(key))
        proc = subprocess.run(["bash", str(pkg / "scripts" / "run_web.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=30)
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return proc, calls
    return run


def test_run_web_default_is_one_http_process(run_web):
    proc, calls = run_web()
    assert proc.returncode == 0, proc.stderr
    assert calls == ["--host 0.0.0.0 --port 8880"]


def test_run_web_single_with_tls_configured_still_starts_one_process(run_web):
    proc, calls = run_web(tls=True)
    assert proc.returncode == 0, proc.stderr
    assert calls == ["--host 0.0.0.0 --port 8880"]
    assert "HTTPS → not started (--single)" in proc.stderr


def test_run_web_https_is_one_tls_process(run_web):
    proc, calls = run_web("--https", tls=True)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 1 and "--port 8881 --ssl-certfile" in calls[0]


def test_run_web_https_without_certs_says_why(run_web):
    proc, calls = run_web("--https")
    assert proc.returncode == 1 and calls == []
    assert "ADVISOR_SSL_CERTFILE" in proc.stderr


def test_run_web_both_is_explicit_and_warns(run_web):
    proc, calls = run_web("--both", tls=True)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2
    assert "--host 0.0.0.0 --port 8880" in calls
    assert any(c.startswith("--host 0.0.0.0 --port 8881 --ssl-certfile ") for c in calls)
    assert "do not share running jobs" in proc.stderr


def test_run_web_rejects_unknown_options(run_web):
    proc, calls = run_web("--bogus")
    assert proc.returncode == 2 and calls == []
