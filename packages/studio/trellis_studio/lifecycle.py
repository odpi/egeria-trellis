"""S3: versions, diff, promote / publish / withdraw, and the impact view, as the user, through pyegeria.authoring.

A plan is never taken from the browser. Every step rebuilds it on the server, as the user, from the same inputs
(a draft, or a saved draft's reference, plus folders), and "process" runs only when:
  - the rebuilt plan's digest equals the digest the user saw in preview (so nothing changed in between: not the
    draft, not the held element's published version), and the user confirmed; and
  - when the diff against the held version has breaking changes, the user confirmed those too.
pyegeria's own checks run on top (async_apply re-reads the target and refuses a changed one).

"Used by": pyegeria.authoring's used-by read (async_used_by, pyegeria 6.2.42), as the user. A failed read is said
in words and never claims "used by none"; a first publish of something Egeria doesn't hold yet says nothing uses
it yet.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from trellis_studio.authoring import _authoring, _run_sync, as_draft_dict, in_user_thread, parse_draft

USED_BY_NOT_READ = ("Not read for a withdraw: Studio doesn't list the Reports, sheets or folders that use this here. "
                    "This is not \"used by none\".")
USED_BY_UNPUBLISHED = "Nothing uses it yet: it isn't published."


def consumers() -> Dict[str, Any]:
    from pyegeria.authoring.serialize import DASHBOARD_FOLDER_PREFIX, SPEC_FOLDER_PREFIX
    a = _authoring()
    return {"consumers": list(a.KNOWN_CONSUMERS), "spec_prefix": SPEC_FOLDER_PREFIX,
            "sheet_prefix": DASHBOARD_FOLDER_PREFIX}


def _folders(kind: str, names: List[str]) -> List[str]:
    """Consumer names to folder qualified names; pyegeria checks them (unknown ones are refused in words)."""
    c = consumers()
    prefix = c["spec_prefix"] if kind == "spec" else c["sheet_prefix"]
    return [n if n.startswith(prefix) else prefix + n for n in names]


def _key(d) -> str:
    return d.label if d.kind == "spec" else d.display_name if d.kind == "report" else d.name


async def history(user, settings, kind: str, label: str) -> Dict[str, Any]:
    a = _authoring()
    return await in_user_thread(user, settings, lambda c: _run_sync(a.async_history(kind, label, c)))


async def published_versions(user, settings, label: str) -> Dict[str, Any]:
    """A spec's published version numbers (D14), read whole as the user, or pyegeria's refusal."""
    def fn(client):
        from pyegeria.authoring.publication import ordered_entries, published_versions as pv
        from pyegeria.authoring.versions import async_spec_versions
        current, hist = _run_sync(async_spec_versions(client, label))
        return {"label": label, "published_versions": pv(ordered_entries(current, hist))}
    return await in_user_thread(user, settings, fn)


def _diff_dict(a, b) -> Dict[str, Any]:
    out = _authoring().diff(a, b)
    return out.model_dump(mode="json") if hasattr(out, "model_dump") else out


#: The words pyegeria's async_load_held starts with when Egeria holds no such artifact. Used ONLY with a pyegeria
#: whose AuthoringRefused has no `answer` (before 5e38d483); a test pins it to the real refusal text.
HOLDS_NO_PREFIX = "Egeria holds no"


def refusal_state(exc: BaseException) -> str:
    """'absent' only when pyegeria says so; everything else is 'unknown' (S3 R2). pyegeria's machine-readable
    `answer` (absent | unknown | refused) decides; an older pyegeria without it falls back to its words."""
    answer = getattr(exc, "answer", None)
    if answer is not None:
        return "absent" if answer == "absent" else "unknown"
    return "absent" if str(exc).startswith(HOLDS_NO_PREFIX) else "unknown"


def _held_or_none(client, kind, label, notes: List[str]):
    """The held artifact as a draft, or None with words. A refusal is said; it is never taken as "not held" for a
    breaking-change check (the caller treats unknown as unknown)."""
    a = _authoring()
    try:
        return _run_sync(a.async_load_held(kind, label, client)), "read"
    except a.AuthoringRefused as exc:
        notes.append(str(exc))
        return None, refusal_state(exc)


async def diff(user, settings, *, draft: Optional[Dict[str, Any]] = None, kind: Optional[str] = None,
               label: Optional[str] = None, a_version: Optional[int] = None,
               b_version: Optional[int] = None) -> Dict[str, Any]:
    """Two modes: the draft against what Egeria holds now (as the user); or two published versions of a spec."""
    a = _authoring()
    if draft is not None:
        d = parse_draft(draft)

        def fn(client):
            notes: List[str] = []
            held, state = _held_or_none(client, d.kind, _key(d), notes)
            if held is None:
                return {"against": state, "changes": [], "breaking": None, "notes": notes}
            res = _diff_dict(held, d)
            res.update(against="held", notes=notes, held_version=held.base_version)
            return res
        return await in_user_thread(user, settings, fn)

    def fn2(client):
        from pyegeria.authoring.versions import async_spec_at_version
        fa = _run_sync(async_spec_at_version(client, label, a_version))
        fb = _run_sync(async_spec_at_version(client, label, b_version))
        res = _diff_dict(a.SpecDraft(label=label, format_set=fa), a.SpecDraft(label=label, format_set=fb))
        res.update(against=f"published version {a_version}", notes=[])
        return res
    return await in_user_thread(user, settings, fn2)


def _breaking(diff_out: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [c for c in diff_out.get("changes", []) if c.get("breaking")]


def _impact(client, d, plan: Dict[str, Any]) -> Dict[str, Any]:
    a = _authoring()
    notes: List[str] = []
    held, state = _held_or_none(client, d.kind, _key(d), notes)
    if held is not None:
        dd = _diff_dict(held, d)
        breaking: Optional[List[Dict[str, Any]]] = _breaking(dd)
        changes = dd.get("changes", [])
    elif state == "absent":
        breaking, changes = [], []
        notes.append("Not held in Egeria yet as far as you can see: this publish creates it.")
    else:
        breaking, changes = None, []   # unknown: confirmation is required, as for a breaking change
    try:
        used = {"available": True, **_run_sync(a.async_used_by(d.kind, _key(d), client))}
    except a.AuthoringRefused as exc:
        if state == "absent" and getattr(exc, "answer", None) == "absent":
            # Both reads agree Egeria holds nothing by this name (as this user): a first publish.
            used = {"available": False, "words": USED_BY_UNPUBLISHED}
        else:
            used = {"available": False, "words": f"The used-by read failed ({type(exc).__name__}): not \"none\"."}
    except Exception as exc:  # noqa: BLE001
        used = {"available": False, "words": f"The used-by read failed ({type(exc).__name__}): not \"none\"."}
    return {"held": state, "changes": changes, "breaking": breaking, "notes": notes, "used_by": used,
            "steps": plan.get("steps", []), "plan_notes": plan.get("notes", [])}


def _plan_public(plan: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("kind", "label", "qualified_name", "markdown", "digest", "steps", "notes", "published_version",
            "folders", "target")
    return {k: plan[k] for k in keep if k in plan}


def _build_plan(client, op: str, *, draft=None, ref=None, folders=(), kind=None, label=None):
    """The plan for op ("publish" | "promote" | "withdraw"), rebuilt as the user each time."""
    a = _authoring()
    if op == "withdraw":
        return None, a.withdraw_plan(kind, label, folders=_folders(kind, list(folders)))
    if op == "promote":
        store = a.EgeriaDraftStore(client, a.inprocess_runner(client))
        k, name = ref["kind"], ref["name"]
        d = _run_sync(store.async_load(client.user_id, k, name))
        if d is None:
            raise a.AuthoringRefused(f"You have no {k} draft named {name!r} in your private zone.")
        fl = _folders(k, list(folders)) if k != "report" else []
        return d, _run_sync(store.async_promotion_plan(client.user_id, k, name, folders=fl))
    d = parse_draft(draft)
    fl = _folders(d.kind, list(folders)) if d.kind != "report" else []
    return d, _run_sync(a.async_publish_plan(d, client, folders=fl))


async def plan(user, settings, op: str, **kw) -> Dict[str, Any]:
    def fn(client):
        d, p = _build_plan(client, op, **kw)
        out = {"op": op, "plan": _plan_public(p)}
        if d is not None:
            out["impact"] = _impact(client, d, p)
            if op == "promote":
                # The held element IS the private draft, so a diff against it says nothing: this is the draft's
                # first publication, not "no changes" (sd-dcat advisory).
                out["impact"].update(first_publication=True, changes=[], breaking=[])
            out["needs_breaking_confirm"] = out["impact"]["breaking"] is None or bool(out["impact"]["breaking"])
        else:
            out["impact"] = {"used_by": {"available": False, "words": USED_BY_NOT_READ}, "steps": p.get("steps", []),
                             "notes": ["Withdrawing removes it from these folders only; the element stays in "
                                       "Egeria and published versions stay readable (D14)."]}
            out["needs_breaking_confirm"] = True   # a consumer loses it: always confirm
        return out
    return await in_user_thread(user, settings, fn)


class StaleDigest(Exception):
    pass


async def apply(user, settings, op: str, *, step: str, digest: Optional[str], confirmed: bool,
                confirm_breaking: bool, **kw) -> Dict[str, Any]:
    a = _authoring()

    def fn(client):
        d, p = _build_plan(client, op, **kw)
        if step == "process":
            if not confirmed:
                raise a.AuthoringRefused("Read the steps and confirm before running them.")
            if digest != p.get("digest"):
                raise StaleDigest("The plan changed since you previewed it (the draft, or what Egeria holds). "
                                  "Preview it again before running it.")
            if op == "withdraw":
                if not confirm_breaking:
                    raise a.AuthoringRefused("Withdrawing removes it from consumers' folders. Confirm that first.")
            elif op == "publish":                   # promote is a first publication: it can't break a consumer
                imp = _impact(client, d, p)
                if (imp["breaking"] is None or imp["breaking"]) and not confirm_breaking:
                    raise a.AuthoringRefused("This changes or may change what consumers see (breaking changes, "
                                             "or the held version couldn't be read). Confirm that first.")
        run = a.inprocess_runner(client) if step in ("validate", "process") else None
        out = _run_sync(a.async_apply(p, step=step, run=run, client=client, confirmed=confirmed, digest=digest))
        return {"op": op, "step": step, "result": out.get("result"), "plan": _plan_public(out)}
    return await in_user_thread(user, settings, fn)
