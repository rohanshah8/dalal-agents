"""Point-in-time feature assembly, provenance, and explicit data-quality scoring."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..analytics.outlook import indicators
from .config import ForecastConfig
from .models import DataQuality, FeatureSnapshot, FundamentalRecord, MacroRecord, MarketRecord, NewsRecord
from .providers import market_frame


def business_age(timestamp: datetime, as_of: datetime) -> int:
    return max(0, int(np.busday_count(timestamp.date(), as_of.date())))


def build_snapshot(symbol: str, exchange: str, as_of: datetime, config: ForecastConfig,
                   market: MarketRecord | None = None, fundamental: FundamentalRecord | None = None,
                   news: NewsRecord | None = None, benchmark: MarketRecord | None = None,
                   sector: MarketRecord | None = None, macro: MacroRecord | None = None,
                   warnings: list[str] | None = None) -> FeatureSnapshot:
    warnings = list(warnings or [])
    sources, source_ids, categories = {}, {}, {}

    def add(category, source, freshness):
        copy = source.model_copy(update={"freshness": freshness})
        sources[copy.id] = copy
        source_ids.setdefault(category, []).append(copy.id)

    if market and market.source.available_at > as_of:
        warnings.append("Market record first available after the analysis timestamp was excluded.")
        market = None
    frame = market_frame(market)
    if not frame.empty:
        frame = frame[frame.index <= as_of]
    market_time = frame.index[-1].to_pydatetime() if not frame.empty else None
    cutoff = market_time or as_of
    tech = indicators(frame, market_frame(benchmark), market_frame(sector), cutoff)
    history = tech.get("history_days") or 0
    market_age = business_age(market_time, as_of) if market_time else 9999
    categories["TECHNICAL"] = "FRESH" if market_age <= config.fresh_business_days else "STALE" if market_time else "UNAVAILABLE"
    if market:
        add("TECHNICAL", market.source, categories["TECHNICAL"])
    if market_age > config.fresh_business_days:
        warnings.append("Adjusted market prices are stale or unavailable.")
    if history < config.full_history:
        warnings.append(f"Only {int(history)} valid trading sessions; long-term indicators or model reliability are limited.")
    liquidity = tech.get("daily_turnover_inr")
    if liquidity is None or liquidity < config.min_daily_turnover_inr or (tech.get("zero_volume_fraction") or 0) > .1:
        warnings.append("Liquidity is insufficient or cannot be verified.")

    fund = {}
    categories["FUNDAMENTAL"] = "UNAVAILABLE"
    if fundamental:
        fs = fundamental.source
        if fs.available_at > as_of or (fs.published_at and fs.published_at > as_of):
            warnings.append("Fundamental data published or first observed after the analysis timestamp was excluded.")
        else:
            age = (as_of - fs.retrieved_at).total_seconds() / 86400
            period_age = 9999
            try:
                # Fiscal period is used ONLY to assess staleness, never as a publication date.
                end = pd.Timestamp(fs.data_period) + pd.offsets.MonthEnd(0)
                period_age = (pd.Timestamp(as_of).tz_localize(None) - end).days
            except (ValueError, TypeError):
                pass
            categories["FUNDAMENTAL"] = "FRESH" if age <= 7 and 0 <= period_age <= 180 else "STALE"
            fund = dict(fundamental.metrics)
            add("FUNDAMENTAL", fs, categories["FUNDAMENTAL"])
            if not fs.published_at:
                warnings.append("Filing publication dates are unavailable; fundamentals are known only from their retrieval time.")
            if fundamental.unavailable:
                warnings.append("Unavailable fundamental fields: " + ", ".join(fundamental.unavailable) + ".")
    if not fund:
        warnings.append("Fundamental data is unavailable; confidence is reduced.")
    elif categories["FUNDAMENTAL"] == "STALE":
        warnings.append("Fundamental statements are stale or their reporting period is unknown.")

    events = []
    categories["NEWS"] = "UNAVAILABLE"
    if news and news.source.available_at <= as_of:
        news_age = (as_of - news.source.retrieved_at).total_seconds() / 3600
        categories["NEWS"] = "FRESH" if news_age <= 24 else "STALE"
        add("NEWS", news.source, categories["NEWS"])
        warnings.extend(news.warnings)
        for event in news.events:
            s = event.source
            if s.published_at and s.available_at <= as_of and 0 <= (as_of - s.published_at).total_seconds() <= 60 * 86400:
                events.append(event)
                add("NEWS", s, "FRESH")
    if not events:
        warnings.append("No recent material company news was available for scoring.")

    context_metrics = {}
    for key, record in (("MARKET", benchmark), ("SECTOR", sector)):
        rf = market_frame(record)
        if not rf.empty:
            rf = rf[rf.index <= cutoff]
        fresh = not rf.empty and business_age(rf.index[-1].to_pydatetime(), as_of) <= config.fresh_business_days
        synchronized = fresh and market_time is not None and rf.index[-1] == market_time
        categories[key] = "FRESH" if synchronized else "STALE" if not rf.empty else "UNAVAILABLE"
        if record:
            add(key, record.source, categories[key])
        context_metrics[key] = indicators(rf, as_of=cutoff) if synchronized else {}
        if not synchronized:
            warnings.append(f"{key.title()} benchmark is unavailable or not synchronized with the stock.")

    macro_values = {}
    categories["MACRO"] = "UNAVAILABLE"
    if macro:
        # Each adapter supplies a small coherent snapshot; stale components are excluded.
        for source in macro.sources:
            age_limit = 90 if source.name != "Yahoo Finance" else 5
            age = (as_of - (source.published_at or source.retrieved_at)).total_seconds() / 86400
            fresh = source.available_at <= as_of and 0 <= age <= age_limit
            add("MACRO", source, "FRESH" if fresh else "STALE")
        valid_ids = {s.id for s in sources.values() if s.freshness == "FRESH" and s.id in source_ids.get("MACRO", [])}
        for key, value in macro.metrics.items():
            sid = macro.metric_sources.get(key)
            if sid in valid_ids:
                macro_values[key] = value
        categories["MACRO"] = "FRESH" if macro_values else "UNAVAILABLE"
        warnings.extend("Macro data unavailable: " + item + "." for item in macro.unavailable)

    quality = 0.0
    quality += 40 * (1 if history >= config.full_history else .7 if history >= config.min_history else .25)
    quality *= 1 if categories["TECHNICAL"] == "FRESH" else .4 if market_time else 0
    required_fund = ("sales_growth_1y", "profit_growth_1y", "roe", "pe", "pb")
    coverage = sum(fund.get(k) is not None for k in required_fund) / len(required_fund)
    quality += 25 * coverage * (1 if categories["FUNDAMENTAL"] == "FRESH" else .3)
    quality += 10 * (1 if categories["NEWS"] == "FRESH" else .25 if news else 0)
    quality += 10 * (categories["SECTOR"] == "FRESH")
    quality += 5 * (categories["MARKET"] == "FRESH")
    quality += 10 * min(len(macro_values) / 5, 1)
    if liquidity is None or liquidity < config.min_daily_turnover_inr:
        quality -= 10
    if history < config.min_history or not market_time:
        status = "INSUFFICIENT"
    elif categories["TECHNICAL"] == "STALE":
        status = "STALE"
    else:
        status = "GOOD" if quality >= 95 and not warnings else "PARTIAL"
    event_risk = max((e.relevance * e.credibility for e in events if e.upcoming and
                      (as_of - e.source.published_at).days <= 14), default=0.0)
    warnings.append("A complete upcoming earnings/event calendar is unavailable; unscheduled events can invalidate the outlook.")
    return FeatureSnapshot(
        symbol=symbol, exchange=exchange, company_name=fundamental.company_name if fundamental else symbol,
        analysis_timestamp=as_of, market_timestamp=market_time,
        industry=fundamental.industry if fundamental else "", sector=fundamental.sector if fundamental else "",
        is_financial=fundamental.is_financial if fundamental else False,
        technical=tech, fundamental=fund, benchmark=context_metrics["MARKET"], sector_metrics=context_metrics["SECTOR"],
        macro=macro_values, events=events, source_ids=source_ids, event_risk=event_risk,
        data_quality=DataQuality(status=status, score=round(max(0, min(100, quality)), 1),
                                 categories=categories, warnings=sorted(set(warnings)), sources=list(sources.values())),
    )
