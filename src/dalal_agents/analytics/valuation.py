"""Valuation analytics — descriptive multiples and market-implied growth (reverse DCF).

We deliberately never output a target price. The reverse DCF answers:
"what growth rate is the current price already pricing in?"
"""
from __future__ import annotations


def present_value(cash0: float, g: float, years: int, ke: float, g_terminal: float) -> float:
    """PV of a cash stream starting at cash0·(1+g) growing at g for `years`, then a Gordon terminal value."""
    pv, c = 0.0, cash0
    for t in range(1, years + 1):
        c *= 1 + g
        pv += c / (1 + ke) ** t
    tv = c * (1 + g_terminal) / (ke - g_terminal)
    return pv + tv / (1 + ke) ** years


def implied_growth(market_cap: float, cash0: float, ke: float = 0.12, g_terminal: float = 0.05,
                   years: int = 10, lo: float = -0.5, hi: float = 1.0) -> float | None:
    """Solve PV(g) = market_cap for g by bisection. Returns g in %, or None if outside range."""
    if not market_cap or not cash0 or cash0 <= 0 or market_cap <= 0 or ke <= g_terminal:
        return None
    f_lo = present_value(cash0, lo, years, ke, g_terminal) - market_cap
    f_hi = present_value(cash0, hi, years, ke, g_terminal) - market_cap
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = present_value(cash0, mid, years, ke, g_terminal) - market_cap
        if abs(f_mid) < 1e-6 * market_cap:
            break
        if f_lo * f_mid <= 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid * 100


def compute_valuation(top: dict, fund: dict, tech: dict, ke: float, g_terminal: float) -> dict:
    """`top` = Screener top ratios (₹ cr market cap, P/E, Book Value, Dividend Yield)."""
    v: dict = {}
    mcap = top.get("Market Cap")
    price = top.get("Current Price") or tech.get("price")
    v["market_cap"] = mcap
    v["price"] = price
    v["pe"] = top.get("Stock P/E")
    bv = top.get("Book Value")
    v["pb"] = price / bv if price and bv else None
    v["dividend_yield"] = top.get("Dividend Yield")
    v["earnings_yield"] = 100 / v["pe"] if v["pe"] and v["pe"] > 0 else None
    sales = fund.get("sales_ttm") or fund.get("sales")
    v["mcap_to_sales"] = mcap / sales if mcap and sales and not fund.get("is_financial") else None
    eps_g = fund.get("eps_cagr_5y") or fund.get("profit_cagr_5y")
    v["peg"] = v["pe"] / eps_g if v["pe"] and eps_g and eps_g > 0 and v["pe"] > 0 else None

    # Reverse DCF on earnings for financials (FCF is not meaningful), FCF otherwise,
    # falling back to net profit if FCF is negative.
    np_ttm = fund.get("net_profit_ttm") or fund.get("net_profit")
    base, base_label = None, None
    if fund.get("is_financial"):
        base, base_label = np_ttm, "net profit"
    else:
        fcf = fund.get("fcf")
        if fcf and fcf > 0 and np_ttm and fcf >= 0.4 * np_ttm:
            base, base_label = fcf, "free cash flow"
        else:
            base, base_label = np_ttm, "net profit"
    v["implied_growth_10y"] = implied_growth(mcap, base, ke, g_terminal) if mcap and base else None
    v["implied_growth_base"] = base_label
    v["cost_of_equity"] = ke * 100
    v["terminal_growth"] = g_terminal * 100
    hist = fund.get("profit_cagr_5y")
    if v["implied_growth_10y"] is not None and hist is not None:
        v["implied_minus_hist_growth"] = v["implied_growth_10y"] - hist
    return v
