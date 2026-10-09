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
import dashboard  # noqa: E402

from dalal_agents import __version__  # noqa: E402
from dalal_agents.models import Report  # noqa: E402
from dalal_agents.outlook.render import render_html as render_outlook
from dalal_agents.report import DISCLAIMER, render_html, render_markdown  # noqa: E402

EXAMPLES, DEFAULT_MODELS, REPO = core.EXAMPLES, core.DEFAULT_MODELS, core.REPO

st.set_page_config(page_title="Dalal Agents · Open-source finance research", page_icon="🐂",
                   layout="wide", menu_items={"About": f"Dalal Agents v{__version__} · {REPO}"})


def run_analysis(query: str, n_peers: int, n_concalls: int, llm: dict | None) -> dict:
    progress_area = st.empty()
    try:
        with progress_area.container():
            st.html(dashboard.loading_html())
            with st.status(f"Analysing {query}…", expanded=False) as status:
                for kind, payload in core.analyze_stream(query, n_peers, n_concalls, llm):
                    if kind == "done":
                        return payload
                    status.write(core.fmt_progress(payload))
                    if payload.startswith(("Stage", "Found", "Done", "The server is busy")):
                        status.update(label=payload.strip()[:90])
    finally:
        progress_area.empty()
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
    st.html(dashboard.render(r))
    with st.expander("Advanced Analysis", expanded=False):
        for level, text in core.notices(r, st.session_state.get("api_key")):
            (st.info if level == "info" else st.warning)(text)
        d1, d2, d3 = st.columns(3)
        stem = f"{r['symbol']}_{r['generated_at'][:10]}"
        d1.download_button("⬇️ HTML", render_html(md, f"{r['name']} — Dalal Agents"), file_name=f"{stem}.html", mime="text/html")
        d2.download_button("⬇️ Markdown", md, file_name=f"{stem}.md", mime="text/markdown")
        d3.download_button("⬇️ JSON", json.dumps(r, indent=2, ensure_ascii=False, default=str), file_name=f"{stem}.json", mime="application/json")
        with st.expander("Business summary & detailed scores"):
            tab_summary(r, sec)
            tab_competition(r, sec)
        with st.expander("Price history & detailed financials"):
            tab_price(r, sec)
            tab_financials(r, sec)
        with st.expander("Ownership"):
            tab_ownership(r, sec)
        with st.expander("Management & earnings calls"):
            tab_management(r, sec)
        with st.expander("Detailed news"):
            tab_news(r, sec)
        with st.expander("Forecast evidence, risks & sources"):
            st.html(render_outlook(r.get("outlook")))
        with st.expander("Full report & sources"):
            _md(md)
        st.caption("Scores are relative to peers: Strong ≥70, Weak ≤30, otherwise Neutral. Overall rank includes all scored factors; ties share a rank. Risk flags consider volatility, drawdown, liquidity, events and missing data.")


# ------------------------------------------------------------------ page
def controls() -> tuple[str | None, int, int, dict | None]:
    st.markdown("### 🐂 Dalal Agents")
    st.html("""<style>
    .block-container{padding-top:1rem;max-width:1280px}
    .st-key-rd-search [data-testid=stHorizontalBlock]{flex-wrap:nowrap!important;gap:8px;align-items:end}
    .st-key-rd-search [data-testid=stColumn]{min-width:0!important;width:auto!important}
    .st-key-rd-search [data-testid=stColumn]:first-child{flex:1 1 0!important}
    .st-key-rd-search [data-testid=stColumn]:last-child{flex:0 0 100px!important}
    @media(max-width:640px){.block-container{padding-left:12px;padding-right:12px}}
    </style>""")
    with st.container(key="rd-search"):
        search, button = st.columns([5, 1], vertical_alignment="bottom")
        query = search.text_input("Find a stock", key="query", label_visibility="collapsed", placeholder="Search company or NSE/BSE ticker")
        go_clicked = button.button("Analyse", type="primary", width="stretch")

    def choose(symbol):
        st.session_state["query"] = symbol
        st.session_state["pending_query"] = symbol

    with st.expander("Research settings & examples", expanded=False):
        cols = st.columns(3)
        for i, ex in enumerate(EXAMPLES):
            cols[i % 3].button(ex, key=f"ex_{ex}", width="stretch", on_click=choose, args=(ex,))
        n_peers = st.slider("Competitors to analyse", 2, 6, 4)
        n_concalls = st.slider("Earnings-call transcripts to read", 0, 3, 2, help="Used for the optional AI report.")
        with st.expander("Optional AI report", expanded=False):
            provider = st.radio("Provider", list(DEFAULT_MODELS), horizontal=False)
            key = st.text_input("API key", type="password", key="api_key", help="Used only for this run, never logged or stored.")
            model = st.text_input("Model", value=DEFAULT_MODELS[provider], key=f"model_{provider}")
            base_url = ""
            if provider == "OpenAI-compatible":
                base_url = st.text_input("Base URL", value="https://api.openai.com/v1")
            st.caption("The dashboard and forecasts work without an API key.")
    pending = st.session_state.pop("pending_query", None)
    chosen = pending or (query.strip() if go_clicked and query.strip() else None)
    llm = {"provider": provider, "key": key.strip(), "model": model.strip(), "base_url": base_url.strip()} if key.strip() else None
    return chosen, n_peers, n_concalls, llm


def landing():
    st.caption("Search for a stock to see its outlook, stronger alternatives and key trade-offs.")
    st.caption(DISCLAIMER)


def main():
    chosen, n_peers, n_concalls, llm = controls()
    if chosen:
        if st.session_state.get("running"):
            st.warning("An analysis is already running in this session.")
        else:
            st.session_state["running"] = True
            st.session_state.pop("report", None)
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
    elif not st.session_state.get("error"):
        landing()


main()
