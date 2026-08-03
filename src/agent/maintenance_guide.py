"""
Maintenance guide — what running this labeler involves, and what a group would need to deploy one.

Started life as the opt-out path's content (for a group that declines or defers going live) and is
still that, but it is now also the running record of the group's going-live decisions: the final
section is TIERED on how far along they are, so the same page answers "what would deploying take?"
before any decisions exist and "here's where we got to" once some do.

SCOPE — read before editing any copy here. CLEO BUILDS labelers; it does not maintain them. Whether
a later version mediates maintenance (adding a label, processing an appeal, standing up Ozone) is
undecided, so no section may promise that CLEO does any of it. What CLEO does own is the PROVISION
stage, where it prompts the group for the handle, the custodian, and the appeals contact — the
`_COLLECTED_ITEMS` below, whose keys are `COLLECTED_KEYS`. Email addresses are NOT collected (there
is no mechanism to take one); both named roles need one, so `_LATER_ITEMS` states the requirement
instead of pretending to satisfy it. This guide's job is to flag all of that in advance so a group
that wants to deploy knows what it will be asked, and to describe everything else (updates, reports,
appeals, Ozone) as work the group arranges rather than a service it is being offered.

`build_maintenance_guide(spec, deployment, governance)` renders a short, plain-language guide,
LIGHTLY TAILORED to the group's design (labeler name, label count) and their recorded governance
answers, but with all the maintenance CONCEPTS drawn from the fixed, curated templates below — so
nothing is hallucinated. The audience is non-technical (see prompts.py), so the copy avoids atproto
jargon. Both extra arguments are optional and default to the pre-decision ('sandbox') rendering.

Consumed by GET /maintenance-guide/{channel_id} (chatbot.py) and rendered by the frontend
MaintenanceGuide screen at /?guide=<channel_id>. Also offered inline in chat via GOING_LIVE_NEXT_MSG.
The guide is informational only in v1 — going live is a separate, deliberate step in the channel.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from src.agent.spec import LabelerSpec
from src.agent.state import DeploymentRecord, GovernanceRecord

# How far along the group is on going live. Drives which variant of the final section renders.
#   sandbox — no governance answers recorded yet: the section explains what going live would take.
#   partial — some answers recorded: the same checklist, with what's settled filled in.
#   live    — actually provisioned. Deliberately a STUB (see _DEPLOY_LIVE): the operating record a
#             live labeler needs (appeals contact, hosting term, sunset) commits the lab to promises
#             nobody has made yet, and a guide that invents them is worse than one that says so.
Readiness = Literal["sandbox", "partial", "live"]


class GuideSection(TypedDict):
    id: str
    title: str
    body: str


class MaintenanceGuide(TypedDict):
    labeler_name: str
    label_count: int
    labels: list[str]
    mode: str            # the operating model the group actually built ("automated")
    readiness: Readiness
    outstanding: list[str]  # unanswered items that actually BLOCK going live (recommended ones excluded)
    sections: list[GuideSection]


# Curated sections. `{name}`, `{label_count}`, `{label_word}`, `{label_list}` are the only slots;
# every maintenance concept is fixed text so the guide can't drift or invent capabilities.
_SECTIONS: list[tuple[str, str, str]] = [
    (
        "what-you-built",
        "What you've built",
        "*{name}* is an automated, rule-based labeler. It's been tested privately in a sandbox but "
        "isn't public on Bluesky yet. It has {label_count} {label_word}: {label_list}. Because it's "
        "rule-based, it applies labels on its own by matching posts against the words, patterns, and "
        "account traits you set. It doesn't judge tone or meaning.",
    ),
    (
        "two-ways",
        "Two ways to run it",
        "A labeler can operate in two ways:\n\n"
        "• *Automated* (what you have): the rules apply labels on their own. Low effort to run, but "
        "only as good as the rules; it catches what the rules describe and nothing more.\n\n"
        "• *Hands-on*: people (moderators) review reported posts and apply labels by hand. Better on "
        "nuance and context, but it needs volunteers and a way to manage a review queue.\n\n"
        "Many communities run a mix: rules handle the obvious cases, people handle the judgment calls.",
    ),
    (
        "ozone",
        "If you want people in the loop: Ozone",
        "If you want moderators reviewing reports by hand, you'd use *Ozone*, Bluesky's moderation "
        "console, where moderators see reported posts and apply or remove labels. It's a separate "
        "service from the labeler you built here, and requires more to set up and keep running. "
        "It's worth it mainly once you have people ready to moderate.",
    ),
    # SCOPE: CLEO builds labelers; it does not maintain them. Whether a future version mediates
    # updates, appeals, or Ozone setup is undecided, so this section describes what a change
    # REQUIRES and says who does it is unsettled — it must not promise "just tell me".
    (
        "keeping-current",
        "Changing it later",
        "Your labeler runs the rules your group approved, and only those; it doesn't learn or drift. "
        "Adding a label, changing what a label means, or adjusting a rule all mean updating the "
        "design and then re-packaging, re-testing, and redeploying it. Nothing takes effect on its "
        "own.\n\n"
        "Who does that after your build session isn't settled. I'm built to help a group design and "
        "test a labeler, and am not currently able to run one for you afterwards. So if being able to "
        "change it later matters to your group, ask what the arrangement is before you rely on it.",
    ),
    # Names what the group must ARRANGE, without offering CLEO as the mechanism. Removing a label is
    # possible in the protocol (a negation), but the tool a non-technical person would use for it is
    # Ozone, which is not deployed here and is not a bolt-on — it would own the signing key and the
    # label store. So: state the need, name the tool, be explicit it isn't included.
    (
        "reports-appeals",
        "Reports and appeals",
        "Sooner or later someone will think they were labeled unfairly, so decide early who they "
        "talk to and how that person can act.\n\n"
        "Two things your group has to arrange, because neither comes with the labeler you built "
        "here:\n\n"
        "• *A way for people to reach you.* Your labeler doesn't collect reports through Bluesky "
        "itself, so you'd publish a contact — an email or an account handle — where someone can "
        "appeal.\n\n"
        "• *A way to act on an appeal.* Taking a label back off a post is possible, but doing it "
        "needs a moderation tool like *Ozone*, the console described above. Setting one up is "
        "separate work.\n\n"
        "None of this is urgent while your labeler is private: nobody outside your group sees its "
        "labels. It becomes real the moment you deploy, which is why it's worth finalizing first.",
    ),
]

# ---- The going-live section, tiered on how far the group has got ----
#
# This REPLACES the old flat "going live, whenever you're ready" section rather than sitting next to
# it: two sections about going live in one short guide is bad copy, and everything the old one said
# is said here with the group's own answers filled in.
#
# `{checklist}` is the only computed slot. It is assembled deterministically from GovernanceRecord
# (see _checklist) — no LLM, same anti-hallucination property as the sections above.

_DEPLOY_SANDBOX = (
    "Going live gives your labeler a permanent, public identity on Bluesky that people can subscribe "
    "to. It's optional, and it's the one step that can't be undone; staying in the private sandbox "
    "keeps everything exactly as it is, and you can go live later without redoing any of this.\n\n"
    "*What your group would need to have ready:*\n\n{checklist}\n\n"
    "*What you don't need:* a website, a domain name, or anywhere to host it because we run the service. We "
    "also hold the technical keys the labeler uses to sign its labels, so there's nothing for your "
    "group to install or store. (If your group already owns a domain name, there's a version where "
    "the labeler's identity lives on your domain and belongs to you outright)\n\n"
    "*One recommendation:* make the custodian's address one the group controls (e.g, a shared mailbox, or "
    "an alias that reaches two or three people) rather than someone's personal inbox. That address "
    "is your group's strongest claim on the labeler, and if it belongs to one person, so does the "
    "labeler.\n\n"
    "There's nothing to do about any of this today. Deploying isn't part of this session. When it "
    "is available, these are the answers I'll be able to ask your group for."
)

_DEPLOY_PARTIAL = (
    "You've started answering what going live would take. Here's where your group has got to: "
    "nothing is committed yet, and none of it goes live until the whole group approves a final "
    "confirmation.\n\n"
    "{checklist}\n\n"
    "Anything not yet ticked can be answered whenever you're ready; your answers are kept, so "
    "you can pick this up in a week without starting over. Any of them can also be changed before "
    "you go live and the custodian can be changed after, too.\n\n"
    "*What you don't need:* a website, a domain name, or anywhere to host it. We'll run the service, "
    "and we hold the keys the labeler uses to sign its labels."
)

# Deliberately minimal — see the Readiness docstring. States the facts that exist and DISCLOSES the
# ones that don't, rather than rendering a hosting term or appeals promise nobody has committed to.
# Unreachable today (nothing sets deployment.environment == 'prod'); fill in when those are decided.
_DEPLOY_LIVE = (
    "Your labeler is live on Bluesky as {handle}, under the permanent identity `{did}`. "
    "{custodian_line}\n\n"
    "Its labels are public now, and people can subscribe to it. Changing a rule or a label from "
    "here means re-packaging, re-testing and redeploying (see *Changing it later* above).\n\n"
    "⚠️ The rest of the operating record — who handles appeals in practice, how long the service is "
    "hosted for, and what happens at the end of that term — isn't written down here yet. Ask in "
    "your channel before relying on any of it."
)

# The going-live checklist, in three groups:
#   _COLLECTED_ITEMS   — what CLEO actually asks for and records at the provision stage. These are
#                        the blockers, and COLLECTED_KEYS below is the single source of truth that
#                        keeps this list and lifecycle/provision.py from disagreeing.
#   _LATER_ITEMS       — needed to deploy, but NOT collected here. Email addresses: both named roles
#                        need one, there is no mechanism to take one, so the guide names the
#                        requirement instead of pretending to satisfy it.
#   _RECOMMENDED_ITEMS — advised, never gates.
# Each entry is (key, label, done-template, open-template); `done` renders with the recorded value.
_COLLECTED_ITEMS: list[tuple[str, str, str, str]] = [
    (
        "handle",
        "a name on Bluesky",
        "✅ *A name on Bluesky* — `@{value}`. That's what people subscribe to.",
        "◻️ *A name on Bluesky* — the handle people subscribe to. *Still open:* I'll suggest a few "
        "based on *{name}* and the group picks one.",
    ),
    (
        "custodian",
        "a custodian",
        "✅ *A custodian* — {value} holds the account on the group's behalf. They're a caretaker, not "
        "an owner: the group can replace them at any time. They'll need an email address for the "
        "account when the time comes.",
        "◻️ *A custodian* — one member who holds the labeler's account for the group. A caretaker, "
        "not an owner — replaceable by the group at any time. They'll need an email address for the "
        "account. *Still open.*",
    ),
    # Required, not optional: a public labeler with nobody to appeal to is the failure this whole
    # section exists to prevent. What the contact does NOT get is a tool — see reports-appeals.
    (
        "appeals",
        "an appeals contact",
        "✅ *Someone to handle appeals* — {value}. They'll need an email address your group is "
        "willing to publish, and a moderation tool to act on an appeal (see *Reports and appeals* "
        "above) — neither comes with the labeler itself.",
        "◻️ *Someone to handle appeals* — the person a mislabeled someone talks to. They'll need an "
        "email address your group is willing to publish, and a moderation tool to act on an appeal; "
        "neither comes with the labeler itself. *Still open.*",
    ),
]

# Needed to deploy, deliberately NOT collected. There is no mechanism to take an email address —
# no one-time link, no verification — so naming the requirement is the honest move. Renders as a
# note (▫️, not a checkbox) so it never reads as something the group forgot to do.
_LATER_ITEMS: list[str] = [
    "▫️ *Email addresses for those two people* — not something I collect here, and nothing to do "
    "about it today. The custodian's address becomes the account's recovery address, so it's the "
    "group's real hold on the labeler; the appeals contact's is the one you'd publish so people "
    "can reach a human. Worth agreeing now which addresses those would be.",
]

_RECOMMENDED_ITEMS: list[tuple[str, str, str, str]] = [
    (
        "backup_custodian",
        "a backup custodian",
        "✅ *A backup custodian* — {value}, so the account doesn't depend on one person being "
        "reachable.",
        "◻️ *A backup custodian* — recommended, not required. A custodian of one is a single point of "
        "failure on the group's only real hold over the labeler.",
    ),
]

# The keys CLEO collects at the provision stage, in the order it asks for them. Shared with
# lifecycle/provision.py so the checklist and the executor can never disagree about what's missing.
COLLECTED_KEYS: tuple[str, ...] = ("handle", "custodian", "appeals")


def answered_fields(governance: GovernanceRecord | None) -> dict[str, str | None]:
    """Normalize the recorded governance answers to {checklist key: displayable value or None}.

    Blank strings count as unanswered. Public because lifecycle/provision.py reads it to decide what
    is still outstanding; no email key, because no email is collected (see _LATER_ITEMS).
    """
    g = governance or {}
    return {
        "handle": ((g.get("handle_choice") or "").strip().lstrip("@")) or None,
        "custodian": ((g.get("custodian_display_name") or "").strip()) or None,
        "appeals": ((g.get("appeals_contact") or "").strip()) or None,
        "backup_custodian": ((g.get("backup_custodian_display_name") or "").strip()) or None,
    }


def _checklist(governance: GovernanceRecord | None, name: str) -> tuple[str, list[str]]:
    """Render the going-live checklist, and return the blocking items still unanswered.

    Deterministic: each item picks its done- or open-template from the recorded answer. The
    not-collected and recommended items render in the same list but never count as outstanding.
    """
    answers = answered_fields(governance)
    outstanding: list[str] = []

    def render(items: list[tuple[str, str, str, str]], blocking: bool) -> list[str]:
        lines = []
        for key, label, done_tpl, open_tpl in items:
            value = answers[key]
            if value:
                line = done_tpl.format(value=value, name=name)
            else:
                line = open_tpl.format(value="", name=name)
                if blocking:
                    outstanding.append(label)
            lines.append(line)
        return lines

    collected = render(_COLLECTED_ITEMS, blocking=True)
    recommended = render(_RECOMMENDED_ITEMS, blocking=False)
    body = (
        "\n\n".join(collected)
        + "\n\n*Needed to deploy, but not something I collect:*\n\n"
        + "\n\n".join(_LATER_ITEMS)
        + "\n\n*Also worth deciding (not required):*\n\n"
        + "\n\n".join(recommended)
    )
    return body, outstanding


def _readiness(
    deployment: DeploymentRecord | None, governance: GovernanceRecord | None
) -> Readiness:
    """Which variant of the going-live section applies. A sandbox deployment is not 'live' —
    only a provisioned prod identity is."""
    d = deployment or {}
    if d.get("environment") == "prod" and d.get("labeler_did"):
        return "live"
    return "partial" if any(answered_fields(governance).values()) else "sandbox"


def _going_live_section(
    readiness: Readiness,
    deployment: DeploymentRecord | None,
    governance: GovernanceRecord | None,
    name: str,
) -> tuple[GuideSection, list[str]]:
    """Build the tiered final section, plus the outstanding blockers it reports."""
    if readiness == "live":
        d = deployment or {}
        handle = (d.get("handle") or "").strip().lstrip("@")
        custodian = ((governance or {}).get("custodian_display_name") or "").strip()
        body = _DEPLOY_LIVE.format(
            handle=f"`@{handle}`" if handle else "its published handle",
            did=d.get("labeler_did") or "unknown",
            custodian_line=(
                f"The account is held by {custodian} on the group's behalf."
                if custodian
                else "No custodian is recorded for the account — worth fixing in your channel."
            ),
        )
        return {"id": "going-live", "title": "Your labeler is live", "body": body}, []

    checklist, outstanding = _checklist(governance, name)
    template, title = (
        (_DEPLOY_PARTIAL, "Going live: where your group has got to")
        if readiness == "partial"
        else (_DEPLOY_SANDBOX, "Going live: what it would take")
    )
    return (
        {"id": "going-live", "title": title, "body": template.format(checklist=checklist)},
        outstanding,
    )


def build_maintenance_guide(
    spec: LabelerSpec,
    deployment: DeploymentRecord | None = None,
    governance: GovernanceRecord | None = None,
) -> MaintenanceGuide:
    """Render the tailored guide. Pure function over build_spec output + the recorded records.

    `deployment` and `governance` only affect the final going-live section, which is tiered on how
    far the group has got (see Readiness). Both default to None, which renders the pre-decision
    version — the one a group in the sandbox sees, and the only one most groups will.
    """
    labeler = spec.get("labeler") or {}
    name = (labeler.get("display_name") or "").strip() or "Your labeler"
    labels = [l.get("identifier") or "" for l in (spec.get("labels") or []) if l.get("identifier")]
    count = len(labels)
    label_word = "label" if count == 1 else "labels"
    label_list = ", ".join(f"`{lid}`" for lid in labels) if labels else "no labels yet"

    slots = {"name": name, "label_count": count, "label_word": label_word, "label_list": label_list}
    sections: list[GuideSection] = [
        {"id": sid, "title": title, "body": body.format(**slots)} for sid, title, body in _SECTIONS
    ]

    readiness = _readiness(deployment, governance)
    going_live, outstanding = _going_live_section(readiness, deployment, governance, name)
    sections.append(going_live)

    return {
        "labeler_name": name,
        "label_count": count,
        "labels": labels,
        "mode": "automated",
        "readiness": readiness,
        "outstanding": outstanding,
        "sections": sections,
    }
