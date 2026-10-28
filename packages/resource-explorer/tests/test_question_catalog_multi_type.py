"""The question-catalog generator is no longer repo-only.

`docs/multi-resource-questions-design.md` §1.1 named five places RE hardcoded
`repo`; two of them are in `scripts/csv_to_question_catalog_yaml.py`:

  item 1  `_load_known_analysis_ids()` read only `repo_analyses`, so a
          database question naming `schema_inventory` -- a real entry in
          `database_analyses` -- got no `analysis_ids` and `kind: unknown`.
  item 2  `generate()` emitted a literal `{"repo_questions": ...}`.

The guard test (`test_question_catalog_generator_guard.py`) already pins that
the committed YAML is what the committed CSV generates. These tests pin the
behaviour that guard cannot see: what the generator does with a CSV row for a
resource type that has no authored rows in the real CSV yet, since stream 4's
database rows do not exist in this worktree.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"
CSV_PATH = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
YAML_PATH = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"


@pytest.fixture(scope="module")
def generator():
    """The real generator module, imported by path the way
    question_catalog_writer.py imports it."""
    spec = importlib.util.spec_from_file_location("csv_to_question_catalog_yaml_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def _rows() -> tuple[list[str], list[dict]]:
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def _as_csv(fieldnames: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return buf.getvalue()


class TestTheCsvGainedItsColumn:
    def test_resource_types_is_a_real_column(self):
        fieldnames, _ = _rows()
        assert "Resource Types" in fieldnames

    def test_every_row_declares_its_resource_types(self):
        _, rows = _rows()
        blank = [r["Question"] for r in rows if not (r.get("Resource Types") or "").strip()]
        assert blank == []

    def test_every_row_declares_a_real_resource_type_combination(self):
        # Stream 4 (re/db-questions-csv, 2026-09-21) landed the real
        # cross-type rewording and the database rows this class's own
        # docstring anticipated -- "repo" is no longer the only value, and
        # asserting it still is would be asserting the authoring stream never
        # happened. What still has to hold: every value parses under the
        # real vocabulary (parse_resource_types raises loudly otherwise, so
        # this is a smoke check, not a duplicate of that raise).
        from resource_explorer.resource_types import parse_resource_types

        _, rows = _rows()
        for r in rows:
            parse_resource_types(r["Resource Types"])

    def test_the_column_is_excluded_from_both_generators_perspective_scan(self, generator):
        """The by-elimination trap that produced 17 phantom Perspectives when
        "Catalog History" was added without this step."""
        assert "Resource Types" in generator.NON_PERSPECTIVE_COLUMNS

        spec = importlib.util.spec_from_file_location(
            "csv_to_dr_egeria_questions_test", ROOT / "scripts" / "csv_to_dr_egeria_questions.py",
        )
        dr_egeria = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(spec.name, dr_egeria)
        spec.loader.exec_module(dr_egeria)
        assert "Resource Types" in dr_egeria.OPTIONAL_LEAD_COLUMNS


class TestFunnelStageDroppedAutomate:
    """Decision (project owner, 2026-09-20), design §2.1: automation is a
    property of any survey, not a funnel stage. The one row that used it asks
    whether a re-run is worth paying for, which is a Discovery judgement."""

    def test_no_row_uses_automate_as_a_funnel_stage(self):
        _, rows = _rows()
        stages = {
            part.strip()
            for r in rows
            for part in (r.get("Funnel Stage") or "").split("/")
            if part.strip()
        }
        assert "Automate" not in stages

    def test_the_changed_since_survey_question_is_now_discovery(self):
        from resource_explorer.surveyors import question_catalog_reader as qcr

        qcr.clear_cache()
        entry = next(
            e for e in qcr.get_questions("repo")
            if e["question"].startswith("How much has changed since the last time")
        )
        assert entry["stage"] == "Discovery"


class TestRepoRoundTripsUnchanged:
    def test_the_committed_catalog_now_has_every_resource_type_section(self):
        # Before Stream 4 (re/db-questions-csv), the committed CSV was
        # 100% "repo" and this asserted exactly one section, exactly 52
        # entries. Stream 4 landed the real cross-type (`*`) rewording and
        # the database rows -- every declared resource type now has real,
        # non-empty authored content, which is the thing worth pinning now.
        from resource_explorer.resource_types import RESOURCE_TYPES

        raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
        for rt in RESOURCE_TYPES:
            key = f"{rt}_questions"
            assert key in raw, f"{key} missing -- no row declared this resource type"
            assert raw[key], f"{key} is empty"

    def test_a_repo_only_question_still_lands_only_in_repo(self):
        # Spot-checks that a question never touched by the cross-type
        # rewording (design §4 only reworded specific rows) still resolves
        # as repo-exclusive, not swept into every section by the split.
        raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
        from resource_explorer.resource_types import RESOURCE_TYPES

        target = "Is this repository actively maintained?"
        for rt in RESOURCE_TYPES:
            entries = raw.get(f"{rt}_questions", [])
            present = any(e["question"] == target for e in entries)
            assert present == (rt == "repo"), f"{rt}_questions: {present}"

    def test_generating_from_the_committed_csv_is_byte_identical(self, tmp_path):
        # The same guard test_question_catalog_generator_guard.py runs; kept
        # here too because this file's other tests mutate row copies, and a
        # reader of this file should see the invariant they are held against.
        out = tmp_path / "question_catalog.yaml"
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(CSV_PATH), "--output", str(out)],
            capture_output=True, text=True, timeout=120,
        )
        assert r.returncode == 0, r.stdout + r.stderr
        assert out.read_text(encoding="utf-8") == YAML_PATH.read_text(encoding="utf-8")


class TestAHypotheticalDatabaseRow:
    """Stream 4 (re/db-questions-csv) has since authored the real database
    rows -- these fixtures no longer stand in ahead of them, but stay as
    regression coverage for the generator's multi-type plumbing itself,
    independent of whatever the CSV's real row content happens to be. Each
    test appends its own synthetic row with distinctive question text and
    locates it by that text, rather than assuming it lands at any particular
    index or that its resource type's section is otherwise empty -- both
    became false the moment real rows existed."""

    def _generated(self, generator, extra: dict):
        fieldnames, rows = _rows()
        row = {c: "" for c in fieldnames} | {"Question ID": "Q-9001"}   # every row needs a stable id
        row.update(extra)
        return yaml.safe_load(generator.generate(rows + [row])), rows

    def test_a_database_row_gets_its_own_yaml_key(self, generator):
        raw, rows = self._generated(generator, {
            "Question": "Which schemas carry the data (hypothetical)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "database",
            "Answering Analysis": "schema_inventory",
            "Data Expert": "X",
        })
        assert "database_questions" in raw
        real_repo_count = sum(1 for r in rows if "repo" in r["Resource Types"].split(";") or r["Resource Types"] == "*")
        assert len(raw["repo_questions"]) == real_repo_count  # repo is untouched by this row
        entry = next(e for e in raw["database_questions"]
                     if e["question"] == "Which schemas carry the data (hypothetical)?")
        assert entry["perspectives"] == ["Data Expert"]

    def test_a_database_analysis_id_is_recognised_not_unknown(self, generator):
        """Design §1.1 item 1, the whole point: `schema_inventory` lives in
        `database_analyses`, and reading only `repo_analyses` made this row
        `kind: unknown` with no analysis_ids."""
        raw, _ = self._generated(generator, {
            "Question": "Which schemas carry the data (hypothetical)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "database",
            "Answering Analysis": "schema_inventory",
        })
        entry = next(e for e in raw["database_questions"]
                     if e["question"] == "Which schemas carry the data (hypothetical)?")
        assert entry["answering"]["kind"] == "analysis"
        assert entry["answering"]["analysis_ids"] == ["schema_inventory"]

    def test_schema_inventory_really_is_a_database_only_analysis(self):
        """Pins the premise of the test above -- if `schema_inventory` were
        also in `repo_analyses`, that test would pass for the wrong reason."""
        cat = yaml.safe_load(
            (ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml").read_text()
        )
        repo_ids = {a["id"] for a in cat["repo_analyses"]}
        db_ids = {a["id"] for a in cat["database_analyses"]}
        assert "schema_inventory" in db_ids
        assert "schema_inventory" not in repo_ids

    def test_a_star_row_lands_in_every_resource_type(self, generator):
        from resource_explorer.resource_types import RESOURCE_TYPES

        raw, _ = self._generated(generator, {
            "Question": "Who owns this data (hypothetical)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "*",
            "Answering Analysis": "GAP: Egeria Ownership read not built",
        })
        for rt in RESOURCE_TYPES:
            entries = raw[f"{rt}_questions"]
            assert any(e["question"] == "Who owns this data (hypothetical)?" for e in entries), rt

    def test_a_multi_type_row_lands_in_exactly_those_types(self, generator):
        raw, _ = self._generated(generator, {
            "Question": "What is the grain of each table (hypothetical)?",
            "Funnel Stage": "Discovery",
            "Resource Types": "database;filesystem",
            # Not a registered analysis/step id (gap guard, §C): a real one
            # here would now fail generation as a false gap. This fixture's
            # own point is cross-type row placement, not the answering note.
            "Answering Analysis": "GAP: grain_ranking_hypothetical not built",
        })
        target = "What is the grain of each table (hypothetical)?"
        assert any(e["question"] == target for e in raw["database_questions"])
        assert any(e["question"] == target for e in raw["filesystem_questions"])
        # Real rows may already populate dataset_questions/repo_questions --
        # what must hold is that THIS row didn't land in either.
        assert not any(e["question"] == target for e in raw.get("dataset_questions", []))
        assert not any(e["question"] == target for e in raw["repo_questions"])

    def test_a_cross_type_row_is_not_a_shared_yaml_anchor(self, generator):
        """`yaml.safe_dump` emits `&id001`/`*id001` for a repeated object, and
        two resource types would then share one mutable entry."""
        fieldnames, rows = _rows()
        row = {c: "" for c in fieldnames} | {"Question ID": "Q-9001"}   # every row needs a stable id
        row.update({
            "Question": "Who owns this data?",
            "Funnel Stage": "Scouting",
            "Resource Types": "database;filesystem",
            "Answering Analysis": "GAP: not built",
        })
        text = generator.generate(rows + [row])
        assert "&id00" not in text and "*id00" not in text

    def test_an_unknown_resource_type_fails_loudly(self, generator):
        fieldnames, rows = _rows()
        row = {c: "" for c in fieldnames} | {"Question ID": "Q-9001"}   # every row needs a stable id
        row.update({"Question": "Q?", "Funnel Stage": "Scouting", "Resource Types": "databse"})
        with pytest.raises(ValueError, match="databse"):
            generator.generate(rows + [row])

    def test_the_reader_sees_the_generated_database_section(self, generator, tmp_path):
        """End to end: a CSV row through the generator, through the reader,
        and out as an AUTHORED question set rather than a NOT_AUTHORED one."""
        from resource_explorer.surveyors import question_catalog_reader as qcr

        fieldnames, rows = _rows()
        row = {c: "" for c in fieldnames} | {"Question ID": "Q-9001"}   # every row needs a stable id
        row.update({
            "Question": "Which schemas carry the data (hypothetical)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "database",
            "Answering Analysis": "schema_inventory",
        })
        out = tmp_path / "question_catalog.yaml"
        out.write_text(generator.generate(rows + [row]), encoding="utf-8")

        loaded = qcr._load(out)
        assert "database" in loaded
        assert "Which schemas carry the data (hypothetical)?" in [
            e.question for e in loaded["database"]
        ]


class TestKnownAnalysisIdsSpanEverySection:
    def test_ids_from_all_three_sections_are_known(self, generator):
        known = set(generator.KNOWN_ANALYSIS_IDS)
        assert "repository_health" in known        # repo_analyses
        assert "schema_inventory" in known         # database_analyses
        assert "filesystem_inventory" in known     # filesystem_analyses

    def test_publish_actions_are_still_excluded(self, generator):
        assert "egeria_publish" not in generator.KNOWN_ANALYSIS_IDS

    def test_ids_are_deduped(self, generator):
        ids = generator.KNOWN_ANALYSIS_IDS
        assert len(ids) == len(set(ids))


class TestPerTypeAnalysisValidation:
    """2026-09-23: `_load_known_analysis_ids()`'s union (above) is right for
    classifying `kind` -- it must recognize an id from ANY resource type's
    section, or a real `database_analyses` id classifies as `kind: unknown`
    (that's the whole point of the class above). But `generate()` was using
    that SAME union to decide whether an already-computed `analysis_ids` list
    is valid when stamping a `*`/multi-type row into a *specific* type's
    section -- so a repo-only id like `repository_health` (reads git
    contributor history) "validated" straight into `database_questions`,
    `filesystem_questions`, `dataset_questions` and `model_questions`, none of
    which have git history. Found auditing the generated YAML against
    `get_analyses(resource_type)`'s real per-type ids (~15 bad rows, see the
    PR this landed with). These tests pin the fix: `_restrict_answering_to_type`
    checks each type's OWN section (`KNOWN_ANALYSIS_IDS_BY_TYPE`), not the
    union, and downgrades a now-empty `analysis`-kind entry to `gap` rather
    than silently keeping an id that cannot answer for that type.
    """

    def _generated_for(self, generator, extra: dict):
        fieldnames, rows = _rows()
        row = {c: "" for c in fieldnames} | {"Question ID": "Q-9001"}   # every row needs a stable id
        row.update(extra)
        return yaml.safe_load(generator.generate(rows + [row]))

    def test_a_repo_only_id_downgrades_to_gap_in_the_database_section(self, generator):
        raw = self._generated_for(generator, {
            "Question": "Who maintains this (hypothetical, repo-only id)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "*",
            "Answering Analysis": "repository_health",
        })
        entry = next(e for e in raw["database_questions"]
                     if e["question"] == "Who maintains this (hypothetical, repo-only id)?")
        assert entry["answering"]["kind"] == "gap"
        assert entry["answering"]["analysis_ids"] == []
        assert "repository_health" in entry["answering"]["note"]
        assert "not a real analysis for database" in entry["answering"]["note"]

    def test_the_same_row_stays_kind_analysis_in_the_repo_section(self, generator):
        raw = self._generated_for(generator, {
            "Question": "Who maintains this (hypothetical, repo-only id)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "*",
            "Answering Analysis": "repository_health",
        })
        entry = next(e for e in raw["repo_questions"]
                     if e["question"] == "Who maintains this (hypothetical, repo-only id)?")
        assert entry["answering"]["kind"] == "analysis"
        assert entry["answering"]["analysis_ids"] == ["repository_health"]

    def test_repository_health_really_is_repo_only(self):
        """Pins the premise of the two tests above."""
        cat = yaml.safe_load(
            (ROOT / "resource_explorer" / "configdata" / "analysis_catalog.yaml").read_text()
        )
        assert "repository_health" not in {a["id"] for a in cat.get("database_analyses", [])}
        assert "repository_health" not in {a["id"] for a in cat.get("filesystem_analyses", [])}

    def test_an_id_valid_for_the_stamped_type_is_kept_and_not_downgraded(self, generator):
        """A `*` row naming an id that DOES exist for a given type (here,
        `schema_inventory`, database-only) must not be touched for that type
        -- only ids absent from the type's own section get dropped."""
        raw = self._generated_for(generator, {
            "Question": "Which schemas exist (hypothetical, valid-for-type id)?",
            "Funnel Stage": "Scouting",
            "Resource Types": "database",
            "Answering Analysis": "schema_inventory",
        })
        entry = next(e for e in raw["database_questions"]
                     if e["question"] == "Which schemas exist (hypothetical, valid-for-type id)?")
        assert entry["answering"]["kind"] == "analysis"
        assert entry["answering"]["analysis_ids"] == ["schema_inventory"]

    def test_a_non_analysis_kind_just_loses_the_inapplicable_id(self, generator):
        """A `human`/`mixed`/etc. row never claimed the analysis alone answers
        the question, so an out-of-type id is dropped from the structured
        `analysis_ids` list without downgrading `kind` further -- there is
        nothing to downgrade it TO."""
        raw = self._generated_for(generator, {
            "Question": "Does it fit our security posture (hypothetical)?",
            "Funnel Stage": "Analysis/Enrichment",
            "Resource Types": "database",
            "Answering Analysis": (
                "N/A — human-supplied via Enrichment, informed by security_scan findings."
            ),
        })
        entry = next(e for e in raw["database_questions"]
                     if e["question"] == "Does it fit our security posture (hypothetical)?")
        assert entry["answering"]["kind"] == "human"
        assert entry["answering"]["analysis_ids"] == []

    def test_known_analysis_ids_by_type_has_one_entry_per_analyses_section(self, generator):
        by_type = generator.KNOWN_ANALYSIS_IDS_BY_TYPE
        assert "repository_health" in by_type["repo"]
        assert "schema_inventory" in by_type["database"]
        assert "filesystem_inventory" in by_type["filesystem"]
        assert "schema_inventory" not in by_type["repo"]
        assert "repository_health" not in by_type["database"]

    def test_the_committed_catalog_names_no_out_of_type_analysis_id(self):
        """Coverage guard: fails if ANY committed question entry, in ANY
        non-repo section, names an `analysis_ids` entry that is not present
        in that section's own `analysis_catalog.yaml` `<type>_analyses` list.
        This is the exact bug class the tests above pin at the unit level --
        this one guards the real, committed file so it cannot silently
        recur through a future CSV edit that nobody re-audits by hand."""
        from resource_explorer.resource_types import RESOURCE_TYPES
        from resource_explorer.surveyors.analysis_catalog_reader import get_analyses

        raw = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
        offenders = []
        for rt in RESOURCE_TYPES:
            valid = {a["id"] for a in get_analyses(rt)}
            for entry in raw.get(f"{rt}_questions", []):
                bad = [
                    aid for aid in (entry["answering"].get("analysis_ids") or [])
                    if aid not in valid
                ]
                if bad:
                    offenders.append((rt, entry["question"], bad))
        assert not offenders, (
            f"question(s) name an analysis_id that does not exist for their "
            f"resource type: {offenders}"
        )
