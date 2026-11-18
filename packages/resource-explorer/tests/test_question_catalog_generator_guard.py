"""The question catalog YAML is generated from its CSV and says so in its
own header. Nothing checked it: a hand-edit to the YAML passed 144 tests
on 2026-09-12 (caught in review of a peer's branch, not by the suite). The
survey definitions have had this guard since their generator landed --
test_survey_definition_generator_guard -- and this is the same shape for
the catalog: a clean tree regenerates to byte-identical output, so a YAML
that drifts from its CSV fails here and names the fix.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "csv_to_question_catalog_yaml.py"
CSV = ROOT / "docs" / "dr-egeria" / "resource_questions.csv"
YAML = ROOT / "resource_explorer" / "configdata" / "question_catalog.yaml"


def test_the_committed_catalog_is_what_its_csv_generates(tmp_path):
    out = tmp_path / "question_catalog.yaml"
    r = subprocess.run([sys.executable, str(SCRIPT), str(CSV), "--output", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    committed = YAML.read_text(encoding="utf-8")
    generated = out.read_text(encoding="utf-8")
    assert committed == generated, (
        "resource_explorer/configdata/question_catalog.yaml does not match what "
        "docs/dr-egeria/resource_questions.csv generates. Edit the CSV, then run\n"
        f"  python scripts/csv_to_question_catalog_yaml.py {CSV.relative_to(ROOT)} "
        f"--output {YAML.relative_to(ROOT)}\n"
        "-- never the YAML by hand."
    )


QUESTIONS_DOC = ROOT / "docs" / "dr-egeria" / "questions" / "scouting-questions.md"
QUESTIONS_SCRIPT = ROOT / "scripts" / "csv_to_dr_egeria_questions.py"
FOUNDATIONS = ROOT / "docs" / "dr-egeria" / "foundations"


def _perspectives_the_foundations_define() -> set[str]:
    """Every `Perspective::<name>` the foundations batch lists — the only names a
    `Link Perspective to Question` block may legitimately reference. Since
    2026-11-09 these are a `- `Perspective::<name>`` reference list (the
    elements are the content pack's), not `### Qualified Name` values of
    `Create Perspective` commands."""
    names: set[str] = set()
    for doc in FOUNDATIONS.glob("*.md"):
        for line in doc.read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("- ").strip("`")
            if line.startswith("Perspective::"):
                names.add(line)
    return names


def test_the_committed_questions_document_is_what_its_csv_generates(tmp_path):
    """Same guard as the YAML, for the Dr.Egeria document the questions batch
    executes — it is generated from the same CSV and its header says so."""
    out = tmp_path / "scouting-questions.md"
    r = subprocess.run([sys.executable, str(QUESTIONS_SCRIPT), str(CSV), "--output", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert QUESTIONS_DOC.read_text(encoding="utf-8") == out.read_text(encoding="utf-8"), (
        "docs/dr-egeria/questions/scouting-questions.md does not match what the CSV "
        "generates. Edit the CSV, then run\n"
        f"  python scripts/csv_to_dr_egeria_questions.py {CSV.relative_to(ROOT)} "
        f"--output {QUESTIONS_DOC.relative_to(ROOT)}\n-- never the document by hand."
    )


def test_every_perspective_the_document_links_exists_in_the_foundations():
    """The generator identifies perspective columns by elimination, so a CSV
    column missing from its OPTIONAL_LEAD_COLUMNS becomes a phantom
    Perspective on every question. Happened 2026-09-12: "Catalog History" was
    added to the CSV (#45) and the next regeneration (#58) emitted 17
    `Link Perspective to Question -> Perspective::Catalog History` blocks for a
    Perspective that does not exist. Caught two days later by reading a diff,
    not by any test — this is that test."""
    defined = _perspectives_the_foundations_define()
    assert defined, "no Perspective:: names found under docs/dr-egeria/foundations — the scan broke"
    linked: set[str] = set()
    text = QUESTIONS_DOC.read_text(encoding="utf-8")
    for block in text.split("\n___\n"):
        if "## Link Perspective to Question" not in block:
            continue
        after = block.split("### Perspective Name", 1)
        if len(after) == 2:
            linked.add(after[1].strip().splitlines()[0].strip())
    phantoms = sorted(linked - defined)
    assert not phantoms, (
        f"scouting-questions.md links Perspectives the foundations never list: {phantoms}. "
        "A CSV column is being read as a perspective — add it to OPTIONAL_LEAD_COLUMNS in "
        "scripts/csv_to_dr_egeria_questions.py and regenerate."
    )
