import logging
import os
import re
from functools import lru_cache
from typing import Any

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from db_manager import save_financials

load_dotenv()
logger = logging.getLogger(__name__)

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "SECFinancialAnalyzer contact@example.com")
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
SEC_BASE_URL = "https://www.sec.gov"
DATA_BASE_URL = "https://data.sec.gov"
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
VECTOR_DB_DIR = os.path.join(CURRENT_DIR, "chroma_db")


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


def _annual_value(entries: list[dict[str, Any]], year: int, instant: bool = False) -> float | None:
    candidates = [
        entry for entry in entries
        if entry.get("form") == "10-K" and entry.get("fy") == year
        and (instant or entry.get("fp") == "FY")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda entry: entry.get("filed", ""), reverse=True)
    return candidates[0].get("val")


def parse_financials(facts_json: dict[str, Any] | None) -> dict[int, dict[str, float | None]]:
    """Extract the latest annual US-GAAP values available for each fiscal year."""
    if not facts_json:
        return {}
    gaap = facts_json.get("facts", {}).get("us-gaap", {})
    metric_tags = {
        "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
        "net_income": ["NetIncomeLoss"],
        "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
        "assets": ["Assets"],
        "liabilities": ["Liabilities"],
    }
    years = range(2018, 2027)
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
            if any(metric in result[year] for year in years):
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
    """Download recent SEC filings and replace this ticker's vector documents."""
    records = _filing_records(cik)
    splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=180)
    documents: list[Document] = []
    for record in records:
        try:
            text = _filing_text(record)
        except requests.RequestException:
            logger.exception("Could not download filing %s", record["accession"])
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
    if not documents:
        return 0
    embeddings = HuggingFaceEmbeddings(model_name=os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    vector_db = Chroma(persist_directory=VECTOR_DB_DIR, embedding_function=embeddings)
    existing = vector_db.get(where={"company": ticker})
    if existing.get("ids"):
        vector_db.delete(ids=existing["ids"])
    vector_db.add_documents(documents)
    logger.info("Indexed %d SEC filing chunks for %s", len(documents), ticker)
    return len(documents)


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
    return True, f"Loaded {name} ({normalized}) from SEC EDGAR: {chunk_count} filing chunks indexed."
