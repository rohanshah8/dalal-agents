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
    outlook_enabled: bool = field(default_factory=lambda: _env("DALAL_OUTLOOK_ENABLED", "1") == "1")
    outlook_config_file: Path | None = field(default_factory=lambda: Path(v) if (v := _env("DALAL_OUTLOOK_CONFIG")) else None)
    outlook_macro_file: Path | None = field(default_factory=lambda: Path(v) if (v := _env("DALAL_MACRO_FILE")) else None)
    outlook_db: Path | None = field(default_factory=lambda: Path(v) if (v := _env("DALAL_OUTLOOK_DB")) else None)
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


def safe_public_url(url: str) -> str:
    """Validate a user-supplied endpoint (hosted app): https only, never private/loopback addresses."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("Base URL must be an https:// URL.")
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(u.hostname, u.port or 443)}
    except OSError as e:
        raise ValueError(f"Cannot resolve {u.hostname}.") from e
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("Base URL must be a public endpoint.")
    return url.rstrip("/")
