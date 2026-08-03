#!/usr/bin/env bash
# Clears the LangGraph checkpointer so every channel/thread starts fresh.
#
# Removes the runtime SQLite DB the API server (start.sh / uvicorn) uses — src/data/checkpoints.sqlite
# by default, or $CHECKPOINT_DB_PATH if set — plus its -wal / -shm sidecars (the real state can live
# in -wal until the connection closes). LangGraph recreates an empty DB on the next start.
#
# STOP THE API SERVER FIRST: the AsyncSqliteSaver holds an open connection; deleting the file while
# it's running leaves a live handle to a now-unlinked DB and the WAL may be re-flushed on shutdown.
#
# This does NOT touch .langgraph_api/*.pckl (a different run mode: `langgraph dev` / the LangGraph
# platform), which the start.sh server does not use.
#
# Usage: ./scripts/clear_checkpoints.sh [-y]
#   -y   skip the confirmation prompt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${CHECKPOINT_DB_PATH:-$ROOT/src/data/checkpoints.sqlite}"

# Guard: refuse if the API server looks like it's still running (best-effort).
if pgrep -f "uvicorn src.api.chatbot:app" >/dev/null 2>&1; then
    echo "ERROR: the API server appears to be running (uvicorn src.api.chatbot:app)." >&2
    echo "       Stop it first, then re-run this script." >&2
    exit 1
fi

if [ ! -e "$DB" ] && [ ! -e "$DB-wal" ] && [ ! -e "$DB-shm" ]; then
    echo "Nothing to clear — no checkpoint DB at: $DB"
    exit 0
fi

if [ "${1:-}" != "-y" ]; then
    echo "This will DELETE the checkpointer (all channels/threads reset):"
    echo "  $DB (+ -wal, -shm)"
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        [yY]|[yY][eE][sS]) ;;
        *) echo "Aborted."; exit 0 ;;
    esac
fi

rm -f "$DB" "$DB-wal" "$DB-shm"
echo "Cleared checkpointer: $DB (+ -wal, -shm). A fresh DB is created on the next server start."
