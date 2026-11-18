"""When evidence was produced: the one way survey times are written (BRIEF-CONTEXT-SECTION-AS-OF.md, PR B).

A survey time says when an analysis result was produced. The context compile packs it as each section's as-of,
and the closed-loop diagnosis ages it. Until 2026-11 these times were written by `datetime.utcnow().isoformat()`:
UTC by construction, but without a zone, so nothing downstream could tell them from local time and the diagnosis
correctly refused to read them. They are now written with a zone (`+00:00`).

Stored values keep their form: old rows stay without a zone and are reported as unreadable, never assumed to be
UTC at read time. Rows of both forms order correctly as strings (the fixed-width date and time come first), so
`MAX(surveyed_at)` still picks the latest run. That holds only while every zone written is UTC (`+00:00`): a
different offset would sort by its local wall time, not by the instant. Write through this module and it is.

Egeria names stay naive on purpose, for every engine: a SurveyReport or Annotation qualified name built from a
survey time takes `egeria_name_time(...)` of it. `SurveyResult.surveyed_at` (repos) stays a naive datetime and
`survey_snapshot.to_result` strips the zone back off, so a kept survey published later gets the live run's
name; the database and filesystem surveyors build names from the stored string, through `egeria_name_time`. One
form across engines, and no `+` in a name that is also passed as a query parameter (where it would arrive as a
space).
"""
from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    """The current time, in UTC, with its zone."""
    return datetime.now(UTC)


def now_utc_iso() -> str:
    """The current time as an ISO string with a zone, e.g. `2026-11-10T12:00:00.123456+00:00`."""
    return now_utc().isoformat()


def as_utc_iso(moment: datetime) -> str:
    """`moment` as an ISO string with a zone. A naive datetime is taken to be UTC: callers pass only values they
    made with `datetime.utcnow()`, never a local time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat()


def egeria_name_time(value: str) -> str:
    """A survey time as it appears in an Egeria qualified name: naive UTC, the form every name had before survey
    times carried a zone. A zoned time is converted to UTC and its zone dropped; a naive one, or anything that is
    not an ISO time, is returned unchanged."""
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat()
