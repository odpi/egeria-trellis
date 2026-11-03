"""2d: the tile's spec written as a Dr.Egeria report-spec document parses to the Python FormatSet."""
import importlib.util
from pathlib import Path

from trellis_analytics.portal import TILE_LABEL, report_specs

DOC = Path(__file__).resolve().parents[1] / "examples" / "curation_coverage_tile.md"


def load_parser():
    """The Advisor's parser module loaded by path: `import advisor` pulls the Advisor's settings and its heavy
    dependency set, which the parser does not need (it needs only pyegeria and md_processing). A missing file fails."""
    path = Path(__file__).resolve().parents[2] / "egeria-advisor" / "advisor" / "report_spec_parser.py"
    spec = importlib.util.spec_from_file_location("report_spec_parser_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_document_parses_to_the_python_formatset():
    parse_report_spec_markdown = load_parser().parse_report_spec_markdown
    parsed = parse_report_spec_markdown(DOC.read_text())
    expected = report_specs()[TILE_LABEL]
    assert parsed.model_dump() == expected.model_dump()
    # and the fields that make it a presentation spec are really there
    assert parsed.action.analytic_function == "curation_coverage_tile"
    assert parsed.action.analytic_spec_params == {"resource_type": "database"}
    assert [f.types for f in parsed.formats] == [["DICT", "JSON", "BAR"]]
