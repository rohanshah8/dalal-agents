"""Shared UI core: cache, streaming pipeline, key policy."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import core  # noqa: E402


def test_research_cache_ttl_and_lru(monkeypatch):
    c = core.ResearchCache(ttl_s=10, max_entries=2)
    now = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: now[0])
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") == 1  # a is now most recent
    c.put("c", 3)  # evicts b
    assert c.get("b") is None and c.get("c") == 3
    now[0] += 11
    assert c.get("a") is None  # expired


def test_redact_and_query_normalisation():
    assert "sk-ant-secret123456" not in core.redact("bad key sk-ant-secret123456 here")
    assert core.redact("token=abc123", "abc123") == "token=•••"
    assert core.normalize_query("  hdfc   bank ") == "HDFC BANK"


def test_analyze_stream_offline_caches_key_free_runs(tmp_path, offline_providers, monkeypatch):
    monkeypatch.setenv("DALAL_CACHE_DIR", str(tmp_path))
    calls = []
    real = core.research
    monkeypatch.setattr(core, "research", lambda *a, **k: calls.append(1) or real(*a, **k))
    cache = core.ResearchCache()

    def run(q):
        events = list(core.analyze_stream(q, 2, 0, None, cache=cache))
        assert events[-1][0] == "done"
        assert all(k in ("progress", "queued") for k, _ in events[:-1])
        return events

    first = run("TCS")
    assert any("Stage 1/3" in p for _, p in first[:-1])
    r = first[-1][1]
    assert r["symbol"] == "TCS" and r["narrative"] and r["charts"]["prices"]
    second = run(" tcs ")  # normalised → cache hit, research not re-run
    assert len(calls) == 1
    assert any("served from cache" in p for _, p in second[:-1])


def test_analyze_stream_with_key_is_uncached_and_redacted(tmp_path, offline_providers, monkeypatch):
    monkeypatch.setenv("DALAL_CACHE_DIR", str(tmp_path))
    from dalal_agents import llm as llm_mod

    secret = "sk-test-SECRET-abcdef123456"

    def boom(self, *a, **k):
        raise llm_mod.LLMError(f"anthropic call failed: HTTP 401 invalid x-api-key {secret}")

    monkeypatch.setattr(llm_mod.LLM, "complete", boom)
    cache = core.ResearchCache()
    llm = {"provider": "Anthropic (Claude)", "key": secret, "model": "claude-opus-5-5", "base_url": ""}
    events = list(core.analyze_stream("TCS", 2, 0, llm, cache=cache))
    kind, r = events[-1]
    assert kind == "done"
    assert secret not in str(events)
    assert any("LLM writer failed" in w for w in r["warnings"])
    assert not cache._d  # key runs never populate the shared cache
    notes = core.notices(r, secret)
    assert any("rejected" in t for _, t in notes)


def test_empty_query_rejected():
    with pytest.raises(LookupError):
        list(core.analyze_stream("   ", 2, 0, None))


def test_markdown_builders_on_minimal_report():
    r = {"findings": {}, "scorecard": {"edges": [{"dimension": "Growth", "score": 80, "leader": True}],
                                        "gaps": [{"dimension": "Valuation", "score": 10, "leader": "X",
                                                  "leader_score": 90}], "rank": 2, "n_companies": 5}}
    md = core.edges_md(r)
    assert "Growth" in md and "Valuation" in md and "rank 2 of 5" in md
    assert core.concalls_md(r) == "" and core.headlines_md(r) == ""
    assert core.clean_md("x [F1, E2]") == "x <sub>[F1, E2]</sub>"
    assert core.sections("intro\n## A\nbody a\n## B\nbody b") == {"_head": "intro", "A": "body a", "B": "body b"}
