#!/usr/bin/env bash
# Starts the API server, and optionally the ngrok tunnel and Stream webhook (--e2e).
# Usage: ./start.sh [--docker] [--e2e] [--reload]
#   --reload  hot-reload the API on .py changes (local/non-docker only; dev convenience)

# set -euo pipefail

error_and_exit() {
    echo "ERROR: $@" 1>&2
    exit 1
}

USE_DOCKER=false
USE_E2E=false
USE_RELOAD=false
for arg in "$@"
do
    case "$arg" in
        --docker) USE_DOCKER=true ;;
        --e2e)    USE_E2E=true ;;
        --reload) USE_RELOAD=true ;;
        *) error_and_exit "Unknown argument: $arg" ;;
    esac
done

# --reload only affects the local uvicorn process; the Docker image runs its own CMD.
if $USE_DOCKER && $USE_RELOAD; then
    error_and_exit "--reload is not supported with --docker (it only applies to the local uvicorn server)"
fi

## Preflight checks (i.e, do the commands exist)
if $USE_DOCKER; then
    command -v docker >/dev/null 2>&1 || error_and_exit "docker not found"
else
    command -v uvicorn >/dev/null 2>&1 || error_and_exit "uvicorn not found - activate your virtual env first"
fi
if $USE_E2E; then
    command -v ngrok >/dev/null 2>&1 || error_and_exit "ngrok not found - install it and run: ngrok config add-authtoken <YOUR_AUTHTOKEN>"
fi
command -v python3 >/dev/null 2>&1 || error_and_exit "python3 not found"
# Setting variables and their defaults
PORT="${PORT:-8000}"
IMAGE="${IMAGE:-bsky-agent-api}"
CONTAINER="bsky-api-server"
NGROK_PID=""

## Build the labeler-engine (Node interpreter the generate-stage quality report shells out to).
## dist/ is gitignored; non-fatal so the server still starts if the build can't run (quality
## reports are then unavailable until it is built). NOTE: the Docker path builds its own engine —
## the Dockerfile is multi-stage (node stage compiles labeler-engine/dist, runtime stage installs
## node), so quality reports + the sandbox run work in-container without this local build.
if ! $USE_DOCKER; then
    ./scripts/build_labeler_engine.sh || echo "WARNING: labeler-engine build failed; quality reports will be unavailable until './scripts/build_labeler_engine.sh' succeeds."
fi

## Start API server
echo "Starting CLEO API server on port $PORT..."
if $USE_DOCKER; then
    docker build -t "$IMAGE" .
    docker run --rm -d \
        --name "$CONTAINER" \
        --env-file .env \
        -p "$PORT:8000" \
        "$IMAGE"
else
    RELOAD_FLAG=""
    if $USE_RELOAD; then
        RELOAD_FLAG="--reload"
        echo "Hot reload enabled (uvicorn --reload)"
    fi
    uvicorn src.api.chatbot:app --host 0.0.0.0 --port "$PORT" $RELOAD_FLAG &
    API_PID=$!
fi

## Start ngrok and register Stream webhook (e2e only)
if $USE_E2E; then
    echo "Starting ngrok tunnel..."
    ngrok http "$PORT" --log=stdout >/tmp/ngrok-bsky.log 2>&1 &
    NGROK_PID=$!

    # Wait for ngrok to start and capture its generated HTTPS URL
    GET_NGROK_URL_PYTHON_SCRIPT="
import sys, json
try:
    data = json.load(sys.stdin)
    tunnels = data.get('tunnels', [])
    url = next((t['public_url'] for t in tunnels if t['public_url'].startswith('https')), '')
    print(url)
except Exception:
    print('')
"    
    NGROK_URL=""
    for i in {1..20} # poll up to 20 times for URL, with 1 sec intervals
    do
        NGROK_URL=$(
            curl -s http://localhost:4040/api/tunnels 2>/dev/null \
            | python3 -c "$GET_NGROK_URL_PYTHON_SCRIPT" 2>/dev/null
        )
        if [ -n "$NGROK_URL" ]; then # if url field isn't empty, exit loop
            break
        fi
        sleep 1
    done
    
    if [ -z "$NGROK_URL" ]; then # if URL is still empty after the loop completes
        echo "ngrok log tail:" >&2
        tail -20 /tmp/ngrok-bsky.log >&2
        error_and_exit "Could not get ngrok public URL after 20 seconds"
    fi

    WEBHOOK_URL="${NGROK_URL}/new-message"
    echo "ngrok URL:    $NGROK_URL"
    echo "Webhook URL:  $WEBHOOK_URL"

    echo "Registering webhook with Stream..."
    python3 "./scripts/register_stream_webhook.py" "$WEBHOOK_URL"
fi

## Cleanup
cleanup(){
    echo ""
    echo "Shutting down..."
    if [ -n "$WEBHOOK_URL" ]; then
        echo "Removing Stream webhook..."
        python3 "./scripts/deregister_stream_webhook.py" "$WEBHOOK_URL" || true
    fi
    if $USE_DOCKER; then
        docker stop "$CONTAINER" 2>/dev/null || true
    else
        kill "$API_PID" 2>/dev/null || true
    fi
    if [ -n "$NGROK_PID" ]; then
        kill "$NGROK_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

echo ""
echo "All services running :)"
echo "  API server: http://localhost:${PORT}"
if $USE_E2E; then
    echo "  Webhook:    ${WEBHOOK_URL}"
fi
echo ""
echo "Press Ctrl+C to stop the service."

## Keep alive
if $USE_DOCKER; then
    if $USE_E2E; then
        wait "$NGROK_PID"
    else
        docker wait "$CONTAINER"
    fi
else
    wait "$API_PID"
fi
