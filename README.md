# SEC EDGAR Financial Intelligence Analyzer

This project provides a React dashboard backed by FastAPI, LangGraph, LangChain, SEC EDGAR Company Facts, SEC submissions/archive filings, SQLite, and Chroma.

## Setup

1. Create `multihop-rag/.venv` and install `multihop-rag/requirements.txt`.
2. In `multihop-rag/.env`, set:
   - `GROQ_API_KEY` — required. The server refuses to start without it.
   - `SEC_USER_AGENT` — required, format `"YourAppName your-real-email@example.com"`. SEC EDGAR blocks/throttles placeholder or `example.com` contacts, so this must be a real, monitored address. The server refuses to start without it.
   - `GROQ_MODEL` — optional, defaults to a current Groq-hosted model. Override if you want a different one.
   - `EMBEDDING_MODEL` — optional, defaults to `all-MiniLM-L6-v2`.
   - `FRONTEND_ORIGINS` — optional, comma-separated list of allowed CORS origins. Defaults to the Vite dev server (`http://localhost:5173,http://127.0.0.1:5173`).
   - `EXTERNAL_DATA_URL` — optional. Consumer/market-research questions fail closed and are routed to filing search instead unless this points at a real provider.
3. Start the API from `multihop-rag`:

```powershell
uvicorn app:app --reload --port 8000
```

4. Start the React app from `frontend`:

```powershell
npm install
npm run dev
```

Optionally set `VITE_API_URL` in a `frontend/.env` file if the backend isn't on `http://localhost:8000`.

Use the dashboard to load a ticker. Loading resolves ticker to CIK, fetches Company Facts, stores structured annual metrics in SQLite, downloads recent 10-K/10-Q/8-K filings, cleans and chunks them, and indexes them in Chroma. Selecting a company chip in the sidebar pins that company for your next question; leave nothing selected and the agent infers the company from the question text (asking for clarification instead of guessing if it's ambiguous).

## Routes

- Financial and comparison questions use SQLite values for calculations and return chart-ready data.
- Filing questions use only indexed SEC filing chunks and return archive URLs as citations.
- Consumer questions fail closed unless `EXTERNAL_DATA_URL` is configured for a real external provider.
- If a question doesn't clearly name a loaded company (and none is pinned in the sidebar), the agent asks which company you meant instead of defaulting to one.

The old local corpus ingestion path and synthetic filing/demo data have been removed. Do not commit `.env`, `sec_financials.db`, `chroma_db`, or virtual environments.