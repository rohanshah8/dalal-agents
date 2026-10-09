"""Strict same-horizon, same-snapshot qualification and bounded risk-adjusted ranking."""
from __future__ import annotations

from .config import ForecastConfig
from .engine import factor_scores
from .models import Alternative, ComparisonMetrics, FeatureSnapshot, HorizonOutlook


def ratio_matches(a, b, maximum: float) -> bool:
    return a is not None and b is not None and a > 0 and b > 0 and 1 / maximum <= a / b <= maximum


def comparable(target: FeatureSnapshot, candidate: FeatureSnapshot, c: ForecastConfig) -> tuple[bool, str]:
    if target.symbol == candidate.symbol:
        return False, "selected stock"
    if target.analysis_timestamp != candidate.analysis_timestamp or target.market_timestamp != candidate.market_timestamp:
        return False, "unsynchronized snapshot"
    if target.currency != candidate.currency or not target.industry or target.industry != candidate.industry or target.is_financial != candidate.is_financial:
        return False, "industry or business model mismatch"
    if candidate.data_quality.status in ("STALE", "INSUFFICIENT") or candidate.data_quality.score < c.alternative_quality_min:
        return False, "data quality"
    if target.data_quality.status in ("STALE", "INSUFFICIENT"):
        return False, "selected stock data is not current"
    if any(candidate.data_quality.categories.get(k) != "FRESH" for k in ("TECHNICAL", "FUNDAMENTAL", "NEWS")):
        return False, "important category unavailable or stale"
    if (candidate.technical.get("history_days") or 0) < c.full_history:
        return False, "insufficient history"
    if not ratio_matches(target.fundamental.get("market_cap"), candidate.fundamental.get("market_cap"), c.max_market_cap_ratio):
        return False, "market capitalization mismatch"
    if not ratio_matches(target.technical.get("volatility_63d"), candidate.technical.get("volatility_63d"), c.max_volatility_ratio):
        return False, "volatility mismatch"
    turnover = candidate.technical.get("daily_turnover_inr")
    if turnover is None or turnover < c.min_daily_turnover_inr or (candidate.technical.get("zero_volume_fraction") or 0) > .1:
        return False, "insufficient liquidity"
    if not ratio_matches(target.technical.get("daily_turnover_inr"), turnover, c.max_liquidity_ratio):
        return False, "liquidity mismatch"
    return True, "qualifies"


def rank_score(snapshot: FeatureSnapshot, outlook: HorizonOutlook, config: ForecastConfig) -> float:
    factors = factor_scores(snapshot, outlook.trading_days)
    # All components have bounded scales; no raw currency/market-cap values are added to scores.
    def unit(value):
        return max(0, min(1, value))
    components = {
        "return": unit(.5 + outlook.expected_return_percent / max(2 * outlook.uncertainty_percent, .1)),
        "confidence": outlook.confidence_score / 100,
        "fundamental": ((factors.get("fundamental") or 0) + 1) / 2,
        "technical": ((factors.get("technical") or 0) + 1) / 2,
        "sector": ((factors.get("sector") or 0) + 1) / 2,
        "quality": snapshot.data_quality.score / 100,
        "volatility": unit((snapshot.technical.get("volatility_63d") or 100) / 100),
        "liquidity": unit(config.min_daily_turnover_inr / max(snapshot.technical.get("daily_turnover_inr") or 1, 1)),
        "event": snapshot.event_risk,
    }
    return round(sum(config.ranking_weights[k] * v for k, v in components.items()) * 100, 6)


def discover(target: FeatureSnapshot, original: list[HorizonOutlook],
             candidates: list[tuple[FeatureSnapshot, list[HorizonOutlook]]], config: ForecastConfig,
             limit: int = 3) -> tuple[list[Alternative], list[dict]]:
    result, audit = [], []
    candidates = list({s.symbol: (s, forecasts) for s, forecasts in candidates}.values())
    for selected in original:
        ranked = []
        for candidate, forecasts in candidates:
            allowed, why = comparable(target, candidate, config)
            same = next((o for o in forecasts if o.horizon == selected.horizon), None)
            if allowed and (selected.status != "AVAILABLE" or same is None or same.status != "AVAILABLE"):
                allowed, why = False, "forecast unavailable"
            if allowed and (same.model_version != selected.model_version or same.generated_at != selected.generated_at):
                allowed, why = False, "forecast methodology or generation timestamp mismatch"
            if allowed and (same.expected_return_percent <= selected.expected_return_percent or same.confidence_score <= selected.confidence_score):
                allowed, why = False, "return and confidence must both be higher"
            score = rank_score(candidate, same, config) if allowed else None
            audit.append({"symbol": candidate.symbol, "horizon": selected.horizon, "qualified": allowed,
                          "reason": why, "ranking_score": score})
            if allowed:
                ranked.append((score, candidate.symbol, candidate, same))
        for score, _, candidate, same in sorted(ranked, key=lambda row: (-row[0], row[1]))[:max(0, min(limit, 3))]:
            difference = same.expected_return_percent - selected.expected_return_percent
            confidence_difference = same.confidence_score - selected.confidence_score
            result.append(Alternative(
                symbol=candidate.symbol, company_name=candidate.company_name, exchange=candidate.exchange,
                currency=candidate.currency, comparison_horizon=selected.horizon, movement=same.movement,
                confidence_score=same.confidence_score, current_price=same.current_price,
                expected_return_percent=same.expected_return_percent, price_range=same.price_range,
                reasons=same.reasoning[:3], key_risk=same.risks[0],
                why_ranked_higher=f"Higher forecasted return by {difference:.2f} percentage points and higher model confidence by {confidence_difference:.2f} points, with comparable industry, size, liquidity and volatility.",
                comparison_metrics=ComparisonMetrics(return_difference_percent=difference,
                                                     confidence_difference=confidence_difference, risk_adjusted_score=score),
                data_quality=candidate.data_quality, market_timestamp=candidate.market_timestamp,
                analysis_timestamp=candidate.analysis_timestamp))
    return result, audit
