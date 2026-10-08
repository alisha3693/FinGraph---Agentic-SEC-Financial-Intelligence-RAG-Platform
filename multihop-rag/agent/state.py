"""The LangGraph state passed between nodes."""

from typing import Dict, Any, List, Optional
from typing_extensions import TypedDict


# State definition
class AgentState(TypedDict):
    query: str
    route: str
    # Set by understand_node; read by resolve_node's fan-out decision and by
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
    # Set by understand_node: companies, metrics, years, and whether a verdict is asked for.
    # None when that LLM call failed and the keyword fallbacks are in use.
    plan: Optional[Dict[str, Any]]
    # Summary, verdict, titled sections, and figure tiles for the frontend (see _parse_answer).
    # Optional: the frontend falls back to rendering `answer` as bullets when it is None.
    structured: Optional[Dict[str, Any]]
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
