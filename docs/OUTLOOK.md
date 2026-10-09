# Multi-horizon outlook and alternative discovery

Dalal Agents generates independent 5, 21 and 63 trading-session research scenarios for NSE/BSE equities. The feature is available in the **Outlook & alternatives** tab in both UIs, exported research reports, the Python API, CLI and REST API. It requires no LLM or paid credentials.

This analysis is for research and educational purposes and does not constitute investment advice.

## Architecture and scope

The existing project has Gradio and Streamlit frontends, a threaded Python research pipeline, Pydantic schemas, Yahoo/Screener/news/PDF providers, standard Python logging, disk and memory caches, and pytest. It had no database, authentication layer, dedicated REST API, background queue service or trained forecasting model.

The implementation reuses those components. `outlook/` contains the typed provider protocols, adapters, feature assembly, heuristic engine, alternative screening, SQLite audit store, REST routes and shared escaped rendering. `analytics/outlook.py` reuses the existing RSI/MACD and fundamental calculations. A research `Context` supplies already-fetched company data and the shared price cache. The existing LLM writer remains responsible for its original report sections; it receives no authority to change the outlook.

FastAPI is already used by Gradio and is now an explicit optional web dependency. The Gradio entry point mounts the original UI under the same server as `/api/*`. Streamlit uses the same service directly; `dalal serve` can run its companion REST server. SQLite uses the Python standard library, with a versioned migration packaged in the wheel. There is no order execution or brokerage integration.

The public Space starts through Gradio's native `Blocks.launch`, with the REST application mounted at `/api`. This preserves the Spaces SDK startup hook required by the existing ZeroGPU hardware. Launching Uvicorn directly bypasses that hook and causes the Space to fail startup even when local API tests pass. The production launch path is covered by a local HTTP integration test.

**Supported universe:** Indian NSE symbols and BSE symbols/codes, INR, adjusted daily completed-session prices. US exchanges and other currencies are not silently mapped to Indian equities. Market prices are delayed vendor data, not executable live quotes. A configured custom provider can implement the protocols, but expanding the public universe requires explicit exchange, currency and model validation.

## Run

```bash
pip install -e ".[dev,web]"
export DALAL_CACHE_DIR=/path/to/writable/dalal-cache
dalal outlook TCS
dalal outlook 500325 --exchange BSE
dalal outlook TCS --no-alternatives
dalal serve --port 8000
python app/gradio_app.py   # UI and REST on port 7860
streamlit run app/streamlit_app.py
```

```python
from dalal_agents import outlook

result = outlook("TCS", include_alternatives=True, alternative_limit=3)
print(result.outlooks[0].price_range)
print(result.analysis_id)
```

```bash
curl -X POST http://localhost:8000/api/stocks/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"TCS","exchange":"NSE","include_alternatives":true,"alternative_limit":3}'
```

Routes:

| Route | Behavior |
|---|---|
| `POST /api/stocks/analyze` | Validated analysis; extra request fields are rejected |
| `GET /api/stocks/{symbol}/outlook` | Same idempotent analysis service |
| `GET /api/stocks/{symbol}/outlook/history?limit=20` | Previously observed snapshots, at most 100 |
| `GET /api/stocks/{symbol}/alternatives?horizon=ONE_MONTH` | Alternatives for exactly one horizon |
| `GET /api/docs` | OpenAPI documentation |
| `GET /api/health` | Process health; does not call providers |

Errors use `{"error":{"code":"...","message":"...","trace_id":"..."}}`, with HTTP 422 for invalid input, 404 for an unresolved stock, 429 for request/concurrency limits, 503 for service unavailability and 504 for request timeout. Every request receives `X-Request-ID`. Provider failures become data-quality warnings whenever enough stock information remains. Unavailable forecasts have `status="UNAVAILABLE"`, `movement=null`, `price_range=null`, `expected_return_percent=null`, and confidence zero. All three horizon entries remain present.

The existing application is public and has no accounts. The API adds no credentials or authentication policy. Rate limits are per process/client address, using the actual connection address rather than untrusted forwarding headers. Deploy behind an ingress with appropriate request limits for Internet-scale traffic. Body size is bounded at 4 KiB. Work is globally bounded per service process; SQLite leases deduplicate the same request across processes sharing the database. Use a persistent local volume for SQLite; a multi-host deployment requires a suitable shared store and distributed rate limiter.

## Forecast methodology

There is **no trained or empirically calibrated forecasting model** in the repository. Version `heuristic-1.0` is a replaceable baseline implementing `ForecastEngine`. Confidence is a conservative directional-confidence score, not a validated probability of profit. No historical accuracy or interval coverage is claimed.

Each normalized factor is clipped to [-1, 1]. Correlated momentum and moving-average measures are averaged inside one technical group. Missing factors contribute zero; their weights are not redistributed to make remaining evidence look stronger. Freshness and availability are checked before optional evidence contributes.

| Factor | 1 week | 1 month | 3 months |
|---|---:|---:|---:|
| Technical | 35% | 25% | 15% |
| News/events | 25% | 20% | 10% |
| Broad market | 15% | 10% | 10% |
| Sector | 10% | 15% | 10% |
| Macro | 5% | 5% | 10% |
| Fundamentals | 7% | 15% | 25% |
| Peer valuation | 3% | 10% | 20% |

These weights, thresholds, ranking weights and operating limits live in `outlook/config.py::ForecastConfig`. A JSON override supplied through `DALAL_OUTLOOK_CONFIG` is validated and hashed; the configuration hash participates in caching and audit IDs.

Let `sigma` be the largest available 21/63/252-session realized daily log-return standard deviation, with a conservative 0.5% daily floor. For horizon `h`, baseline noise is `sigma * sqrt(h)`. The mean log-return scenario is the weighted factor score times this noise and the horizon's signal scale. Uncertainty widens the noise for missing factors, incomplete data and event risk.

Lower/median/upper prices are `P * exp(mu - z*u)`, `P * exp(mu)` and `P * exp(mu + z*u)`, where `z=1.28155` and `u` is the widened uncertainty. These are illustrative 10/50/90 distribution percentiles, **not measured 80% coverage**. Expected percentage return is explicitly the return of the median scenario, not the arithmetic expectation of a lognormal distribution. Tail risks and gaps can exceed the displayed bounds.

Direction uses a configurable neutral band: transaction-cost allowance plus 0.2 times horizon uncertainty. Directional confidence starts from the corresponding mass of the illustrative distribution, then applies an explicitly uncalibrated reliability discount, signal agreement, data completeness, stale-data penalties, earnings/event risk, illiquidity, volatility and missing-calendar penalties. The default cap is 70/100 and falls to 45 with incomplete long-term history. This does not claim 70% predictive accuracy. Excessively stale data, fewer than 64 valid sessions, or extreme out-of-distribution prices withhold the forecast entirely.

## Data coverage and point-in-time limitations

Prices use adjusted OHLCV with sorted unique dates, invalid-row filtering and no forward filling across missing sessions. Returns and relative strength use matching start/end trading sessions. Intraday cached candles are not later relabeled as completed closes. Indicators include returns 1/5/10/21/63, SMA20/50/200 distances, RSI, MACD/signal/histogram, Wilder ATR, realized volatility, Bollinger position, volume ratio, 20-session support/resistance and adjusted 52-week high/low distances. Business-day freshness excludes weekends but has no exchange holiday calendar; holiday periods can conservatively appear stale.

Fundamental data reuses Screener financial statements and sector-aware bank treatment. Growth, margin changes, returns on capital, leverage, cash flow and valuation are computed from available rows. Exact FCF is used only when the source supplies it; cash from investing is not relabeled as capex. Actual share-count changes require share counts. Equity-capital changes are not presented as share counts. Earnings surprises, guidance revisions, EV/EBITDA, gross margins and historical valuation percentiles remain unavailable when the current provider lacks the necessary data. Current peer multiples require at least two usable industry observations. No fiscal-period-end timestamp is invented as a filing publication date.

**Publication dates:** the current Screener tables do not expose reliable individual filing publication dates. Their effective availability is the actual original HTTP retrieval time. An analysis timestamp before that observation excludes the record, even if its fiscal period ended earlier. Historical audits replay the observed feature snapshots; they are not point-in-time historical backtests. A future filing provider must preserve actual release dates and restatements before walk-forward performance evaluation is possible.

News uses the existing RSS provider, preserves publication/retrieval times, removes HTML/scripts, deduplicates titles and downweights related coverage. Rule-based event types cover earnings, guidance, products, regulations, litigation, management, financing, M&A, cybersecurity, supply chains, insider coverage and sector developments. Relevance, novelty, explicit source weights and age decay determine impact. Source credibility weights are heuristics, not fact-checking. Undated/future articles and instruction-like content are excluded. No LLM is used in this feature, so articles cannot invoke tools, reveal keys or change numerical outputs through instructions. A complete earnings calendar is unavailable; the model always discloses and discounts that limitation and applies another penalty for observed upcoming events.

Sector indices are explicitly mapped where Yahoo provides a relevant index. Missing indices remain unavailable. Macro adapters retrieve USD/INR, Brent and India VIX proxies. FX/commodity effects are generic Indian market conditions and may differ for exporters or producers. Policy rates and inflation are unavailable by default rather than fabricated. Optional official releases can be supplied as a normalized local JSON file through `DALAL_MACRO_FILE`:

```json
{
  "metrics": {"policy_rate_change_pp": -0.25, "inflation_change_pp": 0.1},
  "metric_sources": {"policy_rate_change_pp": "rbi-release", "inflation_change_pp": "cpi-release"},
  "sources": [
    {
      "id": "rbi-release", "name": "RBI", "title": "Replace with actual official release title",
      "url": "https://www.rbi.org.in/",
      "published_at": "2026-10-01T06:00:00Z", "retrieved_at": "2026-10-01T07:00:00Z",
      "available_at": "2026-10-01T06:00:00Z", "data_period": "Replace with actual period"
    },
    {
      "id": "cpi-release", "name": "MoSPI", "title": "Replace with actual official release title",
      "url": "https://www.mospi.gov.in/",
      "published_at": "2026-09-12T12:00:00Z", "retrieved_at": "2026-09-12T13:00:00Z",
      "available_at": "2026-09-12T12:00:00Z", "data_period": "Replace with actual period"
    }
  ]
}
```

This is a **schema example with illustrative values**, not bundled economic data. Replace it with verified releases. Each metric must map to a source with a known, non-future publication timestamp; stale releases are excluded. No user-supplied remote endpoint is fetched.

Screener page access checks robots.txt and fails closed when permission cannot be checked. Yahoo calls are rate limited, bounded by timeouts/retries, cached and written atomically. Source terms still apply; heavier use should supply licensed adapters. Optional provider failure never substitutes invented data.

## Alternative requirements

The candidate universe is the existing same-industry Screener peer table, ordered by the project's size-similarity ranking and bounded to six candidates by default. Each candidate receives the same provider normalization, analysis timestamp, feature rules and engine as the target.

Qualification requires strictly higher median return **and** confidence, the identical horizon and completed-session timestamp, matching currency/industry/financial-business type, at least 252 observations, quality at least 75, fresh price/fundamental/news categories, average daily turnover at least ₹2 crore, market cap within 4×, volatility within 1.75× and liquidity within 10×. Missing comparison data fails qualification. Ranking combines bounded return, confidence, fundamental, technical, sector and quality components with volatility, liquidity and event penalties. Up to three alternatives are returned **per horizon** (up to nine in the flat response array). No rules are weakened to fill the list. Rejections and ranking scores are recorded in the audit.

## Configuration, storage and operations

| Variable | Default / purpose |
|---|---|
| `DALAL_OUTLOOK_ENABLED` | `1`; set `0` to omit outlooks from the legacy research workflow |
| `DALAL_OUTLOOK_CONFIG` | Optional JSON override for `ForecastConfig` |
| `DALAL_OUTLOOK_DB` | `${DALAL_CACHE_DIR}/outlook.sqlite3` |
| `DALAL_MACRO_FILE` | Optional local validated official-release JSON |
| `DALAL_CACHE_DIR` | Existing project cache setting |
| `GRADIO_SERVER_NAME`, `GRADIO_SERVER_PORT` | Existing Gradio server bind options; default port 7860 |

The provisional request lookup includes canonical symbol/exchange, alternative options, a 15-minute bucket, model/feature/configuration versions, offline mode, price-history setting and macro-file revision. The final cache/audit ID additionally hashes the complete normalized feature snapshot, including the market timestamp and data version. Duplicated in-flight requests share one Future; expiring SQLite leases cover independent processes. Failed requests release leases and do not poison cache entries. Timed-out workers retain their bounded slots until they actually finish. Provider data may have a longer TTL than result rendering; all original retrieval and market timestamps remain visible.

Audits store the validated request, every target/candidate feature snapshot, source metadata, all horizon outputs, effective configuration, versions, ranking rejections and selected alternatives. They store no Settings object, API key, authentication token or LLM prompt. SQLite schema migration `001.sql` is installed automatically. Default audit retention is 365 days. HF `/tmp` storage is ephemeral unless configured to a persistent volume; configure `DALAL_OUTLOOK_DB`/`DALAL_CACHE_DIR` accordingly when historical retention is required.

Rendering escapes external text and uses validated public HTTPS links with `noopener noreferrer`. API input permits stock identifiers and exchanges, not provider URLs. Error logs expose error classes and trace/analysis IDs rather than raw provider responses or secrets. The current application has no authentication or user-specific forecast state, so only public-source deterministic outlooks are shared and persisted; API-key research results keep the existing private-cache policy.

## Validation

Use the existing `pytest -q` and `ruff check src tests app` gates. Feature tests use normalized fixtures and fake providers, including numerical indicators, publication timing, missing/stale data, conflict/event penalties, range and confidence bounds, strict alternative qualification/ranking, response validation, REST errors/rate limits, audit replay, single-flight behavior, HTML escaping and frontend states. Live provider availability and empirical forecast accuracy require separate operational monitoring/evaluation; passing fixture tests is not evidence of investment performance.
