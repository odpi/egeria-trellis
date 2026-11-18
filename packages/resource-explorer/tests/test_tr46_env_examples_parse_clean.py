"""
TR-46: every .env.example in the repo parses to the values it means.

python-dotenv reads `KEY=   # a comment` (an EMPTY value followed by a comment) as the comment text. RE's
.env.example had three such lines (GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET, EMBEDDINGS__DEVICE), so a newcomer who
copied it got "# ..." as their GitHub token and `add` failed with an authentication error. Comments go on their
own line.
"""
from pathlib import Path

import pytest
from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = sorted(p for p in REPO.rglob(".env.example") if ".venv" not in p.parts and "node_modules" not in p.parts)


def test_there_are_examples_to_check():
    assert EXAMPLES, "no .env.example found: the scan ran nothing"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_value_is_a_comment(path):
    bad = [k for k, v in dotenv_values(path).items() if v is not None and v.lstrip().startswith("#")]
    assert not bad, f"{path.relative_to(REPO)}: these read as comment text, put the comment on its own line: {bad}"
