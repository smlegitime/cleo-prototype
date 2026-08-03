"""
Tests for classification-signal validation and feasibility checks.

Covers signal_error / sanitize_rule / sanitize_rules (the enforceability rules for
keyword, pattern, and account signals) and the finalize_rules tool wiring.
"""

import pytest

from src.agent.feedback.signal_validation import (
    signal_error,
    sanitize_rule,
    sanitize_rules,
    MAX_PATTERN_LENGTH,
)
from src.agent.feedback.tools import finalize_rules


# ---------------------------------------------------------------------------
# signal_error — per-signal enforceability
# ---------------------------------------------------------------------------

def test_keyword_signal_is_valid():
    assert signal_error({"type": "keyword", "value": "miracle cure"}) is None


def test_empty_value_is_rejected():
    assert "empty" in signal_error({"type": "keyword", "value": "   "})


def test_unknown_signal_type_is_rejected():
    err = signal_error({"type": "semantic", "value": "mocks disabled people"})
    assert "unknown signal type" in err


def test_valid_regex_pattern_is_accepted():
    assert signal_error(
        {"type": "pattern", "value": r"\bcure[sd]?\s+my\b", "plain_name": "a cure claim"}
    ) is None


def test_pattern_without_plain_name_is_rejected():
    """A regex cannot be shown to the group, so an unnamed pattern is not approvable."""
    err = signal_error({"type": "pattern", "value": r"\bcure[sd]?\b"})
    assert "no plain name" in err


def test_keyword_without_plain_name_is_fine():
    """Keywords read plainly on their own — only patterns need naming."""
    assert signal_error({"type": "keyword", "value": "miracle cure"}) is None


def test_invalid_regex_pattern_is_rejected():
    err = signal_error({"type": "pattern", "value": r"cure(sd?", "plain_name": "a cure word"})
    assert "invalid regex" in err


def test_match_everything_pattern_is_rejected():
    assert "matches every post" in signal_error({"type": "pattern", "value": ".*", "plain_name": "anything"})


def test_overlong_pattern_is_rejected():
    long_value = "a" * (MAX_PATTERN_LENGTH + 1)
    assert "too long" in signal_error({"type": "pattern", "value": long_value, "plain_name": "a long one"})


@pytest.mark.parametrize("value", [
    "account_age_days < 30",
    "follower_count <= 10",
    "has_avatar == false",
    "has_description != true",
])
def test_valid_account_signals(value):
    assert signal_error({"type": "account", "value": value}) is None


def test_account_wrong_arity_is_rejected():
    assert "must read" in signal_error({"type": "account", "value": "account_age_days<30"})


def test_account_unknown_field_is_rejected():
    assert "unknown account field" in signal_error({"type": "account", "value": "karma > 5"})


def test_account_unknown_operator_is_rejected():
    assert "unknown operator" in signal_error({"type": "account", "value": "post_count =< 5"})


def test_numeric_field_needs_numeric_threshold():
    assert "numeric" in signal_error({"type": "account", "value": "follower_count > many"})


def test_boolean_field_needs_true_false():
    assert "true/false" in signal_error({"type": "account", "value": "has_avatar == 0"})


def test_boolean_field_rejects_ordering_operator():
    assert "== or !=" in signal_error({"type": "account", "value": "has_avatar > false"})


# ---------------------------------------------------------------------------
# sanitize_rule / sanitize_rules — dropping signals and feasibility
# ---------------------------------------------------------------------------

def test_invalid_signal_drops_its_whole_group_not_just_itself():
    """Signals in a group are AND-ed, so dropping one WIDENS the rule — the survivors would
    fire alone. The group goes with it rather than enforcing something broader than approved.
    """
    rule = {
        "label_identifier": "fake_cure",
        "include_groups": [
            {"all_of": [{"type": "keyword", "value": "miracle cure"}]},
            {"all_of": [
                {"type": "keyword", "value": "detox"},
                {"type": "pattern", "value": "cure(sd?", "plain_name": "a cure word"},  # invalid regex
            ]},
        ],
        "exclude_signals": [{"type": "keyword", "value": "helps me manage"}],
        "notes": "n",
    }
    cleaned, errors = sanitize_rule(rule)
    assert [[s["value"] for s in g["all_of"]] for g in cleaned["include_groups"]] == [["miracle cure"]]
    assert any("fake_cure" in e and "include" in e for e in errors)


def test_legacy_flat_include_signals_read_as_one_group_each():
    """Pre-DNF rules stored a flat list whose signals each fired independently — N groups of
    one. They must keep that meaning rather than becoming a single AND."""
    rule = {
        "label_identifier": "fake_cure",
        "include_signals": [
            {"type": "keyword", "value": "miracle cure"},
            {"type": "keyword", "value": "detox"},
        ],
        "exclude_signals": [],
        "notes": None,
    }
    cleaned, _ = sanitize_rule(rule)
    assert [[s["value"] for s in g["all_of"]] for g in cleaned["include_groups"]] == [
        ["miracle cure"], ["detox"]]


def test_infeasible_rule_reports_no_enforceable_include():
    rule = {
        "label_identifier": "harassment",
        "include_groups": [{"all_of": [{"type": "semantic", "value": "mocks sick people"}]}],
        "exclude_signals": [],
        "notes": None,
    }
    cleaned, errors = sanitize_rule(rule)
    assert cleaned["include_groups"] == []
    assert any("no enforceable include signal" in e for e in errors)


def test_sanitize_rules_omits_infeasible_labels_keeps_feasible():
    rules = [
        {
            "label_identifier": "ableist_slur",
            "include_groups": [{"all_of": [{"type": "keyword", "value": "slur1"}]}],
            "exclude_signals": [],
            "notes": None,
        },
        {
            "label_identifier": "harassment",  # no enforceable include -> omitted
            "include_groups": [{"all_of": [{"type": "semantic", "value": "mocks sick people"}]}],
            "exclude_signals": [],
            "notes": None,
        },
    ]
    cleaned, errors = sanitize_rules(rules)
    assert [r["label_identifier"] for r in cleaned] == ["ableist_slur"]
    assert any("harassment" in e for e in errors)


# ---------------------------------------------------------------------------
# finalize_rules tool — self-correction feedback vs success
# ---------------------------------------------------------------------------

def test_finalize_rules_returns_errors_for_unenforceable_signals():
    result = finalize_rules.invoke({"rules": [{
        "label_identifier": "harassment",
        "include_groups": [{"all_of": [{"type": "account", "value": "karma > 5"}]}],
        "exclude_signals": [],
        "notes": None,
    }]})
    assert "can't be enforced" in result
    assert "harassment" in result


def test_finalize_rules_success_for_valid_rules():
    result = finalize_rules.invoke({"rules": [{
        "label_identifier": "fake_cure",
        "include_groups": [
            {"all_of": [{"type": "keyword", "value": "miracle cure"}]},
            {"all_of": [
                {"type": "keyword", "value": "detox"},
                {"type": "pattern", "value": r"(buy|link in bio|dm me)", "plain_name": "a sales pitch"},
            ]},
        ],
        "exclude_signals": [{"type": "keyword", "value": "helps me manage"}],
        "notes": "catches cure claims, leaves personal management alone",
    }]})
    assert "staged for approval" in result
