"""Helper functions"""

import re
from typing import Any, Iterable, List
from langchain_core.messages import HumanMessage, AIMessage

# Bare names that always count as addressing the assistant, in addition to AI_USER_NAME
AI_NAME_ALIASES = ("cleo",)


def _ai_name_pattern(ai_user_name: str) -> str:
    names = {alias.lower() for alias in AI_NAME_ALIASES}
    if ai_user_name and ai_user_name.strip():
        names.add(ai_user_name.strip().lower())
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


def message_addresses_ai(
    text: str,
    ai_user_name: str,
    mentioned_user_ids: Iterable[str] = (),
    ai_user_id: str = "",
) -> bool:
    """Deterministic addressing pre-check, run before the routing graph node.

    Returns True when the message unambiguously addresses the assistant:
    an explicit @-mention (Stream mention metadata or a literal "@name" in
    the text), or the assistant's bare name used vocatively at the start of
    the message ("cleo, show me the config"). A leading name followed by a
    plain word ("cleo said blur was default") is about the assistant as often
    as it is to it, so that case is left to the LLM router.
    """
    if ai_user_id and ai_user_id in set(mentioned_user_ids or ()):
        return True

    text = (text or "").strip()
    if not text:
        return False

    names = _ai_name_pattern(ai_user_name)
    if re.search(rf"@({names})\b", text, re.IGNORECASE):
        return True
    return bool(re.match(rf"({names})\s*(?:[,:;!?—-]|$)", text, re.IGNORECASE))


def clean_channel_id(channel_id: str) -> str:
    """Clean the channel id"""
    channel_id_updated = channel_id
    if ":" in channel_id:
        parts = channel_id.split(":")
        if len(parts) > 1:
            channel_id_updated = parts[1]
    return channel_id_updated


def create_bot_id(channel_id: str) -> str:
    """Create a bot id"""
    return f"ai-bot-{channel_id.replace('!', '')}"


def get_last_messages_from_channel(channel: Any, limit: int = 20) -> List[dict]:
    """Get the last messages from the channel as LLM-formatted dicts."""
    result = channel.query(messages={"limit": limit})
    raw = result.get("messages", [])
    messages = [
        {
            "id": m["id"],
            "content": m["text"].strip(),
            "role": "assistant" if m["user"]["id"].startswith("ai-") else "user",
        }
        for m in raw
        if m.get("text", "").strip()
    ]
    return messages


def messages_to_langchain(stream_messages: List[dict]) -> List:
    """Convert Stream chat message dicts (role/content) to LangChain message objects."""
    result = []
    for m in stream_messages:
        msg_id = m.get("id")
        if m["role"] == "assistant":
            result.append(AIMessage(content=m["content"], id=msg_id))
        else:
            result.append(HumanMessage(content=m["content"], id=msg_id))
    return result


# The documented way back to the going-live path after the group chose the maintenance guide.
# Deterministic on purpose: PATH_NOT_TAKEN_NOTE and GUIDE_CHOSEN_MSG both print the phrase, so the
# group is told exactly what to say rather than having its intent guessed at. Kept deliberately
# narrow — "we're not going live" and "what would going live involve?" must NOT reopen the vote,
# so a bare mention of the phrase isn't enough; it has to read as a request.
_GO_LIVE_REQUEST = re.compile(
    r"""(?:^|\b)
        (?:let'?s\s+|we(?:'?d)?\s+(?:want|like)\s+to\s+|can\s+we\s+|please\s+|i\s+want\s+to\s+)?
        (?:go\s+live|going\s+live)
        (?:\s+(?:now|please|after\s+all))?
        \s*[.!?]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


# "we decided against going live" ends with the phrase too, and reopening a vote the group just
# declined is the one failure this must not have. Checked before the request pattern.
_GO_LIVE_NEGATED = re.compile(
    r"\b(?:not|never|against|without|instead\s+of|rather\s+than|no)\b(?:\s+\S+){0,3}?\s+"
    r"(?:go|going)\s+live",
    re.IGNORECASE,
)

# "what happens if we go live?" ends on the phrase but is asking about it, not for it. An
# interrogative or conditional lead-in disqualifies the message; CLEO answers it as an ordinary
# question instead. This also swallows "if everyone agrees, let's go live" — a false negative,
# and the right way to be wrong: the group says it again plainly and nothing was reopened behind
# their back.
_GO_LIVE_ASKED_ABOUT = re.compile(
    r"\b(?:what|how|why|when|whether|if|suppose|imagine)\b(?:\s+\S+){0,6}?\s+(?:go|going)\s+live",
    re.IGNORECASE,
)


def asks_to_go_live(text: str) -> bool:
    """True when a message is asking to reopen the going-live path.

    Matches the trailing clause, so "@CLEO go live" and "cleo, let's go live" both count while
    "what would going live involve?" does not — a question about the path doesn't end on it. A
    negation anywhere before the phrase disqualifies the whole message.
    """
    text = (text or "").strip()
    if not text:
        return False
    if _GO_LIVE_NEGATED.search(text) or _GO_LIVE_ASKED_ABOUT.search(text):
        return False
    return bool(_GO_LIVE_REQUEST.search(text))
