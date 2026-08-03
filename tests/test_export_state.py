"""Tests for the operator-only /export-state endpoint and its token gate.

The payload is a group's entire conversation and design, so the gate matters more than the dump:
an unset token must DISABLE the route, never open it. A deploy that forgets the env var has to
fail closed.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.api.chatbot as chatbot

TOKEN = "test-token-abc123"


def _client_with_state(values: dict, token: str | None = TOKEN):
    """A TestClient whose graph returns `values`, with ADMIN_TOKEN patched to `token`."""
    snapshot = MagicMock()
    snapshot.values = values
    graph = MagicMock()

    async def aget_state(_config):
        return snapshot

    graph.aget_state = aget_state
    return patch.multiple(chatbot, graph=graph, ADMIN_TOKEN=(token or ""))


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_export_returns_the_channel_state_keyed_by_channel_id():
    with _client_with_state({"setup_stage": "complete", "lifecycle_stage": "provision"}):
        with TestClient(chatbot.app) as client:
            res = client.get("/export-state/ch1", headers=_auth())

    assert res.status_code == 200
    # same shape scripts/export_checkpoints.py emits, so exports are comparable
    assert res.json() == {"ch1": {"setup_stage": "complete", "lifecycle_stage": "provision"}}


def test_non_serializable_state_is_stringified_rather_than_erroring():
    """State holds LangChain message objects; the export must not 500 on them."""

    class _Msg:
        def __repr__(self):
            return "HumanMessage(hello)"

    with _client_with_state({"messages": [_Msg()]}):
        with TestClient(chatbot.app) as client:
            res = client.get("/export-state/ch1", headers=_auth())

    assert res.status_code == 200
    assert res.json()["ch1"]["messages"] == ["HumanMessage(hello)"]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": TOKEN}],
    ids=["no header", "wrong token", "missing bearer scheme"],
)
def test_bad_or_missing_credentials_are_rejected(headers):
    with _client_with_state({"setup_stage": "complete"}):
        with TestClient(chatbot.app) as client:
            res = client.get("/export-state/ch1", headers=headers)

    assert res.status_code == 401


def test_an_unset_token_disables_the_route_instead_of_opening_it():
    """Fail closed: a deploy that forgets CLEO_ADMIN_TOKEN must not serve group state to anyone."""
    with _client_with_state({"setup_stage": "complete"}, token=None):
        with TestClient(chatbot.app) as client:
            unauthenticated = client.get("/export-state/ch1")
            with_a_guess = client.get("/export-state/ch1", headers=_auth(""))

    assert unauthenticated.status_code == 503
    assert with_a_guess.status_code == 503


def test_unknown_channel_is_404_not_an_empty_dump():
    with _client_with_state({}):
        with TestClient(chatbot.app) as client:
            res = client.get("/export-state/nope", headers=_auth())

    assert res.status_code == 404


def test_clear_channel_stays_ungated():
    """Deliberate: it backs the in-app Clear button, and a token compiled into the Vite bundle
    would be public anyway. If this ever starts 401ing, the button broke."""
    with _client_with_state({}):
        with TestClient(chatbot.app) as client:
            res = client.post("/clear-channel/dev2")  # protected channel, so no real truncation

    assert res.status_code != 401
