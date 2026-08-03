"""
The labeler lifecycle: the stages a group's spec moves through after the design is approved.

    preview  ->  generate  ->  deploy  ->  provision  ->  (live)
                    |             |            |
                    |             |            +-- provision.py  collect the group's governance
                    |             |                              answers (no side effects)
                    |             +-- bundle.py    materialize labeler.spec.json + engine bundle
                    |             +-- sandbox.py   run the interpreter over the corpus
                    |
                    +-- queries.py   derive search queries from the spec (pure)
                    +-- corpus.py    fetch real posts for those queries (I/O)
                    +-- quality.py   score the rules against the corpus

`provision` is currently the terminal stage: it records who the group named and which handle they
chose, and stops. Reaching `live` needs account creation, which needs email addresses this system
does not collect — see provision.py's scope note.

`orchestration.py` drives those pieces; the stage entrypoints are re-exported here so callers keep
writing `from src.agent.lifecycle import run_generate_stage`.

Note that `spec.py` and `state.py` deliberately stay in `src/agent/` — they are shared
foundations (voting stamps spec_id, feedback reads state), not lifecycle-only machinery.
"""

from src.agent.lifecycle.orchestration import (
    capture_governance,
    run_deploy_stage,
    run_execute_stage,
    run_generate_stage,
    run_provision_stage,
    stand_down_provision,
)

__all__ = [
    "capture_governance",
    "run_deploy_stage",
    "run_execute_stage",
    "run_generate_stage",
    "run_provision_stage",
    "stand_down_provision",
]
