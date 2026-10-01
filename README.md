# 🐂 Dalal Agents

**Open-source multi-agent equity research for Indian stocks (NSE/BSE).**
One command gives you a research report on any listed Indian company and its closest competitors. Every number in it is cited.

### 👉 [Try it in your browser](https://huggingface.co/spaces/rohanshah8/dalal-agents) — no install, no sign-up

The web app runs without an API key: you get every number, chart and the competitor scorecard. Paste your own Anthropic or OpenAI key in the sidebar to add the AI analyst narrative. The key is used only for that run and is never stored.

```bash
dalal analyze TCS
dalal analyze "hdfc bank"
dalal analyze 500325          # BSE code works too
```

[![CI](https://github.com/rohanshah8/dalal-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/rohanshah8/dalal-agents/actions)
[![Open in Spaces](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Open%20app-yellow)](https://huggingface.co/spaces/rohanshah8/dalal-agents)
![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

> ⚠️ **Not investment advice.** Dalal Agents is an educational research tool. It never issues buy/sell/hold calls or target prices, and the authors are not SEBI-registered Research Analysts. See the [disclaimer](#disclaimer).

---

## What you get

Each run has 11 agents gather the data and do the analysis that would take a human analyst days:

| Agent | What it does | Sources |
|---|---|---|
| **Market** | Trend (SMA50/200), momentum (RSI, MACD), returns vs NIFTY 50, β, volatility, drawdowns | Yahoo Finance |
| **Fundamentals** | 12 years of P&L, balance sheet and cash flow → CAGR, margins, ROE/ROCE, DuPont, cash conversion, working-capital days, Piotroski F-score | Screener.in |
| **Valuation** | P/E, P/B, PEG, and a **reverse DCF**: what growth is the market price already pricing in? | Computed |
| **Ownership** | Promoter / FII / DII / retail trends and shareholder-count growth | Exchange filings via Screener |
| **Filings** | Latest exchange announcements, annual reports, credit ratings | BSE/NSE |
| **Concall** | Reads the latest earnings-call transcripts (PDF) and extracts guidance, capex, initiatives, risks and analyst concerns, each with a **verbatim quote checked against the transcript**; tracks management-tone shifts | BSE filings |
| **News & Web** | ~60 days of headlines with sentiment and event tags, plus open-web search for capex, expansion, order book and strategy | Google News, Bing / Tavily |
| **Competitor discovery** | Same-industry peers ranked by size similarity, with an optional LLM check of business overlap | Screener peer data |
| **Peer analysis** | Runs the same maths on each competitor | — |
| **Edge** | A percentile **scorecard** across 7 dimensions that shows where the company wins and where it lags vs peers | Computed |
| **Writer + Verifier** | The LLM writes the narrative using *only* the cited facts. A deterministic verifier then checks every number against its source | Claude / OpenAI / Ollama |

**Example reports** (real runs, 1 Oct 2026): [TCS vs Infosys, HCLTech, Wipro, Tech Mahindra](examples/TCS.md) · [HDFC Bank vs ICICI, Kotak, Axis, IDBI](examples/HDFCBANK.md) · [Asian Paints vs Berger, Kansai Nerolac, JSW Dulux, Indigo](examples/ASIANPAINT.md)

```
           Edge scorecard (percentile vs peers)
┏━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ Dimension     ┃ TCS ★ ┃ INFY ┃ HCLTECH ┃ WIPRO ┃ TECHM ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ Growth        │    58 │   83 │      58 │     8 │    42 │
│ Profitability │   100 │   75 │      50 │     8 │    17 │
│ Balance sheet │    50 │   75 │      75 │     0 │    50 │
│ Cash quality  │    50 │   38 │      75 │    38 │    50 │
│ Valuation     │    42 │   83 │      42 │    75 │     8 │
│ Momentum      │    50 │   17 │      75 │     8 │   100 │
│ Stability     │    75 │   38 │      25 │    75 │    38 │
│ Composite     │    62 │   65 │      59 │    25 │    40 │
└───────────────┴───────┴──────┴─────────┴───────┴───────┘
Verifier: 150/151 sentences passed · numeric grounding 98.9%
```

## Why it can be trusted

LLMs are good at reasoning but bad at arithmetic, and they hallucinate numbers. Dalal Agents is built around that:

1. **Python calculates, LLMs write.** Every ratio, CAGR, indicator and score is computed deterministically and unit-tested.
2. **Every fact gets an ID.** Agents register each data point as `[F12] TCS · ROCE (Mar 2026): 59.9% ← screener.in` and each text excerpt as `[E7] …`. The writer sees only these facts.
3. **Citations are mandatory.** Every sentence with a number must cite a fact.
4. **Deterministic verifier.** It extracts every number from the narrative and checks that it matches one of the cited facts (±1.5%). It also flags unknown citations, uncited numbers and recommendation language ("buy", "target price", "undervalued"…). `--strict` removes failing sentences; by default they are marked ⚠.
5. **Grounded transcript extraction.** Concall items whose "verbatim" quote is not actually in the transcript are dropped.
6. **It works without an LLM.** With no API key you still get a full quantitative report with a rule-based narrative that follows the same citation contract.

## Install

```bash
git clone https://github.com/rohanshah8/dalal-agents.git
cd dalal-agents
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Optional: configure an LLM for the AI-written narrative.

```bash
# Anthropic Claude (default when the key is present)
export ANTHROPIC_API_KEY=sk-ant-...
export DALAL_MODEL=claude-sonnet-5-5       # or claude-opus-5-5 for the deepest analysis

# …or any OpenAI-compatible endpoint (OpenAI, Ollama, vLLM, LM Studio, Groq, OpenRouter…)
export DALAL_LLM_PROVIDER=openai
export OPENAI_BASE_URL=http://localhost:11434/v1   # e.g. Ollama
export DALAL_MODEL=llama3.1:70b

# Optional: better web search
export TAVILY_API_KEY=tvly-...
```

## Usage

```bash
dalal analyze TCS                          # full report → reports/TCS_<date>.{md,html,json}
dalal analyze INFY --peers 6 --concalls 3  # wider peer set, more transcripts
dalal analyze "bajaj finance" --no-llm     # deterministic only, zero API cost
dalal analyze RELIANCE --strict            # drop any sentence that fails verification
dalal analyze TCS --offline                # replay from cache (reproducible)
dalal peers ASIANPAINT                     # quick competitor list
dalal quote HDFCBANK                       # quick ratio snapshot
```

From Python:

```python
from dalal_agents import analyze
from dalal_agents.report import render_markdown

report = analyze("TCS", n_peers=4)
print(report.scorecard["edges"])           # [{'dimension': 'Profitability', 'score': 100.0, ...}]
print(report.findings["fundamentals"].data["roce"])
open("tcs.md", "w").write(render_markdown(report))
```

## Web app

The public app is a **Gradio** UI hosted on a free Hugging Face Space. A Streamlit UI with the same features is included for local or self-hosted use. Both share `app/core.py`, which holds the pipeline wrapper, caching and API-key policy.

```bash
pip install -e ".[web]"
python app/gradio_app.py                    # Gradio UI → http://localhost:7860
streamlit run app/streamlit_app.py          # or the Streamlit UI → http://localhost:8501
docker build -t dalal-agents . && docker run -p 7860:7860 dalal-agents   # Streamlit in Docker
```

How the app works:
- **Tabs:** Summary, Competitors & edge (scorecard heatmap), Price & trend vs peers and Nifty, Financials, Ownership, Management & plans (concall guidance with quotes), News, and the full report with sources.
- **Downloads:** HTML, Markdown and JSON.
- **Caching:** key-free analyses are cached for 6 hours and shared by all visitors, so popular stocks load instantly.
- **API keys:** an analysis that uses a visitor's key runs in full with their LLM (concall reading, news themes, competitor filtering, narrative). It is never cached, and the key is never stored, logged or echoed back.
- **Load limits:** at most 2 analyses run at once (`DALAL_MAX_CONCURRENT`). Other visitors see their place in Gradio's queue.

### Deploy your own Space (free)
1. Create a Hugging Face [access token](https://huggingface.co/settings/tokens) with **write** permission.
2. [Create a Space](https://huggingface.co/new-space): SDK **Gradio**, template **Blank**, hardware **CPU basic (free)**.
3. In this GitHub repo, open **Settings → Secrets and variables → Actions**. Add the secret `HF_TOKEN`. If your Space isn't `rohanshah8/dalal-agents`, also add the variable `HF_SPACE=<user>/<space>`.
4. Push to `main`, or run **Deploy to Hugging Face Space** from the Actions tab. The workflow assembles the Space with `scripts/build_space.sh`, which you can run locally to inspect exactly what gets deployed.

## Report contents

1. Key metrics strip
2. Executive summary
3. Business overview
4. Price & trend (technicals vs NIFTY 50)
5. Financial performance (growth table, margins, returns, DuPont)
6. Balance sheet & cash-flow quality (D/E, CFO/NP, FCF, working capital, F-score)
7. Valuation: multiples and reverse-DCF implied growth vs history
8. Ownership (promoter / FII / DII trends)
9. Future plans & management commentary (concalls and web)
10. News flow & sentiment
11. Competitive landscape: peer table, **edge scorecard**, where it leads and lags
12. Bull case · Bear case · What to monitor
13. Sources, methodology, verification statistics, full fact table, evidence excerpts

## Banks and NBFCs

Lenders are detected automatically and get a different playbook. Industrial metrics such as ROCE, D/E and working capital are meaningless for a bank, so they are dropped. Instead the report covers ROA, financing margin, Gross/Net NPA, deposit growth, equity/assets and P/B. It also uses **standalone** statements, so insurance and AMC subsidiaries don't distort the lending numbers.

## Architecture

```
            ┌────────────── Stage 1 (parallel) ───────────────┐
query ─▶ resolve ─▶ Market · Fundamentals · Ownership · Filings · Concall · News/Web · Competitors
                                         │
                                    FactStore [F#]/[E#]
                                         │
            ┌───── Stage 2 ─────┐        ▼
            Peer snapshots (parallel) ─▶ Edge scorecard
                                         │
            ┌───── Stage 3 ─────┐        ▼
            Writer (LLM, facts-only) ─▶ Verifier ─▶ Markdown / HTML / JSON
```

The full design is in **[docs/DESIGN.md](docs/DESIGN.md)**: product thinking, the formula for every metric, the competitor-selection method, scorecard weights, grounding design and roadmap.

```
src/dalal_agents/
├── agents/        # market, fundamentals, ownership, filings, concall, news, competition, synthesis
├── analytics/     # pure functions: fundamentals, technicals, valuation, peers/scorecard, text
├── providers/     # screener, yahoo, news/web search, PDF documents
├── llm/           # provider-agnostic client (Anthropic / OpenAI-compatible / none), raw HTTP
├── report/        # Markdown + HTML + JSON rendering
├── facts.py       # FactStore — the only thing the LLM may cite
├── orchestrator.py
└── cli.py
```

## Data sources and etiquette

Dalal Agents uses free, public sources: Screener.in, Yahoo Finance, Google News RSS, Bing RSS search and BSE filing PDFs. It is a **polite client**:
- it fetches only paths allowed by robots.txt;
- it waits at least 1 second between requests to each host;
- it caches on disk (12 h for pages, 60 days for PDFs);
- it keeps data on your machine.

Please respect each source's terms of use. For heavy or commercial use, plug in a licensed data provider (see `providers/`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `SSLError: certificate verify failed` when calling the LLM (corporate proxy or gateway) | `export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt` (or your company's CA bundle) |
| `cache disabled (...)` warning, or a disk quota / read-only home directory | `export DALAL_CACHE_DIR=/path/with/space` |
| `LLM writer failed ... deterministic narrative used` | The report is still produced without the AI narrative. Check your key, model id (`--model`) and base URL |
| A model id is rejected by your gateway | Pass one it supports, e.g. `--model claude-opus-5-5` |

## Development

```bash
pip install -e ".[dev,web]"
pytest -q            # 52 offline tests: parsers on real HTML fixtures, finance maths, verifier, end-to-end pipeline
```

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues: more sector playbooks (insurance, real estate), annual-report RAG, and a quarterly "promise vs delivery" tracker for concall guidance.

## Roadmap

- [x] Web UI (Gradio on Hugging Face Spaces; Streamlit for self-hosting)
- [ ] Track concall guidance against actual delivery across 8 quarters
- [ ] Annual-report RAG (segment data, related-party transactions, contingent liabilities)
- [ ] Global peers (e.g. TCS vs Accenture, Cognizant)
- [ ] Watchlists with quarterly "what changed" diffs
- [ ] Evaluation leaderboard across LLMs (grounding rate, cost)

## Disclaimer

This software and its reports are for **educational and informational purposes only**. They are not investment advice, research recommendations or an offer to buy or sell securities. The authors and contributors are not registered with SEBI as Research Analysts or Investment Advisers. Data comes from third-party public sources and may be inaccurate, incomplete or delayed. AI-generated text can contain errors even after verification. Always check primary filings and consult a SEBI-registered professional before investing. Use at your own risk.

## License

MIT © 2026 Rohan Shah
