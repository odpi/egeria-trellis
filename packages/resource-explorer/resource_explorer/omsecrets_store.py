"""RE's direct read/write access to the `.omsecrets` YAML file.

Background — docs/design-notes/exchange/REPLY-DATABASE-CREDENTIAL-CAPABILITY-
VISIBILITY.md §7: RE writes a credential to two places in the same
operation — its own registry (`registry.py`'s `databases.db_password`,
encrypted at rest via `credential_crypto.py`) and this file, which is the
"projection for the engine host". Egeria's own YAML secrets-store connector
(`YAMLSecretsFileConnector`) is what actually READS this file at survey
time; RE never asks Egeria to read a secret back (pyegeria exposes no such
API — see credential_crypto.py's docstring), it only keeps its own copy of
what it wrote in sync with what the engine host sees.

File shape — confirmed live against the real file in this dev environment
(`egeria-workspaces-fs/runtime-volumes/quickstart-platform-data/secrets/
resource-explorer.omsecrets`, hand-edited earlier this session), not
guessed from the design doc's paraphrase:

    ---
    secretsCollections:
      <collection name>:
        displayName: "<collection name>"
        refreshTimeInterval: 60
        secrets:
          userId: "<user>"
          clearPassword: "<password>"

Path configuration
-------------------
`config.egeria.secrets_store_local_path` (`EGERIA_SECRETS_STORE_LOCAL_PATH`)
is the host-visible path to this file — see that field's docstring in
config.py for why it is a SEPARATE setting from `secrets_store_path_name`
(that one is the engine-host-container path RE hands to Egeria, and is not
a path RE itself, running as a bare host process, can open). Empty by
default. Every function here degrades to a silent no-op when it is unset,
since most deployments (CI, a from-scratch checkout, a remote engine host)
have no host-visible path to this file at all — this must never be an
error, only "there is nothing to project to."
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def secrets_collection_name(db_slug: str) -> str:
    """The collection-name convention for a database's credential set —
    confirmed live this session: `"{slug}::PostgreSQL Secret"`. `db_slug` is
    already the full registry slug (e.g. `localhost_docker_coco_pharma`);
    nothing needs reconstructing from `server_slug`/`database_name`, since
    that's what the slug already encodes.

    Kept as the single definition RE uses; `surveyors/database/
    egeria_database_surveyor.py::_secrets_collection_name` predates this
    module and computes the same string independently for the native-survey
    path — not unified here to stay inside this change's stated scope
    (credential storage: registry.py/web/routes/databases.py/cli/main.py),
    but the two must be kept in sync by hand if the convention ever changes.
    """
    return f"{db_slug}::PostgreSQL Secret"


def local_path() -> str:
    """The configured host-visible `.omsecrets` path, or `""` if unset."""
    from resource_explorer.config import get_config

    return (get_config().egeria.secrets_store_local_path or "").strip()


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"secretsCollections": {}}
    try:
        with p.open("r") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        # Class and line only: a YAML error's text can carry a snippet of the offending line, which in
        # this file may be a password.
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "an unknown line"
        log.warning("omsecrets_store: could not read %s: %s at %s", path, type(exc).__name__, where)
        return {"secretsCollections": {}}
    if "secretsCollections" not in data or data["secretsCollections"] is None:
        data["secretsCollections"] = {}
    return data


def _save(path: str, data: dict) -> None:
    """Write the file atomically: a temp file in the same directory, then a
    rename, so Egeria's per-action read never sees a half-written file. The
    mode matches what the previous in-place writer produced: an existing
    file keeps its mode, a new one gets the umask default (this writer never
    set restrictive permissions; the file is bind-mounted into the engine
    container, whose user must still be able to read it)."""
    import tempfile

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = p.stat().st_mode & 0o7777
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("---\n")
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_credential(
    collection_name: str,
    user_id: str,
    clear_password: str,
    *,
    path: str | None = None,
    refresh_time_interval: int = 60,
) -> bool:
    """Write/update one secrets collection entry. No-op (returns False) when
    no local path is configured — this is the expected, common case, never
    logged as an error. Returns True when the file was actually written."""
    target = path if path is not None else local_path()
    if not target:
        return False
    data = _load(target)
    data["secretsCollections"][collection_name] = {
        "displayName": collection_name,
        "refreshTimeInterval": refresh_time_interval,
        "secrets": {
            "userId": user_id,
            "clearPassword": clear_password,
        },
    }
    _save(target, data)
    return True


def collection_names(path: str | None = None) -> set[str]:
    """Every collection name currently present in the `.omsecrets` file, or
    an empty set when no local path is configured or the file doesn't exist
    yet — both are "nothing to compare against," not an error."""
    target = path if path is not None else local_path()
    if not target or not os.path.exists(target):
        return set()
    data = _load(target)
    return set(data.get("secretsCollections", {}).keys())


def has_collection(collection_name: str, path: str | None = None) -> bool:
    return collection_name in collection_names(path=path)
