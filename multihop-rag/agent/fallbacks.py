"""Keyword rules used only when the understanding LLM call fails (see understand.py)."""

import re
from db_manager import get_companies
from typing import List, Optional, Tuple

from .config import METRIC_ORDER
from .companies import _comparison_tickers


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


def _keyword_is_comparison(query: str) -> bool:
    """Fallback only, used when the understanding LLM call fails: comparison wording plus
    two or more named companies. understand_node normally decides this instead, since a
    word list misses phrasings like "which of the loaded companies has the most debt"."""
    q = query.lower()
    if not any(k in q for k in ["compare", "comparison", "versus", " vs ", "difference", "better"]):
        return False
    return len(_comparison_tickers(query, get_companies())) >= 2
