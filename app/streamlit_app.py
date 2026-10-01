"""Dalal Agents — Streamlit web UI (local / self-hosted; the public Space uses app/gradio_app.py).

Run locally:   pip install -e ".[web]" && streamlit run app/streamlit_app.py
Pipeline, caching and key policy live in app/core.py and are shared with the Gradio UI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import charts, core` when run from repo root

import charts  # noqa: E402
import core  # noqa: E402

from dalal_agents import __version__  # noqa: E402
from dalal_agents.models import Report  # noqa: E402
from dalal_agents.report import DISCLAIMER, render_html, render_markdown  # noqa: E402

EXAMPLES, DEFAULT_MODELS, REPO = core.EXAMPLES, core.DEFAULT_MODELS, core.REPO

st.set_page_config(page_title="Dalal Agents · AI equity research for Indian stocks", page_icon="🐂",
                   layout="wide", menu_items={"About": f"Dalal Agents v{__version__} · {REPO}"})


def run_analysis(query: str, n_peers: int, n_concalls: int, llm: dict | None) -> dict:
    with st.status(f"Analysing {query}…", expanded=True) as status:
        for kind, payload in core.analyze_stream(query, n_peers, n_concalls, llm):
            if kind == "done":
                status.update(state="complete", expanded=False)
                return payload
            status.write(core.fmt_progress(payload))
            if payload.startswith(("Stage", "Found", "Done", "The server is busy")):
                status.update(label=payload.strip()[:90])
    raise RuntimeError("analysis ended without a result")


# ------------------------------------------------------------------ rendering helpers
def _md(text: str | None):
    if text:
        st.markdown(core.clean_md(text), unsafe_allow_html=True)


def _chart(fig):
    if fig is not None:
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def header(r: dict):
    st.markdown(f"## {r['name']} · `{r['symbol']}`")
    st.caption(core.header_line(r))
    for col, (k, v, d) in zip(st.columns(7), core.kpis(r)):
        col.metric(k, v, d)


def tab_summary(r, sec):
    c1, c2 = st.columns([3, 2])
    with c1:
        _md(sec.get("Executive summary") or "_No AI narrative for this run — add an API key in the sidebar._")
        for t in ("Bull case", "Bear case", "What to monitor"):
            if sec.get(t):
                st.markdown(f"#### {t}")
                _md(sec[t])
    with c2:
        _md(core.edges_md(r))
        _chart(charts.composite_bar(r))
        if sec.get("Business overview"):
            with st.expander("Business overview", expanded=False):
                _md(sec["Business overview"])


def tab_competition(r, sec):
    _chart(charts.scorecard_heatmap(r))
    st.caption("Percentile ranks within this peer set (0 = worst, 100 = best). They measure relative quality, "
               "not attractiveness as an investment. Small peer sets give coarse scores.")
    _md(core.competition_body(sec))


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
    _md(core.concalls_md(r))


def tab_news(r, sec):
    _md(sec.get("News flow & sentiment") or "_No news summary._")
    _md(core.headlines_md(r))


def tab_full(r, md):
    st.download_button("⬇️ Markdown", md, file_name=f"{r['symbol']}_{r['generated_at'][:10]}.md")
    _md(md)


def show_report(r: dict):
    rep = Report.model_validate(r)
    md = render_markdown(rep)
    sec = core.sections(md)
    header(r)
    for level, text in core.notices(r, st.session_state.get("api_key")):
        (st.info if level == "info" else st.warning)(text)
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
    st.markdown(f"#### {core.TAGLINE}")
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
                st.session_state["error"] = core.redact(e, (llm or {}).get("key"))
            except Exception as e:
                st.session_state["error"] = "Analysis failed: " + core.redact(e, (llm or {}).get("key"))
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
