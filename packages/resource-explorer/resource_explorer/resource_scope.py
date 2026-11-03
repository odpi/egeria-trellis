"""The selection record for what a repository publishes (brief 2a, explicit selection).

**Selecting a folder never implies publishing what is nested in it. Publishing is of named items, previewed,
plus the ancestors needed to hold them. Worthiness is a proposal, never a decision.**

A choice is RE's record (`resource_scope_events`, append-only), saved before and apart from any publish.
`sub_resources` stays the record of what was PUBLISHED; nothing here writes it. This module is the one place
that turns the two records plus the sub-resource survey into the view both panes draw (Curate's "what's in
it" and the Analysis pane's sub-resource panel) and the manifest the commit table shows, so the screen's
counts are the record's counts and never the DOM's.

Pure reads: building a view writes nothing and calls nothing outside the registry.
"""
from __future__ import annotations

import json

RESOURCE_TYPE = "repo"
SURVEY_KIND = "repo_sub_resource_survey"

#: The word on a row whose folder holds an included file but is not itself included.
CONTAINER_WORDS = "needed as a container · not an asset of its own"

#: The survey reason of a folder that is worthy ONLY because a worthy file lives in it (it has no merit of its
#: own). It is a holder, never a proposal. Read from the stored reason, so surveys stored before this rule
#: render the same without a re-survey.
HOLDER_ONLY_REASON = "container_for_worthy_file"


def _ancestors(locator: str) -> list[str]:
    from resource_explorer.surveyors.sub_surveyors import ancestor_folder_paths
    return ancestor_folder_paths(locator)


def candidates(registry, slug: str) -> dict[str, dict]:
    """locator -> {kind, label, reason} from the latest sub-resource survey (the candidate set at its
    current depth; nesting depth is a later brief)."""
    out: dict[str, dict] = {}
    for f in registry.query_findings(slug, SURVEY_KIND):
        try:
            detail = json.loads(f.get("detail_json") or "{}") or {}
        except (TypeError, ValueError):
            detail = {}
        out[f["check_name"]] = {"kind": detail.get("kind") or "folder", "label": f.get("label") or "",
                                "reason": f.get("summary") or "", "detail": detail}
    return out


def _published(registry, slug: str) -> dict[str, dict]:
    """locator -> {guid, when} for every row in `sub_resources` that has an Egeria GUID. `when` is the newest
    read-back proof for it, else the day it was cataloged."""
    from resource_explorer import repo_publish
    read_at: dict[str, str] = {}
    for p in registry.list_catalogue_commit_proofs(slug):
        if p["node_kind"] == repo_publish.NODE_SUB_RESOURCE and p["proof"] == repo_publish.P_SUB_RESOURCE:
            read_at[p["table_name"]] = p["read_at"]
    out = {}
    for r in registry.list_sub_resources(RESOURCE_TYPE, slug):
        if r.get("egeria_guid"):
            out[r["locator"]] = {"guid": r["egeria_guid"], "when": read_at.get(r["locator"]) or r.get("cataloged_at") or ""}
    return out


def build_view(registry, slug: str) -> dict:
    """The rows and the manifest, from the record.

    rows       one per candidate, plus a row for every container folder that is not a candidate and one for
               any locator that has a choice but is no longer in the survey. Each row carries its current
               choice, who and when, whether it is only a container, and what is published.
    manifest   files / folders / containers chosen and the left-out counts, from the newest event per
               locator; `chosen` is the locators the press sends (files and chosen folders, containers are
               added by the commit through the ancestor rule and listed in `container_locators`).
    """
    cands = candidates(registry, slug)
    scope = registry.current_resource_scope(RESOURCE_TYPE, slug)
    published = _published(registry, slug)

    def kind_of(loc: str) -> str:
        return cands[loc]["kind"] if loc in cands else (scope.get(loc) or {}).get("kind", "folder")

    included = {loc for loc, ev in scope.items() if ev["choice"] == "include"}
    chosen_files = sorted(loc for loc in included if kind_of(loc) == "file")
    chosen_folders = sorted(loc for loc in included if kind_of(loc) != "file")
    containers: set[str] = set()
    for f in chosen_files:
        containers.update(_ancestors(f))
    containers -= set(chosen_folders)

    locators = set(cands) | containers | set(scope)
    rows = []
    for loc in sorted(locators):
        c = cands.get(loc)
        ev = scope.get(loc) or {}
        choice = ev.get("choice", "")
        rows.append({
            "locator": loc, "kind": kind_of(loc),
            "label": (c or {}).get("label") or ("container" if loc in containers else "not in the latest survey"),
            "reason": (c or {}).get("reason", ""),
            "candidate": c is not None,
            "choice": choice, "source": ev.get("source", "") if choice else "",
            "proposal_rule": ev.get("proposal_rule", "") if choice else "",
            "by": ev.get("author", ""), "at": ev.get("changed_at", ""),
            "cleared": bool(ev) and not choice,
            "role": "container" if loc in containers else "",
            "proposed": bool(c) and c["label"] == "worthy" and not choice
                        and c["reason"] != HOLDER_ONLY_REASON,
            "holder_only": bool(c) and c["reason"] == HOLDER_ONLY_REASON,
            "published": published.get(loc),
        })

    cand_rows = [r for r in rows if r["candidate"]]
    left_out = [r for r in cand_rows if r["choice"] == "leave_out"]
    undecided = [r for r in cand_rows if not r["choice"]]
    manifest = {
        "files": len(chosen_files), "folders": len(chosen_folders), "containers": len(containers),
        "items": len(chosen_files) + len(chosen_folders) + len(containers),
        "chosen": sorted(chosen_files + chosen_folders), "container_locators": sorted(containers),
        "not_selected": sum(1 for r in undecided if not r["proposed"]),
        "proposals_not_accepted": sum(1 for r in undecided if r["proposed"]),
        "left_out": len(left_out),
        "published_earlier": len(published),
    }
    return {"rows": rows, "manifest": manifest, "proposals": [r["locator"] for r in rows if r["proposed"]]}


def chosen_locators(registry, slug: str) -> list[str]:
    """What a press publishes: the included files and folders, from the record (never from a request)."""
    return build_view(registry, slug)["manifest"]["chosen"]


#: Longest free text a choice may carry; over it the batch is refused with a sentence.
MAX_LOCATOR, MAX_REASON, MAX_RULE = 1024, 500, 100


FILE_TYPE_SENTENCE = "file types are a measurement, published as Egeria's profile annotations; they are not a choice"


def validate_events(registry, slug: str, events: list[dict]) -> list[dict]:
    """Check a batch against the record's own candidate set; return the clean events or raise ValueError.

    A folder or file locator must be a row of the view (a survey candidate, or a container folder that
    holds an included file), and its kind must match. File types are not a choice and are refused."""
    if not events:
        raise ValueError("no choices given")
    view = {r["locator"]: r for r in build_view(registry, slug)["rows"]}
    clean = []
    for e in events:
        kind = e.get("kind") or ""
        action = e.get("action") or "set"
        choice = e.get("choice") or ""
        if kind == "file_type":
            raise ValueError(FILE_TYPE_SENTENCE)
        if kind not in ("folder", "file"):
            raise ValueError(f"unknown kind {kind!r}")
        if action not in ("set", "clear"):
            raise ValueError(f"unknown action {action!r}")
        if action == "set" and choice not in ("include", "leave_out"):
            raise ValueError("a choice is include or leave_out (or clear it)")
        loc = e.get("locator", "")
        for name, val, cap in (("locator", loc, MAX_LOCATOR), ("reason", e.get("reason") or "", MAX_REASON),
                               ("proposal_rule", e.get("proposal_rule") or "", MAX_RULE)):
            if len(val) > cap:
                raise ValueError(f"the {name} is too long ({len(val)} characters; the most is {cap})")
        row = view.get(loc)
        if row is None:
            raise ValueError(f"{loc!r} is not a candidate of the sub-resource survey")
        if row["kind"] != kind:
            raise ValueError(f"{loc!r} is a {row['kind']}, not a {kind}")
        src = e.get("source") or "person"
        if src not in ("person", "proposal"):
            raise ValueError(f"unknown source {src!r}")
        clean.append({"locator": loc, "kind": kind, "choice": "" if action == "clear" else choice, "action": action,
                      "source": src, "proposal_rule": e.get("proposal_rule") or "", "reason": e.get("reason") or ""})
    return clean


def _not_checked_of(publisher) -> dict:
    """The publisher's `sub_resources_not_checked` for this call ({} when it has none to give)."""
    found = getattr(publisher, "sub_resources_not_checked", None)
    return dict(found) if isinstance(found, dict) else {}


def publish_chosen(registry, slug: str, *, github_url: str, asset_guid: str, curation_id: str, author: str,
                   locators: list[str], publisher=None) -> dict:
    """Publish exactly `locators` (the chosen files and folders) plus the ancestor folders a file needs, once
    each, and write one proof row per element BY GUID after a read of that GUID.

    Shared by the Curate commit and the Analysis pane's press, so the two cannot disagree about what a press
    sends. Local cataloguing (`sub_resources`) happens here, at publish time, and never at choice time.
    Nothing here can remove an element from Egeria: a row that was left out after it was published is
    simply not in `locators`. A second call finds each element by qualifiedName and creates nothing.

    Returns `{want, guids, counts, missing, ancestors, rename_counts}`; `counts` are the proof rows' counts."""
    from resource_explorer import repo_publish
    from resource_explorer.surveyors.egeria_publisher import EgeriaPublisher

    cands = candidates(registry, slug)
    scope = registry.current_resource_scope(RESOURCE_TYPE, slug)
    kind_of = lambda loc: cands[loc]["kind"] if loc in cands else (scope.get(loc) or {}).get("kind", "folder")  # noqa: E731
    want: dict[str, str] = {loc: kind_of(loc) for loc in locators}
    for loc, kind in list(want.items()):
        if kind == "file":
            for anc in _ancestors(loc):
                want.setdefault(anc, "folder")
    for loc in sorted(want):
        registry.catalog_sub_resource(RESOURCE_TYPE, slug, loc, want[loc], source_finding=SURVEY_KIND,
                                      detail=(cands.get(loc) or {}).get("detail") or None)
    publisher = publisher or EgeriaPublisher(registry=registry)
    guids = publisher.publish_sub_resources(slug, github_url, asset_guid, sorted(want))
    counts = repo_publish.record_sub_resource_proofs(
        registry, slug, curation_id, author, want, list(locators), guids,
        reader=lambda g: publisher._asset_maker.get_asset_by_guid(g, output_format="JSON"),
        not_checked=_not_checked_of(publisher))
    return {"want": want, "guids": guids, "counts": counts,
            "missing": [l for l in locators if l not in guids], "ancestors": len(want) - len(locators),
            "rename_counts": getattr(publisher, "rename_counts", None)}
