import os
import re
import json
import difflib
import logging
from functools import lru_cache
from typing import Dict, Any, List, Optional, Tuple
from typing_extensions import TypedDict
from dotenv import load_dotenv

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_chroma import Chroma
from langgraph.graph import StateGraph, END
import requests
from db_manager import get_company_facts_source, get_financials_for_ticker, get_companies
from vector_store import get_embeddings, VECTOR_DB_DIR
from sec_client import find_unloaded_ticker_candidates, resolve_ticker_to_cik

logger = logging.getLogger(__name__)

# Every financial metric the DB can produce, in a fixed display order. "unit" drives both
# formatting (frontend) and $B-vs-raw division (backend) — keep the two in lockstep.
METRIC_DEFS = {
    "revenue": {"label": "Revenue", "unit": "$B"},
    "net_income": {"label": "Net Income", "unit": "$B"},
    "eps": {"label": "EPS", "unit": "$"},
    "assets": {"label": "Assets", "unit": "$B"},
    "liabilities": {"label": "Liabilities", "unit": "$B"},
}
METRIC_ORDER = ["revenue", "net_income", "eps", "assets", "liabilities"]
# Comparisons support any number of already-ingested companies from 2 up to this cap —
# beyond it a multi-line chart/table stops being readable.
MAX_COMPARISON_COMPANIES = 6
METRIC_KEYWORDS = {
    "eps": ["eps", "earnings per share", "per share"],
    "net_income": ["net income", "net profit", "bottom line", "profitability", "profit margin", " profit"],
    "revenue": ["revenue", "sales", "top line", "topline"],
    "assets": ["assets", "total assets"],
    "liabilities": ["liabilities", "total liabilities"],
}


def _select_metrics(query: str, default: List[str]) -> List[str]:
    """Pick which financial metrics the question is actually asking about, so the table
    and chart reflect the prompt instead of always dumping every column. Falls back to
    `default` only when nothing specific was named — that's an honest "you didn't say,
    so here's the fuller picture" default, not a guess at intent."""
    q = query.lower()
    selected = set()

    if "balance sheet" in q:
        selected.update(["assets", "liabilities"])
    if "income statement" in q:
        selected.update(["revenue", "net_income", "eps"])
    for metric, keywords in METRIC_KEYWORDS.items():
        if any(k in q for k in keywords):
            selected.add(metric)

    if not selected:
        return list(default)
    return [m for m in METRIC_ORDER if m in selected]


def _format_value(value: float, unit: str) -> str:
    if unit == "$B":
        return f"${value:.2f}B"
    if unit == "$":
        return f"${value:.2f}"
    return str(value)


_YEAR_SPAN_RE = re.compile(r"(20\d{2})\s*(?:-|to|through|–|—)\s*(20\d{2})")
_YEAR_BETWEEN_RE = re.compile(r"between\s+(20\d{2})\s+and\s+(20\d{2})")
_YEAR_SINCE_RE = re.compile(r"(?:since|from|after)\s+(20\d{2})\b(?!\s*(?:-|to|through))")
_YEAR_ANY_RE = re.compile(r"\b(20\d{2})\b")


def _extract_year_range(query: str) -> Optional[Tuple[int, int]]:
    """Pull an explicit fiscal-year window out of the question ("2023 to 2025", "between
    2021 and 2024", "since 2022", or just two bare years) so trend/comparison views can be
    scoped to it instead of always dumping the full cached history."""
    q = query.lower()

    m = _YEAR_SPAN_RE.search(q) or _YEAR_BETWEEN_RE.search(q)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (min(a, b), max(a, b))

    m = _YEAR_SINCE_RE.search(q)
    if m:
        return (int(m.group(1)), 9999)

    years = [int(y) for y in _YEAR_ANY_RE.findall(q)]
    if len(years) == 1:
        return (years[0], years[0])
    if len(years) >= 2:
        return (min(years), max(years))

    return None


def _match_company(query_words: set, co_ticker: str, name_words: list) -> bool:
    """Match a company against a tokenized query using exact OR fuzzy word matching.
    Handles any casing (words are pre-lowercased), typos, and partial names.
    Operates on whole words only so short tickers (e.g. 'F', 'T') can't match as a
    substring of an unrelated word.
    """
    if co_ticker in query_words:
        return True
    if any(word in query_words for word in name_words):
        return True
    # Fuzzy match — catches typos like 'tesle', 'amzon', 'microsft'
    candidates = [w for w in query_words if len(w) > 2]
    for name_word in name_words:
        if difflib.get_close_matches(name_word, candidates, n=1, cutoff=0.82):
            return True
    return False


def _find_matching_tickers(query: str, companies: List[Dict[str, Any]], limit: Optional[int] = None) -> List[str]:
    """Return tickers (in DB order) whose name/ticker is referenced in the query text."""
    query_words = set(re.findall(r"[a-z0-9]+", query.lower()))
    matched = []
    for co in companies:
        co_ticker = co["ticker"].lower()
        name_words = [re.sub(r"[^a-z]", "", w) for w in co["name"].lower().split()]
        name_words = [w for w in name_words if len(w) > 3]
        if _match_company(query_words, co_ticker, name_words):
            matched.append(co["ticker"].upper())
            if limit and len(matched) >= limit:
                break
    return matched


def _company_name_for_ticker(ticker: str) -> str:
    """Return the canonical displayed company name for a loaded ticker."""
    for co in get_companies():
        if co["ticker"].upper() == ticker.upper():
            return co["name"]
    return ticker.upper()


def _find_companies(query: str, all_companies: List[Dict[str, Any]], limit: int) -> List[str]:
    """Tickers referenced in the query text, up to `limit` — already-loaded companies first
    (typo-tolerant), then the full SEC universe for anything not yet loaded. Never ingests
    anything — a returned ticker not already in `all_companies` is only ever used so a
    caller can name the actual company in a "not ingested yet" response (see
    _not_ingested_response); companies are added exclusively via the sidebar's manual
    "Ingest Ticker" form."""
    matched = _find_matching_tickers(query, all_companies, limit=limit)
    if len(matched) < limit:
        loaded = {co["ticker"].upper() for co in all_companies}
        extra = find_unloaded_ticker_candidates(query, exclude=loaded | set(matched))
        matched += extra[: limit - len(matched)]
    return matched


def _not_ingested_response(tickers: List[str]) -> Dict[str, Any]:
    """Honest "add it first" response for a company recognized from query text (via the
    full SEC universe, see _find_companies) that isn't in the ingested watchlist yet.
    Companies are only ever added via the sidebar's manual "Ingest Ticker" form (app.py) —
    nothing here or upstream triggers an ingest on its own."""
    labels = []
    for t in tickers:
        _, name = resolve_ticker_to_cik(t)
        labels.append(f"{name} ({t})" if name else t)
    verb = "haven't" if len(labels) > 1 else "hasn't"
    pronoun = "them" if len(labels) > 1 else "it"
    return {
        "answer": f"{', '.join(labels)} {verb} been ingested yet. Add {pronoun} from the "
                  "'Ingest Ticker' sidebar first, then ask again.",
        "sources": [],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
    }


def _resolve_ticker_or_clarify(state: "AgentState") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a single target ticker for this query from the query text alone, considering
    every SEC-registered company (not just already-loaded ones) — not to auto-load it, only
    so an honest "not ingested yet" response can name the actual company instead of a vague
    non-answer. Deliberately ignores any sidebar/UI selection — a query about an
    already-loaded company should still answer correctly regardless of what's selected."""
    all_companies = get_companies()
    loaded = {co["ticker"].upper() for co in all_companies}

    matched = _find_companies(state["query"], all_companies, limit=1)
    if matched:
        ticker = matched[0]
        if ticker not in loaded:
            return None, _not_ingested_response([ticker])
        return ticker, None

    if not all_companies:
        return None, {
            "answer": "No companies are loaded yet. Use the 'Ingest Ticker' sidebar to add one first.",
            "sources": [],
            "chart_data": [],
            "chart_meta": {},
            "table": [],
            "table_columns": [],
        }
    if len(all_companies) == 1:
        return all_companies[0]["ticker"].upper(), None

    names = ", ".join(f"{co['ticker']} ({co['name']})" for co in all_companies)
    return None, {
        "answer": f"I couldn't tell which company you meant. Loaded companies: {names}. "
                  "Mention one by name or ticker in your question.",
        "sources": [],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
    }


@lru_cache(maxsize=1)
def _llm() -> ChatGroq:
    # qwen3.8 is a reasoning model. Two settings matter here:
    # - reasoning_format="hidden": without it, the raw <think>...</think> chain-of-thought
    #   leaks straight into .content — this was the main reason answers looked broken.
    # - reasoning_effort="none": these are grounded summarization/formatting tasks over data
    #   we've already computed, not multi-step reasoning problems. Leaving effort at its
    #   default burns ~2000 hidden reasoning tokens per call (measured) and can exhaust
    #   max_tokens before any visible answer is emitted at all, returning empty content.
    #   "none" measured at 85 tokens and 0.2s for the same prompt — faster, cheaper, reliable.
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        temperature=0,
        reasoning_format="hidden",
        reasoning_effort="none",
        # Groq's on_demand tier enforces an output-tokens-per-minute (OTPM) cap of 1000
        # for this model — it rejects the request outright (429) if max_tokens alone
        # exceeds that, before any generation happens. 1500 blew past it every call
        # (measured actual usage here is ~85 tokens with reasoning_effort="none"), so
        # this stays a generous-but-safe ceiling under the hard 1000 limit.
        max_tokens=900,
    )


# State definition
class AgentState(TypedDict):
    query: str
    route: str
    # Set by router_node; read by resolve_node's fan-out decision and by
    # financial_analysis_node/filing_rag_node's mixed_note. Independent booleans instead of
    # a single exclusive route string, since a query can need either, both, or (in
    # principle) neither.
    needs_financial: bool
    needs_qualitative: bool
    # True when the query names 2+ companies with comparison phrasing — tells
    # quantitative_node/qualitative_node to run their multi-company variant instead of the
    # single-ticker one. `tickers` (set by resolve_node) holds the resolved company list in
    # that case; unused/absent for a single-company query.
    is_comparison: bool
    tickers: Optional[List[str]]
    # Set by resolve_node before the quantitative/qualitative fan-out runs. When set, both
    # branches are skipped and mixed_node returns this directly — see _build_graph.
    clarification: Optional[Dict[str, Any]]
    # Written by quantitative_node/qualitative_node instead of the shared answer/sources/etc.
    # keys below, so the two can run in the same parallel superstep without both trying to
    # write the same state channel. mixed_node reads these back and merges them.
    financial_result: Optional[Dict[str, Any]]
    qualitative_result: Optional[Dict[str, Any]]
    answer: str
    sources: List[Dict[str, Any]]
    chart_data: List[Dict[str, Any]]
    chart_meta: Dict[str, Any]
    table: List[Dict[str, Any]]
    table_columns: List[Dict[str, str]]
    resolved_companies: List[Dict[str, str]]


# Router definition
def router_node(state: AgentState) -> Dict[str, Any]:
    """Entry point: a single cheap, deterministic check — does this query name 2+
    companies with comparison phrasing ("compare", "versus", etc.)? That's real entity
    extraction (matching ticker/company names against the SEC universe), not fuzzy intent,
    so it stays a fast keyword+lookup check here rather than an LLM call. The fuzzier
    question of *what kind* of answer the query actually needs — numeric, qualitative,
    both, or external — used to also be decided here via FINANCIAL_KEYWORDS/
    QUALITATIVE_KEYWORDS/EXTERNAL_KEYWORDS substring lists; that's now classify_node's job
    (see _build_graph), via a real LLM judgment call instead of keyword matching."""
    query = state["query"].lower()
    wants_comparison = any(k in query for k in ["compare", "comparison", "versus", " vs ", "difference", "better"])
    is_comparison = False
    if wants_comparison:
        # "Compare X and Y" only means the cross-company path when two-or-more companies
        # are actually named — "Compare Apple's revenue and net income" names one company
        # and two metrics, an intra-company question, not a cross-company one. Checks the
        # full SEC universe, not just already-loaded companies, so a comparison naming a
        # not-yet-ingested company is still flagged as a comparison — resolve_node then
        # reports it as not ingested rather than silently falling back to a single-company
        # answer.
        matched = _find_companies(state["query"], get_companies(), limit=MAX_COMPARISON_COMPANIES)
        is_comparison = len(matched) >= 2
    return {"is_comparison": is_comparison}


def classify_node(state: AgentState) -> Dict[str, Any]:
    """LLM-based intent classifier sitting between router_node and the quantitative/
    qualitative/external nodes (see _build_graph) — replaces the old FINANCIAL_KEYWORDS/
    QUALITATIVE_KEYWORDS/EXTERNAL_KEYWORDS substring matching with an actual judgment call
    about what the question needs. Returns independent needs_financial/needs_qualitative
    booleans (a query can need either, both, or neither) instead of an exclusive route
    string, so the fan-out after this node (route_after_classify) can activate any
    combination of branches — including a genuinely mixed query, which the old keyword
    router could only detect via one specific "both lists matched" case."""
    query = state["query"]
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "Classify what this question about a public company needs, along three "
        "independent yes/no dimensions:\n"
        "- needs_financial: the actual reported figures/trend for one of these 5 structured "
        "metrics: revenue, net income, EPS, assets, liabilities (e.g. \"what was revenue,\" "
        "\"how has net income grown,\" \"compare their EPS\"). A question asking how "
        "management *discusses or explains* a financial concept in narrative/MD&A form — "
        "liquidity, capital resources, cash flow, critical accounting estimates — is "
        "needs_qualitative, not needs_financial, unless it also explicitly asks for the "
        "underlying figures or a trend.\n"
        "- needs_qualitative: narrative text from SEC filings (risk factors, strategy, "
        "outlook, litigation, competition, MD&A discussion, business overview, workforce, "
        "cybersecurity, supply chain, regulatory, intellectual property, accounting "
        "estimates/judgments, etc.)\n"
        "- wants_external: external CONSUMER/market-research data entirely outside SEC "
        "filings (product reviews, brand sentiment, market-share surveys) — false for "
        "anything answerable from SEC filings\n\n"
        "A question can be true on more than one dimension at once (e.g. a question about "
        "both risk factors and revenue is true on both needs_financial and "
        "needs_qualitative). If it names neither a specific number nor a specific "
        "narrative topic, set needs_qualitative true and the other two false — an honest "
        "\"give me the fuller picture\" default, not a guess.\n\n"
        "Question: {question}\n\n"
        "Respond with ONLY a JSON object, no other text, no markdown fences, in exactly "
        "this shape: {{\"needs_financial\": true or false, \"needs_qualitative\": true or "
        "false, \"wants_external\": true or false}}"
    )
    chain = prompt | llm | StrOutputParser()

    # Same safe default the old keyword router fell back to (qualitative search) when
    # nothing else matched — used here if the LLM call or its JSON comes back unusable, so
    # a classification hiccup degrades to a reasonable answer instead of failing the query.
    needs_financial, needs_qualitative, wants_external = False, True, False
    try:
        raw = chain.invoke({"question": query})
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
        needs_financial = bool(parsed.get("needs_financial", False))
        needs_qualitative = bool(parsed.get("needs_qualitative", False))
        wants_external = bool(parsed.get("wants_external", False))
    except Exception:
        logger.exception("Intent classification failed; defaulting to qualitative search")

    if wants_external and not needs_financial and not needs_qualitative and os.getenv("EXTERNAL_DATA_URL"):
        # Only route to the external provider if one is actually configured — otherwise
        # fall through to qualitative search rather than dead-ending on an unconfigured
        # feature.
        return {"route": "external", "needs_financial": False, "needs_qualitative": False}

    if not needs_financial and not needs_qualitative:
        # Neither flag ended up true — whether from a parsed {false, false} response (the
        # model doesn't always follow the "default to qualitative" instruction above) or a
        # wants_external classification with no provider configured. route_after_resolve
        # (see _build_graph) fans out to whichever of quantitative/qualitative is true, so
        # leaving both false here would return an empty branch list and dead-end the query
        # with a blank answer and no error — reproduced live before this check existed.
        # Comparisons default to numeric (matches the old comparison_node's fallback);
        # everything else defaults to qualitative, same as the old keyword router's default.
        if state.get("is_comparison"):
            needs_financial = True
        else:
            needs_qualitative = True

    return {"route": "quant_qual", "needs_financial": needs_financial, "needs_qualitative": needs_qualitative}


def _build_financial_view(financials: List[Dict[str, Any]], metrics: List[str]) -> Dict[str, Any]:
    """Shared table/chart builder for a ticker's annual financials, given which metric
    columns to include. Used both by the query-driven trend route (which narrows metrics
    to what was asked) and by the default overview endpoint (which always passes every
    metric), so both views stay derived from the exact same rows and formatting."""
    table_columns = [{"key": "year", "label": "Fiscal Year", "unit": ""}]
    table_columns += [{"key": m, "label": METRIC_DEFS[m]["label"], "unit": METRIC_DEFS[m]["unit"]} for m in metrics]

    # Structured rows carry the real numeric values (not pre-formatted strings) so the
    # frontend can sort/export them; chart_data and the LLM prompt's markdown table are
    # both derived from these same rows, so every view agrees on the same numbers.
    table = []
    chart_data = []
    header = ["Fiscal Year"] + [METRIC_DEFS[m]["label"] for m in metrics]
    markdown_table = "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"

    for row in financials:
        values = {
            "revenue": round(row["revenue"] / 1e9, 2) if row["revenue"] else 0,
            "net_income": round(row["net_income"] / 1e9, 2) if row["net_income"] else 0,
            "eps": round(row["eps"], 2) if row["eps"] else 0,
            "assets": round(row["assets"] / 1e9, 2) if row["assets"] else 0,
            "liabilities": round(row["liabilities"] / 1e9, 2) if row["liabilities"] else 0,
        }

        table_row = {"year": row["year"]}
        chart_row = {"name": str(row["year"])}
        cells = [str(row["year"])]
        for m in metrics:
            table_row[m] = values[m]
            chart_row[METRIC_DEFS[m]["label"]] = values[m]
            cells.append(_format_value(values[m], METRIC_DEFS[m]["unit"]))
        table.append(table_row)
        chart_data.append(chart_row)
        markdown_table += "| " + " | ".join(cells) + " |\n"

    return {"table": table, "table_columns": table_columns, "chart_data": chart_data, "markdown_table": markdown_table}


def get_default_financials(ticker: str) -> Dict[str, Any]:
    """Full-metric overview (Revenue, Net Income, EPS, Assets, Liabilities) for a ticker,
    used to populate the default table/chart before the user has asked any question —
    unlike the query-driven route, this never narrows to a subset of metrics."""
    ticker = ticker.strip().upper()
    financials = get_financials_for_ticker(ticker)
    if not financials:
        return {"table": [], "table_columns": [], "chart_data": [], "chart_meta": {}}

    view = _build_financial_view(financials, METRIC_ORDER)
    return {
        "table": view["table"],
        "table_columns": view["table_columns"],
        "chart_data": view["chart_data"],
        "chart_meta": {"type": "trend", "series": [ticker], "metrics": [METRIC_DEFS[m]["label"] for m in METRIC_ORDER]},
    }


def financial_analysis_node(state: AgentState) -> Dict[str, Any]:
    ticker, clarification = _resolve_ticker_or_clarify(state)
    if clarification:
        return clarification

    query = state["query"]
    financials = get_financials_for_ticker(ticker)
    company_name = _company_name_for_ticker(ticker)
    resolved_companies = [{"ticker": ticker, "name": company_name}]

    if not financials:
        return {
            "answer": f"I currently do not have cached financials for {ticker}. Please load the ticker using the dashboard sidebar first.",
            "chart_data": [],
            "chart_meta": {},
            "table": [],
            "table_columns": [],
            "sources": [],
            "resolved_companies": resolved_companies,
        }

    # Scope to an explicit fiscal-year window ("2023 to 2025") when the question names one,
    # instead of always plotting every cached year.
    year_range = _extract_year_range(query)
    if year_range:
        scoped = [r for r in financials if year_range[0] <= r["year"] <= year_range[1]]
        if not scoped:
            years_available = ", ".join(str(r["year"]) for r in financials)
            return {
                "answer": f"No cached {ticker} financials fall within {year_range[0]}-{year_range[1]}. Years available: {years_available}.",
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": [],
                "resolved_companies": resolved_companies,
            }
        financials = scoped

    # Which columns actually get shown is driven by what the question asks for — "what's
    # AAPL's EPS trend" gets just Fiscal Year + EPS, not all five metrics every time.
    metrics = _select_metrics(query, default=METRIC_ORDER)
    view = _build_financial_view(financials, metrics)
    table, table_columns, markdown_table = view["table"], view["table_columns"], view["markdown_table"]
    chart_data = view["chart_data"]

    # Use LLM to formulate narrative using only the retrieved database numbers (preventing math hallucinations)
    metric_labels = ", ".join(METRIC_DEFS[m]["label"] for m in metrics)
    llm = _llm()
    # When the router activates both branches (see router_node/_build_graph), the *same*
    # full question (which may also ask about a qualitative topic this node has no data
    # for) gets passed in here too — without this note the model would flag that it can't
    # address that part, not realizing a separate qualitative section already covers it in
    # the merged response built by mixed_node.
    mixed_note = (
        "\n\nNote: this question may also ask about a qualitative topic (e.g. risk factors, "
        "strategy) that this structured data can't address — that part is covered in a "
        "separate section of the response. Do not mention that you lack that information; "
        "just answer the quantitative part."
        if state.get("needs_financial") and state.get("needs_qualitative") else ""
    )
    prompt = ChatPromptTemplate.from_template(
        "You are an expert financial analyst. Formulate a short executive summary describing the {metrics} "
        "trends for {ticker} using ONLY the following structured facts:\n\n{table}\n\n"
        "Question: {question}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. The data itself is already shown to the user as a table, so do not repeat it as a table "
        "or list of numbers. Cite the source once as [1] (a single reference to the SQLite SEC EDGAR Facts DB, "
        "listed separately below your answer) — do not restate the source name after every point."
        + mixed_note
    )

    chain = prompt | llm | StrOutputParser()
    narrative = chain.invoke({"ticker": ticker, "table": markdown_table, "question": query, "metrics": metric_labels})

    return {
        "answer": narrative,
        "chart_data": chart_data,
        "chart_meta": {"type": "trend", "series": [company_name], "metrics": [METRIC_DEFS[m]["label"] for m in metrics]},
        "table": table,
        "table_columns": table_columns,
        "sources": [{"content": f"Structured annual financial facts for {company_name}.", "metadata": {"source": get_company_facts_source(ticker)}}],
        "resolved_companies": resolved_companies,
    }


def filing_rag_node(state: AgentState) -> Dict[str, Any]:
    ticker, clarification = _resolve_ticker_or_clarify(state)
    if clarification:
        return clarification

    query = state["query"]
    resolved_companies = [{"ticker": ticker, "name": _company_name_for_ticker(ticker)}]

    if not os.path.isdir(VECTOR_DB_DIR):
        return {"answer": f"No SEC filing index exists for {ticker}. Load the ticker first.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}
    db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())

    # Retrieve documents relevant to ticker
    retriever = db.as_retriever(search_kwargs={
        "k": 4,
        "filter": {"company": ticker},
    })
    docs = retriever.invoke(query)

    if not docs:
        return {"answer": f"No indexed SEC filing evidence is available for {ticker}.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}

    # Numbered 1..N in the same order as `sources` below, so the model can cite a chunk by
    # its bracketed number instead of restating its full source URL/filename inline.
    context = "\n\n".join(f"[{i + 1}] {doc.page_content}" for i, doc in enumerate(docs))

    llm = _llm()
    # Mirror of the same note in financial_analysis_node: when the router activates both
    # branches, the full question (which may also ask about numbers this filing context
    # doesn't contain) gets passed in here too.
    mixed_note = (
        "\n\nNote: this question may also ask about a quantitative topic (e.g. revenue, "
        "net income) that this filing context can't address — that part is covered in a "
        "separate section of the response. Do not say you don't know or lack that "
        "information; just answer the qualitative part."
        if state.get("needs_financial") and state.get("needs_qualitative") else ""
    )
    prompt = ChatPromptTemplate.from_template(
        "You are an assistant for question-answering tasks analyzing SEC EDGAR filings.\n"
        "Use the following pieces of retrieved filing context to answer the question.\n"
        "If you don't know the answer, say that you don't know.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. Ground every point in the context and cite it only by its bracketed number (e.g. [1], "
        "[2], matching the numbered context above) — do not restate the source URL or filename in your "
        "answer text, since the numbered sources are already listed separately below your answer."
        + mixed_note + "\n\nAnswer:"
    )

    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": query})

    sources = [
        {
            "content": doc.page_content,
            "metadata": doc.metadata
        }
        for doc in docs
    ]

    return {
        "answer": answer,
        "sources": sources,
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
        "resolved_companies": resolved_companies,
    }


def comparison_financial_result(tickers: List[str], query: str) -> Dict[str, Any]:
    """Numeric multi-company pivot table + trend narrative — the quantitative half of a
    comparison query (see quantitative_node). Extracted from the original comparison_node;
    ticker resolution now happens once up front in resolve_node instead of here, since this
    can run in the same parallel superstep as comparison_qualitative_result and shouldn't
    duplicate that work. resolve_node guarantees every ticker here is already ingested."""
    # A ticker resolved from text but with no usable financials (e.g. a real company that
    # simply doesn't have cached annual facts) is dropped rather than failing the whole
    # comparison, as long as at least two others remain to actually compare.
    financials_by_ticker = {t: get_financials_for_ticker(t) for t in tickers}
    tickers = [t for t in tickers if financials_by_ticker[t]]
    if len(tickers) < 2:
        missing = [t for t in financials_by_ticker if t not in tickers]
        return {
            "answer": f"Comparison requires at least two companies with loaded financials. "
                      f"{', '.join(missing)} had none available.",
            "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
        }

    # Scope every company to an explicit fiscal-year window ("2023 to 2025") when named.
    year_range = _extract_year_range(query)
    if year_range:
        for t in tickers:
            financials_by_ticker[t] = [r for r in financials_by_ticker[t] if year_range[0] <= r["year"] <= year_range[1]]
        tickers = [t for t in tickers if financials_by_ticker[t]]
        if len(tickers) < 2:
            return {
                "answer": f"Fewer than two of the named companies have cached financials within {year_range[0]}-{year_range[1]}.",
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
            }

    # Pivot each company's rows by year for easy per-year, per-ticker lookups below.
    by_ticker_by_year = {t: {r["year"]: r for r in financials_by_ticker[t]} for t in tickers}
    years = sorted({r["year"] for t in tickers for r in financials_by_ticker[t]})

    # Default to revenue+net income (the classic comparison view) when the question doesn't
    # name a specific metric; narrow to exactly what was asked ("compare their EPS",
    # "compare balance sheets") otherwise.
    metrics = _select_metrics(query, default=["revenue", "net_income"])
    names_by_ticker = {t: _company_name_for_ticker(t) for t in tickers}

    table_columns = [{"key": "year", "label": "Year", "unit": ""}]
    header = ["Year"]
    for m in metrics:
        label, unit = METRIC_DEFS[m]["label"], METRIC_DEFS[m]["unit"]
        for t in tickers:
            table_columns.append({"key": f"{t}_{m}", "label": f"{t} {label}", "unit": unit})
            header.append(f"{t} {label}")
    markdown_table = "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"

    table = []
    chart_data = []
    for yr in years:
        table_row = {"year": yr}
        chart_row = {"name": str(yr)}
        cells = [str(yr)]
        for m in metrics:
            unit = METRIC_DEFS[m]["unit"]
            label = METRIC_DEFS[m]["label"]
            for t in tickers:
                raw = (by_ticker_by_year[t].get(yr, {}).get(m, 0) or 0)
                val = round(raw / 1e9, 2) if unit == "$B" else round(raw, 2)
                table_row[f"{t}_{m}"] = val
                chart_row[f"{t} {label}"] = val
                cells.append(_format_value(val, unit))

        table.append(table_row)
        chart_data.append(chart_row)
        markdown_table += "| " + " | ".join(cells) + " |\n"

    metric_labels = ", ".join(METRIC_DEFS[m]["label"] for m in metrics)
    company_list = ", ".join(tickers[:-1]) + f" and {tickers[-1]}" if len(tickers) > 2 else " and ".join(tickers)
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "Compare the annual {metrics} and growth rates of {companies} "
        "based ONLY on this data table:\n{table}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. Provide a concise analysis of who is growing faster and how they differ. The data itself "
        "is already shown to the user as a table, so do not repeat it as a table or list of numbers. Cite the "
        "source once as [1] (a single reference to the SQLite SEC database, listed separately below your "
        "answer) — do not restate company or source names after every point."
    )
    chain = prompt | llm | StrOutputParser()
    analysis = chain.invoke({"companies": company_list, "table": markdown_table, "metrics": metric_labels})

    return {
        "answer": analysis,
        "chart_data": chart_data,
        "chart_meta": {"type": "comparison", "series": [names_by_ticker[t] for t in tickers], "metrics": [METRIC_DEFS[m]["label"] for m in metrics]},
        "table": table,
        "table_columns": table_columns,
        "sources": [
            {"content": f"Structured {names_by_ticker[t]} annual SEC facts.", "metadata": {"source": get_company_facts_source(t)}}
            for t in tickers
        ],
        "resolved_companies": [{"ticker": t, "name": names_by_ticker[t]} for t in tickers],
    }


def comparison_qualitative_result(tickers: List[str], query: str, needs_financial: bool = False) -> Dict[str, Any]:
    """Qualitative multi-company comparison — the qualitative half of a comparison query
    (see qualitative_node), and the counterpart to comparison_financial_result above.
    Chroma's filter is a single equality match, so this retrieves each company's chunks
    with its own call rather than one combined query, then asks the LLM to synthesize a
    comparative narrative citing across all of them. Previously there was no qualitative
    comparison path at all — comparison_node only ever produced a numeric table."""
    names_by_ticker = {t: _company_name_for_ticker(t) for t in tickers}
    resolved_companies = [{"ticker": t, "name": names_by_ticker[t]} for t in tickers]

    if not os.path.isdir(VECTOR_DB_DIR):
        return {"answer": "No SEC filing index exists yet. Load the companies first.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}
    db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())

    # A smaller per-company k than the single-company path (4) keeps total retrieved
    # context bounded as the company count scales up to MAX_COMPARISON_COMPANIES.
    per_company_k = 3
    docs_by_ticker = {t: db.as_retriever(search_kwargs={"k": per_company_k, "filter": {"company": t}}).invoke(query) for t in tickers}
    all_docs = [(t, doc) for t in tickers for doc in docs_by_ticker[t]]

    if not all_docs:
        return {"answer": f"No indexed SEC filing evidence is available for {', '.join(tickers)}.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}

    # Numbered 1..N across all companies combined, each line tagged with its company so the
    # model attributes a point to the right company instead of just citing a bare number.
    context = "\n\n".join(f"[{i + 1}] ({names_by_ticker[t]}) {doc.page_content}" for i, (t, doc) in enumerate(all_docs))

    llm = _llm()
    company_list = ", ".join(tickers[:-1]) + f" and {tickers[-1]}" if len(tickers) > 2 else " and ".join(tickers)
    # Mirror of the same note in financial_analysis_node/filing_rag_node: when the router
    # also activates the quantitative comparison branch, the full question gets passed in
    # here too.
    mixed_note = (
        "\n\nNote: this question may also ask about a quantitative topic (e.g. revenue, "
        "net income) that this filing context can't address — that part is covered in a "
        "separate section of the response. Do not say you don't know or lack that "
        "information; just answer the qualitative part."
        if needs_financial else ""
    )
    prompt = ChatPromptTemplate.from_template(
        "You are an assistant analyzing SEC EDGAR filings for multiple companies.\n"
        "Use the following pieces of retrieved filing context — each tagged with which "
        "company it's from — to compare {companies} on the question asked. If the context "
        "doesn't cover a company for this question, say so for that company specifically "
        "rather than guessing.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. Ground every point in the context and cite it only by its bracketed number (e.g. [1], "
        "[2], matching the numbered context above) — do not restate the source URL or filename in your "
        "answer text, since the numbered sources are already listed separately below your answer."
        + mixed_note + "\n\nAnswer:"
    )
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"companies": company_list, "context": context, "question": query})

    return {
        "answer": answer,
        "sources": [{"content": doc.page_content, "metadata": doc.metadata} for _, doc in all_docs],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
        "resolved_companies": resolved_companies,
    }


def resolve_node(state: AgentState) -> Dict[str, Any]:
    """Resolves the query's target company/companies against the already-ingested
    watchlist only — companies are added exclusively via the sidebar's manual "Ingest
    Ticker" form (app.py), never automatically from a query. This still runs as its own
    step before quantitative_node/qualitative_node's fan-out (see _build_graph) because
    `route_after_resolve` needs its output (`clarification`/`tickers`) to decide the
    fan-out itself, regardless of ingestion. A single-company query resolves one ticker
    (_resolve_ticker_or_clarify); a comparison query (is_comparison=True) resolves the full
    list into `tickers` instead. An ambiguous single-company query, a comparison with fewer
    than two recognized companies, or any recognized-but-not-yet-ingested company is
    stashed in state as `clarification` rather than returned directly, so the graph still
    funnels through one real node (mixed_node) instead of special-casing an early exit
    here."""
    if state.get("is_comparison"):
        query = state["query"]
        all_companies = get_companies()
        loaded = {co["ticker"].upper() for co in all_companies}
        matched = _find_companies(query, all_companies, limit=MAX_COMPARISON_COMPANIES)

        if len(matched) < 2:
            names = ", ".join(f"{co['ticker']} ({co['name']})" for co in all_companies)
            found = f" I only recognized {matched[0]}." if matched else ""
            loaded_note = f" Loaded companies: {names}." if names else ""
            return {"clarification": {
                "answer": f"I need at least two companies to compare.{found}{loaded_note} "
                          "Please name the companies explicitly.",
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": [],
            }}

        not_ingested = [t for t in matched if t not in loaded]
        if not_ingested:
            return {"clarification": _not_ingested_response(not_ingested)}

        return {"tickers": matched, "clarification": None}

    _, clarification = _resolve_ticker_or_clarify(state)
    return {"clarification": clarification}


def quantitative_node(state: AgentState) -> Dict[str, Any]:
    """Runs the quantitative half — financial_analysis_node for a single company, or
    comparison_financial_result across state["tickers"] for a comparison — and writes the
    result to `financial_result` instead of the shared top-level answer/sources/etc. keys.
    This lets the graph run it in the same parallel superstep as qualitative_node (see
    _build_graph) without both branches racing to write the same state channel. mixed_node
    reads this back and merges it."""
    if state.get("is_comparison"):
        result = comparison_financial_result(state["tickers"], state["query"])
    else:
        result = financial_analysis_node(state)
    return {"financial_result": result}


def qualitative_node(state: AgentState) -> Dict[str, Any]:
    """Mirror of quantitative_node for the qualitative half — filing_rag_node for a single
    company, or comparison_qualitative_result across state["tickers"] for a comparison. See
    quantitative_node for why the result is namespaced under its own state key instead of
    the shared answer/sources fields."""
    if state.get("is_comparison"):
        result = comparison_qualitative_result(state["tickers"], state["query"], needs_financial=state.get("needs_financial", False))
    else:
        result = filing_rag_node(state)
    return {"qualitative_result": result}


def mixed_node(state: AgentState) -> Dict[str, Any]:
    """Real join point for the quantitative_node/qualitative_node fan-out (see
    _build_graph) — every "quant_qual" query ends here, whether only one branch actually
    ran (a financial-only or qualitative-only question) or both did (a genuinely mixed
    question, e.g. "what are Apple's risk factors and how did their revenue perform").
    Replaces the previous version of this node, which called financial_analysis_node and
    filing_rag_node directly as sequential plain Python calls from inside one node instead
    of letting the graph itself run them as parallel branches."""
    if state.get("clarification"):
        return state["clarification"]

    financial_result = state.get("financial_result")
    qualitative_result = state.get("qualitative_result")

    if financial_result and qualitative_result:
        financial_ok = bool(financial_result.get("table"))
        qualitative_ok = bool(qualitative_result.get("sources"))

        if financial_ok and qualitative_ok:
            # financial_analysis_node always returns exactly one source, so shifting every
            # bracketed citation number in the qualitative half by that fixed offset keeps
            # its [N] markers pointing at the right entry once the two source lists are
            # concatenated (financial source first, then filing sources) into one combined
            # Sources list.
            offset = len(financial_result["sources"])
            shifted_qualitative_answer = re.sub(
                r"\[(\d+)\]", lambda m: f"[{int(m.group(1)) + offset}]", qualitative_result["answer"]
            )
            return {
                "answer": f"Financial trend:\n{financial_result['answer']}\n\nQualitative analysis:\n{shifted_qualitative_answer}",
                "chart_data": financial_result.get("chart_data", []),
                "chart_meta": financial_result.get("chart_meta", {}),
                "table": financial_result.get("table", []),
                "table_columns": financial_result.get("table_columns", []),
                "sources": financial_result["sources"] + qualitative_result["sources"],
                "resolved_companies": financial_result.get("resolved_companies", []),
            }

        # Only one half produced real content (e.g. no filing index yet, or no cached
        # financials) — surface that half alone rather than a response half-padded with
        # explanatory filler text from the other.
        return financial_result if financial_ok else qualitative_result

    # Only one branch ran at all (financial-only or qualitative-only query) — pass its
    # result straight through.
    return financial_result or qualitative_result


def external_queries_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"]
    source_url = os.getenv("EXTERNAL_DATA_URL")
    if not source_url:
        return {
            "answer": "This question requires an external consumer-data provider. Configure EXTERNAL_DATA_URL; SEC filings do not contain that evidence.",
            "sources": [],
            "chart_data": [],
            "chart_meta": {},
            "table": [],
            "table_columns": [],
        }
    try:
        response = requests.get(source_url, params={"q": query}, timeout=20)
        response.raise_for_status()
        consumer_facts = response.text[:12000]
    except requests.RequestException:
        logger.exception("External data request failed")
        return {"answer": "The configured external consumer-data provider is unavailable.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": []}

    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "You are a consumer research analyst. Answer the user question utilizing the following consumer data:\n\n"
        "{facts}\n\nQuestion: {question}\n\nFormat clearly, do not hallucinate, and cite the configured external source."
    )

    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"facts": consumer_facts, "question": query})

    return {
        "answer": answer,
        "sources": [{"content": consumer_facts, "metadata": {"source": source_url}}],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
    }


# Build LangGraph workflow once at import time — the graph structure is static per process.
def _build_graph():
    builder = StateGraph(AgentState)

    builder.add_node("router", router_node)
    builder.add_node("classify", classify_node)
    builder.add_node("resolve", resolve_node)
    builder.add_node("quantitative", quantitative_node)
    builder.add_node("qualitative", qualitative_node)
    builder.add_node("mixed", mixed_node)
    builder.add_node("external", external_queries_node)

    builder.set_entry_point("router")
    # router only extracts is_comparison (deterministic entity matching); classify_node
    # right after it is the real LLM decision about which branch(es) to activate next.
    builder.add_edge("router", "classify")

    def route_from_classify(state: AgentState) -> str:
        return state["route"]

    builder.add_conditional_edges(
        "classify",
        route_from_classify,
        {
            # A comparison query is no longer a separate terminal node — it's just
            # is_comparison=True flowing through the same quant/qual fan-out as everything
            # else (see resolve_node/quantitative_node/qualitative_node), so it can come
            # back numeric, qualitative, or both via the same mixed_node merge. "external"
            # stays a dedicated branch since it never populates financial_result/
            # qualitative_result — routing it through mixed_node first wouldn't do
            # anything for it.
            "external": "external",
            "quant_qual": "resolve",
        }
    )

    def route_after_resolve(state: AgentState) -> List[str]:
        # Returns every branch that actually needs to run. LangGraph schedules every name
        # in the returned list as a real parallel branch in the same execution step — this
        # is what makes "mixed" an actual graph fan-out/join instead of the old mixed_node,
        # which called financial_analysis_node/filing_rag_node as sequential plain Python
        # function calls from inside a single node.
        if state.get("clarification"):
            return ["mixed"]
        targets = []
        if state["needs_financial"]:
            targets.append("quantitative")
        if state["needs_qualitative"]:
            targets.append("qualitative")
        return targets

    builder.add_conditional_edges(
        "resolve",
        route_after_resolve,
        {
            "quantitative": "quantitative",
            "qualitative": "qualitative",
            "mixed": "mixed",
        }
    )

    builder.add_edge("quantitative", "mixed")
    builder.add_edge("qualitative", "mixed")
    builder.add_edge("mixed", END)
    builder.add_edge("external", END)

    return builder.compile()


_GRAPH = _build_graph()


def run_agent(query_str: str) -> Dict[str, Any]:
    load_dotenv()

    initial_state: AgentState = {
        "query": query_str,
        "route": "",
        "needs_financial": False,
        "needs_qualitative": False,
        "is_comparison": False,
        "tickers": None,
        "clarification": None,
        "financial_result": None,
        "qualitative_result": None,
        "answer": "",
        "sources": [],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
        "resolved_companies": [],
    }

    result = _GRAPH.invoke(initial_state)
    return result
