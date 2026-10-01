"""Core data models shared by providers, agents and the report layer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Source(BaseModel):
    """Where a fact or excerpt came from."""

    provider: str
    url: str | None = None
    title: str | None = None
    retrieved_at: str = Field(default_factory=utcnow)


class Fact(BaseModel):
    """A single machine-checkable datum. IDs (F1, F2…) are assigned by the FactStore."""

    id: str = ""
    key: str  # e.g. "TCS.sales_cagr_5y"
    label: str  # human-readable label
    value: float | str | None
    unit: str = ""  # "%", "₹ cr", "x", "days", ""
    period: str | None = None  # "FY2026", "TTM", "Jun 2026"
    company: str | None = None
    source: Source


class Excerpt(BaseModel):
    """A piece of text evidence (headline, snippet, transcript quote). IDs: E1, E2…"""

    id: str = ""
    kind: Literal["news", "web", "transcript", "filing", "profile", "other"] = "other"
    text: str
    date: str | None = None
    company: str | None = None
    source: Source


class Finding(BaseModel):
    """Output of one agent: structured data + the fact/excerpt IDs it produced."""

    agent: str
    company: str
    status: Literal["ok", "partial", "unavailable"] = "ok"
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    fact_ids: list[str] = Field(default_factory=list)
    excerpt_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0


class Table(BaseModel):
    """A labelled row × period table (Screener style). Values in ₹ crore unless noted."""

    periods: list[str]
    rows: dict[str, list[float | None]]

    def get(self, *labels: str) -> list[float | None] | None:
        """Return the first row whose normalised label matches any of `labels`."""
        norm = {_norm(k): k for k in self.rows}
        for label in labels:
            k = norm.get(_norm(label))
            if k is not None:
                return self.rows[k]
        return None


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


class Document(BaseModel):
    kind: Literal["concall", "annual_report", "announcement", "credit_rating", "presentation"]
    title: str
    url: str
    date: str | None = None


class CompanyProfile(BaseModel):
    symbol: str  # NSE symbol or BSE code as used by Screener
    name: str
    yahoo_symbol: str | None = None
    about: str | None = None
    website: str | None = None
    sector_path: list[str] = Field(default_factory=list)  # broad sector → … → industry
    is_financial: bool = False
    consolidated: bool = True
    screener_url: str | None = None
    screener_company_id: str | None = None
    screener_warehouse_id: str | None = None
    top_ratios: dict[str, float | None] = Field(default_factory=dict)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    growth_ranges: dict[str, dict[str, float | None]] = Field(default_factory=dict)


class CompanyData(BaseModel):
    """Everything fetched for one company from structured sources."""

    profile: CompanyProfile
    quarters: Table | None = None
    profit_loss: Table | None = None
    balance_sheet: Table | None = None
    cash_flow: Table | None = None
    ratios: Table | None = None
    shareholding: Table | None = None
    documents: list[Document] = Field(default_factory=list)
    source: Source | None = None


class Peer(BaseModel):
    symbol: str
    name: str
    url: str | None = None
    price: float | None = None
    pe: float | None = None
    market_cap: float | None = None  # ₹ cr
    div_yield: float | None = None
    np_qtr: float | None = None
    qtr_profit_var: float | None = None
    sales_qtr: float | None = None
    qtr_sales_var: float | None = None
    roce: float | None = None
    rank: int | None = None
    relevance: float | None = None
    reason: str | None = None


class Report(BaseModel):
    symbol: str
    name: str
    generated_at: str = Field(default_factory=utcnow)
    model: str | None = None
    findings: dict[str, Finding] = Field(default_factory=dict)
    peers: list[Peer] = Field(default_factory=list)
    peer_findings: dict[str, dict[str, Finding]] = Field(default_factory=dict)
    scorecard: dict[str, Any] = Field(default_factory=dict)
    narrative: dict[str, str] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    facts: list[Fact] = Field(default_factory=list)
    excerpts: list[Excerpt] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
