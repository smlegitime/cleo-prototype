"""Tests for the out-of-graph lifecycle orchestration: generate (corpus + quality report),
deploy (bundle + sandbox run), and provision (governance collection)."""

from unittest.mock import MagicMock, patch

import pytest

from src.agent.lifecycle import (
    capture_governance,
    run_deploy_stage,
    run_execute_stage,
    run_generate_stage,
    run_provision_stage,
    stand_down_provision,
)

CHANNEL_ID = "channel-1"
KEY = "corpus:sha256:abc"
REPORT = {"total": 2, "matched_any": 1, "matched_none": 1, "per_label": {}, "false_positive_candidates": []}


def _state(values):
    s = MagicMock()
    s.values = values
    return s


def _written(mock_graph):
    """Merge all update_state payloads into one dict (last write wins per key)."""
    merged = {}
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


def _patches(state_values, posts, *, report=REPORT, report_raises=False):
    """Patch graph/build_spec/corpus_key/fetch_corpus/build_quality_report for a run."""
    p_graph = patch("src.agent.lifecycle.orchestration.graph")
    p_spec = patch("src.agent.lifecycle.orchestration.build_spec", return_value={"spec_id": "sha256:x"})
    p_key = patch("src.agent.lifecycle.orchestration.corpus_key", return_value=KEY)
    p_fetch = patch("src.agent.lifecycle.orchestration.fetch_corpus", return_value=posts)
    p_report = patch(
        "src.agent.lifecycle.orchestration.build_quality_report",
        side_effect=RuntimeError("boom") if report_raises else None,
        return_value=None if report_raises else report,
    )
    mg, _, mk, mf, mq = p_graph.start(), p_spec.start(), p_key.start(), p_fetch.start(), p_report.start()
    mg.get_state.return_value = _state(state_values)
    return (p_graph, p_spec, p_key, p_fetch, p_report), mg, mf, mq


def _stop(patchers):
    for p in patchers:
        p.stop()


@pytest.mark.asyncio
async def test_skipped_when_not_in_generate():
    patchers, mg, mf, mq = _patches({"lifecycle_stage": "preview"}, [{"text": "x"}])
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "skipped"
    mf.assert_not_called()
    mq.assert_not_called()
    mg.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_fetches_caches_and_reports_when_no_cache():
    posts = [{"text": "a"}, {"text": "b"}]
    patchers, mg, mf, mq = _patches(
        {"lifecycle_stage": "generate", "labeler_config": {}, "classification_rules": {}}, posts
    )
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "succeeded"
    assert result["report"] == REPORT
    mf.assert_called_once()
    mq.assert_called_once()
    written = _written(mg)
    assert written["lifecycle_status"] == "succeeded"
    assert written["quality_corpus"]["posts"] == posts
    assert written["quality_report"]["total"] == 2
    assert "generated_at" in written["quality_report"]


@pytest.mark.asyncio
async def test_reuses_cache_but_still_reports():
    cached = {"corpus_key": KEY, "posts": [{"text": "cached"}], "fetched_at": "t"}
    patchers, mg, mf, mq = _patches({"lifecycle_stage": "generate", "quality_corpus": cached}, [{"text": "fresh"}])
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "succeeded"
    mf.assert_not_called()          # corpus reused
    mq.assert_called_once()         # but quality still (re)computed on the cached posts
    written = _written(mg)
    assert "quality_corpus" not in written  # cache untouched
    assert written["quality_report"]["total"] == 2


@pytest.mark.asyncio
async def test_refetches_when_cached_key_differs():
    cached = {"corpus_key": "corpus:stale", "posts": [{"text": "old"}], "fetched_at": "t"}
    posts = [{"text": "new"}]
    patchers, mg, mf, mq = _patches({"lifecycle_stage": "generate", "quality_corpus": cached}, posts)
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "succeeded"
    mf.assert_called_once()
    assert _written(mg)["quality_corpus"]["posts"] == posts


@pytest.mark.asyncio
async def test_failed_when_fetch_returns_no_posts():
    patchers, mg, mf, mq = _patches(
        {"lifecycle_stage": "generate", "labeler_config": {}, "classification_rules": {}}, []
    )
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "failed"
    mq.assert_not_called()          # no posts -> no evaluation
    written = _written(mg)
    assert written["lifecycle_status"] == "failed"
    assert written.get("lifecycle_error")
    assert "quality_corpus" not in written


@pytest.mark.asyncio
async def test_failed_when_quality_eval_raises():
    posts = [{"text": "a"}]
    patchers, mg, mf, mq = _patches(
        {"lifecycle_stage": "generate", "labeler_config": {}, "classification_rules": {}},
        posts, report_raises=True,
    )
    try:
        result = await run_generate_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "failed"
    written = _written(mg)
    # corpus was still cached before the eval failed, but no report and status is failed
    assert written["quality_corpus"]["posts"] == posts
    assert written["lifecycle_status"] == "failed"
    assert written.get("lifecycle_error")
    assert "quality_report" not in written


# --- deploy stage (bundle materialization) ---

_SPEC = {
    "spec_id": "sha256:dep",
    "labels": [
        {"identifier": "a", "rule": {"include_groups": [], "exclude_signals": []}},
        {"identifier": "b", "rule": None},
    ],
}
_HANDLE = {"bundle_dir": "/bundles/c/dep", "created_at": "2026-07-23T00:00:00+00:00"}


def _deploy_patches(state_values, *, materialize_raises=False):
    p_graph = patch("src.agent.lifecycle.orchestration.graph")
    p_spec = patch("src.agent.lifecycle.orchestration.build_spec", return_value=_SPEC)
    p_mat = patch(
        "src.agent.lifecycle.orchestration.materialize_bundle",
        side_effect=OSError("disk") if materialize_raises else None,
        return_value=None if materialize_raises else _HANDLE,
    )
    mg, _, mm = p_graph.start(), p_spec.start(), p_mat.start()
    mg.get_state.return_value = _state(state_values)
    return (p_graph, p_spec, p_mat), mg, mm


@pytest.mark.asyncio
async def test_deploy_skipped_when_not_in_deploy():
    patchers, mg, mm = _deploy_patches({"lifecycle_stage": "generate"})
    try:
        result = await run_deploy_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "skipped"
    mm.assert_not_called()
    mg.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_deploy_materializes_and_records_deployment():
    patchers, mg, mm = _deploy_patches(
        {"lifecycle_stage": "deploy", "labeler_config": {}, "classification_rules": {}}
    )
    try:
        result = await run_deploy_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "succeeded"
    assert result["labels"] == 2 and result["rules"] == 1  # only 'a' has a rule
    assert result["bundle_dir"] == _HANDLE["bundle_dir"]
    mm.assert_called_once()
    written = _written(mg)
    assert written["lifecycle_status"] == "succeeded"
    assert written["deployment"]["environment"] == "sandbox"
    assert written["deployment"]["deployed_spec_id"] == "sha256:dep"
    assert written["deployment"]["bundle_dir"] == _HANDLE["bundle_dir"]


@pytest.mark.asyncio
async def test_deploy_preserves_existing_deployment_fields():
    patchers, mg, mm = _deploy_patches(
        {"lifecycle_stage": "deploy", "deployment": {"handle": "cleo.bsky.social"}}
    )
    try:
        await run_deploy_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    # a prior deployment field is carried forward, not clobbered by the sandbox materialize
    assert _written(mg)["deployment"]["handle"] == "cleo.bsky.social"


@pytest.mark.asyncio
async def test_deploy_failed_when_materialize_raises():
    patchers, mg, mm = _deploy_patches(
        {"lifecycle_stage": "deploy", "labeler_config": {}, "classification_rules": {}},
        materialize_raises=True,
    )
    try:
        result = await run_deploy_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "failed"
    written = _written(mg)
    assert written["lifecycle_status"] == "failed"
    assert written.get("lifecycle_error")
    assert "deployment" not in written


# --- execute stage (sandbox run) ---

_RUN_SUMMARY = {
    "status": "succeeded", "did": "did:web:sandbox-cleo-abc", "total": 3,
    "records_emitted": 1, "per_label": {"a": 1}, "examples": [],
}


def _execute_patches(state_values, *, raises=False):
    p_graph = patch("src.agent.lifecycle.orchestration.graph")
    p_exec = patch(
        "src.agent.lifecycle.orchestration.run_executor",
        side_effect=RuntimeError("boom") if raises else None,
        return_value=None if raises else _RUN_SUMMARY,
    )
    mg, mx = p_graph.start(), p_exec.start()
    mg.get_state.return_value = _state(state_values)
    return (p_graph, p_exec), mg, mx


@pytest.mark.asyncio
async def test_execute_skipped_when_not_in_deploy():
    patchers, mg, mx = _execute_patches({"lifecycle_stage": "generate"})
    try:
        result = await run_execute_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "skipped"
    mx.assert_not_called()
    mg.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_execute_skipped_when_no_materialized_bundle():
    patchers, mg, mx = _execute_patches({"lifecycle_stage": "deploy", "deployment": {}})
    try:
        result = await run_execute_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "skipped"      # materialize must run first
    mx.assert_not_called()


@pytest.mark.asyncio
async def test_execute_runs_and_records_did_and_summary():
    patchers, mg, mx = _execute_patches({
        "lifecycle_stage": "deploy",
        "deployment": {"bundle_dir": "/b", "handle": "keep-me"},
        "quality_corpus": {"posts": [{"text": "x"}]},
    })
    try:
        result = await run_execute_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "succeeded" and result["records_emitted"] == 1
    mx.assert_called_once()
    written = _written(mg)
    assert written["lifecycle_status"] == "succeeded"
    assert written["deployment"]["labeler_did"] == "did:web:sandbox-cleo-abc"
    assert written["deployment"]["handle"] == "keep-me"        # existing field preserved
    assert written["sandbox_run"]["records_emitted"] == 1
    assert "generated_at" in written["sandbox_run"]


@pytest.mark.asyncio
async def test_execute_failed_when_executor_raises():
    patchers, mg, mx = _execute_patches(
        {"lifecycle_stage": "deploy", "deployment": {"bundle_dir": "/b"}, "quality_corpus": {"posts": []}},
        raises=True,
    )
    try:
        result = await run_execute_stage(CHANNEL_ID)
    finally:
        _stop(patchers)
    assert result["status"] == "failed"
    written = _written(mg)
    assert written["lifecycle_status"] == "failed"
    assert written.get("lifecycle_error")
    assert "sandbox_run" not in written


# ---- provision stage: opening the governance conversation and capturing answers ----

@pytest.mark.asyncio
async def test_run_provision_stage_skips_outside_provision():
    state = MagicMock()
    state.values = {"lifecycle_stage": "deploy"}
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        result = await run_provision_stage("ch1")
    assert result["status"] == "skipped"
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_run_provision_stage_records_handle_candidates():
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "provision",
        "labeler_config": {"display_name": "Wellness Watch", "labels": []},
        "classification_rules": {},
        "governance": {},
    }
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        result = await run_provision_stage("ch1")

    assert result["status"] == "succeeded"
    assert result["candidates"][0] == "wellness-watch.bsky.social"
    assert result["outstanding"] == ["handle", "custodian", "appeals"]
    payload = mock_graph.update_state.call_args.args[1]
    assert payload["provision_handle_candidates"] == result["candidates"]


@pytest.mark.asyncio
async def test_capture_governance_stages_new_answers():
    state = MagicMock()
    state.values = {"lifecycle_stage": "provision", "governance": {}, "messages": []}
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        extract.return_value = {"proposal": {"custodian_display_name": "Ama"}, "stand_down": False}
        result = await capture_governance("ch1")

    assert result["status"] == "staged"
    assert result["proposal"] == {"custodian_display_name": "Ama"}
    assert result["outstanding_after"] == ["handle", "appeals"]


@pytest.mark.asyncio
async def test_capture_governance_ignores_a_restatement_of_what_is_already_recorded():
    """Re-saying a settled answer must not raise a second confirm card to vote on."""
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "provision",
        "governance": {"custodian_display_name": "Ama"},
        "messages": [],
    }
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        extract.return_value = {"proposal": {"custodian_display_name": "Ama"}, "stand_down": False}
        result = await capture_governance("ch1")

    assert result["status"] == "none"
    assert result["proposal"] == {}


@pytest.mark.asyncio
async def test_capture_governance_stages_a_genuine_change():
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "provision",
        "governance": {"custodian_display_name": "Ama"},
        "messages": [],
    }
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        extract.return_value = {"proposal": {"custodian_display_name": "Ren"}, "stand_down": False}
        result = await capture_governance("ch1")

    assert result["status"] == "staged"
    assert result["proposal"] == {"custodian_display_name": "Ren"}


@pytest.mark.asyncio
async def test_capture_governance_skips_outside_provision():
    state = MagicMock()
    state.values = {"lifecycle_stage": "deploy", "governance": {}, "messages": []}
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        result = await capture_governance("ch1")

    assert result["status"] == "skipped"
    extract.assert_not_called()


@pytest.mark.asyncio
async def test_capture_governance_short_circuits_once_all_answers_are_in():
    """Without this guard a settled channel pays for one model call per message, forever, silently."""
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "provision",
        "governance": {
            "handle_choice": "x.bsky.social",
            "custodian_display_name": "Ama",
            "appeals_contact": "the mod team",
        },
        "messages": [],
    }
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        result = await capture_governance("ch1")

    assert result["status"] == "complete"
    extract.assert_not_called()


@pytest.mark.asyncio
async def test_capture_governance_reports_a_stand_down_without_staging_anything():
    state = MagicMock()
    state.values = {"lifecycle_stage": "provision", "governance": {}, "messages": []}
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph, \
         patch("src.agent.lifecycle.orchestration.extract_governance") as extract:
        mock_graph.get_state.return_value = state
        extract.return_value = {"proposal": {"custodian_display_name": "Ama"}, "stand_down": True}
        result = await capture_governance("ch1")

    # stopping wins over any answer in the same breath — don't raise a card they must now vote on
    assert result["status"] == "stand_down"
    assert result["proposal"] == {}


@pytest.mark.asyncio
async def test_stand_down_returns_the_channel_to_deploy_and_keeps_the_answers():
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "provision",
        "governance": {"custodian_display_name": "Ama"},
    }
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        result = await stand_down_provision("ch1")

    assert result["status"] == "succeeded"
    # answers already approved survive — parking must never read as losing them
    assert result["governance"] == {"custodian_display_name": "Ama"}
    payload = mock_graph.update_state.call_args.args[1]
    assert payload["lifecycle_stage"] == "deploy"
    assert payload["lifecycle_status"] == "succeeded"
    # the old anchor is committed and can never fire again, so it must be cleared for a fresh one
    assert payload["pending_provision_approval"] is None


@pytest.mark.asyncio
async def test_stand_down_is_a_noop_outside_provision():
    state = MagicMock()
    state.values = {"lifecycle_stage": "deploy", "governance": {}}
    with patch("src.agent.lifecycle.orchestration.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        result = await stand_down_provision("ch1")

    assert result["status"] == "skipped"
    mock_graph.update_state.assert_not_called()
