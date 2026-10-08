"""Answers from the structured SEC figures, for one company or a comparison."""

from db_manager import get_company_facts_source, get_financials_for_ticker
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, Any, List, Optional

from .config import METRIC_DEFS, METRIC_ORDER, _llm
from .state import AgentState
from .companies import _company_name_for_ticker, _resolve_ticker_or_clarify
from .understand import _plan_metrics, _plan_year_range
from .facts import _build_financial_view, _chart_dataset, _comparison_facts, _format_value, _grounded_invoke, _scoreboard, _single_kpis
from .structure import SUMMARY_NOTE, _structured_part, _verdict_note


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

    # Scope to the fiscal-year window the question asks about ("2023 to 2025", "last five
    # years") instead of always plotting every cached year.
    plan = state.get("plan")
    year_range = _plan_year_range(plan, query, [r["year"] for r in financials])
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
    metrics = _plan_metrics(plan, query, default=METRIC_ORDER)
    view = _build_financial_view(financials, metrics)
    table, table_columns, markdown_table = view["table"], view["table_columns"], view["markdown_table"]
    chart_data = view["chart_data"]

    # Use LLM to formulate narrative using only the retrieved database numbers (preventing math hallucinations)
    metric_labels = ", ".join(METRIC_DEFS[m]["label"] for m in metrics)
    llm = _llm()
    # When understand_node activates both branches (see _build_graph), the *same*
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
        "Computed figures (exact — use these for every growth rate, CAGR, ratio, or margin, and do not "
        "calculate any other numbers yourself):\n{facts}\n\n"
        "Question: {question}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. Answer the question directly. The data itself is already shown to the user as a table, so "
        "do not repeat it as a table or list of numbers. Cite the source once as [1] (a single reference to the "
        "SQLite SEC EDGAR Facts DB, listed separately below your answer) — do not restate the source name after "
        "every point."
        + SUMMARY_NOTE + mixed_note + _verdict_note(plan, "the table and computed figures") + "{correction}"
    )

    # Same computed-facts helper as comparisons, run for one company: YoY, CAGR, leverage, margin.
    by_year = {r["year"]: r for r in financials}
    facts = _comparison_facts({ticker: by_year}, [ticker], metrics, sorted(by_year))
    facts_text = "\n".join(facts) if facts else "none"

    chain = prompt | llm | StrOutputParser()
    narrative = _grounded_invoke(
        chain,
        {"ticker": ticker, "table": markdown_table, "question": query, "metrics": metric_labels, "facts": facts_text},
        grounding=markdown_table + "\n" + facts_text,
    )

    return {
        "answer": narrative,
        "chart_data": chart_data,
        "chart_meta": {
            "type": "trend", "series": [company_name], "metrics": [METRIC_DEFS[m]["label"] for m in metrics],
            "metric_keys": metrics, "dataset": _chart_dataset({ticker: by_year}, [ticker]),
            "suggested": (plan or {}).get("chart"),
        },
        "table": table,
        "table_columns": table_columns,
        "sources": [{"content": f"Structured annual financial facts for {company_name}.", "metadata": {"source": get_company_facts_source(ticker)}}],
        "resolved_companies": resolved_companies,
        "structured": _structured_part("figures", narrative, kpis=_single_kpis(by_year, metrics)),
    }


def comparison_financial_result(tickers: List[str], query: str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Numeric multi-company pivot table + trend narrative — the quantitative half of a
    comparison query (see quantitative_node). Ticker resolution happens once up front in
    resolve_node instead of here, since this can run in the same parallel superstep as comparison_qualitative_result and shouldn't
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

    # Scope every company to the fiscal-year window the question asks about, when it names one.
    all_years = [r["year"] for t in tickers for r in financials_by_ticker[t]]
    year_range = _plan_year_range(plan, query, all_years)
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
    metrics = _plan_metrics(plan, query, default=["revenue", "net_income"])
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
                # A year with no value stays None (shown as n/a), never zero: a zero reads as a
                # real decline or collapse in the table, the chart, and the narrative.
                raw = by_ticker_by_year[t].get(yr, {}).get(m)
                if raw is None:
                    val = None
                else:
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
        "Computed figures (exact — use these for every growth rate, CAGR, ratio, or margin, and do "
        "not calculate any other numbers yourself):\n{facts}\n\n"
        "A value shown as n/a means that company has no data for that year. Say the data is "
        "missing; do not treat it as zero or as a decline.\n\n"
        "Question: {question}\n\n"
        "Format the answer as a bulleted list — one point per line, each starting with \"- \" — not prose "
        "paragraphs. Answer the question directly, then add how the companies differ. The data itself "
        "is already shown to the user as a table, so do not repeat it as a table or list of numbers. Cite the "
        "source once as [1] (a single reference to the SQLite SEC database, listed separately below your "
        "answer) — do not restate company or source names after every point."
        + SUMMARY_NOTE + _verdict_note(plan, "the table and computed figures") + "{correction}"
    )
    chain = prompt | llm | StrOutputParser()
    facts = _comparison_facts(by_ticker_by_year, tickers, metrics, years)
    facts_text = "\n".join(facts) if facts else "none"
    analysis = _grounded_invoke(
        chain,
        {"companies": company_list, "table": markdown_table, "metrics": metric_labels, "facts": facts_text, "question": query},
        grounding=markdown_table + "\n" + facts_text,
    )

    return {
        "answer": analysis,
        "chart_data": chart_data,
        "chart_meta": {
            "type": "comparison", "series": [names_by_ticker[t] for t in tickers],
            "metrics": [METRIC_DEFS[m]["label"] for m in metrics],
            "metric_keys": metrics, "dataset": _chart_dataset(by_ticker_by_year, tickers),
            "suggested": (plan or {}).get("chart"),
        },
        "table": table,
        "table_columns": table_columns,
        "sources": [
            {"content": f"Structured {names_by_ticker[t]} annual SEC facts.", "metadata": {"source": get_company_facts_source(t)}}
            for t in tickers
        ],
        "resolved_companies": [{"ticker": t, "name": names_by_ticker[t]} for t in tickers],
        "structured": _structured_part("figures", analysis,
                                       scoreboard=_scoreboard(by_ticker_by_year, tickers, metrics, years)),
    }
