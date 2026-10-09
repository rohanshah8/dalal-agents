"""The concise dashboard must preserve evidence and never invent missing signals."""
import copy
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import dashboard  # noqa: E402


@pytest.fixture
def report():
    stamp = "2026-10-09T10:00:00Z"
    reasons = [{"category": c, "summary": text, "impact": "NEUTRAL", "source_ids": []} for c, text in (
        ("TECHNICAL", "21-session return -2.0%; distance from the 50-session average -1.0%."),
        ("FUNDAMENTAL", "Revenue growth +4.0%; profit growth +2.0%; ROE +10.0%."),
        ("NEWS", "Recent material news evidence is unavailable; the model does not assume a positive catalyst."))]
    forecasts = [{"horizon": h, "trading_days": d, "movement": "BULLISH", "confidence_score": 40,
                  "current_price": 100, "expected_return_percent": 2, "price_range": {"lower": 90, "median": 102, "upper": 115},
                  "reasoning": copy.deepcopy(reasons), "risks": ["Unexpected company news"], "model_version": "test",
                  "generated_at": stamp} for h, d in zip(dashboard.LABELS, (5, 21, 63))]
    quality = {"status": "PARTIAL", "score": 80, "warnings": ["Earnings calendar unavailable"], "sources": []}
    outlook = {"analysis_id": "test", "symbol": "TCS", "company_name": "TCS Ltd", "exchange": "NSE", "currency": "INR",
               "analysis_timestamp": stamp, "market_timestamp": stamp, "current_price": 100, "data_quality": quality,
               "outlooks": forecasts, "alternatives": [], "model_version": "test", "feature_version": "test",
               "configuration_version": "test", "data_version": "test", "methodology": "Heuristic", "confidence_method": "Uncalibrated", "range_method": "Volatility"}
    return {"symbol": "TCS", "name": "TCS Ltd", "industry": "Technology › Software", "outlook": outlook,
            "findings": {"market": {"data": {"volatility_1y": 18, "max_drawdown_1y": -10}},
                         "fundamentals": {"data": {"valuation": {"market_cap": 100000}}}},
            "peers": [{"symbol": s, "name": s + " Ltd"} for s in ("INFY", "HCLTECH", "WIPRO", "EXTRA")],
            "scorecard": {"composite": {"TCS": 80, "INFY": 80, "HCLTECH": 50, "WIPRO": 20, "EXTRA": 100},
                          "dimensions": {d: {"scores": {"TCS": 80, "INFY": 80, "HCLTECH": 50, "WIPRO": 20}}
                                         for d in (*dashboard.DIMENSIONS, "Balance sheet")}}}


def soup(report):
    return BeautifulSoup(dashboard.render(report), "html.parser")


def test_five_sections_forecast_values_and_disclaimer(report):
    doc = soup(report)
    sections = doc.select(".research-dashboard > section")
    assert [s.select_one("h2").get_text()[2:] for s in sections] == list(dashboard.SECTION_NAMES)
    cards = doc.select(".rd-outlook-card")
    assert len(cards) == 3
    for card in cards:
        assert "+2.0%" in card.get_text() and "40/100" in card.get_text()
        assert [n.get_text() for n in card.select(".rd-range dd")] == ["₹90.00", "₹102.00", "₹115.00"]
        assert len(card.select(".rd-reasons li")) == 3
        assert not card.select_one("details").has_attr("open")
    assert dashboard.DISCLAIMER in doc.get_text()
    assert "Partial data" in doc.get_text() and "UTC" in doc.get_text()
    assert "1 month" in doc.select_one(".rd-verdict").get_text()


def test_ranks_keep_target_limit_peers_and_share_ties(report):
    rows = dashboard.comparison_rows(report)
    assert [r["rank"] for r in rows] == [1, 1, 3, 4]
    assert {r["symbol"] for r in rows} == {"TCS", "INFY", "HCLTECH", "WIPRO"}
    assert all(set(r["scores"]) == set(dashboard.DIMENSIONS) for r in rows)
    assert all("Joint leader" in text for _, text in dashboard.highlights(rows[0], rows))
    for d in report["scorecard"]["dimensions"].values():
        d["scores"] = {s: 50 for s in ("TCS", "INFY", "HCLTECH", "WIPRO")}
    rows = dashboard.comparison_rows(report)
    assert dashboard.highlights(rows[0], rows) == []


def test_missing_data_is_not_a_neutral_forecast_or_weak_score(report):
    report.update(outlook=None, scorecard={}, peers=[])
    doc = soup(report)
    assert "Not enough data" in doc.get_text()
    assert "Unavailable" in doc.select_one(".rd-verdict").get_text()
    assert not doc.select(".rd-main-number")
    assert "Unknown" in doc.select_one(".rd-checklist").get_text()


def test_alternatives_remain_separate_by_horizon_and_limited(report):
    o = report["outlook"]
    for horizon in dashboard.LABELS:
        for i in range(3):
            o["alternatives"].append({"symbol": f"ALT{i}", "company_name": f"Alternative {i}", "exchange": "NSE", "currency": "INR",
                                      "comparison_horizon": horizon, "movement": "BULLISH", "confidence_score": 50, "current_price": 100,
                                      "expected_return_percent": 5, "price_range": {"lower": 85, "median": 105, "upper": 120},
                                      "reasons": copy.deepcopy(o["outlooks"][0]["reasoning"][:2]), "key_risk": "Upcoming earnings",
                                      "why_ranked_higher": "Higher forecasted return and confidence",
                                      "comparison_metrics": {"return_difference_percent": 3, "confidence_difference": 10, "risk_adjusted_score": 0.5},
                                      "data_quality": o["data_quality"], "market_timestamp": o["market_timestamp"], "analysis_timestamp": o["analysis_timestamp"]})
    before = copy.deepcopy(report)
    doc = soup(report)
    assert report == before
    assert doc.select_one(".rd-alt-picker input[checked]")["value"] == "ONE_MONTH"
    for panel in doc.select(".rd-alt-panel"):
        assert len(panel.select(".rd-alt-card")) == 3
        assert [r.get_text() for r in panel.select(".rd-rank")] == ["#1", "#2", "#3"]
        assert "+3.0 pp higher forecasted return" in panel.get_text()


def test_empty_alternatives_copy_and_safe_external_text(report):
    report["name"] = '<img src=x onerror="alert(1)">'
    report["outlook"]["outlooks"][0]["reasoning"][0]["summary"] = '<script>alert(1)</script>'
    doc = soup(report)
    assert not doc.find("script") and not doc.find("img")
    assert "<script>alert(1)</script>" in doc.get_text()
    assert doc.get_text().count("No stronger alternative found currently.") == 3


def test_loading_error_and_financial_checklist(report):
    assert 'aria-busy="true"' in dashboard.loading_html()
    assert 'role="alert"' in dashboard.error_html()
    assert dashboard.DISCLAIMER in dashboard.error_html()
    report["findings"]["fundamentals"]["data"]["is_financial"] = True
    report["scorecard"]["dimensions"]["Capital"] = {"scores": {"TCS": 10, "INFY": 80}}
    checklist = soup(report).select_one(".rd-checklist").get_text()
    assert "Bank capital" in checklist and "Weak" in checklist
    assert "Medium" in checklist


def test_shortening_preserves_negative_evidence(report):
    outlook = dashboard.StockOutlook.model_validate(report["outlook"])
    reason = outlook.outlooks[0].reasoning[0]
    assert dashboard.short_reason(reason) == "Price -2.0% over 21 trading days"
    assert dashboard.reason_tone(reason) == "negative"
    assert dashboard.short_reason(outlook.outlooks[0].reasoning[2]) == "Limited recent news evidence"
    from dalal_agents.outlook.models import Reason
    sector = Reason(category="SECTOR", summary="Sector benchmark 21-session return -2.0%; the stock's relative sector return is +3.0%.", impact="POSITIVE")
    assert dashboard.short_reason(sector) == "Stock ahead of sector by 3.0 pp"
    assert dashboard.reason_tone(sector) == "positive"


@pytest.mark.parametrize("score,label", [(0, "Weak"), (30, "Weak"), (31, "Neutral"), (69, "Neutral"), (70, "Strong"), (None, "Not enough data")])
def test_score_labels(score, label):
    assert dashboard.grade(score)[0] == label
