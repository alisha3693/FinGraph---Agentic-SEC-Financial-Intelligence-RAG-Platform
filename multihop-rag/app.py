import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from graph_agent import run_agent
from db_manager import get_companies
from sec_client import load_company_data

# Load environment variables
load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="SEC EDGAR Financial Intelligence API")

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    prompt: str

class QueryResponse(BaseModel):
    answer: str
    sources: list
    chart_data: list

class LoadTickerRequest(BaseModel):
    ticker: str

class LoadTickerResponse(BaseModel):
    success: bool
    message: str

@app.post("/api/query", response_model=QueryResponse)
async def query_rag(request: QueryRequest):
    if not request.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    
    try:
        result = run_agent(request.prompt)
        return QueryResponse(
            answer=result["answer"],
            sources=result["sources"],
            chart_data=result["chart_data"]
        )
    except Exception:
        logger.exception("Error executing graph agent")
        raise HTTPException(status_code=500, detail="Query processing failed.")

@app.get("/api/companies")
async def get_cached_companies():
    try:
        companies = get_companies()
        return companies
    except Exception:
        logger.exception("Could not load cached companies")
        raise HTTPException(status_code=500, detail="Could not load companies.")

@app.post("/api/load_ticker", response_model=LoadTickerResponse)
async def load_ticker(request: LoadTickerRequest):
    ticker = request.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")
    
    try:
        success, message = load_company_data(ticker)
        return LoadTickerResponse(success=success, message=message)
    except Exception:
        logger.exception("Error loading ticker %s", ticker)
        raise HTTPException(status_code=500, detail="Ticker ingestion failed.")

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
