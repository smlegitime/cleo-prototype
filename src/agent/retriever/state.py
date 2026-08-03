from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage

class RetrieverGraphState(TypedDict):
    messages: Annotated[AnyMessage, add_messages]
    question: str
    context: list[str]