"""Filing retrieval: query expansion, hybrid dense + BM25 search fused with RRF, filing scoping, reranking."""

import json
import logging
import re
from bm25 import BM25Index
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from typing import Dict, Any, List, Optional, Tuple

from .config import _llm

logger = logging.getLogger(__name__)


# Query expansion for filing retrieval. The question's own wording often misses the passage
# that answers it (e.g. "regulatory changes in the EV industry" vs. the 10-K's "government
# regulations and policies affecting electric vehicles"), so the LLM rewrites it into
# filing-style phrasings and every phrasing is searched. Results are fused with reciprocal
# rank fusion, and the final context stays FINAL_CONTEXT_K chunks so the prompt size and
# citation count are unchanged from the single-query path.
EXPANSION_QUERIES = 2


RETRIEVAL_K_PER_QUERY = 4


FINAL_CONTEXT_K = 4


RRF_K = 60


def _expand_query(query: str) -> List[str]:
    """LLM rewrite of the question into alternative SEC-filing phrasings. Returns [] on any
    failure (logged, not silent) so retrieval falls back to the original question alone."""
    prompt = ChatPromptTemplate.from_template(
        "Rewrite the question below as {n} different search queries for finding passages in a "
        "company's SEC 10-K or 10-Q. Use the wording those filings typically use (section "
        "headings, risk-factor language, accounting terms) instead of casual wording. Keep the "
        "same company and topic. Respond with ONLY a JSON array of {n} strings, no other text.\n\n"
        "Question: {question}"
    )
    chain = prompt | _llm() | StrOutputParser()
    try:
        raw = chain.invoke({"question": query, "n": EXPANSION_QUERIES})
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
        return [q.strip() for q in parsed if isinstance(q, str) and q.strip()][:EXPANSION_QUERIES]
    except Exception:
        logger.warning("Query expansion failed; retrieving with the original question only", exc_info=True)
        return []


def _query_phrasings(query: str) -> List[str]:
    """The question plus its LLM-written alternative phrasings, without duplicates."""
    phrasings = [query]
    for alt in _expand_query(query):
        if alt.lower() not in (p.lower() for p in phrasings):
            phrasings.append(alt)
    return phrasings


def _filing_filter(ticker: str, metadatas: List[Dict[str, Any]], plan: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Chroma filter for one company, narrowed to the filing the question asks about ("its
    most recent 10-K") when the plan names one. Stays company-only when it names none or
    when no chunk matches, so a wrong guess can't leave retrieval empty."""
    company_only = {"company": ticker}
    if not plan or not plan.get("filing_type"):
        return company_only
    conditions = [{"company": ticker}, {"filing_type": plan["filing_type"]}]
    matching = [m for m in metadatas if m.get("filing_type") == plan["filing_type"]]
    if not matching:
        return company_only
    if plan.get("most_recent_filing"):
        latest = max(m.get("filing_date", "") for m in matching)
        conditions.append({"filing_date": latest})
    return {"$and": conditions}


def _matches_filter(meta: Dict[str, Any], where: Dict[str, Any]) -> bool:
    conditions = where.get("$and", [where])
    return all(meta.get(key) == value for cond in conditions for key, value in cond.items())


def _retrieve_with_expansion(db: Chroma, ticker: str, query: str, phrasings: Optional[List[str]] = None,
                             k: int = FINAL_CONTEXT_K, plan: Optional[Dict[str, Any]] = None) -> List[Any]:
    """Hybrid retrieval for one company's chunks. Each phrasing (the question plus its
    expansions) is searched twice: by embedding similarity (Chroma) and by keyword overlap
    (BM25, see bm25.py), which catches terms a filing uses verbatim that embeddings can
    rank too low. All ranked lists are then fused with reciprocal rank fusion. A chunk that
    several searches retrieve rises to the top; ties keep first-seen order."""
    if phrasings is None:
        phrasings = _query_phrasings(query)

    got = db.get(where={"company": ticker})
    where = _filing_filter(ticker, got["metadatas"], plan)
    retriever = db.as_retriever(search_kwargs={"k": max(RETRIEVAL_K_PER_QUERY, k), "filter": where})
    chunks = [
        Document(page_content=text, metadata=meta)
        for text, meta in zip(got["documents"], got["metadatas"])
        if _matches_filter(meta, where)
    ]
    keyword_index = BM25Index([chunk.page_content for chunk in chunks])

    ranked_lists = [retriever.invoke(phrasing) for phrasing in phrasings]
    ranked_lists += [
        [chunks[i] for i in keyword_index.top_k(phrasing, max(RETRIEVAL_K_PER_QUERY, k))]
        for phrasing in phrasings
    ]

    scores: Dict[Any, float] = {}
    docs_by_key: Dict[Any, Any] = {}
    for ranked in ranked_lists:
        for rank, doc in enumerate(ranked):
            # Same text can exist in two filings (boilerplate), so the metadata is part of the key.
            key = (doc.page_content, tuple(sorted((k, str(v)) for k, v in doc.metadata.items())))
            docs_by_key.setdefault(key, doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)

    fused_keys = sorted(scores, key=lambda key: -scores[key])[:k]
    return [docs_by_key[key] for key in fused_keys]


# Reranking. Fusion often puts the passage that answers the question at rank 5-12, below
# boilerplate that shares its words (forward-looking-statement disclaimers, tables of
# contents), so a pool of candidates is retrieved and the model picks the best ones. Only
# the start of each chunk is shown, to keep the call cheap on Groq's daily token limit.
RERANK_POOL = 12


RERANK_POOL_MANY = 8  # per company when a comparison covers more than three companies


RERANK_SNIPPET_CHARS = 500


def _rerank(query: str, pools: Dict[str, List[Any]], k: int) -> Dict[str, List[Any]]:
    """Pick the k most useful chunks per company from each pool, in one LLM call. The
    model's choices are validated (real passage numbers, from that company's pool) and
    topped up from fusion order. If the call fails, fusion order is kept."""
    fallback = {t: docs[:k] for t, docs in pools.items()}
    numbered: List[Tuple[str, Any]] = [(t, doc) for t, docs in pools.items() for doc in docs]
    if all(len(docs) <= k for docs in pools.values()):
        return fallback

    passages = "\n\n".join(
        f"[{i + 1}] ({t}) " + " ".join(doc.page_content[:RERANK_SNIPPET_CHARS].split())
        for i, (t, doc) in enumerate(numbered)
    )
    prompt = (
        "Below are numbered passages from SEC filings, each tagged with its company ticker.\n\n"
        f"Question: {query}\n\n{passages}\n\n"
        f"For each company, pick the {k} passages that best help answer the question: passages that "
        "actually discuss the topic asked about. Skip generic boilerplate (forward-looking-statement "
        "disclaimers, tables of contents, signatures, exhibit lists) unless the question asks about it. "
        "Respond with ONLY a JSON object mapping each ticker to a list of passage numbers, best first."
    )
    try:
        raw = _llm().invoke(prompt).content
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        parsed = json.loads(match.group(0) if match else raw)
    except Exception:
        logger.warning("Reranking failed; keeping fusion order", exc_info=True)
        return fallback

    picked: Dict[str, List[Any]] = {}
    for t, docs in pools.items():
        chosen: List[Any] = []
        for n in parsed.get(t, []) if isinstance(parsed, dict) else []:
            if isinstance(n, int) and 1 <= n <= len(numbered) and numbered[n - 1][0] == t:
                doc = numbered[n - 1][1]
                if doc not in chosen:
                    chosen.append(doc)
        for doc in docs:
            if len(chosen) >= k:
                break
            if doc not in chosen:
                chosen.append(doc)
        picked[t] = chosen[:k]
    return picked
