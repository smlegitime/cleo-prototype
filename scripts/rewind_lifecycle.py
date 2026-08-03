#!/usr/bin/env python3
"""Rewind ONE channel back to the `preview` stage — undoing the generate (quality-testing) and
deploy (bundle + sandbox run) steps — so the group can re-run them.

The post-preview lifecycle stages are written to the checkpoint by out-of-graph update_state calls,
so this resets those fields via aupdate_state: lifecycle_stage -> 'preview', and it clears the
generate/deploy artifacts (quality_corpus, quality_report, deployment, sandbox_run, the ship gate).
It also re-arms the preview-approval anchor (committed -> False) so reacting to it re-advances
preview -> generate. Everything else — labeler_config, classification_rules, spec_id, preview_posts,
the conversation — is preserved.

RUN IN THE SAME ENVIRONMENT AS THE SERVER (conda env, .env present) and STOP THE API SERVER FIRST:
the server holds its own AsyncSqliteSaver connection to the same SQLite DB, and a concurrent write
can hit "database is locked" or be superseded mid-operation.

NOTE: this rewinds the graph STATE only. It does NOT delete the messages already posted to the Stream
channel (the old quality report / bundle messages stay in the chat) — Stream is a separate store.

Usage:
    python3 scripts/rewind_lifecycle.py <channel_id>           # rewind (prompts to confirm)
    python3 scripts/rewind_lifecycle.py <channel_id> -y        # skip the prompt
    python3 scripts/rewind_lifecycle.py <channel_id> --list    # just show the checkpoint history
"""

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Fields written by the generate/deploy/execute steps — reset to their pre-generate values. spec_id,
# preview_posts, labeler_config, classification_rules, messages, etc. are deliberately NOT touched.
RESET_FIELDS = {
    "lifecycle_stage": "preview",
    "lifecycle_status": "pending",
    "lifecycle_error": None,
    "quality_corpus": None,
    "quality_report": None,
    "pending_deploy_approval": None,
    "deployment": None,
    "sandbox_run": None,
}


def _server_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "uvicorn src.api.chatbot:app"],
            capture_output=True,
        ).returncode == 0
    except FileNotFoundError:
        return False  # pgrep unavailable; can't check


def _mark(v: dict, key: str) -> str:
    return "Y" if v.get(key) else "-"


async def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    channel_id = args[0]
    list_only = "--list" in args
    assume_yes = "-y" in args

    if not list_only and _server_running():
        print("ERROR: the API server appears to be running (uvicorn src.api.chatbot:app).", file=sys.stderr)
        print("       Stop it first, then re-run this script.", file=sys.stderr)
        return 1

    from src.agent.brainstorming.graph import attach_sqlite_checkpointer, graph

    conn = await attach_sqlite_checkpointer()
    try:
        config = {"configurable": {"thread_id": channel_id}}

        if list_only:
            found = False
            async for s in graph.aget_state_history(config):
                found = True
                v = s.values or {}
                cid = s.config.get("configurable", {}).get("checkpoint_id", "?")
                print(f"{s.created_at}  stage={str(v.get('lifecycle_stage')):<9} "
                      f"corpus={_mark(v, 'quality_corpus')} report={_mark(v, 'quality_report')} "
                      f"deploy={_mark(v, 'deployment')} run={_mark(v, 'sandbox_run')}  ckpt={cid}")
            if not found:
                print(f"No checkpoint history for channel {channel_id!r}.")
            return 0

        snap = await graph.aget_state(config)
        values = snap.values or {}
        stage = values.get("lifecycle_stage")
        if stage in (None, "preview"):
            print(f"Channel {channel_id!r} is at lifecycle_stage={stage!r} — already at/before preview; "
                  "nothing to rewind.")
            return 0

        reset = dict(RESET_FIELDS)
        prev = values.get("pending_preview_approval")
        if prev and prev.get("message_id"):
            reset["pending_preview_approval"] = {
                "message_id": prev["message_id"], "approved_by": [], "committed": False,
            }

        if not assume_yes:
            print(f"Rewind channel {channel_id!r}: lifecycle_stage {stage!r} -> 'preview', clearing "
                  "quality_corpus / quality_report / deployment / sandbox_run / the ship gate.")
            if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Aborted.")
                return 0

        await graph.aupdate_state(config, reset)
        after = (await graph.aget_state(config)).values.get("lifecycle_stage")
        print(f"Done. lifecycle_stage is now {after!r}. Re-react 👍🏾 to the preview-approval message "
              "to re-run the build + quality check.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
