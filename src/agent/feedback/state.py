from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage
from typing import Annotated, Literal, Sequence, TypedDict

from src.agent.state import CommunityPurpose, LabelValueDefinition, LabelerDeclaration

class FeedbackGraphState(TypedDict):
    messages: Annotated[Sequence[AnyMessage], add_messages]
    labels: dict[str, LabelValueDefinition] | None # look up and store labels by identifier
    labeler_config: LabelerDeclaration | None
    system_prompt: str | None # None -> use FEEDBACK_AGENT_PROMPT
    setup_stage: Literal['purpose', 'content', 'rules', 'complete'] | None
    community_purpose: CommunityPurpose | None # what the group said this is for, if established
    design_notes: list[str] | None # details volunteered ahead of the stage that handles them
    stage_context: str | None # live stage + approval tally, so "what's next?" is answered from state