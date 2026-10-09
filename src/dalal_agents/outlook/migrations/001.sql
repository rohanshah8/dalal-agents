CREATE TABLE IF NOT EXISTS outlook_analyses (
    analysis_id TEXT PRIMARY KEY,
    request_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    response_json TEXT NOT NULL,
    audit_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outlook_request_idx ON outlook_analyses(request_key, expires_at);
CREATE INDEX IF NOT EXISTS outlook_history_idx ON outlook_analyses(exchange, symbol, generated_at DESC);
CREATE TABLE IF NOT EXISTS outlook_leases (
    request_key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL
);
PRAGMA user_version = 1;
