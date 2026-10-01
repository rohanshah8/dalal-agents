"""Competitor selection and the relative edge scorecard."""
from __future__ import annotations

import math

from ..models import Peer


def size_similarity(mcap_a: float | None, mcap_b: float | None) -> float:
    """exp(-|ln(a/b)|) ∈ (0, 1]; 1 = same size, 0.5 ≈ 2× apart, 0.1 ≈ 10× apart."""
    if not mcap_a or not mcap_b or mcap_a <= 0 or mcap_b <= 0:
        return 0.3
    return math.exp(-abs(math.log(mcap_a / mcap_b)))


def rank_competitors(target_symbol: str, target_mcap: float | None, peers: list[Peer], k: int = 4) -> list[Peer]:
    """Rank same-industry peers: 0.6 · size similarity + 0.4 · industry rank (by market cap)."""
    cands = [p for p in peers if p.symbol.upper() != target_symbol.upper()]
    if not cands:
        return []
    n = len(cands)
    ordered = sorted(cands, key=lambda p: -(p.market_cap or 0))
    for i, p in enumerate(ordered):
        rank_score = 1 - i / max(n - 1, 1) if n > 1 else 1.0
        sim = size_similarity(target_mcap, p.market_cap)
        p.relevance = round(0.6 * sim + 0.4 * rank_score, 4)
        ratio = (p.market_cap or 0) / target_mcap if target_mcap else None
        p.reason = (f"same industry; market cap {ratio:.2f}× target" if ratio else "same industry")
    return sorted(cands, key=lambda p: -(p.relevance or 0))[:k]


# ------------------------------------------------------------------- scorecard
# (metric key, label, higher_is_better)
DIMENSIONS_NONFIN = {
    "Growth": (0.20, [("sales_cagr_5y", "Sales CAGR 5y", True), ("profit_cagr_5y", "Profit CAGR 5y", True),
                      ("q_sales_yoy", "Latest qtr sales YoY", True)]),
    "Profitability": (0.20, [("opm", "Operating margin", True), ("roe", "ROE", True), ("roce", "ROCE", True)]),
    "Balance sheet": (0.15, [("debt_to_equity", "Debt / equity", False),
                             ("interest_coverage", "Interest coverage", True)]),
    "Cash quality": (0.15, [("cfo_to_np_5y", "CFO / net profit (5y)", True),
                            ("fcf_margin_5y", "FCF margin (5y)", True)]),
    "Valuation": (0.15, [("pe", "P/E", False), ("pb", "P/B", False), ("peg", "PEG", False)]),
    "Momentum": (0.10, [("ret_1y", "1Y return", True), ("ret_6m", "6M return", True),
                        ("pct_vs_sma200", "% vs 200-DMA", True)]),
    "Stability": (0.05, [("opm_stdev_5y", "Margin volatility (5y σ)", False),
                         ("volatility_1y", "Price volatility (1y)", False)]),
}
DIMENSIONS_FIN = {
    "Growth": (0.20, [("sales_cagr_5y", "Revenue CAGR 5y", True), ("profit_cagr_5y", "Profit CAGR 5y", True),
                      ("deposit_cagr_5y", "Deposit CAGR 5y", True)]),
    "Profitability": (0.25, [("roe", "ROE", True), ("roa", "ROA", True), ("opm", "Financing margin", True)]),
    "Asset quality": (0.20, [("gnpa", "Gross NPA %", False), ("nnpa", "Net NPA %", False)]),
    "Capital": (0.10, [("equity_to_assets", "Equity / assets", True)]),
    "Valuation": (0.15, [("pb", "P/B", False), ("pe", "P/E", False)]),
    "Momentum": (0.10, [("ret_1y", "1Y return", True), ("ret_6m", "6M return", True)]),
}


def _valid(x) -> bool:
    return isinstance(x, (int, float)) and not (math.isnan(x) or math.isinf(x))


def _percentile_ranks(values: dict[str, float], higher_better: bool) -> dict[str, float]:
    """Mid-rank percentile in [0,1]; ties share the average rank. Single value → 0.5."""
    items = list(values.items())
    n = len(items)
    if n == 1:
        return {items[0][0]: 0.5}
    out = {}
    for name, v in items:
        less = sum(1 for _, w in items if (w < v if higher_better else w > v))
        equal = sum(1 for _, w in items if w == v) - 1
        out[name] = (less + 0.5 * equal) / (n - 1)
    return out


def _metric_value(key: str, m: dict) -> float | None:
    v = m.get(key)
    if key == "pe" and _valid(v) and v <= 0:
        return None  # loss-making: P/E meaningless
    if key == "peg" and _valid(v) and v <= 0:
        return None
    return v if _valid(v) else None


def scorecard(metrics: dict[str, dict], target: str, financial: bool) -> dict:
    """metrics: {company_symbol: flat metric dict}. Returns dimension scores, winners, edges, gaps."""
    dims = DIMENSIONS_FIN if financial else DIMENSIONS_NONFIN
    companies = list(metrics)
    result: dict = {"dimensions": {}, "composite": {}, "metric_table": {}, "weights": {}}
    totals = {c: 0.0 for c in companies}
    weight_used = {c: 0.0 for c in companies}

    for dim, (w, mlist) in dims.items():
        result["weights"][dim] = w
        per_company: dict[str, list[float]] = {c: [] for c in companies}
        for key, label, hib in mlist:
            vals = {c: _metric_value(key, metrics[c]) for c in companies}
            result["metric_table"][label] = {"key": key, "higher_is_better": hib, "dimension": dim,
                                             "values": vals}
            present = {c: v for c, v in vals.items() if v is not None}
            if len(present) < 2:
                continue
            for c, p in _percentile_ranks(present, hib).items():
                per_company[c].append(p)
        scores = {c: round(100 * sum(v) / len(v), 1) for c, v in per_company.items() if v}
        if not scores:
            continue
        winner = max(scores, key=scores.get)
        result["dimensions"][dim] = {"scores": scores, "winner": winner, "weight": w}
        for c, s in scores.items():
            totals[c] += w * s
            weight_used[c] += w

    for c in companies:
        if weight_used[c] > 0:
            result["composite"][c] = round(totals[c] / weight_used[c], 1)

    edges, gaps = [], []
    for dim, info in result["dimensions"].items():
        s = info["scores"].get(target)
        if s is None:
            continue
        if info["winner"] == target or s >= 70:
            edges.append({"dimension": dim, "score": s, "leader": info["winner"] == target})
        elif s <= 30:
            leader = info["winner"]
            gaps.append({"dimension": dim, "score": s, "leader": leader,
                         "leader_score": info["scores"][leader]})
    result["edges"] = sorted(edges, key=lambda e: -e["score"])
    result["gaps"] = sorted(gaps, key=lambda e: e["score"])
    ranked = sorted(result["composite"].items(), key=lambda kv: -kv[1])
    result["rank"] = next((i + 1 for i, (c, _) in enumerate(ranked) if c == target), None)
    result["n_companies"] = len(companies)
    return result
