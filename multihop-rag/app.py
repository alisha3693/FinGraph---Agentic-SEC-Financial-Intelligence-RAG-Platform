import os

# Must run before anything below transitively imports torch/sentence-transformers
# (graph_agent -> vector_store -> langchain_huggingface -> sentence_transformers -> torch).
# On Windows, torch and other numeric libs each often ship their own bundled OpenMP
# runtime (libiomp5md.dll); when two copies end up loaded in the same process and real
# parallel numeric work starts (the first actual embedding pass, not the harmless
# warm-up), the duplicate can segfault the whole interpreter instead of just erroring —
# this is exactly the crash observed here (exit code 139, mid-ingest). Setting this tells
# the runtime to tolerate the duplicate instead of aborting. `setdefault` so an operator
# who has deliberately set this differently at the OS level is never overridden.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from graph_agent import run_agent, get_default_financials
from db_manager import get_companies, delete_company
from sec_client import load_company_data, delete_ticker_vectors
from vector_store import get_embeddings

# Load environment variables
load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is not set. Set it in multihop-rag/.env before starting the server — "
            "every query route depends on it."
        )
    # Pre-load the embedding model once at startup instead of paying that cost on the
    # first user query.
    get_embeddings()
    logger.info("Startup checks passed: GROQ_API_KEY present, embedding model warmed.")
    yield


app = FastAPI(title="SEC EDGAR Financial Intelligence API", lifespan=lifespan)

# CORS is scoped to the actual local frontend origins (Vite's default dev ports).
# No credentials (cookies/auth) are used, so allow_credentials stays off — that combination
# with a wildcard origin is invalid anyway and browsers reject it.
FRONTEND_ORIGINS = os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    prompt: str
    ticker: Optional[str] = None

class QueryResponse(BaseModel):
    answer: str
    sources: list
    chart_data: list
    chart_meta: dict = {}
    table: list = []
    table_columns: list = []

class LoadTickerRequest(BaseModel):
    ticker: str

class LoadTickerResponse(BaseModel):
    success: bool
    message: str

# Routes are plain `def`, not `async def`: every route body does blocking work (sqlite3,
# `requests`, the embedding model, the Groq client), and none of it is awaited. FastAPI runs
# sync routes in a worker thread pool automatically, so one slow SEC/LLM call no longer
# stalls the event loop — and therefore every other in-flight request — while it waits.

@app.post("/api/query", response_model=QueryResponse)
def query_rag(request: QueryRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    try:
        result = run_agent(request.prompt, ticker_hint=request.ticker)
        return QueryResponse(
            answer=result["answer"],
            sources=result["sources"],
            chart_data=result["chart_data"],
            chart_meta=result.get("chart_meta", {}),
            table=result.get("table", []),
            table_columns=result.get("table_columns", []),
        )
    except Exception:
        logger.exception("Error executing graph agent")
        raise HTTPException(status_code=500, detail="Query processing failed.")

@app.get("/api/companies")
def get_cached_companies():
    try:
        companies = get_companies()
        return companies
    except Exception:
        logger.exception("Could not load cached companies")
        raise HTTPException(status_code=500, detail="Could not load companies.")

@app.get("/api/financials/{ticker}")
def get_financials_overview(ticker: str):
    """All cached annual metrics (Revenue, Net Income, EPS, Assets, Liabilities) for a
    ticker — used to populate the default table/chart before any question is asked."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")

    try:
        return get_default_financials(ticker)
    except Exception:
        logger.exception("Error loading financials overview for %s", ticker)
        raise HTTPException(status_code=500, detail="Could not load financials.")

@app.post("/api/load_ticker", response_model=LoadTickerResponse)
def load_ticker(request: LoadTickerRequest):
    ticker = request.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")

    try:
        success, message = load_company_data(ticker)
        return LoadTickerResponse(success=success, message=message)
    except Exception:
        logger.exception("Error loading ticker %s", ticker)
        raise HTTPException(status_code=500, detail="Ticker ingestion failed.")

@app.delete("/api/companies/{ticker}")
def remove_company(ticker: str):
    ticker = ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")

    try:
        removed = delete_company(ticker)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{ticker} not found.")
        delete_ticker_vectors(ticker)
        return {"success": True, "message": f"{ticker} removed."}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error deleting ticker %s", ticker)
        raise HTTPException(status_code=500, detail="Ticker deletion failed.")

@app.get("/api/health")
def health_check():
    return {"status": "okay"}

if __name__ == "__main__":
    import uvicorn

    # `reload` is OFF by default. The reloader runs the server in a forked child and
    # re-forks it on any file change; a re-fork that lands mid-ingest, while the native
    # torch/OpenMP thread pool is initializing, wedges the child (it dies but the
    # supervisor keeps holding the socket, so every request then hangs with no response).
    # Opt in with `RELOAD=1` only for pure code-editing sessions with no ingest running.
    reload = os.getenv("RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=reload)
