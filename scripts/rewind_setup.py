#!/usr/bin/env python3
"""Rewind ONE channel to an earlier checkpoint — e.g. to before a proposal was staged.

Companion to rewind_lifecycle.py, which resets the post-preview stages by writing corrected values
forward. That approach cannot undo a staged proposal: pending_suggestions and pending_rule_suggestions
merge on update (see _merge_dicts in src/agent/state.py), so update_state can add or overwrite keys
but never remove one. Same for messages, reactions, and feedback_messages.

So this rewinds by FORKING instead: it re-applies an earlier checkpoint's values as a new head of the
thread. Nothing is deleted — the history is appended to, and the checkpoints you rewound past are
still there (this script can roll you forward to them again just as easily).

RUN IN THE SAME ENVIRONMENT AS THE SERVER (conda env, .env present) and STOP THE API SERVER FIRST:
the server holds its own AsyncSqliteSaver connection to the same SQLite DB, and a concurrent write
can hit "database is locked" or be superseded mid-operation.

NOTE: this rewinds the graph STATE only. Messages already posted to the Stream channel stay put, so a
proposal card staged after the target checkpoint is left ORPHANED: its message_id is no longer in
pending_suggestions, so a 👍🏾 on it now falls through every gate and does nothing at all. Delete those
messages in Stream (or say so in the channel) after rewinding. For a total reset with no orphans,
POST /clear-channel/{id} truncates the messages AND deletes the thread.

Usage:
    python3 scripts/rewind_setup.py <channel_id>                       # list the checkpoint history
    python3 scripts/rewind_setup.py <channel_id> <checkpoint_id>       # rewind to it (prompts)
    python3 scripts/rewind_setup.py <channel_id> <checkpoint_id> -y    # skip the prompt
"""

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _server_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "uvicorn src.api.chatbot:app"],
            capture_output=True,
        ).returncode == 0
    except FileNotFoundError:
        return False  # pgrep unavailable; can't check


def _live(suggestions: dict | None) -> int:
    """Proposals still open for a vote — what "before a proposal was staged" actually means."""
    return sum(
        1 for s in (suggestions or {}).values()
        if not s.get("committed") and not s.get("superseded")
    )


def _summary(values: dict) -> str:
    labels = len(((values.get("labeler_config") or {}).get("labels")) or [])
    staged = _live(values.get("pending_suggestions")) + _live(values.get("pending_rule_suggestions"))
    return (
        f"setup={str(values.get('setup_stage')):<9} "
        f"lifecycle={str(values.get('lifecycle_stage')):<8} "
        f"staged={staged} labels={labels} "
        f"rules={len(values.get('classification_rules') or {})} "
        f"notes={len(values.get('design_notes') or [])}"
    )


async def main() -> int:
    args = [a for a in sys.argv[1:] if a != "-y"]
    assume_yes = "-y" in sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    channel_id = args[0]
    target_id = args[1] if len(args) > 1 else None

    if target_id and _server_running():
        print("ERROR: the API server appears to be running (uvicorn src.api.chatbot:app).", file=sys.stderr)
        print("       Stop it first, then re-run this script.", file=sys.stderr)
        return 1

    from src.agent.brainstorming.graph import attach_sqlite_checkpointer, graph

    conn = await attach_sqlite_checkpointer()
    try:
        config = {"configurable": {"thread_id": channel_id}}

        # Read the whole history once: listing needs it, and rewinding needs it to validate the
        # target. An unknown checkpoint_id is accepted silently by the saver and writes a head with
        # no values behind it — a typo would empty the channel — so it is checked, not trusted.
        history = []
        async for snapshot in graph.aget_state_history(config):
            history.append(snapshot)

        if not history:
            print(f"No checkpoint history for channel {channel_id!r}.")
            return 0

        head_id = history[0].config["configurable"]["checkpoint_id"]

        if target_id is None:
            print(f"Checkpoint history for {channel_id!r} (newest first):\n")
            for snapshot in history:
                cid = snapshot.config["configurable"]["checkpoint_id"]
                marker = "<- current" if cid == head_id else ""
                print(f"{snapshot.created_at}  {_summary(snapshot.values or {})}  ckpt={cid} {marker}")
            print(
                "\nTo rewind, re-run with the ckpt= value of the checkpoint you want to return to "
                "(staged=0 is a checkpoint with no proposal open for a vote)."
            )
            return 0

        target = next(
            (s for s in history if s.config["configurable"]["checkpoint_id"] == target_id), None
        )
        if target is None:
            print(f"ERROR: checkpoint {target_id!r} is not in the history for {channel_id!r}.", file=sys.stderr)
            print("       Run without a checkpoint id to list the valid ones.", file=sys.stderr)
            return 1

        if target_id == head_id:
            print(f"Checkpoint {target_id} is already the current state — nothing to rewind.")
            return 0

        values = target.values or {}
        discarded = next(i for i, s in enumerate(history)
                         if s.config["configurable"]["checkpoint_id"] == target_id)

        if not assume_yes:
            print(f"Rewind {channel_id!r} to {target.created_at}:")
            print(f"  {_summary(values)}")
            print(f"  current:  {_summary(history[0].values or {})}")
            print(f"\nThis re-applies those values as the new current state, stepping back past "
                  f"{discarded} checkpoint(s). Nothing is deleted — they stay in the history and you "
                  f"can roll forward to them the same way.")
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 0

        # checkpoint_ns is required here — without it the saver raises a bare KeyError.
        fork_config = {
            "configurable": {
                "thread_id": channel_id,
                "checkpoint_ns": "",
                "checkpoint_id": target_id,
            }
        }
        await graph.aupdate_state(fork_config, {})

        after = (await graph.aget_state(config)).values or {}
        print(f"\nDone. Current state is now: {_summary(after)}")
        print(
            "\nStream messages were NOT touched. Any proposal card posted after the checkpoint you "
            "rewound to is now orphaned — reacting to it does nothing — so delete those messages in "
            "the channel or tell the group to ignore them."
        )
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
