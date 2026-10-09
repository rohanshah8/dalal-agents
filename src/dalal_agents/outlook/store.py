"""SQLite audit snapshots, cache, and expiring cross-process analysis leases. No credentials."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from importlib.resources import files
from pathlib import Path

from .models import StockOutlook, now_utc


class OutlookStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Outlook database schema is newer than this application.")
            if version < 1:
                db.executescript(files("dalal_agents.outlook").joinpath("migrations/001.sql").read_text())

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def cached(self, key: str) -> StockOutlook | None:
        with self.connect() as db:
            row = db.execute("SELECT response_json FROM outlook_analyses WHERE request_key=? AND expires_at>? ORDER BY generated_at DESC LIMIT 1", (key, time.time())).fetchone()
        return StockOutlook.model_validate_json(row[0]) if row else None

    def save(self, key: str, response: StockOutlook, audit: dict, ttl: int, retention_days: int) -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO outlook_analyses VALUES(?,?,?,?,?,?,?,?)", (
                response.analysis_id, key, response.symbol, response.exchange, response.analysis_timestamp.isoformat(),
                time.time() + ttl, response.model_dump_json(), json.dumps(audit, ensure_ascii=False, allow_nan=False)))
            cutoff = (now_utc() - timedelta(days=retention_days)).isoformat()
            db.execute("DELETE FROM outlook_analyses WHERE generated_at<?", (cutoff,))

    def history(self, symbol: str, exchange: str, limit: int = 20) -> list[StockOutlook]:
        with self.connect() as db:
            rows = db.execute("SELECT response_json FROM outlook_analyses WHERE symbol=? AND exchange=? ORDER BY generated_at DESC LIMIT ?", (symbol, exchange, min(max(limit, 1), 100))).fetchall()
        return [StockOutlook.model_validate_json(row[0]) for row in rows]

    def audit(self, analysis_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT audit_json FROM outlook_analyses WHERE analysis_id=?", (analysis_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def acquire(self, key: str, owner: str, ttl: int) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM outlook_leases WHERE expires_at<?", (time.time(),))
            return db.execute("INSERT OR IGNORE INTO outlook_leases VALUES(?,?,?)", (key, owner, time.time() + ttl)).rowcount == 1

    def release(self, key: str, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM outlook_leases WHERE request_key=? AND owner=?", (key, owner))
