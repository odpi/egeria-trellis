"""Zone-restricted report specs, per signed-in user, and the "Used by" consumer index (brief B6).

The shared EGERIA tier (advisor/egeria_spec_sync.py) holds public specs only: pyegeria's shared-cache
sync skips any spec that carries a governance zone (§9). A restricted spec is therefore read here,
**as the signed-in user's token**, with pyegeria's `list_visible_restricted_specs`; Egeria's zone check
decides what comes back, so each user sees exactly the restricted specs their zones allow.

Cache rule: the result is cached per **credential identity** (a hash of the token presented, plus the
platform and view server: `egeria_dashboards.credential_identity`), never by bare user id. Two sessions
of one user id can hold different zone grants; a cache keyed on the id would leak one into the other.
Nothing read here is ever written to the shared cache or registered in the shared registry.

Running one: `run_restricted_spec` fetches the spec from Egeria for that call, as the user
(`exec_report_spec(..., refresh_spec=True)`), never from a shared copy.

Absence is stated: no sign-in, a pyegeria without the function, folders not read, and "read, and
nothing restricted is visible to you" are four different answers, never one empty list.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

CACHE_TTL_S = 120.0
_CACHE_MAX = 256
_lock = threading.Lock()
_cache: Dict[str, Any] = {}

RESTRICTED_NEEDS = ("Restricted report specs need pyegeria 6.2.35 or later "
                    "(list_visible_restricted_specs); this EA has an older pyegeria.")
SIGN_IN_FOR_RESTRICTED = "Sign in to see restricted report specs: they are read from Egeria as you."


def _restricted_reader() -> Optional[Callable[..., Dict[str, Any]]]:
    try:
        from pyegeria.view.egeria_spec_cache import list_visible_restricted_specs
        return list_visible_restricted_specs
    except ImportError:
        return None


def _cache_get(key: str, refresh: bool) -> Any:
    if refresh:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
            return hit[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX // 4]:
                _cache.pop(k, None)
        _cache[key] = (time.monotonic(), value)


def invalidate() -> None:
    """Drop every user's cached listing (after a publish, a withdraw or a sync)."""
    with _lock:
        _cache.clear()


def restricted_specs(creds: Optional[Dict[str, str]], *, refresh: bool = False,
                     client_factory: Optional[Callable[[Dict[str, str]], Any]] = None) -> Dict[str, Any]:
    """The restricted specs this user can see in EA's folders.

    {"state": ok | none_visible | not_signed_in | unsupported | read_failed | partial,
     "specs": [{"label", "zones", "folders", "qualified_name", "guid", "version"}],
     "message": str | None, "viewer": user id | None, "cached": bool, "folders": [...]}

    Never raises for an Egeria failure: it is a state with words. Raises egeria_dashboards.TokenRefused
    when Egeria refuses the user's token (the route turns that into "sign in again")."""
    from advisor import egeria_dashboards as ed
    from advisor.egeria_spec_sync import configured_folders
    try:
        conn = ed._connection(creds)
    except ed.SignInRequired:
        return {"state": "not_signed_in", "specs": [], "message": SIGN_IN_FOR_RESTRICTED, "viewer": None,
                "cached": False, "folders": []}
    except ConnectionError as exc:
        return {"state": "read_failed", "specs": [], "message": str(exc), "viewer": conn_user(creds),
                "cached": False, "folders": []}
    reader = _restricted_reader()
    viewer = conn["user_id"]
    if reader is None:
        return {"state": "unsupported", "specs": [], "message": RESTRICTED_NEEDS, "viewer": viewer,
                "cached": False, "folders": []}
    folders = configured_folders()
    key = f"restricted|{ed.credential_identity(conn)}|{','.join(folders)}"
    cached = _cache_get(key, refresh)
    if cached is not None:
        return {**cached, "cached": True}
    try:
        client = (client_factory or ed._build_client)(conn)
        got = reader(client, folders=folders)
    except Exception as exc:  # noqa: BLE001 -- said in words below; never cached
        if ed._is_token_refused(exc):
            raise ed.TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
        logger.warning(f"restricted specs: reading as {viewer!r} failed: {exc}")
        return {"state": "read_failed", "specs": [], "viewer": viewer, "cached": False, "folders": [],
                "message": f"Could not read restricted report specs from Egeria as {viewer}: {ed._words(exc)}"}
    status = got.get("status")
    specs: List[Dict[str, Any]] = list(got.get("specs") or [])
    if status == "not read":
        state, message = "read_failed", f"Restricted report specs were not read: {got.get('reason')}"
    elif status == "partial":
        state, message = "partial", f"Restricted report specs were partly read: {got.get('reason')}"
    elif not specs:
        state = "none_visible"
        message = got.get("reason") or f"No restricted report specs are visible to {viewer}."
    else:
        state, message = "ok", None
    out = {"state": state, "specs": specs, "message": message, "viewer": viewer,
           "folders": got.get("folders") or [], "notes": got.get("notes") or []}
    if state != "read_failed":
        _cache_put(key, out)
    return {**out, "cached": False}


def conn_user(creds: Optional[Dict[str, str]]) -> Optional[str]:
    return (creds or {}).get("user_id") or None


def _consumer_names(raw: Any) -> Dict[str, str]:
    """{qualifiedName: display name} for the consumer folders the index lists.

    pyegeria's index lists each consumer as a dict, `{"name": "Egeria Advisor", "qualifiedName":
    "ReportSpecFolder::Consumer::EgeriaAdvisor", ...}`, and keys `index` by that display NAME. That is
    the live shape on laz, captured in tests/unit/fixtures/egeria_live/consumer_index.json. A bare
    string, as an older writer might produce, is taken as a name that is also its own key."""
    out: Dict[str, str] = {}
    for c in raw or []:
        if isinstance(c, dict):
            name = c.get("name") or c.get("qualifiedName")
            if name:
                out[c.get("qualifiedName") or name] = name
        elif isinstance(c, str) and c:
            out[c] = c
    return out


def used_by_index() -> Dict[str, Any]:
    """The "Used by" facet: {"state": ok | partial | not_read | unsupported, "index": {label: [consumer]},
    "consumers": [names], "folders": {qualifiedName: name}, "message"}. From the consumer index the last
    sync wrote (pyegeria `read_consumer_index`, offline). "Not read" is never shown as "used by none".

    Every consumer is named by its folder's DISPLAY name ("Egeria Advisor"), the form pyegeria keys
    `index` by. `folders` maps a folder's qualified name to that name, so a restricted spec, which is
    listed by folder qualified name, can be matched against the same facet (`with_used_by`)."""
    try:
        from pyegeria.view.egeria_spec_cache import read_consumer_index
    except ImportError:
        return {"state": "unsupported", "index": {}, "consumers": [], "folders": {},
                "message": "\"Used by\" needs pyegeria 6.2.33 or later (the consumer index)."}
    try:
        data = read_consumer_index()
    except Exception as exc:  # noqa: BLE001 -- stated
        return {"state": "not_read", "index": {}, "consumers": [], "folders": {},
                "message": f"\"Used by\" is unknown: the consumer index could not be read: {exc}"}
    status = data.get("status") or "read"
    index = {k: list(v) for k, v in (data.get("index") or {}).items()}
    folders = _consumer_names(data.get("consumers"))
    consumers = sorted({c for v in index.values() for c in v} | set(folders.values()))
    if status == "not read":
        return {"state": "not_read", "index": {}, "consumers": consumers, "folders": folders,
                "message": f"\"Used by\" is unknown: {data.get('reason') or 'the consumer index was not read'}."}
    if status == "partial":
        return {"state": "partial", "index": index, "consumers": consumers, "folders": folders,
                "message": f"\"Used by\" is incomplete: {data.get('reason')}."}
    return {"state": "ok", "index": index, "consumers": consumers, "folders": folders, "message": None}


def consumer_display(folder_qn: str, names: Dict[str, str]) -> str:
    """The consumer a folder belongs to, by display name.

    - A consumer folder: its own name.
    - A sub-folder (`ReportSpecFolder::Consumer::EgeriaAdvisor::Finance`): the name of the consumer whose
      qualified name is its longest `::` prefix. Consumer folders are walked recursively, so a spec
      there is used by that consumer.
    - Anything else keeps its qualified name: shown, and never matched by a facet choice."""
    if folder_qn in names:
        return names[folder_qn]
    best = ""
    for qn in names:
        if folder_qn.startswith(qn + "::") and len(qn) > len(best):
            best = qn
    return names[best] if best else folder_qn


def with_used_by(restricted: Dict[str, Any], used_by: Dict[str, Any]) -> Dict[str, Any]:
    """Each restricted spec gains `used_by`: its folders as the facet names them (consumer display
    names, `consumer_display`)."""
    names = (used_by or {}).get("folders") or {}
    specs = [{**s, "used_by": sorted({consumer_display(f, names) for f in (s.get("folders") or [])})}
             for s in (restricted or {}).get("specs") or []]
    return {**(restricted or {}), "specs": specs}


def run_restricted_spec(creds: Optional[Dict[str, str]], label: str, *, output_format: str = "TABLE",
                        params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a restricted spec as the user: fetched from Egeria for this call (`refresh_spec=True`),
    never from a shared copy. {"state": ok | refused | failed, "result", "message"}."""
    from advisor import egeria_dashboards as ed
    try:
        conn = ed._connection(creds)
    except ed.SignInRequired:
        return {"state": "refused", "result": None, "message": SIGN_IN_FOR_RESTRICTED}
    import inspect

    from pyegeria import exec_report_spec
    if "refresh_spec" not in inspect.signature(exec_report_spec).parameters:
        return {"state": "refused", "result": None,
                "message": "Running a restricted spec needs pyegeria 6.2.34 or later (refresh_spec)."}
    try:
        result = exec_report_spec(label, output_format=output_format, params=params or {},
                                  view_server=conn["view_server"], view_url=conn["platform_url"],
                                  user=conn["user_id"], user_pass="", token=conn["token"], refresh_spec=True)
    except Exception as exc:  # noqa: BLE001 -- stated
        if ed._is_token_refused(exc):
            raise ed.TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
        return {"state": "failed", "result": None, "message": f"'{label}' did not run: {ed._words(exc)}"}
    return {"state": "ok", "result": result, "message": None}


def _reset_for_tests() -> None:
    invalidate()
