import json
from uuid import uuid4

import faiss
from langchain_community.document_loaders import WebBaseLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

from src.config import FAISS_DB_PATH, RETRIEVAL_SOURCES_PATH

# Fetch source URLs
def _get_src_urls(src_path: str = RETRIEVAL_SOURCES_PATH) -> list[str]:
    with open(src_path) as retrieval_src_file:
        retrieval_srcs = json.load(retrieval_src_file)
        urls = retrieval_srcs['bsky'] + retrieval_srcs['skyware']
        return urls

def preprocess_docs():
    # Load documents
    urls = _get_src_urls()
    docs = [WebBaseLoader(url).load() for url in urls]

    # Split documents into chunks
    docs_list = [item for sublist in docs for item in sublist]
    text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=100,
        chunk_overlap=50
    )
    doc_splits = text_splitter.split_documents(docs_list)
    return doc_splits

def index_docs(doc_splits):
    vectorstore = InMemoryVectorStore.from_documents(
        documents=doc_splits,
        embedding=OpenAIEmbeddings()
    )
    retriever = vectorstore.as_retriever()
    return retriever

def index_docs_faiss(doc_splits):
    # Store docs
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")

    embedding_dim = len(embeddings.embed_query("embed this to get model output dimension"))
    index = faiss.IndexFlatL2(embedding_dim)

    vector_store = FAISS(
        embedding_function=embeddings,
        index=index,
        docstore=InMemoryDocstore(),
        index_to_docstore_id={},
    )

    uuids = [str(uuid4()) for _ in range(len(doc_splits))] # creating unique ids for each Document obj
    vector_store.add_documents(documents=doc_splits, ids=uuids)

    vector_store.save_local(FAISS_DB_PATH)

    retriever = vector_store.as_retriever()
    return retriever

def load_index() -> FAISS:
    embeddings = OpenAIEmbeddings(model="text-embedding-3-large")
    return FAISS.load_local(FAISS_DB_PATH, embeddings, allow_dangerous_deserialization=True)
