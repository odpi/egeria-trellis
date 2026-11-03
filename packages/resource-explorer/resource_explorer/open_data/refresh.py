"""ExternalDataRefresh / external_change (OD4): look at a dataset on its portal again and say what changed there.

Each look reads the dataset (one call) and lists its distributions (one call), then differences them against the
LAST look kept for that dataset: the metadata fields (title, publisher, license, formats, size, modified) and the
distributions (added, removed, a changed size or URL). The look is kept as one `step_runs` row per dataset
(`external_change`, entity type `open_data`); no table is added.

What it never says: "no change" about something it has no earlier look at. The first look compares the fields with
what registration stored and says it has no earlier list of distributions; only a look that has both earlier lists
says "no change on their side". A portal that does not answer leaves the earlier look and the stored values alone.
"""
from __future__ import annotations

from resource_explorer.open_data.absence import NOT_REPORTED
from resource_explorer.open_data.store import OpenDataStore, now

STEP_KEY = "external_change"
ENTITY_TYPE = "open_data"
FIELDS = ("title", "publisher", "licence_id", "modified", "size")
NO_CHANGE = "no change on their side"
FIRST_LOOK_WORDS = "no change in the dataset's fields · first look at its distributions, nothing earlier to compare"


def refresh_price(n: int) -> dict:
    return {"datasets": n, "calls": n * 2,
            "line": f"{n} dataset{'s' if n != 1 else ''} × 2 calls (the dataset, its distributions) = {n * 2} calls"}


def _flags(lic: dict) -> str:
    return ", ".join(f"{k}={lic.get(k, NOT_REPORTED)}" for k in ("attribution", "share_alike", "commercial"))


def snapshot_of(row: dict, dists: list[dict]) -> dict:
    return {"fields": {"title": row["title"], "publisher": row["publisher"], "licence_id": row["licence_id"],
                       "licence_flags": _flags(row["licence"]), "modified": row["modified"],
                       "formats": row["formats"], "size": row["size"]},
            "distributions": sorted(({"key": str(d.get("id") if d.get("id") != NOT_REPORTED else d.get("name")),
                                      "name": d.get("name"), "format": d.get("format"), "url": d.get("url"),
                                      "size": d.get("size")} for d in dists), key=lambda d: d["key"])}


def stored_snapshot(ds: dict) -> dict:
    """What registration (or the last scout) stored, as fields only: used when no look has been kept yet."""
    return {"fields": {"title": ds["title"], "publisher": ds["publisher"], "licence_id": ds["licence_id"],
                       "licence_flags": _flags(ds["licence"]), "modified": ds["modified"], "formats": ds["formats"],
                       "size": ds["size"]}, "distributions": None}


def diff(old: dict, new: dict) -> list[dict]:
    """[{what, before, after, words}] between two snapshots. Pure. A distributions list that was never held
    (None) is not diffed: it is the first look."""
    out = []
    for k, after in new["fields"].items():
        before = old["fields"].get(k, NOT_REPORTED)
        if str(before) != str(after):
            out.append({"what": k, "before": before, "after": after, "words": f"{k}: {before} → {after}"})
    if old.get("distributions") is not None:
        before = {d["key"]: d for d in old["distributions"]}
        after = {d["key"]: d for d in new["distributions"]}
        for key in sorted(after.keys() - before.keys()):
            out.append({"what": "distribution", "before": None, "after": after[key]["name"],
                        "words": f"distribution added: {after[key]['name']}"})
        for key in sorted(before.keys() - after.keys()):
            out.append({"what": "distribution", "before": before[key]["name"], "after": None,
                        "words": f"distribution removed: {before[key]['name']}"})
        for key in sorted(before.keys() & after.keys()):
            for attr in ("url", "size", "format"):
                if str(before[key][attr]) != str(after[key][attr]):
                    out.append({"what": "distribution", "before": before[key][attr], "after": after[key][attr],
                                "words": f"distribution {after[key]['name']}: {attr} {before[key][attr]} → {after[key][attr]}"})
    return out


def _dataset_id(ds: dict) -> str:
    rec = ds.get("record") or {}
    return rec.get("name") or rec.get("id") or str(ds["identifier"]).split(":", 1)[-1]


def refresh_dataset(store: OpenDataStore, ds: dict, *, transport, registry=None) -> dict:
    from resource_explorer.open_data.service import portal_adapter

    registry = registry or store.registry
    base = {"slug": ds["slug"], "title": ds["title"], "outcome": "could_not_refresh", "words": "", "changes": []}
    last = registry.query_step_runs(slug=ds["slug"], step_key=STEP_KEY, entity_type=ENTITY_TYPE, limit=1)
    old = ((last[0].get("metrics") or {}).get("snapshot") if last else None) or stored_snapshot(ds)
    problem = ""
    for portal_slug in ds["portals"]:
        portal = store.get_portal(portal_slug)
        if not portal:
            problem = f"portal '{portal_slug}' is no longer registered"
            continue
        try:
            adapter = portal_adapter(portal, transport)
            row = adapter.describe(_dataset_id(ds))
            dists = adapter.distributions(_dataset_id(ds))
        except Exception as exc:
            problem = f"could not read from {portal['display_name']}: {exc}"[:240]
            continue
        new = snapshot_of(row, dists)
        changes = diff(old, new)
        first = old.get("distributions") is None
        looked = now()
        store.update_scouted(ds["slug"], row, looked)
        words = (f"changed on their side · {len(changes)} change{'s' if len(changes) != 1 else ''}: "
                 + "; ".join(c["words"] for c in changes)) if changes else (FIRST_LOOK_WORDS if first else NO_CHANGE)
        out = {**base, "outcome": "changed" if changes else ("first_look" if first else "unchanged"),
               "words": words, "changes": changes, "looked_at": looked, "portal": portal_slug}
        registry.record_step_run(ds["slug"], STEP_KEY, looked, entity_type=ENTITY_TYPE, source="resource-explorer",
                                 executor="local", metrics={**out, "snapshot": new})
        return out
    return {**base, "words": problem or "no portal to read from"}


def refresh_work_list(store: OpenDataStore, work_list_slug: str, *, transport, registry=None) -> dict:
    datasets = store.datasets_in_work_list(work_list_slug)
    if not datasets:
        from resource_explorer.open_data.service import NotFound

        raise NotFound(f"No open data in work list '{work_list_slug}'.")
    rows = [refresh_dataset(store, ds, transport=transport, registry=registry) for ds in datasets]
    count = lambda o: sum(1 for r in rows if r["outcome"] == o)          # noqa: E731
    return {"work_list": work_list_slug, "price": refresh_price(len(datasets)), "rows": rows,
            "changed": count("changed"), "unchanged": count("unchanged"), "first_look": count("first_look"),
            "could_not_refresh": count("could_not_refresh"), "ran_at": now()}
