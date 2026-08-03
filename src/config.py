import os
from pathlib import Path
from dotenv import load_dotenv

from langchain.chat_models import init_chat_model
from langchain_openai import OpenAIEmbeddings
# from langchain_ollama import ChatOllama

load_dotenv()

cwd = Path.cwd()

# Path constants
FAISS_DB_PATH = cwd / "src" / "data" / "faiss_index"
RETRIEVAL_SOURCES_PATH = cwd / "src" / "data" / "static" / "retrieval-sources.json"
# LangGraph SQLite checkpointer — colocated with the FAISS store under src/data (a sibling
# of faiss_index/, not inside it). Gitignored by basename, so per-channel conversation
# state stays out of version control. Overridable via the CHECKPOINT_DB_PATH env var.
CHECKPOINT_DB_PATH = cwd / "src" / "data" / "checkpoints.sqlite"

# Base URL of the frontend, used to build links CLEO posts into chat (e.g. the preview screen
# at {FRONTEND_URL}/?preview=<channel_id>). Defaults to the local Vite dev server; set the
# FRONTEND_URL env var to point at an external host in deployment.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

# Model configuration
MODEL_PROVIDER = os.environ.get('MODEL_PROVIDER', 'anthropic')

# Settings for the feedback agent's tool-calling model (see tool_model below).
#
# Temperature 0, unlike the 0.7 shared by `model`: this agent's job is a decision — call
# finalize_proposal/finalize_rules, or reply in prose — and sampling that decision makes it
# a coin flip. Replaying a real session where CLEO refused to stage rules, the same input
# staged them 1/3 at 0.7 and 3/3 at 0.0. Prose creativity is not worth a tool call the
# group is waiting on; `model` keeps 0.7 for the conversational nodes.
#
# Rules derivation emits the largest output in the app: one finalize_rules call carrying
# every include/exclude signal for every label. A two-label config with ~30 signals
# measures ~1,450 output tokens, so the 1,000 shared by `model` truncates it mid-JSON —
# which surfaces as a finalize_rules call parsed with ZERO rules rather than an error.
# call_model raises on stop_reason == 'max_tokens' so a future overflow is loud.
#
# The 10s shared by `model` is likewise too tight here: httpx applies it per-read, so it
# fires on time-to-first-token, measured at ~9.5s cold for a 3.4k-token rules prompt.
TOOL_MODEL_TEMPERATURE = 0
TOOL_MODEL_MAX_TOKENS = 4000
TOOL_MODEL_TIMEOUT = 60

if MODEL_PROVIDER == 'ollama':
    # model = ChatOllama(
    #     temperature=0.7,
    #     model=os.environ.get('OLLAMA_MODEL', 'llama3.2:latest'),
    #     reasoning=False
    # )
    # fast_model = model
    # tool_model = model
    pass
elif MODEL_PROVIDER == 'openai':
    model = init_chat_model(
        "gpt-4.1",
        temperature=0.7,
        timeout=10,
        max_tokens=1000
    )
    fast_model = init_chat_model(
        "gpt-4o-mini",
        temperature=0.7,
        timeout=10,
        max_tokens=200
    )
    tool_model = init_chat_model(
        "gpt-4.1",
        temperature=TOOL_MODEL_TEMPERATURE,
        timeout=TOOL_MODEL_TIMEOUT,
        max_tokens=TOOL_MODEL_MAX_TOKENS
    )
else:
    model = init_chat_model(
        "claude-sonnet-4-5-20250929",
        temperature=0.7,
        timeout=10,
        max_tokens=1000
    )
    fast_model = init_chat_model(
        "claude-haiku-4-5-20251001",
        temperature=0.7,
        timeout=10,
        max_tokens=200
    )
    tool_model = init_chat_model(
        "claude-sonnet-4-5-20250929",
        temperature=TOOL_MODEL_TEMPERATURE,
        timeout=TOOL_MODEL_TIMEOUT,
        max_tokens=TOOL_MODEL_MAX_TOKENS
    )

embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
