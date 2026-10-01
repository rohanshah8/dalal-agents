"""Render a Report to Markdown, HTML and JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..facts import fmt_value
from ..models import Report

DISCLAIMER = (
    "> **Disclaimer.** This report was generated automatically by Dalal Agents, an open-source educational "
    "research tool. It is **not investment advice** and not a recommendation to buy, sell or hold any "
    "security. The authors are not SEBI-registered Research Analysts or Investment Advisers. Data comes from "
    "third-party public sources and may be delayed, incomplete or wrong. Verify against primary filings "
    "and consult a SEBI-registered adviser before investing."
)

SECTION_TITLES = [
    ("executive_summary", "Executive summary"),
    ("business_overview", "Business overview"),
    (None, "Price & trend"),
    ("financial_performance", "Financial performance"),
    ("balance_sheet_cash", "Balance sheet & cash-flow quality"),
    ("valuation", "Valuation (what the price implies)"),
    ("ownership", "Ownership"),
    ("future_plans", "Future plans & management commentary"),
    ("news_sentiment", "News flow & sentiment"),
    ("competitive_position", "Competitive landscape & edge"),
    ("bull_case", "Bull case"),
    ("bear_case", "Bear case"),
    ("monitorables", "What to monitor"),
]


def _n(x, unit="", nd=1):
    if x is None:
        return "–"
    if isinstance(x, str):
        return x
    if unit == "%":
        return f"{x:.{nd}f}%"
    if unit == "x":
        return f"{x:.{nd}f}x"
    if unit == "cr":
        return f"{x:,.0f}"
    return f"{x:,.{nd}f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def render_markdown(r: Report) -> str:
    f = r.findings
    fund = f.get("fundamentals").data if f.get("fundamentals") else {}
    val = fund.get("valuation", {}) if fund else {}
    mkt = f.get("market").data if f.get("market") else {}
    own = f.get("ownership").data if f.get("ownership") else {}
    fin = bool(fund.get("is_financial"))
    comp = f.get("competitors").data if f.get("competitors") else {}
    L: list[str] = []

    L.append(f"# {r.name} ({r.symbol}) — Equity Research Report")
    L.append("")
    meta = [f"**Industry:** {comp.get('industry') or '–'}",
            f"**Price:** ₹{_n(mkt.get('price') or val.get('price'), '', 2)} ({mkt.get('price_date', '–')})",
            f"**Market cap:** ₹{_n(val.get('market_cap'), 'cr')} cr",
            f"**Generated:** {r.generated_at[:16].replace('T', ' ')} UTC",
            f"**Engine:** {r.model or 'deterministic (no LLM)'}"]
    L.append(" · ".join(meta))
    L.append("")
    L.append(DISCLAIMER)
    L.append("")
    if r.warnings:
        L.append("<details><summary>Run notes</summary>\n\n" + "\n".join(f"- {w}" for w in r.warnings)
                 + "\n\n</details>\n")

    # Key metrics strip
    L.append("## Key metrics")
    L.append("")
    if fin:
        km = [("P/E", _n(val.get("pe"), "x")), ("P/B", _n(val.get("pb"), "x")), ("ROE", _n(fund.get("roe"), "%")),
              ("ROA", _n(fund.get("roa"), "%", 2)), ("GNPA", _n(fund.get("gnpa"), "%", 2)),
              ("NNPA", _n(fund.get("nnpa"), "%", 2)), ("Rev CAGR 5y", _n(fund.get("sales_cagr_5y"), "%")),
              ("1Y return", _n(mkt.get("ret_1y"), "%"))]
    else:
        km = [("P/E", _n(val.get("pe"), "x")), ("P/B", _n(val.get("pb"), "x")),
              ("ROCE", _n(fund.get("roce"), "%")), ("ROE", _n(fund.get("roe"), "%")),
              ("OPM", _n(fund.get("opm"), "%")), ("D/E", _n(fund.get("debt_to_equity"), "x", 2)),
              ("Sales CAGR 5y", _n(fund.get("sales_cagr_5y"), "%")), ("1Y return", _n(mkt.get("ret_1y"), "%"))]
    L.append(_table([k for k, _ in km], [[v for _, v in km]]))
    L.append("")

    for key, title in SECTION_TITLES:
        if key is None:
            L.append(f"## {title}")
            L.append("")
            L.append(_price_section(mkt))
            L.append("")
            continue
        text = r.narrative.get(key)
        if key == "business_overview" and not text:
            prof = fund.get("profile", {})
            about = _about(r)
            text = about or ""
            if prof.get("pros"):
                text += "\n\n**Screener highlights:** " + "; ".join(prof["pros"])
            if prof.get("cons"):
                text += "\n\n**Screener concerns:** " + "; ".join(prof["cons"])
        if key == "financial_performance":
            L.append(f"## {title}")
            L.append("")
            if text:
                L.append(text)
                L.append("")
            L.append(_growth_table(fund))
            L.append("")
            continue
        if key == "competitive_position":
            L.append(f"## {title}")
            L.append("")
            L.append(_peer_table(r))
            L.append("")
            L.append(_scorecard_table(r))
            L.append("")
            if text:
                L.append(text)
                L.append("")
            continue
        if key == "ownership" and own:
            L.append(f"## {title}")
            L.append("")
            if text:
                L.append(text)
                L.append("")
            L.append(_ownership_table(own))
            L.append("")
            continue
        if text:
            L.append(f"## {title}")
            L.append("")
            L.append(text)
            L.append("")

    L.append("## Sources & methodology")
    L.append("")
    L.append(_verification(r))
    L.append("")
    L.append(_sources(r))
    L.append("")
    L.append("<details><summary>Fact table (every number cited above)</summary>\n")
    L.append(_table(["ID", "Company", "Metric", "Period", "Value", "Source"],
                    [[x.id, x.company or "", x.label, x.period or "", fmt_value(x.value, x.unit),
                      x.source.provider] for x in r.facts]))
    L.append("\n</details>\n")
    L.append("<details><summary>Evidence excerpts</summary>\n")
    for e in r.excerpts:
        url = f" ([link]({e.source.url}))" if e.source.url else ""
        L.append(f"- **[{e.id}]** {e.kind} {e.date or ''} — {e.text[:300]}{url}")
    L.append("\n</details>\n")
    L.append("---")
    L.append(DISCLAIMER)
    return "\n".join(L)


def _about(r: Report) -> str | None:
    for x in r.excerpts:
        if x.kind == "profile":
            return x.text
    return None


def _price_section(m: dict) -> str:
    if not m:
        return "_Price data unavailable._"
    rows = [
        ["Trend", m.get("trend", "–"), "Cross", m.get("cross", "–")],
        ["1M / 3M", f"{_n(m.get('ret_1m'), '%')} / {_n(m.get('ret_3m'), '%')}",
         "6M / 1Y", f"{_n(m.get('ret_6m'), '%')} / {_n(m.get('ret_1y'), '%')}"],
        ["vs NIFTY 50 (1Y)", _n(m.get("rel_ret_1y"), "%"), "NIFTY 50 (1Y)", _n(m.get("bench_ret_1y"), "%")],
        ["3Y / 5Y CAGR", f"{_n(m.get('cagr_3y'), '%')} / {_n(m.get('cagr_5y'), '%')}",
         "Beta (1Y)", _n(m.get("beta_1y"), "", 2)],
        ["SMA50 / SMA200", f"₹{_n(m.get('sma50'), '', 0)} / ₹{_n(m.get('sma200'), '', 0)}",
         "% vs SMA200", _n(m.get("pct_vs_sma200"), "%")],
        ["52W high / low", f"₹{_n(m.get('high_52w'), '', 0)} / ₹{_n(m.get('low_52w'), '', 0)}",
         "From 52W high", _n(m.get("pct_from_52w_high"), "%")],
        ["RSI(14)", _n(m.get("rsi14"), "", 0), "MACD hist", _n(m.get("macd_hist"), "", 2)],
        ["Volatility (1Y)", _n(m.get("volatility_1y"), "%"), "Max drawdown 1Y / 3Y",
         f"{_n(m.get('max_drawdown_1y'), '%')} / {_n(m.get('max_drawdown_3y'), '%')}"],
    ]
    return _table(["Metric", "Value", "Metric", "Value"], rows)


def _growth_table(fund: dict) -> str:
    if not fund:
        return ""
    rows = [
        ["Sales / revenue", _n(fund.get("sales_cagr_3y"), "%"), _n(fund.get("sales_cagr_5y"), "%"),
         _n(fund.get("sales_cagr_10y"), "%"), _n(fund.get("q_sales_yoy"), "%")],
        ["Net profit", _n(fund.get("profit_cagr_3y"), "%"), _n(fund.get("profit_cagr_5y"), "%"),
         _n(fund.get("profit_cagr_10y"), "%"), _n(fund.get("q_profit_yoy"), "%")],
        ["EPS", _n(fund.get("eps_cagr_3y"), "%"), _n(fund.get("eps_cagr_5y"), "%"),
         _n(fund.get("eps_cagr_10y"), "%"), "–"],
    ]
    t = _table(["Growth (CAGR)", "3Y", "5Y", "10Y", f"Latest qtr YoY ({fund.get('latest_quarter', '')})"], rows)
    if fund.get("is_financial"):
        q = [["ROE", _n(fund.get("roe"), "%"), "ROA", _n(fund.get("roa"), "%", 2)],
             ["Financing margin", _n(fund.get("opm"), "%"), "Equity / assets", _n(fund.get("equity_to_assets"), "%")],
             ["Gross NPA", _n(fund.get("gnpa"), "%", 2), "Net NPA", _n(fund.get("nnpa"), "%", 2)],
             ["Deposit CAGR 5y", _n(fund.get("deposit_cagr_5y"), "%"), "Piotroski (adapted)",
              f"{fund.get('piotroski', '–')}/{fund.get('piotroski_max', '–')}"]]
    else:
        q = [["OPM (latest / 5y avg)", f"{_n(fund.get('opm'), '%')} / {_n(fund.get('opm_5y_avg'), '%')}",
              "Net margin", _n(fund.get("npm"), "%")],
             ["ROE (latest / 5y avg)", f"{_n(fund.get('roe'), '%')} / {_n(fund.get('roe_5y_avg'), '%')}",
              "ROCE (latest / 5y avg)", f"{_n(fund.get('roce'), '%')} / {_n(fund.get('roce_5y_avg'), '%')}"],
             ["DuPont: asset turnover", _n(fund.get("asset_turnover"), "x", 2), "Equity multiplier",
              _n(fund.get("equity_multiplier"), "x", 2)],
             ["Debt / equity", _n(fund.get("debt_to_equity"), "x", 2), "Interest coverage",
              _n(fund.get("interest_coverage"), "x")],
             ["CFO / net profit (5y)", _n(fund.get("cfo_to_np_5y"), "x", 2), "FCF margin (5y)",
              _n(fund.get("fcf_margin_5y"), "%")],
             ["Working-capital days", f"{_n(fund.get('wc_days_5y_ago'), '', 0)} → {_n(fund.get('wc_days'), '', 0)}",
              "Piotroski (adapted)", f"{fund.get('piotroski', '–')}/{fund.get('piotroski_max', '–')}"]]
    return t + "\n\n" + _table(["Quality", "Value", "Quality", "Value"], q)


def _ownership_table(o: dict) -> str:
    rows = []
    for k, lbl in [("promoter", "Promoters"), ("fii", "FIIs"), ("dii", "DIIs"), ("public", "Public"),
                   ("government", "Government")]:
        if o.get(f"{k}_latest") is None:
            continue
        rows.append([lbl, _n(o.get(f"{k}_latest"), "%", 2), _pp(o.get(f"{k}_chg_q")), _pp(o.get(f"{k}_chg_1y"))])
    return _table([f"Holder ({o.get('latest_period', '')})", "Holding", "Δ QoQ", "Δ YoY"], rows)


def _pp(x):
    return "–" if x is None else f"{x:+.2f} pp"


def _peer_table(r: Report) -> str:
    fund = r.findings["fundamentals"].data if r.findings.get("fundamentals") else {}
    val = fund.get("valuation", {})
    mkt = r.findings["market"].data if r.findings.get("market") else {}
    fin = bool(fund.get("is_financial"))
    rows = [_peer_row(r.symbol + " ★", fund, val, mkt, fin)]
    for p in r.peers:
        snap = r.peer_findings.get(p.symbol, {}).get("snapshot")
        if not snap or snap.status == "unavailable":
            continue
        d = snap.data
        rows.append(_peer_row(p.symbol, d["fundamentals"], d["valuation"], d["technicals"], fin))
    if fin:
        headers = ["Company", "M-cap ₹cr", "P/E", "P/B", "Rev CAGR 5y", "PAT CAGR 5y", "ROE", "ROA", "GNPA",
                   "1Y ret"]
    else:
        headers = ["Company", "M-cap ₹cr", "P/E", "P/B", "Sales CAGR 5y", "PAT CAGR 5y", "OPM", "ROCE", "D/E",
                   "1Y ret"]
    sel = "\n".join(f"- **{p.symbol}** ({p.name}) — {p.reason}" for p in r.peers)
    return "**Peer set** (from the exchange industry classification, ranked by size similarity):\n" + sel + \
        "\n\n" + _table(headers, rows)


def _peer_row(name, f, v, t, fin):
    if fin:
        return [name, _n(v.get("market_cap"), "cr"), _n(v.get("pe"), "x"), _n(v.get("pb"), "x"),
                _n(f.get("sales_cagr_5y"), "%"), _n(f.get("profit_cagr_5y"), "%"), _n(f.get("roe"), "%"),
                _n(f.get("roa"), "%", 2), _n(f.get("gnpa"), "%", 2), _n(t.get("ret_1y"), "%")]
    return [name, _n(v.get("market_cap"), "cr"), _n(v.get("pe"), "x"), _n(v.get("pb"), "x"),
            _n(f.get("sales_cagr_5y"), "%"), _n(f.get("profit_cagr_5y"), "%"), _n(f.get("opm"), "%"),
            _n(f.get("roce"), "%"), _n(f.get("debt_to_equity"), "x", 2), _n(t.get("ret_1y"), "%")]


def _scorecard_table(r: Report) -> str:
    sc = r.scorecard
    if not sc.get("dimensions"):
        return ""
    comps = list(sc.get("composite", {}).keys())
    headers = ["Dimension (weight)"] + [c + (" ★" if c == r.symbol else "") for c in comps]
    rows = []
    for dim, info in sc["dimensions"].items():
        row = [f"{dim} ({info['weight'] * 100:.0f}%)"]
        for c in comps:
            s = info["scores"].get(c)
            cell = "–" if s is None else f"{s:.0f}"
            if c == info["winner"]:
                cell = f"**{cell}**"
            row.append(cell)
        rows.append(row)
    rows.append(["**Composite**"] + [f"**{sc['composite'].get(c, 0):.0f}**" for c in comps])
    note = ("\n\n_Scores are percentile ranks within this peer set (0 = worst, 100 = best), averaged per dimension. "
            "They measure relative quality, not attractiveness as an investment._")
    edges = ", ".join(f"{e['dimension']} ({e['score']:.0f})" for e in sc.get("edges", [])) or "none"
    gaps = ", ".join(f"{g['dimension']} ({g['score']:.0f}; leader {g['leader']})" for g in sc.get("gaps", [])) or "none"
    return ("### Edge scorecard\n\n" + _table(headers, rows) + note +
            f"\n\n**Edges:** {edges}  \n**Gaps:** {gaps}")


def _verification(r: Report) -> str:
    v = r.verification or {}
    if v.get("mode") == "deterministic":
        s = "Narrative generated deterministically from the fact table (no LLM)."
    else:
        s = (f"AI narrative by `{r.model}`, checked by the verifier: **{v.get('passed', 0)}/{v.get('sentences', 0)}** "
             f"sentences passed ({v.get('pass_rate')}%); numeric sentences grounded in cited facts: "
             f"**{v.get('numeric_pass_rate')}%**. Sentences marked ⚠ failed a check.")
        u = v.get("llm_usage")
        if u:
            s += f" LLM usage: {u['calls']} calls, {u['input_tokens']:,} input / {u['output_tokens']:,} output tokens."
    return (s + "\n\nMethodology: amounts in ₹ crore; FY = April–March; consolidated statements unless the company "
            "is a lender (standalone). CAGR is undefined (–) when either endpoint is ≤ 0. ROE/ROCE use average "
            "opening/closing capital. Reverse DCF: 10-year explicit growth + 5% terminal growth at 12% cost of "
            "equity. Full formulas: docs/DESIGN.md.")


def _sources(r: Report) -> str:
    seen = {}
    for x in list(r.facts) + list(r.excerpts):
        s = x.source
        if s.url and s.url not in seen and s.provider not in ("dalal-agents lexicon", "dalal-agents scorecard"):
            seen[s.url] = s
    lines = [f"- {s.provider}: [{(s.title or s.url)[:90]}]({s.url})" for s in list(seen.values())[:60]]
    return "**Sources**\n\n" + "\n".join(lines)


def render_html(md_text: str, title: str) -> str:
    import markdown
    body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "md_in_html"])
    body = re.sub(r"\[((?:[FE]\d+)(?:,\s*[FE]\d+)*)\]", r'<sup class="cite">[\1]</sup>', body)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
:root {{ --fg:#1b1f24; --muted:#5b6470; --line:#e3e6ea; --accent:#0b6bcb; --bg:#fff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --fg:#e6e8eb; --muted:#9aa4ae; --line:#2b3138; --accent:#5aa9ff; --bg:#0f1317; }} }}
body {{ font: 15px/1.6 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color:var(--fg);
       background:var(--bg); max-width: 1040px; margin: 2rem auto; padding: 0 1.2rem; }}
h1 {{ font-size: 1.8rem; margin-bottom: .2rem; }} h2 {{ border-bottom:1px solid var(--line); padding-bottom:.3rem; margin-top:2.2rem; }}
table {{ border-collapse: collapse; width: 100%; margin: .8rem 0; font-size: 13.5px; font-variant-numeric: tabular-nums; }}
th, td {{ border-bottom: 1px solid var(--line); padding: .35rem .5rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }} th {{ color: var(--muted); font-weight: 600; }}
blockquote {{ margin: 1rem 0; padding: .6rem 1rem; border-left: 3px solid var(--accent); color: var(--muted); font-size: 13px; }}
sup.cite {{ color: var(--muted); font-size: 10px; }} a {{ color: var(--accent); }}
details {{ margin: .8rem 0; }} summary {{ cursor: pointer; color: var(--muted); }}
</style></head><body>{body}</body></html>"""


def save_report(r: Report, out_dir: Path, formats=("md", "html", "json")) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{r.symbol}_{r.generated_at[:10]}"
    md = render_markdown(r)
    paths = {}
    if "md" in formats:
        p = out_dir / f"{stem}.md"
        p.write_text(md, encoding="utf-8")
        paths["md"] = p
    if "html" in formats:
        p = out_dir / f"{stem}.html"
        p.write_text(render_html(md, f"{r.name} — Dalal Agents"), encoding="utf-8")
        paths["html"] = p
    if "json" in formats:
        p = out_dir / f"{stem}.json"
        p.write_text(json.dumps(r.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        paths["json"] = p
    return paths
