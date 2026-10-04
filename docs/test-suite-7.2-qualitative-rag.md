# Test Suite: §7.2 Qualitative Text Analysis (RAG) — Results

_Run: 2026-09-14 · Branch: `ticker-fix` · Target: `filing_rag_node` via `run_agent()`_

This is a live run of all 15 reference queries from [project-status-report.md §7.2](project-status-report.md#72-qualitative-text-analysis-rag) against the real backend (real Groq LLM calls, real Chroma retrieval over already-ingested filings for AAPL/TSLA/NVDA/MSFT/AMZN). Not a unit test file — there's no pytest harness in this repo yet (see project-status-report.md §1) — this is a manual run-and-record pass, kept here so the results don't just live in a chat transcript.

**Summary: 8 pass, 7 fail. All 7 failures are retrieval misses ("I don't know" / "context doesn't contain X" for a topic that should be a standard 10-K section), not routing or classification bugs.** One classification bug was found and fixed along the way (§"Fixed" below) — it doesn't fully explain any of the 7 failures, but it was actively making one of them (#27) worse.

## Results

| # | Question (company) | Resolved | chart_meta | Sources | Status | Note |
|---|---|---|---|---|---|---|
| 16 | Apple supply chain risks | AAPL | `{}` | 4 | **PASS** | Correctly cites single/limited-source component risk, commodity pricing. |
| 17 | Tesla raw material/battery supply | TSLA | `{}` | 4 | **PASS** | Correctly cites lithium/nickel price and supply risk. |
| 18 | Tesla EV regulatory-change risk | TSLA | `{}` | 4 | **FAIL** | "Context does not contain specific information... primarily details financial statements, human capital, patent policies." Retrieval miss. |
| 19 | Apple litigation/legal proceedings | AAPL | `{}` | 4 | **PASS** | Correctly cites the real Ninth Circuit link-out-commission case. |
| 20 | NVIDIA export controls/geopolitical risk | NVDA | `{}` | 4 | **PASS** | Correctly cites Hong Kong warehousing/distribution export-control exposure. |
| 21 | Microsoft FX/international-ops risk | MSFT | `{}` | 4 | **PASS** | Correctly cites the 10% FX sensitivity figures from the filing's own risk disclosure. |
| 22 | Amazon cybersecurity risk | AMZN | `{}` | 4 | **FAIL** | Answer: *"I don't know."* Amazon's 10-K has a mandatory Item 1C Cybersecurity section (SEC rule since 2023) — should exist. Retrieval miss, see root cause below. |
| 23 | NVIDIA IP risk/protections | NVDA | `{}` | 4 | **FAIL** | Answer: *"I don't know."* Retrieval miss. |
| 24 | Microsoft competitive landscape | MSFT | `{}` | 4 | **PASS** | Correctly cites "dynamic and highly competitive... frequent changes in technologies and business models." |
| 25 | Apple growth strategy/business overview | AAPL | `{}` | 4 | **PASS** | Correctly cites product-transition/customer-demand strategy language. |
| 26 | NVIDIA customer-concentration risk | NVDA | `{}` | 4 | **FAIL** | Answer: "does not contain specific details... only references that such risks are described in Item 1A" — found a cross-reference to the section, not the section itself. Retrieval miss. |
| 27 | Tesla MD&A liquidity/capital-resources discussion | TSLA | `{}` (after fix) | 4 | **FAIL** (retrieval) / **FIXED** (classification) | See "Fixed" section — originally also pulled in an unrequested full 5-metric financial trend. That's fixed. The underlying qualitative answer itself is still a retrieval miss: *"context does not contain the MD&A section... only includes financial statement tables and notes."* |
| 28 | Amazon critical accounting estimates | AMZN | `{}` | 4 | **FAIL** | Answer: *"I don't know."* This is a standard, always-present 10-K MD&A subsection. Retrieval miss. |
| 29 | Amazon macroeconomic risk | AMZN | `{}` | 4 | **PASS** | Correctly cites global economic/geopolitical conditions, FX, energy prices. |
| 30 | Microsoft human capital/workforce risk | MSFT | `{}` | 4 | **FAIL** | Hedged non-answer ("no specific disclosure... mentions 'compensating employ[ees]'"). Microsoft's 10-K has a mandatory Human Capital section (Item 1) — should exist. Retrieval miss. |

## Fixed: `classify_node` conflating "discusses a financial concept" with "needs the numbers"

**Symptom:** #27 ("How does Tesla's management discuss liquidity and capital resources in the MD&A section?") classified as `needs_financial: true` in addition to `needs_qualitative: true`, routing through `mixed_node` and prepending an unrequested full 5-metric trend table/narrative ("Financial trend: - Revenue demonstrated strong growth...") ahead of the actual qualitative answer the question asked for.

**Root cause:** `classify_node`'s prompt ([graph_agent.py](../multihop-rag/graph_agent.py)) defined `needs_financial` only by topic keywords ("revenue, net income, EPS, assets, liabilities... balance sheet..."), with no distinction between *asking for the reported figures* and *asking how management narratively discusses a financial concept*. "Liquidity and capital resources" reads as financial-flavored vocabulary, so the model reasonably (given the prompt as written) flagged it as `needs_financial` too, even though the question is explicitly about the *MD&A prose discussion*, not the numbers.

**Fix:** `needs_financial`'s definition now explicitly excludes narrative/MD&A-style discussion of financial concepts (liquidity, capital resources, cash flow, critical accounting estimates) unless the question *also* asks for the underlying figures or a trend — that discussion belongs to `needs_qualitative` instead.

**Verified live:**
- #27 now classifies as `{needs_financial: false, needs_qualitative: true}` and returns a clean single-section qualitative answer (no unrequested trend table).
- Regression-checked unaffected: a genuinely mixed query ("what are Apple's risk factors and how did revenue perform") still classifies both `true`; a pure financial query ("what was Apple revenue in 2023") still classifies `needs_financial` only.

This fix did **not** resolve #27's underlying retrieval miss — see below.

## Not fixed: 7 retrieval misses on standard 10-K sections

**The pattern:** every failure is the model correctly saying "I don't know" / "the context doesn't contain X" for a *real, standard, near-universally-present* 10-K section — cybersecurity (Item 1C, mandatory since 2023), intellectual property, customer concentration, critical accounting estimates (a standard MD&A subsection), human capital (Item 1, mandatory since 2020), MD&A liquidity discussion, and EV regulatory risk. These aren't obscure or company-specific topics where "not applicable" would be a plausible real answer — they should be retrievable.

**Diagnosis (not guessed — checked directly against the live vector store):**

Queried Chroma directly, bypassing the LLM, for two of the failing cases at `k=10` (more than double the production `k=4`):

```
AMZN / "What cybersecurity risks does Amazon disclose?" (k=10):
[1] administration, and performance. We are subject to audits and investigations...
[2] and criminal penalties and administrative sanctions, including termination of contract...
[3] ...Chief Executive Officer of Amazon.com, Inc., pursuant to Rule 13a-14(a)...
[4] ...Amazon Web Services, Inc. in the United States District Court for the Western District of Texas.
...
[10] transportation systems, including as a result of labor market constraints...
```

None of the top 10 chunks is a substantive cybersecurity risk-factor paragraph — it's a grab-bag of unrelated legal/financial boilerplate. **This rules out "just raise `k`" as the fix** — the relevant content isn't sitting just past the current cutoff, it's not surfacing at all in the top 10.

**Also tested MMR** (`search_type="mmr"`, `k=4, fetch_k=25, lambda_mult=0.5`) on all 5 of the topic-based failures (cybersecurity, IP, customer concentration, accounting estimates, human capital) — already an open item in project-status-report.md §3 ("Switch to MMR for better source diversity"). **Result: no improvement.** MMR's results were similarly off-topic boilerplate, just a different selection of it. This means the problem isn't near-duplicate chunks crowding out a relevant one (which MMR would fix) — the embedding similarity ranking itself isn't surfacing the right content at all for these queries, at any diversity setting.

**Conclusion:** this traces to the deeper, already-logged RAG-quality gaps in project-status-report.md §2/§3, not a bug introduced this session and not something fixable with a quick parameter change:
- "Consider a finance-domain embedding model" — `all-MiniLM-L6-v2` is a small general-purpose model; it may simply not embed a casual query phrasing ("cybersecurity risks") close enough to the filing's actual heading/phrasing ("Item 1C. Cybersecurity") for the real section to rank in the top 10.
- "Hybrid (keyword + vector) retrieval" — a keyword/BM25 pass would likely catch an exact heading match ("Cybersecurity", "Human Capital") that dense embeddings are missing here.
- "`filing_rag_node` never filters by filing type or fiscal year" — if the specific ~12-filing window ingested per company doesn't happen to include a 10-K with rich text under that exact heading (vs. a 10-Q that only cross-references it), no amount of retrieval tuning inside that window helps.

**What I'm explicitly not doing:** patching this with a larger `k` or MMR as a placebo — I tested both live above and neither moves the needle on these specific failures. A real fix needs the embedding-model evaluation and/or hybrid retrieval work already scoped as its own item, not a one-line change bolted onto this test run.

## Action items

- [x] Fix `classify_node`'s `needs_financial`/`needs_qualitative` conflation for narrative financial-concept discussions (this run, 2026-09-14).
- [ ] Evaluate a finance-domain or larger embedding model against these 5 specific failing queries as a concrete benchmark (existing §3 item, now with real failing test cases to validate against instead of a blind swap).
- [ ] Evaluate hybrid keyword+vector retrieval, using these same 5 queries as the benchmark.
- [ ] Once either fix lands, re-run this exact suite and update the results table above in place — don't create a second results doc.
