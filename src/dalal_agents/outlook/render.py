"""Shared, escaped presentation for both UIs and exported reports."""
from __future__ import annotations

import html
from urllib.parse import quote

from .models import DISCLAIMER, EMPTY_ALTERNATIVES, StockOutlook

LABELS = {"ONE_WEEK": "1 Week", "ONE_MONTH": "1 Month", "THREE_MONTHS": "3 Months"}
STYLE = """<style>
.dalal-outlook {font:inherit;line-height:1.55}.dalal-outlook * {box-sizing:border-box}
.dalal-cards {display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:1rem}
.dalal-card {border:1px solid #8393a5;border-radius:12px;padding:1rem;min-width:0}
.dalal-card h3 {margin-top:0}.dalal-outlook table {width:100%;font-variant-numeric:tabular-nums}
.dalal-outlook th,.dalal-outlook td {text-align:left;padding:.3rem}.dalal-badge {font-weight:700}
.dalal-outlook li {margin:.3rem 0}.dalal-note {border-left:4px solid #b7791f;padding:.5rem 1rem}
.dalal-skeleton {height:1rem;margin:.8rem 0;background:#8393a544;border-radius:4px}
.dalal-outlook a {text-decoration:underline}.dalal-outlook details {margin:.7rem 0}
</style>"""


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def money(value, currency="INR"):
    if value is None:
        return "Unavailable"
    prefix = {"INR": "₹", "USD": "$"}.get(currency, currency + " ")
    return f"{prefix}{value:,.2f}"


def loading_html() -> str:
    cards = ''.join('<div class="dalal-card"><h3>' + name + '</h3><div class="dalal-skeleton"></div><div class="dalal-skeleton"></div></div>' for name in LABELS.values())
    return STYLE + f'<section class="dalal-outlook" aria-busy="true" role="status"><p>Calculating outlooks and checking comparable stocks…</p><div class="dalal-cards">{cards}</div></section>'


def error_html(message="Outlook data is unavailable. Please retry the analysis.") -> str:
    return f'<section class="dalal-outlook" role="alert"><p>{esc(message)}</p><p>{DISCLAIMER}</p></section>'


def render_html(result: StockOutlook | dict | None) -> str:
    if result is None:
        return error_html()
    r = StockOutlook.model_validate(result)
    stamp = r.market_timestamp.strftime("%Y-%m-%d %H:%M UTC") if r.market_timestamp else "Unavailable"
    out = [STYLE, '<section class="dalal-outlook">', f'<h2>{esc(r.company_name)} · {esc(r.symbol)} · {esc(r.exchange)}</h2>',
           f'<p>Adjusted close: <strong>{money(r.current_price, r.currency)}</strong> · {esc(r.currency)} · {stamp}</p>',
           f'<p><strong>Data quality: {r.data_quality.status}</strong> ({r.data_quality.score:.0f}/100). Analysis: {r.analysis_timestamp:%Y-%m-%d %H:%M UTC}</p>',
           '<p>Heuristic research scenarios. Confidence is not a calibrated probability; returns and range coverage are not guaranteed.</p>']
    if r.data_quality.warnings:
        out.append('<details class="dalal-note" open><summary>Data limitations and freshness</summary><ul>' +
                   ''.join(f'<li>{esc(w)}</li>' for w in r.data_quality.warnings) + '</ul></details>')
    out.append('<div class="dalal-cards">')
    for o in r.outlooks:
        out.append(f'<article class="dalal-card"><h3>{LABELS[o.horizon]} · {o.trading_days} trading days</h3>')
        if o.status == "AVAILABLE":
            p = o.price_range
            out += [f'<p class="dalal-badge">{o.movement.title()} · Confidence {o.confidence_score:.1f}/100</p>',
                    '<table><caption>Illustrative price range</caption><tr><th>Lower</th><th>Median</th><th>Upper</th></tr>',
                    f'<tr><td>{money(p.lower, r.currency)}</td><td>{money(p.median, r.currency)}</td><td>{money(p.upper, r.currency)}</td></tr></table>',
                    f'<p>Expected return (median): <strong>{o.expected_return_percent:+.2f}%</strong></p>']
        else:
            out.append('<p><strong>Forecast unavailable</strong> — insufficient or excessively stale prices.</p>')
        out.append('<ul>' + ''.join(f'<li><strong>{reason.category.title()}:</strong> {esc(reason.summary)} '
                                   f'<small>{esc(", ".join(reason.source_ids))}</small></li>' for reason in o.reasoning) + '</ul>')
        out.append('<details open><summary>Key risks</summary><ul>' + ''.join(f'<li>{esc(risk)}</li>' for risk in o.risks) + '</ul></details>')
        out.append(f'<p><small>Prices as of {stamp}. Data quality: {r.data_quality.status}.</small></p>')
        out.append('<h4>Potential alternatives</h4>')
        alternatives = [a for a in r.alternatives if a.comparison_horizon == o.horizon]
        if not alternatives:
            out.append(f'<p>{EMPTY_ALTERNATIVES}</p>')
        for a in alternatives:
            out.append(f'<details open><summary><strong>{esc(a.symbol)} · {esc(a.company_name)}</strong></summary>'
                       f'<p>{a.movement.title()} · Expected return {a.expected_return_percent:+.2f}% · Confidence {a.confidence_score:.1f}/100</p>'
                       f'<p>Adjusted close {money(a.current_price, a.currency)} · {esc(a.exchange)}<br>Lower {money(a.price_range.lower, a.currency)} · Median {money(a.price_range.median, a.currency)} · Upper {money(a.price_range.upper, a.currency)}</p>'
                       f'<p>{esc(a.why_ranked_higher)}</p><ul>' + ''.join(f'<li>{esc(x.summary)}</li>' for x in a.reasons) +
                       f'</ul><p><strong>Key risk:</strong> {esc(a.key_risk)}</p></details>')
        out.append('</article>')
    out.append('</div><details><summary>Sources and publication dates</summary><ul>')
    all_sources = {s.id: s for s in r.data_quality.sources}
    for a in r.alternatives:
        all_sources.update({s.id: s for s in a.data_quality.sources})
    for s in all_sources.values():
        title = esc(s.title)
        if s.url:
            title = f'<a href="{esc(s.url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
        published = s.published_at.strftime("%Y-%m-%d %H:%M UTC") if s.published_at else "Unknown; first known at retrieval"
        out.append(f'<li><strong>{esc(s.id)} · {esc(s.name)}</strong>: {title}. Published: {published}. Retrieved: {s.retrieved_at:%Y-%m-%d %H:%M UTC}. {s.freshness}.</li>')
    out.append(f'</ul></details><details><summary>Methodology</summary><p>{esc(r.methodology)}</p><p>{esc(r.confidence_method)}</p><p>{esc(r.range_method)}</p><p>Model {esc(r.model_version)} · Audit ID {esc(r.analysis_id)}</p></details><p><strong>{DISCLAIMER}</strong></p></section>')
    return ''.join(out)


def render_markdown(result: StockOutlook) -> str:
    def safe(value):
        return esc(value).replace("[", "&#91;").replace("]", "&#93;").replace("|", "&#124;")
    lines = ["## Multi-horizon outlook & alternatives", "", DISCLAIMER, "", result.methodology, result.confidence_method,
             result.range_method, "", f"Data quality: **{result.data_quality.status}**. Analysis: {result.analysis_timestamp.isoformat()}."]
    for outlook in result.outlooks:
        lines += ["", f"### {LABELS[outlook.horizon]}", ""]
        if outlook.price_range:
            p = outlook.price_range
            lines += [f"**{outlook.movement.title()}** · Confidence {outlook.confidence_score:.1f}/100 · Median return {outlook.expected_return_percent:+.2f}%",
                      f"Lower {money(p.lower)} · Median {money(p.median)} · Upper {money(p.upper)}", ""]
        else:
            lines += ["Forecast unavailable: insufficient or stale prices.", ""]
        lines += [f"- **{r.category.title()}:** {safe(r.summary)} ({', '.join(r.source_ids)})" for r in outlook.reasoning]
        lines += ["", "**Key risks:** " + " ".join(safe(r) for r in outlook.risks), "", "**Potential alternatives**", ""]
        alternatives = [a for a in result.alternatives if a.comparison_horizon == outlook.horizon]
        lines += [f"- **{safe(a.symbol)}**: {a.expected_return_percent:+.2f}% median return; confidence {a.confidence_score:.1f}/100. "
                  f"Range {money(a.price_range.lower)} / {money(a.price_range.median)} / {money(a.price_range.upper)}. "
                  f"{safe(a.why_ranked_higher)} Risk: {safe(a.key_risk)}" for a in alternatives] or [EMPTY_ALTERNATIVES]
    lines += ["", "### Outlook data limitations", ""] + [f"- {safe(w)}" for w in result.data_quality.warnings]
    lines += ["", "### Outlook sources", ""]
    sources = {s.id: s for s in result.data_quality.sources}
    for a in result.alternatives:
        sources.update({s.id: s for s in a.data_quality.sources})
    for s in sources.values():
        link = f" ([source]({quote(s.url, safe=':/?=&%#')}))" if s.url else ""
        lines += [f"- {s.id}: {safe(s.name)} — {safe(s.title)}. Published: {s.published_at or 'unknown'}. Retrieved: {s.retrieved_at.isoformat()}. {s.freshness}.{link}"]
    return "\n".join(lines)
