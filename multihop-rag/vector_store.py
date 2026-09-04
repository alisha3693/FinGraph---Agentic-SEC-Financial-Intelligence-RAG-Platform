import os
from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
VECTOR_DB_DIR = os.path.join(CURRENT_DIR, "chroma_db")


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    """Load the sentence-transformers embedding model once per process and reuse it —
    re-instantiating it per request/ingest was the single biggest latency cost in the app.

    Also runs one real embedding computation here, not just model construction. Merely
    constructing HuggingFaceEmbeddings loads the model's weights but never runs a forward
    pass, so the *first actual* embedding computation was otherwise happening lazily,
    inside whichever thread called it first — in practice a FastAPI request-handler worker
    thread (sync routes are dispatched off the main thread), the first time a ticker was
    ingested. On Windows, PyTorch's native BLAS/OpenMP thread pool initializes lazily on
    that first real forward pass; initializing it off the main thread is a known cause of
    a hard interpreter segfault (observed here: exit code 139, mid-ingest, right as the
    first filing chunk was embedded) rather than a catchable Python exception. This
    function is called once from app.py's lifespan, which runs on the main thread during
    startup before any request is served — so forcing the real pass to happen right here
    makes the one-time native init happen somewhere safe, before any worker thread can
    race to be the first to trigger it.
    """
    model = HuggingFaceEmbeddings(model_name=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    model.embed_query("warmup")
    return model
