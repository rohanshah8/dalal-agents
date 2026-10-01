"""Verifier, FactStore, LLM JSON parsing and the end-to-end pipeline with fakes (no network)."""
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
def test_pipeline_end_to_end_offline(tmp_path, offline_providers):
    """Run the whole orchestrator with providers stubbed from fixtures — no network, no LLM."""
    from dalal_agents.config import Settings
    from dalal_agents.orchestrator import analyze
    from dalal_agents.report import render_markdown, save_report

    s = Settings(llm_provider="none", cache_dir=tmp_path, n_peers=3)
    r = analyze("TCS", s)
    assert r.symbol == "TCS"
    assert r.industry
    assert r.findings["fundamentals"].status == "ok"
    assert r.findings["market"].status == "ok"
    assert r.findings["concall"].status == "unavailable"  # degraded gracefully
    assert len(r.peers) == 3 and "TCS" not in [p.symbol for p in r.peers]
    assert r.scorecard["composite"]["TCS"] >= 0
    prices = r.charts["prices"]
    assert {"TCS", "NIFTY 50"} <= set(prices) and len(prices["TCS"]["close"]) > 100
    assert r.charts["tables"]["profit_loss"]["periods"][-1] == "TTM"
    md = render_markdown(r)
    assert "Edge scorecard" in md and "not investment advice" in md
    # deterministic narrative obeys the same grounding contract
    _, stats = verify_narrative(r.narrative, FactStore.from_report(r))
    assert stats["numeric_pass_rate"] == 100.0, "\n".join(f"{i['sentence']} -> {i['issues']}" for i in stats["issues"])
    paths = save_report(r, tmp_path / "out")
    assert all(p.exists() for p in paths.values())


def test_research_then_write_is_pure(tmp_path, offline_providers):
    """write() must not mutate a (cached) research Report and must reuse its fact IDs."""
    from dalal_agents.config import Settings
    from dalal_agents.orchestrator import research, write

    s = Settings(llm_provider="none", cache_dir=tmp_path, n_peers=2)
    base = research("TCS", s)
    assert base.narrative == {} and base.facts
    before = base.model_dump_json()
    out = write(base, s)
    assert base.model_dump_json() == before
    assert out.narrative and out.verification["mode"] == "deterministic"
    assert [f.id for f in out.facts] == [f.id for f in base.facts]
