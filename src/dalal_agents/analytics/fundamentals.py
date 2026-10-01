"""Deterministic fundamental analytics on Screener-style annual/quarterly tables.

Conventions: Indian FY (Apr–Mar) labelled 'Mar YYYY'; amounts in ₹ crore; consolidated by default.
Every function returns plain floats (percent values already ×100) or None when undefined.
"""
from __future__ import annotations

import math
import statistics

from ..models import CompanyData, Table


# ----------------------------------------------------------------------------- helpers
def annual_cols(t: Table) -> list[int]:
    """Indices of FY columns (exclude TTM)."""
    return [i for i, p in enumerate(t.periods) if p.upper() != "TTM"]


def series(t: Table | None, *labels: str, annual_only: bool = True) -> list[float | None]:
    if t is None:
        return []
    row = t.get(*labels)
    if row is None:
        return []
    if not annual_only:
        return list(row)
    return [row[i] if i < len(row) else None for i in annual_cols(t)]


def ttm(t: Table | None, *labels: str) -> float | None:
    if t is None or "TTM" not in [p.upper() for p in t.periods]:
        return None
    row = t.get(*labels)
    if not row:
        return None
    i = [p.upper() for p in t.periods].index("TTM")
    return row[i] if i < len(row) else None


def last(xs: list[float | None], k: int = 1) -> float | None:
    vals = [x for x in xs if x is not None]
    return vals[-k] if len(vals) >= k else None


def cagr(xs: list[float | None], years: int) -> float | None:
    """CAGR over `years` using the last value and the value `years` periods earlier.
    Undefined (None) if either end is missing or non-positive."""
    if len(xs) <= years:
        return None
    end, start = xs[-1], xs[-1 - years]
    if end is None or start is None or end <= 0 or start <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def safe_div(a, b) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def pct(a, b) -> float | None:
    v = safe_div(a, b)
    return None if v is None else v * 100


def avg2(xs: list[float | None], i: int) -> float | None:
    """Average of xs[i] and xs[i-1] (opening + closing balance); falls back to xs[i]."""
    if i < 0 or i >= len(xs) or xs[i] is None:
        return None
    if i - 1 >= 0 and xs[i - 1] is not None:
        return (xs[i] + xs[i - 1]) / 2
    return xs[i]


def yoy(xs: list[float | None], lag: int) -> float | None:
    if len(xs) <= lag or xs[-1] is None or xs[-1 - lag] in (None, 0) or xs[-1 - lag] < 0:
        return None
    return (xs[-1] / xs[-1 - lag] - 1) * 100


# ---------------------------------------------------------------------- core metrics
def compute_fundamentals(d: CompanyData) -> dict:
    """Return a flat dict of metrics. Keys are stable and used by the scorecard + report."""
    pl, bs, cf, q = d.profit_loss, d.balance_sheet, d.cash_flow, d.quarters
    fin = d.profile.is_financial
    m: dict = {"is_financial": fin}
    if pl is None:
        return m
    periods = [pl.periods[i] for i in annual_cols(pl)]
    m["latest_fy"] = periods[-1] if periods else None
    m["n_years"] = len(periods)

    sales = series(pl, "Sales", "Revenue")
    np_ = series(pl, "Net Profit", "Profit after tax")
    eps = series(pl, "EPS in Rs")
    op = series(pl, "Operating Profit", "Financing Profit")
    pbt = series(pl, "Profit before tax")
    interest = series(pl, "Interest")
    other_inc = series(pl, "Other Income")

    m["sales"] = last(sales)
    m["net_profit"] = last(np_)
    m["sales_ttm"] = ttm(pl, "Sales", "Revenue")
    m["net_profit_ttm"] = ttm(pl, "Net Profit")
    m["eps_ttm"] = ttm(pl, "EPS in Rs")
    for n in (3, 5, 10):
        m[f"sales_cagr_{n}y"] = cagr(sales, n)
        m[f"profit_cagr_{n}y"] = cagr(np_, n)
        m[f"eps_cagr_{n}y"] = cagr(eps, n)
    m["sales_growth_1y"] = yoy(sales, 1)
    m["profit_growth_1y"] = yoy(np_, 1)

    margin_series = [pct(o, s) for o, s in zip(op, sales)]
    m["opm"] = last(margin_series)  # OPM (non-fin) / financing margin (fin)
    m["npm"] = pct(last(np_), last(sales))
    valid_m = [x for x in margin_series[-5:] if x is not None]
    m["opm_stdev_5y"] = statistics.pstdev(valid_m) if len(valid_m) >= 3 else None
    m["opm_5y_avg"] = statistics.mean(valid_m) if valid_m else None
    m["other_income_share"] = pct(last(other_inc), last(pbt))

    # balance sheet
    eq_cap = series(bs, "Equity Capital")
    reserves = series(bs, "Reserves")
    equity = [(a or 0) + (b or 0) if (a is not None or b is not None) else None
              for a, b in zip(eq_cap, reserves)]
    borrow = series(bs, "Borrowings", "Borrowing")
    total_assets = series(bs, "Total Assets")
    deposits = series(bs, "Deposits")
    investments = series(bs, "Investments")
    cwip = series(bs, "CWIP")

    i_last = len(equity) - 1 if equity else -1
    # ROE uses average equity; align NP (P&L) and equity (BS) by FY label.
    bs_periods = [bs.periods[i] for i in annual_cols(bs)] if bs else []

    def aligned(xs_pl, label):
        if label not in periods:
            return None
        j = periods.index(label)
        return xs_pl[j] if j < len(xs_pl) else None

    roe_series, roa_series = [], []
    for k, label in enumerate(bs_periods):
        npk = aligned(np_, label)
        roe_series.append(pct(npk, avg2(equity, k)))
        roa_series.append(pct(npk, avg2(total_assets, k)))
    m["roe"] = last(roe_series)
    m["roe_5y_avg"] = _mean_last(roe_series, 5)
    m["roa"] = last(roa_series)
    m["equity"] = last(equity)
    m["total_assets"] = last(total_assets)
    m["book_value_cr"] = last(equity)

    if not fin:
        ebit = [None if p is None else p + (i or 0) for p, i in zip(pbt, interest)]
        roce_series = []
        for k, label in enumerate(bs_periods):
            ce_k = avg2([None if e is None else e + (b or 0) for e, b in zip(equity, borrow)], k)
            roce_series.append(pct(aligned(ebit, label), ce_k))
        m["roce"] = last(roce_series)
        m["roce_5y_avg"] = _mean_last(roce_series, 5)
        m["debt_to_equity"] = safe_div(last(borrow), last(equity))
        m["interest_coverage"] = safe_div(last(ebit), last(interest)) if last(interest) else None
        m["borrowings"] = last(borrow)
        m["cwip_to_assets"] = pct(last(cwip), last(total_assets))
        # DuPont (latest year)
        if i_last >= 0 and sales and total_assets:
            ta_avg = avg2(total_assets, len(total_assets) - 1)
            eq_avg = avg2(equity, len(equity) - 1)
            m["asset_turnover"] = safe_div(last(sales), ta_avg)
            m["equity_multiplier"] = safe_div(ta_avg, eq_avg)
    else:
        m["deposits"] = last(deposits)
        m["deposit_growth_1y"] = yoy(deposits, 1)
        m["deposit_cagr_5y"] = cagr(deposits, 5)
        m["borrowings"] = last(borrow)
        m["equity_to_assets"] = pct(last(equity), last(total_assets))
        m["investments_to_assets"] = pct(last(investments), last(total_assets))
        m["leverage"] = safe_div(last(total_assets), last(equity))

    # cash flow
    cfo = series(cf, "Cash from Operating Activity")
    cfi = series(cf, "Cash from Investing Activity")
    fcf = series(cf, "Free Cash Flow")
    m["cfo"] = last(cfo)
    m["fcf"] = last(fcf) if fcf else (None if last(cfo) is None else last(cfo) + (last(cfi) or 0))
    if cfo and np_:
        n = min(5, len(cfo), len(np_))
        s_cfo = sum(x for x in cfo[-n:] if x is not None)
        s_np = sum(x for x in np_[-n:] if x is not None)
        m["cfo_to_np_5y"] = safe_div(s_cfo, s_np) if s_np > 0 else None
    if fcf and sales:
        n = min(5, len(fcf), len(sales))
        s_fcf = sum(x for x in fcf[-n:] if x is not None)
        s_sales = sum(x for x in sales[-n:] if x is not None)
        m["fcf_margin_5y"] = pct(s_fcf, s_sales)

    # working capital (Screener ratios section)
    r = d.ratios
    for key, label in [("debtor_days", "Debtor Days"), ("inventory_days", "Inventory Days"),
                       ("payable_days", "Days Payable"), ("ccc", "Cash Conversion Cycle"),
                       ("wc_days", "Working Capital Days")]:
        s_ = series(r, label)
        m[key] = last(s_)
        m[f"{key}_5y_ago"] = s_[-6] if len(s_) >= 6 else None

    # quarterly
    if q is not None:
        qs = series(q, "Sales", "Revenue", annual_only=False)
        qn = series(q, "Net Profit", annual_only=False)
        m["latest_quarter"] = q.periods[-1] if q.periods else None
        m["q_sales_yoy"] = yoy(qs, 4)
        m["q_profit_yoy"] = yoy(qn, 4)
        prev = yoy(qs[:-1], 4) if len(qs) > 5 else None
        m["q_sales_yoy_prev"] = prev
        m["q_sales"] = last(qs)
        m["q_net_profit"] = last(qn)
        if fin:
            m["gnpa"] = last(series(q, "Gross NPA %", annual_only=False))
            m["nnpa"] = last(series(q, "Net NPA %", annual_only=False))
            g = series(q, "Gross NPA %", annual_only=False)
            m["gnpa_4q_ago"] = g[-5] if len(g) >= 5 else None
        qop = series(q, "Operating Profit", "Financing Profit", annual_only=False)
        qm = [pct(o, s) for o, s in zip(qop, qs)]
        m["q_opm"] = last(qm)
        m["q_opm_4q_ago"] = qm[-5] if len(qm) >= 5 else None

    m["piotroski"], m["piotroski_max"], m["piotroski_detail"] = piotroski(
        np_, cfo, total_assets, borrow, eq_cap, margin_series, sales, fin)

    # dividends
    payout = series(pl, "Dividend Payout %")
    m["dividend_payout"] = last(payout)
    return m


def _mean_last(xs, n):
    v = [x for x in xs[-n:] if x is not None]
    return statistics.mean(v) if v else None


def piotroski(np_, cfo, ta, borrow, eq_cap, margins, sales, fin: bool):
    """Adapted Piotroski F-score using Screener annual data. Returns (score, max, detail)."""
    detail: dict[str, bool | None] = {}

    def at(xs, k):
        return xs[k] if len(xs) >= -k and xs[k] is not None else None

    roa_t = safe_div(at(np_, -1), at(ta, -1))
    roa_p = safe_div(at(np_, -2), at(ta, -2))
    detail["Positive net profit"] = None if at(np_, -1) is None else at(np_, -1) > 0
    detail["Positive operating cash flow"] = None if at(cfo, -1) is None else at(cfo, -1) > 0
    detail["ROA improving"] = None if roa_t is None or roa_p is None else roa_t > roa_p
    detail["CFO > net profit (accrual quality)"] = (
        None if at(cfo, -1) is None or at(np_, -1) is None else at(cfo, -1) > at(np_, -1))
    if not fin:
        lev_t = safe_div(at(borrow, -1), at(ta, -1))
        lev_p = safe_div(at(borrow, -2), at(ta, -2))
        detail["Leverage falling"] = None if lev_t is None or lev_p is None else lev_t <= lev_p
    detail["No equity dilution"] = (
        None if at(eq_cap, -1) is None or at(eq_cap, -2) is None else at(eq_cap, -1) <= at(eq_cap, -2))
    detail["Margin improving"] = (
        None if at(margins, -1) is None or at(margins, -2) is None else at(margins, -1) > at(margins, -2))
    at_t = safe_div(at(sales, -1), at(ta, -1))
    at_p = safe_div(at(sales, -2), at(ta, -2))
    detail["Asset turnover improving"] = None if at_t is None or at_p is None else at_t > at_p
    known = [v for v in detail.values() if v is not None]
    if len(known) < 4:
        return None, None, detail
    return sum(1 for v in known if v), len(known), detail


# ------------------------------------------------------------------- shareholding
def shareholding_trends(d: CompanyData) -> dict:
    t = d.shareholding
    if t is None:
        return {}
    out: dict = {"latest_period": t.periods[-1] if t.periods else None, "periods": t.periods}
    for key, label in [("promoter", "Promoters"), ("fii", "FIIs"), ("dii", "DIIs"),
                       ("public", "Public"), ("government", "Government")]:
        row = t.get(label)
        if not row:
            continue
        out[f"{key}_latest"] = last(row)
        out[f"{key}_chg_1y"] = (row[-1] - row[-5]) if len(row) >= 5 and None not in (row[-1], row[-5]) else None
        out[f"{key}_chg_q"] = (row[-1] - row[-2]) if len(row) >= 2 and None not in (row[-1], row[-2]) else None
    sh = t.get("No. of Shareholders")
    if sh and len(sh) >= 5 and sh[-1] and sh[-5]:
        out["shareholders_latest"] = sh[-1]
        out["shareholders_chg_1y_pct"] = (sh[-1] / sh[-5] - 1) * 100
    return out


def is_finite(x) -> bool:
    return isinstance(x, (int, float)) and not (math.isnan(x) or math.isinf(x))
