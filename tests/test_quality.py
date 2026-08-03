"""Tests for rule-quality aggregation (the Node interpreter call is mocked out), plus a real
cross-language check that account signals fire through the actual interpreter when it's built."""

import shutil
from unittest.mock import patch

import pytest

from src.agent.lifecycle.quality import BATCH_JS, _subject, build_quality_report, evaluate_corpus, format_report_summary

SPEC = {
    "labels": [
        {"identifier": "misinfo", "rule": {"include_groups": [], "exclude_signals": []}},
        {"identifier": "harass", "rule": {"include_groups": [], "exclude_signals": []}},
        {"identifier": "no_rule", "rule": None},  # rule-less: never a column in the report
    ]
}


def _post(text, handle="u", bucket="trigger", query="q"):
    return {"text": text, "handle": handle, "bucket": bucket, "query": query}


def test_build_report_counts_and_examples():
    posts = [_post("a"), _post("b"), _post("c"), _post("d")]
    fired = [["misinfo"], [], ["misinfo", "harass"], []]
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=fired):
        report = build_quality_report(SPEC, posts)
    assert report["total"] == 4
    assert report["matched_any"] == 2
    assert report["matched_none"] == 2
    assert report["per_label"]["misinfo"]["count"] == 2
    assert report["per_label"]["harass"]["count"] == 1
    assert "no_rule" not in report["per_label"]  # rule-less label excluded
    assert report["per_label"]["misinfo"]["examples"][0]["text"] == "a"


def test_context_bucket_hits_are_false_positive_candidates():
    posts = [_post("benign on-topic", bucket="context"), _post("triggering", bucket="trigger")]
    fired = [["misinfo"], ["misinfo"]]
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=fired):
        report = build_quality_report(SPEC, posts)
    fps = report["false_positive_candidates"]
    assert len(fps) == 1
    assert fps[0]["text"] == "benign on-topic"
    assert fps[0]["labels"] == ["misinfo"]


def test_build_report_tolerates_length_mismatch():
    posts = [_post("a"), _post("b")]
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=[["misinfo"]]):  # fewer results
        report = build_quality_report(SPEC, posts)
    assert report["total"] == 1  # min(len(posts), len(results))


def test_format_summary_mentions_counts_and_fp_section():
    posts = [_post("scammy dm me", bucket="context")]
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=[["misinfo"]]):
        report = build_quality_report(SPEC, posts)
    text = format_report_summary(report)
    assert "misinfo" in text
    assert "1 real Bluesky posts" in text
    assert "false positives" in text.lower()


def test_subject_carries_account_only_when_present():
    assert _subject({"text": "x"}) == {"text": "x"}
    assert _subject({"text": "x", "account": None}) == {"text": "x"}   # unenriched -> lean payload
    assert _subject({"text": "x", "account": {"follower_count": 3}}) == {
        "text": "x", "account": {"follower_count": 3}
    }


# --- real cross-language check: account signals evaluated by the actual built interpreter ---

_ACCOUNT_SPEC = {
    "spec_version": "1.0", "spec_id": "sha256:acct", "generated_at": "", "warnings": [],
    "labeler": {"display_name": None, "description": None},
    "labels": [{
        "identifier": "scam_newbie", "severity": "alert", "blurs": "content",
        "default_setting": "warn", "locales": [],
        "rule": {
            "include_groups": [{"all_of": [
                {"type": "keyword", "value": "dm me", "plain_name": "DM me"},
                {"type": "account", "value": "account_age_days < 30", "plain_name": "new account"},
            ]}],
            "exclude_signals": [],
        },
    }],
}


@pytest.mark.skipif(
    not BATCH_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine dist or node not available",
)
def test_account_signal_fires_through_real_interpreter():
    posts = [
        {"text": "dm me for deals", "account": {"account_age_days": 5}},    # keyword + new -> fire
        {"text": "dm me for deals", "account": {"account_age_days": 400}},  # old account -> no
        {"text": "dm me for deals"},                                        # traits unknown -> no
        {"text": "an ordinary post", "account": {"account_age_days": 5}},   # no keyword -> no
    ]
    fired = evaluate_corpus(_ACCOUNT_SPEC, posts)
    assert fired == [["scam_newbie"], [], [], []]


_BOOLEAN_SPEC = {
    "spec_version": "1.0", "spec_id": "sha256:bool", "generated_at": "", "warnings": [],
    "labeler": {"display_name": None, "description": None},
    "labels": [{
        "identifier": "no_pfp", "severity": "inform", "blurs": "none",
        "default_setting": "warn", "locales": [],
        "rule": {"include_groups": [{"all_of": [
            {"type": "keyword", "value": "buy now", "plain_name": "buy now"},
            {"type": "account", "value": "has_avatar == false", "plain_name": "no profile pic"},
        ]}], "exclude_signals": []},
    }],
}


@pytest.mark.skipif(
    not BATCH_JS.exists() or shutil.which("node") is None,
    reason="labeler-engine dist or node not available",
)
def test_boolean_account_signal_fires_through_real_interpreter():
    posts = [
        {"text": "buy now!!", "account": {"has_avatar": False}},  # no pfp -> fire
        {"text": "buy now!!", "account": {"has_avatar": True}},   # has pfp -> no
    ]
    assert evaluate_corpus(_BOOLEAN_SPEC, posts) == [["no_pfp"], []]
