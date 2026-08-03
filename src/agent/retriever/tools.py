import os
from langchain_core.tools.retriever import create_retriever_tool
from langgraph.prebuilt import ToolNode
from src.agent.retriever.rag_utils import (
    preprocess_docs,
    index_docs_faiss,
    load_index
)
from src.config import FAISS_DB_PATH

# Builds a new indexer only if one does not already exist on disk
if os.path.exists(FAISS_DB_PATH):
    vector_store = load_index()
else:
    doc_splits = preprocess_docs()
    vector_store = index_docs_faiss(doc_splits)

# Retriever tool for RAG
retriever_tool = create_retriever_tool(
    retriever=vector_store.as_retriever(),
    name="retrieve_bsky_docs",
    description="Search and return information about the Bluesky app from the appropriate sources."
)

tools = [retriever_tool]
tool_node = ToolNode(tools)