"""REST routes on the same FastAPI stack used by Gradio; optional web dependency only."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ..config import Settings
from .config import ForecastConfig
from .models import AnalysisRequest, Horizon, StockOutlook
from .service import OutlookError, OutlookService, get_service

log = logging.getLogger(__name__)


class RateLimiter:
    """Bounded per-client token buckets. Proxy forwarding headers are deliberately untrusted."""
    def __init__(self, per_minute=30):
        self.limit = per_minute
        self.lock = threading.Lock()
        self.clients = OrderedDict()

    def allow(self, client):
        now = time.monotonic()
        with self.lock:
            tokens, timestamp = self.clients.pop(client, (float(self.limit), now))
            tokens = min(self.limit, tokens + (now - timestamp) * self.limit / 60)
            allowed = tokens >= 1
            self.clients[client] = (tokens - int(allowed), now)
            while len(self.clients) > 4096:
                self.clients.popitem(last=False)
            return allowed


def create_app(service: OutlookService | None = None) -> FastAPI:
    app = FastAPI(title="Dalal Agents Outlook API", version="1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
    config = service.config if service else ForecastConfig.load(Settings().outlook_config_file)
    limiter = RateLimiter(config.requests_per_minute)

    def active_service():
        return service or get_service()

    def validated_request(**kwargs):
        try:
            return AnalysisRequest(**kwargs)
        except ValidationError:
            raise OutlookError("INVALID_REQUEST", "The ticker, exchange or parameters are invalid.", 422) from None

    def error(request, code, message, status, headers=None):
        trace = getattr(request.state, "trace_id", uuid.uuid4().hex)
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "trace_id": trace}},
                            headers={"X-Request-ID": trace, **(headers or {})})

    @app.middleware("http")
    async def tracing(request: Request, call_next):
        request.state.trace_id = uuid.uuid4().hex
        if request.url.path.startswith("/api/stocks"):
            if not limiter.allow(request.client.host if request.client else "unknown"):
                return error(request, "RATE_LIMITED", "Too many requests. Please retry shortly.", 429, {"Retry-After": "60"})
            content_length = request.headers.get("content-length", "0")
            if not content_length.isdigit() or int(content_length) > 4096:
                return error(request, "REQUEST_TOO_LARGE", "Request body exceeds the size limit.", 413)
            if request.method == "POST":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 4096:
                        return error(request, "REQUEST_TOO_LARGE", "Request body exceeds the size limit.", 413)
                request._body = bytes(body)  # Starlette's cached Request replays this for the JSON validator.
        try:
            response = await call_next(request)
        except Exception as exc:
            log.error("outlook request failed trace_id=%s error_type=%s", request.state.trace_id, type(exc).__name__)
            return error(request, "INTERNAL_ERROR", "The outlook could not be completed. Please retry.", 500)
        response.headers["X-Request-ID"] = request.state.trace_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return error(request, "INVALID_REQUEST", "Invalid symbol, exchange or parameters. Use an NSE ticker or BSE code and an alternative limit from 0 to 3.", 422)

    @app.exception_handler(OutlookError)
    async def service_error(request, exc):
        return error(request, exc.code, str(exc), exc.status, {"Retry-After": "10"} if exc.status in (429, 503, 504) else None)

    @app.post("/api/stocks/analyze", response_model=StockOutlook)
    def analyze(request: AnalysisRequest):
        return active_service().analyze(request)

    @app.get("/api/stocks/{symbol}/outlook", response_model=StockOutlook)
    def outlook(symbol: str, exchange: Literal["NSE", "BSE"] | None = None,
                include_alternatives: bool = True, alternative_limit: int = Query(3, ge=0, le=3)):
        return active_service().analyze(validated_request(symbol=symbol, exchange=exchange,
                                                        include_alternatives=include_alternatives, alternative_limit=alternative_limit))

    @app.get("/api/stocks/{symbol}/outlook/history", response_model=list[StockOutlook])
    def history(symbol: str, exchange: Literal["NSE", "BSE"] | None = None, limit: int = Query(20, ge=1, le=100)):
        request = validated_request(symbol=symbol, exchange=exchange)
        return active_service().store.history(request.symbol, request.exchange, limit)

    @app.get("/api/stocks/{symbol}/alternatives")
    def alternatives(symbol: str, horizon: Horizon = "ONE_MONTH", exchange: Literal["NSE", "BSE"] | None = None):
        from .models import EMPTY_ALTERNATIVES
        result = active_service().analyze(validated_request(symbol=symbol, exchange=exchange))
        selected = [a for a in result.alternatives if a.comparison_horizon == horizon]
        return {"symbol": result.symbol, "horizon": horizon, "analysis_timestamp": result.analysis_timestamp,
                "alternatives": selected, "message": None if selected else EMPTY_ALTERNATIVES,
                "disclaimer": result.disclaimer}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
