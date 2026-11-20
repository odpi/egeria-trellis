"""Curate's review of a file system's proposals (C3, walk D3 / F-2): a person confirms or rejects each one.

Two lists, both RE's own records (nothing here calls Egeria):

* **folder descriptions**: the newest walk's `folder_narratives` (each README-type file's title and first paragraph,
  `walk_extensions.folder_narratives`), one proposal per narrative file;
* **data-class matches**: the newest `file_data_class_match` run's established matches (a profiled column that matches a
  data class Egeria holds), one proposal per file, column and class.

**The verdict store** is `resource_scope_events` (the signed, append-only selection record Curate's other choices already
use), not a new table. Its `locator` is free text, so a proposal's key (a file path, a column and a class) fits; its
`kind` says which list (`fs_folder_description`, `fs_data_class`, registered in `ProjectRegistry.OTHER_CHOICE_KINDS`,
so `current_resource_scope` never counts them as scope); `choice` is `confirm` or `reject`; `author` and `changed_at`
sign it; `proposal_rule` carries the run that proposed it (the walk's run id, or the match run's time). The newest row
for a key is the verdict; every earlier one stays. The database's own confirm/override store is
`catalogue_scope_events`, keyed by schema and table, which cannot hold a file path and a column: that is why it is not
reused.

**A re-run keeps verdicts** whose key is proposed again (the key holds no run id). A verdict whose proposal is no longer
proposed is listed as "no longer proposed", with who gave it and which run proposed it: never dropped.

A confirmed folder description stays in RE: it is not written to Egeria. A confirmed data-class match is written by
`EgeriaFileSystemSurveyor.publish_confirmed_data_classes`.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

KIND_FOLDER = "fs_folder_description"
KIND_CLASS = "fs_data_class"
KINDS = (KIND_FOLDER, KIND_CLASS)
CONFIRM, REJECT = "confirm", "reject"
VERDICTS = (CONFIRM, REJECT)

#: What the band says about the verdict whose proposal a later run did not make again.
GONE_WORDS = "no longer proposed"
#: One line under the band: where a confirmed proposal goes (TR-71: this is the only explanation).
STAYS_WORDS = "Confirmed descriptions stay in Resource Explorer; confirmed data classes go to Egeria only when you press Send."


class ReviewRefused(ValueError):
    """A verdict this review cannot record (unknown kind, a key nobody proposes now)."""


# ── the proposals ───────────────────────────────────────────────────────────────────────────────────────────────

def folder_proposals(registry, slug: str) -> tuple[str, list[dict]]:
    """(the walk run that proposed them, one proposal per narrative file with something to say), from the newest stored
    walk that looked for narratives. `("", [])` when no walk has."""
    from resource_explorer.surveyors.filesystem import narrative_docs

    got = narrative_docs.newest_narratives(registry, slug)
    if not got:
        return "", []
    run, entries = got
    out = []
    for e in entries:
        key = str(e.get("file") or "")
        if not key or not (e.get("title") or e.get("description")):
            continue
        out.append({"key": key, "folder": str(e.get("folder") or ""), "title": str(e.get("title") or ""),
                    "description": str(e.get("description") or ""), "file": key})
    return run, out


def class_proposals(registry, slug: str) -> tuple[str, list[dict], int]:
    """(the match run's time, one proposal per established match, matches the run did not keep), from the newest
    `file_data_class_match` run. `("", [], 0)` when it never ran."""
    from resource_explorer.surveyors.filesystem import file_class_match as fcm

    rows = registry.query_step_runs(slug=slug, step_key=fcm.STEP_KEY, entity_type="filesystem", limit=1)
    m = (rows[0].get("metrics") or {}) if rows else {}
    if not m:
        return "", [], 0
    out = []
    for d in m.get("matches") or []:
        if not d.get("established"):
            continue
        cls = d.get("matched_qualified_name") or d.get("matched_guid") or ""
        file_path, column = str(d.get("file_path") or ""), str(d.get("column") or "")
        if not (file_path and column and cls):
            continue
        out.append({"key": f"{file_path}::{column}::{cls}", "file": file_path, "column": column,
                    "data_class": str(d.get("matched_display_name") or cls), "data_class_guid": str(d.get("matched_guid") or ""),
                    "confidence": d.get("confidence"), "privacy_relevant": bool(d.get("privacy_relevant"))})
    return str(m.get("ran_at") or ""), out, int(m.get("matches_not_kept") or 0)


# ── the verdicts ────────────────────────────────────────────────────────────────────────────────────────────────

def current_verdicts(registry, slug: str) -> dict[str, dict[str, dict]]:
    """kind -> key -> the newest verdict row (who, when, which choice, which run proposed it)."""
    out: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
    for ev in registry.list_resource_scope_events("filesystem", slug):
        if ev.get("kind") in out and ev.get("choice") in VERDICTS:
            out[ev["kind"]][ev["locator"]] = ev
    return out


def _verdict_view(ev: dict | None) -> dict | None:
    if not ev:
        return None
    return {"verdict": ev["choice"], "by": ev["author"], "at": ev["changed_at"], "run": ev.get("proposal_rule") or ""}


def _section(kind: str, run: str, proposals: list[dict], verdicts: dict[str, dict], *, not_kept: int = 0) -> dict:
    items = [{**p, "listed": True, "verdict": _verdict_view(verdicts.get(p["key"]))} for p in proposals]
    here = {p["key"] for p in proposals}
    gone = [{"key": k, "listed": False, "words": GONE_WORDS, "verdict": _verdict_view(ev),
             **_gone_fields(kind, k)} for k, ev in verdicts.items() if k not in here]
    confirmed = sum(1 for i in items if (i["verdict"] or {}).get("verdict") == CONFIRM)
    rejected = sum(1 for i in items if (i["verdict"] or {}).get("verdict") == REJECT)
    return {"kind": kind, "run": run, "items": items, "gone": gone, "not_kept": not_kept,
            "proposed": len(items), "confirmed": confirmed, "rejected": rejected,
            "to_review": len(items) - confirmed - rejected}


def _gone_fields(kind: str, key: str) -> dict:
    """What a vanished proposal can still say about itself: its key's own parts."""
    if kind == KIND_CLASS:
        file_path, _, rest = key.partition("::")
        column, _, cls = rest.partition("::")
        return {"file": file_path, "column": column, "data_class": cls}
    return {"file": key, "folder": key.rpartition("/")[0], "title": "", "description": ""}


def build_review(registry, slug: str) -> dict:
    """Both lists with each proposal's verdict, and the counts the collapsed band reads. Read-only."""
    verdicts = current_verdicts(registry, slug)
    f_run, f_items = folder_proposals(registry, slug)
    c_run, c_items, c_not_kept = class_proposals(registry, slug)
    folders = _section(KIND_FOLDER, f_run, f_items, verdicts[KIND_FOLDER])
    classes = _section(KIND_CLASS, c_run, c_items, verdicts[KIND_CLASS], not_kept=c_not_kept)
    return {"folders": folders, "classes": classes,
            "to_review": folders["to_review"] + classes["to_review"],
            "confirmed": folders["confirmed"] + classes["confirmed"],
            "proposed": folders["proposed"] + classes["proposed"], "stays": STAYS_WORDS}


def save_verdict(registry, slug: str, *, kind: str, key: str, verdict: str, author: str) -> dict:
    """Record one signed verdict. Refuses a kind this review does not have, a verdict that is not confirm or reject,
    and a key nobody proposes now (a verdict is on a proposal that stands). Returns the section the verdict is in."""
    if kind not in KINDS:
        raise ReviewRefused(f"'{kind}' is not something this review holds")
    if verdict not in VERDICTS:
        raise ReviewRefused(f"a verdict is one of {', '.join(VERDICTS)}")
    if kind == KIND_FOLDER:
        run, items = folder_proposals(registry, slug)
    else:
        run, items, _ = class_proposals(registry, slug)
    if key not in {p["key"] for p in items}:
        raise ReviewRefused(f"'{key}' is not proposed now")
    registry.append_resource_scope_event("filesystem", slug, locator=key, kind=kind, choice=verdict, source="proposal",
                                         proposal_rule=run, author=author)
    review = build_review(registry, slug)
    return review["folders"] if kind == KIND_FOLDER else review["classes"]


def confirmed_classes(registry, slug: str) -> tuple[str, list[dict]]:
    """(the match run, the proposals a person confirmed and the newest run still proposes). A rejected, unreviewed or
    no-longer-proposed match is never in it."""
    run, items, _ = class_proposals(registry, slug)
    verdicts = current_verdicts(registry, slug)[KIND_CLASS]
    return run, [{**p, "verdict": _verdict_view(verdicts[p["key"]])} for p in items
                 if (verdicts.get(p["key"]) or {}).get("choice") == CONFIRM]
