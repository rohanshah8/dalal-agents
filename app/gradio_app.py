"""Dalal Agents — Gradio web UI (the public Hugging Face Space).

Run locally:   pip install -e ".[web]" && python app/gradio_app.py      # http://localhost:7860
Pipeline, caching and the bring-your-own-key policy live in app/core.py (shared with the Streamlit UI).
The API key is only ever a function argument of `analyse`: no gr.State, no logging, no disk.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `import charts, core`

import charts  # noqa: E402
import core  # noqa: E402
import dashboard  # noqa: E402

from dalal_agents import __version__  # noqa: E402
from dalal_agents.models import Report  # noqa: E402
from dalal_agents.outlook.render import render_html as render_outlook
from dalal_agents.report import render_html, render_markdown  # noqa: E402

LANDING = "Search for a stock to see its outlook, stronger alternatives and key trade-offs."

THEME = gr.themes.Soft(primary_hue="blue", font=["Arial", "Helvetica", "sans-serif"]).set(
    body_text_weight="400",
    block_label_text_weight="400",
    block_title_text_weight="400",
    button_large_text_weight="400",
    button_medium_text_weight="400",
)

CSS = """
.gradio-container {width:100%!important;max-width:1280px!important;margin:auto!important;font-family:Arial,Helvetica,sans-serif}
.gradio-container .main {width:100%;padding:16px!important}
#rd-brand {margin-bottom:0} #rd-brand h3 {font-size:20px;margin:0}
#rd-search {align-items:end;gap:12px} #rd-search button {min-height:44px}
#rd-settings {margin-bottom:8px} #rd-progress {min-height:0}
#rd-dashboard {border:0;padding:0;background:transparent}
#rd-dashboard .html-container {padding:0!important}
@media (max-width:640px) {.gradio-container {padding:10px!important}.gradio-container .main{padding:0!important} #rd-search {gap:8px} #rd-search>div {min-width:0!important} #rd-brand h3 {font-size:17px}}
.kpi table { width: 100%; text-align: center; }
.kpi th { font-weight: 500; opacity: .75; }
.kpi td { font-size: 1.25rem; font-weight: 600; }
sub { opacity: .55; font-size: .7em; }
"""


def _kpi_md(r: dict) -> str:
    k = core.kpis(r)
    return ("| " + " | ".join(lab for lab, _, _ in k) + " |\n|" + "---|" * len(k) + "\n| "
            + " | ".join(v + (f"<br><small>{d}</small>" if d else "") for _, v, d in k) + " |")


def _downloads(r: dict, md: str) -> tuple[str, str, str]:
    d = Path(tempfile.mkdtemp(prefix="dalal-"))
    stem = f"{r['symbol']}_{r['generated_at'][:10]}"
    p_md, p_html, p_json = d / f"{stem}.md", d / f"{stem}.html", d / f"{stem}.json"
    p_md.write_text(md, encoding="utf-8")
    p_html.write_text(render_html(md, f"{r['name']} — Dalal Agents"), encoding="utf-8")
    p_json.write_text(json.dumps(r, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return str(p_md), str(p_html), str(p_json)


def render(r: dict, secret: str | None = None) -> dict:
    """Map a report dict to every output component's value (pure: also used by tests)."""
    md = render_markdown(Report.model_validate(r))
    sec = core.sections(md)
    c = core.clean_md
    notes = "\n".join(("> ℹ️ " if lvl == "info" else "> ⚠️ ") + t for lvl, t in core.notices(r, secret))
    summary = (c(sec.get("Executive summary")) or "_No AI narrative for this run. Add an API key for one._")
    for t in ("Bull case", "Bear case", "What to monitor"):
        if sec.get(t):
            summary += f"\n\n#### {t}\n\n" + c(sec[t])
    side = core.edges_md(r)
    if sec.get("Business overview"):
        side += "\n\n#### Business overview\n\n" + c(sec["Business overview"])
    fin = (c(sec.get("Financial performance")) + "\n\n### Balance sheet & cash-flow quality\n\n"
           + c(sec.get("Balance sheet & cash-flow quality")) + "\n\n### Valuation\n\n"
           + c(sec.get("Valuation (what the price implies)")))
    mgmt = (c(sec.get("Future plans & management commentary")) or "_No management commentary available._") \
        + "\n\n" + c(core.concalls_md(r))
    news = (c(sec.get("News flow & sentiment")) or "_No news summary._") + "\n\n" + core.headlines_md(r)
    f_md, f_html, f_json = _downloads(r, md)
    return {
        "dashboard": dashboard.render(r),
        "header": f"## {r['name']} · `{r['symbol']}`\n{core.header_line(r)}\n\n{notes}",
        "kpis": _kpi_md(r),
        "summary": summary, "edges": side, "composite": charts.composite_bar(r),
        "heatmap": charts.scorecard_heatmap(r),
        "competition": "_Percentile ranks within this peer set (0 = worst, 100 = best). They measure relative "
                       "quality, not attractiveness as an investment._\n\n" + c(core.competition_body(sec)),
        "price_peers": charts.price_vs_peers(r), "price_sma": charts.price_with_smas(r),
        "price_text": c(sec.get("Price & trend")),
        "annual": charts.annual_financials(r), "quarterly": charts.quarterly_results(r), "fin_text": fin,
        "ownership_chart": charts.shareholding(r), "ownership_text": c(sec.get("Ownership")),
        "management": mgmt, "news": news, "full": c(md),
        "outlook": render_outlook(r.get("outlook")),
        "dl_md": f_md, "dl_html": f_html, "dl_json": f_json,
    }


def build() -> gr.Blocks:
    with gr.Blocks(title="Dalal Agents · Stock research at a glance") as demo:
        out: dict[str, gr.components.Component] = {}
        gr.Markdown("### 🐂 Dalal Agents", elem_id="rd-brand")
        with gr.Row(elem_id="rd-search"):
            query = gr.Textbox(label="Find a stock", show_label=False, placeholder="Search company or NSE/BSE ticker", scale=6,
                               min_width=180)
            go = gr.Button("Analyse", variant="primary", scale=1, min_width=100)
        with gr.Accordion("Research settings & examples", open=False, elem_id="rd-settings"):
            gr.Examples(core.EXAMPLES, inputs=query, label="Examples")
            with gr.Row():
                peers = gr.Slider(2, 6, value=4, step=1, label="Competitors to analyse")
                concalls = gr.Slider(0, 3, value=2, step=1, label="Earnings-call transcripts to read",
                                     info="Used for the optional AI report")
            with gr.Accordion("Optional AI report", open=False):
                provider = gr.Radio(list(core.DEFAULT_MODELS), value="Anthropic (Claude)", label="Provider")
                key = gr.Textbox(label="API key", type="password", info="Used only for this run. Never stored or logged.")
                model = gr.Textbox(label="Model", value=core.DEFAULT_MODELS["Anthropic (Claude)"])
                base_url = gr.Textbox(label="Base URL (OpenAI-compatible only)",
                                      value="https://api.openai.com/v1", visible=False)
                gr.Markdown("The dashboard and forecasts work without an API key.")
        progress = gr.Markdown(LANDING, elem_id="rd-progress")
        with gr.Column(visible=False) as results:
            out["dashboard"] = gr.HTML(elem_id="rd-dashboard", apply_default_css=False)
            with gr.Accordion("Advanced Analysis", open=False, visible=False) as advanced:
                with gr.Row():
                    out["dl_html"] = gr.DownloadButton("⬇️ HTML", visible=False, size="sm")
                    out["dl_md"] = gr.DownloadButton("⬇️ Markdown", visible=False, size="sm")
                    out["dl_json"] = gr.DownloadButton("⬇️ JSON", visible=False, size="sm")
                with gr.Accordion("Business summary & detailed scores", open=False):
                    out["summary"] = gr.Markdown()
                    out["edges"] = gr.Markdown()
                    out["composite"] = gr.Plot(show_label=False)
                    out["heatmap"] = gr.Plot(show_label=False)
                    out["competition"] = gr.Markdown()
                with gr.Accordion("Price history & detailed financials", open=False):
                    with gr.Row():
                        out["price_peers"] = gr.Plot(show_label=False)
                        out["price_sma"] = gr.Plot(show_label=False)
                    out["price_text"] = gr.Markdown()
                    with gr.Row():
                        out["annual"] = gr.Plot(show_label=False)
                        out["quarterly"] = gr.Plot(show_label=False)
                    out["fin_text"] = gr.Markdown()
                with gr.Accordion("Ownership", open=False):
                    out["ownership_chart"] = gr.Plot(show_label=False)
                    out["ownership_text"] = gr.Markdown()
                with gr.Accordion("Management & earnings calls", open=False):
                    out["management"] = gr.Markdown()
                with gr.Accordion("Detailed news", open=False):
                    out["news"] = gr.Markdown()
                with gr.Accordion("Forecast evidence, risks & sources", open=False):
                    out["outlook"] = gr.HTML()
                with gr.Accordion("Full report & sources", open=False):
                    out["full"] = gr.Markdown()
                with gr.Accordion("Analysis log & data notices", open=False):
                    out["log"] = gr.Markdown()
                gr.Markdown("Scores are relative to the analysed peers: Strong ≥70, Weak ≤30, otherwise Neutral. "
                            "The four displayed companies share ranks when tied. Overall rank includes all scored factors. "
                            "Risk flags consider observed volatility, drawdown, liquidity, events and data gaps; they are not a personal risk assessment.")
                gr.Markdown(f"[GitHub]({core.REPO}) · [Methodology]({core.REPO}/blob/main/docs/DESIGN.md) · v{__version__}")

        names = list(out)
        outputs = [progress, results, advanced] + [out[n] for n in names]

        def on_provider(p):
            return gr.update(value=core.DEFAULT_MODELS[p]), gr.update(visible=p != "Anthropic (Claude)")

        provider.change(on_provider, provider, [model, base_url], api_visibility="private")

        def analyse(q, n_peers, n_concalls, prov, api_key, mdl, url):
            api_key = (api_key or "").strip()
            llm = {"provider": prov, "key": api_key, "model": (mdl or "").strip(),
                   "base_url": (url or "").strip()} if api_key else None
            lines: list[str] = []
            skip = [gr.skip()] * len(names)
            initial = [gr.update(visible=False) if n.startswith("dl_") else dashboard.loading_html() if n == "dashboard" else "" if isinstance(out[n], (gr.Markdown, gr.HTML)) else None for n in names]
            yield [gr.update(value="Analysing…", visible=True), gr.update(visible=True), gr.update(visible=False, open=False)] + initial
            try:
                for kind, payload in core.analyze_stream(q, n_peers, n_concalls, llm):
                    if kind == "done":
                        vals = render(payload, api_key)
                        vals["log"] = "### Analysis log\n\n" + "\n".join(f"- {x}" for x in lines)
                        vals["log"] += "\n\n" + "\n".join(t for _, t in core.notices(payload, api_key))
                        final = [gr.update(value=vals[n], visible=True) if n.startswith("dl_") else vals[n] for n in names]
                        yield [gr.update(value="", visible=False), gr.update(visible=True), gr.update(visible=True, open=False)] + final
                        return
                    lines.append(core.fmt_progress(payload))
                    yield ["Analysing · " + dashboard.esc(core.redact(payload, api_key)[:120]), gr.skip(), gr.skip()] + skip
            except Exception as exc:
                message = core.redact(exc, api_key) if isinstance(exc, (LookupError, ValueError)) else "Please retry. The data provider may be unavailable."
                failure = [dashboard.error_html(message) if n == "dashboard" else gr.skip() for n in names]
                yield [gr.update(value="", visible=False), gr.update(visible=True), gr.update(visible=False)] + failure

        inputs = [query, peers, concalls, provider, key, model, base_url]
        go.click(analyse, inputs, outputs, api_visibility="private", concurrency_limit=core.MAX_CONCURRENT, concurrency_id="analyse", show_progress="hidden")
        query.submit(analyse, inputs, outputs, api_visibility="private", concurrency_limit=core.MAX_CONCURRENT, concurrency_id="analyse", show_progress="hidden")
    demo.queue(max_size=int(os.environ.get("DALAL_QUEUE_SIZE", "20")))
    return demo


demo = build()


def create_app():
    from dalal_agents.outlook.api import create_app as api_app
    return gr.mount_gradio_app(api_app(), demo, path="/", theme=THEME,
                               css=CSS, footer_links=[])


def launch(*, prevent_thread_lock=False):
    from starlette.routing import Mount

    from dalal_agents.outlook.api import create_app as api_app

    # Spaces registers its ZeroGPU startup report on Blocks.launch. Starting
    # Uvicorn directly skips that hook and makes the Space fail its health check.
    return demo.launch(server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
                       server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7860")),
                       theme=THEME, css=CSS, footer_links=[],
                       app_kwargs={"routes": [Mount("/api", app=api_app(path_prefix=""))]},
                       # The Spaces SSR proxy only forwards Gradio's own API
                       # routes; serve custom REST routes from Python directly.
                       ssr_mode=False,
                       prevent_thread_lock=prevent_thread_lock)


if __name__ == "__main__":
    launch()
