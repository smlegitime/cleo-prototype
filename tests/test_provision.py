"""Tests for the provision stage — the governance answers CLEO collects before a labeler could deploy.

The stage's defining property is what it does NOT do: no account creation, no email collection, no
irreversible side effects. Several tests below exist to pin that down rather than to check behavior.
"""

from unittest.mock import patch

import pytest

from src.agent.lifecycle.provision import (
    HANDLE_SUFFIX,
    extract_governance,
    handle_candidates,
    is_complete,
    merge_governance,
    outstanding_keys,
)
from src.agent.maintenance_guide import COLLECTED_KEYS, answered_fields
from src.agent.spec import build_spec


def _spec(display_name="Wellness Watch"):
    return build_spec({"display_name": display_name, "description": "d", "labels": []}, {})


class _FakeAnswers:
    """Stands in for the structured-output model's return value."""

    def __init__(self, **kw):
        for field in (
            "handle_choice",
            "custodian_display_name",
            "backup_custodian_display_name",
            "appeals_contact",
        ):
            setattr(self, field, kw.get(field))
        self.stand_down = kw.get("stand_down", False)


def _patched_model(answers):
    """Patch tool_model.with_structured_output to return `answers` from .invoke."""

    class _Structured:
        def invoke(self, _messages):
            return answers

    class _Model:
        def with_structured_output(self, _schema):
            return _Structured()

    return patch("src.agent.lifecycle.provision.tool_model", _Model())


def _human(text):
    class _M:
        type = "human"
        content = text

    return _M()


# ---- Handle candidates ----

def test_candidates_are_derived_from_the_display_name():
    got = handle_candidates(_spec())
    assert got == [
        f"wellness-watch{HANDLE_SUFFIX}",
        f"wellness-watch-mod{HANDLE_SUFFIX}",
        f"wellness-watch-labels{HANDLE_SUFFIX}",
    ]


def test_candidates_are_dns_safe_and_deterministic():
    got = handle_candidates(_spec("  Sad Girls' Club!! ✨ "))
    assert got[0] == f"sad-girls-club{HANDLE_SUFFIX}"
    for handle in got:
        stem = handle[: -len(HANDLE_SUFFIX)]
        assert stem and stem.strip("-") == stem
        assert all(c.isalnum() or c == "-" for c in stem)
        assert len(stem) <= 63
    assert handle_candidates(_spec("  Sad Girls' Club!! ✨ ")) == got  # deterministic


def test_candidates_fall_back_when_there_is_no_usable_name():
    assert handle_candidates(_spec(""))[0] == f"community-labeler{HANDLE_SUFFIX}"
    assert handle_candidates(_spec("✨✨✨"))[0] == f"community-labeler{HANDLE_SUFFIX}"


# ---- Outstanding / completion ----

def test_outstanding_keys_match_the_guide_s_collected_keys():
    """The guide's checklist and this executor must never disagree about what's missing."""
    assert outstanding_keys({}) == list(COLLECTED_KEYS)
    assert not is_complete({})


def test_completion_needs_all_three_and_ignores_the_recommended_one():
    partial = {"handle_choice": "x.bsky.social", "custodian_display_name": "Ama"}
    assert outstanding_keys(partial) == ["appeals"]
    assert not is_complete(partial)

    full = {**partial, "appeals_contact": "the mod team"}
    assert is_complete(full)
    # a backup custodian is advisory — its absence never blocks
    assert is_complete({**full, "backup_custodian_display_name": None})


def test_blank_answers_do_not_count_as_answered():
    assert outstanding_keys({"handle_choice": "   ", "custodian_display_name": ""}) == list(
        COLLECTED_KEYS
    )


# ---- Merge ----

def test_merge_stamps_custodian_confirmation_and_hosting_tier():
    merged = merge_governance({}, {"custodian_display_name": "Ama"}, "2026-07-28T00:00:00Z")
    assert merged["custodian_confirmed_at"] == "2026-07-28T00:00:00Z"
    # 'hosted' by construction: the group supplies no domain and no infrastructure, so there is
    # nothing to ask them about it.
    assert merged["hosting_tier"] == "hosted"


def test_merge_does_not_restamp_an_existing_custodian_confirmation():
    current = {"custodian_display_name": "Ama", "custodian_confirmed_at": "2026-07-01T00:00:00Z"}
    merged = merge_governance(current, {"custodian_display_name": "Ren"}, "2026-07-28T00:00:00Z")
    assert merged["custodian_display_name"] == "Ren"
    assert merged["custodian_confirmed_at"] == "2026-07-01T00:00:00Z"


def test_merge_is_additive_over_earlier_answers():
    """Partial answers persist across sittings — a group answering one thing keeps the rest."""
    first = merge_governance({}, {"handle_choice": "x.bsky.social"}, "t1")
    second = merge_governance(first, {"appeals_contact": "the mod team"}, "t2")
    assert second["handle_choice"] == "x.bsky.social"
    assert second["appeals_contact"] == "the mod team"


def test_merge_never_records_an_email_address():
    """No mechanism collects one; nothing should be able to smuggle one into the record."""
    merged = merge_governance({}, {"custodian_display_name": "Ama"}, "t")
    assert "recovery_email_confirmed_at" not in merged
    assert not any("email" in k for k in answered_fields(merged))


# ---- Extraction ----

def test_extraction_returns_only_what_the_group_decided():
    answers = _FakeAnswers(custodian_display_name="Ama", appeals_contact="the mod team")
    with _patched_model(answers):
        got = extract_governance([_human("Ama will hold it, and the mod team takes appeals")])
    assert got["proposal"] == {"custodian_display_name": "Ama", "appeals_contact": "the mod team"}
    assert got["stand_down"] is False


def test_extraction_strips_and_normalizes_a_chosen_handle():
    with _patched_model(_FakeAnswers(handle_choice="  @wellness-watch.bsky.social ")):
        got = extract_governance([_human("the first one")])
    assert got["proposal"] == {"handle_choice": "wellness-watch.bsky.social"}


def test_extraction_of_nothing_is_not_a_failure():
    """The common case: the group was talking about something else."""
    with _patched_model(_FakeAnswers()):
        got = extract_governance([_human("what does the second label do again?")])
    assert got == {"proposal": {}, "stand_down": False}


def test_extraction_reports_a_request_to_stop():
    with _patched_model(_FakeAnswers(stand_down=True)):
        got = extract_governance([_human("actually let's park this for now")])
    assert got["stand_down"] is True


def test_extraction_with_no_human_messages_skips_the_model():
    """Guard: don't spend a model call on an empty conversation."""
    with patch("src.agent.lifecycle.provision.tool_model") as model:
        assert extract_governance([]) == {"proposal": {}, "stand_down": False}
        model.with_structured_output.assert_not_called()


def test_extraction_survives_a_model_failure():
    """An LLM error must leave the channel where it was, not break the provision conversation."""

    class _Boom:
        def with_structured_output(self, _schema):
            raise RuntimeError("model down")

    with patch("src.agent.lifecycle.provision.tool_model", _Boom()):
        got = extract_governance([_human("Ama will do it")])
    # a failed extraction must never look like a request to stop
    assert got == {"proposal": {}, "stand_down": False}


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_extraction_treats_blank_fields_as_unanswered(blank):
    with _patched_model(_FakeAnswers(custodian_display_name=blank, appeals_contact="mods")):
        got = extract_governance([_human("hmm")])
    assert got["proposal"] == {"appeals_contact": "mods"}
