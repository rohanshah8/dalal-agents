"""One versioned configuration for forecasting, screening, and operational limits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import Field, model_validator

from .models import Horizon, Schema

MODEL_VERSION = "heuristic-1.0"
FEATURE_VERSION = "outlook-features-1.0"


class HorizonConfig(Schema):
    days: int
    weights: dict[str, float]
    signal_scale: float = Field(gt=0, le=2)


class ForecastConfig(Schema):
    horizons: dict[Horizon, HorizonConfig] = Field(default_factory=lambda: {
        "ONE_WEEK": HorizonConfig(days=5, signal_scale=0.7, weights={
            "technical": .35, "news": .25, "market": .15, "sector": .10, "macro": .05,
            "fundamental": .07, "valuation": .03}),
        "ONE_MONTH": HorizonConfig(days=21, signal_scale=.85, weights={
            "technical": .25, "news": .20, "market": .10, "sector": .15, "macro": .05,
            "fundamental": .15, "valuation": .10}),
        "THREE_MONTHS": HorizonConfig(days=63, signal_scale=1., weights={
            "technical": .15, "news": .10, "market": .10, "sector": .10, "macro": .10,
            "fundamental": .25, "valuation": .20}),
    })
    neutral_sigma: float = Field(default=.20, ge=.05, le=1)
    transaction_cost_percent: float = Field(default=.25, ge=0, le=5)
    range_z: float = Field(default=1.281551566, ge=1, le=3)
    daily_volatility_floor: float = Field(default=.005, gt=0, le=.05)
    heuristic_reliability: float = Field(default=.75, gt=0, le=.8)
    confidence_cap: float = Field(default=70, ge=1, le=80)
    fresh_business_days: int = Field(default=3, ge=1, le=5)
    max_stale_business_days: int = Field(default=10, ge=5, le=30)
    min_history: int = Field(default=64, ge=30, le=252)
    full_history: int = Field(default=252, ge=200)
    min_daily_turnover_inr: float = Field(default=20_000_000, gt=0)
    max_market_cap_ratio: float = Field(default=4, ge=1, le=10)
    max_volatility_ratio: float = Field(default=1.75, ge=1, le=3)
    max_liquidity_ratio: float = Field(default=10, ge=1, le=50)
    alternative_quality_min: float = Field(default=75, ge=60, le=100)
    candidate_limit: int = Field(default=6, ge=1, le=12)
    cache_seconds: int = Field(default=900, ge=60, le=3600)
    request_timeout_seconds: int = Field(default=180, ge=5, le=600)
    max_concurrent: int = Field(default=2, ge=1, le=8)
    requests_per_minute: int = Field(default=30, ge=1, le=300)
    audit_retention_days: int = Field(default=365, ge=1, le=3650)
    ranking_weights: dict[str, float] = Field(default_factory=lambda: {
        "return": .30, "confidence": .20, "fundamental": .10, "technical": .10,
        "sector": .05, "quality": .15, "volatility": -.05, "liquidity": -.025, "event": -.025})
    sector_benchmarks: dict[str, str] = Field(default_factory=lambda: {
        "information technology": "^CNXIT", "it - software": "^CNXIT", "software": "^CNXIT",
        "bank": "^NSEBANK", "pharma": "^CNXPHARMA", "automobile": "^CNXAUTO",
        "fmcg": "^CNXFMCG", "metal": "^CNXMETAL", "energy": "^CNXENERGY", "realty": "^CNXREALTY"})

    @model_validator(mode="after")
    def valid_weights(self):
        expected = {"ONE_WEEK": 5, "ONE_MONTH": 21, "THREE_MONTHS": 63}
        factors = {"technical", "news", "market", "sector", "macro", "fundamental", "valuation"}
        if set(self.horizons) != set(expected):
            raise ValueError("All three horizons must be configured.")
        for key, h in self.horizons.items():
            if h.days != expected[key] or set(h.weights) != factors or any(w < 0 for w in h.weights.values()) or abs(sum(h.weights.values()) - 1) > 1e-6:
                raise ValueError("Horizon weights must cover all factors, be nonnegative, and sum to one.")
        if set(self.ranking_weights) != {"return", "confidence", "fundamental", "technical", "sector", "quality", "volatility", "liquidity", "event"}:
            raise ValueError("Ranking weights must cover all ranking components.")
        if any(v > 0 if k in {"volatility", "liquidity", "event"} else v < 0 for k, v in self.ranking_weights.items()):
            raise ValueError("Ranking penalties must be nonpositive and benefit weights nonnegative.")
        if self.min_history > self.full_history:
            raise ValueError("Minimum history cannot exceed the full-history requirement.")
        return self

    @property
    def version(self) -> str:
        raw = json.dumps(self.model_dump(), sort_keys=True).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    @classmethod
    def load(cls, path: Path | None = None):
        return cls.model_validate_json(path.read_text()) if path else cls()
