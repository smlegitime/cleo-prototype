import logging
from typing import Literal
from langgraph.graph import END
from langgraph.types import interrupt, Command
from langchain_core.messages import HumanMessage, SystemMessage
from src.config import model as llm, fast_model

logger = logging.getLogger(__name__)

# What CLEO says when it's summoned but the group already has something waiting on its own vote.
# Kept here rather than in api/messages.py: the api layer depends on the agent, not the reverse.
PENDING_APPROVAL_MSG = (
    "📌 There's a {what} above waiting on the group — {tally}.\n\n{action}"
)

# The same fact as a footer under a reply that was about something else. The feedback agent is
# given the live tally in its prompt and does not reliably repeat it (measured: it named the stage
# and the next step but dropped the tally in both samples), and its reply is passed through
# verbatim — so on that path the vote is stated deterministically rather than left to the model.
PENDING_VOTE_FOOTER = "📌 Still waiting on the group: the {what} above — {tally}."

# Defensive only: acknowledge_pending is routed to on the strength of a live anchor, so this
# should be unreachable. An explicit summon still has to say something.
NOTHING_PENDING_MSG = "Nothing is waiting on a vote right now — what would you like to work on?"

from src.agent.state import (
    BrainstormingAgentState,
    CommunityGuidelinesValidation,
    MessageClassification,
    ValidationAndClassification,
)

from src.agent.prompts import (
    ROUTER_PROMPT,
    VALIDATION_AND_CLASSIFICATION_PROMPT,
    DRAFT_RESPONSE_PROMPT,
    RULES_DERIVATION_PROMPT,
    CLEO_WORKFLOW,
    LABEL_BEHAVIOR_TABLE,
    LABELER_CAPABILITIES,
)
from src.agent.feedback.signal_validation import sanitize_rules
from src.agent.feedback.label_policy import pin_default_setting

from src.agent.feedback.graph import feedback_graph
from src.agent.retriever.graph import retriever_graph


from src.agent.brainstorming.formatting import (
    format_labeler_context,
    format_rules_context,
    format_rules_block,
    format_config_block,
    format_proposal_block
)



# Every anchor the group can be waiting on, newest stage first — the vote blocking progress NOW is
# the one worth reporting, and a card from an earlier stage is already inert at its own gate.
#
# Two state shapes, deliberately not unified: the *_suggestions stores are dicts keyed by Stream
# message_id (several can exist, superseding each other), while each lifecycle gate is a single
# record that is overwritten in place. 'many' scans newest-first; 'one' is the record itself.
_ANCHOR_SOURCES = (
    ("governance", "pending_governance_suggestions", "many"),
    ("guide", "pending_guide_choice", "one"),
    ("provision", "pending_provision_approval", "one"),
    ("deploy", "pending_deploy_approval", "one"),
    ("preview", "pending_preview_approval", "one"),
    ("rules", "pending_rule_suggestions", "many"),
    ("proposal", "pending_suggestions", "many"),
)

# What each anchor IS, in the group's words. Read straight into "There's a {what} above waiting…".
_ANCHOR_WHAT = {
    "proposal": "labeler proposal",
    "rules": "set of classification rules",
    "preview": "sign-off on the preview",
    "deploy": "go-ahead to build and test your labeler",
    "provision": "decision about going live",
    "guide": "choice to stay in the sandbox with the guide",
    "governance": "set of going-live answers to confirm",
    "fork": "choice between the maintenance guide and going live",
}

# How to act on it. The fork is the only one that isn't a single card to react to.
_ANCHOR_ACTION = {
    "fork": "React 👍🏾 on whichever of the two the group wants, or ask me anything about either.",
}
_DEFAULT_ANCHOR_ACTION = "React 👍🏾 on it to approve, or tell me what to change and I'll revise it."


def _approval_rule(state: BrainstormingAgentState) -> str:
    """Name the rule the threshold came from, not just the number it produced.

    The number can't stand in for the rule: 2 approvals is a majority in a group of three and
    unanimity in a group of two, and a group deciding whether to wait for someone needs to know
    which one it is. Falls back to the majority wording when the roster size is missing (a
    checkpoint written before voting_member_count existed) — the count is only ever absent on
    channels big enough for majority to be the right description anyway, since below that the
    group is being told to wait for everyone by the tally itself.
    """
    # Imported here, not at module scope: voting.py imports this module, so the constant can only
    # travel in this direction lazily. Worth it to keep one definition of where majority starts.
    from src.agent.brainstorming.voting import MAJORITY_THRESHOLD

    needed = state.get("approvals_needed") or 1
    voting = state.get("voting_member_count")

    if voting is not None and voting <= MAJORITY_THRESHOLD:
        return "a single 👍🏾 carries it" if voting <= 1 else "everyone voting has to 👍🏾"
    if needed == 1:
        return "a single 👍🏾 carries it"
    return "a majority of the members"


def _live_entry(value, shape: str) -> dict | None:
    """The live record in one anchor store, or None. Committed and superseded are both dead."""
    if not value:
        return None
    if shape == "one":
        return None if value.get("committed") or value.get("superseded") else value
    for suggestion in reversed(list(value.values())):
        if not suggestion.get("committed") and not suggestion.get("superseded"):
            return suggestion
    return None


def _live_pending_anchor(state: BrainstormingAgentState) -> tuple[str, dict] | None:
    """The card still open for a vote, or None if nothing is waiting on the group.

    Covers the setup proposals AND the lifecycle gates. It used to scan only the two setup stores,
    so a group parked on the preview, ship, go-live or governance vote was told nothing was pending
    — the one moment the answer matters most.

    Staging a new proposal marks the earlier ones superseded, so at most one should be live; the
    'many' scan runs newest-first anyway so checkpoints written before superseding existed still
    resolve to the latest card rather than the oldest.
    """
    # The fork posted after the sandbox run is two cards and ONE decision. Reporting either half
    # alone would quote a count for a vote the group isn't having.
    guide = _live_entry(state.get("pending_guide_choice"), "one")
    going_live = _live_entry(state.get("pending_provision_approval"), "one")
    if guide and going_live:
        return "fork", guide

    for kind, key, shape in _ANCHOR_SOURCES:
        entry = _live_entry(state.get(key), shape)
        if entry:
            return kind, entry
    return None


def _anchor_what_and_tally(state: BrainstormingAgentState, anchor: tuple[str, dict]) -> tuple[str, str]:
    """Name the card waiting on a vote and where its count stands.

    Shared by everything that tells the group about it — acknowledge_pending's whole reply, the
    footer under a feedback reply, and the stage context handed to the drafting prompt — so they
    can't drift into quoting different numbers.
    """
    kind, suggestion = anchor
    needed = state.get("approvals_needed") or 1
    what = _ANCHOR_WHAT.get(kind, "decision")

    if kind == "fork":
        # Two independent counts; a combined one would describe neither card.
        guide = len(((state.get("pending_guide_choice") or {}).get("approved_by")) or [])
        going_live = len(((state.get("pending_provision_approval") or {}).get("approved_by")) or [])
        if guide == 0 and going_live == 0:
            return what, f"no votes either way yet — whichever gets {needed} first decides"
        return what, f"the guide has {guide} of {needed}, going live has {going_live} of {needed}"

    approved = len(suggestion.get("approved_by") or [])
    tally = (
        f"no approvals yet — it needs {needed}"
        if approved == 0
        else f"{approved} of {needed} approvals so far"
    )
    return what, tally


def acknowledge_pending(state: BrainstormingAgentState) -> dict:
    """Point at the proposal already awaiting a vote instead of drafting a new one.

    Reached when CLEO is summoned by a message that asks for nothing — a ping, not a revision.
    Re-deriving the proposal there costs the group a second near-identical card to read and
    splits the vote across two of them, so the useful answer is what the existing one is waiting on.
    """
    anchor = _live_pending_anchor(state)
    if anchor is None:
        # Routing shouldn't send us here without one, but an explicit summon must never go silent.
        return {"draft_response": NOTHING_PENDING_MSG}

    what, tally = _anchor_what_and_tally(state, anchor)
    action = _ANCHOR_ACTION.get(anchor[0], _DEFAULT_ANCHOR_ACTION)
    logger.info("acknowledge_pending: %s anchor, %s", anchor[0], tally)

    return {
        "draft_response": PENDING_APPROVAL_MSG.format(what=what, tally=tally, action=action)
    }


# What each stage is waiting on before the group can move to the next one. Keyed by setup_stage
# first, then lifecycle_stage — setup runs to 'complete' before the lifecycle opens at 'preview'.
_STAGE_NEXT_STEP = {
    "purpose": "The group tells CLEO what community this is for, who the labels are for, and what "
               "they want the labeler to do. Then they approve the labeler's name and description.",
    "content": "The group settles which categories get flagged and how each label behaves, then "
               "approves the proposed set of labels.",
    "rules": "CLEO drafts the classification rules for each label; the group adjusts them and "
             "approves them. That completes setup and opens the preview.",
    "preview": "The group opens the preview screen, checks how the labels behave on sample posts, "
               "and approves the preview.",
    "generate": "CLEO tests the labeler on real posts and reports back; the group approves "
                "shipping it.",
    "deploy": "CLEO builds and runs the labeler in a private sandbox; the group approves going "
              "live. Nothing is published and no Bluesky account exists yet.",
    "provision": "The group settles the handle, who holds the account, and where appeals go, then "
                 "confirms those answers.",
}


def _stage_context(state: BrainstormingAgentState) -> str:
    """Where this channel actually is, for the drafting prompt.

    Without it draft_response answers "what do we do next?" from the config alone — it cannot see
    the stage, the vote threshold, or whether a card is already waiting, so it guesses. Every value
    here is read from state rather than inferred, and the prompt is told this section outranks the
    general stage list.
    """
    setup_stage = state.get("setup_stage")
    lifecycle_stage = state.get("lifecycle_stage")
    stage = setup_stage if setup_stage and setup_stage != "complete" else lifecycle_stage

    lines = []
    if setup_stage:
        lines.append(
            f"Setup stage: {setup_stage}"
            + (" (setup is finished)" if setup_stage == "complete" else "")
        )
    if lifecycle_stage:
        lines.append(f"Lifecycle stage: {lifecycle_stage}")
    if not lines:
        return ""

    if stage and stage in _STAGE_NEXT_STEP:
        lines.append(f"What moves them to the next stage: {_STAGE_NEXT_STEP[stage]}")
    elif setup_stage == "complete" and not lifecycle_stage:
        lines.append(
            "Setup is complete and the lifecycle hasn't opened yet — the group can keep editing "
            "labels and rules, and each edit goes to a vote."
        )

    # At provision the group is mid-way through three questions, and "what's left?" is the most
    # likely thing they'll ask. Answering it from the record beats answering it from the transcript.
    if lifecycle_stage == "provision":
        governance = state.get("governance") or {}
        settled = ", ".join(f"{k} = {v}" for k, v in governance.items() if v) or "nothing yet"
        outstanding = [k for k in ("handle", "custodian", "appeals") if not governance.get(k)]
        lines.append(f"Going-live answers settled so far: {settled}.")
        lines.append(
            "Still to settle: " + (", ".join(outstanding) if outstanding else "nothing — all three are in.")
        )
        lines.append(
            "The group answers these in chat among themselves; CLEO writes down what it hears when "
            "someone asks it to, and posts a confirm card for the group to approve."
        )

    needed = state.get("approvals_needed") or 1
    lines.append(f"Approvals a card needs in this channel: {needed} ({_approval_rule(state)}).")

    anchor = _live_pending_anchor(state)
    if anchor is None:
        lines.append("Nothing is waiting on a vote right now.")
    else:
        # Same helper the group-facing messages use, so the prompt can't describe the vote one way
        # while the footer under the very same reply describes it another.
        what, tally = _anchor_what_and_tally(state, anchor)
        action = _ANCHOR_ACTION.get(anchor[0], _DEFAULT_ANCHOR_ACTION)
        lines.append(f"Waiting on a vote: {what} — {tally}.")
        lines.append(f"What they do about it: {action}")

    return "\n".join(lines)


def _last_user_query(messages: list) -> str:
    """Return the content of the most recent human message."""
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human":
            return m.content
    return ""


def _advance_setup_stage(setup_stage: str | None, config: dict, classification_rules: dict) -> str | None:
    """Move to the next guided setup stage once the current stage's config is complete."""
    if setup_stage == 'purpose' and config.get('display_name') and config.get('description'):
        return 'content'
    if setup_stage == 'content' and config.get('labels'):
        return 'rules'
    if setup_stage == 'rules' and classification_rules:
        return 'complete'
    return setup_stage


def router(state: BrainstormingAgentState) -> Command[Literal["validate_and_classify", "__end__"]]:
    """Decide whether the AI should respond at all based on conversation context.

    In a group channel an explicit trigger is the ONLY thing that makes CLEO speak. Unambiguous
    addressing — @-mention, bare name at message start, 🤖 reaction — is detected deterministically
    before the graph runs and arrives as force_respond; everything else is the group talking among
    themselves, and CLEO stays out of it until pulled in.

    This gate used to lift at setup_stage == 'complete', on the assumption that 'complete' ended the
    design conversation. It doesn't: lifecycle_stage takes over there (preview -> generate -> deploy
    -> provision) and the group keeps deliberating exactly as before, so the LLM router was left
    guessing at addressing for the entire post-setup life of a channel and interrupting them.

    Silence can't strand anyone. Every gate from purpose through provision advances on a 👍🏾 on the
    stage's anchor card, handled in the reaction webhook outside this graph, and mid-provision
    governance answers are captured on their own path alongside the agent. A summon with nothing to
    say lands on acknowledge_pending, which points at whatever anchor is live.

    The LLM router below is reachable only with no setup_stage at all — the direct /chat endpoint,
    which has no group conversation to interrupt.
    """
    logger.info("Current setup_stage: %s", state.get("setup_stage"))

    if state.get("force_respond"):
        logger.info("Router decision: RESPOND (deterministic pre-check)")
        return Command(goto="validate_and_classify")

    if state.get("setup_stage"):
        logger.info("Router decision: SKIP (awaiting explicit trigger)")
        return Command(goto=END)

    formatted = "\n".join(
        f"{'AI' if m.type == 'ai' else 'User'}: {m.content}"
        for m in state["messages"][-6:]
    )
    prompt = ROUTER_PROMPT.format(messages=formatted)
    response = fast_model.invoke(prompt)
    should_respond = response.content.strip().upper().startswith("YES")
    logger.info("Router decision: %s", "RESPOND" if should_respond else "SKIP")
    return Command(goto="validate_and_classify" if should_respond else END)


_validate_and_classify_llm = llm.with_structured_output(ValidationAndClassification)


def validate_and_classify(state: BrainstormingAgentState) -> Command[Literal["search_documentation", "summarize_conversation", "provide_feedback", "acknowledge_pending", "draft_response"]]:
    """Validate against community guidelines and classify intent in a single LLM call."""
    prompt = VALIDATION_AND_CLASSIFICATION_PROMPT.format(query=state['messages'][-1].content)
    result = _validate_and_classify_llm.invoke(state['messages'][:-1] + [HumanMessage(content=prompt)])

    validation: CommunityGuidelinesValidation = {"message": result["message"], "violation": result["violation"]}
    classification: MessageClassification = {"intent": result["intent"], "atproto": result["atproto"], "topic": result["topic"]}

    setup_stage = state.get('setup_stage')
    in_setup = setup_stage and setup_stage != 'complete'

    if result["violation"]:
        goto = "draft_response"
    elif result["intent"] == "question" and result["atproto"] in ["bluesky", "atproto", "labeler", "label"]:
        goto = "search_documentation"
    elif result["intent"] == "summary":
        goto = "summarize_conversation"
    # A summon that asks for nothing, while a proposal is already waiting on the group's vote:
    # point at it rather than re-deriving one. Sits below the question/summary branches so a real
    # request still gets a real answer, and only fires on a live anchor — with nothing pending,
    # a nudge falls through to the normal path.
    elif result["intent"] == "nudge" and _live_pending_anchor(state):
        goto = "acknowledge_pending"
    # Provision is a governance conversation, not a design one. The feedback agent is in reactive
    # mode by this point ("call finalize_proposal as soon as the request is clear"), so left in the
    # path it reads "the custodian should be Maya" as a labeler change and stages a proposal the
    # group never asked for. draft_response answers instead — it has the stage, the three questions
    # and the answers so far in its context, and stages nothing.
    elif state.get('lifecycle_stage') == 'provision':
        goto = "draft_response"
    elif in_setup:
        goto = "provide_feedback"
    elif result["intent"] == "show_config":
        goto = "draft_response"
    elif result["intent"] == "feedback":
        goto = "provide_feedback"
    elif result["intent"] == "generate_code":
        goto = "draft_response" # stub for now until code generation subgraph is built
    else:
        goto = "draft_response"

    return Command(
        # feedback_response is cleared here, at the top of every responding turn, so its presence
        # downstream means "provide_feedback ran on THIS turn" — that's what draft_response keys
        # its passthrough on. Left uncleared, a stale reply from an earlier turn would resurface.
        update={
            "validation": validation,
            "classification": classification,
            "feedback_response": None,
        },
        goto=goto,
    )


def search_documentation(state: BrainstormingAgentState) -> Command[Literal['draft_response']]:
    """Search knowledge base for relevant information"""

    try:
        result = retriever_graph.invoke({"messages": state['messages']})
        search_results = result.get('context', [])
    except Exception as e:
        search_results = [f"Search temporarily unavailable: {str(e)}"]

    return Command(
        update={"search_results": search_results},
        goto="draft_response"
    )


def _is_human(message) -> bool:
    return isinstance(message, HumanMessage) or getattr(message, 'type', None) == 'human'


def _unconsumed_human_messages(messages: list, prior_feedback: list) -> list:
    """Every human message the feedback agent has not seen yet, oldest first.

    The router stays silent during setup unless explicitly triggered, so a group can
    deliberate for several messages before CLEO is pulled in. Passing only the newest one
    silently drops the rest — that is how a group's actual slur and fake-cure lists never
    reached the rules derivation, leaving CLEO to answer about hashtags alone.

    The boundary is the last human message already in prior_feedback: everything after it
    is new. Membership can't be used instead, because prior_feedback is trimmed to
    FEEDBACK_CONTEXT_WINDOW and trimmed-away messages would look new again forever.
    """
    last_seen_id = next(
        (m.id for m in reversed(prior_feedback) if _is_human(m) and getattr(m, 'id', None)),
        None,
    )
    boundary = -1
    if last_seen_id is not None:
        boundary = next(
            (i for i, m in enumerate(messages) if getattr(m, 'id', None) == last_seen_id),
            -1,
        )
        # Boundary scrolled out of the fetched window — fall back to the newest message
        # only, rather than replaying the whole window as if it were new.
        if boundary == -1:
            return [m for m in messages if _is_human(m)][-1:]

    return [m for m in messages[boundary + 1:] if _is_human(m)]


def _tool_call_args(messages: list, name: str) -> list[dict]:
    """Every call to `name` in a feedback-graph run, in order, as its raw args."""
    return [
        tool_call.get('args', {})
        for msg in messages
        for tool_call in (getattr(msg, 'tool_calls', None) or [])
        if tool_call.get('name') == name
    ]


# Purpose-stage escape hatch: after this many CLEO turns the stage advances even without a recorded
# purpose. A group that answers sideways, or an agent that keeps failing to ask, must not be able to
# wedge setup at step one — the parked details are enough to keep going with.
PURPOSE_MAX_TURNS = 3

# The purpose stage's one question, used when the model spends its turn on tool calls and returns
# no text of its own.
PURPOSE_QUESTION = (
    "Before we get into what to flag: in a sentence or two, who is this community, what do you "
    "want the labeler to do for it, and who are the labels for — your own members, newcomers, or "
    "people outside the group?"
)


def _purpose_fallback_reply(noted: list[str]) -> str:
    """What CLEO says at the purpose stage when the feedback agent produced no text.

    Every other stage has something to fall back on — a proposal or rules block gets appended to
    an empty response — so an empty purpose turn is the one case that reaches the channel as
    silence, on a message the group explicitly addressed to CLEO.
    """
    if not noted:
        return PURPOSE_QUESTION
    heard = "; ".join(noted[:3])
    return f"Noted — {heard}. We'll shape those shortly.\n\n{PURPOSE_QUESTION}"


def _purpose_stage_after(prior_feedback: list, purpose_recorded: bool) -> str:
    """The setup_stage to leave the group in after a 'purpose' turn.

    Advances on the purpose actually being captured, not on a turn having happened: the stage
    exists to ask one question, and ending it because time passed is how a group reaches the label
    stage having never been asked what the labeler is for.
    """
    if purpose_recorded:
        return 'content'

    cleo_turns = sum(1 for m in prior_feedback if getattr(m, 'type', None) == 'ai') + 1
    if cleo_turns >= PURPOSE_MAX_TURNS:
        logger.info(
            "Advancing purpose -> content after %d turns without a recorded purpose", cleo_turns
        )
        return 'content'

    return 'purpose'


def provide_feedback(state: BrainstormingAgentState) -> Command[Literal['draft_response']]:
    """Provide feedback on user's queries about labels or labeler configuration.
    Proposed changes are staged in pending_proposal for channel approval rather
    than applied directly to labeler_config.

    Uses the most recent pending proposal as the working state if one exists,
    so the feedback agent can revise proposals that haven't been approved yet.
    """
    uncommitted_proposals = [
        s for s in (state.get('pending_suggestions') or {}).values() if not s.get('committed')
    ]
    if uncommitted_proposals:
        working_config = dict(uncommitted_proposals[-1]['proposal'])
    else:
        working_config = dict(state.get('labeler_config') or {})

    # Most recent staged (or committed) classification rules, so the rules stage can revise
    # existing rules rather than re-deriving them from scratch on every turn.
    uncommitted_rules = [
        s for s in (state.get('pending_rule_suggestions') or {}).values() if not s.get('committed')
    ]
    if uncommitted_rules:
        working_rules = dict(uncommitted_rules[-1]['proposal'])
    else:
        working_rules = dict(state.get('classification_rules') or {})

    working_labels = {
        label['identifier']: label
        for label in (working_config.get('labels') or [])
    }
    # Strip 'labels' key so FeedbackGraphState.labeler_config only holds
    # top-level fields (display_name, description).
    working_config_for_feedback = {k: v for k, v in working_config.items() if k != 'labels'}

    try:
        # Build context from prior feedback conversation (trimmed) + current human message.
        # Using feedback_messages rather than filtering state['messages'] to human-only
        # preserves the feedback agent's own clarifying questions across turns.
        prior_feedback = list(state.get('feedback_messages') or [])
        new_humans = _unconsumed_human_messages(list(state['messages']), prior_feedback)
        context_messages = prior_feedback + new_humans

        # RULES_DERIVATION_PROMPT used directly by feedback agent when classification rules are
        # being built. That's the 'rules' setup stage and also the 'preview' lifecycle stage,
        # where the group reviews the labeler and asks for rule tweaks before approving.
        setup_stage = state.get('setup_stage')
        lifecycle_stage = state.get('lifecycle_stage')
        deriving_rules = setup_stage == 'rules' or lifecycle_stage == 'preview'
        # What the feedback subgraph sees, so its own stage-based logic matches the prompt.
        effective_stage = 'rules' if deriving_rules else setup_stage
        logger.info(
            "provide_feedback: setup_stage=%s lifecycle_stage=%s (%s)",
            setup_stage, lifecycle_stage,
            "deriving rules" if deriving_rules else "designing config",
        )
        system_prompt = None
        if deriving_rules:
            system_prompt = RULES_DERIVATION_PROMPT.format(
                current_config=format_labeler_context(working_config),
                current_rules=format_rules_context(working_rules),
                capabilities=LABELER_CAPABILITIES,
                query=""
            )


        result = feedback_graph.invoke({
            "messages": context_messages,
            "labeler_config": working_config_for_feedback,
            "labels": working_labels,
            "setup_stage": effective_stage,
            "community_purpose": state.get('community_purpose'),
            "design_notes": state.get('design_notes') or [],
            "stage_context": _stage_context(state),
            **({"system_prompt": system_prompt} if system_prompt else {})
        })

        for msg in result.get('messages', []):
            msg_type = getattr(msg, 'type', type(msg).__name__)
            logger.info("feedback graph message [%s]: %s", msg_type, getattr(msg, 'content', msg))

        feedback_msg = result['messages'][-1]
        raw = feedback_msg.content if hasattr(feedback_msg, 'content') else ""
        if isinstance(raw, list):
            feedback_text = "\n".join(
                block.get("text", "") for block in raw
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            feedback_text = raw or ""

        new_feedback_messages = new_humans + [feedback_msg]
        update = {
            "messages": [feedback_msg],
            "pending_proposal": None,
            "feedback_response": feedback_text,
            "feedback_messages": new_feedback_messages,
        }

        # Design details raised ahead of the stage that handles them. Kept in state rather than
        # left to the agent's own history, which is trimmed — a "flag product spam" said during
        # the purpose stage has to survive to the content stage intact.
        noted = [
            detail
            for args in _tool_call_args(result.get('messages', []), 'note_for_later')
            for detail in (args.get('details') or [])
            if isinstance(detail, str) and detail.strip()
        ]
        if noted:
            update['design_notes'] = noted
            logger.info("Parked %d design detail(s) for a later stage", len(noted))

        # The group answered who they are and what they want. Recorded because it happened, and
        # is what ends the purpose stage below.
        recorded_purpose = None
        for args in _tool_call_args(result.get('messages', []), 'record_purpose'):
            if all(args.get(field) for field in ('community', 'audience', 'goal')):
                recorded_purpose = {
                    'community': args['community'],
                    'audience': args['audience'],
                    'goal': args['goal'],
                }
                break
        if recorded_purpose:
            update['community_purpose'] = recorded_purpose
            logger.info("Recorded community purpose: %s", recorded_purpose['goal'])

        # A turn spent entirely on tool calls leaves no text to send. Only the purpose stage can
        # reach the channel empty (see _purpose_fallback_reply), so that's where the floor is.
        if effective_stage == 'purpose' and not feedback_text.strip():
            logger.info("Feedback agent returned no text at the purpose stage — using the fallback")
            feedback_text = _purpose_fallback_reply(noted)
            update['feedback_response'] = feedback_text

        # extract proposal from finalize_proposal tool call args in the message history
        pending_proposal = None
        for msg in result.get('messages', []):
            tool_calls = getattr(msg, 'tool_calls', None)
            if not tool_calls:
                continue
            for tc in tool_calls:
                if tc.get('name') != 'finalize_proposal':
                    continue
                args = tc.get('args', {})
                proposed_config = {**working_config_for_feedback}
                if args.get('display_name') is not None:
                    proposed_config['display_name'] = args['display_name']
                if args.get('description') is not None:
                    proposed_config['description'] = args['description']
                if args.get('labels'):
                    # The model does not choose default_setting; it is pinned here so blurs
                    # and severity alone decide behavior (see label_policy).
                    proposed_config['labels'] = pin_default_setting(args['labels'])
                pending_proposal = proposed_config
                break
            if pending_proposal:
                break
        update['pending_proposal'] = pending_proposal
        update['feedback_response'] = feedback_text

        # extract classification rules from finalize_rules tool call args in the message history
        pending_classification_rules = None
        for msg in result.get('messages', []):
            tool_calls = getattr(msg, 'tool_calls', None)
            if not tool_calls:
                continue
            for tc in tool_calls:
                if tc.get('name') != 'finalize_rules':
                    continue
                args = tc.get('args', {})
                # Enforcement boundary: drop signals the executor can't run and skip
                # labels left with no enforceable include signal, so nothing unenforceable
                # is staged for approval.
                cleaned_rules, rule_errors = sanitize_rules(args.get('rules') or [])
                if rule_errors:
                    logger.info("Dropped unenforceable rule signals: %s", "; ".join(rule_errors))
                if not cleaned_rules:
                    # Nothing enforceable survived — don't stage a phantom rules block.
                    # The agent's message (driven by finalize_rules' error) explains why.
                    continue
                rules_by_label = dict(state.get('classification_rules') or {})
                for rule in cleaned_rules:
                    rules_by_label[rule['label_identifier']] = rule
                pending_classification_rules = rules_by_label
                break
            if pending_classification_rules:
                break
        update['pending_classification_rules'] = pending_classification_rules

        # Advance purpose -> content on the purpose being captured, NOT on a proposal (the stage
        # forbids finalize_proposal, so artifact-gating it on display_name/description deadlocks).
        if setup_stage == 'purpose':
            update['setup_stage'] = _purpose_stage_after(
                prior_feedback,
                purpose_recorded=bool(recorded_purpose or state.get('community_purpose')),
            )
        else:
            update['setup_stage'] = setup_stage

    except Exception as e:
        logger.exception("Feedback agent failed: %s", e)
        error_text = f"Feedback agent temporarily unavailable: {str(e)}"
        update = {
            "messages": [{"role": "assistant", "content": error_text}],
            "pending_proposal": None,
            "pending_classification_rules": None,
            "setup_stage": state.get('setup_stage'),
            "feedback_response": error_text,
        }

    return Command(update=update, goto="draft_response")


def summarize_conversation(state: BrainstormingAgentState) -> Command[Literal['draft_response']]:
    """Summarize the conversation to date, including labeler configuration if present.

    "What have we worked on?" has to cover the classification rules too: a group that has been
    through the rules stage spent most of its effort there, and a summary built from the labeler
    config alone leaves that out entirely. Rules go in as their precise form so the summary is
    accurate, with an explicit instruction to describe them in the group's words — the raw values
    include regex the group never sees.
    """
    summary = state.get('conversation_summary', '')
    labeler_context = format_labeler_context(state.get('labeler_config') or {})
    rules = state.get('classification_rules') or {}
    stage_context = _stage_context(state)

    if summary:
        summary_message = (
            f"This is a summary of the conversation to date: {summary}\n\n"
            "Extend the summary by taking into account the new messages above, "
            "and reflect the current labeler configuration state."
        )
    else:
        summary_message = (
            "Create a summary of the conversation above, including the current "
            "labeler configuration state if present."
        )

    if labeler_context:
        summary_message += f"\n\nCurrent labeler configuration:\n{labeler_context}"

    if rules:
        summary_message += (
            f"\n\nCurrent classification rules:\n{format_rules_context(rules)}\n"
            "Cover what these rules catch in the group's own words. Never reproduce raw patterns, "
            "regex or field syntax in the summary — the group has never seen those."
        )

    if stage_context:
        summary_message += f"\n\nWhere the group is:\n{stage_context}"

    messages = state["messages"] + [HumanMessage(content=summary_message)]
    response = llm.invoke(messages)

    return Command(
        update={"conversation_summary": response.content},
        goto='draft_response'
    )


def draft_response(state: BrainstormingAgentState) -> dict:
    """Generate response using context and route based on quality"""

    # `or {}` rather than a get default: the key is present and None on a fresh state, so the
    # default never fires and every classification.get() below raises.
    classification = state.get('classification') or {}

    # Format context from raw state data on-demand
    context_sections = []

    # First, so a "where are we / what's next" question is answered from live state rather than
    # from whatever the retriever happened to return.
    stage_context = _stage_context(state)
    if stage_context:
        context_sections.append(f"Where the group is:\n{stage_context}")

    if state.get('search_results'):
        formatted_docs = "\n".join([f"- {doc}" for doc in state['search_results']])
        context_sections.append(f"Relevant documentation:\n{formatted_docs}")

    labeler_context = format_labeler_context(state.get('labeler_config') or {})
    if labeler_context:
        context_sections.append(f"Labeler configuration:\n{labeler_context}")

    if state.get('conversation_summary'):
        context_sections.append(f"Conversation summary: {state['conversation_summary']}")

    pending_proposal = state.get('pending_proposal')
    if pending_proposal:
        context_sections.append(
            "A labeler update has been proposed. Explain what the change does and why it makes sense. "
            "Do not list the proposal fields — they will appear in a structured block below your response."
        )

    pending_rules = state.get('pending_classification_rules')
    if pending_rules:
        context_sections.append(
            "Classification rules have been proposed. Explain the reasoning briefly. "
            "Do not list the rule fields — they will appear in a structured block below your response."
        )

    draft_prompt = DRAFT_RESPONSE_PROMPT.format(
        query=_last_user_query(state['messages']),
        intent=classification.get('intent', 'unknown'),
        workflow=CLEO_WORKFLOW,
        capabilities=LABELER_CAPABILITIES,
        # The same table the feedback agent reasons from. Without it this node described label
        # behavior from memory and got it wrong — an 'inform' label announced as an alert.
        behavior_table=LABEL_BEHAVIOR_TABLE,
        context=chr(10).join(context_sections)
    )

    # Surface the feedback agent's own reply rather than re-drafting it. Keyed on the feedback
    # agent having run, not on intent == 'feedback': during setup, validate_and_classify routes
    # EVERY intent to provide_feedback, so an intent test here misses the question/nudge turns
    # that ran the feedback agent and lets the LLM paraphrase a proposal it never read.
    # provide_feedback always writes this key (a string, possibly empty) and every responding
    # turn clears it to None first, so `is not None` means "provide_feedback ran on this turn".
    feedback_response = state.get('feedback_response')
    if feedback_response is not None:
        parts = [feedback_response.strip()]
        if pending_proposal:
            parts.append(format_proposal_block(pending_proposal))
        if pending_rules:
            parts.append(format_rules_block(pending_rules))

        # A vote left open across turns of ordinary design talk goes quiet — the group stops
        # seeing it. Stated here rather than left to the agent, whose reply is passed through
        # verbatim and which drops the tally in practice.
        #
        # Suppressed on a turn that stages something: promotion into pending_suggestions happens
        # AFTER the graph run (see agent_runner), so the anchor still visible from here is the
        # card the new one is about to supersede. Reporting its count under a freshly staged card
        # would advertise a vote that stops counting the moment this reply lands — and the new
        # card carries its own "react to approve" line anyway.
        anchor = _live_pending_anchor(state)
        if anchor and not (pending_proposal or pending_rules):
            what, tally = _anchor_what_and_tally(state, anchor)
            parts.append(PENDING_VOTE_FOOTER.format(what=what, tally=tally))

        return {"draft_response": "\n\n".join(p for p in parts if p)}

    accumulated = []
    for chunk in llm.stream([SystemMessage(content=draft_prompt)] + state['messages']):
        accumulated.append(chunk.content)

    response_text = "".join(accumulated)
    if classification.get('intent') == 'show_config':
        response_text = f"{response_text}\n\n{format_config_block(state.get('labeler_config') or {})}"
    if pending_proposal:
        response_text = f"{response_text}\n\n{format_proposal_block(pending_proposal)}"
    if pending_rules:
        response_text = f"{response_text}\n\n{format_rules_block(pending_rules)}"

    return {"draft_response": response_text}