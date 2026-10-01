"""Orchestrator — runs the agent DAG in three stages with graceful degradation.

Stage 0  resolve ticker, fetch target company data + benchmark
Stage 1  research agents in parallel (market, fundamentals, ownership, filings, concall, news)
         + competitor discovery
Stage 2  peer snapshots in parallel → edge scorecard
Stage 3  writer (LLM, grounded) → verifier; deterministic narrative if no LLM
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from .agents import (
    CompetitorDiscoveryAgent,
    ConcallAgent,
    Context,
    EdgeAgent,
    FilingsAgent,
    FundamentalsAgent,
    MarketAgent,
    NewsWebAgent,
    OwnershipAgent,
    PeerSnapshotAgent,
)
from .agents.competition import flat_metrics
from .agents.synthesis import SECTIONS, WRITER_SYSTEM, WRITER_USER, verify_narrative
from .config import Settings
from .facts import FactStore
from .models import Finding, Peer, Report
from .narrative import deterministic_narrative

log = logging.getLogger(__name__)


def resolve_symbol(ctx: Context, query: str) -> str:
    """Accept an NSE symbol (TCS), BSE code (500325) or a company name ('tata consultancy')."""
    q = query.strip()
    cand = q.upper().replace(".NS", "").replace(".BO", "")
    if cand.replace("&", "").replace("-", "").isalnum() and " " not in cand:
        try:
            ctx.company_data[cand] = ctx.screener.company(cand)
            return cand
        except Exception:
            pass
    try:
        hits = ctx.screener.search(q)
    except Exception:
        hits = []
    for h in hits:
        url = h.get("url", "")
        parts = [p for p in url.split("/") if p]
        if len(parts) >= 2 and parts[0] == "company":
            sym = parts[1].upper()
            try:
                ctx.company_data[sym] = ctx.screener.company(sym)
                return sym
            except Exception:
                continue
    raise LookupError(f"Could not find an Indian listed company matching '{query}'.")


def analyze(query: str, settings: Settings | None = None, progress=None) -> Report:
    """Full pipeline: research (stages 0–2) then write (stage 3)."""
    settings = settings or Settings()
    report = research(query, settings, progress)
    return write(report, settings, progress)


def research(query: str, settings: Settings | None = None, progress=None) -> Report:
    """Stages 0–2: data, research agents, peers, edge scorecard. No LLM writing.

    The returned Report is self-contained (facts, excerpts, industry, price series), so it can be
    cached and later passed to `write()` with different LLM settings.
    """
    settings = settings or Settings()
    ctx = Context.create(settings)
    ctx.progress = progress
    say = ctx.say

    say(f"Resolving '{query}'…")
    symbol = resolve_symbol(ctx, query)
    data = ctx.company_data[symbol]
    prof = data.profile
    say(f"Found {prof.name} ({symbol}) · {' › '.join(prof.sector_path[-2:])}"
        + (" · financial-sector metrics" if prof.is_financial else ""))
    report = Report(symbol=symbol, name=prof.name, industry=" › ".join(prof.sector_path[-2:]))

    ys = ctx.yahoo.resolve(symbol)
    ctx.yahoo_symbols[symbol] = ys
    prof.yahoo_symbol = ys
    try:
        from .providers.yahoo import BENCHMARK
        ctx.benchmark = ctx.yahoo.history(BENCHMARK, settings.price_history)
    except Exception as e:
        report.warnings.append(f"Benchmark (NIFTY 50) unavailable: {e}")
    if ys is None:
        report.warnings.append("No Yahoo Finance ticker found — price analytics skipped.")

    # ---------------- Stage 1
    say("Stage 1/3 · research agents (market, fundamentals, ownership, filings, concalls, news, competitors)…")
    stage1 = [MarketAgent, FundamentalsAgent, OwnershipAgent, FilingsAgent, ConcallAgent, NewsWebAgent,
              CompetitorDiscoveryAgent]
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futures = {cls.name: pool.submit(cls(ctx, symbol).run) for cls in stage1}
        for name, fut in futures.items():
            f: Finding = fut.result()
            report.findings[name] = f
            mark = {"ok": "✓", "partial": "◐", "unavailable": "✗"}[f.status]
            say(f"  {mark} {name:<13} {f.summary or '; '.join(f.errors)[:120]}  ({f.elapsed_s:.1f}s)")

    # ---------------- Stage 2
    comp = report.findings.get("competitors")
    peers = [Peer(**p) for p in (comp.data.get("peers", []) if comp else [])]
    report.peers = peers
    peer_metrics: dict[str, dict] = {}
    if peers:
        say(f"Stage 2/3 · analysing {len(peers)} competitors: {', '.join(p.symbol for p in peers)}…")
        with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
            futs = {p.symbol: pool.submit(PeerSnapshotAgent(ctx, p.symbol).run) for p in peers}
            for sym, fut in futs.items():
                f = fut.result()
                report.peer_findings[sym] = {"snapshot": f}
                if f.status != "unavailable":
                    peer_metrics[sym] = flat_metrics(f.data["fundamentals"], f.data["technicals"],
                                                     f.data["valuation"])
        fund = report.findings["fundamentals"].data
        tech = report.findings["market"].data
        target_metrics = flat_metrics(fund, tech, fund.get("valuation", {}))
        edge = EdgeAgent(ctx, symbol, target_metrics, peer_metrics, prof.is_financial).run()
        report.findings["edge"] = edge
        report.scorecard = edge.data
        say(f"  {'✓' if edge.status == 'ok' else '✗'} edge          {edge.summary}")
    else:
        report.warnings.append("No competitors could be analysed.")

    report.charts = {"prices": _price_series(ctx, [symbol] + [p.symbol for p in peers]),
                     "tables": _tables(data)}
    report.facts = list(ctx.facts.facts.values())
    report.excerpts = list(ctx.facts.excerpts.values())
    return report


def write(report: Report, settings: Settings | None = None, progress=None) -> Report:
    """Stage 3: grounded LLM narrative + verifier, or the deterministic narrative without an LLM.

    Works on a fresh copy, so a cached research Report is never mutated.
    """
    settings = settings or Settings()
    report = report.model_copy(deep=True)
    ctx = Context.create(settings)
    ctx.progress = progress
    ctx.facts = FactStore.from_report(report)
    report.model = ctx.llm.model if ctx.llm.enabled else None
    report.verification = {}
    if not ctx.llm.enabled:
        report.warnings.append("No LLM configured — deterministic narrative used (set ANTHROPIC_API_KEY "
                               "or OPENAI_API_KEY for AI-written analysis).")
    ctx.say("Stage 3/3 · writing report" + (f" with {ctx.llm.model}…" if ctx.llm.enabled else " (deterministic)…"))
    narrative = None
    if ctx.llm.enabled:
        try:
            narrative = write_narrative(ctx, report)
        except Exception as e:
            report.warnings.append(f"LLM writer failed ({e}); deterministic narrative used.")
    if narrative:
        narrative, stats = verify_narrative(narrative, ctx.facts, strict=settings.strict)
        report.verification = stats
        ctx.say(f"  ✓ verifier: {stats['passed']}/{stats['sentences']} sentences passed "
                f"({stats.get('numeric_pass_rate')}% of numeric sentences grounded)")
    else:
        narrative = deterministic_narrative(report, ctx.facts)
        report.verification = {"mode": "deterministic"}
    report.narrative = narrative
    if ctx.llm.enabled:
        report.verification["llm_usage"] = dict(ctx.llm.usage)
    return report


def _tables(data) -> dict:
    """Annual P&L, quarters and shareholding as plain {periods, rows} dicts for charts."""
    out = {}
    for name in ("profit_loss", "quarters", "shareholding"):
        t = getattr(data, name, None)
        if t is not None:
            out[name] = {"periods": list(t.periods), "rows": {k: list(v) for k, v in t.rows.items()}}
    return out


def _price_series(ctx: Context, symbols: list[str]) -> dict:
    """Weekly closes (≈3y) for the target, peers and NIFTY 50 — small enough to embed in the report."""
    out = {}

    def weekly(df):
        w = df["Close"].resample("W-FRI").last().dropna().tail(160)
        return {"dates": [d.strftime("%Y-%m-%d") for d in w.index], "close": [round(float(x), 2) for x in w]}

    for sym in symbols:
        ys = ctx.yahoo_symbols.get(sym)
        if not ys:
            continue
        try:
            out[sym] = weekly(ctx.yahoo.history(ys, ctx.settings.price_history))
        except Exception as e:
            log.debug("price series unavailable for %s: %s", sym, e)
    if ctx.benchmark is not None and len(ctx.benchmark):
        out["NIFTY 50"] = weekly(ctx.benchmark)
    return out


def write_narrative(ctx: Context, report: Report) -> dict[str, str]:
    sym = report.symbol
    store = ctx.facts
    target_ids = [i for f in report.findings.values() if f.company == sym for i in f.fact_ids]
    peer_ids = [i for pf in report.peer_findings.values() for f in pf.values() for i in f.fact_ids]
    ex_ids = [i for f in report.findings.values() for i in f.excerpt_ids]
    concall = report.findings.get("concall")
    news = report.findings.get("news")
    sc = report.scorecard or {}
    cc = []
    if concall and concall.data.get("calls"):
        for c in concall.data["calls"]:
            ext = {k: v for k, v in (c.get("extraction") or {}).items() if not k.startswith("_")}
            cc.append({"date": c["date"], "tone": round(c["tone"]["net_tone"], 3), "extraction": ext})
    user = WRITER_USER.format(
        name=report.name, symbol=sym,
        industry=report.industry or "",
        peers=", ".join(f"{p.symbol} ({p.name})" for p in report.peers) or "none",
        facts_target=store.render_facts(ids=target_ids),
        facts_peers=store.render_facts(ids=peer_ids) or "none",
        excerpts=store.render_excerpts(ids=ex_ids[:120], max_chars=400) or "none",
        edges=json.dumps(sc.get("edges", [])), gaps=json.dumps(sc.get("gaps", [])),
        concall=json.dumps(cc, ensure_ascii=False)[:12000],
        news=json.dumps((news.data.get("llm") if news else None) or {}, ensure_ascii=False)[:6000],
    )
    j = ctx.llm.complete_json(WRITER_SYSTEM, user, max_tokens=8000)
    out = {}
    for k in SECTIONS:
        v = j.get(k)
        if isinstance(v, list):
            v = "\n".join(f"- {x}" for x in v)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    if len(out) < 4:
        raise ValueError("writer returned too few sections")
    return out
