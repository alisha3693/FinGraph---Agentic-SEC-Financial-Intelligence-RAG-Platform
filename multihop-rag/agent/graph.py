"""Graph nodes that resolve, fan out and join, the graph itself, and run_agent."""

import logging
import os
import re
import requests
from db_manager import get_companies
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from typing import Dict, Any, List

from .config import _llm
from .state import AgentState
from .companies import _comparison_tickers, _not_ingested_response, _resolve_ticker_or_clarify
from .understand import understand_node
from .structure import _mixed_structure
from .financial import comparison_financial_result, financial_analysis_node
from .filings import comparison_qualitative_result, filing_rag_node

logger = logging.getLogger(__name__)


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
        plan = state.get("plan")
        matched = plan["tickers"] if plan is not None else _comparison_tickers(query, all_companies)

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
        result = comparison_financial_result(state["tickers"], state["query"], plan=state.get("plan"))
    else:
        result = financial_analysis_node(state)
    return {"financial_result": result}


def qualitative_node(state: AgentState) -> Dict[str, Any]:
    """Mirror of quantitative_node for the qualitative half — filing_rag_node for a single
    company, or comparison_qualitative_result across state["tickers"] for a comparison. See
    quantitative_node for why the result is namespaced under its own state key instead of
    the shared answer/sources fields."""
    if state.get("is_comparison"):
        result = comparison_qualitative_result(state["tickers"], state["query"], needs_financial=state.get("needs_financial", False),
                                               plan=state.get("plan"))
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
                "structured": _mixed_structure(state, financial_result, shifted_qualitative_answer),
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

    builder.add_node("understand", understand_node)
    builder.add_node("resolve", resolve_node)
    builder.add_node("quantitative", quantitative_node)
    builder.add_node("qualitative", qualitative_node)
    builder.add_node("mixed", mixed_node)
    builder.add_node("external", external_queries_node)

    # understand_node makes the one LLM routing decision: companies, comparison or not,
    # metrics, years, and which branch(es) to activate next.
    builder.set_entry_point("understand")

    def route_from_understand(state: AgentState) -> str:
        return state["route"]

    builder.add_conditional_edges(
        "understand",
        route_from_understand,
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
        "plan": None,
        "structured": None,
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
