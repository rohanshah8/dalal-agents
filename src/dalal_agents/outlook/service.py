"""Shared entry point for research, CLI, REST and both UIs; bounded work and durable audit."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import replace

from ..config import Settings
from .alternatives import discover
from .config import FEATURE_VERSION, MODEL_VERSION, ForecastConfig
from .engine import HeuristicForecastEngine, apply_peer_valuations
from .features import build_snapshot
from .models import AnalysisRequest, StockOutlook, now_utc
from .providers import ExistingProviders
from .store import OutlookStore

log = logging.getLogger(__name__)


class OutlookError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.status = code, status


class OutlookService:
    def __init__(self, settings: Settings | None = None, config: ForecastConfig | None = None,
                 providers=None, store: OutlookStore | None = None):
        # A cached service must never retain a visitor's LLM credentials or endpoints.
        self.settings = replace(settings or Settings(), llm_provider="none", model=None,
                                anthropic_api_key=None, openai_api_key=None, tavily_api_key=None,
                                anthropic_base_url="", openai_base_url="")
        self.config = config or ForecastConfig.load(self.settings.outlook_config_file)
        self.store = store or OutlookStore(self.settings.outlook_db or self.settings.cache_dir / "outlook.sqlite3")
        self.providers = providers
        self.engine = HeuristicForecastEngine(self.config)
        self.pool = ThreadPoolExecutor(max_workers=self.config.max_concurrent, thread_name_prefix="outlook")
        self.slots = threading.BoundedSemaphore(self.config.max_concurrent)
        self.lock = threading.Lock()
        self.flights: dict[str, Future] = {}

    def close(self):
        self.pool.shutdown(wait=True)

    def request_key(self, request: AnalysisRequest) -> str:
        macro = self.settings.outlook_macro_file
        macro_revision = str(macro.stat().st_mtime_ns) if macro and macro.exists() else "none"
        bits = [request.model_dump(), int(time.time() // self.config.cache_seconds), MODEL_VERSION, FEATURE_VERSION,
                self.config.version, self.settings.offline, self.settings.no_cache, self.settings.price_history, macro_revision]
        return hashlib.sha256(json.dumps(bits, sort_keys=True).encode()).hexdigest()

    def analyze(self, request: AnalysisRequest, context=None) -> StockOutlook:
        key = self.request_key(request)
        if not self.settings.no_cache:
            cached = self.store.cached(key)
            if cached:
                return cached
        with self.lock:
            future = self.flights.get(key)
            if future is None:
                if not self.slots.acquire(blocking=False):
                    raise OutlookError("BUSY", "The outlook service is busy. Please retry shortly.", 429)
                try:
                    future = self.pool.submit(self._produce, request, key, context)
                    self.flights[key] = future
                except Exception:
                    self.slots.release()
                    raise
        # Register outside the lock: callbacks can execute immediately for a completed Future.
        def release(done):
            with self.lock:
                if self.flights.get(key) is done:
                    del self.flights[key]
                    self.slots.release()
        future.add_done_callback(release)
        try:
            return future.result(timeout=self.config.request_timeout_seconds).model_copy(deep=True)
        except TimeoutError:
            # The bounded worker retains its slot and single-flight entry until it actually ends.
            raise OutlookError("TIMEOUT", "The analysis exceeded the request time limit. Retry shortly; duplicate work is suppressed.", 504) from None

    def _produce(self, request, key, context):
        owner = uuid.uuid4().hex
        started = time.monotonic()
        acquired = False
        try:
            while not acquired:
                acquired = self.store.acquire(key, owner, self.config.request_timeout_seconds + 900)
                if not acquired:
                    cached = self.store.cached(key) if not self.settings.no_cache else None
                    if cached:
                        return cached
                    if time.monotonic() - started > self.config.request_timeout_seconds:
                        raise OutlookError("TIMEOUT", "An analysis is already in progress.", 504)
                    time.sleep(.2)
            if not self.settings.no_cache:
                cached = self.store.cached(key)
                if cached:
                    return cached
            if self.providers is not None:
                providers = self.providers
            else:
                if context is None:
                    from ..agents.base import Context
                    # Keys and LLMs are unnecessary for this deterministic path.
                    context = Context.create(replace(self.settings, llm_provider="none", anthropic_api_key=None,
                                                     openai_api_key=None, tavily_api_key=None))
                providers = ExistingProviders(context, self.config)
            records, shared = self._collect(request, providers, started)
            as_of = now_utc()
            snapshots = []
            for symbol, exchange, market, fundamental, news, warnings, sector in records:
                snapshots.append(build_snapshot(symbol, exchange, as_of, self.config, market, fundamental, news,
                                                shared["benchmark"], sector, shared["macro"], warnings))
            apply_peer_valuations(snapshots)
            target = snapshots[0]
            forecasts = [self.engine.forecast(s) for s in snapshots]
            alternatives, ranking = discover(target, forecasts[0], list(zip(snapshots[1:], forecasts[1:])),
                                              self.config, request.alternative_limit if request.include_alternatives else 0)
            inputs = [s.model_dump(mode="json") for s in snapshots]
            data_version = hashlib.sha256(json.dumps(inputs, sort_keys=True, allow_nan=False).encode()).hexdigest()
            analysis_id = hashlib.sha256(f"{key}|{data_version}".encode()).hexdigest()[:32]
            result = StockOutlook(analysis_id=analysis_id, symbol=request.symbol, exchange=request.exchange,
                                  company_name=target.company_name, currency=target.currency,
                                  current_price=target.technical.get("current_price"), analysis_timestamp=as_of,
                                  market_timestamp=target.market_timestamp, data_quality=target.data_quality,
                                  outlooks=forecasts[0], alternatives=alternatives, model_version=MODEL_VERSION,
                                  feature_version=FEATURE_VERSION, configuration_version=self.config.version,
                                  data_version=data_version,
                                  methodology="Heuristic baseline with grouped factors and separate 5, 21 and 63-session weights. No trained forecast or measured historical reliability is claimed.",
                                  confidence_method="Conservative direction-confidence score from a volatility distribution, signal agreement, data quality and risk penalties. Not a calibrated probability or guarantee.",
                                  range_method="Illustrative 10th/50th/90th percentiles of a log-return distribution, widened for missing evidence and event risk. Coverage is not empirically calibrated; expected return refers to the median scenario.")
            audit = {"request": request.model_dump(), "snapshots": inputs, "configuration": self.config.model_dump(),
                     "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "data_version": data_version,
                     "prompt_version": None, "candidate_forecasts": [[o.model_dump(mode="json") for o in group] for group in forecasts],
                     "alternative_ranking": ranking, "response": result.model_dump(mode="json")}
            ttl = self.config.cache_seconds if result.data_quality.status in ("GOOD", "PARTIAL") else 60
            self.store.save(key, result, audit, ttl, self.config.audit_retention_days)
            log.info("outlook completed analysis_id=%s symbol=%s quality=%s elapsed_s=%.2f", analysis_id, request.symbol,
                     result.data_quality.status, time.monotonic() - started)
            return result
        finally:
            if acquired:
                self.store.release(key, owner)

    def _collect(self, request, providers, started):
        def fetch(label, fn, warnings):
            if time.monotonic() - started > self.config.request_timeout_seconds:
                warnings.append(f"{label} was not fetched before the analysis deadline.")
                return None
            try:
                return fn()
            except Exception as error:
                log.info("outlook provider failure category=%s error_type=%s", label, type(error).__name__)
                warnings.append(f"{label} is unavailable ({type(error).__name__}); no provider error content is exposed.")
                return None

        def company(symbol, exchange):
            warnings = []
            fund = fetch("Fundamentals", lambda: providers.fundamentals(symbol), warnings)
            market = fetch("Market prices", lambda: providers.market(symbol, exchange), warnings)
            name = fund.company_name if fund else symbol
            news = fetch("Company news", lambda: providers.news(symbol, name), warnings)
            return [symbol, exchange, market, fund, news, warnings, None]

        target = company(request.symbol, request.exchange)
        if target[2] is None and target[3] is None:
            failures = [w for w in target[5] if w.startswith(("Fundamentals", "Market prices"))]
            not_found = len(failures) == 2 and all("(LookupError)" in w for w in failures)
            raise OutlookError("SYMBOL_UNAVAILABLE" if not_found else "PROVIDERS_UNAVAILABLE",
                               "The stock could not be resolved from available providers. Check the exchange and ticker, or retry when the providers recover.",
                               404 if not_found else 503)
        shared_warnings = []
        bench = fetch("Broad market benchmark", lambda: providers.market("^NSEI", "INDEX"), shared_warnings)
        macro = fetch("Macro conditions", providers.macro, shared_warnings)
        symbols = fetch("Comparable stock universe", lambda: providers.candidates(request.symbol, self.config.candidate_limit), target[5]) if request.include_alternatives and request.alternative_limit else []
        candidates = []
        for symbol in (symbols or [])[:self.config.candidate_limit]:
            try:
                validated = AnalysisRequest(symbol=symbol)
                if validated.symbol != request.symbol and validated.symbol not in [s.symbol for s in candidates]:
                    candidates.append(validated)
            except ValueError:
                target[5].append("An invalid candidate ticker was excluded.")
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="outlook-provider") as pool:
            peer_records = list(pool.map(lambda r: company(r.symbol, r.exchange), candidates))
        records = [target, *peer_records]
        sector_cache = {}
        for record in records:
            record[5].extend(shared_warnings)
            fund = record[3]
            text = f"{fund.sector} {fund.industry}".lower() if fund else ""
            ticker = next((v for k, v in self.config.sector_benchmarks.items() if k in text), None)
            if ticker and ticker not in sector_cache:
                sector_cache[ticker] = fetch("Sector benchmark", lambda t=ticker: providers.market(t, "INDEX"), record[5])
            record[6] = sector_cache.get(ticker)
        return records, {"benchmark": bench, "macro": macro}


_services: dict[tuple, OutlookService] = {}
_service_lock = threading.Lock()


def get_service(settings: Settings | None = None) -> OutlookService:
    settings = settings or Settings(llm_provider="none")
    config = ForecastConfig.load(settings.outlook_config_file)
    key = (str(settings.outlook_db or settings.cache_dir / "outlook.sqlite3"), config.version, settings.offline,
           settings.no_cache, settings.price_history, str(settings.outlook_macro_file))
    with _service_lock:
        if key not in _services:
            _services[key] = OutlookService(settings, config)
        return _services[key]


def analyze_outlook(symbol: str, exchange: str | None = None, include_alternatives: bool = True,
                    alternative_limit: int = 3, settings: Settings | None = None) -> StockOutlook:
    return get_service(settings).analyze(AnalysisRequest(symbol=symbol, exchange=exchange,
                                                        include_alternatives=include_alternatives,
                                                        alternative_limit=alternative_limit))
