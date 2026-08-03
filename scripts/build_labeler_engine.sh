#!/usr/bin/env bash
# Compiles labeler-engine/*.ts -> labeler-engine/dist/*.js, the Node-runnable form of the ONE
# canonical interpreter. The Python generate-stage quality report shells out to dist/batch.js
# (see src/agent/quality.py). dist/ is gitignored, so run this after cloning and after any edit to
# labeler-engine/*.ts. Needs the TypeScript compiler; it reuses the frontend's tsc so there is no
# extra install to manage.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE_DIR="$ROOT/labeler-engine"

# Locate tsc: prefer the frontend's (already a dependency), fall back to one on PATH.
if [ -x "$ROOT/frontend/node_modules/.bin/tsc" ]; then
    TSC="$ROOT/frontend/node_modules/.bin/tsc"
elif command -v tsc >/dev/null 2>&1; then
    TSC="tsc"
else
    echo "ERROR: tsc not found. Run 'npm install' in ./frontend, or install TypeScript globally." >&2
    exit 1
fi

echo "Building labeler-engine with $TSC ..."
"$TSC" -p "$ENGINE_DIR/tsconfig.build.json"
echo "Built -> $ENGINE_DIR/dist/"
