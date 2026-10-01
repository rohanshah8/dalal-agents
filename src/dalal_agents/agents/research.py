"""Research agents: market, fundamentals, ownership, filings."""
from __future__ import annotations

from ..analytics import compute_fundamentals, compute_technicals, compute_valuation, shareholding_trends
from ..models import Source
from .base import Agent


class MarketAgent(Agent):
    name = "market"
    description = "Price trend, momentum, risk and relative strength vs NIFTY 50"

    def execute(self) -> None:
        ys = self.ctx.yahoo_symbols.get(self.symbol)
        if not ys:
            raise LookupError("no Yahoo Finance ticker resolved")
        prices = self.ctx.yahoo.history(ys, self.ctx.settings.price_history)
        t = compute_technicals(prices, self.ctx.benchmark)
        src = Source(provider="yahoo-finance", url=f"https://finance.yahoo.com/quote/{ys}",
                     title=f"Yahoo Finance — {ys} daily prices")
        self.finding.data = t
        period = t.get("price_date")
        F = self.fact
        F("price", "Last close", t.get("price"), src, "₹", period)
        for k, lbl in [("ret_1m", "1-month return"), ("ret_3m", "3-month return"), ("ret_6m", "6-month return"),
                       ("ret_1y", "1-year return"), ("cagr_3y", "3-year price CAGR"), ("cagr_5y", "5-year price CAGR"),
                       ("rel_ret_1y", "1-year return relative to NIFTY 50"),
                       ("rel_ret_6m", "6-month return relative to NIFTY 50"),
                       ("bench_ret_1y", "NIFTY 50 1-year return"),
                       ("pct_vs_sma200", "Price vs 200-day moving average"),
                       ("pct_from_52w_high", "Distance from 52-week high"),
                       ("volatility_1y", "Annualised volatility (1y)"), ("max_drawdown_1y", "Max drawdown (1y)"),
                       ("max_drawdown_3y", "Max drawdown (3y)")]:
            F(k, lbl, t.get(k), src, "%", period)
        F("sma50", "50-day moving average", t.get("sma50"), src, "₹", period)
        F("sma200", "200-day moving average", t.get("sma200"), src, "₹", period)
        F("high_52w", "52-week high", t.get("high_52w"), src, "₹", period)
        F("low_52w", "52-week low", t.get("low_52w"), src, "₹", period)
        F("rsi14", "RSI (14)", t.get("rsi14"), src, "", period)
        F("beta_1y", "Beta vs NIFTY 50 (1y)", t.get("beta_1y"), src, "", period)
        F("trend", "Trend state (SMA50/SMA200)", t.get("trend"), src, "", period)
        F("macd_hist", "MACD histogram", t.get("macd_hist"), src, "", period)
        self.finding.summary = (f"{t.get('trend', 'n/a')}; 1Y {t.get('ret_1y') or 0:+.1f}% vs NIFTY "
                                f"{t.get('bench_ret_1y') or 0:+.1f}%")


FUND_LABELS = {
    "sales_cagr_3y": ("Sales/revenue CAGR 3y", "%"), "sales_cagr_5y": ("Sales/revenue CAGR 5y", "%"),
    "sales_cagr_10y": ("Sales/revenue CAGR 10y", "%"), "profit_cagr_3y": ("Net profit CAGR 3y", "%"),
    "profit_cagr_5y": ("Net profit CAGR 5y", "%"), "profit_cagr_10y": ("Net profit CAGR 10y", "%"),
    "eps_cagr_5y": ("EPS CAGR 5y", "%"), "sales_growth_1y": ("Sales growth (latest FY)", "%"),
    "profit_growth_1y": ("Net profit growth (latest FY)", "%"),
    "sales": ("Sales/revenue (latest FY)", "₹ cr"), "net_profit": ("Net profit (latest FY)", "₹ cr"),
    "sales_ttm": ("Sales/revenue (TTM)", "₹ cr"), "net_profit_ttm": ("Net profit (TTM)", "₹ cr"),
    "opm": ("Operating margin (latest FY)", "%"), "npm": ("Net profit margin (latest FY)", "%"),
    "opm_5y_avg": ("Operating margin 5y average", "%"), "opm_stdev_5y": ("Operating margin std-dev 5y", "pp"),
    "roe": ("Return on equity (latest FY)", "%"), "roe_5y_avg": ("ROE 5y average", "%"),
    "roce": ("Return on capital employed (latest FY)", "%"), "roce_5y_avg": ("ROCE 5y average", "%"),
    "roa": ("Return on assets (latest FY)", "%"),
    "debt_to_equity": ("Debt to equity", "x"), "interest_coverage": ("Interest coverage (EBIT/interest)", "x"),
    "borrowings": ("Borrowings", "₹ cr"), "equity": ("Shareholders' equity", "₹ cr"),
    "asset_turnover": ("Asset turnover", "x"), "equity_multiplier": ("Equity multiplier", "x"),
    "cfo": ("Cash from operations (latest FY)", "₹ cr"), "fcf": ("Free cash flow (latest FY)", "₹ cr"),
    "cfo_to_np_5y": ("Cash conversion: CFO / net profit (5y)", "x"), "fcf_margin_5y": ("FCF margin (5y)", "%"),
    "debtor_days": ("Debtor days", "days"), "debtor_days_5y_ago": ("Debtor days 5y ago", "days"),
    "inventory_days": ("Inventory days", "days"), "ccc": ("Cash conversion cycle", "days"),
    "wc_days": ("Working capital days", "days"), "wc_days_5y_ago": ("Working capital days 5y ago", "days"),
    "q_sales_yoy": ("Latest quarter sales YoY", "%"), "q_profit_yoy": ("Latest quarter net profit YoY", "%"),
    "q_sales_yoy_prev": ("Previous quarter sales YoY", "%"), "q_opm": ("Latest quarter operating margin", "%"),
    "q_opm_4q_ago": ("Operating margin same quarter last year", "%"),
    "q_sales": ("Latest quarter sales", "₹ cr"), "q_net_profit": ("Latest quarter net profit", "₹ cr"),
    "deposits": ("Deposits", "₹ cr"), "deposit_growth_1y": ("Deposit growth (latest FY)", "%"),
    "deposit_cagr_5y": ("Deposit CAGR 5y", "%"), "equity_to_assets": ("Equity / total assets", "%"),
    "leverage": ("Assets / equity", "x"), "gnpa": ("Gross NPA (latest quarter)", "%"),
    "nnpa": ("Net NPA (latest quarter)", "%"), "gnpa_4q_ago": ("Gross NPA a year ago", "%"),
    "dividend_payout": ("Dividend payout (latest FY)", "%"), "other_income_share": ("Other income / PBT", "%"),
    "piotroski": ("Piotroski F-score (adapted)", ""),
}
VAL_LABELS = {
    "market_cap": ("Market capitalisation", "₹ cr"), "pe": ("Price / earnings (TTM)", "x"),
    "pb": ("Price / book", "x"), "dividend_yield": ("Dividend yield", "%"),
    "earnings_yield": ("Earnings yield", "%"), "mcap_to_sales": ("Market cap / sales", "x"),
    "peg": ("PEG (P/E ÷ 5y EPS CAGR)", "x"),
    "implied_growth_10y": ("Market-implied 10y growth (reverse DCF)", "%"),
    "implied_minus_hist_growth": ("Implied growth minus 5y profit CAGR", "pp"),
    "cost_of_equity": ("Cost of equity assumed (reverse DCF)", "%"),
    "terminal_growth": ("Terminal growth assumed (reverse DCF)", "%"),
}


class FundamentalsAgent(Agent):
    name = "fundamentals"
    description = "Growth, margins, returns, balance sheet, cash quality, valuation"

    def execute(self) -> None:
        d = self.data()
        m = compute_fundamentals(d)
        v = compute_valuation(d.profile.top_ratios, m, {}, self.ctx.settings.cost_of_equity,
                              self.ctx.settings.terminal_growth)
        src = d.source or Source(provider="screener.in")
        if d.profile.about:
            self.excerpt(d.profile.about, src, kind="profile")
        for pro in d.profile.pros:
            self.excerpt(f"Screener highlight: {pro}", src, kind="profile")
        for con in d.profile.cons:
            self.excerpt(f"Screener concern: {con}", src, kind="profile")
        fy, q = m.get("latest_fy"), m.get("latest_quarter")
        for key, (label, unit) in FUND_LABELS.items():
            period = q if key.startswith("q_") or key in ("gnpa", "nnpa") else (
                "TTM" if key.endswith("_ttm") else fy)
            if key == "piotroski" and m.get("piotroski") is not None:
                self.fact(key, f"{label} (out of {m['piotroski_max']})", m["piotroski"], src, "", fy)
                continue
            self.fact(key, label, m.get(key), src, unit, period)
        for key, (label, unit) in VAL_LABELS.items():
            self.fact(key, label, v.get(key), src, unit, "current")
        gr = d.profile.growth_ranges
        for group, vals in gr.items():
            for span, val in vals.items():
                if group.startswith("Stock Price"):
                    continue
                self.fact(f"screener_{group}_{span}".replace(" ", "_").lower(),
                          f"{group} ({span}, as published by Screener)", val, src, "%", span)
        m["valuation"] = v
        m["profile"] = {"pros": d.profile.pros, "cons": d.profile.cons}
        self.finding.data = {k: val for k, val in m.items() if k != "piotroski_detail"} | {
            "piotroski_detail": m.get("piotroski_detail"), "valuation": v}
        self.finding.summary = (
            f"5y sales CAGR {_f(m.get('sales_cagr_5y'))}, ROE {_f(m.get('roe'))}, "
            f"P/E {_f(v.get('pe'), '')}")


class OwnershipAgent(Agent):
    name = "ownership"
    description = "Promoter / FII / DII / public shareholding trends"

    def execute(self) -> None:
        d = self.data()
        s = shareholding_trends(d)
        if not s:
            raise LookupError("no shareholding table")
        src = Source(provider="screener.in", url=(d.profile.screener_url or "") + "#shareholding",
                     title=f"Screener.in — {d.profile.name} shareholding pattern")
        p = s.get("latest_period")
        labels = {"promoter": "Promoter holding", "fii": "FII holding", "dii": "DII holding",
                  "public": "Public (retail) holding", "government": "Government holding"}
        for k, lbl in labels.items():
            self.fact(f"{k}_latest", lbl, s.get(f"{k}_latest"), src, "%", p)
            self.fact(f"{k}_chg_1y", f"{lbl} change over 1 year", s.get(f"{k}_chg_1y"), src, "pp", p)
            self.fact(f"{k}_chg_q", f"{lbl} change over last quarter", s.get(f"{k}_chg_q"), src, "pp", p)
        self.fact("shareholders_latest", "Number of shareholders", s.get("shareholders_latest"), src, "", p)
        self.fact("shareholders_chg_1y_pct", "Shareholder count change (1y)", s.get("shareholders_chg_1y_pct"),
                  src, "%", p)
        self.finding.data = s
        self.finding.summary = ((f"Promoter {_f(s.get('promoter_latest'))}, " if s.get("promoter_latest") is not None else "No promoter group, ") + f"FII {_f(s.get('fii_latest'))}, "
                                f"DII {_f(s.get('dii_latest'))}")


class FilingsAgent(Agent):
    name = "filings"
    description = "Exchange announcements, annual reports, credit ratings"

    def execute(self) -> None:
        d = self.data()
        anns = []
        try:
            anns = self.ctx.screener.announcements(d)[:20]
        except Exception as e:
            self.finding.errors.append(f"announcements: {e}")
            self.finding.status = "partial"
        for a in anns:
            self.excerpt(a.title, Source(provider="bse/nse filing", url=a.url, title="Exchange announcement"),
                         kind="filing", date=a.date)
        docs = [x for x in d.documents if x.kind in ("annual_report", "credit_rating")]
        self.finding.data = {
            "announcements": [a.model_dump() for a in anns],
            "annual_reports": [x.model_dump() for x in docs if x.kind == "annual_report"][:5],
            "credit_ratings": [x.model_dump() for x in docs if x.kind == "credit_rating"][:5],
            "concalls": [x.model_dump() for x in d.documents if x.kind == "concall"][:8],
        }
        self.finding.summary = f"{len(anns)} recent announcements"


def _f(x, unit="%"):
    return "n/a" if x is None else f"{x:.1f}{unit}"
