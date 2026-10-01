"""`dalal` command-line interface."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import Settings

app = typer.Typer(add_completion=False, help="Dalal Agents — AI equity research for Indian stocks (NSE/BSE).",
                  no_args_is_help=True)
console = Console()


def _settings(model, provider, peers, concalls, offline, no_cache, strict) -> Settings:
    s = Settings()
    if model:
        s.model = model
    if provider:
        s.llm_provider = provider
    if peers is not None:
        s.n_peers = peers
    if concalls is not None:
        s.n_concalls = concalls
    s.offline, s.no_cache, s.strict = offline, no_cache, strict
    return s


@app.command()
def analyze(
    company: str = typer.Argument(..., help="NSE symbol (TCS), BSE code (500325) or company name"),
    out: Path = typer.Option(Path("reports"), "--out", "-o", help="Output directory"),
    model: Optional[str] = typer.Option(None, help="LLM model id, e.g. claude-opus-5-5"),
    provider: Optional[str] = typer.Option(None, help="anthropic | openai | none (default: auto)"),
    peers: Optional[int] = typer.Option(None, help="Number of competitors to analyse (default 4)"),
    concalls: Optional[int] = typer.Option(None, help="Number of concall transcripts to read (default 2)"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic mode, no LLM calls"),
    offline: bool = typer.Option(False, help="Use cached data only"),
    no_cache: bool = typer.Option(False, help="Ignore the HTTP cache"),
    strict: bool = typer.Option(False, help="Drop (rather than flag) sentences that fail verification"),
    fmt: str = typer.Option("md,html,json", "--format", help="Comma-separated: md,html,json"),
    verbose: bool = typer.Option(False, "-v", help="Debug logging"),
):
    """Run the full multi-agent analysis and write a cited report."""
    from .orchestrator import analyze as run
    from .report import save_report

    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)
    s = _settings(model, "none" if no_llm else provider, peers, concalls, offline, no_cache, strict)
    console.print(f"[bold]Dalal Agents[/bold] · analysing [cyan]{company}[/cyan]")
    try:
        report = run(company, s, progress=lambda m: console.print(f"[dim]{m}[/dim]"))
    except LookupError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    paths = save_report(report, out, tuple(x.strip() for x in fmt.split(",")))
    _print_summary(report)
    for k, p in paths.items():
        console.print(f"[green]✓[/green] {k.upper()} report → {p}")


@app.command()
def peers(company: str = typer.Argument(...), n: int = typer.Option(6)):
    """List the closest listed competitors (quick, no deep analysis)."""
    from .agents import Context
    from .analytics import rank_competitors
    from .orchestrator import resolve_symbol

    ctx = Context.create(Settings())
    sym = resolve_symbol(ctx, company)
    d = ctx.company_data[sym]
    ranked = rank_competitors(sym, d.profile.top_ratios.get("Market Cap"), ctx.screener.peers(d), k=n)
    t = Table(title=f"Competitors of {d.profile.name} · {' › '.join(d.profile.sector_path[-2:])}")
    for c in ("Symbol", "Name", "M-cap ₹cr", "P/E", "ROCE %", "Qtr sales YoY %", "Relevance"):
        t.add_column(c, justify="left" if c in ("Symbol", "Name") else "right")
    for p in ranked:
        t.add_row(p.symbol, p.name, f"{p.market_cap or 0:,.0f}", f"{p.pe or 0:.1f}", f"{p.roce or 0:.1f}",
                  f"{p.qtr_sales_var or 0:.1f}", f"{p.relevance:.2f}")
    console.print(t)


@app.command()
def quote(company: str = typer.Argument(...)):
    """Quick snapshot: price, valuation and key ratios."""
    from .agents import Context
    from .orchestrator import resolve_symbol

    ctx = Context.create(Settings())
    sym = resolve_symbol(ctx, company)
    d = ctx.company_data[sym].profile
    t = Table(title=f"{d.name} ({sym})")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in d.top_ratios.items():
        t.add_row(k, "–" if v is None else f"{v:,.2f}")
    console.print(t)


def _print_summary(r) -> None:
    sc = r.scorecard or {}
    if sc.get("composite"):
        t = Table(title="Edge scorecard (percentile vs peers)")
        comps = list(sc["composite"])
        t.add_column("Dimension")
        for c in comps:
            t.add_column(c + (" ★" if c == r.symbol else ""), justify="right")
        for dim, info in sc["dimensions"].items():
            t.add_row(dim, *[("[bold green]" if c == info["winner"] else "") + f"{info['scores'].get(c, 0):.0f}"
                             for c in comps])
        t.add_row("[bold]Composite", *[f"[bold]{sc['composite'][c]:.0f}" for c in comps])
        console.print(t)
    v = r.verification or {}
    if v.get("sentences"):
        console.print(f"Verifier: {v['passed']}/{v['sentences']} sentences passed · "
                      f"numeric grounding {v.get('numeric_pass_rate')}%")
    for w in r.warnings:
        console.print(f"[yellow]! {w}[/yellow]")


if __name__ == "__main__":
    app()
