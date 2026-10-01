"""Price-based analytics: returns, trend, momentum, risk, relative strength vs NIFTY 50."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return (close.iloc[-1] / close.iloc[-1 - days] - 1) * 100


def rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(close) <= period:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = (100 - 100 / (1 + rs)).iloc[-1]
    if pd.isna(val):
        return 100.0 if loss.iloc[-1] == 0 else None
    return float(val)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    if len(close) < slow + signal:
        return None
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    line = ema_f - ema_s
    sig = line.ewm(span=signal, adjust=False).mean()
    return float(line.iloc[-1]), float(sig.iloc[-1]), float((line - sig).iloc[-1])


def max_drawdown(close: pd.Series) -> float | None:
    if close.empty:
        return None
    peak = close.cummax()
    return float(((close / peak) - 1).min() * 100)


def beta(stock: pd.Series, market: pd.Series, days: int = TRADING_DAYS) -> float | None:
    df = pd.concat([stock, market], axis=1, join="inner").dropna()
    if len(df) < 60:
        return None
    r = np.log(df / df.shift(1)).dropna().iloc[-days:]
    var = r.iloc[:, 1].var()
    if var == 0:
        return None
    return float(r.iloc[:, 0].cov(r.iloc[:, 1]) / var)


def compute_technicals(prices: pd.DataFrame, bench: pd.DataFrame | None = None) -> dict:
    close = prices["Close"].dropna()
    m: dict = {}
    if close.empty:
        return m
    p = float(close.iloc[-1])
    m["price"] = p
    m["price_date"] = close.index[-1].date().isoformat()
    windows = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}
    for k, d in windows.items():
        m[f"ret_{k}"] = _ret(close, d)
    if len(close) > 3 * TRADING_DAYS:
        m["cagr_3y"] = ((p / close.iloc[-1 - 3 * TRADING_DAYS]) ** (1 / 3) - 1) * 100
    if len(close) > 5 * TRADING_DAYS - 5:
        start = close.iloc[0]
        yrs = (close.index[-1] - close.index[0]).days / 365.25
        m["cagr_5y"] = ((p / start) ** (1 / yrs) - 1) * 100 if yrs > 0 else None

    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
    m["sma50"] = None if sma50 is None or pd.isna(sma50) else float(sma50)
    m["sma200"] = None if sma200 is None or pd.isna(sma200) else float(sma200)
    if m["sma200"]:
        m["pct_vs_sma200"] = (p / m["sma200"] - 1) * 100
    if m["sma50"] and m["sma200"]:
        if p > m["sma50"] > m["sma200"]:
            m["trend"] = "Uptrend"
        elif p < m["sma50"] < m["sma200"]:
            m["trend"] = "Downtrend"
        else:
            m["trend"] = "Sideways / transition"
        m["cross"] = "Golden cross (SMA50 > SMA200)" if m["sma50"] > m["sma200"] else "Death cross (SMA50 < SMA200)"

    m["rsi14"] = rsi(close)
    mc = macd(close)
    if mc:
        m["macd"], m["macd_signal"], m["macd_hist"] = mc

    last_year = close.iloc[-TRADING_DAYS:]
    m["high_52w"] = float(last_year.max())
    m["low_52w"] = float(last_year.min())
    m["pct_from_52w_high"] = (p / m["high_52w"] - 1) * 100
    m["pct_from_52w_low"] = (p / m["low_52w"] - 1) * 100

    logr = np.log(close / close.shift(1)).dropna()
    if len(logr) > 20:
        m["volatility_1y"] = float(logr.iloc[-TRADING_DAYS:].std() * np.sqrt(TRADING_DAYS) * 100)
    m["max_drawdown_1y"] = max_drawdown(last_year)
    m["max_drawdown_3y"] = max_drawdown(close.iloc[-3 * TRADING_DAYS:])

    if "Volume" in prices:
        vol = prices["Volume"].dropna()
        if len(vol) > 60:
            m["volume_20d_vs_90d"] = float(vol.iloc[-20:].mean() / max(vol.iloc[-90:].mean(), 1))

    if bench is not None and not bench.empty:
        bclose = bench["Close"].dropna()
        for k, d in windows.items():
            br = _ret(bclose, d)
            sr = m.get(f"ret_{k}")
            m[f"bench_ret_{k}"] = br
            m[f"rel_ret_{k}"] = None if br is None or sr is None else sr - br
        m["beta_1y"] = beta(close, bclose)
    return m
