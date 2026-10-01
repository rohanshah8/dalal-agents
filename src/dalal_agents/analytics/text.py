"""Deterministic text analytics: finance tone lexicon, keyword-window extraction, event tagging.

The lexicon is a compact finance-specific list in the spirit of Loughran & McDonald (2011):
generic sentiment words ("liability", "tax") are excluded because they are neutral in filings.
"""
from __future__ import annotations

import re

POSITIVE = {
    "growth", "grew", "strong", "robust", "record", "improve", "improved", "improvement", "expansion",
    "expand", "beat", "outperform", "outperformed", "upgrade", "upgraded", "momentum", "healthy",
    "accelerate", "accelerated", "acceleration", "resilient", "gain", "gains", "profitable", "win",
    "wins", "won", "order", "surge", "surged", "rally", "rallied", "jump", "jumped", "high", "highest",
    "optimistic", "confident", "opportunity", "opportunities", "tailwind", "tailwinds", "recovery",
    "positive", "upside", "rise", "rises", "rose", "boost", "boosted", "bullish", "dividend", "buyback",
    "milestone", "launch", "launched", "partnership", "approval", "approved", "deal", "deals",
}
NEGATIVE = {
    "decline", "declined", "declining", "weak", "weakness", "slowdown", "slow", "slower", "miss",
    "missed", "downgrade", "downgraded", "loss", "losses", "pressure", "pressures", "headwind",
    "headwinds", "fall", "fell", "falls", "drop", "dropped", "plunge", "plunged", "crash", "concern",
    "concerns", "risk", "risks", "uncertain", "uncertainty", "challenging", "challenge", "challenges",
    "volatile", "volatility", "default", "fraud", "penalty", "probe", "investigation", "layoff",
    "layoffs", "cut", "cuts", "delay", "delayed", "disappoint", "disappointing", "bearish", "low",
    "lowest", "slump", "slumped", "tumble", "tumbled", "sell-off", "selloff", "attrition", "litigation",
    "pledge", "resign", "resigned", "resignation", "stress", "stressed", "npa", "write-off", "impairment",
}
NEGATORS = {"not", "no", "never", "without", "less", "hardly"}

FORWARD_KEYWORDS = [
    "guidance", "guide", "outlook", "capex", "capital expenditure", "expansion", "new plant", "capacity",
    "order book", "order inflow", "pipeline", "target", "aspire", "aim to", "plan to", "we expect",
    "going forward", "next year", "fy27", "fy28", "fy 27", "fy 28", "medium term", "long term",
    "margin band", "acquisition", "launch", "deal wins", "tcv", "headcount", "hiring", "ai ",
]

EVENT_TAGS = {
    "Results": ["result", "q1", "q2", "q3", "q4", "quarter", "earnings", "profit", "revenue"],
    "Order/Deal": ["order", "contract", "deal", "wins", "bags", "secures", "partnership"],
    "Corporate action": ["dividend", "bonus", "split", "buyback", "rights issue"],
    "M&A": ["acquire", "acquisition", "merger", "stake", "takeover", "demerger"],
    "Regulatory/Legal": ["sebi", "rbi", "penalty", "court", "probe", "tax demand", "notice", "ban"],
    "Management": ["ceo", "cfo", "md ", "chairman", "appoint", "resign", "board"],
    "Rating/Broker": ["target price", "rating", "upgrade", "downgrade", "brokerage", "buy call"],
}

_WORD = re.compile(r"[a-z][a-z\-']+")


def tone(text: str) -> dict:
    """Net tone = (pos − neg) / (pos + neg) ∈ [−1, 1], with simple negation handling."""
    words = _WORD.findall(text.lower())
    pos = neg = 0
    for i, w in enumerate(words):
        negated = any(x in NEGATORS for x in words[max(0, i - 3):i])
        if w in POSITIVE:
            if negated:
                neg += 1
            else:
                pos += 1
        elif w in NEGATIVE:
            if negated:
                pos += 1
            else:
                neg += 1
    total = pos + neg
    return {"pos": pos, "neg": neg, "net_tone": (pos - neg) / total if total else 0.0, "n_words": len(words)}


def tag_events(title: str) -> list[str]:
    t = title.lower()
    return [tag for tag, kws in EVENT_TAGS.items() if any(k in t for k in kws)]


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z“\"(])", text)
    return [p.strip() for p in parts if 40 <= len(p.strip()) <= 600]


def forward_looking(text: str, max_items: int = 12) -> list[str]:
    """Pick sentences mentioning guidance/capex/plans — scored by keyword density and numbers."""
    scored = []
    for s in sentences(text):
        low = s.lower()
        hits = sum(1 for k in FORWARD_KEYWORDS if k in low)
        if not hits:
            continue
        has_num = bool(re.search(r"\d", s))
        scored.append((hits + (1 if has_num else 0), s))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, s in scored:
        k = s[:60].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def split_transcript(text: str) -> tuple[str, str]:
    """Split a concall transcript into (management remarks, Q&A)."""
    m = re.search(r"(question[- ]and[- ]answer|we will now begin the question|first question is from|"
                  r"open the floor for questions|begin the q&a)", text, re.I)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]
