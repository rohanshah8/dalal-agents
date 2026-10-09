"""Transparent heuristic baseline. No LLM, fitted performance claims, or trading actions."""
from __future__ import annotations

import math
from statistics import NormalDist, mean, median
from typing import Protocol

from .config import MODEL_VERSION, ForecastConfig
from .features import business_age
from .models import FeatureSnapshot, HorizonOutlook, PriceRange, Reason


class ForecastEngine(Protocol):
    def forecast(self, snapshot: FeatureSnapshot) -> list[HorizonOutlook]: ...


def bounded(value: float, scale: float = 1) -> float:
    return max(-1., min(1., value / scale))


def average(values) -> float | None:
    values = [v for v in values if v is not None and math.isfinite(v)]
    return mean(values) if values else None


def signal(metrics: dict, key: str, scale: float, center: float = 0) -> float | None:
    value = metrics.get(key)
    return bounded(value - center, scale) if value is not None else None


def apply_peer_valuations(snapshots: list[FeatureSnapshot]) -> None:
    for target in snapshots:
        comparable = [s for s in snapshots if s.symbol != target.symbol and s.industry and
                      s.industry == target.industry and s.is_financial == target.is_financial and
                      s.data_quality.categories.get("FUNDAMENTAL") == "FRESH"]
        for key in ("pe", "pb", "mcap_to_sales"):
            values = [s.fundamental[key] for s in comparable if (s.fundamental.get(key) or 0) > 0]
            if len(values) >= 2:
                target.fundamental[f"peer_median_{key}"] = median(values)
        for peer in comparable:
            for source in peer.data_quality.sources:
                if source.id in peer.source_ids.get("FUNDAMENTAL", []):
                    if source.id not in {s.id for s in target.data_quality.sources}:
                        target.data_quality.sources.append(source)
                    if source.id not in target.source_ids.setdefault("FUNDAMENTAL", []):
                        target.source_ids["FUNDAMENTAL"].append(source.id)


def factor_scores(s: FeatureSnapshot, days: int) -> dict[str, float | None]:
    t, f = s.technical, s.fundamental
    ret_days = 5 if days == 5 else 21 if days == 21 else 63
    vol = max(t.get("volatility_21d") or 0, t.get("volatility_63d") or 0, 8.)
    noise = max(vol / math.sqrt(252) * math.sqrt(ret_days), 1)
    trend = average([signal(t, "distance_sma_20" if days == 5 else "distance_sma_50", noise),
                     signal(t, "distance_sma_200", noise * 2)])
    momentum = average([signal(t, f"return_{ret_days}d", noise * 2), signal(t, f"relative_market_{ret_days}d", noise)])
    # Correlated price measures share a single capped group. RSI only tempers extremes.
    technical = average([trend, momentum])
    if technical is not None and t.get("rsi") is not None:
        if t["rsi"] > 80 and technical > 0 or t["rsi"] < 20 and technical < 0:
            technical *= .75
    growth = average([signal(f, "q_sales_yoy", 30), signal(f, "profit_growth_1y", 40), signal(f, "eps_growth_1y", 40)])
    quality = average([signal(f, "roe", 20, 12), signal(f, "roa", 2, 1) if s.is_financial else signal(f, "roce", 20, 12)])
    margins = average([signal(f, "operating_margin_change", 5), signal(f, "net_margin_change", 5)])
    revisions = average([signal(f, "earnings_surprise", 20), signal(f, "guidance_change", 20)])
    balance = signal(f, "equity_to_assets", 8, 8) if s.is_financial else average([
        -signal(f, "debt_to_equity", 1.5, .5) if f.get("debt_to_equity") is not None else None,
        signal(f, "fcf_margin", 15)])
    fundamental = average([growth, quality, margins, balance, revisions])
    valuations = []
    for key in (("pb", "pe") if s.is_financial else ("pe", "mcap_to_sales", "pb")):
        own, peer = f.get(key), f.get(f"peer_median_{key}")
        if own and peer and own > 0 and peer > 0:
            valuations.append(bounded(math.log(peer / own), math.log(2)))
    valuation = average(valuations)
    scores, weights = [], []
    for event in s.events:
        if event.relevance < .5:
            continue
        age = max(0, (s.analysis_timestamp - event.source.published_at).total_seconds() / 86400)
        weight = event.relevance * event.novelty * event.credibility * math.exp(-age / event.impact_days)
        weight *= min(1, event.impact_days / days)
        scores.append(event.sentiment * weight)
        weights.append(weight)
    news = sum(scores) / max(sum(weights), 1.) if scores else None
    market = average([signal(s.benchmark, f"return_{ret_days}d", noise * 2), signal(s.benchmark, "distance_sma_200", 15)])
    sector = average([signal(s.sector_metrics, f"return_{ret_days}d", noise * 2), signal(t, f"relative_sector_{ret_days}d", noise)])
    # Conservative India-wide risk conditions; commodity/FX effects vary by business.
    macro = average([-signal(s.macro, "usd_inr_return_21d", 5) if s.macro.get("usd_inr_return_21d") is not None else None,
                     -signal(s.macro, "brent_return_21d", 20) if s.macro.get("brent_return_21d") is not None else None,
                     -signal(s.macro, "india_vix", 20, 18) if s.macro.get("india_vix") is not None else None,
                     -signal(s.macro, "policy_rate_change_pp", 1) if s.macro.get("policy_rate_change_pp") is not None else None,
                     -signal(s.macro, "inflation_change_pp", 2) if s.macro.get("inflation_change_pp") is not None else None])
    result = dict(technical=technical, fundamental=fundamental, valuation=valuation, news=news,
                  market=market, sector=sector, macro=macro)
    # Stale optional inputs cannot silently contribute at full strength.
    for key, category in (("fundamental", "FUNDAMENTAL"), ("valuation", "FUNDAMENTAL"), ("news", "NEWS")):
        if s.data_quality.categories.get(category) != "FRESH":
            result[key] = None
    return result


def classify(expected_return_percent: float, neutral_band_percent: float) -> str:
    if expected_return_percent > neutral_band_percent:
        return "BULLISH"
    if expected_return_percent < -neutral_band_percent:
        return "BEARISH"
    return "NEUTRAL"


def reasons(s: FeatureSnapshot, factors: dict, days: int) -> list[Reason]:
    def fmt(value, suffix="%"):
        return "unavailable" if value is None else f"{value:+.1f}{suffix}"

    def reason(category, text, value):
        return Reason(category=category, summary=text,
                      impact="POSITIVE" if value is not None and value > .1 else "NEGATIVE" if value is not None and value < -.1 else "NEUTRAL",
                      source_ids=s.source_ids.get(category, [])[:10])

    t, f = s.technical, s.fundamental
    result = [reason("TECHNICAL", f"{days}-session return {fmt(t.get(f'return_{days}d'))}; distance from the 50-session average {fmt(t.get('distance_sma_50'))}. Correlated trend signals are grouped.", factors.get("technical")),
              reason("FUNDAMENTAL", f"Revenue growth {fmt(f.get('sales_growth_1y'))}; profit growth {fmt(f.get('profit_growth_1y'))}; ROE {fmt(f.get('roe'))}. " +
                     ("Current peer multiples inform valuation." if factors.get("valuation") is not None else "Comparable valuation evidence is limited."), factors.get("fundamental"))]
    material = [e for e in s.events if e.relevance >= .5]
    if material:
        event = max(material, key=lambda e: e.relevance * e.novelty * e.credibility * math.exp(
            -(s.analysis_timestamp - e.source.published_at).total_seconds() / (86400 * e.impact_days)))
        r = reason("NEWS", f"Recent {event.event_type.lower().replace('_', ' ')} coverage: {event.title}", factors.get("news"))
        r.source_ids = [event.source.id]
    else:
        r = reason("NEWS", "Recent material news evidence is unavailable; the model does not assume a positive catalyst.", None)
    result.append(r)
    if s.sector_metrics:
        result.append(reason("SECTOR", f"Sector benchmark {days}-session return {fmt(s.sector_metrics.get(f'return_{days}d'))}; the stock's relative sector return is {fmt(t.get(f'relative_sector_{days}d'))}.", factors.get("sector")))
    else:
        result.append(reason("MACRO", "The model uses available broad-market, currency, commodity and volatility evidence; unavailable releases reduce confidence. Sector-specific sensitivity may differ.", factors.get("macro")))
    return result


class HeuristicForecastEngine:
    def __init__(self, config: ForecastConfig | None = None):
        self.config = config or ForecastConfig()

    def forecast(self, s: FeatureSnapshot) -> list[HorizonOutlook]:
        c, t = self.config, s.technical
        out = []
        price = t.get("current_price")
        history = t.get("history_days") or 0
        stale_age = business_age(s.market_timestamp, s.analysis_timestamp) if s.market_timestamp else 9999
        for horizon, h in c.horizons.items():
            factors = factor_scores(s, h.days)
            explanations = reasons(s, factors, h.days)
            risks = ["Unscheduled earnings, guidance or regulatory developments can invalidate this outlook.",
                     "This heuristic has no measured out-of-sample calibration; confidence is a model score, not a validated probability."]
            base = dict(horizon=horizon, trading_days=h.days, current_price=price, reasoning=explanations,
                        model_version=MODEL_VERSION, generated_at=s.analysis_timestamp)
            if not price or history < max(c.min_history, h.days + 1) or stale_age > c.max_stale_business_days:
                out.append(HorizonOutlook(**base, status="UNAVAILABLE", confidence_score=0,
                                          risks=["Insufficient or excessively stale adjusted price history."] + risks))
                continue
            contributions = {k: (v or 0) * h.weights[k] for k, v in factors.items()}
            score = sum(contributions.values())
            annual = max(t.get("volatility_21d") or 0, t.get("volatility_63d") or 0, t.get("volatility_252d") or 0)
            if annual > 300 or abs(t.get("return_1d") or 0) > 80:
                out.append(HorizonOutlook(**base, status="UNAVAILABLE", confidence_score=0,
                                          risks=["Extreme price behavior is outside the supported heuristic range; check corporate actions and source data."] + risks))
                continue
            daily = max(annual / 100 / math.sqrt(252), c.daily_volatility_floor)
            horizon_vol = daily * math.sqrt(h.days)
            quality = s.data_quality.score / 100
            missing = sum(h.weights[k] for k, v in factors.items() if v is None)
            uncertainty = horizon_vol * (1.1 + .5 * (1 - quality) + .4 * missing + .35 * s.event_risk)
            mu = score * horizon_vol * h.signal_scale
            expected = math.expm1(mu) * 100
            band = c.transaction_cost_percent + c.neutral_sigma * uncertainty * 100
            movement = classify(expected, band)
            normal = NormalDist(mu=mu, sigma=uncertainty)
            upper_boundary, lower_boundary = math.log1p(band / 100), math.log(max(.01, 1 - band / 100))
            directional = (1 - normal.cdf(upper_boundary) if movement == "BULLISH" else
                           normal.cdf(lower_boundary) if movement == "BEARISH" else
                           normal.cdf(upper_boundary) - normal.cdf(lower_boundary))
            total = sum(abs(v) for v in contributions.values())
            agreement = abs(score) / total if total else 0
            confidence = 100 * directional * c.heuristic_reliability * quality * (.75 + .25 * agreement) * (1 - .35 * missing)
            if s.event_risk:
                confidence *= 1 - .4 * s.event_risk
                risks.insert(0, "Recent coverage indicates an upcoming earnings or guidance event; gap risk is elevated.")
            if not s.event_calendar_known:
                confidence *= .95
            tech, fund = factors.get("technical"), factors.get("fundamental")
            if tech is not None and fund is not None and tech * fund < -.15:
                confidence *= .7
                risks.insert(0, "Technical and fundamental signals conflict.")
            if stale_age > c.fresh_business_days:
                confidence *= .5
                risks.insert(0, "Prices are stale; market conditions may have changed.")
            if (t.get("daily_turnover_inr") or 0) < c.min_daily_turnover_inr or (t.get("zero_volume_fraction") or 0) > .1:
                confidence *= .6
                risks.insert(0, "Low or unknown liquidity increases slippage and gap risk.")
            if annual > 60 or abs(t.get("return_1d") or 0) > 15:
                confidence *= .65
                risks.insert(0, "Volatility is outside the baseline's normal operating range.")
            cap = min(c.confidence_cap, 45 if history < c.full_history else c.confidence_cap)
            prices = PriceRange(lower=price * math.exp(mu - c.range_z * uncertainty), median=price * math.exp(mu),
                                upper=price * math.exp(mu + c.range_z * uncertainty))
            out.append(HorizonOutlook(**base, movement=movement, confidence_score=round(max(0, min(cap, confidence)), 2),
                                      expected_return_percent=round(expected, 4), price_range=prices, risks=risks,
                                      factor_contributions={k: round(v, 6) for k, v in contributions.items()},
                                      uncertainty_percent=round(uncertainty * 100, 4), neutral_band_percent=round(band, 4)))
        return out
