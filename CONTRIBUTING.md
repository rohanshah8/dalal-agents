# Contributing to Dalal Agents

Thanks for helping. A few ground rules keep the project trustworthy:

1. **Numbers come from code, not prompts.** New metrics go in `src/dalal_agents/analytics/` as pure functions, with a unit test against a hand-computed value.
2. **Every number must be a Fact.** If an agent produces a number the report shows, register it with `self.fact(...)` so it can be cited and verified.
3. **Tests run offline.** Use fixtures in `tests/fixtures/`, never live network calls. When a source's HTML changes, refresh the fixture and fix the parser in the same PR.
4. **No recommendations.** Prompts, templates and docs must never produce buy/sell/hold calls or target prices.
5. **Be polite to data sources.** Use the shared `HttpClient` (cache plus throttling) and only robots.txt-allowed paths.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q && ruff check src tests
```

## Adding a data provider

Create `providers/<name>.py` with a class that takes an `HttpClient`, return the existing models (`CompanyData`, `Peer`, `Document`), and wire it into `agents/base.py::Context`.

## Adding a sector playbook

Detection lives in `providers/screener.py::parse_company_page` (`is_financial`). Add metric definitions in `analytics/fundamentals.py`, and scorecard dimensions in `analytics/peers.py`.
