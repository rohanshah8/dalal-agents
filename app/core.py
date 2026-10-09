"""UI-independent glue shared by the Gradio and Streamlit front-ends.

* `analyze_stream()` is the single entry point: it yields ("progress", line) events and finally
  ("done", report_dict). It enforces the global concurrency cap and the caching / key policy:
  - no key  → quantitative pipeline, shared cache; outlooks add a versioned 15-minute refresh key;
  - key     → full pipeline with the visitor's LLM, never cached; the key is redacted from all output.
* The shared dashboard and Markdown builders present the same research in both UIs.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections import OrderedDict

from dalal_agents.config import Settings, safe_public_url
from dalal_agents.models import Report
from dalal_agents.orchestrator import research, write

MAX_CONCURRENT = int(os.environ.get("DALAL_MAX_CONCURRENT", "2"))
EXAMPLES = ["TCS", "HDFCBANK", "ASIANPAINT", "RELIANCE", "TITAN", "SUNPHARMA"]
DEFAULT_MODELS = {"Anthropic (Claude)": "claude-opus-5-5", "OpenAI-compatible": "gpt-4.1-mini"}
REPO = "https://github.com/rohanshah8/dalal-agents"
TAGLINE = ("An open-source finance research agent for Indian stocks — financials, price trends, "
           "earnings calls, news, and competitor comparisons with source facts and citations.")

_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT)


# ------------------------------------------------------------------ settings & safety
def base_settings(n_peers: int, n_concalls: int) -> Settings:
    s = Settings(llm_provider="none", n_peers=int(n_peers), n_concalls=int(n_concalls))
    if os.environ.get("DALAL_OFFLINE") == "1":
        s.offline = True
    return s


def llm_settings(n_peers, n_concalls, provider: str, key: str, model: str = "", base_url: str = "") -> Settings:
    s = base_settings(n_peers, n_concalls)
    if provider.startswith("Anthropic"):
        s.llm_provider, s.anthropic_api_key = "anthropic", key
        s.anthropic_base_url = os.environ.get("DALAL_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    else:
        s.llm_provider, s.openai_api_key = "openai", key
        s.openai_base_url = safe_public_url(base_url or "https://api.openai.com/v1")
    s.model = model or None
    return s


def redact(text, secret: str | None = None) -> str:
    text = str(text)
    if secret:
        text = text.replace(secret, "•••")
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+", r"\1•••", text)
    return text[:500]


def normalize_query(q: str) -> str:
    return " ".join(q.split()).upper()


# ------------------------------------------------------------------ cache
class ResearchCache:
    """Thread-safe TTL + LRU cache of key-free research results (plain dicts)."""

    def __init__(self, ttl_s: float = 6 * 3600, max_entries: int = 64):
        self.ttl_s, self.max_entries = ttl_s, max_entries
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return None
            ts, value = hit
            if time.time() - ts > self.ttl_s:
                del self._d[key]
                return None
            self._d.move_to_end(key)
            return value

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = (time.time(), value)
            self._d.move_to_end(key)
            while len(self._d) > self.max_entries:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = ResearchCache()


# ------------------------------------------------------------------ running
def run_in_thread(fn, *args):
    """Run fn(*args, progress=…) in a worker thread; yield ("progress", line) events, then return its result."""
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, progress=q.put)
        except BaseException as e:  # re-raised in the caller's thread
            box["error"] = e
        finally:
            q.put(None)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    while (msg := q.get()) is not None:
        yield "progress", msg
    t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def analyze_stream(query: str, n_peers: int = 4, n_concalls: int = 2, llm: dict | None = None,
                   cache: ResearchCache | None = None):
    """Yield ("queued"|"progress", text) events, then ("done", report_dict)."""
    cache = CACHE if cache is None else cache
    query = (query or "").strip()
    if not query:
        raise LookupError("Type a company name, NSE symbol or BSE code.")
    if not _SLOTS.acquire(blocking=False):
        yield "queued", "The server is busy with other analyses — waiting for a free slot…"
        _SLOTS.acquire()
    try:
        t0 = time.time()
        key = (llm or {}).get("key") or None
        if key:  # per-visitor and uncached: the LLM also reads concalls/news and filters competitors
            settings = llm_settings(n_peers, n_concalls, **llm)
            base = yield from run_in_thread(research, query, settings)
            base = base.model_dump(mode="json")
        else:
            settings = base_settings(n_peers, n_concalls)
            from dalal_agents.outlook.config import ForecastConfig
            outlook_config = ForecastConfig.load(settings.outlook_config_file)
            outlook_key = (int(time.time() // outlook_config.cache_seconds), outlook_config.version) if settings.outlook_enabled else None
            ck = (normalize_query(query), int(n_peers), int(n_concalls), outlook_key)
            base = cache.get(ck)
            if base is not None:
                yield "progress", "⚡ Research served from cache (outlooks refreshed within 15 minutes by default)."
            else:
                r = yield from run_in_thread(research, query, settings)
                base = r.model_dump(mode="json")
                cache.put(ck, base)
        out = yield from run_in_thread(write, Report.model_validate(base), settings)
        dumped = json.dumps(out.model_dump(mode="json"), ensure_ascii=False, default=str)
        if key and key in dumped:  # defense in depth: never echo a key back
            dumped = dumped.replace(key, "•••")
        yield "progress", f"Done in {time.time() - t0:.0f}s"
        yield "done", json.loads(dumped)
    finally:
        _SLOTS.release()


# ------------------------------------------------------------------ formatting
def fmt_progress(line: str) -> str:
    s = line.strip()
    if s.startswith(("✓", "◐", "✗")):
        mark, rest = s[0], s[1:].strip()
        icon = {"✓": "✅", "◐": "🟡", "✗": "❌"}[mark]
        name, _, detail = rest.partition(" ")
        return f"{icon} **{name}** {detail.strip()}"
    return f"**{s}**" if s.startswith(("Stage", "Done")) else s


def sections(md: str) -> dict[str, str]:
    """Split a rendered report into {section title: body} so tabs reuse the verified report text."""
    out, cur, buf = {}, "_head", []
    for line in md.splitlines():
        if line.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


def clean_md(text: str | None) -> str:
    """Open <details> blocks and render citation IDs as subscripts (both UIs allow this HTML)."""
    if not text:
        return ""
    text = re.sub(r"</?details>|<summary>(.*?)</summary>",
                  lambda m: f"\n**{m.group(1)}**\n" if m.group(1) else "", text)
    return re.sub(r"\[((?:[FE]\d+)(?:,\s*[FE]\d+)*)\]", r"<sub>[\1]</sub>", text)


def _pct(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}%"


def _x(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}x"


def _data(r: dict, name: str) -> dict:
    return (r["findings"].get(name) or {}).get("data") or {}


def kpis(r: dict) -> list[tuple[str, str, str | None]]:
    fund, mkt = _data(r, "fundamentals"), _data(r, "market")
    val = fund.get("valuation", {})
    fin = fund.get("is_financial")
    rel = mkt.get("rel_ret_1y")
    return [("P/E", _x(val.get("pe")), None), ("P/B", _x(val.get("pb")), None),
            ("ROA" if fin else "ROCE", _pct(fund.get("roa") if fin else fund.get("roce"), 2 if fin else 1), None),
            ("ROE", _pct(fund.get("roe")), None),
            ("GNPA" if fin else "OPM", _pct(fund.get("gnpa") if fin else fund.get("opm"), 2 if fin else 1), None),
            ("Sales CAGR 5y", _pct(fund.get("sales_cagr_5y")), None),
            ("1Y return", _pct(mkt.get("ret_1y")), None if rel is None else f"{rel:+.1f} pp vs Nifty")]


def header_line(r: dict) -> str:
    fund, mkt = _data(r, "fundamentals"), _data(r, "market")
    val = fund.get("valuation", {})
    price = mkt.get("price") or val.get("price")
    bits = [r.get("industry") or "", f"₹{price:,.2f}" if price else "",
            f"M-cap ₹{val['market_cap']:,.0f} cr" if val.get("market_cap") else "",
            f"as of {mkt.get('price_date') or r['generated_at'][:10]}",
            f"engine: {r.get('model') or 'quantitative (no LLM)'}"]
    v = r.get("verification") or {}
    if v.get("sentences"):
        bits.append(f"verifier {v['passed']}/{v['sentences']} sentences ✓")
    return " · ".join(b for b in bits if b)


def notices(r: dict, secret: str | None = None) -> list[tuple[str, str]]:
    """User-facing (level, text) notices derived from report warnings. level ∈ info|warning."""
    out = []
    for w in r.get("warnings", []):
        if "No LLM configured" in w:
            out.append(("info", "Quantitative mode: all numbers, technicals and the peer scorecard are computed; "
                                "the narrative is template-written. Add your own API key for the AI analyst narrative."))
        elif "LLM writer failed" in w:
            hint = ("The API key was rejected — check it and the provider." if re.search(r"\b40[13]\b", w)
                    else "The AI provider call failed.")
            out.append(("warning", f"{hint} Showing the quantitative report with a template narrative instead. "
                                   f"Details: {redact(w, secret)}"))
        else:
            out.append(("warning", w))
    return out


def edges_md(r: dict) -> str:
    sc = r.get("scorecard") or {}
    if not (sc.get("edges") or sc.get("gaps")):
        return ""
    L = ["#### Edge vs competitors"]
    for e in sc.get("edges", []):
        lead = " · leads peer set" if e.get("leader") is True else ""
        L.append(f"- ✅ **Edge · {e['dimension']}**: score {e['score']:.0f}/100{lead}")
    for g in sc.get("gaps", []):
        L.append(f"- ⚠️ **Gap · {g['dimension']}**: score {g['score']:.0f}/100; "
                 f"leader {g.get('leader')} ({g.get('leader_score', 0):.0f})")
    if sc.get("rank"):
        L.append(f"\n_Composite rank {sc['rank']} of {sc.get('n_companies')}_")
    return "\n".join(L)


def concalls_md(r: dict) -> str:
    calls = _data(r, "concall").get("calls") or []
    L = []
    for c in calls:
        ext = c.get("extraction") or {}
        L.append(f"#### Concall {c.get('date')} · tone {c['tone']['net_tone']:+.2f}")
        if c.get("url"):
            L.append(f"[Transcript PDF]({c['url']})")
        if ext.get("one_line_takeaway"):
            L.append(f"\n**Takeaway:** {ext['one_line_takeaway']}")
        if ext.get("guidance"):
            L.append("\n**Guidance**")
            for g in ext["guidance"]:
                L.append(f"- **{g.get('topic', '').title()}**: {g.get('statement')}  \n"
                         f"  <small>“{g.get('quote', '')}”</small>")
        for k in ("growth_drivers", "capex_or_investment", "new_initiatives", "risks", "analyst_concerns"):
            items = ext.get(k) or []
            if items:
                L.append(f"\n**{k.replace('_', ' ').title()}**")
                L += [f"- {it.get('statement') if isinstance(it, dict) else it}" for it in items]
        L.append("")
    return "\n".join(L)


def headlines_md(r: dict, limit: int = 30) -> str:
    items = _data(r, "news").get("news") or []
    if not items:
        return ""
    L = ["#### Recent headlines"]
    for n in items[:limit]:
        tone = n.get("tone") or 0
        dot = "🟢" if tone > 0.15 else "🔴" if tone < -0.15 else "⚪"
        L.append(f"- {dot} [{n.get('title')}]({n.get('url')}) <small>{n.get('publisher') or ''} · "
                 f"{n.get('date', '')}</small>")
    return "\n".join(L)


def competition_body(sec: dict) -> str:
    """Peer section without the scorecard table (UIs show it as a heatmap instead)."""
    body = sec.get("Competitive landscape & edge", "")
    return re.sub(r"### Edge scorecard.*?(?=\n\*\*Edges|\Z)", "", body, flags=re.S)
