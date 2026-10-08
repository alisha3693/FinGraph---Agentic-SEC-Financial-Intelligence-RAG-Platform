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


# Generic corporate-name words excluded from the name index below — indexing them would
# make almost every one of the ~10k SEC-registered companies a "match" for common phrases
# like "the group" or "national holdings" instead of a real single-company reference.
_GENERIC_NAME_WORDS = {
    "corp", "corporation", "company", "companies", "group", "holding", "holdings",
    "international", "national", "global", "systems", "technologies", "technology",
    "industries", "industry", "enterprises", "incorporated", "limited", "trust", "fund",
}


@lru_cache(maxsize=1)
def _company_name_word_index() -> dict[str, set[str]]:
    """word -> set of tickers whose SEC-registered company name contains that word (len>3,
    excluding generic corporate words). Built once from the full ~10k-company SEC ticker
    reference so a query can be checked against every SEC-registered company — not just
    ones already ingested — without re-scanning the whole reference on every call."""
    index: dict[str, set[str]] = {}
    for ticker, (_, name) in _ticker_reference().items():
        for word in re.findall(r"[a-z]+", name.lower()):
            if len(word) > 3 and word not in _GENERIC_NAME_WORDS:
                index.setdefault(word, set()).add(ticker)
    return index


def find_unloaded_ticker_candidates(query: str, exclude: set[str]) -> list[str]:
    """Find tickers anywhere in the full SEC universe (excluding `exclude`, typically the
    already-loaded tickers) referenced by symbol or company name in free text — lets a
    query auto-resolve/auto-load a company that hasn't been ingested yet, including two at
    once for a comparison naming two new companies.

    Both matching modes require the word to actually be capitalized as typed — the natural
    English signal for a proper noun. This was tightened after two real false positives in
    testing: matching lowercased words let "net" (from "net income") auto-load Cloudflare
    (ticker NET), and let "trend" (from "the general trend") auto-load an obscure filer
    literally named "High-Trend International Group" — being the *only* SEC company whose
    name contains a word is not enough on its own, since the ~10k-company reference includes
    many tiny/obscure filers with ordinary-English-word names. Requiring capitalization (and
    for bare symbols, full uppercase) rules out both without a hand-maintained stopword list.
    The query's own first word is excluded from the name check, since English capitalizes it
    regardless of whether it's a proper noun."""
    ref = _ticker_reference()
    raw_words = re.findall(r"[A-Za-z0-9]+", query)

    # Exact ticker-symbol match: only trusted typed in its real uppercase form (e.g. "AMD",
    # "IBM") — each match is independently high-confidence, so multiple at once (a
    # comparison naming two real tickers) all come back together.
    symbol_matches = sorted({w for w in raw_words if len(w) >= 3 and w == w.upper() and w in ref} - exclude)
    if symbol_matches:
        return symbol_matches

    index = _company_name_word_index()
    found: list[str] = []
    for word in raw_words[1:]:
        if not word[:1].isupper():
            continue
        tickers = index.get(word.lower())
        if tickers and len(tickers) == 1:
            (ticker,) = tickers
            if ticker not in exclude and ticker not in found:
                found.append(ticker)
    return found


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


def _annual_entry(entries: list[dict[str, Any]], year: int, instant: bool = False) -> dict[str, Any] | None:
    """Pick the single fact entry for a fiscal year out of a list of XBRL fact entries.

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
    return candidates[0]


def _annual_value(entries: list[dict[str, Any]], year: int, instant: bool = False) -> float | None:
    entry = _annual_entry(entries, year, instant)
    return entry.get("val") if entry else None


# A restated value counts as a stock split only when the old/new ratio is this close to a whole
# number of at least 2 (EPS is rounded to cents, so 1.74 -> 0.17 reads as 10.2x, not 10x).
SPLIT_RATIO_TOLERANCE = 0.05


def _split_events(entries: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Stock splits inferred from the filings themselves, as (last_filed_before_split, factor).

    A per-share figure is reported again, on the new share basis, in later 10-Ks that carry
    it as a comparative. When the same fiscal period appears in two filings with values
    whose ratio is a whole number (4.52 then 1.13 -> a 4-for-1 split), the split happened
    between those two filing dates. Evidence from different periods for the same split is
    merged by overlapping filing-date intervals. Nothing is looked up or hardcoded: no
    split dates or ratios are assumed."""
    by_period: dict[tuple[str, str], dict[str, float]] = {}
    for e in entries:
        if e.get("form") != "10-K" or e.get("val") in (None, 0):
            continue
        if (_duration_days(e.get("start", ""), e.get("end", "")) or 0) < 340:
            continue
        by_period.setdefault((e["start"], e["end"]), {}).setdefault(e.get("filed", ""), e["val"])

    evidence = []  # (old_filed, new_filed, factor); factor > 1 for a forward split
    for filings in by_period.values():
        ordered = sorted(filings.items())
        for (old_filed, old_val), (new_filed, new_val) in zip(ordered, ordered[1:]):
            if old_val * new_val <= 0:
                continue
            ratio = abs(old_val / new_val)
            factor = ratio if ratio >= 1 else 1 / ratio
            whole = round(factor)
            if whole >= 2 and abs(factor - whole) / whole <= SPLIT_RATIO_TOLERANCE:
                evidence.append((old_filed, new_filed, float(whole) if ratio >= 1 else 1 / whole))

    clusters: list[dict[str, Any]] = []
    for old_filed, new_filed, factor in sorted(evidence, key=lambda x: x[1]):
        for c in clusters:
            if c["factor"] == factor and old_filed < c["new"] and c["old"] < new_filed:
                c["old"], c["new"] = max(c["old"], old_filed), min(c["new"], new_filed)
                break
        else:
            clusters.append({"old": old_filed, "new": new_filed, "factor": factor})
    return [(c["old"], c["factor"]) for c in clusters]


def _split_adjusted_eps(entries: list[dict[str, Any]], year: int) -> float | None:
    """EPS for a fiscal year on the share basis of the newest filing, so every year in the
    series is comparable. A value filed on or before a split's last pre-split filing is
    divided by that split's factor."""
    entry = _annual_entry(entries, year)
    if not entry or entry.get("val") is None:
        return None
    value = entry["val"]
    for last_pre_split_filed, factor in _split_events(entries):
        if entry.get("filed", "") <= last_pre_split_filed:
            value /= factor
    return value


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
                if metric == "eps":
                    value = _split_adjusted_eps(unit_entries, year)
                else:
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
    # Some filers (Amazon) never tag a total "Liabilities" figure. Their balance sheet still gives
    # total liabilities-and-equity and stockholders' equity, and the difference is total liabilities.
    # Only used for years with no tagged liabilities value.
    liabilities_and_equity = gaap.get("LiabilitiesAndStockholdersEquity", {}).get("units", {})
    stockholders_equity = gaap.get("StockholdersEquity", {}).get("units", {})
    total_entries = next(iter(liabilities_and_equity.values()), [])
    equity_entries = next(iter(stockholders_equity.values()), [])
    for year in years:
        if "liabilities" in result[year]:
            continue
        total = _annual_value(total_entries, year, instant=True)
        equity = _annual_value(equity_entries, year, instant=True)
        if total is not None and equity is not None:
            result[year]["liabilities"] = total - equity

    return {year: metrics for year, metrics in result.items() if metrics}


# Filings indexed per company, and the forms guaranteed a slot in that window (see
# _filing_records). Annual and quarterly reports carry most of the substance that filing
# questions need, so they can't be crowded out by 8-K volume.
FILING_WINDOW_SIZE = 12
GUARANTEED_FORMS = ("10-K", "10-Q")


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
    # Keep the latest filing of each guaranteed form, then fill the remaining slots with the
    # newest filings of any form. Without the guarantee, a filer that files many 8-Ks
    # (NVIDIA, Amazon) fills a plain newest-12 window with 8-Ks and 10-Qs, pushing its 10-K
    # out of the index entirely. SEC lists newest first, so the first match per form is the latest.
    keep: set[str] = set()
    for form in GUARANTEED_FORMS:
        latest = next((r["accession"] for r in records if r["form"] == form), None)
        if latest:
            keep.add(latest)
    for record in records:
        if len(keep) >= FILING_WINDOW_SIZE:
            break
        keep.add(record["accession"])
    return [r for r in records if r["accession"] in keep]


def _filing_text(record: dict[str, Any]) -> str:
    response = requests.get(record["url"], headers=SEC_HEADERS, timeout=45)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def ingest_filings(ticker: str, cik: str, name: str) -> int:
    """Sync this ticker's vector documents to its current filing window (see
    _filing_records: the latest 10-K and 10-Q are always included, plus the newest filings
    to fill 12 slots). Diffs against what's already indexed by `accession_number` (SEC's own
    unique ID per filing, stored on every chunk's metadata) instead of always wiping and
    re-embedding everything: filings that fell out of the window are pruned, filings
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
