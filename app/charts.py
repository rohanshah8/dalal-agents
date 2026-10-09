"""Plotly figures for the web UI. Pure functions of a Report dict (`Report.model_dump()`)."""
from __future__ import annotations

import math

import plotly.graph_objects as go

# One accent for the subject company; peers share muted, distinguishable hues.
TARGET = "#0b6bcb"
PEERS = ["#e07b39", "#3a9e6a", "#8e6cc4", "#c4508a", "#7a8a99", "#b59a2e"]
BENCH = "#9aa4ae"

_LAYOUT = dict(
    margin=dict(l=8, r=8, t=36, b=8),
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    font=dict(family="Arial, Helvetica, sans-serif", size=12),
)


def _color(i: int) -> str:
    return PEERS[i % len(PEERS)]


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def price_vs_peers(r: dict) -> go.Figure | None:
    """Weekly closes rebased to 100 at the first common date: target, peers, NIFTY 50."""
    prices = (r.get("charts") or {}).get("prices") or {}
    sym = r["symbol"]
    if sym not in prices:
        return None
    start = max(p["dates"][0] for p in prices.values() if p["dates"])
    fig = go.Figure()
    order = [sym] + [k for k in prices if k not in (sym, "NIFTY 50")] + (["NIFTY 50"] if "NIFTY 50" in prices else [])
    for i, k in enumerate(order):
        p = prices[k]
        pts = [(d, c) for d, c in zip(p["dates"], p["close"]) if d >= start and c]
        if not pts:
            continue
        base = pts[0][1]
        style = (dict(color=TARGET, width=3) if k == sym else
                 dict(color=BENCH, width=2, dash="dot") if k == "NIFTY 50" else dict(color=_color(i - 1), width=1.5))
        fig.add_trace(go.Scatter(x=[d for d, _ in pts], y=[round(c / base * 100, 1) for _, c in pts],
                                 name=k + (" ★" if k == sym else ""), line=style,
                                 hovertemplate="%{y:.1f}"))
    fig.add_hline(y=100, line=dict(color=BENCH, width=1))
    fig.update_layout(title="Price, rebased to 100", yaxis_title="Rebased (start = 100)", height=420, **_LAYOUT)
    return fig


def price_with_smas(r: dict) -> go.Figure | None:
    """Target weekly close with 10- and 40-week moving averages (≈ 50/200-day)."""
    p = ((r.get("charts") or {}).get("prices") or {}).get(r["symbol"])
    if not p or len(p["close"]) < 45:
        return None
    c = p["close"]

    def sma(n):
        return [None if i + 1 < n else round(sum(c[i + 1 - n:i + 1]) / n, 2) for i in range(len(c))]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=p["dates"], y=c, name="Close (weekly)", line=dict(color=TARGET, width=2)))
    fig.add_trace(go.Scatter(x=p["dates"], y=sma(10), name="~50-day avg", line=dict(color=PEERS[0], width=1.3)))
    fig.add_trace(go.Scatter(x=p["dates"], y=sma(40), name="~200-day avg", line=dict(color=PEERS[1], width=1.3)))
    fig.update_layout(title=f"{r['symbol']} price (₹)", height=380, **_LAYOUT)
    return fig


def scorecard_heatmap(r: dict) -> go.Figure | None:
    sc = r.get("scorecard") or {}
    dims = sc.get("dimensions") or {}
    if not dims:
        return None
    sym = r["symbol"]
    companies = [sym] + [p["symbol"] for p in r.get("peers", []) if p["symbol"] in next(iter(dims.values()))["scores"]]
    rows = list(dims) + ["Composite"]
    z, text = [], []
    for d in rows:
        scores = sc.get("composite", {}) if d == "Composite" else dims[d]["scores"]
        vals = [_num(scores.get(c)) for c in companies]
        z.append(vals)
        text.append(["–" if v is None else f"{v:.0f}" for v in vals])
    fig = go.Figure(go.Heatmap(
        z=z, x=[c + (" ★" if c == sym else "") for c in companies], y=rows, text=text, texttemplate="%{text}",
        colorscale=[[0, "#d9534f"], [0.5, "#f5f0e1"], [1, "#2e8b57"]], zmin=0, zmax=100,
        hovertemplate="%{y} · %{x}: %{z:.0f}<extra></extra>", colorbar=dict(title="pctile", thickness=10),
    ))
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(title="Edge scorecard (percentile vs peers, 100 = best)",
                      height=90 + 42 * len(rows), margin=dict(l=8, r=8, t=40, b=8))
    return fig


def composite_bar(r: dict) -> go.Figure | None:
    comp = (r.get("scorecard") or {}).get("composite") or {}
    if not comp:
        return None
    sym = r["symbol"]
    items = sorted(((k, v) for k, v in comp.items() if v is not None), key=lambda kv: kv[1])
    fig = go.Figure(go.Bar(
        x=[v for _, v in items], y=[k + (" ★" if k == sym else "") for k, _ in items], orientation="h",
        marker_color=[TARGET if k == sym else BENCH for k, _ in items], text=[f"{v:.0f}" for _, v in items],
        textposition="outside", hovertemplate="%{y}: %{x:.0f}<extra></extra>",
    ))
    fig.update_layout(title="Composite score", xaxis=dict(range=[0, 110]), height=80 + 38 * len(items),
                      margin=dict(l=8, r=8, t=40, b=8))
    return fig


def _row(table: dict, *labels: str):
    rows = table.get("rows", {})
    norm = {k.lower().rstrip("+ ").strip(): k for k in rows}
    for lab in labels:
        k = norm.get(lab.lower())
        if k:
            return rows[k]
    return None


def annual_financials(r: dict) -> go.Figure | None:
    pl = ((r.get("charts") or {}).get("tables") or {}).get("profit_loss")
    if not pl:
        return None
    periods = pl["periods"]
    sales = _row(pl, "sales", "revenue")
    np_ = _row(pl, "net profit")
    opm = _row(pl, "opm %", "financing margin %")
    if not sales or not np_:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=periods, y=sales, name="Sales / revenue (₹ cr)", marker_color=TARGET))
    fig.add_trace(go.Bar(x=periods, y=np_, name="Net profit (₹ cr)", marker_color=PEERS[1]))
    if opm:
        fig.add_trace(go.Scatter(x=periods, y=opm, name="Margin %", yaxis="y2", mode="lines+markers",
                                 line=dict(color=PEERS[0], width=2)))
    fig.update_layout(title="Annual results", barmode="group", height=400,
                      yaxis=dict(title="₹ cr"), yaxis2=dict(title="%", overlaying="y", side="right", showgrid=False),
                      **_LAYOUT)
    return fig


def quarterly_results(r: dict) -> go.Figure | None:
    q = ((r.get("charts") or {}).get("tables") or {}).get("quarters")
    if not q:
        return None
    sales = _row(q, "sales", "revenue")
    np_ = _row(q, "net profit")
    if not sales:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=q["periods"], y=sales, name="Sales / revenue (₹ cr)", marker_color=TARGET))
    if np_:
        fig.add_trace(go.Bar(x=q["periods"], y=np_, name="Net profit (₹ cr)", marker_color=PEERS[1]))
    fig.update_layout(title="Quarterly results", barmode="group", height=360, **_LAYOUT)
    return fig


def shareholding(r: dict) -> go.Figure | None:
    t = ((r.get("charts") or {}).get("tables") or {}).get("shareholding")
    if not t:
        return None
    fig = go.Figure()
    for i, lab in enumerate(["Promoters", "FIIs", "DIIs", "Government", "Public"]):
        row = _row(t, lab.lower())
        if row and any(v for v in row if v):
            fig.add_trace(go.Scatter(x=t["periods"], y=row, name=lab, stackgroup="one", mode="lines",
                                     line=dict(width=0.5, color=([TARGET] + PEERS)[i]),
                                     hovertemplate="%{y:.2f}%"))
    if not fig.data:
        return None
    fig.update_layout(title="Shareholding pattern (%)", yaxis=dict(range=[0, 100]), height=380, **_LAYOUT)
    return fig
