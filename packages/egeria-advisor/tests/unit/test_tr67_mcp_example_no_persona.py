"""TR-67: the example MCP config a newcomer copies names no person and carries no password.

README.md tells a newcomer to `cp mcp_servers.json.example mcp_servers.json`. The example's "pyegeria" env used to say
`EGERIA_USER: erinoverview` (a persona: a person's identity) and `EGERIA_PASSWORD: secret`, so a copy ran EA's MCP server
as a persona with a password in a file. EA runs that server as its own service account (TR-34: EA_DAEMON_USER_ID /
EA_DAEMON_USER_PASSWORD in EA's .env) and drops any identity found in this JSON, so the example needs none, and says so.
"""
import json
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "advisor" / "configdata" / "mcp_servers.json.example"
README = Path(__file__).resolve().parents[4] / "README.md"
IDENTITY_VARS = ("EGERIA_USER", "EGERIA_PASSWORD", "EGERIA_USER_PASSWORD")


def _servers():
    return json.loads(EXAMPLE.read_text())["mcpServers"]


def test_the_example_is_still_valid_json_with_the_pyegeria_server():
    servers = _servers()
    assert "pyegeria" in servers
    assert servers["pyegeria"]["env"]["EGERIA_VIEW_SERVER"] and servers["pyegeria"]["env"]["EGERIA_VIEW_SERVER_URL"]


def test_no_server_in_the_example_sets_an_egeria_identity_or_password():
    for name, server in _servers().items():
        env = server.get("env", {})
        assert not [v for v in IDENTITY_VARS if v in env], (name, env)


def test_the_example_names_no_persona_and_no_secret_value():
    text = EXAMPLE.read_text()
    assert "erinoverview" not in text
    assert '"secret"' not in text


def test_the_example_says_the_server_runs_as_the_service_account():
    comment = _servers()["pyegeria"]["_comment"]
    assert "EA_DAEMON_USER_ID" in comment and "EA_DAEMON_USER_PASSWORD" in comment
    assert "never a person" in comment.lower() or "service account" in comment.lower()


def test_the_readme_says_the_same_where_it_tells_a_newcomer_to_copy_it():
    text = README.read_text()
    i = text.index("mcp_servers.json.example")
    nearby = text[i:i + 900]
    assert "service account" in nearby and "EA_DAEMON_USER_ID" in nearby
    assert "never as a person" in nearby and "EGERIA_USER" not in nearby      # a persona is named only to rule it out
