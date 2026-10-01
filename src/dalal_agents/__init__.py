"""Dalal Agents — open-source multi-agent equity research for Indian stocks."""
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


__all__ = ["analyze", "__version__"]
