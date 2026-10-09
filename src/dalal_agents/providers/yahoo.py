"""Yahoo Finance provider (via yfinance) — daily prices, benchmark index, ticker search."""
from __future__ import annotations

import io
import logging
import pickle
import threading
import time

import pandas as pd

from ..http import HttpClient

log = logging.getLogger(__name__)

BENCHMARK = "^NSEI"  # NIFTY 50


class YahooProvider:
    name = "yahoo-finance"
    _network_lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, http: HttpClient):
        self.http = http
        self.cache_dir = http.cache_dir.parent / "yahoo"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cached(self, key: str, fn, ttl_s: float = 6 * 3600):
        path = self.cache_dir / (key.replace("^", "_").replace("/", "_") + ".pkl")
        with self._network_lock:
            if not self.http.settings.no_cache and path.exists() and (self.http.settings.offline or time.time() - path.stat().st_mtime < ttl_s):
                try:
                    return pickle.loads(path.read_bytes())
                except (OSError, EOFError, pickle.UnpicklingError):
                    log.warning("Invalid Yahoo cache entry; attempting a fresh fetch")
            if self.http.settings.offline:
                raise RuntimeError(f"offline: no cached Yahoo data for {key}")
            for attempt in range(2):
                delay = max(0, 1 - (time.monotonic() - YahooProvider._last_request))
                time.sleep(delay)
                YahooProvider._last_request = time.monotonic()
                try:
                    val = fn()
                    break
                except Exception:
                    if attempt == 1:
                        raise
                    time.sleep(1)
            if not self.http.settings.no_cache:
                import os
                import tempfile
                buf = io.BytesIO()
                pickle.dump(val, buf)
                try:
                    with tempfile.NamedTemporaryFile(dir=self.cache_dir, delete=False) as staged:
                        staged.write(buf.getvalue())
                    os.replace(staged.name, path)
                except OSError:
                    log.warning("Yahoo cache write unavailable")
            return val

    def history(self, yahoo_symbol: str, period: str = "5y") -> pd.DataFrame:
        import yfinance as yf

        def fetch():
            df = yf.Ticker(yahoo_symbol).history(period=period, auto_adjust=True, timeout=20)
            if df is None or df.empty:
                raise LookupError(f"no price history for {yahoo_symbol}")
            df.index = pd.to_datetime(df.index).tz_localize(None)
            return df[["Open", "High", "Low", "Close", "Volume"]]

        return self._cached(f"hist_{yahoo_symbol}_{period}", fetch)

    def info(self, yahoo_symbol: str) -> dict:
        import yfinance as yf
        try:
            return self._cached(f"info_{yahoo_symbol}", lambda: dict(yf.Ticker(yahoo_symbol).info or {}),
                                ttl_s=24 * 3600)
        except Exception as e:
            log.debug("yahoo info failed for %s: %s", yahoo_symbol, e)
            return {}

    def resolve(self, symbol: str) -> str | None:
        """Map an NSE symbol / BSE code to a Yahoo ticker that has data."""
        candidates = [f"{symbol}.BO"] if symbol.isdigit() else [f"{symbol}.NS", f"{symbol}.BO"]
        for c in candidates:
            try:
                self.history(c, "1mo")
                return c
            except Exception:
                continue
        return None

    def search(self, query: str) -> list[dict]:
        import yfinance as yf
        try:
            quotes = yf.Search(query, max_results=8).quotes
        except Exception:
            return []
        return [q for q in quotes if str(q.get("symbol", "")).endswith((".NS", ".BO"))]
