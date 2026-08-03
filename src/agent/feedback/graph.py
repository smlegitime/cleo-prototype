from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import tools_condition

from src.agent.feedback.state import FeedbackGraphState
from src.agent.feedback.nodes import call_model
from src.agent.feedback.tools import tool_node

workflow = StateGraph(FeedbackGraphState)

workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

workflow.add_edge(START, "agent")
workflow.add_conditional_edges("agent", tools_condition)
workflow.add_edge("tools", "agent")

feedback_graph = workflow.compile()
