"""
Registers the generated NGROK URL from ./start.sh as a Stream webhook 
Run:
    - by ./start.sh script
    - or separately: python scripts/register_stream_webhook.py <ngrok URL>
"""

import os, sys
from pathlib import Path

from getstream import Stream
from getstream.models import EventHook

# Load .env from project root
env_path = Path(__file__).parent / ".env" if "__file__" in dir() else Path(".env")
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=".env")
except ImportError:
    pass

# Create Stream client, read webhook url from python command (in ./start) and push to Stream app
client = Stream(
    api_key=os.environ.get("STREAM_API_KEY"), 
    api_secret=os.environ.get("STREAM_API_SECRET")
)

webook_url = sys.argv[1] if len(sys.argv) > 1 else None
if not webook_url:
    print("ERROR: webhook URL is required", file=sys.stderr)
    sys.exit(1)

# Appends webhook to existing webhooks list instead of overwriting
res = client.get_app()
existing_hooks = res.data.app.event_hooks or []

new_webook_hook = EventHook(
    enabled=True,
    hook_type="webhook",
    webhook_url=webook_url,
    event_types=["message.new", "member.added", "reaction.new"] # member chat, addition, and message reaction events
)
client.update_app(event_hooks=existing_hooks + [new_webook_hook])
