"""
TR-45: one default Prefect work pool everywhere, resource-explorer-pool.

config.py and scripts/prefect_up.sh defaulted to default-agent-pool while the bundled workers (the container
entrypoint and scripts/launchd/re-prefect-worker.sh) and bootstrap's liveness check used resource-explorer-pool.
On all defaults, RE dispatched to a pool no worker served, so every dispatched step ran in-process. This test keeps
the five places in step. The "worker live on another pool" wording is covered by
test_bootstrap_prefect_pool_liveness.py::test_a_live_re_worker_on_another_pool_is_named_once_at_warning.
"""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
POOL = "resource-explorer-pool"


def _default_of(text, pattern):
    m = re.search(pattern, text, re.M)
    assert m, pattern
    return m.group(1)


def test_config_default_is_the_bundled_pool():
    from resource_explorer.config import PrefectConfig
    assert PrefectConfig.model_fields["work_pool"].default == POOL


def test_every_default_names_the_same_pool():
    found = {
        "config.py": _default_of((PKG / "resource_explorer/config.py").read_text(),
                                 r'work_pool: str = Field\(default="([^"]+)", alias="PREFECT_WORK_POOL"\)'),
        "prefect_up.sh": _default_of((PKG / "scripts/prefect_up.sh").read_text(),
                                     r'^WORK_POOL="\$\{WORK_POOL:-([^}]+)\}"'),
        ".env.example": _default_of((PKG / ".env.example").read_text(), r'^PREFECT_WORK_POOL=(\S+)'),
        "re-prefect-worker.sh": _default_of((PKG / "scripts/launchd/re-prefect-worker.sh").read_text(),
                                            r'^POOL="\$\{PREFECT_WORK_POOL:-([^}]+)\}"'),
        "entrypoint-resource-explorer.sh": _default_of(
            (REPO / "docker/entrypoint-resource-explorer.sh").read_text(),
            r'local pool="\$\{PREFECT_WORK_POOL:-([^}]+)\}"'),
    }
    assert set(found.values()) == {POOL}, found
