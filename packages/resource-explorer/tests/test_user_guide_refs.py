"""The user guide names only questions and analyses that exist.

Two checks, both docs tier:

  * every `Q-0NNN` the guide mentions is a row in `docs/dr-egeria/resource_questions.csv`
    (a retired or merged row still counts: its id never goes away);
  * every backticked snake_case token the guide uses as an analysis id is an `id:` in
    `resource_explorer/configdata/analysis_catalog.yaml`. A snake_case token that is not an
    analysis (a library step, a database question key, a code identifier) must be named in
    NOT_ANALYSES below, so adding one is a deliberate act and a mistyped analysis id fails.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
GUIDE = PACKAGE_ROOT / "docs" / "user-guide.md"
QUESTIONS = PACKAGE_ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
CATALOG = PACKAGE_ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml"

# Backticked snake_case words in the guide that are not analysis ids.
NOT_ANALYSES = {
    # the four file-system steps: generated catalog entries, so allowed either way
    "fs_change_rates", "fs_duplicates", "fs_activity", "fs_permissions",
    # database question keys on the Discovery stage
    "db_catalogued", "db_existing_use", "db_feedback", "db_surveyed", "db_disposition",
    # survey step names quoted from the screen
    "postgres_schema_and_stats", "postgres_column_profile",
    # annotation verdicts and properties
    "partial_match", "no_match", "unmatched_patterned", "not_sampled", "no_candidates",
    "not_applicable", "complexity_score", "join_count", "proposed_by",
    # urls and identifiers
    "re_database_staleness",
    # a registered file system used as the worked example
    "loading_bay",
}

TOKEN = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_every_question_id_in_the_guide_exists():
    known = {row["Question ID"] for row in csv.DictReader(QUESTIONS.open(encoding="utf-8"))}
    mentioned = set(re.findall(r"\bQ-0\d{3}\b", _guide()))
    assert mentioned, "the guide should name at least one question id"
    missing = sorted(mentioned - known)
    assert not missing, f"guide names question ids not in resource_questions.csv: {missing}"


def test_the_guide_names_no_analysis_id_absent_from_the_catalog():
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))
    entries = catalog if isinstance(catalog, list) else next(
        v for v in catalog.values() if isinstance(v, list)
    )
    ids = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
    assert ids, "analysis_catalog.yaml yielded no ids"
    unknown = sorted(
        t for t in set(TOKEN.findall(_guide())) if t not in ids and t not in NOT_ANALYSES
    )
    assert not unknown, (
        f"guide names snake_case ids that are neither catalog analyses nor in NOT_ANALYSES: {unknown}"
    )


def test_the_guide_keeps_its_ten_sections_in_order():
    titles = re.findall(r"^## (\d+)\. (.+)$", _guide(), flags=re.M)
    assert [(int(n), t) for n, t in titles] == [
        (1, "Start here"),
        (2, "The words and marks"),
        (3, "Investigations, scope and work lists"),
        (4, "A resource, stage by stage"),
        (5, "Repositories, databases and file systems"),
        (6, "Publishing to Egeria"),
        (7, "Extending and customizing"),
        (8, "Admin"),
        (9, "The CLI"),
        (10, "Not built yet"),
    ]


def test_the_guide_does_not_mention_the_old_route():
    assert "/next" not in _guide()
