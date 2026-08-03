"""
Out-of-graph orchestration for the post-preview lifecycle stages.

The graph owns the conversation and flips `lifecycle_stage`; the heavy, side-effecting work of
each stage (sourcing a corpus, materializing a bundle, deploying to the sandbox, provisioning)
runs here and writes results back into graph state via `update_state` — the same mechanism voting
uses. Keeping it out of the graph means a multi-second network fetch never blocks the async event
stream that drives the conversation.

Today this covers two stages:
  * `generate` (run_generate_stage): fold the approved design into a spec, fetch a real-post corpus
    for it (spec-agnostic; see src/agent/lifecycle/corpus.py), cache it under its domain fingerprint so later
    rule edits replay the same posts, and run the interpreter over it into a quality report.
  * `deploy` has two steps, both run once the group approves the ship gate:
      - run_deploy_stage: materialize the spec into an on-disk sandbox bundle (see src/agent/lifecycle/bundle.py).
      - run_execute_stage: run that bundle end-to-end in the sandbox (see src/agent/lifecycle/sandbox.py) under a
        did:web placeholder identity, emitting signed label records locally. Pass/fail machine gate.
  * `provision` (run_provision_stage): derive handle candidates and open the governance
    conversation. Unlike the stages above this one has NO external side effects — it collects the
    group's decisions (see src/agent/lifecycle/provision.py) and nothing is minted or published.
"""

import asyncio
import logging
from datetime import datetime, timezone

from src.agent.brainstorming.graph import graph
from src.agent.lifecycle.bundle import materialize_bundle
from src.agent.lifecycle.corpus import corpus_key, fetch_corpus
from src.agent.lifecycle.provision import (
    extract_governance,
    handle_candidates,
    is_complete,
    outstanding_keys,
)
from src.agent.lifecycle.quality import build_quality_report
from src.agent.lifecycle.sandbox import run_executor
from src.agent.spec import build_spec

logger = logging.getLogger(__name__)


async def run_generate_stage(channel_id: str) -> dict:
    """Source (or reuse) the rule-quality corpus for a channel that has entered `generate`.

    Returns a small result dict: {"status": "succeeded"|"failed"|"skipped", "corpus_key", "num_posts"}.
    Idempotent: if a cached corpus already matches the current domain fingerprint it is reused
    rather than refetched, so re-entering generate after a rule tweak that doesn't change the query
    basis costs nothing. Never raises for expected failures (missing creds, empty fetch) — it marks
    lifecycle_status='failed' with a human-readable lifecycle_error so the caller can surface it.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "generate":
        logger.info("run_generate_stage skipped: channel %s not in generate", channel_id)
        return {"status": "skipped", "corpus_key": None, "num_posts": 0}

    spec = build_spec(values.get("labeler_config"), values.get("classification_rules"))
    key = corpus_key(spec)

    cached = values.get("quality_corpus")
    if cached and cached.get("corpus_key") == key and cached.get("posts"):
        posts = cached["posts"]
        logger.info("Reusing cached corpus %s (%d posts) for %s", key, len(posts), channel_id)
    else:
        await asyncio.to_thread(graph.update_state, config, {"lifecycle_status": "in_progress"})
        # fetch_corpus is blocking httpx I/O (auth + up to ~32 searchPosts calls); keep it off the loop.
        posts = await asyncio.to_thread(fetch_corpus, spec)
        if not posts:
            await asyncio.to_thread(
                graph.update_state, config,
                {
                    "lifecycle_status": "failed",
                    "lifecycle_error": (
                        "Couldn't fetch any posts to test the rules against — check the BSKY_HANDLE / "
                        "BSKY_APP_PASSWORD credentials and network access."
                    ),
                },
            )
            logger.warning("Corpus fetch returned no posts for %s (%s)", channel_id, key)
            return {"status": "failed", "corpus_key": key, "num_posts": 0, "report": None}
        await asyncio.to_thread(
            graph.update_state, config,
            {"quality_corpus": {
                "corpus_key": key,
                "posts": posts,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        logger.info("Sourced corpus %s (%d posts) for %s", key, len(posts), channel_id)

    # Run the canonical interpreter (labeler-engine) over the corpus and aggregate a quality report.
    # build_quality_report shells out to node, so keep it off the event loop too.
    try:
        report = await asyncio.to_thread(build_quality_report, spec, posts)
    except Exception:
        logger.exception("Quality evaluation failed for %s", channel_id)
        await asyncio.to_thread(
            graph.update_state, config,
            {
                "lifecycle_status": "failed",
                "lifecycle_error": (
                    "Fetched the posts but couldn't run the rules against them — the labeler-engine "
                    "build may be missing or node is unavailable."
                ),
            },
        )
        return {"status": "failed", "corpus_key": key, "num_posts": len(posts), "report": None}

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "quality_report": {**report, "generated_at": datetime.now(timezone.utc).isoformat()},
            "lifecycle_status": "succeeded",
            "lifecycle_error": None,
        },
    )
    logger.info("Quality report ready for %s: %d/%d posts matched", channel_id, report["matched_any"], report["total"])
    return {"status": "succeeded", "corpus_key": key, "num_posts": len(posts), "report": report}


async def run_deploy_stage(channel_id: str) -> dict:
    """Materialize the approved spec into an on-disk bundle for a channel that has entered `deploy`.

    The `deploy` stage's first realized step: fold state -> spec -> a content-addressed bundle
    (labeler.spec.json + manifest) via src/agent/lifecycle/bundle.py. The sandbox EXECUTOR that actually runs
    that bundle over the corpus (did:web + local key) is the next piece; until it lands this records
    the materialized bundle as the sandbox deployment artifact so drift detection and the executor
    have a stable handle. Idempotent: an unchanged spec targets the same bundle directory.

    Never raises for expected failures — marks lifecycle_status='failed' with a human-readable
    lifecycle_error. Returns {"status", "spec_id", "bundle_dir", "labels", "rules"}.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "deploy":
        logger.info("run_deploy_stage skipped: channel %s not in deploy", channel_id)
        return {"status": "skipped", "spec_id": None, "bundle_dir": None}

    await asyncio.to_thread(graph.update_state, config, {"lifecycle_status": "in_progress"})

    spec = build_spec(values.get("labeler_config"), values.get("classification_rules"))
    try:
        handle = await asyncio.to_thread(materialize_bundle, spec, channel_id)
    except Exception:
        logger.exception("Bundle materialization failed for %s", channel_id)
        await asyncio.to_thread(
            graph.update_state, config,
            {
                "lifecycle_status": "failed",
                "lifecycle_error": (
                    "Couldn't assemble the labeler bundle — a filesystem or spec error on my side, "
                    "not a problem with your labeler."
                ),
            },
        )
        return {"status": "failed", "spec_id": spec["spec_id"], "bundle_dir": None}

    labels = spec.get("labels") or []
    rule_count = sum(1 for l in labels if l.get("rule"))
    deployment = {
        **(values.get("deployment") or {}),
        "environment": "sandbox",
        "deployed_spec_id": spec["spec_id"],
        "bundle_dir": handle["bundle_dir"],
        "deployed_at": handle["created_at"],
    }
    await asyncio.to_thread(
        graph.update_state, config,
        {"deployment": deployment, "lifecycle_status": "succeeded", "lifecycle_error": None},
    )
    logger.info("Bundle materialized for %s at %s (spec %s)", channel_id, handle["bundle_dir"], spec["spec_id"])
    return {
        "status": "succeeded",
        "spec_id": spec["spec_id"],
        "bundle_dir": handle["bundle_dir"],
        "labels": len(labels),
        "rules": rule_count,
    }


async def run_execute_stage(channel_id: str) -> dict:
    """Run the materialized bundle in the sandbox for a channel in `deploy` (after materialization).

    The pass/fail machine gate: shells to the labeler-engine executor (see src/agent/lifecycle/sandbox.py),
    which runs the bundle end-to-end under a persistent per-channel did:web sandbox identity, emits a
    signed label record per fired (post, label) to a local store, and returns a summary. Records the
    sandbox DID onto the DeploymentRecord and the run summary under `sandbox_run` (for the chat report
    and the future sandbox screen). Nothing is published; the identity is a placeholder, not served.

    Requires run_deploy_stage to have materialized the bundle first (needs deployment.bundle_dir).
    Never raises for expected failures — marks lifecycle_status='failed' with a human-readable error.
    Returns {"status", "did", "total", "records_emitted", "per_label", "examples"} (or a skipped stub).
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "deploy":
        logger.info("run_execute_stage skipped: channel %s not in deploy", channel_id)
        return {"status": "skipped", "did": None, "records_emitted": 0}

    deployment = values.get("deployment") or {}
    bundle_dir = deployment.get("bundle_dir")
    if not bundle_dir:
        logger.warning("run_execute_stage skipped: no materialized bundle for %s", channel_id)
        return {"status": "skipped", "did": None, "records_emitted": 0}

    posts = (values.get("quality_corpus") or {}).get("posts") or []

    await asyncio.to_thread(graph.update_state, config, {"lifecycle_status": "in_progress"})
    try:
        summary = await asyncio.to_thread(run_executor, bundle_dir, posts)
    except Exception:
        logger.exception("Sandbox execution failed for %s", channel_id)
        await asyncio.to_thread(
            graph.update_state, config,
            {
                "lifecycle_status": "failed",
                "lifecycle_error": (
                    "Your labeler didn't start cleanly in the sandbox — a build or runtime error on "
                    "my side, not a problem with your rules."
                ),
            },
        )
        return {"status": "failed", "did": None, "records_emitted": 0}

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "deployment": {**deployment, "environment": "sandbox", "labeler_did": summary.get("did")},
            "sandbox_run": {**summary, "generated_at": datetime.now(timezone.utc).isoformat()},
            "lifecycle_status": "succeeded",
            "lifecycle_error": None,
        },
    )
    logger.info(
        "Sandbox run for %s: %d records under %s", channel_id,
        summary.get("records_emitted", 0), summary.get("did"),
    )
    return {"status": "succeeded", **summary}


async def run_provision_stage(channel_id: str) -> dict:
    """Open the governance conversation for a channel that has entered `provision`.

    The lightest stage runner: derives handle candidates from the spec (deterministic, offline —
    availability is deliberately not checked, see provision.handle_candidates) and records them so a
    later turn can resolve "the second one". Nothing external happens here; the actual work of the
    stage is the group answering three questions in chat.

    Idempotent: re-entering provision with the same spec recomputes the same candidates. Returns
    {"status", "candidates", "outstanding", "governance"}.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "provision":
        logger.info("run_provision_stage skipped: channel %s not in provision", channel_id)
        return {"status": "skipped", "candidates": [], "outstanding": [], "governance": {}}

    spec = build_spec(values.get("labeler_config"), values.get("classification_rules"))
    candidates = handle_candidates(spec)
    governance = values.get("governance") or {}

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "provision_handle_candidates": candidates,
            "lifecycle_status": "in_progress",
            "lifecycle_error": None,
        },
    )
    logger.info("Provision opened for %s with candidates %s", channel_id, candidates)
    return {
        "status": "succeeded",
        "candidates": candidates,
        "outstanding": outstanding_keys(governance),
        "governance": governance,
    }


async def capture_governance(channel_id: str, messages: list | None = None) -> dict:
    """Extract any governance answers the group just gave, and stage them for approval.

    Runs on each new message while the channel is in `provision`. Returns
    {"status": "staged"|"none"|"stand_down"|"complete"|"skipped", "proposal", "outstanding_after"};
    "none" (the group was talking about something else) is the common case and not a failure.
    Staging only — the answers do not reach `governance` until the group approves the confirm card
    (see voting.process_governance_approval).

    Two early exits keep this from running forever. `complete` short-circuits BEFORE the model call
    once all three answers are in: without it a settled channel pays for one extraction per message
    indefinitely, invisibly, because the capture is silent when it hears nothing. `stand_down` is
    the group asking to stop, which the go-live message promises they can do at any point.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "provision":
        return {"status": "skipped", "proposal": {}, "outstanding_after": []}

    governance = values.get("governance") or {}
    if is_complete(governance):
        return {"status": "complete", "proposal": {}, "outstanding_after": []}

    convo = messages if messages is not None else list(values.get("messages") or [])
    extraction = await asyncio.to_thread(
        extract_governance, convo, governance, values.get("provision_handle_candidates") or []
    )
    if extraction["stand_down"]:
        logger.info("Group asked to stand down from provision in %s", channel_id)
        return {
            "status": "stand_down",
            "proposal": {},
            "outstanding_after": outstanding_keys(governance),
        }

    # Drop answers that merely restate what's already recorded, so an unchanged re-statement
    # doesn't raise a confirm card the group has to vote on again.
    proposal = {k: v for k, v in extraction["proposal"].items() if governance.get(k) != v}
    if not proposal:
        return {"status": "none", "proposal": {}, "outstanding_after": outstanding_keys(governance)}

    logger.info("Staged governance answers for %s: %s", channel_id, sorted(proposal))
    return {
        "status": "staged",
        "proposal": proposal,
        "outstanding_after": outstanding_keys({**governance, **proposal}),
    }


async def stand_down_provision(channel_id: str) -> dict:
    """Return a channel from `provision` to `deploy` — the group stopping the going-live questions.

    The back edge the go-live message promises ("you can stop at any point"). Whatever the group
    already approved STAYS in `governance`, so the maintenance guide keeps showing how far they got
    and resuming later costs nothing. Clears `pending_provision_approval` so the caller can post a
    fresh anchor: the old one is marked committed and would never fire again.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    values = state.values or {}

    if values.get("lifecycle_stage") != "provision":
        return {"status": "skipped", "governance": values.get("governance") or {}}

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "lifecycle_stage": "deploy",
            "lifecycle_status": "succeeded",
            "lifecycle_error": None,
            "pending_provision_approval": None,
        },
    )
    logger.info("Provision stood down for %s — back at deploy", channel_id)
    return {"status": "succeeded", "governance": values.get("governance") or {}}
