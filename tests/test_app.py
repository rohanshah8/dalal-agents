"""Smoke-test the Streamlit app end to end with stubbed providers (no network, no LLM)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


def test_app_landing_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Dalal Agents" in m.value for m in at.markdown)


def test_app_runs_offline_analysis(tmp_path, monkeypatch):
    from conftest import stub_providers

    stub_providers(monkeypatch)
    monkeypatch.setenv("DALAL_CACHE_DIR", str(tmp_path))
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.text_input(key="query").input("TCS")
    next(b for b in at.sidebar.button if "Analyse" in b.label).click().run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert any("TCS" in m.value for m in at.markdown)
    assert len(at.tabs) == 8
    assert {m.label for m in at.metric} >= {"P/E", "ROE", "1Y return"}



def test_safe_public_url():
    from dalal_agents.config import safe_public_url

    for bad in ["http://api.openai.com/v1", "https://127.0.0.1/v1", "https://localhost:8000/v1",
                "https://10.0.0.5/v1", "ftp://x"]:
        with pytest.raises(ValueError):
            safe_public_url(bad)
