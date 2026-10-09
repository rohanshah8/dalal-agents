"""Small provider protocols and adapters for the project's existing Indian-market integrations.

External text is inert evidence. It is never evaluated, passed to tools, or used as an instruction.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd
from bs4 import BeautifulSoup

from ..analytics.outlook import clean_prices, fundamental_features, indicators
from ..analytics.peers import rank_competitors
from ..analytics.text import tone
from .config import ForecastConfig
from .models import (
    AnalysisRequest,
    EvidenceSource,
    FundamentalRecord,
    MacroRecord,
    MarketRecord,
    NewsEvent,
    NewsRecord,
    PriceBar,
    now_utc,
)

log = logging.getLogger(__name__)


class MarketDataProvider(Protocol):
    def market(self, symbol: str, exchange: str) -> MarketRecord: ...


class FundamentalDataProvider(Protocol):
    def fundamentals(self, symbol: str) -> FundamentalRecord: ...
    def candidates(self, symbol: str, limit: int) -> list[str]: ...


class NewsDataProvider(Protocol):
    def news(self, symbol: str, company_name: str) -> NewsRecord: ...


class MacroDataProvider(Protocol):
    def macro(self) -> MacroRecord: ...


def inert_text(text: str, limit: int = 500) -> str:
    soup = BeautifulSoup(str(text or "")[:5000], "html.parser")
    for node in soup(["script", "style", "iframe"]):
        node.decompose()
    return " ".join(soup.get_text(" ").split())[:limit]


def timestamp(value) -> datetime | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.to_pydatetime().replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.to_pydatetime().astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


EVENTS = {
    "EARNINGS": ("earnings|results|quarterly profit", 21),
    "GUIDANCE": ("guidance|outlook|forecast|revision", 63),
    "REGULATORY": ("regulator|sebi|rbi|penalty|ban", 63),
    "LITIGATION": ("litigation|lawsuit|court|settlement", 63),
    "MANAGEMENT": ("ceo|cfo|resign|appoint", 21),
    "FINANCING": ("fundrais|debt issue|rights issue|borrowing", 21),
    "MERGER": ("merger|acquisition|acquire|takeover", 63),
    "CYBERSECURITY": ("cyber|hack|ransomware|data breach", 21),
    "SUPPLY_CHAIN": ("supply chain|shortage|factory closure", 21),
    "INSIDER": ("insider|promoter stake", 21),
    "PRODUCT": ("launch|product|approval|contract|order win", 21),
    "SECTOR": ("sector|industry", 21),
}


def normalize_news(items: list[dict], symbol: str, company: str, source: EvidenceSource) -> NewsRecord:
    events, warnings, seen = [], [], set()
    company_words = [w for w in re.findall(r"[a-z]+", company.lower()) if len(w) > 3 and w not in ("limited", "services", "india")]
    for item in items:
        title = inert_text(item.get("title", ""))
        if not title:
            continue
        if re.search(r"ignore (?:all |previous |system )?instructions|system prompt|api[_ -]?key|<\|.*?\|>", title, re.I):
            warnings.append("An instruction-like news item was excluded from evidence.")
            continue
        published = timestamp(item.get("published_at") or item.get("date"))
        if published is None or published > source.retrieved_at:
            warnings.append("An undated or future-dated news item was excluded.")
            continue
        low = title.lower()
        relevance = 1.0 if symbol.lower() in low or any(w in low for w in company_words) else .25
        key = re.sub(r"\W+", " ", low).strip()
        words = set(key.split())
        if key in seen:
            continue
        novelty = 1.0
        for old in seen:
            other = set(old.split())
            if len(words & other) / max(len(words | other), 1) > .65:
                novelty = .25
                break
        seen.add(key)
        event_type, duration = "OTHER", 5
        for label, (pattern, days) in EVENTS.items():
            if re.search(pattern, low):
                event_type, duration = label, days
                break
        publisher = inert_text(item.get("publisher") or "Unknown publisher", 120)
        # These are explicit source weights, not claims that an article has been fact-checked.
        credibility = .9 if any(p in publisher.lower() for p in ("reuters", "bloomberg", "business standard", "economic times")) else .6
        article = EvidenceSource.make("Google News / " + publisher, title, source.retrieved_at,
                                      url=item.get("url"), published_at=published, available_at=published,
                                      data_period=published.isoformat(), freshness="FRESH")
        events.append(NewsEvent(title=title, event_type=event_type, sentiment=tone(title)["net_tone"],
                                relevance=relevance, novelty=novelty, credibility=credibility,
                                impact_days=duration, source=article,
                                upcoming=event_type in ("EARNINGS", "GUIDANCE") and bool(re.search(
                                    r"\b(?:upcoming|scheduled|to announce|will announce|due on|ahead of)\b", low))))
    return NewsRecord(events=events, source=source, warnings=sorted(set(warnings)))


def market_frame(record: MarketRecord | None) -> pd.DataFrame:
    if record is None or not record.bars:
        return pd.DataFrame()
    return pd.DataFrame([{"Close": b.close, "High": b.high, "Low": b.low, "Volume": b.volume}
                         for b in record.bars], index=pd.DatetimeIndex([b.timestamp for b in record.bars]))


class ExistingProviders:
    """Adapters accept a research Context, so company pages and price histories are reused."""

    def __init__(self, context, config: ForecastConfig):
        self.ctx, self.config = context, config

    def market(self, symbol: str, exchange: str) -> MarketRecord:
        if symbol.startswith("^") or symbol in ("INR=X", "BZ=F"):
            allowed = {"^NSEI", "^INDIAVIX", "INR=X", "BZ=F", *self.config.sector_benchmarks.values()}
            if symbol not in allowed:
                raise ValueError("Unsupported benchmark.")
            ticker = symbol
        else:
            request = AnalysisRequest(symbol=symbol, exchange=exchange)
            ticker = request.symbol + (".NS" if request.exchange == "NSE" else ".BO")
        frame = clean_prices(self.ctx.yahoo.history(ticker, self.ctx.settings.price_history))
        if frame.empty:
            raise LookupError("No adjusted market history is available.")
        # Daily candles represent the session close, not midnight or a live quote.
        bars = []
        retrieved = now_utc()
        cache_path = self.ctx.yahoo.cache_dir / f"hist_{ticker.replace('^', '_')}_{self.ctx.settings.price_history}.pkl"
        if cache_path.exists() and not self.ctx.settings.no_cache:
            retrieved = datetime.fromtimestamp(cache_path.stat().st_mtime, timezone.utc)
        for date, row in frame.iterrows():
            close_time = date.normalize() + pd.Timedelta(hours=10)  # 15:30 Asia/Kolkata
            if symbol in ("INR=X", "BZ=F"):
                close_time = date.normalize() + pd.Timedelta(hours=23, minutes=59)
            if close_time.to_pydatetime() > retrieved:
                continue  # never label an unfinished session as an adjusted close
            def val(key, row=row):
                value = row.get(key)
                return float(value) if value is not None and math.isfinite(value) and (value >= 0 if key == "Volume" else value > 0) else None
            bars.append(PriceBar(timestamp=close_time.to_pydatetime(), close=float(row.Close),
                                 high=val("High"), low=val("Low"), volume=val("Volume")))
        if not bars:
            raise LookupError("No completed market session is available.")
        src = EvidenceSource.make("Yahoo Finance", f"{ticker} adjusted daily OHLCV", retrieved,
                                  url=f"https://finance.yahoo.com/quote/{ticker}", published_at=bars[-1].timestamp,
                                  available_at=bars[-1].timestamp, data_period=bars[-1].timestamp.isoformat())
        return MarketRecord(symbol=symbol, exchange=exchange, bars=bars, source=src)

    def fundamentals(self, symbol: str) -> FundamentalRecord:
        data = self.ctx.company_data.get(symbol)
        if data is None:
            data = self.ctx.screener.company(symbol)
            self.ctx.company_data[symbol] = data
        metrics, unavailable = fundamental_features(data)
        url = data.profile.screener_url
        retrieved = self.ctx.http.retrieved_at(url) if url else now_utc()
        period = data.quarters.periods[-1] if data.quarters and data.quarters.periods else None
        source = EvidenceSource.make("Screener.in", f"{symbol} financial statements", retrieved,
                                     url=url, data_period=period,
                                     note="Filing publication dates are not supplied. Known only from retrieval time; not suitable for historical backtests.")
        return FundamentalRecord(symbol=symbol, company_name=inert_text(data.profile.name, 150),
                                 industry=data.profile.sector_path[-1] if data.profile.sector_path else "",
                                 sector=" / ".join(data.profile.sector_path), is_financial=data.profile.is_financial,
                                 metrics=metrics, unavailable=unavailable, source=source)

    def candidates(self, symbol: str, limit: int) -> list[str]:
        data = self.ctx.company_data.get(symbol)
        if data is None:
            self.fundamentals(symbol)
            data = self.ctx.company_data[symbol]
        peers = rank_competitors(symbol, data.profile.top_ratios.get("Market Cap"), self.ctx.screener.peers(data), k=limit)
        result = []
        for peer in peers:
            try:
                req = AnalysisRequest(symbol=peer.symbol)
                if req.symbol not in result and req.symbol != symbol:
                    result.append(req.symbol)
            except ValueError:
                continue
        return result[:limit]

    def news(self, symbol: str, company_name: str) -> NewsRecord:
        query = f'"{company_name}"'
        items = self.ctx.news.search(query, days=60, limit=40)
        params = {"q": f"{query} when:60d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
        retrieved = self.ctx.http.retrieved_at(self.ctx.news.URL, params)
        src = EvidenceSource.make("Google News RSS", f"{symbol} recent company news", retrieved,
                                  url="https://news.google.com/", freshness="FRESH")
        return normalize_news(items, symbol, company_name, src)

    def macro(self) -> MacroRecord:
        metrics, sources, missing, mapping = {}, [], ["India policy rate", "India inflation"], {}
        for ticker, label in (("INR=X", "usd_inr_return_21d"), ("BZ=F", "brent_return_21d"), ("^INDIAVIX", "india_vix")):
            try:
                record = self.market(ticker, "INDEX")
                m = indicators(market_frame(record))
                metrics[label] = m.get("current_price" if label == "india_vix" else "return_21d")
                sources.append(record.source)
                mapping[label] = record.source.id
            except Exception as error:
                log.info("outlook provider unavailable category=macro source=%s error_type=%s", ticker, type(error).__name__)
                missing.append(label)
        # Optional local, normalized official-release data; no arbitrary remote URL is fetched.
        path = getattr(self.ctx.settings, "outlook_macro_file", None)
        if path:
            try:
                official = MacroRecord.model_validate_json(Path(path).read_text())
                cutoff = now_utc()
                allowed = {"policy_rate_change_pp", "inflation_change_pp"}
                if official.sources and all(s.published_at and s.available_at <= cutoff and s.published_at <= cutoff for s in official.sources):
                    metrics.update({k: v for k, v in official.metrics.items() if k in allowed})
                    sources.extend(official.sources)
                    mapping.update({k: v for k, v in official.metric_sources.items() if k in allowed})
                    if metrics.get("policy_rate_change_pp") is not None:
                        missing.remove("India policy rate")
                    if metrics.get("inflation_change_pp") is not None:
                        missing.remove("India inflation")
                else:
                    missing.append("Official macro release has unknown or future publication dates")
            except (ValueError, OSError):
                missing.append("Configured macro release could not be validated")
        return MacroRecord(metrics=metrics, metric_sources=mapping, sources=sources, unavailable=missing)
