"""Resolving which companies a question is about, and the replies when it can't."""

import difflib
import re
from db_manager import get_companies
from sec_client import find_unloaded_ticker_candidates, resolve_ticker_to_cik
from typing import Dict, Any, List, Optional, Tuple

from .config import MAX_COMPARISON_COMPANIES
from .state import AgentState


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


ALL_COMPANIES_RE = re.compile(
    r"\b(?:all|every|each)\b(?:\s+\w+){0,3}?\s+(?:compan(?:y|ies)|firms|stocks|tickers)\b"
    r"|\bloaded\s+compan(?:y|ies)\b",
    re.I,
)


def _comparison_tickers(query: str, all_companies: List[Dict[str, Any]]) -> List[str]:
    """Companies a comparison question covers: the ones it names, or every loaded company when
    it asks about them as a group ("all six loaded companies", "each company"). Capped at
    MAX_COMPARISON_COMPANIES."""
    if ALL_COMPANIES_RE.search(query):
        return [co["ticker"].upper() for co in all_companies][:MAX_COMPARISON_COMPANIES]
    return _find_companies(query, all_companies, limit=MAX_COMPARISON_COMPANIES)


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

    plan = state.get("plan")
    if plan is not None:
        # The plan's companies are already checked against real tickers; its first entry is
        # the question's main subject.
        matched = plan["tickers"][:1]
    else:
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
