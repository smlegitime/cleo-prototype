"""Tests for the sandbox-executor bridge, incl. a real run against the built engine (skipped if
node / the labeler-engine dist aren't available)."""

import json
import shutil
from pathlib import Path

import pytest

from src.agent.lifecycle.bundle import materialize_bundle
from src.agent.lifecycle.sandbox import EXECUTE_JS, _exec_post, run_executor
from src.agent.spec import build_spec


def test_exec_post_marshals_uri_text_handle_and_account():
    assert _exec_post({"uri": "at://a", "text": "t", "handle": "h"}) == {
        "uri": "at://a", "text": "t", "handle": "h"
    }
    got = _exec_post({"uri": "at://a", "text": "t", "handle": "h", "account": {"follower_count": 3}})
    assert got["account"] == {"follower_count": 3}
    # unenriched post: no account key (lean payload)
    assert "account" not in _exec_post({"uri": "a", "text": "t", "handle": "h", "account": None})


def _spec():
    cfg = {"display_name": "Scam Watch", "description": "d",
           "labels": [{"identifier": "scam", "severity": "alert", "blurs": "content", "locales": []}]}
    rules = {"scam": {"include_groups": [{"all_of": [{"type": "keyword", "value": "dm me", "plain_name": None}]}],
                      "exclude_signals": [], "notes": None}}
    return build_spec(cfg, rules)


@pytest.mark.skipif(
    not EXECUTE_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine execute build or node not available",
)
def test_real_sandbox_run_emits_signed_records(tmp_path):
    handle = materialize_bundle(_spec(), "ch", base_dir=tmp_path)
    posts = [
        {"uri": "at://a/1", "text": "dm me for deals", "handle": "a.bsky.social"},
        {"uri": "at://b/2", "text": "just saying hi", "handle": "b.bsky.social"},
    ]
    summary = run_executor(handle["bundle_dir"], posts)

    assert summary["status"] == "succeeded"
    assert summary["did"].startswith("did:web:sandbox-cleo-")
    assert summary["records_emitted"] == 1 and summary["per_label"] == {"scam": 1}

    # identity is persisted at the channel dir, and the fired record is written + signed
    assert (tmp_path / "ch" / "identity.json").exists()
    rec = json.loads((Path(handle["bundle_dir"]) / "labels.jsonl").read_text().strip())
    assert rec["val"] == "scam" and rec["uri"] == "at://a/1"
    assert rec["src"] == summary["did"] and rec["sig"]


@pytest.mark.skipif(
    not EXECUTE_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine execute build or node not available",
)
def test_sandbox_identity_is_stable_across_runs(tmp_path):
    handle = materialize_bundle(_spec(), "ch", base_dir=tmp_path)
    posts = [{"uri": "at://a/1", "text": "dm me", "handle": "a"}]
    first = run_executor(handle["bundle_dir"], posts)
    second = run_executor(handle["bundle_dir"], posts)
    assert first["did"] == second["did"]           # same persistent per-channel identity


def _two_label_spec():
    """One label that will fire on the test posts, one that can't."""
    cfg = {"display_name": "Scam Watch", "description": "d", "labels": [
        {"identifier": "scam", "severity": "alert", "blurs": "content", "locales": []},
        {"identifier": "quiet", "severity": "inform", "blurs": "none", "locales": []},
    ]}
    rules = {
        "scam": {"include_groups": [{"all_of": [{"type": "keyword", "value": "dm me", "plain_name": None}]}],
                 "exclude_signals": [], "notes": None},
        "quiet": {"include_groups": [{"all_of": [{"type": "keyword", "value": "zzzznevermatches", "plain_name": None}]}],
                  "exclude_signals": [], "notes": None},
    }
    return build_spec(cfg, rules)


@pytest.mark.skipif(
    not EXECUTE_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine execute build or node not available",
)
def test_per_label_reports_zero_for_labels_that_never_fire(tmp_path):
    """A label that matched nothing must appear with a 0, not vanish from the summary — otherwise
    'my label isn't listed' and 'my label doesn't exist' look the same to the group."""
    handle = materialize_bundle(_two_label_spec(), "ch", base_dir=tmp_path)
    posts = [
        {"uri": "at://a/1", "text": "dm me for deals", "handle": "a.bsky.social"},
        {"uri": "at://b/2", "text": "just saying hi", "handle": "b.bsky.social"},
    ]
    summary = run_executor(handle["bundle_dir"], posts)

    assert summary["per_label"] == {"scam": 1, "quiet": 0}
    assert summary["records_emitted"] == 1  # zeros must not inflate the record count


@pytest.mark.skipif(
    not EXECUTE_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine execute build or node not available",
)
def test_per_label_is_all_zeros_when_nothing_matches(tmp_path):
    handle = materialize_bundle(_two_label_spec(), "ch", base_dir=tmp_path)
    summary = run_executor(handle["bundle_dir"], [{"uri": "at://c/3", "text": "hello", "handle": "c"}])

    assert summary["per_label"] == {"scam": 0, "quiet": 0}
    assert summary["records_emitted"] == 0
