# Test Suite: 30 Qualitative Questions — Batch Results

_Scope: the 30 qualitative reference questions from [project-status-report.md](project-status-report.md) — §7.2 (16–30) and the qualitative half of §7.3 (46–60). Run in batches of 5, in order, against the live backend through `run_agent()`._

**Method.** Question text is read from the status doc at run time, so the test set cannot drift from the doc. Nothing about the expected answer is hardcoded: each answer is produced by the agent, then judged by reading it against the question and the retrieved filing text. Where a claim needs checking, it is checked directly against the Chroma index or the SQLite data, not assumed. Classifier fallbacks are logged, since a silent fallback would change the route without any visible error.

**Batch status:** batches 1 (16–20, re-run after the query-expansion and bullet-cap changes) and 2 (21–25) complete. Batches 3–6 are not yet run; each one is reported before any fix is applied.

---

## Batch 1 — Questions 16–20

| # | Question | Company | Verdict | Notes |
|---|---|---|---|---|
| 16 | Apple supply chain risk factors | AAPL | **PASS** | Covers single/limited sources, commodity pricing, supplier failure, consolidation. Cited to sources 1 and 4. |
| 17 | Tesla raw material and battery supply risks | TSLA | **PASS** | Covers lithium/nickel price and availability, supplier reliability, the revenue and brand impact. All from one source (2). |
| 18 | Tesla EV regulatory-change risk | TSLA | **FAIL** (retrieval) | Answers "no information," which is incorrect: the 10-K has a regulatory section. See analysis below. |
| 19 | Apple litigation and legal proceedings | AAPL | **PASS** | The March 2, 2026 rehearing deadline is verified against the Q1 2026 10-Q. Minor: the answer mixes 10-Q and 10-K items without saying which filing each comes from. |
| 20 | NVIDIA export controls and geopolitical trade risk | NVDA | **PASS** | Accurate and well grounded, but long: 25 bullets for a single question. Verbosity is a quality issue, not a correctness one. |

**Batch 1 result: 4 PASS, 1 FAIL.** The one failure (Q18) was already a known failure in the §7.2 run.

### Q18 analysis — the content is in the index; the question's wording misses it

The agent's answer was built from four chunks, none about regulation. I probed the Tesla index directly with five phrasings of the same concern, retrieving the top 6 chunks for each (filter `company=TSLA`):

| Phrasing | Chunks mentioning "regulat" (of top 6) |
|---|---|
| The exact question: "What does Tesla's filing say about risks from regulatory changes in the EV industry?" | **0 / 6** |
| "government regulations and policies affecting electric vehicles" | **6 / 6** |
| "tax credits, emissions credits, and government incentives could change" | **5 / 6** |
| "regulatory approvals for autonomous driving and vehicle safety rules" | **5 / 6** |
| "we are subject to extensive government regulation and changes in laws" | **6 / 6** |

The 10-K contains a "Risks Related to Government Laws and Regulations" section, plus text on IRA tax-credit changes and automotive regulatory credits. Those chunks rank highly for the rephrasings and not at all for the literal question.

**Correction to the earlier diagnosis.** [test-suite-7.2-qualitative-rag.md](test-suite-7.2-qualitative-rag.md) attributed the Q18 miss to the embedding model and the lack of hybrid retrieval. This probe shows the content is retrievable, and the miss comes from the wording of the query. That makes query expansion a more targeted first fix than an embedding-model swap. It is still a hypothesis until it is tested on the failing queries.

---

---

## Batch 1 re-run — after query expansion and the bullet cap

**Changes under test:** (1) `filing_rag_node` retrieves for the question plus two LLM-generated SEC-filing phrasings and fuses the results with reciprocal rank fusion, keeping four chunks; (2) both filing prompts (single-company and comparison) cap answers at six bullets.

| # | Verdict (before → after) | Bullets (before → after) | Notes |
|---|---|---|---|
| 16 | PASS → PASS | 5 → 5 | Different chunks retrieved; the claims match the filing text. |
| 17 | PASS → PASS | 6 → 5 | Same substance, merged. |
| 18 | FAIL → **FAIL (partial)** | 0 → 9 | Covers incentive reductions, foreign regulatory compliance, and AV rules. Misses the two most material points in the 10-K: the repealed or restricted tax credits, and reliance on automotive regulatory-credit revenue. Re-graded from PASS after review. |
| 19 | PASS → PASS | 8 → 4 | The Supreme Court dates (May 21 and June 30, 2026) and the Q3 2026 settlements are verified verbatim in source [3]. Still does not say which filing each point comes from. |
| 20 | PASS → PASS | 21 → 6 | The Mellanox penalty point and the "design-out" language are verified in sources [3] and [2]. |

**Batch 1 result after the change: 4 of 5 pass, Q18 partial.** Q18 was the only verdict that moved, and it moved only partway (FAIL → partial). The earlier "5 of 5" was my error.

**Why Q18 is still partial (diagnosed).** The fused top four keeps two relevant chunks and two noise chunks. Noise comes from the question's literal wording: its top hit is 10-Q boilerplate, and reciprocal rank fusion gives it the same weight as every rephrasing. Dropping or down-weighting the literal query removes that noise. But the two chunks that answer the question best (repealed or restricted tax credits; regulatory-credit revenue) rank below the noise even then. Deeper candidate lists (k=8 per phrasing) bring the relevant share from 1 of 4 to 2 of 4 in simulation. Not yet applied.

**Expansion check on Q18.** The LLM generated: "Item 1A. Risk Factors: Changes in laws, regulations, or government policies affecting the electric vehicle industry" and "Regulatory compliance risks related to environmental standards, emissions regulations, and government incentives for electric vehicles." These are the real rewrites from the code, not hand-written phrasings.

**Regression check, comparison path.** One qualitative comparison query (Apple vs. Tesla supply chain) returned six bullets, so the cap holds there. However, Tesla's side still says its context doesn't cover the question. Query expansion is not applied to the comparison path yet, so that path can still miss the same way.

---

## Batch 2 — Questions 21–25

| # | Question | Company | Verdict | Notes |
|---|---|---|---|---|
| 21 | Microsoft foreign currency and international-operations risk | MSFT | **PASS** | The 10% FX sensitivity figure ($12,305M from revenue) is verified verbatim in source [1]. Hedging and trade-policy points are covered. Ten bullets, at the current single-company cap. |
| 22 | Amazon cybersecurity risks in its SEC filings | AMZN | **PASS** (data caveat) | Answer is grounded in the 2026 10-Q risk factors. Amazon's 10-K is not in the index, so the 10-K cybersecurity section is absent (see the data finding below). |
| 23 | NVIDIA intellectual property risks and protections | NVDA | **FAIL** (data gap) | Answer says the context doesn't cover IP. The NVIDIA 10-K is not in the index. Only two 10-Q chunks mention "intellectual property." Rephrased searches also find nothing, so this is not a retrieval-wording miss. |
| 24 | Microsoft competitive landscape in its most recent 10-K | MSFT | **PASS** (filing selection) | Competitive wording is verified in source [1]. However, the question asks for the most recent 10-K, and the answer draws from a 10-Q. Microsoft's 2026 10-K is indexed. |
| 25 | Apple forward-looking statements or growth strategy in its business overview | AAPL | **PASS** (scope) | Answers the forward-looking-statements half, which the "or" makes sufficient. Growth-strategy content is not included. Ten bullets, at cap. |

**Batch 2 result as first run: 4 PASS (with three caveats), 1 FAIL (data gap).** Re-checked after the ingest fix below: Q22 and Q23 now pass on the 10-K.

### Data finding: 10-Ks missing from the index for NVIDIA and Amazon

`_filing_records` in [sec_client.py](../multihop-rag/sec_client.py) keeps the **12 most recent** 10-K, 10-Q, and 8-K filings combined, with no per-form quota. Companies that file many 8-Ks fill that window with 8-Ks and 10-Qs, which pushes the 10-K out.

| Ticker | Latest 10-K (SEC) | Position in SEC list | In the 12-filing window? | Chunks in index that are 10-K |
|---|---|---|---|---|
| NVDA | 2026-02-25 | 100 | No | 0 |
| AMZN | 2026-02-06 | 137 | No | 0 |
| MSFT, TSLA, AAPL, NFLX | — | — | Yes | yes |

**Impact on the earlier §7.2 run.** The NVIDIA and Amazon misses attributed to embedding quality are data gaps: #22 (Amazon cybersecurity), #23 (NVIDIA IP), #26 (NVIDIA customer concentration), and #28 (Amazon critical accounting estimates). The [§7.2 results doc](test-suite-7.2-qualitative-rag.md) now carries a correction for these.

---

## Ingest fix applied — re-check of the affected queries

**Change:** `_filing_records` in [sec_client.py](../multihop-rag/sec_client.py) now always keeps the latest 10-K and the latest 10-Q, then fills the remaining slots of the 12-filing window with the newest filings of any form. Dry run against SEC's list: only NVIDIA and Amazon change (each gains its 10-K, loses one 8-K). The other four companies already had both reports in their windows. NVIDIA and Amazon were re-ingested: NVIDIA added 228 chunks and pruned 3; Amazon added 207 and pruned 12.

| # | Question | Before fix | After fix | Notes |
|---|---|---|---|---|
| 22 | Amazon cybersecurity risks | PASS (10-Q only) | **PASS** (10-K 2025) | Now cites the Chief Security Officer, audit-committee oversight, and incident response from the 10-K. |
| 23 | NVIDIA IP risks and protections | FAIL (no 10-K) | **PASS** (10-K 2026) | Cites patents, trade secrets, foreign IP protection, and employee-departure risk. Ten bullets, at cap. |
| 26 | NVIDIA customer concentration (§7.2) | FAIL (no 10-K) | **FAIL** (retrieval) | The 10-K does contain it: "We receive a significant amount of our revenue from a limited number of partners and distributors and we have a concentration of sales to customers…". The answer still says the context doesn't cover it, so this is a retrieval miss, the same family as Q18. |
| 28 | Amazon critical accounting estimates (§7.2) | FAIL (no 10-K) | **PARTIAL** | Cites the inventory-valuation sensitivity (about $405M per 1%). Other estimate topics appear in the indexed 10-K, such as useful lives and income taxes, but the answer names only inventory. |

**Net for the four affected §7.2 queries:** #22 and #28 move from fail to a working answer (#28 partial). #23 and #26 move from "data missing" to real content in the index (#23 passes, #26 is a retrieval miss).

### Q26 diagnosis after the ingest fix (before BM25)

The NVIDIA 10-K does contain the concentration risk (see the table above). Ranking it against all 477 NVIDIA chunks:

| Phrasing | Rank of the concentration chunk (of 477) |
|---|---|
| The literal question | 69 |
| LLM rewrite: "Risk Factors: Dependence on a limited number of customers or customer concentration" | **10** |
| LLM rewrite: "Concentration of credit risk and significant customer relationships in NVIDIA's 10-K" | 159 |

The rewrites were the same across three runs, so this is not run-to-run noise. The best rewrite ranks the chunk 10th, which is outside the four candidates per phrasing that the code keeps. Simulation:

- Deeper candidate lists (k=12 per phrasing) still return 0 of 4 and 0 of 6 key chunks. Fusion fills the final slots with the top-ranked chunks of each list, so a chunk at rank 10 never makes it.
- Down-weighting or dropping the literal question does not change this (0 of 4, 0 of 6).
- A cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`) over the 28-chunk union pool, which contains the chunk, still ranks it outside the top 6. It returns 0 of 4 for Q26 and 1 of 4 for Q18.

Not yet tested: keyword (BM25) search, which would match the exact phrase "customer concentration," and smaller chunks, since the concentration sentence sits inside a 1,800-character chunk that covers several risk topics.

---

## BM25 hybrid retrieval — results

**Change:** each phrasing is now searched two ways, by embedding similarity (Chroma) and by BM25 keyword overlap over the company's chunks ([bm25.py](../multihop-rag/bm25.py)). All ranked lists are fused with the same reciprocal rank fusion. No new dependency.

**Q26 (NVIDIA customer concentration) → PASS.** The concentration passage now ranks first in the final context. The answer covers the concentration of revenue across a limited number of direct and indirect customers, with the 10-K's figures: three direct customers at 21%, 17%, and 16% in Q1 FY2027, one at 22% for FY2026, and two others at 14%. Each figure was verified against the source text. Before this change, the same question missed the passage entirely.

**Q18 (Tesla EV regulatory risk) → still partial.** The answer covers incentive reductions, autonomous-vehicle rules, and foreign compliance. It still misses the two most material points: the repealed or restricted tax credits, and reliance on regulatory-credit revenue. Keyword search can't reach these passages, because their key terms ("repealed," "regulatory credit") aren't in the question or its rewrites.

**Batch 1 regression (16–20), against the expansion-only run:**

| # | Verdict | Bullets (before → after) | Notes |
|---|---|---|---|
| 16 | PASS | 5 → 8 | Still grounded in the supply-chain section. |
| 17 | PASS | 5 → 8 | — |
| 18 | partial | 9 → 9 | Unchanged; see above. |
| 19 | PASS | 4 → 6 | Adds a December 11, 2025 Ninth Circuit date. I did not re-verify this new claim against the source. |
| 20 | PASS | 6 → 10 | At the single-company cap of 10. |

The bullet increases come from the single-company cap being raised to 10 on disk, not from this change. Batch 1 ends at 4 of 5 pass, with Q18 partial, as before.

**Not yet re-run with BM25:** questions 21–25 and the §7.2 questions 22, 23, and 28. Those need a re-run before the hybrid change can be called validated across the set.

## Full re-check, questions 16–25, with BM25

Batch 1 (16–20) and batch 2 (21–25) both re-run on the current code (vector + BM25 hybrid, query expansion, ingest fix).

| # | Question | Verdict | Sources (10-K / 10-Q / 8-K) | Notes |
|---|---|---|---|---|
| 16 | Apple supply chain risks | PASS | 10-Q 2026, 10-K 2025 ×3 | — |
| 17 | Tesla raw material and battery supply | PASS | 10-K 2025 ×4 | — |
| 18 | Tesla EV regulatory risk | **PARTIAL** | 10-K 2025 ×3, 10-Q 2026 | Misses the repealed tax credits and regulatory-credit revenue. Unchanged. |
| 19 | Apple litigation | PASS | 10-K 2025 ×2, 10-Q 2025/2026 | The December 11, 2025 date and the Q3 2026 settlements are verified in the sources. |
| 20 | NVIDIA export controls | PASS | 10-Q 2026, 10-K 2026 ×2 | Ten bullets, at the single-company cap. |
| 21 | Microsoft FX and international risk | PASS | 10-Q ×4 | The $12,305M figure is verified. Six bullets. |
| 22 | Amazon cybersecurity | PASS | 10-K 2025 ×4 | Covers the Chief Security Officer, the Security Committee, and incident response from the 10-K. |
| 23 | NVIDIA IP risks | PASS | 8-K 2026, 10-K 2026 ×3 | The June 2045 patent expiry and the compulsory-licensing point are verified in the sources. |
| 24 | Microsoft competitive landscape, "most recent 10-K" | **PARTIAL** | 10-Q ×4 (no 10-K retrieved) | The content is accurate and verified, but the question asks for the 10-K, and no 10-K chunk was retrieved. This is the known filing-selection gap (status doc §2). Before BM25, the answer also cited a 10-Q. |
| 25 | Apple forward-looking statements or growth strategy | PASS | 10-K 2025, 10-Q ×3 | Covers forward-looking statements, as the "or" allows. The answer says the context has no growth-strategy content. |

**Result for 16–25: 8 pass, 2 partial (18 and 24).** Q18 is partial, as before. Q24 is partial for a different reason: it needs a filter on the filing type the question names.

---

## Form filter — reverted (kept for the record)

The filter below was reverted at the user's request. Q24 is back to its earlier behavior: 10-Q sources, five bullets. Q20 and Q26 cite whichever filings the unrestricted search returns, as before. The results below are the reason for the revert.

## Form filter applied — re-check of questions naming a form

**Change:** when a question names one SEC form ("10-K", "10-Q", "8-K"), both the vector and keyword searches only see that form. The detection is a generic regex on the question text, with no per-question logic. A question that names two forms, or none, is not restricted.

| # | Question | Form named | Result | Sources | Notes |
|---|---|---|---|---|---|
| 20 | NVIDIA export controls | 10-K | PASS | 10-K 2026 ×4 | Ten bullets, at cap. |
| 24 | Microsoft competitive landscape | 10-K | **PARTIAL (worse content)** | 10-K 2026 ×4 | The form is now correct, but the answer is a vague inference ("not fully described") rather than the competition passages. Those passages rank 3–19 in the 10-K-only pool, below the top-4 cutoff. |
| 26 | NVIDIA customer concentration | 10-K | PASS | 10-K 2026 ×4 | Concentration passage and the 22% and 14% customer figures, all from the 10-K. |

**Q24 diagnosis.** The competition chunks are in the 10-K. Under the filter they rank 3–15 in vector search and 7–19 in keyword search. Removing the "10-K" token from keyword queries changes nothing. Simulation: deeper candidate lists (8 per phrasing) raise the relevant share to 1 of 4, and a final context of 6 doesn't help. None of this is applied, and it would be a global change, so it needs approval.

## Batch 3 — Questions 26–30 (qualitative set)

| # | Question | Verdict | Sources | Notes |
|---|---|---|---|---|
| 26 | NVIDIA customer concentration (§7.2) | **PASS** | 10-K 2026 ×2, 10-Q 2026 ×2 | Concentration of revenue across a limited number of customers, with the 21%, 17%, 16%, 22%, and 14% figures. The Q1 FY2027 figures come from the 10-Q, and the answer says so. |
| 27 | Tesla MD&A liquidity and capital resources (§7.2) | **PASS** | 10-Q 2026, 10-K 2025, 10-Q 2025 ×2 | Discusses adequacy of liquidity over the 12 months after September 30, 2025, and the funding options. The $9.00B capex, $7.49B debt, and $1.86B current debt figures are in source [4]. Nine bullets. Classification correct: qualitative only. |
| 28 | Amazon critical accounting estimates (§7.2) | **PARTIAL** | 10-Q 2026 ×2, 10-K 2025 ×2 | Names inventory valuation ($405M sensitivity) and uncertain tax positions. The 10-K covers other estimates the answer doesn't list. **Classifier fell back to qualitative** on this query (logged error). The route still matches the question, but the cause is unconfirmed. The traceback was lost (the runner stored messages only), so the cause can't be read from the run. Reproduction: three single classifier calls and a burst of twelve all succeeded with no logged exception. Two candidates remain: a transient API error (for example a rate limit), or a malformed model response that `json.loads` rejects. The runner now keeps tracebacks, so the next occurrence will show which it was. |
| 29 | Amazon macroeconomic risks (§7.2) | **PASS** | 10-K 2025 ×2, 10-Q 2026 ×2 | FX, energy, tariffs, memory chips, recession fears, interest rates, and AI investment, all cited. Ten bullets, at cap. |
| 30 | Microsoft human capital and workforce risks (§7.2) | **PASS** | 10-Q 2025, 10-K 2026, 10-Q 2026 ×2 | Talent competition, immigration, succession, employment law, and unionization. This was a §7.2 FAIL before the BM25 and expansion changes. |

**Batch 3 result: 4 PASS, 1 PARTIAL (Q28).**

## §7.1 scope

This run also covers the 15 questions in §7.1, "Qualitative Analysis of DB Metrics." I left them out of the 30-question set earlier because I read "qualitative" as the text-retrieval questions. §7.1 is also labelled qualitative, so the test set should have included it. Batch 1 of §7.1 (questions 1–5) has run, and its results are not yet reviewed. Batches 2 and 3 of §7.1 have not run.

## §7.3.1 quantitative comparison — batch 1 (questions 31–35)

Checked against the SQLite data (`get_financials_for_ticker`), not against the answer text.

| # | Question | Verdict | Notes |
|---|---|---|---|
| 31 | Apple vs Microsoft revenue growth, five years | **PARTIAL** | Apple has no 2026 row, so the table shows $0.00B for 2026. The answer treats that as an anomaly. It also calls Microsoft's 2026 figure "projected," but the database row is not an estimate. "Consistent and accelerating" overstates it: 2023 growth was 6.9%. |
| 32 | Tesla vs NVIDIA revenue durability | **PARTIAL** | The table is right through 2025. The answer then says Tesla's revenue "turned negative in 2026," but Tesla has no 2026 row. The "$0.00B" is missing data, not a decline. The qualitative section cites filing figures I haven't verified. |
| 33 | Netflix vs Amazon revenue growth | **FAIL** | Says Netflix has "a higher compound annual growth rate," but Netflix's CAGR is about 16.2% and Amazon's about 17.4%. Says Netflix "generally outpaces" Amazon in 2018–2020, but Amazon grew 37.6% in 2020 against Netflix's 24%. Says Amazon has double-digit growth "throughout," but 2022 was 9.4%. Says Netflix slows to single digits "in the final years," but 2024 and 2025 were about 16%. |
| 34 | Tesla vs NVIDIA net income consistency | **PARTIAL** | The conclusion (NVIDIA more consistent) holds. The "$0.00B in 2026" claim and the claim that Tesla's profits "declined steadily … to 2026" both rest on missing data. The 39x NVIDIA figure is arithmetically right. |
| 35 | Apple vs Tesla revenue and net income discipline | **FAIL** | Says Apple's revenue is "four to five" times Tesla's in every year. The ratio was 12.4x in 2018 and 6.8x in 2021, and only dropped to 4–5x from 2022. Says Apple's net income is "an order of magnitude" larger in every profitable Tesla year. The ratio was 6.5x in 2023 and 7.9x in 2022. |

**Batch 1 result: 0 pass, 3 partial, 2 fail.** Two causes:
1. **Missing years are shown as $0.00.** The comparison code reads a missing year as zero, so Apple and Tesla look like they collapsed in 2026 (see the status doc, §1).
2. **The model computes growth rates and ratios itself, and gets them wrong.** The prompt sends only the raw table, so the model does the arithmetic and it isn't reliable.

## §7.3.1 batch 1 re-run after the missing-value and computed-facts fixes

**Changes:** missing years show as n/a in the table and break the line in the chart; growth figures and ratios are computed in code (`_comparison_facts`), with CAGR over the common window and the faster-growing company per year.

| # | Question | Before | After | Notes |
|---|---|---|---|---|
| 31 | Apple vs Microsoft revenue growth | PARTIAL | **FAIL (this run)** | The classifier hit Groq's rate limit and fell back to qualitative only, so the numeric comparison never ran and the answer says the data is missing. Not caused by the fix; see the status doc §1. |
| 32 | Tesla vs NVIDIA revenue | PARTIAL | **PARTIAL** | Numbers now correct (2018–2025 CAGR: NVIDIA 44.9%, Tesla 23.6%). "Projected" for NVIDIA's 2026 figure is wrong: 2026 is a reported row. The qualitative section cites filing figures I haven't verified. |
| 33 | Netflix vs Amazon revenue | FAIL | **PASS** | CAGR, ratios, and yearly leaders all match the data. |
| 34 | Tesla vs NVIDIA net income | PARTIAL | **PASS** | 57.4% NVIDIA CAGR (2018–2025) and the yearly leaders are correct. |
| 35 | Apple vs Tesla revenue and net income | FAIL | **PASS** | Ratios and CAGRs are correct; the "order of magnitude" and "four to five times" claims are gone. |

**Rate limits confirmed.** The Q31 classifier call and the Q32 answer call both returned Groq's 429 error (1,000 output tokens per minute). The runner's retry handled Q32. Q31 has no retry, so it fell back. The Q28 failure was probably the same error, though that run didn't keep the text.

## §7.3.1 batch 1 — run after the fixes (second run)

| # | Question | Verdict | Notes |
|---|---|---|---|
| 31 | Apple vs Microsoft revenue growth | **PASS** | CAGR, the six-of-seven faster-growth years, and the ratio are correct. "Projected" for Microsoft's 2026 figure is wrong, since that row is reported. |
| 32 | Tesla vs NVIDIA revenue | **PARTIAL** | The numeric half is correct. The qualitative section cites filing figures I haven't verified. |
| 33 | Netflix vs Amazon revenue | **PASS** | CAGR, ratios, and yearly leaders match the data. |
| 34 | Tesla vs NVIDIA net income | **FAIL (routing)** | The classifier hit Groq's rate limit (1,000 output tokens per minute) and fell back to qualitative only. The numeric path never ran, so there's no table or chart, and the answer uses filing figures only. The content isn't the issue; the route is. |
| 35 | Apple vs Tesla revenue and net income | **PASS** | Ratios, CAGRs, and yearly leaders match the data. |

**Result: 3 pass, 1 partial, 1 fail (Q34, from the classifier rate limit).** The rate-limit fallback on comparisons is still the open defect logged in the status doc §1. Re-running Q34 alone would likely pass, but that isn't a fix.

## §7.3.1 batch 1 — third run (2026-10-05)

All five queries routed correctly, and none hit a classifier fallback.

| # | Question | Verdict | Notes |
|---|---|---|---|
| 31 | Apple vs Microsoft revenue growth | **PASS** | Still says Microsoft's 2026 figure is "projected." It's a reported row. |
| 32 | Tesla vs NVIDIA revenue | **PARTIAL** | The numeric half matches the earlier run. The qualitative section still has unverified filing figures. |
| 33 | Netflix vs Amazon revenue | **PASS** | |
| 34 | Tesla vs NVIDIA net income | **PASS** | Routed to the numeric path this time. The 57.4% CAGR, 581.3% and 144.9% jumps, and 3.43x and 0.05x ratios match the computed facts. The earlier fail was the rate limit. |
| 35 | Apple vs Tesla revenue and net income | **PASS** | |

**Result: 4 pass, 1 partial.** The rate-limit fallback is still a risk. Q34 passed here, but it failed in the previous run for the same reason.

## §7.3.1 batch 2 — questions 36–40

Checked against the SQLite data and the computed facts. Question 36 needed one retry after a rate-limit error (the retry succeeded).

| # | Question | Verdict | Notes |
|---|---|---|---|
| 36 | Tesla vs Netflix revenue and net income | **PASS** | Ratios, CAGRs (Netflix net income 37.0%), and yearly figures match the data. One small error: Netflix's 2020–2022 growth range should be 6.5% to 24.0%; 27.6% is 2019. The qualitative section quotes figures that match the data. |
| 37 | Amazon vs Microsoft asset growth | **PASS** | CAGRs (26.0% vs 13.3%), yearly leaders, and the ratio match. Amazon's liabilities are missing, so the answer says so. "Projected" for Microsoft's 2026 figure is wrong; it's reported. |
| 38 | Tesla vs Amazon liabilities relative to assets | **PARTIAL (data gap)** | The asset comparison is correct. Amazon has no liability data, so the leverage question can't be answered. The answer says so but gives no leverage view. |
| 39 | Amazon vs Netflix asset-to-liability trends | **PARTIAL (data gap)** | Asset growth is correct (Amazon grew faster every year from 2019 to 2025). Without Amazon's liabilities, the balance-sheet strength question isn't answered. |
| 40 | Microsoft vs Apple liabilities growth | **PARTIAL** | Growth figures match the data. The question asks about obligations "relative to its size," which needs liabilities over assets. The answer compares growth only. It also omits Apple's 2025 decline (-7.3%). |

**Result: 2 pass, 3 partial.** Three of these questions depend on Amazon's liabilities, which the database doesn't have.

**Root cause (data gap).** Amazon's SEC filings don't tag a total `Liabilities` figure. They report `LiabilitiesAndStockholdersEquity` and `StockholdersEquity`, so total liabilities could be derived as the difference. The current ingest only reads the `Liabilities` tag. Fix: derive the value when that tag is missing. This changes the ingest and re-syncs the financial data, so it needs approval.

## §7.3.1 batches 1–2 — re-run after the Amazon liabilities fix

**Fix applied:** `parse_financials` in [sec_client.py](../multihop-rag/sec_client.py) now derives total liabilities as `LiabilitiesAndStockholdersEquity` minus `StockholdersEquity` for any year with no tagged `Liabilities` value. Amazon was re-synced, and its liabilities are now stored for 2018–2025. The derived values match the SEC figures: Amazon's 2018 equity is $43.55B, which gives $119.1B of liabilities.

| # | Question | Before | After | Notes |
|---|---|---|---|---|
| 31 | Apple vs Microsoft revenue | PASS | **PASS** | No regression. Routed numeric. |
| 32 | Tesla vs NVIDIA revenue | PARTIAL | **PARTIAL** | Numeric half unchanged. The qualitative section is still unverified. |
| 33 | Netflix vs Amazon revenue | PASS | **PASS** | No regression. |
| 34 | Tesla vs NVIDIA net income | PASS | **PASS** | No regression. Numeric route held. |
| 35 | Apple vs Tesla revenue and net income | PASS | **PASS** | No regression. |
| 36 | Tesla vs Netflix revenue and net income | PASS | **PASS** | Same minor error as before: Netflix's 2020–2022 growth range should end at 24.0%. |
| 37 | Amazon vs Microsoft asset growth | PASS | **PASS** | Now also reports Amazon's liabilities: CAGR 19.2% vs Microsoft's 6.6%, which match the data. |
| 38 | Tesla vs Amazon liabilities relative to assets | PARTIAL (data gap) | **PARTIAL** | Liability CAGRs and yearly growth now match the data. The question asks about leverage, meaning liabilities over assets, and the answer never computes that ratio. |
| 39 | Amazon vs Netflix asset-to-liability trends | PARTIAL (data gap) | **PARTIAL** | Liability CAGRs (19.2% vs 4.9%) and the liability ratio (5.74x to 14.04x) match the data. It still doesn't compare leverage or say which balance sheet is stronger. |
| 40 | Microsoft vs Apple liabilities growth | PARTIAL | **PARTIAL** | Unchanged. "Relative to its size" still needs liabilities over assets. |

**Result: 6 pass, 4 partial (32, 38, 39, 40).** Batch 1 is 4 pass and 1 partial. Batch 2 is 2 pass and 3 partial.

**Next gap (proposed, not applied):** the comparison facts don't include leverage, meaning liabilities over assets for each company and year. Adding that ratio would answer Q38, Q39, and Q40 directly.

## §7.3.1 batch 2 — re-run after the leverage facts

**Change:** `_comparison_facts` now adds liabilities as a share of assets for each company and year, which is the leverage ratio. It also gives the more-leveraged company for each year and the latest common year. Assets come from the database rows directly, so the ratio is available even when a question names only liabilities.

| # | Question | Before | After | Notes |
|---|---|---|---|---|
| 38 | Tesla vs Amazon liabilities relative to assets | PARTIAL | **PASS** | The first run hit the rate limit and fell back to the qualitative route. That's the known classifier defect, not the new facts. A valid re-run gives the right answer: Tesla fell from 78.8% to 39.9%, Amazon is 49.8% in 2025, and Amazon is more leveraged in every year from 2020 to 2025. It names Amazon as the greater leverage risk. |
| 39 | Amazon vs Netflix asset-to-liability trends | PARTIAL | **PASS** | Leverage by year matches the data. Netflix was more leveraged in 2018–2020, Amazon in 2021–2024, and Netflix again in 2025 (52.1% vs 49.8%). The answer gives a view on which balance sheet is more conservative. |
| 40 | Microsoft vs Apple liabilities growth | PARTIAL | **PASS** | Apple is more leveraged in every year, with 79.5% vs 44.5% in 2025. It says "projects" for Microsoft's 2026 figure, but that row is reported. |

Batch 2 now passes 5 of 5 on valid runs, with Q36 keeping its small growth-range error. Batch 1 doesn't use liabilities, so the change doesn't affect it.

## §7.3.1 batch 3 — questions 41–45

| # | Question | Verdict | Notes |
|---|---|---|---|
| 41 | Apple, Microsoft, NVIDIA EPS growth | **FAIL (data artifact)** | Microsoft's CAGR (30.4%) and Apple's (14.0%) match the data. NVIDIA's EPS series mixes pre- and post-split values. It shows −95.6% in 2023 and +600% in 2024, and a −6.8% CAGR. These are artifacts, so the "highest-risk" conclusion is wrong. |
| 42 | Apple vs NVIDIA EPS sustainability | **FAIL (data artifact)** | Same NVIDIA artifact. The "36.06x" ratio in 2023 and the 2018–2025 CAGR are wrong. The qualitative section quotes Q1 2026 filing figures I haven't verified. |
| 43 | NVIDIA vs Tesla net income volatility | **PARTIAL** | The numbers are correct, and the answer matches Q34 word for word. It doesn't measure volatility directly. It calls Tesla volatile through its swings, which is a reasonable reading. |
| 44 | Apple, Microsoft, Amazon revenue growth 2023–2025 | **PARTIAL** | Growth and CAGRs are correct (Microsoft 15.3%, Amazon 11.7%, Apple 4.2%). It says Apple has the highest absolute revenue in 2023 and 2024, which is wrong. Amazon is higher in both years. |
| 45 | Compare all six loaded companies' revenue (most recent year) | **FAIL (routing)** | The router doesn't read "all six loaded companies" as a comparison. The agent asks the user to name a company. The phrase needs to mean every loaded company. |

**Result: 0 pass, 2 partial, 3 fail.**

## §7.3.2 batch 4 — questions 46–50 (qualitative)

| # | Question | Verdict | Notes |
|---|---|---|---|
| 46 | Apple vs Tesla supply chain concentration | **FAIL (retrieval)** | Says Tesla has no supplier-concentration disclosure. Tesla's 10-K says "some of our procured components and systems are sourced from single suppliers." Comparisons don't use query expansion or keyword search, so this passage was missed. |
| 47 | NVIDIA vs Tesla export controls and geopolitical trade risk | **PASS** | Covers both companies' trade exposure. One bullet on Tesla's EV tax credits is off-topic. |
| 48 | Tesla vs Netflix regulatory risk | **FAIL (retrieval)** | Says Tesla's context has no regulatory risk section. The 10-K has one ("Risks Related to Government Laws and Regulations"), found in the earlier Q18 probe. Same cause as Q46. |
| 49 | Tesla vs Amazon litigation | **PASS** | Amazon's Kove figures ($525M plus $148M interest) are in the cited source. Tesla's $329M is correct: $129M compensatory plus $200M punitive, from the Benavides case. |
| 50 | Apple vs Tesla international and FX risk | **PASS** | Hedging, currency exposure, and the 10% sensitivity point are all grounded in the 10-K and 10-Q sources. |

**Result: 3 pass, 2 fail.** Both failures are for Tesla, on topics its filings do cover. Comparison retrieval has no query expansion and no keyword search, unlike single-company filing questions.

## Open items

- §7.3.1 batch 3 is done. §7.1 batches 2–3 and §7.3.1 batch-3-style re-checks are not yet run.
- Qualitative batch 5 (51–55) and batch 6 (56–60) not yet run.
- Open defects, all awaiting approval (status doc §1): NVIDIA EPS split artifacts; "all loaded companies" routing; comparison retrieval lacks expansion and keyword search; the classifier rate-limit fallback.
- Batches 4–6 of the qualitative set are not yet run (questions 46–50, 51–55, 56–60).
- Amazon liabilities: derive from `LiabilitiesAndStockholdersEquity` minus `StockholdersEquity`. Needs approval.
- Classifier fallback on comparisons (Q31) and rate-limit handling need your approval; see the status doc §1.
- §7.3.1 batches 2 and 3 (36–45) not yet run. §7.1 batches 2 and 3 not yet run. Question 1–5 review pending.
- The two causes above need a fix. Both change how comparison answers are built, so they need your go-ahead.
- §7.1 (questions 1–15): batch 1 has run and needs review. Batches 2 and 3 have not run.
- Q28 is partial. Its classifier fallback is unconfirmed: the traceback was lost in that run, and 15 reproduction calls succeeded. The runner now keeps tracebacks, so the next occurrence will show the cause.
- **Q24 is still partial, and the filing-type filter is reverted.** The question asks for the 10-K, but retrieval still returns 10-Q chunks. Any future form filter needs the ranking gap solved first (see the reverted section above). Next step, pending approval: a larger candidate pool and final context, applied globally and checked on the whole set.
- **Q18 needs a retrieval change that doesn't depend on the question's wording.** Options: expansion prompts that ask for the regulatory and financial terms a 10-K would use, or smaller chunks so the key passages aren't diluted. Neither is tested yet.
- Q28 is partial: the answer doesn't list the other critical estimates in the 10-K.
- The single-company bullet cap is now 10 (changed on disk). The §0 entry for the earlier six-bullet cap is out of date.
- Query expansion is applied only to `filing_rag_node`. The comparison path (`comparison_qualitative_result`) still uses one query per company, so it can still miss Q18-type passages.
- Answers don't say which filing each point comes from (e.g. Q19 mixes 10-Q and 10-K items without labels).

## Q41–50 re-run after the split, routing, retrieval and level-leader fixes (2026-10-08)

| Q | Before | After |
|---|---|---|
| 41 | FAIL | **PASS.** NVIDIA EPS CAGR +57.8% (was −6.8%); 2024 +600% is the only large jump. |
| 42 | FAIL | **PASS.** NVIDIA grows faster (+57.8% vs +14.0%) while Apple holds the higher EPS level; AAPL/NVDA ratio 24.73x → 2.54x. Minor: lists 2026 among overlapping years although Apple has no 2026 data. |
| 43 | partial | partial (unchanged): volatility is shown through year-over-year swings, with no single volatility verdict. |
| 44 | partial | **PASS.** Amazon is correctly named the largest by absolute revenue; Microsoft leads growth. |
| 45 | FAIL | **PASS.** Routed as a six-company comparison; Amazon ranked largest. |
| 46 | partial | **PASS.** Tesla's single-source and supplier disclosures are now retrieved. |
| 47 | PASS | PASS |
| 48 | FAIL | **PASS.** Both companies' regulatory exposures retrieved and contrasted by industry. |
| 49 | PASS | partial: with k=3 Tesla's litigation was missed; with k=4 it cites a Tesla derivative suit but not the $329M Autopilot verdict. |
| 50 | PASS | PASS |

**Result: 7 pass, 2 partial (43, 49), 0 fail** (from 3 pass, 2 partial, 5 fail). Q49 was re-checked individually after the k=4 change; the other nine come from the batch run before it, with only Q48 re-checked after.

## Batches 5–6 — Questions 51–60 (2026-10-08)

| Q | Verdict | Notes |
|---|---|---|
| 51 | partial | Microsoft's AI and supply-chain attack surface is covered; Apple's side is thin (suppliers only). No explicit "broader" verdict. |
| 52 | partial | No verdict on who has more IP litigation exposure; the NVIDIA litigation bullet is a securities class action, not IP. |
| 53 | PASS | NVIDIA 22% and 14% direct customers vs Microsoft's no-10% customer statement. |
| 54 | partial | Amazon's competition risk is covered; Microsoft's cloud competition from rivals is missing (GPU/power supply instead). |
| 55–60 | PASS | Grounded, both companies covered, contrasts drawn. |

**Result: 7 pass, 3 partial, 0 fail.** Common gap: the closing "which one" verdict is often missing.

## Re-run of 10 qualitative questions after the AI understand step and reranking (2026-10-08)

Chosen to test the change: the seven earlier partials (24, 25, 28, 49, 51, 52, 54) and three passing comparisons as a regression check (46, 48, 53). All 10 ran without errors or fallbacks, and every comparison was routed as one by the understand step.

| Q | Before | Now | Notes |
|---|---|---|---|
| 24 | partial | **PASS** | Now scoped to Microsoft's latest 10-K (2026-07-29) and describes the competitive landscape. Minor: no named competitors per segment; two bullets drift to regulation. |
| 25 | partial | partial (improved) | No more forward-looking-statement boilerplate; draws on the 10-K (product innovation, competitive factors, 40/60 direct/indirect mix). Still mostly risk-factor language rather than a stated growth strategy. |
| 28 | partial | partial | Now lists ten items, but mixes the 10-K's critical accounting estimates (inventory, income taxes) with the 10-Q's general "use of estimates" list, and repeats inventory sensitivity for two dates. Earlier answer had the right two but read as incomplete. |
| 46 | PASS | PASS | Adds a correct verdict (Apple more concentrated). |
| 48 | PASS | PASS | Industry contrast is sharper (NHTSA/AV rules vs DMA/AVMSD content quotas). |
| 49 | partial | partial | Tesla now covers regulatory investigations and derivative suits, still not the $329M Autopilot verdict; last bullet (tariff refunds) is not a legal proceeding. |
| 51 | partial | **PASS** | Both companies' attack surfaces covered; verdict: Microsoft broader. |
| 52 | partial | partial | Verdict line present but declines to choose; NVIDIA bullet is still a securities class action, not IP litigation. |
| 53 | PASS | PASS | NVIDIA 22% and 14% direct customers. |
| 54 | partial | **PASS** | Microsoft's cloud competition (hyperscalers, open source) now retrieved from its 10-K. |

**Result: 6 pass, 4 partial, 0 fail** (from 3 pass, 7 partial). Remaining issues: Q28 needs the 10-K's "Critical Accounting Estimates" section specifically; Q49/Q52 litigation retrieval still picks generic or off-topic cases; verdicts can decline to choose.

## Ad-hoc check: 10 new prompts outside the suite (2026-10-08)

Five figures questions and five filing questions, written to exercise the understand step (relative years, comparisons without "compare", "all loaded companies"). Figures were checked against the SQLite data.

| # | Prompt | Verdict | Notes |
|---|---|---|---|
| Q1 | Netflix net margin trend over the past four years | PASS | "Past four years" resolved to 2022–2025; margins 14.2% → 16.0% → 22.3% → 24.3% match the data. |
| Q2 | Which grew EPS faster since 2020, Microsoft or Apple? | PASS | Routed as a comparison without "compare"; CAGR +18.8% vs +17.9% over the common 2020–2025 window matches; correct verdict. |
| Q3 | NVIDIA more or less leveraged, 2021–2025 | PASS (minor) | Liabilities/assets 41.3% → 28.9% is correct, verdict correct. The summary says asset growth "consistently" outpaced liabilities, but 2023 leverage rose (39.8% → 46.3%). |
| Q4 | Rank the loaded companies by 2024 net income | partial | Order is correct (AAPL, MSFT, AMZN, NVDA, NFLX, TSLA), but no values are given, and it calls Apple's lead "significant" ($93.7B vs $88.1B). |
| Q5 | Amazon profitability, 2025 vs 2022 | PASS | −$2.72B → $77.67B and −0.5% → 10.8% net margin match. |
| Q6 | Netflix on paid / password sharing | PASS (honest no-data) | None of Netflix's 427 indexed chunks contain "sharing"; the answer correctly says the context doesn't cover it. |
| Q7 | NVIDIA export-control risks in China | PASS | Detailed and grounded (design-out risk, H20 inventory, China antitrust finding). The understand call failed (rate limit) and keyword routing was used; the answer was unaffected. |
| Q8 | Apple App Store and EU DMA risks | PASS | €500M DMA fine, Article 6(4) exposure, DOJ antitrust suit, all cited. |
| Q9 | Amazon and Microsoft AI infrastructure / capex | FAIL → PASS on re-run | First run: the understand call failed, keyword fallback saw no "compare", so only Microsoft was answered. Re-run with the understand step: both companies covered, themed. |
| Q10 | Tesla workforce disclosures | PASS | Headcount 134,785, veteran/disability %, internal promotion, Pulse survey. |

**Result: 8 pass, 1 partial (Q4), 1 fail that passed on re-run (Q9).** Two follow-ups: the understand step should retry on a rate-limit error before falling back to keywords (Q7 and Q9 both fell back mid-run), and ranking answers should quote the values.
