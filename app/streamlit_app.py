"""Dalal Agents — web UI.

Run locally:   pip install -e ".[web]" && streamlit run app/streamlit_app.py
Hosted:        Hugging Face Spaces (Docker), see Dockerfile.

Design notes
* Without a key, the full quantitative pipeline runs and its result is cached for everyone for 6 h,
  keyed only by (symbol query, peers, concalls). Popular stocks are instant and we don't re-hit sources.
* With a key, the whole pipeline runs with the visitor's LLM (concall reading, news themes, competitor
  relevance filter and the grounded narrative) and is never cached. Keys live only in session_state →
  a fresh Settings object; they are never cached, logged or written to disk.
* Agents run in worker threads; their progress messages travel over a Queue and are rendered by the
  script thread (Streamlit elements must not be touched from other threads).
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import charts` when run from repo root

import charts  # noqa: E402

from dalal_agents import __version__  # noqa: E402
from dalal_agents.config import Settings, safe_public_url  # noqa: E402
from dalal_agents.models import Report  # noqa: E402
from dalal_agents.orchestrator import research, write  # noqa: E402
from dalal_agents.report import DISCLAIMER, render_html, render_markdown  # noqa: E402

MAX_CONCURRENT = int(os.environ.get("DALAL_MAX_CONCURRENT", "2"))
EXAMPLES = ["TCS", "HDFCBANK", "ASIANPAINT", "RELIANCE", "TITAN", "SUNPHARMA"]
DEFAULT_MODELS = {"Anthropic (Claude)": "claude-opus-5-5", "OpenAI-compatible": "gpt-4.1-mini"}
REPO = "https://github.com/rohanshah8/dalal-agents"

st.set_page_config(page_title="Dalal Agents · AI equity research for Indian stocks", page_icon="🐂",
                   layout="wide", menu_items={"About": f"Dalal Agents v{__version__} · {REPO}"})


@st.cache_resource
def _slots() -> threading.BoundedSemaphore:
    """Process-wide cap on concurrent analyses (shared across all sessions)."""
    return threading.BoundedSemaphore(MAX_CONCURRENT)


# ------------------------------------------------------------------ pipeline wrappers
def _base_settings(n_peers: int, n_concalls: int) -> Settings:
    s = Settings(llm_provider="none", n_peers=n_peers, n_concalls=n_concalls)
    if os.environ.get("DALAL_OFFLINE") == "1":
        s.offline = True
    return s


def _run_in_thread(fn, *args):
    """Run fn(*args, progress=q.put) in a worker thread; yield progress lines, then return the result."""
    q: queue.Queue = queue.Queue()
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, progress=q.put)
        except BaseException as e:  # surfaced to the UI below
            box["error"] = e
        finally:
            q.put(None)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    while True:
        msg = q.get()
        if msg is None:
            break
        yield msg
    t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def _drain(gen, status) -> object:
    """Write each progress line into the status box; return the generator's return value."""
    try:
        while True:
            line = next(gen)
            status.write(_fmt_progress(line))
            if line.startswith(("Stage", "Found")):
                status.update(label=line.strip()[:90])
    except StopIteration as stop:
        return stop.value


def _fmt_progress(line: str) -> str:
    s = line.strip()
    if s.startswith(("✓", "◐", "✗")):
        mark, rest = s[0], s[1:].strip()
        icon = {"✓": "✅", "◐": "🟡", "✗": "❌"}[mark]
        name, _, detail = rest.partition(" ")
        return f"{icon} **{name}** {detail.strip()}"
    return f"**{s}**" if s.startswith("Stage") else s


@st.cache_data(ttl=6 * 3600, max_entries=64, show_spinner=False)
def _cached_research(query: str, n_peers: int, n_concalls: int, _progress=None) -> dict:
    """Key-free research, shared by all visitors. Returned as a dict so the cache stores plain data."""
    r = research(query, _base_settings(n_peers, n_concalls), progress=_progress)
    return r.model_dump(mode="json")


def _research_with_progress(query, n_peers, n_concalls, progress):
    return _cached_research(" ".join(query.split()).upper(), n_peers, n_concalls, _progress=progress)


def _llm_settings(n_peers, n_concalls, provider, key, model, base_url) -> Settings:
    s = _base_settings(n_peers, n_concalls)
    if provider.startswith("Anthropic"):
        s.llm_provider, s.anthropic_api_key = "anthropic", key
        s.anthropic_base_url = os.environ.get("DALAL_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    else:
        s.llm_provider, s.openai_api_key = "openai", key
        s.openai_base_url = safe_public_url(base_url or "https://api.openai.com/v1")
    s.model = model or None
    return s


def _redact(text: str, secret: str | None) -> str:
    text = str(text)
    if secret:
        text = text.replace(secret, "•••")
    text = re.sub(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+", r"\1•••", text)
    return text[:500]


def run_analysis(query: str, n_peers: int, n_concalls: int, llm: dict | None) -> dict:
    slots = _slots()
    if not slots.acquire(blocking=False):
        with st.spinner("The server is busy with other analyses — you're in the queue…"):
            slots.acquire()
    try:
        with st.status(f"Analysing {query}…", expanded=True) as status:
            t0 = time.time()
            if llm:  # per-visitor, uncached: the LLM also reads concalls/news and filters competitors
                settings = _llm_settings(n_peers, n_concalls, **llm)
                base = _drain(_run_in_thread(research, query, settings), status).model_dump(mode="json")
            else:
                settings = _base_settings(n_peers, n_concalls)
                base = _drain(_run_in_thread(_research_with_progress, query, n_peers, n_concalls), status)
                if time.time() - t0 < 1.5:
                    status.write("⚡ Research served from cache (refreshed every 6 h).")
            out = _drain(_run_in_thread(write, Report.model_validate(base), settings), status)
            status.update(label=f"Done in {time.time() - t0:.0f}s", state="complete", expanded=False)
        dumped = json.dumps(out.model_dump(mode="json"), ensure_ascii=False, default=str)
        if llm and llm.get("key") and llm["key"] in dumped:  # defense in depth: never echo a key back
            dumped = dumped.replace(llm["key"], "•••")
        return json.loads(dumped)
    finally:
        slots.release()


# ------------------------------------------------------------------ rendering helpers
def _sections(md: str) -> dict[str, str]:
    """Split the rendered report into {"## title": body} so tabs reuse the verified report text."""
    out, cur, buf = {}, "_head", []
    for line in md.splitlines():
        if line.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = line[3:].strip(), []
        else:
            buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


def _md(text: str | None):
    if not text:
        return
    # Streamlit renders <details> poorly inside markdown; open them up and make citations subtle.
    text = re.sub(r"</?details>|<summary>(.*?)</summary>", lambda m: f"\n**{m.group(1)}**\n" if m.group(1) else "",
                  text)
    text = re.sub(r"\[((?:[FE]\d+)(?:,\s*[FE]\d+)*)\]", r"<sub>[\1]</sub>", text)
    st.markdown(text, unsafe_allow_html=True)


def _chart(fig):
    if fig is not None:
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _pct(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}%"


def _x(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}x"


def kpis(r: dict):
    f = r["findings"]
    fund = (f.get("fundamentals") or {}).get("data", {})
    val = fund.get("valuation", {})
    mkt = (f.get("market") or {}).get("data", {})
    fin = fund.get("is_financial")
    rel = mkt.get("rel_ret_1y")
    items = [("P/E", _x(val.get("pe")), None), ("P/B", _x(val.get("pb")), None),
             ("ROA" if fin else "ROCE", _pct(fund.get("roa") if fin else fund.get("roce"), 2 if fin else 1), None),
             ("ROE", _pct(fund.get("roe")), None),
             ("GNPA" if fin else "OPM", _pct(fund.get("gnpa") if fin else fund.get("opm"), 2 if fin else 1), None),
             ("Sales CAGR 5y", _pct(fund.get("sales_cagr_5y")), None),
             ("1Y return", _pct(mkt.get("ret_1y")), None if rel is None else f"{rel:+.1f} pp vs Nifty")]
    for col, (k, v, d) in zip(st.columns(len(items)), items):
        col.metric(k, v, d)


def header(r: dict):
    f = r["findings"]
    fund = (f.get("fundamentals") or {}).get("data", {})
    val = fund.get("valuation", {})
    mkt = (f.get("market") or {}).get("data", {})
    st.markdown(f"## {r['name']} · `{r['symbol']}`")
    price = mkt.get("price") or val.get("price")
    bits = [r.get("industry") or "", f"₹{price:,.2f}" if price else "",
            f"M-cap ₹{val['market_cap']:,.0f} cr" if val.get("market_cap") else "",
            f"as of {mkt.get('price_date') or r['generated_at'][:10]}",
            f"engine: {r.get('model') or 'quantitative (no LLM)'}"]
    v = r.get("verification") or {}
    if v.get("sentences"):
        bits.append(f"verifier {v['passed']}/{v['sentences']} sentences ✓")
    st.caption(" · ".join(b for b in bits if b))
    kpis(r)


def tab_summary(r, sec):
    sc = r.get("scorecard") or {}
    c1, c2 = st.columns([3, 2])
    with c1:
        _md(sec.get("Executive summary") or "_No AI narrative for this run — add an API key in the sidebar._")
        for t in ("Bull case", "Bear case", "What to monitor"):
            if sec.get(t):
                st.markdown(f"#### {t}")
                _md(sec[t])
    with c2:
        if sc.get("edges") or sc.get("gaps"):
            st.markdown("#### Edge vs competitors")
            for e in sc.get("edges", []):
                lead = " · leads peer set" if e.get("leader") is True else ""
                st.success(f"**Edge · {e['dimension']}** — score {e['score']:.0f}/100{lead}")
            for g in sc.get("gaps", []):
                st.warning(f"**Gap · {g['dimension']}** — score {g['score']:.0f}/100; "
                           f"leader {g.get('leader')} ({g.get('leader_score', 0):.0f})")
            if sc.get("rank"):
                st.caption(f"Composite rank {sc['rank']} of {sc.get('n_companies')}")
        _chart(charts.composite_bar(r))
        if sec.get("Business overview"):
            with st.expander("Business overview", expanded=False):
                _md(sec["Business overview"])


def tab_competition(r, sec):
    _chart(charts.scorecard_heatmap(r))
    st.caption("Percentile ranks within this peer set (0 = worst, 100 = best). They measure relative quality, "
               "not attractiveness as an investment. Small peer sets give coarse scores.")
    body = sec.get("Competitive landscape & edge", "")
    body = re.sub(r"### Edge scorecard.*?(?=\n\*\*Edges|\Z)", "", body, flags=re.S)  # heatmap replaces table
    _md(body)


def tab_price(r, sec):
    c1, c2 = st.columns(2)
    with c1:
        _chart(charts.price_vs_peers(r))
    with c2:
        _chart(charts.price_with_smas(r))
    _md(sec.get("Price & trend"))


def tab_financials(r, sec):
    c1, c2 = st.columns(2)
    with c1:
        _chart(charts.annual_financials(r))
    with c2:
        _chart(charts.quarterly_results(r))
    _md(sec.get("Financial performance"))
    _md("### Balance sheet & cash-flow quality\n\n" + sec.get("Balance sheet & cash-flow quality", ""))
    _md("### Valuation\n\n" + sec.get("Valuation (what the price implies)", ""))


def tab_ownership(r, sec):
    _chart(charts.shareholding(r))
    _md(sec.get("Ownership"))


def tab_management(r, sec):
    _md(sec.get("Future plans & management commentary") or "_No management commentary available._")
    calls = ((r["findings"].get("concall") or {}).get("data") or {}).get("calls") or []
    for c in calls:
        ext = c.get("extraction") or {}
        with st.expander(f"Concall {c.get('date')} · tone {c['tone']['net_tone']:+.2f}", expanded=False):
            if c.get("url"):
                st.markdown(f"[Transcript PDF]({c['url']})")
            for g in ext.get("guidance", []):
                st.markdown(f"- **{g.get('topic', '').title()}** — {g.get('statement')}  \n"
                            f"  <small>“{g.get('quote', '')}”</small>", unsafe_allow_html=True)
            if ext.get("one_line_takeaway"):
                st.markdown(f"**Takeaway:** {ext['one_line_takeaway']}")
            for k in ("growth_drivers", "capex_or_investment", "new_initiatives", "risks", "analyst_concerns"):
                items = ext.get(k) or []
                if items:
                    st.markdown(f"**{k.replace('_', ' ').title()}**")
                    for it in items:
                        st.markdown(f"- {it.get('statement') if isinstance(it, dict) else it}")


def tab_news(r, sec):
    _md(sec.get("News flow & sentiment") or "_No news summary._")
    news = ((r["findings"].get("news") or {}).get("data") or {})
    items = news.get("news") or []
    if items:
        st.markdown("#### Recent headlines")
        for n in items[:30]:
            tone = n.get("tone")
            dot = "🟢" if (tone or 0) > 0.15 else "🔴" if (tone or 0) < -0.15 else "⚪"
            src = n.get("publisher") or ""
            st.markdown(f"{dot} [{n.get('title')}]({n.get('url')}) <small>{src} · {n.get('date', '')}</small>",
                        unsafe_allow_html=True)


def tab_full(r, md):
    st.download_button("⬇️ Markdown", md, file_name=f"{r['symbol']}_{r['generated_at'][:10]}.md")
    _md(md)


def show_report(r: dict):
    rep = Report.model_validate(r)
    md = render_markdown(rep)
    sec = _sections(md)
    header(r)
    for w in r.get("warnings", []):
        if "No LLM configured" in w:
            st.info("Quantitative mode: all numbers, technicals and the peer scorecard are computed; the narrative "
                    "is template-written. Add your own API key in the sidebar for the AI analyst narrative.")
        elif "LLM writer failed" in w:
            hint = ("The API key was rejected — check it and the provider." if re.search(r"\b40[13]\b", w)
                    else "The AI provider call failed.")
            st.warning(f"{hint} Showing the quantitative report with a template narrative instead.")
            with st.expander("Error details"):
                st.code(_redact(w, st.session_state.get("api_key")))
        else:
            st.warning(w)
    d1, d2, d3, _ = st.columns([1, 1, 1, 5])
    stem = f"{r['symbol']}_{r['generated_at'][:10]}"
    d1.download_button("⬇️ HTML", render_html(md, f"{r['name']} — Dalal Agents"), file_name=f"{stem}.html",
                       mime="text/html")
    d2.download_button("⬇️ Markdown", md, file_name=f"{stem}.md", mime="text/markdown", key="md_top")
    d3.download_button("⬇️ JSON", json.dumps(r, indent=2, ensure_ascii=False, default=str),
                       file_name=f"{stem}.json", mime="application/json")
    tabs = st.tabs(["📋 Summary", "🥊 Competitors & edge", "📈 Price & trend", "📊 Financials", "👥 Ownership",
                    "🎙️ Management & plans", "📰 News", "📄 Full report & sources"])
    with tabs[0]:
        tab_summary(r, sec)
    with tabs[1]:
        tab_competition(r, sec)
    with tabs[2]:
        tab_price(r, sec)
    with tabs[3]:
        tab_financials(r, sec)
    with tabs[4]:
        tab_ownership(r, sec)
    with tabs[5]:
        tab_management(r, sec)
    with tabs[6]:
        tab_news(r, sec)
    with tabs[7]:
        tab_full(r, md)


# ------------------------------------------------------------------ page
def sidebar() -> tuple[str | None, int, int, dict | None]:
    sb = st.sidebar
    sb.markdown("# 🐂 Dalal Agents")
    sb.caption("AI research analysts for Indian stocks · open source")
    query = sb.text_input("Company", key="query", placeholder="TCS, 500325, or 'hdfc bank'",
                          help="NSE symbol, BSE code or company name")
    cols = sb.columns(3)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 3].button(ex, key=f"ex_{ex}", width="stretch"):
            st.session_state["pending_query"] = ex
    n_peers = sb.slider("Competitors to analyse", 2, 6, 4)
    n_concalls = sb.slider("Earnings-call transcripts to read", 0, 3, 2,
                           help="Transcripts are read by the LLM; ignored in quantitative mode.")
    with sb.expander("🤖 AI narrative (bring your own key)", expanded=False):
        provider = st.radio("Provider", list(DEFAULT_MODELS), horizontal=False)
        key = st.text_input("API key", type="password", key="api_key",
                            help="Used only for this run, held in memory, never logged or stored.")
        model = st.text_input("Model", value=DEFAULT_MODELS[provider], key=f"model_{provider}")
        base_url = ""
        if provider == "OpenAI-compatible":
            base_url = st.text_input("Base URL", value="https://api.openai.com/v1",
                                     help="Any OpenAI-compatible endpoint: OpenAI, Groq, OpenRouter, Together…")
        st.caption("No key? You still get every number, chart and the peer scorecard.")
    go_clicked = sb.button("🔍 Analyse", type="primary", width="stretch")
    sb.markdown(f"---\n[GitHub]({REPO}) · [Methodology]({REPO}/blob/main/docs/DESIGN.md) · v{__version__}")
    pending = st.session_state.pop("pending_query", None)
    chosen = pending or (query.strip() if go_clicked and query.strip() else None)
    llm = {"provider": provider, "key": key.strip(), "model": model.strip(), "base_url": base_url.strip()} \
        if key.strip() else None
    return chosen, n_peers, n_concalls, llm


def landing():
    st.markdown("# 🐂 Dalal Agents")
    st.markdown("#### AI research analysts for Indian stocks — fundamentals, trends, concalls, news and "
                "**how a company stacks up against its competitors**, with every number cited.")
    c1, c2, c3 = st.columns(3)
    c1.markdown("**1 · Research.** 7 agents fetch 12 years of financials, price trends vs Nifty, shareholding, "
                "filings, earnings-call transcripts and news.")
    c2.markdown("**2 · Compare.** Competitors are discovered automatically and put through the same maths, "
                "then ranked on growth, profitability, balance sheet, cash quality, valuation and momentum.")
    c3.markdown("**3 · Explain.** An LLM writes the analyst narrative using only cited facts; a verifier checks "
                "every number against its source.")
    st.info("👈 Type a company or pick an example in the sidebar. A fresh analysis takes about 1–2 minutes.")
    st.caption(DISCLAIMER)


def main():
    chosen, n_peers, n_concalls, llm = sidebar()
    if chosen:
        if st.session_state.get("running"):
            st.warning("An analysis is already running in this session.")
        else:
            st.session_state["running"] = True
            try:
                st.session_state["report"] = run_analysis(chosen, n_peers, n_concalls, llm)
                st.session_state.pop("error", None)
            except (LookupError, ValueError) as e:
                st.session_state["error"] = _redact(e, (llm or {}).get("key"))
            except Exception as e:
                st.session_state["error"] = "Analysis failed: " + _redact(e, (llm or {}).get("key"))
            finally:
                st.session_state["running"] = False
    if st.session_state.get("error"):
        st.error(st.session_state["error"])
    if st.session_state.get("report"):
        show_report(st.session_state["report"])
        st.caption(DISCLAIMER)
    elif not st.session_state.get("error"):
        landing()


main()
