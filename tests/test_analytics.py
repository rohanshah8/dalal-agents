"""Offline tests for parsers and finance maths (fixtures captured from live pages)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dalal_agents.analytics import (
    compute_fundamentals,
    compute_technicals,
    compute_valuation,
    shareholding_trends,
)
from dalal_agents.analytics.fundamentals import cagr
from dalal_agents.analytics.peers import _percentile_ranks, rank_competitors, scorecard, size_similarity
from dalal_agents.analytics.technicals import max_drawdown, rsi
from dalal_agents.analytics.text import forward_looking, split_transcript, tone
from dalal_agents.analytics.valuation import implied_growth, present_value
from dalal_agents.models import Peer
from dalal_agents.providers.documents import canonical_pdf_url
from dalal_agents.providers.news import parse_bing, parse_news
from dalal_agents.providers.screener import parse_announcements, parse_company_page, parse_number, parse_peers

FX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def tcs():
    return parse_company_page((FX / "screener_TCS.html").read_text(), "TCS", "https://x/TCS", True)


@pytest.fixture(scope="module")
def bank():
    return parse_company_page((FX / "screener_HDFCBANK_standalone.html").read_text(), "HDFCBANK", "u", False)


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("raw,val", [("7,41,925", 741925.0), ("26%", 26.0), ("-3.5", -3.5), ("", None),
                                     ("₹ 2,051", 2051.0), ("—", None)])
def test_parse_number(raw, val):
    assert parse_number(raw) == val


def test_parse_company_nonfinancial(tcs):
    p = tcs.profile
    assert p.name.startswith("Tata Consultancy")
    assert not p.is_financial
    assert p.screener_warehouse_id and p.screener_company_id
    assert "IT - Software" in p.sector_path
    assert p.top_ratios["Market Cap"] > 100000
    assert p.top_ratios["52W High"] > p.top_ratios["52W Low"]
    assert tcs.profit_loss.periods[-1] == "TTM"
    assert tcs.profit_loss.get("Sales") and tcs.balance_sheet.get("Borrowings")
    assert tcs.shareholding.get("Promoters")
    kinds = {d.kind for d in tcs.documents}
    assert {"concall", "annual_report"} <= kinds
    assert not any(d.url.endswith(".mp3") for d in tcs.documents)


def test_parse_company_bank(bank):
    assert bank.profile.is_financial
    assert bank.balance_sheet.get("Deposits")
    assert bank.quarters.get("Gross NPA %")


def test_parse_peers():
    peers = parse_peers((FX / "screener_peers_TCS.html").read_text())
    assert len(peers) >= 5
    infy = next(p for p in peers if p.symbol == "INFY")
    assert infy.market_cap > 0 and infy.pe > 0 and infy.roce > 0


def test_parse_announcements():
    anns = parse_announcements((FX / "screener_ann_TCS.html").read_text())
    assert anns and all(a.url.startswith("http") for a in anns)
    assert anns[0].date


def test_parse_news_and_bing():
    news = parse_news((FX / "gnews_TCS.xml").read_bytes(), days=100000)
    assert len(news) > 10 and all(n["title"] for n in news)
    assert not any(n["title"].endswith(" - " + (n["publisher"] or "§")) for n in news)
    web = parse_bing((FX / "bing.xml").read_bytes())
    assert web and web[0]["url"].startswith("http")


def test_canonical_pdf_url():
    u = "https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=abc-123.pdf"
    assert canonical_pdf_url(u) == "https://www.bseindia.com/xml-data/corpfiling/AttachHis/abc-123.pdf"
    assert canonical_pdf_url("https://x.com/a.pdf") == "https://x.com/a.pdf"


# ------------------------------------------------------------ fundamentals
def test_cagr():
    assert cagr([100, 110, 121], 2) == pytest.approx(10.0)
    assert cagr([-5, 10], 1) is None  # negative base → undefined
    assert cagr([10], 3) is None


def test_fundamentals_tcs_matches_screener(tcs):
    m = compute_fundamentals(tcs)
    gr = tcs.profile.growth_ranges["Compounded Sales Growth"]
    # our 5y sales CAGR (FY-end to FY-end) should agree with Screener's published figure within ~1pp
    assert m["sales_cagr_5y"] == pytest.approx(gr["5 Years"], abs=1.0)
    assert m["roce"] == pytest.approx(tcs.profile.top_ratios["ROCE"], abs=5)
    assert m["roe"] == pytest.approx(tcs.profile.top_ratios["ROE"], abs=5)
    assert 0 < m["debt_to_equity"] < 1
    assert m["cfo_to_np_5y"] > 0.7
    assert m["piotroski"] is not None and 0 <= m["piotroski"] <= m["piotroski_max"]


def test_fundamentals_bank(bank):
    m = compute_fundamentals(bank)
    assert m["is_financial"]
    assert "roce" not in m and "debt_to_equity" not in m  # industrial metrics suppressed
    assert 0 < m["gnpa"] < 10 and 0 < m["nnpa"] < m["gnpa"]
    assert 0.5 < m["roa"] < 3
    assert m["deposit_cagr_5y"] > 0


def test_shareholding(tcs):
    s = shareholding_trends(tcs)
    assert 50 < s["promoter_latest"] < 80
    assert s["latest_period"] == tcs.shareholding.periods[-1]


# ---------------------------------------------------------------- valuation
def test_reverse_dcf_roundtrip():
    mcap = present_value(100.0, 0.15, 10, 0.12, 0.05)
    assert implied_growth(mcap, 100.0, 0.12, 0.05) == pytest.approx(15.0, abs=0.01)
    assert implied_growth(1000, -5) is None


def test_valuation_tcs(tcs):
    v = compute_valuation(tcs.profile.top_ratios, compute_fundamentals(tcs), {}, 0.12, 0.05)
    assert v["pe"] > 0 and v["pb"] > 0
    assert -10 < v["implied_growth_10y"] < 40


# --------------------------------------------------------------- technicals
def _prices(n=600, drift=0.0005, seed=1):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.01, n)
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6}, index=idx)


def test_technicals_basic():
    p = _prices()
    t = compute_technicals(p, _prices(seed=2))
    assert t["price"] == pytest.approx(p["Close"].iloc[-1])
    assert 0 <= t["rsi14"] <= 100
    assert t["max_drawdown_1y"] <= 0
    assert t["trend"] in {"Uptrend", "Downtrend", "Sideways / transition"}
    assert t["beta_1y"] is not None and -1 < t["beta_1y"] < 1  # independent series → beta ≈ 0
    assert t["volatility_1y"] == pytest.approx(10 * np.sqrt(252) / 10, rel=0.25)


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(up) == pytest.approx(100.0)
    assert max_drawdown(pd.Series([100, 50, 75.0])) == pytest.approx(-50.0)


def test_beta_identity():
    p = _prices()
    t = compute_technicals(p, p)
    assert t["beta_1y"] == pytest.approx(1.0, abs=1e-6)


# ------------------------------------------------------------- competition
def test_size_similarity():
    assert size_similarity(100, 100) == 1
    assert size_similarity(100, 200) == pytest.approx(0.5)


def test_rank_competitors_excludes_target_and_prefers_similar_size():
    peers = [Peer(symbol="A", name="A", market_cap=1000), Peer(symbol="B", name="B", market_cap=900),
             Peer(symbol="C", name="C", market_cap=10), Peer(symbol="T", name="T", market_cap=1000)]
    out = rank_competitors("T", 1000, peers, k=2)
    assert [p.symbol for p in out] == ["A", "B"]


def test_percentile_ranks_direction_and_ties():
    r = _percentile_ranks({"a": 1, "b": 2, "c": 3}, True)
    assert r == {"a": 0.0, "b": 0.5, "c": 1.0}
    r = _percentile_ranks({"a": 1, "b": 2, "c": 3}, False)
    assert r["a"] == 1.0
    r = _percentile_ranks({"a": 1, "b": 1}, True)
    assert r == {"a": 0.5, "b": 0.5}


def test_scorecard_edges_and_gaps():
    best = {"sales_cagr_5y": 20, "profit_cagr_5y": 20, "q_sales_yoy": 20, "opm": 30, "roe": 30, "roce": 30,
            "debt_to_equity": 0.1, "interest_coverage": 50, "pe": 40, "pb": 10, "peg": 3}
    worse = {k: (v / 2 if k not in ("debt_to_equity", "pe", "pb", "peg") else v / 4) for k, v in best.items()}
    sc = scorecard({"T": best, "P": worse}, "T", financial=False)
    dims = {e["dimension"] for e in sc["edges"]}
    assert {"Growth", "Profitability", "Balance sheet"} <= dims
    assert any(g["dimension"] == "Valuation" for g in sc["gaps"])  # T is pricier
    assert sc["rank"] == 1


def test_scorecard_ignores_negative_pe():
    a = {"pe": -5, "pb": 1}
    b = {"pe": 20, "pb": 2}
    sc = scorecard({"A": a, "B": b}, "A", financial=False)
    assert sc["metric_table"]["P/E"]["values"]["A"] is None


# --------------------------------------------------------------------- text
def test_tone_and_negation():
    assert tone("Strong growth and record profit")["net_tone"] > 0
    assert tone("Weak demand and margin pressure")["net_tone"] < 0
    assert tone("not strong")["net_tone"] < 0


def test_forward_looking_and_split():
    txt = ("Good evening everyone. We expect revenue growth of 12% in FY27 driven by deal wins. "
           "The weather was pleasant during the quarter and nothing else matters here at all. "
           "Our capex plan for the new plant is Rs 500 crore over the next two years. "
           "We will now begin the question-and-answer session. First question is from Rahul.")
    fl = forward_looking(txt)
    assert any("capex" in s for s in fl) and any("FY27" in s for s in fl)
    assert not any("weather" in s for s in fl)
    mgmt, qa = split_transcript(txt)
    assert "question-and-answer" in qa and "capex" in mgmt
