"""Dalal Agents — an open-source finance research agent for Indian stocks."""
from __future__ import annotations

__version__ = "0.1.0"


def analyze(company: str, **settings):
    """Run a full analysis. Keyword args override `Settings` fields, e.g. analyze("TCS", n_peers=3)."""
    from .config import Settings
    from .orchestrator import analyze as _run

    s = Settings()
    for k, v in settings.items():
        setattr(s, k, v)
    return _run(company, s)


def outlook(symbol: str, exchange: str | None = None, **kwargs):
    """Return a typed multi-horizon outlook. See outlook.service.analyze_outlook."""
    from .outlook.service import analyze_outlook
    return analyze_outlook(symbol, exchange, **kwargs)


__all__ = ["analyze", "outlook", "__version__"]
