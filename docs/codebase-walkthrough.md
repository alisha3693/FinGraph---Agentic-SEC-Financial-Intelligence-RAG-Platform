# FinGraph / RagProject — Codebase Walkthrough

This is a step-by-step explanation of how the project actually works, grounded in the
current code (not a design doc — everything below reflects what's really implemented as of
2026-08-25). Read this if you're new to the codebase and want to understand the pipeline
end to end. For open bugs, planned work, and RAG optimization ideas, see
[project-status-report.md](project-status-report.md).

---

## 1. What this project is

A financial research tool: you type a ticker (e.g. `AAPL`), the backend pulls that
company's real filings and structured financial facts straight from **SEC EDGAR**, indexes
them, and then you can ask natural-language questions ("what was Apple's revenue from 2021
to 2025?", "what risks did Apple flag in its 10-K?", "compare Apple and Microsoft revenue").
A LangGraph agent decides whether the question needs hard numbers (SQL-like lookup),
document search (vector RAG over filing text), a two-company comparison, or an external
data source — then returns a narrative answer plus, where relevant, a chart and a table.

Two independently-run processes:
- **Backend**: FastAPI app in [multihop-rag/](../multihop-rag/), Python, port 8000.
- **Frontend**: React 19 + Vite SPA in [frontend/](../frontend/), port 5173 (dev).

They talk over plain JSON HTTP; there's no shared code between them, no websockets, no
server-side rendering.

---

## 2. High-level architecture

```
┌─────────────────────┐        HTTP/JSON         ┌──────────────────────────────────┐
│   React frontend     │ ───────────────────────▶ │           FastAPI app.py          │
│   (App.jsx)          │ ◀─────────────────────── │                                    │
└─────────────────────┘                          │  ┌──────────────────────────────┐  │
                                                   │  │  graph_agent.py (LangGraph)  │  │
                                                   │  │  router → 4 possible nodes   │  │
                                                   │  └──────────────────────────────┘  │
                                                   │           │           │            │
                                                   │           ▼           ▼            │
                                                   │  ┌────────────┐ ┌───────────────┐  │
                                                   │  │ db_manager │ │ Chroma vector  │  │
                                                   │  │ (SQLite)   │ │ store (RAG)    │  │
                                                   │  └────────────┘ └───────────────┘  │
                                                   │           ▲           ▲            │
                                                   │           └─────┬─────┘            │
                                                   │          sec_client.py             │
                                                   │        (SEC EDGAR ingestion)        │
                                                   └──────────────────────────────────────┘
                                                                    │
                                                                    ▼
                                                          SEC EDGAR (data.sec.gov,
                                                          www.sec.gov) — external, real data
```

Five backend modules, each with one job:

| File | Responsibility |
|---|---|
| [app.py](../multihop-rag/app.py) | FastAPI routes — the only HTTP surface |
| [graph_agent.py](../multihop-rag/graph_agent.py) | LangGraph agent: routing + all 4 answer strategies |
| [sec_client.py](../multihop-rag/sec_client.py) | Talks to SEC EDGAR; ingests filings + facts |
| [db_manager.py](../multihop-rag/db_manager.py) | SQLite schema + queries for structured financials |
| [vector_store.py](../multihop-rag/vector_store.py) | Shared embedding-model singleton + Chroma path |

---

## 3. The two data stores

The backend keeps two persisted stores side by side, both scoped by ticker:

### 3.1 SQLite — `multihop-rag/sec_financials.db`
Structured, numeric financial facts. Schema ([db_manager.py:14-43](../multihop-rag/db_manager.py#L14-L43)):

- `companies(id, ticker UNIQUE, cik, name)`
- `financials(id, company_id → companies.id, year, revenue, net_income, eps, assets, liabilities)` — one row per company per fiscal year, `UNIQUE(company_id, year)`
- `migrations(name, applied_at)` — a one-row guard table so a one-time cleanup migration (`remove_static_seed_data`, which wiped some early hardcoded seed rows) only ever runs once, even across restarts.

This is why companies persist across backend restarts: it's a real file on disk, not
in-memory state. (The "tickers disappeared after refresh" incident was a *hung server*
process, not lost data — see [project-status-report.md](project-status-report.md).)

### 3.2 Chroma — `multihop-rag/chroma_db/`
A vector store of chunked SEC filing text (10-K/10-Q/8-K), embedded with a local
sentence-transformers model. Used only for qualitative/narrative questions ("what risks did
they flag"), not for numeric lookups — those go straight to SQLite instead.

Both stores are populated by the same action: **ingesting a ticker** (§4).

---

## 4. Pipeline: ingesting a ticker (`POST /api/load_ticker`)

This is the "Ingest Ticker" box in the sidebar. Frontend: `handleLoadTicker` in
[App.jsx:460-491](../frontend/src/App.jsx#L460-L491). Backend entry point:
`load_company_data(ticker)` in [sec_client.py:215-228](../multihop-rag/sec_client.py#L215-L228).

Step by step:

1. **Resolve ticker → CIK.** `resolve_ticker_to_cik` looks up the ticker in SEC's own
   `company_tickers.json` reference file, fetched once and cached forever via
   `@lru_cache(maxsize=1)` on `_ticker_reference()` ([sec_client.py:44-60](../multihop-rag/sec_client.py#L44-L60)). If the ticker isn't SEC-recognized, ingestion fails immediately with a clear message.

2. **Fetch structured facts.** `fetch_company_facts(cik)` calls
   `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` — SEC's XBRL "company facts" API,
   which returns every standardized US-GAAP tag the company has ever reported.

3. **Parse the 5 tracked metrics.** `parse_financials()` ([sec_client.py:83-119](../multihop-rag/sec_client.py#L83-L119)) walks a fixed metric→tag map (e.g. revenue tries
   `RevenueFromContractWithCustomerExcludingAssessedTax`, then `Revenues`, then
   `SalesRevenueNet`, etc., in order — different companies/years use different tags) and
   pulls the most-recently-filed **annual (10-K, FY)** value for each fiscal year from
   2018 through next year. Result: `{year: {revenue, net_income, eps, assets, liabilities}}`.

4. **Save to SQLite.** `save_financials()` upserts the company row and one `financials` row
   per year ([db_manager.py:53-80](../multihop-rag/db_manager.py#L53-L80)).

5. **Ingest filings for RAG.** `ingest_filings(ticker, cik, name)` ([sec_client.py:151-221](../multihop-rag/sec_client.py#L151-L221)):
   - `_filing_records(cik)` hits `data.sec.gov/submissions/CIK{cik}.json`, filters to
     `10-K`/`10-Q`/`8-K` forms, and takes the most recent **12** filings.
   - **Diffs against what's already indexed, by `accession_number`** (SEC's own unique ID
     per filing, stored on every chunk's metadata) — as of 2026-08-30 this is no longer a
     blind wipe-and-rebuild:
     - Chunks belonging to filings that fell out of the current top-12 window are **pruned**
       (deleted).
     - Filings whose `accession_number` is already indexed are **skipped entirely** — no
       re-download, no re-embed.
     - Only genuinely new filings continue past this point.
   - Each new filing's primary HTML document is downloaded concurrently
     (`ThreadPoolExecutor(max_workers=4)` — bounded to stay under SEC's ~10 req/sec
     fair-access guideline) and stripped to plain text with BeautifulSoup
     (`html.parser` — `lxml` is not installed, see the status report for why that matters).
   - Text is split into ~1800-character chunks (220-char overlap) via
     `RecursiveCharacterTextSplitter`, each chunk tagged with metadata: `company`,
     `filing_type`, `fiscal_year`, `filing_date`, a best-effort `section` guess (regex for
     `ITEM 1A. ...` style headers), `accession_number`, and the source URL.
   - New chunks are added to Chroma; the function returns the **total** chunk count now
     indexed for the ticker (existing + new − pruned), not just what changed this call.

6. Response bubbles back up as `(success: bool, message: str)` → HTTP `LoadTickerResponse`
   → frontend shows it in `tickerMessage` and, on success, refreshes the watchlist and
   auto-selects the new ticker.

**Why first-time ingestion is slow (15–30s typical):** almost all of the time is the 12
sequential-per-batch HTTP round-trips to SEC (rate-limited, real network latency) plus
local CPU-bound embedding of every chunk. **Re-ingesting an already-loaded, unchanged
ticker is now fast** (a couple of metadata reads, no downloads, no re-embedding) thanks to
the `accession_number` diffing above — only a genuinely new filing since the last ingest
triggers real work.

---

## 5. Pipeline: asking a question (`POST /api/query`)

This is the core of the app. Frontend: `handleSubmit` in
[App.jsx:405-438](../frontend/src/App.jsx#L405-L438) — POSTs `{ prompt, ticker }` (ticker
is whichever watchlist item is currently selected, or `null`). Backend: `run_agent()` in
[graph_agent.py:596-613](../multihop-rag/graph_agent.py#L596-L613), which builds a fresh
`AgentState` and runs it through a **LangGraph `StateGraph`** built once at import time
(`_GRAPH = _build_graph()`, [graph_agent.py:593](../multihop-rag/graph_agent.py#L593)).

### 5.1 The graph shape

```
            ┌────────┐
   query ──▶│ router │
            └───┬────┘
                │ (conditional edge on state["route"])
   ┌────────────┼────────────┬───────────────┐
   ▼            ▼            ▼               ▼
financial_   filing_rag   comparison      external
analysis                                (only if
   │            │            │        EXTERNAL_DATA_URL
   └────────────┴────────────┴────────────┘   is set)
                │
               END
```

Every node is a plain Python function taking/returning dict slices of `AgentState`
(a `TypedDict`, [graph_agent.py:197-207](../multihop-rag/graph_agent.py#L197-L207)) — no
memory, no loops, single pass, exactly one node executes per query.

### 5.2 The router — `router_node` ([graph_agent.py:211-231](../multihop-rag/graph_agent.py#L211-L231))

Pure keyword/regex logic, no LLM call (fast and deterministic by design):

1. Is this a comparison request? (`compare`, `comparison`, `versus`, ` vs `)
   - If yes: try to match **two** distinct companies by name/ticker in the query text
     (`_find_matching_tickers(..., limit=2)`). Only route to `comparison` if two were
     actually found. Otherwise, if the query also has a financial keyword, treat it as an
     **intra-company** comparison (e.g. "compare Apple's revenue and net income") and route
     to `financial_analysis` instead — this is the fix for the bug where a single-company,
     multi-metric question was wrongly demanding two companies.
2. Else, is it financial? (keyword list: revenue, net income, profit, eps, earnings,
   assets, liabilities, balance sheet, income statement, financials) → `financial_analysis`.
3. Else, if `EXTERNAL_DATA_URL` is configured and the query matches consumer-research
   keywords (gen z, consumer sentiment, etc.) → `external`.
4. Otherwise → `filing_rag` (the default: treat it as a qualitative question over indexed
   filing text).

### 5.3 `financial_analysis_node` — numeric, single-company

([graph_agent.py:291-349](../multihop-rag/graph_agent.py#L291-L349))

1. Resolve the target ticker (`_resolve_ticker_or_clarify` — uses the pinned/selected
   ticker if valid, else tries to find one mentioned in the query text, else asks the user
   to clarify instead of guessing).
2. Pull **all** cached years for that ticker from SQLite (`get_financials_for_ticker`).
3. **Scope by year range**, if the query names one — `_extract_year_range()` understands
   `"2023 to 2025"`, `"between 2021 and 2024"`, `"since 2022"`, or two bare years anywhere
   in the sentence. If a range is named but no cached year falls inside it, the node
   returns an honest "no data in that range, years available: ..." message rather than
   silently showing something else.
4. **Scope by metric**, via `_select_metrics()` — matches keywords like "eps", "net
   income"/"profit margin", "revenue"/"sales", "balance sheet" (→ assets+liabilities),
   "income statement" (→ revenue+net_income+eps). If nothing specific is named, it falls
   back to all 5 metrics — an honest default, not a guess.
5. `_build_financial_view()` turns the filtered rows into three parallel representations
   that all come from the exact same numbers: a structured `table` (raw numbers, for
   sorting/CSV export), `chart_data` (same numbers, chart-shaped), and a `markdown_table`
   string (fed to the LLM).
6. The LLM (`_llm()`, a Groq-hosted `qwen/qwen3.6-27b`) is given **only** the markdown
   table and asked to write a short prose narrative — explicitly instructed not to repeat
   the table as text, since the table is already rendered separately. This is a
   grounding technique: the model can't hallucinate numbers because it isn't asked to
   produce any, just narrate ones already computed in Python.

### 5.4 `filing_rag_node` — qualitative, single-company (real RAG)

([graph_agent.py:352-403](../multihop-rag/graph_agent.py#L352-L403))

1. Resolve ticker (same clarify-or-resolve logic as above).
2. Open the Chroma store and retrieve the top **4** chunks filtered to
   `{"company": ticker}` — a metadata filter, not a full-corpus search, so results are
   always scoped to the selected company even though all companies share one Chroma dir.
3. Concatenate the 4 chunks (each tagged with its source URL) into a context block.
4. Ask the LLM to answer **using only that context**, explicitly told to say "I don't know"
   if the context doesn't cover it, and to cite sources.
5. Returns the answer plus a `sources` list (each chunk's raw text + metadata) — this is
   what powers the "Sources" / citations section in the UI. No chart/table for this route.

This is the one place actual vector similarity search happens; there's currently no
year/filing-type filter beyond company, fixed `k=4`, no MMR/reranking, and only one
retrieval hop despite "multihop" in the project name (all documented as open RAG
optimizations in the status report).

### 5.5 `comparison_node` — numeric, two-company

([graph_agent.py:406-517](../multihop-rag/graph_agent.py#L406-L517)) Same shape as
`financial_analysis_node` but pivoted across two tickers: matches exactly two companies by
name from the query, pulls both companies' financials, applies the same year-range
scoping to both, defaults metrics to `["revenue", "net_income"]` if nothing specific was
asked (the "classic" comparison view) or narrows via `_select_metrics()` otherwise, builds
side-by-side table columns (`{ticker}_revenue`, etc.), and asks the LLM to narrate growth
differences from the resulting markdown table.

Known gap: this route triggers whenever two tickers are matched in a "compare" query,
even if the actual ask is qualitative ("compare their risk factors") — there's no
qualitative two-company path yet, so it always produces a numeric revenue/net-income table
regardless of intent (tracked in the status report).

### 5.6 `external_queries_node` — optional third-party data

([graph_agent.py:520-556](../multihop-rag/graph_agent.py#L520-L556)) Only reachable if
`EXTERNAL_DATA_URL` is set in the environment; otherwise the router never selects it. Calls
that URL, feeds the response text to the LLM as "consumer data" context. Not used by
default in this deployment (no such env var is set).

### 5.7 Response shape

Every node returns the same dict shape (`answer`, `sources`, `chart_data`, `chart_meta`,
`table`, `table_columns`), which `app.py`'s `/api/query` route wraps directly into
`QueryResponse` and returns as JSON.

---

## 6. Pipeline: the default (pre-query) overview

Before you've asked anything, the UI already shows a full table + chart for the selected
company. This is a separate, simpler endpoint — `GET /api/financials/{ticker}` → `get_default_financials()` ([graph_agent.py:273-288](../multihop-rag/graph_agent.py#L273-L288)) —
which reuses the exact same `_build_financial_view()` helper as the query path, just
called with **all 5 metrics, no year filtering, no LLM call**. It's pure SQLite → JSON, so
it's fast and has no narrative text.

Frontend: a `useEffect` keyed on `selectedTicker` ([App.jsx:385-403](../frontend/src/App.jsx#L385-L403)) fetches this whenever the watchlist selection changes and stores it in
`defaultFinancials`. The chart/table area shows this default view until a real query
response (`currentResponse`) exists, at which point the query's own (possibly narrower)
chart/table takes over — see the `showQueryChart` / `showDefaultChart` / `showDefaultTable`
booleans at [App.jsx:493-495](../frontend/src/App.jsx#L493-L495).

---

## 7. Frontend structure (`frontend/src/App.jsx`)

Single-file React app, no router, no external state library, no charting library (charts
are hand-rolled inline SVG). Everything lives in one `App` component plus a handful of
presentational sub-components defined in the same file:

- **`DataTable`** — sortable, exportable-to-CSV table for whatever `table`/`table_columns`
  the current view has (shared by both the default overview and query responses).
- **`MetricStrip`** — the KPI row (latest value + YoY delta per metric) shown above the
  default overview.
- **`TrendChart`** — dependency-free SVG line chart with a hover crosshair + tooltip;
  handles both single-series (`trend`) and multi-series (`comparison`) chart_meta shapes,
  coloring lines from a fixed metric-color map or a shared palette by position.
- **`renderFigureLine`** — highlights currency/percentage figures inline in the LLM's prose
  answer (a regex, not markdown parsing) so numbers stand out in the "research note" style
  answer block.

State is all local `useState`: `companies` (watchlist), `selectedTicker`, `currentResponse`
(last query result), `defaultFinancials`, `theme` (persisted to `localStorage` +
mirrored to `<html data-theme>` so dark mode has no flash-of-wrong-theme on load — the
synchronous inline script in [index.html](../frontend/index.html) does the same check
before React even mounts).

Layout: a fixed navbar (brand + theme toggle) → a two-column layout: a `sidebar`
(ticker ingestion form, watchlist with per-item delete, canned "Analysis Templates"
sample questions) and a `main` workspace (context bar showing the active ticker, a chart
column, a content column for table/answer/citations, and the query input bar at the
bottom).

---

## 8. Environment & config

Both processes read from `.env` / Vite env vars — nothing is hardcoded that shouldn't be:

- Backend requires `GROQ_API_KEY` (fails fast at startup, [app.py:35-39](../multihop-rag/app.py#L35-L39)) and `SEC_USER_AGENT` (fails at import time in `sec_client.py` — SEC EDGAR
  requires a real identifying User-Agent header or it throttles/blocks requests).
- `GROQ_MODEL` (default `qwen/qwen3.6-27b`), `EMBEDDING_MODEL` (default
  `all-MiniLM-L6-v2`), `FRONTEND_ORIGINS` (CORS allow-list, defaults to the two Vite dev
  ports), `EXTERNAL_DATA_URL` (optional, gates the `external` route entirely).
- `RELOAD` (default off) — only read by the `python app.py` entrypoint
  ([app.py:165-173](../multihop-rag/app.py#L165-L173)). Set `RELOAD=1` to turn on uvicorn's
  auto-reload for code-editing sessions. Leave it off when ingesting tickers: a reloader
  re-fork that lands mid-ingest, while the native torch/OpenMP thread pool is initializing,
  kills the worker child while the supervisor keeps holding port 8000 — every request then
  hangs with no response (see the status report's 2026-08-30 incident writeup).
- Frontend: `VITE_API_URL` (defaults to `http://localhost:8000`).

`app.py`'s very first executable line (before any other import, [app.py:1-12](../multihop-rag/app.py#L1-L12))
sets `os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")` — a defensive workaround for a
Windows-specific native-library issue (a duplicated OpenMP runtime between `torch` and other
numeric libraries) that must be set before anything transitively imports `torch`.

At FastAPI startup ([app.py:33-44](../multihop-rag/app.py#L33-L44)), the embedding model is
pre-loaded via `get_embeddings()` so the *first* real query doesn't pay that cost — and, as
of 2026-08-30, `get_embeddings()` also runs one real `embed_query("warmup")` call itself
([vector_store.py](../multihop-rag/vector_store.py)), not just model construction. That's not
just a latency optimization: it fixes a real segfault (exit code 139) that happened when the
model's first genuine embedding computation was left to occur lazily, off the main thread,
inside the first `load_ticker` request instead. This startup step is also what can make the
whole server hang if Hugging Face Hub is slow/unreachable, since it runs before `yield` and
blocks every route from serving (see the status report's incident writeups for both).

---

## 9. Running it locally

```
# backend
cd multihop-rag
python -m venv venv && venv\Scripts\activate   # first time
pip install -r requirements.txt
# .env needs GROQ_API_KEY and SEC_USER_AGENT at minimum
python app.py                       # single process, no auto-reload (safe for ingest)
# RELOAD=1 python app.py            # opt in to auto-reload for code-editing sessions only

# frontend
cd frontend
npm install
npm run dev   # http://localhost:5173
```

No Docker, no build step beyond Vite's own dev server, no database migrations to run by
hand — `db_manager.py`'s `init_db()` runs at import time and is idempotent.

---

## 10. Where to look for what

| I want to... | Look at |
|---|---|
| Change how questions get routed | `router_node`, [graph_agent.py:211](../multihop-rag/graph_agent.py#L211) |
| Add a new financial metric | `METRIC_DEFS`/`METRIC_ORDER` in graph_agent.py + the tag map in `parse_financials`, sec_client.py |
| Change chunking/retrieval for filings | `ingest_filings` and `filing_rag_node`, both in their respective files |
| Change the chart/table visuals | `TrendChart`/`DataTable` in App.jsx, styles in `index.css` |
| Add a new API route | app.py, then wire it into App.jsx's fetch calls |
| Understand known bugs / what's next | [project-status-report.md](project-status-report.md) |
