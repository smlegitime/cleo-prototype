"""Tests for rule-quality aggregation (the Node interpreter call is mocked out), plus a real
cross-language check that account signals fire through the actual interpreter when it's built."""

import shutil
from unittest.mock import patch

import pytest

from src.agent.lifecycle.quality import BATCH_JS, _subject, build_quality_report, evaluate_corpus, format_report_summary

SPEC = {
    "labels": [
        {
            "identifier": "misinfo",
            "locales": [{"lang": "en", "name": "Health Misinfo", "description": ""}],
            "rule": {"include_groups": [], "exclude_signals": []},
        },
        # No locale name: the report falls back to a humanized identifier ("Harass").
        {"identifier": "harass", "rule": {"include_groups": [], "exclude_signals": []}},
        {"identifier": "no_rule", "rule": None},  # rule-less: never a column in the report
    ]
}


def _post(text, handle="u", bucket="trigger", query="q"):
    return {"text": text, "handle": handle, "bucket": bucket, "query": query}


def _evaluated(*fired_per_post):
    """Engine results in the (fired, why) shape, with a stock reason for each fired label."""
    return [
        (fired, {lid: [f"{lid} signal"] for lid in fired})
        for fired in fired_per_post
    ]


def test_build_report_counts_and_examples():
    posts = [_post("a"), _post("b"), _post("c"), _post("d")]
    evaluated = _evaluated(["misinfo"], [], ["misinfo", "harass"], [])
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=evaluated):
        report = build_quality_report(SPEC, posts)
    assert report["total"] == 4
    assert report["matched_any"] == 2
    assert report["matched_none"] == 2
    assert report["per_label"]["misinfo"]["count"] == 2
    assert report["per_label"]["harass"]["count"] == 1
    assert "no_rule" not in report["per_label"]  # rule-less label excluded
    assert report["per_label"]["misinfo"]["examples"][0]["text"] == "a"


def test_report_carries_the_groups_own_label_names():
    """The formatter only ever sees the report, so the display name has to be resolved here."""
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated([])):
        report = build_quality_report(SPEC, [_post("a")])
    assert report["per_label"]["misinfo"]["name"] == "Health Misinfo"
    assert report["per_label"]["harass"]["name"] == "Harass"  # no locale -> humanized identifier


def test_examples_carry_why_they_fired():
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated(["misinfo"])):
        report = build_quality_report(SPEC, [_post("a")])
    assert report["per_label"]["misinfo"]["examples"][0]["why"] == ["misinfo signal"]


def test_a_stale_engine_build_degrades_to_counts_without_explanations():
    """`why` postdates the report; an older dist/batch.js omits it. The counts must survive."""
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=[(["misinfo"], {})]):
        report = build_quality_report(SPEC, [_post("a")])
    assert report["per_label"]["misinfo"]["count"] == 1
    assert report["per_label"]["misinfo"]["examples"][0]["why"] == []


def test_context_bucket_hits_are_false_positive_candidates():
    posts = [_post("benign on-topic", bucket="context"), _post("triggering", bucket="trigger")]
    with patch(
        "src.agent.lifecycle.quality.evaluate_corpus",
        return_value=_evaluated(["misinfo"], ["misinfo"]),
    ):
        report = build_quality_report(SPEC, posts)
    fps = report["false_positive_candidates"]
    assert len(fps) == 1
    assert fps[0]["text"] == "benign on-topic"
    assert fps[0]["labels"] == ["Health Misinfo"]  # named, not identified
    assert fps[0]["why"] == ["misinfo signal"]


def test_build_report_tolerates_length_mismatch():
    posts = [_post("a"), _post("b")]
    with patch(
        "src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated(["misinfo"])
    ):  # fewer results
        report = build_quality_report(SPEC, posts)
    assert report["total"] == 1  # min(len(posts), len(results))


def test_long_posts_are_cut_at_a_word_boundary():
    """Blind slicing ended quotes mid-word ('coming from the bar' for 'bargaining table'), which
    changes what the post appears to say."""
    text = "the bargaining committee met " * 20
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated(["misinfo"])):
        report = build_quality_report(SPEC, [_post(text)])
    snippet = report["per_label"]["misinfo"]["examples"][0]["text"]
    assert snippet.endswith("…")
    assert not snippet.rstrip("…").endswith(" ")
    assert snippet.rstrip("…").split()[-1] in text.split()  # never a partial word


# --- the chat message ---

def test_format_summary_names_labels_and_explains_the_corpus():
    posts = [_post("scammy dm me", bucket="context")]
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated(["misinfo"])):
        report = build_quality_report(SPEC, posts)
    text = format_report_summary(report)

    assert "Health Misinfo" in text
    assert "`misinfo`" not in text  # the raw identifier never reaches the group
    # The corpus is stocked from the rules' own search terms, so a fire count without this
    # caveat reads as a share of Bluesky rather than a share of posts chosen to fire.
    assert "aren't random posts" in text
    assert "1 of 1" in text
    assert "got caught anyway" in text  # the false-positive section


def test_no_line_relies_on_a_soft_break_to_stand_alone():
    """The original defect. A single newline is a SOFT break in markdown — it renders as a space —
    so lines separated by one collapse into a paragraph, which is what made the old report a wall.
    Every newline here must therefore be either a list item or the start of a blockquote."""
    posts = [_post("benign", bucket="context"), _post("triggering")]
    with patch(
        "src.agent.lifecycle.quality.evaluate_corpus",
        return_value=_evaluated(["misinfo"], ["misinfo", "harass"]),
    ):
        report = build_quality_report(SPEC, posts)

    for para in format_report_summary(report).split("\n\n"):
        for continuation in para.split("\n")[1:]:
            assert continuation.startswith(("- ", "> ")), f"would collapse upward: {continuation!r}"


def test_format_summary_flags_a_label_that_never_fired():
    """The one verdict the report can honestly reach — and today's biggest silent failure."""
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated([])):
        report = build_quality_report(SPEC, [_post("a")])
    text = format_report_summary(report)

    assert "**Health Misinfo didn't catch anything.**" in text
    assert "**Harass didn't catch anything.**" in text


def test_format_summary_stays_quiet_when_every_label_fired():
    with patch(
        "src.agent.lifecycle.quality.evaluate_corpus",
        return_value=_evaluated(["misinfo", "harass"]),
    ):
        report = build_quality_report(SPEC, [_post("a")])
    assert "didn't catch anything" not in format_report_summary(report)


def test_format_summary_quotes_the_wording_that_fired():
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=_evaluated(["misinfo"])):
        report = build_quality_report(SPEC, [_post("a")])
    assert "matched “misinfo signal”" in format_report_summary(report)


def test_format_summary_omits_the_reason_line_on_a_stale_engine_build():
    with patch("src.agent.lifecycle.quality.evaluate_corpus", return_value=[(["misinfo"], {})]):
        report = build_quality_report(SPEC, [_post("a")])
    text = format_report_summary(report)
    assert "matched “" not in text
    assert "Health Misinfo" in text  # everything else still renders


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
    evaluated = evaluate_corpus(_ACCOUNT_SPEC, posts)
    assert [fired for fired, _ in evaluated] == [["scam_newbie"], [], [], []]

    # The reason travels with the fire, across the process boundary: the engine names both halves
    # of the AND-group, which is what lets the chat report say WHICH wording caught a post.
    why = evaluated[0][1]["scam_newbie"]
    assert why == ["DM me + new account"]


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
    evaluated = evaluate_corpus(_BOOLEAN_SPEC, posts)
    assert [fired for fired, _ in evaluated] == [["no_pfp"], []]
    assert evaluated[0][1] == {"no_pfp": ["buy now + no profile pic"]}
    assert evaluated[1][1] == {}  # nothing fired, so nothing to explain
