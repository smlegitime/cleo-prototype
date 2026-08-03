#!/usr/bin/env python3
"""Export the LangGraph checkpointer contents — each channel's state — as readable JSON.

For every channel (thread) in the checkpoint DB, dumps the latest state values: labeler config,
classification rules, lifecycle stage, spec_id, deployment record, in-flight votes, and the
conversation. Useful for inspecting or backing up what a group has built. Rich objects (LangChain
messages, etc.) are stringified via json default=str.

RUN IN THE SAME ENVIRONMENT AS THE SERVER (conda env, .env present). Reading is safe while the server
runs (WAL allows concurrent readers), but stop it if you want a guaranteed-consistent snapshot.

Usage:
    python3 scripts/export_checkpoints.py                    # all channels -> stdout
    python3 scripts/export_checkpoints.py <channel_id> ...   # only these channels -> stdout
    python3 scripts/export_checkpoints.py --out dump.json    # write to a file instead of stdout
    python3 scripts/export_checkpoints.py --list             # just the channel ids + lifecycle stage
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _thread_ids(conn) -> list[str]:
    """All distinct channel/thread ids present in the checkpoint DB."""
    async with conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id") as cur:
        return [row[0] for row in await cur.fetchall()]


def _parse_args(argv: list[str]) -> tuple[list[str], str | None, bool]:
    channels: list[str] = []
    out_path: str | None = None
    list_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--list":
            list_only = True
        elif a == "--out":
            i += 1
            out_path = argv[i] if i < len(argv) else None
        elif a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        elif a.startswith("--"):
            pass  # ignore unknown flags
        else:
            channels.append(a)
        i += 1
    return channels, out_path, list_only


async def main() -> int:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return 0
    channels_arg, out_path, list_only = _parse_args(sys.argv[1:])

    from src.agent.brainstorming.graph import attach_sqlite_checkpointer, graph

    conn = await attach_sqlite_checkpointer()
    try:
        channels = channels_arg or await _thread_ids(conn)
        if not channels:
            print("No channels found in the checkpoint DB.")
            return 0

        if list_only:
            for cid in channels:
                v = (await graph.aget_state({"configurable": {"thread_id": cid}})).values or {}
                print(f"{cid:<24} setup={v.get('setup_stage')}  lifecycle={v.get('lifecycle_stage')}")
            return 0

        dump = {}
        for cid in channels:
            dump[cid] = (await graph.aget_state({"configurable": {"thread_id": cid}})).values or {}
        text = json.dumps(dump, indent=2, default=str, ensure_ascii=False)

        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"Wrote {len(dump)} channel(s) to {out_path}")
        else:
            print(text)
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
