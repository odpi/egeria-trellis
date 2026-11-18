"""Dashboard Sheets held in Egeria, read-only (brief EA-CATCHUP-AND-DASHBOARDS B5, decisions D4 and D6).

EA lists the Dashboard Sheets Egeria holds and shows one, AS THE SIGNED-IN USER. The data step is
pyegeria's (`pyegeria.view.dashboard_resolver`, the same step the Portal uses, D4); the display, the cache
and the sign-in are EA's own.

Visibility (§4.7 of the brief), stated here because the cache is where it would leak:
  - Every Egeria read here is made with the viewer's own bearer token (`apply_token`). There is no
    service-user fallback: with no token there is nothing to read as, so the caller is told to sign in.
    Egeria's zone check on that read decides what the viewer sees: a sheet in a zone they cannot see is
    not listed and reads as "not visible"; a Report or an EGERIA-tier ReportSpec in such a zone comes back
    from the resolver as a "restricted" placement ("not visible to you"), never runnable.
  - The cache is keyed by CREDENTIAL IDENTITY: a hash of the token actually presented, plus the platform
    and view server (the Portal's `credential_identity` rule, 11-09). Never a bare user_id and never a
    token's unverified `sub`: a cache hit makes no Egeria call, so a claim-based key would let one user
    read another's entry. A renewed token is a new key. Nothing secret is stored.
  - Only answers are cached ("ok", "no sheets", "not visible"). A failed read is never cached, and a
    refused token is raised to the caller as a sign-in problem, never cached as an answer.
  - The EGERIA spec tier is filled by EA's service user into a shared cache (B3), so a spec found there
    may be one this viewer cannot see. The resolver re-reads such a spec by GUID as the viewer
    (`check_spec_visibility=True`): Egeria's own check is the authority over the shared tier.

Absence is not zero: "no sheets" (Egeria answered and holds none this viewer can see), "could not read"
(Egeria did not answer), and "not visible" (no sheet of that name for this viewer) are three answers with
three messages.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

#: How long a viewer's listing or resolved sheet is reused before Egeria is read again.
CACHE_TTL_S = 120.0
_CACHE_MAX = 512

#: The reason a restricted placement gives; the same words as pyegeria and the Portal.
NOT_VISIBLE = "not visible to you"

_cache: Dict[str, Tuple[float, Any]] = {}
_lock = threading.Lock()


class SignInRequired(Exception):
    """No usable credential of the viewer's own: dashboards are read only as the signed-in user."""


class TokenRefused(Exception):
    """Egeria refused the viewer's token (expired or invalid): they must sign in again."""


# ── identity and clients ──────────────────────────────────────────────────────

def _connection(creds: Optional[Dict[str, str]]) -> Dict[str, str]:
    """The viewer's connection: EA's platform/server, the viewer's user id and token. Raises
    SignInRequired when there is no token: a password-backed (service) connection is never used here."""
    if not creds or not creds.get("token") or not creds.get("user_id"):
        raise SignInRequired("Sign in to see dashboards: they are read from Egeria as you.")
    from advisor.report_pipeline import get_report_pipeline
    conn = get_report_pipeline()._read_pyegeria_connection(egeria_credentials=creds)
    if not (conn.get("view_server") and conn.get("platform_url")):
        raise ConnectionError("Egeria connection is not configured (config/mcp_servers.json -> pyegeria.env)")
    conn["user_pwd"] = ""          # never a password: the token is the only credential used
    return conn


def credential_identity(conn: Dict[str, str]) -> str:
    """Who a cache entry belongs to: a hash of the token PRESENTED, plus platform and server."""
    digest = hashlib.sha256(conn["token"].encode()).hexdigest()
    return f"t:{digest}|{conn['platform_url']}|{conn['view_server']}"


def _build_client(conn: Dict[str, str]) -> Any:
    from pyegeria import EgeriaTech
    from advisor.auth import apply_token
    client = EgeriaTech(conn["view_server"], conn["platform_url"], conn["user_id"], "")
    apply_token(client, conn["token"])
    return client


def _is_token_refused(exc: BaseException) -> bool:
    """A 401 is the viewer's token, not an element: it must reach the UI as "sign in again". A 403 for an
    element in a hidden zone is the resolver's business ("restricted"), not this.

    By exception class and HTTP code along the cause chain (TR-22, `trellis_auth.egeria_errors`), never by text: the
    old fallback read "401" plus "token"/"unauthor" anywhere in a message. pyegeria 6.2.34+ raises a hidden-zone
    403-020 as PyegeriaUnauthorizedException with related_http_code 403, so it is told apart by its code."""
    from trellis_auth.egeria_errors import SIGN_IN_AGAIN, auth_error_status

    return auth_error_status(exc) == SIGN_IN_AGAIN


def _words(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"


# ── the cache, per credential identity ────────────────────────────────────────

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
    """Drop every cached listing and sheet (e.g. after the EGERIA spec tier is re-synced)."""
    with _lock:
        _cache.clear()


# ── source badges ─────────────────────────────────────────────────────────────

def _badge_source(spec_source: Optional[Dict[str, Any]], spec: Optional[str]) -> Optional[str]:
    """egeria / local / builtin from the resolver's spec_source, with EA's own registered specs shown as
    "user", consistent with the report catalog (B3). None when the spec was not found."""
    if not spec_source:
        return None
    tier = spec_source.get("tier")
    if tier == "local":
        from advisor.report_spec_parser import ea_registered_specs
        if spec in ea_registered_specs() or spec_source.get("label") in ea_registered_specs():
            return "user"
    return tier


# ── list ──────────────────────────────────────────────────────────────────────

def _sheet_summary(sheet: Any) -> Dict[str, Any]:
    placements = list(getattr(sheet, "placements", None) or [])
    return {
        "name": sheet.name,
        "heading": getattr(sheet, "heading", None) or sheet.name,
        "description": getattr(sheet, "description", None) or "",
        "family": getattr(sheet, "family", None),
        "placement_count": len(placements),
        "perspectives": sorted({x for p in placements for x in (getattr(p, "perspectives", None) or [])}),
        "breaks": list(getattr(sheet, "breaks", None) or []),
    }


def _listing_notes(sheets: Any) -> Tuple[List[str], List[Dict[str, Any]]]:
    """pyegeria's DashboardSheetListing says how it read the configured dashboard folders: `notes` (a folder
    not found or not visible to the viewer, a sub-folder not read, no folders configured) and `folders`
    (qualifiedName, status read / partial / not found / not read, reason, sheets). Kept, never dropped:
    without them a folder EA cannot see reads as "no sheets"."""
    notes = [str(n) for n in (getattr(sheets, "notes", None) or []) if str(n).strip()]
    folders = [{"qualifiedName": f.get("qualifiedName"), "status": f.get("status"),
                "reason": f.get("reason"), "sheets": f.get("sheets")}
               for f in (getattr(sheets, "folders", None) or []) if isinstance(f, dict)]
    return notes, folders


def list_sheets(creds: Optional[Dict[str, str]], *, refresh: bool = False,
                client_factory: Optional[Callable[[Dict[str, str]], Any]] = None) -> Dict[str, Any]:
    """The sheets Egeria holds that this viewer can see.

    {"state": "ok" | "no_sheets" | "read_failed", "sheets": [...], "message": str | None, "viewer": id,
     "notes": [...], "folders": [...]}. With no sheets, a configured folder that was not found or not read
    makes the message that folder's reason, not "no sheets"; with sheets, any notes are the message.
    Raises SignInRequired (no token) or TokenRefused (Egeria refused it)."""
    conn = _connection(creds)
    key = f"list|{credential_identity(conn)}"
    cached = _cache_get(key, refresh)
    if cached is not None:
        return {**cached, "cached": True}
    viewer = conn["user_id"]
    try:
        from pyegeria.view.dashboard_resolver import list_dashboard_sheets_for_viewer
        client = (client_factory or _build_client)(conn)
        sheets = list_dashboard_sheets_for_viewer(client)
    except Exception as exc:  # noqa: BLE001 -- said in words below; never cached
        if _is_token_refused(exc):
            raise TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
        logger.warning(f"dashboards: listing sheets as {viewer!r} failed: {exc}")
        return {"state": "read_failed", "sheets": [], "viewer": viewer, "cached": False,
                "message": f"Could not read Dashboard Sheets from Egeria as {viewer}: {_words(exc)}"}
    notes, folders = _listing_notes(sheets)
    out: Dict[str, Any] = {"state": "ok", "sheets": [_sheet_summary(s) for s in sheets], "viewer": viewer,
                           "message": None, "notes": notes, "folders": folders}
    unread = [f for f in folders if f.get("status") != "read"]
    if not out["sheets"]:
        out["state"] = "no_sheets"
        if unread:
            # A folder that was not found or not read is not an empty one: say the folder's own reason. With
            # no notes, each unread folder's own `reason`, else its status ("not read").
            why = notes or [f"{f.get('qualifiedName') or 'a dashboard folder'}: "
                            f"{f.get('reason') or f.get('status') or 'not read'}" for f in unread]
            out["message"] = f"No Dashboard Sheets to show for {viewer}: " + "; ".join(why) + "."
        else:
            out["message"] = (f"Egeria holds no Dashboard Sheets that {viewer} can see. None may exist yet, "
                              "or every one may be in a zone you cannot see."
                              + (" (" + "; ".join(notes) + ")" if notes else ""))
    elif notes:
        out["message"] = "; ".join(notes)
    _cache_put(key, out)
    return {**out, "cached": False}


# ── show ──────────────────────────────────────────────────────────────────────

def _placement_dict(pl: Any) -> Dict[str, Any]:
    report = pl.report
    return {
        "position": pl.position,
        "ref": pl.ref,
        "kind": pl.kind,
        "title": pl.title,
        "span": pl.span,
        "emphasis": pl.emphasis,
        "perspectives": list(pl.perspectives or []),
        "content": pl.content,
        "note": pl.note,
        "restricted": bool(pl.restricted),
        "restricted_element": pl.restricted_element,
        "reason": pl.reason,
        "spec": pl.spec,
        "spec_source": dict(pl.spec_source) if pl.spec_source else None,
        "source": _badge_source(pl.spec_source, pl.spec),
        # A restricted or missing placement is never runnable, whatever else is known about it.
        "runnable": bool(pl.runnable) if pl.kind == "report" else False,
        "runnable_known": pl.runnable is not None,
        "refusal": pl.refusal,
        "error": pl.error,
        "report": None if report is None else {
            "name": report.name, "description": report.description, "output_format": report.output_format,
        },
        # B7a: a drill-down spec to run in a drawer; whether the spec is analytic (its ⚙ edits analytic
        # params, never a search string); a Route B saved query's name and cached/live mode.
        "detail_spec": getattr(pl, "detail_spec", None),
        **_spec_flags(pl),
    }


def _spec_flags(pl: Any) -> Dict[str, Any]:
    if pl.kind != "report" or pl.report is None:
        return {"analytic": False, "saved_query": None, "params": {}}
    analytic, fn = _spec_is_analytic(pl.format_set)
    params = _base_params(pl, analytic)
    return {"analytic": analytic, "saved_query": _saved_query_info(fn, params), "params": params}


def show_sheet(creds: Optional[Dict[str, str]], name: str, *, perspective: Optional[str] = None,
               refresh: bool = False, client_factory: Optional[Callable[[Dict[str, str]], Any]] = None,
               ) -> Dict[str, Any]:
    """One sheet, resolved for this viewer (no report is run).

    {"state": "ok" | "not_visible" | "read_failed", "sheet": {...} | None, "message": str | None}
    Raises SignInRequired or TokenRefused."""
    resolved, answer = _resolved(creds, name, perspective, refresh, client_factory)
    if resolved is None:
        return answer
    sheet = {
        "name": resolved.name, "heading": resolved.heading, "description": resolved.description,
        "family": resolved.family, "perspective": resolved.perspective, "breaks": list(resolved.breaks),
        "viewer": resolved.viewer, "source": resolved.source,
        "placements": [_placement_dict(p) for p in sorted(resolved.placements, key=lambda p: p.position)],
    }
    _mark_nested_sheets(creds, sheet, client_factory)
    message = None
    if not sheet["placements"]:
        message = (f"Sheet '{resolved.name}' has no placements"
                   + (f" for perspective '{perspective}'." if perspective else "."))
    return {**answer, "sheet": sheet, "message": message}


def _mark_nested_sheets(creds, sheet: Dict[str, Any], client_factory) -> None:
    """A placement whose Report is missing but whose name is another sheet this viewer can see is a NESTED
    sheet (kind "sheet"), opened in place: the Portal's rule (its by-name fallback, sheets.get(ref)). The
    viewer's own listing decides, so a sheet they cannot see stays "missing", never named."""
    missing = [p for p in sheet["placements"] if p["kind"] == "missing" and p["ref"] != sheet["name"]]
    if not missing:
        return
    try:
        listing = list_sheets(creds, client_factory=client_factory)
    except (SignInRequired, TokenRefused):
        raise
    except Exception as exc:  # noqa: BLE001 -- the placement stays "missing", with its own words
        logger.debug(f"dashboards: no listing to check nested sheets against: {exc}")
        return
    visible = {s["name"]: s for s in listing.get("sheets") or []}
    for p in missing:
        sub = visible.get(p["ref"])
        if sub is not None:
            p.update(kind="sheet", sheet_ref=sub["name"], reason=None,
                     content=None, title=p["title"] or sub["heading"],
                     note=f"nested sheet: {sub['heading']} ({sub['placement_count']} placements)")


def _resolved(creds, name, perspective, refresh, client_factory) -> Tuple[Any, Dict[str, Any]]:
    """(ResolvedSheet | None, answer). The ResolvedSheet itself is cached so a run can reuse it."""
    conn = _connection(creds)
    viewer = conn["user_id"]
    key = f"sheet|{credential_identity(conn)}|{name}|{perspective or ''}"
    cached = _cache_get(key, refresh)
    if cached is not None:
        resolved = cached
        answer = {"cached": True}
    else:
        try:
            from pyegeria.view.dashboard_resolver import resolve_dashboard_sheet
            client = (client_factory or _build_client)(conn)
            resolved = resolve_dashboard_sheet(client, name, perspective=perspective, run=False,
                                               check_spec_visibility=True)
        except Exception as exc:  # noqa: BLE001 -- said in words; never cached
            if _is_token_refused(exc):
                raise TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
            logger.warning(f"dashboards: resolving sheet {name!r} as {viewer!r} failed: {exc}")
            return None, {"state": "read_failed", "sheet": None, "viewer": viewer, "cached": False,
                          "message": f"Could not read Dashboard Sheet '{name}' from Egeria as {viewer}: "
                                     f"{_words(exc)}"}
        _cache_put(key, resolved if resolved is not None else _NOT_VISIBLE_MARK)
        answer = {"cached": False}
    if resolved is None or resolved is _NOT_VISIBLE_MARK:
        return None, {**answer, "state": "not_visible", "sheet": None, "viewer": viewer,
                      "message": (f"Egeria holds no Dashboard Sheet named '{name}' that {viewer} can see. "
                                  "It may not exist, or it may be in a zone you cannot see.")}
    return resolved, {**answer, "state": "ok", "viewer": viewer}


_NOT_VISIBLE_MARK = object()


# ── run one placement: tiles (B7a) ────────────────────────────────────────────
# A placement's report runs AS THE VIEWER and comes back as a TILE the page can draw without parsing text:
#   kpi    a single number (count_elements: an int), or a saved query's answer (its `display`, "≥N" for a
#          lower bound, and `asOf` for a cached answer)
#   kpis   a small dict of numbers ({"total": 1065, "defined": 540, "undefinedPct": 49})
#   chart  a ready-made Vega-Lite spec (SERIES / BAR / PIE): pyegeria draws it, the page embeds it
#   trend  a metric_trend series: each point keeps pyegeria's own `display` and `measurement`, so a lower
#          bound is "≥N" and is never drawn as a bare number (W2, pyegeria 6.2.34)
#   table  a TABLE placement: pyegeria's structured table as is ({"columns", "rows", "total", "total_basis",
#          "shown", "notes"}), drawn by the shared renderer (trellis-ui's vendored egeria-table.js, the same
#          one the Portal uses); no markdown round trip (T1)
#   markdown  everything else: text kinds as written, a list of records as a table (EA's own table)
#   empty / absent  ran and found nothing / not established: the sentence is the answer, never a "0"
# Absence is said in words: an `unmeasured` note (a count pyegeria could not measure) rides on any tile.

#: The output formats a placement can be shown in. Every kind pyegeria's executor returns is drawable:
#: text kinds (REPORT, MERMAID, LIST, FORM, HTML, MD) as sanitised Markdown, JSON kinds as a tile. A name
#: outside this set (a typo, or a format added later) is shown as TABLE, and the tile says so.
_DISPLAY_FORMATS = {"TABLE", "DICT", "JSON", "REPORT", "REPORT-GRAPH", "MERMAID", "LIST", "FORM", "HTML", "MD",
                    "SERIES", "BAR", "PIE"}
_CHART_FORMATS = {"SERIES", "BAR", "PIE"}

#: A tile result is FRESH this long (served without running); then STALE until RESULT_KEEP_S: served at
#: once, with its age, while one background run refreshes it (the Portal's stale-while-revalidate).
RESULT_FRESH_S = 60.0
RESULT_KEEP_S = 15 * 60.0
#: How long a request waits for an identical run already in flight before saying so.
FLIGHT_WAIT_S = 180.0
_RESULTS_MAX = 1024
#: The Portal's limits on a viewer-typed value (saved_query's own rule): no control characters, ≤ 512.
_MAX_PARAM_CHARS = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _display_format(report_format: Optional[str], requested: Optional[str]) -> Tuple[str, Optional[str]]:
    """(the format to show, a note when the placement's own format had to be replaced)."""
    if requested:
        if requested.upper() in _DISPLAY_FORMATS:
            return requested.upper(), None
        return "TABLE", f"'{requested}' is not an output format EA can show; shown as TABLE."
    if report_format and report_format.upper() in _DISPLAY_FORMATS:
        return report_format.upper(), None
    if report_format:
        return "TABLE", f"the Report asks for '{report_format}', which EA cannot show; shown as TABLE."
    return "TABLE", None


def _is_list_junk(value: Any) -> bool:
    """A stringified list ("['ACTIVE']") stored as a Report parameter: an attribute-default quirk the Portal
    skips too, never a value a viewer chose."""
    return isinstance(value, str) and value.strip().startswith("[")


def _base_params(pl: Any, analytic: bool) -> Dict[str, Any]:
    report = pl.report
    if analytic:
        return dict(report.analytic_params or {})
    return {k: v for k, v in dict(report.params or {}).items() if not _is_list_junk(v)}


class ParamRefused(ValueError):
    """A viewer's ⚙ override the placement does not take; `str()` says why."""


def _apply_overrides(base: Dict[str, Any], overrides: Optional[Dict[str, Any]], analytic: bool) -> Dict[str, Any]:
    """The placement's stored parameters with the viewer's overrides on top (this view only; never saved).

    Only parameters the placement already has can be changed, plus the search string of a find spec
    (the Portal's ⚙ rule): a viewer tailors a tile, they do not author a new query. A value is text of at
    most 512 characters with no control characters; where the stored value is not text (a number, a list,
    a type map) the override must be JSON of the same kind."""
    out = dict(base)
    for key, value in (overrides or {}).items():
        if not isinstance(key, str):
            raise ParamRefused("a parameter name must be text")
        searchy = key in ("searchString", "search_string")
        if key not in base and not (searchy and not analytic):
            raise ParamRefused(f"'{key}' is not a parameter of this placement; ⚙ adjusts the parameters it "
                               "already has" + ("" if analytic else ", and its search string") + ".")
        if value is None or value == "":
            continue   # an empty field keeps the stored value, as the Portal's ⚙ does
        if not isinstance(value, str):
            value = json.dumps(value)
        if len(value) > _MAX_PARAM_CHARS or _CONTROL.search(value):
            raise ParamRefused(f"'{key}' must be at most {_MAX_PARAM_CHARS} characters with no control characters.")
        stored = base.get(key)
        if stored is not None and not isinstance(stored, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                raise ParamRefused(f"'{key}' holds {type(stored).__name__} data; give it as JSON "
                                   f"(it is now {json.dumps(stored)[:80]}).") from None
            if type(parsed) is not type(stored) and not (isinstance(stored, (int, float))
                                                        and isinstance(parsed, (int, float))):
                raise ParamRefused(f"'{key}' must stay a {type(stored).__name__}.")
            value = parsed
        out[key] = value
    return out


# ── classifying a result ──────────────────────────────────────────────────────

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _unmeasured_words(notes: Any) -> List[str]:
    out = []
    for n in notes or []:
        if isinstance(n, dict):
            words = f"{n.get('count') or 'a count'}: {n.get('reason') or 'not measured'}"
            out.append(words)
        elif n:
            out.append(str(n))
    return out


def _trend_point(pt: Dict[str, Any]) -> Dict[str, Any]:
    """One metric_trend point, keeping pyegeria's own words for its number. Before pyegeria 6.2.34 a point
    carries no `measurement`: its number is shown, marked `exact: None` ("not checked"), never claimed exact."""
    m = pt.get("measurement") if isinstance(pt.get("measurement"), dict) else None
    value = pt.get("value")
    if m is None:
        exact, capped, at_least = None, False, None
        display = str(value) if _is_number(value) else "not measured"
    else:
        exact, capped, at_least = bool(m.get("exact")), bool(m.get("capped")), m.get("at_least")
        display = pt.get("display")
        if not display:
            display = (f"≥{at_least}" if capped and at_least is not None
                       else str(value) if exact and _is_number(value) else "not measured")
    if capped and not str(display).startswith("≥"):   # W2's rule, whatever the producer said
        display = f"≥{at_least}" if at_least is not None else "not measured"
    return {"label": pt.get("label") or pt.get("date") or "", "date": pt.get("date"),
            "value": value if (exact is not False and _is_number(value)) else None,
            "display": str(display), "exact": exact, "capped": capped, "at_least": at_least,
            "how": (m or {}).get("how"),
            "reasons": [r.get("reason") for r in (m or {}).get("reasons") or [] if isinstance(r, dict)]}


def _is_trend(data: Any) -> bool:
    """A metric_trend series: a list of points that each carry a single `value` (with or without 6.2.34's
    `measurement`). A multi-series growth table (several counts per point) is not a trend tile."""
    return (isinstance(data, list) and len(data) >= 2 and all(isinstance(p, dict) for p in data)
            and all("value" in p and ("label" in p or "date" in p) for p in data))


def _is_saved_query_answer(data: Any) -> bool:
    return isinstance(data, dict) and "display" in data and ("asOf" in data or "mode" in data)


def _tile(raw: Any, fmt: str, spec: str, pipeline: Any) -> Dict[str, Any]:
    """The tile for exec_report_spec's raw result (see the section comment)."""
    def markdown(value: Any, as_fmt: str) -> Dict[str, Any]:
        return {"kind": "markdown", "markdown": pipeline._format_output(value, as_fmt, spec)}

    if raw is None:
        return {"kind": "empty", "message": f"'{spec}' ran and found nothing."}
    if isinstance(raw, str):
        return markdown(raw, fmt)
    if not isinstance(raw, dict):
        return markdown(json.dumps(raw, default=str), "JSON")
    notes = _unmeasured_words(raw.get("unmeasured"))
    kind = raw.get("kind")
    absence = raw.get("absence") if isinstance(raw.get("absence"), dict) else {}
    if kind == "empty":
        tile = {"kind": "empty", "message": absence.get("sentence") or f"'{spec}' ran and found nothing."}
    elif kind == "absent":
        tile = {"kind": "absent", "message": raw.get("content") or absence.get("sentence") or "Not established."}
    elif kind == "table":
        # pyegeria's structured TABLE (table_shape="structured"): native values, per-column render, the
        # "first N of M" / "more may exist" basis and every note, passed through for the page to draw.
        tile = {"kind": "table", "table": {"kind": "table", **{k: raw.get(k) for k in
                ("columns", "rows", "total", "total_basis", "shown", "notes")}}}
    elif kind == "text":
        tile = markdown(raw.get("content") or "", fmt)
    elif kind == "json" and "data" in raw:
        data = raw["data"]
        if isinstance(data, dict) and "vega-lite" in str(data.get("$schema", "")):
            tile = {"kind": "chart", "spec": data}
        elif _is_number(data) or isinstance(data, bool):
            tile = {"kind": "kpi", "value": data, "display": str(data).lower() if isinstance(data, bool) else str(data)}
        elif _is_saved_query_answer(data) and data.get("display") is None:
            # never refreshed, hidden, refused: not established, and never a 0
            tile = {"kind": "absent", "message": data.get("state_reason") or "The saved query's answer is not established.",
                    "mode": data.get("mode"), "as_of": data.get("asOf")}
        elif _is_saved_query_answer(data):
            tile = {"kind": "kpi", "value": data.get("value") if _is_number(data.get("value")) else None,
                    "display": str(data.get("display")), "capped": bool(data.get("capped")),
                    "at_least": data.get("atLeast"), "as_of": data.get("asOf"), "mode": data.get("mode"),
                    "state": data.get("state"), "reason": data.get("state_reason"), "subtitle": data.get("subtitle")}
            if tile["capped"] and not tile["display"].startswith("≥"):
                tile["display"] = f"≥{tile['at_least']}" if tile["at_least"] is not None else "not measured"
            # output "list": the count, and the members below it. A count answer carries
            # elements: None (pyegeria always sends the key), so the key alone is not "a list".
            if data.get("output") == "list" or data.get("elements") is not None:
                tile["markdown"] = pipeline._format_output(data.get("elements") or [], "TABLE", spec)
        elif _is_trend(data):
            points = [_trend_point(p) for p in data]
            tile = {"kind": "trend", "points": points, "latest": points[-1]["display"],
                    "bounds": sum(1 for p in points if p["capped"]),
                    "inexact": sum(1 for p in points if p["exact"] is False)}
        elif (isinstance(data, dict) and 0 < len(data) <= 8
              and all(_is_number(v) or v is None for v in data.values())):
            tile = {"kind": "kpis", "items": [{"label": str(k), "value": v,
                                               "display": str(v) if v is not None else "not measured"}
                                              for k, v in data.items()]}
        elif isinstance(data, str):
            tile = markdown(data, fmt if fmt not in ("DICT", "JSON", "TABLE") else "REPORT")
        else:
            tile = markdown(data, "JSON" if fmt == "JSON" else "TABLE")
    else:
        tile = markdown(json.dumps(raw, default=str), "JSON")
    if notes:
        tile["unmeasured"] = notes
    if isinstance(raw.get("envelope"), dict) and raw["envelope"].get("subtitle"):
        tile["subtitle"] = raw["envelope"]["subtitle"]
    return tile


# ── the result cache: per credential identity, stale-while-revalidate ─────────

class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: Optional[Dict[str, Any]] = None
        self.error: Optional[BaseException] = None


_results: Dict[str, Tuple[float, float, Dict[str, Any]]] = {}   # key -> (wall time, monotonic, payload)
_flights: Dict[str, _Flight] = {}
_refreshing: set = set()
_rlock = threading.Lock()
_refresh_pool: Optional[ThreadPoolExecutor] = None


def _refresher() -> ThreadPoolExecutor:
    global _refresh_pool
    with _rlock:
        if _refresh_pool is None:
            _refresh_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="ea-tile-refresh")
        return _refresh_pool


def _store(key: str, payload: Dict[str, Any]) -> None:
    """Keep an ANSWER (state ok, including "found nothing" and "not established"); never a failed run,
    and never in place of a good answer."""
    if payload.get("state") != "ok":
        return
    with _rlock:
        if len(_results) >= _RESULTS_MAX:
            for k, _ in sorted(_results.items(), key=lambda kv: kv[1][1])[: _RESULTS_MAX // 4]:
                _results.pop(k, None)
        _results[key] = (time.time(), time.monotonic(), payload)


def _stamped(payload: Dict[str, Any], state: str, wall: Optional[float], mono: Optional[float]) -> Dict[str, Any]:
    age = None if mono is None else round(time.monotonic() - mono, 1)
    return {**payload, "cache": {"state": state, "age_s": age, "stored_at": wall,
                                 "fresh_s": RESULT_FRESH_S}}


def _background_refresh(key: str, compute: Callable[[], Dict[str, Any]]) -> None:
    try:
        _store(key, compute())
    except Exception as exc:  # noqa: BLE001 -- the stale answer stays; nothing failed is cached
        logger.info(f"dashboards: background refresh failed, keeping the stale tile: {_words(exc)}")
    finally:
        with _rlock:
            _refreshing.discard(key)


def _cached_run(key: str, compute: Callable[[], Dict[str, Any]], refresh: bool) -> Dict[str, Any]:
    """Serve `key`'s result: fresh from the cache, stale (with one background refresh), or by running it
    once however many requests ask at the same moment (in-flight dedupe)."""
    if not refresh:
        with _rlock:
            hit = _results.get(key)
        if hit is not None:
            wall, mono, payload = hit
            age = time.monotonic() - mono
            if age < RESULT_FRESH_S:
                return _stamped(payload, "hit", wall, mono)
            if age < RESULT_KEEP_S:
                with _rlock:
                    start = key not in _refreshing and key not in _flights
                    if start:
                        _refreshing.add(key)
                if start:
                    _refresher().submit(_background_refresh, key, compute)
                return _stamped(payload, "stale", wall, mono)
    with _rlock:
        flight = _flights.get(key)
        leader = flight is None
        if leader:
            flight = _flights[key] = _Flight()
    if not leader:
        if not flight.event.wait(FLIGHT_WAIT_S):
            return {"state": "failed", "tile": None, "response": None,
                    "message": "The same tile is still running for you; try again shortly.",
                    "cache": {"state": "waited", "age_s": None, "stored_at": None, "fresh_s": RESULT_FRESH_S}}
        if flight.error is not None:
            raise flight.error
        return _stamped(flight.payload or {}, "shared", time.time(), time.monotonic())
    try:
        payload = compute()
        flight.payload = payload
        _store(key, payload)
        return _stamped(payload, "refresh" if refresh else "miss", time.time(), time.monotonic())
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _rlock:
            if _flights.get(key) is flight:
                _flights.pop(key, None)
        flight.event.set()


def _snake_key(key: str) -> str:
    """camelCase -> snake_case exactly as the Portal's snakeKey (local-dashboards.html): every capital
    becomes "_" + its lower case. A key already in snake_case is unchanged."""
    return re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), key)


def _snake_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """A Report's parameters as pyegeria's executor names them.

    Egeria holds a Report's parameters as additionalProperties in camelCase (`collectionQname`,
    `pageSize`, ...), and pyegeria's find functions take snake_case keyword arguments. The Portal
    snake-cases every key before it runs a placement; EA passed them through unchanged, so a spec
    whose required argument is not search_string never saw it ("Give collection_guid, collection_qname
    or collection_name": the Tasks tile on local-dashboards-next-steps, found live on 8880 2026-11-12).
    Values are passed as stored, as the Portal does. Analytic params are already the analytic function's
    own argument names and are not changed (the Portal does not change them either)."""
    out: Dict[str, Any] = {}
    for key, value in params.items():
        snake = _snake_key(key)
        if snake in out and snake != key and snake in params:
            continue   # both spellings stored: the one already in snake_case is the one pyegeria names
        out[snake] = value
    return out


def _execute(spec: str, *, analytic: bool, params: Dict[str, Any], fmt: str, creds: Dict[str, str],
             pipeline: Any, title: str, run_as: Optional[str] = None) -> Dict[str, Any]:
    """Run one spec as the viewer and make its tile. Never raises for a run that fails: says so.

    `run_as` is the name the executor knows the spec by when it is not the label: a spec no tier held,
    which pyegeria's resolver fetched from Egeria as this viewer, runs as "egeria:<label>@v<N>"
    (pyegeria 6.2.34, ResolvedPlacement.fetched_as). The tile still names the label."""
    name = run_as or spec
    # A chart format is asked for as such (pyegeria draws it). TABLE on a find spec asks pyegeria for its
    # structured table (T1: no DICT-then-markdown round trip); an analytic spec's TABLE stays DICT (its
    # answers are counts, charts and saved-query results, which the structured table does not carry).
    structured = fmt == "TABLE" and not analytic
    exec_fmt = ("TABLE" if structured else "DICT") if fmt == "TABLE" else ("REPORT" if fmt == "MARKDOWN" else fmt)
    try:
        if analytic:
            raw, error = pipeline.exec_spec_raw(name, output_type=exec_fmt, extra_params=dict(params),
                                                egeria_credentials=creds, exact_params=True)
        else:
            rest = _snake_params(params)
            search = rest.pop("search_string", None) or "*"
            raw, error = pipeline.exec_spec_raw(name, search_string=search, output_type=exec_fmt,
                                                extra_params=rest or None, egeria_credentials=creds,
                                                **({"table_shape": "structured"} if structured else {}))
    except ConnectionError as exc:   # Egeria unreachable, or the executor raised: the run failed, in words
        raw, error = None, str(exc) or type(exc).__name__
    if error:
        logger.info(f"dashboards: running {spec!r} as {creds.get('user_id')!r} failed: {error}")
        return {"state": "failed", "tile": None, "response": None, "message": f"'{title}' did not run: {error}"}
    tile = _tile(raw, fmt, spec, pipeline)
    return {"state": "ok", "tile": tile, "response": tile.get("markdown"), "message": None}


def _spec_is_analytic(format_set: Any) -> Tuple[bool, Optional[str]]:
    action = getattr(format_set, "action", None) if format_set is not None else None
    fn = getattr(action, "analytic_function", None) if action is not None else None
    return bool(fn), (fn if isinstance(fn, str) else None)


def _saved_query_info(analytic_fn: Optional[str], params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Route B (pyegeria 6.2.34 `saved_query`): which saved query, and cached or live."""
    if analytic_fn != "saved_query":
        return None
    return {"name": params.get("name"), "mode": str(params.get("mode") or "cached").lower(),
            "output": str(params.get("output") or "count").lower()}


def run_placement(creds: Optional[Dict[str, str]], name: str, position: int, *,
                  perspective: Optional[str] = None, output_format: Optional[str] = None,
                  params: Optional[Dict[str, Any]] = None, refresh: bool = False,
                  client_factory: Optional[Callable[[Dict[str, str]], Any]] = None,
                  pipeline: Any = None) -> Dict[str, Any]:
    """Run the report placement at `position` as the viewer, and return its tile.

    {"state": "ok" | "failed" | "refused" | "not_visible" | "read_failed", "tile": {...} | None,
     "response": markdown | None, "message": str | None, "cache": {"state", "age_s", ...}, ...}.
    A restricted, missing or text placement is refused with its reason; a run that fails is "failed"
    with the executor's words, never "ok" with nothing in it.

    `params` are the viewer's ⚙ overrides (this view only; see _apply_overrides) and `refresh` runs it now
    instead of using the cache. An ANALYTIC spec (its action names an analytic_function) is sent its
    analytic params and nothing else: it refuses the find vocabulary (search_string, page_size,
    start_from) with a TypeError. The Portal learned this first (local_dashboards_handler, isAnalytic)."""
    conn = _connection(creds)
    resolved, answer = _resolved(creds, name, perspective, False, client_factory)
    if resolved is None:
        return {**answer, "tile": None, "response": None}
    pl = next((p for p in resolved.placements if p.position == position), None)
    if pl is None:
        return {"state": "refused", "tile": None, "response": None,
                "message": f"Sheet '{name}' has no placement at position {position} for this view."}
    if pl.kind == "restricted":
        return {"state": "refused", "tile": None, "response": None, "restricted": True,
                "message": f"'{pl.title}' is restricted: {pl.reason or NOT_VISIBLE}."}
    if pl.kind != "report":
        return {"state": "refused", "tile": None, "response": None,
                "message": f"'{pl.title}' is a {pl.kind} placement; there is no report to run"
                           + (f" ({pl.reason})." if pl.reason else ".")}
    if pl.error:
        return {"state": "refused", "tile": None, "response": None, "message": pl.error}
    if pl.runnable is False:
        return {"state": "refused", "tile": None, "response": None,
                "message": pl.refusal or f"Report spec '{pl.spec}' cannot run."}

    analytic, fn = _spec_is_analytic(pl.format_set)
    try:
        effective = _apply_overrides(_base_params(pl, analytic), params, analytic)
    except ParamRefused as exc:
        return {"state": "refused", "tile": None, "response": None, "message": str(exc)}
    fmt, fmt_note = _display_format(pl.report.output_format, output_format)
    if pipeline is None:
        from advisor.report_pipeline import get_report_pipeline
        pipeline = get_report_pipeline()
    # A spec fetched from Egeria as this viewer (no tier held it) runs under the name pyegeria gave it.
    run_as = getattr(pl, "fetched_as", None) or None
    key = "run|" + "|".join([credential_identity(conn), run_as or pl.spec or "", fmt, "a" if analytic else "f",
                             json.dumps(effective, sort_keys=True, default=str)])

    def compute() -> Dict[str, Any]:
        return _execute(pl.spec, analytic=analytic, params=effective, fmt=fmt, creds=creds,
                        pipeline=pipeline, title=pl.title, run_as=run_as)

    out = _cached_run(key, compute, refresh)
    return {**out, "output_format": fmt, "format_note": fmt_note, "spec": pl.spec,
            "source": _badge_source(pl.spec_source, pl.spec), "analytic": analytic,
            "params": effective, "overridden": bool(params), "saved_query": _saved_query_info(fn, effective)}


def run_detail(creds: Optional[Dict[str, str]], name: str, position: int, *,
               perspective: Optional[str] = None, refresh: bool = False,
               client_factory: Optional[Callable[[Dict[str, str]], Any]] = None,
               pipeline: Any = None) -> Dict[str, Any]:
    """The drill-down of the placement at `position`: run its `detail_spec` as the viewer, as a table.

    The drill-down spec runs with its own defaults (an analytic spec with none, a find spec matching
    everything, capped by EA's page size): the placement's parameters belong to the placement's own spec,
    and an analytic function refuses another spec's vocabulary. A restricted placement has no drill-down."""
    conn = _connection(creds)
    resolved, answer = _resolved(creds, name, perspective, False, client_factory)
    if resolved is None:
        return {**answer, "tile": None, "response": None}
    pl = next((p for p in resolved.placements if p.position == position), None)
    if pl is None or pl.kind == "restricted" or not pl.detail_spec:
        why = ("it is restricted" if pl is not None and pl.kind == "restricted"
               else "it has no drill-down" if pl is not None else "there is no such placement")
        return {"state": "refused", "tile": None, "response": None,
                "message": f"No drill-down for position {position} of '{name}': {why}."}
    detail = pl.detail_spec
    try:
        from pyegeria.view.base_report_formats import lookup_report_spec
        fs = lookup_report_spec(detail)[0]          # (FormatSet, tier, label), or (None, None, None)
    except Exception as exc:  # noqa: BLE001 -- said in words
        fs, missing = None, _words(exc)
    else:
        missing = None
    if fs is None:
        return {"state": "refused", "tile": None, "response": None, "detail_spec": detail,
                "message": f"The drill-down spec '{detail}' is not in EA's report catalog"
                           + (f" ({missing})." if missing else ".")}
    analytic, _ = _spec_is_analytic(fs)
    if pipeline is None:
        from advisor.report_pipeline import get_report_pipeline
        pipeline = get_report_pipeline()
    key = "detail|" + "|".join([credential_identity(conn), detail, "a" if analytic else "f"])

    def compute() -> Dict[str, Any]:
        return _execute(detail, analytic=analytic, params={}, fmt="TABLE", creds=creds, pipeline=pipeline,
                        title=detail)

    return {**_cached_run(key, compute, refresh), "detail_spec": detail, "analytic": analytic}


def _reset_for_tests() -> None:
    invalidate()
    with _rlock:
        _results.clear()
        _flights.clear()
        _refreshing.clear()
