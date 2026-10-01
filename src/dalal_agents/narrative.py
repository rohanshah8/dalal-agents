"""Rule-based narrative used when no LLM is configured (and as a sanity baseline).

Every sentence is generated from facts and carries its [F#] citation, so the deterministic
report satisfies the same grounding contract as the LLM report.
"""
from __future__ import annotations

from .facts import FactStore, fmt_value
from .models import Report


def deterministic_narrative(report: Report, store: FactStore) -> dict[str, str]:
    sym = report.symbol

    def f(key):
        return store.by_key(f"{sym}.{key}")

    def v(key, default="n/a"):
        x = f(key)
        return (fmt_value(x.value, x.unit) + f" [{x.id}]") if x else default

    def val(key):
        x = f(key)
        return x.value if x and isinstance(x.value, (int, float)) else None

    fund = report.findings.get("fundamentals")
    fin = bool(fund and fund.data.get("is_financial"))
    out: dict[str, str] = {}

    # executive summary
    bullets = []
    if val("sales_cagr_5y") is not None:
        bullets.append(f"- Revenue compounded at {v('sales_cagr_5y')} over five years, with net profit at "
                       f"{v('profit_cagr_5y')}.")
    if fin:
        bullets.append(f"- Return on equity is {v('roe')} and return on assets {v('roa')}; gross NPA stands at "
                       f"{v('gnpa')}.")
    elif val("roce") is not None:
        bullets.append(f"- Returns are {'high' if (val('roce') or 0) > 20 else 'moderate' if (val('roce') or 0) > 12 else 'low'}: "
                       f"ROCE {v('roce')}, ROE {v('roe')}, operating margin {v('opm')}.")
    if val("pe") is not None:
        bullets.append(f"- The stock trades at {v('pe')} earnings and {v('pb')} book; the price implies "
                       f"{v('implied_growth_10y')} annual growth for a decade versus a 5-year profit CAGR of "
                       f"{v('profit_cagr_5y')}.")
    if val("ret_1y") is not None:
        bullets.append(f"- Price trend: {v('trend')}; 1-year return {v('ret_1y')} versus NIFTY 50 at "
                       f"{v('bench_ret_1y')}.")
    sc = report.scorecard
    if sc.get("composite"):
        bullets.append(f"- Against {len(report.peers)} listed peers the composite relative score is "
                       f"{v('composite')}, rank {v('composite_rank')}"
                       + (f"; edges in {', '.join(e['dimension'] for e in sc.get('edges', []))}" if sc.get("edges") else "")
                       + (f"; lags in {', '.join(g['dimension'] for g in sc.get('gaps', []))}." if sc.get("gaps") else "."))
    if val("promoter_latest") is not None:
        bullets.append(f"- Promoters hold {v('promoter_latest')} (1-year change {v('promoter_chg_1y')}); FIIs "
                       f"{v('fii_latest')} (change {v('fii_chg_1y')}).")
    out["executive_summary"] = "\n".join(bullets)

    out["financial_performance"] = " ".join(x for x in [
        f"Latest FY revenue was {v('sales')} and net profit {v('net_profit')}." if val("sales") else "",
        f"Revenue growth: 3y {v('sales_cagr_3y')}, 5y {v('sales_cagr_5y')}, 10y {v('sales_cagr_10y')}."
        if val("sales_cagr_5y") is not None else "",
        f"The latest quarter grew sales {v('q_sales_yoy')} YoY (previous quarter {v('q_sales_yoy_prev')}) and "
        f"net profit {v('q_profit_yoy')}." if val("q_sales_yoy") is not None else "",
        (f"Operating margin is {v('opm')} against a 5-year average of {v('opm_5y_avg')} "
         f"(σ {v('opm_stdev_5y')}).") if not fin and val("opm") is not None else "",
        (f"Financing margin is {v('opm')}; ROA {v('roa')}." if fin else ""),
    ] if x)

    if fin:
        out["balance_sheet_cash"] = " ".join(x for x in [
            f"Deposits stand at {v('deposits')}, growing {v('deposit_growth_1y')} last year and {v('deposit_cagr_5y')} "
            f"over five years." if val("deposits") else "",
            f"Equity is {v('equity_to_assets')} of assets (leverage {v('leverage')})."
            if val("equity_to_assets") else "",
            f"Gross NPA is {v('gnpa')} versus {v('gnpa_4q_ago')} a year ago; net NPA {v('nnpa')}."
            if val("gnpa") is not None else "",
        ] if x)
    else:
        out["balance_sheet_cash"] = " ".join(x for x in [
            f"Debt-to-equity is {v('debt_to_equity')} with interest coverage of {v('interest_coverage')}."
            if val("debt_to_equity") is not None else "",
            f"Over five years operating cash flow was {v('cfo_to_np_5y')} of reported profit and the FCF margin "
            f"averaged {v('fcf_margin_5y')}." if val("cfo_to_np_5y") is not None else "",
            f"Working-capital days moved from {v('wc_days_5y_ago')} to {v('wc_days')}; debtor days from "
            f"{v('debtor_days_5y_ago')} to {v('debtor_days')}." if val("wc_days") is not None else "",
            f"Adapted Piotroski F-score: {v('piotroski')}." if val("piotroski") is not None else "",
        ] if x)

    out["valuation"] = " ".join(x for x in [
        f"Market capitalisation is {v('market_cap')}; P/E {v('pe')}, P/B {v('pb')}, dividend yield "
        f"{v('dividend_yield')}, PEG {v('peg')}." if val("market_cap") else "",
        (f"A reverse DCF (cost of equity {v('cost_of_equity')}, terminal growth {v('terminal_growth')}) shows "
         f"the current price implies about {v('implied_growth_10y')} annual growth for ten years, versus a "
         f"5-year profit CAGR of {v('profit_cagr_5y')} (gap {v('implied_minus_hist_growth')}).")
        if val("implied_growth_10y") is not None else "",
    ] if x)

    out["ownership"] = " ".join(x for x in [
        f"Promoter holding is {v('promoter_latest')} ({v('promoter_chg_1y')} over a year)."
        if val("promoter_latest") is not None else "",
        f"FIIs hold {v('fii_latest')} ({v('fii_chg_1y')}) and DIIs {v('dii_latest')} ({v('dii_chg_1y')})."
        if val("fii_latest") is not None else "",
        f"The shareholder base changed {v('shareholders_chg_1y_pct')} over the year."
        if val("shareholders_chg_1y_pct") is not None else "",
    ] if x)

    cc = report.findings.get("concall")
    lines = []
    if cc and cc.data.get("calls"):
        c0 = cc.data["calls"][0]
        items = (c0.get("extraction") or {}).get("forward_looking", [])[:6]
        lines.append(f"From the {c0['date']} earnings call, forward-looking statements include:")
        lines += [f"- “{it['statement'][:260]}” [{it['excerpt_id']}]" for it in items]
    nw = report.findings.get("news")
    if nw and nw.data.get("web"):
        lines.append("\nWeb results on plans and outlook:")
        lines += [f"- {w['title']} [{w['excerpt_id']}]" for w in nw.data["web"][:5]]
    out["future_plans"] = "\n".join(lines) if lines else "No transcript or web data available."

    if nw and nw.data.get("news"):
        top = nw.data["news"][:6]
        out["news_sentiment"] = (
            f"{v('news_count')} headlines in the window; average lexicon tone {v('news_tone')} "
            f"({v('news_pos')} positive vs {v('news_neg')} negative). Recent headlines:\n"
            + "\n".join(f"- {n['title']} ({n.get('publisher') or 'news'}, {n['date'] or 'n.d.'}) "
                         f"[{n['excerpt_id']}]" for n in top))

    if sc.get("dimensions"):
        parts = []
        for e in sc.get("edges", []):
            parts.append(f"**{e['dimension']}** is an edge (score {v('score_' + e['dimension'])}"
                         + (", best in peer set" if e.get("leader") else "") + ").")
        for g in sc.get("gaps", []):
            parts.append(f"**{g['dimension']}** is a gap (score {v('score_' + g['dimension'])}; "
                         f"leader {g['leader']}).")
        out["competitive_position"] = " ".join(parts) or "No dimension stands out against peers."
    return {k: x for k, x in out.items() if x}
