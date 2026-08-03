import os

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from src.config import CHECKPOINT_DB_PATH
from src.agent.state import BrainstormingAgentState
from src.agent.brainstorming.nodes import (
    router,
    validate_and_classify,
    search_documentation,
    summarize_conversation,
    provide_feedback,
    acknowledge_pending,
    draft_response
)

# Create the graph
workflow = StateGraph(BrainstormingAgentState)

workflow.add_node("router", router)
workflow.add_node("validate_and_classify", validate_and_classify)
workflow.add_node("search_documentation", search_documentation)
workflow.add_node("summarize_conversation", summarize_conversation)
workflow.add_node("provide_feedback", provide_feedback)
workflow.add_node("acknowledge_pending", acknowledge_pending)
workflow.add_node("draft_response", draft_response)

# Add only the essential edges (Command responses determine conditional control flow)
workflow.add_edge(START, "router")
workflow.add_edge("draft_response", END)
# Terminal like draft_response: it writes draft_response itself (no LLM call, nothing to stream),
# which the runner picks up from final state.
workflow.add_edge("acknowledge_pending", END)

# Compile with an in-memory checkpointer. Persistence uses AsyncSqliteSaver, which binds to
# the running event loop when constructed (in chatbot.py)
graph = workflow.compile(checkpointer=MemorySaver())
# graph = workflow.compile() # Langgraph server


def sqlite_persistence_enabled() -> bool:
    """True for the running server; False when CHECKPOINT_BACKEND=memory (tests/evals)."""
    return os.environ.get("CHECKPOINT_BACKEND", "sqlite").lower() != "memory"


async def attach_sqlite_checkpointer(db_path: str | None = None):
    """Attach a persistent AsyncSqliteSaver to the graph, bound to the running event loop.

    This is what lets CLEO resume a group mid-lifecycle after a restart: setup_stage, labeler_config, 
    classification_rules, lifecycle_stage/spec_id and in-flight votes all live in the checkpoint, 
    so without persistence a restart silently resets every channel to setup_stage='purpose'.
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = db_path or os.environ.get("CHECKPOINT_DB_PATH") or str(CHECKPOINT_DB_PATH)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the single writer
    saver = AsyncSqliteSaver(conn)
    await saver.setup()  # create checkpoint tables if they don't exist yet (idempotent)
    graph.checkpointer = saver
    return conn
