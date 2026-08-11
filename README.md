# CLEO - The Collective Labeler Engineering Orchestrator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

 <div align="center">
  <picture>
    <source
    srcset="./frontend/public/pixel_logo_1.svg"
    width="125" height="125"
    />
  </picture>
    
  <h3 align="center">Test</h3>
</div>

A group chat AI assistant that helps teams collaboratively design [Bluesky labelers](https://docs.bsky.app/docs/advanced-guides/moderation). Through a guided setup mechanism, CLEO answers questions about Bluesky and the AT Protocol, creates and provides feedback on labeler definitions, labels, and classification rules, stages proposed labeler configuration changes for channel approval via majority vote, runs previews to support deliberation, evaluated the quality of the labeler in a sandbox environment, and offer maintenance guidance to the group.
<!-- <img width="756" height="391" alt="Screenshot 2026-03-09 at 6 10 52 PM" src="https://github.com/user-attachments/assets/1bf9b78e-d94a-4d47-bb3a-bedf12446d9b" /> -->
<!-- <img width="974" height="612" alt="Screenshot 2026-04-03 at 4 42 57 PM" src="https://github.com/user-attachments/assets/d2c67e32-e8ab-4c62-bde8-7202c09b29ff" /> -->
<img width="892" height="407" alt="cleo_graph" src="https://github.com/user-attachments/assets/7132c3b7-3c74-4c4b-966e-c7e9c6b5e531" />


## Features

### Conversation

- **Q&A** - Answers questions about Bluesky, the AT Protocol, and labelers using RAG over a curated knowledge base
- **Guided setup** - Walks a new channel through purpose -> content -> rules before opening the full assistant
- **Label feedback** - Creates or refines label definitions (identifier, severity, blurs, default_setting, locales)
- **Labeler config feedback** - Suggests updates to the labeler's display name and description
- **Classification rules** - Derives structured, human-readable matching rules (keyword / pattern / account signals grouped in disjunctive normal form) that decide when each label fires
- **Show config** - Displays the current labeler configuration as a structured block on request
- **Conversation summary** - Summarizes the discussion to date, including current config state
- **Community guidelines** - Rejects off-topic, harmful, or sensitive content before it reaches the agent
- **Majority voting** - Proposed changes (label config, rules, and lifecycle advances) are staged for channel approval; applied only once a vote threshold is met
- **Summon to speak** - In a channel CLEO answers only when explicitly addressed — an @-mention, its name at the start of a message, or a 🤖 reaction — so the group deliberates undisturbed and pulls it in when ready. Stage handoffs don't depend on this: every gate advances on a 👍🏾 vote
- **Streaming responses** - Token-level streaming to Stream Chat with debounced partial updates
- **Concurrency guard** - Per-channel lock prevents concurrent agent runs on the same thread

### Lifecycle

Once setup is complete and the rules are approved, the group moves the design through a staged, opt-in lifecycle (`preview → generate → deploy → provision → live`):

- **Preview** - A shareable screen (`/?preview=<channel_id>`) that renders each label's rules against AI-generated sample posts, so the group can judge the design before anything is built
- **Quality report** - On entering `generate`, fetches a real Bluesky post corpus derived from the spec and runs the rules over it to measure match quality against actual posts
- **Sandbox deploy** - On the ship gate, materializes the design into a content-addressed bundle and runs it end-to-end under a throwaway `did:web` identity, emitting signed label records locally — nothing is published and no Bluesky identity is claimed
- **Maintenance guide** - A plain-language opt-out guide (`/?guide=<channel_id>`) for groups that decline, or defer, going live

## Agent Architecture

The app has two cooperating halves: the **conversation graph** (a LangGraph `StateGraph` that owns the chat and flips `lifecycle_stage`) and the **lifecycle orchestrator** (out-of-graph, side-effecting work that runs each post-setup stage). They share one per-channel checkpoint.

### Conversation/Brainstorming graph

The brainstorming agent is a [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph`. State is checkpointed per channel (thread): the graph compiles with an in-memory `MemorySaver`, which the server swaps for a persistent `AsyncSqliteSaver` on startup (`attach_sqlite_checkpointer` in `graph.py`) so a group's progress persists beyond API restarts.

```
message.new webhook
    │
    ▼
router ──[skip]──► (no response)
    │  [respond]
    │
    ▼
validate_and_classify
    │
    ├─ [violation] ─────────────────────────────────────────────┐
    ├─ [summary] ──► summarize_conversation ────────────────────┤
    ├─ [show_config] ───────────────────────────────────────────┤
    ├─ [question] ──► search_documentation ─────────────────────┤
    │                (bluesky / atproto / labeler / label)      │
    ├─ [feedback] ──► provide_feedback ─────────────────────────┤
    ├─ [nudge + live vote] ──► acknowledge_pending ──► (END)    │
    └─ [other] ─────────────────────────────────────────────────┤
                                                                │
                                                                ▼
                                                          draft_response
```

**Sub-graphs invoked inline:**
- `retriever_graph` — searches a FAISS knowledge base using tool-calling
- `feedback_graph` — a ReAct agent with tools: `get_label`, `create_label`, `get_labeler_config`, `update_labeler_config`

**Proposal flow:**
1. `provide_feedback` stages proposed changes in `pending_proposal` (label config) or `pending_classification_rules`
2. After streaming, `agent_runner.py` moves the proposal to `pending_suggestions[message_id]` / `pending_rule_suggestions[message_id]`
3. Users react with 👍🏾 to vote; `voting.py` applies the change when the threshold is met

**One live proposal at a time.** Reactions resolve by `message_id` alone, and every card CLEO ever posted stays in the scroll — so staging a revised proposal marks the earlier ones `superseded` (`voting.superseded_entries`) and appends a note to those messages. Votes on a superseded card are inert and answered with an explanation rather than silence. Relatedly, a summon (🤖 / @-mention) whose message asks for nothing while a vote is open routes to `acknowledge_pending`, which reports what the open vote is waiting on instead of deriving a second near-identical proposal to split it.

**Guided setup:** a new channel is walked through `setup_stage` (`purpose → content → rules → complete`) before the full assistant opens up. The router keeps the agent focused on the current step, and `content` and `rules` advance only once their artifact exists (labels, then classification rules) — see [`brainstorming/nodes.py`](src/agent/brainstorming/nodes.py) (`_advance_setup_stage`).

`purpose` is the exception: it forbids `finalize_proposal`, so it has no artifact to gate on. It ends when the group's answer is captured by the `record_purpose` tool (`community_purpose` in state), with a `PURPOSE_MAX_TURNS` backstop so a group that answers sideways can't get wedged at step one. Groups routinely open by discussing what to flag while CLEO is still establishing purpose; those details are parked via `note_for_later` into `design_notes`, acknowledged in the reply, and replayed at the `content` stage instead of derailing this one or being lost to the feedback agent's trimmed history. They're cleared once the labels they describe are approved.

### Post-setup lifecycle

When setup reaches `complete` and the rules are approved, the design enters a staged lifecycle. Unlike setup (artifact-gated), each advance here is an explicit group vote. The graph owns the conversation and flips `lifecycle_stage`; the heavy, side-effecting work of each stage runs **outside the graph** in [`lifecycle/`](src/agent/lifecycle), orchestrated by `chatbot.py`, so a multi-second network fetch never blocks the async event stream driving the chat. Results are written back into graph state via `update_state` — the same mechanism voting uses.

```
setup: purpose → content → rules → complete
                                       │  rules approved (vote)
                                       ▼
                                    preview  ──vote──►  generate  ──ship gate (vote)──►  deploy  ──►  provision  ──►  live
                                    sample feed        real-post corpus                 materialize    mint prod       serving
                                    vs. rules          + quality report                 bundle +       DID / handle    in prod
                                    (/?preview=id)     (corpus.py, quality.py)          sandbox run    / signing key
                                                                                        (bundle.py,
                                                                                        sandbox.py)
```

- The **irreversible identity step (`provision`) is last**: the labeler is seralized and run end-to-end in the sandbox (`environment='sandbox'`, a `did:web` placeholder + a locally-generated signing key, nothing published) so the group can judge real-post quality *before* any Bluesky identity is bound.
- Runtime identity lives in a `DeploymentRecord` kept **out** of the labeler spec (i.e.,the group's design); the record is the identity it was deployed under.
- `spec.build_spec` folds `labeler_config` + `classification_rules` into one deterministic, content-addressed `labeler.spec.json` (`spec_id`). Every downstream stage reads that spec.
- **Rule matching** is done by the `labeler-engine`. A small Node/TypeScript interpreter that evaluates the spec's rules against posts. It is the single source of truth for match semantics, shared by the quality report and the sandbox executor (the frontend preview evaluates its own copy of the same logic).

> [!NOTE]
> `preview`, `generate`, and `deploy` (materialize + sandbox run) are wired end-to-end today. `provision` (minting a real prod identity) and `live` are designed into the state model but remain a gated stub. A group-owned prod labeler needs an owner for its handle, DID, signing key, domain, and hosting bill first.

### State, channels & checkpoints

Each Stream channel *is* a LangGraph thread, so **one channel designs one labeler** — in a pilot, one channel per group.

`STREAM_CHANNELS` (default `dev2,general`) is the allowlist of joinable channels. A group joins via an invite link carrying its join code — `https://<app>/?c=sourdough-pilot` — and `POST /token` adds them to that channel alone. Codes are matched case-insensitively; an unrecognised one is refused with a 404 rather than auto-created, so a mistyped link can't strand people in an empty room with no lifecycle state. A link with no `?c=` falls back to `STREAM_CHANNEL_ID` (default `general`). Create each group's channel in Stream ahead of the session — the endpoint will create a missing allowlisted channel on first join, but pre-creating lets you seed it and confirm the roster first.

User ids are scoped to the channel (`<channel>-<name>`), so the same display name in two groups stays two distinct users and neither roster leaks into the other. The sidebar lists only channels the user is a member of, which is the whole picker anyone needs.

Channels in `PROTECTED_CHANNELS` (default `dev2`) refuse the in-app "Clear channel" button so the demo can't be wiped. Setting `STREAM_JOIN_ALL_CHANNELS=true` restores the old demo behaviour — one global user per display name, joined to every allowlisted channel — which is only sound when there's a single group.

Per-channel state (setup progress, labeler config, classification rules, lifecycle stage/status, cached preview feed, quality corpus + report, sandbox run, in-flight votes) is checkpointed to `src/data/checkpoints.sqlite`, overridable via `CHECKPOINT_DB_PATH`, gitignored, created on first run so a group's progress survives a restart. Helpers manage it. **Stop the server first**, as the running server holds the DB connection:

- `./scripts/clear_checkpoints.sh` — wipe the checkpointer entirely (all channels reset).
- `python3 scripts/rewind_lifecycle.py <channel_id>` — rewind one channel back to the `preview` stage, undoing the build + sandbox/quality-test steps so they can be re-run.
- `python3 scripts/export_checkpoints.py` / `export_channel.py` — dump checkpoint / channel state for inspection.

## Directory Structure

```
└── 📁bsky-collective-eng-agent
    └── 📁src
        └── 📁agent
            └── 📁brainstorming
                ├── graph.py          # Conversation StateGraph + checkpointer (AsyncSqliteSaver at runtime)
                ├── nodes.py          # Node functions + setup-stage routing
                ├── formatting.py     # Plain-language rendering of labels, rules & config for the chat
                └── voting.py         # Majority vote logic (proposals, rules, lifecycle advances)
            └── 📁feedback
                ├── graph.py          # ReAct agent for label/config/rule feedback
                ├── nodes.py
                ├── state.py
                ├── tools.py          # get_label, create_label, get/update_labeler_config, commit_proposal
                ├── label_policy.py   # Guardrails on proposed label definitions
                └── signal_validation.py  # Validates classification signals (keyword/pattern/account)
            └── 📁retriever
                ├── graph.py          # RAG retrieval graph
                ├── nodes.py
                ├── rag_utils.py
                ├── state.py
                └── tools.py
            └── 📁lifecycle           # Out-of-graph work for the post-setup stages
                ├── orchestration.py  # run_generate_stage / run_deploy_stage / run_execute_stage
                ├── queries.py        # Derives Bluesky search queries from any spec (pure, offline)
                ├── corpus.py         # Fetches + normalizes real posts for those queries (all I/O)
                ├── quality.py        # Runs labeler-engine over the corpus → rule-quality report
                ├── bundle.py         # Materializes a spec into a content-addressed sandbox bundle
                ├── sandbox.py        # Runs a bundle end-to-end under a did:web sandbox identity
                └── preview_posts.py  # AI-generated sample feed for the preview screen
            ├── spec.py               # build_spec — folds config + rules into labeler.spec.json (spec_id)
            ├── maintenance_guide.py  # Plain-language opt-out guide (curated templates)
            ├── prompts.py            # All LLM prompt templates
            └── state.py              # BrainstormingAgentState + lifecycle/spec/deployment TypedDicts
        └── 📁api
            ├── chatbot.py            # FastAPI app — Stream webhook, /token, lifecycle & screen endpoints
            ├── stream.py             # Stream config + client; message send/update & indicator helpers
            ├── agent_runner.py       # Brainstorming-graph driver + per-channel run scheduler
            ├── reactions.py          # Approval-vote tallying and the stage handoffs it triggers
            ├── reporters.py          # Background stage runs that report back into the channel
            ├── messages.py           # Lifecycle chat copy (stage intros, approval prompts, reports)
            ├── helpers.py            # Stream message → LangChain message conversion
            └── model.py              # Pydantic request/response models
        └── 📁data
            └── 📁faiss_index         # Prebuilt FAISS vector index (index.faiss, index.pkl)
            └── 📁static
                └── retrieval-sources.json
        └── config.py                 # Model, embeddings, channel & URL configuration
    └── 📁labeler-engine        # Node/TypeScript rule interpreter (the canonical match semantics)
        ├── spec.ts              # Spec types
        ├── evaluate.ts          # Pure rule evaluation
        ├── execute.ts           # Sandbox executor (signs + emits label records)
        ├── batch.ts             # Batch quality evaluation
        └── index.ts
    └── 📁scripts
        ├── start.sh                     # Start the server (+ optional --docker / --e2e ngrok)
        ├── build_labeler_engine.sh      # Compile labeler-engine/dist (needed for quality + sandbox)
        ├── register_stream_webhook.py   # Register/deregister the Stream webhook
        ├── clear_checkpoints.sh         # Wipe all checkpoint state
        ├── rewind_lifecycle.py          # Rewind one channel to the preview stage
        └── export_checkpoints.py        # Dump checkpoint / channel state
    └── 📁tests               # pytest suite (routing, voting, lifecycle, spec, corpus, quality, sandbox, ...)
    └── 📁frontend            # React + TypeScript chat UI
        └── 📁src
            └── 📁api             # getToken() — calls backend /token
            └── 📁components      # Chat + LabelerPreview + MaintenanceGuide + Onboarding screens
        ├── package.json
        └── vite.config.ts
    ├── Dockerfile            # Multi-stage: builds labeler-engine, then the Python runtime
    ├── render.yaml           # Render Blueprint (backend Docker service + frontend static site)
    ├── langgraph.json
    └── requirements.txt
```

## Backend

### Installation

Clone the repository and create a conda environment:

```sh
conda create -n bsky-coll-eng python=3.12
conda activate bsky-coll-eng
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
# Required — LLM provider (choose one)
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...         # also required for embeddings regardless of MODEL_PROVIDER

# Model provider: anthropic (default), openai, or ollama
MODEL_PROVIDER=anthropic
OLLAMA_MODEL=llama3.2:latest   # only used when MODEL_PROVIDER=ollama

# Required — Stream Chat
STREAM_API_KEY=...
STREAM_API_SECRET=...
STREAM_CHANNELS=dev2,general    # allowlist of joinable channels — one per group; join code = channel id
PROTECTED_CHANNELS=dev2         # channels the in-app "Clear channel" button refuses to wipe
STREAM_CHANNEL_ID=general       # channel used when a link carries no ?c= code (also the /chat default)
STREAM_JOIN_ALL_CHANNELS=false  # true = legacy demo mode: one global user, joined to every channel
AI_USER_ID=ai-assistant
AI_USER_NAME=AI Assistant

# Required for the generate stage — Bluesky corpus fetch.
# One shared, service-level, READ-ONLY app password (NOT a labeler's own identity). A throwaway
# account is fine. Without these, the generate stage can't fetch posts to test the rules against.
BSKY_HANDLE=...                 # bare handle, e.g. cleo-corpus.bsky.social
BSKY_APP_PASSWORD=...           # create under Bluesky Settings → App Passwords

# Optional — where the frontend is served. Builds the preview / maintenance-guide links CLEO posts
# in chat ({FRONTEND_URL}/?preview=<id>, /?guide=<id>). Defaults to the local Vite server.
FRONTEND_URL=http://localhost:5173

# Optional — override checkpoint / bundle storage paths (defaults live under src/data)
CHECKPOINT_DB_PATH=...
LABELER_BUNDLES_DIR=...

# Optional — LangSmith tracing
LANGSMITH_API_KEY=...
```

> [!IMPORTANT]
> `OPENAI_API_KEY` is required for embeddings (`text-embedding-3-large`) regardless of which chat model provider is selected.

#### Model configuration

| Variable | Description | Default |
|---|---|---|
| `MODEL_PROVIDER` | Chat model provider | `anthropic` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2:latest` |

| Provider | Chat model | Fast model (router + welcome messages) |
|---|---|---|
| `anthropic` | `claude-sonnet-4-5-20250929` | `claude-haiku-4-5-20251001` |
| `openai` | `gpt-4.1` | `gpt-4o-mini` |
| `ollama` | `OLLAMA_MODEL` | `OLLAMA_MODEL` |

### Running

Use `scripts/start.sh` to start the server for API and end-to-end development. The `--docker` and `--e2e` flags are composable:

| Command | What it does |
|---|---|
| `./scripts/start.sh` | Start with uvicorn (requires active virtualenv) |
| `./scripts/start.sh --docker` | Build and run via Docker |
| `./scripts/start.sh --e2e` | Also start ngrok and register the Stream webhook |
| `./scripts/start.sh --docker --e2e` | Docker + ngrok + Stream webhook |

`--e2e` requires [ngrok](https://ngrok.com) to be installed and authenticated:

```sh
brew install ngrok
ngrok config add-authtoken <your-ngrok-token>
```

> [!NOTE]
> ngrok free tier URLs change on each restart. Re-run with `--e2e` to re-register the webhook automatically.

The server exposes:

| Method | Path | Description |
|---|---|---|
| `POST` | `/token` | Generate a Stream JWT for a user |
| `POST` | `/new-message` | Stream webhook receiver (drives the agent) |
| `POST` | `/chat` | Direct graph invocation (no Stream, for testing) |
| `GET` | `/labeler-spec/{channel_id}` | Current `labeler.spec.json` for a channel |
| `GET` | `/preview-posts/{channel_id}` | Sample feed for the preview screen |
| `GET` | `/maintenance-guide/{channel_id}` | Rendered maintenance guide |
| `POST` | `/clear-channel/{channel_id}` | Clear a channel's messages + state (blocked for `PROTECTED_CHANNELS`) |

Interactive API docs are available at `http://localhost:8000/docs` once the server is running.

> [!NOTE]
> The `generate` and `deploy` stages shell out to the compiled `labeler-engine`. `scripts/start.sh` builds it automatically for the local (non-Docker) path; the Docker image builds it in a dedicated stage. If quality reports or sandbox runs are unavailable, run `./scripts/build_labeler_engine.sh` (requires Node).

### Tests

```sh
conda activate bsky-coll-eng
python -m pytest -v
```

The suite covers routing, voting, setup-stage routing, the lifecycle stages, spec building, the corpus/quality/sandbox pipeline, label policy, and signal validation. Tests mock LLM and graph calls (no API keys required).

### LangSmith and LangGraph Studio
> [!NOTE]
> You need to create a LangSmith account to obtain eval results and use LangGraph Studio after spinning up the LangGraph server. [FOLLOW THE SETUP INSTRUCTIONS HERE.](https://docs.langchain.com/langsmith/home)
#### LangSmith evaluation

First create the dataset (one-time):

```sh
python tests/dataset.py
```

Then run the evaluation against the actual graph:

```sh
python tests/eval.py
```

Results are logged to your LangSmith project under the `brainstorm-eval` experiment prefix.

#### LangGraph Studio

To inspect and debug the agent graph interactively:
- Make sure the graph isn't compiling with a checkpointer argument in `./src/agent/brainstorming/graph.py`
- Then, run

```sh
langgraph dev
```

> [!NOTE]
> Use `langgraph dev --tunnel` on browsers that block `http` connections (Safari, Brave).
> Use `langgraph dev --allow-blocking` if running blocking I/O in dev mode.

## Frontend

### Installation

```sh
cd frontend
npm install
```

Create `frontend/.env`:

```env
VITE_STREAM_API_KEY=...           # same as backend STREAM_API_KEY
VITE_AI_ASSISTANT_URL=http://localhost:8000
VITE_PROTECTED_CHANNELS=dev2      # channels where the Clear button is hidden (comma-separated)
```

### Running

```sh
npm run dev
```

The frontend is served at `http://localhost:5173`.

## Deployment (Render, for now)

The included [`render.yaml`](render.yaml) Blueprint deploys both pieces to [Render](https://render.com): the FastAPI backend as a Docker web service (the [`Dockerfile`](Dockerfile) is multi-stage and builds the Node labeler-engine, so the generate/deploy stages work in-container) and the Vite frontend as a static site.

1. **New -> Blueprint** in the Render dashboard, pointing at this repo.
2. **Set the secrets** (marked `sync: false`) on both services: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `STREAM_API_KEY`/`STREAM_API_SECRET`, `BSKY_HANDLE`/`BSKY_APP_PASSWORD`, and the frontend's `VITE_STREAM_API_KEY`.
3. **Wire the URLs** after the first deploy: backend `FRONTEND_URL` = the frontend URL; frontend `VITE_AI_ASSISTANT_URL` = the backend URL.
4. **Register the Stream webhook** to `https://<backend-url>/new-message` (Stream dashboard, or `scripts/register_stream_webhook.py`).

State (checkpoints + bundles) persists on a mounted disk (`/data`), which needs a paid instance; for a free deploy, set `plan: free` and remove the `disk:` block — state then resets on each redeploy.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Resources

- [Bluesky Labeler docs](https://docs.bsky.app/docs/advanced-guides/moderation)
- [AT Protocol](https://atproto.com/docs)
- [LangGraph — Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)
- [LangGraph — Choosing Between Graph and Functional APIs](https://docs.langchain.com/oss/python/langgraph/choosing-apis)
- [LangSmith Evaluation guide](https://docs.langchain.com/langsmith/evaluation-quickstart#sdk)
- [Stream Chat webhooks](https://getstream.io/chat/docs/react/webhooks_overview/)
