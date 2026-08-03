"""
Tests for POST /token — how a user joins one pilot group's channel.

The invariants that matter for running groups in parallel: a join code only ever resolves to an
allowlisted channel (never creates one), a user is added to that channel alone, and identical
display names in different channels stay different Stream users.

STREAM_* are set before importing chatbot, which requires them at module load.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.chatbot import app
from src.api.stream import AI_USER_ID, channel_user_id, resolve_channel

client = TestClient(app)

CHANNELS = ["sourdough-pilot", "tech-pilot", "general"]


@pytest.fixture
def stream_client():
    """A stubbed StreamChat whose channel() calls are recorded, wired into /token."""
    fake = MagicMock()
    fake.create_token.return_value = "jwt-token"
    fake.query_users.return_value = {"users": []}
    fake.channel.side_effect = lambda _type, ch_id: MagicMock(name=f"channel:{ch_id}")

    with patch("src.api.chatbot.get_stream_client", return_value=fake), \
         patch("src.api.stream.STREAM_CHANNELS", CHANNELS), \
         patch("src.api.stream.DEFAULT_CHANNEL_ID", "general"):
        yield fake


def _joined_channels(fake) -> list[str]:
    return [call.args[1] for call in fake.channel.call_args_list]


# --- resolve_channel ---

def test_resolve_channel_matches_allowlist_case_insensitively():
    with patch("src.api.stream.STREAM_CHANNELS", CHANNELS):
        assert resolve_channel("sourdough-pilot") == "sourdough-pilot"
        assert resolve_channel("  Sourdough-Pilot  ") == "sourdough-pilot"


def test_resolve_channel_rejects_unknown_code():
    with patch("src.api.stream.STREAM_CHANNELS", CHANNELS):
        assert resolve_channel("sourdogh-pilot") is None


def test_resolve_channel_falls_back_to_default_when_code_missing():
    with patch("src.api.stream.STREAM_CHANNELS", CHANNELS), \
         patch("src.api.stream.DEFAULT_CHANNEL_ID", "general"):
        assert resolve_channel(None) == "general"
        assert resolve_channel("   ") == "general"


# --- channel_user_id ---

def test_same_name_in_two_channels_is_two_users():
    assert channel_user_id("sourdough-pilot", "Sam") != channel_user_id("tech-pilot", "Sam")


def test_channel_user_id_is_deterministic_and_id_safe():
    first = channel_user_id("tech-pilot", "Sam O'Neill")
    assert first == channel_user_id("tech-pilot", "Sam O'Neill")
    assert len(first) <= 64
    assert all(c.isalnum() or c == "-" for c in first)


# --- POST /token ---

def test_join_code_joins_only_that_channel(stream_client):
    res = client.post("/token", json={"user_name": "Sam", "channel_id": "sourdough-pilot"})

    assert res.status_code == 200
    body = res.json()
    assert body["channel_id"] == "sourdough-pilot"
    assert body["token"] == "jwt-token"
    assert _joined_channels(stream_client) == ["sourdough-pilot"]


def test_unknown_join_code_is_refused_and_creates_nothing(stream_client):
    res = client.post("/token", json={"user_name": "Sam", "channel_id": "not-a-group"})

    assert res.status_code == 404
    assert "join code" in res.json()["detail"]
    assert _joined_channels(stream_client) == []
    stream_client.upsert_user.assert_not_called()


def test_missing_code_lands_in_default_channel(stream_client):
    res = client.post("/token", json={"user_name": "Sam"})

    assert res.status_code == 200
    assert res.json()["channel_id"] == "general"
    assert _joined_channels(stream_client) == ["general"]


def test_same_display_name_across_groups_gets_distinct_user_ids(stream_client):
    a = client.post("/token", json={"user_name": "Sam", "channel_id": "sourdough-pilot"}).json()
    b = client.post("/token", json={"user_name": "Sam", "channel_id": "tech-pilot"}).json()

    assert a["user_id"] != b["user_id"]
    assert a["user_name"] == b["user_name"] == "Sam"


def test_rejoining_same_channel_resumes_same_identity(stream_client):
    first = client.post("/token", json={"user_name": "Sam", "channel_id": "tech-pilot"}).json()
    second = client.post("/token", json={"user_name": "Sam", "channel_id": "tech-pilot"}).json()

    assert first["user_id"] == second["user_id"]


def test_ai_assistant_is_added_alongside_the_user(stream_client):
    client.post("/token", json={"user_name": "Sam", "channel_id": "tech-pilot"})

    upserted = [call.args[0]["id"] for call in stream_client.upsert_user.call_args_list]
    assert AI_USER_ID in upserted


def test_blank_display_name_rejected(stream_client):
    assert client.post("/token", json={"user_name": "   "}).status_code == 400


def test_join_all_channels_mode_keeps_legacy_demo_behaviour(stream_client):
    """The demo deployment still puts one global user in every allowlisted channel."""
    stream_client.query_users.return_value = {"users": [{"id": "sam", "name": "Sam"}]}

    with patch("src.api.chatbot.JOIN_ALL_CHANNELS", True), \
         patch("src.api.chatbot.STREAM_CHANNELS", CHANNELS):
        res = client.post("/token", json={"user_name": "Sam", "channel_id": "tech-pilot"})

    assert res.status_code == 200
    assert res.json()["user_id"] == "sam"  # reused, not channel-scoped
    assert _joined_channels(stream_client) == CHANNELS
