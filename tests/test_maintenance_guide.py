"""Tests for the tailored opt-out maintenance guide."""

from src.agent.maintenance_guide import build_maintenance_guide
from src.agent.spec import build_spec


def _spec(display_name="Wellness Watch", identifiers=("health_misinfo", "scam_newbie")):
    cfg = {"display_name": display_name, "description": "d",
           "labels": [{"identifier": i, "severity": "alert", "blurs": "content", "locales": []}
                      for i in identifiers]}
    rules = {i: {"include_groups": [{"all_of": [{"type": "keyword", "value": i, "plain_name": None}]}],
                 "exclude_signals": [], "notes": None} for i in identifiers}
    return build_spec(cfg, rules)


def test_guide_is_tailored_with_name_and_label_facts():
    guide = build_maintenance_guide(_spec())
    assert guide["labeler_name"] == "Wellness Watch"
    assert guide["label_count"] == 2
    assert guide["labels"] == ["health_misinfo", "scam_newbie"]
    assert guide["mode"] == "automated"
    first = next(s for s in guide["sections"] if s["id"] == "what-you-built")
    assert "Wellness Watch" in first["body"]
    assert "2 labels" in first["body"]
    assert "`health_misinfo`" in first["body"] and "`scam_newbie`" in first["body"]


def test_singular_label_wording():
    guide = build_maintenance_guide(_spec(identifiers=("only",)))
    body = next(s for s in guide["sections"] if s["id"] == "what-you-built")["body"]
    assert "1 label:" in body and "1 labels" not in body


def test_missing_display_name_falls_back():
    guide = build_maintenance_guide(_spec(display_name=""))
    assert guide["labeler_name"] == "Your labeler"


def test_all_six_curated_sections_present_and_concept_bearing():
    guide = build_maintenance_guide(_spec())
    ids = [s["id"] for s in guide["sections"]]
    assert ids == [
        "what-you-built", "two-ways", "ozone", "keeping-current", "reports-appeals", "going-live",
    ]
    joined = " ".join(s["body"] for s in guide["sections"])
    # curated maintenance concepts are present regardless of the group's design
    for concept in ["Automated", "Ozone", "report", "custodian", "redeploy"]:
        assert concept in joined


# ---- Scope guard: CLEO builds labelers, it does not maintain them ----

def _all_copy(guide) -> str:
    return " ".join(s["body"] for s in guide["sections"])


def test_guide_never_offers_cleo_as_the_maintenance_or_appeals_mechanism():
    """Regression guard. Maintenance via CLEO (updates, appeals, Ozone) is undecided, so no variant
    of this guide may imply it — the earlier copy promised exactly that and had to be pulled."""
    guides = [
        build_maintenance_guide(_spec()),
        build_maintenance_guide(_spec(), None, {"appeals_contact": "the mod team",
                                                "handle_choice": "x.bsky.social"}),
        build_maintenance_guide(
            _spec(),
            {"environment": "prod", "labeler_did": "did:plc:x", "handle": "x.bsky.social"},
            {"custodian_display_name": "Ama"},
        ),
    ]
    for guide in guides:
        copy = _all_copy(guide)
        for promise in [
            "just tell me",
            "tells me in your channel",
            "ask me to take a label off",
            "I take the label off",
            "we'll update it together",
            "I re-package and re-test",
        ]:
            assert promise not in copy, f"guide re-introduced a CLEO maintenance promise: {promise!r}"


def test_changing_it_later_states_what_it_requires_without_promising_who():
    body = next(
        s for s in build_maintenance_guide(_spec())["sections"] if s["id"] == "keeping-current"
    )["body"]
    assert "redeploying" in body                    # what a change actually requires
    assert "isn't settled" in body                  # who does it, stated as open
    assert "not currently able to run one for you afterwards" in body


def _going_live(guide):
    return next(s for s in guide["sections"] if s["id"] == "going-live")["body"]


# ---- Appeals copy: name what the group must arrange, offer no mechanism ----

def test_appeals_section_names_the_need_and_disclaims_the_tooling():
    """Neither a report inbox nor a moderation console comes with the labeler — say so plainly."""
    body = next(
        s for s in build_maintenance_guide(_spec())["sections"] if s["id"] == "reports-appeals"
    )["body"]
    assert "neither comes with the labeler you built here" in body
    assert "doesn't collect reports through Bluesky itself" in body   # no inbox
    assert "needs a moderation tool" in body and "Ozone" in body      # named, not included
    assert "Setting one up is separate work" in body


def test_appeals_contact_is_a_prompted_blocker_not_a_nice_to_have():
    open_body = _going_live(build_maintenance_guide(_spec()))
    done_body = _going_live(build_maintenance_guide(_spec(), None, {"appeals_contact": "the mod team"}))
    assert "an appeals contact" in build_maintenance_guide(_spec())["outstanding"]
    for body in (open_body, done_body):
        assert "neither comes with the labeler itself" in body
    assert "the mod team" in done_body


# ---- Tiered going-live section ----

def test_no_records_renders_the_sandbox_checklist():
    """A group that hasn't answered anything sees what going live WOULD take, with every blocker open."""
    guide = build_maintenance_guide(_spec())
    assert guide["readiness"] == "sandbox"
    assert guide["outstanding"] == ["a name on Bluesky", "a custodian", "an appeals contact"]
    body = _going_live(guide)
    assert "what it would take" in next(
        s for s in guide["sections"] if s["id"] == "going-live"
    )["title"]
    # every checkbox item open: 3 collected + 1 recommended. The email note is NOT a checkbox.
    assert body.count("◻️") == 4
    assert "✅" not in body
    # missing domain/hosting must read as "not needed", never as a blocker
    assert "*What you don't need:*" in body and "domain name" in body
    # the collective-ownership recommendation is the headline advice
    assert "shared mailbox" in body and "so does the labeler" in body
    # the handle suggestion is tailored to the group's labeler
    assert "*Wellness Watch*" in body


def test_a_sandbox_deployment_is_not_live():
    """environment='sandbox' is how every group ends the build — it must not read as provisioned."""
    guide = build_maintenance_guide(
        _spec(), {"environment": "sandbox", "labeler_did": "did:web:localhost%3A1234"}
    )
    assert guide["readiness"] == "sandbox"


def test_partial_answers_fill_in_and_narrow_the_outstanding_list():
    guide = build_maintenance_guide(
        _spec(),
        None,
        {"handle_choice": "@wellness-watch.bsky.social", "custodian_display_name": "Ama"},
    )
    assert guide["readiness"] == "partial"
    assert guide["outstanding"] == ["an appeals contact"]
    body = _going_live(guide)
    assert "`@wellness-watch.bsky.social`" in body  # leading @ not doubled
    assert "Ama holds the account" in body
    assert "caretaker, not an owner" in body       # custody framing survives into the answered copy
    assert "*Still open.*" in body                  # the email item
    assert "starting over" in body                  # partial answers are resumable


def test_email_is_named_as_needed_but_never_collected():
    """No mechanism takes an email address, so the guide states the requirement and nothing else.
    It must not render as a checkbox: an unticked box reads as something the group forgot to do."""
    body = _going_live(build_maintenance_guide(_spec()))
    assert "*Needed to deploy, but not something I collect:*" in body
    assert "not something I collect here" in body
    assert "nothing to do about it today" in body
    # the two roles' emails are explained, so the group knows why they'll be asked for
    assert "recovery address" in body and "publish so people can reach a human" in body
    # never a blocker, never a checkbox
    assert "an email address" not in build_maintenance_guide(_spec())["outstanding"]


def test_both_collected_roles_flag_their_email_requirement():
    """The custodian and appeals items each say an address will be needed, answered or not."""
    open_body = _going_live(build_maintenance_guide(_spec()))
    done_body = _going_live(build_maintenance_guide(
        _spec(), None, {"custodian_display_name": "Ama", "appeals_contact": "the mod team"}
    ))
    for body in (open_body, done_body):
        assert "They'll need an email address for the account" in body
        assert "email address your group is willing to publish" in body


def test_recommended_item_never_blocks():
    """The backup custodian appears in the checklist but never gates deploying."""
    guide = build_maintenance_guide(_spec(), None, {"backup_custodian_display_name": "Ren"})
    body = _going_live(guide)
    assert "Ren" in body
    # answering only the recommended item leaves every blocker outstanding
    assert guide["outstanding"] == ["a name on Bluesky", "a custodian", "an appeals contact"]


def test_blank_answers_count_as_unanswered():
    guide = build_maintenance_guide(_spec(), None, {"handle_choice": "  ", "custodian_display_name": ""})
    assert guide["readiness"] == "sandbox"
    assert len(guide["outstanding"]) == 3


def test_live_variant_states_facts_and_discloses_what_is_undecided():
    """The live variant is deliberately a stub: it must not invent a hosting term or appeals promise."""
    guide = build_maintenance_guide(
        _spec(),
        {"environment": "prod", "labeler_did": "did:plc:abc123", "handle": "wellness-watch.bsky.social"},
        {"custodian_display_name": "Ama"},
    )
    assert guide["readiness"] == "live"
    assert guide["outstanding"] == []
    body = _going_live(guide)
    assert "`did:plc:abc123`" in body and "`@wellness-watch.bsky.social`" in body
    assert "held by Ama" in body
    assert "isn't written down here yet" in body
