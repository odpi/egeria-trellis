"""TR-66: QUICKSTART and the deploy guide say what a fresh install really takes and use the right names.

Found by the 2026-11-19 fresh-Mac install: "Fifteen minutes" was about three hours; the schema command for the
egeria-workspaces path named the standalone `trellis-postgres` container; the deploy guide's "wait for" string never
appears in the log; and nobody said where the demo password lives.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = (ROOT / "QUICKSTART.md").read_text()
DEPLOY = (ROOT / "docs" / "deploying-trellis-with-quickstart.md").read_text()


def test_the_time_claim_is_measured_not_fifteen_minutes():
    head = QUICKSTART[:QUICKSTART.index("Trellis is a `uv` workspace")]
    assert "Fifteen minutes" not in QUICKSTART
    for measured in ("24 min", "34 min", "5.1 GB", "80 min", "2 h 55 min"):
        assert measured in head, f"the measured figure {measured!r} is missing from the opening"


def test_the_workspaces_path_uses_the_shared_postgres_container_for_the_schema():
    i = QUICKSTART.index("### 2. Start Postgres")
    step = QUICKSTART[i:QUICKSTART.index("### 3. Pull a model")]
    assert "docker exec egeria-shared-postgres psql" in step
    assert "docker exec trellis-postgres psql" in step                     # the standalone path keeps its own
    ws = step[:step.index("**Standalone**")]
    assert "trellis-postgres" not in ws                                     # the workspaces text no longer sends you there
    assert step.index("egeria-shared-postgres psql") < step.index("trellis-postgres psql")


def test_the_deploy_guides_start_string_is_the_one_the_log_prints():
    i = DEPLOY.index("### 1.2 What must already be running")
    section = DEPLOY[i:DEPLOY.index("### 1.3 Identities")]
    assert "successful start" not in section
    assert "SERVER-OPS-0004" in section and "successfully completed start up" in section and "qs-view-server" in section


def test_the_demo_password_location_is_named():
    for text in (QUICKSTART, DEPLOY):
        assert "QUICKSTART_PERSONA_PASSWORD" in text
    i = QUICKSTART.index("QUICKSTART_PERSONA_PASSWORD")
    assert ".env" in QUICKSTART[max(0, i - 300):i + 300] and "egeria-workspaces" in QUICKSTART[max(0, i - 600):i + 300]
