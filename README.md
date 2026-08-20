# SEC EDGAR Financial Intelligence Analyzer

This project provides a React dashboard backed by FastAPI, LangGraph, LangChain, SEC EDGAR Company Facts, SEC submissions/archive filings, SQLite, and Chroma.

## Setup

1. Create `multihop-rag/.venv` and install `multihop-rag/requirements.txt`.
2. Set `SEC_USER_AGENT` to include an application name and a monitored contact email. SEC requests require this header.
3. Set `GROQ_API_KEY` and optionally `GROQ_MODEL` and `EMBEDDING_MODEL`.
4. Start the API from `multihop-rag`:

```powershell
uvicorn app:app --reload --port 8000
```

5. Start the React app from `frontend`:

```powershell
npm install
npm run dev
```

Use the dashboard to load a ticker. Loading resolves ticker to CIK, fetches Company Facts, stores structured annual metrics in SQLite, downloads recent 10-K/10-Q/8-K filings, cleans and chunks them, and indexes them in Chroma.

## Routes

- Financial and comparison questions use SQLite values for calculations and return chart-ready data.
- Filing questions use only indexed SEC filing chunks and return archive URLs as citations.
- Consumer questions fail closed unless `EXTERNAL_DATA_URL` is configured for a real external provider.

The old local corpus ingestion path and synthetic filing/demo data have been removed. Do not commit `.env`, `sec_financials.db`, `chroma_db`, or virtual environments.