"""
Brainstorming Agent State. The Retriever and Feedback agent states
are child states of this parent state. The lifecycle orchestrator
also uses this state.
"""

import operator
from typing import Literal, TypedDict, Annotated, Sequence
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


# Label Definition schema
class Locale(TypedDict):
    lang: str | None = None
    name: str | None = None
    description: str | None = None

class LabelValueDefinition(TypedDict):
    identifier: str | None = None
    blurs: Literal['content', 'media', 'none'] = 'none'
    severity: Literal['alert', 'inform', 'none'] = 'inform'
    default_setting: Literal['hide', 'warn', 'ignore'] = 'warn'
    locales: list[Locale] | None = None


# Defines the structure for message classification
class MessageClassification(TypedDict):
    intent: Literal['question', 'feedback', 'summary', 'show_config', 'generate_code', 'nudge']
    atproto: Literal['bluesky', 'atproto', 'labeler', 'label']
    topic: str

# Defines the structure for message classification
class CommunityGuidelinesValidation(TypedDict):
    message: str
    violation: bool

# Combined schema for single-call validation + classification
class ValidationAndClassification(TypedDict):
    violation: bool
    message: str
    intent: Literal['question', 'feedback', 'summary', 'show_config', 'generate_code', 'nudge']
    atproto: Literal['bluesky', 'atproto', 'labeler', 'label']
    topic: str

# Reaction to an AI message (approval signal for labeler updates)
class Reaction(TypedDict):
    message_id: str
    user_id: str
    reaction_type: str  # e.g. "love", "thumbs_up"

# Labeler configuration schema
class LabelerDeclaration(TypedDict):
    display_name: str | None = None
    description: str | None = None
    labels: list[LabelValueDefinition] | None = None

# What the group said the labeler is FOR, captured at the 'purpose' setup stage. Recorded in the
# group's own words by the record_purpose tool; it is what lets the purpose stage end because it
# happened rather than because a turn elapsed.
class CommunityPurpose(TypedDict, total=False):
    community: str   # who this group is
    audience: str    # who the labels are for
    goal: str        # what they want the labeler to accomplish


# A proposed labeler change pending channel approval
class PendingSuggestion(TypedDict, total=False):
    proposal: LabelerDeclaration
    approved_by: list[str]  # user_ids who reacted with the approval emoji
    committed: bool         # set once the vote carried and the proposal was applied
    # Set when a newer proposal replaced this one. Votes on a superseded anchor are inert: the
    # scroll keeps every proposal CLEO ever posted, and without this a 👍🏾 on an older card would
    # commit a design the group had already moved past.
    superseded: bool

# Derived signals that tell the labeler when to apply each label
# For type='account', value must follow the format: "<field> <op> <threshold>"
# Supported fields: account_age_days, follower_count, following_count,
#                   post_count, has_avatar, has_description
# Supported operators: <, <=, >, >=, ==, !=
# Examples: "account_age_days < 30", "has_avatar == false"
#
# 'plain_name' names the signal in the group's language ("a cure word", "a sales pitch"). It is
# what the proposal card shows instead of a regex. The model names its own signals instead of 
# the card leaking syntax.
class ClassificationSignal(TypedDict):
    type: Literal['keyword', 'pattern', 'account']
    value: str
    plain_name: str | None = None

# One AND-branch: every signal in all_of must match the SAME post for the group to fire.
class SignalGroup(TypedDict):
    all_of: list[ClassificationSignal]

# include_groups is disjunctive normal form: the label applies when ANY group fires, and a
# group fires only when ALL its signals match. A single-signal group fires on its own, so a
# flat OR list is just N groups of one — which is exactly what legacy `include_signals`
# rules mean, and how they are read (see _include_groups in brainstorming/nodes.py).
#
# Grouping exists because a flat OR cannot say what groups actually ask for. "Flag a cure
# claim PLUS a link to buy" flattened to OR labels every post containing "DM me"; an
# account trait OR'd in labels every post by every new account.
class ClassificationRule(TypedDict):
    label_identifier: str
    include_groups: list[SignalGroup]
    exclude_signals: list[ClassificationSignal]  # flat: skip the post if ANY of these match
    notes: str | None # rationale that's human-readable

# Proposed classification rules pending channel approval
class PendingRuleSuggestion(TypedDict, total=False):
    proposal: dict[str, ClassificationRule]
    approved_by: list[str]  # user_ids who reacted with the approval emoji
    committed: bool         # set once the vote carried and the rules were applied
    superseded: bool        # set when a newer rule proposal replaced this one (see PendingSuggestion)


# A pending preview-approval vote. Unlike the proposal/rule suggestions this doesn't commit an
# artifact — approving it advances the lifecycle preview -> generate (materialize the sandbox bundle).
class PendingPreviewApproval(TypedDict, total=False):
    message_id: str # the message whose approval reactions count toward advancing
    approved_by: list[str] # user_ids who reacted with the approval emoji
    committed: bool # set once the advance has fired, so later reactions are no-ops


# Post-setup lifecycle: preview -> generate -> deploy -> provision -> live
LifecycleStage = Literal['preview', 'generate', 'deploy', 'provision', 'live']
LifecycleStatus = Literal['pending', 'in_progress', 'succeeded', 'failed']

# Deployment target environment, orthogonal to LifecycleStage. A labeler is exercised end-to-end in
# 'sandbox' (did:web + a locally-generated signing key. Nothing published to the PLC directory, no
# handle claimed, fully reversible) before a 'prod' identity is ever provisioned.
Environment = Literal['sandbox', 'prod']

# Where a live labeler's identity and infrastructure would come from. 'hosted' is the default and
# needs nothing technical from the group (we run the service, the identity lives on our domain);
# 'self_owned' is the upgrade for a group that already owns a domain (did:web on it — they own the
# identity outright, at the cost of the identity dying if the domain lapses).
HostingTier = Literal['sandbox', 'hosted', 'self_owned']

# The group's answers to the going-live governance questions, gathered at the provision stage.
# Deliberately SEPARATE from DeploymentRecord: that record is what was actually deployed, this is
# what the group decided. total=False because these fill in one at a time, over more than one
# sitting — a group that answers two of them today must not have to restart in three weeks.
#
# Only three of these actually block going live (handle, custodian, recovery email); the rest are
# strongly recommended and never gate. See maintenance_guide.py, which renders them as a checklist.
#
# NOTE: the custodian's email address is NEVER stored here (or anywhere in the checkpoint). It is
# submitted straight to the backend from a one-time link form; all that survives is `recovery_email_kind`
# ('role' = a shared group address, 'personal' = one member's inbox — worth flagging back to the group,
# since a personal address quietly makes that member the owner) and a confirmation timestamp.
class GovernanceRecord(TypedDict, total=False):
    handle_choice: str | None                   # handle the group approved, pre-provisioning
    custodian_user_id: str | None               # who the group designated to hold the account
    custodian_display_name: str | None          # human-readable, for the guide copy
    custodian_confirmed_at: str | None
    backup_custodian_display_name: str | None   # recommended: a custodian of one is a bus factor of one
    # NOTHING POPULATES THESE YET. The provision stage collects names and roles only — there is no
    # email-collection mechanism (no one-time link, no verification), so the guide names the
    # requirement instead. Kept because they're the right model for when that flow lands.
    recovery_email_kind: Literal['role', 'personal'] | None
    recovery_email_confirmed_at: str | None
    appeals_contact: str | None                 # who a mislabeled person talks to
    hosting_tier: HostingTier | None
    own_domain: str | None                      # only for hosting_tier == 'self_owned'


# A governance answer set staged by the provision executor, waiting for group approval. Mirrors
# PendingSuggestion/PendingRuleSuggestion so the same vote mechanic applies; `committed` guards
# against a late reaction re-applying an answer set that already landed.
class PendingGovernanceSuggestion(TypedDict, total=False):
    proposal: GovernanceRecord
    approved_by: list[str]
    committed: bool


# Operational record for a provisioned/deployed labeler. Kept out of the labeler spec on
# purpose: the spec is the group's design, this is the runtime identity it was deployed
# under. total=False because the fields fill in progressively across the lifecycle stages.
class DeploymentRecord(TypedDict, total=False):
    environment: Environment | None  # 'sandbox' (did:web + local key) or 'prod'; set at deploy time
    labeler_did: str | None # did:plc / did:web of the labeler account
    handle: str | None # bsky handle it publishes under
    service_endpoint: str | None # where the labeler service runs
    deployed_spec_id: str | None # spec_id actually live; != current spec_id => drift
    bundle_dir: str | None # on-disk path of the materialized sandbox bundle (see lifecycle/bundle.py)
    provisioned_at: str | None
    deployed_at: str | None


# Mock feed content for the preview stage. Generated from the labeler spec + the group's
# conversation (see src/agent/lifecycle/preview_posts.py), cached in state keyed by the spec_id it was
# generated for so it's stable across refreshes and regenerates only when the design changes.
class PreviewPost(TypedDict):
    name: str # author display name
    handle: str # author handle (no @)
    text: str # post body
    media: bool # whether the post carries an image

class PreviewPostsCache(TypedDict):
    spec_id: str # spec_id these posts were generated for; the cache key
    posts: list[PreviewPost]


# Real Bluesky posts fetched at the generate stage to test rule quality (see src/agent/lifecycle/corpus.py).
# Keyed by corpus_key: a fingerprint of the query BASIS, NOT the spec_id so editing rules within
# the same domain replays the same posts and quality diffs stay attributable to the rules. Posts are
# stored as plain dicts (corpus.CorpusPost) to avoid a state <-> corpus import cycle.
class QualityCorpusCache(TypedDict):
    corpus_key: str         # corpus.corpus_key(spec); the cache key
    posts: list[dict]       # corpus.CorpusPost dicts
    fetched_at: str         # ISO-8601 UTC


def _merge_dicts(a: dict, b: dict) -> dict:
    return {**a, **b}


def _append_notes(existing: list | None, new: list | None) -> list:
    """Additive, except that None clears.

    Parked design details are spent once the stage that consumes them lands, and a plain additive
    list has no way to say so — they'd follow the group into every later turn as context that
    reads like an outstanding request.
    """
    if new is None:
        return []
    return (existing or []) + new


# Max messages kept for the feedback agent.
FEEDBACK_CONTEXT_WINDOW = 20

def _append_and_trim_feedback(existing: list, new: list) -> list:
    return ((existing or []) + (new or []))[-FEEDBACK_CONTEXT_WINDOW:]


# Agent state schema
class BrainstormingAgentState(TypedDict):
    # Deterministic addressing signal computed before the graph runs
    # (@-mention or bare name at message start).
    # Must be set explicitly on every invocation — checkpointed values persist otherwise.
    force_respond: bool | None

    # How many approvals a pending suggestion currently needs (derived from the channel's non-AI
    # member count by voting.approvals_needed, passed in per run because the graph can't see the
    # Stream roster). Only read when telling the group what a pending vote is still waiting on.
    approvals_needed: int | None

    # Community guidelines validation
    validation: CommunityGuidelinesValidation | None

    # Classification result
    classification: MessageClassification | None

    # Raw search results
    search_results: list[str] | None
    conversation_summary: str | None

    # Generate labeler config code results
    generated_code: str | None

    # Labeler config and generated content
    labeler_config: LabelerDeclaration
    draft_response: str | None
    feedback_response: str | None
    feedback_messages: Annotated[list, _append_and_trim_feedback]
    messages: Annotated[Sequence[AnyMessage], add_messages]

    # Classification rules
    classification_rules: dict[str, ClassificationRule] | None

    # Setup stages, to help the agent prompt the user with needed info about the labeler
    setup_stage: Literal['purpose', 'content', 'rules', 'complete'] | None

    # Who the group is and what they want the labeler to do, in their words. Gates purpose -> content.
    community_purpose: CommunityPurpose | None

    # Design details the group volunteered before the stage that handles them — "flag product spam",
    # "blur the images" said while CLEO is still establishing purpose. Parked here so the answer can
    # be "noted, we'll get to it" instead of either derailing the stage or losing the detail: the
    # feedback agent's own history is trimmed to FEEDBACK_CONTEXT_WINDOW, so early asks can scroll
    # out before the stage that needs them. Additive (None clears); rendered de-duplicated.
    design_notes: Annotated[list[str], _append_notes]

    # Reactions to AI messages — appended on each new reaction
    reactions: Annotated[list[Reaction], operator.add]

    # Proposed labeler change staged by the agent, waiting to be keyed by message_id
    pending_proposal: LabelerDeclaration | None

    # Approved proposals keyed by Stream message_id — merged on update
    pending_suggestions: Annotated[dict[str, PendingSuggestion], _merge_dicts]

    # Classification rules staged by the agent, waiting for group approval
    pending_classification_rules: dict[str, ClassificationRule] | None

    # Rule proposals keyed by Stream message_id — merged on update, voted on like labeler proposals
    pending_rule_suggestions: Annotated[dict[str, PendingRuleSuggestion], _merge_dicts]

    # Post-setup lifecycle. None until setup_stage reaches 'complete', at which point the
    # rules-approval handoff sets stage='preview'/status='pending'. Advancement is explicit
    # (group approves the preview, the sandbox deploy passes, provisioning succeeds, ...), not
    # artifact-gated like setup_stage. All fields additive + nullable so old checkpoints stay readable.
    lifecycle_stage: LifecycleStage | None
    lifecycle_status: LifecycleStatus | None
    lifecycle_error: str | None            # human-readable last failure, when status == 'failed'

    # spec currently driving the lifecycle (from spec.build_spec). Compared against
    # deployment.deployed_spec_id to tell whether a maintenance edit owes a redeploy.
    spec_id: str | None
    deployment: DeploymentRecord | None

    # The group's going-live governance answers (custodian, handle, appeals contact, ...). None
    # until the group starts answering them; partial answers persist across sittings so the
    # provision conversation can be resumed rather than restarted.
    governance: GovernanceRecord | None

    # Handle candidates offered to the group at the start of provision (see lifecycle/provision.py).
    # Kept so the extractor can resolve "the second one" on a later turn.
    provision_handle_candidates: list[str] | None

    # Pending go-live gate — reacting to the anchor advances deploy -> provision. Separate from
    # pending_deploy_approval (which is the earlier generate -> deploy ship gate).
    pending_provision_approval: PendingPreviewApproval | None

    # The other half of the fork posted after the sandbox run: reacting here says the group wants
    # the maintenance guide instead of the going-live questions. Same shape as the gates, but it
    # advances nothing — the channel stays at `deploy`. Whichever of the two anchors reaches the
    # threshold first wins and closes the other (see voting.process_guide_choice).
    pending_guide_choice: PendingPreviewApproval | None

    # Governance answer sets staged during provision, keyed by Stream message_id and voted on like
    # config/rule proposals. Merged on update so a later answer set doesn't clobber an earlier one.
    pending_governance_suggestions: Annotated[dict[str, PendingGovernanceSuggestion], _merge_dicts]

    # Cached mock feed for the preview stage, keyed by the spec_id it was generated for.
    preview_posts: PreviewPostsCache | None

    # Real-post corpus fetched at the generate stage for rule-quality checks, keyed by the domain
    # fingerprint (corpus_key) so rule edits within a domain replay the same posts.
    quality_corpus: QualityCorpusCache | None

    # Aggregated rule-quality report from running the interpreter over quality_corpus at generate
    # (see src/agent/lifecycle/quality.py). Stored for the later quality screen; the chat summary is derived
    # from it. Plain dict to avoid a state <-> quality import cycle.
    quality_report: dict | None

    # Summary of the sandbox executor run at the deploy stage (see src/agent/lifecycle/sandbox.py): the sandbox
    # DID, how many signed label records were emitted, per-label counts, and examples. Feeds the chat
    # run report and the future sandbox screen. Plain dict to avoid a state <-> sandbox import cycle.
    sandbox_run: dict | None

    # Pending preview-approval vote — reacting advances the lifecycle preview -> generate.
    pending_preview_approval: PendingPreviewApproval | None

    # Pending ship-gate approval — reacting to the quality-report anchor advances generate -> deploy
    # and materializes the sandbox bundle. Reuses PendingPreviewApproval's shape; the quality report
    # itself stays informational (this is the single explicit "go", not a vote on the report).
    pending_deploy_approval: PendingPreviewApproval | None
