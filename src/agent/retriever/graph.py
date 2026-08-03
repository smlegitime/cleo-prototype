from langgraph.graph import END, START, StateGraph

from src.agent.retriever.state import RetrieverGraphState
from src.agent.retriever.tools import tool_node
from src.agent.retriever.nodes import call_model, retrieve_results

# Define a new graph
workflow = StateGraph(RetrieverGraphState)

# Define the two nodes we will cycle between
workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)
workflow.add_node("retrieve_results", retrieve_results)

workflow.add_edge(START, "agent")
workflow.add_edge("agent", "tools")
workflow.add_edge("tools", 'retrieve_results')
workflow.add_edge('retrieve_results', END)

retriever_graph = workflow.compile()