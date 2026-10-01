"""Synthesis agents: the grounded Writer and the deterministic Verifier."""
from __future__ import annotations

import re

from ..facts import FactStore

WRITER_SYSTEM = """You are a CFA charterholder writing an institutional-quality equity research note on an
Indian listed company for educated retail investors.

HARD RULES (a verifier will check them mechanically):
1. Use ONLY the facts [F#] and excerpts [E#] provided. Never use outside knowledge for numbers.
2. Every sentence that contains a number MUST end with the citation(s) it relies on, e.g. "… 27.1% [F12]."
   Copy numbers exactly as given (you may round to 1 decimal place).
3. Qualitative claims from news/transcripts must cite [E#].
4. NEVER give a recommendation: no buy/sell/hold/accumulate/target price/"undervalued"/"overvalued"
   verdicts. Describe what the data shows and what the market price implies.
5. If data is missing, say "data not available" instead of guessing.
6. Be specific, analytical and concise. Compare with peers using their facts. Use Indian conventions
   (₹ crore, FY = April–March)."""

WRITER_USER = """Company: {name} ({symbol}) · Industry: {industry} · Peers: {peers}

=== FACTS (target) ===
{facts_target}

=== FACTS (peers) ===
{facts_peers}

=== EXCERPTS (news, web, transcripts, filings) ===
{excerpts}

=== STRUCTURED NOTES ===
Scorecard edges: {edges}
Scorecard gaps: {gaps}
Concall extraction: {concall}
News themes: {news}

Write JSON with these keys (each value is Markdown prose, 80–220 words unless noted, all sentences cited):
{{
  "executive_summary": "5–7 bullet points ('- ' prefixed) covering business, growth, profitability, balance sheet, valuation-implied expectations, momentum and competitive position",
  "business_overview": "...",
  "financial_performance": "...",
  "balance_sheet_cash": "...",
  "valuation": "explain multiples and the reverse-DCF implied growth vs history; no verdict",
  "ownership": "...",
  "future_plans": "guidance, capex, initiatives, management tone (cite transcripts/news)",
  "news_sentiment": "...",
  "competitive_position": "where it leads and lags vs named peers, with numbers",
  "bull_case": "3–5 bullets",
  "bear_case": "3–5 bullets",
  "monitorables": "4–6 bullets: specific metrics/events to track next quarters"
}}"""

SECTIONS = ["executive_summary", "business_overview", "financial_performance", "balance_sheet_cash",
            "valuation", "ownership", "future_plans", "news_sentiment", "competitive_position",
            "bull_case", "bear_case", "monitorables"]

BANNED = [
    r"\bstrong buy\b", r"\bbuy\b(?! ?back)", r"\bsell\b(?![- ]?off|ing pressure| side)", r"\bhold\b(?:ing)?(?= (?:the|this) stock)",
    r"\baccumulate\b", r"\btarget price\b", r"\bprice target\b", r"\bundervalued\b", r"\bovervalued\b",
    r"\bmultibagger\b", r"\bmust[- ]buy\b", r"\bguaranteed returns?\b",
]
_CITE = re.compile(r"\[((?:[FE]\d+)(?:\s*,\s*[FE]\d+)*)\]")


def split_sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # split on ". " / "] " boundaries followed by a capital, but never inside parentheses
        parts, buf, depth = [], "", 0
        for i, ch in enumerate(line):
            buf += ch
            depth += ch == "("
            depth -= ch == ")" and depth > 0
            nxt = line[i + 1:i + 3]
            if depth == 0 and ch in ".!?]" and len(nxt) == 2 and nxt[0] == " " and (nxt[1].isupper() or nxt[1] in "₹*-"):
                if not re.search(r"\b(?:Rs|vs|approx|e\.g|i\.e|no|Ltd|Inc|Co)\.$", buf, re.I):
                    parts.append(buf.strip())
                    buf = ""
        if buf.strip():
            parts.append(buf.strip())
        out.extend(p for p in parts if p)
    return out


_STRIP_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO dates
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?(?:\s+\d{4})?\b",  # 1 Oct 2026
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?\b",  # Oct 8, 2026
    r"\b(?:FY|Q[1-4]|H[12])\s?'?\d{2,4}(?:-\d{2,4})?\b",  # FY27, Q1, H1, FY2025-26
    r"\b(?:Q[1-4]|H[12])\b",
    r"\b(?:19|20)\d\d(?:-\d{2,4})?\b",  # years 2026, 2025-26
    r"\b\d+\s?(?:-|–|to)?\s?(?:years?|yrs?|months?|days?|quarters?|weeks?)\b",  # 5-year, 3 months, 52 weeks
    r"\b\d+[ -]?(?:y|yr|m|d|q|w)\b",  # 5y, 1Y, 3m
    r"\b(?:Nifty|NIFTY|Sensex|SENSEX|BSE|NSE)\s?\d+\b",
    r"\b\d+[- ]?(?:DMA|SMA|EMA)\b|\b(?:SMA|EMA|DMA|RSI)\s?\(?\d+\)?",
    r"\b(?:top|rank(?:ed)?|of|among|out of|against|vs\.?|versus|all|these|the)\s+\d{1,2}\b(?!\.\d|\s*%)",
    r"\b\d{1,2}\s+(?:listed\s+)?(?:peers?|competitors?|companies|players|calls?|transcripts?|headlines?)\b",
    r"\b\d+(?:st|nd|rd|th)\b",
    r"\b\d+\s?/\s?100\b|/100\b",
    r"\b(?:Gen|GEN|Phase|Tier|Level|Plan)\s?\d+\b",
]


def _numbers_in(sentence: str) -> list[float]:
    s = _CITE.sub(" ", sentence)
    for pat in _STRIP_PATTERNS:
        s = re.sub(pat, " ", s, flags=re.I)
    nums = []
    for m in re.finditer(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", s):
        raw = m.group(0).replace(",", "")
        try:
            nums.append(float(raw))
        except ValueError:
            continue
    return nums


def _fact_values(store: FactStore, ids: list[str]) -> list[float]:
    vals = []
    for i in ids:
        f = store.facts.get(i)
        if f is not None and isinstance(f.value, (int, float)):
            v = float(f.value)
            vals.extend([v, abs(v)])
            if f.unit in ("x",) or f.unit == "":
                vals.append(v * 100)  # ratio quoted as percent
            if f.unit == "%":
                vals.append(v / 100)
        elif f is None:
            e = store.excerpts.get(i)
            if e is not None:
                for m in re.finditer(r"-?\d[\d,]*\.?\d*", e.text):
                    try:
                        vals.append(float(m.group(0).replace(",", "").rstrip(".")))
                    except ValueError:
                        pass
    return vals


def _matches(n: float, vals: list[float]) -> bool:
    for v in vals:
        if v == 0 and n == 0:
            return True
        if v == 0:
            continue
        tol = max(abs(v) * 0.015, 0.051 if abs(v) < 100 else 0.51)
        if abs(abs(n) - abs(v)) <= tol:
            return True
        # rounded representations: lakh crore / thousand crore
        for scale in (1e5, 1e3, 1e7):
            if abs(abs(n) * scale - abs(v)) <= abs(v) * 0.02:
                return True
    return False


def verify_text(text: str, store: FactStore) -> dict:
    """Return per-sentence verdicts and aggregate stats for one section."""
    results = []
    for s in split_sentences(text):
        cites = [c.strip() for grp in _CITE.findall(s) for c in grp.split(",")]
        unknown = [c for c in cites if store.get(c) is None]
        nums = _numbers_in(s)
        issues = []
        if unknown:
            issues.append(f"unknown citation(s): {', '.join(unknown)}")
        if nums and not cites:
            issues.append("number without citation")
        elif nums:
            vals = _fact_values(store, [c for c in cites if c not in unknown])
            bad = [n for n in nums if not _matches(n, vals)]
            if bad:
                issues.append("number(s) not found in cited facts: " + ", ".join(f"{b:g}" for b in bad[:4]))
        low = s.lower()
        banned = [b for b in BANNED if re.search(b, low)]
        if banned:
            issues.append("recommendation language")
        results.append({"sentence": s, "ok": not issues, "issues": issues, "n_numbers": len(nums),
                        "citations": cites})
    return {"sentences": results, "n": len(results), "ok": sum(1 for r in results if r["ok"])}


def verify_narrative(narrative: dict[str, str], store: FactStore, strict: bool = False) -> tuple[dict, dict]:
    """Verify every section. In strict mode failing sentences are removed; otherwise they are flagged ⚠."""
    stats = {"sentences": 0, "passed": 0, "numeric_sentences": 0, "numeric_passed": 0, "issues": []}
    cleaned: dict[str, str] = {}
    for key, text in narrative.items():
        if not isinstance(text, str):
            continue
        v = verify_text(text, store)
        stats["sentences"] += v["n"]
        stats["passed"] += v["ok"]
        out = text
        for r in v["sentences"]:
            if r["n_numbers"]:
                stats["numeric_sentences"] += 1
                stats["numeric_passed"] += int(r["ok"])
            if not r["ok"]:
                stats["issues"].append({"section": key, "sentence": r["sentence"][:300], "issues": r["issues"]})
                if strict:
                    out = out.replace(r["sentence"], "")
                else:
                    out = out.replace(r["sentence"], r["sentence"] + " ⚠")
        cleaned[key] = out.strip()
    stats["pass_rate"] = round(100 * stats["passed"] / stats["sentences"], 1) if stats["sentences"] else None
    stats["numeric_pass_rate"] = (round(100 * stats["numeric_passed"] / stats["numeric_sentences"], 1)
                                  if stats["numeric_sentences"] else None)
    return cleaned, stats
