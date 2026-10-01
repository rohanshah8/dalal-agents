"""Runtime configuration, read from environment variables with CLI overrides."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Settings:
    # LLM
    llm_provider: str = field(default_factory=lambda: _env("DALAL_LLM_PROVIDER", "auto") or "auto")
    model: str | None = field(default_factory=lambda: _env("DALAL_MODEL"))
    anthropic_api_key: str | None = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"))
    anthropic_base_url: str = field(
        default_factory=lambda: _env("ANTHROPIC_BASE_URL", "https://api.anthropic.com") or "")
    openai_api_key: str | None = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(
        default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1") or "")
    llm_max_tokens: int = 4096
    llm_timeout_s: int = 180

    # Search
    tavily_api_key: str | None = field(default_factory=lambda: _env("TAVILY_API_KEY"))

    # Analysis
    n_peers: int = 4
    n_concalls: int = 2
    news_days: int = 60
    cost_of_equity: float = 0.12
    terminal_growth: float = 0.05
    price_history: str = "5y"

    # Infra
    cache_dir: Path = field(default_factory=lambda: Path(
        _env("DALAL_CACHE_DIR", str(Path.home() / ".cache" / "dalal-agents")) or ""))
    offline: bool = False
    no_cache: bool = False
    max_workers: int = 6
    strict: bool = False
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 dalal-agents/0.1 (+https://github.com/rohanshah8/dalal-agents)"
    )

    def resolved_llm(self) -> tuple[str, str | None]:
        """Return (provider, model). provider ∈ {anthropic, openai, none}."""
        p = (self.llm_provider or "auto").lower()
        if p == "auto":
            if self.anthropic_api_key:
                p = "anthropic"
            elif self.openai_api_key or "localhost" in self.openai_base_url:
                p = "openai"
            else:
                p = "none"
        default_models = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-4.1-mini", "none": None}
        return p, self.model or default_models.get(p)
