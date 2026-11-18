"""
session_logger.py — Planning session transcript capture.

Saves every turn of a planning session (user message → system response) as a
structured JSONL file in ~/egeria-plans/sessions/{session_id}.jsonl.

Each line is one turn:
  {timestamp, role, content, phase, query_type, perspective, metadata}

A summary line is appended when the session reaches a terminal state.

Owned per user (SS-4, A-I1 2026-11-11): every entry written while a signed-in user is ambient
(advisor.request_context) carries "owner". A transcript's owner is its first entry's. The owner sees it,
an admin sees all; a transcript with no owner (written before SS-4, or in anonymous mode) is a private
conversation of unknown authorship, so only an admin sees it. Not visible reads as not found.

The session_id maps 1:1 to a draft_id for planning sessions.  For non-plan
queries the caller may pass any stable session identifier.

Usage:
  log = get_session_logger()
  log.log_turn(session_id, role="user",   content=user_msg,    phase="confirm_commands", ...)
  log.log_turn(session_id, role="system", content=response_md, phase="confirm_commands", ...)
  log.finalize(session_id, outcome="plan_generated", doc_id="...", perspective="data_steward")
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from loguru import logger


# ── Path resolution ───────────────────────────────────────────────────────────

def _sessions_path() -> Path:
    default = Path.home() / "egeria-plans" / "sessions"
    try:
        cfg_file = Path(__file__).parent / "configdata" / "advisor.yaml"
        with open(cfg_file) as f:
            cfg = yaml.safe_load(f)
        gp = cfg.get("governance_plans", {})
        p = Path(gp["sessions"]).expanduser() if "sessions" in gp else default
    except Exception:
        p = default
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── SessionLogger ─────────────────────────────────────────────────────────────

class SessionLogger:
    """Append-only JSONL session transcript logger."""

    def __init__(self) -> None:
        self._root = _sessions_path()

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_").replace("\\", "_")
        return self._root / f"{safe}.jsonl"

    # ── Public API ────────────────────────────────────────────────────────────

    def log_turn(
        self,
        session_id: str,
        role: str,                    # "user" | "system"
        content: str,
        phase: Optional[str] = None,
        query_type: Optional[str] = None,
        perspective: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one conversation turn to the session log."""
        entry: Dict[str, Any] = {
            "ts":          datetime.utcnow().isoformat() + "Z",
            "role":        role,
            "content":     content,
        }
        if phase:        entry["phase"]        = phase
        if query_type:   entry["query_type"]   = query_type
        if perspective:  entry["perspective"]  = perspective
        if metadata:     entry["meta"]         = metadata
        self._append(session_id, entry)

    def log_event(
        self,
        session_id: str,
        event: str,
        **kwargs: Any,
    ) -> None:
        """Append a lifecycle or correction event (not a conversation turn)."""
        entry: Dict[str, Any] = {
            "ts":    datetime.utcnow().isoformat() + "Z",
            "event": event,
            **kwargs,
        }
        self._append(session_id, entry)

    def finalize(
        self,
        session_id: str,
        outcome: str,                   # "plan_generated" | "executed" | "cancelled" | "abandoned"
        doc_id: Optional[str] = None,
        perspective: Optional[str] = None,
        user: Optional[str] = None,
        command_families: Optional[str] = None,
    ) -> None:
        """Append a terminal summary entry to close the session."""
        summary: Dict[str, Any] = {
            "ts":       datetime.utcnow().isoformat() + "Z",
            "event":    "session_end",
            "outcome":  outcome,
            "user":     user or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        }
        if doc_id:           summary["doc_id"]           = doc_id
        if perspective:      summary["perspective"]      = perspective
        if command_families: summary["command_families"] = command_families
        self._append(session_id, summary)
        logger.info(f"SessionLogger: session {session_id!r} finalised → {outcome}")

    @staticmethod
    def _visible(owner: Optional[str], user_id: Optional[str], role: Optional[str], all_users: bool) -> bool:
        if all_users or (role or "").lower() in ("admin", "curator"):
            return True
        return owner is not None and user_id is not None and owner == user_id

    def list_sessions(self, user_id: Optional[str] = None, role: Optional[str] = None,
                      all_users: bool = False) -> list:
        """Metadata for the sessions this requester may see, newest first: their own, or every session for
        an admin/curator role (or all_users=True, for non-request callers)."""
        entries = []
        for p in sorted(self._root.glob("*.jsonl"), reverse=True):
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
                if not lines:
                    continue
                first = json.loads(lines[0])
                last  = json.loads(lines[-1])
                if not self._visible(first.get("owner"), user_id, role, all_users):
                    continue
                entries.append({
                    "session_id":  p.stem,
                    "started_at":  first.get("ts", ""),
                    "last_at":     last.get("ts", ""),
                    "outcome":     last.get("outcome") if last.get("event") == "session_end" else None,
                    "user":        last.get("user") or first.get("meta", {}).get("user", ""),
                    "perspective": last.get("perspective") or first.get("perspective", ""),
                    "turns":       sum(1 for l in lines if '"role"' in l),
                    "owner":       first.get("owner"),
                })
            except Exception:
                pass
        return entries

    def load_session(self, session_id: str, user_id: Optional[str] = None, role: Optional[str] = None,
                     all_users: bool = True) -> list:
        """All entries for a session, or [] when it doesn't exist or this requester may not see it.
        all_users defaults to True for non-request callers; the web route passes the requester."""
        p = self._path(session_id)
        if not p.exists():
            return []
        entries = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
        if entries and not self._visible(entries[0].get("owner"), user_id, role, all_users):
            return []
        return entries

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append(self, session_id: str, entry: Dict[str, Any]) -> None:
        if "owner" not in entry:
            from advisor.request_context import current_user_id
            owner = current_user_id()
            if owner:
                entry = {**entry, "owner": owner}
        try:
            with open(self._path(session_id), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"SessionLogger: failed to write turn: {exc}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_logger: Optional[SessionLogger] = None


def get_session_logger() -> SessionLogger:
    global _logger
    if _logger is None:
        _logger = SessionLogger()
    return _logger
