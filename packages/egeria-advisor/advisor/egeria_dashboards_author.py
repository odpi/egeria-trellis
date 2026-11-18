"""Dashboard authoring, as the signed-in user (brief B7b, N4): save a report to a sheet, create a sheet, move
or remove a placement, delete a sheet.

Every write is PREVIEWED, then CONFIRMED, then made, and always as the viewer's own Egeria token, so the
real person owns what they make (the writer sets the Dashboard's Ownership to them). Never a service user.

Which writer, and why (the brief asks which):
  - The sheet chain, its placements, moving and removing them, and deleting a sheet go through pyegeria's
    DashboardSheetEgeriaWriter directly: it is the one implementation (Dr.Egeria's sheet commands call it),
    and its `dry_run` produces the listing of every write from the SAME code that makes them, so the
    preview is checked against the write. The Dr.Egeria `Create Dashboard Sheet` processor would also write
    pyegeria's local JSON sheet store on EA's host, and Dr.Egeria has no command to remove a placement or
    delete a sheet.
  - The Report a new placement shows is written by Dr.Egeria `Create Report` (pyegeria's ReportProcessor, the
    one Report writer), run in-process through EA's Dr.Egeria executor as the viewer: validated for the
    preview, processed on confirm. It is idempotent by qualifiedName (a second run updates it).
  - Filing a NEW sheet is a Dr.Egeria `Add Member to Collection` into DashboardFolder::Consumer::EgeriaAdvisor
    (the LD folder convention). If that folder is not in Egeria the write SAYS "not filed: ..."; it never
    skips silently. (Until LD-1 merges, nothing lists sheets by folder; B7b is held behind it.)

A plan is one-shot: applying it twice writes once (the second answer is the first result, "already
written"), and a plan can only be applied by the credential that made it. Re-planning the same save after
it was written lists only "unchanged" actions, because every element is found by qualifiedName.

User text never becomes Dr.Egeria structure: names and values are single-line, with no control characters
and no Markdown headings, separators or table pipes, so a value cannot add a command to the document.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

from advisor import egeria_dashboards as ed

#: The LD convention's consumer folder for Egeria Advisor's sheets (BRIEFS-EA-TRACK-B-C §c).
CONSUMER_FOLDER_QN = os.environ.get("ADVISOR_DASHBOARD_FOLDER") or "DashboardFolder::Consumer::EgeriaAdvisor"
PLAN_TTL_S = 600.0
#: The GUID the dry run gives a Report the plan will create, so the placement's link is listed too.
PLANNED_REPORT = "planned:new-report"
_SPANS = ("1", "2", "full")
_EMPHASES = ("kpi", "panel")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.,:()&'\-]{0,119}$")
_MAX_TEXT = 512


class AuthorRefused(ValueError):
    """A request that cannot be planned or applied; str() says why, in words."""


# ── input hygiene ─────────────────────────────────────────────────────────────

def _text(value: Any, what: str, *, required: bool = False, max_len: int = _MAX_TEXT) -> str:
    """A single line of user text that is safe inside a Dr.Egeria document."""
    if value is None or value == "":
        if required:
            raise AuthorRefused(f"{what} is required.")
        return ""
    if not isinstance(value, (str, int, float, bool)):
        raise AuthorRefused(f"{what} must be text.")
    text = str(value)
    if len(text) > max_len:
        raise AuthorRefused(f"{what} must be at most {max_len} characters.")
    # pyegeria reads the document with str.splitlines(), which also breaks a line at U+0085, U+2028, U+2029
    # and \x0b \x0c \x1c-\x1e: any of them would start a new line, and so a new heading or command.
    if len(text.splitlines()) != 1 or re.search(r"[\x00-\x1f\x7f\x85\u2028\u2029]", text):
        raise AuthorRefused(f"{what} must be one line, with no control characters or line breaks.")
    stripped = text.strip()
    if stripped.startswith(("#", ">")) or "___" in text or "|" in text:
        raise AuthorRefused(f"{what} may not start with '#' or '>', or contain '___' or '|'.")
    if re.fullmatch(r"-{3,}", stripped):
        # a block boundary: it could cut "### Zone Membership" off a Report and leave it public
        raise AuthorRefused(f"{what} may not be a line of dashes.")
    return stripped


def _name(value: Any, what: str) -> str:
    text = _text(value, what, required=True, max_len=120)
    if not _NAME.match(text):
        raise AuthorRefused(f"{what} may use letters, digits, spaces and . , : ( ) & ' - _ only, "
                            "and must start with a letter or digit.")
    return text


def _zones(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise AuthorRefused("Zones must be a list of at most 10 zone names (none: public).")
    return [_name(z, "A zone name") for z in value]


def _params(value: Any, what: str) -> Dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict) or len(value) > 30:
        raise AuthorRefused(f"{what} must be an object of at most 30 parameters.")
    out = {}
    for k, v in value.items():
        key = _text(k, f"A {what} name", required=True, max_len=80)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise AuthorRefused(f"'{key}' is not a parameter name.")
        if isinstance(v, (dict, list)):
            v = json.dumps(v)
        out[key] = _text(v, f"Parameter '{key}'")
    return out


# ── Dr.Egeria documents ───────────────────────────────────────────────────────

def _section(title: str, value: str) -> str:
    return f"### {title}\n{value}\n\n"


def _table(rows: Dict[str, Any]) -> str:
    return ("| Parameter Name | Parameter Value |\n|---|---|\n"
            + "".join(f"| {k} | {v} |\n" for k, v in rows.items()))


def report_document(report: Dict[str, Any], zones: List[str]) -> str:
    """The Dr.Egeria `Create Report` command for a placement's Report (all values already checked)."""
    doc = "## Create Report\n\n" + _section("Display Name", report["name"]) + _section("Report Spec", report["spec"])
    doc += _section("Output Format", report["output_format"])
    if report["description"]:
        doc += _section("Description", report["description"])
    params = dict(report["params"])
    search = params.pop("search_string", None) or params.pop("searchString", None)
    if search:
        doc += _section("Search String", search)
    if params:
        doc += "### Report Parameters\n" + _table(params) + "\n"
    if report["analytic_params"]:
        doc += "### Analytic Parameters\n" + _table(report["analytic_params"]) + "\n"
    if zones:
        doc += _section("Zone Membership", ", ".join(zones))
    return doc + "___\n"


def filing_document(sheet: str) -> str:
    from md_processing.v2.dashboard_sheet import dashboard_qn
    return ("## Add Member to Collection\n\n" + _section("Collection Id", CONSUMER_FOLDER_QN)
            + _section("Element Id", dashboard_qn(sheet))
            + _section("Membership Rationale", "Filed by Egeria Advisor when the sheet was created.") + "___\n")


def _dr_conn(conn: Dict[str, str]) -> Dict[str, str]:
    return {"auth": "user", "token": conn["token"], "user_id": conn["user_id"],
            "server_name": conn["view_server"], "url": conn["platform_url"]}


async def _run_dr_egeria(conn: Dict[str, str], markdown: str, directive: str) -> Dict[str, Any]:
    """EA's in-process Dr.Egeria executor (the same one Act uses), as the viewer. Patched in tests."""
    from advisor.agents.dr_egeria_agent import _execute_dr_egeria_markdown
    return await _execute_dr_egeria_markdown(markdown, directive, _dr_conn(conn))


def _dr_words(envelope: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """(succeeded, words, the first command's guid) from Dr.Egeria's result envelope."""
    errors = [e.get("message", "") for e in (envelope.get("validation_errors") or [])
              + (envelope.get("execution_errors") or [])]
    detail = envelope.get("commands_detail") or []
    ok = bool(envelope.get("success", not errors)) and not errors
    guid = next((d.get("guid") for d in detail if d.get("guid")), None)
    words = "; ".join(e for e in errors if e) or (detail[0].get("message", "") if detail else "")
    return ok, words, guid


# ── the writer, previewed and real ────────────────────────────────────────────

def _writer_class():
    from md_processing.v2.dashboard_sheet import DashboardSheetEgeriaWriter

    class _Writer(DashboardSheetEgeriaWriter):
        """The writer, with removals remembered so a dry run of "remove, then re-add" (a move) reads the sheet
        the way the real run will. A real run deletes the field, so its list_fields never sees it anyway."""

        def __init__(self, client: Any, *, dry_run: bool = False):
            super().__init__(client, dry_run=dry_run)
            self._gone: set = set()

        async def list_fields(self, container_guid):
            return [f for f in await super().list_fields(container_guid) if f.guid not in self._gone]

        async def remove_placement(self, sheet_name: str, placement_name: str) -> bool:
            chain = await self.find_chain(sheet_name)
            target = next((f for f in await self.list_fields(chain.container_guid)
                           if f.display_name == placement_name), None)
            done = await super().remove_placement(sheet_name, placement_name)
            if done and target is not None:
                self._gone.add(target.guid)
            return done

    return _Writer


def _normal(action: str) -> str:
    """The real run words an action "created"/"updated" where the dry run says "create"/"update"."""
    first, _, rest = action.partition(" ")
    first = {"created": "create", "updated": "update"}.get(first.lower(), first)
    return f"{first} {rest}".strip()


async def _add_zones(writer: Any, guid: Optional[str], qn: str, zones: List[str]) -> None:
    if not zones:
        return
    writer.actions.append(f"classify ZoneMembership {qn} (zones {', '.join(zones)})")
    if not writer.dry_run:
        await writer.client.classification_manager._async_add_zone_membership(
            guid, {"class": "NewClassificationRequestBody",
                   "properties": {"class": "ZoneMembershipProperties", "zoneMembership": zones}})


async def _sheet_placements(client: Any, sheet: str) -> List[Any]:
    """The sheet's placements as the writer reads them (name, span, emphasis, perspectives, drill-down,
    text, report GUID), in position order."""
    from pyegeria.view.dashboards import read_sheet_from_chain
    read = await read_sheet_from_chain(client, sheet)
    return list(read.placements) if read else []


async def _writes(writer: Any, op: Dict[str, Any], report_guid: Optional[str]) -> Dict[str, Any]:
    """Make (or, dry, list) the writer's part of `op`. Returns what the caller reports."""
    from pyegeria.view._output_dashboard_sheet_models import Placement
    from md_processing.v2.dashboard_sheet import dashboard_qn

    kind, sheet = op["kind"], op["sheet"]
    chain = await writer.find_chain(sheet)
    out: Dict[str, Any] = {}
    if kind in ("create_sheet", "save") and op.get("new_sheet"):
        chain = await writer.ensure_sheet(sheet, op["new_sheet"]["heading"], op["new_sheet"]["description"])
        await writer.ensure_ownership(chain.dashboard_guid, dashboard_qn(sheet), op["owner"])
        await _add_zones(writer, chain.dashboard_guid, dashboard_qn(sheet), op["zones"])
    elif not chain.dashboard_guid:
        raise AuthorRefused(f"There is no Dashboard Sheet '{sheet}' that you can see.")
    if kind == "save":
        if not chain.container_guid and not writer.dry_run:
            raise AuthorRefused(f"Dashboard Sheet '{sheet}' has a broken chain (no sheet container); "
                                "it cannot take a placement.")
        p = op["placement"]
        await writer.upsert_placement(chain, Placement(ref=op["report"]["name"], span=p["span"],
                                                       emphasis=p["emphasis"]), report_guid=report_guid)
    elif kind == "remove":
        if not await writer.remove_placement(sheet, op["placement_name"]):
            raise AuthorRefused(f"Sheet '{sheet}' has no placement named '{op['placement_name']}'.")
    elif kind == "move":
        placements = await _sheet_placements(writer.client, sheet)
        fields = await writer.list_fields(chain.container_guid)
        names = [f.display_name for f in fields]
        name = op["placement_name"]
        if name not in names:
            raise AuthorRefused(f"Sheet '{sheet}' has no placement named '{name}'.")
        i = names.index(name)
        j = i - 1 if op["direction"] == "up" else i + 1
        if j < 0 or j >= len(names):
            raise AuthorRefused(f"'{name}' is already {'first' if j < 0 else 'last'} on the sheet.")
        by_name = {pl.ref: pl for pl in placements}
        a, b = fields[i], fields[j]
        pa, pb = by_name.get(a.display_name), by_name.get(b.display_name)
        if pa is None or pb is None:
            raise AuthorRefused("The sheet changed while it was being read; open it again and retry.")
        # Remove both, then re-add each at the other's position, keeping its report link and settings.
        await writer.remove_placement(sheet, a.display_name)
        await writer.remove_placement(sheet, b.display_name)
        for pl, pos in ((pa, b.position), (pb, a.position)):
            plain = Placement(ref=pl.ref, span=pl.span, emphasis=pl.emphasis, content=pl.content,
                              perspectives=list(pl.perspectives or []), detail_spec=pl.detail_spec)
            await writer.upsert_placement(chain, plain, position=pos, report_guid=getattr(pl, "report_guid", None))
        out["moved"] = {"from": a.position, "to": b.position}
    elif kind == "delete_sheet":
        await writer.delete_sheet(sheet)
    return out


# ── reads for the preview ─────────────────────────────────────────────────────

async def _existing_report(client: Any, name: str) -> Optional[Dict[str, Any]]:
    """The one Report whose displayName is exactly `name` that this viewer can see, or None."""
    from md_processing.v2.dashboard_sheet import DashboardSheetEgeriaWriter
    guid = await DashboardSheetEgeriaWriter(client, dry_run=True)._resolve_report(name)
    if not guid:
        return None
    element = await client._async_get_element_by_guid_(guid)
    return element if isinstance(element, dict) else None


async def _filing_state(client: Any, sheet: str) -> Dict[str, Any]:
    """Whether a new sheet will be filed: "will_file", "already_filed" (its Dashboard is a member already:
    Dr.Egeria's Add Member does not dedupe, so a second filing would add a second membership), or
    "not_filed" with why. Read at preview AND again at apply, so two plans for one sheet file once."""
    from md_processing.v2.dashboard_sheet import (DashboardSheetEgeriaWriter, dashboard_qn,
                                                  find_element_by_qualified_name)
    folder = await find_element_by_qualified_name(client, CONSUMER_FOLDER_QN)
    if not folder:
        return {"state": "not_filed", "folder": CONSUMER_FOLDER_QN,
                "message": f"not filed: the EgeriaAdvisor dashboard folder ({CONSUMER_FOLDER_QN}) isn't seeded "
                           "in Egeria, or you cannot see it."}
    reader = DashboardSheetEgeriaWriter(client, dry_run=True)
    members = await reader._related(reader._guid(folder), "CollectionMembership", start_at_end=1)
    if any((m.get("properties") or {}).get("qualifiedName") == dashboard_qn(sheet) for m in members):
        return {"state": "already_filed", "folder": CONSUMER_FOLDER_QN,
                "message": f"already filed in {CONSUMER_FOLDER_QN}"}
    return {"state": "will_file", "folder": CONSUMER_FOLDER_QN,
            "message": f"will add {dashboard_qn(sheet)} to {CONSUMER_FOLDER_QN}",
            "dr_egeria": filing_document(sheet)}


# ── plans ─────────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    id: str
    identity: str
    op: Dict[str, Any]
    preview: Dict[str, Any]
    created: float = field(default_factory=time.monotonic)
    result: Optional[Dict[str, Any]] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_plans: Dict[str, Plan] = {}
_plans_lock = threading.Lock()


def _validate(kind: str, req: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    if kind not in ("save", "create_sheet", "remove", "move", "delete_sheet"):
        raise AuthorRefused(f"'{kind}' is not something a dashboard can be asked to do.")
    op: Dict[str, Any] = {"kind": kind, "sheet": _name(req.get("sheet"), "The sheet name"), "owner": user_id,
                          "zones": _zones(req.get("zones")), "new_sheet": None}
    if kind == "create_sheet" or (kind == "save" and req.get("new_sheet")):
        ns = req.get("new_sheet") if kind == "save" else req
        ns = ns if isinstance(ns, dict) else {}
        op["new_sheet"] = {"heading": _text(ns.get("heading"), "The heading") or op["sheet"],
                           "description": _text(ns.get("description"), "The description")}
    elif op["zones"]:
        raise AuthorRefused("Zones are set when a sheet is created; this sheet keeps the zones it has.")
    if kind == "save":
        r = req.get("report") if isinstance(req.get("report"), dict) else {}
        fmt = _text(r.get("output_format"), "The output format") or "DICT"
        if fmt.upper() not in ed._DISPLAY_FORMATS:
            raise AuthorRefused(f"'{fmt}' is not an output format a dashboard tile can show.")
        op["report"] = {"name": _name(r.get("name"), "The Report name"),
                        "spec": _text(r.get("spec"), "The report spec", required=True, max_len=200),
                        "output_format": fmt.upper(),
                        "description": _text(r.get("description"), "The description"),
                        "params": _params(r.get("params"), "parameter"),
                        "analytic_params": _params(r.get("analytic_params"), "analytic parameter")}
        p = req.get("placement") if isinstance(req.get("placement"), dict) else {}
        span, emphasis = str(p.get("span") or "1"), str(p.get("emphasis") or "kpi")
        if span not in _SPANS or emphasis not in _EMPHASES:
            raise AuthorRefused(f"Span is one of {', '.join(_SPANS)} and emphasis one of {', '.join(_EMPHASES)}.")
        op["placement"] = {"span": span, "emphasis": emphasis}
    if kind in ("remove", "move"):
        op["placement_name"] = _text(req.get("placement"), "The placement", required=True, max_len=200)
    if kind == "move":
        if req.get("direction") not in ("up", "down"):
            raise AuthorRefused("A placement moves 'up' or 'down'.")
        op["direction"] = req["direction"]
    return op


def _client(conn: Dict[str, str], client_factory: Optional[Callable]) -> Any:
    return (client_factory or ed._build_client)(conn)


def _words(exc: BaseException) -> str:
    return ed._words(exc)


async def _plan_async(conn, op, client) -> Dict[str, Any]:
    from md_processing.v2.dashboard_sheet import dashboard_qn
    preview: Dict[str, Any] = {"kind": op["kind"], "sheet": op["sheet"], "notes": [], "dr_egeria": [],
                               "report": None, "filing": None}
    chain_exists = bool((await _writer_class()(client, dry_run=True).find_chain(op["sheet"])).dashboard_guid)
    if op["new_sheet"] and chain_exists:
        raise AuthorRefused(f"A Dashboard Sheet named '{op['sheet']}' already exists; pick another name, "
                            "or save to it as an existing sheet.")
    report_guid = None
    if op["kind"] == "save":
        r = op["report"]
        existing = await _existing_report(client, r["name"])
        if existing is not None:
            props = existing.get("properties") or {}
            spec = props.get("reportSpec") or (props.get("additionalProperties") or {}).get("reportSpec")
            if spec and spec != r["spec"]:
                raise AuthorRefused(f"A Report named '{r['name']}' already exists for spec '{spec}'; give this "
                                    "one another name.")
            report_guid = existing["elementHeader"]["guid"]
            preview["report"] = {"state": "existing", "guid": report_guid,
                                 "message": f"uses the existing Report '{r['name']}' (its stored parameters)"}
        else:
            doc = report_document(r, op["zones"])
            envelope = await _run_dr_egeria(conn, doc, "validate")
            ok, words, _ = _dr_words(envelope)
            if not ok:
                raise AuthorRefused(f"Dr.Egeria refused the Report: {words}")
            preview["report"] = {"state": "create", "message": f"create Report '{r['name']}' (Dr.Egeria Create Report)"}
            preview["dr_egeria"].append(doc)
            report_guid = PLANNED_REPORT
    writer = _writer_class()(client, dry_run=True)
    preview["extra"] = await _writes(writer, op, report_guid)
    preview["actions"] = list(writer.actions)
    preview["notes"] += list(writer.notes)
    if op["new_sheet"]:
        preview["filing"] = await _filing_state(client, op["sheet"])
        if preview["filing"].get("dr_egeria"):
            preview["dr_egeria"].append(preview["filing"]["dr_egeria"])
    if op["kind"] == "delete_sheet":
        preview["confirm_with"] = op["sheet"]
        preview["notes"].append(f"Deletes only {dashboard_qn(op['sheet'])} and its own layout elements; its "
                                "Reports and report specs stay.")
    return preview


def plan(creds: Optional[Dict[str, str]], kind: str, request: Dict[str, Any], *,
         client_factory: Optional[Callable] = None) -> Dict[str, Any]:
    """Preview a write: every action it would make, as the viewer, and a plan id to confirm it with.
    Nothing is written. {"state": "planned" | "refused", ...}."""
    conn = ed._connection(creds)
    try:
        op = _validate(kind, request or {}, conn["user_id"])
        client = _client(conn, client_factory)
        preview = asyncio.run(_plan_async(conn, op, client))
    except AuthorRefused as exc:
        return {"state": "refused", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 -- a read that failed is said, never planned around
        if ed._is_token_refused(exc):
            raise ed.TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
        logger.warning(f"dashboards author: planning {kind} as {conn['user_id']!r} failed: {exc}")
        return {"state": "refused", "message": f"Could not read Egeria to plan this: {_words(exc)}"}
    plan_id = secrets.token_urlsafe(16)
    with _plans_lock:
        now = time.monotonic()
        for k in [k for k, p in _plans.items() if now - p.created > PLAN_TTL_S]:
            _plans.pop(k, None)
        _plans[plan_id] = Plan(plan_id, ed.credential_identity(conn), op, preview)
    return {"state": "planned", "plan_id": plan_id, "expires_in_s": PLAN_TTL_S, **preview}


async def _apply_async(conn, plan_: Plan, client) -> Dict[str, Any]:
    op, preview = plan_.op, plan_.preview
    result: Dict[str, Any] = {"kind": op["kind"], "sheet": op["sheet"], "notes": [], "report": None,
                              "filing": None}
    report_guid = None
    if op["kind"] == "save":
        if preview["report"]["state"] == "existing":
            report_guid = preview["report"]["guid"]
            result["report"] = {"state": "existing", "guid": report_guid}
        else:
            envelope = await _run_dr_egeria(conn, preview["dr_egeria"][0], "process")
            ok, words, guid = _dr_words(envelope)
            if not ok or not guid:
                return {**result, "state": "failed", "message": f"The Report was not written: {words or 'no GUID returned'}. "
                                                                "Nothing else was written."}
            report_guid = guid
            result["report"] = {"state": "created", "guid": guid}
    writer = _writer_class()(client, dry_run=False)
    try:
        result["extra"] = await _writes(writer, op, report_guid)
    except Exception as exc:  # noqa: BLE001 -- a part-written change is said, with what was written
        return {**result, "state": "failed", "actions": list(writer.actions),
                "message": f"Stopped part-way: {_words(exc)}. Written so far: {len(writer.actions)} action(s), "
                           "listed in actions. Re-planning shows what is left."}
    result["actions"] = list(writer.actions)
    result["notes"] = list(writer.notes)
    planned = [_normal(a) for a in preview["actions"]]
    made = [_normal(a) for a in writer.actions]
    result["matches_preview"] = planned == made
    if not result["matches_preview"]:
        result["notes"].append("What was written differs from the preview (Egeria changed in between): "
                               "see actions.")
    if op["new_sheet"]:
        result["filing"] = await _file(conn, client, op["sheet"])
    return {**result, "state": "written"}


async def _file(conn: Dict[str, str], client: Any, sheet: str) -> Dict[str, Any]:
    """File a new sheet AFTER its write. Never fails the apply: the sheet is written whatever happens here,
    and a filing that did not happen says why ("not_filed: <reason>"), so it can be filed later."""
    try:
        filing = await _filing_state(client, sheet)
        if filing["state"] == "will_file":
            ok, words, _ = _dr_words(await _run_dr_egeria(conn, filing["dr_egeria"], "process"))
            filing = ({"state": "filed", "folder": CONSUMER_FOLDER_QN, "message": f"filed in {CONSUMER_FOLDER_QN}"}
                      if ok else {"state": "not_filed", "folder": CONSUMER_FOLDER_QN,
                                  "message": f"not filed: Dr.Egeria refused the membership: {words}"})
    except Exception as exc:  # noqa: BLE001 -- said in the result; the sheet write stands
        logger.warning(f"dashboards author: filing {sheet!r} failed after the sheet was written: {exc}")
        filing = {"state": "not_filed", "folder": CONSUMER_FOLDER_QN,
                  "message": f"not filed: {_words(exc)}. The sheet was written; file it again later."}
    return {k: v for k, v in filing.items() if k != "dr_egeria"}


def apply(creds: Optional[Dict[str, str]], plan_id: str, *, confirm: Any = None,
          client_factory: Optional[Callable] = None) -> Dict[str, Any]:
    """Make a previewed write, once. {"state": "written" | "already_written" | "refused" | "failed", ...}."""
    conn = ed._connection(creds)
    with _plans_lock:
        plan_ = _plans.get(plan_id or "")
    if plan_ is None or time.monotonic() - plan_.created > PLAN_TTL_S:
        return {"state": "refused", "message": "That preview has expired or is unknown; preview it again."}
    if plan_.identity != ed.credential_identity(conn):
        return {"state": "refused", "message": "That preview was made by another sign-in; preview it again."}
    want = plan_.preview.get("confirm_with")
    if (want is not None and confirm != want) or (want is None and confirm is not True):
        return {"state": "refused", "message": (f"Type the sheet's name, '{want}', to confirm." if want
                                               else "Confirm the preview to write it.")}
    with plan_.lock:   # one apply at a time per plan; a retry gets the first answer
        if plan_.result is not None:
            return {**plan_.result, "state": "already_written",
                    "message": "This preview was already written; nothing was written again."}
        try:
            result = asyncio.run(_apply_async(conn, plan_, _client(conn, client_factory)))
        except Exception as exc:  # noqa: BLE001
            if ed._is_token_refused(exc):
                raise ed.TokenRefused("Egeria refused your sign-in token; sign in again.") from exc
            logger.warning(f"dashboards author: applying {plan_.op['kind']} failed: {exc}")
            return {"state": "failed", "message": f"Not written: {_words(exc)}"}
        if result["state"] == "written":
            plan_.result = result
    ed.invalidate()   # every viewer's sheet and list caches: the sheet changed
    return result


def _reset_for_tests() -> None:
    with _plans_lock:
        _plans.clear()
