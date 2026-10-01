"""Agent base class and the shared run context."""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..facts import FactStore
from ..http import HttpClient
from ..llm import LLM
from ..models import CompanyData, Finding, Source
from ..providers import DocumentProvider, NewsProvider, ScreenerProvider, WebSearchProvider, YahooProvider

log = logging.getLogger(__name__)


@dataclass
class Context:
    """Shared, thread-safe-enough state for one analysis run."""

    settings: Settings
    http: HttpClient
    llm: LLM
    facts: FactStore
    screener: ScreenerProvider
    yahoo: YahooProvider
    news: NewsProvider
    web: WebSearchProvider
    docs: DocumentProvider
    company_data: dict[str, CompanyData] = field(default_factory=dict)  # symbol → data
    yahoo_symbols: dict[str, str | None] = field(default_factory=dict)
    benchmark: Any = None  # NIFTY 50 price DataFrame
    progress: Any = None  # callable(str) for UI updates

    @classmethod
    def create(cls, settings: Settings) -> Context:
        http = HttpClient(settings)
        return cls(
            settings=settings, http=http, llm=LLM(settings), facts=FactStore(),
            screener=ScreenerProvider(http), yahoo=YahooProvider(http), news=NewsProvider(http),
            web=WebSearchProvider(http, settings.tavily_api_key), docs=DocumentProvider(http),
        )

    def say(self, msg: str) -> None:
        log.info(msg)
        if self.progress:
            self.progress(msg)


class Agent:
    """An agent turns (company, context) into a Finding, registering facts/excerpts as it goes."""

    name: str = "agent"
    description: str = ""

    def __init__(self, ctx: Context, symbol: str):
        self.ctx = ctx
        self.symbol = symbol
        self.finding = Finding(agent=self.name, company=symbol)

    # convenience -----------------------------------------------------------
    def fact(self, key: str, label: str, value, source: Source, unit: str = "", period: str | None = None):
        fid = self.ctx.facts.add(f"{self.symbol}.{key}", label, value, source, unit=unit, period=period,
                                 company=self.symbol)
        if fid and fid not in self.finding.fact_ids:
            self.finding.fact_ids.append(fid)
        return fid

    def excerpt(self, text: str, source: Source, kind: str = "other", date: str | None = None):
        eid = self.ctx.facts.excerpt(text, source, kind=kind, date=date, company=self.symbol)
        self.finding.excerpt_ids.append(eid)
        return eid

    def data(self) -> CompanyData:
        d = self.ctx.company_data.get(self.symbol)
        if d is None:
            raise LookupError(f"no company data for {self.symbol}")
        return d

    # lifecycle -------------------------------------------------------------
    def run(self) -> Finding:
        t0 = time.time()
        try:
            self.execute()
        except Exception as e:
            log.debug("%s failed for %s: %s", self.name, self.symbol, traceback.format_exc())
            self.finding.status = "unavailable"
            self.finding.errors.append(f"{type(e).__name__}: {e}")
        self.finding.elapsed_s = round(time.time() - t0, 2)
        return self.finding

    def execute(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError
