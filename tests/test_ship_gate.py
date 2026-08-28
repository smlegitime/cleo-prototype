"""
Tests for the ship gate posted after the rule-quality report (generate -> deploy).

A rule edit during `generate` re-runs the quality check, which posts a fresh report and a fresh
gate. pending_deploy_approval holds ONE record, so the previous anchor id is overwritten and
forgotten: without closing it, the old card stays in the scroll under a report that no longer
describes the rules, and a 👍🏾 on it falls through every gate and is answered with silence.

The ordering matters as much as the closing: the old gate is retired only once its replacement is
up, so a re-check that fails leaves the group a working card rather than none at all.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.api import reporters
from src.api.messages import DEPLOY_GATE_SUPERSEDED_NOTE

CHANNEL_ID = "ch1"
OLD_GATE = "msg-old-gate"
NEW_GATE = "msg-new-gate"

_SUCCEEDED = {"status": "succeeded", "corpus_key": "k", "num_posts": 12, "report": {"labels": {}}}
_FAILED = {"status": "failed", "corpus_key": "k", "num_posts": 0, "report": None}


def _channel() -> MagicMock:
    channel = MagicMock()
    channel.send_message.return_value = {"message": {"id": NEW_GATE}}
    return channel


def _graph(pending_deploy_approval) -> MagicMock:
    mock_graph = MagicMock()
    state = MagicMock()
    state.values = {"pending_deploy_approval": pending_deploy_approval}
    mock_graph.get_state.return_value = state
    return mock_graph


async def _regenerate(pending_deploy_approval, result=_SUCCEEDED) -> AsyncMock:
    """Run the generate reporter over a channel holding this deploy gate; return the close mock."""
    close = AsyncMock()
    client = MagicMock()
    client.channel = lambda _type, _id: _channel()

    with patch.object(reporters, "run_generate_stage", return_value=result), \
         patch.object(reporters, "get_stream_client", return_value=client), \
         patch.object(reporters, "graph", _graph(pending_deploy_approval)), \
         patch.object(reporters, "format_report_summary", return_value="a summary"), \
         patch.object(reporters, "close_path_in_stream", close):
        await reporters._run_generate_and_report("messaging", CHANNEL_ID)

    return close


@pytest.mark.asyncio
async def test_a_re_check_closes_the_gate_it_replaces():
    close = await _regenerate({"message_id": OLD_GATE, "approved_by": []})

    close.assert_awaited_once_with(OLD_GATE, DEPLOY_GATE_SUPERSEDED_NOTE)


@pytest.mark.asyncio
async def test_a_gate_carrying_votes_is_still_closed():
    """Votes cast on the old report don't carry over to a design they weren't cast on."""
    close = await _regenerate({"message_id": OLD_GATE, "approved_by": ["user-1"]})

    close.assert_awaited_once_with(OLD_GATE, DEPLOY_GATE_SUPERSEDED_NOTE)


@pytest.mark.asyncio
async def test_the_first_pass_through_generate_has_nothing_to_close():
    close = await _regenerate(None)

    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_gate_the_group_already_used_is_not_closed_again():
    """Committed means the group shipped through it; it is already retagged as approved, and
    overwriting that with 'superseded' would rewrite what they did."""
    close = await _regenerate({"message_id": OLD_GATE, "approved_by": ["u1", "u2"], "committed": True})

    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_re_check_leaves_the_old_gate_working():
    """The failure path posts CORPUS_FAILED_MSG and registers no replacement. Closing the old card
    here would leave the group at `generate` with no way forward until a later run succeeded."""
    close = await _regenerate({"message_id": OLD_GATE, "approved_by": []}, result=_FAILED)

    close.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_new_gate_is_the_one_registered_for_votes():
    mock_graph = _graph({"message_id": OLD_GATE, "approved_by": []})
    client = MagicMock()
    client.channel = lambda _type, _id: _channel()

    with patch.object(reporters, "run_generate_stage", return_value=_SUCCEEDED), \
         patch.object(reporters, "get_stream_client", return_value=client), \
         patch.object(reporters, "graph", mock_graph), \
         patch.object(reporters, "format_report_summary", return_value="a summary"), \
         patch.object(reporters, "close_path_in_stream", AsyncMock()):
        await reporters._run_generate_and_report("messaging", CHANNEL_ID)

    written = mock_graph.update_state.call_args[0][1]
    assert written["pending_deploy_approval"] == {"message_id": NEW_GATE, "approved_by": []}


def test_the_closed_gate_says_where_the_live_one_is():
    """Someone scrolling back lands on the old card; it has to point them forward, not just grey."""
    assert "closed" in DEPLOY_GATE_SUPERSEDED_NOTE
    assert "further down" in DEPLOY_GATE_SUPERSEDED_NOTE
