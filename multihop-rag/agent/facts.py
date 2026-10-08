"""Numbers computed in code: tables, growth and ratio facts, the figure check, tiles, scoreboard, chart data."""

import logging
import re
from typing import Dict, Any, List, Optional, Tuple

from .config import METRIC_DEFS, METRIC_ORDER

logger = logging.getLogger(__name__)


def _format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "$B":
        return f"${value:.2f}B"
    if unit == "$":
        return f"${value:.2f}"
    return str(value)


def _chart_dataset(by_ticker_by_year: Dict[str, Dict[int, Dict[str, Any]]], tickers: List[str]) -> Dict[str, Any]:
    """Every metric for every year and company, in display units ($B or $), so the frontend
    can draw any chart view without another request. Missing values stay None."""
    years = sorted({yr for t in tickers for yr in by_ticker_by_year[t]})
    values: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}
    for t in tickers:
        values[t] = {}
        for m in METRIC_ORDER:
            series = {}
            for yr in years:
                raw = by_ticker_by_year[t].get(yr, {}).get(m)
                series[str(yr)] = None if raw is None else (round(raw / 1e9, 4) if METRIC_DEFS[m]["unit"] == "$B" else round(raw, 4))
            values[t][m] = series
    return {
        "companies": tickers,
        "years": years,
        "metrics": {m: {"label": METRIC_DEFS[m]["label"], "unit": METRIC_DEFS[m]["unit"]} for m in METRIC_ORDER},
        "values": values,
    }


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


def _comparison_facts(by_ticker_by_year: Dict[str, Dict[int, Dict[str, Any]]], tickers: List[str], metrics: List[str], years: List[int]) -> List[str]:
    """Exact growth figures and ratios for the comparison narrative, computed here rather than
    by the model, which gets arithmetic wrong (multiples and CAGR claims contradicted the
    table). Each line is a finished fact the model can quote: a CAGR, year-over-year changes,
    a two-company ratio by year, or a note that a year has no data."""
    lines: List[str] = []
    for m in metrics:
        label = METRIC_DEFS[m]["label"]
        series_by_ticker: Dict[str, Dict[int, float]] = {
            t: {yr: row[m] for yr, row in by_ticker_by_year[t].items() if row.get(m) is not None}
            for t in tickers
        }
        for t in tickers:
            series = series_by_ticker[t]
            missing = [str(yr) for yr in years if yr not in series]
            if missing:
                lines.append(f"{t} {label}: no data for {', '.join(missing)}.")
            yoy = []
            for yr in sorted(series):
                prev = series.get(yr - 1)
                if prev and prev > 0:
                    yoy.append(f"{yr} {series[yr] / prev - 1:+.1%}")
            if yoy:
                lines.append(f"{t} {label} year-over-year: {', '.join(yoy)}.")

        # CAGR over the years every compared company has data, so the companies are measured
        # over the same window. A longer window for one company (2026 for MSFT, not Apple) would
        # not be comparable.
        common = [yr for yr in years if all(yr in series_by_ticker[t] for t in tickers)]
        if len(common) >= 2:
            first, last = common[0], common[-1]
            for t in tickers:
                start, end = series_by_ticker[t][first], series_by_ticker[t][last]
                if start > 0 and end > 0:
                    cagr = (end / start) ** (1 / (last - first)) - 1
                    lines.append(f"{t} {label} CAGR over the common window {first}-{last}: {cagr:+.1%}.")
                else:
                    lines.append(f"{t} {label} CAGR over the common window {first}-{last}: not meaningful (non-positive value).")

        # Which company grew faster in each year, computed here so the model doesn't have to
        # compare the numbers itself. Only years where every company has a prior-year value count.
        leaders = []
        for yr in years:
            growth = {
                t: series_by_ticker[t][yr] / series_by_ticker[t][yr - 1] - 1
                for t in tickers
                if yr in series_by_ticker[t] and (yr - 1) in series_by_ticker[t] and series_by_ticker[t][yr - 1] > 0
            }
            if len(growth) == len(tickers):
                best = max(growth, key=growth.get)
                leaders.append(f"{yr} {best} ({growth[best]:+.1%})")
        if leaders and len(tickers) >= 2:
            lines.append(f"Faster year-over-year growth, by year: {', '.join(leaders)}.")
        # Which company is largest in each year (absolute level, not growth), so a "who is biggest"
        # claim comes from the data instead of the model's memory of the companies.
        if len(tickers) >= 2:
            level_leaders = []
            for yr in years:
                at_year = {t: series_by_ticker[t][yr] for t in tickers if yr in series_by_ticker[t]}
                if len(at_year) == len(tickers):
                    top = max(at_year, key=at_year.get)
                    level_leaders.append(f"{yr} {top}")
            if level_leaders:
                lines.append(f"Highest {label} (absolute level), by year: {', '.join(level_leaders)}.")
            latest_common = [yr for yr in years if all(yr in series_by_ticker[t] for t in tickers)]
            if latest_common:
                yr = latest_common[-1]
                ranked = sorted(tickers, key=lambda t: series_by_ticker[t][yr], reverse=True)
                lines.append(f"{label} ranking in {yr}, largest first: {', '.join(ranked)}.")
        if len(tickers) == 2:
            a, b = tickers
            ratios = [
                f"{yr} {series_by_ticker[a][yr] / series_by_ticker[b][yr]:.2f}x"
                for yr in years
                if yr in series_by_ticker[a] and yr in series_by_ticker[b] and series_by_ticker[b][yr] > 0
            ]
            if ratios:
                lines.append(f"{a} / {b} {label} ratio: {', '.join(ratios)}.")

    # Leverage: liabilities as a share of assets. Assets come from the rows directly, not from the
    # selected metrics, since a question about liabilities ("relative to its size") still needs them.
    if "liabilities" in metrics:
        leverage_by_ticker: Dict[str, Dict[int, float]] = {}
        for t in tickers:
            leverage = {
                yr: row["liabilities"] / row["assets"]
                for yr, row in by_ticker_by_year[t].items()
                if row.get("liabilities") is not None and row.get("assets")
            }
            leverage_by_ticker[t] = leverage
            if leverage:
                by_year = ", ".join(f"{yr} {leverage[yr]:.1%}" for yr in sorted(leverage))
                lines.append(f"{t} liabilities as a share of assets: {by_year}.")
        common = [yr for yr in years if all(yr in leverage_by_ticker[t] for t in tickers)]
        if common and len(tickers) >= 2:
            leaders = []
            for yr in common:
                most = max(tickers, key=lambda t: leverage_by_ticker[t][yr])
                leaders.append(f"{yr} {most} ({leverage_by_ticker[most][yr]:.1%})")
            lines.append(f"More leveraged (higher liabilities as a share of assets), by year: {', '.join(leaders)}.")
            latest = common[-1]
            lines.append(
                f"Latest common year {latest} liabilities as a share of assets: "
                + ", ".join(f"{t} {leverage_by_ticker[t][latest]:.1%}" for t in tickers) + "."
            )

    # Net margin, for any question about profit. Revenue comes from the rows directly for the
    # same reason assets do above.
    if "net_income" in metrics:
        for t in tickers:
            margin = {
                yr: row["net_income"] / row["revenue"]
                for yr, row in by_ticker_by_year[t].items()
                if row.get("net_income") is not None and row.get("revenue")
            }
            if margin:
                by_year = ", ".join(f"{yr} {margin[yr]:.1%}" for yr in sorted(margin))
                lines.append(f"{t} net margin (net income / revenue): {by_year}.")
    return lines


# Checking an answer's figures against the data it was given. The model is told to use only
# the table and computed facts, but it still sometimes does its own arithmetic or rounds a
# ratio from memory. Every figure with a decimal point, a %, an x, or a $ is compared with
# the numbers in the prompt; plain integers (years, counts, citation numbers) are skipped.
_FIGURE_RE = re.compile(r"(\$)?(\d[\d,]*\d|\d)(?:\.(\d+))?\s*(%|x(?![a-z])|×)?", re.I)


_SCALE_WORDS = {"trillion": 1000.0, "million": 0.001, "thousand": 0.000001}


def _figures(text: str) -> List[Tuple[float, float, bool, str]]:
    """(value, in $B when a scale word follows; rounding tolerance at the precision shown;
    whether it gets checked; raw text)."""
    found = []
    for m in _FIGURE_RE.finditer(text):
        start = m.start()
        if start > 0 and (text[start - 1].isalnum() or text[start - 1] in "._"):
            continue
        dollar, whole, frac, unit = m.groups()
        value = float(whole.replace(",", "") + ("." + frac if frac else ""))
        tolerance = 0.5 * 10 ** -(len(frac) if frac else 0)
        after = text[m.end():m.end() + 10].lower().lstrip()
        for word, scale in _SCALE_WORDS.items():
            if after.startswith(word):
                value *= scale
                tolerance *= scale
        found.append((value, tolerance, bool(dollar or unit or frac), m.group(0).strip()))
    return found


def _unsupported_figures(answer: str, grounding: str) -> List[str]:
    """Figures in the answer that don't match any number in the grounding text at the
    precision the answer shows ("12%" matches 12.3%, "$394.3B" matches 394.33)."""
    known = [value for value, _, _, _ in _figures(grounding)]
    unsupported = []
    for value, tolerance, checked, raw in _figures(answer):
        if not checked:
            continue
        if not any(abs(value - k) <= tolerance + 1e-9 for k in known):
            unsupported.append(raw)
    return unsupported


def _grounded_invoke(chain: Any, inputs: Dict[str, Any], grounding: str) -> str:
    """Run a prompt whose template ends with {correction}. If the answer states figures that
    aren't in `grounding`, ask once more naming them, and keep whichever answer has fewer."""
    answer = chain.invoke({**inputs, "correction": ""})
    unsupported = _unsupported_figures(answer, grounding)
    if not unsupported:
        return answer
    logger.warning("Answer used figures not in the data (%s); asking for a corrected answer", ", ".join(unsupported))
    retry = chain.invoke({**inputs, "correction": (
        "\n\nA previous draft of this answer stated these figures, which are not in the table or the "
        f"computed figures above: {', '.join(unsupported)}. Do not state them. Use only figures that "
        "appear above, quoted as given, and describe anything else in words without a number."
    )})
    still_unsupported = _unsupported_figures(retry, grounding)
    if still_unsupported:
        logger.warning("Corrected answer still has figures not in the data: %s", ", ".join(still_unsupported))
    return retry if len(still_unsupported) <= len(unsupported) else answer


def _fmt_metric(m: str, value: float) -> str:
    return _format_value(value / 1e9 if METRIC_DEFS[m]["unit"] == "$B" else value, METRIC_DEFS[m]["unit"])


def _trend(change: Optional[float]) -> str:
    if change is None or abs(change) < 0.0005:
        return "flat"
    return "up" if change > 0 else "down"


def _single_kpis(rows_by_year: Dict[int, Dict[str, Any]], metrics: List[str]) -> List[Dict[str, Any]]:
    """Figure tiles for one company: latest value of each metric in scope with its
    year-over-year change and CAGR, plus net margin and leverage when relevant."""
    years = sorted(rows_by_year)
    tiles = []
    for m in metrics:
        series = {yr: rows_by_year[yr][m] for yr in years if rows_by_year[yr].get(m) is not None}
        if not series:
            continue
        last = max(series)
        prev = series.get(last - 1)
        yoy = series[last] / prev - 1 if prev and prev > 0 else None
        first = min(series)
        cagr = None
        if last > first and series[first] > 0 and series[last] > 0:
            cagr = (series[last] / series[first]) ** (1 / (last - first)) - 1
        detail = []
        if yoy is not None:
            detail.append(f"{yoy:+.1%} YoY")
        if cagr is not None:
            detail.append(f"{cagr:+.1%} CAGR {first}–{last}")
        tiles.append({
            "label": f"{METRIC_DEFS[m]['label']} · {last}",
            "value": _fmt_metric(m, series[last]),
            "detail": " · ".join(detail),
            "trend": _trend(yoy),
            # Direction only, no good/bad colour: falling liabilities aren't bad news.
            "neutral": m == "liabilities",
        })

    def ratio_tile(label: str, num: str, den: str, neutral: bool = False) -> None:
        ratio = {yr: rows_by_year[yr][num] / rows_by_year[yr][den] for yr in years
                 if rows_by_year[yr].get(num) is not None and rows_by_year[yr].get(den)}
        if ratio:
            last = max(ratio)
            prev = ratio.get(last - 1)
            change = ratio[last] - prev if prev is not None else None
            tiles.append({
                "label": f"{label} · {last}",
                "value": f"{ratio[last]:.1%}",
                "detail": f"{change * 100:+.1f} pts YoY" if change is not None else "",
                "trend": _trend(change),
                "neutral": neutral,
            })

    if "net_income" in metrics:
        ratio_tile("Net margin", "net_income", "revenue")
    if "liabilities" in metrics:
        ratio_tile("Liabilities / assets", "liabilities", "assets", neutral=True)
    return tiles[:6]


def _scoreboard(by_ticker_by_year: Dict[str, Dict[int, Dict[str, Any]]], tickers: List[str],
                metrics: List[str], years: List[int]) -> Optional[Dict[str, Any]]:
    """Companies side by side for the latest year they all report, plus CAGR over the
    years they all report. `top` marks the highest value in each row (not "best": high
    leverage is not good)."""
    def common(m: Optional[str] = None, num: str = "", den: str = "") -> List[int]:
        def has(t: str, yr: int) -> bool:
            row = by_ticker_by_year[t].get(yr, {})
            return row.get(m) is not None if m else (row.get(num) is not None and bool(row.get(den)))
        return [yr for yr in years if all(has(t, yr) for t in tickers)]

    rows = []
    for m in metrics:
        yrs = common(m)
        if not yrs:
            continue
        last = yrs[-1]
        latest = {t: by_ticker_by_year[t][last][m] for t in tickers}
        rows.append({"label": f"{METRIC_DEFS[m]['label']} {last}",
                     "values": {t: _fmt_metric(m, v) for t, v in latest.items()},
                     "top": max(latest, key=latest.get)})
        first = yrs[0]
        if last > first:
            cagr = {}
            for t in tickers:
                a, b = by_ticker_by_year[t][first][m], by_ticker_by_year[t][last][m]
                if a > 0 and b > 0:
                    cagr[t] = (b / a) ** (1 / (last - first)) - 1
            if cagr:
                rows.append({"label": f"{METRIC_DEFS[m]['label']} CAGR {first}–{last}",
                             "values": {t: f"{cagr[t]:+.1%}" if t in cagr else "n/m" for t in tickers},
                             "top": max(cagr, key=cagr.get)})
    for label, num, den, wanted in (("Net margin", "net_income", "revenue", "net_income"),
                                    ("Liabilities / assets", "liabilities", "assets", "liabilities")):
        if wanted not in metrics:
            continue
        yrs = common(num=num, den=den)
        if yrs:
            last = yrs[-1]
            ratio = {t: by_ticker_by_year[t][last][num] / by_ticker_by_year[t][last][den] for t in tickers}
            rows.append({"label": f"{label} {last}",
                         "values": {t: f"{v:.1%}" for t, v in ratio.items()},
                         "top": max(ratio, key=ratio.get)})
    return {"companies": tickers, "rows": rows} if rows else None
