"""Shared fixtures: stub every network provider with recorded fixtures (no network, no LLM)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FX = Path(__file__).parent / "fixtures"


def stub_providers(mp) -> None:
    """Monkeypatch providers to serve recorded fixtures. `mp` is a pytest MonkeyPatch."""
    from dalal_agents.providers import news as news_mod
    from dalal_agents.providers import screener as scr
    from dalal_agents.providers.documents import DocumentProvider
    from dalal_agents.providers.yahoo import YahooProvider

    tcs_html = (FX / "screener_TCS.html").read_text()

    def company(self, symbol, consolidated=True):
        html = tcs_html.replace("Tata Consultancy Services Ltd", f"{symbol} Ltd") if symbol != "TCS" else tcs_html
        return scr.parse_company_page(html, symbol, f"https://www.screener.in/company/{symbol}/", True)

    def history(self, sym, period="5y"):
        rng = np.random.default_rng(sum(map(ord, sym)))
        close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 1250)))
        idx = pd.bdate_range("2021-10-01", periods=1250)
        return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=idx)

    def pdf_text(self, url, max_pages=60):
        raise RuntimeError("no network in tests")

    mp.setattr(scr.ScreenerProvider, "company", company)
    mp.setattr(scr.ScreenerProvider, "peers",
               lambda self, d: scr.parse_peers((FX / "screener_peers_TCS.html").read_text()))
    mp.setattr(scr.ScreenerProvider, "announcements",
               lambda self, d: scr.parse_announcements((FX / "screener_ann_TCS.html").read_text()))
    mp.setattr(news_mod.NewsProvider, "search",
               lambda self, q, days=60, limit=40: news_mod.parse_news((FX / "gnews_TCS.xml").read_bytes(),
                                                                      days=100000, limit=limit))
    mp.setattr(news_mod.WebSearchProvider, "search",
               lambda self, q, limit=8: news_mod.parse_bing((FX / "bing.xml").read_bytes(), limit))
    mp.setattr(YahooProvider, "history", history)
    mp.setattr(YahooProvider, "resolve", lambda self, s: f"{s}.NS")
    mp.setattr(DocumentProvider, "pdf_text", pdf_text)


@pytest.fixture
def offline_providers(monkeypatch):
    stub_providers(monkeypatch)
