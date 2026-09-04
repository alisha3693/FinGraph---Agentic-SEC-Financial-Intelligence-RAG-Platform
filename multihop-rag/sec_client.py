import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from db_manager import save_financials
from vector_store import get_embeddings, VECTOR_DB_DIR

load_dotenv()
logger = logging.getLogger(__name__)

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT")
if not SEC_USER_AGENT:
    raise RuntimeError(
        "SEC_USER_AGENT is not set. SEC EDGAR requires a real identifying header "
        "(e.g. 'YourAppName your-real-email@example.com') and blocks/throttles requests "
        "using placeholder or example.com contacts. Set SEC_USER_AGENT in multihop-rag/.env."
    )
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SEC_BASE_URL = "https://www.sec.gov"
DATA_BASE_URL = "https://data.sec.gov"
# Bounded concurrency for filing downloads: fast enough to matter (measured ~3x faster
# than sequential for a 12-filing batch) while staying well under SEC's ~10 req/sec
# fair-access guideline.
MAX_CONCURRENT_FILING_DOWNLOADS = 4


def _get_json(url: str) -> dict[str, Any]:
    response = requests.get(url, headers=SEC_HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


@lru_cache(maxsize=1)
def _ticker_reference() -> dict[str, tuple[str, str]]:
    data = _get_json(f"{SEC_BASE_URL}/files/company_tickers.json")
    return {
        item["ticker"].upper(): (str(item["cik_str"]).zfill(10), item["title"])
        for item in data.values()
    }


def resolve_ticker_to_cik(ticker: str) -> tuple[str | None, str | None]:
    """Resolve a ticker using the SEC-maintained ticker reference."""
    normalized = ticker.strip().upper()
    try:
        return _ticker_reference().get(normalized, (None, None))
    except requests.RequestException:
        logger.exception("SEC ticker lookup failed for %s", normalized)
        return None, None


def fetch_company_facts(cik: str) -> dict[str, Any] | None:
    try:
        return _get_json(f"{DATA_BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json")
    except requests.RequestException:
        logger.exception("SEC Company Facts request failed for %s", cik)
        return None


_ANNUAL_FRAME_RE = re.compile(r"^CY\d{4}$")
_INSTANT_FRAME_RE = re.compile(r"^CY\d{4}Q\dI$")


def _end_year(end: str) -> int | None:
    try:
        return datetime.strptime(end, "%Y-%m-%d").year
    except (TypeError, ValueError):
        return None


def _duration_days(start: str, end: str) -> int | None:
    try:
        return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return None


def _annual_value(entries: list[dict[str, Any]], year: int, instant: bool = False) -> float | None:
    """Pick the single value for a fiscal year out of a list of XBRL fact entries.

    Matches on the fact's own reporting period (`start`/`end` dates), not on `fy`/`fp` —
    `fy` is the fiscal year *of the filing* a value was reported in, not necessarily the
    year the value covers. A 10-K's prior-year comparative figures carry the *current*
    filing's `fy` (e.g. Tesla's FY2021/2022 revenue shows up tagged `fy=2023` inside its
    2023 10-K), so matching on `fy` silently drops every year that was later restated as
    a comparative — which is most of them.

    Buckets by the `end` date's calendar year rather than requiring a literal
    Jan 1-Dec 31 span, so companies with non-calendar fiscal years are covered too —
    e.g. Microsoft's FY2024 duration fact runs `2023-07-01` to `2024-06-30`, which a
    strict `{year}-01-01`/`{year}-12-31` match never finds. This also matches how
    companies label their own fiscal years (by the calendar year the FY ends in).
    """
    if instant:
        candidates = [e for e in entries if e.get("form") == "10-K" and _end_year(e.get("end", "")) == year]
    else:
        candidates = [
            e for e in entries
            if e.get("form") == "10-K"
            and _end_year(e.get("end", "")) == year
            and (_duration_days(e.get("start", ""), e.get("end", "")) or 0) >= 340
        ]
    if not candidates:
        return None
    # SEC stamps `frame` only on the one de-duplicated canonical value per period, so
    # prefer it over the (often several) duplicate values re-reported as comparatives in
    # later filings; break ties by most recently filed.
    frame_re = _INSTANT_FRAME_RE if instant else _ANNUAL_FRAME_RE
    candidates.sort(key=lambda e: (bool(frame_re.match(e.get("frame") or "")), e.get("filed", "")), reverse=True)
    return candidates[0].get("val")


def parse_financials(facts_json: dict[str, Any] | None) -> dict[int, dict[str, float | None]]:
    """Extract the latest annual US-GAAP values available for each fiscal year."""
    if not facts_json:
        return {}
    gaap = facts_json.get("facts", {}).get("us-gaap", {})
    metric_tags = {
        "revenue": [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "NetProductSales",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "ServiceSalesRevenueNet",
            "ProductSalesRevenueNet",
        ],
        "net_income": ["NetIncomeLoss"],
        "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
        "assets": ["Assets"],
        "liabilities": ["Liabilities"],
    }
    # Companies occasionally file FY data slightly ahead of the calendar year end,
    # so keep a 1-year lookahead instead of a fixed cutoff that goes stale.
    current_year = datetime.now(timezone.utc).year
    years = range(2018, current_year + 2)
    result = {year: {} for year in years}
    for metric, tags in metric_tags.items():
        for tag in tags:
            if tag not in gaap:
                continue
            unit_entries = next(iter(gaap[tag].get("units", {}).values()), [])
            for year in years:
                value = _annual_value(unit_entries, year, instant=metric in {"assets", "liabilities"})
                if value is not None and metric not in result[year]:
                    result[year][metric] = value
            # Only stop trying further tags once every year is covered — a company can
            # switch which XBRL tag it reports its total under across years (Tesla tags
            # revenue as `Revenues` for some fiscal years and
            # `RevenueFromContractWithCustomerExcludingAssessedTax` for others), so the
            # first tag that fills *some* years must not block fallback tags from filling
            # the rest.
            if all(metric in result[year] for year in years):
                break
    return {year: metrics for year, metrics in result.items() if metrics}


def _filing_records(cik: str) -> list[dict[str, Any]]:
    submissions = _get_json(f"{DATA_BASE_URL}/submissions/CIK{cik}.json")
    recent = submissions.get("filings", {}).get("recent", {})
    records = []
    for index, form in enumerate(recent.get("form", [])):
        if form not in {"10-K", "10-Q", "8-K"}:
            continue
        accession = recent["accessionNumber"][index]
        primary_document = recent["primaryDocument"][index]
        records.append({
            "form": form,
            "filing_date": recent["filingDate"][index],
            "fiscal_year": recent.get("reportDate", [""])[index][:4],
            "accession": accession,
            "document": primary_document,
            "url": f"{SEC_BASE_URL}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_document}",
        })
    return records[:12]


def _filing_text(record: dict[str, Any]) -> str:
    response = requests.get(record["url"], headers=SEC_HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def ingest_filings(ticker: str, cik: str, name: str) -> int:
    """Sync this ticker's vector documents to its current 12 most recent filings.

    Diffs against what's already indexed by `accession_number` (SEC's own unique ID per
    filing, stored on every chunk's metadata) instead of always wiping and re-embedding
    everything: filings that fell out of the most-recent-12 window are pruned, filings
    already indexed are left untouched, and only genuinely new filings are downloaded and
    embedded. A routine re-ingest of an already-loaded, unchanged company is therefore a
    near no-op instead of a full re-download-and-re-embed.
    """
    records = _filing_records(cik)
    current_accessions = {r["accession"] for r in records}

    vector_db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())
    existing = vector_db.get(where={"company": ticker})
    existing_ids = existing.get("ids", [])
    existing_metadatas = existing.get("metadatas", [])
    existing_accessions = {m.get("accession_number") for m in existing_metadatas}

    # Prune chunks belonging to filings no longer in the current top-12 window, so the
    # store stays in sync with what SEC currently reports instead of only ever growing.
    stale_ids = [
        doc_id for doc_id, meta in zip(existing_ids, existing_metadatas)
        if meta.get("accession_number") not in current_accessions
    ]
    if stale_ids:
        vector_db.delete(ids=stale_ids)

    new_records = [r for r in records if r["accession"] not in existing_accessions]
    if not new_records:
        total = len(existing_ids) - len(stale_ids)
        logger.info("No new filings to index for %s (%d already indexed, %d pruned).", ticker, total, len(stale_ids))
        return total

    # Filing downloads dominated wall-clock time when done one at a time (~10s for 12
    # filings); a small bounded pool cuts that to ~3s without exceeding SEC's rate limit.
    texts_by_index: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FILING_DOWNLOADS) as pool:
        futures = {pool.submit(_filing_text, record): i for i, record in enumerate(new_records)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                texts_by_index[index] = future.result()
            except requests.RequestException:
                logger.exception("Could not download filing %s", new_records[index]["accession"])

    # Larger chunks (1800/220 vs. the previous 1200/180) cut the chunk count ~35% and the
    # local CPU embedding pass roughly in half, with no meaningful loss in retrieval quality
    # for filing-length prose.
    splitter = RecursiveCharacterTextSplitter(chunk_size=1800, chunk_overlap=220)
    documents: list[Document] = []
    for index, record in enumerate(new_records):
        text = texts_by_index.get(index)
        if not text:
            continue
        for chunk in splitter.split_text(text):
            section_match = re.search(r"(ITEM\s+\d+[A-Z]?\.?\s+[A-Z][^\d]{2,80})", chunk, re.IGNORECASE)
            section = section_match.group(1).strip() if section_match else "Filing text"
            documents.append(Document(
                page_content=chunk,
                metadata={
                    "company": ticker,
                    "company_name": name,
                    "cik": cik,
                    "filing_type": record["form"],
                    "fiscal_year": record["fiscal_year"],
                    "filing_date": record["filing_date"],
                    "section": section,
                    "accession_number": record["accession"],
                    "source": record["url"],
                },
            ))
    if documents:
        vector_db.add_documents(documents)

    total = len(existing_ids) - len(stale_ids) + len(documents)
    logger.info(
        "Indexed %d new SEC filing chunks for %s (%d filings unchanged/skipped, %d pruned). Total chunks now: %d.",
        len(documents), ticker, len(records) - len(new_records), len(stale_ids), total,
    )
    return total


def delete_ticker_vectors(ticker: str) -> None:
    """Remove all indexed filing chunks for a ticker from the Chroma store."""
    if not os.path.isdir(VECTOR_DB_DIR):
        return
    normalized = ticker.strip().upper()
    vector_db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=get_embeddings())
    existing = vector_db.get(where={"company": normalized})
    if existing.get("ids"):
        vector_db.delete(ids=existing["ids"])


def load_company_data(ticker: str) -> tuple[bool, str]:
    normalized = ticker.strip().upper()
    cik, name = resolve_ticker_to_cik(normalized)
    if not cik or not name:
        return False, f"SEC could not resolve ticker {normalized}."
    facts = fetch_company_facts(cik)
    financials = parse_financials(facts)
    if not financials:
        return False, f"SEC returned no annual financial facts for {normalized}."
    save_financials(normalized, cik, name, financials)
    chunk_count = ingest_filings(normalized, cik, name)
    if not chunk_count:
        return False, f"Saved financial facts, but SEC filings could not be indexed for {normalized}."
    # chunk_count is the total now indexed (existing + new − pruned), not just what changed
    # this call — accurate whether this is a first ingest or a resync of an already-loaded ticker.
    return True, f"Loaded {name} ({normalized}) from SEC EDGAR: {chunk_count} filing chunks indexed."
