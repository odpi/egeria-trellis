"""TR-54 W5 (on top of TR-147): B4's presses and its runs view obey the investigation guard.

B4 turned scout, profile and refresh into presses that queue one run per dataset, and added GET /work-lists/{slug}/runs. Both take a
work-list slug. A list linked to an investigation the caller cannot see is a 404 in the words of a list that is not there, and a refused
press queues NOTHING (no run row, no activity entry): a press is a write, and it costs portal calls.
Same fixtures as TR-147's tests: A's creator-only investigation and its work lists, B who cannot see them, an anonymous caller.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_tr147_investigation_visibility_guard import A, B, api, world  # noqa: E402,F401  (fixtures)
from test_open_data import reg, store  # noqa: E402,F401

PRESSES = [("scout", "external_data_scout"), ("refresh", "external_data_refresh"), ("profile", "external_data_profile")]


def _queued(reg):
    with reg._conn() as conn:
        runs = conn.execute("SELECT COUNT(*) FROM runs WHERE kind = 'open_data_analysis_run'").fetchone()[0]
        activity = conn.execute("SELECT COUNT(*) FROM activity_log WHERE entity_type = 'open_data'").fetchone()[0]
    return runs, activity


@pytest.mark.usefixtures("mock_egeria_client_connections")
@pytest.mark.parametrize("verb,analysis_id", PRESSES)
def test_b_pressing_on_a_private_list_gets_the_missing_list_404_and_nothing_is_queued(api, reg, world, verb, analysis_id):
    client, tokens = api
    private = world["private_data"]
    before = _queued(reg)
    hidden = client.post(f"/api/open-data/work-lists/{private}/{verb}", headers=tokens[B])
    missing = client.post(f"/api/open-data/work-lists/no-such-list/{verb}", headers=tokens[B])
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(private, "X") == missing.json()["detail"].replace("no-such-list", "X")
    assert _queued(reg) == before                                      # no run row, no activity entry: nothing queued, nothing priced


@pytest.mark.usefixtures("mock_egeria_client_connections")
@pytest.mark.parametrize("verb,analysis_id", PRESSES)
def test_a_pressing_on_their_own_private_list_queues_its_run(api, reg, world, verb, analysis_id):
    client, tokens = api
    before_runs, _ = _queued(reg)
    r = client.post(f"/api/open-data/work-lists/{world['private_data']}/{verb}", headers=tokens[A])
    assert r.status_code == 200, r.text
    assert (r.json()["queued"], r.json()["analysis_id"]) == (1, analysis_id)
    assert _queued(reg)[0] == before_runs + 1


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_an_anonymous_press_is_a_401_and_queues_nothing(api, reg, world):
    client, _ = api
    before = _queued(reg)
    assert client.post(f"/api/open-data/work-lists/{world['private_data']}/scout").status_code == 401
    assert _queued(reg) == before


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_the_runs_view_of_a_private_list_is_a_404_for_b_in_a_missing_lists_words_and_200_for_a(api, world):
    client, tokens = api
    private = world["private_data"]
    path = lambda slug: f"/api/open-data/work-lists/{slug}/runs?analysis_id=external_data_scout"        # noqa: E731
    hidden = client.get(path(private), headers=tokens[B])
    missing = client.get(path("no-such-list"), headers=tokens[B])
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(private, "X") == missing.json()["detail"].replace("no-such-list", "X")
    assert "title" not in hidden.text and "summary" not in hidden.text           # nothing about the list's datasets
    seen = client.get(path(private), headers=tokens[A])
    assert seen.status_code == 200 and len(seen.json()["runs"]) == 1
    assert client.get(path(private)).status_code == 401                          # nobody signed in


@pytest.mark.usefixtures("mock_egeria_client_connections")
def test_a_list_with_no_investigation_is_pressed_and_read_by_anyone_signed_in(api, reg, world):
    client, tokens = api
    for user in (A, B):
        press = client.post(f"/api/open-data/work-lists/{world['plain']}/refresh", headers=tokens[user])
        assert press.status_code == 200
        assert client.get(f"/api/open-data/work-lists/{world['plain']}/runs?analysis_id=external_data_refresh",
                          headers=tokens[user]).status_code == 200
