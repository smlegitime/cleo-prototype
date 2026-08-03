"""
FastAPI backend for AI Group Collective Eng Chat MVP.

Thin app layer: it owns the FastAPI instance, the lifespan (checkpointer attach/detach) and the
HTTP surface, and delegates the actual work to its siblings —

    stream.py     Stream config + the low-level chat client helpers
    messages.py   the lifecycle chat copy
    reporters.py  background tasks that run a lifecycle stage and report back in-channel
    reactions.py  approval-vote tallying and the stage handoffs it triggers
    agent_runner.py  the brainstorming-graph driver and its per-channel scheduler

Routes here should stay short enough to read at a glance; anything with real logic belongs in one
of the modules above.
"""

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.brainstorming.graph import graph, attach_sqlite_checkpointer, sqlite_persistence_enabled
from src.agent.brainstorming.voting import APPROVAL_REACTION
from src.agent.maintenance_guide import build_maintenance_guide
from src.agent.spec import build_spec
from src.agent.lifecycle.preview_posts import generate_preview_posts
from src.agent.state import Reaction
from src.api.agent_runner import _schedule_agent
from src.api.helpers import asks_to_go_live, message_addresses_ai
from src.api.model import ChatRequest, ChatResponse, TokenRequest, TokenResponse
from src.api.reactions import _process_approval_reaction, _vote_locks
from src.api.reporters import (
    _reopen_go_live_and_report,
    _run_governance_capture,
    _send_welcome_message,
)
from src.api.stream import (
    AI_USER_ID,
    AI_USER_NAME,
    JOIN_ALL_CHANNELS,
    PROTECTED_CHANNELS,
    STREAM_CHANNEL_ID,
    STREAM_CHANNELS,
    SUMMON_REACTION,
    _slug,
    channel_user_id,
    get_stream_client,
    resolve_channel,
)

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Attaching the persistent checkpointer here because AsyncSqliteSaver binds to
    # the running event loop. Skipped when CHECKPOINT_BACKEND=memory.
    checkpoint_conn = None
    if sqlite_persistence_enabled():
        checkpoint_conn = await attach_sqlite_checkpointer()
        logger.info("SQLite checkpointer attached — conversation state persists across restarts")
    else:
        logger.info("Using in-memory checkpointer (CHECKPOINT_BACKEND=memory)")
    yield
    if checkpoint_conn is not None:
        await checkpoint_conn.close()
    get_stream_client.cache_clear()


app = FastAPI(title="AI Group Chat MVP", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ROUTES

def _join_channels(client, user_id: str, channel_ids: list[str]) -> None:
    """Add the user + the AI assistant to each channel (creating it if it doesn't exist yet)."""
    client.upsert_user({"id": AI_USER_ID, "name": AI_USER_NAME})
    for ch_id in channel_ids:
        try:
            channel = client.channel("messaging", ch_id)
            channel.create(user_id)
            channel.add_members([user_id, AI_USER_ID])
        except Exception:
            logger.warning("Failed to create/join channel %s for user %s", ch_id, user_id, exc_info=True)


def _global_demo_user(client, user_name: str) -> tuple[str, str]:
    """Resolve a channel-independent user for the JOIN_ALL_CHANNELS demo deployment.

    Looks the display name up across the whole app and reuses that user if it exists. Only sound
    when everyone shares one set of channels — with parallel pilot groups this would merge two
    people who happen to pick the same name, which is why pilot mode uses channel_user_id instead.
    """
    existing = client.query_users(filter_conditions={"name": user_name}, limit=1)
    users = getattr(existing, "users", None) or existing.get("users", [])

    if users:
        user = users[0] if isinstance(users[0], dict) else getattr(users[0], "__dict__", users[0])
        user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
        if user_id:
            display_name = user.get("name", user_name) if isinstance(user, dict) else getattr(user, "name", user_name)
            return user_id, display_name or user_name

    user_id = _slug(user_name)
    client.upsert_user({"id": user_id, "name": user_name})
    return user_id, user_name


@app.post("/token", response_model=TokenResponse)
def create_token(body: TokenRequest):
    """Return a Stream JWT for a user joining one group's channel.

    The channel comes from the join code in the group's invite link (`?c=<code>`) and must be on
    the STREAM_CHANNELS allowlist — an unknown code is refused, never auto-created. The user id is
    scoped to that channel, so the same display name in two pilot groups stays two distinct users
    and neither group's roster leaks into the other.

    Under STREAM_JOIN_ALL_CHANNELS the legacy demo behaviour applies instead: one global user,
    joined to every allowlisted channel.
    """

    client = get_stream_client()
    user_name = (body.user_name or "").strip()

    if not user_name:
        raise HTTPException(status_code=400, detail="user_name is required")

    channel_id = resolve_channel(body.channel_id)
    if channel_id is None:
        raise HTTPException(
            status_code=404,
            detail="That join code isn't valid — check the link your group was sent.",
        )

    if JOIN_ALL_CHANNELS:
        user_id, display_name = _global_demo_user(client, user_name)
        channels = STREAM_CHANNELS
    else:
        user_id, display_name = channel_user_id(channel_id, user_name), user_name
        client.upsert_user({"id": user_id, "name": user_name})
        channels = [channel_id]

    token = client.create_token(user_id)
    _join_channels(client, user_id, channels)

    return TokenResponse(
        token=token, user_id=user_id, user_name=display_name, channel_id=channel_id
    )


@app.get("/labeler-spec/{channel_id}")
async def labeler_spec(channel_id: str):
    """Return the labeler spec for a channel's preview stage.

    Rebuilt on demand from the group's approved labeler_config + classification_rules (both
    persisted in the checkpoint) via build_spec. Consumed by the frontend preview screen 
    (?preview=<channel_id>). 404 until rules have been approved and lifecycle_stage is set).
    """
    snapshot = await graph.aget_state({"configurable": {"thread_id": channel_id}})
    values = snapshot.values
    if values.get("lifecycle_stage") is None:
        raise HTTPException(status_code=404, detail="No labeler in preview for this channel")
    return build_spec(values.get("labeler_config"), values.get("classification_rules"))


@app.get("/maintenance-guide/{channel_id}")
async def maintenance_guide(channel_id: str):
    """Return the tailored maintenance guide for a channel — the group's operating record.

    Curated section templates (src/agent/maintenance_guide.py) filled with the group's spec facts
    (labeler name, label count) plus their recorded deployment/governance answers, which tier the
    final going-live section (what it would take -> where they got to -> live). Consumed by the
    frontend guide screen (?guide=<channel_id>) and linked from GOING_LIVE_NEXT_MSG. 404 until the
    channel has an approved labeler (lifecycle set).
    """
    values = (await graph.aget_state({"configurable": {"thread_id": channel_id}})).values
    if values.get("lifecycle_stage") is None:
        raise HTTPException(status_code=404, detail="No labeler for this channel")
    spec = build_spec(values.get("labeler_config"), values.get("classification_rules"))
    return build_maintenance_guide(spec, values.get("deployment"), values.get("governance"))


# Shared secret for the operator-only routes. Generate with `openssl rand -hex 32` and set it on the
# server (Render Environment page) and in the shell that calls the endpoint. UNSET MEANS DISABLED,
# never open: a deploy that forgets it must refuse to dump group state, not serve it to anyone with
# the URL. Note /clear-channel is deliberately NOT gated — it backs the in-app Clear button, and a
# token compiled into the Vite bundle would be public anyway (see docs/technical-pilot-run-sheet.md).
ADMIN_TOKEN = os.environ.get("CLEO_ADMIN_TOKEN", "").strip()


def require_admin_token(authorization: str | None = Header(default=None)) -> None:
    """Guard for operator-only routes. Expects `Authorization: Bearer <CLEO_ADMIN_TOKEN>`."""
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Operator export is not configured — set CLEO_ADMIN_TOKEN on the server.",
        )
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented.strip(), ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing operator token")


@app.get("/export-state/{channel_id}", dependencies=[Depends(require_admin_token)])
async def export_state(channel_id: str):
    """Dump a channel's full checkpoint state — the remote equivalent of scripts/export_checkpoints.py.

    That script reads the SQLite file directly, which on Render lives on the mounted disk at
    /data/checkpoints.sqlite and is unreachable from a laptop. This serves the same values over
    HTTPS so a run can be archived without shelling into the container.

    Emits the same shape the script does ({channel_id: values}, json-safe via default=str, since
    state holds LangChain message objects). Operator-only: the payload is the group's entire
    conversation and design.
    """
    values = (await graph.aget_state({"configurable": {"thread_id": channel_id}})).values
    if not values:
        raise HTTPException(status_code=404, detail=f"No checkpoint state for channel '{channel_id}'")
    payload = json.loads(json.dumps({channel_id: values}, default=str, ensure_ascii=False))
    return JSONResponse(payload)


@app.post("/clear-channel/{channel_id}")
async def clear_channel(channel_id: str):
    """Reset a channel: truncate its Stream messages AND delete its checkpoint thread, so the next
    message starts a fresh CLEO onboarding. Refuses PROTECTED_CHANNELS (e.g. the dev2 demo) so the
    preserved example can't be wiped. Backs the in-app "Clear channel" button."""
    if channel_id in PROTECTED_CHANNELS:
        raise HTTPException(status_code=403, detail=f"Channel '{channel_id}' is a preserved demo and can't be cleared")
    client = get_stream_client()
    channel = client.channel("messaging", channel_id)
    try:
        await asyncio.to_thread(channel.truncate)
    except Exception:
        logger.warning("Failed to truncate Stream channel %s", channel_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Couldn't clear the channel's messages")
    # Wipe the LangGraph thread so setup/lifecycle state doesn't survive the reset.
    try:
        await graph.checkpointer.adelete_thread(channel_id)
    except Exception:
        logger.warning("Failed to delete checkpoint thread for %s", channel_id, exc_info=True)
    return {"status": "cleared", "channel_id": channel_id}


@app.get("/preview-posts/{channel_id}")
async def preview_posts(channel_id: str):
    """Return the mock feed for a channel's preview, generated from the spec + conversation.

    Cached in state keyed by spec_id: generated once per design, reused across refreshes, and
    regenerated only when the rules change (a new spec_id). 404 until the channel is in preview.
    Returns {"spec_id", "posts": [{name, handle, text, media}]}; posts may be [] if generation
    failed, in which case the frontend falls back to its static feed.
    """
    graph_config = {"configurable": {"thread_id": channel_id}}
    values = (await graph.aget_state(graph_config)).values
    if values.get("lifecycle_stage") is None:
        raise HTTPException(status_code=404, detail="No labeler in preview for this channel")

    spec = build_spec(values.get("labeler_config"), values.get("classification_rules"))
    spec_id = spec["spec_id"]

    cached = values.get("preview_posts")
    if cached and cached.get("spec_id") == spec_id:
        return {"spec_id": spec_id, "posts": cached["posts"]}

    posts = await asyncio.to_thread(generate_preview_posts, spec, values.get("messages"))
    if posts:
        # as_node pins the cache write to the terminal node so it's unambiguous no matter where
        # the thread's checkpoint currently sits (langgraph raises "Ambiguous update" otherwise).
        await graph.aupdate_state(
            graph_config, {"preview_posts": {"spec_id": spec_id, "posts": posts}}, as_node="draft_response"
        )
    return {"spec_id": spec_id, "posts": posts}


@app.post("/new-message")
async def new_message(request: Request):
    """Stream webhook — called on every new channel event."""

    body = await request.body()
    signature = request.headers.get("x-signature", "")

    client = get_stream_client()
    if not client.verify_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event_type = payload.get("type")
    logger.info("Webhook received: type=%s", event_type)

    channel_id = payload.get("channel_id", "")
    channel_type = payload.get("channel_type", "messaging")

    if event_type == "reaction.new":
        reaction_data = payload.get("reaction", {})
        message_data = payload.get("message", {})
        message_user_id = (message_data.get("user") or {}).get("id", "")
        reaction_type = reaction_data.get("type", "")

        # 🤖 on a user message summons the agent, same as an @-mention.
        if reaction_type == SUMMON_REACTION:
            if message_user_id.startswith("ai-"):
                return {"status": "ignored"}
            _schedule_agent(channel_type, channel_id, force_respond=True)
            return {"status": "ok"}

        # Only process reactions on AI messages
        if not message_user_id.startswith("ai-"):
            return {"status": "ignored"}

        message_id = reaction_data.get("message_id", "")
        reactor_user_id = (reaction_data.get("user") or {}).get("id", "")

        # Record the reaction regardless of type
        reaction: Reaction = {
            "message_id": message_id,
            "user_id": reactor_user_id,
            "reaction_type": reaction_type,
        }
        config = {"configurable": {"thread_id": channel_id}}

        await asyncio.to_thread(graph.update_state, config, {"reactions": [reaction]})

        logger.info("Stored reaction '%s' on AI message %s", reaction_type, message_id)

        # Only approval reactions trigger the vote check
        if reaction_type != APPROVAL_REACTION:
            return {"status": "ok"}

        # Resolve the vote under a per-channel lock so concurrent 👍🏾 reactions serialize their
        # read-modify-write of the tally instead of clobbering each other (see _vote_locks).
        async with _vote_locks[channel_id]:
            await _process_approval_reaction(channel_type, channel_id, message_id, reactor_user_id)

        return {"status": "ok"}

    # AI welcome message logic for a new channel member event
    if event_type == "member.added":
        member = payload.get("member", {})
        user = member.get("user") or {}
        user_id = user.get("id", "")
        user_name = user.get("name") or user_id

        if user_id.startswith("ai-"):
            return {"status": "ignored"}
        
        asyncio.create_task(_send_welcome_message(channel_type, channel_id, user_name))

        return {"status": "ok"}

    # AI agent is run after a new message event
    if event_type == "message.new":
        message = payload.get("message", {})
        sender_id = (message.get("user") or {}).get("id", "")

        if sender_id.startswith("ai-") or message.get("ai_generated"):
            return {"status": "ignored"}

        # Deterministic pre-check: an @-mention or a bare name at message start
        # always responds — no LLM router call needed.
        mentioned_ids = [(u or {}).get("id", "") for u in message.get("mentioned_users") or []]
        force_respond = message_addresses_ai(
            message.get("text", ""),
            ai_user_name=AI_USER_NAME,
            mentioned_user_ids=mentioned_ids,
            ai_user_id=AI_USER_ID,
        )

        snapshot = await graph.aget_state({"configurable": {"thread_id": channel_id}})
        lifecycle_stage = snapshot.values.get("lifecycle_stage")

        # Both follow-ups below write graph state, so they are handed to the scheduler as after-run
        # hooks rather than spawned as parallel tasks: a bare create_task here races the agent's
        # own graph run and loses the write (see _after_run_hooks). They're mutually exclusive —
        # each is gated on a different lifecycle_stage — so at most one is ever queued.
        after_run = None

        # The going-live questions are answered as a CONVERSATION: the group talks among itself,
        # decides, and pulls CLEO in when it's done or stuck. So the capture runs only on an
        # explicit trigger, never per message — it used to fire on every message, which put a
        # confirm card under each answer and turned a group discussion into an interrogation.
        # capture_governance reads the accumulated history, so one trigger picks up everything
        # said since the last one; nothing is lost by waiting.
        if lifecycle_stage == "provision" and force_respond:
            after_run = _run_governance_capture

        # The way back to going live after the group took the guide path. Deterministic rather than
        # classified: PATH_NOT_TAKEN_NOTE and GUIDE_CHOSEN_MSG both name this exact phrase, so it's
        # a documented affordance and not a guess at intent. _reopen_go_live_and_report re-checks
        # the stage and that no anchor is already open.
        elif lifecycle_stage == "deploy" and force_respond and asks_to_go_live(message.get("text", "")):
            after_run = _reopen_go_live_and_report

        _schedule_agent(channel_type, channel_id, force_respond, after_run=after_run)

        return {"status": "ok"}

    return {"status": "ignored"}


@app.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest):
    """Direct graph invocation - for testing without Stream frontend."""

    lc_messages = [
        AIMessage(content=m.content) if m.role == "assistant" else HumanMessage(content=m.content)
        for m in body.messages
    ]
    last_user_text = next((m.content for m in reversed(body.messages) if m.role != "assistant"), "")
    force_respond = message_addresses_ai(last_user_text, ai_user_name=AI_USER_NAME)
    graph_config = {"configurable": {"thread_id": STREAM_CHANNEL_ID}}
    result = graph.invoke(
        {"messages": lc_messages, "labeler_config": {}, "force_respond": force_respond},
        graph_config,
    )
    response_text = result.get("draft_response", "")

    return ChatResponse(messages=[*body.messages, {"role": "assistant", "content": response_text}])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.chatbot:app", host="0.0.0.0", port=8000, reload=True)
