from langchain_core.messages import HumanMessage
from src.config import model as base_model
from src.agent.retriever.state import RetrieverGraphState

from src.agent.retriever.tools import tools

model = base_model.bind_tools(tools)


def _last_user_query(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human":
            return m.content
    return messages[0].content if messages else ""


def call_model(state: RetrieverGraphState):
    messages = state['messages']
    search_prompt = (
        "Retrieve the relevant information based on the user query. "
        "If the intent and topic are not specified, do your best to infer them from the user query. "
        "Use the retriever tool to help you answer the user query."
    )
    messages = [{'role': 'system', 'content': search_prompt}] + messages
    response = model.invoke(messages)

    return {"messages": [response]}

def retrieve_results(state: RetrieverGraphState):
    question = _last_user_query(state['messages'])
    tool_message = state['messages'][-1]
    docs = getattr(tool_message, 'artifact', None)
    context = [doc.page_content for doc in docs] if docs else [tool_message.content]

    return {
        "question": question,
        "context": context
    }