"""Validated wire contracts and normalized provider records. All timestamps use UTC."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISCLAIMER = "This analysis is for research and educational purposes and does not constitute investment advice."
EMPTY_ALTERNATIVES = (
    "No qualifying alternative stock was identified based on the current forecast, confidence, "
    "risk, liquidity, and data-quality requirements."
)
Horizon = Literal["ONE_WEEK", "ONE_MONTH", "THREE_MONTHS"]
Category = Literal["FUNDAMENTAL", "TECHNICAL", "NEWS", "SECTOR", "MACRO"]
Freshness = Literal["FRESH", "STALE", "UNKNOWN", "UNAVAILABLE"]
Movement = Literal["BULLISH", "NEUTRAL", "BEARISH"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def public_link(value: str | None) -> str | None:
    """Only public HTTPS links are rendered; these links are never fetched by the engine."""
    import ipaddress

    if not value or any(c in value for c in '\r\n<>"\\'):
        return None
    try:
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
            return None
        host = u.hostname.lower()
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        return value
    except ValueError:
        return None


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)

    @field_validator("*", mode="after")
    @classmethod
    def timezone_required(cls, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Timestamps must include a timezone.")
            return value.astimezone(timezone.utc)
        return value


class AnalysisRequest(Schema):
    symbol: str = Field(min_length=1, max_length=24)
    exchange: Literal["NSE", "BSE"] | None = None
    include_alternatives: bool = True
    alternative_limit: int = Field(default=3, ge=0, le=3)

    @field_validator("symbol", mode="before")
    @classmethod
    def symbol_format(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use an NSE ticker or a six-digit BSE code.")
        value = value.strip().upper()
        if not re.fullmatch(r"(?:[A-Z][A-Z0-9&-]{0,19}|\d{6})(?:\.(?:NS|BO))?", value):
            raise ValueError("Use an NSE ticker or a six-digit BSE code; company names are resolved in the research UI.")
        return value

    @model_validator(mode="after")
    def resolve_exchange(self):
        suffix = "NSE" if self.symbol.endswith(".NS") else "BSE" if self.symbol.endswith(".BO") else None
        bare = self.symbol.removesuffix(".NS").removesuffix(".BO")
        inferred = suffix or ("BSE" if bare.isdigit() else "NSE")
        if self.exchange and ((suffix and suffix != self.exchange) or (bare.isdigit() and self.exchange != "BSE")):
            raise ValueError("Ticker suffix/code and exchange disagree.")
        object.__setattr__(self, "exchange", self.exchange or inferred)
        object.__setattr__(self, "symbol", bare)
        return self


class EvidenceSource(Schema):
    id: str
    name: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    available_at: datetime
    data_period: str | None = None
    freshness: Freshness = "UNKNOWN"
    note: str | None = None

    @field_validator("url")
    @classmethod
    def safe_link(cls, value):
        return public_link(value)

    @field_validator("published_at", "retrieved_at", "available_at")
    @classmethod
    def aware_time(cls, value):
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("Source timestamps must include a timezone.")
        return value.astimezone(timezone.utc)

    @classmethod
    def make(cls, name: str, title: str, retrieved_at: datetime, **kw):
        identity = f"{name}|{title}|{kw.get('url')}|{kw.get('data_period')}"
        return cls(id="S" + hashlib.sha256(identity.encode()).hexdigest()[:12], name=name, title=title,
                   retrieved_at=retrieved_at, available_at=kw.pop("available_at", retrieved_at), **kw)


class PriceBar(Schema):
    timestamp: datetime
    close: float = Field(gt=0)
    high: float | None = Field(default=None, gt=0)
    low: float | None = Field(default=None, gt=0)
    volume: float | None = Field(default=None, ge=0)


class MarketRecord(Schema):
    symbol: str
    exchange: str
    currency: str = "INR"
    adjusted: Literal[True] = True
    bars: list[PriceBar]
    source: EvidenceSource


class FundamentalRecord(Schema):
    symbol: str
    company_name: str
    industry: str = ""
    sector: str = ""
    is_financial: bool = False
    metrics: dict[str, float | None] = Field(default_factory=dict)
    unavailable: list[str] = Field(default_factory=list)
    source: EvidenceSource


class NewsEvent(Schema):
    title: str
    event_type: str
    sentiment: float = Field(ge=-1, le=1)
    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    credibility: float = Field(ge=0, le=1)
    impact_days: int = Field(ge=1, le=90)
    upcoming: bool = False
    source: EvidenceSource


class NewsRecord(Schema):
    events: list[NewsEvent] = Field(default_factory=list)
    source: EvidenceSource
    warnings: list[str] = Field(default_factory=list)


class MacroRecord(Schema):
    metrics: dict[str, float | None] = Field(default_factory=dict)
    metric_sources: dict[str, str] = Field(default_factory=dict)
    sources: list[EvidenceSource] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)


class DataQuality(Schema):
    status: Literal["GOOD", "PARTIAL", "STALE", "INSUFFICIENT"]
    score: float = Field(ge=0, le=100)
    warnings: list[str] = Field(default_factory=list)
    categories: dict[str, Freshness] = Field(default_factory=dict)
    sources: list[EvidenceSource] = Field(default_factory=list)


class FeatureSnapshot(Schema):
    symbol: str
    exchange: str
    company_name: str
    currency: str = "INR"
    analysis_timestamp: datetime
    market_timestamp: datetime | None = None
    industry: str = ""
    sector: str = ""
    is_financial: bool = False
    technical: dict[str, float | None] = Field(default_factory=dict)
    fundamental: dict[str, float | None] = Field(default_factory=dict)
    benchmark: dict[str, float | None] = Field(default_factory=dict)
    sector_metrics: dict[str, float | None] = Field(default_factory=dict)
    macro: dict[str, float | None] = Field(default_factory=dict)
    events: list[NewsEvent] = Field(default_factory=list)
    source_ids: dict[str, list[str]] = Field(default_factory=dict)
    data_quality: DataQuality
    event_risk: float = Field(default=0, ge=0, le=1)
    event_calendar_known: bool = False


class PriceRange(Schema):
    lower: float = Field(gt=0)
    median: float = Field(gt=0)
    upper: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if not self.lower <= self.median <= self.upper:
            raise ValueError("Price range must be ordered lower <= median <= upper.")
        return self


class Reason(Schema):
    category: Category
    summary: str
    impact: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    source_ids: list[str] = Field(default_factory=list)


class HorizonOutlook(Schema):
    horizon: Horizon
    trading_days: Literal[5, 21, 63]
    status: Literal["AVAILABLE", "UNAVAILABLE"] = "AVAILABLE"
    movement: Movement | None = None
    confidence_score: float = Field(ge=0, le=100)
    current_price: float | None = Field(default=None, gt=0)
    expected_return_percent: float | None = None
    price_range: PriceRange | None = None
    reasoning: list[Reason] = Field(min_length=3, max_length=4)
    risks: list[str]
    factor_contributions: dict[str, float] = Field(default_factory=dict)
    uncertainty_percent: float | None = Field(default=None, ge=0)
    neutral_band_percent: float | None = Field(default=None, ge=0)
    model_version: str
    generated_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        if {"ONE_WEEK": 5, "ONE_MONTH": 21, "THREE_MONTHS": 63}[self.horizon] != self.trading_days:
            raise ValueError("Horizon and trading days disagree.")
        if self.status == "AVAILABLE":
            if self.movement is None or self.current_price is None or self.price_range is None or self.expected_return_percent is None:
                raise ValueError("An available outlook must have prices, movement, and return.")
            implied = (self.price_range.median / self.current_price - 1) * 100
            if abs(implied - self.expected_return_percent) > 0.02:
                raise ValueError("Expected return must agree with the median price.")
        elif self.movement is not None or self.price_range is not None or self.expected_return_percent is not None or self.confidence_score != 0:
            raise ValueError("Unavailable outlooks must not contain a fabricated forecast.")
        return self


class ComparisonMetrics(Schema):
    return_difference_percent: float = Field(gt=0)
    confidence_difference: float = Field(gt=0)
    risk_adjusted_score: float


class Alternative(Schema):
    symbol: str
    company_name: str
    exchange: str
    currency: str
    comparison_horizon: Horizon
    movement: Movement
    confidence_score: float = Field(ge=0, le=100)
    current_price: float = Field(gt=0)
    expected_return_percent: float
    price_range: PriceRange
    reasons: list[Reason] = Field(min_length=2, max_length=3)
    key_risk: str
    why_ranked_higher: str
    comparison_metrics: ComparisonMetrics
    data_quality: DataQuality
    market_timestamp: datetime
    analysis_timestamp: datetime


class StockOutlook(Schema):
    analysis_id: str
    symbol: str
    company_name: str
    exchange: str
    currency: str = "INR"
    analysis_timestamp: datetime
    market_timestamp: datetime | None
    current_price: float | None = Field(default=None, gt=0)
    data_quality: DataQuality
    outlooks: list[HorizonOutlook] = Field(min_length=3, max_length=3)
    alternatives: list[Alternative] = Field(default_factory=list)
    model_version: str
    feature_version: str
    configuration_version: str
    data_version: str
    methodology: str
    confidence_method: str
    range_method: str
    disclaimer: Literal[DISCLAIMER] = DISCLAIMER

    @model_validator(mode="after")
    def consistent(self):
        if {o.horizon for o in self.outlooks} != {"ONE_WEEK", "ONE_MONTH", "THREE_MONTHS"}:
            raise ValueError("Each horizon must occur exactly once.")
        known = {s.id for s in self.data_quality.sources}
        for outlook in self.outlooks:
            if any(set(r.source_ids) - known for r in outlook.reasoning):
                raise ValueError("Unknown evidence source.")
        for horizon in ("ONE_WEEK", "ONE_MONTH", "THREE_MONTHS"):
            alts = [a for a in self.alternatives if a.comparison_horizon == horizon]
            if len(alts) > 3 or len({a.symbol for a in alts}) != len(alts):
                raise ValueError("At most three distinct alternatives per horizon.")
            original = next(o for o in self.outlooks if o.horizon == horizon)
            for a in alts:
                if a.symbol == self.symbol or a.analysis_timestamp != self.analysis_timestamp or a.market_timestamp != self.market_timestamp:
                    raise ValueError("Alternative snapshot must match the target.")
                if original.expected_return_percent is None or a.expected_return_percent <= original.expected_return_percent or a.confidence_score <= original.confidence_score:
                    raise ValueError("Alternatives must have strictly higher return and confidence.")
                known_alt = {s.id for s in a.data_quality.sources}
                if any(set(reason.source_ids) - known_alt for reason in a.reasons):
                    raise ValueError("Alternative references unknown evidence.")
                if abs((a.price_range.median / a.current_price - 1) * 100 - a.expected_return_percent) > .02:
                    raise ValueError("Alternative prices and returns disagree.")
                if abs(a.comparison_metrics.return_difference_percent - (a.expected_return_percent - original.expected_return_percent)) > .001 or abs(a.comparison_metrics.confidence_difference - (a.confidence_score - original.confidence_score)) > .001:
                    raise ValueError("Alternative comparison differences disagree.")
        return self
