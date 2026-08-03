"""Tests for the catch-all failure notice on the background stage tasks.

Every stage's real work runs fire-and-forget, and an unhandled error used to be logged and
swallowed — the group saw a channel that had stopped talking, with nothing distinguishing
"thinking" from "dead". These pin the notice in place: it is the only in-channel record that a run
failed, and for a facilitated pilot it is the timestamped marker the transcript is read for.
"""

from unittest.mock import MagicMock, patch

import pytest

import src.api.reporters as reporters
from src.api.messages import UNEXPECTED_ERROR_MSG


def _capturing_client(sent: list):
    """A Stream client stub that records the text of every message sent."""

    def factory():
        channel = MagicMock()

        def send_message(payload, _user_id):
            sent.append(payload["text"])
            return {"message": {"id": "msg-1"}}

        channel.send_message = send_message
        client = MagicMock()
        client.channel = lambda _type, _id: channel
        return client

    return factory


# Each entry: (reporter coroutine, name of the lifecycle call it wraps).
_HANDLERS = [
    ("_run_generate_and_report", "run_generate_stage"),
    ("_run_deploy_and_report", "run_deploy_stage"),
    ("_run_provision_and_report", "run_provision_stage"),
    ("_run_governance_capture", "capture_governance"),
    ("_stand_down_and_report", "stand_down_provision"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name,stage_name", _HANDLERS)
async def test_unhandled_stage_error_is_reported_into_the_channel(handler_name, stage_name):
    sent: list[str] = []
    handler = getattr(reporters, handler_name)
    with patch.object(reporters, "get_stream_client", _capturing_client(sent)), \
         patch.object(reporters, stage_name, side_effect=RuntimeError("boom")):
        await handler("messaging", "ch1")

    assert UNEXPECTED_ERROR_MSG in sent, f"{handler_name} swallowed an unhandled error silently"


@pytest.mark.asyncio
async def test_the_notice_promises_no_retry_and_reassures_about_state():
    """The stage gates are reaction-anchored and already committed, so 'nudge me and I'll retry'
    would be a lie. What it must say is that nothing was published and nothing was lost."""
    assert "nothing your group has built was lost" in UNEXPECTED_ERROR_MSG
    assert "Nothing was published" in UNEXPECTED_ERROR_MSG
    for false_promise in ["try again", "retry", "nudge", "I'll pick"]:
        assert false_promise not in UNEXPECTED_ERROR_MSG


@pytest.mark.asyncio
async def test_the_reporter_never_raises_when_stream_itself_is_broken():
    """An exception in the error path would kill the task silently again — the exact failure this
    exists to remove."""
    with patch.object(reporters, "get_stream_client", side_effect=RuntimeError("stream down")):
        await reporters._report_unexpected("messaging", "ch1")  # must not raise


@pytest.mark.asyncio
async def test_expected_failures_keep_their_specific_copy():
    """A handled failure (no corpus) must not be downgraded to the generic notice."""
    sent: list[str] = []
    with patch.object(reporters, "get_stream_client", _capturing_client(sent)), \
         patch.object(reporters, "run_generate_stage", return_value={
             "status": "failed", "corpus_key": "k", "num_posts": 0, "report": None,
         }):
        await reporters._run_generate_and_report("messaging", "ch1")

    assert UNEXPECTED_ERROR_MSG not in sent
    assert any("couldn't pull posts" in text for text in sent)
