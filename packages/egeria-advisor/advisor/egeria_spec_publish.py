"""Publish an EA spec to Egeria, and withdraw it (brief B6, N1/N2).

**How.** Publishing writes a Dr.Egeria document and runs it **as the signed-in user's token**
(`DrEgeriaActionAgent.execute(..., egeria_credentials=...)`; never the service account, so Egeria
records the real person). The steps are fixed: **preview** (the document, nothing sent), **validate**
(Dr.Egeria's validate run), the user **confirms**, then **process**. The process step rebuilds the
document and refuses if it differs from the one the user previewed and confirmed.

**What.** One `Create Report Spec` (or `Update Report Spec` when the label is already held in Egeria),
then `Add Member to Collection` for each consumer folder:

- `ReportSpecFolder::Consumer::EgeriaAdvisor` always ("membership is publication");
- `ReportSpecFolder::Consumer::Portal` when asked.

**Visibility.** Public (the default) carries no zones. Restricted carries `Zone Membership` (a Basic
attribute since pyegeria 6.2.30); a restricted spec never enters EA's shared cache (pyegeria skips a
zoned spec there) and is read per user instead (advisor/egeria_spec_restricted.py). A blank Zone
Membership on an Update leaves the old zones in place (6.2.30), so re-publishing a restricted spec as
public adds `Declassify Zone Membership`.

**Hazard (memory dr_egeria_parent_id_update_hazard).** Never a Parent ID on the spec command, on Create
or Update: folder membership is only ever an `Add Member to Collection`, which is an upsert.

**Withdraw.** `Remove Member from Collection` from each consumer folder, as the user, after a confirm;
then a sync starts, so the catalog shows it gone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

from loguru import logger

ADVISOR_FOLDER = "ReportSpecFolder::Consumer::EgeriaAdvisor"
PORTAL_FOLDER = "ReportSpecFolder::Consumer::Portal"
STEPS = ("preview", "validate", "process")
_ZONE_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.:-]{0,127}")   # used with fullmatch: no trailing newline
_RULE = re.compile(r"(-{3,}|_{3,}|\*{3,})")

#: D5: restricted publish stays off until the live zone proof (a user without the zone cannot see a zoned
#: ReportSpec; one with it can) has been captured on this platform. Set this to where the proof is recorded
#: (e.g. the read-back file name) to turn it on.
RESTRICTED_PROOF_ENV = "ADVISOR_RESTRICTED_PUBLISH_PROVEN"
NOT_PROVEN = ("Restricted publish is not yet proven on this platform: Egeria's zone check on report specs has "
              "not been shown live here, so a 'restricted' spec might be visible to everyone. Publish it as "
              "public, or wait for the zone proof.")


def _v2_enabled() -> bool:
    from advisor.spec_authoring_v2 import enabled
    return enabled()


class PublishRefused(ValueError):
    """The request cannot be turned into a document: the words say why."""


def restricted_publish_status() -> Dict[str, Any]:
    """{"enabled": bool, "message": str | None, "proof": str | None} (D5)."""
    proof = (os.getenv(RESTRICTED_PROOF_ENV) or "").strip()
    if proof:
        return {"enabled": True, "message": None, "proof": proof}
    return {"enabled": False, "message": NOT_PROVEN, "proof": None}


def _one_line(field: str, value: Any) -> str:
    """A value written into the Dr.Egeria document as one attribute line, or PublishRefused.

    Dr.Egeria splits the document with str.splitlines(), which also breaks on U+0085, U+2028, U+2029,
    \\x0b, \\x0c and \\x1c-\\x1e. A value holding any of those could start its own "## Command" or
    "### Zone Membership" line, and a lone rule ("---") or a leading ">" changes how the block is
    read: a restricted spec could be published as public. So: exactly one line, not a rule, not a
    heading or quote."""
    text = str(value)
    if len(text.splitlines()) != 1 or text != text.splitlines()[0]:
        raise PublishRefused(f"{field} must be a single line of text (line breaks of any kind are refused).")
    stripped = text.strip()
    if not stripped:
        raise PublishRefused(f"{field} is empty.")
    if _RULE.fullmatch(stripped) or stripped.startswith((">", "#")):
        raise PublishRefused(f"{field} cannot be a rule ('---') or start with '>' or '#': {stripped[:40]!r}.")
    return stripped


def _list_item(field: str, value: Any) -> str:
    """One entry of a list attribute: one line, and no comma (Dr.Egeria splits lists on commas)."""
    text = _one_line(field, value)
    if "," in text:
        raise PublishRefused(f"{field} cannot contain a comma (it would become two entries): {text[:40]!r}.")
    return text


def _flatten(field: str, value: Any) -> Optional[str]:
    """Free text (a description) folded onto one line: every kind of line break becomes a space."""
    if value in (None, ""):
        return None
    folded = " ".join(str(value).split())
    return _one_line(field, folded) if folded else None


def spec_qualified_name(label: str) -> str:
    return f"ReportSpec::{label}"


def _check_label(label: str) -> str:
    if not (label or "").strip():
        raise PublishRefused("A report spec name is needed.")
    return _one_line("The report spec name", label)


def _check_zones(visibility: str, zones: Optional[List[str]]) -> List[str]:
    if visibility not in ("public", "restricted"):
        raise PublishRefused(f"Visibility must be 'public' or 'restricted', not {visibility!r}.")
    cleaned, bad = set(), []
    for z in zones or []:
        if not isinstance(z, str):
            bad.append(repr(z))
        elif _ZONE_OK.fullmatch(z.strip()):
            cleaned.add(z.strip())
        elif z.strip():
            bad.append(repr(z))
    if bad:
        raise PublishRefused(f"Not a zone name (letters, digits, space, _ . : - only, one line): {', '.join(bad)}.")
    zones = sorted(cleaned)
    if visibility == "public":
        if zones:
            raise PublishRefused("A public spec carries no zones; choose 'Restricted to zones' to set them.")
        return []
    if not zones:
        raise PublishRefused("Restricted needs at least one zone.")
    status = restricted_publish_status()
    if not status["enabled"]:
        raise PublishRefused(NOT_PROVEN)
    return zones


def _local_spec(label: str):
    """The FormatSet to publish: an EA or local spec. A built-in or Egeria-held spec is not EA's to
    publish (it already lives in pyegeria or in Egeria)."""
    from advisor.egeria_spec_sync import spec_sources
    from pyegeria.view.base_report_formats import get_report_registry
    fs = get_report_registry().get(label)
    if fs is None:
        raise PublishRefused(f"No report spec named '{label}' is registered in EA.")
    source = (spec_sources().get(label) or {}).get("source")
    if source in ("builtin", "egeria"):
        raise PublishRefused(f"'{label}' is a {source} spec, not one of yours; only local specs are published.")
    return fs


def _microflow_json(fs) -> str:
    from pyegeria.view.microflow import format_set_to_microflow
    try:
        mf = format_set_to_microflow(fs)
    except Exception as exc:  # noqa: BLE001 -- the words go to the user
        raise PublishRefused(f"This spec cannot be written as a microflow: {exc}") from exc
    # ensure_ascii: every non-ASCII character (U+2028 and the other Unicode line breaks too) is written as
    # a \\u escape, so the microflow is one line whatever its column labels hold
    return _one_line("The microflow", json.dumps(json.loads(mf.to_json()), ensure_ascii=True, separators=(",", ":")))


def _output_types(fs) -> List[str]:
    out: List[str] = []
    for fmt in getattr(fs, "formats", None) or []:
        for t in getattr(fmt, "types", None) or []:
            if t and t.upper() not in out:
                out.append(t.upper())
    return out


def held_in_egeria(creds: Optional[Dict[str, str]], label: str) -> Dict[str, Any]:
    """{"held": bool, "zones": [...], "how": words}: whether Egeria already holds this label, as far
    as EA can tell (the shared tier for public specs, the user's restricted listing for zoned ones).
    Dr.Egeria also upserts a Create whose qualified name exists, so a miss here is never a duplicate."""
    tier_note = ""
    try:
        from pyegeria.view.egeria_spec_cache import egeria_tier_info
        if egeria_tier_info(label):
            return {"held": True, "zones": [], "how": "held in Egeria (EA's shared tier)"}
    except Exception as exc:  # noqa: BLE001 -- carried into "how"
        tier_note = f"; EA's shared tier could not be read ({exc})"
    from advisor import egeria_spec_restricted as rs
    try:
        got = rs.restricted_specs(creds)
    except Exception as exc:  # noqa: BLE001 -- a refused token is the process step's to report
        return {"held": False, "zones": [], "how": f"not known: restricted specs not read ({exc}){tier_note}"}
    for s in got.get("specs") or []:
        if s.get("label") == label:
            return {"held": True, "zones": list(s.get("zones") or []), "how": "held in Egeria, restricted"}
    return {"held": False, "zones": [], "how": f"not held in Egeria as far as EA can see{tier_note}"}


def _section(title: str, rows: List[tuple]) -> str:
    lines = [f"## {title}"]
    for name, value in rows:
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(value)
        lines += [f"### {name}", str(value)]
    return "\n".join(lines) + "\n\n___\n"


def build_publish_document(label: str, fs, *, visibility: str = "public", zones: Optional[List[str]] = None,
                           also_portal: bool = False, held: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The Dr.Egeria document that publishes `fs` as `label`, with a plain summary of what it does."""
    if _v2_enabled():
        # Stage 1b (EA re-base prototype): pyegeria's shared helper builds the same document.
        from pyegeria.view import report_spec_publish as rsp
        try:
            return rsp.build_publish_document(
                label, fs, folders=[ADVISOR_FOLDER] + ([PORTAL_FOLDER] if also_portal else []),
                visibility=visibility, zones=zones, held=held, restricted=restricted_publish_status())
        except rsp.PublishRefused as exc:
            raise PublishRefused(str(exc)) from exc
    label = _check_label(label)
    zones = _check_zones(visibility, zones)
    held = held or {"held": False, "zones": []}
    verb = "Update" if held.get("held") else "Create"
    qn = spec_qualified_name(label)
    folders = [ADVISOR_FOLDER] + ([PORTAL_FOLDER] if also_portal else [])
    target = getattr(fs, "target_type", None)
    rows = [("Display Name", label), ("Qualified Name", qn),
            ("Description", _flatten("The description", getattr(fs, "description", None))),
            ("Content Status", "ACTIVE"),
            ("Aliases", [_list_item("An alias", a) for a in (getattr(fs, "aliases", None) or [])]),
            ("Target Type Name", _one_line("The target type", target) if target else None),
            ("Output Types", [_list_item("An output type", t) for t in _output_types(fs)]),
            ("Microflow", _microflow_json(fs))]
    if zones:
        rows.append(("Zone Membership", zones))
    # Never a Parent ID here (Create or Update): membership is the Add Member below.
    doc = [f"# Publish report spec {label}\n", _section(f"{verb} Report Spec", rows)]
    steps = [f"{verb} the ReportSpec {qn}" + (f", restricted to zones {', '.join(zones)}" if zones else ", public")]
    if visibility == "public" and held.get("zones"):
        doc.append(_section("Declassify Zone Membership", [("Target Element", qn)]))
        steps.append(f"remove its zones ({', '.join(held['zones'])}): it was restricted, it becomes public")
    for folder in folders:
        doc.append(_section("Add Member to Collection", [("Element Id", qn), ("Collection Id", folder)]))
        steps.append(f"add it to {folder}")
    markdown = "\n".join(doc)
    return {"label": label, "qualified_name": qn, "verb": verb, "visibility": visibility, "zones": zones,
            "folders": folders, "markdown": markdown, "digest": _digest(markdown), "steps": steps,
            "held": held}


def build_withdraw_document(label: str, *, folders: Optional[List[str]] = None) -> Dict[str, Any]:
    if _v2_enabled():
        from pyegeria.view import report_spec_publish as rsp
        try:
            return rsp.build_withdraw_document(
                label, folders or [ADVISOR_FOLDER], allowed=(ADVISOR_FOLDER, PORTAL_FOLDER),
                allowed_words="EA's and the Portal's consumer folders")
        except rsp.PublishRefused as exc:
            raise PublishRefused(str(exc)) from exc
    label = _check_label(label)
    qn = spec_qualified_name(label)
    folders = folders or [ADVISOR_FOLDER]
    bad = [f for f in folders if f not in (ADVISOR_FOLDER, PORTAL_FOLDER)]
    if bad:
        raise PublishRefused(f"Withdraw works on EA's and the Portal's consumer folders only, not {', '.join(bad)}.")
    doc = [f"# Withdraw report spec {label}\n"]
    doc += [_section("Remove Member from Collection", [("Element Id", qn), ("Collection Id", f)]) for f in folders]
    markdown = "\n".join(doc)
    return {"label": label, "qualified_name": qn, "folders": folders, "markdown": markdown,
            "digest": _digest(markdown),
            "steps": [f"remove {qn} from {f} (the spec element itself stays in Egeria)" for f in folders]}


def _digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()[:16]


def _run(markdown: str, directive: str, creds: Optional[Dict[str, str]], agent=None) -> Dict[str, Any]:
    if agent is None:
        from advisor.agents.dr_egeria_agent import get_dr_egeria_agent
        agent = get_dr_egeria_agent()
    raw = agent.execute(markdown, directive=directive, egeria_credentials=creds)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"success": False, "output": str(raw), "execution_errors": [{"message": "unreadable result"}]}


def _summarise(env: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("success", "output", "validation_errors", "execution_errors", "commands_total",
            "commands_succeeded", "commands_failed", "commands_detail", "auth_required", "auth_reason")
    return {k: env.get(k) for k in keep if k in env}


def _after_write() -> Dict[str, Any]:
    """A publish or withdraw changed Egeria: drop per-user listings and start a sync (public specs)."""
    from advisor import egeria_spec_restricted
    from advisor.egeria_spec_sync import SyncBusy, start_sync
    egeria_spec_restricted.invalidate()
    try:
        return {"started": True, "message": start_sync()["message"]}
    except SyncBusy as exc:
        return {"started": False, "message": f"{exc} The catalog shows this change after it finishes."}


def _step(plan: Dict[str, Any], step: str, creds, *, confirmed: bool, digest: Optional[str], agent) -> Dict[str, Any]:
    if step not in STEPS:
        raise PublishRefused(f"Step must be one of {', '.join(STEPS)}, not {step!r}.")
    if step == "preview":
        return {**plan, "step": "preview", "result": None}
    if step == "validate":
        env = _run(plan["markdown"], "validate", creds, agent)
        return {**plan, "step": "validate", "result": _summarise(env)}
    if not confirmed:
        raise PublishRefused("Confirm first: the document runs as you, and writes to Egeria.")
    if digest != plan["digest"]:
        raise PublishRefused("The document changed since you previewed it (the spec, or what Egeria holds); "
                             "preview it again before confirming.")
    env = _run(plan["markdown"], "process", creds, agent)
    out = {**plan, "step": "process", "result": _summarise(env)}
    if env.get("success"):
        out["after"] = _after_write()
    return out


def publish(creds: Optional[Dict[str, str]], label: str, *, step: str = "preview", visibility: str = "public",
            zones: Optional[List[str]] = None, also_portal: bool = False, confirmed: bool = False,
            digest: Optional[str] = None, agent=None) -> Dict[str, Any]:
    """One step of publishing `label`. Raises PublishRefused with words for a request that cannot run."""
    fs = _local_spec(_check_label(label))
    plan = build_publish_document(label, fs, visibility=visibility, zones=zones, also_portal=also_portal,
                                  held=held_in_egeria(creds, label))
    return _step(plan, step, creds, confirmed=confirmed, digest=digest, agent=agent)


def withdraw(creds: Optional[Dict[str, str]], label: str, *, step: str = "preview",
             folders: Optional[List[str]] = None, confirmed: bool = False, digest: Optional[str] = None,
             agent=None) -> Dict[str, Any]:
    """One step of withdrawing `label` from EA's (and optionally the Portal's) consumer folder."""
    plan = build_withdraw_document(label, folders=folders)
    return _step(plan, step, creds, confirmed=confirmed, digest=digest, agent=agent)
