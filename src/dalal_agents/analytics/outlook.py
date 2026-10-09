"""Adjusted OHLCV indicators for outlooks. Missing sessions are never forward-filled."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .fundamentals import compute_fundamentals, pct, series, yoy
from .technicals import macd, rsi
from .valuation import compute_valuation


def clean_prices(prices: pd.DataFrame, as_of=None) -> pd.DataFrame:
    if prices is None or prices.empty or "Close" not in prices:
        return pd.DataFrame(columns=["Close", "High", "Low", "Volume"])
    frame = prices.copy()
    frame.index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if as_of is not None:
        frame = frame[frame.index <= pd.Timestamp(as_of)]
    for column in ("Close", "High", "Low", "Volume"):
        frame[column] = pd.to_numeric(frame.get(column, np.nan), errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame = frame[frame.Close > 0]
    frame.loc[frame.Volume < 0, "Volume"] = np.nan
    invalid = (frame.High < frame.Low) | (frame.High < frame.Close) | (frame.Low > frame.Close)
    frame.loc[invalid, ["High", "Low"]] = np.nan
    return frame


def indicators(prices: pd.DataFrame, benchmark: pd.DataFrame | None = None,
               sector: pd.DataFrame | None = None, as_of=None) -> dict[str, float | None]:
    p = clean_prices(prices, as_of)
    if p.empty:
        return {"history_days": 0.0}
    close = p.Close
    price = float(close.iloc[-1])
    out = {"current_price": price, "history_days": float(len(p))}
    for days in (1, 5, 10, 21, 63):
        out[f"return_{days}d"] = float((price / close.iloc[-days - 1] - 1) * 100) if len(p) > days else None
    for days in (20, 50, 200):
        sma = float(close.iloc[-days:].mean()) if len(p) >= days else None
        out[f"sma_{days}"] = sma
        out[f"distance_sma_{days}"] = (price / sma - 1) * 100 if sma else None
    out["rsi"] = 50.0 if len(close) > 14 and close.iloc[-15:].nunique() == 1 else rsi(close)
    mc = macd(close)
    out.update(dict(zip(("macd", "macd_signal", "macd_histogram"), mc or (None, None, None))))
    previous = close.shift()
    tr = pd.concat([p.High - p.Low, (p.High - previous).abs(), (p.Low - previous).abs()], axis=1).max(axis=1)
    tr[p.High.isna() | p.Low.isna()] = np.nan
    out["atr"] = float(tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]) if tr.iloc[-14:].notna().all() and len(tr) >= 14 else None
    logr = np.log(close / close.shift()).dropna()
    for days in (21, 63, 252):
        out[f"volatility_{days}d"] = float(logr.iloc[-days:].std(ddof=1) * math.sqrt(252) * 100) if len(logr) >= days else None
    if len(close) >= 20:
        mean, sd = close.iloc[-20:].mean(), close.iloc[-20:].std(ddof=0)
        out["bollinger_position"] = float((price - (mean - 2 * sd)) / (4 * sd)) if sd > 0 else .5
    else:
        out["bollinger_position"] = None
    volumes = p.Volume.iloc[-20:]
    vavg = volumes.mean() if len(volumes) == 20 and volumes.notna().all() else None
    out["volume_ratio_20d"] = float(p.Volume.iloc[-1] / vavg) if vavg and vavg > 0 else None
    out["daily_turnover_inr"] = float((p.Close * p.Volume).iloc[-20:].mean()) if vavg is not None else None
    out["zero_volume_fraction"] = float((volumes == 0).mean()) if vavg is not None else None
    for key, column, fn in (("support_20d", "Low", "min"), ("resistance_20d", "High", "max")):
        values = p[column].iloc[-20:]
        out[key] = float(getattr(values, fn)()) if len(values) == 20 and values.notna().all() else None
    year_high, year_low = p.High.iloc[-252:], p.Low.iloc[-252:]
    out["high_52w"] = float(year_high.max()) if len(close) >= 252 and year_high.notna().all() else None
    out["low_52w"] = float(year_low.min()) if len(close) >= 252 and year_low.notna().all() else None
    for key in ("high", "low"):
        level = out[f"{key}_52w"]
        out[f"distance_52w_{key}"] = (price / level - 1) * 100 if level else None
    for name, frame in (("market", benchmark), ("sector", sector)):
        ref = clean_prices(frame, as_of)
        if ref.empty:
            continue
        # Use exactly the stock's start and end sessions; do not compare unsynchronized windows.
        for days in (5, 21, 63):
            if len(p) <= days:
                continue
            first, end = p.index[-days - 1], p.index[-1]
            if first in ref.index and end in ref.index:
                br = float((ref.loc[end, "Close"] / ref.loc[first, "Close"] - 1) * 100)
                out[f"relative_{name}_{days}d"] = out[f"return_{days}d"] - br
    return {k: (None if v is None or not math.isfinite(v) else float(v)) for k, v in out.items()}


def fundamental_features(data) -> tuple[dict[str, float | None], list[str]]:
    """Reuse audited financial maths; never fabricate unavailable vendor fields."""
    base = compute_fundamentals(data)
    val = compute_valuation(data.profile.top_ratios, base, {}, .12, .05)
    out = {k: float(v) for k, v in (base | val).items()
           if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    pl = data.profit_loss
    sales = series(pl, "Sales", "Revenue")
    op = series(pl, "Operating Profit", "Financing Profit")
    net = series(pl, "Net Profit")
    gross = series(pl, "Gross Profit")
    for key, values in (("operating", op), ("net", net), ("gross", gross)):
        margins = [pct(v, s) for v, s in zip(values, sales)]
        out[f"{key}_margin_change"] = margins[-1] - margins[-2] if len(margins) >= 2 and None not in margins[-2:] else None
    out["eps_growth_1y"] = yoy(series(pl, "EPS in Rs"), 1)
    # Equity capital changes are not share counts (face-value splits can change shares).
    shares = series(data.balance_sheet, "Shares Outstanding", "Number of Shares")
    out["share_count_change"] = yoy(shares, 1)
    exact_fcf = series(data.cash_flow, "Free Cash Flow")
    fcf = exact_fcf[-1] if exact_fcf else None
    out["fcf"] = fcf
    out["fcf_margin"] = pct(fcf, sales[-1]) if sales else None
    out["fcf_yield"] = pct(fcf, val.get("market_cap"))
    for key in ("gross_margin_change", "share_count_change", "earnings_surprise", "guidance_change",
                "ev_ebitda", "historical_pe_percentile"):
        out.setdefault(key, None)
    if data.profile.is_financial:
        for key in ("debt_to_equity", "interest_coverage", "fcf", "fcf_margin", "fcf_yield", "mcap_to_sales"):
            out[key] = None
    unavailable = [k for k in ("gross_margin_change", "share_count_change", "earnings_surprise", "guidance_change",
                               "ev_ebitda", "historical_pe_percentile", "fcf") if out.get(k) is None]
    return out, unavailable
