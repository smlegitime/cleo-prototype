"""
Tests for the default_setting pin (label_policy), the plain-language rendering of label
behavior (_label_behavior), and of classification rules (_format_rules_block and its
signal translators).
"""

import pytest

from src.agent.feedback.label_policy import pin_default_setting, PINNED_DEFAULT_SETTING
from src.agent.feedback.tools import LabelInput
from src.agent.brainstorming.formatting import (
    format_proposal_block,
    format_rules_block,
    _label_behavior,
    _signal_to_plain,
    _account_signal_to_plain,
)


# ---------------------------------------------------------------------------
# pin_default_setting — every label warns by default; blurs/severity do the rest
# ---------------------------------------------------------------------------

def test_default_setting_is_pinned_to_warn():
    labels = [{"identifier": "harassment", "blurs": "content", "severity": "alert"}]
    pinned = pin_default_setting(labels)
    assert pinned[0]["default_setting"] == PINNED_DEFAULT_SETTING == "warn"


def test_pin_overrides_any_supplied_default_setting():
    labels = [
        {"identifier": "a", "default_setting": "hide"},
        {"identifier": "b", "default_setting": "ignore"},
    ]
    assert [l["default_setting"] for l in pin_default_setting(labels)] == ["warn", "warn"]


def test_pin_does_not_mutate_input():
    labels = [{"identifier": "x", "default_setting": "hide"}]
    pin_default_setting(labels)
    assert labels[0]["default_setting"] == "hide"


def test_pin_preserves_other_fields():
    labels = [{"identifier": "x", "severity": "alert", "blurs": "content"}]
    pinned = pin_default_setting(labels)
    assert pinned[0]["severity"] == "alert"
    assert pinned[0]["blurs"] == "content"


def test_model_cannot_set_default_setting():
    # default_setting is absent from the finalize_proposal schema, so the model has no way
    # to propose one — pin_default_setting is the only writer.
    assert "default_setting" not in LabelInput.model_fields


# ---------------------------------------------------------------------------
# _label_behavior — the card never claims a hide or a warning that blurs doesn't deliver
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blurs,severity", [
    (b, s) for b in ("content", "media", "none") for s in ("alert", "inform", "none")
])
def test_every_blurs_severity_pair_has_plain_language(blurs, severity):
    out = _label_behavior({"blurs": blurs, "severity": severity})
    assert "blurs:" not in out  # not the raw-field fallback


@pytest.mark.parametrize("severity", ["alert", "inform"])
def test_no_blur_says_nothing_is_hidden_and_offers_no_click_through(severity):
    out = _label_behavior({"blurs": "none", "severity": severity})
    assert "nothing is hidden" in out
    assert "tap to view" not in out


@pytest.mark.parametrize("severity", ["alert", "inform", "none"])
def test_content_blur_reads_as_whole_post_hidden(severity):
    out = _label_behavior({"blurs": "content", "severity": severity})
    assert "Whole post hidden" in out


@pytest.mark.parametrize("severity", ["alert", "inform", "none"])
def test_media_blur_says_text_stays_visible(severity):
    out = _label_behavior({"blurs": "media", "severity": severity})
    assert "the text stays visible" in out


def test_unknown_pair_falls_back_without_raising():
    assert "blurs:" in _label_behavior({"blurs": "bogus", "severity": "alert"})


def test_proposal_card_describes_a_warn_only_label_as_hiding_nothing():
    # Regression: an alert label with blurs='none' used to render "No blur" alongside
    # "Post shown after click-through warning", which contradicted itself.
    block = format_proposal_block({
        "display_name": "Disability Community Protection",
        "labels": [{
            "identifier": "ableist_harassment",
            "severity": "alert",
            "blurs": "none",
            "default_setting": "warn",
            "locales": [{"lang": "en", "name": "Ableist Harassment", "description": "Targeted attacks"}],
        }],
    })
    assert "nothing is hidden" in block
    assert "click-through" not in block
    assert "No blur" not in block


# ---------------------------------------------------------------------------
# Plain-language signal translation
# ---------------------------------------------------------------------------

def test_keyword_renders_as_quoted_text():
    assert _signal_to_plain({"type": "keyword", "value": "miracle cure"}) == '"miracle cure"'


def test_pattern_renders_without_claiming_meaning():
    out = _signal_to_plain({"type": "pattern", "value": r"\bcure[sd]?\b"})
    assert "wording like" in out


def test_account_age_renders_plainly():
    assert _account_signal_to_plain("account_age_days < 30") == "accounts with account age in days under 30"


def test_account_has_avatar_false_reads_as_no_profile_picture():
    assert _account_signal_to_plain("has_avatar == false") == "accounts with no profile picture"


def test_account_has_avatar_not_true_also_reads_as_no_profile_picture():
    # '!= true' is equivalent to '== false'
    assert _account_signal_to_plain("has_avatar != true") == "accounts with no profile picture"


def test_account_has_description_true():
    assert _account_signal_to_plain("has_description == true") == "accounts with a bio"


# ---------------------------------------------------------------------------
# _format_rules_block — plain-language, no raw type:value or field syntax leaked
# ---------------------------------------------------------------------------

def test_rules_block_is_plain_language():
    rules = {
        "fake_cure": {
            "label_identifier": "fake_cure",
            "include_groups": [
                {"all_of": [{"type": "keyword", "value": "miracle cure"}]},
                {"all_of": [{"type": "account", "value": "account_age_days < 30"}]},
            ],
            "exclude_signals": [{"type": "keyword", "value": "helps me manage"}],
            "notes": "Catches cure claims, leaves personal management talk alone.",
        }
    }
    block = format_rules_block(rules)
    # Humanized label name, notes surfaced, plain framing
    assert "Fake Cure" in block
    assert "Catches cure claims" in block
    assert '"miracle cure"' in block
    assert "accounts with account age in days under 30" in block
    assert "Never flags posts that also say" in block
    # No raw "type:value" rendering of keyword/account signals
    assert "keyword:miracle cure" not in block
    assert "account:account_age_days" not in block
    # Disclaimer about capability limits is present
    assert "can't judge tone or meaning" in block


# ---------------------------------------------------------------------------
# _format_rules_block — AND/OR must be legible to the members who vote on it
# ---------------------------------------------------------------------------

DNF_RULE = {
    "health_misinfo": {
        "label_identifier": "health_misinfo",
        "include_groups": [
            {"all_of": [{"type": "keyword", "value": "MMS"}]},
            {"all_of": [{"type": "keyword", "value": "chlorine dioxide"}]},
            {"all_of": [
                {"type": "pattern", "value": r"\b(cure|cured|reversed)\b", "plain_name": "a cure word"},
                {"type": "pattern", "value": r"(buy|link in bio|dm me)", "plain_name": "a sales pitch"},
            ]},
        ],
        "exclude_signals": [],
        "notes": None,
    }
}


def test_single_signal_groups_collapse_into_one_any_of_condition():
    block = format_rules_block(DNF_RULE)
    assert "Flags a post if ANY of these is true:" in block
    assert "① the post mentions any of:" in block
    assert '"MMS", "chlorine dioxide"' in block


def test_multi_signal_group_spells_out_the_and():
    """'a cure word AND a sales pitch' vs 'OR' are one word apart and describe very
    different labelers — the card must not leave that to a comma."""
    block = format_rules_block(DNF_RULE)
    assert "② the post mentions BOTH:" in block
    assert "· a cure word" in block
    assert "· AND a sales pitch" in block


def test_card_shows_plain_names_never_regex():
    """Members voting on these rules cannot read a regex."""
    block = format_rules_block(DNF_RULE)
    assert "cure|cured|reversed" not in block
    assert "link in bio|dm me" not in block
    assert "wording like" not in block


def test_three_signal_group_says_all_three_not_both():
    rule = {"r": {"label_identifier": "r", "include_groups": [{"all_of": [
        {"type": "keyword", "value": "a"}, {"type": "keyword", "value": "b"},
        {"type": "keyword", "value": "c"}]}], "exclude_signals": [], "notes": None}}
    block = format_rules_block(rule)
    assert "ALL 3 of:" in block


def test_legacy_flat_rule_still_renders_as_alternatives():
    """Pre-DNF rules meant 'any of these', and must not start reading as an AND."""
    rule = {"spam": {
        "label_identifier": "spam",
        "include_signals": [{"type": "keyword", "value": "buy now"},
                            {"type": "keyword", "value": "act fast"}],
        "exclude_signals": [],
        "notes": None,
    }}
    block = format_rules_block(rule)
    assert "① the post mentions any of:" in block
    assert '"buy now", "act fast"' in block
    assert "BOTH" not in block
