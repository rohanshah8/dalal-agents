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

from dalal_agents import __version__  # noqa: E402
from dalal_agents.models import Report  # noqa: E402
from dalal_agents.report import DISCLAIMER, render_html, render_markdown  # noqa: E402

TAB_NAMES = ["📋 Summary", "🥊 Competitors & edge", "📈 Price & trend", "📊 Financials", "👥 Ownership",
             "🎙️ Management & plans", "📰 News", "📄 Full report & sources"]

LANDING = f"""
### 🐂 {core.TAGLINE}

| 1 · Research | 2 · Compare | 3 · Explain |
|---|---|---|
| 7 agents fetch 12 years of financials, price trends vs Nifty, shareholding, filings, earnings-call transcripts and news. | Competitors are found automatically, put through the same maths, and ranked on growth, profitability, balance sheet, cash quality, valuation and momentum. | With your API key, an LLM writes the analyst narrative using only cited facts, and a verifier checks every number. |

👈 **Type a company or pick an example, then press Analyse.** A fresh analysis takes about 15 s
(quantitative) or 1–2 min (with AI narrative). Popular stocks are cached.
"""

CSS = """
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
        "dl_md": f_md, "dl_html": f_html, "dl_json": f_json,
    }


def build() -> gr.Blocks:
    with gr.Blocks(title="Dalal Agents · AI equity research for Indian stocks") as demo:
        out: dict[str, gr.components.Component] = {}
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=280):
                gr.Markdown(f"# 🐂 Dalal Agents\nAI research analysts for Indian stocks · open source · v{__version__}")
                query = gr.Textbox(label="Company", placeholder="TCS, 500325, or 'hdfc bank'",
                                   info="NSE symbol, BSE code or company name")
                gr.Examples(core.EXAMPLES, inputs=query, label="Examples")
                peers = gr.Slider(2, 6, value=4, step=1, label="Competitors to analyse")
                concalls = gr.Slider(0, 3, value=2, step=1, label="Earnings-call transcripts to read",
                                     info="Read by the LLM; ignored without a key")
                with gr.Accordion("🤖 AI narrative (bring your own key)", open=False):
                    provider = gr.Radio(list(core.DEFAULT_MODELS), value="Anthropic (Claude)", label="Provider")
                    key = gr.Textbox(label="API key", type="password",
                                     info="Used only for this run. Never stored or logged.")
                    model = gr.Textbox(label="Model", value=core.DEFAULT_MODELS["Anthropic (Claude)"])
                    base_url = gr.Textbox(label="Base URL (OpenAI-compatible only)",
                                          value="https://api.openai.com/v1", visible=False)
                    gr.Markdown("<small>No key? You still get every number, chart and the peer scorecard.</small>")
                go = gr.Button("🔍 Analyse", variant="primary")
                gr.Markdown(f"<small>[GitHub]({core.REPO}) · [Methodology]({core.REPO}/blob/main/docs/DESIGN.md)"
                            f"</small>\n\n<small>{DISCLAIMER.lstrip('> ')}</small>")
            with gr.Column(scale=3):
                progress = gr.Markdown(LANDING)
                out["header"] = gr.Markdown()
                out["kpis"] = gr.Markdown(elem_classes="kpi")
                with gr.Row():
                    out["dl_html"] = gr.DownloadButton("⬇️ HTML", visible=False, size="sm")
                    out["dl_md"] = gr.DownloadButton("⬇️ Markdown", visible=False, size="sm")
                    out["dl_json"] = gr.DownloadButton("⬇️ JSON", visible=False, size="sm")
                with gr.Tabs(visible=False) as tabs:
                    with gr.Tab(TAB_NAMES[0]):
                        with gr.Row():
                            with gr.Column(scale=3):
                                out["summary"] = gr.Markdown()
                            with gr.Column(scale=2):
                                out["edges"] = gr.Markdown()
                                out["composite"] = gr.Plot(show_label=False)
                    with gr.Tab(TAB_NAMES[1]):
                        out["heatmap"] = gr.Plot(show_label=False)
                        out["competition"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[2]):
                        with gr.Row():
                            out["price_peers"] = gr.Plot(show_label=False)
                            out["price_sma"] = gr.Plot(show_label=False)
                        out["price_text"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[3]):
                        with gr.Row():
                            out["annual"] = gr.Plot(show_label=False)
                            out["quarterly"] = gr.Plot(show_label=False)
                        out["fin_text"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[4]):
                        out["ownership_chart"] = gr.Plot(show_label=False)
                        out["ownership_text"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[5]):
                        out["management"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[6]):
                        out["news"] = gr.Markdown()
                    with gr.Tab(TAB_NAMES[7]):
                        out["full"] = gr.Markdown()

        names = list(out)
        outputs = [progress, tabs] + [out[n] for n in names]

        def on_provider(p):
            return gr.update(value=core.DEFAULT_MODELS[p]), gr.update(visible=p != "Anthropic (Claude)")

        provider.change(on_provider, provider, [model, base_url], api_visibility="private")

        def analyse(q, n_peers, n_concalls, prov, api_key, mdl, url):
            api_key = (api_key or "").strip()
            llm = {"provider": prov, "key": api_key, "model": (mdl or "").strip(),
                   "base_url": (url or "").strip()} if api_key else None
            lines: list[str] = []
            skip = [gr.skip()] * len(names)
            try:
                for kind, payload in core.analyze_stream(q, n_peers, n_concalls, llm):
                    if kind == "done":
                        vals = render(payload, api_key)
                        dl = {"dl_md", "dl_html", "dl_json"}
                        final = [gr.update(value=vals[n], visible=True) if n in dl else vals[n] for n in names]
                        log = "\n".join(f"- {x}" for x in lines)
                        yield [f"<details><summary>✅ Analysis log</summary>\n\n{log}\n\n</details>",
                               gr.update(visible=True)] + final
                        return
                    lines.append(core.fmt_progress(payload))
                    yield ["### ⏳ Analysing…\n\n" + "\n".join(f"- {x}" for x in lines[-14:]), gr.skip()] + skip
            except (LookupError, ValueError) as e:
                yield [f"### ❌ {core.redact(e, api_key)}", gr.skip()] + skip
            except Exception as e:
                yield [f"### ❌ Analysis failed\n\n`{core.redact(e, api_key)}`", gr.skip()] + skip

        inputs = [query, peers, concalls, provider, key, model, base_url]
        go.click(analyse, inputs, outputs, api_visibility="private", concurrency_limit=core.MAX_CONCURRENT, concurrency_id="analyse",
                 show_progress="hidden")
        query.submit(analyse, inputs, outputs, api_visibility="private", concurrency_limit=core.MAX_CONCURRENT, concurrency_id="analyse",
                     show_progress="hidden")
    demo.queue(max_size=int(os.environ.get("DALAL_QUEUE_SIZE", "20")))
    return demo


demo = build()

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft(primary_hue="blue"), css=CSS, footer_links=[],
                server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"))
