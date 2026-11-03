"""
Externalized table of Egeria Technology Types and their known native
governance action processes/types — loaded from
config/technology_type_processes.yaml, so adding support for a new
Technology Type or a newly-authored native survey/catalog process is a
config change, not a code change here.

See that file's header comment for the schema and the meaning of `kind`.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "configdata" / "technology_type_processes.yaml"

KIND_SURVEY_EXISTING = "survey_existing"
KIND_CATALOG_AND_SURVEY = "catalog_and_survey"
KIND_DELETE = "delete"


@dataclass(frozen=True)
class NativeProcess:
    qualified_name: str
    display_name: str
    kind: str
    description: str = ""
    #: Name of the action target the survey service expects its subject under
    #: (Egeria's `supportedActionTarget` spec for that GovernanceActionType).
    #: "serverToSurvey" for PostgreSQL; the folder survey names it "fileToSurvey".
    action_target_name: str = "serverToSurvey"
    #: What the action target IS. "asset" (the default) is the resource's own
    #: Egeria asset (`egeria_asset_guid`); "server" is the database SERVER
    #: element the resource's server pointer holds. Only a kind with a
    #: separate server element (see `server_has_separate_element`) has any.
    target: str = "asset"
    #: Whether Egeria's engine needs this database's credentials to run it (the projected `.omsecrets`
    #: collection). False for a process RE runs as an engine host from rows it already stores
    #: (`ResourceExplorerSurvey::re-db-derived`): no connection is opened, so no credential is checked.
    needs_credentials: bool = True


@dataclass(frozen=True)
class TypeWiring:
    """What the config says about how a technology type is registered in Egeria.

    `separate_server` is a three-valued answer on purpose: True (the kind has a
    server element distinct from the database element -- PostgreSQL), False
    (the kind's server and database coincide, e.g. a single-file or embedded
    database -- the single-step variant, NOT built yet) and None (the config
    says nothing: this kind is not wired)."""
    separate_server: bool | None = None
    server_technology_type: str = ""


@functools.lru_cache(maxsize=1)
def _load_wiring(config_path: Path = _DEFAULT_CONFIG_PATH) -> dict[tuple[str, str], TypeWiring]:
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}
    out: dict[tuple[str, str], TypeWiring] = {}
    for tt in raw.get("technology_types") or []:
        sep = tt.get("separate_server_element")
        out[(tt.get("entity_type", ""), tt.get("name", ""))] = TypeWiring(
            separate_server=sep if isinstance(sep, bool) else None,
            server_technology_type=tt.get("server_technology_type") or "")
    return out


def get_wiring(entity_type: str, technology_type: str) -> TypeWiring:
    return _load_wiring().get((entity_type, technology_type), TypeWiring())


def server_has_separate_element(entity_type: str, technology_type: str) -> bool | None:
    """Does this kind of resource have a server element in Egeria distinct from
    its database element? True: PostgreSQL. False: the two coincide (not built:
    see the implemented note's named follow-up). None: the config does not say,
    so the kind is NOT WIRED and callers must say so, never guess."""
    return get_wiring(entity_type, technology_type).separate_server


@functools.lru_cache(maxsize=1)
def _load(config_path: Path = _DEFAULT_CONFIG_PATH) -> dict[tuple[str, str], list[NativeProcess]]:
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    result: dict[tuple[str, str], list[NativeProcess]] = {}
    for tt in raw.get("technology_types") or []:
        key = (tt.get("entity_type", ""), tt.get("name", ""))
        result[key] = [
            NativeProcess(
                qualified_name=p["qualified_name"],
                display_name=p.get("display_name", p["qualified_name"]),
                kind=p.get("kind", "unknown"),
                description=(p.get("description") or "").strip(),
                action_target_name=p.get("action_target_name") or "serverToSurvey",
                target=p.get("target") or "asset",
                needs_credentials=p.get("needs_credentials", True) is not False,
            )
            for p in tt.get("processes") or []
        ]
    return result


def get_native_processes(entity_type: str, technology_type: str) -> list[NativeProcess]:
    """All known native processes for this (entity_type, technology_type) pair
    (Egeria's real Technology Type display name — see the config file's
    header), or [] if none are configured yet."""
    return _load().get((entity_type, technology_type), [])


def get_process_by_kind(entity_type: str, technology_type: str, kind: str) -> NativeProcess | None:
    """First configured process of a given kind for this (entity_type,
    technology_type) pair, or None if not configured."""
    for p in get_native_processes(entity_type, technology_type):
        if p.kind == kind:
            return p
    return None


def kind_of(qualified_name: str) -> str | None:
    """The configured kind of a process by its qualified name, whichever technology type
    lists it; None when no type lists it."""
    for procs in _load().values():
        for p in procs:
            if p.qualified_name == qualified_name:
                return p.kind
    return None


def clear_cache() -> None:
    """Testing hook — the loader result is cached via lru_cache."""
    _load.cache_clear()
    _load_wiring.cache_clear()
