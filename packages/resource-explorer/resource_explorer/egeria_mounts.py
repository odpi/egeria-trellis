"""What Egeria's platform container can read: the prefix map (Files step 2, fix round 1).

One deployment setting holds host-prefix -> container-prefix pairs (`EgeriaConfig.mount_map`, no DDL). At start the map
is topped up from two read-only sources for any host prefix the configuration did not set, in this order:

    1. configured                                       the setting itself (always wins)
    2. read from quickstart-egeria-main mounts          `docker inspect` of the platform container, when docker is
                                                        reachable and the container is local (what is actually running)
    3. read from the quickstart compose file            the `egeria-main` service's `volumes:` (host side relative to
                                                        the compose file's directory)

With none of them the map is EMPTY and every file system says "Egeria's mounts unknown on this deployment"; nothing is
guessed. Each pair keeps its source and the date it was read, so Admin can show them and a recorded probe row can name
the prefix it relied on.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

PLATFORM_CONTAINER = "quickstart-egeria-main"
COMPOSE_SERVICE = "egeria-main"
DEFAULT_COMPOSE_FILE = "egeria-workspaces-fs/compose-configs/egeria-quickstart/egeria-quickstart.yaml"

SOURCE_CONFIGURED = "configured"
SOURCE_DOCKER = f"read from {PLATFORM_CONTAINER} mounts"
SOURCE_COMPOSE = "read from the quickstart compose file"


class DockerUnavailable(RuntimeError):
    """Docker could not tell us the container's mounts (not installed, not running, container not local, bad output)."""


@dataclass(frozen=True)
class MountPair:
    host_prefix: str
    container_prefix: str
    source: str
    read_on: str          # YYYY-MM-DD

    def as_dict(self) -> dict:
        return {"host_prefix": self.host_prefix, "container_prefix": self.container_prefix, "source": self.source,
                "read_on": self.read_on, "label": f"{self.source} · {self.read_on}"}


def _norm(path: str) -> str:
    path = (path or "").strip()
    return path.rstrip("/") if len(path) > 1 else path


def is_under(path: str, prefix: str) -> bool:
    """`path` is `prefix` or inside it (whole path components, so /a/bc is not under /a/b)."""
    path, prefix = _norm(path), _norm(prefix)
    if not path or not prefix:
        return False
    return path == prefix or path.startswith(prefix + "/")


def parse_configured(text: str) -> list[tuple[str, str]]:
    """The setting's pairs. A JSON object, or `host=container` entries separated by commas. Never raises: an entry that
    cannot be read is skipped with one warning."""
    text = (text or "").strip()
    if not text:
        return []
    pairs: list[tuple[str, str]] = []
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            log.warning("EGERIA_MOUNT_MAP: could not read %r as JSON; treating it as empty", text)
            return []
        if not isinstance(obj, dict):
            return []
        pairs = [(str(k), str(v)) for k, v in obj.items()]
    else:
        for item in text.split(","):
            if "=" not in item:
                if item.strip():
                    log.warning("EGERIA_MOUNT_MAP: entry %r has no '='; skipped", item)
                continue
            host, container = item.split("=", 1)
            pairs.append((host, container))
    return [(_norm(h), _norm(c)) for h, c in pairs if _norm(h) and _norm(c)]


def docker_inspect_mounts(container: str = PLATFORM_CONTAINER, *, timeout: float = 5.0) -> list[dict]:
    """`docker inspect` of the platform container, its mounts as a list of {Type, Source, Destination, ...}. Raises
    `DockerUnavailable` for anything that is not a clean answer."""
    try:
        done = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DockerUnavailable(f"docker could not be run: {exc}") from exc
    if done.returncode != 0:
        raise DockerUnavailable((done.stderr or "docker inspect failed").strip()[:200])
    try:
        mounts = json.loads(done.stdout)
    except ValueError as exc:
        raise DockerUnavailable("docker inspect gave output that is not JSON") from exc
    if not isinstance(mounts, list):
        raise DockerUnavailable("docker inspect gave no mounts list")
    return mounts


def find_compose_file(setting: str, *, start: Path | None = None) -> Path | None:
    """The compose file: an absolute setting as is, else the relative setting looked for in each ancestor directory of
    this package (a trellis checkout sits beside egeria-workspaces-fs). None when it is not found."""
    setting = (setting or "").strip() or DEFAULT_COMPOSE_FILE
    p = Path(setting).expanduser()
    if p.is_absolute():
        return p if p.is_file() else None
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        cand = parent / setting
        if cand.is_file():
            return cand
    return None


def compose_volumes(compose_file: Path, service: str = COMPOSE_SERVICE) -> list[tuple[str, str]]:
    """(host path, container path) for each bind volume of `service`, the host side resolved against the compose file's
    own directory. Named volumes (no path separator on the host side) are skipped. Raises ValueError on a file that
    cannot be read as a compose file with that service."""
    import yaml

    data = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
    svc = ((data.get("services") or {}).get(service))
    if not isinstance(svc, dict):
        raise ValueError(f"no service {service!r} in {compose_file}")
    out: list[tuple[str, str]] = []
    base = compose_file.resolve().parent
    for vol in svc.get("volumes") or []:
        if isinstance(vol, dict):
            host, container = str(vol.get("source") or ""), str(vol.get("target") or "")
        else:
            parts = str(vol).split(":")
            if len(parts) < 2:
                continue
            host, container = parts[0], parts[1]
        if not host or not container.startswith("/") or not (host.startswith((".", "/", "~"))):
            continue          # a named volume, not a path
        host_path = Path(host).expanduser()
        resolved = host_path if host_path.is_absolute() else (base / host_path)
        out.append((_norm(_lexical(resolved)), _norm(container)))
    return out


def _lexical(path: Path) -> str:
    """Collapse `..` and `.` without touching the file system (the host path need not exist where RE runs)."""
    import os

    return os.path.normpath(str(path))


def build_mount_map(*, configured: str = "", compose_setting: str = "",
                    inspect: Callable[..., list[dict]] | None = None,
                    today: str | None = None) -> list[MountPair]:
    """Assemble the map: configured pairs, then docker inspect, then the compose file, each only for a host prefix no
    earlier source set. Never raises for a source that cannot be read (that source contributes nothing)."""
    day = today or datetime.now(timezone.utc).date().isoformat()
    pairs: dict[str, MountPair] = {}

    def add(host: str, container: str, source: str) -> None:
        if host not in pairs:
            pairs[host] = MountPair(host, container, source, day)

    for host, container in parse_configured(configured):
        add(host, container, SOURCE_CONFIGURED)
    try:
        for m in (inspect or docker_inspect_mounts)(PLATFORM_CONTAINER):
            src, dst = _norm(str(m.get("Source") or "")), _norm(str(m.get("Destination") or ""))
            if src and dst:
                add(src, dst, SOURCE_DOCKER)
    except DockerUnavailable as exc:
        log.info("mount map: docker gave no mounts for %s (%s)", PLATFORM_CONTAINER, exc)
    try:
        compose = find_compose_file(compose_setting)
        if compose is not None:
            for host, container in compose_volumes(compose):
                add(host, container, SOURCE_COMPOSE)
    except (OSError, ValueError, ImportError) as exc:
        log.info("mount map: the quickstart compose file gave no volumes (%s)", exc)
    return list(pairs.values())


_MAP: list[MountPair] | None = None


def load_at_start() -> list[MountPair]:
    """Build the process's map from the configuration and the two read-only sources. Called at web start; also the
    lazy first-use path, so a process that never ran the start hook still has a map."""
    global _MAP
    from resource_explorer.config import get_config

    cfg = get_config().egeria
    _MAP = build_mount_map(configured=cfg.mount_map, compose_setting=cfg.quickstart_compose_file)
    log.info("Egeria mount map: %d pair(s) (%s)", len(_MAP),
             ", ".join(sorted({p.source for p in _MAP})) or "empty: Egeria's mounts unknown on this deployment")
    return _MAP


def get_mount_map() -> list[MountPair]:
    return _MAP if _MAP is not None else load_at_start()


def reset_for_tests() -> None:
    global _MAP
    _MAP = None


def prefix_for(path: str, pairs: list[MountPair]) -> MountPair | None:
    """The pair whose container prefix holds `path` (the longest prefix wins)."""
    hits = [p for p in pairs if is_under(path, p.container_prefix)]
    return max(hits, key=lambda p: len(p.container_prefix)) if hits else None
