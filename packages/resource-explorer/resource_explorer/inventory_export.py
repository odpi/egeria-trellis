"""Export the resource inventory — everything needed to rebuild it from scratch.

Nothing today can reproduce the inventory if the store is lost. That is a gap on
its own, and it also blocks two planned pieces of work
(`docs/resource-vocabulary-rename-plan.md` §4): a pgvector wipe-and-reingest, and
the `project` → `resource` rename, whose only genuinely hard part is that slugs
are baked into live vector-store collection names. With an export, the wipe
becomes safe and the rename's hard part disappears — there is no data to migrate
if the data is being rebuilt.

**Registrations, not derivations.** Chunks, embeddings, survey results, code
symbols and containment trees are all rebuilt by re-ingesting; exporting them
would be exporting the expensive thing rather than the irreplaceable one. What
cannot be rebuilt is the human input: which resources someone chose to register,
what they were called, how they were grouped, and which investigation they belong
to.

**An empty export is indistinguishable from an empty inventory.** So every
section is counted in a manifest, and `verify()` checks the counts against the
sections. A truncated or partly-failed export should be loud rather than look
like a small estate.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES

#: 2 (TR-121): adds `open_data` (the dataset rows investigation members point at) and `refused_members`.
FORMAT_VERSION = 2
#: Versions verify() accepts. A v1 backup (before TR-121) is still a valid backup of what it holds; notes() says what
#: it lacks (W-121-1).
KNOWN_VERSIONS = (1, 2)
#: Manifest keys a v1 export never had.
_V2_KEYS = ("open_data", "refused_members")
V1_NOTE = "v1 export (before TR-121): open_data datasets not included"

# Kinds of resource in the inventory. `entity_type` elsewhere carries these same
# values -- "repo" is a KIND of resource, not an older word for one, which is why
# the rename does not touch those rows.
#
# The SURVEYED subset, not the full vocabulary: an inventory row exists only for
# a type with a registry table behind it. See resource_explorer/resource_types.py.
RESOURCE_KINDS = SURVEYED_RESOURCE_TYPES


def _plain(obj: Any) -> Any:
    """Best-effort conversion of a registry row to plain JSON-able data."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _plain(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def _sorted_by(rows: list[Any], key: str) -> list[dict]:
    out = [_plain(r) for r in rows]
    # Deterministic order so two exports of an unchanged inventory diff clean.
    # Without this, a re-export looks like a change and nobody reads the diff.
    return sorted(out, key=lambda r: str(r.get(key, "")))


def export_inventory(registry, *, generated_at: str = "") -> dict:
    """Everything needed to re-register the estate after a wipe.

    `generated_at` is passed in rather than read from the clock so an export is
    reproducible: two runs over an unchanged inventory should be byte-identical,
    which is what makes the diff meaningful.
    """
    resources = {
        "repo": _sorted_by(registry.list_all(), "slug"),
        "database": _sorted_by(registry.list_databases(), "slug"),
        "filesystem": _sorted_by(registry.list_filesystems(), "slug"),
    }

    investigations = []
    open_data: dict[str, dict] = {}
    refused: list[dict] = []
    for inv in registry.list_investigations(include_closed=True):
        row = _plain(inv)
        slug = row.get("slug", "")
        members = []
        for m in registry.list_investigation_members(slug):
            member = {"entity_type": m.get("entity_type"), "entity_slug": m.get("entity_slug")}
            if member["entity_type"] == OPEN_DATA_KIND:
                dataset, why = _open_data_row(registry, member["entity_slug"], open_data)
                if dataset is None:
                    # TR-121: never written dangling. Left out, and said, so verify() makes the export loud.
                    refused.append({"investigation": slug, **member, "reason": why})
                    continue
                open_data[member["entity_slug"]] = dataset
            members.append(member)
        row["members"] = sorted(members, key=lambda m: (str(m["entity_type"]), str(m["entity_slug"])))
        investigations.append(row)
    investigations.sort(key=lambda r: str(r.get("slug", "")))

    payload = {
        "format_version": FORMAT_VERSION,
        "generated_at": generated_at,
        "resources": resources,
        "orgs": _sorted_by(registry.list_groups(), "slug"),
        "servers": _sorted_by(registry.list_servers(), "slug"),
        "aliases": _sorted_by(registry.list_aliases(), "alias"),
        "investigations": investigations,
        # TR-121: the open-data datasets the investigations' members point at, as stored (only those: a dataset on a
        # work list alone is not a registration an investigation depends on), and the members left out, with why.
        "open_data": [open_data[k] for k in sorted(open_data)],
        "refused_members": sorted(refused, key=lambda r: (str(r["investigation"]), str(r["entity_slug"]))),
    }
    payload["manifest"] = _manifest(payload)
    return payload


#: The member kind TR-54 A1 adds for an imported dataset; its rows live in the `open_data` table, not in `resources`.
OPEN_DATA_KIND = "open_data"


def _open_data_row(registry, slug: str, seen: dict) -> tuple[dict | None, str]:
    """(the stored `open_data` row for `slug`, "") or (None, why it cannot be exported). Read only for a member that
    points at one, so a registry with no open-data members is never asked."""
    if slug in seen:
        return seen[slug], ""
    try:
        with registry._conn() as conn:
            row = conn.execute("SELECT * FROM open_data WHERE slug = ?", (slug,)).fetchone()
    except Exception as exc:  # noqa: BLE001 -- the table missing, or unreadable: say so, do not guess
        return None, (f"the open_data table could not be read ({type(exc).__name__}: {str(exc)[:120]}), so dataset "
                      f"{slug!r} cannot be exported; the member was left out rather than written dangling")
    if row is None:
        return None, (f"dataset {slug!r} is not in the open_data table, so it cannot be exported; the member was "
                      "left out rather than written dangling")
    return {k: row[k] for k in row.keys()}, ""


def _manifest(payload: dict) -> dict[str, int]:
    return {
        "repo": len(payload["resources"]["repo"]),
        "database": len(payload["resources"]["database"]),
        "filesystem": len(payload["resources"]["filesystem"]),
        "orgs": len(payload["orgs"]),
        "servers": len(payload["servers"]),
        "aliases": len(payload["aliases"]),
        "investigations": len(payload["investigations"]),
        "investigation_members": sum(len(i.get("members", [])) for i in payload["investigations"]),
        "open_data": len(payload.get("open_data", [])),
        "refused_members": len(payload.get("refused_members", [])),
    }


def verify(payload: dict) -> list[str]:
    """Problems with an export. Empty list means it is internally consistent.

    Returns findings rather than raising: a caller writing a backup wants to see
    everything wrong at once, not the first thing.
    """
    problems: list[str] = []
    version = payload.get("format_version")
    if type(version) is not int or version not in KNOWN_VERSIONS:
        problems.append(
            f"format_version {version!r}, expected one of {' or '.join(map(str, KNOWN_VERSIONS))}"
        )
    v1 = version == 1

    recomputed = _manifest(payload)
    stated = payload.get("manifest") or {}
    for key, count in recomputed.items():
        if v1 and key in _V2_KEYS:
            continue                       # a v1 export never counted these; notes() says what that means
        if stated.get(key) != count:
            problems.append(f"manifest {key}={stated.get(key)!r} but found {count}")

    if not any(recomputed[k] for k in RESOURCE_KINDS):
        # The failure this whole module is shaped around: an export that
        # captured nothing reads exactly like a correct export of an empty
        # estate, and only says so at restore time.
        problems.append("no resources of any kind — refusing to call this a valid inventory")

    known = {(m["entity_type"], m["entity_slug"])
             for kind, rows in payload["resources"].items()
             for m in ({"entity_type": kind, "entity_slug": r.get("slug")} for r in rows)}
    known |= {(OPEN_DATA_KIND, r.get("slug")) for r in payload.get("open_data", [])}
    for refused in payload.get("refused_members", []):
        problems.append(f"investigation {refused.get('investigation')!r}: member {refused.get('entity_type')} "
                        f"{refused.get('entity_slug')!r} refused at export: {refused.get('reason')}")
    for inv in payload["investigations"]:
        for member in inv.get("members", []):
            pair = (member.get("entity_type"), member.get("entity_slug"))
            if v1 and pair[0] == OPEN_DATA_KIND:
                continue                   # v1 never exported the datasets: said by notes(), not a v1 defect
            if pair not in known:
                problems.append(
                    f"investigation {inv.get('slug')!r} references unknown "
                    f"{pair[0]} {pair[1]!r}"
                )
    return problems


def notes(payload: dict) -> list[str]:
    """What an export that verifies does not hold (W-121-1): words for a reader, not problems."""
    if payload.get("format_version") != 1:
        return []
    members = sum(1 for inv in payload.get("investigations", []) for m in inv.get("members", [])
                  if m.get("entity_type") == OPEN_DATA_KIND)
    return [f"{V1_NOTE}; {members} open_data member(s) in it point at datasets it does not hold"]
