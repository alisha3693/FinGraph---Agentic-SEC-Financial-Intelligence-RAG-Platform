"""Shared settings: metric definitions, limits, chart and filing types, and the LLM client."""

import os
from functools import lru_cache
from langchain_groq import ChatGroq


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


FILING_TYPES = ("10-K", "10-Q", "8-K")


# Chart views the frontend can draw. The plan suggests one; the frontend shows it first
# when the data supports it, and the user can switch to any other the data supports.
CHART_TYPES = ("trend", "indexed", "yoy", "margin", "composition", "ranking", "growth", "scatter", "themes", "sources")
