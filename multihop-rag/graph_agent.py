import os
import re
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

logger = logging.getLogger(__name__)

# Consumer/market-research topics that only the (optional) external provider can answer.
# Gated on EXTERNAL_DATA_URL being configured so these words don't hijack real filing
# questions (e.g. "what devices does Apple flag as a supply risk") when no provider exists.
EXTERNAL_KEYWORDS = ["gen z", "consumer sentiment", "phone preference", "brand ownership", "survey data"]

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
FINANCIAL_KEYWORDS = ["revenue", "net income", "profit", "eps", "earnings", "assets", "liabilities", "balance sheet", "income statement", "financials"]
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


def _resolve_ticker_or_clarify(state: "AgentState") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Resolve a single target ticker for this query, or return an honest clarification
    response instead of silently guessing a default company."""
    all_companies = get_companies()
    loaded = {co["ticker"].upper() for co in all_companies}

    pinned = state.get("ticker", "").upper()
    if state.get("ticker_pinned") and pinned in loaded:
        return pinned, None

    matched = _find_matching_tickers(state["query"], all_companies, limit=1)
    if matched:
        return matched[0], None

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
                  "Mention one by name or ticker, or select it in the sidebar first.",
        "sources": [],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
    }


@lru_cache(maxsize=1)
def _llm() -> ChatGroq:
    # qwen3.6 is a reasoning model. Two settings matter here:
    # - reasoning_format="hidden": without it, the raw <think>...</think> chain-of-thought
    #   leaks straight into .content — this was the main reason answers looked broken.
    # - reasoning_effort="none": these are grounded summarization/formatting tasks over data
    #   we've already computed, not multi-step reasoning problems. Leaving effort at its
    #   default burns ~2000 hidden reasoning tokens per call (measured) and can exhaust
    #   max_tokens before any visible answer is emitted at all, returning empty content.
    #   "none" measured at 85 tokens and 0.2s for the same prompt — faster, cheaper, reliable.
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "qwen/qwen3.6-27b"),
        temperature=0,
        reasoning_format="hidden",
        reasoning_effort="none",
        max_tokens=1500,
    )


# State definition
class AgentState(TypedDict):
    query: str
    ticker: str
    ticker_pinned: bool
    route: str
    answer: str
    sources: List[Dict[str, Any]]
    chart_data: List[Dict[str, Any]]
    chart_meta: Dict[str, Any]
    table: List[Dict[str, Any]]
    table_columns: List[Dict[str, str]]


# Router definition
def router_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"].lower()

    # Simple keyword rule router for speed and reliability.
    route = "filing_rag"
    wants_comparison = any(k in query for k in ["compare", "comparison", "versus", " vs "])
    is_financial = any(k in query for k in FINANCIAL_KEYWORDS)
    if wants_comparison:
        # "Compare X and Y" only means the cross-company route when two companies are
        # actually named. "Compare Apple's revenue and net income" names one company and
        # two metrics — that's an intra-company trend question, not a cross-company one.
        matched = _find_matching_tickers(state["query"], get_companies(), limit=2)
        route = "comparison" if len(matched) >= 2 else ("financial_analysis" if is_financial else "filing_rag")
    elif is_financial:
        route = "financial_analysis"
    elif os.getenv("EXTERNAL_DATA_URL") and any(k in query for k in EXTERNAL_KEYWORDS):
        # Only route to the external provider if one is actually configured — otherwise
        # these words are far more likely to be about a real filing.
        route = "external"

    return {"route": route}


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

    if not financials:
        return {
            "answer": f"I currently do not have cached financials for {ticker}. Please load the ticker using the dashboard sidebar first.",
            "chart_data": [],
            "chart_meta": {},
            "table": [],
            "table_columns": [],
            "sources": []
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
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
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
    prompt = ChatPromptTemplate.from_template(
        "You are an expert financial analyst. Formulate a short executive summary describing the {metrics} "
        "trends for {ticker} using ONLY the following structured facts:\n\n{table}\n\n"
        "Question: {question}\n\nInclude the source as SQLite SEC EDGAR Facts DB. "
        "Write plain prose only — the data itself is already shown to the user as a table, so do not repeat it as a table or list of numbers."
    )

    chain = prompt | llm | StrOutputParser()
    narrative = chain.invoke({"ticker": ticker, "table": markdown_table, "question": query, "metrics": metric_labels})

    return {
        "answer": narrative,
        "chart_data": chart_data,
        "chart_meta": {"type": "trend", "series": [ticker], "metrics": [METRIC_DEFS[m]["label"] for m in metrics]},
        "table": table,
        "table_columns": table_columns,
        "sources": [{"content": f"Structured annual financial facts for {ticker}.", "metadata": {"source": get_company_facts_source(ticker)}}]
    }


def filing_rag_node(state: AgentState) -> Dict[str, Any]:
    ticker, clarification = _resolve_ticker_or_clarify(state)
    if clarification:
        return clarification

    query = state["query"]

    if not os.path.isdir(VECTOR_DB_DIR):
        return {"answer": f"No SEC filing index exists for {ticker}. Load the ticker first.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": []}
    db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())

    # Retrieve documents relevant to ticker
    retriever = db.as_retriever(search_kwargs={
        "k": 4,
        "filter": {"company": ticker},
    })
    docs = retriever.invoke(query)

    if not docs:
        return {"answer": f"No indexed SEC filing evidence is available for {ticker}.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": []}

    context = "\n\n".join(f"[{doc.metadata.get('source', 'Unknown')}]: {doc.page_content}" for doc in docs)

    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "You are an assistant for question-answering tasks analyzing SEC EDGAR filings.\n"
        "Use the following pieces of retrieved filing context to answer the question.\n"
        "If you don't know the answer, say that you don't know.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer (be comprehensive and ground everything in source documents. Cite the files/items):"
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
        "table_columns": []
    }


def comparison_node(state: AgentState) -> Dict[str, Any]:
    query = state["query"]
    all_companies = get_companies()
    matched = _find_matching_tickers(query, all_companies, limit=2)

    if len(matched) < 2:
        if len(all_companies) < 2:
            return {
                "answer": "Comparisons need at least two loaded companies. Use the 'Ingest Ticker' sidebar to add another one first.",
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
            }
        names = ", ".join(f"{co['ticker']} ({co['name']})" for co in all_companies)
        found = f" I only recognized {matched[0]}." if matched else ""
        return {
            "answer": f"I couldn't tell which two companies to compare.{found} Loaded companies: {names}. "
                      "Please name both companies explicitly.",
            "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
        }

    ticker_a, ticker_b = matched[0], matched[1]
    financials_a = get_financials_for_ticker(ticker_a)
    financials_b = get_financials_for_ticker(ticker_b)

    if not financials_a or not financials_b:
        return {
            "answer": f"Comparison requires both {ticker_a} and {ticker_b} to be loaded. Please ingest them via the sidebar.",
            "chart_data": [],
            "chart_meta": {},
            "table": [],
            "table_columns": [],
            "sources": []
        }

    # Scope both companies to an explicit fiscal-year window ("2023 to 2025") when named.
    year_range = _extract_year_range(query)
    if year_range:
        financials_a = [r for r in financials_a if year_range[0] <= r["year"] <= year_range[1]]
        financials_b = [r for r in financials_b if year_range[0] <= r["year"] <= year_range[1]]
        if not financials_a or not financials_b:
            return {
                "answer": f"No cached financials for both {ticker_a} and {ticker_b} fall within {year_range[0]}-{year_range[1]}.",
                "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "sources": []
            }

    # Pivot datasets by year
    years = sorted(list(set([r["year"] for r in financials_a] + [r["year"] for r in financials_b])))
    a_by_yr = {r["year"]: r for r in financials_a}
    b_by_yr = {r["year"]: r for r in financials_b}

    # Default to revenue+net income (the classic comparison view) when the question doesn't
    # name a specific metric; narrow to exactly what was asked ("compare their EPS",
    # "compare balance sheets") otherwise.
    metrics = _select_metrics(query, default=["revenue", "net_income"])

    table_columns = [{"key": "year", "label": "Year", "unit": ""}]
    for m in metrics:
        label, unit = METRIC_DEFS[m]["label"], METRIC_DEFS[m]["unit"]
        table_columns.append({"key": f"a_{m}", "label": f"{ticker_a} {label}", "unit": unit})
        table_columns.append({"key": f"b_{m}", "label": f"{ticker_b} {label}", "unit": unit})

    table = []
    chart_data = []
    header = ["Year"]
    for m in metrics:
        label = METRIC_DEFS[m]["label"]
        header += [f"{ticker_a} {label}", f"{ticker_b} {label}"]
    markdown_table = "| " + " | ".join(header) + " |\n|" + "---|" * len(header) + "\n"

    for yr in years:
        table_row = {"year": yr}
        chart_row = {"name": str(yr)}
        cells = [str(yr)]
        for m in metrics:
            unit = METRIC_DEFS[m]["unit"]
            a_raw = (a_by_yr.get(yr, {}).get(m, 0) or 0)
            b_raw = (b_by_yr.get(yr, {}).get(m, 0) or 0)
            a_val = round(a_raw / 1e9, 2) if unit == "$B" else round(a_raw, 2)
            b_val = round(b_raw / 1e9, 2) if unit == "$B" else round(b_raw, 2)

            table_row[f"a_{m}"] = a_val
            table_row[f"b_{m}"] = b_val
            label = METRIC_DEFS[m]["label"]
            chart_row[f"{ticker_a} {label}"] = a_val
            chart_row[f"{ticker_b} {label}"] = b_val
            cells += [_format_value(a_val, unit), _format_value(b_val, unit)]

        table.append(table_row)
        chart_data.append(chart_row)
        markdown_table += "| " + " | ".join(cells) + " |\n"

    metric_labels = ", ".join(METRIC_DEFS[m]["label"] for m in metrics)
    llm = _llm()
    prompt = ChatPromptTemplate.from_template(
        "Compare the annual {metrics} and growth rates of {ticker_a} and {ticker_b} "
        "based ONLY on this data table:\n{table}\n\nProvide a concise analysis of who is growing faster and how "
        "they differ. Cite 'SQLite SEC database' as the source. "
        "Write plain prose only — the data itself is already shown to the user as a table, so do not repeat it as a table or list of numbers."
    )
    chain = prompt | llm | StrOutputParser()
    analysis = chain.invoke({"ticker_a": ticker_a, "ticker_b": ticker_b, "table": markdown_table, "metrics": metric_labels})

    return {
        "answer": analysis,
        "chart_data": chart_data,
        "chart_meta": {"type": "comparison", "series": [ticker_a, ticker_b], "metrics": [METRIC_DEFS[m]["label"] for m in metrics]},
        "table": table,
        "table_columns": table_columns,
        "sources": [
            {"content": f"Structured {ticker_a} annual SEC facts.", "metadata": {"source": get_company_facts_source(ticker_a)}},
            {"content": f"Structured {ticker_b} annual SEC facts.", "metadata": {"source": get_company_facts_source(ticker_b)}}
        ]
    }


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
    builder.add_node("financial_analysis", financial_analysis_node)
    builder.add_node("filing_rag", filing_rag_node)
    builder.add_node("comparison", comparison_node)
    builder.add_node("external", external_queries_node)

    builder.set_entry_point("router")

    def route_decision(state: AgentState) -> str:
        return state["route"]

    builder.add_conditional_edges(
        "router",
        route_decision,
        {
            "financial_analysis": "financial_analysis",
            "filing_rag": "filing_rag",
            "comparison": "comparison",
            "external": "external"
        }
    )

    builder.add_edge("financial_analysis", END)
    builder.add_edge("filing_rag", END)
    builder.add_edge("comparison", END)
    builder.add_edge("external", END)

    return builder.compile()


_GRAPH = _build_graph()


def run_agent(query_str: str, ticker_hint: Optional[str] = None) -> Dict[str, Any]:
    load_dotenv()

    initial_state: AgentState = {
        "query": query_str,
        "ticker": (ticker_hint or "").upper(),
        "ticker_pinned": bool(ticker_hint),
        "route": "",
        "answer": "",
        "sources": [],
        "chart_data": [],
        "chart_meta": {},
        "table": [],
        "table_columns": [],
    }

    result = _GRAPH.invoke(initial_state)
    return result
