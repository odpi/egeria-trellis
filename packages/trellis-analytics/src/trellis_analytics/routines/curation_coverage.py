"""curation_coverage: what of this resource is published to Egeria, what is not yet and why, what is blocked.

Analysis 1 of DESIGN-CURATE-BY-ANALYSIS.md. Reads the curation record from the ODS (commit proofs,
catalogue scope events, the outbox); no credential, no Egeria call. Every count carries the row ids it
counts (`rows`: table name -> ids), and the whole result carries an Envelope.

Definitions (a database), all derived from persisted rows, never from the branch code took:
- *published*: a schema whose newest state proof is `elements_read_back` (its tables are the ones that
  read-back listed) or `restored`; plus the newest `report_published` proof (a read-back row only).
- *blocked*: a schema whose newest state-or-blocked proof is `leave_out_blocked` (ISSUE-117, kept not sent).
- *known schemas* (the universe) are the non-system schemas of `resolve_node_set(registry, slug)`, the same
  function Curate's scope tree takes its nodes from, so this counts what Curate shows. `counted_against`
  says which source that was. Needs the optional `re` extra; without it the result is `not_applicable`.
- *not yet*: every other known schema, partitioned by its newest scope choice, so the counts add up:
  undecided; chosen by a person; chosen by a rule a person confirmed; left out by a person; left out by a
  rule a person confirmed.
- Proofs read before the newest `egeria_reset` marker are what Egeria once held and are not counted.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from trellis_analytics import __version__
from trellis_analytics.envelope import Envelope, ResultState
from trellis_analytics.readers.ods import OdsReader, OdsUnreadable
from trellis_analytics.readers.registry_view import ResourceExplorerMissing

_DB_NODES = {"database", "schema", "table", "column"}
_PUBLISHED = ("elements_read_back", "restored")
_STATE = ("attach_requested", "target_attached", "elements_read_back", "removed", "archived", "restored")
_BLOCKED = "leave_out_blocked"
_REPORT = "report_published"
_RESET = "egeria_reset"


class CountLine(BaseModel):
    key: str
    label: str
    count: int
    rows: dict[str, list[int]] = Field(default_factory=dict)   # table -> the ids counted
    names: list[str] = Field(default_factory=list)


class Group(BaseModel):
    key: str                    # published | not_yet | blocked
    words: str
    lines: list[CountLine] = Field(default_factory=list)


class CoverageResult(BaseModel):
    resource_type: str
    slug: str
    envelope: Envelope
    as_of: str = ""             # when Egeria was last read back for this resource ('' when never)
    known_schemas: int = 0
    known: list[str] = Field(default_factory=list)      # the schema names counted: Curate's non-system schemas
    counted_against: dict = Field(default_factory=dict)   # resolve_node_set's chosen / egeria / local headers
    outside_node_set: list[str] = Field(default_factory=list)   # have a choice or proof but are not in the node set
    groups: list[Group] = Field(default_factory=list)
    summary: str = ""

    def group(self, key: str) -> Group:
        return next(g for g in self.groups if g.key == key)

    def count(self, group: str, line: str) -> int:
        return next((l.count for l in self.group(group).lines if l.key == line), 0)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


def _ts(value: str):
    """read_at comes in more than one shape ('2026-10-09T17:51:36.4', '20261006T2100Z'); None when unreadable."""
    from datetime import datetime, timezone
    for parse in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")),
                  lambda v: datetime.strptime(v, "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)):
        try:
            d = parse(value or "")
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _result(resource_type, slug, state, reason, **kw) -> CoverageResult:
    env = Envelope(result_state=state, result_state_reason=reason, producer_version=__version__,
                   scope=f"{resource_type}:{slug}")
    return CoverageResult(resource_type=resource_type, slug=slug, envelope=env, **kw)


def _node_set(ods: OdsReader, slug: str) -> dict:
    """Curate's node set for this database, read-only. Raises ResourceExplorerMissing, or OdsUnreadable."""
    view = ods.registry_view()
    try:
        from resource_explorer.catalogue_scope import resolve_node_set    # lazy: RE is an optional dependency
    except ImportError as exc:
        raise ResourceExplorerMissing(str(exc)) from exc
    try:
        return resolve_node_set(view, slug)
    except Exception as exc:  # noqa: BLE001  the registry could not answer: not "no schemas"
        raise OdsUnreadable(f"the node set could not be read: {type(exc).__name__}: {exc}") from exc


def curation_coverage(ods: OdsReader, resource_type: str, slug: str) -> CoverageResult:
    if resource_type != "database":
        return _result(resource_type, slug, ResultState.NOT_APPLICABLE,
                       f"curation coverage is measured for databases in this release; {resource_type} has no curation record it reads")
    try:
        proofs_all = [p for p in ods.commit_proofs(slug) if p["node_kind"] in _DB_NODES or p["proof"] == _RESET]
        events = ods.catalogue_scope_events(slug)
        outbox = ods.outbox_status_counts("database", slug)
        node_set = _node_set(ods, slug)
    except OdsUnreadable as exc:
        return _result(resource_type, slug, ResultState.NOT_ESTABLISHED, str(exc))
    except ResourceExplorerMissing as exc:
        return _result(resource_type, slug, ResultState.NOT_APPLICABLE,
                       f"curation coverage counts against Resource Explorer's node set and it is not installed "
                       f"(install trellis-analytics[re]): {exc}")
    surveyed = sorted({s["schema"] for s in node_set["schemas"]
                       if s.get("schema") and s.get("classification") != "system"})
    counted_against = {k: node_set.get(k) for k in ("chosen", "egeria", "local", "disagree")}

    resets = [p for p in proofs_all if p["proof"] == _RESET]
    cutoff = max((t for t in (_ts(p["read_at"]) for p in resets) if t), default=None)
    # a proof whose time cannot be read is not counted once a reset exists: no claim either way
    proofs = [p for p in proofs_all if p["proof"] != _RESET
              and (cutoff is None or ((_ts(p["read_at"]) or None) is not None and _ts(p["read_at"]) >= cutoff))]

    # newest scope choice per schema (a cleared choice is absent), oldest-first so the last write wins
    choice: dict[str, dict] = {}
    for e in events:
        if e["node_kind"] != "schema":
            continue
        choice[e["schema_name"]] = e
    choice = {k: v for k, v in choice.items() if v["choice"]}

    schema_proofs: dict[str, list[dict]] = {}
    for p in proofs:
        if p["node_kind"] == "schema":
            schema_proofs.setdefault(p["schema_name"], []).append(p)

    universe = surveyed     # exactly Curate's schemas; a choice or proof for any other name is reported, not counted
    outside = sorted((set(choice) | set(schema_proofs)) - set(universe))

    published, blocked = {}, {}
    for name in universe:
        last = next((p for p in reversed(schema_proofs.get(name, [])) if p["proof"] in _STATE + (_BLOCKED,)), None)
        if last is None:
            continue
        if last["proof"] in _PUBLISHED:
            published[name] = last
        elif last["proof"] == _BLOCKED:
            blocked[name] = last

    report = next((p for p in reversed(proofs) if p["node_kind"] == "database" and p["proof"] == _REPORT), None)

    if not published and not blocked and report is None and not choice and not any(
            p["proof"] in _STATE for p in proofs):
        if not universe:
            # No survey has told RE of any schema: nothing was looked at, so "nothing found" would be a guess.
            return _result(resource_type, slug, ResultState.NOT_ESTABLISHED,
                           "no survey of this database: RE knows none of its schemas",
                           counted_against=counted_against, outside_node_set=outside,
                           summary="not surveyed · no schemas known to RE")
        return _result(resource_type, slug, ResultState.NOTHING_FOUND,
                       f"nothing published yet; {len(universe)} schema(s) known to RE and no choice made",
                       known_schemas=len(universe), as_of="", counted_against=counted_against, known=universe,
                       outside_node_set=outside,
                       summary=f"nothing published yet · {len(universe)} known to RE",
                       groups=[Group(key="published", words="nothing published yet"),
                               Group(key="not_yet", words=f"{len(universe)} known to RE · no choice made"),
                               Group(key="blocked", words="none")])

    table_ids = [p["id"] for p in published.values() if p["proof"] == "elements_read_back"]
    tables = sorted(f"{n}.{t}" for n, p in published.items() if p["proof"] == "elements_read_back"
                    for t in (p["detail"].get("tables") or []))
    pub_lines = [
        CountLine(key="schemas", label="schemas", count=len(published),
                  rows={"catalogue_commit_proofs": [p["id"] for p in published.values()]}, names=sorted(published)),
        CountLine(key="tables", label="tables", count=len(tables),
                  rows={"catalogue_commit_proofs": table_ids}, names=tables),
    ]
    if report is not None:
        n = report["detail"].get("annotation_count")
        pub_lines.append(CountLine(key="report", label="report", count=1,
                                   rows={"catalogue_commit_proofs": [report["id"]]},
                                   names=[f"{n} annotations" if n is not None else "annotation count not read"]))
    read_ats = [p["read_at"] for p in list(published.values()) + ([report] if report else [])]
    as_of = max(read_ats, key=lambda v: _ts(v) or _ts("1970-01-01T00:00:00"), default="")

    rest = [n for n in universe if n not in published and n not in blocked]
    buckets: dict[str, list[str]] = {k: [] for k in
                                     ("undecided", "chosen", "chosen_confirmed", "left_out", "left_out_confirmed")}
    for n in rest:
        ev = choice.get(n)
        if ev is None:
            buckets["undecided"].append(n)
        elif ev["choice"] == "catalogue":
            buckets["chosen" if ev["source"] == "person" else "chosen_confirmed"].append(n)
        else:
            buckets["left_out" if ev["source"] == "person" else "left_out_confirmed"].append(n)
    labels = {"undecided": "undecided", "chosen": "chosen by a person, not published",
              "chosen_confirmed": "chosen by a confirmed proposal, not published",
              "left_out": "left out by a person", "left_out_confirmed": "left out by a confirmed proposal"}
    not_yet_lines = [
        CountLine(key=k, label=labels[k], count=len(v), names=v,
                  rows={"catalogue_scope_events": [choice[n]["id"] for n in v if n in choice]})
        for k, v in buckets.items()]
    not_yet_total = len(rest)

    superseded = outbox.get("superseded", [])
    blocked_lines = [CountLine(key="blocked", label="blocked (ISSUE-117, kept not sent)", count=len(blocked),
                               rows={"catalogue_commit_proofs": [p["id"] for p in blocked.values()]},
                               names=sorted(blocked))]
    if superseded:
        blocked_lines.append(CountLine(key="superseded", label="outbox writes superseded", count=len(superseded),
                                       rows={"egeria_outbox": superseded}))

    pub_words = ("published · " + " · ".join(f"{l.count} {l.label}" for l in pub_lines if l.count)
                 + (f" · read back {as_of}" if as_of else "")) if published or report else "nothing published yet"
    ny_words = f"not yet published · {not_yet_total}: " + " · ".join(
        f"{l.count} {l.label}" for l in not_yet_lines if l.count) if not_yet_total else "not yet published · none"
    bl_words = f"blocked · {len(blocked)}" if blocked else "blocked · none"

    return _result(resource_type, slug, ResultState.MEASURED, "", known_schemas=len(universe), as_of=as_of,
                   counted_against=counted_against, known=universe, outside_node_set=outside,
                   summary=" | ".join([pub_words, ny_words, bl_words]),
                   groups=[Group(key="published", words=pub_words, lines=pub_lines),
                           Group(key="not_yet", words=ny_words, lines=not_yet_lines),
                           Group(key="blocked", words=bl_words, lines=blocked_lines)])
