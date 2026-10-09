"""Gradio UI: builds, and the analyse handler streams progress then fills every output (offline)."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("gradio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))


def test_gradio_render_and_handler(tmp_path, offline_providers, monkeypatch):
    monkeypatch.setenv("DALAL_CACHE_DIR", str(tmp_path))
    import core
    import gradio_app

    core.CACHE.clear()
    demo = gradio_app.demo
    fn = next(f for f in demo.fns.values() if getattr(f.fn, "__name__", "") == "analyse").fn
    events = list(fn("TCS", 3, 0, "Anthropic (Claude)", "", "", ""))
    assert len(events) > 3
    assert "Analysing" in events[0][0]["value"]
    last = events[-1]
    assert last[0]["visible"] is False
    assert last[2]["open"] is False
    assert any(isinstance(v, str) and "Stock Snapshot" in v for v in last)
    n_out = len(last)
    assert all(len(e) == n_out for e in events)
    vals = gradio_app.render(_report(core), None)
    assert "TCS" in vals["dashboard"] and "Investment Checklist" in vals["dashboard"]
    assert not any(block.get("type") == "tabitem" for block in demo.config["components"])
    for k in ("heatmap", "composite", "price_peers", "price_sma", "annual", "quarterly", "ownership_chart"):
        assert vals[k] is not None, k
    for k in ("dl_md", "dl_html", "dl_json"):
        assert Path(vals[k]).exists()
    assert "Edge" in vals["edges"]


def _report(core):
    return list(core.analyze_stream("TCS", 3, 0, None))[-1][1]


def test_gradio_handler_reports_bad_query(monkeypatch):
    import gradio_app

    fn = next(f for f in gradio_app.demo.fns.values() if getattr(f.fn, "__name__", "") == "analyse").fn
    events = list(fn("   ", 3, 0, "Anthropic (Claude)", "", "", ""))
    assert any(isinstance(v, str) and "Analysis unavailable" in v for v in events[-1])
    assert events[-1][2]["visible"] is False


def test_space_requirements_cover_package_deps():
    """The Space installs from app/space_requirements.txt, not pyproject: keep them in sync."""
    import re

    try:
        import tomllib
    except ModuleNotFoundError:  # py3.10
        tomllib = pytest.importorskip("tomli")
    root = Path(__file__).resolve().parents[1]
    deps = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    name = lambda d: re.split(r"[<>=!~ \[]", d, maxsplit=1)[0].lower()  # noqa: E731
    reqs = {name(x) for x in (root / "app" / "space_requirements.txt").read_text().splitlines()
            if x.strip() and not x.startswith("#")}
    missing = {name(d) for d in deps} - reqs
    assert not missing, f"add to app/space_requirements.txt: {missing}"
    assert "plotly" in reqs
    readme = (root / "app" / "SPACE_README.md").read_text()
    import gradio
    assert "sdk: gradio" in readme and f"sdk_version: {gradio.__version__}" in readme
