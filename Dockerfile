# ---- Stage 1: build the labeler-engine (TypeScript -> dist/*.js) ----
# The generate (rule-quality report) and deploy (sandbox executor) stages run Node over
# labeler-engine/dist, so the runtime image must ship a built engine + a Node runtime. dist/ is
# gitignored, so we build it here rather than relying on a local copy.
FROM node:22-slim AS engine-builder
WORKDIR /build
COPY labeler-engine/ ./labeler-engine/
RUN npm install -g typescript@5 \
    && cd labeler-engine \
    && tsc -p tsconfig.build.json

# ---- Stage 2: runtime ----
FROM python:3.12-slim

WORKDIR /app

# System deps: build-essential/libgomp1 for faiss-cpu / onnxruntime / grpcio; nodejs to run the
# compiled interpreter (labeler-engine/dist/*.js) at the generate/deploy stages.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source + ops scripts (clear/rewind/export checkpoints)
COPY src/ ./src/
COPY scripts/ ./scripts/

# The labeler-engine: source + the dist built in stage 1. Python locates it at
# <repo>/labeler-engine/dist/{batch,execute}.js (see quality.py / sandbox.py) = /app/labeler-engine/dist.
COPY labeler-engine/ ./labeler-engine/
COPY --from=engine-builder /build/labeler-engine/dist ./labeler-engine/dist

EXPOSE 8000

# Render (and most PaaS) inject $PORT; fall back to 8000 for local `./scripts/start.sh --docker`.
CMD ["sh", "-c", "uvicorn src.api.chatbot:app --host 0.0.0.0 --port ${PORT:-8000}"]
