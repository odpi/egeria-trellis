"""Which `Status` values in `docs/dr-egeria/resource_questions.csv` take a row out of the generated outputs.

The CSV is append-only: a question is never deleted and its id is never reused. Two kinds of Status mean the row
no longer produces a question in the YAML catalog or the Dr.Egeria document, while the row (and so its id) stays:

* `retired <date>: <reason>` and `merged into Q-00NN <date>` (owner ruling 2026-10-22,
  REVIEW-QUESTIONS-AND-ANALYTICS-2026-10-22.md): skipped from both outputs; the id is listed under
  `reserved_question_ids` in the YAML so a manifest that names it still names something real.
* the bare word `Retired`, which the Admin pane's write path (`question_catalog_writer.retire_question`) sets: the
  row stays in the YAML flagged `retired: true`, exactly as before.

One definition, imported by both generators, so the YAML and the Dr.Egeria document cannot disagree about which
rows are gone.
"""
from __future__ import annotations

#: The Admin pane's retirement marker. Kept in the YAML, flagged; not a skip.
WRITER_RETIRED = "retired"


def is_skipped_status(status: str | None) -> bool:
    """True when a row's Status takes it out of the generated outputs (retired with a date, or merged)."""
    text = (status or "").strip().lower()
    if not text or text == WRITER_RETIRED:
        return False
    return text.startswith("retired") or text.startswith("merged into")
