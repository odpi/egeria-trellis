"""Batch import/export of resources as CSV — an inventory that is also a scorecard.

Registering resources one at a time does not scale to the corpus sizes that
actually surface bugs: of the nine repos here with a derived homepage, three
exhibited shapes nobody anticipated (a meta-refresh stub, a homepage pointing at
the code forge, a dead domain). Breadth is the limiting factor, so loading it has
to be cheap.

**The one rule that keeps this honest: import reads intent, never state.**

Two kinds of column live in this file and they have opposite lifecycles:

  * *Intent* — what a human decided: which resources to track, what group they
    belong to, what disposition they have been given. Stable, hand-edited,
    meaningful to re-apply.
  * *Observed state* — what RE found: whether something is registered, cataloged
    in Egeria, when it was last surveyed. Derived, changes without anyone
    touching the file, and already has a source of truth in the registry.

Every observed column is prefixed `status_` and is **ignored on import**. Without
that rule the format is ambiguous in a way that cannot be resolved later: is
`status_surveyed_at=2026-08-01` a record of something that happened, or an
instruction to make it so? Worse, an export taken on Tuesday still asserts
`status_cataloged=yes` on Friday after Egeria was reset — a file that looks like
a record while being wrong. Reading state back in would let that fiction write
itself into the registry.

So the export is a superset of the import: it round-trips, it is reviewable in a
spreadsheet, it can be sliced by group to ask "did every ASF repo fail homepage
derivation?" — and re-importing it is exactly as safe as re-importing the list
you started from.

Disposition is deliberately on the *intent* side even though it is also current
state, because it is a human judgement rather than an observation: bulk-tagging a
cohort as `tracking` is a real thing to want, and disposition is keyed by URL so
it can be set before a resource is ever registered.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from resource_explorer.resource_types import SURVEYED_RESOURCE_TYPES

log = logging.getLogger(__name__)

#: Resource types a batch intent file may name. The SURVEYED subset, not the
#: full vocabulary (resource_explorer/resource_types.py) — a batch row registers
#: a resource, and only these have somewhere to be registered.
RESOURCE_TYPES = SURVEYED_RESOURCE_TYPES

# Columns a human fills in. Only `resource_type` and `address` are required.
INTENT_COLUMNS = (
    "resource_type",        # repo | database | filesystem
    "address",              # the natural key — see resource_key()
    "display_name",
    "group",
    "subpath",              # repos only: monorepo sub-project path
    "server",               # databases only: slug of a REGISTERED db server (the credential comes from it)
    "connection_ref",       # databases only: a reference NAME for a secrets entry, never a value
    "disposition",
    "disposition_reason",
    "notes",
)

# Columns RE writes and never reads back. The prefix is the contract.
# NOTE: `status_last_published_at` is a bare claim (the newest publish row's time). After an Egeria reset it can
# name a publish whose elements no longer exist, and the CSV does not say so: the layout is unchanged on purpose
# (importers depend on it). The screens read the `egeria_reset` marker; this export does not.
STATUS_COLUMNS = (
    "status_registered",      # is it in RE's registry at all
    "status_slug",            # what RE called it, once registered
    "status_cataloged",       # has an Egeria asset GUID
    "status_egeria_link",     # ok | stale — see egeria_linkage.py
    "status_last_surveyed_at",
    "status_last_published_at",
    "status_lifecycle",       # active | archived | ...
    "status_indexed",         # has RAG collections
    "status_in_scope",        # in scope / not in scope of the investigation the export names
    "status_fit",             # the fit headline against that investigation's lens, in words
)

ALL_COLUMNS = INTENT_COLUMNS + STATUS_COLUMNS

# Kinds a CSV row can register. Databases became importable in the CSV in/out
# slice: a database row names WHERE its credential comes from (`server`, a
# registered db server, or `connection_ref`, a reference name) and never carries
# one. File systems stay exported-only: their mount points are meaningful on one
# machine, and there is no file-system import path yet.
IMPORTABLE_TYPES = ("repo", "database")

# -- Credentials never appear in a CSV, in or out --------------------------------
#
# Owner ruling 2026-10-01/02. The rule is enforced by shape, not by trust: a
# column whose NAME looks like a credential is never read on import (it is not in
# INTENT_COLUMNS, so no code path reaches its value) and is named, once, in the
# preview; and no column of that shape may be in ALL_COLUMNS, so no export can
# write one. `connection_ref` is the one exception by name: it is a reference
# NAME for a secrets entry, validated below to be a plain name and never a value.
import re as _re

_CREDENTIAL_COLUMN_RE = _re.compile(
    r"(passw|pwd|passphrase|secret|credential|token|api[_-]?key|private[_-]?key"
    r"|connection[_-]?string|conn[_-]?str|(^|[_-])dsn($|[_-]))", _re.IGNORECASE)

#: A reference NAME: letters, digits, dot, dash, underscore. Not a connection
#: string, not user:pass@host, not a URL.
_REF_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")


def is_credential_column(name: str) -> bool:
    """True for a column named like a credential. `connection_ref` is never one."""
    n = (name or "").strip().lower()
    if n == "connection_ref":
        return False
    return bool(_CREDENTIAL_COLUMN_RE.search(n))


def assert_no_credential_columns(columns) -> None:
    """Raise if any column in `columns` is named like a credential."""
    bad = [c for c in columns if is_credential_column(c)]
    if bad:
        raise AssertionError(
            f"credential-like column(s) {bad} may not be in a CSV (in or out); "
            "a CSV names where a credential comes from, never the credential")


assert_no_credential_columns(ALL_COLUMNS)


def normalize_github_url(url: str) -> str:
    """The same normalization registry.get_by_github_url() applies.

    Duplicated deliberately rather than imported: this must work on a CSV row
    before any registry lookup happens, so that dedup within the file itself
    (the same repo listed twice with and without .git) is caught too.
    """
    return (url or "").strip().lower().rstrip("/").removesuffix(".git")


def github_org_from_url(address: str) -> str:
    """The org/user name if this URL names a whole account rather than one repo.

    A repo URL has two path segments (`github.com/apache/airflow`); an account
    has one (`github.com/apache`). People list both — a foundation's page is the
    obvious thing to copy when the point is "everything these people publish" —
    and treating an account URL as a repo simply fails to fetch, which surfaces
    as an unexplained empty result rather than "that is an organisation".

    Returns "" when the URL is a repo, is not GitHub, or is neither.
    """
    from urllib.parse import urlparse

    u = (address or "").strip()
    if not u:
        return ""
    parsed = urlparse(u if "://" in u else f"https://{u}")
    if "github.com" not in (parsed.netloc or "").lower():
        return ""
    parts = [seg for seg in (parsed.path or "").split("/") if seg]
    # /orgs/<name> is GitHub's own canonical URL for an organisation and is what
    # the browser address bar shows on an org page, so it is at least as likely
    # to be pasted as the short form.
    if len(parts) == 2 and parts[0].lower() == "orgs":
        return parts[1].removesuffix(".git")
    if len(parts) != 1:
        return ""
    name = parts[0].removesuffix(".git")
    # Not every one-segment path is an account: these are GitHub's own pages.
    if name.lower() in {"orgs", "topics", "search", "explore", "features",
                        "marketplace", "sponsors", "settings", "notifications"}:
        return ""
    return name


def resource_key(resource_type: str, address: str) -> str:
    """Stable identity for a resource, independent of RE's slug.

    The slug is RE's own name for something and is derived, so keying an
    interchange format on it would break the moment a repo is registered under a
    different slug (a monorepo sub-project, say). The address is what the user
    actually knows.
    """
    if resource_type == "repo":
        return normalize_github_url(address)
    # Databases (host:port/name) and filesystems (mount point) are already
    # natural keys; only case and trailing separators need settling.
    return (address or "").strip().rstrip("/").lower()


@dataclass
class ImportRow:
    resource_type: str
    address: str
    display_name: str = ""
    group: str = ""
    subpath: str = ""
    disposition: str = ""
    disposition_reason: str = ""
    notes: str = ""
    server: str = ""                   # databases: slug of a registered db server
    connection_ref: str = ""           # databases: a reference NAME, never a value
    line: int = 0                      # 1-based CSV line, for error messages
    errors: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return resource_key(self.resource_type, self.address)


def describe_skipped(row: "ImportRow", max_value: int = 80) -> str:
    """One line naming where a skipped row was and what was on it.

    The line number alone is enough to find it in an editor; the value is what
    makes the problem obvious without going and looking. In a 500-row file the
    difference between "line 312: not a URL" and
    `line 312: "htps://github.com/a/b" — not a URL` is the difference between a
    hunt and a fix.

    Truncated because a malformed CSV row can be enormous — a whole record
    collapsed into one cell — and one bad row must not flood the report.
    """
    value = (row.address or "").strip()
    if not value:
        shown = "(empty)"
    elif len(value) > max_value:
        shown = f'"{value[:max_value]}…"'
    else:
        shown = f'"{value}"'
    return f"line {row.line}: {shown} — {'; '.join(row.errors)}"


@dataclass
class ImportPlan:
    """What a file would do, computed before anything is written."""
    to_register: list[ImportRow] = field(default_factory=list)
    already_registered: list[ImportRow] = field(default_factory=list)
    unsupported_type: list[ImportRow] = field(default_factory=list)
    invalid: list[ImportRow] = field(default_factory=list)
    duplicate_in_file: list[ImportRow] = field(default_factory=list)
    #: New database rows that name neither a `server` nor a `connection_ref`.
    #: They are candidates ("needs a person: name a server or a credential") and
    #: are NOT importable until one is chosen in the preview; they count as "new".
    needs_person: list[ImportRow] = field(default_factory=list)
    #: Intent changes a file proposes for rows that are already registered.
    #: Shown, never applied until the person confirms them.
    proposed_changes: list["ProposedChange"] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (len(self.to_register) + len(self.needs_person)
                + len(self.already_registered)
                + len(self.unsupported_type) + len(self.invalid)
                + len(self.duplicate_in_file))


@dataclass
class ProposedChange:
    """One intent change a file proposes for an already-registered row."""
    line: int
    resource_type: str
    key: str
    slug: str
    field: str            # "group" | "disposition"
    current: str
    proposed: str
    blocked: str = ""     # non-empty = cannot be applied, and why (e.g. unknown group)


_VALID_DISPOSITIONS = {"undecided", "tracking", "investigating", "ignored", "abandoned", "using"}


def parse_csv_text(text: str) -> list[ImportRow]:
    """Parse a pasted/uploaded list — CSV, or one address per line.

    Accepting both is not indulgence: the two natural ways to arrive here are a
    spreadsheet export and a list someone pasted out of a wiki, and rejecting the
    second would push people back to registering one at a time. A file with no
    delimiter and no recognised header is treated as one address per line; blank
    lines and `#` comments are skipped so an annotated list still works.
    """
    import io

    lines = [ln for ln in (text or "").splitlines()]
    meaningful = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not meaningful:
        return []

    header = meaningful[0].lower()
    looks_like_csv = "," in header and ("address" in header or "resource_type" in header)
    if looks_like_csv:
        return _parse_rows(csv.DictReader(io.StringIO("\n".join(meaningful))))

    # Plain list. Keep the original line numbers so error messages point at the
    # file the user is looking at, not at the filtered subset.
    rows: list[ImportRow] = []
    for idx, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        row = ImportRow(resource_type="repo", address=line, line=idx)
        if " " in line:
            row.errors.append("not a URL (contains a space) — if this is a CSV, "
                              "it needs an 'address' column header")
        elif "," in line:
            # Reached plain-list mode with comma-separated content, which means
            # the header sniff failed — an exported file whose header row was
            # edited or dropped is the likely cause. Without this the whole row
            # is treated as one URL and sent to GitHub, coming back as
            # "unreachable", which points at the network instead of the header.
            row.errors.append("looks like a CSV row, but the file has no "
                              "recognised header — the first line must include "
                              "an 'address' column")
        rows.append(row)
    return rows


def parse_csv(path: str | Path) -> list[ImportRow]:
    """Read intent rows. Unknown columns are ignored, not rejected.

    Ignoring rather than rejecting is what lets an exported scorecard — which
    carries every status_ column — be handed straight back to import without
    editing. That round-trip is the whole point of the prefix convention.
    """
    rows: list[ImportRow] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return rows
        known = {c.strip().lower() for c in reader.fieldnames if c}
        if "address" not in known:
            raise ValueError(
                "CSV has no 'address' column. Required columns are resource_type and "
                f"address; optional: {', '.join(c for c in INTENT_COLUMNS[2:])}.")
        return _parse_rows(reader)


def _build_row(raw: dict, line: int, *, strict: bool = False) -> ImportRow:
    """One CSV record -> ImportRow. Reads INTENT columns by name and nothing else:
    a credential-like column in the file is never looked at, because no name here
    refers to one."""
    get = lambda k: (raw.get(k) or "").strip()  # noqa: E731
    rtype = get("resource_type").lower()
    row = ImportRow(
        resource_type=rtype if (rtype or strict) else "repo",
        address=get("address"),
        display_name=get("display_name"),
        group=get("group"),
        subpath=get("subpath"),
        disposition=get("disposition").lower(),
        disposition_reason=get("disposition_reason"),
        notes=get("notes"),
        server=get("server"),
        connection_ref=get("connection_ref"),
        line=line,
    )
    if not row.address:
        row.errors.append("no address")
    if strict and not row.resource_type:
        row.errors.append("no resource_type")
    elif row.resource_type not in RESOURCE_TYPES:
        row.errors.append(
            f"unknown resource_type '{row.resource_type}' "
            f"(expected one of {', '.join(RESOURCE_TYPES)})")
    if row.disposition and row.disposition not in _VALID_DISPOSITIONS:
        row.errors.append(f"unknown disposition '{row.disposition}'")
    if row.connection_ref and not _REF_NAME_RE.match(row.connection_ref):
        # A reference is a NAME. Anything with '@', ':', '/', '=' or spaces looks
        # like a connection string or a value, and a value never belongs here.
        row.errors.append(
            "connection_ref must be a reference name (letters, digits, '.', '-', '_'), "
            "never a connection string or a value")
    if row.resource_type == "repo" and row.address and "github.com" not in row.address.lower():
        # Not fatal — GitHub Enterprise hosts are legitimate — but worth
        # saying, since a typo'd URL otherwise fails much later with a 404
        # during import, one repo at a time.
        log.debug("row %d: %s is not a github.com URL", line, row.address)
    return row


def _parse_rows(reader) -> list[ImportRow]:
    """Shared row loop for the file and text entry points, so validation cannot
    differ between the CLI and the UI."""
    # start=2: line 1 is the header
    return [_build_row(raw, i) for i, raw in enumerate(reader, start=2)]


@dataclass
class ParsedFile:
    """A CSV read for the web preview: the rows, and what the header said."""
    rows: list[ImportRow] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    refused: str = ""                                   # non-empty = the file is refused
    missing: list[str] = field(default_factory=list)    # required columns not in the header
    unknown_columns: list[str] = field(default_factory=list)
    status_columns: list[str] = field(default_factory=list)
    credential_columns: list[str] = field(default_factory=list)


REQUIRED_COLUMNS = ("resource_type", "address")


def parse_csv_strict(text: str) -> ParsedFile:
    """Read a file for the web preview, strictly.

    Unlike `parse_csv_text` (which also takes a bare URL list and defaults a
    missing resource_type to repo), this wants a header with both required
    columns, because the preview's job is to say exactly what the file is. A
    missing required column REFUSES the file, naming the column and showing the
    header row that was found. Line numbers are the file's own physical lines
    (comments and blank lines still count).
    """
    import io

    out = ParsedFile()
    text = (text or "").lstrip("\ufeff")
    lines = text.splitlines()
    # Blank out comment lines but keep them, so csv.reader's line_num stays the
    # physical line number in the file the person is looking at.
    cleaned = ["" if ln.lstrip().startswith("#") else ln for ln in lines]
    reader = csv.reader(io.StringIO("\n".join(cleaned)))
    header_raw = None
    records: list[tuple[int, list[str]]] = []
    for rec in reader:
        if not any((c or "").strip() for c in rec):
            continue
        if header_raw is None:
            header_raw = rec
            continue
        records.append((reader.line_num, rec))
    if header_raw is None:
        out.refused = "The file is empty: it has no header row and no rows."
        return out

    names = [(c or "").strip() for c in header_raw]
    out.header = names
    lowered = [n.lower() for n in names]
    out.missing = [c for c in REQUIRED_COLUMNS if c not in lowered]
    if out.missing:
        out.refused = (
            f"The file is missing the required column(s): {', '.join(out.missing)}. "
            f"The header row found was: {', '.join(n or '(blank)' for n in names)}.")
        return out

    known = set(INTENT_COLUMNS)
    seen: set[str] = set()
    for n, low in zip(names, lowered):
        if not low or low in seen:
            continue
        seen.add(low)
        if low in known:
            continue
        if low.startswith("status_"):
            out.status_columns.append(n)
        elif is_credential_column(low):
            out.credential_columns.append(n)
        else:
            out.unknown_columns.append(n)

    for line_no, rec in records:
        raw: dict[str, str] = {}
        for low, cell in zip(lowered, rec):
            if low and low in known and low not in raw:
                raw[low] = cell      # ONLY intent columns are copied out of the record
        out.rows.append(_build_row(raw, line_no, strict=True))
    return out


_DB_ADDRESS_RE = _re.compile(r"^(?P<host>[^:/\s]+):(?P<port>\d+)/(?P<name>[^\s/][^\s]*)$")


def parse_db_address(address: str):
    """`host:port/name` -> (host, port, name), or None when it is not that shape."""
    m = _DB_ADDRESS_RE.match((address or "").strip())
    if not m:
        return None
    return m.group("host"), int(m.group("port")), m.group("name")


def database_slug(server_slug: str, host: str, port: int, name: str) -> str:
    """The registry slug a CSV database row is registered under. A row on a
    registered server gets the slug the Find dialog's add-database gives it."""
    if server_slug:
        return f"{server_slug}-{name}".replace("_", "-")
    raw = f"{host}-{port}-{name}".lower()
    return _re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")


def plan_import(registry, rows: list[ImportRow], *, importable=None,
                server_choices: dict[int, str] | None = None,
                not_importable_note: dict[str, str] | None = None) -> ImportPlan:
    """Classify every row without writing anything.

    Separate from execution so `--dry-run` and the real run agree by
    construction: a plan you cannot inspect before a few hundred writes is not
    much of a safeguard.

    `importable` is the set of kinds the CALLER can register (the command line
    registers repos; the web Find-databases dialog registers databases); it
    defaults to IMPORTABLE_TYPES. `server_choices` maps a line number to the
    db server a person picked in the preview for a row that named none.
    """
    importable = tuple(importable) if importable is not None else IMPORTABLE_TYPES
    server_choices = server_choices or {}
    plan = ImportPlan()
    existing = _existing(registry)
    seen: set[str] = set()
    groups = None
    servers = None

    def _groups():
        nonlocal groups
        if groups is None:
            groups = {g.slug for g in registry.list_groups()}
        return groups

    def _server(slug):
        # The registry stores slugs with '_' for '-' and looks them up the same
        # way; a CSV may spell either.
        nonlocal servers
        if servers is None:
            servers = {registry._normalize_slug(sv.slug): sv for sv in registry.list_servers()}
        return servers.get(registry._normalize_slug(slug))

    for row in rows:
        if row.line in server_choices and row.resource_type == "database" and not row.server:
            row.server = server_choices[row.line]
        if row.errors:
            plan.invalid.append(row)
        elif row.key in seen:
            row.errors.append("duplicate of an earlier row in this file")
            plan.duplicate_in_file.append(row)
        elif row.key in existing:
            plan.already_registered.append(row)
            _propose_changes(registry, plan, row, existing[row.key], _groups())
        elif row.resource_type not in importable:
            row.errors.append(_not_importable_reason(row.resource_type, not_importable_note))
            plan.unsupported_type.append(row)
        elif row.resource_type == "database":
            _classify_database(registry, plan, row, _server, _groups)
        else:
            plan.to_register.append(row)
        if not row.errors:
            seen.add(row.key)
    return plan


def _not_importable_reason(resource_type: str, notes: dict[str, str] | None) -> str:
    if notes and resource_type in notes:
        return notes[resource_type]
    if resource_type == "filesystem":
        return ("file system rows are exported but cannot be registered from CSV yet: "
                "there is no file-system import path, and mount points are machine-local")
    if resource_type == "database":
        return "database rows are registered from the web Find databases dialog, not here"
    return f"{resource_type} rows cannot be registered from this place"


def _classify_database(registry, plan: ImportPlan, row: ImportRow, get_server, get_groups) -> None:
    """A new database row: invalid, needs-a-person, or importable.

    The credential comes from the server, never from the CSV. A row that names a
    registered server is importable; a row naming a `connection_ref` is
    importable with that reference recorded; a row naming neither is a candidate
    that needs a person to choose one in the preview.
    """
    parsed = parse_db_address(row.address)
    if parsed is None:
        row.errors.append(
            f'address "{row.address}" is not host:port/name (a database address, '
            "for example pg.regional.example:5432/orders)")
        plan.invalid.append(row)
        return
    host, port, name = parsed
    if row.group and row.group not in get_groups():
        row.errors.append(f"group '{row.group}' does not exist")
        plan.invalid.append(row)
        return
    if row.server:
        srv = get_server(row.server)
        if srv is None:
            row.errors.append(f"server '{row.server}' is not a registered database server")
            plan.invalid.append(row)
            return
        if resource_key("database", f"{srv.host}:{srv.port}") != resource_key(
                "database", f"{host}:{port}"):
            row.errors.append(
                f"address {host}:{port} is not on server '{row.server}' "
                f"({srv.host}:{srv.port})")
            plan.invalid.append(row)
            return
    slug = database_slug(row.server, host, port, name)
    if registry.get_database(slug, allow_unreadable=True):
        row.errors.append(
            f"a different database is already registered as '{slug}'; this row would collide with it")
        plan.invalid.append(row)
        return
    if not row.server and not row.connection_ref:
        plan.needs_person.append(row)
        return
    plan.to_register.append(row)


def _existing(registry) -> dict[str, dict]:
    """Every resource RE already has, by natural key -> {type, slug, group, address}.

    Built once per import rather than a lookup per row: get_by_github_url scans
    the whole projects table and normalizes in Python, so a few hundred rows
    would otherwise be a few hundred full scans.
    """
    out: dict[str, dict] = {}
    for p in registry.list_all():
        out[resource_key("repo", p.github_url)] = {
            "type": "repo", "slug": p.slug, "group": p.group_slug or "",
            "address": p.github_url}
    for d in getattr(registry, "list_databases", lambda: [])():
        addr = f"{d.host}:{d.port}/{d.database_name}"
        out[resource_key("database", addr)] = {
            "type": "database", "slug": d.slug, "group": d.group_slug or "", "address": addr}
    for f in getattr(registry, "list_filesystems", lambda: [])():
        addr = f.canonical_mount_point or f.local_mount_point
        out[resource_key("filesystem", addr)] = {
            "type": "filesystem", "slug": f.slug, "group": f.group_slug or "", "address": addr}
    return out


def _existing_keys(registry) -> set[str]:
    return set(_existing(registry))


def _current_disposition(registry, info: dict) -> str:
    try:
        if info["type"] == "repo":
            d = registry.get_disposition(info["address"])
        else:
            d = registry.get_disposition_for_entity(info["type"], info["slug"])
    except Exception:       # noqa: BLE001 - a lookup failure must not hide the row
        return "unknown"
    return (d or {}).get("disposition") or "undecided"


def _propose_changes(registry, plan: ImportPlan, row: ImportRow, info: dict, groups: set) -> None:
    """Intent a file carries for a row that already exists. Proposed, never applied
    until confirmed. Only what the file actually states is compared: an empty
    cell proposes nothing."""
    if row.group and row.group != info["group"]:
        plan.proposed_changes.append(ProposedChange(
            line=row.line, resource_type=info["type"], key=row.key, slug=info["slug"],
            field="group", current=info["group"], proposed=row.group,
            blocked="" if row.group in groups else f"group '{row.group}' does not exist"))
    if row.disposition:
        cur = _current_disposition(registry, info)
        if row.disposition != cur:
            plan.proposed_changes.append(ProposedChange(
                line=row.line, resource_type=info["type"], key=row.key, slug=info["slug"],
                field="disposition", current=cur, proposed=row.disposition))


#: What status_in_scope / status_fit say when an export names no investigation.
#: A word, not a blank: an empty cell in a scorecard reads as "fine".
NO_INVESTIGATION = "no investigation named"
#: What status_fit says for an investigation: the fit headline needs a data lens,
#: and no lens is applied to an investigation on this build. When one is, the
#: headline replaces this word (see `fit_headline`).
NO_LENS = "no data lens applied"


def fit_headline(registry, investigation: str, entity_type: str, entity_slug: str) -> str:
    """The fit headline for a member, in the words the screen uses, or NO_LENS.

    The headline ("3 of 6 fit · 1 doesn't · 1 couldn't check · 1 no reader yet")
    needs a data lens bound to the investigation. None exists on this build, so
    every member reads NO_LENS: a fit is never invented for an export. This is
    the one place the real headline lands when the lens does.
    """
    return NO_LENS


def export_rows(registry, *, only: set[tuple[str, str]] | None = None,
                investigation: str | None = None,
                in_scope: dict[tuple[str, str], str] | None = None) -> list[dict[str, Any]]:
    """Current state of every registered resource, as scorecard rows.

    Every value here is read from the registry at call time. Nothing is cached,
    because a stale scorecard that looks authoritative is the failure mode this
    format is most likely to produce.

    `only` restricts to (resource_type, slug) pairs (a work list, a scope).
    `investigation` names the investigation `status_in_scope` / `status_fit` are
    stated against; `in_scope` maps (type, slug) -> the member's state there.
    Without an investigation both columns say NO_INVESTIGATION.

    No credential is ever read into a row: the registry objects carry
    `db_password`, and none of the keys below refers to it.
    """
    out: list[dict[str, Any]] = []
    in_scope = in_scope or {}

    def _wanted(kind: str, slug: str) -> bool:
        return only is None or (kind, slug) in only

    def _scope_words(kind: str, slug: str) -> tuple[str, str]:
        if not investigation:
            return NO_INVESTIGATION, NO_INVESTIGATION
        state = in_scope.get((kind, slug))
        if state == "in-scope":
            return "in scope", fit_headline(registry, investigation, kind, slug)
        return "not in scope", NO_LENS

    def _linkage(entity_type: str, slug: str) -> str:
        try:
            row = registry.get_egeria_linkage(entity_type, slug)
        except Exception as exc:
            # "unknown", not "" — an empty cell in a scorecard reads as "fine",
            # and a column that cannot distinguish "healthy" from "could not
            # tell" is worse than no column.
            log.warning("linkage lookup failed for %s/%s: %s", entity_type, slug, exc)
            return "unknown"
        return "stale" if row and row.get("status") == "stale" else "ok"

    for p in registry.list_all():
        if not _wanted("repo", p.slug):
            continue
        # Deliberately unguarded. These read the same registry that produced the
        # list being iterated, so a failure here is not a per-row problem — it is
        # the registry being unavailable, and an export that carries on would
        # emit hundreds of rows with silently empty disposition and publish
        # columns. A scorecard assembled from a registry we could not read is
        # worse than an error, because it looks like an answer.
        disp = registry.get_disposition(p.github_url) or {}
        latest = registry.get_latest_egeria_survey(p.slug)
        published = (latest or {}).get("published_at", "") if latest else ""
        scope_word, fit_word = _scope_words("repo", p.slug)
        out.append({
            "resource_type": "repo",
            "address": p.github_url,
            "display_name": p.display_name,
            "group": p.group_slug or "",
            "subpath": p.subproject_path or "",
            "server": "", "connection_ref": "",
            "disposition": disp.get("disposition", ""),
            "disposition_reason": disp.get("reason", ""),
            "notes": "",
            "status_registered": "yes",
            "status_slug": p.slug,
            "status_cataloged": "yes" if p.egeria_asset_guid else "no",
            "status_egeria_link": _linkage("repo", p.slug),
            "status_last_surveyed_at": p.last_surveyed_at or "",
            "status_last_published_at": published or "",
            "status_lifecycle": p.status or "",
            "status_indexed": "yes" if (p.collections or []) else "no",
            "status_in_scope": scope_word, "status_fit": fit_word,
        })

    for d in getattr(registry, "list_databases", lambda: [])():
        if not _wanted("database", d.slug):
            continue
        disp = registry.get_disposition_for_entity("database", d.slug) or {}
        scope_word, fit_word = _scope_words("database", d.slug)
        out.append({
            "resource_type": "database",
            "address": f"{d.host}:{d.port}/{d.database_name}",
            "display_name": d.display_name,
            "group": d.group_slug or "",
            "subpath": "",
            # Where the credential comes FROM, never the credential itself.
            "server": d.server_slug or "",
            "connection_ref": d.connection_ref or "",
            "disposition": disp.get("disposition", ""),
            "disposition_reason": disp.get("reason", ""),
            "notes": "",
            "status_registered": "yes",
            "status_slug": d.slug,
            "status_cataloged": "yes" if d.egeria_asset_guid else "no",
            "status_egeria_link": _linkage("database", d.slug),
            "status_last_surveyed_at": d.last_surveyed_at or "",
            "status_last_published_at": "",
            "status_lifecycle": d.status or "",
            "status_indexed": "",
            "status_in_scope": scope_word, "status_fit": fit_word,
        })

    for f in getattr(registry, "list_filesystems", lambda: [])():
        if not _wanted("filesystem", f.slug):
            continue
        disp = registry.get_disposition_for_entity("filesystem", f.slug) or {}
        scope_word, fit_word = _scope_words("filesystem", f.slug)
        out.append({
            "resource_type": "filesystem",
            "address": f.canonical_mount_point or f.local_mount_point,
            "display_name": f.display_name,
            "group": f.group_slug or "",
            "subpath": "", "server": "", "connection_ref": "",
            "disposition": disp.get("disposition", ""),
            "disposition_reason": disp.get("reason", ""),
            "notes": "",
            "status_registered": "yes",
            "status_slug": f.slug,
            "status_cataloged": "yes" if f.egeria_asset_guid else "no",
            "status_egeria_link": _linkage("filesystem", f.slug),
            "status_last_surveyed_at": f.last_surveyed_at or "",
            "status_last_published_at": "",
            "status_lifecycle": f.status or "",
            "status_indexed": "",
            "status_in_scope": scope_word, "status_fit": fit_word,
        })
    return out


def _sluggish(text: str) -> str:
    return _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unnamed"


def export_filename(what: str, name: str = "", date: str = "") -> str:
    """`re-<what>-<name>-<date>.csv`, e.g. `re-scope-customer-360-2026-10-01.csv`.
    The inventory has no name: `re-inventory-<date>.csv`."""
    from datetime import datetime, timezone

    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = ["re", _sluggish(what)] + ([_sluggish(name)] if name else []) + [date]
    return "-".join(parts) + ".csv"


def rows_to_csv_text(rows: list[dict[str, Any]]) -> str:
    """The same writer `write_csv` uses, to a string (web downloads). Refuses to
    write if the column set is not credential-free."""
    import io

    assert_no_credential_columns(ALL_COLUMNS)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(ALL_COLUMNS))
    writer.writeheader()
    for r in rows:
        writer.writerow({c: r.get(c, "") for c in ALL_COLUMNS})
    return buf.getvalue()


def scope_export_rows(registry, investigation_slug: str) -> list[dict[str, Any]]:
    """An investigation's scope as CSV rows: its in-scope members only.

    Only members whose state is `in-scope` are written, so importing the file
    into a second investigation never promotes a candidate or excluded member to
    in scope (status_ columns are not read back). A member whose resource is no
    longer registered is still written, with no address, so the import names it
    as invalid by line instead of dropping it.
    """
    members = [m for m in registry.list_investigation_members(investigation_slug)
               if (m.get("state") or "in-scope") == "in-scope"]
    return _rows_for_members(registry, members, investigation_slug)


def work_list_export_rows(registry, work_list: dict) -> list[dict[str, Any]]:
    """A work list's members as CSV rows, each as its own kind, stated against
    the work list's investigation if it has one."""
    kind = work_list.get("entity_type") or "repo"
    members = [{"entity_type": m.get("entity_type") or kind, "entity_slug": m["entity_slug"], "state": None}
               for m in work_list.get("members", [])]
    inv = work_list.get("investigation") or ""
    return _rows_for_members(registry, members, inv)


def _rows_for_members(registry, members: list[dict], investigation: str) -> list[dict[str, Any]]:
    wanted = {(m["entity_type"], m["entity_slug"]) for m in members}
    states: dict[tuple[str, str], str] = {}
    if investigation:
        for m in registry.list_investigation_members(investigation):
            states[(m["entity_type"], m["entity_slug"])] = m.get("state") or "in-scope"
    if not investigation:
        states = {}
    rows = export_rows(registry, only=wanted, investigation=investigation or None,
                       in_scope=states)
    have = {(r["resource_type"], r["status_slug"]) for r in rows}
    for kind, slug in sorted(wanted - have):
        rows.append({
            "resource_type": kind, "address": "", "display_name": slug,
            "status_registered": "no longer in the registry", "status_slug": slug,
            "status_in_scope": "in scope" if investigation else NO_INVESTIGATION,
            "status_fit": NO_LENS if investigation else NO_INVESTIGATION,
        })
    order = {(m["entity_type"], m["entity_slug"]): i for i, m in enumerate(members)}
    rows.sort(key=lambda r: order.get((r["resource_type"], r["status_slug"]), 0))
    return rows


def candidate_export_rows(registry, candidates: list[dict], source_slug: str = "") -> list[dict[str, Any]]:
    """The candidate table a person is looking at, as CSV rows.

    Written from the rows the dialog shows (name, address, server, verdict,
    registered), so the file is that table and not a second opinion. Candidate
    facts that are not registry state (size, owner, CONNECT) are not columns of
    this contract; a candidate is written the way it would be IMPORTED, which is
    what makes the file re-importable.
    """
    existing = _existing(registry)
    rows = []
    for c in candidates:
        addr = (c.get("address") or "").strip()
        if not addr:
            continue
        info = existing.get(resource_key("database", addr))
        verdict = c.get("verdict") or {}
        rows.append({
            "resource_type": "database",
            "address": addr,
            "display_name": c.get("name") or "",
            "group": "", "subpath": "",
            "server": (c.get("server_slug") or source_slug or ""),
            "connection_ref": "",
            "disposition": verdict.get("disposition") if verdict.get("disposition") not in (None, "undecided") else "",
            "disposition_reason": verdict.get("reason") or "",
            "notes": "",
            "status_registered": "yes" if info else "no",
            "status_slug": info["slug"] if info else "",
            "status_in_scope": NO_INVESTIGATION, "status_fit": NO_INVESTIGATION,
        })
    return rows


# -- The web preview ----------------------------------------------------------------

WEB_IMPORTABLE_TYPES = ("database",)
_WEB_NOT_IMPORTABLE = {
    "repo": "repo rows are not imported from this dialog: use Find repos, 'load from file'",
}


def _line_item(row: ImportRow, message: str = "") -> dict:
    return {
        "line": row.line, "resource_type": row.resource_type, "address": row.address,
        "display_name": row.display_name, "group": row.group, "server": row.server,
        "connection_ref": row.connection_ref, "disposition": row.disposition,
        "message": message or "; ".join(row.errors),
        "needs_person": False,
    }


def preview_file(registry, text: str, *, server_choices: dict[int, str] | None = None,
                 investigation: str = "") -> dict:
    """What a file would do, for the web preview: the five counts, each with its
    lines; the columns that were ignored, said once; or a refusal.

    Nothing is written. A credential-like column is named (the header only) and
    its values are never read; no cell of one can reach this payload.
    """
    parsed = parse_csv_strict(text)
    out: dict[str, Any] = {
        "refused": parsed.refused or None, "header": parsed.header, "missing": parsed.missing,
        "messages": [], "ignored": {"unknown": [], "status": [], "credential": []},
        "counts": None, "lines": None, "proposed_changes": [], "servers": [],
    }
    if parsed.refused:
        return out

    ign = out["ignored"]
    ign["unknown"], ign["status"], ign["credential"] = (
        list(parsed.unknown_columns), list(parsed.status_columns), list(parsed.credential_columns))
    if parsed.unknown_columns:
        out["messages"].append("Ignored columns: " + ", ".join(parsed.unknown_columns))
    if parsed.status_columns:
        n = len(parsed.status_columns)
        out["messages"].append(
            f"{n} status_ column{'s' if n != 1 else ''} ignored: those are written by RE, never read")
    if parsed.credential_columns:
        out["messages"].append(
            "Credential column(s) ignored: " + ", ".join(parsed.credential_columns)
            + ". A CSV names where a credential comes from (server or connection_ref), never the credential.")

    plan = plan_import(registry, parsed.rows, importable=WEB_IMPORTABLE_TYPES,
                       server_choices=server_choices, not_importable_note=_WEB_NOT_IMPORTABLE)
    servers = [{"slug": sv.slug, "display_name": sv.display_name, "host": sv.host, "port": sv.port}
               for sv in registry.list_servers()]
    out["servers"] = servers
    out["rows"] = len(parsed.rows)
    out["counts"] = {
        "new": len(plan.to_register) + len(plan.needs_person),
        "ready": len(plan.to_register),
        "needs_person": len(plan.needs_person),
        "already_registered": len(plan.already_registered),
        "already_in_investigation": 0,
        "duplicate_in_file": len(plan.duplicate_in_file),
        "invalid": len(plan.invalid),
        "not_importable": len(plan.unsupported_type),
    }
    needs = []
    for r in plan.needs_person:
        item = _line_item(r, "needs a person: name a server or a credential")
        item["needs_person"] = True
        parsed_addr = parse_db_address(r.address)
        item["matching_servers"] = [
            sv["slug"] for sv in servers
            if parsed_addr and resource_key("database", f"{sv['host']}:{sv['port']}")
            == resource_key("database", f"{parsed_addr[0]}:{parsed_addr[1]}")]
        needs.append(item)
    existing = _existing(registry)
    # Which already-registered rows are ALREADY members of the chosen
    # investigation, from its real member rows (read only; nothing is created).
    members: set[tuple[str, str]] = set()
    if investigation:
        ws_slug = registry.investigation_working_set_slug(investigation)
        if ws_slug:
            members = {(m["entity_type"], m["entity_slug"])
                       for m in registry.list_working_set_members(ws_slug)}
    already = []
    for r in plan.already_registered:
        info = existing.get(r.key) or {}
        item = dict(_line_item(r, "already registered"), slug=info.get("slug", ""),
                    resource_type=r.resource_type)
        item["in_investigation"] = bool(investigation) and (info.get("type"), info.get("slug")) in members
        already.append(item)
    out["counts"]["already_in_investigation"] = sum(1 for i in already if i["in_investigation"])
    out["investigation"] = investigation or None
    out["lines"] = {
        "new": [_line_item(r, "ready to register") for r in plan.to_register] + needs,
        "already_registered": already,
        "duplicate_in_file": [_line_item(r) for r in plan.duplicate_in_file],
        "invalid": [_line_item(r) for r in plan.invalid],
        "not_importable": [_line_item(r) for r in plan.unsupported_type],
    }
    out["proposed_changes"] = [
        {"line": c.line, "resource_type": c.resource_type, "slug": c.slug, "field": c.field,
         "current": c.current, "proposed": c.proposed, "blocked": c.blocked}
        for c in plan.proposed_changes]
    return out


def import_file(registry, text: str, *, lines: list[int] | None = None,
                server_choices: dict[int, str] | None = None, group: str = "",
                investigation: str = "", accept_changes: list[dict] | None = None,
                rationale: str = "") -> dict:
    """Re-plan the file (the text is the truth; the browser sends no rows) and
    apply the confirmed lines. Raises ValueError for a refused file."""
    parsed = parse_csv_strict(text)
    if parsed.refused:
        raise ValueError(parsed.refused)
    plan = plan_import(registry, parsed.rows, importable=WEB_IMPORTABLE_TYPES,
                       server_choices=server_choices, not_importable_note=_WEB_NOT_IMPORTABLE)
    accepted = {(int(c["line"]), str(c["field"])) for c in (accept_changes or [])}
    res = apply_import(registry, plan, lines=set(lines) if lines is not None else None,
                       group=group, investigation=investigation,
                       accept_changes=accepted, rationale=rationale)
    res["counts"] = {
        "registered": len(res["registered"]), "scoped": len(res["scoped"]),
        "changed": len(res["changed"]), "failed": len(res["failures"]),
    }
    return res


# -- Applying a confirmed import ---------------------------------------------------

def build_database_entity(server, database_name: str, display_name: str = ""):
    """The DatabaseEntity for one database on a registered server. The credential
    is copied from the SERVER (never from a CSV); this is the single place the
    Find dialog's add-database route and the CSV import both build it."""
    from resource_explorer.egeria_host import default_egeria_host
    from resource_explorer.registry import DatabaseEntity

    return DatabaseEntity(
        slug=database_slug(server.slug, server.host, server.port, database_name),
        display_name=display_name or f"{database_name} @ {server.display_name}",
        db_type=server.db_type,
        host=server.host,
        port=server.port,
        database_name=database_name,
        server_slug=server.slug,
        group_slug=server.group_slug,
        db_user=server.db_user,
        db_password=server.db_password,
        egeria_host=default_egeria_host(server.host, server.egeria_host),
        egeria_url=server.egeria_url,
        egeria_server=server.egeria_server,
        # per-resource Egeria credentials are no longer copied or used (owner, 2026-10-09)
    )


def _build_database_from_ref(row: ImportRow, host: str, port: int, name: str):
    """A database named by a `connection_ref` and no server: registered with the
    reference NAME recorded and no stored credential."""
    from resource_explorer.registry import DatabaseEntity

    return DatabaseEntity(
        slug=database_slug("", host, port, name),
        display_name=row.display_name or f"{name} @ {host}:{port}",
        db_type="postgresql",
        host=host, port=port, database_name=name,
        connection_ref=row.connection_ref,
    )


def apply_import(registry, plan: ImportPlan, *, lines: set[int] | None = None,
                 group: str = "", investigation: str = "",
                 accept_changes: set[tuple[int, str]] | None = None,
                 rationale: str = "") -> dict:
    """Do what a confirmed preview said, row by row, isolating failures.

    `lines` limits which rows (by file line) are acted on; None = all of them.
    New database rows are registered (credential from their server), put in the
    group (their own `group` cell, else `group`), given their disposition, and,
    when an investigation is named, added to its scope. Already-registered rows
    are only added to the investigation's scope: nothing about them changes
    unless a (line, field) pair is in `accept_changes`. Nothing here writes to
    Egeria. A group change is checked first (Brief T round 2, `_check_group_change`): that check may read the
    resource's zones from Egeria, and without a signed-in caller the change is refused per row.
    """
    accept_changes = accept_changes or set()
    if group and not registry.get_group(group):
        raise ValueError(f"group '{group}' does not exist")
    if investigation and not registry.get_investigation(investigation):
        raise ValueError(f"investigation '{investigation}' does not exist")

    def chosen(row: ImportRow) -> bool:
        return lines is None or row.line in lines

    existing = _existing(registry)
    result: dict[str, Any] = {"registered": [], "scoped": [], "changed": [], "failures": []}
    ws_slug = ""
    if investigation:
        ws = registry.get_or_create_working_set(investigation)
        ws_slug = ws["slug"]

    def add_to_scope(kind: str, slug: str, line: int) -> None:
        if not investigation:
            return
        try:
            # keep_existing: re-confirming a file must not reset a rationale or
            # state the owner has set on a member since the first import.
            registry.add_working_set_member(ws_slug, kind, slug, membership_rationale=rationale,
                                            keep_existing=True)
            result["scoped"].append({"line": line, "resource_type": kind, "slug": slug})
        except Exception as exc:        # noqa: BLE001 - per-row isolation
            result["failures"].append(
                {"line": line, "message": f"registered, but not added to the investigation: {exc}"})

    for row in plan.to_register:
        if not chosen(row):
            continue
        if row.resource_type != "database":
            result["failures"].append({"line": row.line, "message":
                                       f"{row.resource_type} rows are not imported here"})
            continue
        parsed = parse_db_address(row.address)
        try:
            host, port, name = parsed                     # validated by the plan
            if row.server:
                srv = registry.get_server(row.server)
                if srv is None:
                    raise ValueError(f"server '{row.server}' is not registered")
                entity = build_database_entity(srv, name, row.display_name)
                if row.connection_ref:
                    entity.connection_ref = row.connection_ref
            else:
                entity = _build_database_from_ref(row, host, port, name)
            if registry.get_database(entity.slug, allow_unreadable=True):
                raise ValueError(f"'{entity.slug}' is already registered")
            registry.register_database(entity)
        except Exception as exc:            # noqa: BLE001 - per-row isolation
            result["failures"].append({"line": row.line, "message": str(exc)})
            continue
        # The registry stores a slug with '_' for '-'; every later write (scope,
        # disposition) must use the STORED slug or it points at nothing.
        stored = registry._normalize_slug(entity.slug)
        result["registered"].append({"line": row.line, "slug": stored, "address": row.address})
        g = row.group or group
        if g:
            try:
                _check_group_change(registry, "database", stored, g)
                registry.set_database_group(stored, g)
            except Exception as exc:        # noqa: BLE001
                result["failures"].append({"line": row.line, "message": f"registered, but the group was not set: {exc}"})
        if row.disposition:
            try:
                registry.set_disposition_for_entity(
                    "database", stored, row.disposition, reason=row.disposition_reason)
            except Exception as exc:        # noqa: BLE001
                result["failures"].append({"line": row.line, "message": f"registered, but the disposition was not set: {exc}"})
        add_to_scope("database", stored, row.line)

    for row in plan.already_registered:
        if not chosen(row):
            continue
        info = existing.get(row.key)
        if info:
            add_to_scope(info["type"], info["slug"], row.line)

    for ch in plan.proposed_changes:
        if (ch.line, ch.field) not in accept_changes or ch.blocked:
            continue
        try:
            if ch.field == "group":
                setter = {"repo": registry.set_project_group, "database": registry.set_database_group,
                          "filesystem": registry.set_filesystem_group}[ch.resource_type]
                _check_group_change(registry, ch.resource_type, ch.slug, ch.proposed)
                setter(ch.slug, ch.proposed)
            elif ch.field == "disposition":
                info = existing.get(ch.key) or {}
                if ch.resource_type == "repo":
                    row = next((r for r in plan.already_registered if r.line == ch.line), None)
                    registry.set_disposition(info.get("address", ""), ch.proposed,
                                             reason=(row.disposition_reason if row else ""))
                else:
                    row = next((r for r in plan.already_registered if r.line == ch.line), None)
                    registry.set_disposition_for_entity(
                        ch.resource_type, ch.slug, ch.proposed,
                        reason=(row.disposition_reason if row else ""))
            result["changed"].append({"line": ch.line, "field": ch.field, "slug": ch.slug,
                                      "from": ch.current, "to": ch.proposed})
        except Exception as exc:            # noqa: BLE001
            result["failures"].append({"line": ch.line, "message": f"{ch.field} not changed: {exc}"})
    return result


def _check_group_change(registry, resource_type: str, slug: str, group_slug: str) -> None:
    """Brief T round 2: a group is a Folio in Egeria, so an import's group change takes the same curation check as
    the web and the CLI. Raises with "not permitted · <reason>" (the row's failure) when refused; with nobody
    signed in the reason says to sign in. The Folio follows at the resource's next sync."""
    from resource_explorer.curation_egeria import group_change_refusal

    why = group_change_refusal(registry, resource_type, slug, group_slug)
    if why:
        raise PermissionError(f"not permitted · {why}")


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> int:
    """Write scorecard rows, always with the full column set.

    Every column every time, even when empty: a file whose columns vary with its
    contents cannot be diffed against last week's, which is most of what a
    running scorecard is for.
    """
    assert_no_credential_columns(ALL_COLUMNS)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ALL_COLUMNS))
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in ALL_COLUMNS})
    return len(rows)
