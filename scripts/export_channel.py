"""
Export all messages from the Stream Chat channel to JSON and text transcript files.

Usage:
    conda run -n bsky-coll-eng python scripts/export_channel.py
    conda run -n bsky-coll-eng python scripts/export_channel.py --limit 300 --output-dir exports/
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from stream_chat import StreamChat

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.api.helpers import get_last_messages_from_channel

load_dotenv()


def fetch_all_messages(channel, limit: int) -> list[dict]:
    """Fetch up to `limit` messages, paginating if needed (Stream max is 300 per call)."""
    PAGE_SIZE = 300
    all_messages = []
    oldest_id = None

    while len(all_messages) < limit:
        batch_limit = min(PAGE_SIZE, limit - len(all_messages))
        query_params = {"limit": batch_limit}
        if oldest_id:
            query_params["id_lt"] = oldest_id

        result = channel.query(messages=query_params)
        batch = result.get("messages", [])
        if not batch:
            break

        all_messages = batch + all_messages
        oldest_id = batch[0]["id"]

        if len(batch) < batch_limit:
            break

    return [m for m in all_messages if m.get("text", "").strip()]


def to_export_record(m: dict) -> dict:
    return {
        "id": m["id"],
        "role": "assistant" if m["user"]["id"].startswith("ai-") else "user",
        "user_id": m["user"]["id"],
        "user_name": m["user"].get("name", m["user"]["id"]),
        "text": m["text"].strip(),
        "created_at": m.get("created_at", ""),
    }


def write_json(records: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"JSON saved:       {path}")


def write_transcript(records: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            label = "AI Assistant" if r["role"] == "assistant" else r["user_name"]
            timestamp = r["created_at"][:16].replace("T", " ") if r["created_at"] else ""
            header = f"[{timestamp}] {label}" if timestamp else label
            f.write(f"{header}\n{r['text']}\n\n")
    print(f"Transcript saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="Export Stream Chat channel messages.")
    parser.add_argument("--limit", type=int, default=500, help="Max messages to fetch (default: 500)")
    parser.add_argument("--output-dir", type=str, default=".", help="Directory to write output files (default: current dir)")
    args = parser.parse_args()

    api_key = os.environ.get("STREAM_API_KEY")
    api_secret = os.environ.get("STREAM_API_SECRET")
    channel_id = os.environ.get("STREAM_CHANNEL_ID", "general")
    if not api_key or not api_secret:
        print("Error: STREAM_API_KEY and STREAM_API_SECRET must be set.")
        sys.exit(1)

    client = StreamChat(api_key=api_key, api_secret=api_secret)
    channel = client.channel("messaging", channel_id)

    print(f"Fetching up to {args.limit} messages from channel '{channel_id}'...")
    raw = fetch_all_messages(channel, args.limit)
    records = [to_export_record(m) for m in raw]
    print(f"Fetched {len(records)} messages.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    write_json(records, output_dir / f"channel_{channel_id}_{timestamp}.json")
    write_transcript(records, output_dir / f"channel_{channel_id}_{timestamp}.txt")


if __name__ == "__main__":
    main()
