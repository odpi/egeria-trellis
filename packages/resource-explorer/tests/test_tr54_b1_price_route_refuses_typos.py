"""W-B1-1: the price route answers an analysis id that is not declared with a 400 in the refusal's own words, as the run route does, not with
"has no reader yet" (which is true only of an analysis that IS declared and has no runner). A typo must read as a typo."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_tr54_b1_open_data_analysis_run import api, dataset  # noqa: E402,F401  (fixtures)
from test_open_data import reg, store  # noqa: E402,F401

from resource_explorer.open_data import analyses  # noqa: E402


@pytest.mark.usefixtures("signed_in_caller")
def test_a_typo_is_a_400_in_the_refusals_words_the_same_as_the_run_route_says(api, dataset):
    client, headers = api
    price = client.get(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_licence_usabel/price", headers=headers)
    run = client.post(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_licence_usabel/run", headers=headers)
    assert price.status_code == run.status_code == 400
    assert price.json()["detail"] == run.json()["detail"] == analyses.refusal("od_licence_usabel")
    assert "is not an open data analysis" in price.json()["detail"] and "od_licence_usable" in price.json()["detail"]
    assert "has no reader yet" not in price.json()["detail"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_declared_and_built_analysis_is_still_priced(api, dataset):
    client, headers = api
    r = client.get(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_licence_usable/price", headers=headers)
    assert r.status_code == 200 and r.json()["priced"] is True and "no portal call" in r.json()["line"]


@pytest.mark.usefixtures("signed_in_caller")
def test_a_declared_analysis_with_no_runner_is_still_not_priced_never_zero(api, dataset, monkeypatch):
    monkeypatch.delitem(analyses.BUILT_IN, "od_profile")                     # declared, and no reader: the one case the old words are true of
    client, headers = api
    r = client.get(f"/api/open-data/datasets/{dataset['slug']}/analyses/od_profile/price", headers=headers)
    assert r.status_code == 200 and r.json()["priced"] is False and r.json()["calls"] is None
    assert "has no reader yet" in r.json()["line"]


@pytest.mark.usefixtures("signed_in_caller")
def test_an_unknown_dataset_is_still_a_404(api):
    client, headers = api
    assert client.get("/api/open-data/datasets/ghost/analyses/od_licence_usable/price", headers=headers).status_code == 404
