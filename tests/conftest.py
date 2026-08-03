"""Shared pytest configuration.

Force the in-memory checkpointer for the entire test session. This module is imported
by pytest before any test module (and therefore before src.agent.brainstorming.graph is
imported), so the graph compiles with MemorySaver instead of writing a checkpoints.sqlite
file to the repo and leaking state across test runs.
"""

import os

os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
