"""One-time import of EA's JSONL feedback into the trellis-feedback store (Phase 2, A-F1).

Run through `scripts/import_feedback_jsonl.py`. Reads, under `<data dir>/feedback/`:

- `feedback_extended.jsonl`: one line per web vote, with the full answer text and the triage
  status and notes that the retired line-index PATCH edited.
- `user_feedback.jsonl`: one line per vote from every caller: the web route, which also wrote the
  extended line, and the CLI, which wrote only this one.

Each line becomes one `answer` item via `FeedbackStore.import_item`.

**Ids.** An item's id is derived from the line's own content: its file, its timestamp, and a hash of
the user, question, vote and answer. The triage fields are left out, so a line whose status was
edited later still maps to the same id. Running the import again therefore writes nothing twice
(`import_item` returns False for an id it already holds).

**One vote, two files.** The web route wrote both files for one vote, a moment apart. A
`user_feedback.jsonl` line is skipped, with the extended line it matches, when that extended line has
the same user, question and rating, and a timestamp within `PAIR_WINDOW_S`. Each extended line pairs
at most once.

**Status.** An extended line's `triage_status`, when it isn't `new`, becomes the item's first
triage event, written in the same transaction as the item, by actor `migration`, with the line's
`analysis_comments` as the note. Comments on a line still `new` are kept in the item's `extra`.

**Reported, never silent:** every line read is counted, and every line not imported says why, with
its file and line number: not JSON, no question, an unknown vote, a duplicate, already imported, or
refused by the store. The files themselves are kept.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

EXTENDED = "feedback_extended.jsonl"
USER = "user_feedback.jsonl"
FILES = (EXTENDED, USER)     # extended first: it has the answer text and the triage, so it wins a pair
PAIR_WINDOW_S = 5.0
ACTOR = "migration"
ID_PREFIX = "eaj-"           # + 32 hex = 36, the library's id limit

_VOTE_BY_RATING = {"positive": 1, "neutral": 0, "negative": -1}
#: Fields the item has columns for; everything else on the line goes into `extra`.
_COLUMNS = {"query", "vote", "rating", "response_text", "user_id", "session_id", "perspective",
            "routing_agent", "intent_override", "timestamp", "triage_status", "analysis_comments"}


@dataclass
class Line:
    file: str
    number: int
    record: Dict[str, Any]
    vote: int
    created_at: str
    item_id: str


@dataclass
class Report:
    dry_run: bool
    data_dir: str
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    #: Lines imported with a caveat (an unknown legacy triage status, imported as `new`).
    notes: List[Dict[str, Any]] = field(default_factory=list)
    imported: int = 0
    events: int = 0
    store: str = ""

    def skip(self, file: str, number: int, reason: str) -> None:
        self.skipped.append({"file": file, "line": number, "reason": reason})
        self.files[file]["skipped"] += 1

    def to_dict(self) -> Dict[str, Any]:
        reasons: Dict[str, int] = {}
        for s in self.skipped:
            key = s["reason"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        return {"dry_run": self.dry_run, "data_dir": self.data_dir, "store": self.store,
                "files": self.files, "imported": self.imported, "events": self.events,
                "skipped_by_reason": reasons, "skipped": self.skipped, "notes": self.notes}


def _utc(ts: str) -> Tuple[str, Optional[datetime]]:
    """The line's timestamp as an ISO string with a zone (EA wrote naive utcnow()), and parsed."""
    value = (ts or "").strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(), parsed


def item_id(file: str, rec: Dict[str, Any], vote: int) -> str:
    """Stable for a line's content, whatever later happened to its triage fields."""
    text = json.dumps([rec.get("user_id") or "", rec.get("query") or "", vote, rec.get("response_text") or ""],
                      ensure_ascii=False)
    key = f"{file}\n{rec.get('timestamp') or ''}\n{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    return ID_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _vote(rec: Dict[str, Any]) -> Optional[int]:
    v = rec.get("vote")
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v in (1, 0, -1):
        return v
    if v is None:
        return _VOTE_BY_RATING.get(rec.get("rating"))
    return None


def read_lines(path: Path, report: Report) -> List[Line]:
    """The importable lines of one file. Every other line is recorded in `report.skipped`."""
    name = path.name
    report.files[name] = {"path": str(path), "present": path.exists(), "read": 0, "candidates": 0, "skipped": 0}
    if not path.exists():
        return []
    out: List[Line] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for n, raw in enumerate(f, start=1):
            if not raw.strip():
                continue
            report.files[name]["read"] += 1
            try:
                rec = json.loads(raw)
            except ValueError as exc:
                report.skip(name, n, f"not JSON: {exc}")
                continue
            if not isinstance(rec, dict):
                report.skip(name, n, "not a JSON object")
                continue
            if not (rec.get("query") or "").strip():
                report.skip(name, n, "no question text")
                continue
            vote = _vote(rec)
            if vote is None:
                report.skip(name, n, f"unknown vote: vote={rec.get('vote')!r} rating={rec.get('rating')!r}")
                continue
            created_at, _ = _utc(str(rec.get("timestamp") or ""))
            if not created_at:
                report.skip(name, n, "no timestamp")
                continue
            out.append(Line(name, n, rec, vote, created_at, item_id(name, rec, vote)))
            report.files[name]["candidates"] += 1
    return out


def pair_duplicates(extended: List[Line], user: List[Line], report: Report) -> List[Line]:
    """The user_feedback lines that are NOT the same vote as an extended line (see the module doc)."""
    free = list(extended)
    keep: List[Line] = []
    for u in user:
        _, ut = _utc(str(u.record.get("timestamp") or ""))
        match = None
        if ut is not None:
            for e in free:
                _, et = _utc(str(e.record.get("timestamp") or ""))
                if (et is not None and abs((et - ut).total_seconds()) <= PAIR_WINDOW_S
                        and (e.record.get("user_id") or None) == (u.record.get("user_id") or None)
                        and (e.record.get("query") or "").strip() == (u.record.get("query") or "").strip()
                        and e.vote == u.vote):
                    match = e
                    break
        if match is not None:
            free.remove(match)
            report.skip(u.file, u.number, f"same vote as {match.file} line {match.number}")
        else:
            keep.append(u)
    return keep


def to_item(line: Line, app: str):
    from trellis_feedback import FeedbackItem
    rec = line.record
    extra = {k: v for k, v in rec.items() if k not in _COLUMNS and v not in (None, "", [])}
    extra.update(imported_from=line.file, line=line.number, intent_selected=rec.get("intent_override") or "")
    extra.pop("query_type", None)
    status = (rec.get("triage_status") or "new").strip() if line.file == EXTENDED else "new"
    if status == "new" and rec.get("analysis_comments"):
        extra["analysis_comments"] = rec["analysis_comments"]
    return FeedbackItem(
        app=app, kind="answer", vote=line.vote, query_text=rec["query"].strip(),
        response_text=rec.get("response_text") or "", user_id=rec.get("user_id") or "",
        session_id=rec.get("session_id") or "", perspective=rec.get("perspective") or "",
        # A-F1 N2: intent_label is the ROUTED intent (the line's query_type); the UI selector is
        # extra.intent_selected.
        routing_agent=rec.get("routing_agent") or "", intent_label=rec.get("query_type") or "",
        id=line.item_id, created_at=line.created_at, extra=extra,
    ), status


def run_import(store, data_dir: Path, *, dry_run: bool) -> Report:
    """Read both files under `data_dir/feedback` and import them into `store` (nothing written when
    `dry_run`: each line is then reported as would-import or already-imported)."""
    from trellis_feedback import STATUSES, FeedbackError
    report = Report(dry_run=dry_run, data_dir=str(data_dir))
    folder = Path(data_dir) / "feedback"
    extended = read_lines(folder / EXTENDED, report)
    user = pair_duplicates(extended, read_lines(folder / USER, report), report)
    for line in extended + user:
        item, status = to_item(line, store.app)
        unknown = status not in STATUSES
        legacy_status = status
        if unknown:
            item.extra["legacy_triage_status"] = legacy_status
            status = "new"
        if dry_run:
            if store.get(item.id) is not None:
                report.skip(line.file, line.number, "already imported")
            else:
                report.imported += 1
                report.events += int(status != "new")
            continue
        try:
            written = store.import_item(
                item, status=status, actor=ACTOR,
                note=(line.record.get("analysis_comments") or "") if status != "new" else "")
        except FeedbackError as exc:
            report.skip(line.file, line.number, f"refused by the store: {exc}")
            continue
        if not written:
            report.skip(line.file, line.number, "already imported")
            continue
        report.imported += 1
        report.events += int(status != "new")
        if unknown:
            report.notes.append({"file": line.file, "line": line.number,
                                 "note": f"unknown triage status {legacy_status!r}: imported as new"})
    return report
