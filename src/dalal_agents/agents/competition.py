"""Competition agents: competitor discovery, peer analysis, edge scorecard."""
from __future__ import annotations

from ..analytics import (
    compute_fundamentals,
    compute_technicals,
    compute_valuation,
    rank_competitors,
    scorecard,
)
from ..models import Peer, Source
from .base import Agent

FILTER_SYSTEM = """You are an equity analyst. Decide which candidate companies are genuine business
competitors of the target (overlapping products/customers), based only on the descriptions given."""

FILTER_USER = """Target: {target} — {target_about}

Candidates:
{cands}

Return JSON: {{"competitors": [{{"symbol": "...", "overlap": "high|medium|low|none", "reason": "..."}}]}}"""


class CompetitorDiscoveryAgent(Agent):
    name = "competitors"
    description = "Find the most relevant listed competitors"

    def execute(self) -> None:
        d = self.data()
        k = self.ctx.settings.n_peers
        peers = self.ctx.screener.peers(d)
        if not peers:
            raise LookupError("no peer table available")
        target_mcap = d.profile.top_ratios.get("Market Cap")
        ranked = rank_competitors(self.symbol, target_mcap, peers, k=k + 3)
        # fetch candidate company pages (needed for analysis anyway)
        fetched: list[Peer] = []
        for p in ranked:
            if p.symbol in self.ctx.company_data:
                fetched.append(p)
                continue
            try:
                self.ctx.company_data[p.symbol] = self.ctx.screener.company(p.symbol)
                fetched.append(p)
            except Exception as e:
                self.finding.errors.append(f"{p.symbol}: {e}")
        chosen = fetched
        if self.ctx.llm.enabled and len(fetched) > 1:
            try:
                cands = "\n".join(
                    f"- {p.symbol} ({self.ctx.company_data[p.symbol].profile.name}): "
                    f"{(self.ctx.company_data[p.symbol].profile.about or 'no description')[:400]}" for p in fetched)
                j = self.ctx.llm.complete_json(FILTER_SYSTEM, FILTER_USER.format(
                    target=d.profile.name, target_about=(d.profile.about or "")[:600], cands=cands), max_tokens=1200)
                verdict = {c["symbol"].upper(): c for c in j.get("competitors", []) if c.get("symbol")}
                kept = []
                for p in fetched:
                    v = verdict.get(p.symbol.upper())
                    if v and v.get("overlap") == "none":
                        self.finding.data.setdefault("excluded", []).append(
                            {"symbol": p.symbol, "reason": v.get("reason")})
                        continue
                    if v:
                        p.reason = f"{p.reason}; business overlap {v.get('overlap')}: {v.get('reason')}"
                    kept.append(p)
                chosen = kept or fetched
            except Exception as e:
                self.finding.errors.append(f"LLM overlap check: {e}")
        chosen = chosen[:k]
        self.finding.data["peers"] = [p.model_dump() for p in chosen]
        self.finding.data["candidates"] = len(peers)
        self.finding.data["industry"] = d.profile.sector_path[-1] if d.profile.sector_path else None
        if self.finding.errors:
            self.finding.status = "partial"
        self.finding.summary = ", ".join(p.symbol for p in chosen)


class PeerSnapshotAgent(Agent):
    """Lightweight fundamentals + technicals for one peer (same maths as the target)."""

    name = "peer"

    def execute(self) -> None:
        d = self.data()
        m = compute_fundamentals(d)
        tech = {}
        ys = self.ctx.yahoo_symbols.get(self.symbol)
        if ys is None:
            ys = self.ctx.yahoo.resolve(self.symbol)
            self.ctx.yahoo_symbols[self.symbol] = ys
        if ys:
            try:
                tech = compute_technicals(self.ctx.yahoo.history(ys, "2y"), self.ctx.benchmark)
            except Exception as e:
                self.finding.errors.append(f"prices: {e}")
        v = compute_valuation(d.profile.top_ratios, m, tech, self.ctx.settings.cost_of_equity,
                              self.ctx.settings.terminal_growth)
        self.finding.data = {"fundamentals": {k: x for k, x in m.items() if k != "piotroski_detail"},
                             "technicals": tech, "valuation": v, "name": d.profile.name,
                             "is_financial": d.profile.is_financial}
        src = d.source or Source(provider="screener.in")
        from .research import FUND_LABELS, VAL_LABELS
        for key in ("sales_cagr_5y", "profit_cagr_5y", "opm", "roe", "roce", "roa", "debt_to_equity",
                    "cfo_to_np_5y", "q_sales_yoy", "gnpa", "nnpa", "sales", "net_profit"):
            label, unit = FUND_LABELS[key]
            self.fact(key, label, m.get(key), src, unit, m.get("latest_fy"))
        for key in ("market_cap", "pe", "pb", "implied_growth_10y"):
            label, unit = VAL_LABELS[key]
            self.fact(key, label, v.get(key), src, unit, "current")
        if tech:
            ysrc = Source(provider="yahoo-finance", url=f"https://finance.yahoo.com/quote/{ys}", title=ys)
            self.fact("ret_1y", "1-year return", tech.get("ret_1y"), ysrc, "%", tech.get("price_date"))
        if self.finding.errors:
            self.finding.status = "partial"


def flat_metrics(fund: dict, tech: dict, val: dict) -> dict:
    out = dict(fund)
    out.update({k: tech.get(k) for k in ("ret_1y", "ret_6m", "pct_vs_sma200", "volatility_1y")})
    out.update({k: val.get(k) for k in ("pe", "pb", "peg", "market_cap")})
    return out


class EdgeAgent(Agent):
    name = "edge"
    description = "Relative scorecard: where the company wins and loses vs peers"

    def __init__(self, ctx, symbol, target_metrics: dict, peer_metrics: dict[str, dict], financial: bool):
        super().__init__(ctx, symbol)
        self.target_metrics = target_metrics
        self.peer_metrics = peer_metrics
        self.financial = financial

    def execute(self) -> None:
        if not self.peer_metrics:
            raise LookupError("no peers analysed")
        metrics = {self.symbol: self.target_metrics, **self.peer_metrics}
        sc = scorecard(metrics, self.symbol, self.financial)
        src = Source(provider="dalal-agents scorecard", title="Percentile scorecard across peer set")
        for dim, info in sc["dimensions"].items():
            s = info["scores"].get(self.symbol)
            self.fact(f"score_{dim}", f"{dim} score vs peers (0–100 percentile)", s, src)
        self.fact("composite", "Composite relative-quality score (0–100)", sc["composite"].get(self.symbol), src)
        self.fact("composite_rank", f"Composite rank among {sc['n_companies']} companies", sc.get("rank"), src)
        self.finding.data = sc
        self.finding.summary = (f"rank {sc.get('rank')}/{sc['n_companies']}; edges: "
                                + ", ".join(e["dimension"] for e in sc["edges"]) or "none")
