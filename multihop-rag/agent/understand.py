"""The entry node: one LLM call that plans how to answer the question, checked in code."""

import json
import logging
import os
import re
from db_manager import get_companies
from typing import Dict, Any, List, Optional, Tuple

from .config import CHART_TYPES, FILING_TYPES, MAX_COMPARISON_COMPANIES, METRIC_ORDER, _llm
from .state import AgentState
from .companies import _find_companies
from .fallbacks import _extract_year_range, _keyword_is_comparison, _select_metrics

logger = logging.getLogger(__name__)


UNDERSTAND_PROMPT = """You plan how a financial Q&A system answers a question about public companies, using their SEC filings and their annual reported figures.

Loaded companies (ticker: name):
{companies}

Question: {question}

Respond with ONLY a JSON object, no other text, no markdown fences, with these keys:
- "companies": every company the question asks about, as its ticker if it is a loaded company, otherwise its name as written. Main subject first. Leave out companies mentioned only as an example or a competitor of the subject. Empty list if no specific company is named.
- "all_loaded_companies": true if the question covers the loaded companies as a group instead of naming them (for example "all companies", "which of the loaded companies", "each of them", "across the portfolio"), else false.
- "is_comparison": true if answering requires comparing, ranking, or contrasting two or more companies, else false.
- "wants_verdict": true if the question asks for a single answer about which company (or which year) is highest, fastest, riskiest, better, most exposed, and so on, else false.
- "metrics": the structured metrics needed to answer, chosen only from ["revenue", "net_income", "eps", "assets", "liabilities"]. Include every metric needed to work out what is asked, not only the ones named: leverage, debt load or liabilities relative to size need liabilities and assets; profitability or margins need revenue and net_income; overall financial performance or growth quality needs revenue, net_income and eps. Empty list if no structured metric is needed.
- "year_start", "year_end": fiscal years the question explicitly restricts to, as integers, or null. A single year sets both. "since 2021" sets only year_start.
- "last_n_years": an integer if the question says "last N years" or "past N years", else null.
- "filing_type": "10-K", "10-Q", or "8-K" if the question asks about one kind of filing (an annual report is a 10-K, a quarterly report a 10-Q), else null.
- "most_recent_filing": true if the question asks about the latest or most recent filing, else false.
- "chart": the chart that best answers the question, one of "trend" (values over time), "indexed" (growth of differently sized companies or metrics, rebased to 100), "yoy" (year-over-year growth rates), "margin" (net margin), "composition" (liabilities vs equity, leverage), "ranking" (which company is largest in a year), "growth" (which company grew fastest), "scatter" (size vs growth across many companies), "themes" (filing topics by company), "sources" (which filings the answer draws on).
- "needs_financial": true if answering needs reported figures or trends of those five metrics. A question asking only how management discusses or explains a financial topic in narrative form (liquidity, capital resources, cash flow, accounting estimates) is not needs_financial unless it also asks for figures or a trend.
- "needs_qualitative": true if answering needs narrative text from SEC filings (risk factors, strategy, outlook, litigation, competition, MD&A discussion, business overview, workforce, cybersecurity, supply chain, regulation, intellectual property, accounting judgments, and similar).
- "wants_external": true only if the question needs consumer or market-research data that SEC filings do not contain (product reviews, brand sentiment, market-share surveys).

needs_financial and needs_qualitative can both be true. If the question names neither a figure nor a narrative topic, set needs_qualitative true."""


def _valid_year(value: Any) -> Optional[int]:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1990 <= year <= 2100 else None


def _plan_tickers(parsed: Dict[str, Any], all_companies: List[Dict[str, Any]]) -> List[str]:
    """Turn the model's company list into real tickers. Every name is looked up against the
    loaded companies first, then the SEC universe, so a ticker the model invents is dropped
    instead of trusted."""
    if parsed.get("all_loaded_companies"):
        return [co["ticker"].upper() for co in all_companies][:MAX_COMPARISON_COMPANIES]
    loaded = {co["ticker"].upper() for co in all_companies}
    tickers: List[str] = []
    for name in parsed.get("companies") or []:
        if not isinstance(name, str) or not name.strip():
            continue
        candidate = name.strip().upper()
        found = [candidate] if candidate in loaded else _find_companies(name, all_companies, limit=1)
        for t in found:
            if t not in tickers:
                tickers.append(t)
    return tickers[:MAX_COMPARISON_COMPANIES]


def understand_node(state: AgentState) -> Dict[str, Any]:
    """Entry point. One LLM call reads the question against the loaded-company list and
    returns the whole plan: which companies, whether it is a comparison, which metrics and
    years, whether it needs figures, filing text, or both, and whether it asks for a verdict.
    This replaced a keyword router (comparison words) plus a separate classifier call, at
    the same cost of one call per question. Every field is checked in code: companies are looked up
    against real tickers, metrics must be one of METRIC_ORDER, years must be real years. If
    the call fails, the old keyword logic runs instead and a warning is logged."""
    query = state["query"]
    all_companies = get_companies()
    companies_text = "\n".join(f"{co['ticker']}: {co['name']}" for co in all_companies) or "(none)"

    try:
        raw = _llm().invoke(UNDERSTAND_PROMPT.format(companies=companies_text, question=query)).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    except Exception:
        logger.warning("Question understanding failed; falling back to keyword routing", exc_info=True)
        return {
            "plan": None,
            "is_comparison": _keyword_is_comparison(query),
            "route": "quant_qual",
            "needs_financial": False,
            "needs_qualitative": True,
        }

    tickers = _plan_tickers(parsed, all_companies)
    is_comparison = bool(parsed.get("is_comparison") or parsed.get("all_loaded_companies")) and len(tickers) >= 2
    last_n = parsed.get("last_n_years")
    plan = {
        "tickers": tickers,
        "metrics": [m for m in METRIC_ORDER if m in (parsed.get("metrics") or [])],
        "year_start": _valid_year(parsed.get("year_start")),
        "year_end": _valid_year(parsed.get("year_end")),
        "last_n_years": last_n if isinstance(last_n, int) and 0 < last_n <= 30 else None,
        "wants_verdict": bool(parsed.get("wants_verdict")),
        "filing_type": parsed.get("filing_type") if parsed.get("filing_type") in FILING_TYPES else None,
        "most_recent_filing": bool(parsed.get("most_recent_filing")),
        "chart": parsed.get("chart") if parsed.get("chart") in CHART_TYPES else None,
    }
    logger.info("Question plan: %s, comparison=%s", plan, is_comparison)

    needs_financial = bool(parsed.get("needs_financial"))
    needs_qualitative = bool(parsed.get("needs_qualitative"))
    wants_external = bool(parsed.get("wants_external"))
    base = {"plan": plan, "is_comparison": is_comparison}

    if wants_external and not needs_financial and not needs_qualitative and os.getenv("EXTERNAL_DATA_URL"):
        # Only route to the external provider if one is actually configured — otherwise
        # fall through to qualitative search rather than dead-ending on an unconfigured
        # feature.
        return {**base, "route": "external", "needs_financial": False, "needs_qualitative": False}

    if not needs_financial and not needs_qualitative:
        # Neither flag ended up true — whether from a parsed {false, false} response (the
        # model doesn't always follow the "default to qualitative" instruction above) or a
        # wants_external classification with no provider configured. route_after_resolve
        # (see _build_graph) fans out to whichever of quantitative/qualitative is true, so
        # leaving both false here would return an empty branch list and dead-end the query
        # with a blank answer and no error — reproduced live before this check existed.
        # Comparisons default to numeric; everything else defaults to qualitative.
        if is_comparison:
            needs_financial = True
        else:
            needs_qualitative = True

    return {**base, "route": "quant_qual", "needs_financial": needs_financial, "needs_qualitative": needs_qualitative}


def _plan_metrics(state_or_plan: Optional[Dict[str, Any]], query: str, default: List[str]) -> List[str]:
    """Metrics from the question plan; the keyword picker only when there is no plan."""
    if state_or_plan is None:
        return _select_metrics(query, default)
    return list(state_or_plan.get("metrics") or default)


def _plan_year_range(plan: Optional[Dict[str, Any]], query: str, available_years: List[int]) -> Optional[Tuple[int, int]]:
    """Fiscal-year window from the question plan. "Last N years" is resolved here against
    the years actually in the data, which the model doesn't know. Without a plan, the regex
    extractor runs instead."""
    if plan is None:
        return _extract_year_range(query)
    if plan.get("last_n_years") and available_years:
        end = max(available_years)
        return (end - plan["last_n_years"] + 1, end)
    start, end = plan.get("year_start"), plan.get("year_end")
    if start is None and end is None:
        return None
    start, end = start or 0, end or 9999
    return (min(start, end), max(start, end))
