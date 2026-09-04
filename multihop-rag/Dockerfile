# --- Backend dev image (FastAPI + LangGraph agent) ---
#
# This image only carries the *installed dependencies* and a fallback copy of the code.
# In dev mode (see ../docker-compose.yml) the real source is bind-mounted over it at
# runtime, so editing a .py file on the host is reflected inside the container instantly
# — no rebuild needed. You only need to rebuild this image when requirements.txt changes.
FROM python:3.11-slim

# build-essential: chromadb's hnswlib and sentence-transformers' native deps sometimes
# need a C/C++ compiler to build during `pip install`. curl: used by the HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copied separately from the rest of the source so this layer — and the slow pip
# install — is cached by Docker and only re-runs when requirements.txt actually changes,
# not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fallback copy: makes `docker build` + `docker run` (no compose) produce a runnable
# image on its own. In the compose dev setup this is immediately shadowed by the bind
# mount of ../multihop-rag over /app.
COPY . .

EXPOSE 8000

# start-period is generous because startup blocks on warming the embedding model, which
# on a cold cache makes several live calls out to huggingface.co (see
# docs/project-status-report.md for the incident this caused when it hung).
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Delegates to app.py's own RELOAD env-var switch (default off) instead of hardcoding a
# uvicorn command here — one source of truth for that behavior.
CMD ["python", "app.py"]
