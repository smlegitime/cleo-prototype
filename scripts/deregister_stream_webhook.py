"""
Removes a previously-registered ngrok webhook URL from the Stream app.
Run:
    - by ./start.sh cleanup (automatically on Ctrl+C after --e2e)
    - or separately: python scripts/deregister_stream_webhook.py <webhook URL>
"""

import os, sys
from pathlib import Path

from getstream import Stream

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env")
except ImportError:
    pass

# Create Stream client, read webhook url from python command (in ./start) and push to Stream app
client = Stream(
    api_key=os.environ.get("STREAM_API_KEY"),
    api_secret=os.environ.get("STREAM_API_SECRET"),
)

webhook_url = sys.argv[1] if len(sys.argv) > 1 else None
if not webhook_url:
    print("ERROR: webhook URL is required", file=sys.stderr)
    sys.exit(1)

# Looks for existing webhooks, especially the one that matches the arg, and removes it
res = client.get_app()
existing_hooks = res.data.app.event_hooks or []
filtered = [h for h in existing_hooks if h.webhook_url != webhook_url]

if len(filtered) == len(existing_hooks):
    print(f"Webhook not found, nothing to remove: {webhook_url}")
else:
    client.update_app(event_hooks=filtered)
    print(f"Webhook removed: {webhook_url}")
