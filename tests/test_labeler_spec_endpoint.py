"""
Tests for GET /labeler-spec/{channel_id}.

The endpoint rebuilds the spec on demand from checkpoint state, so the test seeds graph state
via graph.update_state (MemorySaver, forced by conftest) and reads it back through the route.
STREAM_* are set before importing chatbot, which requires them at module load.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.chatbot import app
from src.agent.brainstorming.graph import graph
from src.agent.spec import build_spec

client = TestClient(app)

LABELER_CONFIG = {
    "display_name": "Test Labeler",
    "description": "A test labeler",
    "labels": [{"identifier": "spam", "severity": "alert"}],
}
RULES = {
    "spam": {
        "label_identifier": "spam",
        "include_groups": [{"all_of": [{"type": "keyword", "value": "buy now", "plain_name": "a sales pitch"}]}],
        "exclude_signals": [],
        "notes": None,
    },
}


def _seed(channel_id: str, **values):
    graph.update_state({"configurable": {"thread_id": channel_id}}, values)


def test_404_before_channel_enters_lifecycle():
    _seed("chan-setup", labeler_config=LABELER_CONFIG, setup_stage="rules")  # no lifecycle_stage
    res = client.get("/labeler-spec/chan-setup")
    assert res.status_code == 404


def test_404_for_unknown_channel():
    assert client.get("/labeler-spec/does-not-exist").status_code == 404


def test_returns_spec_once_in_preview():
    _seed(
        "chan-preview",
        labeler_config=LABELER_CONFIG,
        classification_rules=RULES,
        setup_stage="complete",
        lifecycle_stage="preview",
    )
    res = client.get("/labeler-spec/chan-preview")
    assert res.status_code == 200

    body = res.json()
    expected = build_spec(LABELER_CONFIG, RULES)
    assert body["spec_id"] == expected["spec_id"]
    assert body["labeler"]["display_name"] == "Test Labeler"
    assert [l["identifier"] for l in body["labels"]] == ["spam"]
    # the rule is nested under its label, and the keyword signal survives serialization
    assert body["labels"][0]["rule"]["include_groups"][0]["all_of"][0]["value"] == "buy now"


# ---- GET /preview-posts/{channel_id} ----

FAKE_POSTS = [
    {"name": "Coach A", "handle": "coacha", "text": "buy now, spots limited", "media": False},
    {"name": "Reg B", "handle": "regb", "text": "just a normal wellness post", "media": True},
]


def test_preview_posts_404_before_preview():
    _seed("pp-setup", labeler_config=LABELER_CONFIG, setup_stage="rules")  # no lifecycle_stage
    assert client.get("/preview-posts/pp-setup").status_code == 404


def test_preview_posts_generates_then_serves_from_cache():
    _seed("pp-gen", labeler_config=LABELER_CONFIG, classification_rules=RULES,
          setup_stage="complete", lifecycle_stage="preview")
    with patch("src.api.chatbot.generate_preview_posts", return_value=FAKE_POSTS) as gen:
        r1 = client.get("/preview-posts/pp-gen")
        r2 = client.get("/preview-posts/pp-gen")

    assert r1.status_code == 200
    assert r1.json()["posts"] == FAKE_POSTS
    assert r2.json()["posts"] == FAKE_POSTS
    assert gen.call_count == 1  # second request served from the spec_id-keyed cache


def test_preview_posts_regenerate_when_spec_changes():
    _seed("pp-stale", labeler_config=LABELER_CONFIG, classification_rules=RULES,
          setup_stage="complete", lifecycle_stage="preview",
          preview_posts={"spec_id": "sha256:stale", "posts": [{"name": "old", "handle": "o", "text": "x", "media": False}]})
    with patch("src.api.chatbot.generate_preview_posts", return_value=FAKE_POSTS) as gen:
        r = client.get("/preview-posts/pp-stale")

    assert r.json()["posts"] == FAKE_POSTS  # stale cache (different spec_id) ignored -> regenerated
    assert gen.call_count == 1


def test_preview_posts_empty_when_generation_fails():
    _seed("pp-fail", labeler_config=LABELER_CONFIG, classification_rules=RULES,
          setup_stage="complete", lifecycle_stage="preview")
    with patch("src.api.chatbot.generate_preview_posts", return_value=[]):
        r = client.get("/preview-posts/pp-fail")

    assert r.status_code == 200
    assert r.json()["posts"] == []  # frontend falls back to its static feed
