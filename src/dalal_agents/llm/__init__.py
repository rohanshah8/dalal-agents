"""Provider-agnostic LLM client over raw HTTP (no SDK dependencies).

* anthropic — Anthropic Messages API (default model: claude-sonnet-5-5)
* openai    — any OpenAI-compatible Chat Completions endpoint (OpenAI, Ollama, vLLM, LM Studio,
              Groq, Together, OpenRouter…) via OPENAI_BASE_URL
* none      — deterministic mode; agents fall back to rule-based logic
"""
from __future__ import annotations

import json
import logging
import re
import threading

import requests

from ..config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider, self.model = settings.resolved_llm()
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def complete(self, system: str, user: str, max_tokens: int | None = None, temperature: float = 0.2) -> str:
        if not self.enabled:
            raise LLMError("LLM disabled (no API key configured)")
        max_tokens = max_tokens or self.settings.llm_max_tokens
        last: Exception | None = None
        for attempt in range(3):
            try:
                if self.provider == "anthropic":
                    return self._anthropic(system, user, max_tokens, temperature)
                return self._openai(system, user, max_tokens, temperature)
            except requests.HTTPError as e:
                last = e
                code = e.response.status_code if e.response is not None else 0
                if code not in (408, 429, 500, 502, 503, 504, 529):
                    break
            except requests.RequestException as e:
                last = e
            import time
            time.sleep(3 * (2 ** attempt))
        raise LLMError(f"{self.provider} call failed: {last}")

    def complete_json(self, system: str, user: str, max_tokens: int | None = None) -> dict:
        system = system + "\n\nRespond with a single valid JSON object and nothing else. Be concise."
        text = self.complete(system, user, max_tokens=max_tokens, temperature=0.0)
        try:
            return extract_json(text)
        except (LLMError, ValueError):
            pass
        # One repair attempt: usually the output was truncated or had a stray character.
        fixed = self.complete(
            "You repair malformed or truncated JSON. Output only the corrected, complete JSON object; "
            "drop any trailing incomplete item.",
            text[-30000:], max_tokens=max_tokens, temperature=0.0)
        return extract_json(fixed)

    # ------------------------------------------------------------------ providers
    def _anthropic(self, system, user, max_tokens, temperature) -> str:
        s = self.settings
        url = s.anthropic_base_url.rstrip("/") + "/v1/messages"
        headers = {"anthropic-version": "2023-06-01", "content-type": "application/json",
                   "x-api-key": s.anthropic_api_key or "", "authorization": f"Bearer {s.anthropic_api_key}"}
        payload = {"model": self.model, "max_tokens": max_tokens, "system": system,
                   "messages": [{"role": "user", "content": user}], "temperature": temperature}
        r = requests.post(url, headers=headers, json=payload, timeout=s.llm_timeout_s)
        if r.status_code == 400 and "temperature" in r.text:
            payload.pop("temperature")
            r = requests.post(url, headers=headers, json=payload, timeout=s.llm_timeout_s)
        if r.status_code >= 400:
            raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:400]}", response=r)
        data = r.json()
        u = data.get("usage", {})
        self._track(u.get("input_tokens", 0), u.get("output_tokens", 0))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    def _openai(self, system, user, max_tokens, temperature) -> str:
        s = self.settings
        url = s.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {"content-type": "application/json"}
        if s.openai_api_key:
            headers["authorization"] = f"Bearer {s.openai_api_key}"
        payload = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = requests.post(url, headers=headers, json=payload, timeout=s.llm_timeout_s)
        if r.status_code >= 400:
            raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:400]}", response=r)
        data = r.json()
        u = data.get("usage", {}) or {}
        self._track(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        return data["choices"][0]["message"]["content"] or ""

    def _track(self, i, o):
        with self._lock:
            self.usage["input_tokens"] += i or 0
            self.usage["output_tokens"] += o or 0
            self.usage["calls"] += 1


def extract_json(text: str) -> dict:
    """Parse the first JSON object in `text` (tolerates ```json fences and prose)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise LLMError("no JSON object in LLM output")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise LLMError("unterminated JSON object in LLM output")
