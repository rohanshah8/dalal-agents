"""Verifier, FactStore, LLM JSON parsing and the end-to-end pipeline with fakes (no network)."""
import pandas as pd
import pytest

from dalal_agents.agents.synthesis import _numbers_in, verify_narrative, verify_text
from dalal_agents.agents.text import _norm, _quote_found
from dalal_agents.facts import FactStore
from dalal_agents.llm import extract_json
from dalal_agents.models import Source

SRC = Source(provider="test")


@pytest.fixture
def store():
    s = FactStore()
    s.add("X.roce", "ROCE", 59.92, SRC, "%", company="X")  # F1
    s.add("X.sales", "Sales", 267021.0, SRC, "₹ cr", company="X")  # F2
    s.add("X.de", "Debt/equity", 0.11, SRC, "x", company="X")  # F3
    s.excerpt("TCS won a $1.2 billion deal with a UK insurer", SRC, kind="news")  # E1
    return s


def test_factstore_ids_and_idempotency(store):
    assert store.by_key("X.roce").id == "F1"
    assert store.add("X.roce", "ROCE", 60.0, SRC, "%") == "F1"
    assert store.facts["F1"].value == 60.0
    assert store.add("X.nan", "nan", float("nan"), SRC) is None


def test_verifier_accepts_grounded(store):
    v = verify_text("ROCE is a high 59.9% [F1]. Sales reached ₹2.67 lakh crore [F2].", store)
    assert v["ok"] == v["n"] == 2


def test_verifier_flags_wrong_number(store):
    v = verify_text("ROCE is 72% [F1].", store)
    assert v["ok"] == 0 and "not found" in v["sentences"][0]["issues"][0]


def test_verifier_flags_uncited_number_and_unknown_id(store):
    v = verify_text("ROCE is 59.9%. Margins rose [F99].", store)
    issues = [i for s in v["sentences"] for i in s["issues"]]
    assert any("without citation" in i for i in issues)
    assert any("unknown citation" in i for i in issues)


def test_verifier_flags_recommendations(store):
    v = verify_text("Investors should buy the stock [F1]. It is undervalued [F1].", store)
    assert v["ok"] == 0
    assert verify_text("The company announced a buyback [E1].", store)["ok"] == 1


def test_verifier_excerpt_numbers(store):
    assert verify_text("It won a $1.2 billion deal [E1].", store)["ok"] == 1


def test_numbers_ignore_years_and_periods():
    assert _numbers_in("In FY2026 and FY27 the 5-year CAGR over 10y, NIFTY 50, was 12% [F1]") == [12.0]


def test_numbers_indian_digit_grouping():
    # lakh/crore grouping must parse as one number, even after words like "of"/"the"
    assert _numbers_in("An inter-se transfer of 1,64,000 shares [E5].") == [164000.0]
    assert _numbers_in("Market cap of ₹11,00,888 cr [F1]") == [1100888.0]


def test_strict_mode_drops_bad_sentences(store):
    out, stats = verify_narrative({"a": "Good [F1]. ROCE is 59.9% [F1]. ROCE is 99% [F1]."}, store, strict=True)
    assert "99%" not in out["a"] and stats["passed"] == 2


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}


def test_quote_grounding():
    text = _norm("We expect margins to be in the 26 to 28% band going forward, said the CFO.")
    assert _quote_found(_norm("margins to be in the 26 to 28% band"), text)
    assert not _quote_found(_norm("margins will expand to 35% next year for sure"), text)


# ---------------------------------------------------------------- end-to-end (fakes)
def test_pipeline_end_to_end_offline(tmp_path, monkeypatch):
    """Run the whole orchestrator with providers stubbed from fixtures — no network, no LLM."""
    from pathlib import Path

    import numpy as np

    from dalal_agents.config import Settings
    from dalal_agents.orchestrator import analyze
    from dalal_agents.providers import news as news_mod
    from dalal_agents.providers import screener as scr
    from dalal_agents.providers.yahoo import YahooProvider
    from dalal_agents.report import render_markdown, save_report

    fx = Path(__file__).parent / "fixtures"
    tcs_html = (fx / "screener_TCS.html").read_text()

    def company(self, symbol, consolidated=True):
        html = tcs_html.replace("Tata Consultancy Services Ltd", f"{symbol} Ltd") if symbol != "TCS" else tcs_html
        return scr.parse_company_page(html, symbol, f"https://www.screener.in/company/{symbol}/", True)

    monkeypatch.setattr(scr.ScreenerProvider, "company", company)
    monkeypatch.setattr(scr.ScreenerProvider, "peers",
                        lambda self, d: scr.parse_peers((fx / "screener_peers_TCS.html").read_text()))
    monkeypatch.setattr(scr.ScreenerProvider, "announcements",
                        lambda self, d: scr.parse_announcements((fx / "screener_ann_TCS.html").read_text()))
    monkeypatch.setattr(news_mod.NewsProvider, "search",
                        lambda self, q, days=60, limit=40: news_mod.parse_news((fx / "gnews_TCS.xml").read_bytes(),
                                                                               days=100000, limit=limit))
    monkeypatch.setattr(news_mod.WebSearchProvider, "search",
                        lambda self, q, limit=8: news_mod.parse_bing((fx / "bing.xml").read_bytes(), limit))

    def history(self, sym, period="5y"):
        rng = np.random.default_rng(abs(hash(sym)) % 1000)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 1250)))
        idx = pd.bdate_range("2021-10-01", periods=1250)
        return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=idx)

    monkeypatch.setattr(YahooProvider, "history", history)
    monkeypatch.setattr(YahooProvider, "resolve", lambda self, s: f"{s}.NS")
    from dalal_agents.providers.documents import DocumentProvider

    def pdf_text(self, url, max_pages=60):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(DocumentProvider, "pdf_text", pdf_text)

    s = Settings(llm_provider="none", cache_dir=tmp_path, n_peers=3)
    r = analyze("TCS", s)
    assert r.symbol == "TCS"
    assert r.findings["fundamentals"].status == "ok"
    assert r.findings["market"].status == "ok"
    assert r.findings["concall"].status == "unavailable"  # degraded gracefully
    assert len(r.peers) == 3 and "TCS" not in [p.symbol for p in r.peers]
    assert r.scorecard["composite"]["TCS"] >= 0
    md = render_markdown(r)
    assert "Edge scorecard" in md and "not investment advice" in md
    # deterministic narrative obeys the same grounding contract
    _, stats = verify_narrative(r.narrative, _store_from(r))
    assert stats["numeric_pass_rate"] == 100.0, "\n".join(f"{i['sentence']} -> {i['issues']}" for i in stats["issues"])
    paths = save_report(r, tmp_path / "out")
    assert all(p.exists() for p in paths.values())


def _store_from(r):
    s = FactStore()
    for f in r.facts:
        s.facts[f.id] = f
        s._by_key[f.key] = f.id
    for e in r.excerpts:
        s.excerpts[e.id] = e
    return s
