"""An outbox apply that fails with a pyegeria exception carrying Egeria's FFDC body stores Egeria's own
words in last_error, bounded, not only pyegeria's generic CLIENT_ERROR_400 text (which names only the URL).
The body is the live one captured 2026-10-14 from a DataGrainAnnotation create on qs-view-server."""
from __future__ import annotations

import json
from unittest.mock import create_autospec

import httpx
import pytest
from pyegeria import CollectionManager
from pyegeria.core._exceptions import PyegeriaClientException

import resource_explorer.egeria_outbox as outbox
from resource_explorer.registry import Project, ProjectRegistry

pytestmark = pytest.mark.usefixtures("as_daemon")

LIVE_BODY = {
    "class": "FFDCResponseBase", "relatedHTTPCode": 400,
    "exceptionClassName": "com.fasterxml.jackson.databind.exc.InvalidFormatException",
    "exceptionErrorMessage": "Cannot deserialize value of type `long` from String \"per-date\": not a valid `long` value"
                             + " x" * 400,   # long, as Egeria's are: last_error must stay bounded
    "exceptionSystemAction": "The system is unable to map the request to Egeria's implementation and returns with an error.",
    "exceptionUserAction": "Ensure JSON fields match the expected Java object structure and types.",
}


def _client_error():
    url = "https://localhost:9443/servers/qs-view-server/api/open-metadata/collection-manager/x"
    response = httpx.Response(400, json=LIVE_BODY, request=httpx.Request("POST", url))
    return PyegeriaClientException(response, {"class name": "BaseServerClient"},
                                   {"userid": "u", "reason": json.dumps(LIVE_BODY)})


@pytest.fixture
def reg(tmp_path):
    r = ProjectRegistry(db_path=str(tmp_path / "rel.db"))
    r.add(Project(slug="myproj", display_name="My Project",
                  github_url="https://github.com/test/myproj", description="A test repo."))
    return r


def test_last_error_carries_egerias_exception_error_message_bounded(reg):
    mgr = create_autospec(CollectionManager, instance=True, spec_set=True)
    mgr.add_to_collection.side_effect = _client_error()
    reg.enqueue_outbox_element("repo", "myproj", "collection_membership", "CollectionMembership::b::m",
                               {"collection_guid": "b", "member_guid": "m"})
    clients = outbox.OutboxClients(discovery=None, metadata_expert=None, collection_manager=mgr, acting_as="svc")
    outbox.drain_outbox(reg, clients, lambda qn: "")
    row = reg.list_outbox_elements()[0]
    assert mgr.add_to_collection.called
    assert row["status"] == "failed"
    err = row["last_error"]
    assert "Cannot deserialize value of type `long` from String \"per-date\"" in err
    assert "InvalidFormatException" in err
    detail = err.split("Egeria said: ", 1)[1]
    assert len(detail) <= 300
