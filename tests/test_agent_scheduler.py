"""
Tests for the per-channel agent scheduler / coalescing layer in agent_runner.py.

Covers: idle-start (runs immediately), coalesce (a burst during a run collapses to exactly one
catch-up, not one reply per message), forcing (the catch-up bypasses the router only if something
in the burst addressed CLEO), clean release (the channel is freed after a cycle so the next trigger
starts fresh), and the debounce wait.

_run_ai_agent is mocked, so these exercise ONLY the scheduling logic, never the graph.
STREAM_* are set before importing agent_runner, which requires them at module load.
"""

import asyncio
import os
import time

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest

from src.api import agent_runner

CH_TYPE = "messaging"
CH = "chan-sched"


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """Isolate each test: clear coalescing state and default to a zero debounce (instant catch-up)."""
    agent_runner._active_channels.clear()
    agent_runner._pending_rerun.clear()
    agent_runner._pending_forced.clear()
    agent_runner._last_trigger_ts.clear()
    agent_runner._after_run_hooks.clear()
    saved = agent_runner.AGENT_DEBOUNCE_SECONDS
    agent_runner.AGENT_DEBOUNCE_SECONDS = 0.0
    yield
    agent_runner._active_channels.clear()
    agent_runner._pending_rerun.clear()
    agent_runner._pending_forced.clear()
    agent_runner._last_trigger_ts.clear()
    agent_runner._after_run_hooks.clear()
    agent_runner.AGENT_DEBOUNCE_SECONDS = saved


async def _drain(channel_id=CH, timeout=2.0):
    """Wait until the channel's runner has fully finished (released the channel)."""
    deadline = time.monotonic() + timeout
    while channel_id in agent_runner._active_channels:
        if time.monotonic() > deadline:
            raise AssertionError(f"runner for {channel_id} did not finish within {timeout}s")
        await asyncio.sleep(0.01)


class _FakeAgent:
    """Stand-in for _run_ai_agent. Records force_respond per call; when gate_first is set the FIRST
    call blocks on an event so the test can fire more triggers while a run is 'in flight'."""

    def __init__(self, gate_first=False):
        self.calls = []
        self.gate_first = gate_first
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, channel_type, channel_id, force_respond=False):
        self.calls.append(force_respond)
        if self.gate_first and len(self.calls) == 1:
            self.started.set()
            await self.release.wait()


@pytest.mark.asyncio
async def test_idle_start_runs_immediately(monkeypatch):
    agent = _FakeAgent()
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    await _drain()

    assert agent.calls == [True]
    assert CH not in agent_runner._active_channels
    assert CH not in agent_runner._pending_rerun


@pytest.mark.asyncio
async def test_addressed_triggers_during_run_coalesce_into_one_catchup(monkeypatch):
    agent = _FakeAgent(gate_first=True)
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)  # run 1 starts, then blocks
    await agent.started.wait()

    # Two more addressed messages land mid-run -> should collapse to a single catch-up.
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    assert CH in agent_runner._pending_rerun
    assert agent.calls == [True]  # still only run 1 so far

    agent.release.set()
    await _drain()

    assert agent.calls == [True, True]  # run 1 + exactly one catch-up (not three)
    assert CH not in agent_runner._active_channels
    assert CH not in agent_runner._pending_rerun


@pytest.mark.asyncio
async def test_unaddressed_trigger_during_run_gets_an_unforced_catch_up(monkeypatch):
    """Chatter during a run earns a catch-up, but an unforced one — the router still gates it.

    An idle channel already runs the graph on unaddressed messages, so dropping them mid-run made
    the same message answered or ignored depending on CLEO's timing. What must NOT change is the
    catch-up bypassing the router: force_respond stays False, or unaddressed chatter would start
    provoking replies it never did before.
    """
    agent = _FakeAgent(gate_first=True)
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)  # run 1 starts, blocks
    await agent.started.wait()

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=False)  # unaddressed chatter
    assert CH in agent_runner._pending_rerun
    assert CH not in agent_runner._pending_forced

    agent.release.set()
    await _drain()

    assert agent.calls == [True, False]


@pytest.mark.asyncio
async def test_one_addressed_message_forces_the_whole_coalesced_catch_up(monkeypatch):
    """A burst is one catch-up, so a single addressed message in it has to carry the whole burst."""
    agent = _FakeAgent(gate_first=True)
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)  # run 1 starts, blocks
    await agent.started.wait()

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=False)
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=False)

    agent.release.set()
    await _drain()

    assert agent.calls == [True, True]  # one catch-up, forced by the addressed message in the burst
    assert CH not in agent_runner._pending_forced


@pytest.mark.asyncio
async def test_channel_released_after_cycle_so_next_trigger_starts_fresh(monkeypatch):
    agent = _FakeAgent()
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    await _drain()
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=False)
    await _drain()

    assert agent.calls == [True, False]  # two independent runs, each honored


class _RecordingHook:
    """Stand-in for a state-writing follow-up (_run_governance_capture and friends). Records when it
    ran relative to the agent runs, so a test can assert it never overlapped one."""

    def __init__(self, log):
        self.log = log
        self.__name__ = "recording_hook"

    async def __call__(self, channel_type, channel_id):
        self.log.append("hook")


@pytest.mark.asyncio
async def test_after_run_hook_never_overlaps_a_run(monkeypatch):
    """The regression this whole mechanism exists for.

    A hook spawned as a parallel task calls graph.update_state while astream_events is mid-flight;
    the running loop then checkpoints the full pre-write snapshot over it and the write disappears.
    So the hook must run strictly BETWEEN runs, never interleaved with one.
    """
    log = []

    class _LoggingAgent(_FakeAgent):
        async def __call__(self, channel_type, channel_id, force_respond=False):
            log.append("run-start")
            await super().__call__(channel_type, channel_id, force_respond)
            log.append("run-end")

    agent = _LoggingAgent(gate_first=True)
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)
    hook = _RecordingHook(log)

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True, after_run=hook)
    await agent.started.wait()

    # An addressed message lands mid-run and queues the hook again: the run in flight must still
    # finish before anything writes state.
    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True, after_run=hook)
    assert log == ["run-start"]  # hook has NOT jumped the gun

    agent.release.set()
    await _drain()

    # The property that matters: never "run-start" -> "hook" -> "run-end". The hook sits strictly
    # between a run-end and the next run-start, so it can never write state under a live run.
    # Both queue attempts were drained together after run 1 (dedup), which is why the catch-up run
    # is followed by nothing — one hook call covers the whole coalesced burst.
    assert log == ["run-start", "run-end", "hook", "run-start", "run-end"]
    assert "hook" not in log[log.index("run-start", 1):]  # nothing interleaved into the catch-up
    assert CH not in agent_runner._after_run_hooks


@pytest.mark.asyncio
async def test_duplicate_hooks_in_one_drain_run_once(monkeypatch):
    """A burst queues the same hook repeatedly; each reads accumulated history, so one call covers
    them all. Running it per message would post duplicate confirm cards."""
    log = []
    agent = _FakeAgent()
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)
    hook = _RecordingHook(log)

    agent_runner._after_run_hooks[CH].extend([hook, hook, hook])
    await agent_runner._drain_after_run(CH_TYPE, CH)

    assert log == ["hook"]
    assert CH not in agent_runner._after_run_hooks


@pytest.mark.asyncio
async def test_failing_hook_does_not_strand_the_channel(monkeypatch):
    """A hook that raises is logged and skipped — the runner still owes the channel its release,
    and a later hook in the same drain still gets its turn."""
    log = []
    agent = _FakeAgent()
    monkeypatch.setattr(agent_runner, "_run_ai_agent", agent)

    async def boom(channel_type, channel_id):
        raise RuntimeError("hook exploded")

    good = _RecordingHook(log)
    agent_runner._after_run_hooks[CH].extend([boom, good])

    agent_runner._schedule_agent(CH_TYPE, CH, force_respond=True)
    await _drain()

    assert log == ["hook"]
    assert CH not in agent_runner._active_channels


@pytest.mark.asyncio
async def test_await_quiet_waits_for_debounce(monkeypatch):
    monkeypatch.setattr(agent_runner, "AGENT_DEBOUNCE_SECONDS", 0.05)
    agent_runner._last_trigger_ts[CH] = time.monotonic()

    start = time.monotonic()
    await agent_runner._await_quiet(CH)
    elapsed = time.monotonic() - start

    assert elapsed >= 0.04  # blocked for ~the debounce window before returning
