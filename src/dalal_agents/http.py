"""Polite, cached HTTP client shared by all providers.

* disk cache keyed by URL + params (TTL per call)
* per-host minimum interval (default 1s) so we never hammer a site
* retries with exponential backoff on 429/5xx
* `offline=True` serves only from cache (reproducible runs, tests)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

from .config import Settings

log = logging.getLogger(__name__)


class OfflineMiss(RuntimeError):
    pass


class HttpClient:
    def __init__(self, settings: Settings, min_interval_s: float = 1.0):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": settings.user_agent,
            "Accept-Language": "en-IN,en;q=0.9",
        })
        self.cache_dir = Path(settings.cache_dir) / "http"
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # read-only home, disk quota, ...: run uncached rather than crash
            log.warning("cache disabled (%s); set DALAL_CACHE_DIR to a writable path", e)
            settings.no_cache = True
        self.min_interval_s = min_interval_s
        self._last_hit: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ cache
    def _key(self, url: str, params: dict | None) -> Path:
        full = url + ("?" + urlencode(sorted(params.items())) if params else "")
        return self.cache_dir / hashlib.sha1(full.encode()).hexdigest()

    def _read_cache(self, path: Path, ttl_s: float) -> bytes | None:
        meta = path.with_suffix(".json")
        if not path.exists() or not meta.exists():
            return None
        try:
            ts = json.loads(meta.read_text())["ts"]
        except Exception:
            return None
        if self.settings.offline or time.time() - ts <= ttl_s:
            return path.read_bytes()
        return None

    def _write_cache(self, path: Path, url: str, content: bytes) -> None:
        try:
            path.write_bytes(content)
            path.with_suffix(".json").write_text(json.dumps({"url": url, "ts": time.time()}))
        except OSError as e:
            log.warning("cache write failed for %s: %s", url, e)

    # ------------------------------------------------------------- throttling
    def _throttle(self, host: str) -> threading.Lock:
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        return lock

    def get_bytes(self, url: str, params: dict | None = None, ttl_s: float = 12 * 3600,
                  headers: dict | None = None, timeout: float = 30, retries: int = 3) -> bytes:
        path = self._key(url, params)
        if not self.settings.no_cache:
            cached = self._read_cache(path, ttl_s)
            if cached is not None:
                return cached
        if self.settings.offline:
            raise OfflineMiss(f"offline mode and no cache for {url}")

        host = urlparse(url).netloc
        lock = self._throttle(host)
        last_err: Exception | None = None
        for attempt in range(retries):
            with lock:
                wait = self.min_interval_s - (time.time() - self._last_hit.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                self._last_hit[host] = time.time()
                try:
                    r = self.session.get(url, params=params, headers=headers, timeout=timeout)
                except requests.RequestException as e:
                    last_err = e
                    r = None
            if r is not None:
                if r.status_code == 200:
                    self._write_cache(path, url, r.content)
                    return r.content
                if r.status_code == 404:
                    raise requests.HTTPError(f"404 Not Found: {url}", response=r)
                last_err = requests.HTTPError(f"HTTP {r.status_code} for {url}", response=r)
                if r.status_code not in (202, 429, 500, 502, 503, 504):
                    break
            time.sleep(1.5 * (2 ** attempt))
        raise last_err or RuntimeError(f"failed to fetch {url}")

    def get_text(self, url: str, **kw) -> str:
        return self.get_bytes(url, **kw).decode("utf-8", errors="replace")

    def post_json(self, url: str, payload: dict, headers: dict | None = None, timeout: float = 120) -> dict:
        r = self.session.post(url, json=payload, headers=headers, timeout=timeout)
        if r.status_code >= 400:
            raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:500]}", response=r)
        return r.json()
