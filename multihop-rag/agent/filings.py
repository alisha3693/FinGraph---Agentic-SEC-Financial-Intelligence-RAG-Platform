"""Answers from SEC filing text, for one company or a comparison."""

import os
from langchain_chroma import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, Any, List, Optional
from vector_store import get_embeddings, VECTOR_DB_DIR

from .config import _llm
from .state import AgentState
from .companies import _company_name_for_ticker, _resolve_ticker_or_clarify
from .structure import SUMMARY_NOTE, THEMES_NOTE, _structured_part, _verdict_note
from .retrieval import FINAL_CONTEXT_K, RERANK_POOL, RERANK_POOL_MANY, _query_phrasings, _rerank, _retrieve_with_expansion


def filing_rag_node(state: AgentState) -> Dict[str, Any]:
    ticker, clarification = _resolve_ticker_or_clarify(state)
    if clarification:
        return clarification

    query = state["query"]
    resolved_companies = [{"ticker": ticker, "name": _company_name_for_ticker(ticker)}]

    if not os.path.isdir(VECTOR_DB_DIR):
        return {"answer": f"No SEC filing index exists for {ticker}. Load the ticker first.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}
    db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())

    # Retrieve documents relevant to ticker — the question plus LLM rephrasings, fused (see
    # _retrieve_with_expansion for why the question's own wording alone misses passages).
    plan = state.get("plan")
    pool = _retrieve_with_expansion(db, ticker, query, k=RERANK_POOL, plan=plan)
    docs = _rerank(query, {ticker: pool}, FINAL_CONTEXT_K)[ticker]

    if not docs:
        return {"answer": f"No indexed SEC filing evidence is available for {ticker}.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}

    # Numbered 1..N in the same order as `sources` below, so the model can cite a chunk by
    # its bracketed number instead of restating its full source URL/filename inline.
    context = "\n\n".join(f"[{i + 1}] {doc.page_content}" for i, doc in enumerate(docs))

    llm = _llm()
    # Mirror of the same note in financial_analysis_node: when understand_node activates both
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
        "paragraphs. Use at most 10 bullets: merge related points into one bullet instead of listing "
        "every detail, and lead with the most material points. Ground every point in the context and cite it only by its bracketed number (e.g. [1], "
        "[2], matching the numbered context above) — do not restate the source URL or filename in your "
        "answer text, since the numbered sources are already listed separately below your answer."
        + THEMES_NOTE + SUMMARY_NOTE + mixed_note + "\n\nAnswer:"
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
        "chart_meta": {"suggested": (plan or {}).get("chart")},
        "table": [],
        "table_columns": [],
        "resolved_companies": resolved_companies,
        "structured": _structured_part("filings", answer),
    }


def comparison_qualitative_result(tickers: List[str], query: str, needs_financial: bool = False,
                                  plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Qualitative multi-company comparison — the qualitative half of a comparison query
    (see qualitative_node), and the counterpart to comparison_financial_result above.
    Chroma's filter is a single equality match, so this retrieves each company's chunks
    with its own call rather than one combined query, then asks the LLM to synthesize a
    comparative narrative citing across all of them."""
    names_by_ticker = {t: _company_name_for_ticker(t) for t in tickers}
    resolved_companies = [{"ticker": t, "name": names_by_ticker[t]} for t in tickers]

    if not os.path.isdir(VECTOR_DB_DIR):
        return {"answer": "No SEC filing index exists yet. Load the companies first.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}
    db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())

    # A smaller per-company k than the single-company path (4) keeps total retrieved
    # context bounded as the company count scales up to MAX_COMPARISON_COMPANIES.
    # Same hybrid retrieval as the single-company path (expansion + BM25 + RRF), then one rerank
    # call over every company's candidate pool. The question is expanded once and the phrasings
    # shared, so a 6-company comparison costs two extra LLM calls, not twelve.
    per_company_k = 4 if len(tickers) <= 3 else 3
    phrasings = _query_phrasings(query)
    pool_k = RERANK_POOL if len(tickers) <= 3 else RERANK_POOL_MANY
    pools = {t: _retrieve_with_expansion(db, t, query, phrasings=phrasings, k=pool_k, plan=plan) for t in tickers}
    docs_by_ticker = _rerank(query, pools, per_company_k)
    all_docs = [(t, doc) for t in tickers for doc in docs_by_ticker[t]]

    if not all_docs:
        return {"answer": f"No indexed SEC filing evidence is available for {', '.join(tickers)}.", "sources": [], "chart_data": [], "chart_meta": {}, "table": [], "table_columns": [], "resolved_companies": resolved_companies}

    # Numbered 1..N across all companies combined, each line tagged with its company so the
    # model attributes a point to the right company instead of just citing a bare number.
    context = "\n\n".join(f"[{i + 1}] ({names_by_ticker[t]}) {doc.page_content}" for i, (t, doc) in enumerate(all_docs))

    llm = _llm()
    company_list = ", ".join(tickers[:-1]) + f" and {tickers[-1]}" if len(tickers) > 2 else " and ".join(tickers)
    # Mirror of the same note in financial_analysis_node/filing_rag_node: when understand_node
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
        "paragraphs. Use at most 6 bullets: merge related points into one bullet instead of listing "
        "every detail, and lead with the most material points. Ground every point in the context and cite it only by its bracketed number (e.g. [1], "
        "[2], matching the numbered context above) — do not restate the source URL or filename in your "
        "answer text, since the numbered sources are already listed separately below your answer."
        + THEMES_NOTE + SUMMARY_NOTE + mixed_note + _verdict_note(plan, "the filing context above") + "\n\nAnswer:"
    )
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"companies": company_list, "context": context, "question": query})

    return {
        "answer": answer,
        "sources": [{"content": doc.page_content, "metadata": doc.metadata} for _, doc in all_docs],
        "chart_data": [],
        "chart_meta": {"suggested": (plan or {}).get("chart")},
        "table": [],
        "table_columns": [],
        "resolved_companies": resolved_companies,
        "structured": _structured_part("filings", answer),
    }
