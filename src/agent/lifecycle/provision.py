"""
Provision stage — collecting the governance answers a group needs before a labeler can be deployed.

SCOPE (decided 2026-07-28). This stage collects DECISIONS, not credentials. It records which handle
the group picked and who they name as custodian and appeals contact. It deliberately does NOT collect
email addresses: both roles need one, and the executor says so plainly in chat and in the maintenance
guide, but there is no address-collection mechanism here (no one-time link, no OTP relay) and
therefore no account creation.

Two consequences worth holding onto:
  * Nothing in this stage is irreversible. No did:plc is minted, no handle is claimed, nothing is
    published. Every earlier gate warned that provisioning is the point of no return; that is still
    true of the step AFTER this one, which isn't built.
  * `provision` is therefore the furthest the lifecycle can honestly go today. It completes with a
    recorded GovernanceRecord and lifecycle_status='succeeded', still at stage 'provision'.
    Advancing to 'live' needs the account-creation path (see the maintenance guide's live variant,
    which is a deliberate stub for the same reason).

The three collected answers are the ones the maintenance guide has been forewarning groups about;
`maintenance_guide.COLLECTED_KEYS` is the single source of truth for what they are, so the guide's
checklist and this executor can never disagree about what is still outstanding.
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agent.maintenance_guide import COLLECTED_KEYS, answered_fields
from src.agent.spec import LabelerSpec
from src.agent.state import GovernanceRecord
from src.config import tool_model

logger = logging.getLogger(__name__)

# Handles are proposed on the default PDS domain. The service endpoint (a PaaS URL) is a separate
# thing the group never sees or chooses — see the provisioning notes: a free PaaS hostname is a fine
# endpoint and a terrible community identity, so the two stay decoupled.
HANDLE_SUFFIX = ".bsky.social"

# One DNS label caps at 63 chars; keep well inside it so the suffixed handle is always legal.
_MAX_BASE = 40

NUM_CANDIDATES = 3


def _slug(text: str) -> str:
    """Lowercase, hyphen-separated, DNS-safe stem of a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:_MAX_BASE].strip("-")


def handle_candidates(spec: LabelerSpec, limit: int = NUM_CANDIDATES) -> list[str]:
    """Derive handle candidates from the labeler's display name.

    Deterministic and offline. Availability is NOT checked: claiming a handle happens at account
    creation, which isn't built, so a green tick here would be a promise nothing verifies. The chat
    copy says as much rather than implying these are reserved.
    """
    base = _slug((spec.get("labeler") or {}).get("display_name") or "")
    if not base:
        base = "community-labeler"
    stems = [base, f"{base}-mod", f"{base}-labels", f"{base}-bsky"]
    out: list[str] = []
    for stem in stems:
        handle = f"{stem[:_MAX_BASE].strip('-')}{HANDLE_SUFFIX}"
        if handle not in out:
            out.append(handle)
    return out[:limit]


class _GovernanceAnswers(BaseModel):
    """What the group can state in chat. Every field is optional — a turn that answers one question
    and ignores the others is the normal case, not an error."""

    handle_choice: str | None = Field(
        default=None,
        description=(
            "The handle the group chose, as a full handle (e.g. 'wellness-watch.bsky.social'). "
            "Resolve ordinal references ('the second one', 'the -mod one') against the candidate "
            "list given below. Null unless the group actually picked one."
        ),
    )
    custodian_display_name: str | None = Field(
        default=None,
        description=(
            "The person the group designated to hold the labeler account, as the group referred to "
            "them (a first name or @handle is fine). Null unless someone was actually named."
        ),
    )
    backup_custodian_display_name: str | None = Field(
        default=None,
        description="A second/backup custodian, if the group named one. Null otherwise.",
    )
    appeals_contact: str | None = Field(
        default=None,
        description=(
            "Who someone who thinks they were mislabeled should talk to. May be a person or a "
            "group ('the mod team'). Null unless the group actually said."
        ),
    )
    stand_down: bool = Field(
        default=False,
        description=(
            "True ONLY if the group clearly wants to stop or postpone the going-live questions "
            "themselves — 'let's not do this', 'park it for now', 'we'll come back to this'. "
            "False for ordinary hesitation about a single answer ('not sure who should hold it'), "
            "and false when they're simply discussing something else."
        ),
    )


_EXTRACT_SYSTEM = (
    "You are reading a community group's chat while they answer setup questions about deploying "
    "their Bluesky labeler. Extract ONLY what the group has explicitly decided in these messages.\n\n"
    "Rules:\n"
    "  • Never invent a name. If nobody was named for a role, return null for that field.\n"
    "  • A suggestion is not a decision. 'maybe Ama could do it' is null; 'Ama will do it' or "
    "'Ama, can you?' followed by agreement is Ama.\n"
    "  • Do not carry over answers already recorded (listed below) unless the group is CHANGING "
    "them — return null for anything they didn't discuss this time.\n"
    "  • Do not collect email addresses. If someone posts one, ignore it: this stage records names "
    "and roles only.\n"
    "  • The custodian and the appeals contact may be the same person if the group says so.\n"
    "  • Set stand_down only for stopping the PROCESS, not for hesitating over one answer."
)


def _recent_human_texts(messages: list | None, limit: int = 12) -> list[str]:
    """The group's own recent messages, oldest first. Mirrors preview_posts._recent_human_texts."""
    out: list[str] = []
    for msg in reversed(list(messages or [])):
        kind = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else None)
        if kind not in ("human", "user"):
            continue
        text = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
        if len(out) >= limit:
            break
    return list(reversed(out))


class GovernanceExtraction(TypedDict):
    """What one pass over the group's chat yielded."""

    proposal: GovernanceRecord  # newly-decided answers; empty when they discussed something else
    stand_down: bool            # the group asked to stop or postpone the questions themselves


_NOTHING: GovernanceExtraction = {"proposal": {}, "stand_down": False}


def extract_governance(
    messages: list | None,
    current: GovernanceRecord | None = None,
    candidates: list[str] | None = None,
) -> GovernanceExtraction:
    """Pull newly-decided governance answers (or a request to stop) out of the group's recent chat.

    Returns only the fields the group actually settled this time — an empty proposal when they were
    talking about something else, which is the common case and not a failure. Never raises: an LLM
    or parsing failure returns nothing, so the channel just doesn't advance rather than breaking.
    """
    texts = _recent_human_texts(messages)
    if not texts:
        return dict(_NOTHING)  # type: ignore[return-value]

    already = {k: v for k, v in answered_fields(current).items() if v}
    user = (
        f"Handle candidates offered to the group:\n"
        + ("\n".join(f"  - {c}" for c in (candidates or [])) or "  (none offered yet)")
        + "\n\nAlready recorded (do not repeat unless they are changing it):\n"
        + ("\n".join(f"  - {k}: {v}" for k, v in already.items()) or "  (nothing yet)")
        + "\n\nThe group's recent messages:\n"
        + "\n".join(f"  - {t}" for t in texts)
        + "\n\nExtract only what they decided."
    )

    try:
        structured = tool_model.with_structured_output(_GovernanceAnswers)
        answers: _GovernanceAnswers = structured.invoke(
            [SystemMessage(content=_EXTRACT_SYSTEM), HumanMessage(content=user)]
        )
    except Exception:
        logger.exception("Governance extraction failed; treating as no answers this turn")
        return dict(_NOTHING)  # type: ignore[return-value]

    proposal: GovernanceRecord = {}
    if answers.handle_choice and answers.handle_choice.strip():
        proposal["handle_choice"] = answers.handle_choice.strip().lstrip("@")
    for field in ("custodian_display_name", "backup_custodian_display_name", "appeals_contact"):
        value = (getattr(answers, field) or "").strip()
        if value:
            proposal[field] = value
    return {"proposal": proposal, "stand_down": bool(answers.stand_down)}


def merge_governance(
    current: GovernanceRecord | None, proposal: GovernanceRecord, now_iso: str
) -> GovernanceRecord:
    """Apply an approved governance proposal. Called once a pending suggestion clears the vote.

    Mirrors commit_proposal/commit_rules (a shallow merge over what's there), plus two derived
    stamps: when a custodian first lands, and the hosting tier, which is 'hosted' by construction —
    the group supplies no domain and no infrastructure, so there is nothing to ask them about it.
    """
    merged: GovernanceRecord = {**(current or {}), **(proposal or {})}
    if proposal.get("custodian_display_name") and not (current or {}).get("custodian_confirmed_at"):
        merged["custodian_confirmed_at"] = now_iso
    merged.setdefault("hosting_tier", "hosted")
    return merged


def outstanding_keys(governance: GovernanceRecord | None) -> list[str]:
    """Which of the three collected answers are still missing, in the order they're asked for."""
    answers = answered_fields(governance)
    return [key for key in COLLECTED_KEYS if not answers.get(key)]


def is_complete(governance: GovernanceRecord | None) -> bool:
    """True once every collected answer is recorded. Note this does NOT mean deployable — the email
    addresses and the account-creation step are still outside what this stage does."""
    return not outstanding_keys(governance)
