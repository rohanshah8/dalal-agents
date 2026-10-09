"""A concise, escaped view of existing research results, shared by both GUIs.

Presentation only: forecasts and alternative qualification remain in the engine.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from dalal_agents.outlook.models import DISCLAIMER, StockOutlook
from dalal_agents.outlook.render import LABELS, esc, money

CSS = Path(__file__).with_suffix(".css").read_text(encoding="utf-8")
DIMENSIONS = ("Growth", "Profitability", "Valuation", "Momentum")
SECTION_NAMES = ("Stock Snapshot", "Outlook", "Better Alternatives", "Competitor Comparison", "Investment Checklist")
ICONS = {"positive": "✅", "negative": "❌", "neutral": "⚠", "unknown": "—"}


def number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def grade(score):
    if score is None:
        return "Not enough data", "unknown"
    if score >= 70:
        return "Strong", "positive"
    if score <= 30:
        return "Weak", "negative"
    return "Neutral", "neutral"


def badge(label, tone="neutral"):
    return f'<span class="rd-badge rd-{tone}">{esc(label)}</span>'


def pct(value):
    return "Unavailable" if value is None else f"{value:+.1f}%"


def stamp(value):
    if not value:
        return "Time unavailable"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M UTC")
    return str(value)[:10] + " · daily close"


def data(report, name):
    return ((report.get("findings") or {}).get(name) or {}).get("data") or {}


def score_for(report, dimension, symbol=None):
    scores = ((report.get("scorecard") or {}).get("dimensions", {}).get(dimension) or {}).get("scores", {})
    return number(scores.get(symbol or report["symbol"]))


def comparison_rows(report):
    """Keep the target + three selected peers; preserve scores and share tied ranks."""
    names = {report["symbol"]: report["name"]}
    for peer in report.get("peers", []):
        if peer["symbol"] not in names and len(names) < 4:
            names[peer["symbol"]] = peer["name"]
    composite = (report.get("scorecard") or {}).get("composite", {})
    rows = [{"symbol": s, "name": name, "score": number(composite.get(s)),
             "scores": {d: score_for(report, d, s) for d in DIMENSIONS}} for s, name in names.items()]
    for row in rows:
        row["rank"] = None if row["score"] is None else 1 + sum(
            other["score"] is not None and other["score"] > row["score"] for other in rows)
    return sorted(rows, key=lambda row: (row["rank"] or 999, row["symbol"] != report["symbol"], row["symbol"]))


def highlights(row, rows):
    positives, negatives = [], []
    strengths = {"Growth": "Best growth", "Profitability": "Best profitability",
                 "Valuation": "Lowest relative valuation", "Momentum": "Strongest price trend"}
    weaknesses = {"Growth": "Slower growth", "Profitability": "Weaker profitability",
                  "Valuation": "Pricier than peers", "Momentum": "Weak price trend"}
    for dim, score in row["scores"].items():
        values = [r["scores"][dim] for r in rows if r["scores"][dim] is not None]
        if score is None or len(values) < 2 or min(values) == max(values):
            continue
        if score == max(values):
            text = strengths[dim]
            if values.count(score) > 1:
                text = "Joint leader: " + dim.lower()
            positives.append(("positive", text))
        elif score <= 30:
            negatives.append(("negative", weaknesses[dim]))
    return positives[:2] + negatives[:2]


def short_reason(reason):
    """Shorten known engine phrasing without changing the underlying evidence."""
    text = " ".join(reason.summary.split())
    if reason.category == "TECHNICAL":
        match = re.match(r"(\d+)-session return ([+-]?[\d.]+%|unavailable)", text)
        if match:
            days, change = match.groups()
            return "Recent price change unavailable" if change == "unavailable" else f"Price {change} over {days} trading days"
    if reason.category == "FUNDAMENTAL":
        match = re.match(r"Revenue growth ([+-]?[\d.]+%|unavailable); profit growth ([+-]?[\d.]+%|unavailable)", text)
        if match:
            revenue, profit = match.groups()
            return f"Revenue {revenue} · profit {profit}"
    if reason.category == "SECTOR":
        match = re.search(r"relative sector return is ([+-]?[\d.]+)%", text)
        if match:
            value = float(match[1])
            return f"Stock {'ahead of' if value >= 0 else 'behind'} sector by {abs(value):.1f} pp"
    if text.startswith("Recent material news evidence is unavailable"):
        return "Limited recent news evidence"
    if text.startswith("The model uses available broad-market"):
        return "Market signals used; some economic data missing"
    return text


def reason_tone(reason):
    """An icon describes the displayed fact, not an omitted part of a factor group."""
    text = short_reason(reason)
    values = []
    if text.startswith("Price ") or text.startswith("Revenue "):
        values = [float(v) for v in re.findall(r"([+-][\d.]+)%", text)]
    elif text.startswith("Stock ahead of sector"):
        return "positive"
    elif text.startswith("Stock behind sector"):
        return "negative"
    if values:
        return "positive" if min(values) > 0 else "negative" if max(values) < 0 else "neutral"
    return {"POSITIVE": "positive", "NEGATIVE": "negative", "NEUTRAL": "neutral"}[reason.impact]


def short_risk(text):
    mappings = (("Recent coverage indicates an upcoming", "Upcoming earnings may move the price"),
                ("Technical and fundamental signals conflict", "Business and price signals disagree"),
                ("Prices are stale", "Prices are out of date"),
                ("Low or unknown liquidity", "Limited or unknown trading activity"),
                ("Volatility is outside", "Large price swings"),
                ("Unscheduled earnings", "Unexpected company news"),
                ("Insufficient or excessively stale", "Not enough recent price history"),
                ("This heuristic has no measured", "Forecast accuracy has not been calibrated"))
    return next((short for prefix, short in mappings if text.startswith(prefix)), text)


def one_line(text, tone="neutral"):
    return f'<li class="rd-reason"><span aria-hidden="true">{ICONS[tone]}</span><span title="{esc(text)}">{esc(text)}</span></li>'


def risk_level(report, outlook):
    """Conservative screening flag, not a probability or a personal risk assessment."""
    if not outlook or outlook.data_quality.status in ("STALE", "INSUFFICIENT"):
        return "Unknown", "unknown", "Fresh data is needed to assess risk"
    vol = number(data(report, "market").get("volatility_1y"))
    drawdown = number(data(report, "market").get("max_drawdown_1y"))
    risks = " ".join(r for o in outlook.outlooks for r in o.risks)
    if any(x in risks for x in ("gap risk is elevated", "Low or unknown liquidity", "outside the baseline")) or (vol is not None and vol > 40) or (drawdown is not None and drawdown < -35):
        return "High", "negative", "Large price swings, liquidity or event flags"
    if vol is None:
        return "Unknown", "unknown", "Not enough price-risk data"
    if outlook.data_quality.status != "GOOD" or "calendar" in " ".join(outlook.data_quality.warnings):
        return "Medium", "neutral", "Some data or upcoming events are unverified"
    return ("Low", "positive", "Lower observed volatility; losses remain possible") if vol < 20 and drawdown is not None and drawdown > -15 else ("Medium", "neutral", "Normal price and company-event uncertainty")


def _section(number_, name, content, note=""):
    ident = name.lower().replace(" ", "-")
    return (f'<section class="rd-section" id="rd-{ident}" aria-labelledby="rd-title-{ident}">'
            f'<header class="rd-section-head"><h2 id="rd-title-{ident}"><span>{number_:02d}</span>{esc(name)}</h2>'
            f'{f"<small>{esc(note)}</small>" if note else ""}</header>{content}</section>')


def _snapshot(report, outlook):
    fund, market = data(report, "fundamentals"), data(report, "market")
    valuation = fund.get("valuation") or {}
    current = outlook.current_price if outlook else number(market.get("price") or valuation.get("price"))
    currency = outlook.currency if outlook else "INR"
    timestamp = outlook.market_timestamp if outlook else market.get("price_date")
    industry = (report.get("industry") or "").split("›")
    sector, industry = industry[0].strip() or "Unavailable", industry[-1].strip() or "Unavailable"
    overall = next((o for o in outlook.outlooks if o.horizon == "ONE_MONTH"), None) if outlook else None
    direction = overall.movement.title() if overall and overall.movement else "Unavailable"
    direction_tone = {"Bullish": "positive", "Bearish": "negative", "Neutral": "neutral"}.get(direction, "unknown")
    score = number((report.get("scorecard") or {}).get("composite", {}).get(report["symbol"]))
    label, tone = grade(score)
    business_scores = [score_for(report, d) for d in ("Growth", "Profitability")]
    business = sum(business_scores) / 2 if all(s is not None for s in business_scores) else None
    business_text = {"Strong": "Strong business results versus peers", "Weak": "Business results lag peers",
                     "Neutral": "Business results are mixed versus peers", "Not enough data": "Business evidence is limited"}[grade(business)[0]]
    ending = {"Bullish": "the 1-month outlook points higher", "Bearish": "the 1-month outlook points lower",
              "Neutral": "the 1-month outlook is broadly flat", "Unavailable": "fresh data is needed for a forecast"}[direction]
    mcap = number(valuation.get("market_cap"))
    cap = f"₹{mcap / 100000:.2f} lakh cr" if mcap and mcap >= 100000 else f"₹{mcap:,.0f} cr" if mcap else "Unavailable"
    quality = outlook.data_quality.status if outlook else "INSUFFICIENT"
    quality_label = {"GOOD": "Fresh data", "PARTIAL": "Partial data", "STALE": "Stale data", "INSUFFICIENT": "Insufficient data"}[quality]
    warnings = outlook.data_quality.warnings if outlook else ["Outlook data is unavailable. Please retry the analysis."]
    content = (f'<div class="rd-snapshot"><div class="rd-company"><h1>{esc(report["name"])}</h1>'
               f'<span class="rd-muted">{esc(report["symbol"])} · {esc(outlook.exchange if outlook else "Indian equity")}</span></div>'
               f'<div class="rd-price"><small>Last close · {currency}</small><strong>{money(current, currency)}</strong></div></div>'
               f'<dl class="rd-meta"><div><dt>Market cap</dt><dd>{cap}</dd></div><div><dt>Sector</dt><dd>{esc(sector)}</dd></div>'
               f'<div><dt>Industry</dt><dd>{esc(industry)}</dd></div></dl>'
               f'<div class="rd-verdict"><span>Research score {badge(label, tone)} <small>vs peers</small></span>'
               f'<span>Overall Outlook {badge(direction, direction_tone)} <small>1 month</small></span></div>'
               f'<p class="rd-takeaway">{business_text}; {ending}.</p>'
               f'<div class="rd-freshness"><small>{esc(stamp(timestamp))}</small><details><summary>{"⚠ " if quality != "GOOD" else ""}{quality_label}</summary>'
               '<ul>' + ''.join(f'<li>{esc(w)}</li>' for w in warnings) + '</ul></details></div>')
    return _section(1, SECTION_NAMES[0], content)


def _outlook_card(o, currency):
    tone = {"BULLISH": "positive", "BEARISH": "negative", "NEUTRAL": "neutral"}.get(o.movement, "unknown")
    pieces = [f'<article class="rd-card rd-outlook-card"><header><h3>{LABELS[o.horizon]}</h3>{badge(o.movement.title() if o.movement else "Unavailable", tone)}</header>']
    if o.status == "AVAILABLE":
        p = o.price_range
        pieces += [f'<div class="rd-main-number rd-{tone}"><small>Expected return</small><strong>{pct(o.expected_return_percent)}</strong><small>median estimate</small></div>',
                   f'<div class="rd-confidence"><span>Confidence</span><strong>{o.confidence_score:.0f}/100</strong></div>',
                   f'<meter min="0" max="100" value="{o.confidence_score}" aria-label="{LABELS[o.horizon]} confidence">{o.confidence_score:.0f}/100</meter>',
                   '<dl class="rd-range" aria-label="Estimated price range">' + ''.join(f'<div><dt>{label}</dt><dd>{money(value, currency)}</dd></div>' for label, value in (("Lower", p.lower), ("Median", p.median), ("Upper", p.upper))) + '</dl>']
    else:
        pieces += ['<div class="rd-unavailable">Not enough recent price data.<br>Forecast withheld.</div>']
    pieces.append('<h4>Why</h4><ul class="rd-reasons">' + ''.join(one_line(short_reason(r), reason_tone(r)) for r in o.reasoning[:4]) + '</ul>')
    risk = short_risk(o.risks[0]) if o.risks else "Unexpected market changes"
    pieces.append(f'<div class="rd-key-risk" title="{esc(risk)}">⚠ <strong>Key risk</strong> · {esc(risk)}</div>')
    pieces.append('<details class="rd-evidence"><summary>Evidence & all risks</summary><ul>' + ''.join(f'<li><strong>{r.category.title()}:</strong> {esc(r.summary)}</li>' for r in o.reasoning) + '</ul><ul>' + ''.join(f'<li>{esc(r)}</li>' for r in o.risks) + '</ul></details></article>')
    return ''.join(pieces)


def _outlooks(outlook):
    if outlook:
        by_horizon = {o.horizon: o for o in outlook.outlooks}
        cards = ''.join(_outlook_card(by_horizon[h], outlook.currency) for h in LABELS)
    else:
        cards = ''.join(f'<article class="rd-card"><h3>{name}</h3>{badge("Unavailable", "unknown")}<p>Recent price data is needed.</p></article>' for name in LABELS.values())
    return _section(2, SECTION_NAMES[1], '<p class="rd-swipe">Swipe to compare all three periods →</p><div class="rd-outlook-grid">' + cards + '</div><small class="rd-muted">Estimates, not guarantees. Confidence is a model score, not a probability of profit.</small>')


def _alternative_card(a, rank):
    c = a.comparison_metrics
    content = (f'<article class="rd-card rd-alt-card"><header><span class="rd-rank">#{rank}</span><div><h3>{esc(a.symbol)}</h3>'
               f'<small>{esc(a.company_name)}</small></div></header><div class="rd-alt-stats"><div><small>Expected return</small>'
               f'<strong>{pct(a.expected_return_percent)}</strong></div><div><small>Confidence</small><strong>{a.confidence_score:.0f}/100</strong></div></div>'
               f'<h4>Why it ranks higher</h4><ul class="rd-reasons">{one_line(f"{c.return_difference_percent:+.1f} pp higher forecasted return", "positive")}'
               f'{one_line(f"{c.confidence_difference:+.1f} points higher confidence", "positive")}')
    # A reason's impact is absolute, so do not claim it is stronger than the target.
    if a.reasons:
        reason = next((r for r in a.reasons if r.impact == "POSITIVE"), a.reasons[0])
        content += one_line(short_reason(reason), reason_tone(reason))
    p = a.price_range
    content += (f'</ul><dl class="rd-range"><div><dt>Lower</dt><dd>{money(p.lower, a.currency)}</dd></div>'
                f'<div><dt>Median</dt><dd>{money(p.median, a.currency)}</dd></div><div><dt>Upper</dt><dd>{money(p.upper, a.currency)}</dd></div></dl>'
                f'<div class="rd-key-risk" title="{esc(short_risk(a.key_risk))}">⚠ {esc(short_risk(a.key_risk))}</div>'
                f'<details class="rd-evidence"><summary>Comparison details</summary><p>{esc(a.why_ranked_higher)}</p>'
                + '<ul>' + ''.join(f'<li>{esc(r.summary)}</li>' for r in a.reasons) + f'</ul><p>Key risk: {esc(a.key_risk)}</p></details></article>')
    return content


def _alternatives(outlook):
    # Native radio controls are accessible without JavaScript or another backend request.
    content = '<fieldset class="rd-alt-picker"><legend>Compare the same period</legend>'
    for horizon, label in LABELS.items():
        checked = ' checked' if horizon == "ONE_MONTH" else ''
        content += f'<input type="radio" name="rd-period" id="rd-{horizon}" value="{horizon}"{checked}><label for="rd-{horizon}">{label}</label>'
    for horizon, label in LABELS.items():
        candidates = [a for a in outlook.alternatives if a.comparison_horizon == horizon] if outlook else []
        # Preserve the forecast engine's risk-adjusted ordering.
        content += f'<div class="rd-alt-panel" data-horizon="{horizon}" role="region" aria-label="{label} alternatives">'
        if candidates:
            content += '<div class="rd-alt-grid">' + ''.join(_alternative_card(a, i + 1) for i, a in enumerate(candidates[:3])) + '</div>'
        else:
            content += '<div class="rd-empty"><strong>No stronger alternative found currently.</strong><span>No comparable stock passed every return, confidence, liquidity and data check for this period.</span></div>'
        content += '</div>'
    content += '</fieldset><small class="rd-muted">Higher forecasted return and confidence, with comparable risk. Returns are not assured.</small>'
    return _section(3, SECTION_NAMES[2], content, "If you like this stock, compare these first")


def _comparison(report):
    rows = comparison_rows(report)
    if len(rows) < 2:
        return _section(4, SECTION_NAMES[3], '<div class="rd-empty">Comparable company data is unavailable.</div>')
    content = '<div class="rd-peer-grid">'
    for row in rows:
        selected = row['symbol'] == report['symbol']
        rank = f'#{row["rank"]}' if row['rank'] else '—'
        content += f'<article class="rd-card rd-peer-card{" rd-selected" if selected else ""}"><header><span class="rd-rank">{rank}</span><div><h3>{esc(row["symbol"])}</h3><small>{"Current stock" if selected else esc(row["name"])}</small></div></header><dl class="rd-factor-list">'
        for dimension, value in row['scores'].items():
            label, tone = grade(value)
            content += f'<div><dt>{dimension}</dt><dd><span class="rd-{tone}">{ICONS[tone]} {label if value is not None else "No data"}</span><span class="rd-score">{f"{value:.0f}/100" if value is not None else "—"}</span></dd></div>'
        content += '</dl><ul class="rd-reasons">' + ''.join(one_line(text, tone) for tone, text in highlights(row, rows)) + '</ul>'
        content += f'<footer>Overall rank <strong>{rank}</strong></footer></article>'
    content += '</div><small class="rd-muted">Scores compare peers, not future returns. Overall rank uses all available factors. Ties share a rank.</small>'
    return _section(4, SECTION_NAMES[3], content, "Current stock + up to 3 industry peers")


def _checklist(report, outlook):
    financial = data(report, "fundamentals").get("is_financial")
    content = '<dl class="rd-checklist">'
    for label, dim in (("Growth", "Growth"), ("Profitability", "Profitability"), ("Balance Sheet", "Capital" if financial else "Balance sheet"), ("Momentum", "Momentum"), ("Valuation", "Valuation")):
        text, tone = grade(score_for(report, dim))
        content += f'<div><dt>{label}{"<small>Bank capital</small>" if financial and label == "Balance Sheet" else ""}</dt><dd class="rd-{tone}">{ICONS[tone]} {text}</dd></div>'
    level, tone, explanation = risk_level(report, outlook)
    content += f'<div><dt>Risk Level</dt><dd class="rd-{tone}" title="{esc(explanation)}">{ICONS[tone]} {level}</dd></div></dl>'
    content += f'<small class="rd-muted">{esc(explanation)}. Checklist strength is relative to peers.</small>'
    return _section(5, SECTION_NAMES[4], content)


def render(report):
    outlook = StockOutlook.model_validate(report["outlook"]) if report.get("outlook") else None
    return ('<style>' + CSS + '</style><div class="research-dashboard">' + _snapshot(report, outlook)
            + _outlooks(outlook) + _alternatives(outlook) + _comparison(report) + _checklist(report, outlook)
            + f'<p class="rd-disclaimer">{DISCLAIMER}</p></div>')


def loading_html():
    cards = ''.join(f'<article class="rd-card"><h3>{name}</h3><div class="rd-skeleton"></div><div class="rd-skeleton"></div><div class="rd-skeleton"></div></article>' for name in LABELS.values())
    return '<style>' + CSS + '</style><div class="research-dashboard" aria-busy="true" role="status"><p>Building your stock snapshot…</p>' + _section(2, SECTION_NAMES[1], '<div class="rd-outlook-grid">' + cards + '</div>') + '</div>'


def error_html(message="The analysis could not be completed. Please try again."):
    return '<style>' + CSS + f'</style><div class="research-dashboard rd-empty" role="alert"><strong>Analysis unavailable</strong><span>{esc(message)}</span><small>{DISCLAIMER}</small></div>'
