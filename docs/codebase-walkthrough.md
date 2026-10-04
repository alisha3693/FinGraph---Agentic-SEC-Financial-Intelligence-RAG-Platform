# FinGraph / RagProject — Codebase Walkthrough

This is a step-by-step explanation of how the project actually works, grounded in the
current code (not a design doc). Kept live as the project changes — for the detailed
history of *why* each piece looks the way it does, see
[project-status-report.md](project-status-report.md) §0. Read this if you're new to the
codebase and want to understand the pipeline end to end.

---

## 1. What this project is

A financial research tool: you ask a natural-language question — ("what was Apple's revenue
from 2021 to 2025?", "what risks did Apple flag in its 10-K?", "compare Apple, Microsoft,
and Tesla revenue", "what are Apple's risk factors and how did revenue perform?") — naming
whichever company you mean directly in the question. There is no separate "select a
company" step: the backend resolves the company (or companies) from the query text itself,
and if it's never been loaded before, ingests it from **SEC EDGAR** on the spot before
answering. A LangGraph agent then decides whether the question needs hard numbers (SQL-like
lookup), document search (vector RAG over filing text), a comparison across two-or-more
companies, both quantitative and qualitative content merged together, or an external data
source — then returns a narrative answer plus, where relevant, a chart and a table.

Two independently-run processes, orchestrated via Docker Compose ([docker-compose.yml](../docker-compose.yml)):
- **Backend**: FastAPI app in [multihop-rag/](../multihop-rag/), Python, port 8000.
- **Frontend**: React 19 + Vite SPA in [frontend/](../frontend/), port 5173 (dev).

They talk over plain JSON HTTP; there's no shared code between them, no websockets, no
server-side rendering.

---

## 2. High-level architecture

```
┌─────────────────────┐        HTTP/JSON         ┌────────────────────────────────────────────┐
│   React frontend     │ ───────────────────────▶ │              FastAPI app.py                 │
│   (App.jsx)          │ ◀─────────────────────── │                                              │
│                       │                          │  ┌────────────────────────────────────┐    │
│  heading/overview     │                          │  │      graph_agent.py (LangGraph)     │    │
│  driven by the        │                          │  │  router → 5 possible nodes:         │    │
│  `resolved_companies` │                          │  │  financial_analysis · filing_rag ·  │    │
│  a query answers      │                          │  │  comparison (2-6 cos.) · mixed ·    │    │
│  — not a manual        │                         │  │  external (opt-in)                  │    │
│  selection             │                         │  └────────────────────────────────────┘    │
└─────────────────────┘                          │           │           │                      │
                                                   │           ▼           ▼                      │
                                                   │  ┌────────────┐ ┌───────────────┐            │
                                                   │  │ db_manager │ │ Chroma vector  │            │
                                                   │  │ (SQLite,   │ │ store (RAG,    │            │
                                                   │  │ full hist.)│ │ 12 most recent │            │
                                                   │  │            │ │ filings only)  │            │
                                                   │  └────────────┘ └───────────────┘            │
                                                   │           ▲           ▲                      │
                                                   │           └─────┬─────┘                      │
                                                   │              sec_client.py                   │
                                                   │   auto-loads a company resolved from query    │
                                                   │   text — even one never ingested before —     │
                                                   │   by searching the full ~10k-company SEC       │
                                                   │   ticker universe, not just already-loaded     │
                                                   └────────────────────────────────────────────────┘
                                                                    │
                                                                    ▼
                                                          SEC EDGAR (data.sec.gov,
                                                          www.sec.gov) — external, real data
```

Five backend modules, each with one job:

| File | Responsibility |
|---|---|
| [app.py](../multihop-rag/app.py) | FastAPI routes — the only HTTP surface |
| [graph_agent.py](../multihop-rag/graph_agent.py) | LangGraph agent: routing + all 5 answer strategies |
| [sec_client.py](../multihop-rag/sec_client.py) | Talks to SEC EDGAR; ingests filings + facts; resolves a company name/ticker against the full SEC universe |
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

Sourced from SEC's XBRL `companyfacts` endpoint
([`fetch_company_facts`](../multihop-rag/sec_client.py#L126-L129) →
[`parse_financials`](../multihop-rag/sec_client.py#L187)), which returns **every
XBRL-tagged fact the company has ever reported, with no recency limit** — this is why the
table/chart can show 2018 (or earlier) even though the vector store below only covers the
last ~2-3 years of filings.

**Why SQLite instead of pulling these numbers via RAG:** `_filing_text` ([sec_client.py:252-258](../multihop-rag/sec_client.py#L252-L258)) strips a filing down to flattened plain text via `soup.get_text()` — this destroys HTML table structure, so a balance sheet or income statement ends up as a dense run of numbers with no reliable row/column meaning left. Asking an LLM to extract "Apple's 2019 Assets" from that flattened text is a probabilistic guess (it can misattribute a number to the wrong year or line item), whereas the XBRL `companyfacts` API gives an exact, machine-tagged fact (`Assets.units.USD[] → {start, end, val}`) with zero inference involved. RAG retrieval also isn't guaranteed to surface the right chunk at all (top-`k` similarity search, not a targeted lookup), and — as covered below — the vector store doesn't even go back to 2019 for most companies, since it only ever holds the 12 most recent filings. Structured storage also enables features RAG output can't support at all: `MetricStrip`'s YoY % deltas, `TrendChart`'s multi-year lines, `comparison_node`'s per-year pivot table — all real arithmetic over typed floats, not text a model generated. In short: SQLite is used because the numbers pipeline is a **lookup problem** (exact, deterministic, needs typed data), while RAG is reserved for what it's actually good at — fuzzy, contextual **retrieval** of narrative text (§3.2).

### 3.2 Chroma — `multihop-rag/chroma_db/`
A vector store of chunked SEC filing text, embedded with a local sentence-transformers
model. Used only for qualitative/narrative questions ("what risks did they flag"), not for
numeric lookups — those go straight to SQLite instead.

Sourced differently from §3.1: [`_filing_records`](../multihop-rag/sec_client.py#L232-L249)
fetches only the **12 most recent** 10-K/10-Q/8-K filings (`records[:12]`), and
[`_filing_text`](../multihop-rag/sec_client.py#L252-L258) scrapes each one's primary
document **in full** — every section (Business, Risk Factors, MD&A, the financial
statements themselves, boilerplate, signatures), with nothing filtered out beyond
`<script>`/`<style>`/`<noscript>` tags — before it's chunked (1800 chars/220 overlap) and
embedded. There's no per-section extraction: a `section` metadata tag is only a best-effort
regex match for an `"ITEM X..."` heading inside that specific chunk, not a real content
boundary. Net effect: qualitative retrieval is narrow-but-complete (full filing text, short
window), while §3.1 is deep-but-narrow-in-kind (5 numeric fields, full history) — the two
stores' *time coverage* is asymmetric and neither is capped to match the other.

**Why only the 12 most recent filings:** there's no comment in the code justifying this
specific number — `records[:12]` ([sec_client.py:249](../multihop-rag/sec_client.py#L249))
is a bare literal — but an unbounded ingest would be a real problem on three fronts,
confirmed against the actual running store (2026-09-13): (1) **Storage** — 6 companies at
just their 12-filing window already measured 146 MB / ~4,000 chunks (vs. 32 KB for the
*entire* multi-year SQLite table for the same companies); a company's full filing history
back to when XBRL tagging began (~2009-2011) could be 100+ filings, roughly a 9x multiplier
in chunk count and disk space per company. (2) **Embedding/ingest time** — each filing costs
a real HTTP fetch plus a real embedding pass per chunk; the thread-pool comment near
`MAX_CONCURRENT_FILING_DOWNLOADS` is calibrated around "~10s for 12 filings" — ingesting a
full history would multiply that ingest latency well past the point of a responsive
"load ticker" action. (3) **Relevance** — a decade-old Risk Factors or MD&A section is
frequently stale/superseded by how the company describes itself *now*; qualitative
questions ("what risks does this company face") are almost always about current state, not
historical-research questions about 2015. 12 also happens to roughly match "about a year of
filings" for a typical company (1 10-K + 3 10-Q + several 8-Ks), which is likely why that
number specifically was picked, even without an explicit comment saying so.

### 3.3 What each filing type actually contains

`_filing_records` treats 10-K/10-Q/8-K identically — same download, same full-text scrape,
no branching by form type anywhere in the code. The *content* difference between them comes
entirely from what SEC itself requires in each filing type's primary document:

**10-K (annual report)** — the richest of the three. Standard items: 1 Business, 1A Risk
Factors, 1B Unresolved Staff Comments, 2 Properties, 3 Legal Proceedings, 4 Mine Safety
Disclosures, 5 Market for Registrant's Common Equity, 7 MD&A, 7A Quantitative/Qualitative
Disclosures About Market Risk, 8 Financial Statements and Supplementary Data (the actual
balance sheet/income statement/cash flow statement/notes), 9A Controls and Procedures, plus
governance/executive-compensation items (10-14) and exhibits/signatures (15). This is the
only filing type with a full Business Overview and a comprehensive Risk Factors section.

**10-Q (quarterly report)** — lighter. Part I: Item 1 unaudited Financial Statements, Item 2
MD&A (quarterly), Item 3 Market Risk, Item 4 Controls and Procedures. Part II: Item 1 Legal
Proceedings, Item 1A Risk Factors (often just "no material changes from our 10-K" rather
than a full section), Item 2 Unregistered Sales of Equity, Item 5 Other Information, Item 6
Exhibits. **No Item 1 Business at all.**

**8-K (current report)** — no standardized Business/Risk Factors/MD&A items whatsoever.
It's whichever numbered item matches the triggering event — commonly Item 2.02 (Results of
Operations, i.e. an earnings announcement), 1.01 (material agreement), 5.02
(executive/director changes), 7.01 (Reg FD disclosure), 8.01 (other events), 9.01
(financial statements/exhibits). **Important gap:** an 8-K's actual earnings press release
is very often filed as a *separate exhibit* (e.g. `EX-99.1`), not inside the primary
document — since only the primary document is fetched (`record["url"]`, built from
`primaryDocument`, never the exhibit list), the ingested 8-K text can be just a short item
description referencing that exhibit, not the release content itself.

**Net effect:** the vector store's Risk Factors/Business/MD&A coverage comes almost
entirely from whichever 10-Ks fall inside the 12-filing window (plus lighter updates from
10-Qs) — the 8-Ks in that same window contribute narrower, event-specific (and sometimes
exhibit-truncated) text, not those three sections.

Both stores are populated by the same action: **ingesting a ticker** (§4).

---

## 4. Pipeline: ingesting a ticker (`POST /api/load_ticker`)

This is the manual "Ingest Ticker" box in the sidebar. Frontend: `handleLoadTicker` in
[App.jsx](../frontend/src/App.jsx). Backend entry point: `load_company_data(ticker)` in
[sec_client.py](../multihop-rag/sec_client.py).

**This same function also runs automatically mid-query.** When a query names a company
that hasn't been ingested yet, `graph_agent.py`'s `_load_new_ticker_or_error` calls this
exact `load_company_data` — the manual sidebar form and the automatic query-time path are
the same code, not two separate implementations (§5.1 below).

Step by step:

1. **Resolve ticker → CIK.** `resolve_ticker_to_cik` looks up the ticker in SEC's own
   `company_tickers.json` reference file, fetched once and cached forever via
   `@lru_cache(maxsize=1)` on `_ticker_reference()` ([sec_client.py](../multihop-rag/sec_client.py)). If the ticker isn't SEC-recognized, ingestion fails immediately with a clear message. This same cached reference (plus a word index built over it) is what §5.1's auto-load uses to recognize a company that isn't in the local watchlist at all yet.

2. **Fetch structured facts.** `fetch_company_facts(cik)` calls
   `data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` — SEC's XBRL "company facts" API,
   which returns every standardized US-GAAP tag the company has ever reported, **with no
   recency limit** (this is why the quantitative table/chart can show 2018 or earlier even
   though the vector store below only covers the last ~2-3 years of filings).

3. **Parse the 5 tracked metrics.** `parse_financials()` ([sec_client.py](../multihop-rag/sec_client.py)) walks a fixed metric→tag map (e.g. revenue tries
   `RevenueFromContractWithCustomerExcludingAssessedTax`, then `Revenues`, then
   `SalesRevenueNet`, etc., in order — different companies/years use different tags) and
   buckets each fact by the **calendar year its own `end` date falls in** for each fiscal
   year from 2018 through next year — not a literal `fy`/`fp` match, since that's the
   fiscal year *of the filing the value was reported in*, not necessarily the year the
   value covers (a 10-K's prior-year comparative figures carry the filing's own `fy`), and
   not a literal Jan-1–Dec-31 window either, since non-calendar-fiscal-year companies
   (Microsoft's FY runs July–June) would never match one. Result:
   `{year: {revenue, net_income, eps, assets, liabilities}}`.

4. **Save to SQLite.** `save_financials()` upserts the company row and one `financials` row
   per year ([db_manager.py](../multihop-rag/db_manager.py)).

5. **Ingest filings for RAG.** `ingest_filings(ticker, cik, name)` ([sec_client.py](../multihop-rag/sec_client.py)):
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
   → frontend shows it in `tickerMessage` and, on success, refreshes the watchlist and sets
   the newly-ingested company as the active/focused one (`activeCompanies`, §7).

**Why first-time ingestion is slow (15–30s typical):** almost all of the time is the 12
sequential-per-batch HTTP round-trips to SEC (rate-limited, real network latency) plus
local CPU-bound embedding of every chunk. **Re-ingesting an already-loaded, unchanged
ticker is now fast** (a couple of metadata reads, no downloads, no re-embedding) thanks to
the `accession_number` diffing above — only a genuinely new filing since the last ingest
triggers real work.

---

## 5. Pipeline: asking a question (`POST /api/query`)

This is the core of the app. Frontend: `handleSubmit` in
[App.jsx](../frontend/src/App.jsx) — POSTs `{ prompt }` only, no ticker (§5.1 explains
why). Backend: `run_agent()` in [graph_agent.py](../multihop-rag/graph_agent.py), which
builds a fresh `AgentState` and runs it through a **LangGraph `StateGraph`** built once at
import time (`_GRAPH = _build_graph()`).

### 5.1 Company resolution — no manual selection, auto-load from the full SEC universe

There is no "selected ticker" concept anywhere in the query path. Every node that needs a
company calls `_resolve_ticker_or_clarify` (single company) or `_find_companies`
(comparison, up to `MAX_COMPARISON_COMPANIES`), which:

1. Tries to match a company by name/ticker against the **already-loaded** watchlist first
   (typo-tolerant fuzzy matching via `difflib`).
2. If nothing matches there, falls back to `find_unloaded_ticker_candidates()`
   ([sec_client.py](../multihop-rag/sec_client.py)) — a search over the **full
   ~10k-company SEC ticker reference**, not just what's already ingested. Matching requires
   the word to actually be capitalized/uppercase **as typed in the query** — the natural
   English signal for a proper noun (a full-uppercase ticker symbol like `AMD`, or a
   Title-Case company-name word like `Netflix`). This was tightened after two real false
   positives in testing: matching lowercased words let a lowercase "net" (from "net
   income") auto-load Cloudflare's ticker `NET`, and a lowercase "trend" (from "the general
   trend") auto-load an obscure filer literally named "High-Trend International Group" —
   being the *only* SEC company whose name contains a word isn't a safe signal on its own,
   since the reference includes many tiny/obscure filers with ordinary-English-word names.
3. If a resolved ticker isn't already loaded, `_load_new_ticker_or_error` calls the exact
   same `load_company_data(ticker)` from §4 — auto-ingesting it from SEC EDGAR before
   answering (the query blocks on a real ingest, ~15-40s, the first time a company is
   mentioned).
4. If nothing can be resolved (no company named, or genuinely ambiguous with several
   companies loaded), the node returns an honest clarification instead of guessing.

Every successful node response includes a `resolved_companies: [{ticker, name}, ...]`
field — the frontend uses this to update the context-bar heading and fetch that company's
full-metric overview (§6), replacing what used to be a sidebar click.

### 5.2 The graph shape

```
            ┌────────┐
   query ──▶│ router │
            └───┬────┘
                │ (conditional edge on state["route"])
   ┌────────────┼─────────────┬─────────────┬───────────────┐
   ▼            ▼             ▼             ▼               ▼
financial_   filing_rag   comparison       mixed         external
analysis                  (2 to          (both quant     (only if
                     MAX_COMPARISON_    + qual intent    EXTERNAL_DATA_URL
                     COMPANIES)          detected)        is set)
   │            │             │             │               │
   └────────────┴─────────────┴─────────────┴───────────────┘
                             │
                            END
```

Every node is a plain Python function taking/returning dict slices of `AgentState`
(a `TypedDict`, [graph_agent.py](../multihop-rag/graph_agent.py)) — no memory, no loops.
`mixed` is the one exception to "exactly one node executes per query": it calls
`financial_analysis_node`/`filing_rag_node` directly as plain Python function calls from
inside itself (§5.7), not via a second graph traversal.

### 5.3 The router — `router_node`

Pure keyword/regex logic, no LLM call (fast and deterministic by design):

1. Is this a comparison request? (`compare`, `comparison`, `versus`, ` vs `, `difference`,
   `better`)
   - If yes: try to match **two or more** distinct companies by name/ticker in the query
     text via `_find_companies` (§5.1's resolution logic, so this also reaches brand-new,
     not-yet-loaded companies), up to `MAX_COMPARISON_COMPANIES`. Only route to
     `comparison` if two-or-more were actually found. Otherwise, if the query also has a
     financial keyword, treat it as an **intra-company** comparison (e.g. "compare Apple's
     revenue and net income") and route to `financial_analysis` instead.
2. Else, does the query match both `FINANCIAL_KEYWORDS` (revenue, net income, profit, eps,
   earnings, assets, liabilities, balance sheet, income statement, financials) **and**
   `QUALITATIVE_KEYWORDS` (risk, strategy, litigation, competitive, supply chain,
   regulatory, workforce, cybersecurity, etc. — deliberately excludes generic verbs like
   "explain"/"discuss", which previously false-triggered this route for purely numeric
   questions phrased as explanations) → `mixed`.
3. Else, is it financial (matches `FINANCIAL_KEYWORDS` alone) → `financial_analysis`.
4. Else, if `EXTERNAL_DATA_URL` is configured and the query matches consumer-research
   keywords (gen z, consumer sentiment, etc.) → `external`.
5. Otherwise → `filing_rag` (the default: treat it as a qualitative question over indexed
   filing text).

### 5.4 `financial_analysis_node` — numeric, single-company

([graph_agent.py](../multihop-rag/graph_agent.py))

1. Resolve the target ticker via `_resolve_ticker_or_clarify` (§5.1 — auto-loads if new).
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
6. The LLM (`_llm()`, a Groq-hosted `qwen/qwen3.8-27b`) is given **only** the markdown
   table and asked to write a short bulleted narrative, citing the single source as `[1]` —
   explicitly instructed not to repeat the table as text, since it's already rendered
   separately. This is a grounding technique: the model can't hallucinate numbers because
   it isn't asked to produce any, just narrate ones already computed in Python. When
   reached via `mixed_node` (`state["route"] == "mixed"`), an extra prompt note tells it
   not to flag that it lacks qualitative information — a sibling section covers that.

### 5.5 `filing_rag_node` — qualitative, single-company (real RAG)

([graph_agent.py](../multihop-rag/graph_agent.py))

1. Resolve ticker (same §5.1 logic).
2. Open the Chroma store and retrieve the top **4** chunks filtered to
   `{"company": ticker}` — a metadata filter, not a full-corpus search, so results are
   always scoped to the resolved company even though all companies share one Chroma dir.
   No date/year filter exists, so a question about a specific past year outside the
   12-most-recent-filings window (§3.2/3.3) still retrieves *something* — just nothing
   actually from that year, with no indication anything's mismatched.
3. The 4 chunks are numbered `[1]`-`[4]` in the prompt context (matching the order of the
   `sources` list returned to the frontend), so the model cites a chunk by its bracket
   number instead of restating its full source URL.
4. Ask the LLM to answer as a bulleted list, grounded only in that context, citing by
   number — explicitly told to say "I don't know" if the context doesn't cover it (unless
   reached via `mixed_node`, where a note tells it not to flag missing *quantitative*
   information instead — a sibling section covers that).
5. Returns the answer plus a `sources` list (each chunk's raw text + metadata) — this is
   what powers the "Sources" / citations section in the UI. No chart/table for this route.

This is the one place actual vector similarity search happens; there's currently no
year/filing-type filter beyond company, fixed `k=4`, no MMR/reranking, and only one
retrieval hop despite "multihop" in the project name (all documented as open RAG
optimizations in the status report).

### 5.6 `comparison_node` — numeric, N-way (2 to `MAX_COMPARISON_COMPANIES`)

([graph_agent.py](../multihop-rag/graph_agent.py)) Same shape as `financial_analysis_node`
but pivoted across a `tickers: List[str]` of any length from 2 up to
`MAX_COMPARISON_COMPANIES = 6` — not a hardcoded pair. Matches every company named via
§5.1's resolution (auto-loading any that aren't loaded yet), pulls each one's financials,
drops any ticker that ends up with no usable data as long as at least two survive, applies
the same year-range scoping to all of them, defaults metrics to `["revenue", "net_income"]`
if nothing specific was asked (the "classic" comparison view) or narrows via
`_select_metrics()` otherwise, builds one column-pair per company in the table, and asks
the LLM to narrate differences across all of them from the resulting markdown table.

Known gap, still open: this route triggers whenever two-or-more tickers are matched in a
"compare" query, even if the actual ask is qualitative ("compare their risk factors") —
`router_node` checks for comparison intent *before* the quant+qual `mixed` check (§5.3), so
it always produces a numeric table regardless of intent. `mixed_node` doesn't cover this
either — it's single-company only, reusing `financial_analysis_node` and
`filing_rag_node`, neither of which handles multiple tickers (tracked in the status
report).

### 5.7 `mixed_node` — both quantitative and qualitative, single-company

([graph_agent.py](../multihop-rag/graph_agent.py)) Runs `financial_analysis_node` and
`filing_rag_node` **directly as plain Python function calls** against the same `state` (not
via a genuine LangGraph fan-out — a version that dispatches to both as real parallel graph
nodes, roughly halving latency, was discussed and deferred; see the status report), then
merges their output:

1. Resolves (and auto-loads, if needed) the ticker **once** up front, before calling either
   sub-node — both would otherwise independently trigger their own auto-load for an
   unfamiliar company, roughly doubling the ~15-40s SEC ingest cost for no benefit.
2. If both sub-nodes produced real content (financial: non-empty `table`; qualitative:
   non-empty `sources`), combines them into one answer — `"Financial trend:"` bullets, then
   `"Qualitative analysis:"` bullets — with the qualitative half's `[N]` citation markers
   shifted by a fixed offset (the financial half's source count, always 1) so they still
   point at the right entry once the two `sources` lists are concatenated into one list.
3. If only one half produced real content, that half is returned alone rather than padding
   the response with the other's fallback/error text.

### 5.8 `external_queries_node` — optional third-party data

([graph_agent.py](../multihop-rag/graph_agent.py)) Only reachable if `EXTERNAL_DATA_URL` is
set in the environment; otherwise the router never selects it. Calls that URL, feeds the
response text to the LLM as "consumer data" context. Not used by default in this
deployment (no such env var is set).

### 5.9 Response shape

Every node returns the same dict shape (`answer`, `sources`, `chart_data`, `chart_meta`,
`table`, `table_columns`, `resolved_companies`), which `app.py`'s `/api/query` route wraps
directly into `QueryResponse` and returns as JSON.

---

## 6. Pipeline: the full-metric overview alongside a query

The UI also shows a full 5-metric table + `MetricStrip` KPI row for whichever single
company is currently in focus — a separate, simpler endpoint from the query path:
`GET /api/financials/{ticker}` → `get_default_financials()`
([graph_agent.py](../multihop-rag/graph_agent.py)), which reuses the exact same
`_build_financial_view()` helper as `financial_analysis_node`, just called with **all 5
metrics, no year filtering, no LLM call**. Pure SQLite → JSON, fast, no narrative text.

Frontend: a `useEffect` keyed on `activeTicker` — derived from `activeCompanies` (§7),
which itself is set from `resolved_companies` on every query response, not a sidebar click
— fetches this whenever the resolved company changes and stores it in `defaultFinancials`.
Unlike before the redesign, the `MetricStrip` now stays visible **alongside** a query's own
answer instead of disappearing the moment `currentResponse` exists — it acts as a
persistent "current company snapshot" that tracks whatever the latest query resolved to.
The chart column falls back to this default chart only when the query's own response has
no `chart_data` of its own (e.g. a `filing_rag`-routed qualitative answer) — see the
`showQueryChart`/`showDefaultChart`/`showDefaultTable` booleans in
[App.jsx](../frontend/src/App.jsx).

---

## 7. Frontend structure (`frontend/src/App.jsx`)

Single-file React app, no router, no external state library, no charting library (charts
are hand-rolled inline SVG). Everything lives in one `App` component plus a handful of
presentational sub-components defined in the same file:

- **`DataTable`** — sortable, exportable-to-CSV table for whatever `table`/`table_columns`
  the current view has (shared by both the default overview and query responses).
- **`MetricStrip`** — the KPI row (latest value + YoY delta per metric), now shown
  alongside a query's own answer too, not just in a no-query default state (§6).
- **`TrendChart`** — dependency-free SVG line chart with a hover crosshair + tooltip;
  handles both single-series (`trend`) and multi-series (`comparison`, any number of
  companies — coloring is generic-by-array-position, so N-way comparisons render correctly
  with zero chart code changes) chart_meta shapes.
- **`renderFigureLine`** — highlights currency/percentage figures inline in the LLM's
  bulleted answer (a regex, not markdown parsing) so numbers stand out.

State is all local `useState`. The company/companies "in focus" is `activeCompanies` — an
array of `{ticker, name}`, one entry for a single-company query, two-or-more for a
comparison — set from a query response's `resolved_companies` field (or from the manual
"Ingest Ticker" form's result), **not** from a watchlist click (there is no click-to-select
anymore). `activeTicker` (derived: the single ticker when `activeCompanies.length === 1`)
drives the §6 overview fetch. Other state: `companies` (watchlist, display-only),
`currentResponse` (last query result), `defaultFinancials`, `promptHistory` (last 5
questions, cached to `localStorage`, click-to-fill the search bar — not auto-submit),
`theme` (persisted to `localStorage` + mirrored to `<html data-theme>` so dark mode has no
flash-of-wrong-theme on load — the synchronous inline script in
[index.html](../frontend/index.html) does the same check before React even mounts).

Layout: a fixed navbar (brand + theme toggle) → a two-column layout: a `sidebar` (ticker
ingestion form, an inert display-only watchlist with per-item delete, and "Recent Prompts"
— the last 5 questions asked) and a `main` workspace (context bar showing whichever
company/companies the latest query resolved — or `"CompanyA vs CompanyB"` for a comparison
— a chart column, a content column for table/answer/citations, and the query input bar at
the bottom). Navbar and query bar stay pinned; only the content column scrolls for long
answers.

---

## 8. Environment & config

Both processes read from `.env` / Vite env vars — nothing is hardcoded that shouldn't be:

- Backend requires `GROQ_API_KEY` (fails fast at startup, [app.py:35-39](../multihop-rag/app.py#L35-L39)) and `SEC_USER_AGENT` (fails at import time in `sec_client.py` — SEC EDGAR
  requires a real identifying User-Agent header or it throttles/blocks requests).
- `GROQ_MODEL` (default `qwen/qwen3.8-27b`), `EMBEDDING_MODEL` (default
  `all-MiniLM-L6-v2`), `FRONTEND_ORIGINS` (CORS allow-list, defaults to the two Vite dev
  ports), `EXTERNAL_DATA_URL` (optional, gates the `external` route entirely).
- `RELOAD` — only read by the `python app.py` entrypoint ([app.py](../multihop-rag/app.py)),
  defaults to off. **[docker-compose.yml](../docker-compose.yml) explicitly sets
  `RELOAD=1`** for the backend container — the original reason to leave it off (a
  reloader re-fork landing mid-ingest killing the worker child while native
  torch/OpenMP initializes) was a Windows-host-specific failure mode that doesn't reproduce
  inside the Linux container, so hot-reload is safely re-enabled there for a normal dev
  loop (see the status report's 2026-08-30 incident writeup for the original bug).
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

**Docker Compose is how this project actually runs** ([docker-compose.yml](../docker-compose.yml)) — two containers, both running live source via bind mounts, so it behaves
like a normal `python app.py` + `npm run dev` workflow with consistent, disposable
environments instead of a host Python/Node install:

```
# from the project root — needs a .env with GROQ_API_KEY and SEC_USER_AGENT at minimum
docker compose up --build   # first run, or after a requirements.txt/package.json change
docker compose up           # subsequent runs
docker compose down         # stop + remove containers (bind-mounted data is untouched)
```

- **backend** container: FastAPI on `http://localhost:8000`, source bind-mounted from
  `./multihop-rag`, `RELOAD=1` (safe inside the Linux container — see §8).
- **frontend** container: Vite dev server on `http://localhost:5173`, source bind-mounted
  from `./frontend`.

**Known Windows/Docker gotcha:** Vite's file watcher inside the container doesn't reliably
detect edits made from the Windows host (a bind-mount inotify limitation). If a frontend
change doesn't show up after a browser refresh, run `docker compose restart frontend`, then
hard-refresh. The backend picks up its own edits automatically (`RELOAD=1`).

No database migrations to run by hand — `db_manager.py`'s `init_db()` runs at import time
and is idempotent. A bare (non-Docker) `python app.py` / `npm run dev` workflow still works
if you'd rather not use Docker — see [app.py](../multihop-rag/app.py)'s `__main__` block
and [frontend/package.json](../frontend/package.json) — but every fix/feature in the
project's changelog ([project-status-report.md](project-status-report.md) §0) has been
developed and verified against the Docker Compose setup specifically.

---

## 10. Where to look for what

| I want to... | Look at |
|---|---|
| Change how questions get routed | `router_node`, `graph_agent.py` |
| Change which words trigger auto-load/comparison/mixed | `FINANCIAL_KEYWORDS`/`QUALITATIVE_KEYWORDS`/`MAX_COMPARISON_COMPANIES`, `graph_agent.py` |
| Change how an unfamiliar company gets recognized/auto-loaded | `find_unloaded_ticker_candidates`, `sec_client.py` |
| Add a new financial metric | `METRIC_DEFS`/`METRIC_ORDER` in graph_agent.py + the tag map in `parse_financials`, sec_client.py |
| Change chunking/retrieval for filings | `ingest_filings` and `filing_rag_node`, both in their respective files |
| Change the chart/table visuals | `TrendChart`/`DataTable` in App.jsx, styles in `index.css` |
| Change how the mixed-answer merge works | `mixed_node`, `graph_agent.py` |
| Add a new API route | app.py, then wire it into App.jsx's fetch calls |
| Understand known bugs / what's next | [project-status-report.md](project-status-report.md) |
