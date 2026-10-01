# Dalal Agents — Design Document

> *Dalal Street, but every analyst is an agent.*
> Open-source, multi-agent equity research for Indian listed companies (NSE/BSE).
> Status: v0.1 design · Author: founding team · Date: 2026-10-01

---

## Part I — The Entrepreneur's View

### 1. Problem

An Indian retail investor who wants to *really* understand one stock today has to stitch together:

| Need | Where it lives today | Pain |
|---|---|---|
| Price, trend, momentum | Broker app / TradingView | No context vs. sector or peers |
| 10-year financials, ratios | Screener.in, Tickertape | Raw tables, no interpretation |
| Concall transcripts | BSE filings PDFs (40–60 pages each) | Nobody reads 8 quarters of them |
| Future plans, capex, guidance | Concalls, annual reports, press releases, news | Spread across dozens of documents |
| Shareholding (promoter / FII / DII) | Exchange filings | Trends are not obvious |
| Competitors and relative edge | Manual comparison | Takes hours, often skipped |
| News and sentiment | Moneycontrol, ET, Business Standard | Noisy, no synthesis |

A sell-side analyst spends 2–3 days on an initiation report. Retail investors spend 20 minutes and decide on a tip.

### 2. Value proposition

**One command → an analyst-grade, fully cited research report on any Indian stock and its top competitors, in minutes, for free.**

```
dalal analyze TCS
```

The output is a Markdown, HTML and JSON report with these parts:
- a deterministic quantitative core (every number computed in Python from a named source),
- an LLM narrative layer that may only state facts it can cite (`[F12]`), checked by a verifier agent,
- a competitor edge scorecard: where the company wins and where it loses against peers, dimension by dimension.

### 3. Users and personas

1. **Priya, serious retail investor.** Holds 15 stocks and reviews them every quarter. Wants "what changed?" and "is the thesis intact?"
2. **Arjun, finance student or CFA candidate.** Wants to learn *how* an analyst thinks. The transparent formulas and sources are the product.
3. **Small PMS/RIA analyst.** Wants a first draft and a peer table to start from, not a final recommendation.
4. **Developer or quant.** Wants a clean Python library: `from dalal_agents import analyze`.

### 4. Differentiation

| | Screener / Tickertape | ChatGPT / Perplexity | **Dalal Agents** |
|---|---|---|---|
| Data depth | ✅ tables | ⚠️ shallow, sometimes stale | ✅ tables + documents + news |
| Interpretation | ❌ | ✅ | ✅ |
| Numbers trustworthy | ✅ | ❌ hallucination-prone | ✅ computed, never LLM-generated |
| Citations per claim | n/a | partial | ✅ enforced and verified |
| Competitor edge analysis | manual | generic | ✅ quantitative scorecard |
| Works with no API key | ✅ | ❌ | ✅ (quant-only mode) |
| Open source, any LLM | ❌ | ❌ | ✅ Claude / OpenAI / Ollama / any OpenAI-compatible endpoint |

**The moat is trust.** Numbers are deterministic and LLM text is grounded and verified. Everything is reproducible from a cached snapshot.

### 5. Scope

**v0.1 (this release)**
- Ticker resolution (NSE symbol, BSE code or company name)
- Data: Screener.in company page (financials, ratios, shareholding, peers, documents), Yahoo Finance (prices, index), Google News RSS, DuckDuckGo web search, BSE-hosted PDFs (concall transcripts)
- Agents: Market, Fundamentals, Ownership, Filings, Concall, News & Web, Competitor Discovery, Peer Analysis, Edge, Writer, Verifier
- Bank/NBFC-aware fundamentals
- Reports: Markdown, HTML, JSON; on-disk HTTP cache
- CLI (`dalal`) and Python API; offline test suite with fixtures

**Roadmap**
- v0.2: Web UI (shipped: Gradio app on a free Hugging Face Space, Streamlit for self-hosting; see `app/`), multi-quarter "promise vs. delivery" tracker for concalls, annual-report RAG with embeddings
- v0.3: watchlists with quarterly diff alerts, sector reports, global peers (e.g. TCS vs. Accenture)
- v0.4: eval leaderboard across LLMs, plugin data providers (paid APIs)

### 6. Success metrics
- Time to first report (fresh clone to report): under 5 minutes
- Verifier pass rate (share of LLM sentences with valid citations and matching numbers): at least 95%
- GitHub: 1k stars in 6 months; 10 external contributors

### 7. Risks and compliance

| Risk | Mitigation |
|---|---|
| **SEBI (Research Analyst) Regulations, 2014.** Giving buy/sell recommendations for a fee requires registration. | The tool gives **no buy/sell/target-price calls**. It is educational and open source, and there is a prominent disclaimer on every report. Prompts forbid recommendations, and the verifier flags recommendation words. |
| Scraping terms of service | Polite access: we honour robots.txt (we only fetch allowed paths), throttle requests (≥1 s per host), cache aggressively, send an identifying User-Agent, keep the data on the user's machine (no redistribution) and make providers pluggable. |
| Source breakage (HTML changes) | Parsers are defensive and keyed on labels rather than positions. Fixture-based tests catch changes, and the pipeline degrades gracefully: a failed agent produces a "data unavailable" section, not a crash. |
| Hallucination | Facts-only context, mandatory `[F#]` citations, and a numeric verifier. Without an LLM the tool still produces a deterministic report. |
| Stale data | Every fact carries `as_of` and `retrieved_at`, and the report header shows the data date. |

---

## Part II — The Scientist's View

### 8. System architecture

```
                            ┌──────────────────────┐
  dalal analyze TCS ───────▶│   Orchestrator (DAG)  │  thread pool, per-agent timeouts,
                            └──────────┬───────────┘  graceful degradation
                                       │
             ┌──────────── Stage 1: Research (parallel) ─────────────┐
             ▼            ▼             ▼            ▼               ▼
        MarketAgent  FundamentalsAgent OwnershipAgent FilingsAgent  NewsWebAgent
        (yfinance)   (screener tables) (shareholding) (announce.,   (Google News RSS,
             │            │             │             ARs, ratings)  DuckDuckGo search:
             │            │             │            ConcallAgent    capex / guidance /
             │            │             │            (BSE PDFs →     order book)
             │            │             │             LLM/regex)
             └────────────┴──────┬──────┴────────────┴───────────────┘
                                 ▼
                     ┌────────────────────────┐
                     │ FactStore (F1..Fn)     │  every number/claim + Source
                     └───────────┬────────────┘
             ┌──────── Stage 2: Competition ─────────┐
             ▼                                        ▼
   CompetitorDiscoveryAgent ──▶ PeerAnalysisAgent (Market+Fundamentals per peer, parallel)
   (screener peer set, same-industry,                 │
    size proximity, optional LLM check)               ▼
                                          EdgeAgent (percentile scorecard, moat signals)
                                 │
             ┌──────── Stage 3: Synthesis ───────────┐
             ▼                                        ▼
       WriterAgent (LLM, facts-only, [F#])  ──▶  VerifierAgent (citations, numbers,
                                                  banned-recommendation words)
                                 ▼
                     Report renderer → report.md / report.html / report.json
```

**Design principles**
1. **LLMs reason, Python calculates.** Every ratio, CAGR, indicator and score is deterministic code with unit tests.
2. **Facts are first-class objects.** Agents emit `Fact(id, key, value, unit, period, source)`. The LLM only ever sees facts and cited excerpts.
3. **Every agent is optional.** A failure becomes a `Finding(status="unavailable")`, never an exception that kills the run.
4. **Provider-agnostic.** Data providers and LLM providers sit behind small interfaces.
5. **Reproducible.** There is an HTTP disk cache with TTL, and `--offline` replays from the cache.

### 9. Why not LangGraph, CrewAI or AutoGen?

We evaluated them. For an open-source tool that must be easy to install and audit, the DAG is static and small (around 11 nodes, 3 stages), so a framework adds dependency weight, version churn and hidden prompts without adding capability. We use a **~150-line typed orchestrator** on `concurrent.futures`. Each agent is a plain class with `run(ctx) -> AgentResult`. Porting to LangGraph later is mechanical, because the nodes and edges are explicit.

LLM access goes over raw HTTP (`requests`) to three wire protocols: **Anthropic Messages**, **OpenAI Chat Completions** (which also covers Ollama, vLLM, LM Studio, Groq, Together and OpenRouter) and **none**. That means no SDK dependencies.

Default model: `claude-sonnet-5-5` (good cost/quality for long-context synthesis). Users can pick `claude-opus-5-5` with `--model`.

### 10. Data layer

| Provider | Data | Access | Notes |
|---|---|---|---|
| `ScreenerProvider` | Name, about, top ratios, 12y P&L, BS, CF, ratios, 12 quarters, shareholding (quarterly), compounded growth, pros/cons, sector hierarchy, peers (`/api/company/{wid}/peers/`), announcements, annual reports, credit ratings, concall transcript links | HTML parse (bs4) | Units are ₹ crore. Consolidated first, standalone fallback. Bank layout detected via `Financing Profit` / `Deposits` rows. |
| `YahooProvider` | Daily OHLCV (5y), market info, NIFTY 50 (`^NSEI`) for beta and relative strength, ticker search | `yfinance` | `.NS` suffix, `.BO` fallback |
| `NewsProvider` | Last ~60 days of headlines | Google News RSS (`hl=en-IN`) | Personal-use feed; headlines and links only |
| `WebSearchProvider` | Forward-looking facts: capex, guidance, expansion, order book, management commentary | DuckDuckGo HTML endpoint; Tavily if `TAVILY_API_KEY` is set | Snippets kept as cited excerpts |
| `DocumentProvider` | Concall transcript text | BSE PDF → `pypdf` | Latest N transcripts (default 2) |

**HTTP layer:** a shared `requests.Session` with a disk cache (`~/.cache/dalal-agents`, SHA-1 keyed, TTL 12 h for pages and 30 d for PDFs), retry with exponential backoff, a per-host minimum interval, and a descriptive User-Agent.

### 11. Finance methodology

Conventions: Indian FY (Apr–Mar, labelled `Mar YYYY`), amounts in ₹ crore, consolidated numbers by default. TTM is used where available.

#### 11.1 Growth
- CAGR(n) = (X_t / X_{t−n})^{1/n} − 1, defined only if both ends are > 0; otherwise reported as n/a (never a misleading negative-base CAGR).
- Computed for Sales/Revenue, Operating Profit, Net Profit and EPS over 3, 5 and 10 years.
- Quarterly YoY: Q_t / Q_{t−4} − 1. Revenue acceleration = YoY_t − YoY_{t−1}.

#### 11.2 Profitability and returns (non-financials)
- OPM = Operating Profit / Sales; NPM = Net Profit / Sales
- ROE_t = NP_t / avg(Equity_t, Equity_{t−1}), where Equity = Equity Capital + Reserves
- ROCE_t = EBIT_t / avg(CE_t, CE_{t−1}), where EBIT = PBT + Interest and CE = Equity + Borrowings
- DuPont: ROE = NPM × Asset Turnover (Sales / avg Total Assets) × Leverage (avg TA / avg Equity)
- Margin stability = σ(OPM over 5y); a lower value means pricing power or a moat

#### 11.3 Balance sheet and cash quality
- Debt/Equity = Borrowings / Equity; Interest coverage = EBIT / Interest
- Cash conversion = Σ CFO / Σ Net Profit over 5y (above 0.8 is healthy; below 0.6 is a red flag)
- FCF = CFO + Cash from investing (conservative proxy) or Screener's FCF row when present
- Working capital: Debtor days, Inventory days, Payable days, CCC (from Screener ratios); the trend matters more than the level
- **Piotroski F-score (adapted, 0–9):** NP>0, CFO>0, ΔROA>0, CFO>NP, ΔLeverage<0, ΔCurrent-ratio proxy, no dilution (equity capital unchanged), ΔOPM>0, ΔAsset turnover>0. If an input is missing, that criterion is dropped and the score is normalised and labelled `F (k/m)`.

#### 11.4 Banks, NBFCs and insurers (auto-detected)
Screener's bank layout (Revenue / Interest / Financing Profit / Deposits) triggers a different metric set: Revenue and NP CAGR, Financing margin, ROE, ROA = NP / avg Total Assets, Deposit growth, Advances proxy, Gross/Net NPA % trend (from quarters), Cost-to-income proxy, and P/B as the primary valuation multiple. Industrial metrics (ROCE, D/E, CCC) are suppressed, because they are meaningless for lenders.

#### 11.5 Technicals (from daily closes)
- Returns: 1M, 3M, 6M, 1Y and 3Y CAGR; relative return vs. NIFTY 50 over the same windows
- Trend: SMA50 and SMA200, golden/death cross state, price vs. SMA200
- RSI(14) (Wilder), MACD(12, 26, 9)
- Annualised volatility σ_d·√252; max drawdown over 1y and 3y; distance from the 52-week high and low
- β = Cov(r_s, r_m) / Var(r_m) on 1y of daily log returns vs. ^NSEI
- Trend label: *Uptrend* if P > SMA50 > SMA200; *Downtrend* if P < SMA50 < SMA200; otherwise *Sideways/Transition*

#### 11.6 Valuation (descriptive, never a target price)
- P/E, P/B, Dividend yield, Earnings yield = 1/PE
- Market cap / Sales; PEG = PE / (5y EPS CAGR × 100)
- **Reverse DCF (market-implied growth):** solve for g such that the present value of 10y FCFE growing at g, plus a terminal value at g_T = 5%, equals market cap, with cost of equity k_e = 12% (configurable; roughly the Indian rf of 6.5% plus an ERP of about 5.5%). We report what the price *implies* and compare it with historical growth. This is the honest way to discuss valuation without issuing a target price.

#### 11.7 Competitor discovery
1. Candidates: the Screener peer table (same industry classification), which is reliable and exchange-classification based.
2. Rank by relevance score s = 0.6·size_similarity + 0.4·rank_in_industry, where size_similarity = exp(−|ln(MCap_i / MCap_0)|). This prefers peers of comparable scale; a ₹7 lakh-crore company should not be compared with a ₹500-crore microcap.
3. Optional LLM sanity check: given the business descriptions, drop candidates whose business clearly doesn't overlap. This is an explicit agent decision that is logged in the report.
4. Top K (default 4) are analysed fully.

#### 11.8 Edge scorecard
For each dimension d and each company i in the peer set (target + K peers), compute raw metrics. Each metric's percentile rank p ∈ [0, 1] among the set is direction-adjusted (higher-is-better or lower-is-better). The dimension score is the mean percentile × 100.

| Dimension | Metrics (non-financial) | Weight |
|---|---|---|
| Growth | Sales CAGR 5y, NP CAGR 5y, latest quarter YoY sales | 20% |
| Profitability | OPM, ROE, ROCE | 20% |
| Balance-sheet strength | D/E (↓), interest coverage | 15% |
| Cash quality | CFO/NP 5y, FCF margin | 15% |
| Valuation (cheapness) | P/E (↓), P/B (↓), PEG (↓) | 15% |
| Momentum | 1Y return, 6M return, price vs. SMA200 | 10% |
| Stability | OPM σ (↓), volatility (↓) | 5% |

The bank variant uses ROA, ROE, NIM proxy, GNPA (↓), NNPA (↓), deposit growth and P/B (↓).

The output includes per-dimension winners, the target's **edges** (dimensions where it ranks first or scores ≥ 70) and **gaps** (scores ≤ 30), and a composite score. The composite is a *relative-quality summary, not a recommendation*.

#### 11.9 Text analytics
- **Concall:** download the latest N transcripts, extract text, and split into management remarks and Q&A. With an LLM: structured extraction of guidance (revenue, margin, capex), new initiatives, risks flagged and the analyst's top concerns, each item with a verbatim quote. Without an LLM: keyword-window extraction (guidance, capex, expand, margin, order book, target, FY27…) returning cited sentences.
- **Tone:** net tone = (pos − neg) / (pos + neg), computed with a finance lexicon (Loughran–McDonald-style subset) per transcript. A *shift* between quarters is a known signal.
- **News:** headline sentiment with the same lexicon (deterministic), with the LLM optionally summarising themes, plus event tagging (results, order win, regulatory, management change, M&A, rating).

### 12. Grounding and verification

1. The **FactStore** assigns `F1…Fn` to every numeric fact and `E1…En` to every text excerpt (news headline, search snippet, transcript quote), each with a `Source(url, title, provider, retrieved_at)`.
2. The **Writer** prompt contains only the facts and excerpts. Rules: cite every claim with `[F#]`/`[E#]`; never introduce numbers not present in the facts; no buy/sell/hold or target prices; say "insufficient data" rather than guess.
3. The **Verifier** checks, deterministically:
   - every sentence containing a digit has at least one citation;
   - each cited ID exists;
   - each number in a sentence matches (within ±1% or rounding tolerance) a value in one of the cited facts (handling %, crore, lakh and x-multiples);
   - there are no banned phrases (`buy`, `sell`, `target price`, `strong buy`, `accumulate`, …).

   Failing sentences are removed or flagged according to `--strict`. The pass rate is printed in the report footer.
4. The report's appendix lists all sources with URLs, so readers can audit every claim.

### 13. Report outline
1. Header: company, ticker, sector hierarchy, price, market cap, data date, disclaimer
2. Executive summary (LLM, cited) or deterministic key points
3. Business overview (Screener "about" plus segments from search)
4. Price and trend (technicals table, relative strength vs. NIFTY)
5. Financial performance (growth table, margins, returns, DuPont)
6. Balance sheet and cash-flow quality (D/E, CFO/NP, FCF, working-capital days, F-score)
7. Valuation (multiples, reverse-DCF implied growth vs. history)
8. Ownership (promoter / FII / DII trend, shareholder count)
9. Future plans and management commentary (concall extraction, web search)
10. News flow and sentiment
11. Competitive landscape (peer table)
12. Edge scorecard (dimension matrix, edges and gaps)
13. Bull case / Bear case / Key monitorables (LLM, cited)
14. Methodology notes, sources, verification stats, disclaimer

### 14. Repository layout

```
dalal-agents/
├── src/dalal_agents/
│   ├── __init__.py          # analyze() public API
│   ├── cli.py               # Typer CLI: analyze, peers, quote
│   ├── config.py            # Settings from env / CLI
│   ├── models.py            # Pydantic: Source, Fact, Excerpt, Finding, CompanyData, Report…
│   ├── facts.py             # FactStore
│   ├── orchestrator.py      # DAG runner
│   ├── http.py              # cached, throttled session
│   ├── providers/           # screener, yahoo, news, websearch, documents
│   ├── analytics/           # fundamentals, technicals, valuation, peers, scorecard, text
│   ├── llm/                 # base, anthropic, openai_compat, none
│   ├── agents/              # market, fundamentals, ownership, filings, concall, news, competitor, edge, writer, verifier
│   └── report/              # markdown, html renderer
├── tests/                   # offline unit tests + fixtures
├── docs/DESIGN.md
├── examples/                # sample report
├── pyproject.toml, README.md, LICENSE (MIT), CONTRIBUTING.md, .github/workflows/ci.yml
```

### 15. Evaluation
- **Unit tests:** every formula against hand-computed values; parsers against saved HTML fixtures (TCS = IT, HDFCBANK = bank).
- **Verifier tests:** synthetic LLM outputs with planted wrong numbers and missing citations must be caught.
- **Golden set (manual, v0.2):** 10 stocks across sectors; compare computed ratios against Screener's displayed ratios (ROCE and ROE within 2 pp).
- **CI:** offline tests on every PR (no network, no keys).

### 16. Milestones
1. M1: core models, HTTP cache, providers, analytics with tests
2. M2: agents, orchestrator, deterministic report (no LLM)
3. M3: LLM layer, writer, verifier
4. M4: README, examples, CI, GitHub launch

---

*Disclaimer: Dalal Agents is an educational research tool. It is not investment advice, is not registered with SEBI as a Research Analyst, and makes no buy/sell recommendations. Data may be delayed, incomplete or wrong. Always verify against primary filings.*
