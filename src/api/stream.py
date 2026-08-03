"""
Stream Chat configuration and the low-level helpers that talk to it.

Everything the rest of the API layer needs to reach the chat service lives here: the env-derived
config (credentials, channel list, the AI user's identity) and the thin async wrappers around the
synchronous stream_chat SDK. Importing this module asserts the credentials are present, so any
importer fails loudly at startup rather than on the first webhook.

Kept free of graph/lifecycle imports so the modules that DO orchestrate those (reporters,
agent_runner, reactions) can all depend on this one without an import cycle.
"""

import asyncio
import logging
import os
from functools import lru_cache

from stream_chat import StreamChat

logger = logging.getLogger(__name__)

STREAM_API_KEY = os.environ.get("STREAM_API_KEY")
STREAM_API_SECRET = os.environ.get("STREAM_API_SECRET")
STREAM_CHANNEL_ID = os.environ.get("STREAM_CHANNEL_ID", "general")
# Allowlist of joinable channels — one channel per pilot group, since a channel IS a LangGraph
# thread. A join code that isn't on this list is refused rather than auto-created, so a mistyped
# invite link can't strand a group in an empty room with no lifecycle state.
STREAM_CHANNELS = [c.strip() for c in os.environ.get("STREAM_CHANNELS", "dev2,general").split(",") if c.strip()] or [STREAM_CHANNEL_ID]
# Where a user lands when their link carries no join code.
DEFAULT_CHANNEL_ID = STREAM_CHANNEL_ID if STREAM_CHANNEL_ID in STREAM_CHANNELS else STREAM_CHANNELS[0]
# Channels the in-app "Clear" action refuses to wipe (preserved demos, e.g. dev2).
PROTECTED_CHANNELS = {c.strip() for c in os.environ.get("PROTECTED_CHANNELS", "dev2").split(",") if c.strip()}
# Legacy demo behaviour: put every user in every channel above under one global user id. Off by
# default — pilot groups run in parallel and shouldn't see each other's channels.
JOIN_ALL_CHANNELS = os.environ.get("STREAM_JOIN_ALL_CHANNELS", "").strip().lower() in {"1", "true", "yes"}
AI_USER_ID = os.environ.get("AI_USER_ID", "ai-assistant")
AI_USER_NAME = os.environ.get("AI_USER_NAME", "AI Assistant")
STREAM_DEBOUNCE_SECONDS = 0.15  # 150ms ceiling for partial updates
SUMMON_REACTION = "summon"  # 🤖

# Members who take part in the conversation but not in its decisions — a researcher or facilitator
# sitting in on a pilot group. Comma-separated Stream user ids; ids are deterministic
# (channel_user_id -> "<channel>-<name>", e.g. "dev3-sybille"), so they can be listed up front.
#
# Deliberately NOT the `ai-` prefix. That prefix also routes a member's messages into the graph as
# CLEO's own words (helpers.messages_to_langchain), drops their messages as agent triggers, skips
# their welcome, and ignores their 🤖 summon — a facilitator wants every one of those to behave
# like any other member. The only thing that changes is the vote.
FACILITATOR_USER_IDS = {
    u.strip() for u in os.environ.get("FACILITATOR_USER_IDS", "").split(",") if u.strip()
}

if not STREAM_API_KEY or not STREAM_API_SECRET:
    raise RuntimeError("STREAM_API_KEY and STREAM_API_SECRET must be set")


@lru_cache(maxsize=1)
def get_stream_client() -> StreamChat:
    return StreamChat(api_key=STREAM_API_KEY, api_secret=STREAM_API_SECRET)


def is_voting_member(user_id: str) -> bool:
    """True for members who both SET the vote threshold and can carry it with a 👍🏾.

    The single definition of who the vote belongs to — excluded on both sides, so a facilitator can
    neither raise the bar by being present nor help clear it by reacting. Used by _ensure_ai_member
    for the denominator and by the reaction webhook for the numerator; they must agree, or CLEO
    announces a threshold different from the one it enforces.
    """
    return not user_id.startswith("ai-") and user_id not in FACILITATOR_USER_IDS


def _slug(name: str) -> str:
    """Stable user id derived from display name (lowercase, alphanumeric + hyphens)."""
    s = name.strip().lower().replace(" ", "-")
    s = "".join(c for c in s if c.isalnum() or c == "-").strip("-")
    return s[:64] or "user"


def resolve_channel(code: str | None) -> str | None:
    """Map a join code from a group's invite link to an allowlisted channel id.

    Matching is case-insensitive and whitespace-tolerant (people retype these). An empty or missing
    code falls back to DEFAULT_CHANNEL_ID; an unrecognised one returns None so the caller can say
    "check your link" instead of quietly creating a channel nobody else will ever join.
    """
    wanted = (code or "").strip().lower() or DEFAULT_CHANNEL_ID.lower()
    for ch_id in STREAM_CHANNELS:
        if ch_id.lower() == wanted:
            return ch_id
    return None


def channel_user_id(channel_id: str, name: str) -> str:
    """Channel-scoped user id, so a 'Sam' in two pilot groups stays two distinct Stream users.

    Deterministic, so rejoining with the same display name in the same channel resumes the same
    identity (and its message history) without a lookup.
    """
    return f"{_slug(channel_id)}-{_slug(name)}"[:64]


async def _ensure_ai_member(channel) -> int:
    """Ensure the AI user exists and is a member of the channel; return the VOTING member count.

    The count falls out of the roster query this already makes, and is what the vote threshold is
    computed from — so returning it saves the caller a second round trip to Stream. Excludes CLEO
    and any facilitators (see is_voting_member).
    """
    client = get_stream_client()

    await asyncio.to_thread(client.upsert_user, {"id": AI_USER_ID, "name": AI_USER_NAME})
    await asyncio.to_thread(channel.create, AI_USER_ID)

    query_result = await asyncio.to_thread(channel.query)
    member_ids = [m["user_id"] for m in query_result.get("members", [])]
    if AI_USER_ID not in member_ids:
        await asyncio.to_thread(channel.add_members, [AI_USER_ID])

    return len([m for m in member_ids if is_voting_member(m)])


async def _set_ai_indicator(channel, state: str) -> None:
    event = {"type": "ai_indicator.clear"} if state == "clear" else {
        "type": "ai_indicator.update",
        "ai_state": state,
    }
    await asyncio.to_thread(channel.send_event, event, AI_USER_ID)


async def _update_stream_message(message_id: str, text: str) -> None:
    client = get_stream_client()

    try:
        await asyncio.to_thread(
            client.update_message_partial,
            message_id,
            {"set": {"text": text}},
            AI_USER_ID,
        )
    except Exception:
        logger.warning(f"Failed to update Stream message {message_id}")


# Vote state carried as a custom field on the Stream message, so the client can tell an anchor the
# group still owes a vote from one that's been settled. Read only by the frontend
# (MessageBubble.tsx keys its accent color off it) — no backend logic branches on it, since the
# authoritative tally lives in the checkpoint. It has to be RETAGGED when a vote resolves: an anchor
# left permanently "pending" fills the scroll with cards that look actionable and aren't.
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_SUPERSEDED = "superseded"


def approval_anchor(text: str) -> dict:
    """A send_message payload for a card the group has to vote on.

    Tagging at creation rather than in a follow-up update keeps the bubble from rendering for a beat
    as an ordinary message; use set_approval_state for anchors whose message already exists.
    """
    return {"text": text, "approval_state": APPROVAL_PENDING}


async def set_approval_state(message_id: str, state: str) -> None:
    """Retag an existing bubble with the vote state it should show.

    Partial update, so it can't disturb text a streamed message is still accumulating. Best-effort:
    losing the tag costs the color, never the vote, so failures warn rather than raise.
    """
    client = get_stream_client()

    try:
        await asyncio.to_thread(
            client.update_message_partial,
            message_id,
            {"set": {"approval_state": state}},
            AI_USER_ID,
        )
    except Exception:
        logger.warning(
            "Failed to set approval_state=%s on Stream message %s", state, message_id, exc_info=True
        )
